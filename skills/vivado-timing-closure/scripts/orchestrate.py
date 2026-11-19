#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
orchestrate.py -- prepares each stage of the timing-closure flow.

Standalone and stdlib-only: no third-party packages, no network access.

LAYER BOUNDARY -- read this before changing anything here.

    This is the DETERMINISTIC layer. It transforms files into files:
    checkpoints and reports in, TCL scripts and JSON verdicts out. Everything
    it does is reproducible and inspectable. Every engineering decision --
    which runs exist, whether a stage may start, whether a failed run may be
    retried, how many runs the budget allows, and the final verdict -- is made
    here, never by the caller.

    It does NOT run Vivado. It contains no MCP client, no bsub, no session
    handling, and must never acquire any. All Vivado execution belongs to the
    caller -- the interactive agent following SKILL.md, or the Workflow script
    under workflows/ -- which owns every session exclusively. `wait-run` only
    sleeps and looks at files.

    It also does not direct the agent. It reports what it produced and what
    stage comes next as plain facts.

    The only processes it starts are its own sibling scripts in this directory.

Commands, in order:

    orchestrate.py init            --dcp <dcp> [--output-dir <dir>] [--design NAME]
    orchestrate.py stage1-collect  --output-dir <dir>
    orchestrate.py stage2-plan     --output-dir <dir>   (refused when Stage 1 closed)
    orchestrate.py stage2-generate --output-dir <dir>
    orchestrate.py stage2-collect  --output-dir <dir>
    orchestrate.py stage3-plan     --output-dir <dir>   (opt-in: config stage3.enabled)
    orchestrate.py stage3-collect  --output-dir <dir>
    orchestrate.py finalize        --output-dir <dir>

Evidence-driven planner round (after any finished stage; repeatable):

    orchestrate.py planner-start    --output-dir <dir>   (evidence run on the winner)
    orchestrate.py planner-context  --output-dir <dir>   (what the planner agent needs)
    orchestrate.py planner-validate --output-dir <dir> --plan <plan.json>
    orchestrate.py planner-apply    --output-dir <dir> --plan <plan.json>
    orchestrate.py planner-collect  --output-dir <dir>
    orchestrate.py finalize         --output-dir <dir>

Helpers (safe any time; never run Vivado):

    orchestrate.py status        --output-dir <dir> [--json]
    orchestrate.py run-status    --run-dir <d> [--session-exit N] [--session-gone]
                                 [--lsf-status S] [--output-dir <dir>]
    orchestrate.py wait-run      --run-dir <d> [--run-dir <d2> ...] [--timeout S]
                                 [--poll S] [--mode all|any]
    orchestrate.py prepare-retry --run-dir <d> [--output-dir <dir>] [...run-status facts]
    orchestrate.py relocate      --output-dir <new> [--dcp <new dcp>]
    orchestrate.py batch-plan    --cfg <designs.cfg> --output-root <dir>

Every stage command checks the flow phase and exits 3 ("refused by guard")
when run out of order; --force overrides for interactive use.

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import copy
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
# TCA_CONFIG points one run (or a test) at its own agent_config.yaml without
# touching the shared file.
CONFIG = os.environ.get("TCA_CONFIG") or os.path.join(SKILL_ROOT, "config",
                                                      "agent_config.yaml")
SKILLS_CONFIG = os.path.join(SKILL_ROOT, "config", "skills_config.yaml")
# The skill detect/fix Tcl a run sources: TCA_ASSETS_ROOT when tc staged a copy
# for farm runs (engine/staging.py), else the skill's own assets/.
ASSETS_ENV = "TCA_ASSETS_ROOT"
SKILLS_DIR = os.path.join(os.environ.get(ASSETS_ENV) or os.path.join(SKILL_ROOT, "assets"),
                          "skills")

sys.path.insert(0, HERE)
from yaml_lite import load_yaml  # noqa: E402
from tcl_gen import (FLOW_RUN_ID, STAGE1_FLOWS, SENTINEL, DUPLICATE_MARKER,  # noqa: E402
                     FAILED_MARKER, PROBE_JSON, XDC_CHECK as tcl_xdc_check,
                     PRE_ROUTE_MARKER, OC_RESET, STAGE1_DUPLICATE_CANDIDATES,
                     POSTPLACE_MARKER, SLR_PBLOCK_XDC, NON_VERSAL_TWINS, is_versal,
                     device_family)
from decision_engine import (timing_met, pick_best_run, floor_verdict,  # noqa: E402
                             load_selection, SELECTION_MODES)
import report_parser  # noqa: E402
import planner  # noqa: E402
import dedup  # noqa: E402
import xdc_chain  # noqa: E402
import screen  # noqa: E402
import design_analyzer  # noqa: E402

RULE = "=" * 78
EXIT_REFUSED = 3
# The flow supports AMD Versal only. Not EXIT_REFUSED: tc batch stops every
# remaining design on 3 (preflight.NEEDS_EXIT), and one non-Versal design in a
# batch must fail alone.
EXIT_UNSUPPORTED_DEVICE = 6
NOT_VERSAL = ("%s is not an AMD Versal device. This flow supports Versal only; "
              "nothing was generated or launched.")

# Written into a run the flow cancelled after its design closed timing
# (close-design): {reason, closed_by, utc}. Checked before a run's other files,
# so a queued job's job.json does not make it "pending" and a killed job's
# TERM_ is not read as a retryable lsf_killed.
CANCELLED = "run.cancelled"
ON_TIMING_CLOSED = ("kill_all", "cancel_pending", "off")

# Which checkpoint a detection pass must open, per required design state.
DETECT_CHECKPOINT = {
    "pre_opt": "__INPUT_DCP__",
    "pre_place": "__INPUT_DCP__",
    "post_place": "postplace_physopt.dcp",
    "post_route": "postroute_physopt.dcp",
}

# Failure classes a single retry may fix (the infrastructure, not the design).
RETRYABLE = ("oom", "lsf_killed", "license", "interrupted")
# disk_full is not here: a retry while the volume is still full fails the same way.
# prepare-retry --space-ok retries it once a probe write shows space is back (the
# supervisor passes it when a triage answer says to retry a disk_full run).
# Build 6511674 segfaults are not deterministic (a twin run passes the same step):
# one retry, unless guardrails.retry_crash is false.
RETRYABLE_CRASH = "crash"

# Phases in which each stage has a pending run list.
ROW_PHASES = {
    "probe_ready": "probe_rows",
    "stage1_ready": "stage1_rows",
    "stage1_rerun": "stage1_rerun_rows",
    "stage2_detect": "detect_rows",
    "stage2_ready": "stage2_rows",
    "stage3_ready": "stage3_rows",
    "planner_evidence": "planner_evidence_rows",
    "planner_screen": "planner_screen_rows",
    "planner_ready": "planner_rows",
    "planner_restore": "planner_restore_rows",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def run_script(name, argv):
    """Run a sibling script with the same interpreter; abort loudly on failure."""
    cmd = [sys.executable, os.path.join(HERE, name)] + [str(a) for a in argv]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    if proc.returncode != 0:
        print("ERROR: %s failed (exit %d)" % (name, proc.returncode), file=sys.stderr)
        print(proc.stderr.strip(), file=sys.stderr)
        sys.exit(1)
    return proc.stdout


def read_json(path, default=None, lenient=False):
    """lenient: an empty or truncated file (a run's marker written onto a full disk)
    reads as {} instead of raising."""
    if not os.path.isfile(path):
        return default
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        if not lenient:
            return json.load(f)
        try:
            return json.load(f)
        except ValueError:
            return {}


def write_json(path, data):
    """Atomic: a killed step can never leave a half-written state.json."""
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_config():
    return load_yaml(CONFIG) if os.path.isfile(CONFIG) else {}


def cfg_get(cfg, section, key, default):
    val = (cfg.get(section, {}) or {}).get(key, default)
    return default if val is None else val


def as_bool(val):
    return val is True or str(val).lower() in ("true", "yes", "1")


def paths_for(output_dir):
    o = os.path.abspath(output_dir)
    j = lambda n: os.path.join(o, n)  # noqa: E731
    return {
        "root": o,
        "state": j("state.json"),
        "worklist": j("worklist.json"),
        "stage1": j("stage1"),
        "stage2": j("stage2"),
        "stage3": j("stage3"),
        "detect": j("detect"),
        "stage1_runs": j("stage1_runs.json"),
        "stage1_best": j("stage1_best.json"),
        "classification": j("classification.json"),
        "stage2_plan": j("stage2_plan.json"),
        "stage2_runs": j("stage2_runs.json"),
        "stage2_best": j("stage2_best.json"),
        "stage3_floor": j("stage3_floor.json"),
        "stage3_runs": j("stage3_runs.json"),
        "stage3_best": j("stage3_best.json"),
        "planner": j("planner"),
        "path_b": j("path_b"),
        "planner_floor": j("planner_floor.json"),
        "planner_runs": j("planner_runs.json"),
        "planner_best": j("planner_best.json"),
        "verification": j("verification.json"),
        "summary": j("final_summary.txt"),
        "summary_json": j("final_summary.json"),
    }


def refuse(msg):
    print("ERROR: %s" % msg, file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def load_state(p, args=None):
    state = read_json(p["state"])
    if state is None:
        print("ERROR: no state.json in %s -- run `orchestrate.py init` first."
              % p["root"], file=sys.stderr)
        sys.exit(1)
    recorded = state.get("output_dir")
    force = bool(args is not None and getattr(args, "force", False))
    if recorded and os.path.realpath(recorded) != os.path.realpath(p["root"]) \
            and not force:
        print("ERROR: output dir moved from %s; run.tcl scripts and state embed "
              "absolute paths. Run `orchestrate.py relocate --output-dir %s` first "
              "(or pass --force)." % (recorded, p["root"]), file=sys.stderr)
        sys.exit(1)
    return state


def require_phase(state, allowed, cmd, args):
    phase = state.get("phase")
    if phase not in allowed and not getattr(args, "force", False):
        refuse("%s not allowed in phase %s (expected one of: %s); pass --force to "
               "override" % (cmd, phase, ", ".join(allowed)))


def write_worklist(p, label, rows, next_step):
    write_json(p["worklist"], {"label": label, "pending_runs": rows,
                               "next_step": next_step})


def emit_worklist(p, label, rows, next_step):
    """Record the runs that are now ready to execute, and report them as facts."""
    write_worklist(p, label, rows, next_step)

    print("")
    print(RULE)
    print("%s -- %d run script(s) ready" % (label, len(rows)))
    print(RULE)
    if rows:
        print("  %-26s %s" % ("RUN_ID", "RUN DIRECTORY (contains run.tcl)"))
        print("  %-26s %s" % ("-" * 26, "-" * 48))
        for r in rows:
            print("  %-26s %s" % (r["run_id"], r["working_dir"]))
    print("")
    print("  worklist : %s" % p["worklist"])
    print("  next step: %s" % next_step)
    print(RULE)


def finish_step(p, label, next_step, message):
    """A step with no runs to execute: clear the worklist and name what is next."""
    write_worklist(p, label, [], next_step)
    print("")
    print(message)
    print("next step: %s" % (next_step or "none"))


def row(run_id, run_dir, **extra):
    r = {"run_id": run_id, "working_dir": run_dir,
         "run_tcl": os.path.join(run_dir, "run.tcl")}
    r.update(extra)
    return r


class RunGenerator(object):
    """
    Writes run.tcl via tcl_gen.py unless the run would only replay one that
    already completed (or one generated earlier in the same batch) -- see
    dedup.py. `prior` is every run dir outside the batch being generated.

    role "planner": a planner fix also yields to a run that is generated, queued
    or running anywhere in the flow, Path B included -- it will produce the same
    result. Static stages (the default role) keep yielding to completed runs
    only, so a static run is never skipped because of a planner one.
    """

    def __init__(self, p, cfg, exclude, role="static"):
        self.root = p["root"]
        self.on = as_bool(cfg_get(cfg, "guardrails", "skip_duplicate_runs", True))
        self.unfinished = role == "planner"
        self.prior = dedup.run_dirs(p["root"], exclude=exclude,
                                    with_path_b=self.unfinished) if self.on else []
        self.batch = {}

    @staticmethod
    def _empty_rqs(argv):
        """A pure RQS run that would enable no suggestion only replays its parent flow."""
        if "--kind" not in argv or argv[argv.index("--kind") + 1] != "rqs" or \
                "--rqs-file" not in argv:
            return None
        rqs = argv[argv.index("--rqs-file") + 1]
        cats = [argv[i + 1] for i, a in enumerate(argv) if a == "--rqs-category"]
        if dedup.rqs_enabled_count(rqs, cats or None) == 0:
            return ("no QoR suggestion in %s would be enabled%s -- the run would only "
                    "replay its parent flow" % (os.path.basename(rqs),
                                                " for " + ", ".join(cats) if cats else ""))
        return None

    def generate(self, argv, run_dir):
        """-> None when written, else a reason the run was not generated."""
        empty = self._empty_rqs(argv)
        if empty:
            return empty
        if not self.on:
            run_script("tcl_gen.py", argv)
            return None
        why, fp = self.duplicate(argv)
        if why:
            return why
        run_script("tcl_gen.py", argv)
        self.batch[fp] = run_dir
        return None

    def duplicate(self, argv):
        """-> (reason or None, fingerprint), from a dry run; nothing is written."""
        text = run_script("tcl_gen.py", argv + ["--dry-run"])
        fp = dedup.run_fingerprint(text)
        dup = self.batch.get(fp) or dedup.find_duplicate(text, self.prior, self.unfinished)
        if not dup:
            return None, fp
        if base_state(dup) == "done":
            t = report_parser.parse_timing_summary(
                os.path.join(dup, "postroute_physopt_timing_summary.rpt"))
            got = (" (WNS %+.3f)" % t["wns"]) if t.get("wns") is not None else ""
            return ("same inputs as %s%s -- Vivado is deterministic, it would replay "
                    "that result" % (os.path.relpath(dup, self.root), got)), fp
        return ("same inputs as %s, which is already %s -- it will produce this result"
                % (os.path.relpath(dup, self.root),
                   "generated" if base_state(dup) == "not_started" else "queued or running")), fp


# ---------------------------------------------------------------------------
# Run-directory inspection (facts from files only; no Vivado)
# ---------------------------------------------------------------------------
# Files the orchestrator writes into a run dir BEFORE launch. They are inputs, not
# run output: counting them made a never-started planner fix look "running" (its
# launch was skipped) and a retry would have moved them into attempt_<n>/.
RUN_INPUTS = ("run.tcl", "planner_fix.xdc")


def _top_entries(run_dir):
    try:
        return [e for e in os.listdir(run_dir)
                if e not in RUN_INPUTS and not e.startswith("attempt_")]
    except OSError:
        return []


def attempts_of(run_dir):
    return len(glob.glob(os.path.join(run_dir, "attempt_*")))


def _run_tcl_text(run_dir):
    try:
        with open(os.path.join(run_dir, "run.tcl"), 'r', encoding='utf-8',
                  errors='replace') as f:
            return f.read()
    except (IOError, OSError):
        return ""


def _newest_mtime(run_dir):
    """Newest mtime among run outputs (top level + one level down, e.g. .vivado-ai/)."""
    newest = 0.0
    for e in _top_entries(run_dir):
        path = os.path.join(run_dir, e)
        try:
            newest = max(newest, os.path.getmtime(path))
            if os.path.isdir(path):
                for n in os.listdir(path):
                    newest = max(newest, os.path.getmtime(os.path.join(path, n)))
        except OSError:
            pass
    return newest


def _log_files(run_dir):
    pats = ["*.log", "*.jou", ".vivado-ai/*.log", ".vivado-ai/*.jou"]
    out = []
    for pat in pats:
        out.extend(glob.glob(os.path.join(run_dir, pat)))
    return sorted(set(out))


def _grep(files, patterns, limit=10):
    """Return up to `limit` matching lines (verbatim) for any regex in patterns."""
    hits = []
    rx = [re.compile(p) for p in patterns]
    for fn in files:
        try:
            with open(fn, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if any(r.search(line) for r in rx):
                        hits.append("%s: %s" % (os.path.basename(fn), line.rstrip()[:240]))
                        if len(hits) >= limit:
                            return hits
        except (IOError, OSError):
            continue
    return hits


OOM_PATTERNS = [r"exit status 137", r"insufficient memory", r"OutOfMemory",
                r"Out of [Mm]emory", r"std::bad_alloc", r"TERM_MEMLIMIT",
                # The MCP session's Go proxy dying when the host runs out of memory.
                r"fatal error: runtime: out of memory"]
CRASH_PATTERNS = [r"exit status 139", r"Abnormal program termination",
                  r"FATAL_ERROR", r"Segmentation fault", r"SIGSEGV"]
LICENSE_PATTERNS = [r"[Ll]icense checkout failed", r"Failed to get a license",
                    r"A valid license was not found", r"\[Common 17-345\]",
                    r"No such feature exists", r"Licensed number of users already reached"]
VIVADO_ERROR_PATTERNS = [r"^ERROR: \["]
# Writes failing on a full volume. `df` on a shared project volume showed 4.6T free while
# the project quota (~1T) was full, so the logs are the only reliable signal.
DISK_FULL_PATTERNS = [r"No space left on device", r"Disk quota exceeded",
                      r"sufficient free disk space", r"\[Common 17-141\] Failed to write",
                      r"\[Common 17-99\] Failed to open zip archive",
                      r"\[Runs 36-406\] Could not open the file .* for writing",
                      r"\[Common 17-1294\] Unable to create directory"]
# LSF's own kill notice in the session log; the caller does not always pass
# --lsf-status (design A Stage 1 auto_3 hit the 8 h limit and stayed "unknown").
LSF_KILL_PATTERNS = [r"\bTERM_(RUNLIMIT|CPULIMIT|PREEMPT|DEADLINE|WINDOW|LOAD|REQUEUE_\w+)\b",
                     r"job killed after reaching LSF run time limit"]


def classify_failure(run_dir, lsf_status=None, session_gone=False):
    """Return (failure_class, evidence_lines) for a run that did not finish."""
    logs = _log_files(run_dir)
    hs_err = _hs_err_files(run_dir)
    lsf = (lsf_status or "").upper()
    failed = read_json(os.path.join(run_dir, FAILED_MARKER), lenient=True)
    if failed is not None:
        # run.tcl's own catch: an uncaught Tcl error, with Vivado's message. An
        # empty run.failed means even that small write failed.
        msg = str(failed.get("error", "")) or "(empty run.failed -- the disk is probably full)"
        if not failed or any(re.search(pat, msg) for pat in DISK_FULL_PATTERNS):
            return "disk_full", ["run.failed: %s" % msg[:240]] + _grep(logs, DISK_FULL_PATTERNS, 3)
        if any(re.search(pat, msg) for pat in LICENSE_PATTERNS):
            return "license", ["run.failed: %s" % msg[:240]]
        return "vivado_error", ["run.failed: %s" % msg[:240]]

    ev = _grep(logs + hs_err, OOM_PATTERNS)
    if ev or "TERM_MEMLIMIT" in lsf:
        return "oom", ev + ([lsf_status] if "TERM_MEMLIMIT" in lsf else [])
    if "TERM_" in lsf:
        return "lsf_killed", [lsf_status]
    ev = _grep(logs, LSF_KILL_PATTERNS)
    if ev:
        return "lsf_killed", ev
    ev = _grep(logs, LICENSE_PATTERNS)
    if ev:
        return "license", ev
    ev = _grep(logs + hs_err, CRASH_PATTERNS)
    if ev or hs_err:
        return "crash", ev or ["hs_err file: %s" % os.path.basename(hs_err[0])]
    ev = _grep(logs, DISK_FULL_PATTERNS)
    if ev:
        return "disk_full", ev
    ev = _grep(logs, VIVADO_ERROR_PATTERNS)
    if ev:
        return "vivado_error", ev
    if session_gone:
        return "interrupted", ["no error evidence in logs; the Vivado session is gone"]
    return "unknown", []


def _hs_err_files(run_dir):
    # Vivado writes hs_mangled_err_pid*.log on some builds, hs_err_pid*.log on others.
    return sorted(glob.glob(os.path.join(run_dir, "hs_err_pid*.log")) +
                  glob.glob(os.path.join(run_dir, "hs_mangled_err_pid*.log")))


def crash_evidence(run_dir):
    """True once a run has certainly stopped abnormally (no need to wait for staleness)."""
    if os.path.isfile(os.path.join(run_dir, FAILED_MARKER)) or _hs_err_files(run_dir):
        return True
    return bool(_grep(_log_files(run_dir), [r"exit status 139", r"Abnormal program termination",
                                            r"Segmentation fault"], limit=1))


def routed_stage(run_dir):
    """The latest routed result a run has: postroute_physopt, else postroute (or None)."""
    for st in ("postroute_physopt", "postroute"):
        if os.path.isfile(os.path.join(run_dir, "%s_timing_summary.rpt" % st)):
            return st
    return None


def routed_checkpoint(run_dir):
    for st in ("postroute_physopt", "postroute"):
        d = os.path.join(run_dir, "%s.dcp" % st)
        if os.path.isfile(d):
            return d
    return os.path.join(run_dir, "postroute_physopt.dcp")


def final_results_written(run_dir):
    """
    Everything the flow uses from this run is on disk, even if Vivado died after
    (the final RQS runs last precisely so a crash there costs nothing).
    """
    text = _run_tcl_text(run_dir)
    if "evidence.tcl" in text:
        return os.path.isfile(os.path.join(run_dir, "evidence.json"))
    if "Build Probe" in text:
        return os.path.isfile(os.path.join(run_dir, PROBE_JSON))
    # The final RQS step starts with `file mkdir postroute_physopt_rqs`, and it runs
    # only after every final report, the checkpoint and the extra reports.
    return (os.path.isfile(os.path.join(run_dir, "postroute_physopt_timing_summary.rpt")) and
            os.path.isdir(os.path.join(run_dir, "postroute_physopt_rqs")))


def _is_detect_dir(run_dir):
    return "Skill Detection Pass" in _run_tcl_text(run_dir)


def base_state(run_dir):
    """done | cancelled | not_started | pending(=has output, no sentinel) -- no staleness yet."""
    if not os.path.isfile(os.path.join(run_dir, "run.tcl")):
        return "missing"
    if os.path.isfile(os.path.join(run_dir, SENTINEL)):
        return "done"
    if os.path.isfile(os.path.join(run_dir, CANCELLED)):
        return "cancelled"
    if SENTINEL not in _run_tcl_text(run_dir):
        # Legacy run.tcl without a sentinel: fall back to the final artifact.
        if _is_detect_dir(run_dir):
            if _top_entries(run_dir):
                return "done"
        elif os.path.isfile(os.path.join(run_dir, "postroute_physopt.dcp")) or \
                os.path.isfile(os.path.join(run_dir, "postroute_physopt_timing_summary.rpt")):
            return "done"
    if not _top_entries(run_dir):
        return "not_started"
    return "pending"


def launched_total(p, state):
    """Implementation runs that have started (any output) plus every retry."""
    total = 0
    for rows in [state.get(k) for k in ("stage1_rows", "stage2_rows", "stage3_rows",
                                         "planner_rows_all", "planner_restore_rows")] + \
            [path_b_rows(state)]:
        for r in rows or []:
            d = r["working_dir"]
            if base_state(d) not in ("not_started", "missing"):
                total += 1
            total += attempts_of(d)
    return total


def run_status(run_dir, cfg, state=None, p=None, session_exit=None,
               session_gone=False, lsf_status=None, job_state=None):
    run_dir = os.path.abspath(run_dir)
    guard = cfg.get("guardrails", {}) or {}
    stale_s = float(guard.get("stale_minutes", 15) or 15) * 60.0
    max_retries = int(guard.get("max_retries_per_run", 1) or 0)
    max_total = int(guard.get("max_total_runs", 40) or 40)

    st = base_state(run_dir)
    failure_class, evidence = None, []
    lsf = (lsf_status or "").upper()
    lsf_ended = any(k in lsf for k in ("EXIT", "TERM_", "DONE"))
    ended = session_gone or session_exit is not None or lsf_ended
    note = None
    if st == "pending":
        # File activity is authoritative: an MCP backend restart can make a live
        # session look "gone" while Vivado keeps writing. Only an LSF job that
        # has definitely ended, or certain crash evidence (hs_err / run.failed /
        # exit 139 -- Ciena's crashed run otherwise looked "running" for 15 min
        # and stopped the workflow), may override fresh output.
        #
        # The scheduler's own word comes next: a job it still has queued or
        # running is waiting, however old the run's files are. The staleness
        # limit is for runs nobody can vouch for -- on a busy queue it declared
        # 455 queued Rubik runs failed, and nothing collected them after.
        age = time.time() - _newest_mtime(run_dir)
        queued = (job_state or "").lower() in ("pending", "running")
        if crash_evidence(run_dir):
            st = "failed"
        elif queued and not lsf_ended:
            st = "running"
            note = "the scheduler has the job %s" % job_state.lower()
        elif age < stale_s and not lsf_ended:
            st = "running"
        else:
            st = "failed"
    if st == "failed":
        failure_class, evidence = classify_failure(run_dir, lsf_status, session_gone)
        if session_exit not in (None, 0) and failure_class == "unknown":
            failure_class = "vivado_error"
            evidence = ["session reported last_completed_code=%s" % session_exit]
        if final_results_written(run_dir):
            note = ("%s after every result was written (only the trailing steps, e.g. the "
                    "final QoR suggestions, are missing)" % failure_class)
            st, failure_class = "done", None

    attempts = attempts_of(run_dir)
    total = launched_total(p, state) if (p and state) else 0
    retryable = RETRYABLE + ((RETRYABLE_CRASH,) if as_bool(guard.get("retry_crash", True))
                             else ())
    retry_eligible = bool(st == "failed" and failure_class in retryable
                          and attempts < max_retries and total < max_total
                          and not (state or {}).get("closed_by"))

    tm = None
    summary = os.path.join(run_dir, "postroute_physopt_timing_summary.rpt")
    if os.path.isfile(summary):
        tm = timing_met(report_parser.parse_timing_summary(summary))

    stop_stage = False
    if state and st == "done" and tm and state.get("stop_on_first_close"):
        stage1_dirs = [os.path.abspath(r["working_dir"])
                       for r in state.get("stage1_rows") or []]
        stop_stage = run_dir in stage1_dirs

    return {
        "working_dir": run_dir,
        "state": st,
        "failure_class": failure_class,
        "evidence": evidence[:10],
        "attempts": attempts,
        "retry_eligible": retry_eligible,
        "launched_total": total,
        "max_total_runs": max_total,
        "timing_met": tm,
        "stop_stage": stop_stage,
        "note": note,
    }


def _find_output_dir(run_dir):
    """Walk up from a run dir to the directory holding state.json."""
    d = os.path.abspath(run_dir)
    for _ in range(4):
        d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, "state.json")):
            return d
    return None


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def stage1_rows_for(p, flows):
    return [row(FLOW_RUN_ID.get(f, "S1_" + f.upper()), os.path.join(p["stage1"], f),
                flow=f) for f in flows]


# User TCL hooks: init flag -> state key. Every generated run gets all of them;
# each lands after its implementation step in runs that have that step.
HOOK_FLAGS = (("--extra-cmd", "extra_cmd"), ("--post-opt-cmd", "post_opt_cmd"),
              ("--post-place-cmd", "post_place_cmd"), ("--post-popt-cmd", "post_popt_cmd"),
              ("--post-route-cmd", "post_route_cmd"),
              ("--post-route-popt-cmd", "post_route_popt_cmd"))


def normalize_hook_cmd(cmd):
    """
    A hook value is raw Tcl, or just the path of a Tcl script (upstream PR #742):
    an existing file ending in .tcl becomes `source {<absolute path>}`. Resolved
    now, against the directory init runs in, since run.tcl runs in its run dir.
    """
    stripped = cmd.strip()
    if stripped.lower().endswith(".tcl") and os.path.isfile(stripped):
        return "source {%s}" % os.path.abspath(stripped)
    return cmd


def hook_path_problems(cmds):
    """Single-token values ending in .tcl that aren't files: a typo, caught at init."""
    return [c for c in cmds or []
            if len(c.split()) == 1 and c.lower().endswith(".tcl") and not os.path.isfile(c)]


def hook_argv(src):
    """tcl_gen.py flags for the user hooks in src (init args or state)."""
    argv = []
    for flag, key in HOOK_FLAGS:
        vals = src.get(key) if isinstance(src, dict) else getattr(src, key, None)
        for c in vals or []:
            argv += [flag, c]
    return argv


def replay_argv(p, flow):
    """
    --replay-run-dir for a run that replays Stage 1 flow `flow`: that Stage 1
    run's log says what the placer predicted for its Auto_n subdirectives, and
    the replay places the same way (upstream 2026-10-08).
    """
    d = os.path.join(p["stage1"], flow or "")
    return ["--replay-run-dir", d] if flow and os.path.isdir(d) else []


def design_checks_flow(flows):
    """
    The Stage 1 run that also writes report_methodology / check_timing /
    report_cdc: the baseline when there is one (upstream's choice), else the
    first strategy that can never stop early as a duplicate.
    """
    if "baseline" in flows:
        return "baseline"
    for f in flows:
        if f not in STAGE1_DUPLICATE_CANDIDATES:
            return f
    return flows[0] if flows else None


KNOWN_KEYS_NUMERIC = (
    # (section, key, minimum) -- upstream validates these at start (2026-10-08):
    # a typo used to surface hours into a run.
    ("guardrails", "max_total_runs", 1),
    ("guardrails", "stage2_min_improvement_ps", 0),
    ("guardrails", "max_retries_per_run", 0),
    ("guardrails", "stale_minutes", 1),
    ("stage2", "multi_physopt.max_iterations", 1),
    ("planner", "max_fixes", 0),
    ("planner", "max_rounds", 1),
    ("flow", "postplace_physopt_max_iter", 1),
)

# flow: switches (upstream b0cfa7a7 choices) and their accepted values.
FLOW_CHOICES = (
    ("postplace_physopt", ("flow", "skill")),
    ("postroute_physopt", ("flow", "single")),
)


def validate_config(cfg):
    """Problems in agent_config.yaml that would only show up mid-run."""
    problems = []
    for f in cfg_get(cfg, "stage1", "runs", []) or []:
        if f not in STAGE1_FLOWS:
            problems.append("stage1.runs: unknown flow %r (known: %s)"
                            % (f, ", ".join(sorted(STAGE1_FLOWS))))
    for section, key, low in KNOWN_KEYS_NUMERIC:
        val = cfg.get(section, {}) or {}
        for part in key.split("."):
            val = val.get(part) if isinstance(val, dict) else None
        if val is None:
            continue
        try:
            if float(val) < low:
                problems.append("%s.%s: %s is below %s" % (section, key, val, low))
        except (TypeError, ValueError):
            problems.append("%s.%s: %r is not a number" % (section, key, val))
    mults = cfg_get(cfg, "stage2", "over_constrain", {}) or {}
    for m in mults.get("wns_multipliers", []) or []:
        try:
            if float(m) <= 0:
                problems.append("stage2.over_constrain.wns_multipliers: %s is not > 0" % m)
        except (TypeError, ValueError):
            problems.append("stage2.over_constrain.wns_multipliers: %r is not a number" % m)
    fl = cfg.get("flow") or {}
    for key, choices in FLOW_CHOICES:
        val = fl.get(key)
        if val is not None and str(val).strip().lower() not in choices:
            problems.append("flow.%s: %r is not one of %s" % (key, val, ", ".join(choices)))
    rd = fl.get("route_directive")
    if rd is not None and (not isinstance(rd, str) or not re.match(r"^[A-Za-z]+$", rd.strip())):
        problems.append("flow.route_directive: %r is not a route_design directive name" % (rd,))
    kd = cfg_get(cfg, "output", "keep_dcps", "winner")
    if str(kd).strip().lower() not in ("winner", "all"):
        problems.append("output.keep_dcps: %r is not one of winner, all" % (kd,))
    otc = cfg_get(cfg, "guardrails", "on_timing_closed", "kill_all")
    if str(otc).strip().lower() not in ON_TIMING_CLOSED:
        problems.append("guardrails.on_timing_closed: %r is not one of %s"
                        % (otc, ", ".join(ON_TIMING_CLOSED)))
    skip = (cfg.get("detection") or {}).get("skip_sub_problems") or []
    if not isinstance(skip, list):
        problems.append("detection.skip_sub_problems: %r is not a list" % (skip,))
    else:
        from skill_resolver import SKILL_CATALOG
        known = sorted({e["sub_problem"] for e in SKILL_CATALOG})
        for sub in skip:
            if sub not in known:
                problems.append("detection.skip_sub_problems: unknown sub-problem %r (known: %s)"
                                % (sub, ", ".join(known)))
    scr = (cfg.get("planner") or {}).get("screen")
    if scr is not None and scr is not False and str(scr).strip().lower() not in screen.SCREEN_MODES:
        problems.append("planner.screen: %r is not one of %s" % (scr, ", ".join(screen.SCREEN_MODES)))
    sel = cfg.get("selection") or {}
    if sel.get("mode") is not None and str(sel["mode"]).strip().lower() not in SELECTION_MODES:
        problems.append("selection.mode: %r is not one of %s" % (sel["mode"], ", ".join(SELECTION_MODES)))
    for key in ("wns_bands_ps", "tns_bands_ns"):
        edges = sel.get(key)
        if edges is None:
            continue
        try:
            vals = [float(e) for e in edges]
        except (TypeError, ValueError):
            problems.append("selection.%s: %r is not a list of numbers" % (key, edges))
            continue
        if not vals or vals != sorted(vals, reverse=True):
            problems.append("selection.%s: %r must run from best to worst (descending)" % (key, edges))
    return problems


def checkpoint_part(dcp):
    """
    The part a checkpoint targets, from the dcp.xml inside it (a .dcp is a zip:
    <Part Name="xcvc1902-vsva2197-2MP-e-S"/>). None when it cannot be read.
    """
    import zipfile
    try:
        with zipfile.ZipFile(dcp) as z:
            names = [n for n in z.namelist() if n.endswith("dcp.xml")]
            if not names:
                return None
            text = z.read(names[0]).decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r'<Part\s+Name="([^"]+)"', text)
    return m.group(1) if m else None


