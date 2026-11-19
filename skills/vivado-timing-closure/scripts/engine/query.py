#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
query.py -- read-only Vivado queries, owned by the engine.

The planner's best result came from queries nobody had pre-generated. On Ciena
five live queries produced findings E4-E6 -- GT quad to SLR mapping, per-module
clocks, and the 195 MHz SLL pipeline sitting at +2.2 ns slack while the 390 MHz
FIFOs were on the far SLR -- and that is what closed a design the fixed menu
could not. Take that away and the judge loses its edge, so the engine has to
provide it once the agent no longer holds a Vivado session of its own.

Three things this buys beyond portability:

* **The :18090 problem goes away.** The session is the engine's, opened on one
  checkpoint and closed after. A shared backend switch cannot orphan it, which
  is how three design A runs were lost.

* **The data boundary becomes enforceable.** An agent with raw `vivado_execute`
  could do anything. Here every command is checked against an allow-list, and
  anything that writes, implements or escapes to the shell is refused before
  Vivado sees it.

* **Queries are recorded.** Each one lands in `queries.jsonl` in the run
  directory, so a later planner round can see what was already asked -- the
  same idea as `xdc_check.json` reporting patterns that matched nothing.

Where it runs: on the compute farm when the flow runs there. A query opens a
routed checkpoint -- 10-30 GB on the large TSR designs -- and the agent calling
`tc query` sits on a shared login host, where that much memory is
not ours to take. `lsf_submitter()` sends the batch through `bsub -K` with the
flow's queue, OS selection and memory, and waits for it; `local` remains for
a laptop or a dedicated machine.

Scope: commands are batched into one session per invocation, because
`open_checkpoint` on a large design costs minutes and the planner asks a
handful of questions at once. An attach-on-demand daemon for iterative
question-by-question use is the obvious follow-up; it is not needed to answer
the questions that closed Ciena.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import glob
import json
import os
import re
import socket
import subprocess
import time

QUERY_LOG = "queries.jsonl"

# Readers. Anything not matched here is refused: an allow-list, so a Vivado
# release that adds a new writer does not silently become reachable.
ALLOWED = frozenset("""
    report_timing report_timing_summary report_utilization report_clocks
    report_clock_interaction report_clock_utilization report_design_analysis
    report_qor_assessment report_qor_suggestions report_route_status
    report_methodology report_drc report_cdc report_high_fanout_nets
    report_property report_pblocks report_slack report_exceptions
    report_datasheet report_power report_ram_utilization report_carry_chains
    report_control_sets report_disable_timing report_incremental_reuse
    get_cells get_nets get_pins get_ports get_clocks get_timing_paths
    get_sites get_slrs get_tiles get_bels get_property get_pblocks
    get_designs get_parts get_speed_models get_package_pins get_nodes
    get_path_groups get_generated_clocks get_debug_cores
    llength lsort lindex lrange lsearch lappend list concat
    join split string format regexp regsub expr set puts foreach for if
    while incr append array dict info clock catch return break continue
    current_design current_instance version
""".split())

# Refused outright, anywhere in the command, including inside [].
DENIED = frozenset("""
    exec source file eval open close socket cd exit glob load unload
    write_checkpoint write_xdc write_edif write_bitstream write_verilog
    write_vhdl write_qor_suggestions write_debug_probes write_csv
    place_design route_design opt_design phys_opt_design power_opt_design
    synth_design link_design implement_debug_core place_ports
    set_property set_false_path set_multicycle_path set_max_delay
    set_min_delay set_clock_groups set_clock_uncertainty set_input_delay
    set_output_delay create_clock create_generated_clock create_pblock
    create_debug_core resize_pblock add_cells_to_pblock delete_pblocks
    remove_cells_from_pblock reset_run reset_timing reset_property
    delete_timing_results close_design close_project open_checkpoint
    open_project open_run save_project_as create_project update_design
    import_files remove_files refresh_design unset rename
""".split())

_MARKER = "__TC_QUERY_DONE_%d__"
# Command position: start of input, or after ; [ { } or a newline.
_CMD_POS = re.compile(r"(?:^|[\[;{}\n])\s*([A-Za-z_][A-Za-z0-9_:]*)")


