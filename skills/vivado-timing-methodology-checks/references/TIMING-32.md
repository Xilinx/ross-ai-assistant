<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-32: Bus skew constraint applied on too many signals

## Metadata

- **Check ID**: TIMING-32
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A `set_bus_skew` constraint covers more than 2500 paths (UltraScale/UltraScale+/Versal) or 1000 paths (7 series) between a clock pair. Meeting bus skew requires routing detours on individual signals, which causes congestion, failed skew targets, and long `route_design` runtime.

### Full Message Description property.

```
Bus skew constraint is set on too many signals from clock <CLOCK_GROUP> to clock <CLOCK_GROUP> (> 2500 paths for Ultrascale/Ultrascale+/Versal or 1000 paths for 7-Series) (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Bus skew constraint is set on too many signals from clock clk_wr to clock clk_rd (> 2500 paths for Ultrascale/Ultrascale+/Versal or 1000 paths for 7-Series) (see constraint position 14 in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: u_cdc/data_sync_reg[0]
```

### Explanation
* `set_bus_skew` makes the router add delay to individual signals to keep their relative arrival within a limit.
* The cost grows with the number of signals; very wide buses (typically a data bus constrained as if it were a gray-coded pointer) are the common cause.
* The recommended CDC pattern constrains only the **few signals that need skew control** (for example pointers or handshakes), not an entire wide data bus.

**DO**
✅  Count the paths covered by the constraint
✅  Identify what the signals are (data, gray-coded pointer, control)
✅  Review whether every signal needs the skew requirement

**DO NOT**
❌ Reduce the object list automatically (which signals need skew is a design decision)
❌ Loosen the skew value to avoid the warning (use TIMING-34 guidance for values)

## Generate Data

1. Find the `set_bus_skew` constraint at the given position in `constraints.xdc` (file, line, value, `-from`, `-to`).
2. Count covered paths and endpoints:
```tcl
report_bus_skew -file ./methodology_results/bus_skew.rpt
get_cells -quiet <objects of -from>
```
3. Classify the signals (data vs pointer) from names and driving logic; check for XPM_CDC cells in the crossing: `get_property XPM_CDC [get_cells <cell>]` (non-empty for cells inside an XPM_CDC macro).
4. Report CDC structure: `report_cdc -details -file ./methodology_results/cdc.rpt`.

## Flow

1. Locate the constraint and capture its value, object lists, and clocks.
2. Count and classify signals.
3. Select a case. **No automated resolution** exists: the signal list defines CDC correctness.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The constraint covers a wide data bus plus pointers/handshakes
* **Prompt**:
```
Do you want to:
a) Narrow the set_bus_skew objects to only the signals needing skew control (for example the gray-coded pointers), and constrain the data bus with set_max_delay -datapath_only
b) Restructure the CDC interface (for example asynchronous FIFO, or XPM_CDC_GRAY / handshake) to reduce the number of skew-controlled signals (RTL change)
c) Leave as is and create a waiver
```

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: Low
* **When**: All covered signals genuinely require bounded skew
* **Explanation**: Offer a waiver `<USER> - <DATE> - <REASON>`
* **Examples**:
```
poldark 01/26/26 "All 3000 signals form one ADC sample bus that must align at the destination; routing impact reviewed"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_bus_skew` path count is below the threshold, or the waiver is recorded
* [ ] No new TIMING-33/34/37/38/39 warnings are created
* [ ] `route_design` runtime/congestion not worsened (if implementation data is available)

## Final Report

| Aspect | Constraint | Paths covered |
|--------|-----------|---------------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/data_reg[*]] -to [get_cells u_cdc/data_sync_reg[*]] 2.000` | 3200 |
| **After** | `set_bus_skew -from [get_cells u_cdc/gray_ptr_reg[*]] -to [get_cells u_cdc/gray_sync_reg[*]] 2.000` | 12 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-32
- Vivado Design Suite User Guide: Using Constraints (UG903) - Bus Skew
- [TIMING-33](./TIMING-33.md), [TIMING-34](./TIMING-34.md), [TIMING-37](./TIMING-37.md), [TIMING-38](./TIMING-38.md), [TIMING-39](./TIMING-39.md) - Other bus skew checks
