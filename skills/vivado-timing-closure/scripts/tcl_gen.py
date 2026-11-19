#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
tcl_gen.py -- Generate the Vivado TCL scripts each run executes.

Standalone and stdlib-only: no third-party packages, no network access.

Emits three kinds of script:
  * stage1  -- one of the seven baseline placer strategies, with early fixes
               applied in-session before opt_design
  * detect  -- a short detection-only pass that sources selected skill
               detect.tcl scripts against an opened checkpoint
  * stage2  -- a targeted fix run (RQS, over-constrain, or a skill fix applied
               as a placement subdirective / XDC / TCL)

IMPORTANT -- the generated script derives its own output directory with
    set run_dir [file normalize [file dirname [info script]]]
    cd $run_dir
so **run.tcl must be written into the run directory it should populate**. The
`--run-dir` flag does exactly that; don't relocate the file afterwards.

Every generator emits the same report set at each stage prefix, so
report_parser.py behaves identically across Stage 1 and Stage 2 runs.

Usage:
    tcl_gen.py stage1 --flow explore --dcp <dcp> --run-dir <dir>
    tcl_gen.py detect --dcp <checkpoint> --run-dir <dir> --detect <a.tcl> --detect <b.tcl>
    tcl_gen.py stage2 --kind rqs              --dcp <dcp> --run-dir <dir> --rqs-file <f>
    tcl_gen.py stage2 --kind over_constrain   --dcp <dcp> --run-dir <dir> \
        --oc-value 0.225 --oc-clock clk0 --oc-clock clk0
    tcl_gen.py stage2 --kind category_directive --dcp <dcp> --run-dir <dir> \
        --subdirective "GPlace.ReduceCongestion.high ..."
    tcl_gen.py stage2 --kind xdc_fix   --dcp <dcp> --run-dir <dir> --xdc <fix.xdc>
    tcl_gen.py stage2 --kind tcl_fix   --dcp <dcp> --run-dir <dir> \
        --fix-tcl <fix.tcl> --parent-run-dir <parent>
    tcl_gen.py stage2 --kind stacked   --dcp <dcp> --run-dir <dir> \
        [--stack-xdc <a.xdc>] [--stack-xdc-read <b.xdc>] [--subdirective "..."] \
        [--rqs-file <f.rqs> --rqs-category Timing]
    tcl_gen.py stage2 --kind incremental --dcp <winner>/postroute_physopt.dcp --run-dir <dir> \
        [--stack-xdc <group_path.xdc>] [--path-group g] [--incr-physopt D] [--route-directive R]
    tcl_gen.py stage3 --kind last_mile --dcp <winner>/postroute_physopt.dcp --run-dir <dir>
    tcl_gen.py stage3 --kind reroute   --dcp <winner>/postplace_physopt.dcp --run-dir <dir> \
        [--pre-route-cmd "<winner's pre-route TCL>"]

Every script ends by writing $run_dir/run.done, then `exit`.

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yaml_lite import load_yaml  # noqa: E402

# Skill package root: the parent of scripts/. Everything under skills/ and
# config/ is resolved relative to this, so the package relocates cleanly.
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The Tcl a run.tcl sources (assets/) comes from TCA_ASSETS_ROOT when set: for
# farm runs tc stages a copy on the shared folder (engine/staging.py), because a
# farm host cannot read a skill installed on the submitting host's local disk.
ASSETS_ENV = "TCA_ASSETS_ROOT"


def assets_root():
    return os.environ.get(ASSETS_ENV) or os.path.join(SKILL_ROOT, "assets")


def asset(*parts):
    """A path under the assets tree the generated Tcl should source."""
    return os.path.join(assets_root(), *parts)


ASSETS_ROOT = assets_root()


# ---------------------------------------------------------------------------
# Parameters enabling the automatic placer sub-directive phases used by the
# AUTO1-AUTO5 strategies.
#
# `place.autosubdir.pythonSPPath` points Vivado at a Python site-packages
# directory used by the automatic sub-directive selector. It is site-specific,
# so it is NOT hard-coded here: set `stage1.auto_placer_python_path` in
# config/agent_config.yaml if your Vivado installation requires it. When unset
# the parameter is simply not emitted and Vivado uses its own default.
# ---------------------------------------------------------------------------
AUTO_PARAMS_BASE = """catch { set_param flows.enableExtractMetrics 1 }
catch { set_param flows.enableHierDBExtractMetrics 2 }
set_param place.adv.autosubdir.enabledPhases 7"""


def auto_params(config):
    """Placer AUTO parameters, plus the optional site-specific Python path."""
    lines = [AUTO_PARAMS_BASE]
    py_path = (config.get('stage1', {}) or {}).get('auto_placer_python_path')
    if py_path:
        lines.append('set_param place.autosubdir.pythonSPPath "%s"' % py_path)
    return "\n".join(lines)


def auto_params_enabled(config):
    """
    stage1.auto_placer_params (default true). The upstream framework dropped
    these params on 2026-10-08 (its Auto_n runs set none); our 2026.1 runs
    failed with Constraints 18-12534 without them, so they stay on by default.
    """
    return _as_bool((config.get('stage1', {}) or {}).get('auto_placer_params'), True)


# "The placer predicted 'Floorplan.ExtraTimingOpt.med' subdirective(s) for
# 'Floorplan.Auto_3' subdirective." -- one line per Auto_n token in vivado.log.
_PLACER_PREDICTED_RE = re.compile(
    r"The placer predicted '([^']+)' subdirective\(s\) for '([^']+)' subdirective\.")


def run_logs(run_dir):
    """Vivado logs a run may have written: vivado.log, or the MCP session's."""
    logs = [os.path.join(run_dir, "vivado.log")]
    logs += sorted(glob.glob(os.path.join(run_dir, ".vivado-ai", "*.log")))
    return [p for p in logs if os.path.isfile(p)]


def placer_predicted(run_dir):
    """
    {Auto_n token: concrete subdirective(s)} from a finished run's logs.
    Several predictions for one token are logged comma-separated; place_design
    wants them space-separated (a comma-joined one is "not a recognized
    subdirective"), so they are converted here.
    """
    resolved = {}
    for path in run_logs(run_dir or ""):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if "placer predicted" not in line:
                        continue
                    m = _PLACER_PREDICTED_RE.search(line)
                    if m:
                        resolved[m.group(2)] = m.group(1).replace(',', ' ')
        except OSError:
            continue
        if resolved:
            break
    return resolved


# "INFO: [Place 30-1947] Predicted directive using ML models is: EarlyBlockPlacement"
# -- what place_design -directive Auto_n chose (UG904 "Auto Directives").
_ML_DIRECTIVE_RE = re.compile(r"Predicted directive using ML models is:\s*(\w+)")


def placer_predicted_directive(run_dir):
    """The directive an Auto_n placement in run_dir resolved to, or None."""
    for path in run_logs(run_dir or ""):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    m = _ML_DIRECTIVE_RE.search(line)
                    if m:
                        return m.group(1)
        except OSError:
            continue
    return None


def resolve_auto_subdirective(subdirective, run_dir):
    """
    The concrete subdirective the placer actually used for an Auto_n one in
    run_dir, or None. All or nothing: a half-pinned list is neither a replay of
    that run nor a fresh prediction (upstream's rule).
    """
    if not subdirective or ".Auto_" not in subdirective or not run_dir:
        return None
    resolved = placer_predicted(run_dir)
    tokens = subdirective.strip().strip("{}").split()
    new = [resolved.get(t) for t in tokens]
    if not resolved or any(n is None for n in new):
        return None
    return "{ %s }" % " ".join(new)


def replay_flow(flow_def, config):
    """
    flow_def with its Auto_n placer subdirective replaced by the one the parent
    run (config['_parent_run_dir']) actually used, so a replay places the way
    that run did instead of re-predicting in a different context (upstream
    2026-10-08). Unchanged when there is nothing to resolve.
    """
    place = flow_def.get('place') or {}
    parent = config.get('_parent_run_dir')
    concrete = resolve_auto_subdirective(place.get('subdirective'), parent)
    if concrete:
        new = dict(flow_def)
        new['place'] = {"subdirective": concrete, "resolved_from": place.get('subdirective')}
        return new
    # A parent on a non-Versal part ran the ML-predicted directive instead.
    chosen = placer_predicted_directive(parent) if place.get('ml_directive') else None
    if chosen:
        new = dict(flow_def)
        new['place'] = {"directive": chosen, "resolved_from": place['ml_directive']}
        return new
    return flow_def


# AUTO placer sub-directive variants used by the AUTO1-AUTO5 strategies.
AUTO_SUBDIRECTIVES = [
    "{ Floorplan.Auto_1 GPlace.Auto_3 DPlace.Auto_2 }",
    "{ Floorplan.Auto_1 GPlace.Auto_3 DPlace.Auto_3 }",
    "{ Floorplan.Auto_2 GPlace.Auto_3 DPlace.Auto_2 }",
    "{ Floorplan.Auto_2 GPlace.Auto_3 DPlace.Auto_3 }",
    "{ Floorplan.Auto_3 GPlace.Auto_3 DPlace.Auto_2 }",
]

# Placer sub-directives exist on Versal only: on an UltraScale+ part place_design
# fails with Vivado_Tcl 4-2310 (all five auto_* runs, 10-14). There the same
# strategies run Vivado's ML-predicted directive instead (UG904 "Auto
# Directives": Auto_1..Auto_3, the best three predictions), chosen by each
# variant's Floorplan.Auto_n. So on UltraScale+ auto_2 places as auto_1 and
# auto_4 as auto_3 -- the twins stage1_duplicate_stop_tcl already expects.
AUTO_ML_DIRECTIVES = ["Auto_1", "Auto_1", "Auto_2", "Auto_2", "Auto_3"]
# Strategy -> the one it places identically to where sub-directives are absent.
NON_VERSAL_TWINS = {"auto_2": "auto_1", "auto_4": "auto_3"}


def is_versal(architecture):
    """A part's ARCHITECTURE property ('versal' for Versal and Versal Gen 2)."""
    return str(architecture or "").lower().startswith("versal")


# Part-name prefixes, for when only the part is known (the checkpoint's dcp.xml,
# before any Vivado runs). Versal: xcvc/xcve/xcvm/xcvp/xcvh/xcvn and Gen 2 xc2v*;
# xq/xa are the defense and automotive grades. xcvu (Virtex UltraScale+) is why
# this is not a bare "xcv" prefix.
_VERSAL_PART = re.compile(r"^(xc|xq|xa)(2v|v[cemphn])")
_OTHER_PART = re.compile(r"^(xc|xq|xa)(7|zu|ku|vu|au|su|u\d|k\d)")


def device_family(part):
    """'versal', 'other', or None when the part name does not say."""
    p = str(part or "").strip().lower()
    if _VERSAL_PART.match(p):
        return "versal"
    if _OTHER_PART.match(p):
        return "other"
    return None

_COMMON_TAIL = {
    "postplace_physopt": {"directive": "AggressiveExplore"},
    "route": {"directive": "NoTimingRelaxation", "switches": "-tns_cleanup"},
    "postroute_physopt": {"directive": "AggressiveExplore"},
}


def _flow(place):
    """Build one Stage 1 flow definition: Explore opt + given place + common tail."""
    flow = {"opt": {"directive": "Explore"}, "place": place}
    flow.update(_COMMON_TAIL)
    return flow


# The seven baseline placer strategies swept in Stage 1.
STAGE1_FLOWS = {
    "default_placer": _flow({"directive": None}),
    "explore":        _flow({"directive": "Explore"}),
    "auto_1":         _flow({"subdirective": AUTO_SUBDIRECTIVES[0], "auto_params": True,
                             "ml_directive": AUTO_ML_DIRECTIVES[0]}),
    "auto_2":         _flow({"subdirective": AUTO_SUBDIRECTIVES[1], "auto_params": True,
                             "ml_directive": AUTO_ML_DIRECTIVES[1]}),
    "auto_3":         _flow({"subdirective": AUTO_SUBDIRECTIVES[2], "auto_params": True,
                             "ml_directive": AUTO_ML_DIRECTIVES[2]}),
    "auto_4":         _flow({"subdirective": AUTO_SUBDIRECTIVES[3], "auto_params": True,
                             "ml_directive": AUTO_ML_DIRECTIVES[3]}),
    "auto_5":         _flow({"subdirective": AUTO_SUBDIRECTIVES[4], "auto_params": True,
                             "ml_directive": AUTO_ML_DIRECTIVES[4]}),
    # Congestion-spreading strategies (opt-in via stage1.runs; not in the default
    # sweep). For routability-limited designs whose default placements meet setup
    # but cannot route (VIAVI_100gx8: Route 35-162, 252 signals unrouted). The
    # sub-directive combinations are the congestion skills' own (UG1388: on Versal,
    # spreading is Floorplan/GPlace.ForceSpreading, GPlace.ReduceCongestion,
    # DPlace.ReducePinDensity). off_versal: the nearest place directive (UG835)
    # where sub-directives do not exist.
    "spread_high":      _flow({"subdirective": "{ GPlace.ReduceCongestion.high GPlace.ForceSpreading.high "
                                               "DPlace.ReducePinDensity.high Floorplan.ForceSpreading.high "
                                               "Floorplan.PartitioningClusteringEffort.low }",
                                 "off_versal": "AltSpreadLogic_high"}),
    "spread_balanced":  _flow({"subdirective": "{ Floorplan.BalancedSLR.high Floorplan.ForceSpreading.high "
                                               "Floorplan.PartitioningClusteringEffort.low GPlace.ReduceCongestion.high "
                                               "GPlace.ForceSpreading.high DPlace.ReducePinDensity.high }",
                                 "off_versal": "SSI_SpreadLogic_high"}),
    "reduce_cong_high": _flow({"subdirective": "{ GPlace.ReduceCongestion.high DPlace.ReducePinDensity.high }",
                               "off_versal": "AltSpreadLogic_medium"}),
    # Upstream's third default strategy (b0cfa7a7: default_placer, explore,
    # aggressive_placer replaced auto_1..5). Opt-in here via stage1.runs.
    "aggressive_placer": {
        "opt": {"directive": "Explore"},
        "place": {"directive": "AggressiveExplore"},
        "postplace_physopt": {"directive": "AggressiveExplore"},
        "route": {"directive": "AggressiveExplore", "switches": "-tns_cleanup"},
        "postroute_physopt": {"directive": "AggressiveExplore"},
    },
    # Upstream's plain-Vivado reference (stage1.include_baseline, opt-in here):
    # Explore everywhere, no early fixes, no post-route phys_opt. Never a winner
    # and never a parent -- it only shows what Vivado alone gets on this build.
    "baseline": {
        "opt": {"directive": "Explore"},
        "place": {"directive": "Explore"},
        "postplace_physopt": {"directive": "Explore"},
        "route": {"directive": "Explore"},
        "postroute_physopt": None,
        "baseline_only": True,
    },
}

