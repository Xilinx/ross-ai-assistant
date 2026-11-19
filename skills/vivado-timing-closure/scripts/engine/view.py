#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
view.py -- timing-closure runs as workflow-view/1 documents.

An IDE (the Vivado AI extension's Workflows view) shows any workflow's runs
without knowing what the workflow does: it calls the actions the manifest's
`view` names and draws the generic document they return -- items with a
state, metrics and badges, and for one item, sections (key/values, tables,
lists, a timeline, logs, a stepper, tiles, a chart, cards, tabs), paths to
open and prompts to hand the agent. This
module is the only place that turns timing-closure's own snapshot into that
shape; nothing in the IDE knows what WNS is.

The format is specified in Vivado MCP's docs/design/vivado_workflow_tool.md.

Pure functions over watch.snapshot / watch.dashboard output, so they test
without a run. Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import calendar
import datetime
import json
import os
import re
import time

from . import token_usage

SCHEMA = "workflow-view/1"
TONES = ("ok", "warn", "bad", "info", "muted")
STATES = ("running", "attention", "done", "failed", "stopped", "idle")

# Outcomes a stopped supervisor records that mean "this went wrong".
_FAILED_OUTCOMES = ("needs_attention", "crashed", "error", "failed")


def _ns(v):
    return None if v is None else "%+.3f" % v if v != 0 else "+0.000"


def _tone_slack(v):
    if v is None:
        return "muted"
    return "ok" if v >= 0 else "bad"


# A pending decision is the agent's while it is attached and checking in; it
# falls to the user only when nobody is on it, or this close to the deadline
# (when the rule default would otherwise be applied without anyone choosing).
AGENT_ON_IT = ("waiting", "active")
DUE_SOON_S = 30 * 60


def run_variant(output_dir):
    """`..._tc_sa` -> `sa`: what tells runs of one design apart, or ''."""
    base = os.path.basename(os.path.normpath(output_dir))
    for marker in ("_tc_", "_timing_closure_"):
        if marker in base:
            return base.rsplit(marker, 1)[1]
    return ""


def run_title(output_dir, design=None):
    """The design's short name, plus the run folder's variant: `mydesign_cu1 · sa`."""
    variant = run_variant(output_dir)
    if design:
        name = short_name(design)
    else:
        base = os.path.basename(os.path.normpath(output_dir))
        name = short_name(base[:-len(variant) - 1] if variant else base)
    return "%s · %s" % (name, variant) if variant else name


def agent_on_it(agent, pending, now=None):
    """
    Whether the attached agent is handling the pending decisions: it is
    checking in (a `tc wait` running, or seen within watch.AGENT_ACTIVE_S) and
    no decision is within DUE_SOON_S of its deadline.
    """
    if not pending or (agent or {}).get("state") not in AGENT_ON_IT:
        return False
    now = now or datetime.datetime.utcnow()
    for d in pending:
        t = _parse_iso(d.get("deadline_utc") if isinstance(d, dict) else None)
        if t and (t - now).total_seconds() <= DUE_SOON_S:
            return False
    return True


def _pending(snap):
    return [d for d in snap.get("pending_decisions") or [] if isinstance(d, dict) and d.get("id")]


def short_name(output_dir):
    """`.../mydesign_cu1_bd_0_wrapper_opt_tc` -> `mydesign_cu1`."""
    base = os.path.basename(os.path.normpath(output_dir))
    for suffix in ("_tc", "_timing_closure"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    for tail in ("_bd_0_wrapper_opt", "_wrapper_opt", "_opt", "_routed", "_synth"):
        if base.endswith(tail) and len(base) > len(tail):
            return base[:-len(tail)]
    return base


def run_state(snap):
    """One of STATES, from a snapshot (watch.snapshot or watch.dashboard)."""
    outcome = (snap.get("outcome") or {}).get("outcome") if isinstance(
        snap.get("outcome"), dict) else snap.get("outcome")
    if snap.get("phase") == "complete" or outcome == "complete":
        return "done"
    if snap.get("supervisor_alive"):
        return "attention" if snap.get("pending_decisions") else "running"
    if outcome in _FAILED_OUTCOMES:
        return "failed"
    if snap.get("phase"):
        return "stopped"
    return "idle"


def _metrics(best):
    best = best or {}
    out = []
    for key, label in (("wns", "WNS"), ("whs", "WHS")):
        if best.get(key) is not None:
            out.append({"label": label, "value": _ns(best[key]), "unit": "ns",
                        "tone": _tone(best[key], best.get("whs")) if key == "wns"
                        else _tone_slack(best[key])})
    if best.get("tns") is not None:
        out.append({"label": "TNS", "value": "%.2f" % best["tns"], "unit": "ns",
                    "tone": _tone_slack(best["tns"])})
    return out


def _decision_badges(pending):
    out = []
    for d in pending or []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        out.append({"text": "decision %s" % d["id"], "tone": "warn"})
    return out


def _runs_badge(runs):
    runs = runs or {}
    parts = []
    if runs.get("running"):
        parts.append("%d running" % runs["running"])
    if runs.get("done"):
        parts.append("%d done" % runs["done"])
    if runs.get("failed"):
        parts.append("%d failed" % runs["failed"])
    return {"text": " / ".join(parts), "tone": "bad" if runs.get("failed") else "info"} \
        if parts else None


def list_item(snap, design=None, updated=None, agent=None, now=None):
    """
    One run as a list item. `snap` is a watch.snapshot; `agent` is
    watch.agent_state for it. A pending decision makes it `attention` unless
    the attached agent is on it (agent_on_it): then it stays `running` with a
    badge saying the agent is answering. A run whose orchestrating chat has gone
    quiet while the supervisor works on needs the user: someone has to resume it.
    """
    out = snap["output_dir"]
    best = snap.get("best") or {}
    pend = _pending(snap)
    handled = snap.get("supervisor_alive") and agent_on_it(agent, pend, now)
    if handled:
        due = _clock(min((d["deadline_utc"] for d in pend if d.get("deadline_utc")), default=""))
        badges = [{"text": "agent answering %s%s" % (", ".join(d["id"] for d in pend),
                                                    " (rules at %s)" % due if due else ""),
                   "tone": "info"}]
    else:
        badges = _decision_badges(snap.get("pending_decisions"))
    rb = _runs_badge(snap.get("runs"))
    if rb:
        badges.append(rb)
    subtitle = snap.get("phase") or "not started"
    if best.get("run"):
        subtitle += " · best %s (%s)" % (best["run"], best.get("from") or "?")
    item = {
        "id": out,
        "title": run_title(out, design),
        "subtitle": subtitle,
        "state": "running" if handled else run_state(snap),
        "metrics": _metrics(best),
        "badges": badges,
        "tooltip": out,
    }
    if (agent or {}).get("state") == "idle" and snap.get("supervisor_alive") \
            and item["state"] in ("running", "attention"):
        item["state"] = "attention"
        ago = _ago(agent.get("last_seen_utc"), now or datetime.datetime.utcnow())
        item["badges"].insert(0, {"text": "agent idle %s" % ago if ago else "agent idle",
                                  "tone": "bad"})
    if updated:
        item["updated"] = updated
    return item


_URGENCY = {"attention": 0, "failed": 1, "stopped": 1, "running": 2, "idle": 3, "done": 4}


def sort_items(items):
    """Most urgent first (waiting on someone, then stopped, running, finished), newest first within."""
    items = sorted(items, key=lambda i: i.get("updated") or "", reverse=True)
    return sorted(items, key=lambda i: _URGENCY.get(i.get("state"), 3))


def list_document(items, workspace):
    doc = {"schema": SCHEMA, "items": items}
    if not items:
        doc["empty"] = ("No timing-closure runs under %s. Ask the agent to close timing on a "
                        "checkpoint (.dcp) to start one." % workspace)
    return doc


# ---------------------------------------------------------------------------
# detail: the companion dashboard's layout -- a phase stepper above the tabs
# Overview / Runs / Decisions / Timeline / Logs
# ---------------------------------------------------------------------------
_STAGE_ORDER = ("probe", "stage1", "detect", "stage2", "planner", "stage3")
PHASES = (("probe", "Probe"), ("stage1", "Stage 1"), ("detect", "Detect"),
          ("stage2", "Stage 2"), ("planner", "Planner"), ("stage3", "Stage 3"),
          ("complete", "Done"))
STEPS = (("opt", "opt"), ("place", "place"), ("place_physopt", "p·opt"),
         ("route", "route"), ("route_physopt", "r·opt"))
_STEP_INDEX = {k: i for i, (k, _) in enumerate(STEPS)}
JUDGE_LABEL = {"ide_agent": "agent", "rules": "rules", "human": "you",
               "headless": "agent (headless)"}
TABS = (("overview", "Overview"), ("runs", "Runs"), ("decisions", "Decisions"),
        ("timeline", "Timeline"), ("logs", "Logs"))
_FOLLOWED_RUNS = 4


def _tns(v):
    return "" if v is None else "%.1f" % v


def _tone(wns, whs=None):
    """Setup and hold together: missing hold makes a result unusable."""
    if wns is None:
        return None
    hold_ok = whs is None or whs >= 0
    if wns >= 0 and hold_ok:
        return "ok"
    if wns > -0.1 and hold_ok:
        return "warn"
    return "bad"


def _parse_iso(iso):
    if not iso:
        return None
    try:
        return datetime.datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _dur(sec):
    sec = max(0, int(round(sec)))
    d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
    return "%dd %dh" % (d, h) if d else "%dh %dm" % (h, m) if h else "%dm" % m


def _ago(iso, now):
    t = _parse_iso(iso)
    return _dur((now - t).total_seconds()) if t else ""


def _until(iso, now):
    t = _parse_iso(iso)
    if not t:
        return ""
    s = (t - now).total_seconds()
    return "now" if s <= 0 else "in " + _dur(s)


def _clock(iso):
    """HH:MM in this host's time zone (the IDE's, for a local or remote window)."""
    t = _parse_iso(iso)
    if not t:
        return ""
    return time.strftime("%H:%M", time.localtime(calendar.timegm(t.timetuple())))


def phase_key(phase):
    p = str(phase or "")
    if p == "complete":
        return "complete"
    if p == "stage2_detect":
        return "detect"
    for k in ("probe", "stage1", "stage2", "planner", "stage3"):
        if p.startswith(k):
            return k
    return p


def _stepper(dash):
    cur = phase_key(dash.get("phase"))
    seen = set(dash.get("phases_seen") or [])
    passed = cur in [k for k, _ in PHASES]
    steps = []
    for k, label in PHASES:
        if k == cur:
            state = "done" if k == "complete" else "current"
            passed = False
        elif passed:
            state = "done" if k in seen else "skipped"
        else:
            state = "todo"
        steps.append({"label": label, "state": state})
    return {"kind": "stepper", "pin": True, "title": "", "steps": steps}


def _value_text(v):
    """A decision's value in words, and the file it names (a plan) if any."""
    if v is True:
        return "yes", None
    if v is False:
        return "no", None
    if v is None:
        return "—", None
    if isinstance(v, dict):
        if v.get("plan_path"):
            f = str(v["plan_path"])
            return ("the rule plan" if f.endswith("plan.rules.json")
                    else "plan " + os.path.basename(f)), f
        if v.get("skip"):
            return "skip this round", None
        return json.dumps(v, sort_keys=True), None
    s = str(v)
    if s.endswith(".json") and os.sep in s:
        return ("the rule plan" if s.endswith("plan.rules.json")
                else "plan " + os.path.basename(s)), s
    return s, None


def _all_runs(dash):
    runs = list(dash.get("all_runs") or [])
    for r in dash.get("run_list") or []:
        if r not in runs:
            runs.append(r)
    seen, out = set(), []
    for r in runs:
        key = (r.get("stage"), r.get("run_id"), r.get("dir"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    order = {s: i for i, s in enumerate(_STAGE_ORDER)}
    return sorted(out, key=lambda r: order.get(r.get("stage"), len(order)))


def _chips(r):
    by = {s.get("step"): s for s in r.get("steps") or []}
    chips = []
    for k, label in STEPS:
        s = by.get(k)
        if not s:
            chips.append({"text": label, "tooltip": "%s: not reached" % label})
            continue
        chips.append({"text": label, "tone": _tone(s.get("wns"), s.get("whs")) or "muted",
                      "tooltip": "%s at %s: WNS %s, WHS %s, TNS %s" % (
                          label, _clock(s.get("at_utc")), _ns(s.get("wns")) or "—",
                          _ns(s.get("whs")) or "—", _tns(s.get("tns")) or "—")})
    return chips


def _run_time(r, now):
    t0, t1 = _parse_iso(r.get("submitted_utc")), _parse_iso(r.get("finished_utc"))
    if t0 and t1:
        return _dur((t1 - t0).total_seconds())
    return "%s so far" % _ago(r["submitted_utc"], now) if t0 else ""


def _run_rows(dash, now):
    rows = []
    for r in _all_runs(dash):
        state = r.get("state") or ""
        note = ("same placement as %s — stopped after place" % (r.get("duplicate_of") or "?")
                if state == "duplicate" else
                "cancelled: the design met timing" if state == "cancelled" else
                r.get("error") or "")
        tone = {"failed": "bad", "running": "info"}.get(state)
        if r.get("backend") == "session":
            job = "MCP session %s · LSF job %s" % (r.get("job_id") or "—",
                                                       r.get("lsf_job_id") or "—")
        else:
            job = "LSF job %s" % (r.get("job_id") or "—")
        detail = {"summary": "details" + (" — " + note[:90] if note else ""),
                  "items": [{"label": "job", "value": job}]}
        text = "\n".join(x for x in (r.get("methodology"), note) if x)
        if text:
            detail["text"] = text
            if r.get("error"):
                detail["tone"] = "bad"
        steps = r.get("steps") or []
        if steps:
            detail["table"] = {"columns": ["step", "WNS", "WHS", "TNS", "at"], "numeric": [1, 2, 3],
                               "rows": [{"cells": [s.get("step") or "", _ns(s.get("wns")) or "",
                                                   _ns(s.get("whs")) or "", _tns(s.get("tns")),
                                                   _clock(s.get("at_utc"))],
                                         "cell_tones": [None, _tone(s.get("wns"), s.get("whs")),
                                                        "bad" if (s.get("whs") or 0) < 0 else None,
                                                        None, None]} for s in steps]}
        detail["paths"] = [{"label": os.path.basename(p), "path": p} for p in r.get("logs") or []]
        if r.get("dir"):
            detail["paths"].append({"label": "folder", "path": r["dir"]})
        row = {"group": r.get("stage") or "",
               "cells": [r.get("run_id") or "", state, _ns(r.get("wns")) or "",
                         _ns(r.get("whs")) or "", _tns(r.get("tns")), _run_time(r, now)],
               "cell_tones": [None, tone, _tone(r.get("wns"), r.get("whs")),
                              "bad" if (r.get("whs") or 0) < 0 else None, None, None],
               "chips": _chips(r), "detail": detail}
        if r.get("dir"):
            row["path"] = r["dir"]
            row["id"] = r["dir"]
        rows.append(row)
    return rows


def _leading(dash):
    """Before anything is routed: the best run so far at the furthest step it reached."""
    lead, lead_rank = None, None
    for r in dash.get("all_runs") or []:
        if r.get("wns") is None or r.get("state") in ("failed", "duplicate", "cancelled"):
            continue
        rank = (_STEP_INDEX.get(r.get("step"), -1),
                0 if (r.get("whs") is not None and r["whs"] < 0) else 1, r["wns"])
        if lead is None or rank > lead_rank:
            lead, lead_rank = r, rank
    return lead


def _chart(dash):
    series = []
    for r in _all_runs(dash):
        pts = [[_STEP_INDEX[s["step"]], s["wns"]] for s in r.get("steps") or []
               if s.get("step") in _STEP_INDEX and s.get("wns") is not None]
        if pts:
            series.append({"name": re.sub(r"^S\d_", "", str(r.get("run_id") or "?")),
                           "points": pts, "live": r.get("state") == "running"})
    if not series:
        return None
    series = series[-40:]
    # End labels for the five best finishes and anything still running; with
    # twenty runs, labelling all of them only piles them up. Hover shows the rest.
    best = sorted(series, key=lambda se: se["points"][-1][1], reverse=True)[:5]
    for se in series:
        if se not in best and not se["live"]:
            se["label"] = False
    return {"kind": "chart", "tab": "overview", "title": "WNS by step",
            "x": {"labels": [label for _, label in STEPS]}, "y": {"label": "ns", "zero": True},
            "series": series,
            "caption": "WNS (ns) at each step for every run; labels on the five best, "
                       "hover a line for any run"}


def _timeline_items(events, now):
    out = []
    for e in events:
        it = {"at": e.get("at"), "text": "%s  (%s ago)" % (e.get("text") or "", _ago(e.get("at"), now))
              if _ago(e.get("at"), now) else e.get("text") or ""}
        tone = {"failed": "bad", "ask": "warn", "answer": "info", "done": "ok"}.get(e.get("kind"))
        if tone:
            it["tone"] = tone
        out.append(it)
    return out


def _overview(dash, now):
    best = dash.get("best") or {}
    label = best.get("run") or ""
    if best.get("wns") is None:
        lead = _leading(dash)
        if lead:
            best = lead
            label = "%s at %s (not routed yet)" % (lead.get("run_id"), lead.get("step"))
    counts = {}
    for r in _all_runs(dash):
        counts[r.get("state")] = counts.get(r.get("state"), 0) + 1
    runs = counts if counts else (dash.get("runs") or {})
    whs = best.get("whs")
    sections = [{"kind": "stats", "tab": "overview", "title": "", "tiles": [
        {"label": "best WNS", "value": _ns(best.get("wns")) or "—",
         "sub": label or "no result yet", "tone": _tone(best.get("wns"), whs)},
        {"label": "WHS", "value": _ns(whs) or "—", "sub": "hold",
         "tone": "bad" if whs is not None and whs < 0 else None},
        {"label": "TNS", "value": _tns(best.get("tns")) or "—", "sub": "ns"},
        {"label": "runs", "value": str(sum(v for v in runs.values() if isinstance(v, int))),
         "sub": "%d running · %d done · %d failed" % (runs.get("running") or 0,
                                                             runs.get("done") or 0,
                                                             runs.get("failed") or 0)},
    ]}]
    llm = _llm_tile(dash.get("token_usage"))
    if llm:
        sections[0]["tiles"].append(llm)
    chart = _chart(dash)
    if chart:
        sections.append(chart)
    stages = dash.get("stages") or []
    if stages:
        sections.append({"kind": "table", "tab": "overview", "title": "Best per stage",
                         "columns": ["stage", "run", "WNS", "WHS", "TNS"], "numeric": [2, 3, 4],
                         "rows": [{"cells": [s.get("stage"), s.get("run") or "",
                                             _ns(s.get("wns")) or "", _ns(s.get("whs")) or "",
                                             _tns(s.get("tns"))],
                                   "cell_tones": [None, None, _tone(s.get("wns"), s.get("whs")),
                                                  None, None]} for s in stages]})
    latest = (dash.get("timeline") or [])[-6:]
    if latest:
        sections.append({"kind": "timeline", "tab": "overview", "title": "Latest",
                         "items": _timeline_items(list(reversed(latest)), now)})
    launch = dash.get("launch") or {}
    agent = dash.get("agent") or {}
    pid = dash.get("supervisor_pid")
    kv = [
        {"label": "Phase", "value": dash.get("phase") or "-"},
        {"label": "Next", "value": dash.get("description") or dash.get("next_step") or "-"},
        {"label": "Supervisor", "value": "running, pid %s" % pid if dash.get("supervisor_alive")
         else "finished" if run_state(dash) == "done" else "stopped",
         "tone": "ok" if dash.get("supervisor_alive") or run_state(dash) == "done" else "warn"},
        {"label": "Launch", "value": "%s (%s)" % (launch.get("backend") or "-",
                                                 launch.get("reason") or "-")},
        {"label": "Agent", "value": "%s via %s" % (agent.get("state") or "unknown",
                                                  agent.get("via") or "-")},
        {"label": "Checkpoint", "value": dash.get("dcp") or "-"},
    ]
    if dash.get("web_url"):
        # The secret link to this page in a browser (tc web): to share or open elsewhere.
        kv.append({"label": "Live page", "value": dash["web_url"]})
    sections.append({"kind": "kv", "tab": "overview", "title": "Run", "items": kv})
    return sections


def _llm_tile(usage):
    """
    The model calls' cost as one stat tile (token_usage.totals), or None when
    no call was made and no decision was answered by someone who may have
    spent tokens elsewhere.
    """
    if not usage:
        return None
    elsewhere = (usage.get("decisions_without_engine_tokens") or {}).get("ide_agent", 0)
    calls = usage.get("calls") or 0
    if not calls and not elsewhere:
        return None
    sub = "%d call%s · %s in · %s out" % (
        calls, "" if calls == 1 else "s",
        token_usage.fmt_tokens((usage.get("input") or 0) + (usage.get("cache_read") or 0)
                               + (usage.get("cache_write") or 0)),
        token_usage.fmt_tokens(usage.get("output")))
    if usage.get("calls_without_usage"):
        sub += " · %d without usage" % usage["calls_without_usage"]
    if elsewhere:
        sub += " · %d answered by the editor agent (its tokens are not seen here)" % elsewhere
    value = token_usage.fmt_cost(usage.get("cost_usd"))
    if usage.get("cost_basis") == "unknown" and usage.get("cost_usd") is not None:
        value += " (est.)"
    return {"label": "model cost", "value": value if calls else "—", "sub": sub}


def _decisions(dash, now):
    out_dir = dash.get("output_dir") or ""
    decs = dash.get("decisions") or []
    handled = bool(dash.get("supervisor_alive")) and agent_on_it(dash.get("agent"), _pending(dash), now)
    waiting, answered = [], []
    for d in decs:
        if d.get("state") == "pending":
            val, plan = _value_text(d.get("default"))
            card = {"title": d.get("point") or d.get("id") or "?", "tone": "info" if handled else "warn",
                    "badges": [{"text": "agent answering", "tone": "info"} if handled
                               else {"text": "waiting", "tone": "warn"}],
                    "text": "default %s applies %s" % (val, _until(d.get("deadline_utc"), now)
                                                       or "at the deadline"),
                    "detail": str(d.get("default_why") or "")[:1500],
                    "paths": [{"label": "evidence & question", "path": d["file"]}] if d.get("file") else [],
                    "ask": ("Answer the pending timing-closure decision %s for the run in %s, "
                            "following the timing-closure skill." % (d.get("id"), out_dir))}
            if plan:
                card["paths"].append({"label": val, "path": plan})
            waiting.append(card)
        else:
            who = d.get("answered_by") or "?"
            val, plan = _value_text(d.get("answer"))
            badges = [{"text": JUDGE_LABEL.get(who, who), "tone": "info" if who != "rules" else "muted"}]
            text = "→ %s" % val
            if who != "rules" and d.get("state") == "answered":
                same = json.dumps(d.get("answer"), sort_keys=True) == json.dumps(d.get("default"), sort_keys=True)
                if same:
                    text += " · same as the rule"
                else:
                    text += " · overrode the rule"
                    badges.append({"text": "overrode the rule", "tone": "warn"})
            if d.get("state") == "expired":
                badges.append({"text": "expired", "tone": "muted"})
            card = {"title": d.get("point") or d.get("id") or "?", "badges": badges, "text": text,
                    "detail": " · ".join(x for x in (str(d.get("why") or "")[:1500],
                                                          _clock(d.get("answered_utc")),
                                                          d.get("model") or "") if x),
                    "paths": [{"label": "record", "path": d["file"]}] if d.get("file") else []}
            if plan:
                card["paths"].append({"label": val, "path": plan})
            answered.append(card)
    plans = [{"title": "plan %s" % p.get("round"), "tone": "info", "text": p.get("summary") or "",
              "items": ["%s (%s) %s" % (f.get("name"), f.get("kind"), f.get("why") or "")
                        for f in p.get("fixes") or []],
              "paths": [{"label": "plan.json", "path": p["file"]}] if p.get("file") else []}
             for p in dash.get("plans") or []]
    if not (waiting or answered or plans):
        return [{"kind": "cards", "tab": "decisions", "title": "", "cards": [],
                 "empty": "No judgement calls yet. They come at post-place, before Stage 2/3, "
                          "on failures, and for the planner."}]
    out = []
    for title, cards in (("Waiting", waiting), ("Planner", plans), ("Answered", list(reversed(answered)))):
        if cards:
            out.append({"kind": "cards", "tab": "decisions", "title": title, "cards": cards})
    return out


def _tail(path, lines=20, limit=32768):
    """The last lines of a log, for the first paint; the IDE follows it from there."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            text = fh.read().decode("utf-8", "replace")
    except (IOError, OSError):
        return []
    out = text.splitlines()
    return (out[1:] if size > limit else out)[-lines:]


def _logs(dash):
    out_dir = dash["output_dir"]
    sections = [{"kind": "log", "tab": "logs", "title": "Supervisor",
                 "lines": dash.get("serve_log_tail") or [],
                 "path": os.path.join(out_dir, "serve.log"), "follow": True}]
    running = [r for r in _all_runs(dash) if r.get("state") == "running" and r.get("logs")]
    for r in running[:_FOLLOWED_RUNS]:
        log = next((p for p in r["logs"] if re.search(r"vivado(_[^/]*)?\.log$", p)), r["logs"][0])
        sections.append({"kind": "log", "tab": "logs",
                         "title": "%s %s" % (r.get("run_id"), os.path.basename(log)),
                         "lines": _tail(log), "path": log, "follow": True})
    if dash.get("guard"):
        sections.append({"kind": "log", "tab": "logs", "title": "Stop guard", "lines": dash["guard"]})
    return sections


def _elapsed(dash, state, now):
    start = _parse_iso(dash.get("started_utc"))
    if not start:
        return ""
    outcome = dash.get("outcome") if isinstance(dash.get("outcome"), dict) else {}
    end = _parse_iso(outcome.get("finished_utc")) if outcome else None
    if state == "done" or (state != "running" and state != "attention" and end):
        end = end or _parse_iso(dash.get("updated_utc"))
        return "took " + _dur((end - start).total_seconds()) if end else "finished"
    return "running " + _dur((now - start).total_seconds())


def _header_badges(dash, state, now):
    badges = []
    if dash.get("supervisor_alive"):
        badges.append({"text": "supervisor running", "tone": "ok"})
    elif state == "done":
        badges.append({"text": "finished", "tone": "ok"})
    else:
        oc = dash.get("outcome") if isinstance(dash.get("outcome"), dict) else {}
        badges.append({"text": "supervisor stopped" + (": %s" % oc["outcome"] if oc.get("outcome") else ""),
                       "tone": "bad"})
    if "attached" in dash:
        a = dash.get("agent") or {}
        if not dash.get("attached"):
            badges.append({"text": "no agent attached", "tone": "muted"})
        elif a.get("state") == "waiting":
            badges.append({"text": "agent watching", "tone": "info"})
        elif a.get("state") in ("active", None):
            badges.append({"text": "agent orchestrating", "tone": "info"})
        elif a.get("state") == "unknown":
            badges.append({"text": "agent attached", "tone": "muted"})
        else:
            badges.append({"text": "agent idle %s" % _ago(a.get("last_seen_utc"), now), "tone": "bad"})
    l = dash.get("launch") or {}
    if l.get("backend"):
        label = {"lsf": "bsub", "session": "via MCP" + (" " + l["mcp"] if l.get("mcp") else ""),
                 "local": "this host"}.get(l["backend"], l["backend"])
        badges.append({"text": label + (" → bsub (failover)" if l.get("failover") else ""),
                       "tone": "bad" if l.get("failover") else "muted"})
    return badges


def _banner(dash, state, now=None):
    pend = len(dash.get("pending_decisions") or [])
    pending = _pending(dash)
    if pending and dash.get("supervisor_alive") and agent_on_it(dash.get("agent"), pending, now):
        due = _clock(min((d["deadline_utc"] for d in pending if d.get("deadline_utc")), default=""))
        return {"text": "The agent is answering %s.%s" % (
                    ", ".join(d["id"] for d in pending),
                    " If nobody answers by %s, the rule default applies." % due if due else ""),
                "tone": "info", "tab": "decisions"}
    if pend:
        return {"text": "Waiting for %d decision%s — see them under Decisions. Defaults apply "
                        "at the deadline if nobody answers." % (pend, "s" if pend > 1 else ""),
                "tone": "warn", "tab": "decisions"}
    if dash.get("verdict"):
        v = dash["verdict"]
        text = "; ".join(v) if isinstance(v, list) else str(v)
        return {"text": text, "tone": "bad" if "NOT CLOSED" in text else "ok"}
    if state in ("stopped", "failed"):
        return {"text": "The supervisor stopped before the run finished. Ask the agent to resume it.",
                "tone": "bad"}
    return None


def detail_document(dash, now=None):
    """One run in full, laid out like the companion dashboard. `dash` is a watch.dashboard."""
    now = now or datetime.datetime.utcnow()
    out = dash["output_dir"]
    item = list_item(dash, design=dash.get("design"), updated=dash.get("updated_utc"),
                     agent=dash.get("agent"), now=now)
    state = item["state"]
    sections = [_stepper(dash)] + _overview(dash, now)
    rows = _run_rows(dash, now)
    sections.append({"kind": "table", "tab": "runs", "title": "",
                     "columns": ["run", "state", "WNS", "WHS", "TNS", "time"],
                     "numeric": [2, 3, 4, 5], "chips_title": "progress", "rows": rows}
                    if rows else {"kind": "cards", "tab": "runs", "title": "", "cards": [],
                                  "empty": "No runs yet."})
    sections += _decisions(dash, now)
    events = list(reversed(dash.get("timeline") or []))
    sections.append({"kind": "timeline", "tab": "timeline", "title": "",
                     "items": _timeline_items(events, now)} if events else
                    {"kind": "cards", "tab": "timeline", "title": "", "cards": [], "empty": "Nothing yet."})
    sections += _logs(dash)
    for s in sections:
        for coll in ("items", "rows", "tiles", "cards"):
            for it in s.get(coll) or []:
                if isinstance(it, dict) and it.get("tone") is None:
                    it.pop("tone", None)

    pend = len(dash.get("pending_decisions") or [])
    doc = {"schema": SCHEMA}
    doc.update({k: item[k] for k in ("id", "title", "state", "metrics")})
    doc["subtitle"] = " · ".join(x for x in (_elapsed(dash, state, now),
                                                  dash.get("description") or "") if x) \
        or item["subtitle"]
    doc["badges"] = _header_badges(dash, state, now)
    if dash.get("updated_utc"):
        doc["updated"] = dash["updated_utc"]
    doc["tabs"] = [dict({"id": k, "label": label}, **({"count": pend} if k == "decisions" and pend else {}))
                   for k, label in TABS]
    banner = _banner(dash, state, now)
    if banner:
        doc["banner"] = banner
    doc["sections"] = sections
    doc["paths"] = [{"label": "Output directory", "path": out}]
    if dash.get("dcp"):
        doc["paths"].append({"label": "Checkpoint", "path": dash["dcp"]})
    if os.path.isfile(os.path.join(out, "final_summary.txt")):
        doc["paths"].append({"label": "Final summary",
                             "path": os.path.join(out, "final_summary.txt")})
    if dash.get("web_url"):
        # Outside the renderer's schema (it ignores the key): for an agent reading the
        # view action's reply, the link to give the user.
        doc["web_url"] = dash["web_url"]
    doc["ask"] = ("Look at the timing-closure run in %s and tell me where it stands; if it is "
                  "active and you are not orchestrating it, resume the loop from the "
                  "timing-closure skill." % out)
    return doc
