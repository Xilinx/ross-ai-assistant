<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-31: Multicycle path on phase shifted clock

## Metadata

- **Check ID**: TIMING-31
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A phase-shifted MMCM/PLL output clock is involved in multicycle path constraints, but the CMB models phase shift as **latency** (`PHASESHIFT_MODE=LATENCY`). The legacy multicycle constraints that compensated for the shifted waveform are probably no longer needed and can now be wrong.

### Full Message Description property.

```
The MMCM (or PLL) generated clock <CLOCK_GROUP> is phase shifted and is involved in one or several multicycle path constraints. Since the MMCM (or PLL) property PHASESHIFT_MODE is set to LATENCY, the legacy multicycle path constraint(s) may not be needed anymore
```
**Example:**
```
The MMCM (or PLL) generated clock clk_out2_clk_wiz_0 is phase shifted and is involved in one or several multicycle path constraints. Since the MMCM (or PLL) property PHASESHIFT_MODE is set to LATENCY, the legacy multicycle path constraint(s) may not be needed anymore
```

### Explanation
| PHASESHIFT_MODE | Behavior | Multicycle needed for the shift? |
|-----------------|----------|-----------------------------------|
| `WAVEFORM` (7 series and UltraScale default) | Clock edges are shifted in the waveform | Often yes |
| `LATENCY` (UltraScale+ and Versal default) | Phase shift modeled as latency through the CMB; waveform unchanged | No |

Moving a design from a WAVEFORM device to a LATENCY device (or changing the property) leaves multicycle constraints that now over-relax or mis-time the path. This is a Critical Warning because the result can hide real violations.

**DO**
✅  Read `PHASESHIFT_MODE` and the output phase (`CLKOUTn_PHASE`) on the CMB
✅  List every multicycle constraint that references the clock (directly or through derived clocks)
✅  Check why each multicycle exists (phase shift vs real multi-cycle data transfer)

**DO NOT**
❌ Remove a multicycle that implements a real multi-cycle datapath (enable-based) that is unrelated to the phase shift
❌ Change `PHASESHIFT_MODE` without the user's agreement (it changes timing for every path on the clock)

## Generate Data

1. Get the CMB and its properties:
```tcl
set cmb [get_cells -of_objects [get_pins <CMB output pin of CLOCK_GROUP>]]
get_property PHASESHIFT_MODE $cmb
get_property CLKOUT<n>_PHASE $cmb
```
2. Get the multicycle constraints involving the clock: `report_exceptions -file ./methodology_results/exceptions.rpt`, plus search `constraints.xdc` for `set_multicycle_path` referencing <CLOCK_GROUP> or its derived clocks.
3. For each, capture `-setup`/`-hold`, value, `-start`/`-end`, source/destination clocks and file/line.
4. Compare the path requirement with and without the constraint using `report_timing -from -to`.

## Flow

1. Find all multicycle constraints referencing <CLOCK_GROUP>.
2. Read `PHASESHIFT_MODE` and phase of the CMB output.
3. Decide with the user whether the multicycle constraints exist only to compensate the phase shift.

All resolutions require design intent, so this check has no automated case.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The multicycle constraint was added to compensate the phase shift (WAVEFORM-era constraint)
* **Prompt**:
```
Do you want to:
a) Remove the multicycle path constraint(s) (LATENCY mode times the phase shift correctly)
b) Keep the legacy behavior: set PHASESHIFT_MODE=WAVEFORM on the CMB and keep the constraints
c) Leave as is and create a waiver
```
Option (b) uses
```tcl
set_property PHASESHIFT_MODE WAVEFORM [get_cells <cmb_cell>]
```

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: The multicycle implements a real multi-cycle transfer (clock-enable or slow data rate) in addition to the phase shift
* **Explanation**: Ask the user to recompute the multiplier for LATENCY mode and show `report_timing` before/after.
* **Examples**:
```
poldark 01/26/26 "Multicycle implements a 2-cycle enable-based datapath; phase shift is handled by latency"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_timing` on the affected paths shows the intended path requirement
* [ ] No new unconstrained or failing paths introduced by removing/changing the multicycle
* [ ] No new TIMING-57 (PHASESHIFT_MODE/deskew) violation if the mode was changed

## Final Report

| Aspect | Constraint / Property | Value |
|--------|-----------------------|-------|
| **Before** | `set_multicycle_path 2 -setup -end -from [get_clocks clk_a] -to [get_clocks clk_out2]` | PHASESHIFT_MODE=LATENCY |
| **After** | *(constraint removed)* | PHASESHIFT_MODE=LATENCY |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-31 (MMCM/PLL Phase Shift Modes)
- Vivado Design Suite User Guide: Using Constraints (UG903) - Multicycle Paths
- [TIMING-29](./TIMING-29.md) - Inconsistent pair of multicycle paths (related check)
- [TIMING-57](./TIMING-57.md) - PHASESHIFT_MODE with digital deskew (related check)
