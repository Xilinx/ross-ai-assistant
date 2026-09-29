<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

````markdown
# TIMING-7: No common node between related clocks

## Metadata

- **Check ID**: TIMING-7
- **Severity**: Critical Warning
- **Group**: 1
- **Priority**: 1
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
2. **Separate PLLs/MMCMs**: Independent clock generation blocks
3. **Missing Clock Constraints**: Asynchronous relationship not declared
4. **External Clock Sources**: Off-chip clocks with no phase correlation
5. **Design Bug**: Unintended clock domain crossing without CDC circuitry

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
report_clock_networks -name $clk1
report_clock_networks -name $clk2
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

**Analyze CDC Implementation:**
```tcl
# Look for synchronizer chains
set paths [get_timing_paths -from [get_clocks $clk1] -to [get_clocks $clk2]]

foreach path $paths {
    set endpoint [get_property ENDPOINT_PIN $path]
    # Check for multi-stage synchronizer (2+ FFs)
    set fanin [all_fanin -flat -levels 2 [get_pins $endpoint]]
    puts "Path endpoint: $endpoint"
    puts "Fanin stages: [llength $fanin]"
}
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
| **Different input ports** | Separate pins | Asynchronous | `set_clock_groups -asynchronous` |
| **Different PLLs** | Independent MMCMs | Asynchronous | `set_clock_groups -asynchronous` |
| **Same PLL, different outputs** | Shared MMCM | Check if synchronous | May need `create_generated_clock` fix |
| **Same source, wrong definition** | Should be related | Fix clock constraints | Re-define clocks properly |

#### Step 3: Apply Resolution

**CASE 1: Truly Asynchronous Clocks (Most Common)**
- **Type**: Automated Resolution
- **When**: Clocks from completely independent sources

```tcl
# Mark as asynchronous
set_clock_groups -asynchronous \
    -group [get_clocks clk1] \
    -group [get_clocks clk2]
```

**Requirements:**
✅ Must have CDC synchronizers on all crossing paths
✅ Should verify with `report_cdc` 
✅ Document the independence in comments

```tcl
# Example with verification
set_clock_groups -asynchronous \
    -group [get_clocks bftClk] \
    -group [get_clocks wbClk] \
    -comment "Independent external clock sources with no phase relationship"

# Verify CDC implementation
report_cdc -file ./tmp/cdc_verification.rpt
```

**CASE 2: Physically Related (Same PLL) - Fix Clock Definitions**
- **Type**: User Resolution
- **When**: TIMING-7 appears but clocks should be related

This typically indicates incorrect clock definitions. The clocks may share a PLL but were defined incorrectly.

```tcl
# BEFORE (causes TIMING-7):
create_clock -period 10 -name clkA [get_pins PLL/CLKOUT0]
create_clock -period 5 -name clkB [get_pins PLL/CLKOUT1]

# AFTER (proper definition):
# Define primary clock at the PLL input
create_clock -period 10 -name clk_primary [get_ports CLK_IN]

# Define generated clocks from outputs
create_generated_clock -name clkA \
    -source [get_pins PLL/CLKIN1] \
    -master_clock clk_primary \
    -divide_by 1 \
    [get_pins PLL/CLKOUT0]

create_generated_clock -name clkB \
    -source [get_pins PLL/CLKIN1] \
    -master_clock clk_primary \
    -divide_by 2 \
    [get_pins PLL/CLKOUT1]
```

**DO:**
✅ Define primary clock at the actual source (input port or oscillator)
✅ Use `create_generated_clock` for PLL/MMCM outputs
✅ Specify proper -source and -master_clock relationships

**DO NOT:**
❌ Create primary clocks on PLL outputs (use generated clocks instead)
❌ Ignore relationships between PLL outputs


**CASE 3: Logically Related but Physically Separate**
- **Type**: User Resolution  
- **When**: Clocks are meant to be synchronous but physically isolated

This is rare but can occur with:
- Multiple identical frequency clocks from independent sources
- Clocks that should be rationally related

```tcl
# If periods are related (e.g., 100 MHz and 200 MHz)
# but from separate sources, you may need:

# Option A: If truly synchronous in design intent
set_clock_groups -physically_exclusive \
    -group [get_clocks clk_100] \
    -group [get_clocks clk_200]

# Option B: If asynchronous despite same frequency
set_clock_groups -asynchronous \
    -group [get_clocks clk_100] \
    -group [get_clocks clk_200]
