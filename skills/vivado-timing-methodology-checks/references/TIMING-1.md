<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-1: Invalid clock waveform on Clock Modifying Block

## Metadata

- **Check ID**: TIMING-1
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Invalid clock waveform on Clock Modifying Block. 

### Full Message Description property.

```
Invalid clock waveform for clock <CLOCK_GROUP> specified at a <MESSAGE_STRING> output <NETLIST_ELEMENT> that does not match the CMB settings. The waveform of the clock is <MESSAGE_STRING>. The expected waveform is <MESSAGE_STRING>
```
**Example:**
```
Invalid clock waveform for clock GTX_CLK specified at a MMCME5 output ios_0/mmcm_0/CLKOUT2 that does not match the CMB settings. The waveform of the clock is 5 {0 2.5}. The expected waveform is 12.8 {0 6.4}
```
For the first clock
5 {0 2.5}
* Period = 5
* First edge = 0
* Second edge = 2.5

For the second clock
12.8 {0 6.4}
* Period = 12.8
* First edge = 0
* Second edge = 6.4

### Explanation
The first clock waveform is a result of a new `create_clock` definition applied to the pin object `ios_0/mmcm_0/CLKOUT2`

The expected waveform is based off one of the clocks on the input of the clock modifying block (CMB). There could be:
* Multiple clock input pins with different clock objects on them
* Multiple clock objects on any given pin
This is then updated by the settings on the clock modifying block.

**DO**
✅  Correctly identify the `create_clock` constraint triggering the issue.
✅  Correctly idenfify the `create_clock` constraint generating the input constraint that is the expected expected values
✅  Perform the clock modificaiton calculation and match it with the constraint

**DO NOT**
❌ Look at the first `create_clock` and assume it is problem `create_clock` related to the methodology warning.


## Flow
1. Search the `./tmp/constraints.xdc` for `create_clock` constraint that creates <CLOCK_GROUP>. 
2. Capture the property values from get_clocks <CLOCK_GROUP> into a json file and make sure the LINE_NUMBER and FILE_NAME matches with the item 1 data.
```tcl
set clk_info [get_clocks -of [get_pins ios_0/mmcm_0/CLKOUT2]]
```

**DO NOT**
❌ Automate fixes when the the constraint file value is null
❌ Automate fixes when the the line number value is null
❌ Automate fixes when the the information does not match the item 1 info.

3. Find the `create_clock` source constraint line that relates to the clock mentioned "The expected waveform..".

**DO**
✅  Use the `./CLOCK_TRACING.md` and `trace_clock_treee.tcl` to trace the clocks
✅  First try tracing back to the CMB input pin and look for clocks


**DO NOT**
❌ Offer automated suggestiones when the the constraint file value is null
❌ Offer automated suggestiones when the the line number value is null
❌ Offer automated suggestiones when the multiply and divide calculations do not match the result
❌ Trace through GT* blocks

3. Determine the best solution to offer the user.
**CASE 1:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: The period and waveform match with the expected period and waveform and there are exceptions that reference the <CLOCK_GROUP> or generated clocks derived from it.
1. Create a constraint to use the method described in UG903 `renaming auto derived clocks` using the `create_generated_clock` constraint after the definition of the clock on the input 
2. Remove the clock definition of  on <NETLIST_ELEMENT>. 
3. Report that exceptions reference the clock name
4. State that no assumptions have been made

**DO**
✅ When there is a matching Period

**DO NOT**
❌ Offer this resolution when the Period (or periods) is different for both clocks

**CASE 2:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: If the Period and waveform match with the expected Period and waveform and there are no exceptions that reference the <CLOCK_GROUP> or generated clocks derived from it.
1. Remove the definition of any clocks on <NETLIST_ELEMENT>.
2. Report that no exceptions reference the clock name.
3. State that no assumptions have been made

**CASE 3:**
* **Type**: Automated Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: If the Period does not match with the expected Period and there are no signal nets connected to DCLK pin of the object.
* **Assumption**: Input clock to CMB is the desired frequency and the modifying parameters are worst case. The clock definition on the pin should be removed.

