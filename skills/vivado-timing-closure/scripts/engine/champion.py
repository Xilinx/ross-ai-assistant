#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
champion.py -- keep exactly one finished design loaded, and make it the right one.

The planner's best work comes from asking a *routed* design questions, each one
informed by the last answer. On Ciena five such queries produced findings E4-E6
-- GT quad placement, per-module clocks, and the 195 MHz crossing pipeline
sitting at +2.2 ns slack while the 390 MHz FIFOs were on the far SLR -- and that
is what closed a design the fixed menu could not. None of it was in a
pre-generated report.

Re-opening a routed checkpoint to ask those questions costs minutes on a large
design (design A: ~986k cells, ~20 GB peak). Keeping the session that *produced*
it costs nothing extra -- the design is already in memory.

The rule here is what keeps that affordable:

    **At most one session is held, and it is the best result so far.**

Whenever a run finishes it is ranked against the current champion. A better run
takes the title and the previous champion's session is released. A worse run is
released immediately. Holding every finished run instead would cost 6 x 20 GB
across a seven-way Stage 1 to save one checkpoint open, and six of those
sessions would never be queried.

No timeout is involved: the title changes only when a genuinely better result
arrives, so the policy is self-regulating.

Two boundaries matter:

* **Ranking is not reimplemented.** `decision_engine.pick_best_run` decides,
  the same hold-first comparison the collect steps use. A second copy of that
  rule in the engine would eventually disagree with the verdict in the report.

* **The title freezes at collect.** Once the judge may be querying, a
  late-finishing run must not swap the session out from under it.

The champion at finish time is a *prediction* of what the collect step will
choose, not the verdict. Collect applies rules this comparison cannot see -- a
run with a post-route WNS but no `postroute.dcp` is not ranked at all, and a
duplicate-placement run stops early with partial results. When the prediction is
wrong the cost is one checkpoint open, so disagreements are logged rather than
prevented.

