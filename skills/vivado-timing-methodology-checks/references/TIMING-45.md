<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-45: Unreasonable user inter-clock uncertainty

## Metadata

- **Check ID**: TIMING-45
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.1

## Description

A user `set_clock_uncertainty -from <clk1> -to <clk2>` (inter-clock) is large enough to hurt timing closure, runtime, QoR and power.

### Full Message Description property.

```
A user clock uncertainty of <MESSAGE_STRING> ns is defined between clocks <CLOCK_GROUP> and <CLOCK_GROUP> (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). High user clock uncertainty may adversely impact timing closure. Consider reviewing how much user clock uncertainty is required.
```
**Example:**
```
A user clock uncertainty of 1.000 ns is defined between clocks clk_a and clk_b (see constraint position 9 in the Timing Constraint window in Vivado IDE). High user clock uncertainty may adversely impact timing closure. Consider reviewing how much user clock uncertainty is required.
```

### Explanation
Inter-clock uncertainty is added to every path crossing from the `-from` clock to the `-to` clock. A large value over-constrains the crossing, increases `route_design` runtime and power, and can make closure impossible. The recommendation is to stay at or below 0.5 ns. It is the inter-clock counterpart of [TIMING-44](./TIMING-44.md).

If the clocks are really asynchronous the crossing should not be timed with uncertainty at all - the correct fix is clock groups/`set_max_delay -datapath_only`, not extra uncertainty.

**DO**
✅  Identify the `set_clock_uncertainty` with both `-from` and `-to` that matches both clocks and the value.
✅  Check whether the crossing is synchronous or asynchronous with `report_clock_interaction`.

**DO NOT**
❌ Reduce the uncertainty without knowing why it was added.
❌ Confuse with intra-clock uncertainty ([TIMING-44](./TIMING-44.md)).

## Generate Data

1. Locate the constraint:
```sh
grep -n "set_clock_uncertainty" ./methodology_results/constraints.xdc
```
Record file, line, value, `-setup`/`-hold`, `-from`/`-to` clocks.
2. Check the clock relationship:
```tcl
report_clock_interaction -delay_type min_max -return_string
```
Record the classification for the pair (Timed, Timed (unsafe), Asynchronous Groups, False Path, Max Delay Datapath Only).
3. Check for existing exceptions between the clocks: `report_exceptions -from [get_clocks <clk1>] -to [get_clocks <clk2>]`.

## Flow

1. Identify the constraint and classify the crossing.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The crossing is synchronous/timed and the value is above 0.5 ns.
* **Prompt**:
```
Inter-clock uncertainty of <V> ns is set from <CLOCK_GROUP> to <CLOCK_GROUP> (<file>:<line>). Do you want to:
a) Reduce it to 0.5 ns or lower (suggested value: <V2>)
b) Remove the constraint
c) Keep it and create a waiver (justification required)
```

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The crossing is intended to be asynchronous (uncertainty was added as a workaround).
* **Prompt**:
```
Crossing <CLOCK_GROUP> -> <CLOCK_GROUP> appears intended to be asynchronous. Do you want to:
a) Remove the uncertainty and add set_clock_groups -asynchronous (or set_max_delay -datapath_only) with proper CDC synchronizers
b) Reduce the uncertainty only
c) Keep as is and waive
```
Never add asynchronous constraints without confirming the synchronizer circuitry (see [TIMING-47](./TIMING-47.md)).

## Verification
* [ ] Methodology violation TIMING-45 is no longer present
* [ ] Slack of the crossing improves; clock interaction classification matches the intent
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Constraint | Uncertainty | From Clock | To Clock |
|--------|-----------|-------------|-----------|----------|
| **Before** | `set_clock_uncertainty -from [get_clocks clk_a] -to [get_clocks clk_b] 1.000` | 1.000 ns | clk_a | clk_b |
| **After**  | `set_clock_uncertainty -from [get_clocks clk_a] -to [get_clocks clk_b] 0.300` | 0.300 ns | clk_a | clk_b |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-45
- UltraFast Design Methodology Guide (UG949)
- [TIMING-44](./TIMING-44.md) - intra-clock uncertainty
