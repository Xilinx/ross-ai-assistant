<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-16: Large setup violation

## Metadata

- **Check ID**: TIMING-16
- **Severity**: Warning
- **Group**: TIMING
- **Hierarchy**: Timing.Bad Practice
- **Owner**: gservel

## Description

Large setup violation between sequential elements that may be difficult to fix during post-placement implementation.

## Message

```
There is a large setup violation of <MESSAGE_STRING> ns between <NETLIST_ELEMENT> (clocked by <CLOCK_GROUP>) and <NETLIST_ELEMENT> (clocked by <CLOCK_GROUP>). Large setup violations at the end of those stages might be difficult to fix during the post-placement implementation flow and could be the result of non-optimal XDC constraints or non-optimal design architecture
```

**Example:**
```
[Real violation message from actual design]
```

## Explanation

This check identifies paths with large setup violations that appear early in the design flow (typically after synthesis or early placement). Large setup violations at these stages often indicate fundamental issues with either the timing constraints or the design architecture itself, rather than just placement/routing issues.

Common root causes include:
- Unrealistic clock period constraints
- Missing or incorrect clock relationships (set_clock_groups, set_false_path, set_max_delay)
- Design architecture issues requiring pipelining or optimization
- Incorrect input/output delays affecting path budgets

## Generate Data

Collect information about the violating path:
1. Get path details: `report_timing -from <NETLIST_ELEMENT> -to <NETLIST_ELEMENT> -delay_type max`
2. Verify clock relationships: `report_clock_interaction`

## Flow

1. **Gather Data** → Collect violation properties, path timing report, and clock information

2. **Analyze** → Determine if issue is constraints or architectural
Constraint Issues have
a. small timing requirements. Typically < 1 ns.
b. inter clock paths with high skew  |skew| > 0.750 ns -  See suboptimal clock network
c. inter clock paths with high uncertainty - Phase Error (PE) -  See suboptimal clock network

Architectural Issues
d. Number of LOGIC_LEVELS is high for REQUIREMENT. Ask the user to examine Net/LUT logic levels in `report_qor_assessment`
```tcl
set lls [get_property LOGIC_LEVELS [get_timing_paths <PATH>]]
set req [get_property REQUIREMENT [get_timing_paths <PATH>]]
```
e. Sub optimal clock network that is creating skew or uncertainties
f. Logic delay is high v net delay

3. **Resolve** → Update constraints or flag for design changes
Determine if it is a constraint or structural issue.
**Constraints**
1. For tight timing requirements, see if adequate clock exceptions are in place between clock groups. Explore:
a. `set_clock_groups`
b. `set_max_delay -datapath_only`

**Clock Network**
If it is a suboptimal clock network issue
1. Determine if an MBUF can be used in Versal or later technology
2. For Ultrascale, if there is PE, determine if the same MMCM output can be used with BUFGCE_DIV to remove phase error.

**Logic level**
If it is a logic level issue
1. Determine if retiming is set globally at synthesis
2. Suggest that RTL recoding could improve the logic levels and point at the file name and line number
```tcl
get_property LINE_NUMBER [get_cells -of [get_pins [get_property ENDPOINT_PIN [get_timing_path <PATH>]]]]
get_property FILE_NAME [get_cells -of [get_pins [get_property ENDPOINT_PIN [get_timing_path <PATH>]]]]
```

4. **Verify** → Re-run methodology checks and timing analysis

### DON'T:
❌ Rerun methodology checks or timing analysis if there is no updated constraint or RTL


## Verification

* [ ] Setup violation is reduced to acceptable level or properly constrained
* [ ] Clock relationships are correctly defined
* [ ] No new unresolved methodology warnings are created
* [ ] Timing closure strategy is appropriate for the violation severity

## Final Report Guidance

Document the resolution:
- If constraints were updated: List the specific XDC changes
- If design changes needed: Document the architectural recommendations
- Include before/after timing numbers
- Note any waived violations with justification

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis) - Design Analysis and Closure Techniques
- [UG903](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints) - Using Constraints
- [UG949](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology) - UltraFast Design Methodology (UltraScale/UltraScale+)
- [UG1388](https://docs.amd.com/r/en-US/ug1388-acap-system-integration-validation-methodology) - Vivado Design Methodology (Versal)
- [CommandHistory](/VIVADO_IMPLEMENTATION_COMMAND_HISTORY.md) - Finding command history in Vivado
