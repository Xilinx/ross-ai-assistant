#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
watch.py -- what an orchestrating agent needs to stay in charge of a long run.

An IDE agent gets no notification when background work finishes: once it ends
its turn, nothing wakes it. The first run started from VS Code showed it --
the agent started the supervisor, said "it is running", and stopped. Three
pieces close that gap without any runtime of our own:

* `snapshot()` / `wait()` -- one blocking call that returns only when
  something needs the agent: a decision is pending, the phase moved, the
  supervisor stopped, or the run finished. A loop of these is the agent's
  heartbeat; each call is one tool call, not a stream of polls.

* `attach()` / `detach()` -- a lease per run in the workspace saying "an
  agent is orchestrating this run". Without it the stop guard would hold every
  chat in the workspace hostage to a run nobody asked about. Several designs
  can close at once; each attached run keeps its own lease.

* `guard()` -- the Stop-hook verdict. While any attached run is still active,
  ending the turn is blocked with a reason that tells the agent what to do
  next. Rate-limited and wall-clock-limited, so it can never become
  the infinite loop the hook docs warn about.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import glob
import json
import os
import subprocess
import time

from . import decisions, paths

PID_FILE = "serve.pid"
OUTCOME_FILE = "serve_outcome.json"
WAIT_SEEN_FILE = "wait_seen.json"
LEASE_DIR = ".tc"
LEASE_FILE = "attached.json"
GUARD_LOG = "guard.log"
ACTIVITY_FILE = "activity.json"

# How long after its last call an orchestrating agent still counts as there.
# A healthy loop calls `tc wait` (up to 9 min) back to back, so 15 min of
# silence means the chat has stopped -- the lease alone cannot tell: it is
# kept on purpose until the run is reported.
AGENT_ACTIVE_S = 15 * 60

# Guard limits. A healthy loop blocks a stop at most a few times an hour (the
# agent normally calls `tc wait`, which blocks for up to 9 min, instead of
# trying to stop); more than this means the agent is fighting the hook, and a
# person should look.
GUARD_MAX_BLOCKS_PER_HOUR = 6
GUARD_MAX_WALL_HOURS = 72

# Under the shortest per-command limit we know (an agent shell tool capped at
# 10 min); a terminal tool that returns earlier is handled in the skill.
WAIT_DEFAULT_S = 540
WAIT_POLL_S = 20

TERMINAL_PHASES = ("complete",)


def _utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def _iso(dt):
    return dt.isoformat() + "Z"


