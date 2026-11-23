<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-2: Invalid primary clock source pin

## Metadata

- **Check ID**: TIMING-2
- **Severity**: Critical Warning
- **First Release**: 2025.1

## Description

Invalid primary clock source pin

## Message

```
A primary clock <CLOCK_GROUP> is created on an inappropriate pin <NETLIST_ELEMENT>. It is recommended to create a primary clock only on a proper clock source (input port or primitive output pin with no timing arc)
```

```real example
A primary clock new_clk is created on an inappropriate pin reg1_q_reg[0]/Q. It is recommended to create a primary clock only on a proper clock source (input port or primitive output pin with no timing arc)
```

### Explanation

Incorrect object specified for `create_clock` definition. Clocks should be defined either on a port or an output pin with no timing arcs

### Flow
1. Using CLOCK_TRACING, trace from the <NETLIST ELEMENT> to find the upstream clock source and the clock that reaches it.
2. Write out the full constraints file.
3. Check CASE 5 first: if CLOCK_TRACING finds no primary pin, or the pin is VCC or GND, apply CASE 5 and stop. The driver class does not matter for it.
4. Otherwise identify the **driver class** of the pin the `create_clock` is defined on (see below). The class, not the clock period, decides the resolution.
5. Understand the expected clock definition based on primary clock and CMB settings.
6. Gather the clock data the class needs (see **Generate Data**).
7. Select the CASE. Report the CASE number that was applied.

**NOTE:** Solve TIMING-1 and TIMING-2 before solving other violations.

### Driver class

Find the cell that owns the <NETLIST_ELEMENT> pin and its type:

```tcl
set pin  [get_pins <NETLIST_ELEMENT>]
set cell [get_cells -of_objects $pin]
get_property REF_NAME  $cell
get_property DIRECTION $pin
```

| Class | `REF_NAME` of the driver | Pin | Cases |
|-------|--------------------------|-----|-------|
| **A. Clock primitive** | `MMCM*`, `PLL*`, `BUFGCE_DIV`, `MBUFG*`, `BUFG*` | Output pin | CASE 1, 2, 3, 4, 8 |
| **B. Register** | `FD*` (`FDRE`, `FDSE`, `FDCE`, `FDPE`) | `Q` | CASE 6, 7 |
| **C. Anything else** | LUTs, latches, IO buffers, any other primitive, hierarchical or unknown | Any | CASE 9 |

CASE 5 depends only on CLOCK_TRACING, not on the driver class. Flow step 3 checks it first.

### Generate Data

For every violation:
- Driver class data: `REF_NAME`, `DIRECTION`, and the full pin name.
- The `create_clock` constraint on the pin: name, period, waveform, and the file and line it came from.
- Whether the clock name is referenced by an exception (`set_false_path`, `set_clock_groups`, `set_max_delay`, `set_multicycle_path`, `set_input_delay`, `set_output_delay`, and so on) anywhere in the constraints file.

For Class B (register) also:
```tcl
set master [get_clocks -of_objects [get_pins <REGISTER_CELL>/C]]   ;# the clock arriving at the register clock pin
get_property NAME         $master
get_property PERIOD       $master
get_property WAVEFORM     $master
get_property SOURCE_PINS  $master       ;# the primary source: port or pin the master clock is defined on
get_property IS_GENERATED $master
```
Also record the cells that CLOCK_TRACING walked from the primary source to the register clock pin (this is the insertion delay path), and whether the register clock is inverted.

Capture the impact on primary clocks of any Clock modifying block (CMB) settings.

## Resolution

