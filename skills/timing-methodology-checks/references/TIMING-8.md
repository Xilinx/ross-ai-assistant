<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

````markdown
# TIMING-8: No common period between related clocks

## Metadata

- **Check ID**: TIMING-8
- **Severity**: Critical Warning
- **Group**: 1
- **Priority**: 1
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Detects when two clocks that have timing paths between them (are related/timed together) do not have periods with a common expandable relationship, making it impossible to establish proper timing correlation.

## Message

```
The clocks <CLOCK_GROUP> and <CLOCK_GROUP> are found related (timed together) but have no common (expandable) period
```

**Example:**
```
The clocks clk_100MHz and clk_133MHz are found related (timed together) but have no common (expandable) period
```

## Explanation

This violation occurs when:
1. **Data paths exist** between two clock domains
2. **Clock periods are not rationally related** - no integer multiple or simple fractional relationship exists
3. **Timing analysis cannot find common period** - Vivado cannot expand the periods to find a common boundary

### What is a "Common Period"?

Clocks can have timing analysis performed between them if their periods can be expanded to a common boundary:

**Examples with Common Period (✅ No TIMING-8):**
- 10 ns (100 MHz) and 20 ns (50 MHz) → Common period: 20 ns (1x and 2x)
- 10 ns (100 MHz) and 5 ns (200 MHz) → Common period: 10 ns (1x and 0.5x)
- 8 ns (125 MHz) and 16 ns (62.5 MHz) → Common period: 16 ns (0.5x and 1x)

**Examples without Common Period (❌ TIMING-8):**
- 10 ns (100 MHz) and 7.5 ns (133.33 MHz) → No simple common period
- 6.4 ns (156.25 MHz) and 10 ns (100 MHz) → No integer relationship
- Arbitrary frequencies with irrational ratios

### Why This Matters

When clocks have no common period:
- ❌ **Timing analysis is unreliable** - Vivado may make incorrect assumptions
- ❌ **Phase relationship is undefined** - clocks drift relative to each other
- ❌ **Setup/hold analysis may be wrong** - timing may pass in simulation but fail in hardware
- ❌ **Metastability risk** - clock domain crossings are asynchronous

### Relationship to TIMING-6 and TIMING-7

| Check | Detects | Typical Cause | Primary Issue |
|-------|---------|---------------|---------------|
| **TIMING-6** | No common primary clock | Independent clock trees | Clock hierarchy |
| **TIMING-7** | No common node | Physically separate sources | Network topology |
| **TIMING-8** | No common period | Irrational frequency relationship | Timing correlation |

**TIMING-8** often appears alongside TIMING-6 and TIMING-7, indicating completely asynchronous clock domains.

### Common Root Causes

1. **Independent Oscillators**: Different frequency crystals/oscillators
2. **Unrelated PLLs**: Separate clock generators with different multiplication factors
3. **External Interfaces**: Different clock domains from system-level architecture
4. **Missing Constraints**: Asynchronous clocks not declared with `set_clock_groups`
5. **Transceivers**: GT clocks vs fabric clocks with no rational relationship

## Generate Data

Extract clock period information and analyze relationships:

**From Violation Object:**
```tcl
# Get violation details
set violation [get_methodology_violations TIMING-8#1]
set description [get_property DESCRIPTION $violation]

# Parse clock names from description
# Expected format: "The clocks <clk1> and <clk2> are found related..."
```

**Analyze Clock Periods:**
```tcl
# Get clock objects
set clk1 [get_clocks clk_100MHz]
set clk2 [get_clocks clk_133MHz]

# Extract period information
set period1 [get_property PERIOD $clk1]
set period2 [get_property PERIOD $clk2]

puts "Clock 1: [get_property NAME $clk1]"
puts "  Period: $period1 ns ([expr 1000.0/$period1] MHz)"
puts "  Sources: [get_property SOURCES $clk1]"

puts "Clock 2: [get_property NAME $clk2]"
puts "  Period: $period2 ns ([expr 1000.0/$period2] MHz)"
puts "  Sources: [get_property SOURCES $clk2]"

# Check for rational relationship
set ratio [expr $period1 / $period2]
puts "Period ratio: $ratio"

# Test for expandable periods (common period)
set lcm_attempts {2 3 4 5 6 8 10 12 15 16 20}
set found_common 0
foreach mult $lcm_attempts {
    set expanded1 [expr $period1 * $mult]
    set expanded2 [expr $period2 * $mult]
    if {abs($expanded1 - $expanded2) < 0.001} {
        puts "Common period found at ${mult}x: $expanded1 ns"
        set found_common 1
        break
    }
}

if {!$found_common} {
    puts "No common period found - clocks are likely asynchronous"
}
```