def _parse_iso(text):
    try:
        return datetime.datetime.strptime(str(text).rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def serve_pid(output_dir):
    """The pid of a live supervisor for this flow, or None."""
    try:
        with open(os.path.join(output_dir, PID_FILE)) as fh:
            pid = int((fh.read() or "").strip())
    except (IOError, OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def _status(output_dir):
    cmd = paths.orchestrate_cmd() + ["status", "--output-dir", output_dir,
                                     "--json", "--compact"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    out, err = proc.communicate()
    if proc.returncode != 0:
        return {"error": (err or out or "").strip()[:300]}
    try:
        return json.loads(out)
    except ValueError:
        return {"error": "status did not print JSON"}


def _best(output_dir):
    """The best routed result so far, from the stage *_best.json files."""
    best = None
    for path in glob.glob(os.path.join(output_dir, "*_best.json")):
        if path.endswith("_floor.json"):
            continue
        rec = _read(path)
        if not isinstance(rec, dict):
            continue
        rec = rec.get("best", rec) if isinstance(rec.get("best"), dict) else rec
        wns = rec.get("wns")
        if wns is None:
            continue
        whs = rec.get("whs") or 0.0
        key = (whs >= 0, wns, rec.get("tns") or 0.0)
        if best is None or key > best[0]:
            best = (key, {"wns": wns, "whs": rec.get("whs"), "tns": rec.get("tns"),
                          "run": rec.get("best_run_id") or rec.get("run_id"),
                          "from": os.path.basename(path).replace("_best.json", "")})
    return best[1] if best else None


def _read(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (IOError, OSError, ValueError):
        return None


def snapshot(output_dir):
    """Everything an orchestrator needs to decide its next move, compactly."""
    output_dir = os.path.abspath(output_dir)
    st = _status(output_dir)
    runs = {"running": 0, "done": 0, "failed": 0, "cancelled": 0}
    for r in st.get("pending_runs") or []:
        if r.get("done"):
            runs["done"] += 1
        elif r.get("cancelled"):
            runs["cancelled"] += 1
        elif r.get("settled"):
            runs["failed"] += 1
        else:
            runs["running"] += 1
    pending = []
    try:
        q = decisions.Queue(output_dir)
        q.expire_due()
        for rec in q.list(state=decisions.PENDING):
            pending.append({"id": rec["id"], "point": rec["point"],
                            "deadline_utc": rec.get("deadline_utc")})
    except Exception as exc:                       # noqa: BLE001 -- report, never crash the loop
        pending = [{"error": str(exc)[:200]}]
    pid = serve_pid(output_dir)
    return {
        "output_dir": output_dir,
        "phase": st.get("phase"),
        "next_step": st.get("next_step"),
        "status_error": st.get("error"),
        "runs": runs,
        "pending_decisions": pending,
        "supervisor_pid": pid,
        "supervisor_alive": pid is not None,
        "outcome": _read(os.path.join(output_dir, OUTCOME_FILE)),
        "best": _best(output_dir),
    }


# Run folders a fast snapshot counts: stage1/<run>, stage2/<recipe>/<run>,
# planner/<round>/<run>, path_b/<run>. attempt_* folders are retired attempts.
_RUN_GLOBS = ("stage1/*/", "stage2/*/*/", "planner/*/*/", "path_b/*/", "stage3/*/", "stage3/*/*/")


def fast_snapshot(output_dir):
    """
    `snapshot()` from the run's own files, without `orchestrate.py status`.

    `snapshot()` starts a Python process per run per poll; across 100 runs a
    `tc wait --all` took 18 minutes to answer (Rubik batch). This reads
    state.json, the decision files and the run folders' markers, which is
    under a millisecond per run. `runs` counts every stage's run folders, not
    only the current stage's as `status` does. It does not expire decisions or compute
    `next_step`: the supervisor expires decisions itself, and an agent that
    wants the next step reads `tc snapshot` for the one run with the event.
    """
    output_dir = os.path.abspath(output_dir)
    st = _read(os.path.join(output_dir, "state.json")) or {}
    runs = {"running": 0, "done": 0, "failed": 0, "cancelled": 0}
    for pat in _RUN_GLOBS:
        for d in glob.glob(os.path.join(output_dir, pat)):
            if os.path.basename(d.rstrip("/")).startswith("attempt_"):
                continue
            if os.path.exists(os.path.join(d, "run.done")):
                runs["done"] += 1
            elif os.path.exists(os.path.join(d, "run.cancelled")):
                runs["cancelled"] += 1
            elif os.path.exists(os.path.join(d, "run.failed")):
                runs["failed"] += 1
            elif os.path.exists(os.path.join(d, "job.json")):
                runs["running"] += 1
    pending = []
    try:
        for rec in decisions.Queue(output_dir).list(state=decisions.PENDING):
            pending.append({"id": rec["id"], "point": rec["point"],
                            "deadline_utc": rec.get("deadline_utc")})
    except Exception as exc:                       # noqa: BLE001 -- report, never crash the loop
        pending = [{"error": str(exc)[:200]}]
    pid = serve_pid(output_dir)
    return {
        "output_dir": output_dir,
        "phase": st.get("phase"),
        "next_step": None,
        "status_error": None if st else "no readable state.json",
        "runs": runs,
        "pending_decisions": pending,
        "supervisor_pid": pid,
        "supervisor_alive": pid is not None,
        "outcome": _read(os.path.join(output_dir, OUTCOME_FILE)),
        "best": _best(output_dir),
    }


# Runs whose stopped supervisor a fast `--all` wait already reported, so the
# next call does not return the same stop again (a dead supervisor with no
# outcome is otherwise invisible to `--all`, which keeps only live runs).
REPORTED_FILE = "wait_reported.json"


def _reported_path(workspace):
    return os.path.join(os.path.abspath(workspace), LEASE_DIR, REPORTED_FILE)


def _stop_marker(output_dir):
    """
    Identifies one particular stop. A clean stop writes a new outcome and
    removes serve.pid; a crash leaves serve.pid behind; a restart rewrites
    serve.pid and removes the old outcome. Either way the pair changes.
    A completed run is reported once for good: its supervisor writes the
    outcome just after the phase turns complete, which would look new.
    """
    if (_read(os.path.join(output_dir, "state.json")) or {}).get("phase") in TERMINAL_PHASES:
        return "complete"

    def mt(name):
        try:
            return "%d" % os.path.getmtime(os.path.join(output_dir, name))
        except OSError:
            return "-"
    return "%s/%s" % (mt(PID_FILE), mt(OUTCOME_FILE))


def fast_active_runs(workspace, dirs):
    """
    The runs a fast `--all` wait watches: every run that has not finished,
    plus one that finished or whose supervisor died, until that has been
    reported once. A run that completes between two calls is otherwise never
    reported: the next call would leave it out as inactive.
    """
    reported = _read(_reported_path(workspace)) or {}
    out = []
    for d in dirs:
        done = (_read(os.path.join(d, "state.json")) or {}).get("phase") in TERMINAL_PHASES
        if (done or serve_pid(d) is None) and reported.get(d) == _stop_marker(d):
            continue
        out.append(d)
    return out


def note_reported_stop(workspace, output_dir):
    path = _reported_path(workspace)
    rec = _read(path) or {}
    rec[os.path.abspath(output_dir)] = _stop_marker(output_dir)
    try:
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            json.dump(rec, fh, indent=1, sort_keys=True)
    except (IOError, OSError):
        pass


STAGE_REPORTS = (("opt", "postopt"), ("place", "postplace"),
                 ("place_physopt", "postplace_physopt"), ("route", "postroute"),
                 ("route_physopt", "postroute_physopt"))
STAGE_ROWS = (("probe", "probe_rows"), ("stage1", "stage1_rows"),
              ("detect", "detect_rows"), ("stage2", "stage2_rows"),
              ("planner", "planner_evidence_rows"), ("planner", "planner_screen_rows"),
              ("planner", "planner_rows_all"),
              ("stage3", "stage3_rows"))
LOG_FILES = ("vivado.log", "vivado_console.log", "lsf.log", "run.failed", "run.tcl")
CACHE_FILE = ".dashboard_cache.json"


def _mtime_iso(path):
    try:
        return _iso(datetime.datetime.utcfromtimestamp(int(os.path.getmtime(path))))
    except OSError:
        return None


class _ReportCache(object):
    """Parsed timing summaries keyed by path+mtime: a dashboard re-reads every
    20 s, and re-parsing every report of every run each time is wasted work."""

    def __init__(self, output_dir, parse):
        self.path = os.path.join(output_dir, CACHE_FILE)
        self.parse = parse
        self.data = _read(self.path) or {}
        self.dirty = False

    def timing(self, rpt):
        try:
            key = "%s@%d" % (rpt, int(os.path.getmtime(rpt)))
        except OSError:
            return None
        if key in self.data:
            return self.data[key]
        val = None
        if self.parse:
            try:
                t = self.parse(rpt) or {}
                val = {"wns": t.get("wns"), "whs": t.get("whs"), "tns": t.get("tns")}
            except Exception:                      # noqa: BLE001 -- display only
                val = None
        self.data[key] = val
        self.dirty = True
        return val

    def save(self):
        if self.dirty:
            try:
                with open(self.path, "w") as fh:
                    json.dump(self.data, fh)
            except (IOError, OSError):
                pass


def _run_state(d, row):
    if os.path.isfile(os.path.join(d, "duplicate_of.json")):
        return "duplicate"
    if os.path.isfile(os.path.join(d, "run.done")):
        return "done"
    # Cancelled after the design closed (close-design): its kill is not a failure.
    if os.path.isfile(os.path.join(d, "run.cancelled")):
        return "cancelled"
    if os.path.isfile(os.path.join(d, "run.failed")):
        return "failed"
    if row is not None and row.get("settled") and not row.get("done"):
        return "failed"
    log = os.path.join(d, "lsf.log")
    try:
        with open(log, errors="replace") as fh:
            tail = fh.read()[-4000:]
        if "Exited with" in tail or "TERM_" in tail:
            return "failed"
        if "Successfully completed" in tail:
            return "failed"                        # Vivado exited without run.done
    except (IOError, OSError):
        pass
    if os.path.isfile(os.path.join(d, "job.json")) or os.path.isdir(os.path.join(d, ".vivado-ai")):
        return "running"
    return "queued"


def _run_detail(stage, row, cache, status_row=None):
    """One run: state, every step reached with its timing, timestamps, logs."""
    d = row.get("working_dir") or ""
    out = {"stage": stage, "run_id": row.get("run_id"), "dir": d,
           "state": _run_state(d, status_row), "steps": [], "step": None,
           "wns": None, "whs": None, "tns": None}
    for step, stem in STAGE_REPORTS:
        rpt = os.path.join(d, stem + "_timing_summary.rpt")
        if os.path.isfile(rpt):
            t = cache.timing(rpt) or {}
            out["steps"].append(dict(t, step=step, at_utc=_mtime_iso(rpt)))
    if out["steps"]:
        last = out["steps"][-1]
        out.update(step=last["step"], wns=last.get("wns"), whs=last.get("whs"),
                   tns=last.get("tns"))
    dup = _read(os.path.join(d, "duplicate_of.json"))
    if isinstance(dup, dict):
        out["duplicate_of"] = dup.get("duplicate_of")
    job = _read(os.path.join(d, "job.json")) or {}
    out["job_id"] = job.get("job_id")
    out["backend"] = job.get("backend")
    # An MCP-launched run is still an LSF job; show the id people can bjobs.
    out["lsf_job_id"] = (job.get("resources") or {}).get("lsf_job_id")
    out["submitted_utc"] = job.get("submitted_utc")
    out["finished_utc"] = (_mtime_iso(os.path.join(d, "run.done"))
                           or _mtime_iso(os.path.join(d, "run.failed")))
    failed = _read(os.path.join(d, "run.failed"))
    if isinstance(failed, dict):
        out["error"] = str(failed.get("error", ""))[:400]
    out["logs"] = _session_logs(d) + [os.path.join(d, f) for f in LOG_FILES
                                      if os.path.isfile(os.path.join(d, f))]
    out["methodology"] = row.get("methodology")
    return out


def _session_logs(d):
    """A run through Vivado MCP logs in .vivado-ai/: its newest Vivado log, then its LSF log."""
    out = []
    for pattern in ("vivado_*.log", "lsf_session_*.log"):
        found = glob.glob(os.path.join(d, ".vivado-ai", pattern))
        found = [f for f in found if not os.path.basename(f).startswith("vivado_proxy_")]
        if found:
            out.append(max(found, key=lambda f: os.path.getmtime(f)))
    return out


def _decisions_all(output_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(output_dir, decisions.DIRNAME, "*.json"))):
        rec = _read(path)
        if not isinstance(rec, dict):
            continue
        ans = rec.get("answer")
        why = None
        if isinstance(ans, dict):
            why = ans.get("why") or (("plan: %s" % ans.get("plan_path")) if ans.get("plan_path") else None)
            ans = ans.get("answer", ans.get("plan_path"))
        default = rec.get("rule_default") or {}
        out.append({"id": rec.get("id"), "point": rec.get("point"), "state": rec.get("state"),
                    "created_utc": rec.get("created_utc"), "deadline_utc": rec.get("deadline_utc"),
                    "answered_utc": rec.get("answered_utc"), "answered_by": rec.get("answered_by"),
                    "answer": ans, "why": why, "validated": rec.get("validated"),
                    "default": default.get("answer"), "default_why": default.get("why"),
                    "model": (rec.get("provenance") or {}).get("model"),
                    "file": path})
    return out


def _plans(output_dir):
    out = []
    for plan_path in sorted(glob.glob(os.path.join(output_dir, "planner", "*", "plan.json"))):
        plan = _read(plan_path)
        if not isinstance(plan, dict):
            continue
        out.append({"round": os.path.basename(os.path.dirname(plan_path)),
                    "summary": (plan.get("summary") or "")[:2000],
                    "fixes": [{"name": f.get("name"), "kind": f.get("kind"),
                               "why": (f.get("justification") or "")[:1000]}
                              for f in plan.get("fixes") or []],
                    "file": plan_path})
    return out


def _timeline(runs, decs, state):
    ev = []
    for r in runs:
        if r.get("submitted_utc"):
            ev.append({"at": r["submitted_utc"][:19] + "Z", "kind": "submit",
                       "text": "%s %s submitted" % (r["stage"], r["run_id"])})
        if r.get("finished_utc"):
            ev.append({"at": r["finished_utc"], "kind": r["state"],
                       "text": "%s %s %s%s" % (r["stage"], r["run_id"], r["state"],
                                               "" if r.get("wns") is None
                                               else " (WNS %+.3f)" % r["wns"])})
    for d in decs:
        if d.get("created_utc"):
            ev.append({"at": d["created_utc"], "kind": "ask",
                       "text": "%s decision asked" % d["point"]})
        if d.get("answered_utc"):
            ev.append({"at": d["answered_utc"], "kind": "answer",
                       "text": "%s answered by %s: %s" % (d["point"], d.get("answered_by"),
                                                          d.get("answer"))})
    ev.sort(key=lambda e: e["at"])
    return ev[-60:]


def dashboard(output_dir):
    """
    snapshot() plus what a viewer wants and an agent does not: every run of
    every stage with each step it reached and its timing, every decision with
    who answered it and why, the planner's plans, a timeline, the guard's
    verdicts and the supervisor log tail. Reads only; never marks a decision
    as seen (that is `wait`'s bookkeeping).
    """
    output_dir = os.path.abspath(output_dir)
    snap = snapshot(output_dir)
    st = _status(output_dir)
    try:
        parse = paths.import_script("report_parser").parse_timing_summary
    except Exception:                              # noqa: BLE001
        parse = None
    cache = _ReportCache(output_dir, parse)
    state = _read(os.path.join(output_dir, "state.json")) or {}
    status_rows = {r.get("working_dir"): r for r in st.get("pending_runs") or []}
    seen, runs = set(), []
    for stage, key in STAGE_ROWS:
        for row in state.get(key) or []:
            d = row.get("working_dir")
            if not d or d in seen or not os.path.isdir(d):
                continue
            seen.add(d)
            runs.append(_run_detail(stage, row, cache, status_rows.get(d)))
    cache.save()
    snap["all_runs"] = runs
    snap["run_list"] = [r for r in runs if r["dir"] in status_rows]
    stages = []
    for sg in ("stage1", "stage2", "planner", "stage3"):
        rec = _read(os.path.join(output_dir, "%s_best.json" % sg))
        if isinstance(rec, dict):
            rec = rec.get("best", rec) if isinstance(rec.get("best"), dict) else rec
            if rec.get("wns") is not None:
                stages.append({"stage": sg, "run": rec.get("best_run_id") or rec.get("run_id"),
                               "wns": rec.get("wns"), "whs": rec.get("whs"),
                               "tns": rec.get("tns")})
    snap["stages"] = stages
    decs = _decisions_all(output_dir)
    snap["decisions"] = decs
    snap["plans"] = _plans(output_dir)
    snap["timeline"] = _timeline(runs, decs, state)
    snap["design"] = state.get("design")
    snap["dcp"] = state.get("dcp")
    snap["description"] = st.get("description")
    snap["phases_seen"] = sorted({r["stage"] for r in runs})
    path, leases = find_lease(output_dir)
    mine = [l for l in leases if os.path.abspath(l["output_dir"]) == os.path.abspath(output_dir)]
    snap["attached"] = bool(mine)
    snap["attached_utc"] = mine[0].get("attached_utc") if mine else None
    snap["attached_runs"] = len(leases)
    snap["agent"] = agent_state(output_dir, snap["attached"])
    guard_lines = []
    if path:
        try:
            with open(os.path.join(os.path.dirname(path), GUARD_LOG)) as fh:
                guard_lines = fh.read().splitlines()[-8:]
        except (IOError, OSError):
            pass
    snap["guard"] = guard_lines
    snap["launch"] = _launch_info(output_dir, runs)
    try:
        with open(os.path.join(output_dir, "serve.log")) as fh:
            snap["serve_log_tail"] = fh.read().splitlines()[-20:]
    except (IOError, OSError):
        snap["serve_log_tail"] = []
    final = os.path.join(output_dir, "final_summary.txt")
    if os.path.isfile(final):
        with open(final) as fh:
            snap["verdict"] = [l.strip() for l in fh if "VERDICT" in l or "Best WNS" in l][:2]
    starts = [r["submitted_utc"] for r in runs if r.get("submitted_utc")]
    snap["started_utc"] = min(starts)[:19] + "Z" if starts else None
    snap["updated_utc"] = _iso(_utc_now())
    return snap


def _launch_info(output_dir, runs):
    """
    How this design's runs are launched, for the dashboard: the `auto`
    decision (backend.json) with its reason and any failover, else what the
    runs' own job.json files say (a backend set explicitly in engine.yaml).
    """
    from . import launch
    rec = launch.load(output_dir)
    if rec:
        return {"backend": rec.get("backend"), "reason": rec.get("reason"),
                "mcp": (rec.get("mcp") or {}).get("version"),
                "channel": (rec.get("mcp") or {}).get("channel"),
                "failover": rec.get("failover")}
    seen = sorted({r.get("backend") for r in runs if r.get("backend")})
    if not seen:
        return None
    return {"backend": "+".join(seen), "reason": "set in engine.yaml", "failover": None}


def is_active(snap):
    """A run the orchestrator must keep watching: supervisor alive, not finished."""
    return bool(snap.get("supervisor_alive")) and snap.get("phase") not in TERMINAL_PHASES


def _fingerprint(snap):
    return (snap.get("phase"), snap.get("next_step"),
            tuple(sorted(d.get("id", "") for d in snap.get("pending_decisions") or [])),
            snap.get("supervisor_alive"),
            (snap.get("runs") or {}).get("failed"))


def wait(output_dir, timeout_s=WAIT_DEFAULT_S, poll_s=WAIT_POLL_S, sleep=time.sleep,
         clock=time.time):
    """
    Block until something needs the orchestrator, then say what.

    event is one of: decision, phase_change, run_failed, supervisor_stopped,
    complete, timeout. A pending decision returns at once -- it has a deadline.
    """
    return wait_many([output_dir], timeout_s=timeout_s, poll_s=poll_s, sleep=sleep,
                     clock=clock)


def wait_many(output_dirs, timeout_s=WAIT_DEFAULT_S, poll_s=WAIT_POLL_S,
              sleep=time.sleep, clock=time.time, fast=False, quiet_phases=False):
    """
    `wait` over several runs: returns the first run with an event, with its
    `output_dir`. One agent orchestrating two designs would otherwise sit in a
    9-minute wait on one while the other's decision waits. `watching` lists
    every run in the call, so a timeout still says what was covered.

    fast: read each run's files (fast_snapshot) instead of `orchestrate.py
    status`. quiet_phases: do not return on phase_change or run_failed -- with
    a hundred runs one of them changes phase every few minutes, and a failure
    worth an answer arrives as a triage decision anyway.
    """
    dirs = [os.path.abspath(d) for d in output_dirs]
    note_activity(dirs, "tc wait", wait_pid=os.getpid())
    try:
        return _wait_many(dirs, timeout_s, poll_s, sleep, clock,
                          snap_fn=fast_snapshot if fast else snapshot, quiet_phases=quiet_phases)
    finally:
        note_activity(dirs, "tc wait", end_wait=True)


def _wait_many(dirs, timeout_s, poll_s, sleep, clock, snap_fn=None, quiet_phases=False):
    # A decision is reported once. One the agent chose to leave to its rule
    # would otherwise make every later wait return at once -- a busy loop.
    snap_fn = snap_fn or snapshot
    seen = {d: set(_read(os.path.join(d, WAIT_SEEN_FILE)) or []) for d in dirs}
    start = clock()
    snaps = {d: snap_fn(d) for d in dirs}
    hit = next(((d, r) for d in dirs for r in [_event(snaps[d], None, seen[d])] if r), None)
    if not hit:
        base = {d: _fingerprint(snaps[d]) for d in dirs}
        while clock() - start < timeout_s and not hit:
            sleep(min(poll_s, max(1, timeout_s - (clock() - start))))
            for d in dirs:
                snaps[d] = snap_fn(d)
                reason = _event(snaps[d], base[d], seen[d])
                if reason in ("phase_change", "run_failed") and quiet_phases:
                    base[d] = _fingerprint(snaps[d])
                    reason = None
                if reason:
                    hit = (d, reason)
                    break
    out, reason = hit or (dirs[0], None)
    snap = snaps[out]
    new = [x.get("id") for x in snap.get("pending_decisions") or [] if x.get("id")]
    if new:
        try:
            with open(os.path.join(out, WAIT_SEEN_FILE), "w") as fh:
                json.dump(sorted(seen[out] | set(new)), fh)
        except (IOError, OSError):
            pass
    res = dict(snap, event=reason or "timeout", waited_s=int(clock() - start))
    res["output_dir"] = out
    if len(dirs) > 1:
        res["watching"] = [{"output_dir": d, "phase": snaps[d].get("phase"),
                            "supervisor_alive": snaps[d].get("supervisor_alive"),
                            "pending": len(snaps[d].get("pending_decisions") or [])}
                           for d in dirs]
    return res


def compact(snap):
    """
    A wait result as an agent should read it. Every reply stays in the agent's
    context and is read again on each later turn: `wait_all` over 101 runs
    returned 24 KB a call, a list of every run plus the event run's whole
    outcome record. This keeps the event and that run's state, and reduces the
    rest to counts and the names that need someone.
    """
    out = {k: v for k, v in snap.items() if k not in ("outcome", "watching")}
    oc = snap.get("outcome")
    if isinstance(oc, dict):
        fails = oc.get("failures") or []
        by = {}
        for f in fails:
            by[f.get("failure_class") or "unknown"] = by.get(f.get("failure_class") or "unknown", 0) + 1
        out["outcome"] = {"outcome": oc.get("outcome"), "failures": by,
                          "decisions": (oc.get("decisions") or {}).get("by_answerer")}
    elif oc is not None:
        out["outcome"] = oc
    watching = snap.get("watching")
    if watching:
        phases = {}
        for w in watching:
            phases[w.get("phase") or "?"] = phases.get(w.get("phase") or "?", 0) + 1
        out["watching"] = {
            "runs": len(watching), "by_phase": phases,
            "pending_decisions": sum(w.get("pending") or 0 for w in watching),
            "supervisor_stopped": [os.path.basename(w["output_dir"]) for w in watching
                                   if not w.get("supervisor_alive")][:20],
            "with_decisions": [os.path.basename(w["output_dir"]) for w in watching
                               if w.get("pending")][:20],
        }
    return out


def _event(snap, base_fp, seen=()):
    if snap.get("phase") in TERMINAL_PHASES:
        return "complete"
    if not snap.get("supervisor_alive"):
        return "supervisor_stopped"
    if any(d.get("id") not in seen for d in snap.get("pending_decisions") or []):
        return "decision"
    if base_fp is None:
        return None
    fp = _fingerprint(snap)
    if fp[4] != base_fp[4]:
        return "run_failed"
    if fp != base_fp:
        return "phase_change"
    return None


# ---------------------------------------------------------------------------
# the lease and the stop guard
# ---------------------------------------------------------------------------
def _activity_path(output_dir):
    path, _ = find_lease(output_dir)
    return os.path.join(os.path.dirname(path), ACTIVITY_FILE) if path else None


def note_activity(output_dirs, how, wait_pid=None, end_wait=False, now=None):
    """
    Record that the orchestrating agent did something for these runs: a
    `tc wait` starting (with its pid) or ending, a hook firing, an answer.
    The dashboard reads it to tell an agent at work from a lease nobody is
    holding any more. Best effort; never raises.
    """
    now = now or _utc_now()
    by_file = {}
    for d in output_dirs or []:
        path = _activity_path(d)
        if path:
            by_file.setdefault(path, []).append(os.path.abspath(d))
    for path, dirs in by_file.items():
        try:
            data = _read(path) or {}
            if not isinstance(data, dict):
                data = {}
            for d in dirs:
                e = dict(data.get(d) or {})
                e["last_seen_utc"] = _iso(now)
                e["via"] = how
                if wait_pid:
                    e["wait_pid"] = int(wait_pid)
                    e["wait_since_utc"] = _iso(now)
                if end_wait and e.get("wait_pid") in (None, os.getpid()):
                    e.pop("wait_pid", None)
                    e.pop("wait_since_utc", None)
                data[d] = e
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w") as fh:
                json.dump(data, fh, indent=2)
            os.rename(tmp, path)
        except (IOError, OSError, ValueError):
            pass


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError) as exc:
        return getattr(exc, "errno", None) == 1        # EPERM: alive, not ours


def agent_state(output_dir, attached, now=None):
    """
    waiting -- a `tc wait` for this run is running now;
    active  -- the agent called in within AGENT_ACTIVE_S;
    idle    -- attached, but silent longer than that (the chat has stopped:
               decisions fall to their rules at the deadline);
    unknown -- attached, but no activity recorded for this run yet;
    none    -- no agent attached.
    """
    if not attached:
        return {"state": "none"}
    now = now or _utc_now()
    path = _activity_path(output_dir)
    e = ((_read(path) or {}) if path else {}).get(os.path.abspath(output_dir)) or {}
    last = _parse_iso(e.get("last_seen_utc") or "")
    idle_s = int((now - last).total_seconds()) if last else None
    out = {"last_seen_utc": e.get("last_seen_utc"), "via": e.get("via"), "idle_s": idle_s}
    if not e:
        # Nothing recorded for this run yet -- e.g. a wait started by an
        # engine from before activity was recorded. Not evidence of absence.
        out["state"] = "unknown"
    elif e.get("wait_pid") and _pid_alive(e["wait_pid"]):
        out.update(state="waiting", wait_since_utc=e.get("wait_since_utc"))
    elif idle_s is not None and idle_s <= AGENT_ACTIVE_S:
        out["state"] = "active"
    else:
        out["state"] = "idle"
    return out


def lease_path(workspace=None):
    return os.path.join(os.path.abspath(workspace or os.getcwd()), LEASE_DIR, LEASE_FILE)


def read_leases(path):
    """
    The leases in one attached.json, as a list. Reads the single-run file the
    first release wrote ({"output_dir": ...}) as a list of one.
    """
    data = _read(path) if path else None
    if isinstance(data, dict) and data.get("output_dir"):
        return [data]
    if isinstance(data, dict) and isinstance(data.get("runs"), list):
        return [l for l in data["runs"] if isinstance(l, dict) and l.get("output_dir")]
    return []


def _write_leases(path, leases):
    if not leases:
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"runs": leases}, fh, indent=2)
    os.rename(tmp, path)


