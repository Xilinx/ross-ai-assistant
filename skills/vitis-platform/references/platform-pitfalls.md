<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Platform Pitfalls

Failures seen when a platform annotated by this skill reaches `v++ --link`. Check these
before annotating; the ones that need a design change are for the user to make.

## Zynq UltraScale+: Tag the PS Control Master Directly

The `v++` linker connects accelerator AXI-Lite control slaves (for example the DPU's
`S_AXI_CONTROL`) to a port tagged `M_AXI_GP` in the platform. On Zynq UltraScale+, tag
`M_AXI_HPM0_FPD` (or HPM1) directly on the PS8. Do not pre-connect it through a
SmartConnect.

`::vitis::pfm::get_control_candidates` does not find the PS8 ports (it searches for
Versal processing systems only), so set this port with the manual path in SKILL.md
step 6.

## Versal: Custom Platform PS-NoC Clock Routing Failure

**Problem:** Manually creating `axi_noc` IP instances in Vivado and connecting them to
CIPS PS CCI / PS RPU / PS PMC interfaces causes `v++ --link` to fail during VPL
implementation with:

```text
ERROR: [VPL 35-19] Pin mapping failure, cannot reach driver pin:
  .../PSPSNOCCCIAXI0CLK at site PS9_X0Y0
```

**Root cause:** The `noc_nmu` sub-IP generated inside a manually created `axi_noc` is
always a PL-type NMU with fabric clocking. Even when `CONFIG.CATEGORY` is set to
`ps_cci` at the BD level, the generated NMU still expects PL clock routing. However,
PS CCI dedicated NMU sites (e.g., `NOC_NMU128_X0Y6`) require direct clock routing
from the PS9 hard block — these dedicated clock paths are only established by Vivado's
board automation during initial platform creation or by vendor-provided base platforms.

**Resolution:** Build the CIPS-to-NoC connections with board automation (or the Vivado
Platform Creation Wizard), not manual Tcl NoC construction, before tagging the design
with this skill. If the design cannot be rebuilt that way, use an AMD-provided base
platform (e.g., `xilinx_vck190_base_202520_1`) for the `v++ --link` flow instead of the
custom platform.
