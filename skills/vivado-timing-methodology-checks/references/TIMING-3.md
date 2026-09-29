<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-3: Invalid primary clock on Clock Modifying Block

## Metadata

- **Check ID**: TIMING-3
- **Severity**: Critical Warning
- **Group**: 1
- **Priority**: 1
- **Hierarchy Name**: Timing
- **First Release**: 2025.1

## Description

Clock modifying block settings do not match the expected settings where expected settings are based on input clock settings and clock modifying block parameters.

### Full Message Description property.

```
A primary clock <CLOCK_GROUP> is created on the output pin or net <NETLIST_ELEMENT> of a Clock Modifying Block
```

**Example:**
```
A primary clock GTX_CLK is created on the output pin or net ios_0/mmcm_0/CLKOUT2 of a Clock Modifying Block
```

* clock name: GTX_CLK
* Pin element: net ios_0/mmcm_0/CLKOUT2
* Cell element: net ios_0/mmcm_0

### Explanation

The pin element has a clock defined on it that is different to the expected value. There will be either a `create_clock` or `create_generated_clock` constraint defined in the constraints on the `net ios_0/mmcm_0/CLKOUT2` pin.

The expected value is based off input clock and clock modifying properties. The input clock can be taken to be pins `CLKIN1` or `CLKIN2` on an MMCM or use `get_timing_arcs` to find the correct source pin.

**Example:**
An MMCM with an input clock CLKIN1 has:
* period = 10 ns
* waveform = 50:50

The MMCM has output pin used CLKOUT0: 
* CLKFBOUT_MULT =30
* DIVCLK_DIVIDE =1
* CLKOUT0_DIVIDE =30
* CLKOUT0_PHASE =0.000
* CLKOUT0_DUTY_CYCLE =0.500

Expected output clock
CLKOUT0 period = input period * (CLKFBOUT_MULT/DIVCLK_DIVIDE) / CLKOUT0_DIVIDE = 10 ns

**DO**
✅  Understand the clock modifying equation from the documentation
✅  Perform the calculation to prove understanding of the message
✅  Identify the constraint causing the issue
✅  Leverage Vivado documentation to determine the properties that influence how the clock is modified.

**DO NOT**
❌ Guess the calculation


## Generate Data
1. Get the properties of the clock modifying block
```tcl
report_property [get_cells net ios_0/mmcm_0]
```
2. Trace the input clock to ports using Clock Tracing Methodology
3. Get the details of the input clock such as period, waveform etc.

## Flow

1. Search the constraints `./tmp/constraints.xdc` for `create_clock` or `create_generated_clock` constraint that creates <CLOCK_GROUP>. 
2. Understand the message. Get the input clock requirement and waveform, calculate the expected requirement and waveform. 
3. Compare the two.
4. Capture the property values from get_clocks <CLOCK_GROUP> into a json file and make sure the LINE_NUMBER and FILE_NAME matches with the item 1 data.
```tcl
set clk_info [get_clocks -of [get_pins ios_0/mmcm_0/CLKOUT2]]
```
5. Determine the best solution to offer the user.
**IMPORTANT**: All `when` conditions must be true. AND together all conditions to trigger the case.

**CASE 1:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] The actual period and waveform match with the expected period and waveform. 
* [ ] There are exceptions that reference the <CLOCK_GROUP> or generated clocks derived from it.
* **Action**: 
1. Create a constraint to use the method described in UG903 `renaming auto derived clocks` using the `create_generated_clock` constraint after the definition of the clock on the input 
2. Remove the clock definition of on <NETLIST_ELEMENT>. 
3. Report that exceptions reference the clock name
4. State that no assumptions have been made

**CASE 2:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] The actual period and waveform match with the expected period and waveform. 
* [ ] There are no exceptions that reference the <CLOCK_GROUP> or generated clocks derived from it.
* **Action**: 
1. Remove the definition of any clocks on <NETLIST_ELEMENT>.
2. Report that no exceptions reference the clock name.
3. State that no assumptions have been made

**CASE 3:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] The actual period and waveform do not match with the expected period and waveform. 
* [ ] There are no CLOCK type nets connected to DCLK pin of the object
* **Assumption**: Input clock to CMB is the desired frequency and the modifying parameters are worst case. The clock definition on the pin should be removed.
* **Action**: 
1. Remove the clock definition on <NETLIST_ELEMENT>
2. Report the assumption. State "If this assumption is incorrect, then update the M/D values on the MMCM and remove the constraint" 

**Example to find if DCLK has connected pins**
```tcl
set nets [get_nets -filter {TYPE==CLOCK} -of [get_pins -filter {REF_PIN_NAME==DCLK} -of [get_cells -of [get_pins output ios_0/mmcm_0/CLKOUT2]]]]
if {[llength $net] == 0} {puts "Assume input clock is the intent"} else {puts "Reconfiguration of MMCM is possible"}
```
**CASE 4:**
* **Type**: Automated Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: 
* [ ] The actual period and waveform do not match with the expected period and waveform. 
* [ ] There are CLOCK type nets connected to DCLK pin of the object
* **Assumption**: Output clock to CMB is the desired frequency and the modifying parameters are not set to the worst case. The pin definition is correct.
* **Action**: 
1. Recommend updated clock modifying parameters so that the output clock is automatically generated.
2. Remove the clock constraint on <NETLIST_ELEMENT>
3. Rename the generated clock to match the name of the output clock removed
4. Report the assumption. State "If this assumption is incorrect, then please update the configuration or constraints or waive the issue" 

