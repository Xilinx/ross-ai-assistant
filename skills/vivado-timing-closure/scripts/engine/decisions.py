#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
decisions.py -- the judgement queue: who answers, by when, and what happens
when nobody does.

Every judgement point in the flow becomes a record with a schema, a deadline
and a rule default. Any answerer may take it -- an IDE agent through `tc
decisions`, a headless CLI, a person, or the rules -- and the first valid
answer wins. The rules always answer at the deadline, which is the property
that makes the engine IDE-agnostic: *a run never blocks on an IDE being open.*

Two design rules, both from incidents in FLOW_ANALYSIS.md section 6.2:

* **A proposed answer is not an accepted answer.** For a planner plan the
  validator is `orchestrate.py planner-validate`, shelled out to, not
  reimplemented -- that is the boundary that stops a model relaxing a
  constraint to "close" timing, and there must be exactly one of it.

* **Provenance is recorded, not inferred.** `answered_by`, the model and the
  attempt count travel with the answer and into the exported recipe. Two runs
  of the same design on the same build can otherwise diverge for reasons that
  appear nowhere in the recipe (risk R3 in ENGINE_PLAN.md).

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import datetime
import glob
import os
import re
import subprocess

from . import jsonio, paths, rules

DIRNAME = "decisions"

PENDING = "pending"
ANSWERED = "answered"

# Who may answer. `rules` is always available and always last.
# "watch": `tc watch --auto-triage`, matching a failure against signatures
# that are deterministic (watch_triage.py); recorded apart from the rules.
ANSWERERS = ("ide_agent", "headless", "human", "rules", "watch")

POINTS = ("planner", "postplace", "stage2", "stage3", "triage")

_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _parse_iso(text):
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(str(text))
    except ValueError:
        return None


class DecisionError(Exception):
    pass


class ValidationError(DecisionError):
    """The answer did not satisfy the point's schema or validator."""


