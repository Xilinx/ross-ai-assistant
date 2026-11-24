<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-28: Auto-derived clock referenced by a timing constraint

## Metadata

- **Check ID**: TIMING-28
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A timing constraint refers to an auto-derived clock (for example an MMCM output clock) by its tool-generated name. That name can change when the netlist or constraints change, which silently invalidates the constraint.

### Full Message Description property.

```
The auto-derived clock <CLOCK_GROUP> is referenced by name inside timing constraint (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). It is recommended to reference an auto-derived clock by the pin name attached to the clock: get_clocks -of_objects [get_pins <NETLIST_ELEMENT>]
```
**Example:**
```
The auto-derived clock clk_out1_clk_wiz_0 is referenced by name inside timing constraint (see constraint position 12 in the Timing Constraint window in Vivado IDE). It is recommended to reference an auto-derived clock by the pin name attached to the clock: get_clocks -of_objects [get_pins clk_wiz_0/inst/mmcme4_adv_inst/CLKOUT0]
```

### Explanation
* `<CLOCK_GROUP>` is an auto-derived clock (`IS_GENERATED=1`, `IS_USER_GENERATED=0`) whose name Vivado chooses.
* `<NETLIST_ELEMENT>` is the pin the clock is attached to.
* `<MESSAGE_STRING>` is the constraint position in the Timing Constraints window; map it to a line in the XDC using `FILE_NAME`/`LINE_NUMBER`.

**DO**
✅  Check the pin has exactly one clock before substituting the pin-based reference
✅  Preserve every other option of the referencing constraint
✅  Apply the same substitution to every constraint referencing the name

**DO NOT**
❌ Change a constraint that references a clock which was explicitly renamed by `create_generated_clock` (it is then user generated and the check does not apply)
❌ Substitute a pin reference when more than one clock is present on the pin (a mux or several clocks would widen the scope of the constraint)

## Generate Data

1. Verify the clock is auto-derived and get its pin:
```tcl
set clk [get_clocks <CLOCK_GROUP>]
get_property IS_GENERATED $clk
get_property IS_USER_GENERATED $clk
get_property SOURCE_PINS $clk
```
2. Count clocks on the pin: `llength [get_clocks -of_objects [get_pins <NETLIST_ELEMENT>]]`
3. Find every constraint referencing the name: grep `constraints.xdc` for `<CLOCK_GROUP>`; capture file and line.
4. For IP scoped constraints see `IP_CONSTRAINTS.md` (do not edit IP constraint files; override in the top-level XDC instead).

## Flow

1. Find all constraints that reference <CLOCK_GROUP> by name (position <MESSAGE_STRING>).
2. Confirm the clock is auto-derived and exactly one clock exists on <NETLIST_ELEMENT>.

**DO NOT**
❌ Automate fixes when the line number or file name is null
❌ Edit constraints owned by an IP

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: One clock is present on <NETLIST_ELEMENT> and the referencing constraint is in a user XDC
1. In each referencing constraint replace `[get_clocks <CLOCK_GROUP>]` with
```tcl
[get_clocks -of_objects [get_pins <NETLIST_ELEMENT>]]
```
2. Report each replaced constraint with before/after lines.

**CASE 2:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: Many constraints reference the same auto-derived clock (replacing each is noisy)
* **Assumption**: A stable user-chosen name is acceptable. UG903 lets an auto-derived clock be renamed without changing it:
```tcl
create_generated_clock -name <stable_name> [get_pins <NETLIST_ELEMENT>]
```
Place it before the first referencing constraint and update the referencing constraints to `<stable_name>`. State the assumption in the report.

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Medium
* **When**: More than one clock is present on <NETLIST_ELEMENT>, or the reference is inside an IP constraint file
* **Prompt**:
```
Do you want to:
a) Reference only the intended clock by renaming it with create_generated_clock
b) Reference all clocks on the pin with get_clocks -of_objects [get_pins <pin>]
c) Leave as is and create a waiver
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] Referencing constraints still resolve to the same clock (`report_exceptions`, `report_clocks`)
* [ ] No new "No valid object(s) found" warnings are created

## Final Report

| Aspect | Constraint |
|--------|-----------|
| **Before** | `set_false_path -from [get_clocks clk_out1_clk_wiz_0] -to [get_clocks clk_b]` |
| **After** | `set_false_path -from [get_clocks -of_objects [get_pins clk_wiz_0/inst/mmcme4_adv_inst/CLKOUT0]] -to [get_clocks clk_b]` |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-28
- Vivado Design Suite User Guide: Using Constraints (UG903) - Renaming Auto-Derived Clocks
- IP Constraints (<skill_dir>/references/IP_CONSTRAINTS.md)
