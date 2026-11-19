<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Versal notes

Source: `scripts/ila_input.tcl`, `ila_plan.tcl`, `ila_apply.tcl`, `ila_clocks.tcl` and `ila_dfx_gate.tcl` (all in `scripts/`) and the project's measured findings.

## CIPS required

- `check_input` looks for a `PS9` cell (CIPS) or a `PS11` cell (PS Wizard, Versal AI Edge Gen 2 and Prime Gen 2). Without one it stops with `ILA_BLOCKED_NO_CIPS` (blocker FB-V01).
- Vivado would fail at `create_debug_core` with error 12-727 "no versal_cips IP instance found". The debug hub reaches the host through the CIPS.
- The user's source needs a CIPS or PS Wizard with at least one PL reset enabled. The skill adds nothing.
- A 12-727 failure during `ila::apply` is the same blocker only when its text says no `versal_cips` IP. 12-727 has other texts (a module with no hub, a name already used, a port that cannot be removed). The library reads the text.

## Hub and image

- The hub is `axi_dbg_hub` (UltraScale: `dbg_hub`). Vivado adds it when the first core is inserted and builds it in `opt_design`.
- The image is a `.pdi` (`write_device_image`), written by the user's own step or run. UltraScale writes a `.bit` (`write_bitstream`). The skill writes neither.
- The user's flow to an image takes a long time on Versal (a full run, about 25 minutes in earlier measurements). The skill does not run it.
- The user's `opt_design` after an insertion builds the debug cores and takes longer on Versal than on UltraScale+: about 10 minutes on a small CIPS design with two ILAs (Vivado 2026.1). Tell the user to expect it.
- The library does not move the hub clock on Versal. On UltraScale it never moves `dbg_hub/clk` either (a hub already in the design is left alone, C03); it prints `debug hub clock: <net>`. The one exception on Versal is Vivado 2026.2 in a checkpoint session, when the hub clock pin has no driver: it connects the pin to the ILA clock and warns (C04).
- A design that already has one AXI Debug Hub (for example from a VIO in the block design) keeps it: `opt_design` connects every debug core not wired to a hub by hand to that hub. Tell the user the existing hub is reused; one hub after `opt_design` is the expected result. With two or more hubs, `opt_design` does no automatic connection, and each core must be bound with `connect_debug_core`: report that, do not do it. (Versal DFX Debug JTAG/HSDP tutorial, UG908.)

## Limits

- One probe port is 1 to 1024 bits (Chipscope 16-338). Each signal is its own port, so a signal wider than 1024 bits is rejected as N10; several signals on one clock may together pass 1024 bits.
- N15: probes on CIPS-to-NoC connections fail at route, so they are rejected in the plan.
- `-replace_existing` deletes the old core and the net on its clock port. `apply` finds the clock net again from a probed register's clock pin.

## DFX

- `check_input` reports each reconfigurable partition and lists the debug cores already there. `plan` reports blockers (no AXI Debug Hub in the module, or none in static, even for a module-only request). A greybox has no hub and cannot take an ILA: `create_debug_core` there fails with 12-727 "Everest debug hub can not be inserted in RP region" (`ILA_BLOCKED_DFX_NO_HUB`).
- Hubs on Versal are AXI Debug Hubs that the user places in the module source. The library never adds one.
- Insertion in a Versal DFX project is described in `dfx.md` (saved and reopened: measured on 2026.1 and 2026.2; a full implementation was not run). UltraScale+ DFX is in `routes.md`.

## Changing a setting

What `ila::change` meets on Versal, measured on 2026.1 and 2026.2. The full flow is in `change-settings.md`.

- Save and reopen renames the ILA clock net (in the CIPS counter test design it becomes `u_ila_0_cips_pl0_ref_clk`). The driver pin of the clock stays the same, so the skill's record matches the core by the clock driver, not the net name.
- The built core's `DEBUG_CORE_INFO` is missing on 2026.2 and stale on 2026.1 after a change (it still said depth 1024 after a change to 4096). The skill never reads it on Versal. The proof is the block RAM or UltraRAM count under the core: 8 block RAM tiles at depth 8192 x 32 bits, 2 at 2048, 1 UltraRAM tile at 4096.
- Capture control with 1 comparator builds with no message, and every read-back says 1 comparator. The skill raises the count to 2 first and says so. Capture control, comparators, advanced trigger, pipe stages and probe type are read back only on Versal.
- A change on a routed design is accepted by Vivado with no message and drops the core's placement. After `opt_design`, `place_design` fails with DRC NDRV-1 (driverless nets), so the routed design cannot be recovered: go back to the synthesized design. `ila::change` refuses any placed design with `ILA_REFUSED_PLACED`, even after `ila::check_input -allow_state_reset 1`.

## Not covered

- Project-mode handoff beyond the tested fixture.
