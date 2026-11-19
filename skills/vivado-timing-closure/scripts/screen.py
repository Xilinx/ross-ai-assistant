#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
screen.py -- a cheap check of a planner round's fixes before any of them runs.

planner.screen (off by default): before planner-apply generates a round's fix
runs, one short job opens the winner's routed checkpoint -- no opt, place or
route, nothing written but a text file -- and asks, for every fix, whether the
objects it names are on the paths that fail:

  * the failing paths (and the near-failing ones an over-constraint would
    pull in): their slack, clocks, cells and nets;
  * every object query in a fix's XDC (pblock cells, MAX_FANOUT nets, group_path
    -from/-to/-through, clocks), evaluated as Vivado would evaluate it.

Then, per fix (assess()):
  touches  -- its objects are on N of the failing paths (reach = N / all failing)
  misses   -- its objects exist but are on none of them
  empty    -- every query matched nothing (the fix would apply to nothing)
  unknown  -- nothing to check without implementing it: directive-only flows,
              early-fix switches, placers; or the screen job did not finish

planner.screen: report keeps every fix and records the verdict on its run (the
methodology tag and state.planner.screen), so later rounds can learn whether
reach predicts a gain. prune drops misses and empty fixes -- never all of
them. Why not rank by post-place WNS instead: on 191 runs it ranked post-route
results at 0.41 (LEARNINGS.md 2026-10-12).

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import json
import os
import re

SCREEN_MODES = ("off", "report", "prune")
RAW = "screen_raw.txt"
RESULT = "screen.json"
MAX_NAMES = 20000            # per query; a larger match is sampled
DEFAULT_MAX_PATHS = 1000
MAX_MARGIN_NS = 1.0
# planner.py's RQS categories -> report_parser's rqs_counts keys.
RQS_KEYS = {"Utilization": "UTIL", "Clocking": "CLOCK", "Congestion": "CONG",
            "Timing": "TIMING", "Netlist": "NETLIST"}
_QUERY = re.compile(r"\[\s*(get_cells|get_nets|get_pins|get_clocks|get_ports|all_[a-z_]+)\b")


def mode_of(cfg):
    m = str(((cfg or {}).get("planner") or {}).get("screen", "off")).strip().lower()
    if m in ("false", "no", "0", "none"):
        return "off"
    return m if m in SCREEN_MODES else "off"


def _outer_queries(line):
    """The outermost [get_* ...] expressions in one XDC line, brackets included."""
    out = []
    for m in _QUERY.finditer(line):
        start = m.start()
        if any(s <= start < e for s, e in out):
            continue
        depth, i = 0, start
        while i < len(line):
            if line[i] == "[":
                depth += 1
            elif line[i] == "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth == 0:
            out.append((start, i + 1))
    return [line[s:e] for s, e in out]


def queries_of(fix):
    """[(qid, tcl)] for the objects a validated fix names."""
    qs = []
    for line in fix.get("xdc_lines") or []:
        if line.strip().startswith("#"):
            continue
        for q in _outer_queries(line):
            if q not in [t for _, t in qs]:
                qs.append(("q%d" % len(qs), q))
    if fix.get("kind") == "over_constrain":
        for c in fix.get("clocks") or []:
            qs.append(("q%d" % len(qs), "[get_clocks {%s}]" % c))
    return qs


def screenable(fix):
    """Whether a fix names anything a checkpoint query can check."""
    if fix.get("kind") in ("over_constrain", "rqs"):
        return True
    return bool(queries_of(fix))


def margin_ns(fixes):
    """How far above zero slack to collect paths: the largest over-constraint."""
    m = 0.0
    for fx in fixes:
        if fx.get("kind") == "over_constrain" and fx.get("oc_value") is not None:
            m = max(m, float(fx["oc_value"]))
    return min(m, MAX_MARGIN_NS)


