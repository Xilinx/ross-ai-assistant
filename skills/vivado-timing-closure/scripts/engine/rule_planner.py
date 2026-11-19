#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
rule_planner.py -- a planner that needs no LLM.

When nobody answers a planner decision (no agent attached, no headless judge,
or the deadline passed), the round still gets a plan: the fix templates that
won planner rounds in our own suites, filled in from the round's measured
evidence. The plan goes through the same `planner-validate` as an agent's, so
it can never relax a constraint.

Templates, in the order they are ranked:

* **incremental endpoint wall** -- group the worst failing endpoints and run
  phys_opt passes plus a re-route on the winner's routed checkpoint, no
  re-place. Won design A (-0.049 -> -0.011), design B (-0.020 -> -0.003),
  VIAVI_800G_Rel2 (+46 ps), Viavi_eth800g and Viavi_drop_0815.
* **clock over-constraint** -- extra setup uncertainty on the clock that owns
  most failing paths, during placement only, at 1.5x and 2.0x |WNS| (<= 0.5 ns,
  UG949). Closed Ciena; Teradyne -0.244 -> -0.056.
* **hold-aware incremental** -- when the winner misses hold, or failing
  endpoints also carry hold violations.
* **QoR suggestions** -- the winner's own RQS, when it has a file.

What it cannot do is diagnose structure: the clock-root move that closed design C,
the SLR assignment that gained 180 ps on Barco_3screen, the fanout limit on HLS
enable nets. Those need an agent reading the evidence.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import collections
import os
import re

MAX_ENDPOINTS = 40
WALL_MAX_GAP_NS = 0.15          # an endpoint wall is worth an incremental pass up to here
OC_MAX_NS = 0.5                 # UG949: keep over-constraint at or under 0.5 ns
DOMINANT_SHARE = 0.6            # a clock "owns" the failures at this share of sampled paths
HIGH_FANOUT = 32
_RANK = re.compile(r"^\d+_")

_BRACKET = re.compile(r"\[([^\]]*)\]")


def _pin_ok(name):
    """
    A pin the XDC checker will accept inside get_pins {...}: no braces, and
    only numeric bus indices -- a `reg[SequenceNumber]` segment reads as a
    bracketed Tcl command and fails validation (the planner agents filtered
    the same way on design B).
    """
    if "{" in name or "}" in name:
        return False
    return all(re.match(r"^\d+$", b) for b in _BRACKET.findall(name))


def _pin(p):
    """A pin name as the planner's group_path lines write it (spaces -> '?')."""
    return re.sub(r"\s", "?", p)


def _wall_endpoints(paths, wns):
    """The worst failing endpoints, deduplicated, worst first."""
    out, seen = [], set()
    for p in sorted(paths, key=lambda x: x.get("slack") if x.get("slack") is not None else 0):
        ep = p.get("endpoint")
        sl = p.get("slack")
        if not ep or sl is None or sl >= 0 or ep in seen or not _pin_ok(ep):
            continue
        seen.add(ep)
        out.append(_pin(ep))
        if len(out) >= MAX_ENDPOINTS:
            break
    return out


def _dominant_clock(paths):
    clocks = collections.Counter(p.get("dst_clock") for p in paths
                                 if p.get("dst_clock") and (p.get("slack") or 0) < 0)
    if not clocks:
        return None, 0.0
    clk, n = clocks.most_common(1)[0]
    return clk, n / float(sum(clocks.values()))


