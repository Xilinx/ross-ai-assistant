#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
token_usage.py -- what the model calls of a timing-closure run used and cost.

The engine makes one kind of model call itself: the headless judge
(judge.py), a configured agent CLI asked to answer one decision. Each call is
recorded here, one JSON line per call, in the design's output directory:

    <output_dir>/token_usage.jsonl   every call: when, which decision, which
                                     model, tokens, cost, how long, ok or not
    <output_dir>/token_usage.json    the totals, rewritten after every call

The numbers come from where upstream takes them (agentic-ai-suite,
lib/token_usage.py): the agent CLI's own final ``result`` event, printed with
``--output-format json`` (one object) or ``stream-json`` (one event per
line). Per model, ``modelUsage`` gives input, output, cache-read and
cache-write tokens and ``costUSD``; ``total_cost_usd`` is the call's cost.
The gateway may report ``costBasis: unknown``, and then the cost is the CLI's
own estimate, not an invoice -- that is kept with the number. Nothing is
estimated here and there is no price table: a CLI that prints no result event
(another vendor's, or a call that timed out) is recorded with its duration and
``usage: null``.

A decision answered by an editor agent or a person costs tokens this engine
cannot see; one answered by the rules or by `tc watch` costs none. Those are
counted by answerer, never given numbers.

Accounting never breaks a flow: every writer here swallows its errors.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import glob
import json
import os
import time

CALLS_FILE = "token_usage.jsonl"
TOTALS_FILE = "token_usage.json"
KINDS = ("input", "output", "cache_read", "cache_write")

# Who answers a decision without a model call this engine makes.
NO_ENGINE_TOKENS = {
    "ide_agent": "an editor agent: its tokens are its client's, not visible here",
    "human": "a person",
    "rules": "the deterministic rule, no model",
    "watch": "tc watch's evidence rules, no model",
}


# ---------------------------------------------------------------------------
# reading a CLI's output
# ---------------------------------------------------------------------------
def _usage_row(usage):
    usage = usage or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
    }


def _model_row(mu):
    return {
        "input": int(mu.get("inputTokens") or 0),
        "output": int(mu.get("outputTokens") or 0),
        "cache_read": int(mu.get("cacheReadInputTokens") or 0),
        "cache_write": int(mu.get("cacheCreationInputTokens") or 0),
        "cost_usd": mu.get("costUSD"),
        "cost_basis": mu.get("costBasis"),
    }


def _result_event(text):
    """The last ``result`` event in a CLI's stdout, or None."""
    text = (text or "").strip()
    if not text:
        return None
    candidates = []
    try:
        obj = json.loads(text)
        candidates = obj if isinstance(obj, list) else [obj]
    except ValueError:
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidates.append(json.loads(line))
            except ValueError:
                continue
    found = None
    for ev in candidates:
        if isinstance(ev, dict) and (ev.get("type") == "result" or "total_cost_usd" in ev
                                     or "modelUsage" in ev):
            found = ev
    return found


def from_cli_output(text):
    """
    The usage of one CLI call, from its stdout: a dict with the four token
    counts, ``cost_usd``, ``cost_basis``, ``by_model``, ``turns``,
    ``duration_ms`` and ``source``; None when the output carries no result
    event with usage or cost.
    """
    ev = _result_event(text)
    if not ev:
        return None
    by_model = {}
    for model, mu in (ev.get("modelUsage") or {}).items():
        if isinstance(mu, dict):
            by_model[model] = _model_row(mu)
    if by_model:
        totals = dict.fromkeys(KINDS, 0)
        for row in by_model.values():
            for k in KINDS:
                totals[k] += row[k]
        source = "modelUsage"
    elif ev.get("usage"):
        totals = _usage_row(ev.get("usage"))
        source = "usage"
    elif ev.get("total_cost_usd") is not None:
        totals = dict.fromkeys(KINDS, 0)
        source = "cost only"
    else:
        return None
    cost = ev.get("total_cost_usd")
    if cost is None and by_model:
        costs = [r["cost_usd"] for r in by_model.values() if r["cost_usd"] is not None]
        cost = sum(costs) if costs else None
    bases = sorted(set(str(r["cost_basis"]) for r in by_model.values() if r.get("cost_basis")))
    out = dict(totals)
    out.update({
        "cost_usd": cost,
        "cost_basis": bases[0] if len(bases) == 1 else (",".join(bases) if bases else None),
        "by_model": by_model,
        "turns": ev.get("num_turns"),
        "duration_ms": ev.get("duration_ms"),
        "source": source,
    })
    return out


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------
def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record(output_dir, entry):
    """
    Append one call to <output_dir>/token_usage.jsonl and refresh the totals.
    `entry` holds decision_id, point, purpose, model, ok, duration_s and
    usage (from_cli_output's dict, or None). Never raises.
    """
    try:
        line = dict(entry)
        line.setdefault("utc", _utc())
        with open(os.path.join(output_dir, CALLS_FILE), "a") as fh:
            fh.write(json.dumps(line, sort_keys=True, default=str) + "\n")
        write_totals(output_dir)
    except Exception:                        # noqa: BLE001 -- never break a flow
        pass


