<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-4: Invalid primary clock redefinition on a clock tree

## Metadata

- **Check ID**: TIMING-4
- **Severity**: Critical Warning
- **Group**: TIMING
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Invalid clock redefinition on a clock tree. 

### Full Message Description Property

```
Invalid clock redefinition on a clock tree. The primary clock <CLOCK_GROUP> is defined downstream of clock <CLOCK_GROUP> and overrides its insertion delay and/or waveform definition
```

**Example:**
```
Invalid clock redefinition on a clock tree. The primary clock clk_b is defined downstream of clock clk_a and overrides its insertion delay and/or waveform definition
```

### Explanation

A primary clock is defined on a net or pin that is downstream from another primary clock. When a clock is redefined downstream on the same clock tree, it overrides the upstream clock's insertion delay and/or waveform definition. This creates conflicts in clock analysis and can lead to incorrect timing results.

Clock definitions should typically follow this hierarchy:
1. Primary clocks should be defined at input ports or appropriate clock source pins
2. Generated clocks should be used to define downstream clocks derived from primary clocks
3. Multiple primary clocks should not be defined on the same clock tree unless intentional and properly understood

**DO**
✅ Identify both clock definitions - the upstream clock and the downstream redefinition
✅ Verify which clock is the intended primary clock
✅ Check if there are timing exceptions or other constraints that reference the downstream clock name
✅ Use `trace_clock_tree.json` to trace the clock tree and confirm the primary clock is defined at the correct object
✅ Use `report_clocks` to understand the clock tree structure

**DO NOT**
❌ Assume the first clock definition in the XDC file is the problematic one
❌ Remove clock definitions without checking for references in timing exceptions
❌ Ignore insertion delay or waveform differences between the clocks

## Generate Data

1. Identify both clocks mentioned in the violation message
```tcl
# Get properties of the downstream redefined clock
report_property [get_clocks <downstream_clock_name>]

# Get properties of the upstream clock
report_property [get_clocks <upstream_clock_name>]
```

2. Find the pins/nets where each clock is defined
report_property Start Time : 1772102992888919 (2026 02 26 03 49 52)
Property           Type     Read-only  Visible  Value
CLASS              string   true       true     clock
FILE_NAME          string   true       true     /proj/dsv/jackjame/DGT/H50Designs/Test2/H50_Test2__4/project_1/project_1.gen/sources_1/bd/hwtest_harness_top/ip/hwtest_harness_top_ps_block_0/bd_0/ip/ip_0/bd_0885_pspmc_0_0.xdc
INPUT_JITTER       double   true       true     0.000
IS_GENERATED       bool     true       true     0
IS_PROPAGATED      bool     true       true     1
IS_USER_GENERATED  bool     true       true     0
IS_VIRTUAL         bool     true       true     0
LINE_NUMBER        int      true       true     52
MODULE             string   true       true     Static 
NAME               string   true       true     clk_pl_0
PERIOD             double   true       true     3.000
SOURCE_PINS        string*  true       true     hwtest_harness_top_i/Base_layer/ps_block/inst/pspmc_0/inst/PS9_inst/PMCRCLKCLK[0]
SYSTEM_JITTER      double   true       true     needs timing update***
WAVEFORM           double*  true       true     0.000 1.500
WEIGHT             double   true       true     1.000
report_property End Time : 1772102992890408 (2026 02 26 03 49 52)
```tcl
# For the downstream clock
get_pins [get_property SOURCE_PINS [get_clocks <upstream_clock_name>]]
get_ports [get_property SOURCE_PINS [get_clocks <upstream_clock_name>]]

# For the upstream clock
get_pins [get_property SOURCE_PINS [get_clocks <downstream_clock_name>]]

```

3. Trace the connectivity between the two clock definition points
```tcl
# Use Clock Tracing Methodology to verify the downstream relationship
# See ../references/CLOCK_TRACING.md
```

4. Check for constraints that reference the downstream clock
Searc `constraints.xdc` file the downstream clock name

## Flow

1. Search the `./tmp/constraints.xdc` for both `create_clock` constraints
   - Find the constraint creating the upstream clock
   - Find the constraint creating the downstream clock

2. Verify the clock definitions in the constraints match the violation message
```tcl
# Capture properties
set downstream_clk [get_clocks <downstream_clock_name>]
set upstream_clk [get_clocks <upstream_clock_name>]
```

**DO NOT**
❌ Automate fixes when the constraint file value is null
❌ Automate fixes when the line number value is null
❌ Automate fixes when the information does not match the violation data

3. Determine the clock tree relationship
   - Use Clock Tracing Methodology to confirm the downstream relationship

4. Determine the best solution to offer the user

**CASE 1:**
* **Type**: Automated Resolution
* **When**: The downstream clock has the same period and waveform as would be expected from the upstream clock, and the downstream clock name is referenced in timing exceptions

1. Remove the `create_clock` constraint on the downstream pin/net
2. Create a `create_generated_clock` constraint on the downstream pin/net that references the upstream clock
   - Use the same clock name as the removed constraint
   - Set `-source` to point to the upstream clock's definition point
   - Maintain the same period and waveform

**DO**
✅ Preserve the clock name when it's referenced elsewhere
✅ Consider if the constraint is coming from an IP file

**DO NOT**
❌ Apply this fix if the period or waveform is intentionally different

**CASE 2:**
* **Type**: Automated Resolution  
* **When**: The downstream clock has the same period and waveform as the upstream clock, and is NOT referenced in any timing exceptions

1. Remove the `create_clock` constraint on the downstream pin/net
2. Allow the clock to propagate naturally from the upstream definition

**CASE 3:**
* **Type**: User Resolution
* **When**: The downstream clock has different period or waveform than expected from the upstream clock

**Prompt**:
```
The downstream clock <downstream_clock_name> has a different period/waveform than would be expected from the upstream clock <upstream_clock_name>. 

Downstream clock: period=<period>, waveform={<waveform>}
Expected from upstream: period=<expected_period>, waveform={<expected_waveform>}

This requires manual review to determine:
1. Do you want to waive the methodology violation?
2. Should the downstream clock be removed?
3. Should the upstream clock be modified?
```


## Additional Checks

- Verify if there are any clock modifying blocks (MMCM, PLL, BUFG) between the two clock definitions
- Check if the constraints come from an IP like clocking wizard
- Check if both clocks are used in exceptions

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)  
- Vivado Design Suite User Guide: Using Constraints (UG903) - Section on Clock Constraints
- [Clock Tracing Methodology](./CLOCK_TRACING.md)
- [IP Constraints](./IP_CONSTRAINTS.md)

## Examples

*To be documented with real-world examples from TIMING-4 testcase as required.*
