#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
local.py -- run Vivado on this host, detached from the engine.

Two details carry the weight here.

**The exit code is written to a file, not waited for.** The run is wrapped in
`sh -c '... ; echo $? > vivado.exit'` and started with `start_new_session=True`,
so it survives the engine exiting and its exit code survives nobody being
around to call waitpid(). Without the file the engine could only ever report
"the pid is gone", which is what made a crashed run look alive for 15 minutes
on Ciena.

**A pid alone is not identity.** A pid can be reused, and an owner-alive check
fooled by a reused pid is a real defect in this codebase's sibling project
(vivado-ai-extensions#550). The handle records the process start time from
/proc/<pid>/stat, and liveness means "that pid exists *and* started when we
think it did".

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import os
import signal
import subprocess

from . import base

EXIT_FILE = "vivado.exit"
LOG_FILE = "vivado_console.log"


def _proc_fields(pid):
    """
    /proc/<pid>/stat from the state field onwards, or None.

    comm (field 2) may itself contain spaces and parentheses, so the split
    starts after the last ')' rather than on whitespace.
    """
    try:
        with open("/proc/%d/stat" % int(pid)) as fh:
            data = fh.read()
    except (IOError, OSError, ValueError):
        return None
    close = data.rfind(")")
    if close < 0:
        return None
    return data[close + 2:].split()


def _proc_starttime(pid):
    """Field 22 overall -- the 20th after state."""
    fields = _proc_fields(pid)
    try:
        return fields[19]
    except (IndexError, TypeError):
        return None


def _alive(pid, starttime=None):
    """
    Whether `pid` is a live process that started when we think it did.

    A zombie counts as dead. It has exited but has not been reaped, and until
    its parent waits on it the entry stays in /proc and answers kill(pid, 0) --
    so a killed run would otherwise report RUNNING for as long as the engine
    process lived. The defunct child is reaped at submit time (see _reap).
    """
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    fields = _proc_fields(pid)
    if fields is None:
        # No /proc (not Linux): fall back to the signal probe alone.
        return True
    if fields[0] == "Z":
        return False
    if starttime is None:
        return True
    try:
        return fields[19] == starttime
    except IndexError:
        return False


def _reap():
    """
    Clear any finished children of this process.

    The engine starts jobs detached and never waits on them, so without this
    every completed local run leaves a defunct entry behind for the lifetime of
    the engine.
    """
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                return
    except (OSError, ChildProcessError):
        return


class LocalBackend(base.ComputeBackend):

    name = "local"

    def __init__(self, vivado="vivado", extra_args=None):
        self.vivado = vivado
        self.extra_args = list(extra_args or [])

    def available(self):
        return True

    def submit(self, run_dir, run_tcl, resources=None):
        if not os.path.isfile(run_tcl):
            raise base.SubmitError("no run.tcl at %s" % run_tcl)
        if not os.path.isdir(run_dir):
            raise base.SubmitError("no run dir at %s" % run_dir)

        exit_path = os.path.join(run_dir, EXIT_FILE)
        log_path = os.path.join(run_dir, LOG_FILE)
        # A resubmit into a dirty directory would read a stale verdict.
        # Quarantining a *failed* run's outputs is prepare-retry's job; this
        # only clears what this backend itself writes.
        for stale in (exit_path,):
            if os.path.exists(stale):
                os.unlink(stale)

        _reap()
        argv = [self.vivado, "-mode", "batch", "-source", run_tcl]
        argv += self.extra_args
        shell_cmd = "%s > %s 2>&1; echo $? > %s" % (
            " ".join(_quote(a) for a in argv),
            _quote(log_path), _quote(exit_path))

        proc = subprocess.Popen(
            ["sh", "-c", shell_cmd], cwd=run_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

        handle = base.JobHandle(
            backend=self.name, job_id=proc.pid, run_dir=run_dir,
            log_path=log_path,
            submitted_utc=datetime.datetime.now(datetime.timezone.utc)
                            .isoformat(),
            resources=(resources.to_dict() if resources else {}))
        handle.resources["starttime"] = _proc_starttime(proc.pid)
        base.save_handle(handle)
        return handle

    def status(self, handle):
        exit_path = os.path.join(handle.run_dir, EXIT_FILE)
        if os.path.isfile(exit_path):
            try:
                with open(exit_path) as fh:
                    code = int((fh.read() or "").strip() or -1)
            except (IOError, OSError, ValueError):
                code = -1
            state = base.DONE if code == 0 else base.FAILED
            reason = None
            if code == 139:
                reason = "SIGSEGV"
            elif code < 0 or code > 128:
                reason = "signal %d" % (code - 128) if code > 128 else None
            return base.JobStatus(state, host=_hostname(), exit_code=code,
                                  term_reason=reason)

        _reap()
        starttime = (handle.resources or {}).get("starttime")
        if _alive(handle.job_id, starttime):
            return base.JobStatus(base.RUNNING, host=_hostname())
        # No exit file and no process: the job died without the wrapper
        # finishing (kill -9, host reboot). The run directory decides.
        return base.JobStatus(base.UNKNOWN, host=_hostname(),
                              term_reason="no exit file and pid gone")

    def kill(self, handle):
        starttime = (handle.resources or {}).get("starttime")
        if not _alive(handle.job_id, starttime):
            return
        try:
            os.killpg(os.getpgid(int(handle.job_id)), signal.SIGTERM)
        except OSError:
            try:
                os.kill(int(handle.job_id), signal.SIGTERM)
            except OSError:
                pass


def _hostname():
    try:
        return os.uname()[1]
    except AttributeError:
        return None


def _quote(s):
    s = str(s)
    if s and all(c.isalnum() or c in "-_./=:+," for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
