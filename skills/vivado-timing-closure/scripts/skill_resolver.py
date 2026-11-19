#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
skill_resolver.py -- the "Arbitrator Agent": turn a classification into a Stage 2 run plan.

Standalone and stdlib-only: no third-party packages, no network access.

Selects, prioritizes and combines skill fixes for the categories the classifier
detected, with the same run set as upstream's parallel fix runs
(flows/parallel_fix.py): per category an RQS-only run, one run per skill fix
and a combined run. Which of them launch is settled by stage2-generate after
detection, from the flags and artifacts it wrote.

WHY A CURATED CATALOG INSTEAD OF PURE DIRECTORY SCANNING
--------------------------------------------------------
Inferring a skill's fix from directory layout alone is unreliable, because:
  * several fix.tcl files are comment-only placeholders (the real artifact is an
    XDC that detect.tcl writes into the run dir);
  * some XDC names don't match their skill name
    (gt_mrmac_crossing writes mrmac_slr_suggestions.xdc);
  * one fix.tcl only works in project mode
    (partition/unbalanced_util does set_property ... [get_runs impl_1]);
  * one skill's detect.tcl depends on another's flag file
    (congestion/with_slr_imbalance needs congestion_only_detected.flag);
  * one detect.tcl applies its fix live and writes no file at all
    (early_resolution/pcie_clock_root).
SKILL_CATALOG below encodes those facts explicitly so the plan is correct rather
than merely plausible. Directory scanning is still used to confirm each script
actually exists before it is planned. When adding a skill, add a catalog entry.

Usage:
    skill_resolver.py --classification classification.json \
        --best-run stage1_best.json \
        --skills-config config/skills_config.yaml --skills-dir skills/

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yaml_lite import load_yaml  # noqa: E402


# ---------------------------------------------------------------------------
# Per-bucket run caps, from the Stage 2 slide.
# ---------------------------------------------------------------------------
BUCKET_CAPS = {
    "S2_CLOCK_SKILLS":  {"skills": 3, "combined": 1},
    "S2_CONG_SKILLS":   {"skills": 2, "combined": 1},
    # 3, not the slide's 2: gt_clock_pblocks (upstream PR #742) is the third
    # Timing skill, and the cap is taken in catalog order before detection runs,
    # so with 2 it could never be planned. A skill whose detector writes no
    # artifact is still dropped before launch.
    "S2_TIMING_SKILLS": {"skills": 3, "combined": 1},
    "S2_PARTN_SKILLS":  {"skills": 3, "combined": 0},
    "S2_COMB_SKILLS":   {"skills": 0, "combined": 4},
}

CATEGORY_BUCKET = {
    "Clocking":    "S2_CLOCK_SKILLS",
    "Congestion":  "S2_CONG_SKILLS",
    "Timing":      "S2_TIMING_SKILLS",
    "Partition":   "S2_PARTN_SKILLS",
    "Utilization": "S2_COMB_SKILLS",
}

# Short, readable prefixes for generated run names (was category.lower()[:5],
# which produced "timin_"/"clock_"/"conge_"/"parti_"/"utili_").
CATEGORY_SHORT = {
    "Clocking": "clock",
    "Congestion": "cong",
    "Timing": "timing",
    "Partition": "partn",
    "Utilization": "util",
}


def short_name(category):
    return CATEGORY_SHORT.get(category, category.lower()[:6])


# RQS CATEGORY filter value per detected category, as upstream's parallel fix
# runs (flows/parallel_fix.py rqs_category_map): Partition's suggestions are the
# Timing ones (RQS_TIMING-100, USER_CROSSING_SLR).
RQS_CATEGORY = {
    "Clocking": "Clocking",
    "Congestion": "Congestion",
    "Timing": "Timing",
    "Partition": "Timing",
    "Utilization": "Utilization",
}

# report_parser.parse_rqs_counts key per category (upstream _category_to_rqs_key).
RQS_COUNT_KEY = {
    "Partition": "TIMING",
    "Congestion": "CONG",
    "Clocking": "CLOCK",
    "Timing": "TIMING",
    "Utilization": "UTIL",
}


def use_rqs_for(category, rqs_file, rqs_counts, partition_rqs=False):
    """
    Upstream's use_rqs (parallel_fix.py): the parent has a .rqs file, the
    category has suggestions at post-route phys_opt, and Partition only when
    enable_partition_rqs is set.
    """
    if not rqs_file:
        return False
    if (rqs_counts or {}).get(RQS_COUNT_KEY.get(category, category.upper()), 0) <= 0:
        return False
    return category != "Partition" or partition_rqs