**Find Timing Paths:**
```tcl
# Report crossing paths
report_timing -from [get_clocks $clk1] -to [get_clocks $clk2] \
    -max_paths 10 -nworst 1 -file ./tmp/timing_paths_incompatible_clocks.rpt

# Check for CDC implementation
set cdc_paths [get_timing_paths -from [get_clocks $clk1] -to [get_clocks $clk2]]
puts "Number of paths crossing clocks: [llength $cdc_paths]"
```

**Check Clock Sources:**
```tcl
# Determine if clocks are from same or different sources
set src1 [get_property SOURCES $clk1]
set src2 [get_property SOURCES $clk2]

if {$src1 eq $src2} {
    puts "WARNING: Clocks share same source but have incompatible periods!"
    puts "  This may indicate incorrect clock definitions"
} else {
    puts "Clocks have independent sources:"
    puts "  Source 1: $src1"
    puts "  Source 2: $src2"
}

# Check if clocks are generated
foreach clk [list $clk1 $clk2] {
    set is_gen [get_property IS_GENERATED $clk]
    if {$is_gen} {
        set master [get_property MASTER_CLOCK $clk]
        puts "Clock [get_property NAME $clk] is generated from: $master"
    }
}
```

**Review Current Constraints:**
```tcl
# Check for existing clock group definitions
report_clock_interaction -delay_type min_max -file ./tmp/clock_interaction.rpt

# Look for exceptions
report_exceptions -from [get_clocks $clk1] -to [get_clocks $clk2] \
    -file ./tmp/exceptions_between_clocks.rpt
```

## Flow

1. **Gather Data** → Extract clock periods and analyze frequency relationship
2. **Analyze** → Determine if clocks are truly asynchronous or incorrectly defined
3. **Resolve** → Apply appropriate constraints based on clock relationship
4. **Verify** → Confirm timing analysis is correct and CDC is implemented

### Detailed Resolution Steps

#### Step 1: Verify Period Incompatibility

Calculate the period relationship:

```tcl
set clk1 [get_clocks clk_100MHz]
set clk2 [get_clocks clk_133MHz]

set period1 [get_property PERIOD $clk1]
set period2 [get_property PERIOD $clk2]

# Check if periods are simple multiples
set ratio [expr $period1 / $period2]
set inverse_ratio [expr $period2 / $period1]

puts "Period ratio: $ratio (could also be $inverse_ratio)"
puts "Is integer multiple? [expr {round($ratio) == $ratio}]"

# Calculate frequencies
set freq1 [expr 1000.0 / $period1]
set freq2 [expr 1000.0 / $period2]
puts "Frequency 1: $freq1 MHz"
puts "Frequency 2: $freq2 MHz"
```

#### Step 2: Determine True Clock Relationship

Ask the following questions:

**Q1: Are these clocks intentionally asynchronous?**
- Independent input clocks → Yes, **asynchronous**
- Different PLLs with unrelated multipliers → Yes, **asynchronous**
- Same PLL with incompatible dividers → Possibly a **design error**

**Q2: Do the clock definitions match hardware?**
```tcl
# Example: Check if PLL configuration matches constraints
# If you have a 100 MHz input and MMCM with:
#   - CLKOUT0: multiply by 10, divide by 10 = 100 MHz
#   - CLKOUT1: multiply by 10, divide by 7.5 = 133.33 MHz
# These would have no common period
```

**Q3: Are CDC circuits present?**
- 2+ FF synchronizer chains → Design expects **asynchronous** clocks
- Direct connections → Either **synchronous** (design bug) or missing CDC

#### Step 3: Apply Resolution

**CASE 1: Truly Asynchronous Clocks (Most Common)**
- **Type**: Automated Resolution
- **When**: Clocks have incompatible periods by design

```tcl
# Mark clocks as asynchronous
set_clock_groups -asynchronous \
    -group [get_clocks clk_100MHz] \
    -group [get_clocks clk_133MHz]
```

**This is the correct solution when:**
✅ Clocks are from independent sources
✅ Period relationship is intentionally irrational
✅ CDC synchronizers are present on all crossings
✅ No timing correlation is needed or expected

**Example with verification:**
```tcl
# Define asynchronous relationship
set_clock_groups -asynchronous \
    -group [get_clocks clk_100MHz] \
    -group [get_clocks clk_133MHz] \
    -comment "DDR4 controller (100 MHz) and PCIe interface (133 MHz) - independent clock domains"

# Verify CDC implementation
report_cdc -from [get_clocks clk_100MHz] -to [get_clocks clk_133MHz] \
    -file ./tmp/cdc_100_to_133.rpt
```

**CASE 2: Clocks Should Be Related - Fix Definitions**
- **Type**: User Resolution
- **When**: TIMING-8 indicates incorrect clock constraints

