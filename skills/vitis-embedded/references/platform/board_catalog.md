<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Board Catalog (built-in fixed hardware platforms)

> **Generated from a 2026.1 Vitis install**, verified against the shipped
> board XSAs at `$XILINX_VITIS/data/embeddedsw/lib/fixed_hwplatforms/<board>.xsa`.
> Ground truth for the shipped board shortcuts, produced by
> `client.get_processor_os_list(xsa=<fixed_hwplatforms>/<board>.xsa)`.
> **Regenerate per Vitis release.** For a **custom `.xsa`** (not a board
> shortcut) this table does not apply — query the cores live with
> `get_processor_os_list(xsa=<path>)`.
>
> **Per-board minimum Vitis** is in the **Min Vitis** column — a board whose `.xsa` is not
> in your install's `fixed_hwplatforms/` will **not resolve** (e.g. `scu200` (MicroBlaze-V)
> needs **2026.1+**; `vek280`/`vek385` need **2025.2+**). `2024.2` marks the skill's supported
> baseline (board present at or before 2024.2). Verified: `scu200.xsa` is absent in
> 2024.1/2024.2/2025.1/2025.2 and present from 2026.1.

## Quick reference

| Board | Min Vitis | Family | Supported OS | Rec. cpu (standalone) | Rec. cpu (linux) |
|-------|-----------|--------|--------------|-----------------------|------------------|
| scu200 | 2026.1 | microblaze | standalone | `microblaze_riscv_0` | `—` |
| vck190 | 2024.2 | versal | freertos, linux, standalone | `psv_cortexa72_0` | `psv_cortexa72` |
| vek280 | 2025.2 | versal | freertos, linux, standalone | `psv_cortexa72_0` | `psv_cortexa72` |
| vek385 | 2025.2 | versal (gen2) | linux, standalone | `cortexa78_0` | `cortexa78` |
| vmk180 | 2024.2 | versal | freertos, linux, standalone | `psv_cortexa72_0` | `psv_cortexa72` |
| zc702 | 2024.2 | zynq | freertos, linux, standalone | `ps7_cortexa9_0` | `ps7_cortexa9` |
| zc706 | 2024.2 | zynq | freertos, linux, standalone | `ps7_cortexa9_0` | `ps7_cortexa9` |
| zcu102 | 2024.2 | zynqmp | freertos, linux, standalone | `psu_cortexa53_0` | `psu_cortexa53` |
| zcu106 | 2024.2 | zynqmp | freertos, linux, standalone | `psu_cortexa53_0` | `psu_cortexa53` |
| zed | 2024.2 | zynq | freertos, linux, standalone | `ps7_cortexa9_0` | `ps7_cortexa9` |

## Per-board processors

### scu200
- **Min Vitis:** 2026.1+ (board absent in 2024.1/2024.2/2025.1/2025.2 — creation fails at board resolution on older installs)
- **Family:** microblaze
- **Supported OS:** standalone
- **Other:** `microblaze_riscv_0`
  - `standalone` cores: `microblaze_riscv_0`

### vck190
- **Family:** versal
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `psv_cortexa72`, `psv_cortexa72_0`, `psv_cortexa72_1`
- **Real-time (R5/R52):** `psv_cortexr5_0`, `psv_cortexr5_1`
- **Service cores:** `psv_pmc_0`, `psv_psm_0`
  - `freertos` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`
  - `linux` cores: `psv_cortexa72`
  - `standalone` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`, `psv_pmc_0`, `psv_psm_0`

### vek280
- **Family:** versal
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `psv_cortexa72`, `psv_cortexa72_0`, `psv_cortexa72_1`
- **Real-time (R5/R52):** `psv_cortexr5_0`, `psv_cortexr5_1`
- **Service cores:** `psv_pmc_0`, `psv_psm_0`
  - `freertos` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`
  - `linux` cores: `psv_cortexa72`
  - `standalone` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`, `psv_pmc_0`, `psv_psm_0`

### vek385
- **Family:** versal (gen2)
- **Supported OS:** linux, standalone
- **Application cores:** `cortexa78`, `cortexa78_0`, `cortexa78_1`, `cortexa78_2`, `cortexa78_3`, `cortexa78_4`, `cortexa78_5`, `cortexa78_6`, `cortexa78_7`
- **Real-time (R5/R52):** `cortexr52_0`, `cortexr52_1`, `cortexr52_2`, `cortexr52_3`, `cortexr52_4`, `cortexr52_5`, `cortexr52_6`, `cortexr52_7`, `cortexr52_8`, `cortexr52_9`
- **Service cores:** `asu`, `pmc_0`
  - `linux` cores: `cortexa78`
  - `standalone` cores: `asu`, `cortexa78_0`, `cortexa78_1`, `cortexa78_2`, `cortexa78_3`, `cortexa78_4`, `cortexa78_5`, `cortexa78_6`, `cortexa78_7`, `cortexr52_0`, `cortexr52_1`, `cortexr52_2`, `cortexr52_3`, `cortexr52_4`, `cortexr52_5`, `cortexr52_6`, `cortexr52_7`, `cortexr52_8`, `cortexr52_9`, `pmc_0`

### vmk180
- **Family:** versal
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `psv_cortexa72`, `psv_cortexa72_0`, `psv_cortexa72_1`
- **Real-time (R5/R52):** `psv_cortexr5_0`, `psv_cortexr5_1`
- **Service cores:** `psv_pmc_0`, `psv_psm_0`
  - `freertos` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`
  - `linux` cores: `psv_cortexa72`
  - `standalone` cores: `psv_cortexa72_0`, `psv_cortexa72_1`, `psv_cortexr5_0`, `psv_cortexr5_1`, `psv_pmc_0`, `psv_psm_0`

### zc702
- **Family:** zynq
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `ps7_cortexa9`, `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `freertos` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `linux` cores: `ps7_cortexa9`
  - `standalone` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`

### zc706
- **Family:** zynq
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `ps7_cortexa9`, `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `freertos` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `linux` cores: `ps7_cortexa9`
  - `standalone` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`

### zcu102
- **Family:** zynqmp
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `psu_cortexa53`, `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`
- **Real-time (R5/R52):** `psu_cortexr5_0`, `psu_cortexr5_1`
- **Service cores:** `psu_pmu_0`
  - `freertos` cores: `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`, `psu_cortexr5_0`, `psu_cortexr5_1`
  - `linux` cores: `psu_cortexa53`
  - `standalone` cores: `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`, `psu_cortexr5_0`, `psu_cortexr5_1`, `psu_pmu_0`

### zcu106
- **Family:** zynqmp
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `psu_cortexa53`, `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`
- **Real-time (R5/R52):** `psu_cortexr5_0`, `psu_cortexr5_1`
- **Service cores:** `psu_pmu_0`
  - `freertos` cores: `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`, `psu_cortexr5_0`, `psu_cortexr5_1`
  - `linux` cores: `psu_cortexa53`
  - `standalone` cores: `psu_cortexa53_0`, `psu_cortexa53_1`, `psu_cortexa53_2`, `psu_cortexa53_3`, `psu_cortexr5_0`, `psu_cortexr5_1`, `psu_pmu_0`

### zed
- **Family:** zynq
- **Supported OS:** freertos, linux, standalone
- **Application cores:** `ps7_cortexa9`, `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `freertos` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`
  - `linux` cores: `ps7_cortexa9`
  - `standalone` cores: `ps7_cortexa9_0`, `ps7_cortexa9_1`
