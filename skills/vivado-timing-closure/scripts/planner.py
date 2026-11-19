#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
planner.py -- validation for the evidence-driven Stage 2 planner.

Standalone and stdlib-only. The planner (an LLM agent) proposes fixes in a
plan.json; nothing it writes is executed until this module accepts it. Every
engineering limit lives here, not in the agent's prompt:

  * only three fix kinds (flow directives + optional physical XDC,
    over-constrain, RQS by category);
  * every fix must cite evidence;
  * XDC may only contain physical-implementation commands -- never timing
    exceptions or clock/constraint changes, so a plan cannot "close" timing by
    relaxing what is being measured -- and no Tcl escape hatches;
  * the number of fixes is capped by the caller (max_fixes and the run budget).

Plan schema (schema_version 1):

  {"schema_version": 1,
   "summary": "one paragraph: what limits timing and why",
   "evidence": [{"id": "E1", "finding": "...", "source": "report/command"}],
   "fixes": [{"rank": 1, "name": "slr_pblock_top", "kind": "flow",
              "justification": "...", "evidence_ids": ["E1"],
              "doc_refs": ["UG949 ..."], "params": {...}}]}

  kind "flow":           params: opt_directive, place_directive | place_subdirective,
                         physopt_directive, route_directive, xdc_lines [str]
                         (at least one, unless the fix sets early_fixes)

  Optional on a fix that re-places (flow, over_constrain, rqs):
    "placers": ["explore", "auto_5"]   one run per Stage 1 flow named: that flow's
                                       placer, opt directive and early fixes are
                                       replayed, with this fix added
    "early_fixes": {"structural_analysis": true}
                                       switch Stage 1 early fixes on or off for
                                       this fix (keys: EARLY_FIX_KEYS)
  kind "over_constrain": params: uncertainty_ns (0 < x <= 1) | multiplier
                         (0.5 .. 4, times |winner WNS|); optional clocks [src] or
                         [src, dst]
  kind "rqs":            params: categories subset of RQS_CATEGORIES (needs the
                         winner's .rqs file)
  kind "incremental":    starts from the WINNER'S ROUTED checkpoint (no re-place):
                         params: xdc_lines [group_path lines only], path_groups
                         [names] (phys_opt_design -path_groups), physopt_directives
                         [names], route_directive, passes (1..4, default 2)
                         (at least one of path_groups / physopt_directives /
                         route_directive)

  XDC group_path (flow and incremental): user groups need -name <ident> plus
  -from/-to/-through; a clock group may be raised with
  `group_path -name [get_clocks X] -weight 2` (UG835: -weight is only for
  clock groups). -default is refused. group_path changes what the tools
  optimise first, never the slack being measured.

Requires Python 3.6+.
"""

from __future__ import print_function

import re

FIX_KINDS = ("flow", "over_constrain", "rqs", "incremental")
# The early_flow_fix switches a fix may set (tcl_gen.early_fixes_block).
EARLY_FIX_KEYS = ("mbufg_group", "hardsync_ce", "gt_mrmac_loc", "pcie_clock_root",
                  "structural_analysis", "hardip_proximity_pblock",
                  "prevent_hardip_slr_crossing")
# Stage 1 flows a fix may name as placers (tcl_gen.STAGE1_FLOWS).
PLACERS = ("default_placer", "explore", "auto_1", "auto_2", "auto_3", "auto_4", "auto_5",
           "aggressive_placer")
RQS_CATEGORIES = ("Utilization", "Clocking", "Congestion", "Timing", "Netlist")

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SUBDIR_TOKEN = re.compile(r"^[A-Za-z]+\.[A-Za-z0-9_.]+$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")
_CLOCK = re.compile(r"^[A-Za-z0-9_./*\[\]-]+$")

# XDC: physical implementation only.
XDC_COMMANDS = {"create_pblock", "resize_pblock", "add_cells_to_pblock",
                "remove_cells_from_pblock", "set_property", "group_path"}
GROUP_PATH_FLAGS = {"name", "from", "to", "through", "weight"}
MAX_PASSES = 4

# -directive values each command accepts (Vivado 2026.1 `help <command>`).
# A plan naming another one failed only once its run reached the command: a
# model's incremental fix with phys_opt_design -directive ExploreWithRemap (an
# opt_design directive) died with Constraints 18-641 after the run had queued
# and opened the checkpoint. RQS is left out: it needs the run's .rqs file read
# first, which only the rqs fix kind does. place_design is not listed: on Versal
# it also takes Auto_n, AltSpreadLogic_*, SSI_* and others its help omits.
DIRECTIVES = {
    "opt_directive": ("Explore", "ExploreArea", "ExploreWithRemap", "ExploreSequentialArea",
                      "RuntimeOptimized", "Default"),
    "physopt_directive": ("Explore", "ExploreWithHoldFix", "ExploreWithAggressiveHoldFix",
                          "AggressiveExplore", "AlternateReplication", "AggressiveFanoutOpt",
                          "AlternateFlowWithRetiming", "AddRetime", "RuntimeOptimized",
                          "Default"),
    "route_directive": ("Explore", "AggressiveExplore", "NoTimingRelaxation",
                        "MoreGlobalIterations", "HigherDelayCost", "AdvancedSkewModeling",
                        "AlternateCLBRouting", "RuntimeOptimized", "Quick", "Default"),
}


def _directive_errors(where, key, value):
    """A name the command does not take, with the ones it does."""
    known = DIRECTIVES.get(key)
    if known is None or value is None or not isinstance(value, str) or value in known:
        return []
    return ["%s: %s %r is not a %s directive (one of %s)" % (
        where, key, value, {"opt_directive": "opt_design", "physopt_directive": "phys_opt_design",
                            "route_directive": "route_design"}[key], ", ".join(known))]
XDC_PROPERTIES = {
    "MAX_FANOUT", "IS_SOFT", "CONTAIN_ROUTING", "EXCLUDE_PLACEMENT", "PARENT",
    "SNAPPING_MODE", "LOC", "BEL", "USER_SLL_REG", "USER_CROSSING_SLR",
    "USER_SLR_ASSIGNMENT", "CLOCK_DELAY_GROUP", "USER_CLOCK_ROOT",
    "CLOCK_BUFFER_TYPE", "USER_CLOCK_EXPANSION_WINDOW", "DONT_TOUCH",
    "FORCE_MAX_FANOUT", "PHYS_OPT_SKIP", "OPT_SKIP", "KEEP_COMPATIBLE",
}
# Bracketed sub-commands allowed inside an XDC command (object queries only).
XDC_QUERY = re.compile(r"^(get_[a-z_]+|all_[a-z_]+|filter|list|concat)$")
# Never allowed anywhere, even inside strings: timing exceptions, constraint
# changes, and Tcl that could execute arbitrary code.
XDC_FORBIDDEN = re.compile(
    r"\b(set_false_path|set_multicycle_path|set_max_delay|set_min_delay|"
    r"set_clock_groups|set_clock_uncertainty|create_clock|create_generated_clock|"
    r"set_input_delay|set_output_delay|set_case_analysis|set_disable_timing|"
    r"set_clock_latency|set_max_time_borrow|set_bus_skew|set_data_check|"
    r"reset_[a-z_]+|delete_[a-z_]+|remove_[a-z_]+_(?:path|constraint)|"
    r"exec|source|open|file|eval|uplevel|upvar|interp|puts|exit|proc|rename|"
    r"namespace|package|load|write_[a-z_]+|read_[a-z_]+)\b")
MAX_XDC_LINES = 200


def _xdc_commands(lines):
    """Join continuations, drop comments, split on top-level ';'."""
    text, buf = [], ""
    for raw in lines:
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        text.append(buf)
        buf = ""
    if buf:
        text.append(buf)
    cmds = []
    for line in text:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for part in stripped.split(";"):
            if part.strip():
                cmds.append(part.strip())
    return cmds


def _top_level(cmd):
    """cmd with every [...] replaced by OBJ and {...} by LIT (innermost first)."""
    prev = None
    while prev != cmd:
        prev = cmd
        cmd = re.sub(r"\[[^\[\]]*\]", " OBJ ", cmd)
        cmd = re.sub(r"\{[^{}]*\}", " LIT ", cmd)
    return cmd


def _group_path_errors(cmd):
    """UG835 rules for group_path, on one command."""
    flat = _top_level(cmd).split()
    flags = [t[1:] for t in flat[1:] if t.startswith("-")]
    bad = [f for f in flags if f not in GROUP_PATH_FLAGS]
    if bad:
        return ["group_path option(s) %s not allowed (allowed: -%s; never -default): %s"
                % (", ".join("-" + b for b in bad), ", -".join(sorted(GROUP_PATH_FLAGS)),
                   cmd[:120])]
    if "name" not in flags:
        return ["group_path needs -name: %s" % cmd[:120]]
    m = re.search(r"-name\s+(\[[^\]]*\]|\S+)", cmd)
    name = m.group(1) if m else ""
    has_filter = any(f in flags for f in ("from", "to", "through"))
    if "weight" in flags:
        w = re.search(r"-weight\s+(\S+)", cmd)
        if not w or w.group(1) not in ("1", "2"):
            return ["group_path -weight must be 1 or 2: %s" % cmd[:120]]
        if has_filter or not name.startswith("[get_clocks"):
            return ["group_path -weight is only for a clock group "
                    "(-name [get_clocks X] -weight 2, no -from/-to/-through): %s" % cmd[:120]]
        return []
    if not _IDENT.match(name):
        return ["group_path -name must be a plain group name: %s" % cmd[:120]]
    if not has_filter:
        return ["group_path needs -from, -to or -through: %s" % cmd[:120]]
    return []


def group_names(lines):
    """User path groups created by group_path -name <ident> in xdc lines."""
    names = []
    for cmd in _xdc_commands(lines or []):
        m = re.match(r"group_path\b.*-name\s+([A-Za-z][A-Za-z0-9_]*)\b", cmd)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def validate_xdc(lines, only=None):
    errors = []
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        return ["xdc_lines must be a list of strings"]
    if len(lines) > MAX_XDC_LINES:
        errors.append("xdc_lines has %d lines (max %d)" % (len(lines), MAX_XDC_LINES))
    for cmd in _xdc_commands(lines):
        m = XDC_FORBIDDEN.search(cmd)
        if m:
            errors.append("forbidden in XDC (%s): %s" % (m.group(1), cmd[:120]))
            continue
        head = cmd.split()[0]
        allowed = only or XDC_COMMANDS
        if head not in allowed:
            errors.append("XDC command %r not allowed (allowed: %s): %s"
                          % (head, ", ".join(sorted(allowed)), cmd[:120]))
            continue
        for sub in re.findall(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)", cmd):
            if not XDC_QUERY.match(sub):
                errors.append("bracketed command %r not allowed (object queries only): %s"
                              % (sub, cmd[:120]))
        if head == "group_path":
            errors.extend(_group_path_errors(cmd))
            continue
        if head == "set_property":
            toks = cmd.split()
            if len(toks) < 3 or toks[1].startswith("-"):
                errors.append("set_property must name one property (no -dict): %s"
                              % cmd[:120])
                continue
            prop = toks[1].upper()
            if prop not in XDC_PROPERTIES:
                errors.append("property %s not allowed (physical properties only): %s"
                              % (toks[1], cmd[:120]))
            if prop == "DONT_TOUCH" and toks[2].strip("{}").upper() not in ("0", "FALSE"):
                errors.append("DONT_TOUCH may only be cleared (0/false): %s" % cmd[:120])
    return errors


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_fix(fix, ctx, evidence_ids):
    """Return (normalized_fix_or_None, [errors])."""
    errors = []
    where = "fix %r" % (fix.get("name") if isinstance(fix, dict) else fix)
    if not isinstance(fix, dict):
        return None, ["%s: must be an object" % where]
    name = fix.get("name", "")
    if not isinstance(name, str) or not _NAME.match(name):
        errors.append("%s: name must match %s" % (where, _NAME.pattern))
    kind = fix.get("kind")
    if kind not in FIX_KINDS:
        errors.append("%s: kind must be one of %s" % (where, ", ".join(FIX_KINDS)))
    rank = fix.get("rank")
    if not isinstance(rank, int) or rank < 1:
        errors.append("%s: rank must be a positive integer" % where)
    just = fix.get("justification", "")
    if not isinstance(just, str) or len(just.strip()) < 20:
        errors.append("%s: justification must explain the fix (>= 20 chars)" % where)
    ev = fix.get("evidence_ids") or []
    if not ev or not all(e in evidence_ids for e in ev):
        errors.append("%s: evidence_ids must cite ids from the plan's evidence list" % where)
    params = fix.get("params") or {}
    if not isinstance(params, dict):
        return None, errors + ["%s: params must be an object" % where]

    norm = {"name": name, "kind": kind, "rank": rank, "justification": (just or "").strip(),
            "evidence_ids": ev, "doc_refs": fix.get("doc_refs") or []}

    placers = fix.get("placers")
    if placers is not None:
        known = ctx.get("placers") or PLACERS
        if kind == "incremental":
            errors.append("%s: placers apply to fixes that re-place; an incremental fix "
                          "starts from the winner's routed checkpoint" % where)
        elif not isinstance(placers, list) or not placers or len(placers) > len(PLACERS) or \
                not all(isinstance(x, str) and x in known for x in placers) or \
                len(set(placers)) != len(placers):
            errors.append("%s: placers must be distinct Stage 1 flows of this run: %s"
                          % (where, ", ".join(known)))
        else:
            norm["placers"] = list(placers)
    early = fix.get("early_fixes")
    if early is not None:
        if kind == "incremental":
            errors.append("%s: early_fixes apply before opt_design; an incremental fix "
                          "starts from a routed checkpoint" % where)
        elif not isinstance(early, dict) or not early or \
                not all(k in EARLY_FIX_KEYS and isinstance(v, bool) for k, v in early.items()):
            errors.append("%s: early_fixes must map some of %s to true or false"
                          % (where, ", ".join(EARLY_FIX_KEYS)))
        else:
            norm["early_fixes"] = dict(early)

    if kind == "flow":
        known = ("opt_directive", "place_directive", "place_subdirective",
                 "physopt_directive", "route_directive", "xdc_lines")
        extra = [k for k in params if k not in known]
        if extra:
            errors.append("%s: unknown flow params %s" % (where, extra))
        if not any(params.get(k) for k in known) and not fix.get("early_fixes"):
            errors.append("%s: flow fix needs at least one of %s (or early_fixes)"
                          % (where, ", ".join(known)))
        for k in ("opt_directive", "place_directive", "physopt_directive", "route_directive"):
            v = params.get(k)
            if v is not None and (not isinstance(v, str) or not _IDENT.match(v)):
                errors.append("%s: %s must be a single directive name" % (where, k))
            else:
                errors += _directive_errors(where, k, v)
        if params.get("place_directive") and params.get("place_subdirective"):
            errors.append("%s: give place_directive OR place_subdirective, not both" % where)
        sd = params.get("place_subdirective")
        arch = str(ctx.get("architecture") or "")
        if sd is not None and arch and not arch.lower().startswith("versal"):
            # Vivado_Tcl 4-2310: placer sub-directives exist on Versal only.
            errors.append("%s: place_subdirective is Versal-only and this part is %s; "
                          "use place_directive (e.g. Auto_1, AltSpreadLogic_high, "
                          "SSI_BalanceSLRs)" % (where, arch))
        elif sd is not None:
            toks = sd.replace("{", " ").replace("}", " ").split() if isinstance(sd, str) else []
            if not toks or not all(_SUBDIR_TOKEN.match(t) for t in toks):
                errors.append("%s: place_subdirective must be tokens like Floorplan.X GPlace.Y"
                              % where)
            else:
                norm["place_subdirective"] = " ".join(toks)
        if params.get("xdc_lines"):
            for e in validate_xdc(params["xdc_lines"]):
                errors.append("%s: %s" % (where, e))
            for cmd in _xdc_commands(params["xdc_lines"]):
                if cmd.startswith("group_path") and re.search(r"-hier(archical)?\b", cmd):
                    errors.append(
                        "%s: in a flow fix, group_path must list the failing endpoints "
                        "explicitly, not a -hierarchical query (design B: 155k-187k-object "
                        "groups drove post-place WNS from -0.38 to -1.08): %s"
                        % (where, cmd[:120]))
        for k in ("opt_directive", "place_directive", "physopt_directive",
                  "route_directive", "xdc_lines"):
            if params.get(k):
                norm[k] = params[k]

    elif kind == "over_constrain":
        u, m = _num(params.get("uncertainty_ns")), _num(params.get("multiplier"))
        wns = ctx.get("winner_wns")
        if u is None and m is None:
            errors.append("%s: give uncertainty_ns or multiplier" % where)
        elif u is not None and not (0 < u <= 1.0):
            errors.append("%s: uncertainty_ns must be in (0, 1]" % where)
        elif u is None and not (0.5 <= m <= 4.0):
            errors.append("%s: multiplier must be in [0.5, 4]" % where)
        elif u is None and (wns is None or wns >= 0):
            errors.append("%s: multiplier needs a negative winner WNS" % where)
        else:
            norm["oc_value"] = round(u if u is not None else abs(wns) * m, 4)
        clocks = params.get("clocks") or ctx.get("default_clocks") or ["*"]
        if not isinstance(clocks, list) or not (1 <= len(clocks) <= 2) or \
                not all(isinstance(c, str) and _CLOCK.match(c) for c in clocks):
            errors.append("%s: clocks must be [src] or [src, dst] clock names" % where)
        else:
            norm["clocks"] = clocks

    elif kind == "incremental":
        known = ("xdc_lines", "path_groups", "physopt_directives", "route_directive",
                 "passes")
        extra = [k for k in params if k not in known]
        if extra:
            errors.append("%s: unknown incremental params %s" % (where, extra))
        xdc = params.get("xdc_lines") or []
        if xdc:
            for e in validate_xdc(xdc, only={"group_path"}):
                errors.append("%s: %s" % (where, e))
        groups = params.get("path_groups") or []
        dirs = params.get("physopt_directives") or []
        route = params.get("route_directive")
        if not (groups or dirs or route):
            errors.append("%s: incremental fix needs path_groups, physopt_directives or "
                          "route_directive" % where)
        if not isinstance(groups, list) or not all(
                isinstance(g, str) and _CLOCK.match(g) for g in groups):
            errors.append("%s: path_groups must be a list of group or clock names" % where)
        if not isinstance(dirs, list) or len(dirs) > 4 or not all(
                isinstance(d, str) and _IDENT.match(d) for d in dirs):
            errors.append("%s: physopt_directives must be up to 4 directive names" % where)
        elif isinstance(dirs, list):
            for d in dirs:
                errors += _directive_errors(where, "physopt_directive", d)
        if route is not None and (not isinstance(route, str) or not _IDENT.match(route)):
            errors.append("%s: route_directive must be a single directive name" % where)
        else:
            errors += _directive_errors(where, "route_directive", route)
        passes = params.get("passes", 2)
        if not isinstance(passes, int) or not 1 <= passes <= MAX_PASSES:
            errors.append("%s: passes must be an integer 1..%d" % (where, MAX_PASSES))
        if not ctx.get("winner_checkpoint"):
            errors.append("%s: the winner has no routed checkpoint to start from" % where)
        norm.update({"xdc_lines": xdc, "path_groups": groups, "physopt_directives": dirs,
                     "route_directive": route, "passes": passes})

    elif kind == "rqs":
        cats = params.get("categories") or []
        if not ctx.get("rqs_file"):
            errors.append("%s: the winner has no QoR suggestions file" % where)
        if not cats or not all(c in RQS_CATEGORIES for c in cats):
            errors.append("%s: categories must be a non-empty subset of %s"
                          % (where, ", ".join(RQS_CATEGORIES)))
        norm["categories"] = cats

    return (None if errors else norm), errors


def validate_plan(plan, ctx):
    """
    Validate a whole plan. Returns {"ok", "errors", "fixes", "dropped"}; fixes are
    normalized, sorted by rank, deduplicated by name, and capped at
    ctx["max_fixes"] (the rest listed in "dropped").
    """
    errors = []
    if not isinstance(plan, dict):
        return {"ok": False, "errors": ["plan must be a JSON object"], "fixes": [],
                "dropped": []}
    if plan.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not isinstance(plan.get("summary"), str) or len(plan["summary"].strip()) < 20:
        errors.append("summary must describe what limits timing (>= 20 chars)")
    evidence = plan.get("evidence") or []
    ids = set()
    for e in evidence:
        if not isinstance(e, dict) or not e.get("id") or not e.get("finding"):
            errors.append("each evidence item needs id and finding")
        else:
            ids.add(e["id"])
    fixes = plan.get("fixes") or []
    if not fixes:
        errors.append("plan has no fixes")
    good, seen = [], set()
    for f in fixes:
        norm, errs = validate_fix(f, ctx, ids)
        errors.extend(errs)
        if norm:
            if norm["name"] in seen:
                errors.append("duplicate fix name %r" % norm["name"])
                continue
            seen.add(norm["name"])
            good.append(norm)
    good.sort(key=lambda f: f["rank"])
    cap = int(ctx.get("max_fixes", 4))
    return {"ok": not errors, "errors": errors, "fixes": good[:cap],
            "dropped": [f["name"] for f in good[cap:]]}
