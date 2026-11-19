#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
watch_triage.py -- what `tc watch` does on its own between agent turns.

A batch of a hundred designs raises the same few judgement calls over and
over: a failure the design has already shown on two other placements, a known
internal error, a first out-of-memory kill. The Rubik batch had an agent answer
each one by hand; this answers the ones whose answer is fixed by the evidence
and leaves everything else (planner calls, gates, unfamiliar failures) to the
agent. Every answer is recorded as answered_by "watch" with its reason.

The housekeeping helpers are each behind their own `tc watch` flag, because
each one changes something outside the decision queue: restarting a
supervisor, deleting checkpoints, killing farm jobs. Nothing here runs unless
the user asked for it.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import glob
import json
import os
import re
import socket
import subprocess

from . import backends, decisions

# Errors that say the same run would fail the same way again. Each is a Vivado
# message id seen to reproduce on rerun across designs in the Rubik batch.
DETERMINISTIC = (
    ("Implflow 47-2636", "Vivado internal placer error (Implflow 47-2636, unplaced instances); "
                         "reproducible under these constraints, a rerun repeats it."),
    ("12-25514", "USER_CLOCK_EXPANSION_WINDOW covering HSR regions is rejected (12-25514); "
                 "deterministic for this recipe on this device."),
    ("12-26068", "USER_CLOCK_EXPANSION_WINDOW rejected on this device (12-26068); "
                 "deterministic for this recipe."),
    ("Vivado_Tcl 4-567", "phys_opt_design got an empty -path_groups list (Vivado_Tcl 4-567): "
                         "a defect of the generated run.tcl, so a rerun repeats it."),
)

# Wrappers that name no cause of their own: "failed due to earlier errors".
_WRAPPERS = ("Common 17-39", "Common 17-69", "Common 17-48")
_ERR_ID = re.compile(r"ERROR: \[([A-Za-z_ ]+ [A-Za-z0-9_]+-\d+)\]")

# Failure classes caused by the machine, not the design: worth one retry.
INFRA_RETRY = ("oom", "disk_full", "lsf_killed", "interrupted")

# A design that failed the same way on this many other runs fails that way.
REPEAT_MIN = 2

_LOGS = ("run.failed", "vivado.log", "vivado_console.log", "lsf.log")


def _text(run_dir):
    out = []
    for name in _LOGS:
        out.append(_read_text(os.path.join(run_dir, name)))
    for p in glob.glob(os.path.join(run_dir, ".vivado-ai", "vivado*.log")):
        out.append(_read_text(p))
    return "\n".join(out)


def _read_text(path, limit=8 << 20):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return ""


def first_cause(text):
    """The first Vivado error id that names a cause, or None."""
    for m in _ERR_ID.finditer(text):
        if m.group(1) not in _WRAPPERS:
            return m.group(1)
    # route_design can fail with only "Router encountered errors" and no
    # ERROR line at all; on some designs it does so on every Stage 2 recipe.
    if "[Route 35-17]" in text and "route_design failed" in text:
        return "Route 35-17 (no error message)"
    return None


def _failed_runs(output_dir):
    """Every failed run folder of a design, retired attempts included."""
    out = []
    for f in glob.glob(os.path.join(output_dir, "**", "run.failed"), recursive=True):
        out.append(os.path.dirname(f))
    return out


def _run_key(run_dir):
    """attempt_<n> folders count as the run they retried."""
    parts = os.path.abspath(run_dir).split(os.sep)
    return os.sep.join(p for p in parts if not p.startswith("attempt_"))


def verdict(output_dir, rec):
    """
    (answer, why, rule) for a pending triage record, or None to leave it to the
    agent. Pure: reads files, answers nothing.
    """
    ev = rec.get("evidence") or {}
    run_dir = ev.get("run_dir") or ""
    fclass = ev.get("failure_class") or "unknown"
    text = _text(run_dir) if run_dir else ""

    for needle, why in DETERMINISTIC:
        if needle in text:
            return False, why, "deterministic:%s" % needle

    if fclass in INFRA_RETRY:
        retried = glob.glob(os.path.join(run_dir, "attempt_*"))
        if not retried and int((rec.get("provenance") or {}).get("attempt") or 0) == 0:
            return True, ("%s is a host or farm failure, not a design one, and this run has not "
                          "been retried yet." % fclass), "infra-first-retry:%s" % fclass
        return None

    cause = first_cause(text)
    if cause:
        me = _run_key(run_dir)
        others = set()
        for d in _failed_runs(output_dir):
            key = _run_key(d)
            if key == me:
                continue
            if first_cause(_text(d)) == cause:
                others.add(key)
        if len(others) >= REPEAT_MIN:
            names = sorted(os.path.relpath(k, output_dir) for k in others)[:4]
            return False, ("Same first error [%s] as %d other runs of this design (%s): the design "
                           "fails this way under different placements, so a rerun repeats it."
                           % (cause, len(others), ", ".join(names))), "repeat:%s" % cause
    return None


