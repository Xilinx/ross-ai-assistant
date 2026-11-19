<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Advice on a routed design with debug cores (read only)

Library: `scripts/ila_advise.tcl`. It stands alone: source only that file. It needs no other file of this skill.

## When

- The user has a routed design that already holds debug cores and asks about a timing problem. Example: "is there a critical path in my ILA logic?"
- Open the routed checkpoint (`open_checkpoint <routed.dcp>`), then run `ila::advise`.
- It changes nothing. It runs no implementation step, no fix, writes no file and changes no setting.

## What it prints (every line starts with `ILA ADVISE`)

- The design, part and state.
- The debug cores: kind, hub clock frequency, ILA depth, probe bits and ports, input pipe stages. Read from `report_debug_core`.
- The worst setup path: slack, requirement, clock and period (ns and MHz, from the design; a clock name says nothing about its frequency), logic levels, start pin, end pin.
- Whether the path touches a debug core, which one, and which kind of path: debug hub, capture memory control, register interface to the hub, other ILA logic.
- When the path runs through a net an ILA probes: the probe port (for example `probe0[7]`), the net, whether your register drives it, and how many loads the net has (and how many are inside the core).
- How many of the 10 worst setup paths touch debug logic.
- The worst hold path, and a note when hold fails.
- `HINT n:` lines, `NOTE:` lines, or one `NO HINT:` line. Then the limits.

## Refusal

- A design that is not routed gives `ILA_ADVISE_NOT_ROUTED: the design state is '<state>'`. Nothing else is printed.

## Hint rules

Each hint is printed only when its measured fact is true. A path that meets timing gets no hint.

| Fact the rule reads | Hint | Source |
|---|---|---|
| The path is in the debug hub, non-Versal, `C_ENABLE_CLK_DIVIDER` is false | Set `C_CLK_INPUT_FREQ_HZ` to the hub clock's frequency, enable `C_ENABLE_CLK_DIVIDER`, use a free-running clock. Names the hub clock net and the MHz set in the core (`C_CLK_INPUT_FREQ_HZ`, the default is 300 MHz; it is not measured from the clock), and the clock the path is timed on with its period and MHz from your constraints | UG908, ILA Core and Timing Considerations |
| The path is in the capture memory control and depth is above 1024 | A lower capture depth. At 1024: a note, no hint | UG908, choose data depth judiciously |
| The path is in the capture memory control of an AXIS-ILA with `C_MEMORY_TYPE` 0 | UltraRAM storage | UG908, same section |
| The path is in other ILA logic, or enters the ILA from your logic, and `C_INPUT_PIPE_STAGES` is below 6 | More input pipe stages (names the current value, up to 6). At 6: a note | UG908, same section |
| The path is in the capture memory control only | No pipe stage hint. A note says input pipe stages did not move this path kind on this skill's measured runs | This skill's measured runs |
| The path is in the memory control, other ILA logic, or enters the ILA, and the core has probe bits | Fewer or narrower probes. Names the probe bits and ports | UG908, choose probe width; UG1388, limit the number and width of signals |
| The path is in an ILA core | A slower capture clock. Names the clock, period, requirement and the miss | General timing practice. UG908 asks for a synchronous capture clock |
| The path runs through a net an ILA probes | Add a register stage in your design, or probe the register output. Names the register, probe and load counts | Design practice. Probe loading is not measured |
| The path is in the user's logic and no probed net is on it | `NO HINT`: the ILA's part is not measured, that needs a build without the ILA | none |
| Hold fails | A note. No hint: hold has no defined fix | none |

## Limits (printed with every answer)

- Only the worst setup path decides. The 10 worst paths are counted, not read.
- Probe loading on your own nets is not measured. The load counts are pin counts.
- A few hundredths of a nanosecond is noise (single runs). A miss under 0.05 ns gets a note.
- Hold has no defined fix. The register interface and hub paths have no probe-side fix.
- Nothing is run or changed. A hint is text, never an action.

## Reads it uses

`report_timing_summary -no_detailed_paths -return_string` (for the design state and the WNS table), `report_debug_core -return_string` (the channel table that maps probe bits to nets), `get_timing_paths`, `get_cells`, `get_nets` (including `-segments`), `get_pins`, `get_clocks`, `get_property`, `current_design`.