def cmd_init(args):
    for flag, key in HOOK_FLAGS:
        bad = hook_path_problems(getattr(args, key, None))
        if bad:
            print("ERROR: %s: Tcl file not found: %s" % (flag, ", ".join(bad)), file=sys.stderr)
            sys.exit(1)
        setattr(args, key, [normalize_hook_cmd(c) for c in getattr(args, key, None) or []])
    dcp = os.path.abspath(args.dcp)
    if not os.path.isfile(dcp):
        print("ERROR: checkpoint not found: %s" % dcp, file=sys.stderr)
        sys.exit(1)
    # Versal only, decided before anything is written. A part that cannot be
    # read is left to the build probe's ARCHITECTURE (probe-collect).
    part = checkpoint_part(dcp)
    family = device_family(part)
    if family == "other":
        print("ERROR: " + NOT_VERSAL % part, file=sys.stderr)
        sys.exit(EXIT_UNSUPPORTED_DEVICE)

    design = args.design or os.path.basename(os.path.dirname(dcp)) or "design"
    output_dir = os.path.abspath(
        args.output_dir or os.path.join(os.getcwd(), "%s_timing_closure" % design))
    p = paths_for(output_dir)

    existing = read_json(p["state"])
    if existing and not args.force:
        if existing.get("dcp") != dcp:
            print("ERROR: %s is already initialised for %s; pass --force to start "
                  "over for %s." % (output_dir, existing.get("dcp"), dcp), file=sys.stderr)
            sys.exit(1)
        if existing.get("phase") == "stage1_ready":
            print("Already initialised; re-emitting the Stage 1 worklist "
                  "(run.tcl files untouched).")
            emit_worklist(p, "STAGE 1", existing["stage1_rows"], "stage1-collect")
            return 0
        print("Already initialised (phase %s); nothing changed." % existing.get("phase"))
        print("next step: %s" % PHASE_NEXT.get(existing.get("phase"), ("", None))[1])
        return 0

    cfg = load_config()
    problems = validate_config(cfg)
    if problems:
        print("ERROR: config/agent_config.yaml:\n  %s" % "\n  ".join(problems),
              file=sys.stderr)
        sys.exit(1)
    flows = list(cfg_get(cfg, "stage1", "runs", []) or [])
    if not flows:
        print("ERROR: config/agent_config.yaml lists no stage1.runs", file=sys.stderr)
        sys.exit(1)
    if as_bool(cfg_get(cfg, "stage1", "include_baseline", False)) and "baseline" not in flows:
        flows.append("baseline")
    os.makedirs(p["stage1"], exist_ok=True)
    checks_flow = (design_checks_flow(flows)
                   if as_bool(cfg_get(cfg, "stage1", "run_methodology_check", True)) else None)

    rows = stage1_rows_for(p, flows)
    for r in rows:
        argv = ["stage1", "--flow", r["flow"], "--dcp", dcp, "--run-dir",
                r["working_dir"], "--config", CONFIG] + hook_argv(args)
        if r["flow"] == checks_flow:
            argv.append("--design-checks")
        run_script("tcl_gen.py", argv)

    state = {
        "design": design,
        "dcp": dcp,
        "output_dir": output_dir,
        "skill_root": SKILL_ROOT,
        "phase": "stage1_ready",
        "stage1_flows": flows,
        "stage1_rows": rows,
        "stop_on_first_close": bool(args.stop_on_first_close or
                                    as_bool(cfg_get(cfg, "stage1", "stop_on_first_close",
                                                    False))),
        "retries": [],
    }
    for _, key in HOOK_FLAGS:
        state[key] = getattr(args, key) or []
    if checks_flow:
        state["design_checks_run"] = os.path.join(p["stage1"], checks_flow)
    state["device"] = {"part": part, "family": family}
    if family is None and not args.probe:
        print("WARNING: could not read the part from the checkpoint and there is no "
              "build probe: the device is not checked (this flow supports Versal only).",
              file=sys.stderr)
    if args.probe:
        probe_dir = os.path.join(output_dir, "probe")
        run_script("tcl_gen.py", ["probe", "--dcp", dcp, "--run-dir", probe_dir,
                                  "--config", CONFIG, "--run-name", "PROBE"])
        state["phase"] = "probe_ready"
        state["probe_rows"] = [row("PROBE", probe_dir)]
    # The staged Tcl copy (tc close with stage_root): later steps and a restarted
    # supervisor keep generating runs against it (use_staged_assets).
    if os.environ.get(ASSETS_ENV):
        state["assets_root"] = os.environ[ASSETS_ENV]
    write_json(p["state"], state)

    print("Design      : %s" % design)
    print("Checkpoint  : %s" % dcp)
    print("Output dir  : %s" % output_dir)
    print("Generated %d Stage 1 run scripts." % len(rows))
    if state["stop_on_first_close"]:
        print("Stop-on-first-close is ON: remaining Stage 1 strategies are skipped "
              "once a run closes timing.")

    if args.probe:
        print("Build probe first: Stage 1 is released by probe-collect.")
        emit_worklist(p, "BUILD PROBE", state["probe_rows"], "probe-collect")
        return 0
    emit_worklist(p, "STAGE 1", rows, "stage1-collect")
    return 0


def cmd_probe_collect(args):
    """Release Stage 1 once the pinned build has opened the input checkpoint."""
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("probe_ready",), "probe-collect", args)
    rows = state.get("probe_rows") or []
    refuse_if_running(rows, args)
    d = rows[0]["working_dir"] if rows else os.path.join(p["root"], "probe")
    st = run_status(d, load_config())
    probe = read_json(os.path.join(d, PROBE_JSON))
    if st["state"] != "done" or not probe:
        state["phase"] = "probe_failed"
        state["probe_result"] = {"state": st["state"], "failure_class": st["failure_class"],
                                 "evidence": st["evidence"][:5]}
        write_json(p["state"], state)
        finish_step(p, "BUILD PROBE", None,
                    "Build probe FAILED (%s, %s): %s. Stage 1 was not released."
                    % (st["state"], st["failure_class"], "; ".join(st["evidence"][:2])))
        return 0
    state["probe_result"] = probe
    arch = probe.get("architecture")
    versal = is_versal(arch) if arch else device_family(probe.get("part")) != "other"
    if not versal:
        state["phase"] = "unsupported_device"
        write_json(p["state"], state)
        finish_step(p, "BUILD PROBE", None,
                    "The device is not AMD Versal (part %s, architecture %s). This flow "
                    "supports Versal only; Stage 1 was not released and nothing was "
                    "launched." % (probe.get("part"), arch))
        return 0
    state["phase"] = "stage1_ready"
    skipped = drop_non_versal_twins(state, probe.get("architecture"))
    write_json(p["state"], state)
    print("Build probe OK: Vivado %s, part %s, %s primitives." % (
        probe.get("vivado"), probe.get("part"), probe.get("primitives")))
    for s in skipped:
        print("  %s not run: on %s it places as %s (no placer sub-directives "
              "outside Versal)." % (s["run_id"], probe.get("architecture"), s["same_as"]))
    emit_worklist(p, "STAGE 1", state["stage1_rows"], "stage1-collect")
    return 0


def drop_non_versal_twins(state, architecture):
    """
    Off Versal, auto_2 and auto_4 run the same ML-predicted directive as auto_1
    and auto_3 (tcl_gen AUTO_ML_DIRECTIVES), so they would only place twice and
    stop as duplicates. Dropped from Stage 1 while their twin runs. Needs the
    probe's architecture; with none (an older probe) nothing changes.
    """
    if not architecture or is_versal(architecture):
        return []
    flows = state.get("stage1_flows") or []
    checks = os.path.basename(state.get("design_checks_run") or "")
    drop = [f for f, twin in NON_VERSAL_TWINS.items()
            if f in flows and twin in flows and f != checks]
    if not drop:
        return []
    skipped = [{"run_id": FLOW_RUN_ID.get(f, f), "flow": f,
                "same_as": FLOW_RUN_ID.get(NON_VERSAL_TWINS[f], NON_VERSAL_TWINS[f]),
                "architecture": architecture} for f in drop]
    state["stage1_flows"] = [f for f in flows if f not in drop]
    state["stage1_rows"] = [r for r in state.get("stage1_rows") or []
                            if r.get("flow") not in drop]
    state["stage1_skipped"] = skipped
    return skipped


# ---------------------------------------------------------------------------
# Collect helpers
# ---------------------------------------------------------------------------
def refuse_if_running(rows, args):
    """Collecting while a run is still writing output would lock in a partial result."""
    if getattr(args, "force", False):
        return
    cfg = load_config()
    busy = [r["run_id"] for r in rows
            if run_status(r["working_dir"], cfg)["state"] == "running"]
    if busy:
        refuse("run(s) still in progress (output changed within guardrails.stale_minutes): "
               "%s. Wait for them, or pass --force." % ", ".join(busy))


def parse_rows(rows, out_path, flow_names=None):
    """Parse exactly the given rows (never a directory scan) into out_path.
    Runs cancelled after the design closed (close-design) have no result."""
    rows = [r for r in rows if base_state(r["working_dir"]) != "cancelled"]
    argv = ["--array", "-o", out_path]
    for r in rows:
        argv += ["--run-dir", r["working_dir"]]
        argv += ["--flow-name", (flow_names or {}).get(r["working_dir"], r.get("flow", ""))]
        argv += ["--methodology", r.get("methodology", "")]
    if not rows:
        write_json(out_path, [])
        return []
    run_script("report_parser.py", argv)
    runs = read_json(out_path, [])
    changed = False
    for rec in runs:
        d = rec.get("run_dir")
        if rec.get("wns") is not None or not d or routed_stage(d) != "postroute":
            continue
        if not any(os.path.isfile(os.path.join(d, c))
                   for c in ("postroute.dcp", "postroute_physopt.dcp", PRUNED)):
            # Died inside write_checkpoint postroute.dcp (design C auto_3): its numbers
            # can't be continued from, and a winner without a checkpoint fails
            # verify_winner and blocks the planner and Stage 3. Report, don't rank.
            t = report_parser.parse_timing_summary(
                os.path.join(d, "postroute_timing_summary.rpt"))
            if t.get("wns") is not None:
                rec["methodology"] = ((rec.get("methodology") or "") +
                                      " [not ranked: post-route WNS %+.3f but no routed checkpoint]"
                                      % t["wns"]).strip()
                changed = True
            continue
        # The run died between route and post-route phys_opt: its routed result
        # is real and ranked, but flagged (Ciena: a closed post-route result would
        # otherwise have been "incomplete" had the run crashed there).
        alt = report_parser.parse_run(d, stage="postroute", run_id=rec.get("run_id"),
                                      flow_name=rec.get("flow_name"),
                                      methodology=rec.get("methodology"))
        alt["result_stage"] = "postroute"
        alt["methodology"] = ((rec.get("methodology") or "") +
                              " [post-route result; post-route phys_opt did not finish]").strip()
        rec.clear()
        rec.update(alt)
        changed = True
    if changed:
        write_json(out_path, runs)
    return runs