def screen_tcl(fixes, max_paths=DEFAULT_MAX_PATHS):
    """The body after open_checkpoint: writes $run_dir/screen_raw.txt, changes nothing."""
    margin = margin_ns(fixes)
    lines = [
        "# --- Planner screen: failing paths and each fix's objects (read-only) ---",
        "set _scr [open [file join $run_dir %s] w]" % RAW,
        "proc _tca_net {n} {",
        "    set p [get_property -quiet PARENT $n]",
        "    if {$p eq \"\"} { return $n }",
        "    return $p",
        "}",
        "proc _tca_put {fh fix qid objs} {",
        "    set n [llength $objs]",
        "    if {$n == 0} { puts $fh \"QUERY\\t$fix\\t$qid\\tnone\\t0\\t\"; return }",
        "    set cls [string tolower [get_property -quiet CLASS [lindex $objs 0]]]",
        "    set objs [lrange $objs 0 %d]" % (MAX_NAMES - 1),
        "    set names {}",
        "    if {$cls eq \"pin\"} {",
        "        set cls cell",
        "        foreach c [get_cells -quiet -of_objects $objs] { lappend names $c }",
        "    } elseif {$cls eq \"net\"} {",
        "        foreach o $objs { lappend names [_tca_net $o] }",
        "    } else {",
        "        foreach o $objs { lappend names $o }",
        "    }",
        "    puts $fh \"QUERY\\t$fix\\t$qid\\t$cls\\t$n\\t[join $names { }]\"",
        "}",
        "set _paths [get_timing_paths -quiet -max_paths %d -nworst 1 -sort_by slack "
        "-slack_lesser_than %.4f]" % (int(max_paths), margin),
        "puts $_scr \"MARGIN\\t%.4f\\t%d\"" % (margin, int(max_paths)),
        "set _i 0",
        "foreach _p $_paths {",
        "    set _nets {}",
        "    foreach _n [get_nets -quiet -of_objects $_p] { lappend _nets [_tca_net $_n] }",
        "    puts $_scr \"PATH\\t$_i\\t[get_property SLACK $_p]\\t[get_property -quiet "
        "STARTPOINT_CLOCK $_p]\\t[get_property -quiet ENDPOINT_CLOCK $_p]\\t"
        "[join [get_cells -quiet -of_objects $_p] { }]\\t[join $_nets { }]\"",
        "    incr _i",
        "}",
    ]
    for fx in fixes:
        for qid, q in queries_of(fx):
            lines += [
                "if {[catch {set _o %s} _e]} {" % q,
                "    puts $_scr \"QUERY\\t%s\\t%s\\terror\\t-1\\t[string map {\\n { } \\t { }} $_e]\"" % (
                    fx["name"], qid),
                "} else {",
                "    _tca_put $_scr %s %s $_o" % (fx["name"], qid),
                "}",
            ]
    lines += ["puts $_scr \"END\"", "close $_scr", ""]
    return "\n".join(lines)


def parse_raw(text):
    """{'margin', 'paths': [{slack, src, dst, cells, nets}], 'queries': {fix: {qid: {...}}}, 'complete'}"""
    out = {"margin": 0.0, "paths": [], "queries": {}, "complete": False}
    for line in (text or "").splitlines():
        f = line.split("\t")
        if f[0] == "MARGIN" and len(f) >= 2:
            out["margin"] = float(f[1])
        elif f[0] == "PATH" and len(f) >= 7:
            try:
                slack = float(f[2])
            except ValueError:
                continue
            out["paths"].append({"slack": slack, "src": f[3], "dst": f[4],
                                 "cells": f[5].split(), "nets": f[6].split()})
        elif f[0] == "QUERY" and len(f) >= 6:
            out["queries"].setdefault(f[1], {})[f[2]] = {
                "class": f[3], "count": int(f[4]) if f[4].lstrip("-").isdigit() else -1,
                "names": f[5].split() if f[3] != "error" else [], "error": f[5] if f[3] == "error" else ""}
        elif f[0] == "END":
            out["complete"] = True
    return out


def _prefixes(name):
    parts = name.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _touches(q, path):
    """A cell query matches a path cell or a hierarchy above it (a pblock's module)."""
    cls = q["class"]
    if "_set" not in q:
        q["_set"] = set(q["names"])
    if cls == "cell":
        return any(pre in q["_set"] for c in path["cells"] for pre in _prefixes(c))
    if cls == "net":
        return any(n in q["_set"] for n in path["nets"])
    if cls == "clock":
        return path["src"] in q["names"] or path["dst"] in q["names"]
    return False


