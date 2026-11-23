<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

````markdown
# TIMING-7: No common node between related clocks

## Metadata

- **Check ID**: TIMING-7
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Detects when two clocks that have timing paths between them (are related/timed together) do not share any common node in the clock network, indicating potentially problematic clock relationships or missing constraints.

## Message

```
The clocks <CLOCK_GROUP> and <CLOCK_GROUP> are related (timed together) but they have no common node. The design could fail in hardware. To find a timing path between these clocks, run the following command: report_timing -from [get_clocks <CLOCK_GROUP>] -to [get_clocks <CLOCK_GROUP>]
```

**Example:**
```
The clocks bftClk and wbClk are related (timed together) but they have no common node. The design could fail in hardware. To find a timing path between these clocks, run the following command: report_timing -from [get_clocks bftClk] -to [get_clocks wbClk]
```

## Explanation

This violation occurs when:
1. **Data paths exist** between two clock domains (registers or ports)
2. **No common node** exists in the clock network topology
3. **Clock relationship is undefined** - Vivado cannot establish timing correlation

### Difference from TIMING-6

| Check | Focus | Issue |
|-------|-------|-------|
| **TIMING-6** | Clock hierarchy | No common primary/master clock in the clock tree |
| **TIMING-7** | Physical topology | No common physical node or connection point |

**TIMING-7** is typically more severe - it indicates clocks that are completely independent in both logical hierarchy and physical implementation.

### Why This Matters

When clocks have no common node:
- ❌ **No phase relationship** can be inferred
- ❌ **Random phase offset** at power-up
- ❌ **Metastability risk** on all clock domain crossings
- ❌ **Timing analysis may be incorrect** - assumes relationships that don't exist in hardware

### Common Root Causes

1. **Independent Input Clocks**: Multiple clock inputs driving separate domains
2. **Missing Clock Constraints**: Asynchronous relationship not declared
3. **External Clock Sources**: Off-chip clocks with no phase correlation
4. **Design Bug**: Unintended clock domain crossing without CDC circuitry

## Generate Data

Extract and analyze the clock relationship:

**From Violation Object:**
```tcl
# Get violation details
set violation [get_methodology_violations TIMING-7#1]
set description [get_property DESCRIPTION $violation]

# Parse clock names from description
# Expected format: "The clocks <clk1> and <clk2> are related..."
```

**Analyze Clock Network:**
```tcl
# Get clock objects
set clk1 [get_clocks bftClk]
set clk2 [get_clocks wbClk]

# Check clock properties
foreach clk [list $clk1 $clk2] {
    puts "Clock: [get_property NAME $clk]"
    puts "  Period: [get_property PERIOD $clk]"
    puts "  Sources: [get_property SOURCES $clk]"
    puts "  Is Generated: [get_property IS_GENERATED $clk]"
    if {[get_property IS_GENERATED $clk]} {
        puts "  Master Clock: [get_property MASTER_CLOCK $clk]"
    }
    puts "  Waveform: [get_property WAVEFORM $clk]"
}

# Find root sources
set src1 [get_property SOURCES $clk1]
set src2 [get_property SOURCES $clk2]

# Trace clock networks
report_clock_networks -clocks $clk1
report_clock_networks -clocks $clk2
```

**Find Timing Paths:**
```tcl
# Report paths between the clock domains
report_timing -from [get_clocks $clk1] -to [get_clocks $clk2] \
    -max_paths 20 -nworst 1 -path_type full_clock_expanded \
    -file ./tmp/timing_cross_clk1_to_clk2.rpt

report_timing -from [get_clocks $clk2] -to [get_clocks $clk1] \
    -max_paths 20 -nworst 1 -path_type full_clock_expanded \
    -file ./tmp/timing_cross_clk2_to_clk1.rpt
```


**Check Existing Constraints:**
```tcl
# Review current clock group definitions
report_clock_interaction -delay_type min_max -file ./tmp/clock_interaction.rpt

# Look for existing false paths or max delays
report_exceptions -ignored -from [get_clocks $clk1] -to [get_clocks $clk2]
```

## Flow

1. **Gather Data** → Identify clock sources and analyze network topology
2. **Analyze** → Determine if clocks are properly isolated or missing constraints
3. **Resolve** → Apply appropriate constraints based on the true relationship
4. **Verify** → Confirm clock relationship is properly defined

### Detailed Resolution Steps

#### Step 1: Verify Clock Independence

**Check if clocks are truly independent:**

```tcl
# Compare clock sources
set src1 [get_property SOURCES [get_clocks clk1]]
set src2 [get_property SOURCES [get_clocks clk2]]

if {$src1 eq $src2} {
    puts "ERROR: Clocks share same source but no common node detected!"
    puts "  This indicates a clock definition problem."
} else {
    puts "Clocks have independent sources"
    puts "  Source 1: $src1"
    puts "  Source 2: $src2"
}
```

#### Step 2: Identify Clock Relationship Type

| Scenario | Clock Sources | Relationship | Action |
|----------|---------------|--------------|--------|
| **Different input ports** | Separate ports | Asynchronous | `set_clock_groups -asynchronous` |
| **Different Pins** | Different pins | Asynchronous | `set_clock_groups -asynchronous` after resolving TIMING-1, TIMING-2, TIMING-3, TIMING-4|
| **Same PLL, different outputs** | Shared MMCM | Check if synchronous | May need `create_generated_clock` fix |
| **Same source, wrong definition** | Should be related | Fix clock constraints | Re-define clocks properly |