# Public run IDs for each strategy.
FLOW_RUN_ID = {
    "default_placer": "S1_DEFAULT",
    "explore": "S1_EXPLORE",
    "auto_1": "S1_AUTO1",
    "auto_2": "S1_AUTO2",
    "auto_3": "S1_AUTO3",
    "auto_4": "S1_AUTO4",
    "auto_5": "S1_AUTO5",
    "spread_high": "S1_SPREAD_HIGH",
    "spread_balanced": "S1_SPREAD_BALANCED",
    "reduce_cong_high": "S1_REDUCE_CONG_HIGH",
    "aggressive_placer": "S1_AGGRESSIVE",
    "baseline": "S1_BASELINE",
}


# ===========================================================================
# TCL building blocks
# ===========================================================================
# The run's own folder, absolute, and the working directory (upstream PR #742):
# relative-path output (a skill's ./physopt_run, a generator writing into the
# cwd) then lands in the run folder whatever directory Vivado started in.
# Normalized first, so $run_dir stays right after the cd.
RUN_DIR_TCL = "set run_dir [file normalize [file dirname [info script]]]\ncd $run_dir"


def header(title, run_name):
    return ("# ============================================================\n"
            "# Timing Closure Agent Skill - %s\n"
            "# Run: %s\n"
            "# Generated by scripts/tcl_gen.py\n"
            "# ============================================================\n"
            % (title, run_name))


def report_block(stage_prefix, include_utilization=False,
                 include_clock_interaction=False, include_rqs=False,
                 include_qor_assessment=False, skip_dcp=False,
                 include_check_timing=False, include_cdc=False,
                 include_route_status=False, stage_checks_config=None):
    """
    Standard report dump. Filenames here MUST stay in sync with
    report_parser.py::report_paths().

    check_timing / report_cdc / report_route_status are the upstream framework's
    root-cause reports (2026-10-08); report_cdc is caught because its licensing
    varies.

    stage_checks_config: the run's config when this is a Stage 1 run, which adds
    the per-stage checks (stage_check_lines); None everywhere else.
    """
    lines = ["\n# --- Reports: %s ---" % stage_prefix]
    p = stage_prefix
    lines.append('report_timing -delay_type max -max_paths 100 -sort_by group '
                 '-input_pins -file $run_dir/%s_timing_max.rpt' % p)
    lines.append('report_timing -delay_type min -max_paths 100 -sort_by group '
                 '-input_pins -file $run_dir/%s_timing_min.rpt' % p)
    lines.append('report_timing_summary -file $run_dir/%s_timing_summary.rpt' % p)
    lines.append('report_design_analysis -timing -setup -max_paths 10 -congestion '
                 '-file $run_dir/%s_design_analysis.rpt' % p)

    if include_utilization:
        lines.append('report_utilization -file $run_dir/%s_utilization.rpt' % p)
    if include_clock_interaction:
        lines.append(clock_interaction_line(p))
    if include_qor_assessment:
        lines.append('report_qor_assessment -file $run_dir/%s_qor_assessment.rpt' % p)
    if include_check_timing:
        lines.append('check_timing -file $run_dir/%s_check_timing.rpt' % p)
    if include_cdc:
        lines.append('catch { report_cdc -file $run_dir/%s_cdc.rpt }' % p)
    if include_route_status:
        lines.append('report_route_status -file $run_dir/%s_route_status.rpt' % p)
    if stage_checks_config is not None:
        lines.extend(stage_check_lines(p, stage_checks_config, include_clock_interaction))
    if not skip_dcp:
        lines.append('write_checkpoint -force $run_dir/%s.dcp' % p)
    # QoR suggestions after the checkpoint: on Build 6511674 the RQS commands
    # corrupt the heap and the crash lands in the next big allocation (14 of 14
    # segfaults seen), so nothing important may follow them.
    if include_rqs:
        lines.append(rqs_lines(p))
    return "\n".join(lines)


def clock_interaction_line(p):
    """Caught, so a failure never stops the run; min_max shows hold too (upstream PR #742)."""
    return ('catch { report_clock_interaction -delay_type min_max '
            '-file $run_dir/%s_clock_interaction.rpt }' % p)


# report_methodology rules checked after each Stage 1 step (upstream PR #742);
# the input-design check (methodology_tcl) still runs every rule.
STAGE_METHODOLOGY_CHECKS = ("TIMING-6", "TIMING-7", "TIMING-8", "TIMING-14", "TIMING-24",
                            "TIMING-35")


def stage_check_lines(p, config, has_clock_interaction=False):
    """
    Per-stage <p>_methodology.rpt / .json and <p>_clock_interaction.rpt, in Stage 1
    runs, before the step's checkpoint. Read-only and each caught; -file and -json
    are separate calls because Vivado 2026.1 rejects them together. Switched by
    reports.per_stage_methodology / per_stage_clock_interaction (both default on).
    """
    rc = config.get('reports', {}) or {}
    lines = []
    # postroute: postroute_extra_reports writes postroute_clock_interaction.rpt
    # after post-route phys_opt, over this one, so it would be wasted runtime.
    if _as_bool(rc.get('per_stage_clock_interaction'), True) and not has_clock_interaction \
            and p != "postroute":
        lines.append(clock_interaction_line(p))
    if _as_bool(rc.get('per_stage_methodology'), True):
        checks = "{%s}" % " ".join(STAGE_METHODOLOGY_CHECKS)
        lines.append('catch { report_methodology -checks %s -file $run_dir/%s_methodology.rpt }'
                     % (checks, p))
        lines.append('catch { report_methodology -checks %s -json $run_dir/%s_methodology.json }'
                     % (checks, p))
    return lines


def rqs_lines(p):
    return "\n".join([
        'file mkdir $run_dir/%s_rqs' % p,
        'report_qor_suggestions -file $run_dir/%s_rqs.rpt' % p,
        'write_qor_suggestions -tcl_output_dir $run_dir/%s_rqs' % p,
        'write_qor_suggestions -force $run_dir/%s_rqs/%s -quiet' % (p, p),
    ])


def intermediate_rqs(config):
    """
    RQS at postopt / postplace / postplace_physopt / postroute: off unless
    output.intermediate_rqs. Nothing reads them (report_parser uses the final
    postroute_physopt one) and each is another chance of the RQS crash.
    """
    return _as_bool((config.get('output', {}) or {}).get('intermediate_rqs'), False)


def intermediate_dcps(config):
    """
    postopt.dcp / postplace.dcp, and postroute.dcp once postroute_physopt.dcp is
    written: off unless output.intermediate_dcps. Nothing reads the first two
    (detect passes, Path B and Stage 3 open postplace_physopt / the routed one),
    and postroute.dcp is only the fallback when post-route phys_opt never
    finished. ~1 GB each on design A, five per run.
    """
    return _as_bool((config.get('output', {}) or {}).get('intermediate_dcps'), False)


def drop_superseded_postroute_dcp():
    """postroute_physopt.dcp is written: the routed fallback is not needed."""
    return "\n".join([
        "# postroute_physopt.dcp supersedes postroute.dcp (output.intermediate_dcps off)",
        "if {[file exists [file join $run_dir postroute_physopt.dcp]]} {",
        "    catch { file delete -force [file join $run_dir postroute.dcp] }",
        "}",
    ])


def final_reports(config, stage1_checks=False):
    """
    postroute_physopt reports + checkpoint + extra reports, then the one RQS the
    flow uses, last: a crash there leaves every result already on disk
    (run-status then still counts the run as done).
    """
    skip_dcp = (config.get('output', {}) or {}).get('skip_dcp', False)
    return "\n".join([
        report_block("postroute_physopt", include_utilization=True,
                     include_qor_assessment=True, skip_dcp=skip_dcp,
                     stage_checks_config=config if stage1_checks else None),
        drop_superseded_postroute_dcp() if not (skip_dcp or intermediate_dcps(config)) else "",
        postroute_extra_reports(),
        "",
        "# --- Final QoR suggestions (last: every result above is already written) ---",
        rqs_lines("postroute_physopt"),
    ])


def opt_cmd(flow_def):
    directive = (flow_def.get('opt') or {}).get('directive')
    return "opt_design -directive %s" % directive if directive else "opt_design"


def place_cmd(flow_def, config=None):
    """place_design, optionally preceded by the AUTO placer parameters."""
    place = flow_def.get('place') or {}
    lines = []

    elsewhere = place.get('ml_directive') or place.get('off_versal')
    if place.get('subdirective') and elsewhere:
        # The run decides, from the part it opened: a strategy also runs in a
        # replay (Stage 2, planner) whose script is written without knowing
        # the part, and when the build probe is off.
        versal = place_cmd(dict(flow_def, place=dict(place, ml_directive=None,
                                                     off_versal=None)), config)
        return "\n".join(
            ["# Placer sub-directives exist on Versal only; elsewhere %s." % (
                "the ML-predicted directive (UG904 Auto Directives)"
                if place.get('ml_directive') else elsewhere),
             "if {[string match -nocase versal* [get_property ARCHITECTURE "
             "[get_parts [get_property PART [current_design]]]]]} {"]
            + [("    " + ln) if ln else "" for ln in versal.splitlines()]
            + ["} else {",
               "    place_design -directive %s" % elsewhere,
               "}"])

    if place.get('auto_params') and auto_params_enabled(config or {}):
        lines.append("# --- AUTO placer params ---")
        lines.append(auto_params(config or {}))
        lines.append("")
    if place.get('resolved_from'):
        lines.append("# Replays the parent run's placer prediction for %s"
                     % place['resolved_from'])

    if place.get('subdirective'):
        sd = place['subdirective'].strip()
        if not sd.startswith('{'):
            sd = '{ ' + sd + ' }'
        lines.append("place_design -subdirective %s" % sd)
    elif place.get('directive'):
        lines.append("place_design -directive %s" % place['directive'])
    else:
        lines.append("place_design")

    return "\n".join(lines)


def physopt_cmd(flow_def, stage):
    directive = (flow_def.get(stage) or {}).get('directive')
    return "phys_opt_design -directive %s" % directive if directive else "phys_opt_design"


def flow_settings(config):
    """
    The `flow:` section: upstream's 2026-10-25 implementation choices, each a
    switch that defaults to this skill's own flow.
    """
    return (config or {}).get('flow') or {}


def route_directive(config, default):
    """
    The route_design directive: a stage's route_directive_override, then
    flow.route_directive (upstream: AggressiveExplore everywhere), then default.
    """
    return ((config or {}).get('_route_directive_override')
            or flow_settings(config).get('route_directive') or default)


def route_line(config, default="NoTimingRelaxation", switches="-tns_cleanup"):
    """A fixed route_design line of a Stage 2 variant, honouring the overrides."""
    return " ".join(x for x in ("route_design", "-directive %s" % route_directive(config, default),
                                switches) if x)


def route_cmd(flow_def, config=None):
    """route_design for this flow; <stage>.route_directive_override wins (upstream)."""
    route = flow_def.get('route') or {}
    parts = ["route_design"]
    directive = route_directive(config, route.get('directive'))
    if directive:
        parts.append("-directive %s" % directive)
    if route.get('switches'):
        parts.append(route['switches'])
    return " ".join(parts)


TNS_PROC = """proc _tca_tns {} {
    # Design TNS from the timing summary line (WNS TNS ...); "" if unavailable.
    if {[catch {report_timing_summary -no_detailed_paths -no_header -return_string} txt]} { return "" }
    set lines [split $txt "\\n"]
    for {set i 0} {$i < [llength $lines]} {incr i} {
        set l [lindex $lines $i]
        if {[string first "WNS(ns)" $l] >= 0 && [string first "TNS(ns)" $l] >= 0 &&
                [string first "Clock" $l] < 0} {
            return [lindex [string trim [lindex $lines [expr {$i + 2}]]] 1]
        }
    }
    return ""
}"""


POSTPLACE_PHYSOPT_SKILL = asset("skills", "early_resolution", "adaptive_physopt",
                                "adaptive_physopt.tcl")