class Queue(object):
    """The decision records for one design's output directory."""

    def __init__(self, output_dir):
        self.output_dir = os.path.abspath(output_dir)
        self.dir = os.path.join(self.output_dir, DIRNAME)

    # -- storage --------------------------------------------------------

    def _path(self, decision_id):
        if not _ID_OK.match(str(decision_id) or ""):
            raise DecisionError("bad decision id %r" % decision_id)
        return os.path.join(self.dir, "%s.json" % decision_id)

    def get(self, decision_id):
        rec = jsonio.read(self._path(decision_id))
        if rec is None:
            raise DecisionError("no decision %r in %s"
                                % (decision_id, self.dir))
        return rec

    def exists(self, decision_id):
        return os.path.isfile(self._path(decision_id))

    def list(self, state=None, point=None):
        out = []
        for path in sorted(glob.glob(os.path.join(self.dir, "*.json"))):
            rec = jsonio.read(path)
            if rec is None:
                continue
            if state and rec.get("state") != state:
                continue
            if point and rec.get("point") != point:
                continue
            out.append(rec)
        out.sort(key=lambda r: (r.get("deadline_utc") or "", r.get("id") or ""))
        return out

    # -- lifecycle ------------------------------------------------------

    def open(self, decision_id, point, design=None, evidence=None,
             schema=None, deadline_s=None, rule_default=None, prompt=None):
        """
        Create a decision. Idempotent: re-opening an existing id returns it
        unchanged, so a restart mid-flow does not reset a deadline or discard
        an answer that already arrived.
        """
        if point not in POINTS:
            raise DecisionError("unknown decision point %r (have: %s)"
                                % (point, ", ".join(POINTS)))
        if self.exists(decision_id):
            return self.get(decision_id)

        now = _utc_now()
        # `0` is meaningful and distinct from None: it means "no better answer
        # is possible, apply the rule now". None means "wait indefinitely".
        deadline = (now + datetime.timedelta(seconds=int(deadline_s))
                    if deadline_s is not None else None)
        rec = {
            "id": str(decision_id),
            "design": design,
            "point": point,
            "state": PENDING,
            "created_utc": _iso(now),
            "deadline_utc": _iso(deadline) if deadline else None,
            "evidence": evidence or {},
            "schema": schema or ("plan.v1" if point == "planner"
                                 else "gate.v1"),
            "prompt": prompt,
            "rule_default": rule_default or {"answer": None,
                                             "why": "no rule supplied"},
            "answered_by": None,
            "answer": None,
            "answered_utc": None,
            "validated": None,
            "provenance": {"judge": None, "model": None, "attempt": 0,
                           "duration_s": None},
        }
        jsonio.write(self._path(decision_id), rec)
        return rec

    def answer(self, decision_id, answer, answered_by, model=None,
               duration_s=None, validate=True, usage=None):
        """
        Record an answer. Raises ValidationError when it does not satisfy the
        point's validator, leaving the decision pending for the next answerer.
        """
        if answered_by not in ANSWERERS:
            raise DecisionError("unknown answerer %r (have: %s)"
                                % (answered_by, ", ".join(ANSWERERS)))
        rec = self.get(decision_id)
        if rec.get("state") == ANSWERED:
            raise DecisionError(
                "decision %r was already answered by %s"
                % (decision_id, rec.get("answered_by")))

        rec["provenance"]["attempt"] = rec["provenance"].get("attempt", 0) + 1
        if validate:
            try:
                self.validate(rec, answer)
            except ValidationError:
                # Keep the failed attempt count, stay pending.
                jsonio.write(self._path(decision_id), rec)
                raise

        rec["state"] = ANSWERED
        rec["answer"] = answer
        rec["answered_by"] = answered_by
        rec["answered_utc"] = _iso(_utc_now())
        rec["validated"] = bool(validate)
        rec["provenance"]["judge"] = answered_by
        rec["provenance"]["model"] = model
        rec["provenance"]["duration_s"] = duration_s
        if usage:
            # The answering call's tokens and cost (headless judge only; see
            # token_usage.py for why other answerers carry none).
            rec["provenance"]["usage"] = usage
        jsonio.write(self._path(decision_id), rec)
        return rec

    def expire_due(self, now=None):
        """
        Answer every past-deadline pending decision from its rule default.

        This is the method that guarantees progress. It is called by `tc serve`
        on every poll, and separately by `tc decisions expire`.
        """
        now = now or _utc_now()
        fired = []
        for rec in self.list(state=PENDING):
            deadline = _parse_iso(rec.get("deadline_utc"))
            if deadline is None or deadline > now:
                continue
            default = rec.get("rule_default") or {}
            rec["state"] = ANSWERED
            rec["answer"] = default.get("answer")
            rec["answered_by"] = "rules"
            rec["answered_utc"] = _iso(now)
            rec["validated"] = True
            rec["provenance"]["judge"] = "rules"
            rec["provenance"]["model"] = None
            jsonio.write(self._path(rec["id"]), rec)
            fired.append(rec)
        return fired

    def apply_rule(self, decision_id, why=None):
        """
        Answer one decision from its rule default, now, without waiting.

        For when the deadline has stopped meaning anything: the only answerer
        that could have improved on the rule has just failed, and there is no
        inbox for a person to find. Waiting out the hour would spend it on
        nobody.
        """
        rec = self.get(decision_id)
        if rec.get("state") == ANSWERED:
            return rec
        default = rec.get("rule_default") or {}
        rec["state"] = ANSWERED
        rec["answer"] = default.get("answer")
        rec["answered_by"] = "rules"
        rec["answered_utc"] = _iso(_utc_now())
        rec["validated"] = True
        rec["provenance"]["judge"] = "rules"
        rec["provenance"]["why"] = why or "no answerer available"
        jsonio.write(self._path(decision_id), rec)
        return rec

    def resolve(self, decision_id, now=None):
        """
        The answer to act on: the recorded one, or the rule default once the
        deadline has passed. Returns None while a decision is still open.
        """
        rec = self.get(decision_id)
        if rec.get("state") == ANSWERED:
            return rec.get("answer")
        deadline = _parse_iso(rec.get("deadline_utc"))
        if deadline is not None and deadline <= (now or _utc_now()):
            fired = self.expire_due(now)
            if any(r["id"] == rec["id"] for r in fired):
                return self.get(decision_id).get("answer")
        return None

    # -- validation -----------------------------------------------------

    def validate(self, rec, answer):
        """
        Check an answer against its point's contract.

        `planner` delegates to `orchestrate.py planner-validate`, which owns
        the fix-kind allow-list, the physical-only XDC whitelist and the
        rejection of timing exceptions and Tcl escapes. Reimplementing any of
        that here would create a second, weaker boundary.
        """
        point = rec.get("point")
        if point == "planner":
            return self._validate_plan(rec, answer)
        return self._validate_gate(rec, answer)

    def _validate_gate(self, rec, answer):
        if not isinstance(answer, dict):
            raise ValidationError("answer must be an object, got %s"
                                  % type(answer).__name__)
        if "answer" not in answer:
            raise ValidationError("answer object needs an 'answer' key")
        if not isinstance(answer["answer"], bool):
            raise ValidationError("'answer' must be true or false, got %r"
                                  % (answer["answer"],))
        why = answer.get("why")
        if not why or not str(why).strip():
            raise ValidationError("'why' is required: the reason is recorded "
                                  "with the answer and read by the next round")
        return True

    def _validate_plan(self, rec, answer):
        plan_path = answer.get("plan_path") if isinstance(answer, dict) else None
        if not plan_path:
            raise ValidationError(
                "a planner answer must be {'plan_path': '<path to plan.json>'}")
        if not os.path.isfile(plan_path):
            raise ValidationError("no plan at %s" % plan_path)
        try:
            cmd = paths.orchestrate_cmd() + [
                "planner-validate", "--output-dir", self.output_dir,
                "--plan", plan_path]
            # planner.early answers the round beside Stage 2. Validated as a
            # normal round, the plan was refused with "no planner round
            # started (run planner-start)"; an agent did exactly that, with
            # --force, and moved the flow past an uncollected Stage 2.
            if rec.get("id") == "planner.early":
                cmd.append("--early")
        except paths.SkillNotFound as exc:
            raise ValidationError(str(exc))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                universal_newlines=True)
        out, _ = proc.communicate()
        if proc.returncode != 0:
            raise ValidationError("planner-validate rejected the plan "
                                  "(rc=%d): %s"
                                  % (proc.returncode, (out or "").strip()[:800]))
        return True

    # -- reporting ------------------------------------------------------

    def provenance_summary(self):
        """
        Who answered what, for the exported recipe.

        Without this a replay that diverges is unexplainable: the same design
        on the same build can take a different path because a human was at
        their desk for one run and the rules fired for the other.
        """
        recs = self.list()
        by_answerer = {}
        for r in recs:
            by_answerer[r.get("answered_by") or PENDING] = (
                by_answerer.get(r.get("answered_by") or PENDING, 0) + 1)
        return {
            "total": len(recs),
            "by_answerer": by_answerer,
            "decisions": [
                {"id": r["id"], "point": r["point"],
                 "answered_by": r.get("answered_by"),
                 "model": (r.get("provenance") or {}).get("model"),
                 "usage": (r.get("provenance") or {}).get("usage"),
                 "attempts": (r.get("provenance") or {}).get("attempt", 0),
                 "answer": r.get("answer"),
                 "rule_default": (r.get("rule_default") or {}).get("answer"),
                 "followed_rule": (
                     r.get("answer") == (r.get("rule_default") or {}).get("answer")),
                 }
                for r in recs
            ],
        }


def open_with_rule(queue, decision_id, point, deadline_s=None, design=None,
                   evidence=None, prompt=None, **rule_kw):
    """Open a decision whose rule default comes from rules.default_for."""
    return queue.open(decision_id, point, design=design, evidence=evidence,
                      deadline_s=deadline_s, prompt=prompt,
                      rule_default=rules.default_for(point, **rule_kw))