Stdlib only (plus the deterministic layer's pure functions). Requires Python 3.6+.
"""

from __future__ import print_function

import os

from . import backends, jsonio, paths

STATE_FILE = "champion.json"

# The stage whose reports decide the ranking. postroute_physopt is the last
# thing a complete run writes, and the same stage the collect steps rank on.
DEFAULT_STAGE = "postroute_physopt"


class Champion(object):

    def __init__(self, output_dir, backend, stage=DEFAULT_STAGE, log=None):
        self.output_dir = os.path.abspath(output_dir)
        self.backend = backend
        self.stage = stage
        self.log = log or (lambda msg: None)
        self._frozen = False
        self._state = jsonio.read(self._path()) or {}

    def _path(self):
        return os.path.join(self.output_dir, STATE_FILE)

    def _save(self):
        jsonio.write(self._path(), self._state)

    # -- state ----------------------------------------------------------

    @property
    def current(self):
        """The held run as {'run_id', 'run_dir', 'wns', 'whs', 'tns'}, or None."""
        return self._state.get("champion")

    @property
    def frozen(self):
        return self._frozen

    def freeze(self):
        """Stop swapping. Called at a collect step, before any judge query."""
        self._frozen = True

    def thaw(self):
        """Allow swapping again, for the next stage's fan-out."""
        self._frozen = False

    # -- ranking --------------------------------------------------------

    def _parse(self, run_id, run_dir):
        """Timing figures for a finished run, or None when unparsable."""
        try:
            report_parser = paths.import_script("report_parser")
        except Exception as exc:                      # noqa: BLE001
            self.log("champion: cannot load report_parser: %s" % exc)
            return None
        try:
            run = report_parser.parse_run(run_dir, stage=self.stage,
                                          run_id=run_id)
        except Exception as exc:                      # noqa: BLE001
            # A half-written or crashed run is simply not a candidate. It must
            # never take the engine down.
            self.log("champion: cannot parse %s: %s" % (run_id, exc))
            return None
        if not run or run.get("wns") is None:
            return None
        run["run_id"] = run_id
        run["run_dir"] = run_dir
        return run

    def _better(self, candidate, incumbent):
        """Whether `candidate` beats `incumbent` under the collect ordering."""
        decision_engine = paths.import_script("decision_engine")
        best = decision_engine.pick_best_run([incumbent, candidate])
        return best.get("run_id") == candidate.get("run_id")

    # -- the policy -----------------------------------------------------

    def offer(self, run_id, run_dir):
        """
        A run has finished. Decide whether to hold its session.

        Returns {'kept': bool, 'released': <run_id or None>, 'reason': str}.

        This never raises. Keeping a session is an optimisation -- it saves the
        judge one checkpoint open -- so no failure in here is worth stalling a
        flow that has already spent hours of farm time. A crashed run, an
        unreadable report, an unreachable session: all degrade to "not held",
        and the judge re-opens a checkpoint as it would have anyway.
        """
        try:
            return self._offer(run_id, run_dir)
        except Exception as exc:                      # noqa: BLE001
            self.log("champion: %s not considered: %s" % (run_id, exc))
            self._release(run_id, run_dir)
            return {"kept": False, "released": run_id,
                    "reason": "error while ranking: %s" % exc}

    def _offer(self, run_id, run_dir):
        if self._frozen:
            self._release(run_id, run_dir)
            return {"kept": False, "released": run_id,
                    "reason": "champion frozen at collect"}

        candidate = self._parse(run_id, run_dir)
        if candidate is None:
            self._release(run_id, run_dir)
            return {"kept": False, "released": run_id,
                    "reason": "no parsable WNS; not a candidate"}

        incumbent = self.current
        if incumbent is None:
            self._crown(candidate)
            return {"kept": True, "released": None,
                    "reason": "first finished run with a parsable result"}

        if not self._better(candidate, incumbent):
            self._release(run_id, run_dir)
            return {"kept": False, "released": run_id,
                    "reason": "does not beat %s (WNS %s vs %s)"
                              % (incumbent.get("run_id"),
                                 _fmt(candidate.get("wns")),
                                 _fmt(incumbent.get("wns")))}

        # A better result: the old champion's session has no further use.
        self._release(incumbent.get("run_id"), incumbent.get("run_dir"))
        self._crown(candidate)
        return {"kept": True, "released": incumbent.get("run_id"),
                "reason": "beats %s (WNS %s vs %s)"
                          % (incumbent.get("run_id"),
                             _fmt(candidate.get("wns")),
                             _fmt(incumbent.get("wns")))}

    def _crown(self, run):
        self._state["champion"] = {
            "run_id": run.get("run_id"),
            "run_dir": run.get("run_dir"),
            "wns": run.get("wns"),
            "whs": run.get("whs"),
            "tns": run.get("tns"),
        }
        self._save()
        self.log("champion: holding %s (WNS %s)"
                 % (run.get("run_id"), _fmt(run.get("wns"))))

    def _release(self, run_id, run_dir):
        """Stop a session we are not keeping. A batch run has already exited."""
        if not run_dir:
            return
        handle = backends.load_handle(run_dir)
        if handle is None:
            return
        try:
            self.backend.release(handle)
        except Exception as exc:                      # noqa: BLE001
            self.log("champion: could not release %s: %s" % (run_id, exc))

    def release_all(self):
        """Drop the held session. Called when the flow is finished with it."""
        held = self.current
        if held:
            self._release(held.get("run_id"), held.get("run_dir"))
        self._state.pop("champion", None)
        self._save()

    # -- honesty about the prediction -----------------------------------

    def check_against(self, verdict_run_id):
        """
        Compare the held champion with what the collect step actually chose.

        Logged rather than corrected: a disagreement costs one checkpoint open,
        and how often it happens is the measurement that says whether this
        heuristic is good enough.
        """
        held = (self.current or {}).get("run_id")
        agreed = bool(verdict_run_id) and held == verdict_run_id
        self._state["last_verdict"] = {
            "held": held, "verdict": verdict_run_id, "agreed": agreed,
        }
        self._save()
        if not agreed:
            self.log("champion: held %s but collect chose %s; the judge will "
                     "re-open a checkpoint" % (held, verdict_run_id))
        return agreed


def _fmt(value):
    return "n/a" if value is None else "%.3f" % value
