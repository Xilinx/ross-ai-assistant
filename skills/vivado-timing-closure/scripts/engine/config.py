#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
config.py -- engine settings, with working defaults and no required file.

Separate from the skill's `config/agent_config.yaml`, which keeps owning the
engineering knobs (stage1 strategies, guardrails, planner limits). This file
owns only *how work is executed and who answers questions*: backend, judge
order and deadlines, poll intervals.

Resolution: $TC_ENGINE_CONFIG, else engine/config.yaml, else the defaults
below. The defaults must be enough to run `tc serve --judge rules` on a local
host, so that a first run needs no configuration at all.

Stdlib only (yaml_lite from the skill). Requires Python 3.6+.
"""

from __future__ import print_function

import copy
import os

from . import paths

DEFAULTS = {
    "backend": {
        # auto: a Vivado MCP server the user already has, if it can launch on
        # LSF; else bsub when LSF is reachable; else this host (launch.py).
        "default": "auto",
        "vivado": "vivado",
        "local": {
            # Sliding window, not fixed batches: a fixed batch idles while its
            # slowest run finishes (we watched auto_5 run alone at the end).
            "concurrency": 1,
        },
        "lsf": {
            "env_script": None,
            "queue": None,
            "memory_mb": None,
            "slots": None,
            "os_type": None,
            "runtime_limit_min": None,
            # LSF mode runs the whole stage in parallel; the farm is the limit.
            "concurrency": 0,
            "retry_queue": None,
        },
        # Runs that stay loaded after their script finishes, so the best one
        # can be queried without re-opening a checkpoint. Submits through the
        # Vivado MCP server, which brings the queue/OS feasibility checks with
        # it. Requires TC_KEEP_SESSION=1 when the run scripts are generated.
        "session": {
            # Must include --stdio: a private backend, never a shared daemon.
            # A daemon switching underneath a six-hour run is how three
            # design A runs were lost.
            "mcp_command": None,
            "cwd": None,
            "env": None,
            "submit_mode": None,          # None -> the server's 'auto'
            "timeout_s": 300,
            "start_timeout_s": 600,
            "concurrency": 0,
        },
    },
    "judge": {
        # First answerer that produces a valid answer wins; `rules` always
        # answers at the deadline, so a run never blocks on a human or an IDE.
        "order": ["ide_agent", "headless", "human", "rules"],
        "deadlines": {
            "postplace": "15m",
            "stage2": "1h",
            "stage3": "1h",
            "triage": "1h",
            "planner": "4h",
            # Beside a running Stage 2: an answer after this only delays the
            # collect, so the rule plan steps in sooner.
            "planner_early": "1h",
        },
        "headless": {
            "enabled": False,
            # A command template, not an integration: any CLI that can take a
            # prompt and emit schema-constrained JSON drops in here.
            "command": None,
            "cwd": None,
            "timeout_s": 900,
        },
    },
    "serve": {
        "poll_seconds": 30,
        # tc close starts with a build probe: one short job that opens the
        # checkpoint with the configured Vivado before Stage 1 is released.
        "probe": True,
        # 'stop' returns needs_attention with the evidence; 'continue' proceeds
        # and lists the failures at the end.
        "on_run_failure": "stop",
        # Stage 1 runs only: one failed strategy leaves the others to finish,
        # and stage1-collect picks among those that did. If none did, the flow
        # still stops (stage1_no_result). --on-run-failure overrides both.
        "on_stage1_failure": "continue",
        # A run whose submit fails this many times running is recorded as
        # failed (submit_failed) instead of being tried again on every tick.
        "max_submit_failures": 3,
        "max_wall_hours": 0,
    },
    # A folder on a filesystem every farm host mounts (NFS). When set, `tc close`
    # for a farm backend (lsf, session) copies a checkpoint that is not already
    # under it to <stage_root>/<design>-<hash>/ and runs the flow there, so farm
    # jobs can read the checkpoint and write their run folders even when the
    # workspace is on a local disk (a CI runner's /scratch). The skill's Tcl
    # assets a run.tcl sources are staged too, to <stage_root>/.assets/<hash>/
    # (TCA_ASSETS_ROOT, recorded as assets_root in state.json), and the
    # supervisor refuses to submit a run that still names the local skill.
    # Ignored for the local backend. Default: unset (runs use the checkpoint
    # where it is).
    "stage_root": None,
    # The run's live page (engine/web): `tc web`, and started with each run when
    # enabled. On by default, so every run reports its link (web_url); off under
    # CI regardless (engine/web/control.py in_ci), and `tc setup --web off` or
    # TC_WEB=off turns it off. Access is a secret link (a random token in
    # ~/.tc/web/token), not a sign-in.
    "web": {
        "enabled": True,
        "port": 8112,
        "bind": "0.0.0.0",
        # The host name the links carry; default: this host's FQDN.
        "host": None,
        # Further workflow folders (each with a workflow.json "view" block) to show.
        "workflow_dirs": [],
    },
}


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


WORKSPACE_CONFIG = os.path.join(".tc", "engine.yaml")


def workspace_config(start=None):
    """
    `.tc/engine.yaml` in the working directory or any parent, or None.

    Site settings (the Vivado build, the LSF queue and OS selection) belong to
    the workspace, not to the shipped skill: re-packaging the skill must not
    wipe them, and a run started from an editor must find them without anyone
    exporting a variable. A local `vivado` that is not on PATH was how the first
    run from VS Code failed seven jobs in a row with exit 127.
    """
    cur = os.path.abspath(start or os.getcwd())
    while True:
        cand = os.path.join(cur, WORKSPACE_CONFIG)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def config_path():
    """$TC_ENGINE_CONFIG, else the workspace's .tc/engine.yaml, else engine/config.yaml."""
    env = os.environ.get("TC_ENGINE_CONFIG")
    if env:
        return env
    return workspace_config() or os.path.join(paths.HERE, "config.yaml")


def load(path=None):
    """Engine settings: defaults merged with the config file when present."""
    path = path or config_path()
    if not os.path.isfile(path):
        return copy.deepcopy(DEFAULTS)
    yaml_lite = paths.import_yaml_lite()
    parsed = yaml_lite.load_yaml(path) or {}
    return _deep_merge(DEFAULTS, parsed)


def get(cfg, *keys, **kw):
    """cfg_get in the style orchestrate.py uses: get(cfg, 'serve', 'poll_seconds')."""
    default = kw.get("default")
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duration_seconds(value, default=None):
    """'15m' -> 900. Plain numbers are seconds. None/'' -> default."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower()
    if not text:
        return default
    unit = text[-1]
    if unit in _UNITS:
        try:
            return int(float(text[:-1]) * _UNITS[unit])
        except ValueError:
            return default
    try:
        return int(float(text))
    except ValueError:
        return default