| Case | Conditions | Action |
|------|------------|--------|
| 1 | **Redefinition Matches Expected, Clock is not referenced by exceptions, driver is clock modifying block or bufg** | Remove redefinition |
| 2 | **Redefinition Matches Expected, Clock is referenced by exceptions, driver is clock modifying block**| Use generated clock redefinition, rename auto derived clock |
| 3 | **Redefinition Matches Expected, Clock is referenced by exceptions, driver is BUFG**| Use generated clock redefinition with -divide_by 1, remove `create_clock` redefinition |
| 4 | **No clock defined on primary pin, driver is clock modifying block**| Create a clock on the primary pin that will generate the same clock at the redefinition pin. Remove the `create_clock` redefinition |
| 5 | **No object found for a primary pin or a VCC or GND returned**| Report to the user that there is no clock primary pin found. Remove the constraint. |
| 6 | **Driver is a register, waveform can be matched**| Define a `create_generated_clock` on the Q pin based off the clock driving the register clock pin. |
| 7 | **Driver is a register, waveform can not be matched**| User must decide what the correct clock is. Provide a `create_generated_clock` recommendation based off the primary clock definition that generates the same waveform as current definition. Give the option to waive. |
| 8 | **Redefinition does not match Expected**| User must decide what the correct clock is. Provide a recommendation based off the primary clock definition. Give the option to waive. |
| 9 | **Driver is not a CMB, *BUFG*, or register**| User must decide what the correct clock is. Give the option to waive. |

### Case Details

"Matches expected" means the user's period and waveform equal the primary clock plus any clock modifying block changes (see Flow step 5).

**CASE 1:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: 
* [ ] Class A driver (CMB or `BUFG*`)
* [ ] An upstream clock is defined on the CLOCK_TRACING objects
* [ ] The definition matches expected
* [ ] The clock name is not referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Remove the existing `create_clock`. The upstream clock propagates through a BUFG. For a CMB, Vivado auto-derives the generated clock on the output pin from the upstream clock and the primitive settings.
* [ ] Report the user's period and waveform next to the derived ones (see [METHODOLOGY_RESOLUTION_REPORT_DETAILS.md, Clock comparison table](./METHODOLOGY_RESOLUTION_REPORT_DETAILS.md#clock-comparison-table)).

**CASE 2:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: 
* [ ] Driver is an `MMCM*`, `PLL*`, `BUFGCE_DIV` or `MBUFG*`
* [ ] An upstream clock is defined on the CLOCK_TRACING objects
* [ ] The definition matches expected
* [ ] The clock name is referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Remove the existing `create_clock`.
* [ ] Rename the auto-derived clock to the existing name so the exceptions keep working. Give the pin only. Do not give `-source`, `-divide_by` or `-multiply_by`, which would redefine the clock instead of renaming it. Place it after the upstream clock definition:
```tcl
create_generated_clock -name <EXISTING_CLOCK_NAME> [get_pins <NETLIST_ELEMENT>]
```
* [ ] Record the user's and the derived period and waveform as in CASE 1.

**CASE 3:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: 
* [ ] Driver is a `BUFG*`
* [ ] An upstream clock is defined on the CLOCK_TRACING objects
* [ ] The definition matches expected
* [ ] The clock name is referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Remove the existing `create_clock`.
* [ ] A BUFG has no auto-derived clock to rename (Vivado error 18-851). Create a generated clock with the existing name from the upstream source, so the exceptions keep working. Place it after the upstream clock definition:
```tcl
create_generated_clock -name <EXISTING_CLOCK_NAME> -divide_by 1 -source <UPSTREAM_SOURCE> [get_pins <NETLIST_ELEMENT>]
```
* [ ] Record the user's and the derived period and waveform as in CASE 1.

**CASE 4:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: 
* [ ] Driver is a CMB (Class A)
* [ ] CLOCK_TRACING returns objects, but no clock is defined on them
* **Action**: 
* [ ] Propose a `create_clock` on the CLOCK_TRACING object. Derive its period from the user's definition, accounting for the CMBs in the path.
* [ ] Remove the existing `create_clock`.
* [ ] Then select CASE 1, 2, 3 or 8 for the pin with the new upstream clock in place. Do not treat the violation as resolved until that case is applied.

**CASE 5:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: Med
* **When**: 
* [ ] CLOCK_TRACING returns no objects, or returns a ground or power driver
* **Action**: 
* [ ] Report that no clock source was found for the pin.
* [ ] Remove the constraint.