class QueryRefused(Exception):
    """The command is not read-only. Raised before Vivado is started."""


class QueryError(Exception):
    """Vivado could not be run, or died."""


def commands_in(text):
    """Every word that appears in command position, including inside []."""
    return [m.group(1) for m in _CMD_POS.finditer(text or "")]


def check(tcl):
    """
    Refuse anything that is not a read-only query.

    Both directions are enforced: the leading command must be on the
    allow-list, and no denied command may appear anywhere -- including nested
    in a bracket, which is where `[exec ...]` would hide.
    """
    text = (tcl or "").strip()
    if not text:
        raise QueryRefused("empty query")
    words = commands_in("\n" + text)
    if not words:
        raise QueryRefused("no command found in %r" % text[:80])
    for w in words:
        if w in DENIED:
            raise QueryRefused(
                "%r writes, implements or escapes; queries are read-only" % w)
    lead = words[0]
    if lead not in ALLOWED:
        raise QueryRefused(
            "%r is not on the read-only allow-list" % lead)
    return True


def _script(dcp, commands, marker_for):
    """The Tcl batch: open once, then each command bracketed by a marker."""
    lines = ["set tc_rc 0"]
    if dcp:
        lines.append("open_checkpoint %s" % _brace(dcp))
    for i, cmd in enumerate(commands):
        lines.append('puts "%s"' % (marker_for(i) + "_BEGIN"))
        lines.append("if {[catch {%s} tc_out]} { puts \"TC_ERROR: $tc_out\" } "
                     "else { puts $tc_out }" % cmd)
        lines.append('puts "%s"' % (marker_for(i) + "_END"))
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def _brace(path):
    return "{%s}" % path