def postplace_physopt_skill_tcl(config, label=""):
    """
    flow.postplace_physopt: skill -- upstream's shared post-place step
    (adaptive_physopt.tcl): congestion-gated; on a met design it over-constrains
    and runs up to PHYSOPT_MAX_ITER AggressiveExplore passes, stripping its own
    uncertainty before the final re-time. With flow.reset_preplace_oc, a
    pre-place over-constraint (oc_reset.tcl, written by over_constrain_tcl) is
    undone first so the skill starts from the real constraints.
    """
    fs = flow_settings(config)
    lines = ["# --- Shared post-place OC + multi-physopt skill (flow.postplace_physopt: skill) ---"]
    if _as_bool(fs.get('reset_preplace_oc'), False):
        lines += ["if {[file exists [file join $run_dir %s]]} {" % OC_RESET,
                  "    puts \"INFO: resetting the pre-place over-constraint before the shared post-place skill\"",
                  "    source [file join $run_dir %s]" % OC_RESET,
                  "}"]
    lines += ["set env(PHYSOPT_MAX_ITER) %d" % int(fs.get('postplace_physopt_max_iter', 6) or 6),
              "set env(PHYSOPT_OUT_DIR) [file join $run_dir physopt_postplace%s]" % label,
              "if {[catch {source {%s}} _pp_err]} {" % POSTPLACE_PHYSOPT_SKILL,
              '    puts "ERROR: shared post-place physopt failed: $_pp_err"',
              "    error \"shared post-place physopt failed: $_pp_err\"",
              "}"]
    return "\n".join(lines)


def adaptive_physopt_tcl(physopt_command, label, max_iterations=4, tns_aware_pct=None):
    """
    phys_opt_design loop that stops early on timing met or on <1ps gain.

    The gain is measured against the WNS *before* the loop, so a first pass that
    changes nothing stops at once (seeding with -999 used to force a second,
    identical pass). _phys_done counts the passes actually run -- the for
    counter is one past the limit when the loop runs out.

    tns_aware_pct: also keep going while a pass cuts TNS by at least this
    percentage even though WNS did not move (design B round 2: path-group passes
    cut TNS -13.6 -> -4.8 at a fixed WNS, and the WNS-only rule stopped them).
    """
    lines = []
    if tns_aware_pct:
        lines += [TNS_PROC, "set _prev_tns [_tca_tns]"]
    lines += [
        "# --- Adaptive phys_opt_design (%s, max %d iterations) ---" % (
            label, max_iterations),
        "set _prev_wns [get_property SLACK [get_timing_paths -max_paths 1 -delay_type max]]",
        'if {$_prev_wns eq ""} { set _prev_wns -999.0 }',
        "set _curr_wns $_prev_wns",
        "set _phys_done 0",
        "for {set _phys_iter 1} {$_phys_iter <= %d} {incr _phys_iter} {" % max_iterations,
        "    set _phys_done $_phys_iter",
        '    puts "INFO: PhysOpt iteration $_phys_iter/%d (%s)"' % (max_iterations, label),
        "    %s" % physopt_command,
        "    set _curr_wns [get_property SLACK [get_timing_paths -max_paths 1 "
        "-delay_type max]]",
        '    puts "INFO: PhysOpt iteration $_phys_iter WNS = $_curr_wns ns"',
        "",
        "    # Stop if timing met",
        "    if {$_curr_wns >= 0} {",
        '        puts "INFO: Timing met after PhysOpt iteration $_phys_iter. Stopping."',
        "        break",
        "    }",
        "",
        "    # Stop if no WNS improvement (< 1ps gain over the previous pass / the input)",
        "    set _gain [expr {$_curr_wns - $_prev_wns}]",
        "    if {$_gain < 0.001} {",
    ]
    if tns_aware_pct:
        lines += [
            "        set _curr_tns [_tca_tns]",
            '        if {$_curr_tns ne "" && $_prev_tns ne "" && $_prev_tns < 0 &&',
            "                ($_curr_tns - $_prev_tns) >= %s * abs($_prev_tns)} {"
            % (float(tns_aware_pct) / 100.0),
            '            puts "INFO: PhysOpt iteration $_phys_iter: no WNS gain but TNS '
            '$_prev_tns -> $_curr_tns; continuing."',
            "            set _prev_tns $_curr_tns",
            "            set _prev_wns $_curr_wns",
            "            continue",
            "        }",
        ]
    lines += [
        '        puts "INFO: No WNS gain after PhysOpt iteration $_phys_iter '
        '(gain=${_gain}ns). Stopping."',
        "        break",
        "    }",
        "",
        "    set _prev_wns $_curr_wns",
        "}",
        'puts "INFO: Adaptive PhysOpt (%s) completed after $_phys_done iteration(s). '
        'Final WNS = $_curr_wns ns"' % label,
    ]
    return "\n".join(lines)


# Where the only QoR report the flow writes (the final one) stamps its suggestions.
RQS_FINAL_GENERATED_AT = "postroute_phys_opt"
RQS_EXCLUDED_ID = "RQS_TIMING-100"


def apply_rqs_block(rqs_file, only_categories=None, require_enabled=False):
    """
    Read a .rqs file, disable every AUTO suggestion, then re-enable only those
    matching the per-category filters below. This keeps the applied suggestion
    set predictable instead of accepting whatever Vivado enables by default.

    only_categories restricts the re-enabled set (e.g. ["Timing"] for a
    category's stacked skills+RQS run).

    The filters pick a suggestion by the step it was generated at (opt_design /
    place_design ...). With the intermediate QoR reports off, every suggestion in
    the final report is stamped postroute_phys_opt, so those filters matched
    nothing and every RQS run replayed its parent (4 runs, 2026-09-28). A category
    with no match at its usual step therefore falls back to its
    postroute_phys_opt suggestions.

    require_enabled: stop the run (run.failed) when nothing was enabled -- a
    pure RQS run would only replay its parent flow.
    """
    applicable_for = ('(applicable_for == "opt_design" || '
                      'applicable_for == "place_design" || '
                      'applicable_for == "route_design")')
    categories = [
        ("Utilization",
         'Auto == 1 && %s && generated_at == "opt_design" && '
         'CATEGORY == "Utilization"' % applicable_for),
        ("Clocking",
         'Auto == 1 && %s && (generated_at == "opt_design" || '
         'generated_at == "place_design") && CATEGORY == "Clocking"' % applicable_for),
        ("Congestion",
         'Auto == 1 && %s && (regenerated_at == "route_design" || '
         'generated_at == "route_design") && CATEGORY == "Congestion"' % applicable_for),
        ("Timing",
         'Auto == 1 && %s && generated_at == "place_design" && '
         'CATEGORY == "Timing"' % applicable_for),
        ("Netlist",
         'Auto == 1 && %s && (generated_at == "opt_design" || '
         'generated_at == "place_design") && CATEGORY == "Netlist"' % applicable_for),
    ]

    if only_categories:
        wanted = set(c.lower() for c in only_categories)
        categories = [c for c in categories if c[0].lower() in wanted]

    lines = ["\n# --- Apply RQS suggestions (category-based%s) ---" % (
                 ": " + ", ".join(c[0] for c in categories) if only_categories else ""),
             'read_qor_suggestions {%s}' % rqs_file,
             'foreach _rqs_sugg [get_qor_suggestions -filter {AUTO}] {',
             '    set_property enabled false $_rqs_sugg',
             '}',
             'set _rqs_enabled_count 0']

    def enable_loop(filt, label, suffix=""):
        return [
            'foreach _rqs_sugg [get_qor_suggestions -filter {%s}] {' % filt,
            '    set _rqs_id [get_property ID $_rqs_sugg]',
            '    if {$_rqs_id eq "%s"} { continue }' % RQS_EXCLUDED_ID,
            '    puts "INFO: Enabling %s suggestion $_rqs_id%s"' % (label, suffix),
            '    set_property enabled true $_rqs_sugg',
            '    incr _rqs_enabled_count',
            '    incr _rqs_cat_count',
            '}',
        ]

    for label, filt in categories:
        final = ('Auto == 1 && %s && generated_at == "%s" && CATEGORY == "%s"'
                 % (applicable_for, RQS_FINAL_GENERATED_AT, label))
        lines.extend(["", '# %s suggestions' % label, 'set _rqs_cat_count 0'])
        lines.extend(enable_loop(filt, label))
        lines.append('if {$_rqs_cat_count == 0} {')
        lines.extend("    " + l for l in enable_loop(
            final, label, " (generated at %s)" % RQS_FINAL_GENERATED_AT))
        lines.append('}')

    lines.extend([
        "",
        'if {$_rqs_enabled_count == 0} {',
        '    puts "WARNING: No RQS suggestions matched any category filter; '
        'proceeding without suggestion-driven constraints."',
        '}',
        "",
        '# Excluded suggestions',
        'foreach _rqs_sugg [get_qor_suggestions -filter {ID == "%s"}] {' % RQS_EXCLUDED_ID,
        '    set_property enabled false $_rqs_sugg',
        '    puts "INFO: Disabled excluded suggestion %s"' % RQS_EXCLUDED_ID,
        '}',
    ])
    if require_enabled:
        # After the block's closing line, so dedup.split_rqs_block still finds it.
        lines.extend([
            'if {$_rqs_enabled_count == 0} {',
            '    error "no QoR suggestion was enabled from %s: this RQS run would only '
            'replay its parent flow"' % os.path.basename(rqs_file),
            '}',
        ])
    return "\n".join(lines)


def early_fixes_block(config):
    """
    Source assets/skills/early_flow_fix/early_flow_fix.tcl with the EF_* env flags set.

    Stage 1 early fixes: MBUFG grouping,
    GT_MRMAC LOC and PCIE clock root (plus HARDSYNC CE and the structural_analysis
    bundle) all detect-and-apply in-session, before opt_design. It writes
    $run_dir/early_fixes.json recording which fixes actually fired.
    """
    ef = config.get('early_fixes', {}) or {}
    script = asset("skills", "early_flow_fix", "early_flow_fix.tcl")

    if not ef.get('enabled', False):
        return "# --- Early Flow Fixes: DISABLED (early_fixes.enabled=false) ---\n"

    if not os.path.isfile(script):
        return ("# --- Early Flow Fixes: SKIPPED (script not found at %s) ---\n"
                % script)

    lines = ["# --- Early Flow Fixes (enabled) ---"]
    for key in ('mbufg_group', 'hardsync_ce', 'gt_mrmac_loc', 'pcie_clock_root',
                'structural_analysis'):
        lines.append('set env(EF_%s) %s' % (key.upper(), "1" if ef.get(key, True) else "0"))
    # Upstream's hard-IP locality skills (b0cfa7a7): opt-in, off unless set.
    for key in ('hardip_proximity_pblock', 'prevent_hardip_slr_crossing'):
        lines.append('set env(EF_%s) %s' % (key.upper(), "1" if ef.get(key, False) else "0"))
    lines.append('set env(RUN_DIR) $run_dir')
    lines.append('if {[catch {source {%s}} _ef_err]} {' % script)
    lines.append('    puts "WARNING: Early flow fixes failed: $_ef_err"')
    lines.append('}')
    lines.append("")
    return "\n".join(lines)


def bram_loc_dump():
    """
    Dump placed BRAM LOCs to Block_Ram_locs.xdc. Opt-in (output.bram_loc_dump):
    nothing in the flow reads the file, and on 2026.1 released all 5 segfaults
    seen (design D, design B Stage 1, design B planner r2) happened right here, just after
    write_checkpoint postplace_physopt.dcp -- a crash no Tcl catch can stop.
    """
    return "\n".join([
        "# --- Dump BRAM LOC constraints ---",
        'set bram_cells [get_cells -hierarchical -filter { PRIMITIVE_TYPE =~ BLOCKRAM.*.* }]',
        'set fp [open "$run_dir/Block_Ram_locs.xdc" w]',
        'foreach cell $bram_cells {',
        '    set site [get_property SITE [get_cells $cell]]',
        '    if {$site ne ""} {',
        '        puts $fp "set_property LOC $site \\[get_cells \\{$cell\\}\\]"',
        '    }',
        '}',
        'close $fp',
        'puts "INFO: Dumped [llength $bram_cells] BRAM LOCs to Block_Ram_locs.xdc"',
    ])


def postroute_extra_reports():
    """The fixed-name reports emitted after post-route phys_opt."""
    return "\n".join([
        'write_xdc -exclude_physical $run_dir/Timing.xdc',
        'write_xdc -exclude_timing $run_dir/Physical.xdc',
        'report_qor_assessment -file $run_dir/postroute_Qor_Assessment.txt',
        'report_clock_interaction -significant_digits 3 '
        '-file $run_dir/postroute_clock_interaction.rpt',
        'report_clock_utilization -file $run_dir/postroute_clock_util.rpt',
        'report_high_fanout_nets -file $run_dir/postroute_high_fanout_nets.rpt',
        'report_control_sets -file $run_dir/postroute_control_sets.rpt',
    ])


def preamble(title, run_name, dcp_path, config, extra_cmds=None):
    """Header + run_dir + open_checkpoint, shared by every generator."""
    lines = [header(title, run_name),
             RUN_DIR_TCL,
             ""]
    lines.append('open_checkpoint {%s}' % dcp_path)
    lines.append('catch {delete_qor_suggestions -q [get_qor_suggestions -q]}')
    if (config.get('early_fixes', {}) or {}).get('enabled', False):
        lines.append('set_param physynth.readIPhysOptRebuildShapes 1')
    lines.append("")
    if extra_cmds:
        lines.append("# --- User extra commands ---")
        lines.extend(extra_cmds)
        lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Implementation-flow body, shared by Stage 1 and every Stage 2 variant
