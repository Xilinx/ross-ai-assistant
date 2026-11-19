<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Prompt

`run_trial.sh` sends this text to the agent, with `<TRIAL_DIR>` replaced by the trial's absolute
path. The text names no IP parameter and contains no Tcl. Both conditions get the same task; the
with-skill condition adds one line naming the skill, as a user would.

```text
On part xc2ve3558-sfva1440-2MP-e-S, use the configured Vivado MCP. Keep the design in project
`proj` with block design `design_1` in the directory `<TRIAL_DIR>/proj`: start Vivado there if
it is not already running, and create the project and block design there if they do not exist
yet. Block-design cell: `ps_wizard_0`. Configure only that cell, save the design, and report
the settings you applied and read back.

Set up the Versal Gen2 processing system's PL fabric clocks: enable PL clocks 0, 1 and 2 at
100 MHz, 150 MHz and 134 MHz respectively, leave PL clock 3 disabled, and generate a single PL
fabric reset.
```

With the skill, this line is appended:

```skill
Use the vivado-ip-configurator skill.
```

## What a correct result looks like

Keep this section out of the agent's workspace; `run_trial.sh` copies only the text above.

On this part the processing system is the `ps_wizard` IP. Its PL clock settings are not
top-level parameters: they are keys inside one nested parameter, `CONFIG.PS11_CONFIG`.
`check/check_pl_clocks.tcl` reads them from the saved design.

| Requirement | Setting |
|---|---|
| PL clock 0 on, 100 MHz | `PS_USE_PMCPL_CLK0 = 1`, `PMC_CRP_PL0_REF_CTRL_FREQMHZ = 100` |
| PL clock 1 on, 150 MHz | `PS_USE_PMCPL_CLK1 = 1`, `PMC_CRP_PL1_REF_CTRL_FREQMHZ = 150` |
| PL clock 2 on, 134 MHz | `PS_USE_PMCPL_CLK2 = 1`, `PMC_CRP_PL2_REF_CTRL_FREQMHZ = 134` |
| PL clock 3 off | `PS_USE_PMCPL_CLK3 = 0` |
| One PL fabric reset | `PS_NUM_FABRIC_RESETS = 1` |
