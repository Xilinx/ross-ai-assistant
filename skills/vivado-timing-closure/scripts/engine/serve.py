#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
serve.py -- the loop. Replaces workflows/timing-closure.js.

    while phase != complete:
        st = status(--json)
        submit whatever is pending, up to the concurrency window
        poll sentinels; classify failures; retry the retryable
        when the worklist is finished, run next_step
        answer any overdue decision from its rule

What the workflow did with one agent per run, this does with a backend and a
poll. The behaviour that is kept is the behaviour that was *earned*: a sliding
window rather than fixed batches (we watched auto_5 run alone at the end of a
batch), resume from sentinels that never relaunches finished work, a bounded
retry policy for infrastructure failures only, and `on_run_failure: stop`
returning the evidence rather than ploughing on (except in Stage 1, whose
strategies are independent: `on_stage1_failure: continue`).

What is deliberately dropped is the prompt discipline that only existed because
an agent was in a conversation and holding a Vivado session:

  * the MANDATE prefix (agents acted on the user's chat messages and got four
    design B LSF jobs killed) -- there is no conversation here;
  * verbatim-JSON relay rules -- the engine reads orchestrate.py's JSON itself;
  * STILL_RUNNING_DETACHED handling -- there is no interactive proxy;
  * "do not improvise LSF options" -- the agent never submits anything.

**Decisions never block the loop.** Every judgement point is opened with a
deadline and a rule default; `expire_due` runs on every tick. An answer from an
IDE agent, a headless CLI or a person improves the outcome, and its absence
only means the rule was used.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import glob
import io
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stdout

from . import (backends, champion, config, decisions, jsonio, judge, paths,
               rule_planner, rules, staging, token_usage)

# Steps that consume a finished worklist. The loop must not run one of these
# while any run in the current worklist is still writing: collecting early
# locks in a partial result, which orchestrate.py also refuses.
COLLECT_STEPS = ("probe-collect", "stage1-collect", "stage2-collect",
                 "stage3-collect", "planner-collect", "finalize")

STOPPED = "stopped"
COMPLETE = "complete"
NEEDS_ATTENTION = "needs_attention"
BUDGET_SPENT = "budget_spent"


class Driver(object):
    """How the loop talks to the deterministic layer. Faked in tests."""

    def status(self):
        raise NotImplementedError

    def step(self, name, extra=None):
        """Run one orchestrate.py command. Returns (rc, combined output)."""
        raise NotImplementedError


PLANNER_STEP = "planner-plan"     # an agent's step in the old workflow; the engine asks it
EARLY_EVIDENCE = "PLANNER_EVIDENCE_EARLY"
# Phases in which Path B (R2) runs are made as Stage 1 runs reach placement.
PATH_B_PHASES = ("stage1_ready", "stage1_rerun", "stage1_done", "stage2_detect",
                 "stage2_ready")
# Phases whose runs are Stage 1 strategies (serve.on_stage1_failure).
STAGE1_PHASES = ("stage1_ready", "stage1_rerun")