# ===========================================================================
# Likely twins: auto_2 == auto_1 and auto_4 == auto_3 after placement on every design
# measured (design D, design A, design B, the test design); auto_5 matched
# default_placer on design A and auto_1 on the test design. A later strategy waits
# for these to finish placement before comparing; every other earlier strategy is
# still compared, just without waiting.
STAGE1_DUPLICATE_CANDIDATES = {"auto_2": ["auto_1"], "auto_4": ["auto_3"],
                               "auto_5": ["default_placer", "auto_1"]}
PLACE_SIGNATURE = "place_signature.txt"
DUPLICATE_MARKER = "duplicate_of.json"
PLACE_SIGNATURE_SAMPLE = 20000
FAILED_MARKER_NAME = "run.failed"


def _as_bool(val, default):
    if val is None:
        return default
    return val is True or str(val).lower() in ("true", "yes", "1")


def stage1_duplicate_stop_tcl(flow_name, config, run_name):
    """
    Right after place_design: record this placement's signature and stop the run
    if an earlier Stage 1 strategy produced the identical placement -- Vivado is
    deterministic, so opt/route/phys_opt would only replay that run (about 3 of 7
    Stage 1 runs on the TSR designs). The signature is the post-place timing
    summary line plus the LOCs of a fixed stride sample of the netlist's cells.
    """
    s1 = config.get('stage1', {}) or {}
    if not _as_bool(s1.get('stop_duplicate_placements'), True):
        return ""
    order = list(STAGE1_FLOWS)
    earlier = order[:order.index(flow_name)] if flow_name in order else []
    wait_for = set(STAGE1_DUPLICATE_CANDIDATES.get(flow_name, []))
    wait_s = int(float(s1.get('duplicate_wait_minutes', 30) or 0) * 60)
    sibs = " ".join("%s %d" % (f, 1 if f in wait_for else 0) for f in earlier)
    return "\n".join([
        "",
        "# --- Stage 1 duplicate stop: is this placement identical to an earlier strategy? ---",
        "proc _tca_place_signature {} {",
        '    set sig ""',
        "    catch {",
        "        set lines [split [report_timing_summary -no_detailed_paths -no_header -return_string] \"\\n\"]",
        "        for {set i 0} {$i < [llength $lines]} {incr i} {",
        "            set l [lindex $lines $i]",
        '            if {[string first "WNS(ns)" $l] >= 0 && [string first "WHS(ns)" $l] >= 0 &&',
        '                    [string first "Clock" $l] < 0} {',
        '                set sig [regsub -all {\\s+} [string trim [lindex $lines [expr {$i + 2}]]] " "]',
        "                break",
        "            }",
        "        }",
        "    }",
        '    if {$sig eq ""} { return "" }',
        "    set cells [get_cells -quiet -hierarchical -filter {IS_PRIMITIVE}]",
        "    set n [llength $cells]",
        "    set step [expr {$n > %d ? $n / %d : 1}]" % (PLACE_SIGNATURE_SAMPLE,
                                                      PLACE_SIGNATURE_SAMPLE),
        "    set sample {}",
        "    for {set i 0} {$i < $n} {incr i $step} { lappend sample [lindex $cells $i] }",
        '    set locs ""',
        "    if {[llength $sample]} { set locs [join [get_property LOC $sample] \" \"] }",
        '    return "$sig\\n$n\\n$locs"',
        "}",
        "proc _tca_twin_dead {d} {",
        "    # Crashed (hs_err / hs_mangled_err), failed (run.failed), or silent for 10 min.",
        "    if {[llength [glob -nocomplain -directory $d hs_*err_pid*.log]]} { return 1 }",
        "    if {[file exists [file join $d %s]]} { return 1 }" % FAILED_MARKER_NAME,
        "    set newest 0",
        "    foreach f [concat [glob -nocomplain -directory $d *] "
        "[glob -nocomplain -directory [file join $d .vivado-ai] *]] {",
        "        catch { set m [file mtime $f]; if {$m > $newest} { set newest $m } }",
        "    }",
        "    return [expr {$newest > 0 && [clock seconds] - $newest > 600}]",
        "}",
        "proc _tca_twin_lost {d} {",
        "    # Crashed or failed before writing a routed checkpoint: stopping as its",
        "    # duplicate would lose this placement (design C auto_2/auto_5 after auto_1 crashed).",
        "    if {![llength [glob -nocomplain -directory $d hs_*err_pid*.log]] &&",
        "            ![file exists [file join $d %s]]} { return 0 }" % FAILED_MARKER_NAME,
        "    foreach c {postroute.dcp postroute_physopt.dcp} {",
        "        if {[file exists [file join $d $c]]} { return 0 }",
        "    }",
        "    return 1",
        "}",
        "set _tca_sig [_tca_place_signature]",
        'if {$_tca_sig ne ""} {',
        "    set _tca_tmp [file join $run_dir %s.tmp]" % PLACE_SIGNATURE,
        "    set _f [open $_tca_tmp w]; puts -nonewline $_f $_tca_sig; close $_f",
        "    file rename -force $_tca_tmp [file join $run_dir %s]" % PLACE_SIGNATURE,
        '    set _tca_dup ""',
        "    set _tca_deadline [expr {[clock seconds] + %d}]" % wait_s,
        "    foreach {_tca_sib _tca_wait} {%s} {" % sibs,
        "        set _sd [file join [file dirname $run_dir] $_tca_sib]",
        "        set _sf [file join $_sd %s]" % PLACE_SIGNATURE,
        "        # Wait only for a likely twin that has started but not yet placed.",
        "        while {$_tca_wait && ![file exists $_sf] && ![file exists [file join $_sd %s]] &&"
        % SENTINEL,
        "                [llength [glob -nocomplain -directory $_sd *]] > 1 &&",
        "                ![_tca_twin_dead $_sd] &&",
        "                [clock seconds] < $_tca_deadline} {",
        '            puts "INFO: waiting for $_tca_sib to finish placement (possible duplicate)"',
        "            after 60000",
        "        }",
        "        if {![file exists $_sf]} { continue }",
        "        if {[_tca_twin_lost $_sd]} {",
        '            puts "INFO: $_tca_sib failed before routing -- not stopping as its duplicate"',
        "            continue",
        "        }",
        "        set _f [open $_sf r]; set _other [read $_f]; close $_f",
        "        if {$_other ne $_tca_sig} { continue }",
        "        set _tca_dup $_tca_sib",
        "        # Resolve to the original if that sibling was itself a duplicate.",
        "        set _df [file join $_sd %s]" % DUPLICATE_MARKER,
        "        if {[file exists $_df]} {",
        "            set _f [open $_df r]; set _j [read $_f]; close $_f",
        '            regexp {"duplicate_of":"([^"]+)"} $_j -> _tca_dup',
        "            if {[_tca_twin_lost [file join [file dirname $run_dir] $_tca_dup]]} {",
        '                puts "INFO: $_tca_dup failed before routing -- not stopping as its duplicate"',
        '                set _tca_dup ""',
        "                continue",
        "            }",
        "        }",
        "        break",
        "    }",
        '    if {$_tca_dup ne ""} {',
        '        puts "INFO: placement identical to $_tca_dup -- stopping %s here (the rest '
        'would replay $_tca_dup)."' % run_name,
        "        set _f [open [file join $run_dir %s] w]" % DUPLICATE_MARKER,
        '        puts $_f "{\\"duplicate_of\\":\\"$_tca_dup\\",\\"stage\\":\\"postplace\\"}"; '
        'close $_f',
        "        set _rd [open [file join $run_dir %s] w]" % SENTINEL,
        '        puts $_rd "{\\"status\\":\\"duplicate\\",\\"run\\":\\"%s\\",'
        '\\"duplicate_of\\":\\"$_tca_dup\\"}"; close $_rd' % run_name,
        "        exit",
        "    }",
        "}",
    ])


# User TCL hooks (init --post-*-cmd): the upstream framework's hook points, each
# right after its implementation step, in every run that has that step.
HOOK_HEADERS = {
    "_post_opt_cmds": "Post-opt commands",
    "_post_place_cmds": "Post-place commands",
    "_post_popt_cmds": "Post-PhysOpt (post-place) commands",
    "_post_route_cmds": "Post-route commands",
    "_post_route_popt_cmds": "Post-PhysOpt (post-route) commands",
}


def hook_lines(config, key):
    cmds = (config or {}).get(key) or []
    if not cmds:
        return []
    return ["", "# --- %s ---" % HOOK_HEADERS[key]] + list(cmds)


def route_status_enabled(config):
    return _as_bool((config.get('output', {}) or {}).get('route_status_report'), True)


METHODOLOGY_RPT = "methodology.rpt"
METHODOLOGY_JSON = "methodology.json"


def methodology_tcl():
    """
    report_methodology on the input design, before any implementation step
    (upstream: inside its baseline run). Two calls, each caught: Vivado 2026.1
    rejects -file together with -json, and neither may stop the run.
    """
    return "\n".join([
        "# --- Methodology check (design-level, one run per flow) ---",
        "catch { report_methodology -file $run_dir/%s }" % METHODOLOGY_RPT,
        "catch { report_methodology -json $run_dir/%s }" % METHODOLOGY_JSON,
        ""])


def flow_body(flow_def, config, pre_opt_lines=None, pre_route_lines=None,
              adaptive=True, label="", opt_override=None, place_override=None,
              route_override=None, after_place_lines=None, stage1_checks=False):
    """
    opt -> reports -> place -> reports -> physopt -> reports -> BRAM dump ->
    route -> reports -> physopt -> reports.

    pre_opt_lines / pre_route_lines let a Stage 2 variant inject its one
    enhancement (over-constrain, an XDC source, a fix.tcl source) without
    duplicating the surrounding flow.

    stage1_checks: a Stage 1 run, which gets the per-stage checks after every step.
    """
    skip_dcp = (config.get('output', {}) or {}).get('skip_dcp', False)
    max_iter = ((config.get('stage2', {}) or {}).get('multi_physopt', {}) or {}).get(
        'max_iterations', 4)
    # check_timing / report_cdc describe the design, not the flow: only the run
    # that hosts the design checks (init --design-checks) writes them.
    design_checks = bool(config.get('_design_checks'))
    route_status = route_status_enabled(config)
    sc = config if stage1_checks else None

    lines = []

    if pre_opt_lines:
        lines.extend(pre_opt_lines)
        lines.append("")

    # --- opt ---
    lines.append("# --- opt_design ---")
    lines.append(opt_override if opt_override is not None else opt_cmd(flow_def))
    lines.extend(hook_lines(config, "_post_opt_cmds"))
    keep_mid = intermediate_dcps(config)
    lines.append(report_block("postopt", include_rqs=intermediate_rqs(config),
                              skip_dcp=skip_dcp or not keep_mid,
                              include_check_timing=design_checks,
                              include_cdc=design_checks, stage_checks_config=sc))
    lines.append('report_high_fanout_nets -file $run_dir/postlogicopt_high_fanout_nets.rpt')
    lines.append('report_control_sets -file $run_dir/postlogicopt_control_sets.rpt')
    lines.append("")

    # --- place ---
    lines.append("# --- place_design ---")
    lines.append(place_override if place_override is not None
                 else place_cmd(flow_def, config))
    if after_place_lines:
        lines.append(after_place_lines)
    lines.extend(hook_lines(config, "_post_place_cmds"))
    lines.append(report_block("postplace", include_utilization=True,
                              include_clock_interaction=True,
                              include_rqs=intermediate_rqs(config),
                              skip_dcp=skip_dcp or not keep_mid, stage_checks_config=sc))
    lines.append("")

    # --- post-place phys_opt ---
    fs = flow_settings(config)
    if str(fs.get('postplace_physopt') or 'flow').lower() == 'skill' and \
            not flow_def.get('baseline_only'):
        lines.append(postplace_physopt_skill_tcl(config, label))
    elif adaptive:
        lines.append(adaptive_physopt_tcl(
            physopt_cmd(flow_def, "postplace_physopt"),
            "postplace_physopt%s" % label, max_iter))
    else:
        lines.append("# --- phys_opt_design (post-place) ---")
        lines.append(physopt_cmd(flow_def, "postplace_physopt"))
    lines.extend(hook_lines(config, "_post_popt_cmds"))
    lines.append(report_block("postplace_physopt", include_utilization=True,
                              include_rqs=intermediate_rqs(config), skip_dcp=skip_dcp,
                              stage_checks_config=sc))
    # Placed result complete (reports + checkpoint): Path B reads them while
    # this run is still routing.
    lines.append("close [open [file join $run_dir %s] w]" % POSTPLACE_MARKER)
    lines.append("")

    if _as_bool((config.get('output', {}) or {}).get('bram_loc_dump'), False):
        lines.append(bram_loc_dump())
    lines.append("")

    if pre_route_lines:
        lines.extend(pre_route_lines)
        lines.append("")

    # --- route ---
    lines.append("# --- route_design ---")
    lines.append(route_override if route_override is not None
                 else route_cmd(flow_def, config))
    lines.extend(hook_lines(config, "_post_route_cmds"))
    lines.append(report_block("postroute", include_utilization=True,
                              include_rqs=intermediate_rqs(config), skip_dcp=skip_dcp,
                              include_route_status=route_status, stage_checks_config=sc))
    lines.append("")

    # --- post-route phys_opt ---
    if flow_def.get('postroute_physopt') is not None:
        if str(fs.get('postroute_physopt') or 'flow').lower() == 'single':
            # flow.postroute_physopt: single -- upstream's one AggressiveExplore pass.
            lines.append("# --- phys_opt_design (post-route, single pass) ---")
            lines.append("phys_opt_design -directive AggressiveExplore")
        elif adaptive:
            lines.append(adaptive_physopt_tcl(
                physopt_cmd(flow_def, "postroute_physopt"),
                "postroute_physopt%s" % label, max_iter))
        else:
            lines.append("# --- phys_opt_design (post-route) ---")
            lines.append(physopt_cmd(flow_def, "postroute_physopt"))
        lines.extend(hook_lines(config, "_post_route_popt_cmds"))
        lines.append(final_reports(config, stage1_checks))
        lines.append("")
    elif flow_def.get('baseline_only'):
        # The baseline has no post-route phys_opt; its routed result is written
        # under the postroute_physopt names so every parser reads it unchanged.
        lines.append("# --- Final reports (baseline: no post-route phys_opt) ---")
        lines.append(final_reports(config, stage1_checks))
        lines.append("")

    return "\n".join(lines)