def auto_triage(output_dir, log=None):
    """Answer every pending triage call `verdict` is sure of. Returns what it did and left."""
    q = decisions.Queue(output_dir)
    done, left = [], []
    for rec in q.list(state=decisions.PENDING, point="triage"):
        v = verdict(output_dir, rec)
        if v is None:
            left.append(rec["id"])
            continue
        answer, why, rule = v
        try:
            q.answer(rec["id"], {"answer": answer, "why": why}, "watch")
        except decisions.DecisionError as exc:
            left.append(rec["id"])
            if log:
                log("tc watch: could not answer %s: %s" % (rec["id"], exc))
            continue
        done.append({"design": os.path.basename(output_dir), "id": rec["id"],
                     "answer": answer, "rule": rule})
    return done, left


# ---------------------------------------------------------------------------
# housekeeping, each behind its own flag
# ---------------------------------------------------------------------------
def writable(path, size=64 << 20):
    """True when `size` bytes can be written under path (quota-full shows as ENOSPC, not df)."""
    probe = os.path.join(path, ".tc_space_probe")
    try:
        with open(probe, "wb") as fh:
            chunk = b"\0" * (1 << 20)
            for _ in range(size >> 20):
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except (IOError, OSError):
        return False
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass


def free_superseded(run_dirs, log_path=None):
    """
    Delete checkpoints a later checkpoint of the same run supersedes, and those
    of retired attempts. Never touches the last checkpoint of a run.
    Returns bytes freed.
    """
    freed = 0
    logf = open(log_path, "a") if log_path else None
    try:
        for out in run_dirs:
            for d in glob.glob(os.path.join(out, "**", ""), recursive=True):
                j = lambda f: os.path.join(d, f)          # noqa: E731
                victims = []
                if os.path.basename(d.rstrip("/")).startswith("attempt_"):
                    victims += glob.glob(j("*.dcp"))
                else:
                    if os.path.isfile(j("postplace_physopt.dcp")) and \
                            os.path.exists(j("postplace_physopt.done")):
                        victims += [j("postopt.dcp"), j("postplace.dcp")]
                    if os.path.isfile(j("postroute_physopt.dcp")) and os.path.exists(j("run.done")):
                        victims.append(j("postroute.dcp"))
                for v in victims:
                    try:
                        size = os.path.getsize(v)
                        os.unlink(v)
                    except OSError:
                        continue
                    freed += size
                    if logf:
                        logf.write(v + "\n")
    finally:
        if logf:
            logf.close()
    return freed


def kill_strays(run_dirs, backend_for, cache_path=None, log=None):
    """
    Kill farm jobs still alive in retired attempt_* folders. A retry moves the
    failed attempt aside, but a job that hung rather than exited keeps its
    slot; nothing collects it (the Rubik batch had 46 such jobs).
    """
    cache = {}
    if cache_path:
        try:
            with open(cache_path) as fh:
                cache = json.load(fh)
        except (IOError, OSError, ValueError):
            cache = {}
    killed = []
    for out in run_dirs:
        jobs = glob.glob(os.path.join(out, "**", "attempt_*", "job.json"), recursive=True)
        if not jobs:
            continue
        try:
            be = backend_for(out)
        except Exception as exc:                    # noqa: BLE001
            if log:
                log("tc watch: no backend for %s: %s" % (out, exc))
            continue
        for jf in jobs:
            handle = backends.load_handle(os.path.dirname(jf))
            if handle is None or cache.get(str(handle.job_id)):
                continue
            try:
                st = be.status(handle)
            except Exception:                       # noqa: BLE001 -- try again next call
                continue
            if st.state in backends.TERMINAL:
                cache[str(handle.job_id)] = "done"
                continue
            if st.state not in (backends.PENDING, backends.RUNNING):
                # Unknown: a job LSF has forgotten looks the same as one it
                # cannot see right now. Only kill what is positively alive.
                continue
            try:
                be.kill(handle)
                killed.append({"job_id": handle.job_id, "run_dir": handle.run_dir})
            except Exception as exc:                # noqa: BLE001
                if log:
                    log("tc watch: could not kill %s: %s" % (handle.job_id, exc))
                continue
            cache[str(handle.job_id)] = "done"
    if cache_path:
        try:
            with open(cache_path, "w") as fh:
                json.dump(cache, fh)
        except (IOError, OSError):
            pass
    return killed


def restart(output_dir, tc_argv):
    """
    `tc close` the run again with its own checkpoint; it adopts jobs still
    running. tc_argv is how to call tc (the interpreter and entry script).
    """
    st = {}
    try:
        with open(os.path.join(output_dir, "state.json")) as fh:
            st = json.load(fh)
    except (IOError, OSError, ValueError):
        pass
    dcp = st.get("dcp")
    if not dcp:
        return False, "no checkpoint in state.json"
    argv = list(tc_argv) + ["close", "--dcp", dcp, "--output-dir", output_dir, "--json"]
    if st.get("design"):
        argv += ["--design", st["design"]]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        out, err = proc.communicate(timeout=300)
    except Exception as exc:                        # noqa: BLE001
        return False, str(exc)[:300]
    if proc.returncode != 0:
        return False, (err or out or "").strip()[-300:]
    return True, ""


def session_server_up(cfg):
    """
    For a shared session server (backend.session.shared + url): is its port
    open? None when the workspace does not use one.
    """
    sess = ((cfg or {}).get("backend") or {}).get("session") or {}
    url = sess.get("url")
    if not (sess.get("shared") and url):
        return None
    m = re.match(r"\w+://([^/:]+)(?::(\d+))?", url)
    if not m:
        return None
    host, port = m.group(1), int(m.group(2) or 80)
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except (socket.error, OSError):
        return False