def _local(argv, cwd, timeout, name):
    """Run Vivado here. (rc, combined output)."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                universal_newlines=True)
        out, _ = proc.communicate(timeout=timeout)
    except OSError as exc:
        raise QueryError("cannot run %s: %s" % (argv[0], exc))
    except subprocess.TimeoutExpired:
        proc.kill()
        raise QueryError("query timed out after %ss" % timeout)
    return proc.returncode, out or "", {"where": "local", "host": socket.gethostname()}


def lsf_submitter(queue=None, memory_mb=None, slots=None, os_type=None,
                  env_script=None, bsub="bsub", bkill="bkill"):
    """
    A submit function that runs the query on LSF and waits for it (`bsub -K`).

    Vivado's stdout goes to a log file beside the script, read back once the
    job ends; the markers find each answer in it as they do locally. A query
    that outlives the timeout is killed by job name, so it never keeps a slot.
    """
    def submit(argv, cwd, timeout, name):
        log_path = os.path.join(cwd, "%s.log" % name)
        cmd = [bsub, "-K", "-J", name, "-o", log_path]
        if queue:
            cmd += ["-q", queue]
        if slots:
            cmd += ["-n", str(slots)]
        select = []
        if os_type:
            select.append(os_type if "=" in os_type else "type==%s" % os_type)
        clause = ("select[%s] " % " && ".join(select)) if select else ""
        if memory_mb:
            clause += "rusage[mem=%d]" % int(memory_mb)
        if clause.strip():
            cmd += ["-R", clause.strip()]
        cmd += argv
        shell = " ".join(_q(a) for a in cmd)
        if env_script:
            shell = ". %s >/dev/null 2>&1; %s" % (_q(env_script), shell)
        try:
            proc = subprocess.Popen(["sh", "-c", shell], cwd=cwd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    universal_newlines=True)
            bsub_out, _ = proc.communicate(timeout=timeout)
        except OSError as exc:
            raise QueryError("cannot run bsub: %s" % exc)
        except subprocess.TimeoutExpired:
            proc.kill()
            kill = " ".join(_q(a) for a in [bkill, "-J", name])
            if env_script:
                kill = ". %s >/dev/null 2>&1; %s" % (_q(env_script), kill)
            subprocess.call(["sh", "-c", kill], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
            raise QueryError("query timed out after %ss on LSF (job %s killed)"
                             % (timeout, name))
        try:
            with open(log_path) as fh:
                out = fh.read()
            os.unlink(log_path)
        except (IOError, OSError):
            out = ""
        if not out:
            raise QueryError("LSF query produced no log: %s"
                             % (bsub_out or "").strip()[-400:])
        return proc.returncode, out, _lsf_header(out)
    return submit


_LSF_JOB = re.compile(r"Subject: Job (\d+):")
_LSF_HOST = re.compile(r"executed on host\(s\) <(?:\d+\*)?([^>]+)>")


def _lsf_header(log_text):
    """Where a bsub job ran, from the header LSF writes into its -o log."""
    info = {"where": "lsf"}
    m = _LSF_JOB.search(log_text)
    if m:
        info["lsf_job_id"] = m.group(1)
    m = _LSF_HOST.search(log_text)
    if m:
        info["host"] = m.group(1)
    return info


def session_submitter(make_backend, resources, fallback=None, poll_s=5.0):
    """
    A submit function that runs the query as a Vivado MCP LSF session
    (vivado_lsf start), the way the flow's own runs start: the job is listed
    with the other Vivado LSF sessions, and the server's queue and OS checks
    apply. A direct bsub job is invisible to both.

    The script ends in `exit`, so the session closes once it has answered;
    the answers are read from its Vivado log. `make_backend()` builds the
    engine's private session backend; when it cannot (no MCP server
    configured or startable), `fallback` -- the direct bsub submitter -- runs
    the query instead, so a query never fails for want of the launcher.
    """
    from .backends import base

    def submit(argv, cwd, timeout, name):
        script = argv[argv.index("-source") + 1]
        # Its own folder: the session backend writes its job handle where the
        # session runs, and the run being queried has one of its own.
        work = os.path.join(cwd, ".tc-query", name)
        os.makedirs(work, exist_ok=True)
        # -notrace: an echoed script would put every marker in the log before
        # its command ran, as batch mode's -notrace avoids.
        wrapper = os.path.join(work, "query.tcl")
        with open(wrapper, "w") as fh:
            fh.write("source -notrace %s\n" % _brace(script))
        with open(script) as fh:
            ends = re.findall(r'puts "(%s_END)"' % re.escape(_MARKER).replace("%d", r"\d+"),
                              fh.read())
        last_end = ends[-1] if ends else None
        try:
            backend = make_backend()
            handle = backend.submit(work, wrapper, resources)
        except (ValueError, base.LauncherUnavailable) as exc:
            if fallback is None:
                raise QueryError("cannot start a Vivado LSF session: %s" % exc)
            rc, out, info = fallback(argv, cwd, timeout, name)
            info["note"] = "no MCP launcher (%s); sent with bsub" % str(exc)[:120]
            return rc, out, info
        except base.SubmitError as exc:
            raise QueryError("Vivado LSF session refused: %s" % exc)
        res = handle.resources or {}
        info = {"where": "session", "session_id": res.get("session_id"),
                "lsf_job_id": res.get("lsf_job_id"), "host": res.get("lsf_host")}
        deadline = time.time() + timeout
        out, state = "", None
        try:
            while True:
                out = _session_log(handle, work)
                if last_end and last_end in out:
                    break
                state = backend.status(handle)
                if state.state in (base.DONE, base.FAILED):
                    out = _session_log(handle, work)
                    break
                if time.time() > deadline:
                    backend.kill(handle)
                    raise QueryError("query timed out after %ss in Vivado LSF session %s "
                                     "(stopped)" % (timeout, info["session_id"]))
                time.sleep(poll_s)
            if state is not None and state.host:
                info["host"] = state.host
        finally:
            try:
                backend.release(handle)
            except Exception:                              # noqa: BLE001
                pass
            try:
                backend.reset()
            except Exception:                              # noqa: BLE001
                pass
        failed = state is not None and state.state == base.FAILED
        return (1 if failed else 0), out, info
    return submit


def _session_log(handle, work):
    """The session's Vivado log: the one the server reported, else the newest."""
    paths = [handle.log_path] if handle.log_path else []
    paths += sorted(glob.glob(os.path.join(work, ".vivado-ai", "vivado_*.log")),
                    key=os.path.getmtime, reverse=True)
    for p in paths:
        try:
            with open(p, errors="replace") as fh:
                return fh.read()
        except (IOError, OSError):
            continue
    return ""


