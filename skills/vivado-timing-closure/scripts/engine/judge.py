#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
judge.py -- run a headless answerer, as a configured command.

The open question this closes: *is there a headless, schema-constrained route
for every agent CLI?* The answer is to stop needing one. A headless judge here
is a **command template** in YAML, not an integration:

    judge:
      headless:
        enabled: true
        command: ["some-agent", "-p", "--output-format", "json",
                  "--json-schema", "{schema_path}"]
        cwd: "{engine_scratch}"
        timeout_s: 900

Anything that takes a prompt and emits JSON drops in by editing config. No
vendor needs to be verified, because none is depended on.

Two safeguards, both deliberate:

* **The judge runs in an engine-owned directory.** Never inside a customer
  design directory, because an agent CLI started there may load that project's
  configuration and hooks without a trust prompt. `{engine_scratch}` resolves
  to a directory this module creates.

* **Output is validated before it is accepted**, by the same queue validator
  every other answerer goes through. A headless judge gets no shortcut.

`replay` re-runs a recorded decision through a live judge and diffs the answer.
That is how the borderline instability gets measured rather than argued about:
identical evidence producing different answers shows up as a diff.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import subprocess
import tempfile
import time

from . import config, decisions, token_usage

SCRATCH = "judge-scratch"


class JudgeUnavailable(Exception):
    """No headless judge is configured, or it could not be started."""


class JudgeFailed(Exception):
    """The judge ran but produced nothing usable."""


def _scratch_dir(output_dir):
    d = os.path.join(os.path.abspath(output_dir), SCRATCH)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def build_prompt(rec):
    """What the judge is asked. Evidence only -- never a path to a netlist."""
    lines = [
        "You are answering one timing-closure judgement call.",
        "",
        "Decision id : %s" % rec.get("id"),
        "Point       : %s" % rec.get("point"),
        "Design      : %s" % (rec.get("design") or "(unnamed)"),
        "",
        "Evidence:",
        json.dumps(rec.get("evidence") or {}, indent=2, sort_keys=True),
        "",
        "The deterministic default, which applies if you do not answer:",
        json.dumps(rec.get("rule_default") or {}, indent=2, sort_keys=True),
        "",
    ]
    if rec.get("point") == "planner":
        lines += [
            "Reply with JSON: {\"plan_path\": \"<path to a plan.json you "
            "wrote>\"}.",
            "The plan is validated before it is accepted; timing exceptions, "
            "constraint changes and Tcl escapes are rejected.",
        ]
    else:
        lines += [
            "Reply with JSON: {\"answer\": true|false, \"why\": \"<one "
            "sentence>\"}.",
            "Agree with the default unless the evidence shows something it "
            "cannot see.",
        ]
    return "\n".join(lines) + "\n"


def _render(template, mapping):
    out = []
    for item in template:
        for key, value in mapping.items():
            item = item.replace("{%s}" % key, value)
        out.append(item)
    return out


def available(cfg):
    hl = config.get(cfg, "judge", "headless", default={}) or {}
    return bool(hl.get("enabled")) and bool(hl.get("command"))


def ask(rec, cfg, output_dir, schema_path=None):
    """
    Run the configured judge for one decision and return (parsed JSON,
    seconds).

    Raises JudgeUnavailable when none is configured, JudgeFailed when it ran
    but produced nothing usable. Neither is fatal to the flow: the caller
    leaves the decision pending and the rule applies at its deadline.
    """
    parsed, duration, _ = _ask(rec, cfg, output_dir, schema_path)
    return parsed, duration