class OrchestrateDriver(Driver):

    def __init__(self, output_dir):
        self.output_dir = os.path.abspath(output_dir)
        self._orc = None

    def _cmd(self, argv):
        return paths.orchestrate_cmd() + list(argv)

    # `status` and `run-status` only read, and the supervisor asks for them on
    # every poll: the flow's status once a tick and a run's status for every
    # run it watches. As subprocesses, the Rubik 101 batch (2026-10-17) started
    # 14 Pythons a second, each importing orchestrate from NFS (about 200 file
    # calls, 5 s on the loaded file server). They run in this process now;
    # anything that writes state still goes through orchestrate's CLI.
    def _inproc(self):
        if self._orc is None:
            try:
                self._orc = paths.import_script("orchestrate")
            except Exception:           # noqa: BLE001 -- the CLI still works
                self._orc = False
        return self._orc or None

    def status(self):
        orc = self._inproc()
        if orc is not None:
            buf = io.StringIO()
            with redirect_stdout(buf):
                orc.cmd_status(argparse.Namespace(output_dir=self.output_dir,
                                                  json=True, compact=False))
            return json.loads(buf.getvalue())
        proc = subprocess.Popen(
            self._cmd(["status", "--output-dir", self.output_dir, "--json"]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("status failed (rc=%d): %s"
                               % (proc.returncode, (err or out).strip()[:400]))
        return json.loads(out)

    def state(self):
        """The flow's state.json (read-only), or {}."""
        return jsonio.read(os.path.join(self.output_dir, "state.json")) or {}

    def planner_context(self, early=False):
        """What a planner needs for the current round (orchestrate planner-context)."""
        proc = subprocess.Popen(
            self._cmd(["planner-context", "--output-dir", self.output_dir]
                      + (["--early"] if early else [])),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        out, err = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("planner-context failed (rc=%d): %s"
                               % (proc.returncode, error_tail(err or out)))
        return json.loads(out)

    def step(self, name, extra=None):
        argv = [name, "--output-dir", self.output_dir] + list(extra or [])
        proc = subprocess.Popen(self._cmd(argv), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                universal_newlines=True)
        out, _ = proc.communicate()
        return proc.returncode, out or ""

    # Where each collect step records the run it chose. Read only to compare
    # the held session against the verdict -- never to make a decision.
    BEST_FILES = {
        "stage1-collect": "stage1_best.json",
        "stage2-collect": "stage2_best.json",
        "stage3-collect": "stage3_best.json",
    }

    def winner(self, step):
        """The run id a collect step chose, or None when it is not recorded."""
        name = self.BEST_FILES.get(step)
        if not name:
            return None
        data = jsonio.read(os.path.join(self.output_dir, name)) or {}
        return data.get("best_run_id")

    def run_status(self, run_dir, term_reason=None, exit_code=None, job_state=None):
        # run-status always prints JSON. It once got a `--json` it does not
        # accept, so every verdict was "unknown" and a failed run (a probe that
        # could not open the checkpoint) was waited on forever.
        argv = ["run-status", "--run-dir", run_dir]
        if term_reason:
            argv += ["--lsf-status", term_reason]
        if exit_code is not None:
            argv += ["--session-exit", str(exit_code)]
        # The scheduler's word that the job is still queued or running: a run
        # with no output yet is then waiting, never stale.
        if job_state in (backends.PENDING, backends.RUNNING):
            argv += ["--job-state", job_state]
        orc = self._inproc()
        if orc is not None:
            try:
                p, state = orc._context_for(run_dir, self.output_dir)
                return orc.run_status(run_dir, orc.load_config(), state, p, exit_code, False,
                                      term_reason, job_state if "--job-state" in argv else None)
            except Exception as e:      # noqa: BLE001 -- as the CLI's crash would read
                return {"state": "unknown", "failure_class": "unknown",
                        "evidence": [("run-status: %s" % e)[:200]]}
        proc = subprocess.Popen(self._cmd(argv), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                universal_newlines=True)
        out, _ = proc.communicate()
        try:
            return json.loads(out)
        except ValueError:
            return {"state": "unknown", "failure_class": "unknown",
                    "evidence": [(out or "").strip()[:200]]}


def error_tail(text, lines=8, limit=1200):
    """
    The end of a failed command's output: a Python traceback names the error
    in its last lines, so keeping the first 400 characters (as the planner
    round used to) kept only "Traceback ... File ..." and lost the cause.
    """
    kept = [l for l in (text or "").strip().splitlines() if l.strip()][-lines:]
    return " | ".join(l.strip() for l in kept)[-limit:]


# Who answered a decision, for the supervisor's log.
ANSWERED_BY = {"ide_agent": "the agent", "headless": "the headless judge", "human": "you",
               "rules": "the rules", "watch": "tc watch's failure signatures"}


class Server(object):

    # A failed planner-context is tried again on later checks before the round
    # is closed: one failure must not cost a planner round (it did on 10-09).
    PLANNER_CTX_TRIES = 3
    PLANNER_CTX_RETRY_S = 60

    def __init__(self, output_dir, backend, driver=None, cfg=None,
                 concurrency=None, on_run_failure=None, log=None,
                 judge=None):
        self.output_dir = os.path.abspath(output_dir)
        self.backend = backend
        self.driver = driver or OrchestrateDriver(self.output_dir)
        self.cfg = cfg or config.load()
        self.queue = decisions.Queue(self.output_dir)
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        if hasattr(backend, "set_log"):
            backend.set_log(self.log)
        self.judge_order = self._judge_order(judge)

        name = getattr(backend, "name", "local")
        self.concurrency = (
            concurrency if concurrency is not None
            else config.get(self.cfg, "backend", name, "concurrency",
                            default=1))
        self.on_run_failure = (
            on_run_failure
            or config.get(self.cfg, "serve", "on_run_failure", default="stop"))
        # Stage 1 is seven independent strategies: one that fails (all five
        # auto_* on an UltraScale+ part, 10-14) must not stop the two still
        # placing. An explicit --on-run-failure applies to every stage.
        self.on_stage1_failure = (
            on_run_failure
            or config.get(self.cfg, "serve", "on_stage1_failure", default="continue"))
        self.poll_seconds = config.get(self.cfg, "serve", "poll_seconds",
                                       default=30)
        # A run that cannot be submitted was tried again on every tick, with
        # no end: 8 farm jobs in 5 minutes, each exiting at once, after the
        # pinned MCP server's folder was removed (10-15).
        self.max_submit_failures = max(1, int(config.get(
            self.cfg, "serve", "max_submit_failures", default=3) or 3))
        self._submit_failures = {}        # run dir -> failed submits so far
        self._closed = False              # the design met timing (state closed_by)
        self._killed = set()              # cancelled runs whose job was already killed
        self._gave_up = []                # rows given up on this tick
        self._stop = False
        self.failures = []
        self.submitted = []
        # Run dirs already recorded as failed. A failed run stays in the
        # worklist until the stage collects, so without this it would be
        # re-classified and re-recorded on every tick -- hundreds of duplicate
        # entries over a stage that waits on a slow sibling.
        self._settled = set()
        self._collect_waiting = False
        # Run dirs already offered to the champion. A finished run also stays
        # in the worklist until collect, and re-offering it would re-rank and
        # re-release it on every tick.
        self._offered = set()
        self.champion = champion.Champion(self.output_dir, self.backend,
                                          log=self.log)
        self._ctx_failures = {}      # early -> (tries, last failure time)
        # A flow staged for the farm generates its runs against the staged
        # copy of the skill's Tcl (state.json's assets_root), also after a
        # restart; the driver's orchestrate steps inherit it from here.
        self.assets_root = staging.assets_from_state(self.output_dir)
        if self.assets_root:
            staging.use_assets(self.assets_root)

    # -- judges ---------------------------------------------------------

    def _judge_order(self, judge):
        """
        Which answerers may take a decision, most preferred first.

        `--judge rules` (or a config whose only answerer is `rules`) means no
        better answer is possible, so decisions are opened with a zero
        deadline and the rule applies at once. Otherwise the configured
        deadline is how long we are willing to wait for a better answer.

        Getting this wrong is expensive in exactly the wrong direction: a
        one-hour triage deadline with nobody to answer would stall a retry for
        an hour per failed run.
        """
        if judge == "rules":
            return ["rules"]
        if judge == "headless":
            return ["headless", "rules"]
        if judge == "inbox":
            return ["ide_agent", "human", "rules"]
        order = list(config.get(self.cfg, "judge", "order",
                                default=["rules"]))
        if not config.get(self.cfg, "judge", "headless", "enabled",
                          default=False):
            order = [a for a in order if a != "headless"]
        if "rules" not in order:
            order.append("rules")
        return order

    def _deadline_for(self, point):
        """Seconds to wait for a better answer; 0 when none is possible."""
        if self.judge_order == ["rules"]:
            return 0
        return config.duration_seconds(
            config.get(self.cfg, "judge", "deadlines", point, default="1h"),
            default=3600)

    def answer_for(self, decision_id):
        """
        Get an answer for an open decision.

        This is what makes an unattended run more than rules-only. Nobody is
        watching the inbox at 3am, so when a headless judge is configured the
        loop asks it directly rather than waiting out the deadline. Without
        this, `--judge headless` was accepted and then behaved exactly like
        `--judge rules`.

        Order: an answer already recorded (a person or an editor agent may
        have got there first), then the headless judge, then the rule at its
        deadline. A judge that fails or is unreachable is logged and skipped --
        it must never be the reason a flow stalls.
        """
        answer = self.queue.resolve(decision_id)
        if answer is not None:
            return answer

        if "headless" in self.judge_order and judge.available(self.cfg):
            try:
                rec = judge.answer(self.queue, decision_id, self.cfg)
                self.log("tc serve: headless judge answered %s" % decision_id)
                return rec.get("answer")
            except (judge.JudgeUnavailable, judge.JudgeFailed,
                    decisions.ValidationError) as exc:
                self.log("tc serve: headless judge did not answer %s: %s"
                         % (decision_id, exc))
                if not self._has_inbox():
                    # Nothing else can answer, so the deadline has stopped
                    # meaning anything. Waiting out the hour would spend it on
                    # nobody -- and on a seven-way sweep that is seven hours.
                    rec = self.queue.apply_rule(
                        decision_id, why="headless judge failed: %s" % exc)
                    return rec.get("answer")

        return self.queue.resolve(decision_id)

    def _has_inbox(self):
        """Whether a person or an editor agent could still pick this up."""
        return any(a in self.judge_order for a in ("ide_agent", "human"))

    # -- signals --------------------------------------------------------

    def install_signal_handlers(self):
        """
        SIGTERM/SIGINT stop the loop but leave submitted jobs running.

        A job is detached on purpose: the engine going away must not lose
        in-flight work, and the next `tc serve` adopts it from job.json.
        """
        def handler(signum, frame):
            self._stop = True
            self.log("tc serve: signal %d; leaving running jobs alone" % signum)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass        # not the main thread, or not supported

    # -- runs -----------------------------------------------------------

    def _live_count(self, runs):
        live = 0
        for r in runs:
            if r.get("done") or r.get("settled"):
                continue
            handle = backends.load_handle(r["working_dir"])
            if handle is None:
                continue
            if self.backend.status(handle).state not in backends.TERMINAL:
                live += 1
        return live

    def submit_pending(self, runs):
        """
        Submit what is not finished, up to the window.

        concurrency <= 0 means "no window": LSF mode runs a whole stage at
        once, because the farm is the limit, not us.
        """
        started = []
        live = self._live_count(runs) if self.concurrency > 0 else 0
        for r in runs:
            if self._stop:
                break
            if r.get("done") or r.get("settled"):
                continue
            run_dir = r["working_dir"]
            handle = backends.load_handle(run_dir)
            if handle is not None:
                # Already submitted. Adopt rather than relaunch -- this is what
                # makes resume safe after an engine restart -- and never
                # relaunch a job that has already reached a terminal state.
                #
                # A terminal job with no run.done is a *failed* run, not an
                # unstarted one. Submitting it again here would resubmit it on
                # every tick, forever, burning a farm slot each time. A failed
                # run becomes eligible again only once `prepare-retry` has
                # quarantined its outputs and cleared the handle.
                if self.backend.status(handle).state not in backends.TERMINAL:
                    continue
                continue
            if self.concurrency > 0 and live >= self.concurrency:
                break
            if self._unstaged(r):
                continue
            res = backends.resources_from_config(
                self.cfg.get("backend") or {},
                getattr(self.backend, "name", "local"),
                job_name=r.get("run_id"))
            try:
                h = self.backend.submit(
                    run_dir, r.get("run_tcl") or
                    os.path.join(run_dir, "run.tcl"), res)
            except backends.SubmitError as exc:
                n = self._submit_failures.get(run_dir, 0) + 1
                self._submit_failures[run_dir] = n
                self.log("tc serve: submit failed for %s (%d of %d): %s"
                         % (r.get("run_id"), n, self.max_submit_failures, exc))
                if n >= self.max_submit_failures:
                    self.log("tc serve: %s could not be submitted %d times; recorded "
                             "as failed" % (r.get("run_id"), n))
                    self._settled.add(run_dir)
                    self.failures.append({"run_id": r.get("run_id"),
                                          "failure_class": "submit_failed",
                                          "evidence": [str(exc)[:400]]})
                    self._gave_up.append(r)
                continue
            self._submit_failures.pop(run_dir, None)
            self.log("tc serve: submitted %s as %s" % (r.get("run_id"),
                                                       h.job_id))
            # A new contender exists, so the title is contestable again. This
            # is the moment to reopen it -- not at collect, which is when the
            # judge starts asking the held design questions.
            self.champion.thaw()
            self._offered.discard(run_dir)
            started.append(r.get("run_id"))
            self.submitted.append(r.get("run_id"))
            live += 1
        return started

    def _unstaged(self, r):
        """
        True when a staged flow's run.tcl still names the local skill folder,
        which a farm host cannot read; the run is recorded as failed instead
        of submitted. Before this guard such a job failed on the farm (a
        planner evidence job) or quietly skipped its early fixes.
        """
        if not self.assets_root or getattr(self.backend, "name", "local") == "local":
            return False
        try:
            skill = paths.skill_root()
        except paths.SkillNotFound:
            return False
        run_tcl = r.get("run_tcl") or os.path.join(r["working_dir"], "run.tcl")
        ref = staging.local_skill_reference(run_tcl, skill)
        if not ref:
            return False
        why = ("%s sources the skill's local folder %s, which farm hosts cannot read; "
               "it should use the staged copy %s" % (run_tcl, ref, self.assets_root))
        self.log("tc serve: not submitting %s: %s" % (r.get("run_id"), why))
        self._settled.add(r["working_dir"])
        self.failures.append({"run_id": r.get("run_id"), "failure_class": "unstaged_path",
                              "evidence": [why]})
        self._gave_up.append(r)
        return True

    def _offer_to_champion(self, run):
        """
        Hand a finished run to the champion policy, once.

        A finished run stays in the worklist until the stage collects, so
        without the guard it would be re-ranked and re-released on every tick.
        """
        run_dir = run["working_dir"]
        if run_dir in self._offered:
            return
        self._offered.add(run_dir)
        result = self.champion.offer(run.get("run_id") or
                                     os.path.basename(run_dir), run_dir)
        self.log("tc serve: %s -- %s"
                 % (run.get("run_id"), result.get("reason")))

    def _after_collect(self, step):
        """
        Record whether the held session matched the verdict.

        The title is deliberately *not* reopened here. Between a collect and
        the next fan-out is exactly when the judge interrogates the held
        design -- the evidence run and the planner's live queries. Reopening
        now would let a straggler stop that session mid-question. It reopens
        when a new run is submitted instead, which is the only way a new
        contender can appear.
        """
        winner = None
        if hasattr(self.driver, "winner"):
            winner = self.driver.winner(step)
        if winner:
            self.champion.check_against(winner)

    def classify(self, run):
        """The run's state, with the backend's termination reason folded in."""
        run_dir = run["working_dir"]
        handle = backends.load_handle(run_dir)
        term, code, job_state = None, None, None
        if handle is not None:
            st = self.backend.status(handle)
            term, code, job_state = st.term_reason, st.exit_code, st.state
            # A process that has ended writes nothing more. Unsaid, a Vivado
            # that died before its Tcl ran (no run.failed) read as "running"
            # until the 15-minute staleness limit -- a failed build probe held
            # Stage 1 that long. Only for backends whose end is a fact; a
            # session can look gone across an MCP restart while Vivado runs.
            if not term and st.state in backends.TERMINAL and \
                    getattr(self.backend, "name", "") in ("local", "lsf"):
                term = "DONE" if st.state == backends.DONE else "EXIT"
        if hasattr(self.driver, "run_status"):
            try:
                return self.driver.run_status(run_dir, term, code, job_state=job_state)
            except TypeError:                       # a driver without job_state
                return self.driver.run_status(run_dir, term, code)
        return {"state": "unknown", "failure_class": "unknown"}

    def abandon(self, run, why):
        """
        Kill the job of a run the flow gives up on, if it is still queued or
        running: nothing will collect it, and it holds a farm slot (the Rubik
        batch left 76 jobs queued for designs that had already closed).
        """
        handle = backends.load_handle(run["working_dir"])
        if handle is None:
            return
        try:
            st = self.backend.status(handle)
        except Exception:                           # noqa: BLE001 -- never stall the flow on tidying
            return
        if st.state in backends.TERMINAL:
            return
        try:
            self.backend.kill(handle)
            self.log("tc serve: killed %s's job %s (%s)" % (run.get("run_id") or os.path.basename(
                run["working_dir"]), handle.job_id, why))
        except Exception as exc:                    # noqa: BLE001
            self.log("tc serve: could not kill %s's job %s: %s" % (run.get("run_id"), handle.job_id, exc))

    def abandon_all(self, why):
        """At the end of a complete flow: every job still alive, except the held champion's."""
        held = (self.champion.current or {}).get("run_dir")
        for jf in sorted(glob.glob(os.path.join(self.output_dir, "**", "job.json"), recursive=True)):
            run_dir = os.path.dirname(jf)
            if held and os.path.abspath(run_dir) == os.path.abspath(held):
                continue
            self.abandon({"working_dir": run_dir, "run_id": os.path.relpath(run_dir, self.output_dir)}, why)

    def handle_failure(self, run, verdict):
        """
        Retry, or record the failure.

        The retry decision is a queued judgement point like any other: the rule
        answers it immediately in rules-only mode, and an agent may take it
        when one is available.
        """
        failure_class = verdict.get("failure_class") or "unknown"
        run_id = run.get("run_id") or os.path.basename(run["working_dir"])
        attempts = int(verdict.get("attempts") or 0)
        guard = (self.cfg.get("guardrails") or {})
        if self._closed:
            # The design met timing: a failed sibling is not retried.
            self._record_failure(run, run_id, failure_class, verdict.get("evidence"))
            return "failed"

        did = decisions.open_with_rule(
            self.queue, "triage.%s" % _slug(run_id), "triage",
            deadline_s=self._deadline_for("triage"),
            design=os.path.basename(self.output_dir),
            evidence={"run_id": run_id, "run_dir": run["working_dir"],
                      "failure_class": failure_class,
                      "evidence": verdict.get("evidence")},
            failure_class=failure_class, attempts=attempts,
            max_retries=int(guard.get("max_retries_per_run", 1)),
            retry_crash=bool(guard.get("retry_crash", True)))

        answer = self.answer_for(did["id"])
        retry = _gate_value(answer)
        if retry is None:
            return "waiting"
        if not retry:
            self._record_failure(run, run_id, failure_class,
                                 verdict.get("evidence"))
            return "failed"

        # A disk_full run is retried only once space is back: --space-ok makes
        # prepare-retry probe-write first and refuse while the volume is full.
        args = ["--run-dir", run["working_dir"]]
        if failure_class == "disk_full":
            args.append("--space-ok")
        rc, out = self.driver.step("prepare-retry", args)
        if rc != 0:
            self.log("tc serve: prepare-retry refused %s: %s"
                     % (run_id, out.strip()[:200]))
            self._record_failure(run, run_id, failure_class,
                                 [out.strip()[:200]])
            return "failed"
        # prepare-retry moves the failed attempt into attempt_<n>/, so the run
        # directory is clean again. Dropping the handle is what makes the next
        # tick resubmit it; without that it stays terminal and never reruns.
        backends.clear_handle(run["working_dir"])
        self.log("tc serve: retrying %s (%s)" % (run_id, failure_class))
        return "retried"

    def _record_failure(self, run, run_id, failure_class, evidence):
        self._settled.add(run["working_dir"])
        self.failures.append({"run_id": run_id,
                              "failure_class": failure_class,
                              "evidence": evidence})

    # -- the planner round ----------------------------------------------

    def _early_open(self):
        """An early planner round started and not yet applied or closed."""
        if not hasattr(self.driver, "state"):
            return False
        pe = (self.driver.state() or {}).get("planner_early")
        return bool(pe) and pe.get("rows") is None

    def handle_planner(self, early=False):
        """
        The planner round as a decision, so it works with or without an LLM.

        The evidence run has finished. A planner decision is opened whose rule
        default is a plan built by rule_planner from that evidence (already
        validated), or "no fixes" when no template fits. An attached agent, a
        headless judge or a person can answer with their own plan.json first;
        at the deadline -- at once with --judge rules -- the rule plan is used.
        A plan is applied with planner-apply; no plan closes the round with
        planner-collect --no-fixes, and the flow routes on to Stage 3.

        Before this, a supervisor stopped here: `planner-plan` was an agent's
        step in the old workflow, and orchestrate.py has no such command.
        """
        tries, last = self._ctx_failures.get(early, (0, 0.0))
        if tries and time.time() - last < self.PLANNER_CTX_RETRY_S:
            return None                                # retry on a later check
        try:
            ctx = self.driver.planner_context(early) if early else self.driver.planner_context()
        except Exception as exc:                       # noqa: BLE001
            tries += 1
            self._ctx_failures[early] = (tries, time.time())
            if tries < self.PLANNER_CTX_TRIES:
                self.log("tc serve: planner-context failed (try %d of %d; again in %ds): %s"
                         % (tries, self.PLANNER_CTX_TRIES, self.PLANNER_CTX_RETRY_S, exc))
                return None
            self.log("tc serve: planner-context failed (try %d of %d, giving up): %s"
                     % (tries, self.PLANNER_CTX_TRIES, exc))
            last_line = str(exc).split(" | ")[-1][:160]
            return self._close_planner("planner-context failed %d times: %s" % (tries, last_line),
                                       early=early)
        self._ctx_failures.pop(early, None)
        rnd = "early" if early else int(ctx.get("round") or 1)
        did = "planner.early" if early else "planner.r%d" % rnd
        label = "early planner round" if early else "planner round %d" % rnd
        ev_dir = ctx.get("evidence_dir") or ""
        ev_path = os.path.join(ev_dir, "evidence.json")
        if not self.queue.exists(did):
            evidence = jsonio.read(ev_path) if os.path.isfile(ev_path) else None
            if not evidence:
                return self._close_planner("the evidence run left no evidence.json", early=early)
            plan, why = rule_planner.build(ctx, evidence)
            default = {"answer": {"skip": True}, "why": why}
            if plan:
                rule_path = os.path.join(os.path.dirname(ctx.get("plan_path") or ev_dir),
                                         "plan.rules.json")
                jsonio.write(rule_path, plan)
                # --prune: rule fixes that would only repeat a run done, generated,
                # queued or running are dropped from the rule plan.
                rc, out = self.driver.step("planner-validate", ["--plan", rule_path, "--prune"]
                                           + (["--early"] if early else []))
                try:
                    res = json.loads(out[out.index("{"):])
                except ValueError:
                    res = {}
                if rc == 0 and res.get("pruned_all"):
                    default = {"answer": {"skip": True},
                               "why": "every rule fix would repeat a run already done or in "
                                      "flight (%s)" % ", ".join(res.get("pruned") or [])}
                elif rc == 0:
                    if res.get("pruned"):
                        why += "; dropped as repeats: %s" % ", ".join(res["pruned"])
                    default = {"answer": {"plan_path": rule_path}, "why": "rule plan: " + why}
                else:
                    default = {"answer": {"skip": True},
                               "why": "rule plan rejected by planner-validate: %s"
                                      % out.strip()[:300]}
            self.queue.open(
                did, "planner", design=os.path.basename(self.output_dir),
                deadline_s=self._deadline_for("planner_early" if early else "planner"),
                rule_default=default,
                evidence={"evidence_json": ev_path, "evidence_dir": ev_dir,
                          "winner": ctx.get("winner"), "plan_path": ctx.get("plan_path"),
                          "already_tried": ctx.get("already_tried"),
                          "max_fixes": ctx.get("max_fixes"),
                          "rule_plan": default["answer"].get("plan_path")},
                prompt=("EARLY round, beside the fixed Stage 2: " if early else "") +
                       "Diagnose why the winner misses timing from evidence_json and write a "
                       "plan.json at plan_path (at most max_fixes fixes); answer "
                       "{\"plan_path\": ...}. If you cannot do better, leave it: the rule "
                       "plan (rule_plan) is used at the deadline.")
            rec = self.queue.get(did) or {}
            due = str(rec.get("deadline_utc") or "")[11:16]
            if default["answer"].get("plan_path"):
                fallback = "the rule plan runs: %s" % default["why"][len("rule plan: "):]
            else:
                fallback = "the round closes without fixes (%s)" % default["why"]
            self.log("tc serve: %s asked (answer by %s; if nobody answers, %s)"
                     % (label, due + "Z" if due else "the deadline", fallback[:200]))
        answer = self.answer_for(did)
        if answer is None:
            return None                                # waiting for a better plan
        plan_path = answer.get("plan_path") if isinstance(answer, dict) else None
        rec = self.queue.get(did) or {}
        who = ANSWERED_BY.get(rec.get("answered_by"), rec.get("answered_by") or "?")
        if rec.get("answered_by") == "rules" and rec.get("deadline_utc") and \
                str(rec.get("answered_utc") or "") >= str(rec["deadline_utc"]):
            who = "the rules (nobody answered by the deadline)"
        if plan_path:
            rc, out = self.driver.step("planner-apply", ["--plan", plan_path]
                                       + (["--early"] if early else []))
            if rc == 0:
                if not early and self.driver.status().get("phase") == "planner_screen":
                    self.log("tc serve: %s: screening the plan from %s on the winner's checkpoint "
                             "first (planner.screen; %s)" % (label, who, plan_path))
                    return None
                head = next((ln.strip() for ln in out.splitlines()
                             if ln.startswith("Planner screen")), "")
                self.log("tc serve: %s: applying the plan from %s (%s)%s" % (
                    label, who, plan_path, ("; " + head.rstrip(":")) if head else ""))
                return None
            self.log("tc serve: planner-apply refused %s: %s" % (plan_path, out.strip()[:300]))
            return self._close_planner("plan refused at apply", early=early)
        return self._close_planner("%s chose no fixes: %s" % (
            who, (rec.get("rule_default") or {}).get("why") or "no plan")
            if rec.get("answered_by") == "rules" else "%s chose no fixes" % who, early=early)

    def _close_planner(self, why, early=False):
        rc, out = self.driver.step("planner-collect", (["--early"] if early else [])
                                   + ["--no-fixes", str(why)[:200]])
        if rc != 0:
            self.log("tc serve: could not close the planner round: %s" % out.strip()[:300])
            return NEEDS_ATTENTION
        self.log("tc serve: %s closed without fixes (%s)"
                 % ("early planner round" if early else "planner round", str(why)[:160]))
        return None

    # -- closing the design ---------------------------------------------

    def close_design(self, st):
        """
        A finished run met timing (status closing_run). orchestrate close-design
        decides what is cancelled; this kills the jobs (rows marked cancelled, on
        this and later ticks), closes an open early planner round and skips any
        planner decision still pending. Returns the status re-read.
        """
        closing = st["closing_run"]
        queued = []
        for r in st.get("pending_runs") or []:
            if r.get("done") or r.get("settled"):
                continue
            handle = backends.load_handle(r["working_dir"])
            try:
                if handle is not None and self.backend.status(handle).state == backends.PENDING:
                    queued.append(r["working_dir"])
            except Exception:                       # noqa: BLE001 -- treated as not queued
                pass
        argv = ["--run-dir", closing["run_dir"]]
        for d in queued:
            argv += ["--queued", d]
        rc, out = self.driver.step("close-design", argv)
        if rc != 0:
            self.log("tc serve: close-design refused %s: %s"
                     % (closing.get("run_id"), out.strip()[:300]))
            return st
        try:
            n = len(json.loads(out.strip().splitlines()[-1]).get("cancelled") or [])
        except (ValueError, IndexError):
            n = 0
        self.log("tc serve: %s met timing (WNS %s, WHS %s): %d other run(s) of this design "
                 "cancelled; nothing new starts" % (closing.get("run_id"), closing.get("wns"),
                                                    closing.get("whs"), n))
        why = "design closed by %s" % closing.get("run_id")
        if self._early_open():
            self._close_planner(why, early=True)
        for rec in self.queue.list(state=decisions.PENDING):
            if rec.get("point") == "planner":
                try:
                    self.queue.answer(rec["id"], {"skip": True}, "rules", validate=False)
                except decisions.DecisionError:     # answered meanwhile
                    pass
        return self.driver.status()

    # -- one tick -------------------------------------------------------

    def tick(self):
        """
        One iteration. Returns None to keep going, or a terminal state.
        """
        self.queue.expire_due()
        st = self.driver.status()
        phase = st.get("phase")

        if phase == COMPLETE:
            return COMPLETE

        if st.get("closing_run"):
            st = self.close_design(st)
            phase = st.get("phase")
        self._closed = bool(st.get("closed_by"))

        # Path B: a Stage 1 run that has placed may now get its R2 run.
        if phase in PATH_B_PHASES and isinstance(self.driver, OrchestrateDriver):
            rc, out = self.driver.step("path-b-scan", ["--quiet"])
            if rc == 0 and out.strip():
                self.log("tc serve: %s" % out.strip()[:300])
                st = self.driver.status()

        runs = st.get("pending_runs") or []
        if runs:
            self.submit_pending(runs)
            gave_up, self._gave_up = self._gave_up, []
            for r in gave_up:
                holds = not r.get("side") and r.get("kind") not in ("path_b_detect",
                                                                     "planner_screen")
                policy = (self.on_stage1_failure if phase in STAGE1_PHASES
                          else self.on_run_failure)
                if holds and policy == "stop":
                    return NEEDS_ATTENTION

            unfinished = []
            for r in runs:
                if r.get("done"):
                    self._offer_to_champion(r)
                    continue
                if r.get("cancelled"):
                    # Marked by close-design. Killed once; the marker stays, so a
                    # restarted supervisor kills whatever is still queued.
                    if r["working_dir"] not in self._killed:
                        self._killed.add(r["working_dir"])
                        self.abandon(r, "design closed timing")
                    continue
                if r["working_dir"] in self._settled:
                    continue
                verdict = self.classify(r)
                state = verdict.get("state")
                if state == "done":
                    self._offer_to_champion(r)
                    continue
                # Path B runs beside Stage 1 never hold the stage, and a failed
                # Path B detect job only means that flow gets no R2 run.
                side = bool(r.get("side"))
                if state == "failed":
                    what = self.handle_failure(r, verdict)
                    if what == "failed":
                        self.abandon(r, "given up as %s" % (verdict.get("failure_class") or "failed"))
                    # A failed screen only means the plan runs unscreened.
                    holds = not side and r.get("kind") not in ("path_b_detect", "planner_screen")
                    if what == "failed" and holds and not self._closed:
                        policy = (self.on_stage1_failure if phase in STAGE1_PHASES
                                  else self.on_run_failure)
                        if policy == "stop":
                            return NEEDS_ATTENTION
                        self.log("tc serve: %s failed (%s); the stage goes on without it"
                                 % (r.get("run_id"), verdict.get("failure_class")))
                    if what == "waiting" and not side:
                        unfinished.append(r)
                    continue
                if not side:
                    unfinished.append(r)

            # The early planner round runs beside Stage 2: once its evidence
            # run is through, its decision opens (or applies); Stage 2 is not
            # collected while the round is open.
            ev = next((r for r in runs if r.get("run_id") == EARLY_EVIDENCE), None)
            # Only while its evidence run is in this worklist (Stage 2). Once
            # the flow is past Stage 2 an open early round cannot be decided
            # here any more, and holding on it stopped the loop for good.
            if ev is not None and self._early_open():
                if ev["working_dir"] in self._settled:
                    out = self._close_planner("the early evidence run failed", early=True)
                elif ev is not None and (ev.get("done") or ev not in unfinished):
                    out = self.handle_planner(early=True)
                else:
                    out = None
                if out is not None:
                    return out
                return None     # re-read the worklist: early fixes may have joined

            if unfinished:
                return None     # still working; caller sleeps

        nxt = st.get("next_step")
        if not nxt:
            return COMPLETE if phase == COMPLETE else NEEDS_ATTENTION

        if nxt == PLANNER_STEP:
            return self.handle_planner()

        if nxt in COLLECT_STEPS:
            # From here a judge may query the held design, so the title must
            # not move. A run finishing late would otherwise stop the session
            # out from under a query in flight.
            self.champion.freeze()

        rc, out = self.driver.step(nxt)
        if rc != 0 and nxt in COLLECT_STEPS and "still in progress" in out:
            # A run is still writing: collect when it is through, never end the
            # flow over it (20 Rubik runs ended needs_attention this way).
            if not self._collect_waiting:
                self.log("tc serve: %s waits: %s" % (nxt, out.strip()[:300]))
            self._collect_waiting = True
            self.champion.thaw()
            return None
        self._collect_waiting = False
        if rc != 0:
            self.log("tc serve: %s exited %d: %s" % (nxt, rc,
                                                     out.strip()[:400]))
            return NEEDS_ATTENTION
        if nxt in COLLECT_STEPS:
            self._after_collect(nxt)
        return None

    # -- the loop -------------------------------------------------------

    def serve(self, max_ticks=None, sleep=None):
        sleep = self.poll_seconds if sleep is None else sleep
        max_hours = config.get(self.cfg, "serve", "max_wall_hours", default=0)
        deadline = time.time() + max_hours * 3600 if max_hours else None

        ticks = 0
        while True:
            if self._stop:
                return STOPPED
            if deadline and time.time() > deadline:
                return BUDGET_SPENT
            outcome = self.tick()
            if outcome is not None:
                if outcome == COMPLETE:
                    self.abandon_all("the flow is complete")
                return outcome
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                return STOPPED
            if sleep:
                time.sleep(sleep)

    def report(self):
        return {
            "output_dir": self.output_dir,
            "submitted": self.submitted,
            "failures": self.failures,
            "decisions": self.queue.provenance_summary(),
            "token_usage": token_usage.totals(self.output_dir),
        }


def _gate_value(answer):
    """A gate answer is {'answer': bool, 'why': str}; rules may store a bool."""
    if answer is None:
        return None
    if isinstance(answer, dict):
        return answer.get("answer")
    return bool(answer)


def _slug(text):
    return "".join(c if (c.isalnum() or c in "._-") else "-"
                   for c in str(text))[:64] or "x"
