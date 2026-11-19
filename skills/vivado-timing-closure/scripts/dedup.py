#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
dedup.py -- recognise a run that would only replay one that already ran.

Vivado implementation is deterministic: the same checkpoint, the same commands
and the same enabled QoR suggestions give a bit-identical result (seen on
design B: Stage 3 `reroute` == planner fix 4 and Stage 3 `rqs` == planner fix 3,
~6 h of LSF slot time). This module answers two questions from files alone:

  run_fingerprint(text)       -- what a run.tcl actually does, with banners,
                                 messages and the run name removed, and the
                                 applied .rqs file reduced to the suggestions
                                 that would really be enabled.
  pick_reroute_directive(...) -- a route_design directive the winner's placement
                                 has not been routed with yet.

Stdlib only; never runs Vivado. Requires Python 3.6+.
"""

from __future__ import print_function

import glob
import hashlib
import json
import os
import re

import report_parser

RQS_BLOCK_START = "# --- Apply RQS suggestions"
RQS_BLOCK_END = 'puts "INFO: Disabled excluded suggestion RQS_TIMING-100"'
RQS_EXCLUDED_IDS = ("RQS_TIMING-100",)
IMPL_STEPS = ("opt_design", "place_design", "route_design")
SENTINEL_NAME = "run.done"

_NAME_RX = re.compile(r"^RQS_[A-Z0-9_]+-\d+-\d+$")
_SECTION_RX = re.compile(r"^\s*(\d+)\.\s+QoR Suggestions")
_ROUTE_RX = re.compile(r"^\s*route_design\b(.*)$")
_DIRECTIVE_RX = re.compile(r"-directive\s+(\S+)")


# ---------------------------------------------------------------------------
# run.tcl text
# ---------------------------------------------------------------------------
def read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except (IOError, OSError):
        return ""


def split_rqs_block(text):
    """
    -> (rqs_file, categories, rest). categories is None when every category is
    enabled; rest is the script with the RQS block removed. No block: ("", None, text).
    """
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.strip().startswith(RQS_BLOCK_START):
            start = i
        elif start is not None and line.strip() == RQS_BLOCK_END:
            end = i + 1  # the closing brace of the exclusion loop
            break
    if start is None or end is None:
        return "", None, text
    block = lines[start:end + 1]
    m = re.search(r"category-based:\s*([^)]*?)\)?\s*---", block[0])
    cats = [c.strip() for c in m.group(1).split(",") if c.strip()] if m else None
    rqs = ""
    for line in block:
        m = re.match(r"\s*read_qor_suggestions\s+\{(.*)\}\s*$", line)
        if m:
            rqs = m.group(1)
            break
    return rqs, cats, "\n".join(lines[:start] + lines[end + 1:])


def _normalise(text):
    """Commands only: no comments, messages, blank lines, or the run.done sentinel."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("puts ") or SENTINEL_NAME in s:
            continue
        out.append(s)
    return "\n".join(out)


def route_directive_of(text):
    """Directive of the first route_design in a run.tcl ('Default' if none given)."""
    for line in text.splitlines():
        m = _ROUTE_RX.match(line)
        if m:
            d = _DIRECTIVE_RX.search(m.group(1))
            return d.group(1) if d else "Default"
    return None


