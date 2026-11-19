#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
failover.py -- launch through MCP while it works, through bsub when it does not.

The MCP server is a launcher, not a dependency of the runs: a run's script is
sourced at job start and ends with `exit`, so it finishes with or without the
server (a 2026-09-29 disconnect, a 2026-10-04 build switch with 42 jobs in
flight, a 2026-10-07 smoke job that finished after its server had exited).
What a dead server costs is *new* launches. So:

* a submit whose launcher is down (LauncherUnavailable) is retried with a
  fresh private server, `restarts` times with a short backoff;
* if it still cannot start, this design's new runs go through bsub for the
  rest of its run -- recorded in backend.json and the supervisor log, and
  never switched back, so a flapping server cannot split a stage across paths
  more than once;
* runs already started keep the path they started with: status, kill and
  release go to the backend named in each run's job.json.
* refusals that keep coming (`refusals_to_failover` in a row, no start in
  between) are an outage too: a launcher whose jobs all exit before their
  session is ready looks like a string of refusals, one per job.

A refusal of a particular job (queue/OS infeasible) is not an outage and is
passed through unchanged.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import time

from . import base


class FailoverBackend(base.ComputeBackend):

    def __init__(self, primary, fallback, fallback_resources, on_failover=None,
                 restarts=3, backoff=(0, 5, 15), sleep=time.sleep, log=None,
                 refusals_to_failover=3):
        self.primary = primary
        self.fallback = fallback
        # Resources for a bsub run, from the site's lsf block (the session's
        # own OS term would be wrong there: "ws8" is not "osver==ws8").
        self.fallback_resources = fallback_resources
        self.on_failover = on_failover or (lambda reason: None)
        self.restarts = max(1, int(restarts))
        self.backoff = list(backoff) or [0]
        self.sleep = sleep
        self.log = log or (lambda msg: None)
        self.degraded = None              # reason, once switched
        self.refusals_to_failover = max(1, int(refusals_to_failover))
        self._refusals = 0                # consecutive refusals from the primary

    # Looks like the primary to the rest of the engine (concurrency settings,
    # the champion's keep-alive support).
    @property
    def name(self):
        return self.primary.name

    @property
    def supports_keepalive(self):
        return getattr(self.primary, "supports_keepalive", False) and not self.degraded

    def set_log(self, log):
        self.log = log
        if hasattr(self.primary, "log"):
            self.primary.log = log

    def _for(self, handle):
        return self.fallback if handle.backend == self.fallback.name else self.primary

    # -- interface ------------------------------------------------------

    def available(self):
        return self.primary.available() or self.fallback.available()

    def submit(self, run_dir, run_tcl, resources=None):
        if self.degraded is None:
            last, tries = None, self.restarts
            for i in range(self.restarts):
                try:
                    handle = self.primary.submit(run_dir, run_tcl, resources)
                    self._refusals = 0
                    return handle
                except base.LauncherUnavailable as exc:
                    last = exc
                    self.log("tc serve: MCP launcher unavailable (%d/%d): %s"
                             % (i + 1, self.restarts, str(exc)[:200]))
                    if hasattr(self.primary, "reset"):
                        self.primary.reset()
                    self.sleep(self.backoff[min(i, len(self.backoff) - 1)])
                except base.SubmitError as exc:
                    self._refusals += 1
                    if self._refusals < self.refusals_to_failover:
                        raise
                    last, tries = exc, self._refusals
                    self.log("tc serve: %d MCP launches in a row refused: %s"
                             % (self._refusals, str(exc)[:200]))
                    break
            self.degraded = ("the MCP server could not start runs after %d tries: %s"
                             % (tries, str(last)[:200]))
            self.log("tc serve: new runs go through bsub from now on -- %s" % self.degraded)
            self.on_failover(self.degraded)
        job_name = getattr(resources, "job_name", None)
        return self.fallback.submit(run_dir, run_tcl, self.fallback_resources(job_name))

    def status(self, handle):
        return self._for(handle).status(handle)

    def kill(self, handle):
        return self._for(handle).kill(handle)

    def logs(self, handle):
        return self._for(handle).logs(handle)

    def release(self, handle):
        target = self._for(handle)
        if hasattr(target, "release"):
            return target.release(handle)
        return None
