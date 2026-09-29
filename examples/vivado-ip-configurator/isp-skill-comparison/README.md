<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Configuring an ISP: With and Without a Skill

Can an AI agent turn a short functional request into the right Vivado IP configuration? This example compares the same request with and without the [`vivado-ip-configurator` skill](../../../skills/vivado-ip-configurator/SKILL.md).

**In one recorded comparison, the skill helped GPT-5.6 Luna expose all 15 required streaming inputs, versus only six without it. Neither Luna run fully passed. GPT-5.6 Sol passed every check both with and without the skill.** This is an example of a useful but limited improvement, not a guarantee that a skill is necessary or sufficient.

## Goal

Configure a hardened image signal processor (ISP) from a human-readable request. The user specifies video formats, input counts, and parallelism; the agent must discover the IP, parameter names, legal settings, and port-enablement dependencies.

An MCP server gives the agent access to Vivado and documentation. The skill adds a configuration and verification workflow, including helper commands and recovery guidance. Both conditions have the same MCP capabilities; the baseline is a capable agent with tools, not an agent without documentation.

## The Prompt

The following task was identical in all four completed runs. It contains no IP parameter names or Tcl implementation instructions.

> Configure a hardened ISP on the xc2ve3558-sfva1440-2MP-e-S with all three tiles enabled and multi-pass disabled. Each tile needs four independent RAW12 inputs to its first ISP and one input to its second ISP—RAW12 on tile 1, 16-bit on tiles 0 and 2. All inputs use four pixels per clock. Expose the corresponding streaming ports, leave other settings at defaults, verify the configuration and ports, and save the design.

RAW12 means 12 bits per pixel. Four pixels per clock require a 48-bit data input; 16-bit pixels require 64 bits. The required boundary is:

| Tile | First ISP | Second ISP | Separate input interfaces |
|---|---|---|---:|
| 0 | Four RAW12 inputs, 48 bits each | One 16-bit input, 64 bits | 5 |
| 1 | Four RAW12 inputs, 48 bits each | One RAW12 input, 48 bits | 5 |
| 2 | Four RAW12 inputs, 48 bits each | One 16-bit input, 64 bits | 5 |

This explanatory table was **not** included in the agent prompt. Keep it and the results out of the agent's workspace when repeating the comparison.

## Recorded Results

Recorded on September 29, 2026, using **Codex CLI 0.157.1**, **medium reasoning**, and **Vivado 2026.1**. These are the exact model identifiers configured through the AMD gateway; availability in another account or client may differ. Copilot CLI and Claude Code were not used.

| Model | Skill | Checks passed | Required input interfaces present | Model time |
|---|---|---:|---:|---:|
| `gpt-5.6-luna` | With | **95/113 (84%) — fail** | **15/15** | 4m01s |
| `gpt-5.6-luna` | Without | **67/113 (59%) — fail** | **6/15** | 3m34s |
| `gpt-5.6-sol` | With | **113/113 (100%) — pass** | **15/15** | 6m10s |
| `gpt-5.6-sol` | Without | **113/113 (100%) — pass** | **15/15** | 3m21s |

Each row represents one completed model attempt. Times cover the model's tool use and recovery within that attempt, excluding environment setup and independent grading. These are observations, not average performance or success-rate estimates.

### What the skill helped with

Luna with the skill selected Advanced mode and exposed five independent input interfaces per tile. Without the skill, Luna left Basic mode enabled: it set internal input counts but exposed only two interfaces per tile. **A setting that says “four inputs” is not proof that four independently connectable ports exist.** The skill run handled this dependency better.

That improvement cost about 27 seconds of additional model time. It is the concrete value demonstrated here: more complete configuration of the requested interface boundary on Luna.

### What it did not solve

Both Luna runs misinterpreted the depth requirement as applying to entire tiles. In the skill run, the first ISP on tiles 0 and 2 incorrectly used 16-bit formats, making eight inputs 64 bits wide instead of 48. The baseline also left the active paths at 16 bits, including paths it reported as RAW12. Both claimed completion despite unmet requirements.

After each run finished, we queried Vivado for the effective IP settings and the ports actually exposed by the design. We compared these against the original request using 113 checks, including input counts, pixel formats, pixels per clock, and data widths. For example, a requested RAW12 input at four pixels per clock had to expose a 48-bit data port. The scores reflect these observed settings and ports, rather than the agent's completion message. Evaluation results were not fed back to the model to correct its design; the [evaluation details](results/README.md#what-the-score-measures) explain the checks and their limits.

**Sol needed no skill to score 100% on this task.** Its no-skill run was also faster than its skill run. The evidence supports testing the skill with your chosen model and workload, rather than assuming every model benefits equally. A full score here means the enumerated configuration and interface checks passed—not that the design was proven on hardware.

## Prerequisites

- Vivado 2026.1 with a valid license and support for `xc2ve3558-sfva1440-2MP-e-S`.
- A configured Vivado MCP server and AMD documentation-search MCP server. See [Getting Started](../../../docs/getting-started/README.md) and [Vivado MCP setup](../../../docs/getting-started/vivado-mcp.md).
- Codex CLI and access to the model you want to compare. Use the same model and reasoning setting for both conditions.
- The `vivado-ip-configurator` skill installed only for the skill-enabled condition.
- No board, camera, RTL sources, or bitstream are needed for this configuration exercise.

## Structure & Input Files

- [`prompt.md`](prompt.md) — setup and copyable task instructions for both conditions.
- [`input/create_project.tcl`](input/create_project.tcl) — creates an empty project and block design; contains no ISP solution.
- [`results/README.md`](results/README.md) — measurement method, token/cost estimates, isolation details, and limitations.
- [`results/measurements.json`](results/measurements.json) — portable recorded metrics and all 113 check outcomes for each run. This is evaluation evidence, not an agent input.

## How to Run

Follow [`prompt.md`](prompt.md). Start two fresh conversations and two separate empty Vivado projects. Give each the same task, appending only the appropriate skill-use instruction. For an honest baseline, remove skill access from both the agent and its MCP server; simply asking an already skill-enabled conversation to ignore the skill is insufficient.

The recorded Luna trials used separate sandboxed agent processes, MCP server processes, and Vivado sessions. They ran sequentially on the same host, with the grader and previous results hidden. See [the execution method](results/README.md#how-the-recorded-trials-were-run) for the exact scope and the weaker isolation used in the earlier Sol comparison. The walkthrough recreates the task; it does not automatically reproduce that sandbox or the full benchmark harness.

## Expected Output and Review

Expect a saved block design and an agent report of its chosen settings and interfaces. Independently inspect the **actual** ports and effective configuration against the table above. Do not accept an agent's completion message as the result.

The recorded grader checked IP identity, enabled tiles, independent-input mode, live-input counts, disabled multi-pass settings, active formats and pixels per clock, interface presence/protocol/direction, actual data-pin widths, shared input-3 settings, selected default output widths, and saved-design presence. Its 113 checks overlap; they are not 113 independent requirements.

The goal is a configured IP boundary. Complete system wiring, synthesis, implementation, and hardware operation are outside this example. Unrelated-default coverage is limited to selected checks, so the score is not a guarantee that every other setting remained unchanged.
