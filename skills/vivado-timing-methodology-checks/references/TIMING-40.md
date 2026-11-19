<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-40: Max Skew violation between OSERDESE3 CLK and CLKDIV pins crossing SLRs

## Metadata

- **Check ID**: TIMING-40
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2017.3

## Description

An OSERDESE3 has a max skew violation between its CLK and CLKDIV pins and the clock networks cross SLRs to reach those pins. UltraScale/UltraScale+ multi-SLR devices only.

### Full Message Description property.

```
A Max Skew violation exists between CLK and CLKDIV pins on the OSERDESE3 instance <NETLIST_ELEMENT> and the clock networks are crossing SLRs to reach the clock pins. It is not recommended to drive the OSERDESE3 CLK and CLKDIV from clock sources placed in another SLR. Please check your design
```
**Example:**
```
A Max Skew violation exists between CLK and CLKDIV pins on the OSERDESE3 instance u_io/oserdes_i and the clock networks are crossing SLRs to reach the clock pins. It is not recommended to drive the OSERDESE3 CLK and CLKDIV from clock sources placed in another SLR. Please check your design
```

### Explanation
CLK and CLKDIV of an OSERDESE3 have a hardware max skew requirement. If the clock buffers driving CLK and CLKDIV are in a different SLR from the OSERDESE3, the clock paths cross the SLR boundary and the extra, differing delay can exceed the skew limit. This is a clock topology/placement problem; it cannot be fixed by timing constraints.

**DO**
✅  Find the clock buffers driving CLK and CLKDIV and their SLR location versus the OSERDESE3.
✅  Check for `CLOCK_DEDICATED_ROUTE` / `USER_CLOCK_ROOT` / `USER_SLR_ASSIGNMENT` properties already set.

**DO NOT**
❌ Add a timing exception to hide the violation.
❌ Modify placement constraints without user confirmation.

## Flow
1. Report the max skew violation and clock sources.
```tcl
set o [get_cells <NETLIST_ELEMENT>]
foreach pin {CLK CLKDIV} {
  set net [get_nets -of_objects [get_pins $o/$pin]]
  set drv [get_cells -of_objects [get_pins -leaf -of_objects $net -filter {DIRECTION==OUT}]]
  puts "$pin driver: $drv slr=[get_property SLR_INDEX $drv] site=[get_property SITE $drv]"
}
puts "oserdes slr: [get_property SLR_INDEX $o]"
report_clock_utilization -file ./methodology_results/clock_util.rpt
report_pulse_width -file ./methodology_results/pw.rpt
```
2. If the design is only synthesized, placement is not yet known: report that placement information is not available and ask the user to re-run after place_design.

**DO NOT**
❌ Automate a fix when placement data is not available.

3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: CLK and/or CLKDIV drivers are in a different SLR from the OSERDESE3.
* **Prompt**:
```
OSERDESE3 <cell> is in SLR<n> but its <CLK|CLKDIV> clock buffer is in SLR<m>. Do you want to:
a) Constrain the clock buffers to the same SLR as the OSERDESE3 (e.g. LOC / pblock on the BUFG / BUFGCE_DIV)
b) Move the OSERDESE3 and its I/O into the SLR of the clock sources
c) Redesign the clocking so CLK and CLKDIV are driven from the same SLR (e.g. BUFGCE_DIV derived from the same input)
d) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "I/O bank only exists in SLR2; clocks already generated locally in SLR2, violation is reported before placement"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] CLK and CLKDIV drivers are in the same SLR as the OSERDESE3
* [ ] No max skew violation on the OSERDESE3 in `report_timing_summary`

## Final Report
Print a table that shows the difference in:
* OSERDESE3 SLR
* CLK driver and SLR
* CLKDIV driver and SLR

| Aspect | OSERDESE3 | CLK Driver (SLR) | CLKDIV Driver (SLR) |
|--------|-----------|------------------|---------------------|
| **Before** | u_io/oserdes_i (SLR2) | bufg_clk (SLR1) | bufg_div (SLR1) |
| **After** | u_io/oserdes_i (SLR2) | bufg_clk (SLR2) | bufg_div (SLR2) |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-40
- UltraScale Architecture SelectIO Resources User Guide (UG571), OSERDESE3
