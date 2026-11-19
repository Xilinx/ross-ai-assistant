#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
rules.py -- the deterministic answer to every judgement point.

Each decision the engine queues carries a rule default, and the rule default is
what runs when nobody answers by the deadline. That is the whole reason the
engine never depends on an IDE being open: an LLM answer improves quality, it
is never required for progress.

Two sources feed these rules:

* **Vivado IDR's published metric gates** as a starting point -- Stage 3 needs
  an RQA score of at least 3 and WNS between -0.250 ns and 0 (WP535/UG906).
* **What our own suites measured**, which is where they diverge.

One rule is here specifically because an LLM could not do it reliably.
FLOW_ANALYSIS.md section 6.1b: on identical probe reports, Teradyne got an
extra `spread_balanced` run in one attempt and not in another, and Ciena the
other way round. The congestion score is a number; a rule reads it the same way
every time. The LLM keeps the judgements a rule cannot express.

Every function returns (answer, why) so the reason is recorded with the answer.

Stdlib only; never runs Vivado. Requires Python 3.6+.
"""

from __future__ import print_function

# IDR's Stage 3 window, in nanoseconds. Outside it the last mile is not worth
# a run: too far out to recover, or already closed.
STAGE3_WNS_FLOOR_NS = -0.250
STAGE3_MIN_RQA = 3

# Vivado's predicted congestion score at which report_qor_assessment says
# REVIEW. At 60-72% LUT with heavy BRAM/URAM this fires where a LUT-only
# threshold would not (Cerebras, Teradyne, 2026-10-01 suite).
CONGESTION_REVIEW_SCORE = 3
LUT_CROWDED_PCT = 75.0


def _ns(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def stage3_gate(wns_ns, rqa_score=None, closed=False):
    """
    Should the last mile (Stage 3) run?

    IDR's own gate, kept because our data agrees with it: a design further out
    than -0.250 ns does not close on a post-route phys_opt sweep or a re-route.
    """
    if closed:
        return False, "already closed; Stage 3 would only spend runs"
    wns = _ns(wns_ns)
    if wns is None:
        return False, "no parsable WNS; refusing to spend runs on a guess"
    if wns >= 0:
        return False, "WNS >= 0 without a close verdict; verify first"
    if wns < STAGE3_WNS_FLOOR_NS:
        return False, ("WNS %.3f ns is beyond the %.3f ns last-mile window"
                       % (wns, STAGE3_WNS_FLOOR_NS))
    if rqa_score is not None and _ns(rqa_score, 0) < STAGE3_MIN_RQA:
        return False, ("RQA score %s is below %d"
                       % (rqa_score, STAGE3_MIN_RQA))
    return True, ("WNS %.3f ns is inside the last-mile window" % wns)


def congestion_hedge(qor_assessment=None, lut_pct=None):
    """
    Should Stage 1 carry an extra `spread_balanced` run?

    This is the rule that replaces an unstable LLM call. Congestion is read
    from Vivado's own QoR assessment; LUT utilization is only a second trigger,
    because the two designs that needed the hedge sat at 60-72% LUT.
    """
    score = _ns((qor_assessment or {}).get("congestion_score"))
    lut = _ns(lut_pct)
    if score is not None and score >= CONGESTION_REVIEW_SCORE:
        return True, ("predicted congestion score %g is at or above the "
                      "REVIEW threshold %d" % (score, CONGESTION_REVIEW_SCORE))
    if lut is not None and lut >= LUT_CROWDED_PCT:
        return True, "LUT utilization %.1f%% is crowded" % lut
    return False, ("congestion score %s and LUT %s are both below threshold"
                   % (score, lut))


def postplace_continue(wns_ns, parent_wns_ns=None, duplicate_of=None):
    """
    Carry on past placement, or stop this run?

    Stopping here saves roughly 80% of a run (phys_opt plus route). Two
    reasons to stop, both measured:

    * the placement duplicates an earlier strategy's -- auto_1 == auto_2 and
      auto_3 == auto_4 on every design measured, about 40% of Stage 1 compute;
    * the placement is far worse than the run it was meant to beat, so routing
      it cannot produce a winner.
    """
    if duplicate_of:
        return False, "placement is identical to %s" % duplicate_of
    wns = _ns(wns_ns)
    parent = _ns(parent_wns_ns)
    if wns is None:
        return True, "no post-place WNS parsed; let the run finish"
    if parent is not None and wns < parent - 1.0:
        return False, ("post-place WNS %.3f ns is more than 1 ns behind the "
                       "parent's %.3f ns" % (wns, parent))
    return True, "post-place WNS %.3f ns is worth routing" % wns


# Failure classes orchestrate.py's run-status produces, and what to do with
# each. "Infrastructure failed us" is retried once; "Vivado said ERROR" is not.
RETRYABLE = ("oom", "lsf_killed", "license", "interrupted", "disk_full")
# Segfaults on Build 6511674 are non-deterministic -- twins passed the same
# step -- so a crash is retried once too (guardrails.retry_crash).
RETRYABLE_CRASH = ("crash",)


def triage(failure_class, attempts=0, max_retries=1, retry_crash=True):
    """Retry this failed run, or leave it failed?"""
    if attempts >= max_retries:
        return False, "already retried %d time(s)" % attempts
    if failure_class in RETRYABLE:
        return True, "%s is an infrastructure failure" % failure_class
    if retry_crash and failure_class in RETRYABLE_CRASH:
        return True, "crashes on this build are non-deterministic"
    if failure_class == "vivado_error":
        return False, "Vivado reported ERROR; a retry would repeat it"
    return False, "unrecognised failure class %r; not retried" % failure_class


def planner_round(wns_ns, rounds_done=0, max_rounds=3, closed=False):
    """
    Run another evidence-driven planner round?

    The rule default is deliberately conservative: a planner round costs an
    evidence run plus up to four fix runs, roughly half a million subagent
    tokens and 6-10 hours. Where no judge answers, the engine spends that only
    while rounds remain.
    """
    if closed:
        return False, "already closed"
    if rounds_done >= max_rounds:
        return False, "%d planner round(s) already run" % rounds_done
    if _ns(wns_ns) is None:
        return False, "no parsable WNS to plan against"
    return True, "round %d of %d" % (rounds_done + 1, max_rounds)


# Which rule answers which decision point. The queue uses this to fill in
# rule_default when a decision is opened.
POINTS = {
    "stage3": stage3_gate,
    "postplace": postplace_continue,
    "triage": triage,
    "planner": planner_round,
    "stage2": congestion_hedge,
}


def default_for(point, **kw):
    """The rule answer for a decision point, as {'answer':..., 'why':...}."""
    fn = POINTS.get(point)
    if fn is None:
        return {"answer": None, "why": "no rule for point %r" % point}
    value, why = fn(**kw)
    return {"answer": value, "why": why}