# ---------------------------------------------------------------------------
# QoR suggestions: which ones a run would really enable
# ---------------------------------------------------------------------------
def rqs_report_for(rqs_file):
    """report_qor_suggestions text written next to a .rqs (<dir>.rpt)."""
    rpt = os.path.dirname(os.path.abspath(rqs_file)) + ".rpt"
    return rpt if os.path.isfile(rpt) else None


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_rqs_report(path):
    """
    -> {"summary": [{name, id, generated_at, applicable_for, automatic}],
        "details": {name: sorted detail rows}}, or None if the layout is unknown.
    """
    text = read_text(path)
    if not text:
        return None
    summary, details = [], {}
    section, header, cur = None, None, None
    for line in text.splitlines():
        m = _SECTION_RX.match(line)
        if m:
            section, header, cur = int(m.group(1)), None, None
            continue
        if not line.lstrip().startswith("|") or section is None:
            continue
        cells = _cells(line)
        first = cells[0] if cells else ""
        if section == 1:
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if not _NAME_RX.match(first):
                continue
            rec = dict(zip(header, cells))
            summary.append({"name": first, "id": rec.get("id", ""),
                            "generated_at": rec.get("generated at", ""),
                            "applicable_for": rec.get("applicable for", ""),
                            "automatic": rec.get("automatic", "")})
        else:
            if _NAME_RX.match(first):
                cur = first
            elif first:
                cur = None  # a header row starts the next table
                continue
            if cur:
                details.setdefault(cur, []).append("|".join(cells[1:]))
    if not summary:
        return None
    return {"summary": summary,
            "details": dict((k, sorted(v)) for k, v in details.items())}


def _category(sugg_id):
    for prefix, cat in (("RQS_UTIL", "utilization"), ("RQS_CLOCK", "clocking"),
                        ("RQS_CONG", "congestion"), ("RQS_TIMING", "timing"),
                        ("RQS_NETLIST", "netlist")):
        if sugg_id.startswith(prefix):
            return cat
    return None


# Where the final QoR report stamps its suggestions (tcl_gen.RQS_FINAL_GENERATED_AT).
RQS_FINAL_GENERATED_AT = "postroute_phys_opt"


def _would_enable(s, categories, final_stage=False):
    """
    Mirror of tcl_gen.apply_rqs_block's per-category filters. final_stage: the
    category's fallback pass (only suggestions generated after post-route phys_opt).
    """
    if s["id"] in RQS_EXCLUDED_IDS:
        return False
    cat = _category(s["id"])
    if cat is None:
        return True  # unknown kind: keep it in the signature (conservative)
    if categories is not None and cat not in categories:
        return False
    if s["automatic"].lower() not in ("yes", "1", "true"):
        return False
    if s["applicable_for"] not in IMPL_STEPS:
        return False
    gen = s["generated_at"]
    if final_stage:
        return gen == RQS_FINAL_GENERATED_AT
    if cat == "utilization":
        return gen == "opt_design"
    if cat in ("clocking", "netlist"):
        return gen in ("opt_design", "place_design")
    if cat == "timing":
        return gen == "place_design"
    return True  # congestion: the filter also looks at regenerated_at, not reported


def enabled_suggestions(parsed, categories=None):
    """
    The suggestions apply_rqs_block would enable: each category's usual filter,
    else (nothing matched in that category) its post-route phys_opt suggestions.
    Unknown kinds are kept (conservative for the signature; see enabled_count).
    """
    cats = None if categories is None else set(c.lower() for c in categories)
    out, hit = [], set()
    for s in parsed["summary"]:
        if _would_enable(s, cats):
            out.append(s)
            hit.add(_category(s["id"]))
    for s in parsed["summary"]:
        cat = _category(s["id"])
        if cat is not None and cat not in hit and _would_enable(s, cats, final_stage=True):
            out.append(s)
    return out


def _parsed_rqs(rqs_file):
    rpt = rqs_report_for(rqs_file) if rqs_file else None
    return parse_rqs_report(rpt) if rpt else None


def rqs_enabled_count(rqs_file, categories=None):
    """How many suggestions a pure RQS run would enable; None if the report is unreadable."""
    parsed = _parsed_rqs(rqs_file)
    if parsed is None:
        return None
    return len([s for s in enabled_suggestions(parsed, categories)
                if _category(s["id"]) is not None])