def read_calls(output_dir):
    """Every recorded call, oldest first; a damaged line is skipped."""
    out = []
    try:
        with open(os.path.join(output_dir, CALLS_FILE)) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except (IOError, OSError):
        pass
    return out


def _answerers(output_dir):
    """Answered decisions by answerer, read from decisions/*.json."""
    counts = {}
    for path in glob.glob(os.path.join(output_dir, "decisions", "*.json")):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (IOError, OSError, ValueError):
            continue
        who = rec.get("answered_by") if isinstance(rec, dict) else None
        if who:
            counts[who] = counts.get(who, 0) + 1
    return counts


def _empty():
    t = dict.fromkeys(KINDS, 0)
    t.update({"calls": 0, "calls_with_usage": 0, "cost_usd": None})
    return t


def _add(t, call):
    t["calls"] += 1
    usage = call.get("usage")
    if not usage:
        return
    t["calls_with_usage"] += 1
    for k in KINDS:
        t[k] += int(usage.get(k) or 0)
    if usage.get("cost_usd") is not None:
        t["cost_usd"] = round((t["cost_usd"] or 0.0) + float(usage["cost_usd"]), 6)


def totals(output_dir):
    """
    The design's totals: all calls, then by decision point (the stage the
    call served), by model and by purpose, and the decisions answered without
    a model call this engine made.
    """
    calls = read_calls(output_dir)
    out = _empty()
    by_point, by_model, by_purpose = {}, {}, {}
    bases = set()
    for c in calls:
        _add(out, c)
        _add(by_point.setdefault(c.get("point") or "?", _empty()), c)
        _add(by_purpose.setdefault(c.get("purpose") or "answer", _empty()), c)
        usage = c.get("usage") or {}
        if usage.get("cost_basis"):
            bases.add(str(usage["cost_basis"]))
        for model, row in (usage.get("by_model") or {}).items():
            m = by_model.setdefault(model, dict(dict.fromkeys(KINDS, 0), cost_usd=None))
            for k in KINDS:
                m[k] += int(row.get(k) or 0)
            if row.get("cost_usd") is not None:
                m["cost_usd"] = round((m["cost_usd"] or 0.0) + float(row["cost_usd"]), 6)
    answered = _answerers(output_dir)
    out.update({
        "by_point": by_point,
        "by_model": by_model,
        "by_purpose": by_purpose,
        "cost_basis": ",".join(sorted(bases)) or None,
        "calls_without_usage": out["calls"] - out["calls_with_usage"],
        "decisions_by_answerer": answered,
        "decisions_without_engine_tokens": {
            who: n for who, n in answered.items() if who in NO_ENGINE_TOKENS},
    })
    return out


def write_totals(output_dir):
    """Rewrite <output_dir>/token_usage.json from the call log. Never raises."""
    try:
        path = os.path.join(output_dir, TOTALS_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(dict(totals(output_dir), updated_utc=_utc()), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:                        # noqa: BLE001
        pass


def brief(output_dir):
    """The few numbers a run list or a status line shows; None when nothing was recorded."""
    t = totals(output_dir)
    if not t["calls"] and not t["decisions_without_engine_tokens"]:
        return None
    return {
        "calls": t["calls"],
        "input": t["input"],
        "output": t["output"],
        "cache_read": t["cache_read"],
        "cache_write": t["cache_write"],
        "cost_usd": t["cost_usd"],
        "cost_basis": t["cost_basis"],
        "calls_without_usage": t["calls_without_usage"],
        "decisions_without_engine_tokens": t["decisions_without_engine_tokens"],
    }


def fmt_tokens(n):
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%.1fk" % (n / 1e3)
    return str(n)


def fmt_cost(c):
    return "-" if c is None else "$%.2f" % c
