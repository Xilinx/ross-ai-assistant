---
name: vitis-platform
description: Annotate an open, validated Vivado block design with Vitis platform (PFM) properties. Discover and set PFM.CLOCK, PFM.AXI_PORT, PFM.IRQ and PFM.AXIS_PORT with the ::vitis::pfm commands, and set the platform identity. Use for "add PFM properties", "annotate this block design for Vitis" or "make this block design a Vitis platform". It does not build or change the design and does not export the XSA.
license: MIT
compatibility: Requires a Vivado Tcl session (Vivado MCP server or Tcl console), Vivado 2025.2 or later. Vivado 2026.2 ships the ::vitis::pfm commands; earlier releases use the bundled copy. Tested on Versal and Zynq UltraScale+ with 2025.2 and 2026.2.
metadata:
  version: "1.1.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Annotate a Vivado Block Design as a Vitis Platform

This skill adds Vitis platform (PFM) annotation to an existing IP Integrator block design: the interfaces the `::vitis::pfm` commands discover, and the platform identity. Its input is an open block design that already validates. It does not create, connect or reconfigure IP, generate output products, create wrappers, synthesize, or write the XSA.

Run this workflow through the user's Vivado Tcl interface. Do not invent command results. If no Tcl execution tool is available, give the commands in small stages and ask the user to paste each stage's output before interpreting it.