def attach(output_dir, workspace=None, max_wall_hours=GUARD_MAX_WALL_HOURS):
    """
    Add (or refresh) this run's lease. Other runs' leases stay: attaching to a
    second design must not leave the first one unwatched -- its decisions would
    quietly fall to their rules.
    """
    path = lease_path(workspace)
    out = os.path.abspath(output_dir)
    # No chat yet: the first Stop of the chat that attached claims it (see
    # leases_for_chat), so other chats in the workspace are left alone.
    lease = {"output_dir": out, "attached_utc": _iso(_utc_now()),
             "max_wall_hours": max_wall_hours, "chat_id": None}
    leases = [l for l in read_leases(path) if os.path.abspath(l["output_dir"]) != out]
    _write_leases(path, leases + [lease])
    note_activity([out], "attach")
    return lease


def detach(workspace=None, output_dir=None, user_asked=False, snap_fn=None):
    """
    Release one run's lease, or every lease when no run is named:
    {"released": [...], "kept_active": [...]}.

    A run that is still active keeps its lease unless the user asked to stop.
    The first VS Code run lost its orchestrator this way: the agent fought the
    stop guard until the rate limit let it go, detached, and the run went on
    for hours with nobody answering its decisions.
    """
    snap_fn = snap_fn or snapshot
    path = lease_path(workspace)
    leases = read_leases(path)
    want = os.path.abspath(output_dir) if output_dir else None
    keep, released, kept_active = [], [], []
    for l in leases:
        out = os.path.abspath(l["output_dir"])
        if want and out != want:
            keep.append(l)
        elif not user_asked and is_active(snap_fn(out)):
            keep.append(l)
            kept_active.append(out)
        else:
            released.append(out)
    if released:
        _write_leases(path, keep)
    return {"released": released, "kept_active": kept_active}


