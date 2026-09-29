<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ISP Configuration — Comparison Prompts

## Step 1 — Prepare each trial

Use a new working directory, new agent conversation, and new Vivado session for each condition. Configure the same model and reasoning effort for both. The recorded runs used Codex CLI with `gpt-5.6-luna` or `gpt-5.6-sol`, each at medium reasoning, via the AMD gateway.

Connect Vivado MCP and AMD documentation-search MCP in both conditions. Stage only the skill-enabled condition's `vivado-ip-configurator` at `.agents/skills/vivado-ip-configurator/`. Ensure the baseline cannot read user-installed skills, the skill repository, or previous trial files through either its shell or MCP server.

Copy only `input/create_project.tcl` into each new trial directory. Before the timed task, start Vivado in that directory and source the script through `vivado_execute`:

```tcl
source create_project.tcl
```

Confirm Vivado 2026.1, the requested part, and an empty `benchmark_bd`. Keep the session open. For the skill condition, the recorded harness also sourced the staged skill's `lib/ipcfg.tcl` during preparation; the baseline had no helper loaded.

Do not give the agent this README, the result tables, or `results/`. Paste only the task below and the condition-specific suffix. The original harness also supplied the fresh session ID with an instruction to reuse that session and leave it open. That ID is infrastructure context, not a configuration hint.

## Step 2 — Use the same functional task

```text
Configure a hardened ISP on the xc2ve3558-sfva1440-2MP-e-S with all three tiles enabled and multi-pass disabled. Each tile needs four independent RAW12 inputs to its first ISP and one input to its second ISP—RAW12 on tile 1, 16-bit on tiles 0 and 2. All inputs use four pixels per clock. Expose the corresponding streaming ports, leave other settings at defaults, verify the configuration and ports, and save the design.
```

Append this for the **with-skill** condition:

```text
Use the vivado-ip-configurator skill provided in .agents/skills/vivado-ip-configurator/SKILL.md for this task.
Do not read benchmark answer keys, previous runs or other workspaces.
```

Append this for the **without-skill** condition:

```text
This is the no-skill baseline. Use Vivado MCP and documentation directly, without skill files or ipcfg helper libraries.
Do not read benchmark answer keys, previous runs or other workspaces.
```

## Step 3 — Record, then evaluate

Let each attempt finish without supplying configuration hints. Record model time, tool calls, errors, and the final saved design. Recovery within the same attempt is allowed; a failed final result should remain a failure rather than silently being replaced by a better retry.

After the model finishes, independently read the effective settings and actual interface pins from Vivado. Compare them with the functional requirements in the [example README](README.md#the-prompt). Keep evaluation feedback out of the measured conversation. The [recorded results](results/README.md) illustrate why checking only an agent's report or internal live-input count is insufficient.

The setup script and prompts are a manual walkthrough, not an automated reproduction of the recorded sandbox or 113-check harness. The machine-readable check records provide the evaluation contract and observed outcomes for inspection.