# ---------------------------------------------------------------------------
# Skill catalog.
#
#   detect          : path under skills/ to source (env RUN_DIR must be set first)
#   detect_stage    : design state detect.tcl needs -- one of
#                     pre_opt | pre_place | post_place | post_route
#   flag            : file detect.tcl writes into RUN_DIR when it fires
#   fix_type        : xdc      -> source/read the named XDC in a fresh run
#                     directive-> replay parent flow with a place subdirective
#                     live     -> detect.tcl already mutated the design in-session
#                     none     -> detection only, no automated fix available
#   fix_artifact    : XDC filename detect.tcl produces (fix_type == xdc)
#   fix_apply       : "source" or "read_xdc"
#   directives_yaml : per-sub-problem directives.yaml (fix_type == directive)
#   requires_flag   : another skill's flag that must exist first
#   stage1_applied  : already applied by early_flow_fix.tcl during Stage 1
#   note            : why anything unusual is true
# ---------------------------------------------------------------------------
SKILL_CATALOG = [
    # ---------------- Clocking ----------------
    {
        "category": "Clocking", "sub_problem": "mbufg_group",
        "detect": "clocking/mbufg_group/detect.tcl",
        "detect_stage": "pre_opt",
        "flag": "mbufg_group_detected.flag",
        "fix_type": "xdc", "fix_artifact": "mbufg_group_fix.xdc",
        "fix_apply": "source",
        "stage1_applied": True,
        "description": "Group BUFG_GTs that share a driver via MBUFG_GROUP.",
        "note": "Must be applied before opt_design (opt_design converts BUFG_GT to "
                "MBUFGT). Already applied in Stage 1 by early_flow_fix.tcl.",
    },
    {
        "category": "Clocking", "sub_problem": "hardsync_ce",
        "detect": "clocking/hardsync_ce/detect.tcl",
        "detect_stage": "pre_opt",
        "flag": "hardsync_ce_detected.flag",
        "fix_type": "xdc", "fix_artifact": "hardsync_ce_fix.xdc",
        "fix_apply": "source",
        "stage1_applied": True,
        "description": "Set CE_TYPE HARDSYNC on BUFGCEs fed from the same MMCM.",
        "note": "Must be applied before opt_design. Already applied in Stage 1 by "
                "early_flow_fix.tcl.",
    },

    # ---------------- Congestion ----------------
    {
        "category": "Congestion", "sub_problem": "congestion_only",
        "detect": "congestion/congestion_only/detect.tcl",
        "detect_stage": "post_place",
        "flag": "congestion_only_detected.flag",
        "fix_type": "directive",
        "directives_yaml": "congestion/congestion_only/directives.yaml",
        "description": "ReduceCongestion + ForceSpreading placement subdirective.",
        "note": "detect.tcl writes its flag unconditionally -- it is only meant to be "
                "sourced once congestion is already confirmed by classification.",
    },
    {
        "category": "Congestion", "sub_problem": "with_slr_imbalance",
        "detect": "congestion/with_slr_imbalance/detect.tcl",
        "detect_stage": "post_place",
        "flag": "with_slr_imbalance_detected.flag",
        "fix_type": "directive",
        "directives_yaml": "congestion/with_slr_imbalance/directives.yaml",
        "requires_flag": "congestion_only_detected.flag",
        "description": "BalancedSLR + ReduceCongestion combined subdirective.",
        "note": "detect.tcl returns immediately unless congestion_only_detected.flag "
                "already exists in RUN_DIR -- source congestion_only first.",
    },
    {
        "category": "Congestion", "sub_problem": "control_set_remap",
        "detect": "congestion/control_set_remap/detect.tcl",
        "detect_stage": "pre_place",
        "flag": "control_set_remap_detected.flag",
        "fix_type": "xdc", "fix_artifact": "control_set_remap_fix.xdc",
        "fix_apply": "source",
        "description": "Remap low-fanout control sets to cut control-set pressure.",
        "note": "detect.tcl sets env(OUTDIR) and delegates to "
                "early_resolution/control_set_analysis. fix.tcl is a placeholder; the "
                "real artifact is control_set_remap_fix.xdc.",
    },
    {
        "category": "Congestion", "sub_problem": "high_fanout",
        "detect": "congestion/high_fanout/detect.tcl",
        "detect_stage": "post_place",
        "flag": "high_fanout_detected.flag",
        "fix_type": "live", "fix_tcl_inline": "phys_opt_design -fanout_opt",
        "description": "Replicate/optimize high-fanout nets on failing paths.",
        "note": "fix.tcl runs phys_opt_design -fanout_opt directly -- it mutates the "
                "design rather than producing an XDC.",
    },
    {
        "category": "Congestion", "sub_problem": "macro_placement",
        "detect": "congestion/macro_placement/detect.tcl",
        "detect_stage": "post_route",
        "flag": "macro_placement_detected.flag",
        "fix_type": "none",
        "requires_report": "%s_design_analysis.rpt",
        "description": "Detect clock regions packed with >50 BRAM/DSP macros.",
        "note": "Detection only -- fix.tcl is a comment placeholder. Its finding feeds "
                "the congestion placement subdirectives instead.",
    },
    {
        "category": "Congestion", "sub_problem": "clock_region_density",
        "detect": "congestion/clock_region_density/detect.tcl",
        "detect_stage": "post_place",
        "flag": "clock_region_density_detected.flag",
        "fix_type": "none",
        "description": "Detect clock regions above 90% SLICE utilization.",
        "note": "Detection only -- fix.tcl is a comment placeholder. Slow on large "
                "devices (two get_sites calls per clock region).",
    },

    # ---------------- Partition ----------------
    {
        "category": "Partition", "sub_problem": "slr_crossing",
        "detect": "partition/slr_crossing/detect.tcl",
        "detect_stage": "post_route",
        "flag": "slr_crossing_detected.flag",
        "fix_type": "xdc", "fix_artifact": "suggested_slr_pblock_aware.xdc",
        # source, not read_xdc: every constraint is wrapped in `catch { }`, which
        # read_xdc rejects (Designutils 20-1307), dropping all of them (Barco
        # rerun 2026-09-29: the run placed identically to its parent). The
        # generated file's own header says to source it.
        "fix_apply": "source",
        "generator_tcl": "partition/slr_crossing/auto_suggest_slr_pblock_aware.tcl",
        "generator_needs_cd": True,
        "description": "SLR-aware pblock assignment for critical paths crossing SLRs.",
        "note": "detect.tcl only writes a comment-only XDC. The real constraints come "
                "from auto_suggest_slr_pblock_aware.tcl, which takes no env vars -- cd "
                "into RUN_DIR before sourcing it.",
    },
    {
        "category": "Partition", "sub_problem": "gt_mrmac_crossing",
        "detect": "partition/gt_mrmac_crossing/detect.tcl",
        "detect_stage": "post_place",
        "flag": "gt_mrmac_crossing_detected.flag",
        "fix_type": "xdc", "fix_artifact": "mrmac_slr_suggestions.xdc",
        "fix_apply": "source",
        "description": "LOC MRMAC/DCMAC blocks into the SLR of their GT.",
        "note": "Writes mrmac_slr_suggestions.xdc (NOT the filename its header "
                "comment claims). Must be sourced, not pasted -- it uses [info script].",
    },
    {
        "category": "Partition", "sub_problem": "unbalanced_util",
        "detect": "partition/unbalanced_util/detect.tcl",
        "detect_stage": "post_place",
        "flag": "unbalanced_util_detected.flag",
        "fix_type": "directive",
        "directives_yaml": "partition/unbalanced_util/directives.yaml",
        "description": "BalancedSLR placement when SLR utilization differs by >30%.",
        "note": "Its fix.tcl is NOT usable here -- it does set_property on "
                "[get_runs impl_1], which requires project mode. The directives.yaml "
                "subdirective is the checkpoint-mode equivalent.",
    },

    # ---------------- Timing ----------------
    {
        "category": "Timing", "sub_problem": "bli_io",
        "detect": "timing/bli_io/detect.tcl",
        "detect_stage": "post_route",
        "flag": "bli_io_detected.flag",
        "fix_type": "xdc", "fix_artifact": "bli_io_fix.xdc",
        "fix_apply": "source",
        "description": "Set BLI TRUE on flops driving critical output ports.",
        "note": "Detect after post-route physopt, apply before place_design. fix.tcl "
                "is a placeholder; the artifact is bli_io_fix.xdc.",
    },
    {
        "category": "Timing", "sub_problem": "cew_limitation",
        "detect": "timing/cew_limitation/detect.tcl",
        "detect_stage": "post_route",
        "flag": "cew_limitation_detected.flag",
        "fix_type": "xdc", "fix_artifact": "cew_timing_report.xdc",
        "fix_apply": "source",
        "description": "Widen USER_CLOCK_EXPANSION_WINDOW on limiting clock nets.",
        "note": "Needs clock routing, so post-route only. Always writes "
                "cew_timing_report.rpt; the .xdc only appears when fixes exist.",
    },
    {
        "category": "Timing", "sub_problem": "gt_clock_pblocks",
        "detect": "timing/gt_clock_pblocks/detect.tcl",
        "detect_stage": "post_place",
        "flag": "gt_clock_pblocks_detected.flag",
        "fix_type": "xdc", "fix_artifact": "gt_clock_pblocks/fix.tcl",
        "fix_apply": "source",
        "description": "Compact a violating GT clock's loads into a pblock near the GT.",
        "note": "Upstream PR #742. Needs a placed design; only GT clocks with "
                "negative slack are candidates. The artifact is the generated "
                "gt_clock_pblocks/fix.tcl (pblock commands), not the placeholder "
                "fix.tcl in assets/.",
    },
    {
        "category": "Timing", "sub_problem": "structural_analysis",
        "detect": "timing/structural_analysis/detect.tcl",
        "detect_stage": "pre_place",
        "flag": "structural_analysis_detected.flag",
        "fix_type": "xdc", "fix_artifact": "structural_analysis_fix.xdc",
        "fix_apply": "source",
        "stage1_applied": True,
        # Applied in Stage 1 only when early_fixes.structural_analysis is on
        # (default off). Otherwise it is a Stage 2 fix like any other, as
        # upstream's apps_skills: its PFR runs it whenever the detector fires.
        "early_fix": "structural_analysis",
        "description": "Bundle: macro clustering, fanout, retiming, SRL, BRAM/URAM unpack.",
        "note": "Aggregates five early_resolution sub-analyses into one XDC. Gated in "
                "Stage 1 by early_fixes.structural_analysis (default off).",
    },
]