```tcl
# BEFORE (causes TIMING-8):
create_clock -period 10.000 -name clk1 [get_pins MMCM/CLKOUT0]
create_clock -period 7.500 -name clk2 [get_pins MMCM/CLKOUT1]  # Wrong period!

# AFTER (correct relationship):
# First define primary input clock
create_clock -period 10.000 -name clk_in [get_ports CLK_IN]

# Define generated clocks with correct multiply/divide
create_generated_clock -name clk1 \
    -source [get_pins MMCM/CLKIN1] \
    -master_clock clk_in \
    -multiply_by 1 \
    [get_pins MMCM/CLKOUT0]

create_generated_clock -name clk2 \
    -source [get_pins MMCM/CLKIN1] \
    -master_clock clk_in \
    -multiply_by 4 -divide_by 3 \
    [get_pins MMCM/CLKOUT1]
```

**DO:**
✅ Use `create_generated_clock` for PLL/MMCM outputs
✅ Match multiply/divide factors to actual hardware configuration
✅ Verify periods match expected hardware behavior

**DO NOT:**
❌ Create primary clocks on PLL outputs with arbitrary periods
❌ Ignore MMCM configuration when defining clocks


**CASE 3: Max Delay Constraints for Specific Paths**
- **Type**: User Resolution
- **When**: Most paths are unconstrained but specific crossings need limits

```tcl
# For quasi-static control signals with max delay requirement
set_max_delay 10.0 -datapath_only \
    -from [get_clocks clk_100MHz] -to [get_clocks clk_133MHz] \
    -through [get_pins control_*/D]

# For completely asynchronous signals
set_false_path \
    -from [get_clocks clk_100MHz] -to [get_clocks clk_133MHz] \
    -through [get_pins async_status_*/D]
```

**CASE 4: Physically Exclusive Clocks**
- **Type**: User Resolution
- **When**: Clocks never exist simultaneously

```tcl
# For mode-multiplexed clocks (e.g., different boot modes)
set_clock_groups -physically_exclusive \
    -group [get_clocks boot_clk_100] \
    -group [get_clocks run_clk_133]
```

**CASE 5: Create Waiver (Rare)**
- **Type**: User Resolution
- **When**: Violation is understood and acceptable

```tcl
create_waiver -type METHODOLOGY -id {TIMING-8} \
    -objects [get_methodology_violations TIMING-8#1] \
    -user [get_property USER [current_project]] \
    -description "Clocks clk_100MHz (PCIe) and clk_133MHz (DDR) are asynchronous by design. All 15 crossing signals use XPM_CDC_SINGLE or XPM_CDC_ARRAY_SINGLE synchronizers. Clock groups constraint applied in line 234 of constraints.xdc."
```

### DON'T:
❌ Ignore TIMING-8 without verifying CDC implementation
❌ Apply asynchronous constraints without confirming clock independence
❌ Use `set_clock_groups -asynchronous` to hide synchronous timing failures
❌ Leave clocks without any relationship constraint when paths exist


## Verification

* [ ] Methodology violation TIMING-8 is no longer present
* [ ] Related TIMING-6 and TIMING-7 violations also resolved
* [ ] `report_clock_interaction` shows appropriate clock relationship
* [ ] All clock domain crossings have proper CDC synchronizers
* [ ] CDC report shows no unsafe crossings
* [ ] Timing analysis correctly handles or excludes cross-domain paths
* [ ] No new unresolved methodology warnings created

**Comprehensive Verification:**
```tcl
# Re-run methodology checks for all related violations
report_methodology -checks {TIMING-6 TIMING-7 TIMING-8} \
    -file ./tmp/methodology_clock_relationship_after_fix.rpt

# Verify clock interaction matrix
report_clock_interaction -delay_type min_max -significant_digits 4 \
    -file ./tmp/clock_interaction_detailed.rpt

# Check CDC implementation
report_cdc -details -file ./tmp/cdc_detailed.rpt

# Verify no timing paths between async clocks (if marked as async)
set paths [get_timing_paths -from [get_clocks clk1] -to [get_clocks clk2]]
if {[llength $paths] > 0} {
    puts "WARNING: Timing paths still exist between clocks marked as asynchronous!"
    puts "  This may indicate missing set_clock_groups constraint"
} else {
    puts "SUCCESS: No timing paths between asynchronous clocks"
}

# Verify CDC synchronizers exist
set cdc_violations [get_cdc_violations -of_objects [get_cdc_paths -from [get_clocks clk1] -to [get_clocks clk2]]]
if {[llength $cdc_violations] > 0} {
    puts "ERROR: CDC violations detected!"
    foreach viol $cdc_violations {
        puts "  [get_property NAME $viol]: [get_property DESCRIPTION $viol]"
    }
} else {
    puts "SUCCESS: All CDC paths properly implemented"
}
```