def assess(fixes, raw, rqs_counts=None):
    """Per fix: {verdict, reach, touched, failing, ...}. raw None: the screen did not run."""
    paths = (raw or {}).get("paths") or []
    failing = [p for p in paths if p["slack"] < 0]
    done = bool(raw and raw.get("complete"))
    results = []
    for fx in fixes:
        r = {"name": fx["name"], "kind": fx.get("kind"), "verdict": "unknown",
             "failing": len(failing), "touched": 0, "reach": None, "why": ""}
        qs = queries_of(fx)
        if fx.get("kind") == "rqs":
            counts = rqs_counts or {}
            n = sum(int(counts.get(RQS_KEYS.get(c, c.upper()), 0) or 0)
                    for c in fx.get("categories") or [])
            r["suggestions"] = n
            r["verdict"], r["why"] = (("touches", "%d suggestion(s) in %s" % (
                n, ", ".join(fx.get("categories") or []))) if n else
                ("empty", "the winner has no suggestions in %s" % ", ".join(fx.get("categories") or [])))
            results.append(r)
            continue
        if not qs and fx.get("kind") != "over_constrain":
            r["why"] = "names no objects (directives, early fixes or placers only)"
            results.append(r)
            continue
        if not done:
            r["why"] = "the screen job left no complete result"
            results.append(r)
            continue
        got = (raw.get("queries") or {}).get(fx["name"], {})
        found = [got[q] for q, _ in qs if q in got and got[q]["count"] > 0]
        errors = [q for q, _ in qs if q in got and got[q]["class"] == "error"]
        r["queries"] = {qid: {"tcl": tcl, "matched": (got.get(qid) or {}).get("count", -1),
                              "class": (got.get(qid) or {}).get("class", "?")} for qid, tcl in qs}
        if fx.get("kind") == "over_constrain":
            window = [p for p in paths if p["slack"] < float(fx.get("oc_value") or 0)]
            if qs:
                window = [p for p in window if any(_touches(q, p) for q in found)]
            r["window"] = len(window)
            if not qs:
                r["touched"] = len(failing)
            else:
                r["touched"] = len([p for p in failing if any(_touches(q, p) for q in found)])
        else:
            if qs and not found and len(errors) < len(qs):
                r["verdict"], r["why"] = "empty", "every object query matched nothing"
                results.append(r)
                continue
            if errors and not found:
                r["why"] = "the queries failed in Vivado: %s" % (got[errors[0]]["error"][:120])
                results.append(r)
                continue
            if all(q["class"] not in ("cell", "net", "clock") for q in found):
                r["why"] = "names only objects that are never on a path (ports, sites ...)"
                results.append(r)
                continue
            r["touched"] = len([p for p in failing if any(_touches(q, p) for q in found)])
        if failing:
            r["reach"] = round(float(r["touched"]) / len(failing), 3)
            worst = min(p["slack"] for p in failing)
            hit = [p["slack"] for p in failing if any(_touches(q, p) for q in found)] \
                if qs else [p["slack"] for p in failing]
            r["worst_touched"] = min(hit) if hit else None
            r["touches_worst"] = bool(hit) and min(hit) == worst
        if r["touched"]:
            r["verdict"] = "touches"
            r["why"] = "on %d of %d failing paths%s" % (
                r["touched"], len(failing), " (incl. the worst)" if r.get("touches_worst") else "")
        elif failing:
            r["verdict"] = "misses"
            r["why"] = "its objects are on none of the %d failing paths" % len(failing)
        else:
            r["why"] = "no failing paths in the winner"
        results.append(r)
    return results


def keep(fixes, results, mode):
    """(kept fixes, dropped [(name, why)]): prune drops misses/empty, never every fix."""
    if mode != "prune":
        return list(fixes), []
    by = dict((r["name"], r) for r in results)
    kept = [fx for fx in fixes if by.get(fx["name"], {}).get("verdict") not in ("misses", "empty")]
    if not kept:
        return list(fixes), []
    return kept, [(fx["name"], "screen: %s (%s)" % (by[fx["name"]]["verdict"], by[fx["name"]]["why"]))
                  for fx in fixes if fx not in kept]


def tag(result):
    """A short methodology tag for a fix's run."""
    if not result:
        return ""
    if result["verdict"] == "touches" and result.get("reach") is not None:
        return " [screen: on %d/%d failing paths]" % (result["touched"], result["failing"])
    return " [screen: %s]" % result["verdict"]


def read_raw(run_dir):
    path = os.path.join(run_dir, RAW)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse_raw(fh.read())


def write_result(run_dir, results, mode, raw):
    data = {"mode": mode, "complete": bool(raw and raw.get("complete")),
            "failing_paths": len([p for p in (raw or {}).get("paths", []) if p["slack"] < 0]),
            "fixes": results}
    with open(os.path.join(run_dir, RESULT), "w") as fh:
        json.dump(data, fh, indent=2)
    return data


def generate(dcp_path, fixes, run_dir, run_name, max_paths=DEFAULT_MAX_PATHS):
    """Write run_dir/run.tcl: open the winner's routed checkpoint, screen, exit."""
    import tcl_gen
    lines = [tcl_gen.header("Planner Screen (read-only)", run_name),
             tcl_gen.RUN_DIR_TCL,
             "",
             "open_checkpoint {%s}" % dcp_path,
             screen_tcl(fixes, max_paths)]
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "run.tcl"), "w") as fh:
        fh.write(tcl_gen._finish(lines, "Planner screen %s completed successfully." % run_name,
                                 run_name) + "\n")
