<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Multi-Violation Timing Methodology — Quick Start Prompts

The example ships as source only (RTL + Tcl). Build and synthesize first,
then run methodology checks.

## Step 1 — Create and synthesize the project

Either run it yourself:

```bash
cd input
vivado -mode batch -source create_project.tcl
# then:
#   launch_runs synth_1 -jobs 8
#   wait_on_run synth_1
```

or ask the agent:

```
Open Vivado, source input/create_project.tcl, then synthesize the design
(launch_runs synth_1, wait_on_run synth_1).
```

## Step 2 — Run methodology checks and resolve violations

```
Use /timing-methodology-checks to run report_methodology on the synthesized
design. The constraints file has intentional errors (duplicate clock, missing
clock, wrong false_path target, missing clock groups). Identify all violations,
prioritize by GROUP, and resolve them. Generate the resolution report.
```