def print_results(title, runs, best):
    print(RULE)
    print(title)
    print(RULE)
    for r in sorted([x for x in runs if x.get("wns") is not None],
                    key=lambda x: x["wns"], reverse=True):
        print("  %-30s WNS %+8.3f  WHS %s" % (
            r.get("run_id", "?"), r["wns"],
            "n/a" if r.get("whs") is None else "%+8.3f" % r["whs"]))
    missing = [r.get("run_id", "?") for r in runs if r.get("wns") is None]
    if missing:
        print("  WARNING: no timing summary from: %s" % ", ".join(missing))
    print("")
    print("  BEST: %s   WNS %s   WHS %s   -> %s" % (
        best.get("best_run_id"), best.get("wns"), best.get("whs"), best.get("verdict")))
    floor = best.get("floor")
    if floor:
        print("  vs previous best: %+.3f ns (%s)" % (floor["delta_ns"], floor_verdict(floor)))
    sel = best.get("selection") or {}
    if sel and not sel.get("agree", True):
        print("  selection.mode %s picked %s; %s would pick %s (%s)" % (
            sel["mode"], best.get("best_run_id"), sel["other_mode"], sel["other_pick"],
            sel.get("why", "")))
    print(RULE)


# ---------------------------------------------------------------------------
# Path B (the upstream framework's R2 fix iteration)
# ---------------------------------------------------------------------------
# A Stage 1 run whose own placement already shows failing SLR crossings gets its
# fix while it is still routing: a short job opens its placed checkpoint and
# writes the SLR-aware pblock XDC (R2D_*), then the same flow runs again from the
# input checkpoint with that XDC (R2_*). The R2 runs are ranked with Stage 2; if
# the best beats Stage 1, Stage 2's over-constrain / RQS fixes run again on it
# (S2R2_*). Upstream does the check and the generator inside every Stage 1 run;
# here the check reads the placed run's reports (no Vivado) and the generator is
# a job of its own, so no Stage 1 run is held up.
PATH_B_SCAN_PHASES = ("stage1_ready", "stage1_rerun", "stage1_done", "stage2_detect",
                      "stage2_ready")
# Before Stage 2 starts, Path B runs go on beside the stage without holding it.
PATH_B_SIDE_PHASES = ("stage1_ready", "stage1_rerun", "stage1_done", "stage2_detect")


def path_b_enabled(cfg):
    return as_bool(cfg_get(cfg, "path_b", "enabled", True))


def path_b_rows(state):
    return (state.get("path_b") or {}).get("rows") or []


def _flow_id(flow):
    return FLOW_RUN_ID.get(flow, "S1_" + flow.upper())[3:]


def _xdc_constraints(path):
    """Constraint lines (not comments, not blank) in an XDC."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
    except (IOError, OSError):
        return 0


def path_b_check(run_dir):
    """
    (detected, why): failing SLR crossings in a placed run's own reports -- the
    Partition checker, read at post-place instead of post-route.
    """
    parsed = report_parser.parse_analysis(run_dir, "postplace_physopt")
    res = design_analyzer.check_partition(parsed)
    if res.get("detected"):
        return True, "%d failing SLR-crossing path(s) after placement (%s)" % (
            len(res.get("evidence") or []), res.get("severity"))
    return False, res.get("summary") or "no failing SLR crossings after placement"


def _stage1_closed(state, cfg):
    for r in state.get("stage1_rows") or []:
        if r.get("flow") == "baseline":
            continue
        t = report_parser.parse_timing_summary(
            os.path.join(r["working_dir"], "postroute_physopt_timing_summary.rpt"))
        if t.get("wns") is not None and timing_met(t):
            return True
    return False


def path_b_scan(p, state, cfg):
    """
    Advance Path B one step for every Stage 1 flow. Safe to call any number of
    times; returns the rows it generated (already added to state["path_b"]).
    """
    if not path_b_enabled(cfg) or state.get("phase") not in PATH_B_SCAN_PHASES \
            or state.get("closed_by"):
        return []
    pb = state.setdefault("path_b", {"flows": {}, "rows": []})
    flows = pb.setdefault("flows", {})
    pb.setdefault("rows", [])
    max_flows = int(cfg_get(cfg, "path_b", "max_flows", 3))
    budget = int(cfg_get(cfg, "guardrails", "max_total_runs", 40)) - launched_total(p, state)
    closed = None
    new = []
    for r in state.get("stage1_rows") or []:
        flow, d = r.get("flow"), r["working_dir"]
        if not flow or flow == "baseline":
            continue
        f = flows.get(flow)
        if f is None:
            if (read_json(os.path.join(d, DUPLICATE_MARKER), lenient=True) or {}).get(
                    "duplicate_of"):
                flows[flow] = {"state": "skipped", "why": "identical placement to an "
                                                          "earlier strategy"}
                continue
            if not os.path.isfile(os.path.join(d, POSTPLACE_MARKER)):
                # Only the files' word: without the scheduler's state,
                # run_status reads a job queued past the staleness limit as
                # failed, and on a busy farm that skipped nearly every flow.
                if crash_evidence(d):
                    flows[flow] = {"state": "skipped", "why": "the run failed before "
                                                              "its placed result"}
                continue
            detected, why = path_b_check(d)
            if not detected:
                flows[flow] = {"state": "no_partition", "why": why}
                continue
            if closed is None:
                closed = _stage1_closed(state, cfg)
            started = sum(1 for x in flows.values() if x.get("detect_dir"))
            ckpt = os.path.join(d, "postplace_physopt.dcp")
            skip = ("timing already closed in Stage 1" if closed else
                    "path_b.max_flows (%d) reached" % max_flows if started >= max_flows else
                    "max_total_runs reached" if budget <= 0 else
                    "no placed checkpoint (output.skip_dcp?)" if not os.path.isfile(ckpt)
                    else None)
            if skip:
                flows[flow] = {"state": "skipped", "why": "%s; %s" % (why, skip)}
                continue
            run_id = "R2D_" + _flow_id(flow)
            rdir = os.path.join(p["path_b"], run_id)
            run_script("tcl_gen.py", ["path-b-detect", "--dcp", ckpt, "--run-dir", rdir,
                                      "--config", CONFIG, "--run-name", run_id])
            nr = row(run_id, rdir, kind="path_b_detect", flow=flow,
                     methodology="Path B: SLR pblock XDC from %s's placement"
                                 % FLOW_RUN_ID.get(flow, flow))
            flows[flow] = {"state": "detect", "why": why, "detect_dir": rdir}
            pb["rows"].append(nr)
            new.append(nr)
            budget -= 1
        elif f.get("state") in ("detect", "detect_failed"):
            dd = f["detect_dir"]
            if base_state(dd) != "done":
                if crash_evidence(dd):          # a queued detect job is not failed
                    f["state"] = "detect_failed"
                continue
            xdc = os.path.join(dd, SLR_PBLOCK_XDC)
            n = _xdc_constraints(xdc)
            if not n:
                f.update(state="no_xdc", why=f.get("why", "") + "; the generator wrote no "
                                                                  "constraints")
                continue
            if closed is None:
                closed = _stage1_closed(state, cfg)
            if closed or budget <= 0:
                f.update(state="skipped", why="timing already closed in Stage 1" if closed
                         else "max_total_runs reached")
                continue
            run_id = "R2_" + _flow_id(flow)
            rdir = os.path.join(p["path_b"], run_id)
            run_script("tcl_gen.py", ["stage1", "--flow", flow, "--dcp", state["dcp"],
                                      "--run-dir", rdir, "--config", CONFIG,
                                      "--run-name", run_id, "--fix-xdc", xdc]
                       + replay_argv(p, flow) + hook_argv(state))
            nr = row(run_id, rdir, kind="r2", flow=flow,
                     methodology="Path B (R2): %s again with the SLR pblock XDC from its "
                                 "own placement (%d constraints)"
                                 % (FLOW_RUN_ID.get(flow, flow), n))
            f.update(state="r2", r2_dir=rdir, xdc=xdc)
            pb["rows"].append(nr)
            new.append(nr)
            budget -= 1
    return new


def path_b_unfinished(state, cfg):
    """Path B runs still to finish, and flows whose R2 run is still to be made."""
    busy = [r["run_id"] for r in path_b_rows(state)
            if base_state(r["working_dir"]) not in ("done", "cancelled")
            and run_status(r["working_dir"], cfg)["state"] != "failed"]
    if path_b_enabled(cfg) and state.get("phase") in PATH_B_SCAN_PHASES \
            and not state.get("closed_by"):
        for flow, f in ((state.get("path_b") or {}).get("flows") or {}).items():
            if f.get("state") == "detect" and base_state(f["detect_dir"]) == "done":
                busy.append("R2_%s (to generate)" % _flow_id(flow))
    return busy


def cmd_path_b_scan(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    cfg = load_config()
    before = json.dumps(state.get("path_b"), sort_keys=True)
    new = path_b_scan(p, state, cfg)
    if json.dumps(state.get("path_b"), sort_keys=True) != before:
        write_json(p["state"], state)
    if new:
        for r in new:
            print("Path B: generated %s (%s)" % (r["run_id"], r["methodology"]))
    elif state.get("path_b") and not args.quiet:
        for flow, f in sorted(state["path_b"].get("flows", {}).items()):
            print("Path B: %-16s %-13s %s" % (flow, f.get("state"), f.get("why", "")))
    if args.json:
        print(json.dumps({"generated": [r["run_id"] for r in new]}))
    return 0


def stage2_on_r2(p, state, cfg, runs):
    """
    Once, when the best R2 run beat Stage 1 without closing: the fixed Stage 2
    over-constrain ladder and its own RQS, replaying that R2 run's flow with its
    pblock XDC (upstream's "Stage 2 on R2"). Returns the rows it generated.
    """
    pb = state.get("path_b") or {}
    if pb.get("s2r2") is not None or \
            not as_bool(cfg_get(cfg, "path_b", "stage2_on_r2", True)):
        return []
    r2 = [r for r in runs if str(r.get("run_id", "")).startswith("R2_")
          and r.get("wns") is not None]
    if not r2:
        return []
    best = pick_best_run(r2, selection=load_selection(cfg))
    s1 = read_json(p["stage1_best"], {}) or {}
    pb["s2r2"] = {"from": best.get("run_id"), "wns": best.get("wns"), "rows": []}
    if timing_met(best):
        pb["s2r2"]["why"] = "the R2 run closed timing"
        return []
    if s1.get("wns") is not None and best["wns"] <= s1["wns"]:
        pb["s2r2"]["why"] = "best R2 %+.3f did not beat Stage 1 %+.3f" % (best["wns"], s1["wns"])
        return []
    src = next((r for r in path_b_rows(state)
                if os.path.abspath(r["working_dir"]) == os.path.abspath(best.get("run_dir", ""))),
               None)
    flow = (src or {}).get("flow")
    xdc = ((pb.get("flows") or {}).get(flow) or {}).get("xdc")
    if not flow or not xdc:
        pb["s2r2"]["why"] = "cannot tell which flow/XDC %s replayed" % best.get("run_id")
        return []
    groups = best.get("top_failing_groups") or []
    src_clk = (groups[0].get("src_clk") if groups else None) or "*"
    dst_clk = (groups[0].get("dst_clk") if groups else None) or src_clk
    oc = cfg_get(cfg, "stage2", "over_constrain", {}) or {}
    threshold = float(oc.get("wns_threshold_ps", -200)) / 1000.0
    specs = []
    if best["wns"] >= threshold:
        for m in oc.get("wns_multipliers", [1.5, 2.0, 2.5, 3.0]) or []:
            argv = ["--kind", "over_constrain", "--oc-value",
                    "%.4f" % (abs(best["wns"]) * float(m)), "--oc-clock", src_clk]
            if dst_clk != src_clk:
                argv += ["--oc-clock", dst_clk]
            specs.append(("S2R2_OC_%sx" % str(m).replace(".", "p"), argv,
                          "Stage 2 on R2: over-constrain %sx on %s" % (m, best["run_id"])))
    rqs = report_parser.find_rqs_file(best.get("run_dir", ""), "postroute_physopt")
    if rqs:
        specs.append(("S2R2_RQS", ["--kind", "rqs", "--rqs-file", rqs],
                      "Stage 2 on R2: QoR suggestions of %s" % best["run_id"]))
    budget = int(cfg_get(cfg, "guardrails", "max_total_runs", 40)) - launched_total(p, state)
    gen = RunGenerator(p, cfg, exclude=[p["path_b"]])
    new = []
    for name, argv, meth in specs:
        if budget <= 0:
            break
        rdir = os.path.join(p["path_b"], name)
        full = ["stage2", "--dcp", state["dcp"], "--run-dir", rdir, "--config", CONFIG,
                "--run-name", name, "--parent-flow", flow, "--stack-xdc", xdc] + argv
        full += replay_argv(p, flow) + hook_argv(state)
        if gen.generate(full, rdir):
            continue
        nr = row(name, rdir, kind="s2r2", flow=flow, methodology=meth)
        pb.setdefault("rows", []).append(nr)
        new.append(nr)
        budget -= 1
    pb["s2r2"]["rows"] = [r["run_id"] for r in new]
    if not new:
        pb["s2r2"]["why"] = ("best R2 %+.3f is outside the over-constrain window and "
                             "has no QoR suggestions" % best["wns"])
    return new


def path_b_lines(state):
    """Path B status for the Stage 1 / Stage 2 printouts."""
    out = []
    for flow, f in sorted(((state.get("path_b") or {}).get("flows") or {}).items()):
        if f.get("state") in ("detect", "detect_failed", "r2", "no_xdc"):
            out.append("Path B %s: %s -- %s" % (flow, f["state"], f.get("why", "")))
    return out


# ---------------------------------------------------------------------------
# stage1-collect
# ---------------------------------------------------------------------------
def baseline_result(r):
    """The baseline's routed result (written under the postroute_physopt names)."""
    t = report_parser.parse_timing_summary(
        os.path.join(r["working_dir"], "postroute_physopt_timing_summary.rpt"))
    return {"run_id": r["run_id"], "run_dir": r["working_dir"], "wns": t.get("wns"),
            "whs": t.get("whs"), "tns": t.get("tns"),
            "state": base_state(r["working_dir"])}


def design_checks(state):
    """
    report_methodology and check_timing from the Stage 1 run that hosts them
    (init --design-checks): rule counts by severity, the top rules, and the
    check_timing items that are not zero. None until that run wrote them.
    """
    d = state.get("design_checks_run")
    if not d:
        return None
    meth = report_parser.parse_methodology_summary(os.path.join(d, "methodology.rpt"))
    ct = report_parser.parse_check_timing(os.path.join(d, "postopt_check_timing.rpt"))
    if meth is None and ct is None:
        return None
    out = {"run_dir": d}
    if meth is not None:
        sev_rank = {"Critical Warning": 0, "Error": 0, "Warning": 1, "Advisory": 2}
        top = sorted(meth["rules"], key=lambda r: (sev_rank.get(r["severity"], 3),
                                                    -r["count"]))
        out["methodology"] = {"by_severity": meth["by_severity"], "top_rules": top[:10],
                              "report": os.path.join(d, "methodology.rpt")}
    if ct is not None:
        out["check_timing"] = dict((k, v) for k, v in ct.items() if v)
    return out


def reference_lines(state):
    """Baseline and design-check lines for the Stage 1 printout."""
    lines = []
    b = state.get("stage1_baseline")
    if b:
        if b.get("wns") is not None:
            lines.append("Reference (plain Vivado, not ranked): %s WNS %+.3f  WHS %s" % (
                b["run_id"], b["wns"], "n/a" if b.get("whs") is None else "%+.3f" % b["whs"]))
        else:
            lines.append("Reference %s: no routed result (%s)" % (b["run_id"], b.get("state")))
    c = state.get("design_checks") or {}
    meth = c.get("methodology")
    if meth:
        sev = ", ".join("%s %d" % kv for kv in sorted(meth["by_severity"].items()))
        lines.append("Methodology checks: %s (%s)" % (sev or "none", meth["report"]))
    if c.get("check_timing"):
        lines.append("check_timing: %s" % ", ".join(
            "%s %d" % kv for kv in sorted(c["check_timing"].items())))
    return lines


def run_lost(run_dir, cfg):
    """Ended without a routed checkpoint (so nothing to continue from)."""
    if not os.path.isdir(run_dir) or run_status(run_dir, cfg)["state"] != "failed":
        return False
    return not any(os.path.isfile(os.path.join(run_dir, c))
                   for c in ("postroute.dcp", "postroute_physopt.dcp"))


def duplicates_to_rerun(p, state, rows, cfg):
    """
    Stage 1 runs that stopped as a duplicate of a strategy which then failed
    before routing: that placement is otherwise lost (design C: auto_2 and
    auto_5 of auto_1). One rerun each, within the retry and run budgets.
    """
    max_retries = int(cfg_get(cfg, "guardrails", "max_retries_per_run", 1) or 0)
    budget = int(cfg_get(cfg, "guardrails", "max_total_runs", 40)) - launched_total(p, state)
    out = []
    for r in rows:
        d = r["working_dir"]
        dup = read_json(os.path.join(d, DUPLICATE_MARKER), lenient=True) or {}
        orig = dup.get("duplicate_of")
        if not orig or base_state(d) != "done" or attempts_of(d) >= max_retries:
            continue
        if run_lost(os.path.join(os.path.dirname(d), orig), cfg) and len(out) < budget:
            out.append((r, orig))
    return out


def cmd_stage1_collect(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage1_ready", "stage1_rerun", "stage1_done", "closed_stage1",
                          "stage1_no_result"), "stage1-collect", args)

    rows = state.get("stage1_rows") or stage1_rows_for(p, state.get("stage1_flows", []))
    refuse_if_running(rows, args)
    rerun = [] if state.get("closed_by") else duplicates_to_rerun(p, state, rows, load_config())
    if rerun:
        for r, orig in rerun:
            n, dest = quarantine_run(r["working_dir"])
            state.setdefault("retries", []).append({
                "run_dir": r["working_dir"], "attempt": n,
                "failure_class": "duplicate_of_failed",
                "evidence": ["%s stopped as a duplicate of %s, which then failed without a "
                             "routed checkpoint" % (r["run_id"], orig)],
                "time": int(time.time())})
        # Own phase and row list: the failed originals must not be relaunched.
        state["phase"] = "stage1_rerun"
        state["stage1_rerun_rows"] = [r for r, _ in rerun]
        write_json(p["state"], state)
        print("Re-running %d Stage 1 duplicate(s) whose original failed before routing: %s"
              % (len(rerun), ", ".join("%s (of %s)" % (r["run_id"], o) for r, o in rerun)))
        emit_worklist(p, "STAGE 1 (duplicates of failed runs)", [r for r, _ in rerun],
                      "stage1-collect")
        return 0

    not_started = []
    duplicates = []
    to_parse = []
    for r in rows:
        dup = read_json(os.path.join(r["working_dir"], DUPLICATE_MARKER), lenient=True) or {}
        if dup.get("duplicate_of") and base_state(r["working_dir"]) == "done":
            # Stopped after placement: identical to an earlier strategy (tcl_gen
            # stage1_duplicate_stop_tcl), so it has no result of its own.
            duplicates.append({"run_id": r["run_id"], "duplicate_of": dup["duplicate_of"]})
        elif state.get("stop_on_first_close") and \
                base_state(r["working_dir"]) == "not_started":
            not_started.append(r["run_id"])
        else:
            to_parse.append(r)
    state["stage1_not_started"] = not_started
    state["stage1_duplicates"] = duplicates
    # The baseline is a plain-Vivado reference: reported, never ranked or replayed.
    baseline_rows = [r for r in to_parse if r.get("flow") == "baseline"]
    to_parse = [r for r in to_parse if r.get("flow") != "baseline"]
    if baseline_rows:
        state["stage1_baseline"] = baseline_result(baseline_rows[0])
    checks = design_checks(state)
    if checks:
        state["design_checks"] = checks

    runs = parse_rows(to_parse, p["stage1_runs"])
    run_script("decision_engine.py",
               ["--runs", p["stage1_runs"], "--stage", "S1",
                "--config", CONFIG, "-o", p["stage1_best"]])
    best = read_json(p["stage1_best"], {})

    print_results("STAGE 1 RESULTS -- %s" % state["design"], runs, best)
    if not_started:
        print("  Not launched (stopped after first close): %s" % ", ".join(not_started))
    for d in duplicates:
        print("  %s: stopped after placement -- identical placement to %s"
              % (d["run_id"], d["duplicate_of"]))
    for line in reference_lines(state):
        print("  " + line)
    for r in path_b_scan(p, state, load_config()):
        print("  Path B: generated %s (%s)" % (r["run_id"], r["methodology"]))
    for line in path_b_lines(state):
        print("  " + line)

    if not best.get("num_complete") and not state.get("closed_by"):
        state["phase"] = "stage1_no_result"
        write_json(p["state"], state)
        finish_step(p, "STAGE 1", None,
                    "No Stage 1 run produced a timing summary; nothing to compare.")
        return 0

    prune_checkpoints(p, state, load_config())
    if best.get("verdict") == "CLOSED":
        state["phase"] = "closed_stage1"
        write_json(p["state"], state)
        finish_step(p, "STAGE 1", "finalize",
                    "Timing closed in Stage 1; Stage 2 is not required.")
        return 0

    state["phase"] = "stage1_done"
    write_json(p["state"], state)
    nxt = route_after(p, state, load_config(), "stage1")
    finish_step(p, "STAGE 1", nxt, "Timing not closed." + (
        " stage2.mode=planner: the planner replaces the fixed Stage 2." if nxt == "planner-start"
        else ""))
    return 0


# ---------------------------------------------------------------------------
# stage2-plan
# ---------------------------------------------------------------------------
def directive_argv(fix):
    """tcl_gen.py flags for a directive skill's fix: its sub-directives, and what
    places instead on a part without them (directives.yaml place.directive)."""
    argv = ["--subdirective", fix["subdirective"]]
    if fix.get("directive"):
        argv += ["--directive-off-versal", fix["directive"]]
    return argv


def parent_flow_of(run_dir):
    base = os.path.basename(os.path.normpath(run_dir or ""))
    return base if base in STAGE1_FLOWS else "explore"