def _ask(rec, cfg, output_dir, schema_path=None, purpose="answer"):
    """
    ask(), plus the call's token usage (token_usage.from_cli_output, or None).
    Every call that ran is recorded in the design's token_usage.jsonl -- a
    failed or timed-out one too, since it was paid for.
    """
    hl = config.get(cfg, "judge", "headless", default={}) or {}
    if not available(cfg):
        raise JudgeUnavailable("no headless judge configured "
                               "(judge.headless.enabled / .command)")

    scratch = _scratch_dir(output_dir)
    prompt = build_prompt(rec)
    fd, prompt_path = tempfile.mkstemp(dir=scratch, prefix="prompt.",
                                       suffix=".txt")
    with os.fdopen(fd, "w") as fh:
        fh.write(prompt)

    mapping = {
        "prompt_path": prompt_path,
        "schema_path": schema_path or "",
        "engine_scratch": scratch,
        "decision_id": str(rec.get("id")),
        "output_dir": os.path.abspath(output_dir),
    }
    argv = _render(list(hl["command"]), mapping)
    cwd = hl.get("cwd") or scratch
    for key, value in mapping.items():
        cwd = cwd.replace("{%s}" % key, value)
    timeout = int(hl.get("timeout_s") or 900)

    started = time.time()

    def spent(out, ok, error=None):
        usage = token_usage.from_cli_output(out)
        token_usage.record(output_dir, {
            "decision_id": rec.get("id"), "point": rec.get("point"),
            "design": rec.get("design"), "purpose": purpose, "answerer": "headless",
            "model": _call_model(usage, cfg), "ok": ok, "error": error,
            "duration_s": round(time.time() - started, 2), "usage": usage})
        return usage

    try:
        with open(prompt_path) as stdin:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=stdin,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    universal_newlines=True)
            out, err = proc.communicate(timeout=timeout)
    except OSError as exc:
        raise JudgeUnavailable("cannot run %r: %s" % (argv[0], exc))
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        spent(out, False, "timeout")
        raise JudgeFailed("judge timed out after %ss" % timeout)
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass

    duration = time.time() - started
    if proc.returncode != 0:
        spent(out, False, "exit %d" % proc.returncode)
        raise JudgeFailed("judge exited %d: %s"
                          % (proc.returncode, (err or out or "").strip()[:400]))
    parsed = _parse(out)
    if parsed is None:
        spent(out, False, "no JSON")
        raise JudgeFailed("judge did not emit JSON: %s"
                          % (out or "").strip()[:200])
    usage = spent(out, True)
    return parsed, duration, usage


def _parse(out):
    """
    Accept raw JSON, or JSON embedded in a wrapper object under common keys.
    """
    text = (out or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        # Last resort: the outermost {...} in the output.
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            obj = json.loads(text[a:b + 1])
        except ValueError:
            return None
    if isinstance(obj, dict):
        for key in ("answer", "plan_path"):
            if key in obj:
                return obj
        for key in ("result", "output", "content", "response"):
            inner = obj.get(key)
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, str):
                return _parse(inner)
    return obj if isinstance(obj, dict) else None


def answer(queue, decision_id, cfg, model=None):
    """Ask the headless judge and record its answer. Returns the record."""
    rec = queue.get(decision_id)
    parsed, duration, usage = _ask(rec, cfg, queue.output_dir)
    return queue.answer(decision_id, parsed, "headless",
                        model=model or _call_model(usage, cfg),
                        duration_s=round(duration, 2),
                        usage=_usage_brief(usage))


def _model_of(cfg):
    cmd = (config.get(cfg, "judge", "headless", "command", default=[]) or [])
    return cmd[0] if cmd else None


def _call_model(usage, cfg):
    """The model the CLI says it used, else the configured command's name."""
    models = sorted((usage or {}).get("by_model") or {})
    return ",".join(models) if models else _model_of(cfg)


def _usage_brief(usage):
    """What a decision record keeps of its call's usage: tokens and cost."""
    if not usage:
        return None
    return {k: usage.get(k) for k in token_usage.KINDS + ("cost_usd", "cost_basis")}


def replay(queue, decision_id, cfg):
    """
    Re-run a recorded decision through the live judge and diff the answer.

    Identical evidence producing a different answer is the instability that
    makes a recipe unreplayable; this turns it into a measurement.
    """
    rec = queue.get(decision_id)
    recorded = rec.get("answer")
    parsed, duration, _ = _ask(rec, cfg, queue.output_dir, purpose="replay")
    return {
        "id": decision_id,
        "point": rec.get("point"),
        "recorded": recorded,
        "recorded_by": rec.get("answered_by"),
        "replayed": parsed,
        "same": _same(recorded, parsed),
        "duration_s": round(duration, 2),
    }


def _same(a, b):
    """Compare on the decision, not the prose: `why` is free text."""
    if isinstance(a, dict) and isinstance(b, dict):
        if "answer" in a or "answer" in b:
            return a.get("answer") == b.get("answer")
        return a == b
    return a == b
