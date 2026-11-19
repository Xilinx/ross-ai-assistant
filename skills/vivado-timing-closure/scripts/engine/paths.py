#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
paths.py -- find the deterministic layer, without importing it.

The engine drives `orchestrate.py` as a subprocess rather than importing it.
That is deliberate: orchestrate.py is 2,334 lines with 177 tests around it, and
every engineering decision (phase, budget, retry eligibility, the winner,
"closed") must keep living there. Shelling out makes it impossible for engine
code to reach past the CLI and change one of those decisions by accident --
risk R5 in ENGINE_PLAN.md.

Resolution order for the skill root:
  1. $TC_SKILL_ROOT
  2. the checked-in skill beside this package
  3. a `timing-closure-agent` directory found upwards from the cwd

Stdlib only. Requires Python 3.6+.
"""

from __future__ import print_function

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# Where the deterministic layer lives, in preference order.
#
# `lib/` is where it belongs in a shipped install: it is a library the engine
# drives, not a skill an agent reads. Leaving it under `.claude/skills/` would
# have an editor discover its old SKILL.md -- the vendor-coupled one the
# portability lint rejects -- sitting beside the judgement skill that replaced
# it. The `.claude/skills/` path is kept so a development checkout, where it
# still lives there, keeps working.
_SKILL_CANDIDATES = (
    # Packaged: the whole thing is one skill directory, and the engine sits
    # inside its scripts/ beside orchestrate.py. REPO_ROOT is then scripts/,
    # so the skill root is its parent.
    os.path.dirname(REPO_ROOT),
    # Development checkout.
    os.path.join(REPO_ROOT, "lib", "timing-closure-agent"),
    os.path.join(REPO_ROOT, ".claude", "skills", "timing-closure-agent"),
)


class SkillNotFound(Exception):
    pass


def _looks_like_skill(root):
    return os.path.isfile(os.path.join(root, "scripts", "orchestrate.py"))


def skill_root():
    """The timing-closure-agent directory that owns orchestrate.py."""
    env = os.environ.get("TC_SKILL_ROOT")
    if env:
        if not _looks_like_skill(env):
            raise SkillNotFound(
                "TC_SKILL_ROOT=%s has no scripts/orchestrate.py" % env)
        return os.path.abspath(env)

    for cand in _SKILL_CANDIDATES:
        if _looks_like_skill(cand):
            return cand

    cur = os.getcwd()
    while True:
        for rel in (("lib", "timing-closure-agent"),
                    (".claude", "skills", "timing-closure-agent")):
            cand = os.path.join(cur, *rel)
            if _looks_like_skill(cand):
                return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    raise SkillNotFound(
        "cannot find timing-closure-agent; set TC_SKILL_ROOT")


def scripts_dir():
    return os.path.join(skill_root(), "scripts")


def orchestrate_py():
    return os.path.join(scripts_dir(), "orchestrate.py")


def orchestrate_cmd():
    """argv prefix for running orchestrate.py with this interpreter."""
    return [sys.executable, orchestrate_py()]


def import_script(name):
    """
    Import a pure-function module from the deterministic layer.

    The rule elsewhere is drive-don't-import, so that engine code cannot reach
    past the CLI and change an engineering decision. These modules are the
    exception because they *are* the decision and have no side effects:
    importing `decision_engine` is how the engine avoids owning a second,
    divergent copy of the hold-first ranking. Reimplementing that would be far
    worse than importing it.

    Only ever use this for modules that compute and return. Anything that
    writes state, advances a phase or generates a run goes through
    `orchestrate_cmd()`.
    """
    d = scripts_dir()
    if d not in sys.path:
        sys.path.insert(0, d)
    return __import__(name)


def import_yaml_lite():
    """The skill's stdlib YAML subset reader, imported rather than duplicated."""
    return import_script("yaml_lite")