**CASE 6:**
* **Type**: Auto Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: 
* [ ] Class B: the driver is an `FD*` register and the pin is `Q`
* [ ] CLOCK_TRACING finds exactly one master clock at the register clock pin, and it has a known primary source
* [ ] The user's waveform can be reproduced on the master clock edges (see **Reproducing the user's waveform**)
* **Action**: 
* [ ] Remove the existing `create_clock`.
* [ ] Create a `create_generated_clock` on the `Q` pin, with the existing name so exceptions keep working.
* [ ] Use the **register's clock pin** (`<REGISTER_CELL>/C`, the pin the register divides) as `-source`, and name the master clock arriving at that pin with `-master_clock`. Do not use the primary source port (the port or pin from `SOURCE_PINS` of the master clock) as `-source`.
* [ ] Record the path from the primary source to the register clock pin: the primary source, the cells CLOCK_TRACING walked to the register clock pin, and the register clock pin used as `-source`. Report a numeric delay only if a Vivado command returned it.
```tcl
create_generated_clock -add -name <EXISTING_CLOCK_NAME> -source [get_pins <REGISTER_CELL>/C] -master_clock <MASTER_CLOCK> \
    <-divide_by N | -edges {<RISE> <FALL> <NEXT_RISE>}> [get_pins <REGISTER_CELL>/Q]
```

**CASE 7:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Class B (a register driver) and any of:
* [ ] The pin is not `Q`
* [ ] CLOCK_TRACING finds no master clock, or more than one, at the register clock pin
* [ ] The user's waveform cannot be reproduced on the master clock edges
* **Action**: 
* [ ] Print the master clock, the user's definition and the reason it cannot be reproduced.
* [ ] Recommend a `create_generated_clock` from the primary clock that gives the closest waveform to the user's definition.
* [ ] The user decides the correct clock, or waives.

**CASE 8:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: 
* [ ] Class A driver (CMB or `BUFG*`)
* [ ] An upstream clock is defined on the CLOCK_TRACING objects
* [ ] The definition does not match expected
* **Action**: 
* [ ] Print the driver type, the pin, the user's definition, and the expected definition from the upstream clock, as in the clock comparison table.
* [ ] The user decides. Options: 1) keep the primary clock and remove the `create_clock` (recommended) 2) update the primary clock to match the user's definition 3) waive.

**CASE 9:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: 
* [ ] Class C driver (not a CMB, `BUFG*` or register)
* **Action**: 
* [ ] Print the driver type, the pin, the user's definition, and what CLOCK_TRACING found upstream.
* [ ] The user decides the correct clock. Options: 1) keep the primary clock and remove the `create_clock` (recommended) 2) update the primary clock 3) waive.


### Reproducing the user's waveform

Use this for CASE 6. The register switches on one active edge of the master clock (the rising edge unless the register clock is inverted). Master edges are numbered from 1 (first rising edge), 2 (first falling), 3 (second rising), and so on.

Let the master have period `Pm` and rising edge at `m_r`. The user's clock has period `Pu` and waveform `{u_r u_f}`. The three times that define it are `u_r`, `u_f` and `u_r + Pu`.

1. For each of the three times `t`, compute `n = (t - m_r) / Pm`. Each `n` must be a whole number. If not, the register cannot produce that edge. This is CASE 7.
2. For a rising-edge register, the master edge index is `2n + 1`. For an inverted clock use the falling edges, `2n + 2`.
3. If the first index is 1, the duty cycle is 50%, and `Pu = N * Pm`, use `-divide_by N`. Otherwise use `-edges {<i1> <i2> <i3>}` with the three indices.
4. Check the result: the generated clock period and waveform must equal the user's period and waveform. If they do not, it is CASE 7.

| Master | User clock | Result |
|--------|------------|--------|
| 10 ns, `{0 5}` | 20 ns, `{0 10}` | `n` = 0, 1, 2, so `-edges {1 3 5}`, which is `-divide_by 2` |
| 10 ns, `{0 5}` | 30 ns, `{0 10}` | `n` = 0, 1, 3, so `-edges {1 3 7}` |
| 10 ns, `{0 5}` | 25 ns, `{0 12.5}` | `n` = 0, 1.25, so CASE 7 |


## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [Clock Tracing Methodology](./CLOCK_TRACING.md)