```

**CASE 4: Specific Paths are Asynchronous**
- **Type**: User Resolution
- **When**: Most paths need timing but specific crossings don't

```tcl
# For quasi-static control signals
set_false_path -from [get_clocks clk1] -to [get_clocks clk2] \
    -through [get_pins config_reg*/D]

# For async signals with max delay requirement
set_max_delay 5.0 -datapath_only \
    -from [get_clocks clk1] -to [get_clocks clk2] \
    -through [get_pins async_data*/D]
```

**CASE 5: Create Waiver (Rare)**
- **Type**: User Resolution
- **When**: Violation is understood and acceptable

```tcl
create_waiver -type METHODOLOGY -id {TIMING-7} \
    -objects [get_methodology_violations TIMING-7#1] \
    -user [get_property USER [current_project]] \
    -description "Clocks clk1 and clk2 are from independent PCB oscillators. All crossings use proper XPM_CDC primitives with MTBF > 1000 years. Clock relationship explicitly defined as asynchronous."
```

### DON'T:
❌ Apply `set_clock_groups -asynchronous` without verifying CDC circuits
❌ Ignore this violation - indicates potential metastability issues
❌ Assume clocks are related when they have no common node
❌ Fix TIMING-7 without also checking for TIMING-6 and TIMING-8


## Verification

* [ ] Methodology violation TIMING-7 is no longer present
* [ ] Related TIMING-6 and TIMING-8 violations also resolved
* [ ] `report_clock_interaction` shows intended clock relationship
* [ ] All clock domain crossings have proper CDC implementation
* [ ] CDC report (`report_cdc`) shows no unknown crossings
* [ ] Timing reports correctly include or exclude cross-domain paths
* [ ] No new unresolved methodology warnings created

**Verification Script:**
```tcl
# Re-run methodology checks  
report_methodology -checks {TIMING-6 TIMING-7 TIMING-8} \
    -file ./tmp/methodology_timing_6_7_8_after_fix.rpt

# Verify clock relationships
report_clock_interaction -delay_type min_max \
    -file ./tmp/clock_interaction_after_fix.rpt

# Check CDC implementation
report_cdc -file ./tmp/cdc_after_fix.rpt

# Verify no unknown clock crossings
set unknown_cdc [get_cdc_violations -of_objects [get_cdc_paths -from * -to *]]
if {[llength $unknown_cdc] > 0} {
    puts "WARNING: Unknown CDC violations still present!"
} else {
    puts "SUCCESS: All CDC paths properly defined"
}
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

### CDC Verification

**Crossing Paths Found:** 24 signals from bftClk → wbClk, 8 signals from wbClk → bftClk

| Signal Path | CDC Type | Sync Stages | MTBF |
|-------------|----------|-------------|------|
| `bft_valid` → `wb_valid_sync` | XPM_CDC_SINGLE | 2-FF | >1000 years |
| `bft_data[31:0]` → `wb_data_sync[31:0]` | XPM_CDC_ARRAY_SINGLE | 2-FF | >1000 years |
| `wb_ack` → `bft_ack_sync` | XPM_CDC_SINGLE | 2-FF | >1000 years |

**CDC Report Summary:**
- ✅ All 32 crossing signals have proper CDC synchronizers
- ✅ No unsafe clock domain crossings detected
- ✅ MTBF requirements met for all paths

### Verification Results

Before Fix:
- ❌ TIMING-7#1: bftClk ↔ wbClk  
- ❌ TIMING-7#2: wbClk ↔ bftClk
- ⚠️  32 unconstrained clock crossings

After Fix:
- ✅ TIMING-7 violations resolved
- ✅ Clock interaction report shows "Asynchronous" relationship
- ✅ All CDC paths properly synchronized
- ✅ No new methodology warnings

### Design Impact

**Timing Analysis:**
- Clock domains now properly isolated
- No timing paths analyzed between bftClk and wbClk domains
- CDC paths excluded from timing (as intended)

**Hardware Reliability:**
- Metastability risk mitigated by 2-stage synchronizers
- MTBF > 1000 years for all crossings
- Design safe for independent clock sources

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques (Chapter 2: Timing Constraints, Clock Domain Crossings)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug903-vivado-using-constraints.pdf) - Using Constraints (Chapter 5: Timing Constraints, `set_clock_groups`)
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology (CDC Guidelines, Metastability)
- [UG953](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug953-vivado-7series-libraries.pdf) - XPM_CDC Library Guide
- [CLOCK_TRACING.md](./CLOCK_TRACING.md) - Clock Tracing Methodology

````