SENTINEL = "run.done"


def sentinel_tcl(run_name):
    """Completion marker written only after every report and checkpoint exists."""
    return ('set _rd [open [file join $run_dir %s] w]; '
            'puts $_rd {{"status":"done","run":"%s"}}; close $_rd'
            % (SENTINEL, run_name))


FAILED_MARKER = "run.failed"


KEEP_SESSION_ENV = "TC_KEEP_SESSION"


def _keep_session():
    """
    Whether a *successful* run returns to the Tcl prompt instead of exiting.

    Off by default: `exit` is what releases the LSF slot the moment the work is
    done. The engine sets this when it intends to keep the best finished run's
    design loaded, so the planner can query it without re-opening a checkpoint
    (minutes on a large design). The engine then releases every session it is
    not holding -- see engine/champion.py.

    This changes nothing under `vivado -mode batch -source run.tcl`, which
    exits at the end of the script either way; only a session-mode run stays up.

    The failure path is never affected. A run that errors always exits 1,
    because an idle Vivado holding ~18 GB for hours is the exact incident the
    surrounding guard exists to prevent.
    """
    return os.environ.get(KEEP_SESSION_ENV, "") == "1"


def _finish(lines, message, run_name):
    """
    Wrap the whole run in one catch: an uncaught Tcl error (e.g. route_design
    ERROR) used to stop `source run.tcl` with Vivado left idle, holding its LSF
    slot for hours. Now it writes run.failed (with the error) and exits 1.
    """
    body = "\n".join(lines + ['puts "INFO: %s"' % message, sentinel_tcl(run_name)])
    return "\n".join([
        "if {[catch {",
        body,
        "} _tca_err]} {",
        '    set _tca_msg [string map [list "\\\\" "/" "\\"" "\'" "\\n" " " "\\r" " "] $_tca_err]',
        # $run_dir once set: it is absolute, and the run has cd-ed (RUN_DIR_TCL).
        "    set _tca_dir [expr {[info exists run_dir] ? $run_dir : [file dirname [info script]]}]",
        "    set _tca_rf [open [file join $_tca_dir %s] w]" % FAILED_MARKER,
        '    puts $_tca_rf "{\\"status\\":\\"failed\\",\\"run\\":\\"%s\\",\\"error\\":\\"$_tca_msg\\"}"'
        % run_name,
        "    close $_tca_rf",
        '    puts "ERROR: run.tcl stopped on a Tcl error: $_tca_err"',
        "    exit 1",
        "}",
    ] + ([] if _keep_session() else ["exit"]))


def opt_line(flow_def, config, stage1=False):
    """opt_design for this flow, or a comment when a post-opt input is not re-optimized."""
    rerun = (config.get('stage1', {}) or {}).get('rerun_opt_design', True)
    if rerun is False or str(rerun).lower() == 'false':
        return ("# opt_design skipped: stage1.rerun_opt_design=false "
                "(input checkpoint is already post-opt)")
    return opt_cmd(flow_def)


def inherit_parent(config):
    val = (config.get('stage2', {}) or {}).get('inherit_parent_flow', False)
    return val is True or str(val).lower() == 'true'


def stage2_base(config, parent_flow):
    """
    Flow, opt line and pre-opt early-fix block shared by every Stage 2 kind.

    With stage2.inherit_parent_flow the run replays the Stage 1 winner's own
    flow (its placer strategy, opt directive and early fixes), so the fix is
    the only difference from the run it is trying to beat. Without it, the
    historical behaviour is kept: a plain opt_design + common tail.
    """
    if inherit_parent(config):
        flow_def = replay_flow(STAGE1_FLOWS.get(parent_flow, STAGE1_FLOWS["explore"]), config)
        return flow_def, opt_line(flow_def, config), early_fixes_block(config)
    # A fix that switches early fixes (--early-fix) gets the block regardless.
    early = early_fixes_block(config) if config.get('_force_early_fixes') else ""
    return dict(_COMMON_TAIL), "opt_design", early


# ===========================================================================
# Generators
# ===========================================================================
def generate_stage1(dcp_path, flow_name, config, extra_cmds=None, fix_xdcs=None,
                    run_name=None):
    """
    One of the 7 Stage 1 strategy runs, with early fixes applied in-session.

    fix_xdcs: [(path, mode)] sourced before the early fixes -- a Path B (R2)
    rerun of this flow with the fix its own post-place check produced. Such a
    run is never stopped as a duplicate: it is meant to replay its parent.
    """
    if flow_name not in STAGE1_FLOWS:
        raise KeyError("unknown Stage 1 flow %r (known: %s)" % (
            flow_name, ", ".join(sorted(STAGE1_FLOWS))))

    flow_def = replay_flow(STAGE1_FLOWS[flow_name], config)
    run_name = run_name or FLOW_RUN_ID.get(flow_name, flow_name)
    adaptive = (config.get('stage1', {}) or {}).get('adaptive_physopt', False)
    baseline = flow_def.get('baseline_only', False)
    config = dict(config)
    config['_route_directive_override'] = (config.get('stage1', {}) or {}).get(
        'route_directive_override')

    lines = [preamble("Stage 1 Implementation Run", run_name, dcp_path, config,
                      extra_cmds)]
    if config.get('_design_checks'):
        lines.append(methodology_tcl())
    for path, mode in fix_xdcs or []:
        lines.append("\n".join(_xdc_apply_lines(path, mode, config)) + "\n")
    lines += [early_fixes_block(config) if not baseline else
              "# --- Early fixes: not applied (baseline reference run) ---\n",
              flow_body(flow_def, config, adaptive=adaptive and not baseline,
                        opt_override=opt_line(flow_def, config),
                        after_place_lines=None if (baseline or fix_xdcs) else
                        stage1_duplicate_stop_tcl(flow_name, config, run_name),
                        # Not in a Path B round-2 run (fix_xdcs), which only
                        # reuses this generator: upstream checks Stage 1 only.
                        stage1_checks=not fix_xdcs)]
    return _finish(lines, "Stage 1 run %s completed successfully." % run_name, run_name)


def generate_stage2_rqs(dcp_path, rqs_file, config, run_name="S2_RQS",
                        parent_flow="explore", extra_cmds=None, rqs_categories=None,
                        xdcs=None):
    """
    Stage 2 RQS run: replay the parent flow with its own QoR suggestions applied.
    xdcs: the winner's skill/planner XDC chain, replayed first (planner rounds).
    """
    flow_def = replay_flow(STAGE1_FLOWS.get(parent_flow, STAGE1_FLOWS["explore"]), config)
    early = early_fixes_block(config) if inherit_parent(config) or \
        config.get('_force_early_fixes') else ""
    opt = opt_line(flow_def, config) if inherit_parent(config) else None
    pre_opt = []
    for path, mode in xdcs or []:
        pre_opt.extend(_xdc_apply_lines(path, mode, config))
    pre_opt.append(apply_rqs_block(rqs_file, rqs_categories, require_enabled=True))
    lines = [preamble("Stage 2 - RQS Run", run_name, dcp_path, config, extra_cmds),
             early,
             flow_body(flow_def, config,
                       pre_opt_lines=pre_opt,
                       adaptive=True, label="_RQS", opt_override=opt)]
    return _finish(lines, "Stage 2 RQS run %s completed successfully." % run_name,
                   run_name)


PRE_ROUTE_MARKER = "# --- Reset over-constraining before route ---"
OC_RESET = "oc_reset.tcl"

UU_PROC = r"""proc _tca_uu {src dst} {
    # User uncertainty (UU, ns) on the worst setup path src -> dst; "" if none.
    set cmd [list report_timing -delay_type max -max_paths 1 -return_string]
    if {$src ne "*"} { lappend cmd -from [get_clocks $src] }
    if {$dst ne "*"} { lappend cmd -to [get_clocks $dst] }
    if {[catch {eval $cmd} txt]} { return "" }
    if {[regexp {\(UU\):\s+(-?[0-9.]+)ns} $txt -> uu]} { return $uu }
    return ""
}"""


def over_constrain_tcl(clock_info):
    """
    Over-constrain lines for Stage 2 / planner OC runs, applied before opt_design.

    The per-clock form is applied first (the original flow's). Then the user
    uncertainty (UU) on the pair's worst path is compared with the value before:
    a clock-pair uncertainty in the design's own XDC outranks a per-clock one
    (design A: -from * -to * 0.000; design B: -from/-to clk_pl_1 0.000), and there
    the OC silently did nothing and the run replayed its parent. Then the pair
    form (the highest precedence) is set to the old value + OC; if the UU still
    does not move, the run stops (run.failed) instead of replaying its parent.
    The matching reset commands, with the measured values, go to oc_reset.tcl,
    which the pre-route step (and a Stage 3 re-route of this placement) sources.
    """
    lines = ["# --- Over-Constrain (WNS-based, applied before opt_design) ---",
             UU_PROC, "set _tca_oc_reset {}"]
    for i, entry in enumerate(clock_info):
        src, dst, val = entry['src_clk'], entry['dst_clk'], float(entry['oc_value'])
        lines += [
            "set _c1 {%s}; set _c2 {%s}; set _v %.4f" % (src, dst, val),
            "set _b [_tca_uu $_c1 $_c2]",
        ]
        if src == dst:
            lines.append("set_clock_uncertainty -setup $_v [get_clocks $_c2]")
            legacy = ("lappend _tca_oc_reset [format {set_clock_uncertainty -setup 0 "
                      "[get_clocks {%s}]} $_c2]")
        else:
            lines.append("set_clock_uncertainty -setup $_v -from [get_clocks $_c1] "
                         "-to [get_clocks $_c2]")
            legacy = ("lappend _tca_oc_reset [format {set_clock_uncertainty -setup 0 "
                      "-from [get_clocks {%s}] -to [get_clocks {%s}]} $_c1 $_c2]")
        lines += [
            "set _a [_tca_uu $_c1 $_c2]",
            'if {$_b eq "" || $_a eq ""} {',
            '    puts "WARNING: over-constrain $_c1 -> $_c2: no timing path to verify it on"',
            "    %s" % legacy,
            "} elseif {$_a - $_b >= $_v / 2.0} {",
            '    puts "INFO: over-constrain $_c1 -> $_c2 applied: UU $_b -> $_a ns"',
            "    %s" % legacy,
            "} else {",
            '    puts "INFO: over-constrain $_c1 -> $_c2 did not apply (UU $_b -> $_a ns): '
            'a clock-pair uncertainty outranks it; re-applying as a clock pair"',
            "    set_clock_uncertainty -setup [expr {$_b + $_v}] -from [get_clocks $_c1] "
            "-to [get_clocks $_c2]",
            "    set _a [_tca_uu $_c1 $_c2]",
            '    if {$_a eq "" || $_a - $_b < $_v / 2.0} {',
            '        error "over-constrain not applied: UU on $_c1 -> $_c2 stayed at $_a ns '
            '(expected [expr {$_b + $_v}])"',
            "    }",
            '    puts "INFO: over-constrain $_c1 -> $_c2 applied as a clock pair: UU $_b -> $_a ns"',
            "    # The first form still covers the clock's other paths: reset both.",
            "    %s" % legacy,
            "    lappend _tca_oc_reset [format {set_clock_uncertainty -setup %s -from "
            "[get_clocks {%s}] -to [get_clocks {%s}]} $_b $_c1 $_c2]",
            "}",
        ]
    lines += [
        "set _f [open [file join $run_dir %s] w]" % OC_RESET,
        'puts $_f [join $_tca_oc_reset "\\n"]',
        "close $_f",
    ]
    return lines


