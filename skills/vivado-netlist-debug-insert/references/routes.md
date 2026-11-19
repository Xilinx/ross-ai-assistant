<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# UltraScale and UltraScale+ DFX routes

Vivado 2026.1 and earlier refuse `create_debug_core` once the cell property `HD.RECONFIGURABLE` is set on a partition (Common 17-69), for static and module nets alike. UG909 says netlist insertion is not supported for UltraScale and UltraScale+ DFX. The library reports it and adds nothing: `ILA_BLOCKED_DFX_NO_BSCAN` (the partition lacks the 12 `S_BSCAN_*` ports) or `ILA_BLOCKED_DFX_UNSUPPORTED` (Vivado's own refusal). Report the message as written.

## Static nets: static-first route (before the flag)

Needs the static checkpoint from before partitioning (the slot is a black box with no flag). Static nets only.

1. `source` `scripts/ila_insert.tcl`, then `scripts/ila_static_first.tcl`.
2. `ila::static_first_check_input` instead of `ila::check_input`. `ILA_REFUSED_FLAGGED` means the design is already flagged: ask for the pre-flag checkpoint, do not try `create_debug_core`. `ILA_REFUSED_NO_BLACKBOX` and `ILA_REFUSED_STATIC_FIRST_VERSAL` are refusals too.
3. `ila::plan`, `ila::apply` as usual. A request for nets under the slot gets N18.
4. `ila::static_first_finish <outdir>`. It only prints (nothing is saved or run): the cores are in the open design, and `opt_design` was NOT run because on a black box it would fail DRC INBB-3.
5. The printed steps, the user's: save the open design as `static_first.dcp` and open it; `read_checkpoint -cell <rp> <rm>.dcp -strict` for each RM, set `HD.RECONFIGURABLE` on the partition, read the floorplan and I/O constraints; `opt_design`, `place_design`, `route_design`; right after `opt_design` write the probes file with `ila::write_probes <outdir>/static_first.ltx` (static nets only); then the user's DFX flow (RM checkpoint, `update_design -black_box`, `lock_design`, greybox child, `pr_verify`, `write_bitstream`).

Measured (2026.1, one KU5P pre-flag checkpoint): check, plan, apply and finish, then every step above run as printed except the probes file: that run wrote it another way, and the printed `ila::write_probes` step was not run on this route (unverified). Link, flag, `opt_design`, place, route, bitstream, RM checkpoint, black box, lock, grey child and `pr_verify` (compatible) all passed.

Say plainly: static nets only, no per-RP probes file, Vivado adds the hub in the static part when the first core is inserted and builds it in `opt_design`, no board run. 

## UltraScale+ DFX (Vivado 2026.2 and later, a design with no debug yet)

In a checkpoint session: `ila::check_input`, `ila::plan`, `ila::apply`. The skill then saves nothing and runs nothing. `ila::apply` ends by printing the user's steps (`ila::next_steps` prints them again). For a DFX design they are, in this order:

1. `read_xdc <your clock and floorplan constraints>`, BEFORE `opt_design`. `opt_design` builds the debug hub, and the hub's own constraint file (`xsdbm_cc_late_late.xdc`) reads the clock periods. With no clock yet it fails (`Common 17-55`, `Common 17-1548`) and the crossing from the system clock to the hub clock loses its constraint. Measured on one KU5P design (2026.2): parent WNS -11.542 ns when the XDC was read after `opt_design`, +2.889 ns on that crossing when it was read first.
2. `write_checkpoint -force <file>.dcp` (constraints read in step 1 are saved with it).
3. `opt_design` (builds the cores and the hub).
4. `write_debug_probes -force <file>.ltx` (or `ila::write_probes <file>`; it refuses before `opt_design`).
5. `place_design`
6. `route_design`
7. `write_bitstream -force <file>.bit`

Then the line "This is a DFX design: from place_design on, your own DFX flow applies (floorplan, module checkpoints, the greybox child, pr_verify)." The user's DFX flow after route is: `write_checkpoint -cell <partition>`, `update_design -black_box`, `lock_design`, the greybox child (`update_design -buffer_ports`, place, route, bitstream), `pr_verify -full_check`. For another module: `read_checkpoint -cell <partition> <module>.dcp -strict`, then `opt_design` onward. Those commands are the user's; the skill prints only the line above.

For each partition with no pblock, the printed steps add a WARNING: `place_design` stops with `DRC HDPR-30` (Missing PBLOCK On Reconfigurable Cell). The required change is the user's: `create_pblock`, `add_cells_to_pblock`, `resize_pblock`, read with `read_xdc` before `place_design`. The skill adds no floorplan (the region is a design decision). A pblock query that fails gives no warning.

Measured (2026.2, the KU5P example, `ila::plan {rp_out[*]}`):
- Printed steps without `read_xdc` first (old text): `opt_design` printed Common 17-55 and 17-1548 critical warnings and Chipscope 16-410 (empty `C_CLK_INPUT_FREQ_HZ` in the probes file).
- With `read_xdc` first, then the printed steps and the user's DFX flow: place and route 0 routing errors, DRC 0 errors, full, grey and child bitstreams, `pr_verify -full_check` compatible (`IMPL_DONE`). The checkpoint saved after `apply` reopened with `dbg_hub` and `u_ila_0` and `opt_design` ran.
- HDPR-30: the printed warning appears for a checkpoint with no pblock. Earlier, on a KU5P checkpoint with no pblock (2026.2), `place_design` stopped with HDPR-30; the saved checkpoint reopened with `dbg_hub` and `u_ila_0`, and after the I/O and pblock files were read into it place and route finished with 0 routing errors.
- A checkpoint session is told from a project by `get_runs`: in a checkpoint session `current_project` returns `project_<checkpoint>` (not empty) but there are no runs (measured on 2026.2).

This is the order of the first KCU116 board run (one design, a 2026.2 daily build): the skill's steps, then those commands in the same session. The skill does not run the user's place and route.

Project mode on UltraScale+ was not run, and no shipped script refuses it: not tested, unverified. `save_constraints` has not been shown valid in an UltraScale+ checkpoint session, so the printed steps use `write_checkpoint` there.

## Module nets

The skill cannot reach them. The user's source must change, and the skill reports the change and never makes it:

- Instantiate the ILA in the module source, and give the partition and every RM the 12 exact `S_BSCAN_*` ports (UG909), left open on the static instance. Ports alone do not enable insertion. Static changes, so the parent and every child run again.
- Or put a Debug Bridge in the module source so an ILA inserted in the module's own netlist links (link and `opt_design` level only, no hardware).

## Version note

The 2026.2 source drops the refusal. Measured on one KU5P checkpoint with `HD.RECONFIGURABLE=1`: Vivado 2026.1 refuses with 17-69; a 2026.2 daily build and a later development build accept `create_debug_core`, and connect, `opt_design`, place, route (0 failed nets), `report_drc` and `write_debug_probes` complete. The core lands in static and probes nets inside the partition. With hand-made cores the whole flow passes on both builds, with 0 failed nets, DRC 0 errors, full and partial bitstreams, the grey child, and `pr_verify -full_check` compatible. Hardware, 2026.2 daily builds: the full image and up to five partial swaps (greybox, RM A, RM B, RM C and back) passed on KCU116, VCU108 and VCU128 boards, the ILA captured and triggered after each (VCU108 is a Virtex UltraScale part and needs clearing bitstreams). Not verified: other boards and UltraScale+ parts, project mode on UltraScale+, any release build. The library end to end (KU5P, the same two builds) passed only for a design with no debug ports or cores before the insertion. Designs that already carried debug ports or a hub failed after the library step while the no-library controls passed: grey child `update_design -buffer_ports` (Common 17-39) for two designs, `opt_design` Opt 31-65 for a third. Cause (raw `create_debug_core` with no library, the same two builds): adding a core makes `opt_design` regenerate the existing debug hub without its 12 BSCAN ports to the partition, which stops with `Netlist 29-77`; the library's scratch core in `valid_depths` hides that error and the flow breaks later, so the skill refuses these designs. A second RM cannot be linked under a core that probes RM-internal nets (`Netlist 29-77`, 24 ports missing); a request on boundary nets only linked it, and `plan` warns with M05.

What the library does by version, set by `ila::dfx_insert_supported` (`version -short` compared as numbers, 2026.2 or later is supported; a version it cannot read counts as 2026.1):

- 2026.1 and earlier: as above. `ILA_BLOCKED_DFX_NO_BSCAN` is decided by the design alone (`dfx_gate`, called from `ila::plan`, before any core is created) and `ila::static_first_check_input` returns `ILA_REFUSED_FLAGGED` for any flagged design.
- 2026.2 and later, UltraScale+, a design with no debug core and no S_BSCAN port (`ila::dfx_insert_proceeds`): neither refusal fires. `plan` prints `NOTE D01` (proven for that design class on one KU5P design in software through route, `pr_verify` and bitstream, and on KCU116, VCU108 and VCU128 board runs), asks for no S_BSCAN ports, and puts every core in the static part, one per clock, probing partition nets across the boundary as the flag check did. A core named inside a partition is refused by Vivado (12-727, measured on 2026.2 in two test designs), so none is planned there. The static-first route accepts a flagged design with the same note.
- 2026.2 and later, UltraScale+, a design that already has a debug core, hub or S_BSCAN port: the skill keeps the 2026.1 refusals, from the design and not from Vivado. A partition without ports gets `ILA_BLOCKED_DFX_NO_BSCAN`; ports or cores present give `ILA_BLOCKED_DFX_UNSUPPORTED` whose text says it is the skill's refusal, why (such designs failed after the library step, see the version note) and the required change (instantiate the ILA in the module source, or give the design before any debug was added). The static-first route gives `ILA_REFUSED_FLAGGED`. Cores this run created do not count.
- Vivado's own refusal still decides `ILA_BLOCKED_DFX_UNSUPPORTED` (17-69 text) and `ILA_CREATE_FAILED` (other text, quoted).
- The `check_input` line `EXISTING DEBUG CORES` says `ila::apply` ends in `ILA_BLOCKED_DFX_UNSUPPORTED` for 2026.1 and earlier (Vivado's own refusal at `create_debug_core`; `ila::plan` can still succeed); on 2026.2 and later it says `ila::plan` ends in it, the skill's own refusal (a design with cores already has debug infrastructure).
- Not changed or run on 2026.2: Versal, a project session on UltraScale+ (not tested), `save_constraints` on UltraScale+ (never called), the printed steps after `opt_design` as a script of the library (they ran as the test runs' own scripts). The board runs covered the library flow for a design with no debug before insertion.
