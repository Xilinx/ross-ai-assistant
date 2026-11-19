<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-30: Sub-optimal master source pin selection for generated clock

## Metadata

- **Check ID**: TIMING-30
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A generated clock is tied to a master source pin much farther upstream than the clock that actually feeds the generated-clock cell. Timing analysis can be pessimistic and extra clock uncertainty may be applied between master and generated clock.

### Full Message Description property.

```
The generated clock <CLOCK_GROUP> has a sub-optimal master source pin selection, timing can be pessimistic
```
**Example:**
```
The generated clock clk_div2 has a sub-optimal master source pin selection, timing can be pessimistic
```

### Explanation
* `create_generated_clock -source <pin>` accepts any upstream pin, but the best choice is the pin of the **direct** upstream clock of the generating cell.
* If another clock-defining element (for example a BUFGCE_DIV, MMCM, or other generated clock) sits between the chosen `-source` and the generating cell, the tool times against the farther master and pessimism can result.

**DO**
✅  Compare the `-source` pin of the generated clock with the direct driver of the generating cell's clock input
✅  Check that nothing between the two pins changes frequency or phase before changing `-source`

**DO NOT**
❌ Change `-source` when a clock-modifying element (MMCM/PLL/divider/another generated clock) lies between the old and new pins without recomputing `-divide_by`/`-multiply_by` (use CASE 2)
❌ Automate when FILE_NAME or LINE_NUMBER of the generated clock is null

## Generate Data

1. Capture the generated clock definition:
```tcl
set gclk [get_clocks <CLOCK_GROUP>]
get_property SOURCE_PINS $gclk
get_property MASTER_CLOCK $gclk
get_property FILE_NAME $gclk
get_property LINE_NUMBER $gclk
```
2. Find the target pin / generating cell and the clock pin of that cell; trace the driver of its clock input (`CLOCK_TRACING.md`, `get_timing_arcs`).
3. List clock-defining objects on the path between the current `-source` pin and the generating cell: `get_clocks -of_objects <intermediate pins>`.
4. Compare the master clock with the clock reaching the direct driver.

## Flow

1. Find the `create_generated_clock` constraint in `constraints.xdc` and confirm FILE_NAME / LINE_NUMBER match.
2. Trace upstream from the generating cell to the closest pin that carries the master clock.
3. Select a case.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Between the current `-source` pin and the nearest upstream pin only buffers/routing exist (no frequency or phase change, same master clock on both)
* **Assumption**: Same master clock and same multiply/divide relation

Replace `-source` with the nearest upstream pin; keep every other option:
```tcl
create_generated_clock -name <CLOCK_GROUP> -source [get_pins <nearest_upstream_pin>] <existing -divide_by/-multiply_by/-edges options> [get_pins <target_pin>]
```

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: A clock modifying block or another generated clock lies between the current master and the nearest upstream pin (relation to the closer clock differs)
* **Prompt**:
```
The generated clock <CLOCK_GROUP> is defined from <old source>, but the clock reaching the generating cell is <closer clock>. Do you want to:
a) Re-reference to the closer master pin and set -divide_by/-multiply_by to <proposed values> (shown for review)
b) Leave as is and create a waiver
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] Generated clock period and waveform are unchanged (`report_clocks`)
* [ ] No new TIMING-1/3/5/36 warnings are created

## Final Report

| Aspect | Constraint | Period | Waveform | Clock Definition Object |
|--------|-----------|--------|----------|------------------------|
| **Before** | `create_generated_clock -name clk_div2 -source [get_ports clk_in] -divide_by 2 [get_pins div_reg/Q]` | 20.000 ns | {0 10.000} | clk_div2 |
| **After** | `create_generated_clock -name clk_div2 -source [get_pins div_reg/C] -divide_by 2 [get_pins div_reg/Q]` | 20.000 ns | {0 10.000} | clk_div2 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-30
- Vivado Design Suite User Guide: Using Constraints (UG903) - Generated Clocks
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