def generate_stage2_over_constrain(dcp_path, clock_info, config, run_name="S2_OC",
                                   parent_flow="explore", extra_cmds=None, xdcs=None):
    """
    Stage 2 Over-Constrain: tighten clock uncertainty before opt, reset before route.
    clock_info: [{src_clk, dst_clk, oc_value}] with oc_value = |WNS| x multiplier.
    xdcs: the winner's skill/planner XDC chain, replayed first (planner rounds).
    """
    flow_def = replay_flow(STAGE1_FLOWS.get(parent_flow, STAGE1_FLOWS["explore"]), config)
    pre_opt = []
    for path, mode in xdcs or []:
        pre_opt.extend(_xdc_apply_lines(path, mode, config))
    pre_opt += over_constrain_tcl(clock_info)
    pre_route = [PRE_ROUTE_MARKER,
                 "source [file join $run_dir %s]" % OC_RESET]

    early = early_fixes_block(config) if inherit_parent(config) or \
        config.get('_force_early_fixes') else ""
    opt = opt_line(flow_def, config) if inherit_parent(config) else None
    lines = [preamble("Stage 2 - Over-Constrain", run_name, dcp_path, config, extra_cmds),
             early,
             flow_body(flow_def, config, pre_opt_lines=pre_opt,
                       pre_route_lines=pre_route, adaptive=True, label="_OC",
                       opt_override=opt,
                       route_override=route_line(config))]
    return _finish(lines, "Stage 2 OC run %s completed successfully." % run_name,
                   run_name)


def _subdirective_place(subdirective, config=None, off_versal=None):
    """
    place_design with a sub-directive list. Auto_n sub-directives only exist once
    the AUTO placer params are set (Stage 1's auto flows set them); without them
    place_design fails with Constraints 18-12534 (VIAVI planner fixes 1 and 4).
    When the parent run's log says what the placer predicted for them, that
    concrete list is used instead (no params needed).

    off_versal: the place directive to run instead on a part without placer
    sub-directives (Vivado_Tcl 4-2310); chosen in the run, from the part it opened.
    """
    if off_versal:
        versal = _subdirective_place(subdirective, config)
        return "\n".join(
            ["# Placer sub-directives exist on Versal only; elsewhere %s." % off_versal,
             "if {[string match -nocase versal* [get_property ARCHITECTURE "
             "[get_parts [get_property PART [current_design]]]]]} {"]
            + [("    " + ln) if ln else "" for ln in versal.splitlines()]
            + ["} else {", "    place_design -directive %s" % off_versal, "}"])
    sd = subdirective.strip().strip("{}").strip()
    concrete = resolve_auto_subdirective(sd, (config or {}).get('_parent_run_dir'))
    if concrete:
        return "\n".join(["# Replays the parent run's placer prediction for { %s }" % sd,
                          "place_design -directive Default -subdirective %s" % concrete])
    cmd = "place_design -directive Default -subdirective { %s }" % sd
    if ".Auto_" in sd and auto_params_enabled(config or {}):
        return "\n".join(["# --- AUTO placer params (Auto_n sub-directives) ---",
                          auto_params(config or {}), "", cmd])
    return cmd


def generate_stage2_category_directive(dcp_path, subdirective, config,
                                       run_name="S2_CAT", extra_cmds=None,
                                       parent_flow="explore", off_versal=None):
    """Stage 2 skill run driven by a placement subdirective from directives.yaml."""
    flow_def, opt, early = stage2_base(config, parent_flow)
    lines = [preamble("Stage 2 - Category Directive Run", run_name, dcp_path,
                      config, extra_cmds),
             early,
             flow_body(flow_def, config, adaptive=True, label="_CatDirective",
                       opt_override=opt,
                       place_override=_subdirective_place(subdirective, config, off_versal),
                       route_override=route_line(config))]
    return _finish(lines,
                   "Stage 2 category directive run %s completed successfully." % run_name,
                   run_name)


PLANNER_XDC = "planner_fix.xdc"
XDC_CHECK = "xdc_check.json"
_OBJ_QUERY = ("get_cells", "get_pins", "get_nets", "get_ports", "get_pblocks",
              "get_clocks", "get_sites", "get_clock_regions")


def _bracket_groups(cmd):
    """Top-level [...] groups of one Tcl command (nested brackets kept inside)."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(cmd):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
            if depth == 0:
                out.append(cmd[start:i + 1])
    return out


def xdc_object_queries(xdc_text):
    """Every object query ([get_cells ...], [get_pins ...], ...) in an XDC, deduplicated."""
    seen = []
    buf = ""
    for raw in xdc_text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        cmd = (buf + line).strip()
        buf = ""
        if not cmd or cmd.startswith("#"):
            continue
        for g in _bracket_groups(cmd):
            head = g[1:].strip().split(None, 1)[0] if g[1:].strip() else ""
            if head in _OBJ_QUERY and g not in seen:
                seen.append(g)
    return seen


def planner_xdc_apply_tcl(xdc_path):
    """
    Apply the planner XDC one command at a time, each in its own catch. Plain
    `source` aborts on the first error, and in Vivado an add_cells_to_pblock
    whose query matched nothing IS an error (12-1433), so one bad pattern used to
    drop the whole fix and the rest of the run.
    """
    import planner
    try:
        with open(xdc_path, 'r', encoding='utf-8') as f:
            cmds = planner._xdc_commands(f.read().splitlines())
    except (IOError, OSError):
        return ['puts {WARNING: planner XDC not found: %s}' % xdc_path,
                "set _tca_xdc_errs {}"]
    lines = ["# --- Apply planner XDC %s (one command at a time) ---" % xdc_path,
             "set _tca_xdc_errs {}",
             "set _tca_xdc_cmds {}"]
    for c in cmds:
        lines.append("lappend _tca_xdc_cmds {%s}" % c)
    lines += [
        "foreach _c $_tca_xdc_cmds {",
        "    if {[catch {uplevel #0 $_c} _e]} {",
        "        lappend _tca_xdc_errs $_c",
        '        puts "WARNING: planner XDC command failed: $_c :: $_e"',
        "    }",
        "}",
        'puts "INFO: Applied planner XDC: [expr {[llength $_tca_xdc_cmds] - '
        '[llength $_tca_xdc_errs]}] of [llength $_tca_xdc_cmds] commands"',
    ]
    return lines


def xdc_check_tcl(xdc_path, policy="warn"):
    """
    After the planner XDC is applied: count what each object query matches.
    A query matching nothing means the fix was only partly applied (the planner
    cannot see the netlist when it writes patterns). Results go to xdc_check.json;
    policy "fail" also stops the run (run.failed) within minutes of launch.
    """
    try:
        with open(xdc_path, 'r', encoding='utf-8') as f:
            queries = xdc_object_queries(f.read())
    except (IOError, OSError):
        return []
    if not queries:
        return []
    lines = ["# --- Check every object query in the planner XDC matched something ---",
             "set _tca_q {}"]
    for q in queries:
        lines.append("lappend _tca_q {%s}" % q)
    lines += [
        "set _tca_empty {}",
        "foreach _q $_tca_q {",
        "    set _n -1",
        "    catch { set _n [llength [eval [string range $_q 1 end-1]]] }",
        "    if {$_n <= 0} { lappend _tca_empty $_q }",
        "}",
        "if {![info exists _tca_xdc_errs]} { set _tca_xdc_errs {} }",
        "set _f [open [file join $run_dir %s] w]" % XDC_CHECK,
        'puts $_f "{\\"queries\\":[llength $_tca_q],\\"empty\\":[llength $_tca_empty],'
        '\\"failed_commands\\":[llength $_tca_xdc_errs]}"',
        "close $_f",
        "foreach _q $_tca_empty { puts \"WARNING: planner XDC query matched nothing: $_q\" }",
    ]
    if policy == "fail":
        lines += ["if {[llength $_tca_empty] || [llength $_tca_xdc_errs]} {",
                  '    error "planner XDC: [llength $_tca_empty] of [llength $_tca_q] object '
                  'queries matched nothing, [llength $_tca_xdc_errs] command(s) failed"',
                  "}"]
    return lines


def _xdc_apply_lines(xdc_path, apply_mode, config=None):
    verb = "read_xdc" if apply_mode == "read_xdc" else "source"
    lines = [
        "# --- Apply skill fix XDC ---",
        'if {[file exists {%s}]} {' % xdc_path,
        '    %s {%s}' % (verb, xdc_path),
        '    puts {INFO: Applied skill fix XDC: %s}' % xdc_path,
        '} else {',
        '    puts {WARNING: Skill fix XDC not found: %s}' % xdc_path,
        '}',
    ]
    if os.path.basename(xdc_path) == PLANNER_XDC:
        policy = str(((config or {}).get('planner', {}) or {}).get(
            'empty_query_policy', 'warn')).lower()
        lines = planner_xdc_apply_tcl(xdc_path) + xdc_check_tcl(xdc_path, policy)
    return lines


def generate_stage2_xdc_fix(dcp_path, xdc_path, config, run_name="S2_XDC",
                            apply_mode="source", extra_cmds=None, parent_flow="explore"):
    """
    Stage 2 skill run that sources a detect-generated XDC before opt_design.

    apply_mode is "source" for most skills; partition/slr_crossing's own fix.tcl
    uses read_xdc for suggested_slr_pblock_aware.xdc, so that is selectable.
    """
    flow_def, opt, early = stage2_base(config, parent_flow)
    lines = [preamble("Stage 2 - Skill XDC Fix", run_name, dcp_path, config, extra_cmds),
             early,
             flow_body(flow_def, config,
                       pre_opt_lines=_xdc_apply_lines(xdc_path, apply_mode, config),
                       adaptive=True, label="_XdcFix", opt_override=opt,
                       route_override=route_line(config))]
    return _finish(lines, "Stage 2 XDC fix run %s completed successfully." % run_name,
                   run_name)


def generate_stage2_stacked(dcp_path, config, run_name="S2_STACKED", xdcs=None,
                            subdirective=None, rqs_file=None, rqs_categories=None,
                            extra_cmds=None, parent_flow="explore", clock_info=None,
                            off_versal=None):
    """
    A category's skill fixes applied together with its RQS suggestions.

    xdcs: [(path, apply_mode)] produced by detection; subdirective: one
    placement subdirective from a directive skill; rqs_file + rqs_categories:
    the parent's suggestions filtered to the category.
    """
    flow_def, opt, early = stage2_base(config, parent_flow)
    pre_opt = []
    for path, mode in xdcs or []:
        pre_opt.extend(_xdc_apply_lines(path, mode, config))
    if rqs_file:
        pre_opt.append(apply_rqs_block(rqs_file, rqs_categories))
    pre_route = None
    if clock_info:
        # The cross-category combined run's optional over-constraint ladder.
        pre_opt += over_constrain_tcl(clock_info)
        pre_route = [PRE_ROUTE_MARKER, "source [file join $run_dir %s]" % OC_RESET]
    place = _subdirective_place(subdirective, config, off_versal) if subdirective else None
    lines = [preamble("Stage 2 - Stacked Skills + RQS", run_name, dcp_path, config,
                      extra_cmds),
             early,
             flow_body(flow_def, config, pre_opt_lines=pre_opt or None, adaptive=True,
                       pre_route_lines=pre_route,
                       label="_Stacked", opt_override=opt, place_override=place,
                       route_override=route_line(config))]
    return _finish(lines, "Stage 2 stacked run %s completed successfully." % run_name,
                   run_name)


def generate_stage2_tcl_fix(dcp_path, fix_tcl, parent_run_dir, config,
                            run_name="S2_TCLFIX", extra_cmds=None, parent_flow="explore"):
    """
    Stage 2 skill run that sources a skill's fix.tcl before opt_design.

    RUN_DIR and PARENT_RUN_DIR are set BEFORE the source: three fix.tcl scripts
    (gt_mrmac_preplace, pcie_clock_root, hardsync_ce) read $env(PARENT_RUN_DIR)
    without a guard and raise a TCL error if it is unset.
    """
    flow_def, opt, early = stage2_base(config, parent_flow)

    pre_opt = [
        "# --- Apply skill fix TCL ---",
        'set env(RUN_DIR) $run_dir',
        'set env(PARENT_RUN_DIR) {%s}' % parent_run_dir,
        'if {[file exists {%s}]} {' % fix_tcl,
        '    if {[catch {source {%s}} _fix_err]} {' % fix_tcl,
        '        puts "WARNING: Skill fix TCL failed: $_fix_err"',
        '    }',
        '} else {',
        '    puts {WARNING: Skill fix TCL not found: %s}' % fix_tcl,
        '}',
    ]

    lines = [preamble("Stage 2 - Skill TCL Fix", run_name, dcp_path, config, extra_cmds),
             early,
             flow_body(flow_def, config, pre_opt_lines=pre_opt, adaptive=True,
                       label="_TclFix", opt_override=opt,
                       route_override=route_line(config))]
    return _finish(lines, "Stage 2 TCL fix run %s completed successfully." % run_name,
                   run_name)


# ===========================================================================
# Stage 3 -- last-mile passes on the overall winner's own checkpoints
# ===========================================================================
LAST_MILE_PHYSOPT = ["AggressiveExplore", "AggressiveFanoutOpt", "AlternateReplication"]


def _postroute_tail(config):
    return final_reports(config)


def generate_stage3_last_mile(dcp_path, config, run_name="S3_LAST_MILE", extra_cmds=None):
    """
    Open the winner's routed checkpoint and push it with a sequence of post-route
    phys_opt directives (each stops early on timing met), then re-route whatever
    phys_opt touched. Cheap relative to a full replay.
    """
    lines = [preamble("Stage 3 - Last-Mile Post-Route Optimization", run_name, dcp_path,
                      config, extra_cmds)]
    for directive in LAST_MILE_PHYSOPT:
        lines.append(adaptive_physopt_tcl("phys_opt_design -directive %s" % directive,
                                          "last_mile_%s" % directive, 2))
        lines.append("")
    lines.append("# --- Route anything phys_opt left unrouted ---")
    lines.append("route_design -directive Explore -tns_cleanup")
    lines.extend(hook_lines(config, "_post_route_cmds"))
    lines.append(adaptive_physopt_tcl("phys_opt_design -directive AggressiveExplore",
                                      "last_mile_final", 2))
    lines.extend(hook_lines(config, "_post_route_popt_cmds"))
    lines.append(_postroute_tail(config))
    return _finish(lines, "Stage 3 last-mile run %s completed successfully." % run_name,
                   run_name)


def generate_stage3_reroute(dcp_path, config, run_name="S3_REROUTE", extra_cmds=None,
                            pre_route_cmds=None, route_directive="AggressiveExplore"):
    """
    Open the winner's placed (post-place phys_opt) checkpoint and route it again
    with another router directive -- a second routing attempt on the same
    placement. The orchestrator picks a directive this placement has not been
    routed with yet (routing is deterministic: the same directive on the same
    placement just replays the winner).

    pre_route_cmds replays the winner's own pre-route step. An over-constrain
    winner's placed checkpoint still carries its extra clock uncertainty (it is
    only reset just before route_design), so without its reset lines this run
    would route -- and report -- against an over-constrained clock.
    """
    lines = [preamble("Stage 3 - Aggressive Re-Route", run_name, dcp_path, config,
                      extra_cmds)]
    if pre_route_cmds:
        lines.append("# --- Winner's own pre-route step (replayed) ---")
        lines.extend(pre_route_cmds)
        lines.append("")
    lines += ["# --- route_design (re-route of the winner's placement) ---",
              "route_design -directive %s -tns_cleanup" % route_directive]
    lines += hook_lines(config, "_post_route_cmds")
    lines += [report_block("postroute", include_utilization=True,
                           include_rqs=intermediate_rqs(config),
                           skip_dcp=(config.get('output', {}) or {}).get('skip_dcp', False),
                           include_route_status=route_status_enabled(config)),
              adaptive_physopt_tcl("phys_opt_design -directive AggressiveExplore",
                                   "reroute_postroute", 3)]
    lines += hook_lines(config, "_post_route_popt_cmds")
    lines.append(_postroute_tail(config))
    return _finish(lines, "Stage 3 re-route run %s completed successfully." % run_name,
                   run_name)


# ===========================================================================
# Planner (evidence-driven Stage 2)
# ===========================================================================
PLANNER_EVIDENCE_TCL = asset("planner", "evidence.tcl")


def generate_planner_evidence(dcp_path, config, run_name="PLANNER_EVIDENCE"):
    """Open the winner's routed checkpoint and dump planner evidence (read-only)."""
    lines = [header("Planner Evidence Collection", run_name),
             RUN_DIR_TCL,
             "",
             'open_checkpoint {%s}' % dcp_path,
             'source {%s}' % PLANNER_EVIDENCE_TCL,
             ""]
    return _finish(lines, "Planner evidence run %s completed successfully." % run_name,
                   run_name)


