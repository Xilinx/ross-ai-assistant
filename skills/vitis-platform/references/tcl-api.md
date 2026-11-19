<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis PFM Tcl API Reference

This skill drives the `::vitis::pfm::*` Tcl API — a concise inspection/application
layer for discovering and assigning Vitis platform (PFM) properties on an open Vivado
block design.

The API ships in Vivado 2026.2 (`scripts/ipintegrator/sdsoc_pfm.tcl`). Vivado defines the commands when
IP Integrator initializes, so they appear only after a block design is created or opened.
For earlier releases this skill bundles the same code in
[`scripts/autopfm.tcl`](../scripts/autopfm.tcl). `scripts/platform_defaults.tcl` sources it
when the commands are missing with a block design open (see SKILL.md step 2).

All commands run in the context of the current project / current block design. Do not
fabricate results — issue the commands through the Vivado Tcl interface and read back the
actual returns.

## Inspection APIs

Each inspection proc discovers candidate BD objects for a given PFM category and returns a
Tcl **list of dicts**. Pass returned elements **unchanged** to the matching application proc.

| Function | Discovers | Return dict keys |
| --- | --- | --- |
| `::vitis::pfm::get_memory_candidates` | `axi_noc`/`smartconnect` cells whose master side reaches a NoC memory controller (`MC_<n>`, HBM), any external port, or a `memory`-usage address segment (AI Engine array, BRAM controllers) | `cell`, `kind`, `resources` (list of reachable memory resources) |
| `::vitis::pfm::get_control_candidates` | `axi_noc`/`smartconnect` cells whose slave side traces back to any AXI master of a `versal_cips`, `ps_wizard` or `psx_wizard` cell or, when the design has none, to any external AXI slave port (off-chip processor) | `cell`, `kind`, `resource` (list of driving interfaces) |
| `::vitis::pfm::get_clock_candidates` | Clock pins that drive a `proc_sys_reset` `slowest_sync_clk` | `cell`, `kind`, `resource` (dict with `clock` pin and `reset` cell) |
| `::vitis::pfm::get_interrupt_candidates` | Undriven pins of type interrupt | `cell`, `kind`, `resource` (interrupt pin) |
| `::vitis::pfm::get_stream_candidates` | Undriven AXI4-Stream master/slave interfaces | `cell`, `kind`, `resource` (AXIS interface) |

- `cell` — the BD cell the property will be set on.
- `kind` — the `name` field of the cell's VLNV.
- `resource` / `resources` — the accessible resource(s) the candidate exposes.

The external-port branches above never fire: the commands follow only a pin of the
opposite mode, and a block-design port has the same mode as the pin it connects to.
`scripts/platform_defaults.tcl` wraps the two affected functions. Pass each result through
its wrapper and use the wrapper's list in their place; elements keep the same keys:

| Wrapper | Adds | Keeps |
| --- | --- | --- |
| `::vitis_platform::control_candidates` | SmartConnect/NoC cells whose slave side reaches an external AXI slave port with an assigned aperture; the port joins `resource` | candidates with at least one terminal that is not a port, or is a port with an aperture; only the non-NoC ones when any remain |
| `::vitis_platform::memory_candidates` | SmartConnect/NoC cells whose master side reaches an external AXI master port with an assigned aperture; the port joins `resources` | same rule, minus a processor-driven NoC with no controller of its own whose resources other candidates all offer |
| `::vitis_platform::clock_candidates` | the clock wizard's other `<clk>_o<n>` outputs, with the reset of the slowest one when the candidate is that slowest one | every candidate, sorted by pin |
| `::vitis_platform::interrupt_candidates` | nothing | input pins; only the non-processor ones when any remain |

`::vitis_platform::set_clock_status` adds `status` to every declared `PFM.CLOCK` entry that
has none: `fixed_non_ref` on an MBUFGCE output, `fixed` otherwise. `set_clock` cannot set it.

A port has an assigned aperture when its `APERTURES` property is nonempty, or when it has
an assigned (not excluded) address segment: a slave port through its address space, a
master port through the segments mapped onto its own.

