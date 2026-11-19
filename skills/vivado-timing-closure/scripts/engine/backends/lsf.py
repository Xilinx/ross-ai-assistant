#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
lsf.py -- submit Vivado to LSF as a batch job.

`bsub vivado -mode batch -source run.tcl`. No interactive session, no proxy, no
MCP: FLOW_ANALYSIS.md section 6.3 records what the interactive path cost us --
a backend switch on the shared :18090 server orphaned sessions and killed three
design A runs, and a Tcl error left Vivado idle holding an ~18 GB slot for
hours.

Three things this module is careful about, each from a real incident:

* **Termination reason is read, not inferred.** `TERM_RUNLIMIT` in the session
  log was classified `unknown` (and so never retried) whenever the caller
  passed no `--lsf-status`. `status()` reports it from bjobs, falling back to
  the output log once the job has left the queue, so `orchestrate.py
  run-status --lsf-status` can classify it `lsf_killed` and retry it.

* **OS selection is passed through verbatim.** On the xsj farm `rhel8` is
  silently ignored where `ws8` works. Guessing here would hide that; the
  configured value goes straight into the select clause.

* **A job LSF still calls RUN may already be dead.** `status()` never
  contradicts the run directory -- it supplies evidence, and
  `orchestrate.py run-status` decides.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import os
import re
import subprocess

from . import base

LOG_FILE = "lsf.log"

_ACK = re.compile(r"Job\s*<(\d+)>\s*is submitted to\s*(?:default\s*)?queue\s*<([^>]+)>")
_TERM = re.compile(r"\b(TERM_[A-Z_]+)\b")
_EXIT_CODE = re.compile(r"Exited with exit code\s+(\d+)")

# bjobs STAT -> our coarse state. UNKWN/ZOMBI stay UNKNOWN so the files decide.
_STAT = {
    "PEND": base.PENDING, "PROV": base.PENDING, "WAIT": base.PENDING,
    "RUN": base.RUNNING,
    "DONE": base.DONE,
    "EXIT": base.FAILED,
    "PSUSP": base.PENDING, "USUSP": base.RUNNING, "SSUSP": base.RUNNING,
}