def generate_stage2_custom_flow(dcp_path, config, run_name="S2_CUSTOM", extra_cmds=None,
                                parent_flow="explore", opt=None, place=None,
                                place_subdirective=None, physopt=None, route=None,
                                xdcs=None):
    """
    A planner-chosen directive combination (validated by orchestrate.py), with
    optional physical-constraint XDCs applied before opt_design.
    """
    base, _, early = stage2_base(config, parent_flow)
    flow_def = dict(base)
    if opt:
        flow_def["opt"] = {"directive": opt}
    if physopt:
        flow_def["postplace_physopt"] = {"directive": physopt}
        flow_def["postroute_physopt"] = {"directive": physopt}
    if route:
        flow_def["route"] = {"directive": route, "switches": "-tns_cleanup"}
        # The planner's router choice is the fix: stage2.route_directive_override
        # must not replace it.
        config = dict(config, _route_directive_override=None)
    place_override = None
    if place_subdirective:
        place_override = _subdirective_place(place_subdirective, config)
    elif place:
        place_override = "place_design -directive %s" % place
    pre_opt = []
    for path, mode in xdcs or []:
        pre_opt.extend(_xdc_apply_lines(path, mode, config))
    lines = [preamble("Stage 2 - Planner Custom Flow", run_name, dcp_path, config,
                      extra_cmds),
             early,
             flow_body(flow_def, config, pre_opt_lines=pre_opt or None, adaptive=True,
                       label="_Planner", opt_override=opt_line(flow_def, config),
                       place_override=place_override)]
    return _finish(lines, "Stage 2 planner run %s completed successfully." % run_name,
                   run_name)


def generate_planner_incremental(dcp_path, config, run_name="SP_INCREMENTAL",
                                 extra_cmds=None, xdcs=None, path_groups=None,
                                 physopt_directives=None, route_directive=None, passes=2):
    """
    A planner fix that starts from the winner's ROUTED checkpoint instead of
    replaying the flow: define path groups (group_path only, validated by
    planner.py), then optimise just those groups (phys_opt_design -path_groups
    cannot take -directive, UG835), then optional directive passes and an
    optional re-route. Hours cheaper than a full place-and-route.
    """
    lines = [preamble("Planner - Incremental From Routed Winner", run_name, dcp_path, config,
                      extra_cmds)]
    # On a routed wall the WNS often stays put while TNS keeps falling; keep going then.
    tns_pct = (config.get('planner', {}) or {}).get('tns_continue_pct', 5)
    for path, mode in xdcs or []:
        lines.extend(_xdc_apply_lines(path, mode, config))
        lines.append("")
    if path_groups:
        lines.append('puts "INFO: path groups: [get_path_groups -quiet]"')
        lines.append(adaptive_physopt_tcl(
            "phys_opt_design -path_groups [get_path_groups {%s}]" % " ".join(path_groups),
            "incremental_path_groups", passes, tns_aware_pct=tns_pct))
        lines.append("")
    for d in physopt_directives or []:
        lines.append(adaptive_physopt_tcl("phys_opt_design -directive %s" % d,
                                          "incremental_%s" % d, passes,
                                          tns_aware_pct=tns_pct))
        lines.append("")
    if route_directive:
        lines.append("# --- Re-route with the planner's directive ---")
        lines.append("route_design -directive %s -tns_cleanup" % route_directive)
        lines.extend(hook_lines(config, "_post_route_cmds"))
        lines.append(adaptive_physopt_tcl("phys_opt_design -directive AggressiveExplore",
                                          "incremental_after_route", passes))
    lines.extend(hook_lines(config, "_post_route_popt_cmds"))
    lines.append(_postroute_tail(config))
    return _finish(lines, "Planner incremental run %s completed successfully." % run_name,
                   run_name)


PROBE_JSON = "probe.json"


def generate_probe(dcp_path, config, run_name="PROBE"):
    """
    Open the input checkpoint with the pinned build and record what opened: a
    few minutes that catch a build that cannot open the design (the 2026.2 daily
    crashed in 13 of 14 runs while parsing XDC) before seven multi-hour runs.
    """
    lines = [header("Build Probe", run_name),
             RUN_DIR_TCL,
             "",
             'open_checkpoint {%s}' % dcp_path,
             'set _v [version -short]',
             'set _p [get_property PART [current_design]]',
             'set _a ""',
             'catch { set _a [get_property ARCHITECTURE [get_parts $_p]] }',
             'set _n [llength [get_cells -quiet -hierarchical -filter {IS_PRIMITIVE}]]',
             'set _f [open [file join $run_dir %s] w]' % PROBE_JSON,
             'puts $_f "{\\"vivado\\":\\"$_v\\",\\"part\\":\\"$_p\\",'
             '\\"architecture\\":\\"$_a\\",\\"primitives\\":$_n}"',
             'close $_f',
             'puts "INFO: probe: Vivado $_v, part $_p ($_a), $_n primitives"',
             ""]
    return _finish(lines, "Build probe %s completed successfully." % run_name, run_name)


POSTPLACE_MARKER = "postplace_physopt.done"
SLR_PBLOCK_TCL = asset("skills", "partition", "slr_crossing",
                       "auto_suggest_slr_pblock_aware.tcl")
SLR_PBLOCK_XDC = "suggested_slr_pblock_aware.xdc"


def generate_path_b_detect(dcp_path, config, run_name="R2D"):
    """
    Path B (upstream's R2): open a Stage 1 run's placed checkpoint and write the
    SLR-aware pblock XDC for its failing SLR crossings. A separate job, so the
    Stage 1 run keeps routing: on the TSR designs this generator took 17-47 min
    in report_slr_crossing alone, and inside every Stage 1 run that would hold
    up all of Stage 2. The generator takes no env vars: it writes into the cwd.
    """
    lines = [header("Path B: SLR pblock XDC from a placed Stage 1 run", run_name),
             RUN_DIR_TCL,
             "",
             'open_checkpoint {%s}' % dcp_path,
             "set _orig_dir [pwd]",
             "cd $run_dir",
             "if {[catch {source {%s}} _gen_err]} {" % SLR_PBLOCK_TCL,
             '    puts "WARNING: SLR pblock generator failed: $_gen_err"',
             "}",
             "cd $_orig_dir",
             'if {[file exists [file join $run_dir %s]]} {' % SLR_PBLOCK_XDC,
             '    puts "INFO: Path B XDC written: [file join $run_dir %s]"' % SLR_PBLOCK_XDC,
             "} else {",
             '    puts "INFO: Path B: the generator wrote no XDC"',
             "}",
             ""]
    return _finish(lines, "Path B detect run %s completed successfully." % run_name, run_name)


def generate_detect(dcp_path, detections, run_name="DETECT"):
    """
    Detection-only session: open a checkpoint and source each skill's detect.tcl.

    `detections` is the ordered list from skill_resolver.py's detection_plan for
    one design state. Each entry needs at least {sub_problem, detect_tcl}; an
    optional generator_tcl (with generator_needs_cd) is sourced afterwards.

    Every source is catch-wrapped so one failing skill can't abort the session,
    and RUN_DIR is exported first because every detect.tcl reads it.
    """
    lines = [header("Skill Detection Pass", run_name),
             RUN_DIR_TCL,
             "",
             'open_checkpoint {%s}' % dcp_path,
             "",
             'set env(RUN_DIR) $run_dir',
             'puts "INFO: Detection pass in $run_dir"',
             ""]

    for det in detections:
        sub = det.get("sub_problem", "?")
        detect_tcl = det.get("detect_tcl", "")
        if not detect_tcl:
            continue
        lines.append("# --- detect: %s/%s ---" % (det.get("category", "?"), sub))
        lines.append('puts "INFO: Running %s detection..."' % sub)
        lines.append('if {[catch {source {%s}} _det_err]} {' % detect_tcl)
        lines.append('    puts "WARNING: %s detection failed: $_det_err"' % sub)
        lines.append('}')

        gen = det.get("generator_tcl")
        if gen:
            lines.append("# %s needs its constraint generator run from inside "
                         "run_dir (it takes no env vars)" % sub)
            if det.get("generator_needs_cd"):
                lines.append('set _orig_dir [pwd]')
                lines.append('cd $run_dir')
            lines.append('if {[catch {source {%s}} _gen_err]} {' % gen)
            lines.append('    puts "WARNING: %s generator failed: $_gen_err"' % sub)
            lines.append('}')
            if det.get("generator_needs_cd"):
                lines.append('cd $_orig_dir')
        lines.append("")

    lines.append('puts "INFO: Detection pass complete. Flag files and fix XDCs '
                 'are in $run_dir."')
    lines.append(sentinel_tcl(run_name))
    lines.append("exit")
    return "\n".join(lines)


# ===========================================================================
# CLI
# ===========================================================================
def load_config(path):
    if path and os.path.isfile(path):
        return load_yaml(path)
    default = os.path.join(SKILL_ROOT, "config", "agent_config.yaml")
    if os.path.isfile(default):
        return load_yaml(default)
    return {}


def write_run_tcl(run_dir, content, dry_run=False):
    """
    Write run.tcl INTO the run directory -- the script resolves $run_dir from
    its own location, so the file's placement is load-bearing.
    """
    if dry_run:
        print(content)
        return ""
    run_dir = os.path.abspath(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "run.tcl")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content + "\n")
    print(path)
    return path