def catalog_for(category):
    """Catalog entries for one category, in declaration order."""
    return [s for s in SKILL_CATALOG if s["category"] == category]


# ---------------------------------------------------------------------------
# skills_config.yaml evaluation (from skills/skill_loader.py)
# ---------------------------------------------------------------------------
def should_trigger(trigger, metrics):
    """Evaluate a skills_config.yaml `trigger:` block against measured metrics."""
    if not trigger:
        return False

    metric_name = trigger.get("metric", "")
    operator = trigger.get("operator", "")
    threshold = trigger.get("value")

    actual = metrics.get(metric_name)
    if actual is None or threshold is None:
        return False

    try:
        if operator == ">=":
            return actual >= threshold
        if operator == "<=":
            return actual <= threshold
        if operator == ">":
            return actual > threshold
        if operator == "<":
            return actual < threshold
        if operator == "==":
            return bool(actual) == bool(threshold) if isinstance(threshold, bool) \
                else actual == threshold
    except TypeError:
        return False

    return False


def conditions_match(condition_spec, conditions):
    """
    Match a directives.yaml `condition:` block against the classifier's booleans.
    Every key in the condition block must match for the fix to apply.
    """
    if not condition_spec:
        return False
    for key, required in condition_spec.items():
        actual = conditions.get(key)
        if actual is None:
            return False
        if isinstance(required, str):
            required = required.lower() in ('true', 'yes', '1')
        if bool(actual) != bool(required):
            return False
    return True


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------
def build_skill_runs(category, cat_cfg, classification, skills_dir, parent_run_dir,
                     stage, include_stage1_applied, skip_sub_problems=(),
                     cap_skills=False, early_fixes_on=()):
    """
    Build the candidate fix runs for one detected category, honouring its cap.
    skip_sub_problems: catalog names never to plan (config detection.skip_sub_problems).
    """
    bucket = CATEGORY_BUCKET.get(category, "S2_COMB_SKILLS")
    caps = BUCKET_CAPS[bucket]
    conditions = classification.get("conditions", {})

    candidates = []
    skipped = []

    for entry in catalog_for(category):
        sub = entry["sub_problem"]
        detect_path = os.path.join(skills_dir, entry["detect"])

        if sub in skip_sub_problems:
            skipped.append({"sub_problem": sub,
                            "reason": "skipped by config (detection.skip_sub_problems)"})
            continue

        if not os.path.isfile(detect_path):
            skipped.append({"sub_problem": sub,
                            "reason": "detect.tcl not found: %s" % detect_path})
            continue

        if entry["fix_type"] == "none":
            skipped.append({"sub_problem": sub,
                            "reason": "detection only, no automated fix available"})
            continue

        stage1_applied = entry.get("stage1_applied") and (
            not entry.get("early_fix") or entry["early_fix"] in early_fixes_on)
        if stage1_applied and not include_stage1_applied:
            skipped.append({
                "sub_problem": sub,
                "reason": "already applied during Stage 1 by early_flow_fix.tcl; "
                          "re-run with --include-stage1-applied to force"})
            continue

        # Directive fixes must match their directives.yaml condition block.
        if entry["fix_type"] == "directive":
            dy_path = os.path.join(skills_dir, entry["directives_yaml"])
            if not os.path.isfile(dy_path):
                skipped.append({"sub_problem": sub,
                                "reason": "directives.yaml not found: %s" % dy_path})
                continue
            try:
                directives = load_yaml(dy_path)
            except Exception as exc:
                skipped.append({"sub_problem": sub,
                                "reason": "directives.yaml unparsable: %s" % exc})
                continue

            # As upstream (parallel_fix._collect_skill_fixes): a directive fix
            # counts when its detect.tcl wrote its flag, which stage2-generate
            # checks after detection. The classifier's condition is recorded
            # for the report, not used as a gate.
            condition = directives.get("condition", {})
            place = directives.get("place", {}) or {}
            subdirective = place.get("subdirective", "")
            if not subdirective:
                skipped.append({"sub_problem": sub,
                                "reason": "directives.yaml has no place.subdirective"})
                continue

            candidates.append({
                "bucket": bucket,
                "kind": "category_directive",
                "category": category,
                "sub_problem": sub,
                "run_name": directives.get("run_name", sub),
                "methodology": directives.get("methodology", "%s/%s" % (category, sub)),
                "detect_tcl": detect_path,
                "detect_stage": entry["detect_stage"],
                "flag": entry.get("flag", ""),
                "requires_flag": entry.get("requires_flag", ""),
                # directive: what places instead off Versal (no sub-directives there).
                "fix": {"type": "directive", "subdirective": subdirective,
                        "directive": place.get("directive", "")},
                "condition_met": conditions_match(condition, conditions)
                                 if condition else None,
                "description": entry.get("description", ""),
                "note": entry.get("note", ""),
            })
            continue

        # XDC fixes: detect.tcl writes an artifact we then source in a fresh run.
        if entry["fix_type"] == "xdc":
            fix = {
                "type": "xdc",
                "artifact": entry["fix_artifact"],
                "apply": entry.get("fix_apply", "source"),
                # Absolute path only once detection has actually run.
                "path": os.path.join(parent_run_dir, entry["fix_artifact"])
                        if parent_run_dir else "",
                "exists": bool(parent_run_dir) and os.path.isfile(
                    os.path.join(parent_run_dir, entry["fix_artifact"])),
            }
            if entry.get("generator_tcl"):
                fix["generator_tcl"] = os.path.join(skills_dir, entry["generator_tcl"])
                fix["generator_needs_cd"] = entry.get("generator_needs_cd", False)
        elif entry["fix_type"] == "live":
            fix = {"type": "live", "tcl": entry.get("fix_tcl_inline", "")}
        else:
            skipped.append({"sub_problem": sub,
                            "reason": "unhandled fix_type %s" % entry["fix_type"]})
            continue

        candidates.append({
            "bucket": bucket,
            "kind": "category_tcl_fix",
            "category": category,
            "sub_problem": sub,
            "run_name": "%s_%s" % (short_name(category), sub),
            "methodology": "%s skill: %s" % (category, entry.get("description", sub)),
            "detect_tcl": detect_path,
            "detect_stage": entry["detect_stage"],
            "flag": entry.get("flag", ""),
            "requires_flag": entry.get("requires_flag", ""),
            "fix": fix,
            "description": entry.get("description", ""),
            "note": entry.get("note", ""),
        })

    # No cap: upstream launches one run per actionable fix. A skill whose
    # detection writes no flag or artifact is dropped by stage2-generate.
    # The BUCKET_CAPS skill caps apply only with --cap-skills.
    if cap_skills:
        cap = caps["skills"]
        planned = candidates[:cap]
        for extra in candidates[cap:]:
            skipped.append({"sub_problem": extra["sub_problem"],
                            "reason": "bucket %s capped at %d skill run(s)" % (bucket, cap)})
    else:
        planned = candidates

    return planned, skipped


