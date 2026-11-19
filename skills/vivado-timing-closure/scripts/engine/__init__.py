#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The timing-closure engine: everything long-running, owned headlessly.

The engine submits Vivado batch jobs, tracks run state, queues judgement
points with deadlines and rule fallbacks, and exports a replayable recipe. It
depends on no IDE, no MCP server and no LLM; an answer from any of those
improves quality but is never required for progress.

The engineering decisions stay in the skill's deterministic layer
(orchestrate.py and friends), which the engine drives as a subprocess. See
ENGINE_PLAN.md.
"""

__all__ = ["backends", "cli", "config", "paths"]
