#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
xdc_chain.py -- the skill / planner XDC files a run applied, so a later fix can
build on them.

A planner fix that re-places (a flow change, over-constraint, RQS) starts from
the input checkpoint. If the current winner got there because of earlier
fixes -- a fanout XDC from the early planner, an SLR pblock, a skill's clock
root -- a new fix that starts from scratch silently throws them away and tests
its idea alone. Replaying the winner's chain first makes fixes stack. On the
suites, planner-round fixes that replayed it beat the best fixed run 14 times
in 33; without it, 6 in 49 (Ciena +768 ps, Teradyne +188, Teledyne +123,
design E +121, omemaccess +100).

Read from the run's own run.tcl (and, for incremental runs, its parent's), so
it needs no bookkeeping of its own. Ported from the insights branch.

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import os
import re


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (IOError, OSError):
        return ""


_APPLY = re.compile(r"^\s*(source|read_xdc)\s+\{([^}]+\.xdc)\}\s*$")
_PLANNER_APPLY = re.compile(r"^# --- Apply planner XDC (\S+\.xdc) \(one command at a time\)")
_OPEN = re.compile(r"^\s*open_checkpoint\s+\{([^}]+)\}")


def chain(run_dir, root=None, depth=4):
    """
    The skill / planner XDC files a run applied before opt_design, including the
    ones its starting checkpoint already carried (an incremental or last-mile
    run inherits its parent's). [(path, "source"|"read_xdc")], oldest first.
    """
    text = _read(os.path.join(run_dir, "run.tcl"))
    if not text:
        return []
    # Not `chain`: that name is this function, which recurses below. Shadowing
    # it made every chain through an incremental run a TypeError -- the
    # planner-context crash that cost a run its second round (an incremental
    # fix had won round 1).
    own, opened = [], None
    for line in text.splitlines():
        m = _OPEN.match(line)
        if m and opened is None:
            opened = m.group(1)
        m = _APPLY.match(line)
        if m and os.path.basename(m.group(2)) != "oc_reset.tcl":
            item = (m.group(2), m.group(1))
            if item not in own:
                own.append(item)
        m = _PLANNER_APPLY.match(line)
        if m and (m.group(1), "source") not in own:
            own.append((m.group(1), "source"))
    parent = []
    if depth > 0 and opened and root and os.path.abspath(opened).startswith(
            os.path.abspath(root) + os.sep) and \
            re.search(r"post(route|place)(_physopt)?\.dcp$", opened):
        parent = chain(os.path.dirname(opened), root, depth - 1)
    out = []
    for item in parent + own:
        if item not in out and os.path.isfile(item[0]):
            out.append(item)
    return out
