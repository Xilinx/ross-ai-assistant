<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-44: Unreasonable user intra-clock uncertainty

## Metadata

- **Check ID**: TIMING-44
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.1

## Description

A user `set_clock_uncertainty` defined on a single clock (intra-clock) is large enough to hurt timing closure, runtime, QoR and power.

### Full Message Description property.

```
A user clock uncertainty of <MESSAGE_STRING> ns is defined on clock <CLOCK_GROUP> (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). High user clock uncertainty may adversely impact timing closure. Consider reviewing how much user clock uncertainty is required.
```
**Example:**
```
A user clock uncertainty of 1.500 ns is defined on clock clk_sys (see constraint position 7 in the Timing Constraint window in Vivado IDE). High user clock uncertainty may adversely impact timing closure. Consider reviewing how much user clock uncertainty is required.
```

### Explanation
`set_clock_uncertainty <value> [get_clocks clk]` is added on top of the tool-computed uncertainty (jitter, discrete jitter, phase error). Uncertainty directly reduces the setup slack (and hold slack with `-hold`) of every path on the clock. Values above 0.5 ns over-constrain the design, increase `route_design` runtime and power, and can make closure impossible. UG906 recommends not over-constraining beyond 0.5 ns.

**DO**
✅  Identify the exact `set_clock_uncertainty` command (no `-from`/`-to`, applied to one clock) using the clock name and value from the message.
✅  Ask why the margin was added (e.g. unmodeled jitter, deliberate margin for hardware).

**DO NOT**
❌ Silently reduce a deliberate margin without confirming intent.
❌ Treat the position number as a line number.
❌ Change inter-clock uncertainty here - that is [TIMING-45](./TIMING-45.md).

## Generate Data

1. Locate the constraint(s) in `./methodology_results/constraints.xdc`:
```sh
grep -n "set_clock_uncertainty" ./methodology_results/constraints.xdc
```
Record file, line, value, `-setup`/`-hold` switches and the clock object.
2. Show the effect on timing:
```tcl
report_clock_interaction -return_string
report_timing -max_paths 1 -clock_from <CLOCK_GROUP> -delay_type max
```
(the Clock Uncertainty row in the path shows the user + system uncertainty)
3. Capture how many paths are at or below zero slack with and without the uncertainty if the user wants evidence.

## Flow

1. Identify the constraint and its value.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The value is above 0.5 ns (any case; the intent behind the margin is unknown).
* **Prompt**:
```
User clock uncertainty of <V> ns is set on clock <CLOCK_GROUP> (<file>:<line>). Do you want to:
a) Reduce it to 0.5 ns or lower (suggested value: <V2>)
b) Remove the constraint and rely on the tool-computed uncertainty
c) Keep it and create a waiver (justification required, e.g. extra margin required for board jitter)
```

**CASE 2:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: The user has previously confirmed in this session that the uncertainty should be lowered/removed and no other constraint depends on it.
* **Assumption**: The constraint is not required to model real jitter.

1. Modify the value (or remove the constraint) in `constraints_after_fix.xdc`.
2. Report the before/after constraint and the value the user confirmed.

## Verification
* [ ] Methodology violation TIMING-44 is no longer present
* [ ] Setup (and hold, if constrained) slack on the clock improves by approximately the uncertainty removed
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Constraint | Uncertainty | Clock |
|--------|-----------|-------------|-------|
| **Before** | `set_clock_uncertainty 1.500 [get_clocks clk_sys]` | 1.500 ns | clk_sys |
| **After**  | `set_clock_uncertainty 0.300 [get_clocks clk_sys]` | 0.300 ns | clk_sys |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-44
- UltraFast Design Methodology Guide (UG949) - Clock uncertainty guidance
- [TIMING-45](./TIMING-45.md) - inter-clock uncertainty