## Application APIs

Each application proc takes **two arguments**: (1) one element returned by the matching
inspection proc, exactly as returned; (2) a dict of additional instructions. The procs
check no keys: a key left out is left out of the underlying `set_property` without an
error. "Always pass" below marks keys the skill must supply for a usable platform.

| Function | Instruction dict keys | Sets |
| --- | --- | --- |
| `::vitis::pfm::set_memory` | `sptag` (always pass); `auto` = `true`/`false`/`preferred`; `hbm` | `PFM.AXI_PORT` |
| `::vitis::pfm::set_control` | `sptag` (leave empty, per XD101); `auto` = `true`/`false`/`preferred` | `PFM.AXI_PORT` |
| `::vitis::pfm::set_clock` | `id` (always pass; integer ≥ 0, unique); `is_default` = `true`/`false` | `PFM.CLOCK` |
| `::vitis::pfm::set_interrupt` | `id` (always pass; integer 0–127, unique); `range` | `PFM.IRQ` |
| `::vitis::pfm::set_stream` | `sptag` (always pass) | `PFM.AXIS_PORT` |

## Usage pattern

```tcl
# 0. With a block design open, load the helpers; this also loads the bundled copy of the
#    commands when Vivado lacks them, and returns builtin or bundled
source {<absolute path of the skill directory>/scripts/platform_defaults.tcl}

# 1. Discover
set memory_candidates [::vitis_platform::memory_candidates [::vitis::pfm::get_memory_candidates]]

# 2. Apply the first candidate, unchanged, with an instruction dict (skip if the list is empty)
::vitis::pfm::set_memory [lindex $memory_candidates 0] [dict create sptag DDR0 auto preferred]
```

## Deterministic defaults (when the user delegates the choice)

`::vitis_platform::apply_defaults` applies all of these in one call and returns the declared cells per category plus every `PFM.*` property it set (SKILL.md step 5). The options `-memory_sptag`, `-stream_sptag`, `-clock_first_id`, `-default_clock` and `-interrupt_first_id` replace the tag, starting ID or default clock for a user who takes every resource but names those values:

- Memory: every `::vitis_platform::memory_candidates` result (DDR/HBM, AI Engine NSUs and apertured external-port memory; BRAM controllers are dropped). `sptag` `AIE` with `auto false` on the AI Engine array; `<DDR|LPDDR|HBM><n>` on each NoC with its own controllers, from the one the processor maps lowest, which gets `auto false`; the bare type (or `MEMORY` for mixed types) with `auto preferred` on a NoC aggregating two or more others; `MEMORY<n>` on the rest. Without an aggregating NoC, the first controller NoC not shared with the PS is preferred. Then `::vitis_platform::trim_noc_ports` on each NoC, which drops slots at or above `NUM_PL_NMU + NUM_AIE_NMU` (SKILL.md step 4).
- Control: every `::vitis_platform::control_candidates` result (SmartConnect cells that trace back to a processor AXI master or an apertured external slave port, or NoCs when there is none); no `sptag` (control ports keep it empty, per XD101); `auto preferred`.
- Zynq UltraScale+ PS: `::vitis_platform::declare_zynq_ps_ports <n>`, which declares the FPD HPM masters (`M_AXI_HPM0_LPD` only when neither is free) and every HP/HPC slave the design does not connect, including ones the PS configuration disables (SKILL.md step 6).
- Clock: unique `id` from `0` in pin order; `is_default true` on the clock closest to 300 MHz; `status` from `::vitis_platform::set_clock_status`.
- Interrupt: `::vitis_platform::interrupt_candidates`; unique `id` from `0`; `range 32` on an `axi_intc` `intr`, which takes 32 IDs; other pins omit `range`. IDs stop at 127.
- Stream: unique `sptag` `STREAM0`, `STREAM1`, ...

## Command naming requirements

- Inspection: `::vitis::pfm::get_(memory|control|clock|interrupt|stream)_candidates`
- Application: `::vitis::pfm::set_(memory|control|clock|interrupt|stream)`