**Example to find if DCLK has connected pins**
```tcl
set nets [get_nets -filter {TYPE==CLOCK} -of [get_pins -filter {REF_PIN_NAME==DCLK} -of [get_cells -of [get_pins output ios_0/mmcm_0/CLKOUT2]]]]
if {[llength $net] == 0} {puts "Assume input clock is the intent"} else {puts "Reconfiguration of MMCM is possible"}
```

**CASE 5:**
* **Type**: User Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: 
* [ ] Expected and actual periods match but waveforms do not. 
* [ ] The waveform first edge on the actual clock is 0
* **Assumption**: User has failed to adjust the CLKOUTn_DUTY_CYLE setting on the MMCM cell. Assumes the constraint on the output of the MMCM is correct
* **Action**:
Propose the following:
1. Remove the clock constraint on <NETLIST_ELEMENT>
2. Update the CLKOUTn_DUTY_CYCLE
3. Report the assumption. State "If this assumption is incorrect, then please resolve the issue manually" 

* **Example**:
```tcl
set hi [string range [expr (${second_edge}-${first_edge})/${period}] 0 4]
set_property CLKOUT0_DUTY_CYCLE $hi [get_cells ios_0/mmcm_0]
```

**CASE 6:**
* **Type**: User Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: 
* [ ] Expected and actual periods match but waveforms do not. 
* [ ] The waveform first edge on the actual clock is not at 0
* **Assumption**: User has failed to adjust the CLKOUTn_DUTY_CYLE and CLKOUTn_PHASE settings on the MMCM cell. Assumes the constraint on the output of the MMCM is correct.
* **Action**:
1. Remove the clock constraint on <NETLIST_ELEMENT>
2. Update the CLKOUTn_DUTY_CYCLE. Make sure the constraint matches the pin. CLKOUT0_DUTY_CYCLE for pin CLKOUT0
2. Update the CLKOUTn_PHASE. Make sure the constraint matches the pin. CLKOUT0_PHASE for pin CLKOUT0
3. Report the assumption. State "If this assumption is incorrect, then please resolve the issue manually" 

* **Example**:
```tcl
set hi [string range [expr (${second_edge}-${first_edge})/ ${period}] 0 4]
set_property CLKOUT0_DUTY_CYCLE $hi [get_cells ios_0/mmcm_0]
set_property CLKOUT0_PHASE <value> [get_cells ios_0/mmcm_0]; # value between -360 and 360
```

**CASE 7:**
* **Type**: User Resolution
* **Condfidence**: High
* **Usefulness**: Low
* **Prompt**: 
```
Any other scenario, prompt the user do you want to: 
a) Remove the constraint that creates <CLOCK_GROUP>
b) Use a create_generated_clock constraint based off the expected constraints to account for insertion delay
c) Leave the constraint as is and create a waiver

**DO**
✅  Use the [CLOCK_TRACING.md](./CLOCK_TRACING.md) to trace the clocks
✅  First try tracing back to the CMB input pin and look for clocks

**DO NOT**
❌ Offer automated suggestiones when the the constraint file value is null
❌ Offer automated suggestiones when the the line number value is null
❌ Offer automated suggestiones when the multiply and divide calculations do not match the result
❌ Automate fixes when the the information does not match the item 1 info.
❌ Trace through GT* blocks

## Verification

* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created


## Final Report
Print a table using [./REPORTING_CLOCK_DIFFERENCES](./REPORTING_CLOCK_DIFFERENCES.md)
Print and constraint changes before and after


## Examples

**Details**
* SYS_CLK is defined on pin get_pins ios_0/mmcm_0/CLKOUT1
* There are references to SYS_CLK in the constraints file
* CLKFBOUT_MULT =30
* DIVCLK_DIVIDE =1
* CLKOUT0_DIVIDE =30


**Before**
```XDC
create_clock -period 8.000 -name SYS_CLK [get_pins ios_0/mmcm_0/CLKOUT1]
set_clock_groups -asynchronous -group [get_clocks SYS_CLK] -group [get_clocks {HOSTCLK}]

```
**After**
```XDC 
# Fixed TIMING-3#2: Changed from create_clock to create_generated_clock
# Preserves clock name SYS_CLK while respecting MMCM output
create_generated_clock -name SYS_CLK -source [get_pins ios_0/mmcm_0/CLKIN1] -multiply_by 1 -divide_by 1 [get_pins ios_0/mmcm_0/CLKOUT1]
```

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [Clock Tracing Methodology](./CLOCK_TRACING.md)