def build_rqs_category_run(category, rqs_file, rqs_counts, partition_rqs=False):
    """Upstream's <Cat>_RQS run: only this category's RQS suggestions."""
    if not use_rqs_for(category, rqs_file, rqs_counts, partition_rqs):
        return None
    rqs_cat = RQS_CATEGORY.get(category, category)
    return {
        "bucket": CATEGORY_BUCKET.get(category, "S2_COMB_SKILLS"),
        "kind": "rqs_category",
        "category": category,
        "sub_problem": "rqs",
        "run_name": "%s_rqs" % short_name(category),
        "methodology": "RQS %s fix only" % rqs_cat,
        "fix": {"type": "rqs", "rqs_file": rqs_file, "rqs_category": rqs_cat},
        "description": "Apply only the %s-filtered RQS suggestions." % rqs_cat,
    }


def build_combined_run(category, planned, parent_run_dir, rqs_file, rqs_counts=None,
                       partition_rqs=False):
    """
    Upstream's <Cat>_Combined run: the category's skill fixes, plus its RQS
    suggestions when it has any. Planned whenever the category has a skill to
    combine; stage2-generate launches it only if, after detection, there is at
    least one actionable fix AND (RQS for the category OR 2+ fixes) --
    parallel_fix.py `if all_fixes and (use_rqs or len(all_fixes) > 1)`.
    """
    if not planned:
        return None
    use_rqs = use_rqs_for(category, rqs_file, rqs_counts, partition_rqs)
    rqs_cat = RQS_CATEGORY.get(category, category)
    return {
        "bucket": CATEGORY_BUCKET.get(category, "S2_COMB_SKILLS"),
        "kind": "combined",
        "category": category,
        "sub_problem": "combined",
        "run_name": "%s_combined" % short_name(category),
        "methodology": "%s combined fix (skills%s)" % (
            category, " + RQS %s" % rqs_cat if use_rqs else ""),
        "fix": {
            "type": "combined",
            "use_rqs": use_rqs,
            "rqs_file": rqs_file if use_rqs else "",
            "rqs_category": rqs_cat if use_rqs else "",
            "components": [p["run_name"] for p in planned],
        },
        "description": "Apply every actionable %s skill fix together%s." % (
            category, " with the %s-filtered RQS suggestions" % rqs_cat if use_rqs else ""),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Resolve a classification into a capped Stage 2 fix plan.")
    parser.add_argument("--classification", required=True,
                        help="design_analyzer.py JSON. Use '-' for stdin.")
    parser.add_argument("--best-run",
                        help="decision_engine.py JSON for the Stage 1 winner "
                             "(supplies the parent run dir, RQS file and WNS).")
    parser.add_argument("--skills-config", help="config/skills_config.yaml")
    parser.add_argument("--skills-dir", help="skills/ directory (default: ../skills)")
    parser.add_argument("--stage", default="postroute_physopt",
                        help="Report stage prefix the classification came from.")
    parser.add_argument("--include-stage1-applied", action="store_true",
                        help="Also plan skills already applied by Stage 1's "
                             "early_flow_fix.tcl (normally skipped as redundant).")
    parser.add_argument("--skip-sub-problem", action="append", default=[], metavar="SUB",
                        help="Never plan this catalog sub-problem (repeatable; config "
                             "detection.skip_sub_problems).")
    parser.add_argument("--enable-partition-rqs", action="store_true",
                        help="Use RQS for Partition (upstream "
                             "parallel_fix.enable_partition_rqs; default off).")
    parser.add_argument("--early-fix-on", action="append", default=[], metavar="KEY",
                        help="An early_fixes key Stage 1 applied (repeatable). A catalog "
                             "skill tied to an early fix that is off is planned in Stage 2.")
    parser.add_argument("--cap-skills", action="store_true",
                        help="Apply the per-bucket skill caps (BUCKET_CAPS). Off by "
                             "default: upstream runs every actionable fix.")
    parser.add_argument("-o", "--output", help="Write JSON here instead of stdout.")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    skills_dir = os.path.abspath(
        args.skills_dir or os.path.join(os.environ.get("TCA_ASSETS_ROOT") or
                                        os.path.join(here, os.pardir, "assets"), "skills"))
    skills_config_path = args.skills_config or os.path.join(
        here, os.pardir, "config", "skills_config.yaml")

    if args.classification == "-":
        classification = json.load(sys.stdin)
    else:
        with open(args.classification, 'r', encoding='utf-8') as f:
            classification = json.load(f)

    best = {}
    if args.best_run:
        with open(args.best_run, 'r', encoding='utf-8') as f:
            best = json.load(f)

    parent_run_dir = best.get("best_run_dir", "") or classification.get("run_dir", "")
    rqs_file = best.get("rqs_file", "")
    rqs_counts = best.get("rqs_counts")
    if rqs_counts is None:
        # A stage1_best.json from before rqs_counts was recorded: assume every
        # category has suggestions when there is a file (the old behaviour).
        rqs_counts = dict((k, 1) for k in RQS_COUNT_KEY.values()) if rqs_file else {}
    partition_rqs = args.enable_partition_rqs

    skills_cfg = {}
    if os.path.isfile(skills_config_path):
        try:
            skills_cfg = load_yaml(skills_config_path).get("skills", {}) or {}
        except Exception as exc:
            print("WARNING: could not parse %s: %s" % (skills_config_path, exc),
                  file=sys.stderr)

    metrics = classification.get("metrics", {})
    conditions = classification.get("conditions", {})
    detected = [c["category"] for c in classification.get("detected_problems", [])]

    # Order categories by the priority declared in skills_config.yaml.
    def priority(cat):
        return (skills_cfg.get(cat.lower(), {}) or {}).get("priority", 99)

    ordered = sorted(set(detected), key=priority)

    runs = []
    skipped = []
    category_reports = []

    for category in ordered:
        cat_cfg = skills_cfg.get(category.lower(), {}) or {}

        if not cat_cfg.get("enabled", True):
            skipped.append({"category": category,
                            "reason": "disabled in skills_config.yaml"})
            continue

        # The checker's detection decides, as upstream's parallel fix runs
        # (which never read skills_config.yaml triggers). The numeric trigger
        # is recorded for the report only.
        trigger = cat_cfg.get("trigger", {}) or {}
        triggered = should_trigger(trigger, metrics) if trigger else None

        planned, cat_skipped = build_skill_runs(
            category, cat_cfg, classification, skills_dir, parent_run_dir,
            args.stage, args.include_stage1_applied, set(args.skip_sub_problem),
            cap_skills=args.cap_skills, early_fixes_on=set(args.early_fix_on))

        # Upstream order per category: <Cat>_RQS, one run per fix, <Cat>_Combined.
        rqs_run = build_rqs_category_run(category, rqs_file, rqs_counts, partition_rqs)
        if rqs_run:
            runs.append(rqs_run)
        runs.extend(planned)
        for s in cat_skipped:
            s["category"] = category
            skipped.append(s)

        combined = build_combined_run(category, planned, parent_run_dir, rqs_file,
                                      rqs_counts, partition_rqs)
        if combined:
            runs.append(combined)

        category_reports.append({
            "category": category,
            "priority": priority(category),
            "bucket": CATEGORY_BUCKET.get(category, "S2_COMB_SKILLS"),
            "trigger_fired": triggered,
            "use_rqs": use_rqs_for(category, rqs_file, rqs_counts, partition_rqs),
            "planned_runs": len(planned) + (1 if combined else 0) + (1 if rqs_run else 0),
        })

    # Detection sequencing: some detect.tcl scripts need a prior skill's flag,
    # and each needs a specific design state. Group them so the agent can run
    # one detection session per state instead of one per skill.
    #
    # A prerequisite skill must be detected even when it is NOT itself a planned
    # run -- e.g. congestion/with_slr_imbalance returns immediately unless
    # congestion_only_detected.flag already exists, but congestion_only's own
    # directive condition may not have matched. Pull those in explicitly.
    flag_producer = dict(
        (e["flag"], e) for e in SKILL_CATALOG if e.get("flag"))

    def detect_entry(catalog_entry, purpose):
        return {
            "category": catalog_entry["category"],
            "sub_problem": catalog_entry["sub_problem"],
            "detect_tcl": os.path.join(skills_dir, catalog_entry["detect"]),
            "flag": catalog_entry.get("flag", ""),
            "requires_flag": catalog_entry.get("requires_flag", ""),
            "purpose": purpose,
        }

    detection_plan = {}
    seen_detections = set()

    def add_detection(catalog_entry, purpose):
        """Add a detection step, recursing into its flag prerequisites first."""
        key = (catalog_entry["category"], catalog_entry["sub_problem"])
        if key in seen_detections:
            return
        seen_detections.add(key)
        if catalog_entry["sub_problem"] in args.skip_sub_problem:
            skipped.append({
                "category": catalog_entry["category"],
                "sub_problem": catalog_entry["sub_problem"],
                "reason": "detection skipped by config (detection.skip_sub_problems)"})
            return

        prereq_flag = catalog_entry.get("requires_flag")
        if prereq_flag and prereq_flag in flag_producer:
            add_detection(flag_producer[prereq_flag], "prerequisite")

        detect_path = os.path.join(skills_dir, catalog_entry["detect"])
        if not os.path.isfile(detect_path):
            skipped.append({
                "category": catalog_entry["category"],
                "sub_problem": catalog_entry["sub_problem"],
                "reason": "detection skipped, detect.tcl missing: %s" % detect_path})
            return

        stage_needed = catalog_entry["detect_stage"]
        detection_plan.setdefault(stage_needed, []).append(
            detect_entry(catalog_entry, purpose))

    catalog_index = dict(
        ((e["category"], e["sub_problem"]), e) for e in SKILL_CATALOG)

    for run in runs:
        key = (run.get("category"), run.get("sub_problem"))
        entry = catalog_index.get(key)
        if not entry:
            continue  # combined runs have no detect.tcl of their own
        add_detection(entry, "planned")

        gen = run.get("fix", {}).get("generator_tcl")
        if gen:
            for e in detection_plan.get(entry["detect_stage"], []):
                if e["sub_problem"] == run["sub_problem"]:
                    e["generator_tcl"] = gen
                    e["generator_needs_cd"] = run["fix"].get(
                        "generator_needs_cd", False)

    # Within a state, a skill that needs another's flag must run after it.
    for stage_needed, entries in detection_plan.items():
        order = dict((e["flag"], i) for i, e in enumerate(entries) if e["flag"])
        entries.sort(key=lambda e: order.get(e.get("requires_flag"), -1))

    plan = {
        "parent_run_dir": parent_run_dir,
        "parent_run_id": best.get("best_run_id", ""),
        "parent_wns": best.get("wns"),
        "rqs_file": rqs_file,
        "rqs_counts": rqs_counts,
        "stage": args.stage,
        "detected_categories": ordered,
        "conditions": conditions,
        "metrics": metrics,
        "categories": category_reports,
        "detection_plan": detection_plan,
        "runs": runs,
        "skipped": skipped,
        "total_skill_runs": len(runs),
        "bucket_caps": BUCKET_CAPS if args.cap_skills else None,
        # RQS filter categories with suggestions, per detected category: what
        # the global all-categories combined run may add (upstream
        # _launch_all_categories_combined).
        "rqs_use": dict((c, RQS_CATEGORY.get(c, c)) for c in ordered
                        if use_rqs_for(c, rqs_file, rqs_counts, partition_rqs)),
    }

    # Fixed-shape branches are decided by decision_engine.py's thresholds, echoed
    # here so a single call yields the complete run list.
    triggers = best.get("triggers", {})
    fixed = []
    if triggers.get("over_constrain"):
        for mult in triggers.get("oc_multipliers", []):
            fixed.append({
                "bucket": "S2_OC",
                "kind": "over_constrain",
                "run_name": "oc_%sx" % mult,
                "methodology": "Over-Constrain %sx|WNS| on %s" % (
                    mult, best.get("best_run_id", "parent")),
                "multiplier": mult,
            })
    if rqs_file:
        fixed.append({
            "bucket": "S2_RQS",
            "kind": "rqs",
            "run_name": "rqs",
            "methodology": "Apply RQS suggestions from %s" % best.get(
                "best_run_id", "parent"),
            "rqs_file": rqs_file,
        })
    plan["fixed_runs"] = fixed
    plan["total_runs"] = len(runs) + len(fixed)

    text = json.dumps(plan, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + "\n")
        print("Planned %d run(s): %d skill-based + %d fixed-shape" % (
            plan["total_runs"], len(runs), len(fixed)), file=sys.stderr)
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