def run_dirs(workspace, depth=3):
    """
    Output directories of the runs under a workspace, up to `depth` levels down:
    any folder whose state.json is a flow's (it has a phase). Not by name: `tc
    close --output-dir` takes any name, and runs named <design>_tc_sa or _tc_fsmfo
    were listed only while a supervisor had them attached, so the view lost each
    one as it finished.
    """
    out = []
    for level in range(1, depth + 1):
        pattern = os.path.join(os.path.abspath(workspace), *(["*"] * level + ["state.json"]))
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path) as fh:
                    state = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and "phase" in state:
                out.append(os.path.dirname(os.path.abspath(path)))
    return out


def unattached_active_runs(workspace, attached=(), snap_fn=None, depth=3):
    """
    Runs under the workspace whose supervisor is alive but that no agent is
    orchestrating: started from a terminal, or left after the user said stop.
    """
    snap_fn = snap_fn or snapshot
    seen = set(os.path.abspath(a) for a in attached)
    out = []
    for d in run_dirs(workspace, depth):
        if d in seen or not os.path.isfile(os.path.join(d, PID_FILE)):
            continue
        seen.add(d)
        if is_active(snap_fn(d)):
            out.append(d)
    return out


def session_context(start=None, snap_fn=None, now=None, chat=None):
    """
    Text for a new chat or a new prompt in a workspace with live runs, or None.

    The lease outlives any one chat (a window reload, a turn limit, a rate-
    limited stop): this tells the next turn it is still the orchestrator, so
    it resumes the loop instead of starting over or doing nothing.
    """
    snap_fn = snap_fn or snapshot
    path, every = find_lease(start)
    _, leases = leases_for_chat(start, chat)
    ws = os.path.dirname(os.path.dirname(path)) if path else os.path.abspath(start or os.getcwd())
    lines = []
    # Every attached run, this chat's or not, is orchestrated: not an orphan.
    attached = [os.path.abspath(l["output_dir"]) for l in every]
    for l in leases:
        out = os.path.abspath(l["output_dir"])
        snap = snap_fn(out)
        if not is_active(snap):
            continue
        pend = [d.get("id") for d in snap.get("pending_decisions") or []]
        lines.append("- %s: phase %s, %d run(s) in flight%s" % (
            out, snap.get("phase"), (snap.get("runs") or {}).get("running", 0),
            ", decision(s) pending: %s" % ", ".join(pend) if pend else ""))
    text = []
    if lines:
        text.append("Timing closure: this workspace's agent is orchestrating %d active run(s):\n%s\n"
                    "Unless the user asks for something else, continue the loop from the "
                    "timing-closure skill: answer pending decisions, then wait (the "
                    "timing-closure `wait_all` action, or `tc wait --attached`). Do not detach "
                    "an active run unless the user asks you to stop."
                    % (len(lines), "\n".join(lines)))
    orphans = unattached_active_runs(ws, attached, snap_fn)
    if orphans:
        text.append("Timing closure: run(s) active with no orchestrator: %s. If the user wants "
                    "one watched, start it again (the `start` action, or `tc close --dcp "
                    "<dcp> --attach`) and run the loop."
                    % ", ".join(orphans))
    return "\n".join(text) or None