#### Step 3: Apply Resolution

**CASE 1: Asynchronous Clocks (Most Common)**
- **Type**: Automated Resolution
- **When**: Clocks are truly independent with no timing relationship

```tcl
# Mark clocks as asynchronous
set_clock_groups -asynchronous \
    -group [get_clocks <clk1>] \
    -group [get_clocks <clk2>]
```

Provide alternative `set_max_delay -datapath_only` constraints
```tcl
# datapath_only constraints
set_max_delay -datapath_only -from [get_clocks <clk1>] -to [get_clocks <clk2>]
set_max_delay -datapath_only -from [get_clocks <clk2>] -to [get_clocks <clk1>]
```

**DO:**
✅ Document why these clocks are asynchronous
✅ Flag a warning that this will hide timing failures between related clocks from the user. 
✅ When setting `set_clock_groups` recommend that a user runs `report_cdc` after to verify cross clock paths
✅ Use `report_clock_interaction` to capture how many paths will now no longer be timed between the specfied clocks. Capture both directions and add it, break it out for setup and hold.


**CASE 2: Physically Related (Same PLL) - Fix Clock Definitions**
- **Type**: User Resolution
- **When**: TIMING-7 appears but clocks should be related

Resolve using TIMING-2 methodology.

**CASE 3: Logically Related but Physically Separate**
- **Type**: User Resolution  
- **When**: Clocks are meant to be synchronous but physically isolated. E.g., OOC 

Ignore this case. Report that the skill does not support OOC runs.


**CASE 4: Create Waiver (Rare)**
- **Type**: User Resolution
- **When**: Violation is understood and acceptable

```tcl
create_waiver -type METHODOLOGY -id {TIMING-7} \
    -objects [get_methodology_violations TIMING-7#1] \
    -user [get_property USER [current_project]] \
    -description "Clocks clk1 and clk2 are from independent PCB oscillators. All crossings use proper XPM_CDC primitives. Clock relationship explicitly defined as asynchronous."
```

### DO:
**DO:**
✅ Document why these clocks are asynchronous
✅ Flag a warning that this will hide timing failures between related clocks from the user. 
✅ When setting `set_clock_groups` recommend that a user runs `report_cdc` after to verify cross clock paths
✅ Use `report_clock_interaction` to capture how many paths will now no longer be timed between the specfied clocks. Capture both directions and add it, break it out for setup and hold.
✅ Provide `set_clock_groups -asynchronous` or `set_max_delay -datapath_only`

### DONT:
**DONT:**
❌ Ignore this violation - indicates potential metastability issues
❌ Assume clocks are related when they have no common node
❌ Fix TIMING-7 without also checking for TIMING-6 and TIMING-8


## Verification

* [ ] Methodology violation TIMING-7 is no longer present
* [ ] Related TIMING-6 and TIMING-8 violations also resolved
* [ ] `report_clock_interaction` shows intended clock relationship

**Verification Script:**
```tcl

# Verify clock relationships
report_clock_interaction -delay_type min_max \
    -file ./tmp/clock_interaction_after_fix.rpt

```

## Final Report Guidance

Document your resolution comprehensively:

### Clock Topology Analysis

| Clock | Source | Type | Period | Root Node |
|-------|--------|------|--------|-----------|
| `bftClk` | `CLK_BFT` port | Primary | 10.000 ns | External source A |
| `wbClk` | `CLK_WB` port | Primary | 8.000 ns | External source B |

**Finding**: No common node - clocks originate from independent external sources

### Resolution Applied

```tcl
# Added to constraints.xdc
# Line: 145

set_clock_groups -asynchronous \
    -group [get_clocks bftClk] \
    -group [get_clocks wbClk] \
    -comment "Independent external oscillators with no phase relationship"

# Justification:
# - bftClk drives the BFT processing logic (125 MHz)
# - wbClk drives the Wishbone interface (100 MHz)
# - No hardware synchronization between clock sources
# - All crossings protected by XPM_CDC_ARRAY_SINGLE synchronizers
```

### Verification Results

Before Fix:
- ❌ TIMING-7#1: bftClk ↔ wbClk  
- ❌ TIMING-7#2: wbClk ↔ bftClk
- ⚠️  32 unconstrained clock crossings

After Fix:
- ✅ TIMING-7 violations resolved
- ✅ Clock interaction report shows "Asynchronous" relationship
- ✅ Message that captures how many paths need looking at in `report_cdc`

### Design Impact

**Timing Analysis:**
- Clock domains now properly isolated
- No timing paths analyzed between bftClk and wbClk domains
- CDC paths excluded from timing (as intended)


## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques (Chapter 2: Timing Constraints, Clock Domain Crossings)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug903-vivado-using-constraints.pdf) - Using Constraints (Chapter 5: Timing Constraints, `set_clock_groups`)
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology (CDC Guidelines, Metastability)
- [UG953](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug953-vivado-7series-libraries.pdf) - XPM_CDC Library Guide
- [CLOCK_TRACING.md](./CLOCK_TRACING.md) - Clock Tracing Methodology

````