```tcl
set nets [get_nets -filter {TYPE==CLOCK} -of [get_pins -filter {REF_PIN_NAME==DCLK} -of [get_cells -of [get_pins output ios_0/mmcm_0/CLKOUT2]]]]
if {[llength $net] == 0} {puts "Assume input clock is the intent"}
```

1. Remove the clock definition on <NETLIST_ELEMENT>
2. Report the assumption. State "If this assumption is incorrect, then update the M/D values on the MMCM and remove the constraint" 

**CASE 4:**
* **Type**: Automated Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: If the Period does not match with the expected Period and there are signal nets connected to DCLK pin of the object.
* **Assumption**: Output clock to CMB is the desired frequency and the modifying parameters are not set to the worst case. The pin definition is correct.

```tcl
set nets [get_nets -filter {TYPE==CLOCK} -of [get_pins -filter {REF_PIN_NAME==DCLK} -of [get_cells -of [get_pins output ios_0/mmcm_0/CLKOUT2]]]]
if {[llength $net] != 0} {puts "potential reconfigurable waveform"}
```

1. Recommend updated clock modifying parameters so that the output clock is automatically generated.
2. Remove the clock constraint on <NETLIST_ELEMENT>
3. Rename the generated clock to match the name of the output clock removed
4. Report the assumption. State "If this assumption is incorrect, then please update the configuration or constraints or waive the issue" 

**CASE 5:**
* **Type**: Automated Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: If the periods match but waveforms do not and the first edge o the first clock is 0
* **Assumption**: User has failed to adjust the CLKOUTn_DUTY_CYLE setting on the MMCM cell. Assumes the constraint on the output of the MMCM is correct

```tcl
set hi [string range [expr (${second_edge}-${first_edge})/${period}] 0 4]
set_property CLKOUT0_DUTY_CYCLE $hi [get_cells ios_0/mmcm_0]
```

1. Remove the clock constraint on <NETLIST_ELEMENT>
2. Update the CLKOUTn_DUTY_CYCLE
3. Report the assumption. State "If this assumption is incorrect, then please resolve the issue manually" 

**CASE 6:**
* **Type**: Automated Resolution
* **Condfidence**: Medium
* **Usefulness**: High
* **When**: If the periods match but waveforms do not and the first edge of the first clock is not at 0
* **Assumption**: User has failed to adjust the CLKOUTn_DUTY_CYLE and CLKOUTn_PHASE settings on the MMCM cell. Assumes the constraint on the output of the MMCM is correct.

```tcl
set hi [string range [expr (${second_edge}-${first_edge})/ ${period}] 0 4]
set_property CLKOUT0_DUTY_CYCLE $hi [get_cells ios_0/mmcm_0]
set_property CLKOUT0_PHASE <value> [get_cells ios_0/mmcm_0]; # value between -360 and 360
```

1. Remove the clock constraint on <NETLIST_ELEMENT>
2. Update the CLKOUTn_DUTY_CYCLE. Make sure the constraint matches the pin. CLKOUT0_DUTY_CYCLE for pin CLKOUT0
3. Report the assumption. State "If this assumption is incorrect, then please resolve the issue manually" 

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
```
* **Explaination**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**: 
```
poldark 01/26/26 "Clock redefinition required to test pulses on the <CLOCK_GROUP> that could be higher frequency than nominal clock"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] Pin has the expected timing characteristics or is waived


## Final Report
Print a table that shows the difference in: 
* constraint
* Period 
* waveform
* clock definition object


| Aspect | Constraint | Period | Waveform | Clock Definition Object |
|--------|-----------|-------------|----------|------------------------|
| **Before** | `create_clock -period 5 -waveform {0 2.5} [get_pins ios_0/mmcm_0/CLKOUT2]` | 5 ns | {0 2.5} | GTX_CLK |
| **After** | `create_generated_clock -source [get_pins ios_0/mmcm_0/CLKIN1] -multiply_by 1 -divide_by 1 [get_pins ios_0/mmcm_0/CLKOUT2]` | 12.8 ns | {0 6.4} | GTX_CLK_GEN |


## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)
- Clock Tracing Methodology (../references/CLOCK_TRACING.md)