class LSFBackend(base.ComputeBackend):

    name = "lsf"

    def __init__(self, vivado="vivado", env_script=None, bsub="bsub",
                 bjobs="bjobs", bkill="bkill", extra_args=None):
        self.vivado = vivado
        self.env_script = env_script
        self.bsub = bsub
        self.bjobs = bjobs
        self.bkill = bkill
        self.extra_args = list(extra_args or [])

    # -- plumbing -------------------------------------------------------

    def _run(self, argv, cwd=None):
        """Run an LSF command, sourcing the env script first when configured."""
        if self.env_script:
            cmd = ". %s >/dev/null 2>&1; %s" % (
                _quote(self.env_script), " ".join(_quote(a) for a in argv))
            argv = ["sh", "-c", cmd]
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    universal_newlines=True)
        except OSError as exc:
            # No LSF client on PATH: an unreachable scheduler, not a crash.
            return 127, str(exc)
        out, _ = proc.communicate()
        return proc.returncode, out or ""

    def available(self):
        rc, _ = self._run([self.bjobs, "-V"])
        return rc == 0

    def build_argv(self, run_dir, run_tcl, res):
        """The bsub command line. Separated out so tests can assert on it."""
        log_path = os.path.join(run_dir, LOG_FILE)
        argv = [self.bsub, "-o", log_path]
        if res.job_name:
            argv += ["-J", res.job_name]
        if res.queue:
            argv += ["-q", res.queue]
        if res.slots:
            argv += ["-n", str(res.slots)]
        if res.runtime_limit_min:
            argv += ["-W", str(res.runtime_limit_min)]

        select = []
        if res.os_type:
            # Verbatim: see the module docstring on rhel8 vs ws8. A value with
            # its own operator ("osver==ws8") is a whole select term; on the xsj
            # farm the OS lives in `osver` and `type` is X86_64 on every host,
            # so "type==ws8" matches nothing.
            term = res.os_type if "=" in res.os_type else "type==%s" % res.os_type
            select.append(term)
        rusage = []
        if res.memory_mb:
            rusage.append("mem=%d" % int(res.memory_mb))
        clause = ""
        if select:
            clause += "select[%s] " % " && ".join(select)
        if rusage:
            clause += "rusage[%s]" % ":".join(rusage)
        if clause.strip():
            argv += ["-R", clause.strip()]

        argv += list(res.extra)
        argv += [self.vivado, "-mode", "batch", "-source", run_tcl]
        argv += self.extra_args
        return argv, log_path

    # -- interface ------------------------------------------------------

    def submit(self, run_dir, run_tcl, resources=None):
        if not os.path.isfile(run_tcl):
            raise base.SubmitError("no run.tcl at %s" % run_tcl)
        res = resources or base.Resources()
        argv, log_path = self.build_argv(run_dir, run_tcl, res)

        rc, out = self._run(argv, cwd=run_dir)
        m = _ACK.search(out)
        if rc != 0 or not m:
            raise base.SubmitError(
                "bsub failed (rc=%s): %s" % (rc, out.strip()[:500]))

        handle = base.JobHandle(
            backend=self.name, job_id=m.group(1), run_dir=run_dir,
            log_path=log_path,
            submitted_utc=datetime.datetime.now(datetime.timezone.utc)
                            .isoformat(),
            resources=res.to_dict())
        handle.resources["queue"] = handle.resources.get("queue") or m.group(2)
        base.save_handle(handle)
        return handle

    def status(self, handle):
        rc, out = self._run([
            self.bjobs, "-noheader", "-o",
            "stat exit_code exec_host exit_reason delimiter='|'",
            str(handle.job_id)])

        if rc == 0 and out.strip() and "not found" not in out:
            parts = [p.strip() for p in out.strip().splitlines()[0].split("|")]
            stat = parts[0] if parts else ""
            state = _STAT.get(stat.upper(), base.UNKNOWN)
            exit_code = _int_or_none(parts[1] if len(parts) > 1 else None)
            host = _clean(parts[2] if len(parts) > 2 else None)
            reason = _clean(parts[3] if len(parts) > 3 else None)
            if not reason:
                reason = self._term_from_log(handle)
            return base.JobStatus(state, host=host, exit_code=exit_code,
                                  term_reason=reason, raw=out.strip())

        # Out of the queue (bjobs forgets finished jobs): the log is all we have.
        return self._status_from_log(handle, raw=out.strip())

    def kill(self, handle):
        self._run([self.bkill, str(handle.job_id)])

    # -- log fallback ---------------------------------------------------

    def _read_log(self, handle):
        path = handle.log_path or os.path.join(handle.run_dir, LOG_FILE)
        try:
            with open(path, "rb") as fh:
                # The LSF epilogue is at the end; 64 KB is more than enough and
                # bounds the read on a job whose stdout ran to gigabytes.
                try:
                    fh.seek(-65536, os.SEEK_END)
                except (IOError, OSError):
                    fh.seek(0)
                return fh.read().decode("utf-8", "replace")
        except (IOError, OSError):
            return ""

    def _term_from_log(self, handle):
        m = _TERM.search(self._read_log(handle))
        return m.group(1) if m else None

    def _status_from_log(self, handle, raw=None):
        text = self._read_log(handle)
        if not text:
            return base.JobStatus(base.UNKNOWN, raw=raw)
        term = _TERM.search(text)
        if term:
            return base.JobStatus(base.FAILED, term_reason=term.group(1),
                                  raw=raw)
        if "Successfully completed." in text:
            return base.JobStatus(base.DONE, exit_code=0, raw=raw)
        m = _EXIT_CODE.search(text)
        if m:
            return base.JobStatus(base.FAILED, exit_code=int(m.group(1)),
                                  raw=raw)
        return base.JobStatus(base.UNKNOWN, raw=raw)


def _int_or_none(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _clean(s):
    s = (s or "").strip()
    return s if s and s != "-" else None


def _quote(s):
    s = str(s)
    if s and all(c.isalnum() or c in "-_./=:+," for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"
