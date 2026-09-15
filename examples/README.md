<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Examples

Usage examples organized by skill. Each example is a self-contained scenario with input files, a recommended prompt, and expected behavior.

## Prerequisites

These examples assume the relevant skills are already installed somewhere your agent can access — either your workspace (`.claude/skills/`) or your home directory (`~/.claude/skills/`). See [Getting Started](../docs/getting-started/) to install them. Each example references installed skills by name; it does not bundle its own copy.

## Structure

```
examples/<skill-name>/<scenario-name>/
README.md # Goal, skills used, prerequisites, prompts, expected behavior
prompt.md # Copy-paste prompts
input/ # Source files (RTL, C++, MATLAB, constraints)
scripts/ # Build/setup scripts (optional)
```

## Conventions

- No `SKILL.md` inside examples — skills live in `skills/` (install them per [Prerequisites](#prerequisites))
- No `.claude/` directories — examples reference installed skills, not bundled copies
- Each scenario is self-contained: unzip the package, `cd` into the scenario, follow the README