def main():
    parser = argparse.ArgumentParser(description="Generate Vivado TCL for this skill.")
    # NOTE: no required=True -- this cluster puts an argparse 1.4.0 backport ahead
    # of the stdlib on sys.path, and it rejects that keyword. Checked by hand below.
    sub = parser.add_subparsers(dest="mode")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dcp", required=True, help="Checkpoint to open.")
    common.add_argument("--run-dir", required=True,
                        help="Run directory; run.tcl is written here and the "
                             "script resolves $run_dir from its own location.")
    common.add_argument("--config", help="agent_config.yaml (default: package config).")
    common.add_argument("--extra-cmd", action="append", default=[],
                        help="Raw TCL inserted after open_checkpoint (repeatable).")
    common.add_argument("--post-opt-cmd", action="append", default=[],
                        help="Raw TCL inserted after opt_design (repeatable).")
    common.add_argument("--post-place-cmd", action="append", default=[],
                        help="Raw TCL inserted after place_design (repeatable).")
    common.add_argument("--post-popt-cmd", action="append", default=[],
                        help="Raw TCL inserted after post-place phys_opt_design "
                             "(repeatable).")
    common.add_argument("--post-route-cmd", action="append", default=[],
                        help="Raw TCL inserted after route_design (repeatable).")
    common.add_argument("--post-route-popt-cmd", action="append", default=[],
                        help="Raw TCL inserted after post-route phys_opt_design "
                             "(repeatable).")
    common.add_argument("--replay-run-dir",
                        help="The Stage 1 run whose flow is replayed: its logged placer "
                             "predictions replace Auto_n subdirectives.")
    common.add_argument("--run-name", help="Run name used in banners/messages.")
    common.add_argument("--dry-run", action="store_true",
                        help="Print the TCL instead of writing run.tcl.")

    p1 = sub.add_parser("stage1", parents=[common], help="A Stage 1 strategy run.")
    p1.add_argument("--flow", required=True, choices=sorted(STAGE1_FLOWS),
                    help="Which of the 7 Stage 1 strategies to generate.")
    p1.add_argument("--fix-xdc", action="append", default=[],
                    help="Path B (R2): XDC to source before opt_design (repeatable); "
                         "the run is then never stopped as a duplicate.")
    p1.add_argument("--design-checks", action="store_true",
                    help="This run also writes the design-level checks: "
                         "report_methodology before opt_design, check_timing and "
                         "report_cdc after it (one Stage 1 run per design).")

    pd = sub.add_parser("detect", parents=[common], help="A skill detection pass.")
    pd.add_argument("--detect", action="append", default=[],
                    help="Path to a detect.tcl (repeatable, order preserved).")
    pd.add_argument("--detection-plan",
                    help="skill_resolver.py JSON; use with --detect-stage.")
    pd.add_argument("--detect-stage",
                    help="Which detection_plan stage to emit "
                         "(pre_opt|pre_place|post_place|post_route).")

    p2 = sub.add_parser("stage2", parents=[common], help="A Stage 2 fix run.")
    p2.add_argument("--kind", required=True,
                    choices=["rqs", "over_constrain", "category_directive",
                             "xdc_fix", "tcl_fix", "stacked", "custom_flow", "incremental"])
    p2.add_argument("--opt-directive", help="kind=custom_flow: opt_design directive.")
    p2.add_argument("--place-directive", help="kind=custom_flow: place_design directive.")
    p2.add_argument("--place-subdirective",
                    help="kind=custom_flow: place_design subdirective list.")
    p2.add_argument("--physopt-directive", help="kind=custom_flow: phys_opt_design directive.")
    p2.add_argument("--route-directive",
                    help="kind=custom_flow|incremental: route_design directive.")
    p2.add_argument("--path-group", action="append", default=[],
                    help="kind=incremental: path group for phys_opt_design -path_groups "
                         "(repeatable).")
    p2.add_argument("--incr-physopt", action="append", default=[],
                    help="kind=incremental: phys_opt_design directive pass (repeatable).")
    p2.add_argument("--passes", type=int, default=2,
                    help="kind=incremental: max adaptive passes per step (default 2).")
    p2.add_argument("--parent-flow", default="explore", choices=sorted(STAGE1_FLOWS),
                    help="Stage 1 flow to replay (rqs / over_constrain always; every "
                         "kind when stage2.inherit_parent_flow is true).")
    p2.add_argument("--inherit-parent", action="store_true",
                    help="replay --parent-flow's own Stage 1 flow (placer, opt directive, early "
                         "fixes) whatever stage2.inherit_parent_flow says: a planner fix "
                         "that names placers")
    p2.add_argument("--early-fix", action="append", default=[], metavar="KEY=0|1",
                    help="switch one early_fixes key for this run (repeatable); the early "
                         "fixes block is then included whatever the parent setting")
    p2.add_argument("--rqs-file", help="kind=rqs|stacked: the .rqs file to apply.")
    p2.add_argument("--rqs-category", action="append", default=[],
                    help="kind=rqs|stacked: only enable these RQS categories (repeatable).")
    p2.add_argument("--stack-xdc", action="append", default=[],
                    help="kind=stacked: XDC to `source` before opt_design (repeatable).")
    p2.add_argument("--stack-xdc-read", action="append", default=[],
                    help="kind=stacked: XDC to `read_xdc` before opt_design (repeatable).")
    p2.add_argument("--oc-value", type=float,
                    help="kind=over_constrain: uncertainty in ns (|WNS| x multiplier).")
    p2.add_argument("--oc-clock", action="append", default=[],
                    help="kind=over_constrain: clock name. Give twice for a "
                         "cross-clock pair (src then dst); once for same-clock.")
    p2.add_argument("--subdirective", help="kind=category_directive: place subdirective.")
    p2.add_argument("--directive-off-versal",
                    help="with --subdirective: the place directive a non-Versal part "
                         "runs instead (no placer sub-directives there).")
    p2.add_argument("--xdc", help="kind=xdc_fix: XDC to apply before opt_design.")
    p2.add_argument("--apply-mode", default="source", choices=["source", "read_xdc"],
                    help="kind=xdc_fix: how to apply the XDC (default: source).")
    p2.add_argument("--fix-tcl", help="kind=tcl_fix: the skill fix.tcl to source.")
    p2.add_argument("--parent-run-dir",
                    help="kind=tcl_fix: exported as PARENT_RUN_DIR (required -- "
                         "several fix.tcl scripts read it unguarded).")

    sub.add_parser("planner-evidence", parents=[common],
                   help="Planner evidence collection on the winner's routed DCP.")
    sub.add_parser("path-b-detect", parents=[common],
                   help="Path B: SLR pblock XDC from a placed Stage 1 checkpoint.")
    sub.add_parser("probe", parents=[common],
                   help="Open the input checkpoint with the pinned build (a few minutes).")

    p3 = sub.add_parser("stage3", parents=[common],
                        help="A Stage 3 last-mile run on the overall winner.")
    p3.add_argument("--kind", required=True, choices=["last_mile", "reroute"],
                    help="last_mile: --dcp is the winner's postroute_physopt.dcp; "
                         "reroute: --dcp is the winner's postplace_physopt.dcp.")
    p3.add_argument("--pre-route-cmd", action="append", default=[],
                    help="kind=reroute: TCL replayed before route_design (repeatable).")
    p3.add_argument("--route-directive", default="AggressiveExplore",
                    help="kind=reroute: route_design directive (default AggressiveExplore).")

    args = parser.parse_args()
    if not getattr(args, "mode", None):
        parser.error("a mode is required: stage1, detect, stage2 or stage3")
    config = load_config(args.config)
    for key, cmds in (("_post_opt_cmds", args.post_opt_cmd),
                      ("_post_place_cmds", args.post_place_cmd),
                      ("_post_popt_cmds", args.post_popt_cmd),
                      ("_post_route_cmds", args.post_route_cmd),
                      ("_post_route_popt_cmds", args.post_route_popt_cmd)):
        if cmds:
            config[key] = cmds
    if getattr(args, "design_checks", False):
        config['_design_checks'] = True
    if getattr(args, "inherit_parent", False):
        config['stage2'] = dict(config.get('stage2') or {}, inherit_parent_flow=True)
    if getattr(args, "early_fix", None):
        ef = dict(config.get('early_fixes') or {}, enabled=True)
        for item in args.early_fix:
            key, sep, val = item.partition("=")
            if not sep or val not in ("0", "1") or \
                    key not in ('mbufg_group', 'hardsync_ce', 'gt_mrmac_loc',
                                'pcie_clock_root', 'structural_analysis',
                                'hardip_proximity_pblock', 'prevent_hardip_slr_crossing'):
                parser.error("--early-fix wants KEY=0|1 for an early_fixes key, got %r" % item)
            ef[key] = val == "1"
        config['early_fixes'] = ef
        config['_force_early_fixes'] = True
    if getattr(args, "replay_run_dir", None):
        config['_parent_run_dir'] = os.path.abspath(args.replay_run_dir)
    if args.mode == "stage2":
        config['_route_directive_override'] = (config.get('stage2', {}) or {}).get(
            'route_directive_override')

    dcp = os.path.abspath(args.dcp)

    if args.mode == "stage1":
        content = generate_stage1(dcp, args.flow, config, args.extra_cmd,
                                  fix_xdcs=[(os.path.abspath(x), "source")
                                            for x in args.fix_xdc],
                                  run_name=args.run_name)

    elif args.mode == "detect":
        detections = [{"sub_problem": os.path.basename(os.path.dirname(d)),
                       "category": "", "detect_tcl": os.path.abspath(d)}
                      for d in args.detect]
        if args.detection_plan:
            import json
            with open(args.detection_plan, 'r', encoding='utf-8') as f:
                plan = json.load(f)
            stages = plan.get("detection_plan", {})
            if args.detect_stage:
                detections.extend(stages.get(args.detect_stage, []))
            else:
                for entries in stages.values():
                    detections.extend(entries)
        if not detections:
            parser.error("no detections: pass --detect and/or --detection-plan")
        content = generate_detect(dcp, detections,
                                  args.run_name or "DETECT")

    elif args.mode == "probe":
        content = generate_probe(dcp, config, args.run_name or "PROBE")

    elif args.mode == "path-b-detect":
        content = generate_path_b_detect(dcp, config, args.run_name or "R2D")

    elif args.mode == "planner-evidence":
        content = generate_planner_evidence(dcp, config, args.run_name or "PLANNER_EVIDENCE")

    elif args.mode == "stage3":
        name = args.run_name or ("S3_%s" % args.kind.upper())
        if args.kind == "last_mile":
            content = generate_stage3_last_mile(dcp, config, name, args.extra_cmd)
        else:
            content = generate_stage3_reroute(dcp, config, name, args.extra_cmd,
                                              args.pre_route_cmd, args.route_directive)

    else:  # stage2
        name = args.run_name or ("S2_%s" % args.kind.upper())
        # A winner's XDC chain for the kinds that re-place from the input DCP.
        chain = ([(os.path.abspath(x), "source") for x in args.stack_xdc] +
                 [(os.path.abspath(x), "read_xdc") for x in args.stack_xdc_read])
        if args.kind == "rqs":
            if not args.rqs_file:
                parser.error("--kind rqs requires --rqs-file")
            content = generate_stage2_rqs(dcp, args.rqs_file, config, name,
                                          args.parent_flow, args.extra_cmd,
                                          args.rqs_category or None, xdcs=chain)
        elif args.kind == "custom_flow":
            xdcs = ([(os.path.abspath(x), "source") for x in args.stack_xdc] +
                    [(os.path.abspath(x), "read_xdc") for x in args.stack_xdc_read])
            content = generate_stage2_custom_flow(
                dcp, config, name, args.extra_cmd, args.parent_flow,
                opt=args.opt_directive, place=args.place_directive,
                place_subdirective=args.place_subdirective,
                physopt=args.physopt_directive, route=args.route_directive, xdcs=xdcs)
        elif args.kind == "incremental":
            xdcs = [(os.path.abspath(x), "source") for x in args.stack_xdc]
            if not (args.path_group or args.incr_physopt or args.route_directive):
                parser.error("--kind incremental needs --path-group, --incr-physopt "
                             "or --route-directive")
            content = generate_planner_incremental(
                dcp, config, name, args.extra_cmd, xdcs, args.path_group,
                args.incr_physopt, args.route_directive, args.passes)
        elif args.kind == "stacked":
            xdcs = ([(os.path.abspath(x), "source") for x in args.stack_xdc] +
                    [(os.path.abspath(x), "read_xdc") for x in args.stack_xdc_read])
            if not xdcs and not args.subdirective and not args.rqs_file:
                parser.error("--kind stacked needs at least one of --stack-xdc, "
                             "--stack-xdc-read, --subdirective, --rqs-file")
            clock_info = None
            if args.oc_value is not None:
                src = args.oc_clock[0] if args.oc_clock else "*"
                dst = args.oc_clock[1] if len(args.oc_clock) > 1 else src
                clock_info = [{"src_clk": src, "dst_clk": dst, "oc_value": args.oc_value}]
            content = generate_stage2_stacked(dcp, config, name, xdcs,
                                              args.subdirective, args.rqs_file,
                                              args.rqs_category or None,
                                              args.extra_cmd, args.parent_flow,
                                              clock_info=clock_info,
                                              off_versal=args.directive_off_versal)
        elif args.kind == "over_constrain":
            if args.oc_value is None or not args.oc_clock:
                parser.error("--kind over_constrain requires --oc-value and --oc-clock")
            clocks = args.oc_clock
            src = clocks[0]
            dst = clocks[1] if len(clocks) > 1 else clocks[0]
            clock_info = [{"src_clk": src, "dst_clk": dst, "oc_value": args.oc_value}]
            content = generate_stage2_over_constrain(dcp, clock_info, config, name,
                                                     args.parent_flow, args.extra_cmd,
                                                     xdcs=chain)
        elif args.kind == "category_directive":
            if not args.subdirective:
                parser.error("--kind category_directive requires --subdirective")
            content = generate_stage2_category_directive(dcp, args.subdirective,
                                                         config, name, args.extra_cmd,
                                                         args.parent_flow,
                                                         args.directive_off_versal)
        elif args.kind == "xdc_fix":
            if not args.xdc:
                parser.error("--kind xdc_fix requires --xdc")
            content = generate_stage2_xdc_fix(dcp, os.path.abspath(args.xdc), config,
                                              name, args.apply_mode, args.extra_cmd,
                                              args.parent_flow)
        else:  # tcl_fix
            if not args.fix_tcl or not args.parent_run_dir:
                parser.error("--kind tcl_fix requires --fix-tcl and --parent-run-dir")
            content = generate_stage2_tcl_fix(dcp, os.path.abspath(args.fix_tcl),
                                              os.path.abspath(args.parent_run_dir),
                                              config, name, args.extra_cmd,
                                              args.parent_flow)

    write_run_tcl(args.run_dir, content, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