def rqs_signature(rqs_file, categories=None):
    """Hash of the suggestions a run would enable from rqs_file; None if unknown."""
    parsed = _parsed_rqs(rqs_file)
    if parsed is None:
        return None
    enabled = sorted([s["name"], s["id"], s["generated_at"], s["applicable_for"],
                      parsed["details"].get(s["name"], [])]
                     for s in enabled_suggestions(parsed, categories))
    return hashlib.sha1(json.dumps(enabled).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fingerprints and duplicate lookup
# ---------------------------------------------------------------------------
def run_fingerprint(text):
    rqs, cats, rest = split_rqs_block(text)
    if rqs:
        sig = rqs_signature(rqs, cats)
        # No readable report: only the very same file (and filter) counts as equal.
        rqs_key = "rqs-sig:" + sig if sig else "rqs-file:%s:%s" % (rqs, cats)
    else:
        rqs_key = ""
    body = _normalise(rest) + "\n" + rqs_key
    return hashlib.sha1(body.encode("utf-8")).hexdigest()


def run_dirs(root, exclude=(), with_path_b=False):
    """
    Every implementation run dir under an output dir (stage1/2/3, planner fixes;
    Path B R2 / Stage-2-on-R2 runs too when with_path_b).
    """
    # planner/*/: the early round (planner/early/) as well as planner/r<n>/. With
    # only r*, a later round never saw the early round's fixes and relaunched the
    # same one (omemaccess 2026-10-27: rule_oc_ap_clk_1p5x in early and r1, the
    # same WNS, 1.4-2.1 h of farm time each).
    pats = ["stage1/*", "stage2/*", "stage2/*/*", "stage3/*", "planner/*/[0-9]*"]
    if with_path_b:
        pats += ["path_b/*", "path_b/*/*"]
    ex = set(os.path.abspath(e) for e in exclude)
    out = []
    for pat in pats:
        for d in sorted(glob.glob(os.path.join(root, pat))):
            d = os.path.abspath(d)
            if d in ex or any(d.startswith(e + os.sep) for e in ex):
                continue
            if os.path.isfile(os.path.join(d, "run.tcl")):
                out.append(d)
    return out


def settled_without_result(run_dir):
    """Failed, crashed or cancelled: it will never produce the result it was for."""
    return (os.path.isfile(os.path.join(run_dir, "run.failed")) or
            os.path.isfile(os.path.join(run_dir, "run.cancelled")) or
            bool(glob.glob(os.path.join(run_dir, "hs_*err_pid*.log"))))


def find_duplicate(text, candidates, unfinished=False):
    """
    First completed run dir whose run.tcl does exactly what `text` would.
    unfinished: also one generated, queued or running that has not failed or
    been cancelled -- it will produce that result (planner fixes yield to it).
    """
    fp = run_fingerprint(text)
    for d in candidates:
        if not os.path.isfile(os.path.join(d, SENTINEL_NAME)) and \
                (not unfinished or settled_without_result(d)):
            continue
        if run_fingerprint(read_text(os.path.join(d, "run.tcl"))) == fp:
            return d
    return None


def placement_signature(run_dir):
    """Post-place phys_opt timing, as a proxy for 'the same placement'."""
    t = report_parser.parse_timing_summary(
        os.path.join(run_dir, "postplace_physopt_timing_summary.rpt"))
    if t.get("wns") is None:
        return None
    return (t["wns"], t["whs"], t["tns"], t["ths"], t["num_failing_setup"],
            t["num_failing_hold"])


def pick_reroute_directive(winner_dir, candidates_dirs, directives):
    """
    -> (directive or None, sorted directives already used on this placement).
    A run counts as the same placement when its post-place phys_opt timing
    summary matches the winner's exactly (WNS, WHS, TNS, THS, failing counts).
    """
    tried = set()
    own = route_directive_of(read_text(os.path.join(winner_dir, "run.tcl")))
    if own:
        tried.add(own)
    sig = placement_signature(winner_dir)
    if sig is not None:
        for d in candidates_dirs:
            if os.path.abspath(d) == os.path.abspath(winner_dir):
                continue
            if placement_signature(d) == sig:
                r = route_directive_of(read_text(os.path.join(d, "run.tcl")))
                if r:
                    tried.add(r)
    for directive in directives:
        if directive not in tried:
            return directive, sorted(tried)
    return None, sorted(tried)