def _q(s):
    s = str(s)
    if s and all(c.isalnum() or c in "-_./=:+,[]" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def run(dcp, commands, vivado="vivado", cwd=None, timeout=1800,
        run_dir=None, log=None, submit=None):
    """
    Open `dcp` once and answer every command against it.

    `submit(argv, cwd, timeout, name) -> (rc, output)` runs Vivado; the
    default runs it on this host. Returns a list of {command, output, error}
    in the order given.
    """
    for c in commands:
        check(c)

    # Absolute, always: on the farm the job runs inside `cwd`, so a relative
    # script, log or checkpoint path resolves against the wrong directory --
    # every planner query on hash_semi_join came back empty that way.
    cwd = os.path.abspath(cwd or run_dir or os.getcwd())
    run_dir = os.path.abspath(run_dir) if run_dir else None
    dcp = os.path.abspath(dcp) if dcp else dcp
    stamp = int(time.time())

    def marker_for(i):
        return _MARKER % (stamp + i)

    script_path = os.path.join(cwd, ".tc_query_%d.tcl" % stamp)
    with open(script_path, "w") as fh:
        fh.write(_script(dcp, commands, marker_for))

    argv = [vivado, "-mode", "batch", "-nojournal", "-notrace",
            "-source", script_path]
    try:
        got = (submit or _local)(argv, cwd, timeout, "tc_query_%d" % stamp)
        rc, out = got[0], got[1]
        info = got[2] if len(got) > 2 else {}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    results = _split(out or "", commands, marker_for)
    if run_dir:
        _record(run_dir, results, info, dcp)
    if log:
        log("tc query: %d command(s), rc=%d, %s" % (
            len(commands), rc, " ".join("%s=%s" % kv for kv in sorted(info.items()) if kv[1])))
    for r in results:
        r["ran"] = dict(info)
    return results


def _split(out, commands, marker_for):
    results = []
    for i, cmd in enumerate(commands):
        begin, end = marker_for(i) + "_BEGIN", marker_for(i) + "_END"
        a, b = out.find(begin), out.find(end)
        if a < 0 or b < 0 or b < a:
            results.append({"command": cmd, "output": None,
                            "error": "no output captured (Vivado may have "
                                     "died before this command)"})
            continue
        body = out[a + len(begin):b].strip("\n")
        err = None
        if body.lstrip().startswith("TC_ERROR:"):
            err = body.lstrip()[len("TC_ERROR:"):].strip()
            body = None
        results.append({"command": cmd, "output": body, "error": err})
    return results


def _record(run_dir, results, info=None, dcp=None):
    """
    Append to queries.jsonl so the next planner round sees what was asked, and
    where it ran (host, LSF job, session) so that can be checked afterwards.
    The open planner round's folder gets the same lines: a query is logged
    beside the checkpoint it read, which is often an earlier round's run.
    """
    utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = []
    for r in results:
        rec = {"utc": utc, "command": r["command"], "ok": r["error"] is None,
               "error": r["error"]}
        for k, v in sorted((info or {}).items()):
            if v:
                rec[k] = v
        lines.append(rec)
    _append(os.path.join(run_dir, QUERY_LOG), lines)
    rnd = _open_round_dir(run_dir)
    if rnd and os.path.abspath(rnd) != os.path.abspath(run_dir):
        _append(os.path.join(rnd, QUERY_LOG),
                [dict(rec, run_dir=run_dir, dcp=dcp) for rec in lines])


def _append(path, records):
    try:
        with open(path, "a") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
    except (IOError, OSError):
        pass


def _open_round_dir(run_dir, depth=6):
    """planner/<round> of the flow run_dir belongs to, while a round is open."""
    d = os.path.abspath(run_dir)
    for _ in range(depth):
        state_path = os.path.join(d, "state.json")
        if os.path.isfile(state_path):
            try:
                with open(state_path) as fh:
                    state = json.load(fh)
            except (IOError, OSError, ValueError):
                return None
            early = state.get("planner_early") or {}
            if early and early.get("rows") is None:
                name = "early"
            elif str(state.get("phase", "")).startswith("planner") and state.get("planner"):
                name = "r%s" % state["planner"].get("round")
            else:
                return None
            path = os.path.join(d, "planner", name)
            return path if os.path.isdir(path) else None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None