def cmd_stage2_plan(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage1_done", "stage2_detect"), "stage2-plan", args)
    if state.get("closed_by") and not args.force:
        return _closed_stage2(p, state)
    best = read_json(p["stage1_best"], {})
    if best.get("verdict") == "CLOSED" and not args.force:
        refuse("Stage 1 closed timing (%s WNS %s WHS %s); Stage 2 not required. "
               "Next step: finalize (or pass --force)."
               % (best.get("best_run_id"), best.get("wns"), best.get("whs")))
    best_dir = best.get("best_run_dir")
    if not best_dir:
        print("ERROR: no Stage 1 winner in %s -- run stage1-collect first."
              % p["stage1_best"], file=sys.stderr)
        sys.exit(1)

    if state.get("phase") == "stage2_detect" and state.get("detect_rows") is not None \
            and not args.force:
        print("Stage 2 already planned; re-emitting the detection worklist "
              "(run.tcl files untouched).")
        if state["detect_rows"]:
            emit_worklist(p, "STAGE 2 DETECTION", state["detect_rows"], "stage2-generate")
        else:
            finish_step(p, "STAGE 2 DETECTION", "stage2-generate",
                        "No detection passes required.")
        return 0

    run_script("design_analyzer.py",
               ["--run-dir", best_dir, "--stage", "postroute_physopt",
                "-o", p["classification"]])
    skip_args = []
    cfg = load_config()
    for sub in cfg_get(cfg, "detection", "skip_sub_problems", []) or []:
        skip_args += ["--skip-sub-problem", sub]
    if as_bool(cfg_get(cfg, "stage2", "enable_partition_rqs", False)):
        skip_args.append("--enable-partition-rqs")
    if as_bool(cfg_get(cfg, "stage2", "cap_skill_runs", False)):
        skip_args.append("--cap-skills")
    ef = cfg.get("early_fixes", {}) or {}
    if as_bool(ef.get("enabled", False)):
        for key, on in ef.items():
            if key != "enabled" and as_bool(on):
                skip_args += ["--early-fix-on", key]
    run_script("skill_resolver.py",
               ["--classification", p["classification"],
                "--best-run", p["stage1_best"],
                "--skills-config", SKILLS_CONFIG,
                "--skills-dir", SKILLS_DIR,
                "-o", p["stage2_plan"]] + skip_args)

    classification = read_json(p["classification"], {})
    plan = read_json(p["stage2_plan"], {})

    print(RULE)
    print("STAGE 2 PLAN -- %s" % state["design"])
    print(RULE)
    print("Classification: %s" % classification.get("summary", ""))
    print("")
    for c in plan.get("categories", []):
        print("  %-12s bucket=%-17s planned=%d" % (
            c["category"], c["bucket"], c["planned_runs"]))
    print("")
    print("Runs planned: %d skill-based + %d fixed-shape = %d total" % (
        plan.get("total_skill_runs", 0), len(plan.get("fixed_runs", [])),
        plan.get("total_runs", 0)))

    detection_plan = plan.get("detection_plan", {})
    rows = []
    for detect_stage, entries in detection_plan.items():
        ckpt_name = DETECT_CHECKPOINT.get(detect_stage)
        if ckpt_name == "__INPUT_DCP__":
            ckpt = state["dcp"]
        else:
            ckpt = os.path.join(best_dir, ckpt_name or "")
        if not os.path.isfile(ckpt):
            print("  SKIP detection stage %s: checkpoint not found (%s)"
                  % (detect_stage, ckpt))
            continue
        run_dir = os.path.join(p["detect"], detect_stage)
        os.makedirs(run_dir, exist_ok=True)
        run_script("tcl_gen.py",
                   ["detect", "--dcp", ckpt, "--run-dir", run_dir,
                    "--detection-plan", p["stage2_plan"],
                    "--detect-stage", detect_stage,
                    "--run-name", "DETECT_" + detect_stage,
                    "--config", CONFIG])
        rows.append(row("DETECT_" + detect_stage, run_dir, detect_stage=detect_stage))

    state["phase"] = "stage2_detect"
    state["stage1_best_dir"] = best_dir
    state["parent_flow"] = parent_flow_of(best_dir)
    state["detect_rows"] = rows
    write_json(p["state"], state)

    if not rows:
        finish_step(p, "STAGE 2 DETECTION", "stage2-generate",
                    "No detection passes required.")
        return 0
    emit_worklist(p, "STAGE 2 DETECTION", rows, "stage2-generate")
    return 0


# ---------------------------------------------------------------------------
# stage2-generate
# ---------------------------------------------------------------------------
def _detect_output_dir(p, detect_stage):
    return os.path.join(p["detect"], detect_stage)


def _find_artifact(p, run, best_dir):
    fix = run.get("fix", {})
    artifact = fix.get("artifact", "")
    candidates = [
        os.path.join(_detect_output_dir(p, run.get("detect_stage", "")), artifact),
        os.path.join(best_dir, artifact),
        fix.get("path", ""),
    ]
    return next((c for c in candidates if artifact and c and os.path.isfile(c)), None)


def _flag_found(p, run, best_dir):
    """
    The run's detection flag, as upstream's sub_detected: detect.tcl wrote it in
    the detection pass (or the parent run). A plan entry without a flag (older
    plans) counts as detected.
    """
    flag = run.get("flag", "")
    if not flag:
        return True
    return any(os.path.isfile(os.path.join(d, flag)) for d in (
        _detect_output_dir(p, run.get("detect_stage", "")), best_dir) if d)


def _actionable(p, run, best_dir):
    """
    One plan entry as an actionable fix (upstream _collect_skill_fixes): an xdc
    fix whose artifact detection produced, or a directive fix whose detection
    flag exists. Returns (fix_argv, why_not); fix_argv None when not actionable.
    """
    fix = run.get("fix", {})
    ftype = fix.get("type")
    if ftype == "xdc":
        found = _find_artifact(p, run, best_dir)
        if not found:
            return None, "detection did not produce %s" % fix.get("artifact", "")
        flag = ("--stack-xdc-read" if fix.get("apply") == "read_xdc" else "--stack-xdc")
        return [flag, found], ""
    if ftype == "directive":
        if not _flag_found(p, run, best_dir):
            return None, "detection did not fire (no %s)" % run.get("flag")
        return directive_argv(fix), ""
    return None, "not a stackable fix (%s)" % ftype


def stack_fixes(p, runs, best_dir):
    """
    Actionable fixes among `runs` as stacked-run argv: every XDC, and one
    placement directive -- the last, as upstream's _launch_combined_run, where
    each directive fix overwrites the subdirective. Returns (argv, used_names,
    actionable_count).
    """
    argv, used, directive, count = [], [], None, 0
    for r in runs:
        fargv, _ = _actionable(p, r, best_dir)
        if fargv is None:
            continue
        count += 1
        if r.get("fix", {}).get("type") == "directive":
            directive = (fargv, r["run_name"])
            continue
        argv += fargv
        used.append(r["run_name"])
    if directive:
        argv += directive[0]
        used.append(directive[1])
    return argv, used, count


def cmd_stage2_generate(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage2_detect", "stage2_ready"), "stage2-generate", args)

    if state.get("phase") == "stage2_ready" and state.get("stage2_rows") is not None \
            and not args.force:
        print("Stage 2 already generated; re-emitting its worklist "
              "(run.tcl files untouched).")
        if state["stage2_rows"] or path_b_rows(state):
            emit_worklist(p, "STAGE 2", state["stage2_rows"] + [
                r for r in path_b_rows(state)
                if base_state(r["working_dir"]) in ("not_started", "missing")],
                "stage2-collect")
        else:
            _stage2_empty(p, state)
        return 0

    if state.get("closed_by") and not args.force:
        return _closed_stage2(p, state)
    plan = read_json(p["stage2_plan"], {})
    best = read_json(p["stage1_best"], {})
    s1_runs = read_json(p["stage1_runs"], [])
    if not plan:
        print("ERROR: no %s -- run stage2-plan first." % p["stage2_plan"],
              file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    dcp = state["dcp"]
    best_wns = best.get("wns")
    best_dir = best.get("best_run_dir", "")
    parent_flow = state.get("parent_flow") or parent_flow_of(best_dir)

    # Clock pair for over-constraining: the worst failing path group of the
    # winner. Fall back to every clock when no group was parsed.
    src_clk = dst_clk = None
    for r in s1_runs:
        if r.get("run_id") == best.get("best_run_id"):
            groups = r.get("top_failing_groups") or []
            if groups:
                src_clk = groups[0].get("src_clk")
                dst_clk = groups[0].get("dst_clk") or src_clk
            break
    oc_clock_note = ""
    if not src_clk:
        src_clk = dst_clk = "*"
        oc_clock_note = ("no failing clock group parsed; over-constraining all "
                         "clocks via [get_clocks *]")

    max_total = int(cfg_get(cfg, "guardrails", "max_total_runs", 40))
    budget = max_total - launched_total(p, state)

    specs = []   # (kind, bucket, run_name, extra_argv, methodology)
    skipped = []

    for r in plan.get("fixed_runs", []):
        if r["kind"] == "over_constrain":
            if best_wns is None:
                skipped.append((r["run_name"], "no WNS parsed from the parent run"))
                continue
            oc_value = abs(best_wns) * float(r["multiplier"])
            argv = ["--oc-value", "%.4f" % oc_value, "--oc-clock", src_clk]
            if dst_clk and dst_clk != src_clk:
                argv += ["--oc-clock", dst_clk]
            specs.append(("over_constrain", "S2_OC", r["run_name"], argv,
                          r["methodology"]))
        elif r["kind"] == "rqs":
            if not os.path.isfile(r.get("rqs_file", "")):
                skipped.append((r["run_name"], "RQS file not found: %s"
                                % r.get("rqs_file")))
                continue
            specs.append(("rqs", "S2_RQS", r["run_name"],
                          ["--rqs-file", r["rqs_file"]], r["methodology"]))

    planned_by_name = dict((r["run_name"], r) for r in plan.get("runs", []))
    if as_bool(cfg_get(cfg, "stage2", "combined_run", False)):
        specs += combined_run_specs(p, plan, best_dir, best_wns, src_clk, dst_clk, cfg,
                                    skipped)
    for r in plan.get("runs", []):
        fix = r.get("fix", {})
        ftype = fix.get("type")
        bucket, run_name = r["bucket"], r["run_name"]

        if ftype == "directive":
            fargv, why = _actionable(p, r, best_dir)
            if fargv is None:
                skipped.append((run_name, why))
                continue
            specs.append(("category_directive", bucket, run_name,
                          fargv, r["methodology"]))

        elif ftype == "xdc":
            found = _find_artifact(p, r, best_dir)
            if not found:
                skipped.append((run_name, "detection did not produce %s"
                                % fix.get("artifact", "")))
                continue
            specs.append(("xdc_fix", bucket, run_name,
                          ["--xdc", found, "--apply-mode", fix.get("apply", "source")],
                          r["methodology"]))

        elif ftype == "rqs":
            # Upstream's <Cat>_RQS: only this category's suggestions.
            if not os.path.isfile(fix.get("rqs_file", "")):
                skipped.append((run_name, "RQS file not found: %s" % fix.get("rqs_file")))
                continue
            argv = ["--rqs-file", fix["rqs_file"]]
            if fix.get("rqs_category"):
                argv += ["--rqs-category", fix["rqs_category"]]
            specs.append(("rqs", bucket, run_name, argv, r["methodology"]))

        elif ftype == "live":
            skipped.append((run_name,
                            "fix applies during detection only (%s); no separate run"
                            % fix.get("tcl", "")))

        elif ftype == "combined":
            # Upstream's <Cat>_Combined: the category's actionable fixes, plus
            # its RQS when it has suggestions; launched only when
            # `all_fixes and (use_rqs or len(all_fixes) > 1)`.
            comps = [planned_by_name[c] for c in fix.get("components", [])
                     if c in planned_by_name]
            argv, used, n_fixes = stack_fixes(p, comps, best_dir)
            rqs_file = fix.get("rqs_file", "")
            use_rqs = fix.get("use_rqs", bool(rqs_file)) and os.path.isfile(rqs_file)
            if not n_fixes:
                skipped.append((run_name, "no actionable %s fix after detection, so "
                                "no combined run" % r.get("category", "")))
                continue
            if not use_rqs and n_fixes < 2:
                skipped.append((run_name, "one actionable fix and no %s RQS, so "
                                "nothing to combine" % r.get("category", "")))
                continue
            if use_rqs:
                argv += ["--rqs-file", rqs_file]
                if fix.get("rqs_category"):
                    argv += ["--rqs-category", fix["rqs_category"]]
            methodology = r["methodology"] + " [stacked: %s]" % ", ".join(used)
            specs.append(("stacked", bucket, run_name, argv, methodology))

        else:
            skipped.append((run_name, "unsupported fix type %r" % ftype))

    inherit = as_bool(cfg_get(cfg, "stage2", "inherit_parent_flow", False))
    gen = RunGenerator(p, cfg, exclude=[p["stage2"]])
    rows = []
    for i, (kind, bucket, run_name, extra, methodology) in enumerate(specs):
        if budget <= 0:
            skipped.append((run_name, "max_total_runs (%d) reached" % max_total))
            continue
        run_dir = os.path.join(p["stage2"], bucket, run_name)
        argv = ["stage2", "--kind", kind, "--dcp", dcp, "--run-dir", run_dir,
                "--config", CONFIG, "--run-name", run_name,
                "--parent-flow", parent_flow if inherit else "explore"] + extra
        if inherit:
            argv += replay_argv(p, parent_flow)
        argv += hook_argv(state)
        why = gen.generate(argv, run_dir)
        if why:
            skipped.append((run_name, why))
            continue
        rows.append(row("%s/%s" % (bucket, run_name), run_dir, methodology=methodology,
                        kind=kind))
        budget -= 1

    print(RULE)
    print("STAGE 2 RUNS GENERATED -- %s" % state["design"])
    print(RULE)
    if oc_clock_note:
        print("  NOTE: %s" % oc_clock_note)
    print("  parent flow: %s (%s)" % (parent_flow, "inherited" if inherit
                                      else "historical explore replay"))
    for r in rows:
        print("  %-40s %s" % (r["run_id"], r["methodology"][:34]))
    if skipped:
        print("")
        print("Not generated:")
        for name, why in skipped:
            print("  %-28s %s" % (name, why))
    print(RULE)

    # Far from closure: a planner round starts now, beside the fixed runs,
    # instead of after them.
    if early_planner_due(p, state, cfg, best) and budget > 0:
        ev = early_planner_evidence(p, state, best)
        if ev:
            rows.append(ev)
            print("  early planner: Stage 1 best %+.3f ns is past %s ps; an evidence run on "
                  "%s starts with Stage 2, and its fixes join it"
                  % (best_wns, cfg_get(cfg, "planner", "early_below_ps", -200),
                     best.get("best_run_id")))

    for r in path_b_scan(p, state, cfg):
        print("  Path B: generated %s (%s)" % (r["run_id"], r["methodology"]))
    state["phase"] = "stage2_ready"
    state["stage2_rows"] = rows
    state["stage2_skipped"] = [{"run_name": n, "reason": w} for n, w in skipped]
    write_json(p["state"], state)

    # Path B runs not yet started join this worklist; running ones are adopted.
    pb_new = [r for r in path_b_rows(state)
              if base_state(r["working_dir"]) in ("not_started", "missing")]
    if not rows and not path_b_rows(state):
        _stage2_empty(p, state)
        return 0
    emit_worklist(p, "STAGE 2", rows + pb_new, "stage2-collect")
    return 0


def combined_run_specs(p, plan, best_dir, best_wns, src_clk, dst_clk, cfg, skipped):
    """
    stage2.combined_run (off by default, as upstream): ONE run with the fixes of
    every detected category in stage2.combined_categories -- their skill XDCs,
    the first placement directive, and their categories' RQS -- instead of one
    category at a time. With combined_oc it runs once per over-constrain
    multiplier instead (upstream's variants).
    """
    selected = set(str(c).strip().lower() for c in cfg_get(
        cfg, "stage2", "combined_categories",
        ["congestion", "clocking", "partition", "timing"]) or [])
    include_rqs = as_bool(cfg_get(cfg, "stage2", "combined_include_rqs", True))
    argv, used, _ = stack_fixes(p, [
        r for r in plan.get("runs", [])
        if str(r.get("category", "")).lower() in selected
        and r.get("fix", {}).get("type") in ("xdc", "directive")], best_dir)
    # RQS straight from the parent's per-category counts (upstream), not from
    # the per-category combined entries.
    rqs_file, rqs_cats = "", []
    if include_rqs and os.path.isfile(plan.get("rqs_file") or ""):
        rqs_cats = [rc for cat, rc in (plan.get("rqs_use") or {}).items()
                    if cat.lower() in selected]
        if rqs_cats:
            rqs_file = plan["rqs_file"]
    if rqs_file:
        argv += ["--rqs-file", rqs_file]
        for c in dict.fromkeys(rqs_cats):
            argv += ["--rqs-category", c]
    if not used and not rqs_file:
        skipped.append(("all_categories_combined",
                        "no actionable fix in the selected categories"))
        return []
    what = ", ".join(used) or "no skill artifacts"
    if rqs_file:
        what += " + RQS %s" % ("/".join(dict.fromkeys(rqs_cats)) or "all")
    variants = [None]
    if as_bool(cfg_get(cfg, "stage2", "combined_oc", False)):
        variants = cfg_get(cfg, "stage2", "combined_oc_multipliers",
                           [1.5, 2.0, 2.5, 3.0]) or []
    specs = []
    for m in variants:
        name, extra = "all_categories_combined", list(argv)
        if m is not None:
            if best_wns is None:
                skipped.append((name + "_OC", "no WNS parsed from the parent run"))
                continue
            name += "_OC_%sx" % str(m).replace(".", "p")
            extra += ["--oc-value", "%.4f" % (abs(best_wns) * float(m)), "--oc-clock", src_clk]
            if dst_clk and dst_clk != src_clk:
                extra += ["--oc-clock", dst_clk]
        specs.append(("stacked", "S2_COMB_ALL", name, extra,
                      "All-category combined: %s%s" % (
                          what, "" if m is None else " + over-constrain %sx" % m)))
    return specs


def _closed_stage2(p, state):
    """
    The design closed (close-design) before Stage 2: no Stage 2 runs. Path B
    rows, the closing R2 among them, are still collected by stage2-collect.
    """
    state["phase"] = "stage2_ready"
    state["stage2_rows"] = []
    write_json(p["state"], state)
    nxt = next_step_for(p, state)
    finish_step(p, "STAGE 2", nxt, "Timing closed by %s: no Stage 2 runs; next: %s."
                % (state["closed_by"].get("run_id"), nxt))
    return 0


def _stage2_empty(p, state):
    """
    No fixed Stage 2 run applies -- WNS outside the over-constraint window, no
    QoR suggestions, detection found nothing. That is exactly when the planner
    is needed, so route on as a finished Stage 2 would; jumping straight to
    finalize skipped the planner on omemaccess (-0.571 ns), the design where it
    had gained 100 ps.
    """
    state["phase"] = "stage2_done"
    write_json(p["state"], state)
    nxt = route_after(p, state, load_config(), "stage2")
    finish_step(p, "STAGE 2", nxt, "No Stage 2 runs could be generated; next: %s." % nxt)


# ---------------------------------------------------------------------------
# stage2-collect
# ---------------------------------------------------------------------------
def overall_best(p):
    """Best of every stage verdict so far, by decision_engine's own rules."""
    cands = []
    for key, stage in (("stage1_best", "S1"), ("stage2_best", "S2"), ("stage3_best", "S3"),
                       ("planner_best", "SP")):
        b = read_json(p[key])
        if b and b.get("wns") is not None:
            cands.append({"run_id": b.get("best_run_id"), "wns": b.get("wns"),
                          "whs": b.get("whs"), "tns": b.get("tns"),
                          "num_failing_setup": b.get("num_failing_setup"),
                          "rqa_score": b.get("rqa_score"),
                          "run_dir": b.get("best_run_dir"), "rqs_file": b.get("rqs_file"),
                          "stage": stage})
    return pick_best_run(cands, selection=load_selection(load_config()))


STAGE2_MODES = ("fixed", "planner", "both")


def stage2_mode(cfg):
    m = str(cfg_get(cfg, "stage2", "mode", "fixed")).strip().lower()
    return m if m in STAGE2_MODES else "fixed"


def route_after(p, state, cfg, finished):
    """
    The single place that decides what follows a collected stage:

      fixed   : Stage 1 -> fixed Stage 2 -> Stage 3 (if eligible) -> finalize
      planner : Stage 1 -> planner round -> Stage 3 (if eligible) -> finalize
      both    : Stage 1 -> fixed Stage 2 -> planner round -> Stage 3 -> finalize
    """
    mode = stage2_mode(cfg)
    cb = state.get("closed_by")
    if cb:
        # Nothing new after close-design. A Path B R2 run that closed beside
        # Stage 1 is ranked by stage2-collect, through an empty Stage 2.
        pb = [os.path.abspath(r["working_dir"]) for r in path_b_rows(state)]
        if finished == "stage1" and os.path.abspath(cb.get("run_dir") or "") in pb:
            return "stage2-plan"
        return "finalize"
    best = overall_best(p)
    if best and timing_met(best):
        return "finalize"
    stage3_left = state.get("stage3_rows") is None and stage3_eligibility(p, cfg)[0]
    if finished == "stage1":
        return "planner-start" if mode == "planner" else "stage2-plan"
    if finished == "stage2":
        if mode == "both" and not state.get("planner"):
            return "planner-start"
        return "stage3-plan" if stage3_left else "finalize"
    if finished == "planner":
        # Another round while rounds keep paying (design B, Ciena took two).
        pl = state.get("planner") or {}
        if pl.get("extend"):
            # A round someone asked for: report it, rather than start more.
            return "stage3-plan" if stage3_left else "finalize"
        rounds = int(cfg_get(cfg, "planner", "max_rounds", 2) or 1)
        floor = (read_json(p["planner_best"], {}) or {}).get("floor") or {}
        if int(pl.get("round") or 0) < rounds and floor.get("improved") \
                and not pl.get("no_fixes"):
            return "planner-start"
        return "stage3-plan" if stage3_left else "finalize"
    return "finalize"


def stage3_eligibility(p, cfg):
    if not as_bool(cfg_get(cfg, "stage3", "enabled", False)):
        return False, "stage3.enabled is false"
    best = overall_best(p)
    if not best:
        return False, "no completed run to start from"
    if timing_met(best):
        return False, "timing already closed"
    gap = float(cfg_get(cfg, "stage3", "max_wns_gap_ps", -150)) / 1000.0
    if best["wns"] < gap:
        return False, ("best WNS %.3f ns is beyond the last-mile window (%.3f ns)"
                       % (best["wns"], gap))
    sel = load_selection(cfg)
    # IDR's own Last Mile gate is RQA >= 3 (UG906); applied with closability only.
    if sel["mode"] == "closability" and best.get("rqa_score") is not None \
            and best["rqa_score"] < int(sel["min_rqa"]):
        return False, ("best run's QoR assessment score %d is below selection.min_rqa %s"
                       % (best["rqa_score"], sel["min_rqa"]))
    return True, "best WNS %.3f ns (%s %s) is within the last-mile window" % (
        best["wns"], best["stage"], best["run_id"])


def cmd_stage2_collect(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage2_ready", "stage2_done"), "stage2-collect", args)
    early = state.get("planner_early")
    if early and early.get("rows") is None and not args.force:
        refuse("the early planner round is still open: apply its plan (planner-apply --early) "
               "or close it (planner-collect --early --no-fixes REASON) first")

    cfg = load_config()
    for r in path_b_scan(p, state, cfg):
        print("Path B: generated %s (%s)" % (r["run_id"], r["methodology"]))
    write_json(p["state"], state)
    busy = path_b_unfinished(state, cfg)
    if busy and not args.force:
        todo = [r for r in path_b_rows(state)
                if base_state(r["working_dir"]) in ("not_started", "missing")]
        if not todo:
            refuse("Path B run(s) still in progress: %s. Stage 2 is collected with them."
                   % ", ".join(busy))
        emit_worklist(p, "PATH B (collected with Stage 2)", todo, "stage2-collect")
        return 0

    rows = state.get("stage2_rows") or []
    refuse_if_running(rows, args)
    # The early round's evidence run and the Path B detect jobs have no timing of
    # their own; R2 and Stage-2-on-R2 runs are ranked with Stage 2.
    runs = parse_rows([r for r in rows if r.get("kind") != "planner_evidence"] +
                      [r for r in path_b_rows(state) if r.get("kind") in ("r2", "s2r2")],
                      p["stage2_runs"])
    if runs:
        run_script("decision_engine.py",
                   ["--runs", p["stage2_runs"], "--stage", "S2",
                    "--floor", p["stage1_best"], "--config", CONFIG,
                    "-o", p["stage2_best"]])
    best2 = read_json(p["stage2_best"], {})
    print_results("STAGE 2 RESULTS -- %s" % state["design"], runs, best2)
    for line in path_b_lines(state):
        print("  " + line)

    s2r2 = [] if state.get("closed_by") else stage2_on_r2(p, state, cfg, runs)
    write_json(p["state"], state)
    prune_checkpoints(p, state, cfg)
    if s2r2:
        emit_worklist(p, "STAGE 2 ON R2", s2r2, "stage2-collect")
        return 0

    state["phase"] = "stage2_done"
    write_json(p["state"], state)
    nxt = route_after(p, state, load_config(), "stage2")
    finish_step(p, "STAGE 2", nxt, {
        "planner-start": "stage2.mode=both: a planner round follows the fixed Stage 2.",
        "stage3-plan": "Stage 3 eligible: %s." % stage3_eligibility(p, load_config())[1],
    }.get(nxt, "Stage 3 not attempted: %s." % stage3_eligibility(p, load_config())[1]))
    return 0


# ---------------------------------------------------------------------------
# stage3-plan / stage3-collect  (F3, opt-in)
# ---------------------------------------------------------------------------
# Stage 3 re-route: the first directive the winner's placement has not been routed
# with. All valid on every family (AlternateCLBRouting is UltraScale-only, UG835).
REROUTE_DIRECTIVES = ["AggressiveExplore", "Explore", "NoTimingRelaxation",
                      "HigherDelayCost", "MoreGlobalIterations"]


def pre_route_lines_of(run_dir):
    """
    The winner's own pre-route reset lines (over-constrain runs only): its placed
    checkpoint still carries the extra uncertainty, so a re-route must replay them.
    Current OC runs write the resets (with the measured original values) to
    oc_reset.tcl in their run dir; the re-route sources that file by absolute path.
    """
    lines, grab = [], False
    for line in _run_tcl_text(run_dir).splitlines():
        if line.strip() == PRE_ROUTE_MARKER:
            grab = True
            continue
        if grab:
            if not line.strip() or line.startswith("#"):
                break
            if OC_RESET in line:
                reset = os.path.join(os.path.abspath(run_dir), OC_RESET)
                if os.path.isfile(reset):
                    lines.append("source {%s}" % reset)
                continue
            lines.append(line)
    return lines


def winner_parent_flow(state, best):
    """
    The Stage 1 flow a winner's run replayed: its own for a Stage 1 winner, the
    planner round's for a planner winner, the Stage 2 one otherwise. (Stage 3
    used to read only state["parent_flow"], which planner mode never sets, so a
    planner winner's RQS run replayed `explore` instead of its `auto_3`.)
    """
    if best["stage"] == "S1":
        return parent_flow_of(best["run_dir"])
    if best["stage"] == "SP" and (state.get("planner") or {}).get("parent_flow"):
        return state["planner"]["parent_flow"]
    return (state.get("parent_flow") or (state.get("planner") or {}).get("parent_flow")
            or "explore")


def cmd_stage3_plan(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage2_done", "planner_done", "stage3_ready"), "stage3-plan", args)
    if state.get("closed_by") and not args.force:
        refuse("timing closed by %s (close-design): no Stage 3; next step: finalize"
               % state["closed_by"].get("run_id"))
    cfg = load_config()

    if state.get("phase") == "stage3_ready" and state.get("stage3_rows") is not None \
            and not args.force:
        print("Stage 3 already planned; re-emitting its worklist.")
        emit_worklist(p, "STAGE 3", state["stage3_rows"], "stage3-collect")
        return 0

    ok, why = stage3_eligibility(p, cfg)
    if not ok and not args.force:
        refuse("Stage 3 not eligible: %s. Next step: finalize (or pass --force)." % why)

    best = overall_best(p)
    wdir = best["run_dir"]
    write_json(p["stage3_floor"], {"best_run_id": best["run_id"], "wns": best["wns"],
                                   "whs": best["whs"], "tns": best["tns"],
                                   "best_run_dir": wdir})
    parent_flow = winner_parent_flow(state, best)

    max_total = int(cfg_get(cfg, "guardrails", "max_total_runs", 40))
    budget = max_total - launched_total(p, state)
    wanted = cfg_get(cfg, "stage3", "runs", ["last_mile", "reroute", "rqs"]) or []
    gen = RunGenerator(p, cfg, exclude=[p["stage3"]])

    rows, skipped = [], []
    for kind in wanted:
        name = "S3_" + kind.upper()
        run_dir = os.path.join(p["stage3"], kind)
        if budget <= 0:
            skipped.append((name, "max_total_runs (%d) reached" % max_total))
            continue
        if kind == "last_mile":
            ckpt = routed_checkpoint(wdir)
            argv = ["stage3", "--kind", "last_mile", "--dcp", ckpt]
            meth = "Post-route phys_opt sweep on %s" % best["run_id"]
        elif kind == "reroute":
            ckpt = os.path.join(wdir, "postplace_physopt.dcp")
            # Routing is deterministic: re-routing this placement with a directive
            # it was already routed with (by the winner or any run sharing its
            # placement) only replays that run.
            directive, tried = dedup.pick_reroute_directive(
                wdir, gen.prior or dedup.run_dirs(p["root"], exclude=[p["stage3"]]),
                cfg_get(cfg, "stage3", "reroute_directives", REROUTE_DIRECTIVES))
            if not directive:
                skipped.append((name, "placement already routed with every "
                                      "stage3.reroute_directives entry (%s)"
                                      % ", ".join(tried)))
                continue
            argv = ["stage3", "--kind", "reroute", "--dcp", ckpt,
                    "--route-directive", directive]
            for cmd in pre_route_lines_of(wdir):
                argv += ["--pre-route-cmd", cmd]
            meth = "Re-route of %s placement with %s%s" % (
                best["run_id"], directive,
                (" (already routed with %s)" % ", ".join(tried)) if tried else "")
        elif kind == "rqs":
            if best["stage"] == "S1":
                skipped.append((name, "winner is from Stage 1, whose RQS was already "
                                      "tried in Stage 2"))
                continue
            ckpt = best.get("rqs_file") or ""
            if not ckpt:
                skipped.append((name, "the winner produced no QoR suggestions file"))
                continue
            argv = ["stage2", "--kind", "rqs", "--dcp", state["dcp"],
                    "--rqs-file", ckpt, "--parent-flow", parent_flow]
            argv += replay_argv(p, parent_flow)
            meth = "Apply RQS suggestions from %s" % best["run_id"]
        else:
            skipped.append((name, "unknown stage3 run kind %r" % kind))
            continue
        if not ckpt or not os.path.isfile(ckpt):
            skipped.append((name, "input not found: %s" % (ckpt or "(none)")))
            continue
        why = gen.generate(argv + ["--run-dir", run_dir, "--config", CONFIG,
                                   "--run-name", name] + hook_argv(state), run_dir)
        if why:
            skipped.append((name, why))
            continue
        rows.append(row(name, run_dir, methodology=meth, kind=kind))
        budget -= 1

    state["phase"] = "stage3_ready"
    state["stage3_rows"] = rows
    state["stage3_skipped"] = [{"run_name": n, "reason": w} for n, w in skipped]
    write_json(p["state"], state)

    print(RULE)
    print("STAGE 3 (last mile) -- %s -- from %s %s (WNS %+.3f)" % (
        state["design"], best["stage"], best["run_id"], best["wns"]))
    print(RULE)
    for n, w in skipped:
        print("  not generated: %-16s %s" % (n, w))
    if not rows:
        state["phase"] = "stage3_done"
        write_json(p["state"], state)
        finish_step(p, "STAGE 3", "finalize", "No Stage 3 runs could be generated.")
        return 0
    emit_worklist(p, "STAGE 3", rows, "stage3-collect")
    return 0


def cmd_stage3_collect(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("stage3_ready", "stage3_done"), "stage3-collect", args)
    rows = state.get("stage3_rows") or []
    refuse_if_running(rows, args)
    runs = parse_rows(rows, p["stage3_runs"])
    if runs:
        run_script("decision_engine.py",
                   ["--runs", p["stage3_runs"], "--stage", "S3",
                    "--floor", p["stage3_floor"], "--config", CONFIG,
                    "-o", p["stage3_best"]])
    best3 = read_json(p["stage3_best"], {})
    print_results("STAGE 3 RESULTS -- %s" % state["design"], runs, best3)
    prune_checkpoints(p, state, load_config())
    state["phase"] = "stage3_done"
    write_json(p["state"], state)
    finish_step(p, "STAGE 3", "finalize", "Stage 3 collected.")
    return 0


# ---------------------------------------------------------------------------
# finalize (+ deterministic verification of the winner)
# ---------------------------------------------------------------------------
def verify_winner(summary, cfg):
    issues = []
    d = summary.get("best_run_dir") or ""
    if not d:
        return {"verified": False, "verify_issues": ["no winning run"]}
    if not os.path.isdir(d):
        return {"verified": False, "verify_issues": ["winning run dir missing: %s" % d]}
    stage = routed_stage(d) or "postroute_physopt"
    rpt = os.path.join(d, "%s_timing_summary.rpt" % stage)
    if not os.path.isfile(rpt):
        issues.append("missing %s" % rpt)
    skip_dcp = as_bool(cfg_get(cfg, "output", "skip_dcp", False))
    ckpt = os.path.join(d, "%s.dcp" % stage)
    if not skip_dcp and not os.path.isfile(ckpt):
        issues.append("missing checkpoint %s" % ckpt)
    if SENTINEL in _run_tcl_text(d) and not os.path.isfile(os.path.join(d, SENTINEL)) \
            and not final_results_written(d) and stage == "postroute_physopt":
        issues.append("run did not complete (no %s in %s)" % (SENTINEL, d))
    if os.path.isfile(rpt):
        t = report_parser.parse_timing_summary(rpt)
        for key in ("wns", "whs"):
            a, b = t.get(key), summary.get(key)
            if a is None or b is None or abs(a - b) > 1e-4:
                issues.append("re-parsed %s %s != reported %s" % (key.upper(), a, b))
        expect = "CLOSED" if timing_met(t) else "NOT_CLOSED"
        if expect != summary.get("verdict"):
            issues.append("verdict %s but re-parsed reports say %s"
                          % (summary.get("verdict"), expect))
    out = {"verified": not issues, "verify_issues": issues}
    # report_route_status after route_design (upstream's root-cause report). A
    # warning, not a failure: post-route phys_opt may still have routed them.
    routes = report_parser.parse_route_status(
        os.path.join(d, "postroute_route_status.rpt")) or {}
    if routes.get("nets with routing errors"):
        out["warnings"] = ["%d net(s) had routing errors after route_design (%s)" % (
            routes["nets with routing errors"],
            os.path.join(d, "postroute_route_status.rpt"))]
    if stage != "postroute_physopt":
        out["verified_stage"] = stage
    return out


# Written into a run whose checkpoints prune_checkpoints removed: it had a
# routed checkpoint (ranking reads this), and tc extend cannot start from it.
PRUNED = "checkpoints.pruned"


def keep_dcps(cfg):
    return str(cfg_get(cfg, "output", "keep_dcps", "winner")).strip().lower()


def _pending_path_b_sources(state, cfg):
    """
    Stage 1 flows whose placed checkpoint a Path B detect job has yet to open:
    its detect job is not through, or the flow has placed and is not scanned yet.
    """
    if not path_b_enabled(cfg) or state.get("closed_by"):
        return set()
    flows = (state.get("path_b") or {}).get("flows") or {}
    keep = set()
    for r in state.get("stage1_rows") or []:
        d = r["working_dir"]
        f = flows.get(r.get("flow"))
        if r.get("flow") == "baseline":
            continue
        if (f is None and os.path.isfile(os.path.join(d, POSTPLACE_MARKER))) or \
                (f is not None and f.get("state") == "detect" and
                 base_state(f.get("detect_dir") or "") != "done"):
            keep.add(os.path.abspath(d))
    return keep


def prune_checkpoints(p, state, cfg, final=False):
    """
    Delete the checkpoints of runs that can no longer be the winner
    (output.keep_dcps: winner), after each collect and at finalize, so the
    flow holds about the best run's checkpoints plus those of runs in flight
    instead of every run's until the end (~1 GB each on design A, 2 a run).

    Kept: the overall best run's (Stage 2 detection, the planner's evidence,
    screen and incremental fixes, Stage 3 and the read-back open them), and,
    before finalize, a Stage 1 flow's placed checkpoint until its Path B detect
    job has run. Before finalize only runs already ranked by a collect, or
    cancelled, are touched: one finished but not yet collected may still win,
    and a failed run's missing checkpoint is what makes its duplicates rerun
    (run_lost). At finalize every other run's go, retired attempt_* included.
    Returns {"count", "bytes"} for this call, and adds it to
    state["checkpoints_pruned"].
    """
    if keep_dcps(cfg) != "winner":
        return None
    best = overall_best(p)
    if not best or not best.get("run_dir"):
        return None                       # no winner yet: nothing can be ruled out
    keep = set([os.path.abspath(best["run_dir"])])
    if not final:
        keep |= _pending_path_b_sources(state, cfg)
    inputs = set(os.path.abspath(d) for d in (state.get("dcp"), state.get("staged_dcp")) if d)
    # Ranked already: a run finished but not yet collected (an R2 run beside
    # Stage 1, ranked with Stage 2) may still turn out to be the best.
    ranked = set()
    for key in ("stage1_runs", "stage2_runs", "stage3_runs", "planner_runs"):
        ranked |= set(os.path.abspath(r.get("run_dir") or "") for r in read_json(p[key], []) or [])
    count = size = 0
    # tc extend --restore rebuilds a checkpoint in <round>/restore: a run too.
    restored = sorted(os.path.abspath(d) for d in glob.glob(os.path.join(p["planner"], "*", "restore"))
                      if os.path.isfile(os.path.join(d, "run.tcl")))
    for d in dedup.run_dirs(p["root"], with_path_b=True) + restored:
        if d in keep:
            dirs = [os.path.join(d, a) for a in os.listdir(d) if a.startswith("attempt_")] \
                if final else []
        else:
            bs = base_state(d)
            # A restore run is no result of its own: once its round is under way
            # (past planner_restore) its checkpoint has been opened by then.
            done_restore = d in restored and bs == "done" and state.get("phase") != "planner_restore"
            if not final and not (bs == "cancelled" or (bs == "done" and d in ranked)
                                  or done_restore):
                continue
            dirs = [d] + ([os.path.join(d, a) for a in os.listdir(d) if a.startswith("attempt_")]
                          if final else [])
        own = 0                           # removed from the run itself, not an attempt_*
        for rd in dirs:
            for f in glob.glob(os.path.join(rd, "*.dcp")):
                if os.path.abspath(f) in inputs:
                    continue
                try:
                    n = os.path.getsize(f)
                    os.remove(f)
                except OSError:
                    continue
                count, size = count + 1, size + n
                own += rd == d
        if own:
            write_json(os.path.join(d, PRUNED), {"utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kept": sorted(keep)})
    total = state.setdefault("checkpoints_pruned", {"count": 0, "bytes": 0})
    total["count"] += count
    total["bytes"] += size
    total["kept"] = os.path.abspath(best["run_dir"])
    write_json(p["state"], state)
    if count:
        print("Checkpoints: removed %d from runs that can no longer win (%.1f GB); kept %s's."
              % (count, size / 1e9, best.get("run_id")))
    return {"count": count, "bytes": size}


def cmd_finalize(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    phase = state.get("phase")
    empty_stage2 = phase == "stage2_ready" and not state.get("stage2_rows")
    if not empty_stage2:
        require_phase(state, ("closed_stage1", "stage1_no_result", "stage2_done",
                              "stage3_done", "planner_done", "complete"), "finalize", args)
    cfg = load_config()

    def report_argv():
        argv = ["--design", state["design"], "--state", p["state"], "--config", CONFIG]
        for flag, key in (("--stage1-runs", "stage1_runs"), ("--stage1-best", "stage1_best"),
                          ("--stage2-runs", "stage2_runs"), ("--stage2-best", "stage2_best"),
                          ("--stage3-runs", "stage3_runs"), ("--stage3-best", "stage3_best"),
                          ("--planner-runs", "planner_runs"), ("--planner-best", "planner_best"),
                          ("--classification", "classification"),
                          ("--plan", "stage2_plan"), ("--verification", "verification")):
            if os.path.isfile(p[key]):
                argv += [flag, p[key]]
        return argv

    if os.path.isfile(p["verification"]):
        os.remove(p["verification"])
    run_script("summary_report.py", report_argv() + ["--json", "-o", p["summary_json"]])
    summary = read_json(p["summary_json"], {})
    verification = verify_winner(summary, cfg)
    write_json(p["verification"], verification)
    prune_checkpoints(p, state, cfg, final=True)
    run_script("summary_report.py", report_argv() + ["--json", "-o", p["summary_json"]])
    run_script("summary_report.py", report_argv() + ["-o", p["summary"]])

    state["phase"] = "complete"
    write_json(p["state"], state)
    write_worklist(p, "COMPLETE", [], None)

    with open(p["summary"], 'r', encoding='utf-8') as f:
        print(f.read())
    print("Report saved to: %s (machine-readable: %s)" % (p["summary"], p["summary_json"]))
    print("")
    print("FLOW COMPLETE. Show the report above to the user.")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
PHASE_NEXT = {
    "probe_ready": ("Build probe generated (opens the input checkpoint with the pinned "
                    "build); Stage 1 waits for it.", "probe-collect"),
    "probe_failed": ("The build probe failed: this build cannot open the input "
                     "checkpoint. Pick another build (vivadoPath) and re-init.", None),
    "unsupported_device": ("The input checkpoint targets a non-Versal device; this flow "
                           "supports AMD Versal only. Nothing was launched.", None),
    "stage1_ready": ("Stage 1 run scripts generated; results not yet collected.",
                     "stage1-collect"),
    "stage1_rerun": ("Re-running Stage 1 duplicates whose original failed before routing.",
                     "stage1-collect"),
    "stage1_no_result": ("No Stage 1 run produced a timing summary.", None),
    "stage1_done": ("Stage 1 finished, timing not closed.", "stage2-plan"),
    "closed_stage1": ("Timing closed in Stage 1.", "finalize"),
    "stage2_detect": ("Detection run scripts generated; not yet collected.",
                      "stage2-generate"),
    "stage2_ready": ("Stage 2 run scripts generated; results not yet collected.",
                     "stage2-collect"),
    "stage2_done": ("Stage 2 finished.", "finalize"),
    "stage3_ready": ("Stage 3 run scripts generated; results not yet collected.",
                     "stage3-collect"),
    "stage3_done": ("Stage 3 finished.", "finalize"),
    "planner_evidence": ("Planner evidence run generated; a planner must write plan.json "
                         "(see planner-context), then planner-apply.", "planner-plan"),
    "planner_screen": ("Planner plan accepted; its fixes are being screened on the winner's "
                       "checkpoint (planner.screen) before any runs.", "planner-plan"),
    "planner_ready": ("Planner fix runs generated; results not yet collected.",
                      "planner-collect"),
    "planner_done": ("Planner round finished.", "finalize"),
    "planner_restore": ("Re-running the run tc extend starts from, to rebuild its removed "
                        "checkpoint (--restore); the round follows.", "planner-restore-collect"),
    "complete": ("Flow complete.", None),
}


def next_step_for(p, state):
    phase = state.get("phase", "?")
    nxt = PHASE_NEXT.get(phase, ("", None))[1]
    if phase == "stage2_ready" and state.get("stage2_rows") == [] \
            and not path_b_rows(state):
        nxt = "finalize"
    if phase == "stage1_done":
        nxt = route_after(p, state, load_config(), "stage1")
    if phase == "stage2_done":
        nxt = route_after(p, state, load_config(), "stage2")
    if phase == "planner_done":
        nxt = route_after(p, state, load_config(), "planner")
    return nxt


def on_timing_closed(cfg):
    m = str(cfg_get(cfg, "guardrails", "on_timing_closed", "kill_all")).strip().lower()
    return m if m in ON_TIMING_CLOSED else "kill_all"


# Rows with no timing of their own: they can never close the design.
NO_RESULT_KINDS = ("path_b_detect", "planner_evidence", "planner_screen")


def closing_candidate(rows):
    """
    The first finished row of the current worklist that meets timing and would
    be ranked: not the baseline (reported, never ranked), not a detect, evidence
    or screen job, not a duplicate stopped after placement.
    """
    for r in rows:
        d = r["working_dir"]
        if r.get("kind") in NO_RESULT_KINDS or r.get("flow") == "baseline" or \
                base_state(d) != "done" or _is_detect_dir(d) or \
                os.path.isfile(os.path.join(d, DUPLICATE_MARKER)):
            continue
        t = report_parser.parse_timing_summary(
            os.path.join(d, "postroute_physopt_timing_summary.rpt"))
        if t.get("wns") is not None and timing_met(t):
            return {"run_id": r["run_id"], "run_dir": os.path.abspath(d),
                    "wns": t.get("wns"), "whs": t.get("whs")}
    return None


def current_rows(state):
    """The rows status reports: the phase's worklist, plus Path B while it runs."""
    key = ROW_PHASES.get(state.get("phase"))
    rows = list(state.get(key) or []) if key else []
    if state.get("phase") in PATH_B_SCAN_PHASES:
        rows += path_b_rows(state)
    return rows


def cmd_close_design(args):
    """
    A run met timing: nothing more is started for this design (as upstream's
    agent kills its siblings once one closes). Every unfinished run of the
    current worklist and Path B gets run.cancelled -- with on_timing_closed
    kill_all the running ones too, with cancel_pending only those not started
    or still queued (--queued). The baseline and the design-checks run are
    references: once running they finish (upstream exempts them too); not yet
    started or still queued, they are cancelled with the rest. The supervisor kills the jobs;
    generation steps refuse or no-op on state["closed_by"].
    """
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    cfg = load_config()
    if state.get("closed_by"):
        print(json.dumps({"closed_by": state["closed_by"], "cancelled": []}))
        return 0
    mode = on_timing_closed(cfg)
    rows = current_rows(state)
    run_dir = os.path.abspath(args.run_dir)
    cand = closing_candidate([r for r in rows if os.path.abspath(r["working_dir"]) == run_dir])
    if not cand:
        refuse("%s is not a finished run of the current worklist that meets timing" % run_dir)
    queued = set(os.path.abspath(d) for d in args.queued or [])
    checks = os.path.abspath(state.get("design_checks_run") or "/nonexistent")
    cancelled = []
    utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for r in rows:
        d = os.path.abspath(r["working_dir"])
        bs = base_state(d)
        if d == run_dir or bs in ("done", "cancelled", "missing"):
            continue
        if (d == checks or r.get("flow") == "baseline") and bs != "not_started" \
                and d not in queued:
            continue
        if mode == "cancel_pending" and bs != "not_started" and d not in queued:
            continue
        if mode != "cancel_pending" and run_status(d, cfg, state, p)["state"] == "failed":
            continue
        write_json(os.path.join(d, CANCELLED), {"reason": "design closed timing",
                                                "closed_by": cand["run_id"], "utc": utc})
        cancelled.append({"run_id": r["run_id"], "working_dir": d, "was": bs})
    state["closed_by"] = dict(cand, utc=utc, mode=mode)
    state.setdefault("cancelled_runs", []).extend(
        {"run_id": c["run_id"], "reason": "closed by %s" % cand["run_id"]} for c in cancelled)
    write_json(p["state"], state)
    print(json.dumps({"closed_by": state["closed_by"], "cancelled": cancelled}))
    return 0


def cmd_status(args):
    p = paths_for(args.output_dir)
    state = read_json(p["state"])
    if state is None:
        if args.json:
            print(json.dumps({"output_dir": p["root"], "phase": None,
                              "next_step": "init", "needs_user": False,
                              "pending_runs": []}, indent=2))
        else:
            print("No run in progress at %s. Start with `orchestrate.py init`."
                  % p["root"])
        return 0
    phase = state.get("phase", "?")
    desc = PHASE_NEXT.get(phase, ("Unknown phase %r." % phase, None))[0]
    nxt = next_step_for(p, state)
    cfg = load_config()

    pending = []
    key = ROW_PHASES.get(phase)
    def with_state(r):
        rr = dict(r)
        bs = base_state(r["working_dir"])
        rr["done"] = bs == "done"
        rr["cancelled"] = bs == "cancelled"
        # A run that already failed is settled: relaunching it in place would start
        # Vivado on top of its old outputs (run.failed, hs_err). A retry goes through
        # prepare-retry, which moves them to attempt_<n>/ first. A cancelled run
        # (close-design) is settled too: nothing starts it again.
        rr["settled"] = rr["cancelled"] or (
            not rr["done"] and run_status(r["working_dir"], cfg)["state"] == "failed")
        return rr

    for r in (state.get(key) or []) if key else []:
        pending.append(with_state(r))
    # Path B runs: beside Stage 1 without holding it ("side"), then part of Stage 2.
    if phase in PATH_B_SCAN_PHASES:
        for r in path_b_rows(state):
            rr = with_state(r)
            rr["side"] = phase in PATH_B_SIDE_PHASES
            pending.append(rr)
    # A finished run met timing and nothing has been stopped for it yet: the
    # supervisor runs close-design with it (guardrails.on_timing_closed).
    closing = None
    if not state.get("closed_by") and on_timing_closed(cfg) != "off":
        closing = closing_candidate([r for r in pending if r["done"]])
    closure = dict({"closed_by": state["closed_by"]} if state.get("closed_by") else {},
                   **({"closing_run": closing} if closing else {}))

    # The run's live page, when the engine started one (tc web; <output_dir>/web.json).
    web_url = (read_json(os.path.join(p["root"], "web.json"), {}) or {}).get("web_url")

    if args.json and getattr(args, "compact", False):
        final = read_json(p["summary_json"], {}) or {}
        print(json.dumps(dict({
            "phase": phase, "next_step": nxt,
            "needs_user": nxt is None and phase != "complete",
            "description": desc,
            "pending_runs": [{"run_id": r["run_id"], "working_dir": r["working_dir"],
                              "done": r["done"], "settled": r["settled"],
                              "cancelled": r["cancelled"],
                              "side": bool(r.get("side"))} for r in pending],
            "final_verdict": final.get("verdict"), "verified": final.get("verified"),
        }, **dict(closure, **({"web_url": web_url} if web_url else {}))),
            separators=(",", ":")))
        return 0

    if args.json:
        final = read_json(p["summary_json"], {}) or {}
        payload = {
            "design": state.get("design"),
            "output_dir": state.get("output_dir"),
            "phase": phase,
            "description": desc,
            "next_step": nxt,
            "needs_user": nxt is None and phase != "complete",
            "stage1_verdict": (read_json(p["stage1_best"], {}) or {}).get("verdict"),
            "stage2_verdict": (read_json(p["stage2_best"], {}) or {}).get("verdict"),
            "stage3_verdict": (read_json(p["stage3_best"], {}) or {}).get("verdict"),
            "final_verdict": final.get("verdict"),
            "verified": final.get("verified"),
            "pending_runs": pending,
            "skipped": (state.get("stage2_skipped") or []) + (state.get("stage3_skipped") or []),
            "stop_on_first_close": bool(state.get("stop_on_first_close")),
            "counts": {
                "stage1_flows": len(state.get("stage1_flows") or []),
                "stage2_rows": len(state.get("stage2_rows") or []),
                "stage3_rows": len(state.get("stage3_rows") or []),
                "retries": len(state.get("retries") or []),
                "launched_total": launched_total(p, state),
                "max_total_runs": int(cfg_get(cfg, "guardrails", "max_total_runs", 40)),
            },
        }
        payload.update(closure)
        if state.get("cancelled_runs"):
            payload["cancelled_runs"] = state["cancelled_runs"]
        if web_url:
            payload["web_url"] = web_url
        print(json.dumps(payload, indent=2))
        return 0

    print("Design    : %s" % state.get("design"))
    print("Output dir: %s" % state.get("output_dir"))
    print("Phase     : %s -- %s" % (phase, desc))
    if pending:
        print("Runs in the current worklist:")
        for r in pending:
            print("  %-28s %-5s %s" % (r["run_id"], "done" if r["done"] else "", r["working_dir"]))
    print("next step: %s" % (nxt or "none"))
    return 0


# ---------------------------------------------------------------------------
# run-status / wait-run / prepare-retry
# ---------------------------------------------------------------------------
def _context_for(run_dir, output_dir=None):
    od = output_dir or _find_output_dir(run_dir)
    if od and os.path.isfile(os.path.join(od, "state.json")):
        p = paths_for(od)
        return p, read_json(p["state"])
    return None, None


def cmd_run_status(args):
    cfg = load_config()
    p, state = _context_for(args.run_dir, args.output_dir)
    st = run_status(args.run_dir, cfg, state, p, args.session_exit, args.session_gone,
                    args.lsf_status, getattr(args, "job_state", None))
    print(json.dumps(st, indent=2))
    return 0


def cmd_wait_run(args):
    cfg = load_config()
    deadline = time.time() + max(0, args.timeout)
    dirs = [os.path.abspath(d) for d in args.run_dir]
    while True:
        states = dict((d, base_state(d)) for d in dirs)
        done = [d for d, s in states.items() if s == "done"]
        if (args.mode == "any" and done) or len(done) == len(dirs):
            break
        if time.time() >= deadline:
            break
        time.sleep(max(1, min(args.poll, deadline - time.time())))
    out = []
    for d in dirs:
        p, state = _context_for(d)
        out.append(run_status(d, cfg, state, p))
    print(json.dumps(out[0] if len(out) == 1 else out, indent=2))
    return 0


def quarantine_run(run_dir):
    """Move a run's outputs into attempt_<n>/ so it can run again; -> (n, dest)."""
    n = attempts_of(run_dir) + 1
    dest = os.path.join(run_dir, "attempt_%d" % n)
    os.makedirs(dest)
    for e in os.listdir(run_dir):
        if e in RUN_INPUTS or e.startswith("attempt_"):
            continue
        shutil.move(os.path.join(run_dir, e), os.path.join(dest, e))
    return n, dest


def probe_write(directory, mb=4096):
    """Write and delete an mb-sized file in directory; -> (ok, message).

    `df` does not show the project quota, so a real write is the only test that
    space is back. It must be bigger than the largest checkpoint a run writes:
    500 MB passed while a 1.1 GB design A write_checkpoint failed (2026-10-01)."""
    path = os.path.join(directory, ".space_probe_%d" % os.getpid())
    block = b"\0" * (1 << 20)
    try:
        with open(path, "wb") as f:
            for _ in range(mb):
                f.write(block)
            f.flush()
            os.fsync(f.fileno())
        return True, "wrote %d MB in %s" % (mb, directory)
    except OSError as e:
        return False, "probe write failed in %s: %s" % (directory, e)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def cmd_prepare_retry(args):
    cfg = load_config()
    p, state = _context_for(args.run_dir, args.output_dir)
    st = run_status(args.run_dir, cfg, state, p, args.session_exit, args.session_gone,
                    args.lsf_status, getattr(args, "job_state", None))
    # disk_full is not retried automatically (a retry would fail the same way), but
    # once space is back the caller can ask for one; the probe write confirms it.
    if (args.space_ok and st["state"] == "failed" and st["failure_class"] == "disk_full"
            and st["launched_total"] < st["max_total_runs"]):
        ok, msg = probe_write(st["working_dir"], args.probe_mb)
        if not ok:
            refuse("disk_full retry refused: " + msg)
        st["retry_eligible"] = st["attempts"] < int(
            cfg_get(cfg, "guardrails", "max_retries_per_run", 1) or 0)
        st["evidence"] = st["evidence"] + [msg]
    if not st["retry_eligible"]:
        refuse("run is not retry-eligible: state=%s failure_class=%s attempts=%d "
               "launched_total=%d/%d" % (st["state"], st["failure_class"], st["attempts"],
                                         st["launched_total"], st["max_total_runs"]))
    run_dir = st["working_dir"]
    n, dest = quarantine_run(run_dir)
    if p and state is not None:
        state.setdefault("retries", []).append({
            "run_dir": run_dir, "attempt": n, "failure_class": st["failure_class"],
            "evidence": st["evidence"][:3], "time": int(time.time())})
        write_json(p["state"], state)
    print(json.dumps({"working_dir": run_dir, "attempt": n,
                      "quarantined_to": dest, "failure_class": st["failure_class"]}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# relocate (F6)
# ---------------------------------------------------------------------------
def cmd_relocate(args):
    p = paths_for(args.output_dir)
    state = read_json(p["state"])
    if state is None:
        print("ERROR: no state.json in %s" % p["root"], file=sys.stderr)
        sys.exit(1)
    swaps = []
    old_out = state.get("output_dir")
    if old_out and os.path.realpath(old_out) != os.path.realpath(p["root"]):
        swaps.append((old_out.rstrip("/"), p["root"]))
    old_skill = args.old_skill_root or state.get("skill_root")
    if old_skill and os.path.realpath(old_skill) != os.path.realpath(SKILL_ROOT):
        swaps.append((old_skill.rstrip("/"), SKILL_ROOT))
    if args.dcp:
        new_dcp = os.path.abspath(args.dcp)
        if not os.path.isfile(new_dcp):
            print("ERROR: checkpoint not found: %s" % new_dcp, file=sys.stderr)
            sys.exit(1)
        swaps.append((state["dcp"], new_dcp))
    if not swaps:
        print("Nothing to relocate: paths already match %s." % p["root"])
        return 0

    changed = 0
    for root, dirs, files in os.walk(p["root"]):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in files:
            if not (n.endswith(".json") or n == "run.tcl"):
                continue
            fn = os.path.join(root, n)
            with open(fn, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
            new = text
            for old, repl in swaps:
                new = new.replace(old, repl)
            if new != text:
                tmp = fn + ".tmp"
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.write(new)
                os.replace(tmp, fn)
                changed += 1
    state = read_json(p["state"])
    state["output_dir"] = p["root"]
    state["skill_root"] = SKILL_ROOT
    write_json(p["state"], state)
    for old, repl in swaps:
        print("  %s\n    -> %s" % (old, repl))
    print("Relocated: rewrote %d file(s) under %s." % (changed, p["root"]))
    return 0


# ---------------------------------------------------------------------------
# planner: evidence-driven Stage 2 (an agent proposes, this script decides)
# ---------------------------------------------------------------------------
def _no_round_msg(state):
    pe = state.get("planner_early")
    if pe and pe.get("rows") is None:
        return ("no normal planner round has started; the round that is open is the EARLY "
                "one beside Stage 2 -- add --early (or answer decision planner.early)")
    return "no planner round started (run planner-start)"


PLANNER_START_PHASES = ("stage1_done", "stage2_done", "stage3_done", "planner_done",
                        "complete")


def _pl_key(early):
    return "planner_early" if early else "planner"


def _planner_round_dir(p, state, early=False):
    if early:
        return os.path.join(p["planner"], "early")
    return os.path.join(p["planner"], "r%d" % state["planner"]["round"])


def early_planner_due(p, state, cfg, s1best):
    """
    Start a planner round alongside Stage 2? When the Stage 1 best is further
    off than planner.early_below_ps: there the fixed Stage 2 is weakest (no
    over-constraint ladder past -200 ps, skills often find nothing), and the
    planner otherwise waits a whole stage to start. On the suites the early
    planner was the best planner result for design C (+524 ps, closed),
    Barco_3screen (+180), dense_similarity (+68) and Barco_MVP (+33).
    """
    if not as_bool(cfg_get(cfg, "planner", "early", True)) or state.get("planner_early"):
        return False
    wns = (s1best or {}).get("wns")
    thr = float(cfg_get(cfg, "planner", "early_below_ps", -200)) / 1000.0
    return wns is not None and wns < thr


def early_planner_evidence(p, state, s1best):
    """The early round's evidence run on the Stage 1 winner; its fixes join Stage 2."""
    wdir = s1best.get("best_run_dir")
    ckpt = routed_checkpoint(wdir) if wdir else ""
    if not ckpt or not os.path.isfile(ckpt):
        return None
    ev_dir = os.path.join(p["planner"], "early", "evidence")
    run_script("tcl_gen.py", ["planner-evidence", "--dcp", ckpt, "--run-dir", ev_dir,
                              "--config", CONFIG, "--run-name", "PLANNER_EVIDENCE_EARLY"])
    state["planner_early"] = {
        "round": "early", "parent_flow": parent_flow_of(wdir), "rows": None,
        "winner": {"stage": "S1", "run_id": s1best.get("best_run_id"), "run_dir": wdir,
                   "wns": s1best.get("wns"), "whs": s1best.get("whs"),
                   "tns": s1best.get("tns"), "rqs_file": s1best.get("rqs_file") or "",
                   "checkpoint": ckpt}}
    return row("PLANNER_EVIDENCE_EARLY", ev_dir, kind="planner_evidence")


def winner_chain(p, cfg, winner_dir):
    """The winner's skill/planner XDC files to replay first, when carrying is on."""
    if not winner_dir or not as_bool(cfg_get(cfg, "planner", "carry_winner_xdc", True)):
        return []
    return xdc_chain.chain(winner_dir, p["root"])


def _planner_ctx(p, state, cfg, early=False):
    pl = state.get(_pl_key(early)) or {}
    w = pl.get("winner") or {}
    rd = _planner_round_dir(p, state, early)
    ev_rqs = os.path.join(rd, "evidence", "ev_rqs", "evidence.rqs")
    rqs = w.get("rqs_file") if w.get("rqs_file") and os.path.isfile(w.get("rqs_file")) \
        else (ev_rqs if os.path.isfile(ev_rqs) else "")
    s1 = read_json(p["stage1_runs"], []) or []
    clocks = None
    for r in s1:
        if r.get("run_dir") == w.get("run_dir"):
            g = (r.get("top_failing_groups") or [])
            if g and g[0].get("src_clk"):
                clocks = [g[0]["src_clk"]] + ([g[0]["dst_clk"]] if g[0].get("dst_clk") and
                                               g[0]["dst_clk"] != g[0]["src_clk"] else [])
    max_total = int(state.get("max_total_runs") or
                    cfg_get(cfg, "guardrails", "max_total_runs", 40))
    budget = max_total - launched_total(p, state)
    ckpt = w.get("checkpoint") if w.get("checkpoint") and os.path.isfile(w["checkpoint"]) \
        else ""
    # A round someone asked for (extend) is not held to planner.max_fixes: its
    # fixes are what they asked to try. The run budget still holds.
    cap = budget if pl.get("extend") else min(int(cfg_get(cfg, "planner", "max_fixes", 4)),
                                                budget)
    return {"winner_wns": w.get("wns"), "rqs_file": rqs, "default_clocks": clocks,
            "winner_checkpoint": ckpt,
            "winner_xdc_chain": winner_chain(p, cfg, w.get("run_dir")),
            "placers": stage1_flow_names(state),
            "max_fixes": max(0, cap),
            "budget_remaining": budget,
            # From the build probe; None when it did not run (nothing is refused then).
            "architecture": (state.get("probe_result") or {}).get("architecture")}


def stage1_flow_names(state):
    """The Stage 1 flows this run swept, which a fix may name as placers."""
    out = []
    for f in state.get("stage1_flows") or []:
        name = f.get("flow") if isinstance(f, dict) else f
        if name and name in STAGE1_FLOWS and name != "baseline":
            out.append(name)
    return out


def cmd_planner_start(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    if state.get("closed_by") and not args.force:
        refuse("timing closed by %s (close-design): no planner round; next step: finalize"
               % state["closed_by"].get("run_id"))
    if state.get("phase") == "planner_evidence" and not args.force:
        print("Planner round already started; re-emitting the evidence worklist.")
        emit_worklist(p, "PLANNER EVIDENCE", state["planner_evidence_rows"], "planner-plan")
        return 0
    pe = state.get("planner_early")
    if pe and pe.get("rows") is None:
        # Not even with --force: it would leave Stage 2 uncollected and the
        # early round undecidable (2026-10-08, omemaccess).
        refuse("the early planner round is still open beside Stage 2. Answer it "
               "(decision planner.early, or planner-apply --early --plan ...) or close it "
               "(planner-collect --early --no-fixes REASON); planner-start comes after "
               "stage2-collect.")
    require_phase(state, PLANNER_START_PHASES, "planner-start", args)
    best = overall_best(p)
    if not best:
        refuse("no completed run to plan from")
    if timing_met(best) and not args.force:
        refuse("timing already closed by %s %s (WNS %s); nothing to plan (or pass --force)"
               % (best["stage"], best["run_id"], best["wns"]))
    ckpt = routed_checkpoint(best["run_dir"])
    if not os.path.isfile(ckpt):
        refuse("winner checkpoint not found: %s" % ckpt)
    pl = state.get("planner") or {}
    rnd = int(pl.get("round", 0)) + 1
    parent = parent_flow_of(best["run_dir"]) if best["stage"] == "S1" else \
        state.get("parent_flow") or "explore"
    state["planner"] = {"round": rnd, "prev_phase": state.get("phase"),
                        "parent_flow": parent,
                        "winner": {"stage": best["stage"], "run_id": best["run_id"],
                                   "run_dir": best["run_dir"], "wns": best["wns"],
                                   "whs": best["whs"], "tns": best["tns"],
                                   "rqs_file": best.get("rqs_file") or "",
                                   "checkpoint": ckpt}}
    ev_dir = os.path.join(p["planner"], "r%d" % rnd, "evidence")
    run_script("tcl_gen.py", ["planner-evidence", "--dcp", ckpt, "--run-dir", ev_dir,
                              "--config", CONFIG, "--run-name", "PLANNER_EVIDENCE_R%d" % rnd])
    rows = [row("PLANNER_EVIDENCE_R%d" % rnd, ev_dir)]
    state["planner_evidence_rows"] = rows
    state["planner_rows"] = None
    state["phase"] = "planner_evidence"
    write_json(p["state"], state)
    print("Planner round %d from %s %s (WNS %+.3f)." % (rnd, best["stage"], best["run_id"],
                                                       best["wns"]))
    emit_worklist(p, "PLANNER EVIDENCE", rows, "planner-plan")
    return 0


EXTEND_PHASES = PLANNER_START_PHASES + ("closed_stage1",)


def find_finished_run(p, ref):
    """A run with a result, by run id or folder (explore, S1_EXPLORE, oc_2.0x, 1_x, a path)."""
    want = os.path.abspath(ref) if os.sep in ref else None
    for key, stage in (("stage1_runs", "S1"), ("stage2_runs", "S2"), ("stage3_runs", "S3"),
                       ("planner_runs", "SP")):
        for r in read_json(p[key], []) or []:
            rd = r.get("run_dir") or ""
            names = {r.get("run_id"), os.path.basename(os.path.normpath(rd)),
                     "S1_" + str(r.get("run_id") or "").upper()}
            if ref in names or (want and os.path.abspath(rd) == want):
                if r.get("wns") is None:
                    refuse("%s has no timing result to start from" % ref)
                return {"run_id": r.get("run_id"), "run_dir": rd, "wns": r.get("wns"),
                        "whs": r.get("whs"), "tns": r.get("tns"),
                        "rqs_file": r.get("rqs_file") or "", "stage": stage}
    refuse("no finished run %r in this flow" % ref)


def cmd_extend(args):
    """
    Add work to a run that exists, instead of starting a new flow.

    Every result so far stays; one more planner round is opened from the
    overall best (or --from a run of your choice):
      --plan FILE   the fixes to try, in the planner's plan format (fixes may
                    name placers and early_fixes): applied at once, no
                    evidence run;
      (no --plan)   an evidence run on the starting point, then the round's
                    plan comes from whoever answers the planner decision (the
                    agent, the headless judge, or the rule plan).
      --restore     the starting run's checkpoint was removed (keep_dcps:
                    winner): re-run its own run.tcl first -- Vivado gives the
                    same result for the same input, commands and build -- then
                    go on as above from the rebuilt checkpoint.
    Without its checkpoint a run can still be extended with flow,
    over-constrain and RQS fixes: they re-place from the input checkpoint,
    replaying the run's settings. Only incremental fixes and the evidence run
    open the run's own routed checkpoint.
    The round is collected like any planner round and the report is rebuilt;
    it does not start further rounds by itself.
    """
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    phase = state.get("phase")
    if phase not in EXTEND_PHASES:
        refuse("extend adds work to a run between stages or finished; this one is at %s. "
               "Let it reach a stage boundary (or finish), then extend it." % phase)
    pe = state.get("planner_early")
    if pe and pe.get("rows") is None:
        refuse("the early planner round is still open beside Stage 2; extend after it")
    base = find_finished_run(p, args.from_run) if args.from_run else overall_best(p)
    if not base:
        refuse("no finished run to extend from")
    cfg = load_config()
    ckpt = routed_checkpoint(base["run_dir"])
    pruned = os.path.isfile(os.path.join(base["run_dir"], PRUNED))
    restore = bool(getattr(args, "restore", False)) and not os.path.isfile(ckpt)
    pl = state.get("planner") or {}
    rnd = int(pl.get("round", 0)) + 1
    parent = parent_flow_of(base["run_dir"]) if base["stage"] == "S1" else \
        state.get("parent_flow") or "explore"
    new = copy.deepcopy(state)
    # An extension is work someone asked for on a closed design: the close no
    # longer stops it (and a run of the new round may close it again).
    if new.get("closed_by"):
        new.setdefault("closed_before", []).append(new.pop("closed_by"))
    if args.max_runs:
        new["max_total_runs"] = launched_total(p, state) + int(args.max_runs)
    new["planner"] = {"round": rnd, "prev_phase": phase, "parent_flow": parent,
                      "winner": {"stage": base["stage"], "run_id": base["run_id"],
                                 "run_dir": base["run_dir"], "wns": base["wns"],
                                 "whs": base["whs"], "tns": base["tns"],
                                 "rqs_file": base.get("rqs_file") or "",
                                 "checkpoint": ckpt if os.path.isfile(ckpt) else ""},
                      "extend": {"from": base["run_id"], "plan": bool(args.plan),
                                 "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}
    new.setdefault("extensions", []).append(
        {"round": rnd, "from": base["run_id"], "stage": base["stage"],
         "kind": "plan" if args.plan else "planner", "utc": new["planner"]["extend"]["utc"]})
    rd = os.path.join(p["planner"], "r%d" % rnd)
    restore_dir = os.path.join(rd, "restore")
    dst = None
    if args.plan:
        plan, err = _load_plan(args.plan)
        ctx = _planner_ctx(p, new, cfg)
        if restore:                     # the checkpoint it will have once rebuilt
            ctx["winner_checkpoint"] = routed_checkpoint(restore_dir)
        res = ({"ok": False, "errors": [err]} if err else planner.validate_plan(plan, ctx))
        if not res["ok"]:
            print(json.dumps(res, indent=2))
            if pruned and any("no routed checkpoint" in e for e in res["errors"]):
                print("%s's checkpoints were removed once it could no longer win "
                      "(output.keep_dcps: winner). Flow, over-constrain and RQS fixes do not "
                      "need them; for incremental fixes add --restore (re-runs %s first to "
                      "rebuild its checkpoint) or extend from the winner."
                      % (base["run_id"], base["run_id"]), file=sys.stderr)
            print("ERROR: plan rejected (%d problem(s)); nothing was changed."
                  % len(res["errors"]), file=sys.stderr)
            sys.exit(1)
        os.makedirs(rd, exist_ok=True)
        dst = os.path.join(rd, "plan.json")
        if os.path.abspath(args.plan) != os.path.abspath(dst):
            shutil.copy(args.plan, dst)
    elif not os.path.isfile(ckpt) and not restore:
        refuse("%s has no routed checkpoint for the evidence run%s; add --restore to rebuild "
               "it first (one more run of %s), give --plan with flow / over-constrain / RQS "
               "fixes, or extend from the winner" % (
                   base["run_id"], " (removed once it could no longer win, output.keep_dcps: "
                   "winner)" if pruned else "", base["run_id"]))
    if restore:
        return _extend_restore(p, state, new, base, rd, restore_dir, dst)
    return _extend_round(p, args.output_dir, state, new, base, rd, ckpt, dst)


def _extend_restore(p, state, new, base, rd, restore_dir, plan_dst):
    """
    The starting run's checkpoint was removed: copy its own inputs (run.tcl,
    planner_fix.xdc) into <round>/restore and run them first. Its run.tcl
    writes beside itself, so the copy rebuilds the same checkpoint there.
    planner-restore-collect then goes on with the round from it.
    """
    os.makedirs(restore_dir, exist_ok=True)
    for name in RUN_INPUTS:
        src = os.path.join(base["run_dir"], name)
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(restore_dir, name))
    run_id = "RESTORE_%s" % re.sub(r"[^A-Za-z0-9_]+", "_", str(base["run_id"])).upper()
    rows = [row(run_id, restore_dir, kind="restore",
                methodology="Rebuild %s's checkpoint (its own run.tcl, same input and build)"
                            % base["run_id"])]
    new["planner"]["extend"]["restore"] = {"from": base["run_dir"], "run_dir": restore_dir,
                                           "plan": plan_dst}
    new["planner_restore_rows"] = rows
    new["phase"] = "planner_restore"
    write_json(p["state"], new)
    print("Extending %s: round %d from %s %s (WNS %+.3f), whose checkpoint was removed; "
          "re-running it first to rebuild it." % (state["design"], new["planner"]["round"],
                                                  base["stage"], base["run_id"], base["wns"]))
    emit_worklist(p, "RESTORE", rows, "planner-restore-collect")
    return 0


def cmd_planner_restore_collect(args):
    """The rebuilt checkpoint is there: go on with the extension round from it."""
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    require_phase(state, ("planner_restore",), "planner-restore-collect", args)
    rows = state.get("planner_restore_rows") or []
    refuse_if_running(rows, args)
    pl = state["planner"]
    rs = pl["extend"].get("restore") or {}
    d = rs.get("run_dir") or (rows[0]["working_dir"] if rows else "")
    ckpt = routed_checkpoint(d)
    if base_state(d) != "done" or not os.path.isfile(ckpt):
        st = run_status(d, load_config())
        state["phase"] = "planner_done"
        pl["no_fixes"] = "the restore run did not rebuild the checkpoint (%s)" % st["state"]
        write_json(p["state"], state)
        nxt = route_after(p, state, load_config(), "planner")
        finish_step(p, "RESTORE", nxt, "Restore run %s %s: %s; the round does not start."
                    % (rows[0]["run_id"] if rows else d, st["state"],
                       "; ".join(st.get("evidence")[:2] or [])))
        return 0
    pl["winner"]["checkpoint"] = ckpt
    pl["winner"]["restored_from"] = rs.get("from")
    base = dict(pl["winner"])
    rd = os.path.dirname(d)
    new = copy.deepcopy(state)
    print("Restored %s's checkpoint: %s" % (base["run_id"], ckpt))
    return _extend_round(p, args.output_dir, state, new, base, rd, ckpt, rs.get("plan"))


def _extend_round(p, output_dir, state, new, base, rd, ckpt, plan_dst):
    """The round itself: apply a given plan now, or an evidence run on ckpt first."""
    rnd = new["planner"]["round"]
    if plan_dst:
        new["planner_evidence_rows"] = []
        new["planner_rows"] = None
        new["phase"] = "planner_evidence"
        write_json(p["state"], new)
        print("Extending %s: round %d from %s %s (WNS %+.3f)." % (
            state["design"], rnd, base["stage"], base["run_id"], base["wns"]))
        apply_args = argparse.Namespace(output_dir=output_dir, plan=plan_dst, early=False,
                                        force=False)
        return cmd_planner_apply(apply_args)
    ev_dir = os.path.join(rd, "evidence")
    run_script("tcl_gen.py", ["planner-evidence", "--dcp", ckpt, "--run-dir", ev_dir,
                              "--config", CONFIG, "--run-name", "PLANNER_EVIDENCE_R%d" % rnd])
    rows = [row("PLANNER_EVIDENCE_R%d" % rnd, ev_dir)]
    new["planner_evidence_rows"] = rows
    new["planner_rows"] = None
    new["phase"] = "planner_evidence"
    write_json(p["state"], new)
    print("Extending %s: planner round %d from %s %s (WNS %+.3f); its plan comes from the "
          "planner decision." % (state["design"], rnd, base["stage"], base["run_id"], base["wns"]))
    emit_worklist(p, "PLANNER EVIDENCE", rows, "planner-plan")
    return 0


def in_flight_runs(state, cfg):
    """Runs generated, queued or running (not finished, failed or cancelled)."""
    rows = []
    for key in ("stage2_rows", "planner_rows_all", "stage3_rows"):
        rows += state.get(key) or []
    rows += path_b_rows(state)
    pe = state.get("planner_early") or {}
    rows += pe.get("rows") or []
    out, seen = [], set()
    for r in rows:
        d = os.path.abspath(r["working_dir"])
        if d in seen or r.get("kind") in NO_RESULT_KINDS:
            continue
        seen.add(d)
        bs = base_state(d)
        if bs in ("done", "cancelled", "missing") or run_status(d, cfg)["state"] == "failed":
            continue
        out.append({"run_id": r["run_id"],
                    "fix_name": re.sub(r"^\d+_", "", os.path.basename(d)),
                    "kind": r.get("kind"), "methodology": r.get("methodology") or "",
                    "state": "generated" if bs == "not_started" else "queued or running"})
    return out


def cmd_planner_context(args):
    """Everything the planner agent needs, as JSON (read-only)."""
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    early = getattr(args, "early", False)
    pkey = _pl_key(early)
    if not state.get(pkey):
        refuse("no early planner round (Stage 1 was not far enough off)" if early else
               _no_round_msg(state))
    cfg = load_config()
    rd = _planner_round_dir(p, state, early)
    ev = os.path.join(rd, "evidence")
    ctx = _planner_ctx(p, state, cfg, early)
    tried = []
    for key in ("stage1_runs", "stage2_runs", "stage3_runs", "planner_runs"):
        for r in read_json(p[key], []) or []:
            item = {"run_id": r.get("run_id"), "methodology": r.get("methodology") or
                    r.get("flow_name") or "", "wns": r.get("wns"), "whs": r.get("whs")}
            chk = read_json(os.path.join(r.get("run_dir") or "", tcl_xdc_check), None) \
                if r.get("run_dir") else None
            if chk and (chk.get("empty") or chk.get("failed_commands")):
                # Its XDC was only partly applied: judge the result accordingly.
                item["xdc_queries_matching_nothing"] = chk.get("empty", 0)
                item["xdc_commands_failed"] = chk.get("failed_commands", 0)
            tried.append(item)
    out = {
        "round": state[pkey]["round"],
        "winner": state[pkey]["winner"],
        "parent_flow": state[pkey]["parent_flow"],
        "winner_xdc_chain": [{"path": x, "mode": m} for x, m in ctx["winner_xdc_chain"]],
        "winner_xdc_note": ("flow, over_constrain and rqs fixes REPLAY this chain "
                            "automatically before their own XDC, so they build on the "
                            "winner's fixes; do not copy it into xdc_lines"
                            if ctx["winner_xdc_chain"] else ""),
        "design_checks": state.get("design_checks") or {},
        "evidence_dir": ev,
        "evidence_done": base_state(ev) == "done",
        "evidence_files": sorted(os.path.join(ev, f) for f in os.listdir(ev)
                                 if f.startswith("ev") or f == "evidence.json")
        if os.path.isdir(ev) else [],
        "plan_path": os.path.join(rd, "plan.json"),
        "rqs_file_available": ctx["rqs_file"],
        "default_over_constrain_clocks": ctx["default_clocks"],
        "max_fixes": ctx["max_fixes"],
        "budget_remaining": ctx["budget_remaining"],
        "already_tried": tried,
        "in_flight": in_flight_runs(state, cfg),
        "in_flight_note": ("runs generated, queued or running now: a fix that would "
                           "repeat one of them, or any run in already_tried, is not run "
                           "(planner-validate lists it under duplicates); propose "
                           "something else"),
        "part": (state.get("probe_result") or {}).get("part"),
        "architecture": ctx["architecture"],
        "fix_kinds": {
            "flow": "params: opt_directive, "
                    + ("place_directive | place_subdirective, "
                       if not ctx["architecture"] or is_versal(ctx["architecture"])
                       else "place_directive (place_subdirective is Versal-only; not on "
                            "this %s part), " % ctx["architecture"])
                    + "physopt_directive, route_directive, xdc_lines (physical-only XDC: "
                    + ", ".join(sorted(planner.XDC_COMMANDS)) + "; set_property only for "
                    + ", ".join(sorted(planner.XDC_PROPERTIES)) + ")",
            "over_constrain": "params: uncertainty_ns (0<x<=1) | multiplier (0.5..4 x |WNS|); "
                              "optional clocks [src] or [src,dst]. The run checks the user "
                              "uncertainty really rose (a design clock-pair uncertainty "
                              "outranks a per-clock one; it then re-applies as a pair) and "
                              "fails fast otherwise",
            "rqs": "params: categories subset of " + ", ".join(planner.RQS_CATEGORIES),
            "incremental": "starts from the WINNER'S ROUTED checkpoint (no re-place; ~1-3 h "
                           "instead of a full flow). params: xdc_lines (group_path only), "
                           "path_groups [names] -> phys_opt_design -path_groups, "
                           "physopt_directives [up to 4], route_directive, passes 1..4",
        },
        "group_path": "allowed in flow and incremental xdc_lines: user groups need -name "
                      "<ident> plus -from/-to/-through (e.g. group_path -name wall -to "
                      "[get_pins ...]); a whole clock may be prioritised with group_path "
                      "-name [get_clocks X] -weight 2 (clock groups only, UG835); -default "
                      "is refused. It changes optimisation priority, never the measured slack. "
                      "Keep groups SMALL: list the failing endpoint pins themselves (e.g. "
                      "-to [get_pins {a/b_reg/D c/d_reg/D ...}] from evidence paths[].endpoint), "
                      "not whole hierarchies -- on design B, groups of 155k-187k cells raised "
                      "Vivado 12-4439 and wrecked full-flow placement (flow fixes may not use "
                      "-hierarchical in group_path). When WNS is pinned while TNS falls, the "
                      "worst few endpoints are the target: evidence paths[] gives their "
                      "src/dst LOC and clock region.",
        "winner_checkpoint": ctx["winner_checkpoint"],
        "forbidden": "timing exceptions and constraint changes (set_false_path, "
                     "set_multicycle_path, set_max/min_delay, clock definitions/groups/"
                     "uncertainty in XDC) and any Tcl escape (exec, source, file, eval ...)",
        "schema": planner.__doc__.split("Plan schema (schema_version 1):", 1)[1]
                  .split("Requires Python", 1)[0].strip(),
    }
    if early:
        out["early_round"] = ("This round runs IN PARALLEL with the fixed Stage 2 runs, "
                              "from the Stage 1 winner: its fixes join Stage 2 and compete "
                              "with them. Aim at causes the fixed menu cannot reach "
                              "(placement structure, clock roots, SLR assignment, fanout).")
    print(json.dumps(out, indent=2))
    return 0


def _load_plan(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f), None
    except (IOError, OSError) as e:
        return None, "cannot read plan: %s" % e
    except ValueError as e:
        return None, "plan is not valid JSON: %s" % e


def _chain_argv(ctx):
    """
    Fixes that re-place from the input checkpoint replay the winner's own
    skill/planner XDC first, so they build on it instead of dropping it.
    """
    argv = []
    for path, mode in ctx.get("winner_xdc_chain") or []:
        argv += ["--stack-xdc-read" if mode == "read_xdc" else "--stack-xdc", path]
    return argv


def _planner_job(p, state, ctx, fx, placer, run_dir, parent, chain_argv):
    """
    tcl_gen.py argv for one planner fix run (a fix naming placers is one run per
    placer, replaying that Stage 1 flow) -> (argv, carried the winner's XDC chain).
    Writes the fix's planner_fix.xdc into run_dir when it has xdc_lines.
    """
    flow = placer or parent
    argv = ["stage2", "--dcp", state["dcp"], "--run-dir", run_dir, "--config", CONFIG,
            "--run-name", "SP_%s" % (fx["name"] + ("_" + placer if placer else "")),
            "--parent-flow", flow]
    argv += replay_argv(p, flow)
    if placer:
        argv.append("--inherit-parent")
    for k, v in sorted((fx.get("early_fixes") or {}).items()):
        argv += ["--early-fix", "%s=%d" % (k, 1 if v else 0)]
    carried = False
    if fx["kind"] == "over_constrain":
        argv += ["--kind", "over_constrain", "--oc-value", "%.4f" % fx["oc_value"]]
        for c in fx["clocks"]:
            argv += ["--oc-clock", c]
        argv += chain_argv
        carried = bool(chain_argv)
    elif fx["kind"] == "rqs":
        argv += ["--kind", "rqs", "--rqs-file", ctx["rqs_file"]]
        for c in fx["categories"]:
            argv += ["--rqs-category", c]
        argv += chain_argv
        carried = bool(chain_argv)
    elif fx["kind"] == "incremental":
        # From the winner's routed checkpoint, not the input DCP.
        argv[argv.index("--dcp") + 1] = ctx["winner_checkpoint"]
        argv += ["--kind", "incremental", "--passes", str(fx["passes"])]
        for g in fx["path_groups"]:
            argv += ["--path-group", g]
        for d in fx["physopt_directives"]:
            argv += ["--incr-physopt", d]
        if fx.get("route_directive"):
            argv += ["--route-directive", fx["route_directive"]]
        if fx.get("xdc_lines"):
            xdc = os.path.join(run_dir, "planner_fix.xdc")
            with open(xdc, 'w', encoding='utf-8') as f:
                f.write("# planner fix %s (validated: group_path only)\n" % fx["name"])
                f.write("\n".join(fx["xdc_lines"]) + "\n")
            argv += ["--stack-xdc", xdc]
    else:
        argv += ["--kind", "custom_flow"]
        for fkey, flag in (("opt_directive", "--opt-directive"),
                           ("place_directive", "--place-directive"),
                           ("place_subdirective", "--place-subdirective"),
                           ("physopt_directive", "--physopt-directive"),
                           ("route_directive", "--route-directive")):
            if fx.get(fkey):
                argv += [flag, fx[fkey]]
        argv += chain_argv
        carried = bool(chain_argv)
        if fx.get("xdc_lines"):
            xdc = os.path.join(run_dir, "planner_fix.xdc")
            with open(xdc, 'w', encoding='utf-8') as f:
                f.write("# planner fix %s (validated physical-only)\n" % fx["name"])
                f.write("\n".join(fx["xdc_lines"]) + "\n")
            argv += ["--stack-xdc", xdc]
    argv += hook_argv(state)
    return argv, carried


def planner_duplicates(p, state, cfg, ctx, fixes, early=False):
    """
    -> [{fix, run, same_as}] for planner fix runs that would only repeat a run
    done, generated, queued or running (planner-role RunGenerator), checked by a
    dry run in a scratch directory. Nothing is written in the flow.
    """
    import tempfile
    rd = _planner_round_dir(p, state, early)
    parent = (state.get(_pl_key(early)) or {}).get("parent_flow") or "explore"
    gen = RunGenerator(p, cfg, exclude=[rd], role="planner")
    chain_argv = _chain_argv(ctx)
    out = []
    scratch = tempfile.mkdtemp(prefix="tca_validate_")
    try:
        for fx in fixes:
            for placer in fx.get("placers") or [None]:
                name = fx["name"] + ("_" + placer if placer else "")
                run_dir = os.path.join(scratch, "%d_%s" % (fx.get("rank", 0), name))
                os.makedirs(run_dir)
                argv, _ = _planner_job(p, state, ctx, fx, placer, run_dir, parent, chain_argv)
                why, fp = gen.duplicate(argv)
                if why:
                    out.append({"fix": fx["name"], "run": name, "same_as": why})
                else:
                    gen.batch[fp] = run_dir      # a later fix in this plan repeating it
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return out


def cmd_planner_validate(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    early = getattr(args, "early", False)
    if not state.get(_pl_key(early)):
        refuse("no early planner round" if early else _no_round_msg(state))
    plan, err = _load_plan(args.plan)
    cfg = load_config()
    ctx = _planner_ctx(p, state, cfg, early)
    res = ({"ok": False, "errors": [err], "fixes": [], "dropped": []} if err else
           planner.validate_plan(plan, ctx))
    if res["ok"]:
        # Reported, not errors: planner-apply skips them anyway, and an error
        # would reject the rest of the plan with them.
        res["duplicates"] = planner_duplicates(p, state, cfg, ctx, res["fixes"], early)
        runs_of = {}
        for fx in res["fixes"]:
            runs_of[fx["name"]] = len(fx.get("placers") or [None])
        dup_runs = {}
        for d in res["duplicates"]:
            dup_runs[d["fix"]] = dup_runs.get(d["fix"], 0) + 1
        gone = [n for n, k in dup_runs.items() if k >= runs_of.get(n, 1)]
        if getattr(args, "prune", False) and gone:
            plan["fixes"] = [f for f in plan.get("fixes") or [] if f.get("name") not in gone]
            for i, f in enumerate(sorted(plan["fixes"], key=lambda f: f.get("rank", 0)), 1):
                f["rank"] = i
            write_json(args.plan, plan)
            res["pruned"] = gone
            res["fixes"] = [f for f in res["fixes"] if f["name"] not in gone]
            if not res["fixes"]:
                res["pruned_all"] = True
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


def cmd_planner_apply(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    early = getattr(args, "early", False)
    key = _pl_key(early)
    if early:
        if not state.get(key):
            refuse("no early planner round (Stage 1 was not far enough off)")
        if state[key].get("rows") is not None and not args.force:
            print("Early planner fixes already generated; re-emitting the Stage 2 worklist.")
            emit_worklist(p, "STAGE 2", state.get("stage2_rows") or [], "stage2-collect")
            return 0
        require_phase(state, ("stage2_ready",), "planner-apply --early", args)
    else:
        pe = state.get("planner_early")
        if pe and pe.get("rows") is None and not state.get("planner"):
            refuse(_no_round_msg(state))
        if state.get("phase") == "planner_ready" and state.get("planner_rows") is not None \
                and not args.force:
            print("Planner fixes already generated; re-emitting the worklist.")
            emit_worklist(p, "PLANNER", state["planner_rows"], "planner-collect")
            return 0
        require_phase(state, ("planner_evidence", "planner_screen"), "planner-apply", args)
    rd = _planner_round_dir(p, state, early)
    given = (state.get(key) or {}).get("extend", {}).get("plan")
    if state.get("closed_by") and not args.force:
        refuse("timing closed by %s (close-design): no planner fixes are generated"
               % state["closed_by"].get("run_id"))
    if not given and run_status(os.path.join(rd, "evidence"), load_config())["state"] != "done" \
            and not args.force:
        refuse("planner evidence run has not completed")
    cfg = load_config()
    ctx = _planner_ctx(p, state, cfg, early)
    plan, err = _load_plan(args.plan)
    res = ({"ok": False, "errors": [err], "fixes": [], "dropped": []} if err else
           planner.validate_plan(plan, ctx))
    if not res["ok"]:
        print(json.dumps(res, indent=2))
        print("ERROR: plan rejected (%d problem(s)); fix plan.json and re-run planner-apply."
              % len(res["errors"]), file=sys.stderr)
        sys.exit(1)
    dst_plan = os.path.join(rd, "plan.json")
    if os.path.abspath(args.plan) != os.path.abspath(dst_plan):
        shutil.copy(args.plan, dst_plan)

    fixes, screened, screen_dropped = res["fixes"], {}, []
    smode = screen.mode_of(cfg)
    if smode != "off" and not early and not given:
        out = screen_step(p, state, cfg, rd, dst_plan, fixes, smode, args)
        if isinstance(out, int):
            return out          # the screen job was generated; apply again once it is through
        fixes, screened, screen_dropped = out

    pl = state[key]
    parent = pl["parent_flow"]
    tag = "early" if early else "r%d" % pl["round"]
    chain = ctx.get("winner_xdc_chain") or []
    chain_argv = _chain_argv(ctx)
    rows, skipped = [], [{"run_name": n, "reason": "beyond max_fixes/budget"}
                         for n in res["dropped"]] + \
        [{"run_name": n, "reason": why} for n, why in screen_dropped]
    gen = RunGenerator(p, cfg, exclude=[rd], role="planner")
    budget = ctx.get("budget_remaining")
    # A fix that names placers becomes one run per Stage 1 flow named, each
    # replaying that flow (placer, opt directive, early fixes) with the fix added.
    jobs = [(fx, pl_name) for fx in fixes for pl_name in (fx.get("placers") or [None])]
    for fx, placer in jobs:
        name = fx["name"] + ("_" + placer if placer else "")
        if budget is not None and len(rows) >= budget:
            skipped.append({"run_name": name, "reason": "beyond the run budget (%d left)" % budget})
            continue
        run_dir = os.path.join(rd, "%d_%s" % (fx["rank"], name))
        os.makedirs(run_dir, exist_ok=True)
        argv, carried = _planner_job(p, state, ctx, fx, placer, run_dir, parent, chain_argv)
        why = gen.generate(argv, run_dir)
        if why:
            skipped.append({"run_name": name, "reason": why})
            try:
                os.rmdir(run_dir)  # only if still empty
            except OSError:
                pass
            continue
        meth = "Planner%s: %s" % (" (early)" if early else "", fx["justification"][:100])
        if placer:
            meth += " [on %s]" % placer
        if fx.get("early_fixes"):
            meth += " [early fixes: %s]" % ", ".join(
                "%s=%s" % (k, "on" if v else "off") for k, v in sorted(fx["early_fixes"].items()))
        if carried:
            meth += " [+ winner XDC chain: %d file(s)]" % len(chain)
        meth += screen.tag(screened.get(fx["name"]))
        rows.append(row("SP/%s/%s" % (tag, name), run_dir, methodology=meth,
                        kind=fx["kind"]))

    if early:
        # The early round's fixes join the running Stage 2 and are collected
        # with it.
        pl["rows"] = rows
        pl["skipped"] = skipped
        state["stage2_rows"] = (state.get("stage2_rows") or []) + rows
        write_json(p["state"], state)
        print("Early planner plan accepted: %d fix run(s) join Stage 2%s." % (
            len(rows), (", %d not generated" % len(skipped)) if skipped else ""))
        for x in skipped:
            print("  not generated: %-24s %s" % (x["run_name"], x["reason"]))
        emit_worklist(p, "STAGE 2", state["stage2_rows"], "stage2-collect")
        return 0

    state["planner_rows"] = rows
    state["planner_rows_all"] = (state.get("planner_rows_all") or []) + rows
    state["planner_skipped"] = skipped
    state["phase"] = "planner_ready"
    write_json(p["state"], state)
    print("Planner plan accepted: %d fix run(s)%s." % (
        len(rows), (", %d not generated" % len(skipped)) if skipped else ""))
    for s in skipped:
        print("  not generated: %-24s %s" % (s["run_name"], s["reason"]))
    if not rows:
        state["phase"] = "planner_done"
        write_json(p["state"], state)
        nxt = route_after(p, state, load_config(), "planner")
        finish_step(p, "PLANNER", nxt, "No planner runs to execute; next: %s." % nxt)
        return 0
    emit_worklist(p, "PLANNER", rows, "planner-collect")
    return 0


def screen_step(p, state, cfg, rd, plan_path, fixes, mode, args):
    """
    planner.screen: the first apply of a plan generates one read-only job on the
    winner's routed checkpoint (phase planner_screen) and returns 0; the next,
    once that job is through (done or failed), returns (fixes to run, results by
    fix name, [(dropped name, why)]). A failed screen drops nothing.
    """
    pl = state["planner"]
    with open(plan_path, "rb") as fh:
        sig = hashlib.sha1(fh.read()).hexdigest()[:10]
    sc = pl.get("screen") or {}
    sdir = os.path.join(rd, "screen_%s" % sig)
    if sc.get("plan_sha") != sig:
        name = "PLANNER_SCREEN_R%d" % pl["round"]
        max_paths = int(cfg_get(cfg, "planner", "screen_max_paths", screen.DEFAULT_MAX_PATHS))
        screen.generate(pl["winner"]["checkpoint"], fixes, sdir, name, max_paths)
        pl["screen"] = {"plan_sha": sig, "dir": sdir, "mode": mode}
        rows = [row(name, sdir, kind="planner_screen")]
        state["planner_screen_rows"] = rows
        state["phase"] = "planner_screen"
        write_json(p["state"], state)
        print("Planner plan accepted; screening its %d fix(es) on %s first (planner.screen %s)."
              % (len(fixes), pl["winner"]["run_id"], mode))
        emit_worklist(p, "PLANNER SCREEN", rows, "planner-plan")
        return 0
    st = run_status(sdir, cfg)["state"]
    if st not in ("done", "failed") and not args.force:
        refuse("the planner screen (%s) is still running (%s)" % (sdir, st))
    rqs = report_parser.parse_rqs_counts(
        report_parser.report_paths(pl["winner"]["run_dir"], "postroute_physopt")["rqs"],
        report_parser.RQS_STAGE_FILTER.get("postroute_physopt"))
    raw = screen.read_raw(sdir)
    results = screen.assess(fixes, raw, rqs)
    data = screen.write_result(sdir, results, mode, raw)
    kept, dropped = screen.keep(fixes, results, mode)
    pl["screen"].update({"complete": data["complete"], "failing_paths": data["failing_paths"],
                         "verdicts": dict((r["name"], r["verdict"]) for r in results),
                         "dropped": [n for n, _ in dropped]})
    print("Planner screen (%s, %d failing path(s)%s):" % (
        mode, data["failing_paths"], "" if data["complete"] else "; the job did not finish, "
        "nothing dropped"))
    for r in results:
        print("  %-28s %-8s %s" % (r["name"], r["verdict"], r["why"]))
    return kept, dict((r["name"], r) for r in results), dropped


def cmd_planner_collect(args):
    p = paths_for(args.output_dir)
    state = load_state(p, args)
    if getattr(args, "early", False):
        # Only closing is needed: early fixes are collected with Stage 2.
        if not getattr(args, "no_fixes", None):
            refuse("early planner fixes are collected by stage2-collect; use "
                   "planner-collect --early --no-fixes REASON to close the round")
        if not state.get("planner_early"):
            refuse("no early planner round")
        state["planner_early"]["rows"] = []
        state["planner_early"]["no_fixes"] = args.no_fixes
        write_json(p["state"], state)
        print("Early planner round closed without fixes: %s" % args.no_fixes)
        emit_worklist(p, "STAGE 2", state.get("stage2_rows") or [], "stage2-collect")
        return 0
    if getattr(args, "no_fixes", None):
        # A round with no plan still has to end, or the flow sits at
        # planner-plan forever (or a supervisor stops there).
        require_phase(state, ("planner_evidence", "planner_screen"), "planner-collect --no-fixes", args)
        state["phase"] = "planner_done"
        state.setdefault("planner", {})["no_fixes"] = args.no_fixes
        write_json(p["state"], state)
        nxt = route_after(p, state, load_config(), "planner")
        finish_step(p, "PLANNER", nxt, "Planner round closed without fixes: %s" % args.no_fixes)
        return 0
    require_phase(state, ("planner_ready", "planner_done"), "planner-collect", args)
    rows = state.get("planner_rows_all") or []
    refuse_if_running(state.get("planner_rows") or [], args)
    runs = parse_rows(rows, p["planner_runs"])
    w = state["planner"]["winner"]
    write_json(p["planner_floor"], {"best_run_id": w["run_id"], "wns": w["wns"],
                                    "whs": w["whs"], "tns": w["tns"],
                                    "best_run_dir": w["run_dir"]})
    if runs:
        run_script("decision_engine.py",
                   ["--runs", p["planner_runs"], "--stage", "SP",
                    "--floor", p["planner_floor"], "--config", CONFIG,
                    "-o", p["planner_best"]])
    print_results("PLANNER RESULTS (round %d) -- %s" % (state["planner"]["round"],
                                                        state["design"]),
                  runs, read_json(p["planner_best"], {}) or {})
    prune_checkpoints(p, state, load_config())
    state["phase"] = "planner_done"
    write_json(p["state"], state)
    nxt = route_after(p, state, load_config(), "planner")
    finish_step(p, "PLANNER", nxt, "Planner round collected." + (
        " Stage 3 last-mile follows from the overall best." if nxt == "stage3-plan" else ""))
    return 0


# ---------------------------------------------------------------------------
# batch-plan (F5)
# ---------------------------------------------------------------------------
def cmd_batch_plan(args):
    """
    Turn a design list into one entry per design. Accepts the TSR .cfg format
    (`name,/path/to/postlogicopt.dcp` per line, # comments) or a JSON list of
    {design, dcp}.
    """
    entries = []
    with open(args.cfg, 'r', encoding='utf-8') as f:
        text = f.read()
    if text.lstrip().startswith("["):
        for e in json.loads(text):
            entries.append((e["design"], e["dcp"]))
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "," not in line:
                continue
            name, dcp = [x.strip() for x in line.split(",", 1)]
            entries.append((name, dcp))
    if args.only:
        entries = [e for e in entries if e[0] in args.only]
    if args.limit:
        entries = entries[:args.limit]
    root = os.path.abspath(args.output_root)
    out = []
    for name, dcp in entries:
        od = os.path.join(root, "%s_timing_closure" % name)
        st = read_json(os.path.join(od, "state.json")) or {}
        out.append({"design": name, "dcp": dcp, "dcp_exists": os.path.isfile(dcp),
                    "output_dir": od, "phase": st.get("phase")})
    print(json.dumps(out, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def use_staged_assets(output_dir):
    """
    A flow tc staged for the farm (state.json's assets_root) generates every
    later run against that copy of the Tcl, also when a step is run without
    TCA_ASSETS_ROOT in the environment (straight from a shell, a restart):
    a farm host cannot read the skill's own folder on the submitting host.
    """
    global SKILLS_DIR
    if os.environ.get(ASSETS_ENV) or not output_dir:
        return
    state = read_json(os.path.join(output_dir, "state.json")) or {}
    staged = state.get("assets_root")
    if staged and os.path.isdir(staged):
        os.environ[ASSETS_ENV] = staged
        SKILLS_DIR = os.path.join(staged, "skills")


def main():
    parser = argparse.ArgumentParser(
        description="Drive the timing-closure flow end to end.")
    # No required=True on add_subparsers: some environments ship an older
    # argparse backport that rejects it. Checked by hand below.
    sub = parser.add_subparsers(dest="command")

    outdir = argparse.ArgumentParser(add_help=False)
    outdir.add_argument("--output-dir", required=True,
                        help="The run's output directory (created by init).")
    outdir.add_argument("--force", action="store_true",
                        help="Override the phase guard / moved-directory check.")

    p_init = sub.add_parser("init", help="Set up the run and generate Stage 1.")
    p_init.add_argument("--dcp", required=True, help="Post-logic-opt checkpoint.")
    p_init.add_argument("--output-dir", help="Where to put everything.")
    p_init.add_argument("--design", help="Design name (default: parent dir of the DCP).")
    p_init.add_argument("--extra-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after open_checkpoint in every run (repeatable).")
    p_init.add_argument("--post-opt-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after opt_design in every run (repeatable).")
    p_init.add_argument("--post-place-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after place_design in every run (repeatable).")
    p_init.add_argument("--post-popt-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after post-place phys_opt_design (repeatable).")
    p_init.add_argument("--post-route-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after route_design in every run (repeatable).")
    p_init.add_argument("--post-route-popt-cmd", action="append", default=[],
                        help="Raw TCL, or a .tcl file to source, after post-route phys_opt_design (repeatable).")
    p_init.add_argument("--stop-on-first-close", action="store_true",
                        help="Skip the remaining Stage 1 strategies once one closes.")
    p_init.add_argument("--force", action="store_true",
                        help="Re-initialise even if the output dir already has a run.")
    p_init.add_argument("--probe", action="store_true",
                        help="Open the checkpoint with the pinned build first (probe/); "
                             "Stage 1 is released by probe-collect.")

    for name, helptext in (
            ("probe-collect", "Check the build probe and release Stage 1."),
            ("stage1-collect", "Parse Stage 1 results and pick the best run."),
            ("stage2-plan", "Classify, arbitrate, and generate detection passes."),
            ("stage2-generate", "Generate the Stage 2 fix runs."),
            ("stage2-collect", "Parse Stage 2 results and pick the best run."),
            ("stage3-plan", "Generate the opt-in Stage 3 last-mile runs."),
            ("stage3-collect", "Parse Stage 3 results."),
            ("finalize", "Render and verify the final report.")):
        sub.add_parser(name, parents=[outdir], help=helptext)

    p_pb = sub.add_parser("path-b-scan", parents=[outdir],
                          help="Path B: generate the R2 runs that are due (safe to repeat).")
    p_pb.add_argument("--json", action="store_true")
    p_pb.add_argument("--quiet", action="store_true")

    p_cd = sub.add_parser("close-design", parents=[outdir],
                          help="A run met timing: cancel this design's other unfinished runs "
                               "(guardrails.on_timing_closed) and start nothing more.")
    p_cd.add_argument("--run-dir", required=True, help="The run that met timing.")
    p_cd.add_argument("--queued", action="append",
                      help="A run whose job the scheduler still has queued (repeatable).")

    p_status = sub.add_parser("status", help="Report flow state and what comes next.")
    p_status.add_argument("--output-dir", required=True)
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--compact", action="store_true",
                          help="With --json: one line, only the fields an executor needs.")

    facts = argparse.ArgumentParser(add_help=False)
    facts.add_argument("--session-exit", type=int,
                       help="last_completed_code reported by the Vivado session.")
    facts.add_argument("--session-gone", action="store_true",
                       help="The Vivado session no longer exists.")
    facts.add_argument("--lsf-status", help="LSF job status/exit reason, e.g. TERM_MEMLIMIT.")
    facts.add_argument("--job-state", choices=["pending", "running"],
                       help="The scheduler still has the job queued or running: never stale.")
    facts.add_argument("--output-dir", help="Flow output dir (default: found from --run-dir).")

    p_rs = sub.add_parser("run-status", parents=[facts],
                          help="Classify one run directory from its files.")
    p_rs.add_argument("--run-dir", required=True)

    p_pr = sub.add_parser("prepare-retry", parents=[facts],
                          help="Quarantine a retry-eligible failed run's outputs.")
    p_pr.add_argument("--run-dir", required=True)
    p_pr.add_argument("--space-ok", action="store_true",
                      help="Also retry a disk_full run, after a probe write shows space is back.")
    p_pr.add_argument("--probe-mb", type=int, default=4096, help=argparse.SUPPRESS)

    p_wr = sub.add_parser("wait-run", help="Sleep until run.done appears (or timeout).")
    p_wr.add_argument("--run-dir", action="append", required=True)
    p_wr.add_argument("--timeout", type=int, default=540,
                      help="Seconds (default 540, under the 10-minute tool limit).")
    p_wr.add_argument("--poll", type=int, default=30)
    p_wr.add_argument("--mode", choices=["all", "any"], default="all")

    p_rl = sub.add_parser("relocate", help="Rewrite paths after moving an output dir.")
    p_rl.add_argument("--output-dir", required=True, help="The directory's NEW location.")
    p_rl.add_argument("--dcp", help="New location of the input checkpoint, if it moved.")
    p_rl.add_argument("--old-skill-root", help="Old skill root (default: from state.json).")

    sub.add_parser("planner-start", parents=[outdir],
                   help="Start a planner round: evidence run on the current winner.")
    p_ex = sub.add_parser("extend", parents=[outdir],
                          help="Add a planner round to a run that exists (finished or between "
                               "stages): --plan to try given fixes now, else an evidence run "
                               "and a planner decision.")
    p_ex.add_argument("--plan", help="plan.json: the fixes to try (planner schema)")
    p_ex.add_argument("--from", dest="from_run",
                      help="start from this run (id or folder) instead of the overall best")
    p_ex.add_argument("--max-runs", type=int,
                      help="allow this many more runs than the guardrail leaves")
    p_ex.add_argument("--restore", action="store_true",
                      help="the starting run's checkpoint was removed: re-run it first to "
                           "rebuild it (needed for incremental fixes and the evidence run)")
    sub.add_parser("planner-restore-collect", parents=[outdir],
                   help="After tc extend --restore: go on with the round from the rebuilt "
                        "checkpoint.")
    p_px = sub.add_parser("planner-context", parents=[outdir],
                          help="Print what the planner agent needs (JSON).")
    p_px.add_argument("--early", action="store_true",
                      help="The early round started with Stage 2.")
    p_pc = sub.add_parser("planner-collect", parents=[outdir],
                          help="Parse planner fix runs against the round's winner.")
    p_pc.add_argument("--early", action="store_true",
                      help="Close the early round (with --no-fixes).")
    p_pc.add_argument("--no-fixes", metavar="REASON",
                      help="Close a round that produced no plan (evidence failed, nothing "
                           "applicable, nobody answered) and route on to Stage 3 / finalize.")
    for name, helptext in (("planner-validate", "Validate a plan.json (no side effects "
                                                "unless --prune)."),
                           ("planner-apply", "Validate a plan.json and generate its runs.")):
        pp = sub.add_parser(name, parents=[outdir], help=helptext)
        pp.add_argument("--plan", required=True)
        pp.add_argument("--early", action="store_true",
                        help="The early round: its fix runs join the running Stage 2.")
        if name == "planner-validate":
            pp.add_argument("--prune", action="store_true",
                            help="Rewrite --plan without the fixes whose every run would "
                                 "repeat one done, generated, queued or running.")

    p_bp = sub.add_parser("batch-plan", help="Expand a design list for batch runs.")
    p_bp.add_argument("--cfg", required=True)
    p_bp.add_argument("--output-root", required=True)
    p_bp.add_argument("--only", action="append", default=[])
    p_bp.add_argument("--limit", type=int)

    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.error("a command is required: init, stage1-collect, stage2-plan, "
                     "stage2-generate, stage2-collect, stage3-plan, stage3-collect, "
                     "finalize, status, run-status, wait-run, prepare-retry, "
                     "relocate, batch-plan")

    handlers = {
        "init": cmd_init,
        "probe-collect": cmd_probe_collect,
        "stage1-collect": cmd_stage1_collect,
        "stage2-plan": cmd_stage2_plan,
        "stage2-generate": cmd_stage2_generate,
        "stage2-collect": cmd_stage2_collect,
        "path-b-scan": cmd_path_b_scan,
        "close-design": cmd_close_design,
        "stage3-plan": cmd_stage3_plan,
        "stage3-collect": cmd_stage3_collect,
        "finalize": cmd_finalize,
        "status": cmd_status,
        "run-status": cmd_run_status,
        "wait-run": cmd_wait_run,
        "prepare-retry": cmd_prepare_retry,
        "relocate": cmd_relocate,
        "batch-plan": cmd_batch_plan,
        "planner-start": cmd_planner_start,
        "extend": cmd_extend,
        "planner-restore-collect": cmd_planner_restore_collect,
        "planner-context": cmd_planner_context,
        "planner-validate": cmd_planner_validate,
        "planner-apply": cmd_planner_apply,
        "planner-collect": cmd_planner_collect,
    }
    use_staged_assets(getattr(args, "output_dir", None))
    return handlers[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
