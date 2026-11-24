<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# DFX on Versal (project mode)

DFX is partial reconfiguration. The RP is the swappable slot, an RM is a module that sits in it, a configuration is the static part plus one RM. The parent configuration is built first, a child reuses the parent's static part. A greybox is an RM with no logic. UltraScale+ DFX is in `routes.md`.

## Steps for each configuration

1. The user opens it: `open_run synth_1 -name <n> -pr_config <cfg>`. The skill does not open it. Parent first, then each child with a module to probe. Give each configuration its own `-name`. Opening the next one does not close the first: the first keeps its unsaved core, and `current_design <n>` switches back to it (measured on 2026.1 and 2026.2 in a Versal DFX test project: after config_b was opened, config_a still held `rp1/u_ila_0`). So do the parent and the children in one session, one after the other; each one still needs its own save.
2. `ila::check_input` (prints one `ILA: DFX` line per partition), `ila::plan`, `ila::apply`. Both are region aware: a net inside an RP goes to a core named `<rp>/u_ila_N` on the RP-side clock, a static net to `u_ila_N`.
3. `ila::apply` ends by printing the user's steps; `ila::next_steps` prints them again. In a DFX project they name the open configuration's own run (`child_b_impl_1` for a child, not `impl_1`) and end at `write_device_image`:
   - `save_constraints` (or `write_checkpoint -force <file>.dcp` of the open configuration)
   - `reset_run <run of this configuration>`
   - `launch_runs <run> -to_step write_device_image`
   - optionally `write_debug_probes -force <file>.ltx` in the implemented design (or `ila::write_probes <file>`)
4. The skill saves and runs nothing. The DFX implementation of that configuration is the user's. Child runs use `-to_step write_device_image`, so `pr_verify` runs.

## What to tell the user

- The save is the user's, and so is the DFX implementation of that configuration. Synthesis stays up to date: the saved debug XDC is implementation-only.
- The implementation run does `opt_design` itself.
- The parent and all children show Out-of-date after the save. That is normal.
- A run that was already complete needs `reset_run` first. Resetting the parent resets every child run.
- A static core is built by the parent and all child runs. A module core is built only by that configuration's run.
- Force up-to-date on the implementation runs keeps old results without the new ILA.
- `save_constraints` writes the debug cores into their own constraint files (one for the static part, one per module with a core) and may rewrite the user's own constraint files in the project (same constraints, different text). Tell the user to keep a copy.

## Measured (Vivado 2026.1 and 2026.2, a Versal DFX test project, parent `config_a` and child `config_b`)

- Saved and reopened, both versions: after check_input, plan, apply and `save_constraints`, then `close_project`, `open_project` and `open_run -pr_config`, the parent still holds `rp1/u_ila_0`, `rp1/axi_dbg_hub_0` and `axi_dbg_hub_0`. So does the child. A `write_checkpoint` of the open configuration reopens with the same three cores. `opt_design` then runs and `ila::write_probes` writes the file; before `opt_design` it refuses with `ILA_PROBES_NOT_READY`.
- `save_constraints` wrote `design_1_wrapper_debug.xdc` (static) and `rp1_rm_a_inst_0_debug.xdc`, `rp1_rm_b_inst_0_debug.xdc` (modules). It rewrote the user's pblock XDC (`CLOCKREGION_X3Y2` became `CLOCKREGION_X3Y2:CLOCKREGION_X3Y2`, two blank lines more).
- The printed `reset_run child_b_impl_1` and `launch_runs child_b_impl_1 -to_step write_device_image` were accepted (accepted by Vivado with the scripts-only launch option). Not run: a full DFX implementation (place, route, device image).

## Rules

- Static cores only while the PARENT is open. In a child the project run does not read the static debug XDC, so the core is silently dropped: `ILA_BLOCKED_DFX_STATIC_IN_CHILD`. Module requests in a child are fine.
- A greybox gives N18 and `NOTHING TO INSERT`. That is a success.
- Hubs must already be in the design (AXI Debug Hub in static and in each module that holds a core). No hub: `ILA_BLOCKED_DFX_NO_HUB`. The skill adds no hub, no port, no tag and edits no source.
- The RP needs a pblock to implement (without one the parent stops at place with DRC HDPR-30). Say so. Do not call it optional.
- Untagged nets work: the saved XDC carries the MARK_DEBUG lines itself. Do not tell the user to tag nets.
- A parent with hubs and no inserted ILA is fine for the tools. Hardware effect is not tested.
- After the user's `save_constraints` the saved XDC is applied when the configuration is opened again: a second `plan` in that session first needs the old core removed (`ila::reset`).
- A synthesized design with block-design hubs is not "optimized": `check_input` reads the Design State line, so no false "already optimized" warning.

## After a partial swap: which probes file to load

- Every configuration has its own probes file, written by the user while that configuration is open after `opt_design` (`write_debug_probes -force <file>.ltx`, or `ila::write_probes <file>`): static cores and that RM's cores. Vivado also writes `<name>_pblock_<rp>_partial.ltx` itself in a DFX run.
- After you load the partial image of configuration X, load the full probes file of X.
  - KCU116: `parent.ltx` after RM A, `child.ltx` after RM B (measured: the full image, then four partial swaps).
  - VCK190: the full file and the partial file of X both worked (measured).
- On UltraScale+ do not load Vivado's `<name>_pblock_<rp>_partial.ltx`. It is an empty probe set (239 bytes for both RMs on KCU116).
- Do not load the file of another configuration. The files do not tell Hardware Manager which RM is loaded: the ILA name and uuid are the same in every configuration. On KCU116 the probe names are the same too. On VCK190 RM A has 32 probe bits and RM B has 16.
- What Hardware Manager shows when the wrong file is loaded, measured on UltraScale+ boards (a design with a static ILA and a second child configuration, RM C loaded, Vivado 2026.2, two KCU116 boards and one VCU128 board, one run each):
  - The file of RM A and the file of RM B both attach without a message. The ILA enumerates, the probes carry the same names and widths, and a capture works. On this design the files are identical for the static ILA, so a wrong choice cannot be seen.
  - Vivado's own `<name>_pblock_<rp>_partial.ltx` gives the warning `Labtools 27-1974` (mismatch between the design in the device and the probes file), the ILA is still enumerated (1 ILA), and a capture stops with `Labtools 27-188` (use refresh_hw_device with a valid probes file). The probe list printed after that refresh still showed the earlier names. Whether that is a stale list is not established.
- A greybox configuration has an empty partial file. Static cores, if there are any, are in its full file.

## Not covered

- Versal DFX board run: one, on a VCK190 with Vivado 2026.1: full image, one partial image loaded with xsdb, the ILA captured and triggered after it. Not shown: the decoupler flow, a swap back, a hub-only module, a static ILA. Other families: see `routes.md`.
- UltraScale+ DFX board evidence beyond the first KCU116 run: the same flow on VCU128 and, as a Virtex UltraScale part that needs clearing bitstreams, VCU108; and a design with a static traffic counter that kept running through every swap, a second child configuration (RM C), and probes on the module's own counter bits, on two boards each of KCU116, VCU108 and VCU128, up to two runs per board. One design shape per part. Not shown: data flowing through the partition during the swap (no decoupler, and the swap call blocks Hardware Manager), a second module under a core that probes nets inside the module (it does not link, `Netlist 29-77`), any build but 2026.2 daily builds.
- UltraScale+ DFX: see `routes.md`. Vivado 2026.1 and earlier refuse insertion; Vivado 2026.2 and later (daily builds tested) do not, and this skill still refuses a design that already carries debug ports or cores (on such designs the flow failed after insertion).