def chat_of(event):
    """The conversation a hook fired in (Cursor: conversation_id; Claude Code: session_id)."""
    for key in ("conversation_id", "conversationId", "session_id", "sessionId"):
        v = (event or {}).get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _transcript_mentions(path, needles, limit=4 << 20):
    """Whether the chat's transcript (its last `limit` bytes) names any needle; None if unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            text = fh.read().decode("utf-8", "replace")
    except (IOError, OSError, TypeError):
        return None
    return any(n in text for n in needles)


def chat_transcript(chat, home=None):
    """Cursor's transcript of a conversation, which its hook payload does not name."""
    if not chat:
        return None
    pattern = os.path.join(home or os.path.expanduser("~"), ".cursor", "projects", "*",
                           "agent-transcripts", glob.escape(chat), glob.escape(chat) + ".jsonl")
    found = glob.glob(pattern)
    return found[0] if found else None


def leases_for_chat(start=None, chat=None, claim=False, transcript=None):
    """
    The leases that belong to this chat. A lease is held by the chat that ran
    `tc attach`: the hooks are installed for every chat (a Cursor plugin's run
    in every window), and a workspace-wide lease blocked and briefed chats that
    had nothing to do with the run. An unclaimed lease is claimed by a Stop
    (claim=True) whose transcript names its run -- Claude Code passes the path,
    Cursor's is found by conversation id -- or by the first Stop at all when no
    transcript can be read. Without a chat id: every lease.
    """
    path, leases = find_lease(start)
    if not chat:
        return path, leases
    mine, changed = [], False
    if claim and not transcript and any(not l.get("chat_id") for l in leases):
        transcript = chat_transcript(chat)
    # A transcript that names some runs' folders claims only those; the
    # attach words alone (a relative path) claim the rest.
    named = {l["output_dir"]: _transcript_mentions(transcript, [l["output_dir"]])
             for l in leases if transcript and not l.get("chat_id")}
    any_named = any(named.values())
    generic = (_transcript_mentions(transcript, ["tc attach", "--attach"])
               if transcript and named and not any_named else None)
    for l in leases:
        owner = l.get("chat_id")
        if owner == chat:
            mine.append(l)
        elif not owner and claim:
            if not transcript:
                seen = None
            elif named.get(l["output_dir"]) is None:
                seen = None                      # transcript unreadable
            elif any_named:
                seen = named[l["output_dir"]]
            else:
                seen = generic
            if seen is not False:
                l["chat_id"] = chat
                changed = True
                mine.append(l)
    if changed:
        _write_leases(path, leases)
    return path, mine