def build(context, evidence):
    """
    (plan, why). `plan` is a planner plan.json dict, or None when no template
    fits -- the round then closes without fixes.
    """
    winner = context.get("winner") or {}
    wns = evidence.get("wns", winner.get("wns"))
    whs = evidence.get("whs", winner.get("whs"))
    paths = [p for p in evidence.get("paths") or [] if isinstance(p, dict)]
    max_fixes = int(context.get("max_fixes") or 4)
    if wns is None:
        return None, "the evidence has no WNS to plan against"
    if wns >= 0 and (whs is None or whs >= 0):
        return None, "the winner already meets timing"

    # A fix's run is <rank>_<name> (planner/r1/1_rule_oc_ap_clk_1p5x): matching
    # bare run ids never caught a rule fix tried in an earlier round. Fixes of
    # runs still generated, queued or running count as tried too.
    tried = set()
    for t in list(context.get("already_tried") or []) + list(context.get("in_flight") or []):
        for v in (t.get("run_id"), t.get("fix_name")):
            if v:
                tried.add(str(v))
                tried.add(_RANK.sub("", os.path.basename(str(v))))
    ev, fixes = [], []
    gap = abs(min(wns, 0.0))
    clk, share = _dominant_clock(paths)
    eps = _wall_endpoints(paths, wns)
    fan = max([p.get("max_fanout") or 0 for p in paths] or [0])
    hold_paths = evidence.get("hold_paths") or []

    ev.append({"id": "E1", "source": "evidence.json",
               "finding": "Winner %s: WNS %.3f ns, WHS %s ns; %d failing setup paths sampled%s."
               % (winner.get("run_id"), wns, "%.3f" % whs if whs is not None else "?",
                  len(paths), ("; %d%% end on clock %s" % (round(share * 100), clk)) if clk else "")})
    if eps:
        ev.append({"id": "E2", "source": "evidence.json paths",
                   "finding": "%d distinct failing endpoints among the sampled paths; the worst %d "
                              "are grouped (max fanout on those paths %d)." % (
                                  len({p.get("endpoint") for p in paths if (p.get("slack") or 0) < 0}),
                                  len(eps), fan)})
    if hold_paths or (whs is not None and whs < 0):
        ev.append({"id": "E3", "source": "evidence.json hold_paths",
                   "finding": "%d hold-violating paths sampled; WHS %s." % (
                       len(hold_paths), "%.3f" % whs if whs is not None else "?")})

    def add(fix):
        if len(fixes) < max_fixes and fix["name"] not in tried:
            fix["rank"] = len(fixes) + 1
            fixes.append(fix)

    if eps and 0 < gap <= WALL_MAX_GAP_NS:
        dirs = ["AggressiveExplore", "AlternateReplication"]
        if fan >= HIGH_FANOUT:
            dirs.append("AggressiveFanoutOpt")
        add({"name": "rule_incr_endpoint_wall", "kind": "incremental",
             "justification": "A %.0f ps wall on %d endpoints: phys_opt focused on exactly those "
                              "endpoints from the routed winner, then a re-route, without re-placing "
                              "(the template that closed most of the gap on design A and design B)."
                              % (gap * 1000, len(eps)),
             "evidence_ids": [e for e in ("E1", "E2") if any(x["id"] == e for x in ev)],
             "doc_refs": ["UG906 phys_opt_design -path_groups", "UG835 group_path"],
             "params": {"xdc_lines": ["group_path -name tc_wall -to [get_pins {%s}]" % " ".join(eps)],
                        "path_groups": ["tc_wall"], "physopt_directives": dirs,
                        "route_directive": "NoTimingRelaxation", "passes": 2}})

    if clk and share >= DOMINANT_SHARE and 0 < gap:
        for mult in (1.5, 2.0):
            val = gap * mult
            params = ({"multiplier": mult} if val <= OC_MAX_NS else {"uncertainty_ns": OC_MAX_NS})
            params["clocks"] = [clk]
            name = "rule_oc_%s_%sx" % (re.sub(r"[^a-z0-9]+", "_", clk.lower()).strip("_")[:20],
                                       str(mult).replace(".", "p"))
            add({"name": name[:40], "kind": "over_constrain",
                 "justification": "%d%% of the failing paths end on %s: tighten setup on that clock "
                                  "during placement (%.3f ns) and remove it before routing, so the "
                                  "placer pushes where the failures are (UG949)."
                                  % (round(share * 100), clk, min(val, OC_MAX_NS)),
                 "evidence_ids": ["E1"], "doc_refs": ["UG949 over-constraining"],
                 "params": params})

    if (whs is not None and whs < 0) or hold_paths:
        if gap <= WALL_MAX_GAP_NS:
            add({"name": "rule_incr_holdfix", "kind": "incremental",
                 "justification": "The winner trades setup against hold: phys_opt with the "
                                  "hold-fix directive from the routed checkpoint, then a re-route.",
                 "evidence_ids": [e for e in ("E1", "E3") if any(x["id"] == e for x in ev)],
                 "doc_refs": ["UG906 phys_opt_design ExploreWithAggressiveHoldFix"],
                 "params": {"physopt_directives": ["ExploreWithAggressiveHoldFix"],
                            "route_directive": "Explore", "passes": 2}})

    if context.get("rqs_file_available"):
        add({"name": "rule_rqs_timing", "kind": "rqs",
             "justification": "Apply the winner's own timing and netlist QoR suggestions from its "
                              "routed checkpoint.",
             "evidence_ids": ["E1"], "doc_refs": ["UG906 report_qor_suggestions"],
             "params": {"categories": ["Timing", "Netlist"]}})

    if not fixes:
        return None, ("no rule template fits: gap %.0f ps, %s" % (
            gap * 1000, "no dominant clock" if not clk or share < DOMINANT_SHARE
            else "already tried"))
    plan = {"schema_version": 1,
            "summary": "Rule plan (no LLM answered): %s. Templates from measured planner wins; "
                       "structural causes (clock root, SLR assignment, fanout of specific nets) "
                       "need an agent." % ev[0]["finding"],
            "evidence": ev, "fixes": fixes, "source": "rules"}
    return plan, "%d rule fix(es): %s" % (len(fixes), ", ".join(f["name"] for f in fixes))