Keep backslashes out of Tcl sent through the Vivado MCP server: version 0.6.9 delivers every `\` as `/`, so a pattern such as `{MC_\d+}` silently matches nothing. Use `[0-9]` and similar bracket classes instead.

Read [references/tcl-api.md](references/tcl-api.md) before issuing candidate or application commands. Read [references/platform-pitfalls.md](references/platform-pitfalls.md) before annotating a platform that `v++ --link` will use.

## Errors and design changes

The only changes this skill makes are the PFM properties on block-design cells, the platform identity properties, and saving the block design. Never edit the user's block design to get past an error or a missing interface: do not add or remove IP, connect or disconnect pins, or change an IP's configuration, even when the fix looks obvious. When a command fails or the design lacks something, stop, report the exact error and the last successful step, propose a fix, and wait for the user to make it or approve it.

## Bundled files

- [scripts/autopfm.tcl](scripts/autopfm.tcl): the `::vitis::pfm::*` Tcl commands for Vivado releases before 2026.2. Its code matches the copy that ships in Vivado 2026.2. `scripts/platform_defaults.tcl` sources it when Vivado does not already define the commands (step 2).
- [scripts/platform_defaults.tcl](scripts/platform_defaults.tcl): `::vitis_platform::*` procedures that apply the step 4 limitations and declare the Zynq UltraScale+ PS ports. Source it on every release (step 2).
- [references/tcl-api.md](references/tcl-api.md): inspection and application command reference.
- [references/platform-pitfalls.md](references/platform-pitfalls.md): platform failures seen in real `v++` builds.

## Workflow

Maintain progress across turns and resume at the first unfinished step.

### 1. Require an open, validated block design

Ask the user to open in Vivado the block design they want to annotate. Stop until they confirm it is open. If they have no block design yet, the `ipi-assembler` skill builds one. Then verify:

```tcl
version -short
current_project
current_bd_design
validate_bd_design
```

Do not proceed if `current_project` or `current_bd_design` returns empty or errors. Record the Vivado version for step 2.

`validate_bd_design` must pass. Two messages are expected and do not block: a critical warning for an unconnected interrupt-controller input such as `axi_intc_0/intr` (`v++` connects kernel interrupts to it at link time), and `[Project 1-1878] ... SELECTED_SIM_MODEL must be set to 'TLM'` on Versal NoC designs (it affects only hardware emulation). Critical warnings about interfaces the design leaves open for kernels, such as an undriven NoC `S_AXI` or `[BD 41-1630]` for an interface whose segments are all excluded, do not block either; only errors do. For any error, follow "Errors and design changes": the user fixes the design, then this step runs again.

### 2. Load the PFM Tcl commands

Run this only after step 1, with the block design open: Vivado 2026.2 defines the commands when IP Integrator initializes. Source the skill's helper script on its own, as one `vivado_execute` call containing only this line. Build the path from the absolute path of the directory that contains this `SKILL.md`; never hard-code a path from another machine. Vivado must be able to read the file, so if Vivado runs on a different host, copy the `scripts` directory there first.

```tcl
source {<absolute path of this skill directory>/scripts/platform_defaults.tcl}
```

It loads the skill's helper procedures and, only when Vivado does not provide the `::vitis::pfm` commands, the bundled copy in `scripts/autopfm.tcl`. It returns `builtin` or `bundled`; record that value. Never load `autopfm.tcl` directly: over the shipped commands it would replace them. The braces must match: a stray `]` in place of the closing `}` leaves Vivado waiting for the rest of the command, and every later command times out.

If it raises an error, stop and report it. The bundled copy is tested on Vivado 2025.2. On an older release, tell the user it is untested there and offer the manual path in step 6.

### 3. Set the platform identity

Obtain a platform identifier in exactly this form:

```text
vendor:board:name:major.minor
```

Require nonempty `vendor`, `board`, and `name` fields and integer `major` and `minor` components. Ask for a corrected value if invalid. Never fill in a missing field yourself, even when the user asks for defaults: defaults cover interface choices, not the platform's identity. Until you have a valid identifier, set no properties.

Apply the settings the Vitis platform creation tutorial (XD101) and UG1701 use. `PFM_NAME` is a property of the block-design **file** object (not the `current_bd_design` object); the rest are project properties. Set all four design intents explicitly, not only `embedded`:

```tcl
set bd_file [get_files [get_property FILE_NAME [current_bd_design]]]
set_property PFM_NAME {vendor:board:name:major.minor} $bd_file
set_property platform.extensible true [current_project]
set_property platform.design_intent.embedded true [current_project]
set_property platform.design_intent.server_managed false [current_project]
set_property platform.design_intent.external_host false [current_project]
set_property platform.design_intent.datacenter false [current_project]
set_property platform.default_output_type sd_card [current_project]
set_property platform.vendor {vendor} [current_project]
set_property platform.board_id {board} [current_project]
set_property platform.name {name} [current_project]
set_property platform.version {major.minor} [current_project]
```

Use braces around the identifier after substituting the validated value. If the user names a different output type, use theirs instead of `sd_card`; Vivado 2026.2 accepts `sd_card`, `xclbin`, `hw_export`, `qspi` and `undefined`. The last four lines repeat the `PFM_NAME` fields as project properties. The first `write_hw_platform` in a project stores them there, and later exports keep the stored values, so without these lines a later export under a new `PFM_NAME` still writes the old identity into the XSA (seen on 2025.2 and 2026.2). Some AMD example designs, such as the Kria starter kit example, already set `platform.extensible false` and their own identity; these lines replace those values.

### 4. Discover and check the interfaces

Run all five discovery functions, apply the filters, and retain the returned list elements unchanged:

```tcl
set control_candidates   [::vitis_platform::control_candidates [::vitis::pfm::get_control_candidates]]
set clock_candidates     [::vitis_platform::clock_candidates [::vitis::pfm::get_clock_candidates]]
set memory_candidates    [::vitis_platform::memory_candidates [::vitis::pfm::get_memory_candidates]]
set interrupt_candidates [::vitis_platform::interrupt_candidates [::vitis::pfm::get_interrupt_candidates]]
set stream_candidates    [::vitis::pfm::get_stream_candidates]
list control $control_candidates clock $clock_candidates memory $memory_candidates interrupt $interrupt_candidates stream $stream_candidates
```

Each category has a requirement level:

| Category | What discovery finds | Level |
| --- | --- | --- |
| Control | SmartConnect or AXI Interconnect cells whose slave side traces back to a processor's AXI master (`versal_cips`, `ps_wizard` or `psx_wizard` `M_AXI*`), or to an external AXI slave port with an assigned aperture, for a processor that is off-chip. NoCs only when no such interconnect qualifies. | Mandatory |
| Clock | Clock pins that drive a `proc_sys_reset` `slowest_sync_clk`, with that reset block, plus the clock wizard's other `<clk>_o<n>` MBUFGCE outputs when the reset is on the slowest of them. In clock wizard pin order. | Mandatory |
| Memory | NoC or SmartConnect cells whose master side reaches a memory resource: NoC DDR or HBM controllers, the AI Engine array's NSUs, other memory-usage address segments, or external AXI master ports with an assigned aperture. BRAM controllers do not count: AMD's Zynq UltraScale+ and Kria platforms declare the PS ports, not the SmartConnect in front of a BRAM, so a cell that reaches only BRAM is not offered. The processor's own NoC (no controller of its own, driven by the processor, and reaching only memory other candidates offer) is dropped. Expected means at least one candidate reaches a DDR memory controller. | Expected |
| Interrupt | Undriven interrupt inputs: a PL interrupt controller's `intr`, or, when there is none, processor inputs such as `pl_ps_irq*` | Expected |
| Stream | Free AXI4-Stream master and slave interfaces | Optional |

When a list is empty, ask the user to clarify before you declare anything:

- **Mandatory (control, clock):** stop. Name the empty category, explain what discovery looks for, and ask the user either to fix the design so discovery finds it, or to name the cell and interface to declare through the manual path in step 6. Rerun step 4 after a design change.
- **Expected (memory, interrupt):** report it and ask whether to continue without that category or to resolve it first, as for a mandatory category.
- **Optional (stream):** tell the user none were found and continue.

On Zynq UltraScale+, discovery does not see the `zynq_ultra_ps_e` cell, so control and memory are usually empty. When the design has one `zynq_ultra_ps_e` cell, the PS ports from `::vitis_platform::declare_zynq_ps_ports` (step 6) supply the control and memory interfaces: tell the user, and do not treat those empty lists as missing.

Present every candidate, grouped by category and numbered from zero. Explain its `cell`, `kind`, and `resource` in design-specific terms. Also explain the cross-category roles:

- Control paths let software on the processor configure accelerators.
- Memory paths give accelerators access to buffers and addressable storage.
- Clocks and their associated resets define timing/reset domains for platform logic.
- Interrupts let hardware signal asynchronous events to software.
- Streams expose direct AXI4-Stream data paths; they complement rather than replace memory-mapped control and memory.

Explain observed relationships from shared cells/resources and connectivity, but do not claim connectivity absent from the returned dictionaries.

Known limitations of the commands, present in both the 2026.2 and bundled versions:

- **External ports are not followed.** The commands reach a peer only through a pin of the opposite mode, but a block-design port has the same mode as the pin it connects to, so they never return a path that ends at an external port. `::vitis_platform::control_candidates` and `memory_candidates` add the SmartConnect and NoC cells whose paths end at external ports. They also drop any candidate whose only terminals are external ports without an assigned aperture: a nonempty `APERTURES` property, or an assigned (not excluded) address segment. If the user expects a cell that is not offered, check whether its path ends at a port without an aperture and tell them; assigning the aperture is their change to make.

- **Clocks are found only through a `proc_sys_reset`.** The commands return one clock per reset block. `::vitis_platform::clock_candidates` adds the MBUFGCE siblings of a clock wizard output (`clk_out1_o1` to `clk_out1_o4` share one reset on the slowest, `clk_out1_o4`, in AMD's VEK385 platform), because they are divided copies of one MMCM output. Any other clock that drives no `slowest_sync_clk`, such as a reserved clock-wizard output, is not offered, even when it is the platform's intended default clock. Vivado needs exactly one default clock (`[BD 41-2088]`). Tell the user which clock sources discovery did not report, and ask which to declare; do not invent or drop clocks.
- **Every PS-reachable NoC is a control candidate.** On a Versal PS the commands offer every NoC the PS reaches (five on the VEK385). AMD's platforms declare control only on the SmartConnect, and `v++` cascades more as needed, so `::vitis_platform::control_candidates` keeps the SmartConnect and AXI Interconnect cells when there are any.
- **The processor's own NoC is a memory candidate.** A NoC that the PS drives directly and that reaches other NoCs' controllers over INI (`Master_NoC` on the VEK385) offers no memory of its own. `::vitis_platform::memory_candidates` drops it when every resource it reaches is offered by another candidate.
- **Zynq UltraScale+ PS ports are not discovered.** The commands look for `versal_cips`, `ps_wizard` and `psx_wizard` only, so the `zynq_ultra_ps_e` HPM masters and HP/HPC slaves are never offered. Declare them with `::vitis_platform::declare_zynq_ps_ports` (step 6), which includes any the PS configuration leaves disabled.
- **DFX platforms get no candidates.** In a DFX platform (UG1701 "DFX-Based Hardware Platform", such as the `versal_dfx_platform` example), memory is exported from an AXI NoC inside the Vitis region block design container, which reaches DDR over INI. That block design has no PS and no memory controller, so the commands return no memory or control candidates for it. Declare DFX platform interfaces as the example's `pfm_decls.tcl` does, or tell the user this workflow does not cover them.
- **Interrupt discovery can list an interrupt controller's own output.** For example, `axi_intc` returns both `intr` (its input, the real candidate) and `irq` (its output). `::vitis_platform::interrupt_candidates` keeps the candidates whose pin `DIR` is `I`. Processor inputs such as `pl_ps_irq*` are valid: XD101 allows them for simple designs, up to 16. When a PL interrupt controller qualifies, the wrapper drops the processor inputs, as AMD's platforms declare only the controller.
- **`set_memory` on a NoC declares too many ports.** It declares `S<nn>_AXI` slots up to `NUM_PL_NMU + NUM_AIE_NMU + CONFIG.NUM_SI`. Vivado 2026.2 flags ports at or above `NUM_PL_NMU + NUM_AIE_NMU` with `[BD 41-2940] ... not in the supported range`, but they are still declared. AMD's VEK385 platform provisions each memory NoC "for up to AIE_NMU + PL_NMU". After `set_memory` on a NoC, drop the slots at or above `NUM_PL_NMU + NUM_AIE_NMU`; the procedure returns how many slots remain:

```tcl
::vitis_platform::trim_noc_ports [get_bd_cells <noc cell>]
```

### 5. Apply the annotation

Ask the user which numbered candidates to apply and ask for required per-candidate values. They may select any or all categories. If the user says they do not care, requests defaults, or delegates the choice, apply every default in one call:

```tcl
::vitis_platform::apply_defaults
```

It selects every candidate from step 4 (as filtered there) and declares the defaults below, including the Zynq UltraScale+ PS ports. Its `pfm_api` value says which copy of the commands ran: `bundled` when step 2 loaded `autopfm.tcl`, `builtin` when Vivado provided them; report that value, not a guess. Declare nothing else afterwards: no further `set_*` calls and no `PFM.*` properties set by hand. If it raises an error, report it; do not fall back to declaring ports yourself. The defaults it applies:

- Memory: every memory candidate from step 4, each NoC then trimmed with `::vitis_platform::trim_noc_ports`. Tags follow AMD's VCK190 and VEK385 platforms:
  - The AI Engine array: `AIE`, `auto false`, so kernels opt in to the AIE memory map.
  - Each NoC with its own controllers: `<type><n>` (`DDR`, `LPDDR` or `HBM`), numbered from the NoC the processor maps at its lowest address, then by name. That one is shared with the PS and gets `auto false`.
  - A NoC that reaches the controllers of two or more other NoCs: the bare type (`LPDDR`, or `MEMORY` for mixed types) and `auto preferred`. Without one, the first controller NoC not shared with the PS is preferred.
  - Anything else, such as a SmartConnect to an external memory port: `MEMORY0`, `MEMORY1`, ...
- Control: every control candidate from step 4. No `sptag` and `auto preferred`. XD101 keeps control-port `sptag` empty because it applies only to AXI slave interfaces.
- Zynq UltraScale+ PS: `::vitis_platform::declare_zynq_ps_ports <n>` (step 6), where `<n>` is the number of `MEMORYn` tags already used.
- Clock: unique IDs starting at `0` in clock wizard pin order. `is_default true` on the clock closest to 300 MHz (lower ID on a tie): AMD's platforms default to 312.5 MHz, and `v++` compiles HLS kernels at the default clock. `status fixed_non_ref` on MBUFGCE outputs (`<clk>_o<n>`), which cannot be a reference clock, and `fixed` on the rest.
- Interrupt: the candidates `::vitis_platform::interrupt_candidates` returns; unique IDs starting at `0`. An `axi_intc` `intr` input gets `range 32` (UG1701) and takes 32 IDs; other pins omit `range`.
- Stream: unique `sptag` values `STREAM0`, `STREAM1`, ....

If the user wants every resource but names tags or numbering, still use one `apply_defaults` call and pass their values as options; anything they leave out keeps its default above. For example, "tag every memory port DDR, number the clocks from 3 and the interrupts from 5":

```tcl
::vitis_platform::apply_defaults -memory_sptag DDR -clock_first_id 3 -interrupt_first_id 5
```

- `-memory_sptag <tag>`: one `sptag` on every memory port, the Zynq UltraScale+ PS ports included.
- `-stream_sptag <tag>`: one `sptag` on every stream port.
- `-clock_first_id <n>`: the first clock's ID; the rest count up from it.
- `-default_clock <pin>`: the default clock's pin, such as `/clk_wizard_0/clk_out2`; it must be a clock candidate.
- `-interrupt_first_id <n>`: the first interrupt's ID; the rest count up from it.

If the selected interrupts need IDs past 127, do not silently truncate them: ask the user which to declare, because interrupt IDs are limited to 0 through 127. `apply_defaults` raises an error when the IDs would pass 127.

When the user picks individual candidates or gives values per candidate, pass each selected candidate's original list element from step 4 directly to its matching `set_*` function. Do not reconstruct or edit candidate dictionaries, and index the lists exactly as step 4 built them. An interrupt output that `::vitis_platform::interrupt_candidates` drops is not applicable: do not pass it to `set_interrupt` and do not set `PFM.IRQ` on its cell by hand. Replace each `<...>` with the user's value for that candidate:

```tcl
::vitis::pfm::set_memory [lindex $memory_candidates <i>] [dict create sptag <memory tag> auto preferred]
::vitis::pfm::set_control [lindex $control_candidates <i>] [dict create auto preferred]
::vitis::pfm::set_clock [lindex $clock_candidates <i>] [dict create id <clock id> is_default <true for the default clock>]
::vitis::pfm::set_interrupt [lindex $interrupt_candidates <i>] [dict create id <interrupt id> range <32 for an axi_intc intr, else omit>]
::vitis::pfm::set_stream [lindex $stream_candidates <i>] [dict create sptag <stream tag>]
```

Construct one call per selection. After each `set_memory` on a NoC, run `::vitis_platform::trim_noc_ports` on it. After the clocks, run `::vitis_platform::set_clock_status`: `set_clock` writes no `status`, and the procedure adds `fixed` or `fixed_non_ref` as in the defaults above. A port's tag is its `sptag`, the name `v++ --connectivity.sp` uses: when the user names a tag for memory or stream ports, put it in `sptag`, not in `memory`, which names the address segment. Several ports may share one `sptag`. Validate all application arguments against the API reference before execution.

Then save the block design and read back every `PFM.*` property, so the report shows what the design holds rather than what was requested:

```tcl
save_bd_design
set pfm {}
foreach cell [get_bd_cells -hierarchical] {
  foreach p [list_property $cell PFM.*] {
    set v [get_property $p $cell]
    if {$v ne ""} { lappend pfm [list $cell $p $v] }
  }
}
list PFM_NAME [get_property PFM_NAME [get_files [get_property FILE_NAME [current_bd_design]]]] PFM $pfm
```

Report the annotation as complete only when the read-back shows `PFM_NAME`, at least one `PFM.CLOCK` with exactly one `is_default true`, and a control `PFM.AXI_PORT`, or the user has accepted a missing category in step 4.

### 6. Manual path

Use this path when the user names an interface discovery did not find (such as the Zynq UltraScale+ PS ports), or when the commands cannot be loaded. Set the PFM property directly on the owning cell with `set_property`, using the syntax in UG1701 "Adding Hardware Interfaces". Confirm it for the user's release with `vivado_doc_search` before running it. Each PFM property type must be set in one `set_property` call per cell, because a second call replaces the first.

On Zynq UltraScale+, XD101 exports the PS interfaces the design leaves unused: HPM masters as control and HP/HPC slaves as memory, as in the AMD `MPSoC_Ext_Platform` example. XD101 uses `M_AXI_HPM0_LPD` for platform control and keeps `M_AXI_HPM0_FPD` and `M_AXI_HPM1_FPD` for kernels, so declare `M_AXI_HPM0_LPD` only when neither FPD master is free. This includes interfaces that are disabled in the PS configuration and so have no pin in the block design: `v++` enables a declared PS interface when it links a kernel to it. Do not skip a port because the PS does not show it, and do not change the PS configuration. Skip only interfaces the design already connects. `::vitis_platform::declare_zynq_ps_ports` applies these rules and declares the ports in a single `set_property` call. Pass the number of `MEMORYn` tags already used (0 if none), and show the user the returned `PFM.AXI_PORT` value:

```tcl
::vitis_platform::declare_zynq_ps_ports 0
```

Then save and read back as at the end of step 5.

## Next steps

The annotated block design is the input to the hardware platform export, which this skill does not run. Tell the user:

- **Export the XSA:** `write_hw_platform` without `-fixed` writes an extensible XSA from the annotated design, after the design is generated and has an HDL wrapper as top (UG1701, XD101). On flat designs a pre-synthesis XSA serves both hardware and hardware emulation. Projects with Segmented Configuration enabled, such as on VEK385 and VRK160, have two export flows (UG1701 "Platform Support for Segmented Configuration Flow"). For deferred load, set `platform.platform_state pre_synth` and export before synthesis. For dynamic reload of the PL, implement through `write_device_image`, open the implemented run, then run `write_hw_platform -hw`; that XSA carries the routed checkpoint and the NoC boot paths. Read that section with `vivado_doc_search` before running either flow. The `segcfg-aie` skill covers designs that also use AI Engines.
- **Software platform (`.xpfm`):** `vitis-embedded`, platform step, with the exported XSA.
- **Kernel linking with `v++`:** `vitis-system`, against the `.xpfm`.