def attached_runs(start=None):
    """Output dirs attached in the nearest workspace lease, oldest first."""
    return [os.path.abspath(l["output_dir"]) for l in find_lease(start)[1]]


def find_lease(start=None):
    """The nearest .tc/attached.json from `start` upwards, as (path, [leases])."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        cand = os.path.join(cur, LEASE_DIR, LEASE_FILE)
        if os.path.isfile(cand):
            return cand, read_leases(cand)
        parent = os.path.dirname(cur)
        if parent == cur:
            return None, []
        cur = parent


def _recent_blocks(log_path, now, window_s=3600):
    n = 0
    try:
        with open(log_path) as fh:
            for line in fh:
                # "<iso> block <phases>". Not any line mentioning a block: the
                # guard's own "allow block limit reached" counted too, and kept
                # it switched off for as long as the agent kept stopping.
                fields = line.split(" ", 2)
                if len(fields) < 2 or fields[1] != "block":
                    continue
                t = _parse_iso(fields[0])
                if t and (now - t).total_seconds() <= window_s:
                    n += 1
    except (IOError, OSError):
        pass
    return n


def guard(start=None, now=None, snap_fn=None,
          max_blocks_per_hour=GUARD_MAX_BLOCKS_PER_HOUR, chat=None, transcript=None):
    """
    The Stop-hook verdict: (block: bool, reason: str).

    Blocks only while (a) an agent attached to a run in this workspace, (b) at
    least one attached run is still active, (c) the agent has not fought the
    hook more than `max_blocks_per_hour` times in the last hour, and (d) that
    run's lease is younger than its wall-clock limit. Anything else lets the
    turn end.
    """
    now = now or _utc_now()
    snap_fn = snap_fn or snapshot
    path, leases = leases_for_chat(start, chat, claim=True, transcript=transcript)
    if not leases:
        return False, ("no timing-closure run attached in this workspace" if path is None
                       else "this chat is not orchestrating a timing-closure run")
    log_path = os.path.join(os.path.dirname(path), GUARD_LOG)
    active, why = [], []
    for lease in leases:
        out = lease["output_dir"]
        attached = _parse_iso(lease.get("attached_utc") or "")
        max_h = float(lease.get("max_wall_hours") or GUARD_MAX_WALL_HOURS)
        if attached and (now - attached).total_seconds() > max_h * 3600:
            why.append("%s: attached more than %g h ago" % (os.path.basename(out), max_h))
            continue
        snap = snap_fn(out)
        if is_active(snap):
            active.append((out, snap))
        else:
            why.append("%s: %s" % (os.path.basename(out),
                                   "run complete" if snap.get("phase") in TERMINAL_PHASES
                                   else "supervisor not running"))
    if not active:
        reason = "; ".join(why)
        _log(log_path, now, "allow", reason)
        return False, reason
    # One stop can fire two hooks (VS Code runs .github/hooks and the Claude
    # file); count it once.
    if _recent_blocks(log_path, now, window_s=5):
        return True, _block_reasons(active)
    if _recent_blocks(log_path, now) >= max_blocks_per_hour:
        _log(log_path, now, "allow", "block limit reached")
        return False, ("stop blocked %d times in the last hour; letting the turn end so "
                       "a person can look" % max_blocks_per_hour)
    _log(log_path, now, "block", ",".join(s.get("phase") or "?" for _, s in active))
    return True, _block_reasons(active)


def _block_reasons(active):
    if len(active) == 1:
        return _block_reason(*active[0])
    parts = []
    for out, snap in active:
        pend = len(snap.get("pending_decisions") or [])
        parts.append("%s (phase %s, %d run(s) in flight%s)" % (
            out, snap.get("phase"), (snap.get("runs") or {}).get("running", 0),
            ", %d decision(s) pending" % pend if pend else ""))
    return ("%d timing-closure runs attached in this workspace are still active: %s. "
            "Do not end your turn: answer any pending decision(s), then wait on all of "
            "them (the timing-closure `wait_all` action, or `tc wait --attached`), and "
            "continue the loop from the skill. If the user asked you to stop, detach "
            "first (the `detach` action with user_asked, or `tc detach --user-asked`)."
            % (len(active), "; ".join(parts)))


def _block_reason(out, snap):
    runs = snap.get("runs") or {}
    pend = snap.get("pending_decisions") or []
    return ("The timing-closure run in %s is still active (phase %s, %d run(s) in flight%s). "
            "Do not end your turn: %s, then continue the loop from the skill. "
            "If the user asked you to stop, detach first (the `detach` action with "
            "user_asked, or `tc detach --user-asked`)."
            % (out, snap.get("phase"), runs.get("running", 0),
               ", %d decision(s) pending" % len(pend) if pend else "",
               "answer the pending decision(s)" if pend
               else "wait on it (the timing-closure `wait` action, or "
                    "`tc wait --output-dir %s`)" % out))


def _log(path, now, verdict, why):
    try:
        with open(path, "a") as fh:
            fh.write("%s %s %s\n" % (_iso(now), verdict, why))
    except (IOError, OSError):
        pass


def context_hook(event, start=None, now=None, snap_fn=None, ide=None, chat=None):
    """
    The SessionStart / UserPromptSubmit hook output, or None. VS Code runs both
    the workspace hook and the Claude-format one, so the same event within 5 s
    is answered once -- the context would otherwise appear twice.
    """
    now = now or _utc_now()
    text = session_context(start=start, snap_fn=snap_fn, chat=chat)
    if not text:
        return None
    path, _ = find_lease(start)
    tc_dir = os.path.dirname(path) if path else os.path.join(
        os.path.abspath(start or os.getcwd()), LEASE_DIR)
    log_path = os.path.join(tc_dir, GUARD_LOG)
    if os.path.isdir(tc_dir):
        if _recent(log_path, now, 5, " context %s" % event):
            return None
        _log(log_path, now, "context", event)
    return context_output(event, text, ide)


def _recent(log_path, now, window_s, needle):
    try:
        with open(log_path) as fh:
            for line in fh:
                t = _parse_iso(line.split(" ", 1)[0])
                if t and (now - t).total_seconds() <= window_s and needle in line:
                    return True
    except (IOError, OSError):
        pass
    return False


def context_output(event, text, ide=None):
    """
    SessionStart / UserPromptSubmit context. Claude Code and VS Code share one
    shape; Cursor's sessionStart takes a flat `additional_context`.
    """
    if not text:
        return None
    if ide == "cursor":
        return {"additional_context": text}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def hook_output(block, reason, ide):
    """
    The Stop-hook JSON for each editor. None means 'allow' (print nothing).

    Cursor cannot block a stop: its `stop` hook may only hand back a
    `followup_message`, which Cursor submits as the next prompt. The effect is
    the same -- the agent goes back to the loop -- so the reason is the message.
    """
    if not block:
        return None
    if ide == "cursor":
        return {"followup_message": reason}
    if ide == "vscode":
        return {"hookSpecificOutput": {"hookEventName": "Stop",
                                       "decision": "block", "reason": reason}}
    return {"decision": "block", "reason": reason}
