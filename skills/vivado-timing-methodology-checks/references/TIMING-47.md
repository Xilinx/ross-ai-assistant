<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-47: False path, asynchronous clock group or max delay datapath only constraint between synchronous clocks

## Metadata

- **Check ID**: TIMING-47
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

A `set_false_path`, `set_clock_groups -asynchronous`, or `set_max_delay -datapath_only` is applied between two clocks that Vivado considers synchronous (they share a common master and have a known phase relationship). Masking a synchronous crossing leaves it untimed and can cause failure in hardware.

### Full Message Description property.

```
A <MESSAGE_STRING> timing constraint is set between synchronous clocks <CLOCK_GROUP> and <CLOCK_GROUP> (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). Masking entire synchronous clock domains via  set_false_path, set_clock_groups or set_max_delay -datapath_only may result in failure in hardware.
```
**Example:**
```
A clock groups timing constraint is set between synchronous clocks clk1 and clk2 (see constraint position 6 in the Timing Constraint window in Vivado IDE). Masking entire synchronous clock domains via  set_false_path, set_clock_groups or set_max_delay -datapath_only may result in failure in hardware.
```
* First `<MESSAGE_STRING>` - constraint kind (false path / clock groups / max delay datapath only)

### Explanation
Clocks from the same MMCM/PLL (or a clock and its divided/generated clock) are synchronous: the tool knows their phase relationship and times the crossing with real setup/hold requirements. A false path, asynchronous group or datapath-only max delay discards that check, so a real timing failure is not reported and sign-off is inaccurate. Often the constraint was copied from a design where the clocks were independent.

**DO**
✅  Verify the crossing classification with `report_clock_interaction` before proposing any change.
✅  Identify whether the clocks truly come from different sources in the *hardware* (different oscillators) or the constraint is a mistake.
✅  Confirm the synchronization circuitry exists if the user says the clocks are asynchronous.

**DO NOT**
❌ Remove the constraint automatically when the user may intend asynchronous behavior (e.g. a PLL that is not actually phase-locked to its input).
❌ Add `set_clock_groups -asynchronous` to "make the warning go away".

## Generate Data

1. Locate the constraint: `grep -n -E "set_false_path|set_clock_groups|set_max_delay" ./methodology_results/constraints.xdc` and record file, line, kind, clock objects.
2. Classify the crossing:
```tcl
report_clock_interaction -delay_type min_max -return_string
```
3. Show the clock sources/masters (`report_clocks`, `<skill_dir>/references/CLOCK_TRACING.md`) and confirm they share a master (same MMCM/PLL/BUFGCE_DIV/primary clock).
4. Check whether the paths are CDC-safe: `report_cdc -from [get_clocks <clk1>] -to [get_clocks <clk2>]`.

## Flow

1. Identify the constraint and classify the crossing.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The clocks are generated from the same MMCM/PLL/clock buffer, no CDC synchronizers exist between them (no `ASYNC_REG` chain, no handshake), and the constraint is a standalone `set_clock_groups -asynchronous` / `set_false_path -from clk1 -to clk2` that covers only these two clocks.
* **Assumption**: The clocks are really synchronous in hardware.

1. Remove the constraint from `constraints_after_fix.xdc`.
2. Report removed constraint and new timed paths; warn that new setup/hold violations may appear and are real.
3. State "If this assumption is incorrect, then please resolve the issue manually"

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Any other situation (clock groups include more than two clocks, constraint also covers other asynchronous clocks, synchronizers exist, or intent is unclear).
* **Prompt**:
```
A <kind> constraint (<file>:<line>) exists between synchronous clocks <CLOCK_GROUP> and <CLOCK_GROUP>. Do you want to:
a) Remove the constraint and time the crossing (clocks are synchronous)
b) Keep the constraint because the clocks are not related in hardware, and fix the clocking (e.g. use different sources) and add CDC synchronizers
c) Narrow the exception to specific synchronized paths only
d) Leave it and create a waiver (justification required)
```
* **Examples**:
```
poldark 01/26/26 "clk1/clk2 are from the same MMCM but the crossing is a quasi-static register bank with a documented handshake"
```

## Verification
* [ ] Methodology violation TIMING-47 is no longer present
* [ ] `report_clock_interaction` shows the pair as timed (not "Asynchronous Groups"/"False Path"/"Max Delay Datapath Only")
* [ ] Any newly exposed violations were reviewed (they are real)
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Constraint | Clock Pair | Interaction |
|--------|-----------|------------|-------------|
| **Before** | `set_clock_groups -asynchronous -group [get_clocks clk1] -group [get_clocks clk2]` | clk1 / clk2 | Asynchronous Groups |
| **After**  | removed | clk1 / clk2 | Timed |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-47
- Vivado Design Suite User Guide: Using Constraints (UG903) - Clock Groups, False Paths
- [TIMING-54](./TIMING-54.md) - scoped false path / clock groups between clocks
