<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-29: Inconsistent pair of multicycle paths

## Metadata

- **Check ID**: TIMING-29
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A setup and a hold `set_multicycle_path` pair covering the same paths reference different clock edges (`-start` vs `-end`). Setup and hold of a pair should reference the **same** clock so the path requirement shifts consistently.

### Full Message Description property.

```
Setup and hold multicycle path constraints should typically reference the same -start pair for SLOW-to-FAST synchronous clocks or -end pair for FAST-to-SLOW synchronous clocks (see constraint positions <MESSAGE_STRING>, <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE)
```
**Example:**
```
Setup and hold multicycle path constraints should typically reference the same -start pair for SLOW-to-FAST synchronous clocks or -end pair for FAST-to-SLOW synchronous clocks (see constraint positions 7, 8 in the Timing Constraint window in Vivado IDE)
```

### Explanation
* By default setup multipliers reference the **destination** clock (`-end`) and hold multipliers the **source** clock (`-start`).
* For multiples between different frequencies the pair must reference the same clock or the hold relationship is placed incorrectly.
* **Note on wording:** the message text and the UG906 *Resolution* text disagree on which option goes with which direction. The UG906 Resolution matches UG903 and is the rule used here:

| Crossing | Setup and hold should both use |
|----------|--------------------------------|
| SLOW source to FAST destination | `-end` (destination clock) |
| FAST source to SLOW destination | `-start` (source clock) |

* If the clocks are the same frequency (or clearly not slow/fast related) the check is informative and the pair only needs consistent options.

**DO**
✅  Determine source/destination clock periods to classify SLOW-to-FAST vs FAST-to-SLOW
✅  Identify both constraints from the two positions in the message (setup and hold)
✅  Keep the multipliers' values unless the user confirms they should change

**DO NOT**
❌ Change a setup multiplier silently (that alters the intended path requirement)
❌ Apply the rule to paths that are asynchronous

## Generate Data

1. Locate both constraints (positions in message) in `constraints.xdc`; capture `-setup`/`-hold`, `-start`/`-end`, value, `-from`, `-to`, `-through`.
2. Get the periods:
```tcl
get_property PERIOD [get_clocks <from_clock>]
get_property PERIOD [get_clocks <to_clock>]
```
3. Review exceptions: `report_exceptions -file ./methodology_results/exceptions.rpt`
4. Confirm the clocks are synchronous: `report_clock_interaction`.

## Flow

1. Find the setup and hold multicycle constraints from the two positions.
2. Classify the crossing: slow-to-fast (source period > destination period) or fast-to-slow.
3. Compare the reference option of each constraint to the table in Explanation.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The setup constraint already uses the correct reference for the crossing, the hold value is `setup value - 1`, and only the hold reference option is inconsistent
* **Assumption**: The user wrote the standard setup N / hold N-1 pair and intended the hold to move with the same clock as the setup

Modify the hold constraint to use the same reference as setup:
```tcl
# slow-to-fast
set_multicycle_path <N-1> -hold  -end   -from [get_clocks <slow>] -to [get_clocks <fast>]
# fast-to-slow
set_multicycle_path <N-1> -hold  -start -from [get_clocks <fast>] -to [get_clocks <slow>]
```
Report the assumption. State "If this assumption is incorrect, review the multicycle values manually".

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Any other mismatch (setup uses the wrong reference, values do not follow the N / N-1 pattern, only one of the pair exists, or the paths are covered by other overlapping exceptions)
* **Prompt**:
```
Do you want to:
a) Change both constraints to the recommended reference (-end for slow-to-fast, -start for fast-to-slow) and recompute the values
b) Change only the hold to match the setup reference
c) Leave as is and create a waiver
```
Show the resulting path requirement before and after (`report_timing -from ... -to ...`).

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_timing` on a path in the group shows the expected setup and hold requirement
* [ ] No new TIMING-5x constraint-overlap warnings are created

## Final Report

| Aspect | Setup constraint | Hold constraint |
|--------|------------------|-----------------|
| **Before** | `set_multicycle_path 2 -setup -end -from [get_clocks clk_slow] -to [get_clocks clk_fast]` | `set_multicycle_path 1 -hold -from [get_clocks clk_slow] -to [get_clocks clk_fast]` |
| **After** | `set_multicycle_path 2 -setup -end -from [get_clocks clk_slow] -to [get_clocks clk_fast]` | `set_multicycle_path 1 -hold -end -from [get_clocks clk_slow] -to [get_clocks clk_fast]` |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-29
- Vivado Design Suite User Guide: Using Constraints (UG903) - Multicycle Paths
- [TIMING-31](./TIMING-31.md) - Multicycle path on phase shifted clock (related check)