## Final Report Guidance

Provide comprehensive documentation of the resolution:

### Clock Period Analysis

| Clock | Period (ns) | Frequency (MHz) | Ratio | Common Period? |
|-------|-------------|-----------------|-------|----------------|
| `clk_100MHz` | 10.000 | 100.000 | 1.000 | No |
| `clk_133MHz` | 7.500 | 133.333 | 0.750 | No |

**Period Relationship:** 10.0 ns / 7.5 ns = 1.333... (irrational ratio)
**Finding:** No common expandable period - clocks are asynchronous

### Clock Source Analysis

| Clock | Source | Type | Origin |
|-------|--------|------|--------|
| `clk_100MHz` | `CLK_100_P` port | Primary | External oscillator (PCIe reference) |
| `clk_133MHz` | `CLK_133_P` port | Primary | External oscillator (DDR4 reference) |

**Conclusion:** Independent clock sources with no phase or frequency relationship

### Resolution Applied

```tcl
# Added to constraints.xdc
# Line: 187-192

# Clock group definition for asynchronous clock domains
set_clock_groups -asynchronous \
    -group [get_clocks clk_100MHz] \
    -group [get_clocks clk_133MHz] \
    -comment "PCIe (100 MHz) and DDR4 (133 MHz) are independent external clocks"

# Justification:
# - clk_100MHz: PCIe reference clock from external oscillator
# - clk_133MHz: DDR4 memory controller clock from separate oscillator
# - No hardware synchronization between sources
# - Periods have no rational relationship (1.333... ratio)
# - All 23 crossing signals protected with proper CDC
```

### CDC Implementation Verification

**Clock Crossing Summary:**
- Paths from clk_100MHz → clk_133MHz: 15 signals
- Paths from clk_133MHz → clk_100MHz: 8 signals
- Total crossings: 23 signals

**CDC Protection:**

| Signal Group | Count | CDC Type | Stages | Status |
|-------------|-------|----------|--------|--------|
| PCIe status → DDR ctrl | 8 | XPM_CDC_SINGLE | 2-FF | ✅ Safe |
| PCIe data → DDR ctrl | 6 | XPM_CDC_ARRAY_SINGLE | 2-FF | ✅ Safe |
| PCIe valid → DDR ctrl | 1 | XPM_CDC_SINGLE | 3-FF | ✅ Safe |
| DDR status → PCIe | 5 | XPM_CDC_SINGLE | 2-FF | ✅ Safe |
| DDR valid → PCIe | 3 | XPM_CDC_GRAY | Gray + 2-FF | ✅ Safe |

**MTBF Analysis:**
- All synchronizers: MTBF > 1000 years
- Synchronizer depth: 2-3 stages per AMD recommendations
- Gray coding used for multi-bit counters

### Verification Results

**Before Fix:**
- ❌ TIMING-8#1: clk_100MHz and clk_133MHz no common period
- ❌ TIMING-8#2: clk_133MHz and clk_100MHz no common period
- ❌ TIMING-6#1, #2: No common primary clock
- ❌ TIMING-7#1, #2: No common node
- ⚠️  23 unconstrained clock domain crossings

**After Fix:**
- ✅ All TIMING-6, TIMING-7, TIMING-8 violations resolved
- ✅ Clock interaction report shows "Asynchronous" relationship
- ✅ All 23 CDC paths properly synchronized
- ✅ report_cdc shows 0 violations
- ✅ No timing paths analyzed between async domains (as intended)
- ✅ No new methodology warnings

### Design Impact

**Timing Analysis:**
- Removed invalid timing paths between incompatible clock domains
- Timing closure simplified by eliminating impossible constraints
- CDC paths properly excluded from setup/hold analysis

**Hardware Reliability:**
- Eliminated potential metastability on all 23 crossings
- MTBF requirements exceeded for all synchronizers
- Design safe for independent clock sources with phase drift

**Performance:**
- No impact on individual clock domain performance
- CDC synchronizers add 2-3 cycle latency (expected and acceptable)

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques (Chapter 2: Timing Constraints)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug903-vivado-using-constraints.pdf) - Using Constraints (Chapter 5: `set_clock_groups`, Clock Domain Crossing Constraints)
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology (Clock Requirements, CDC Best Practices)
- [UG953](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_2/ug953-vivado-7series-libraries.pdf) - 7 Series Libraries Guide (XPM_CDC components)
- [WP272](https://www.xilinx.com/content/dam/xilinx/support/documents/white_papers/wp272.pdf) - Vivado Design Suite White Paper: Clock Domain Crossing
- [CLOCK_TRACING.md](./CLOCK_TRACING.md) - Clock Tracing Methodology

````
