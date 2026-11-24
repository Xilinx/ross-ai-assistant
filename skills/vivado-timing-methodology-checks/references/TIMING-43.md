<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-43: Min period or min pulse width violation on a gigabit transceiver (GT)

## Metadata

- **Check ID**: TIMING-43
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2018.1

## Description

A clock pin on a GT primitive violates its MIN_PERIOD or MIN_PULSE_WIDTH check. The clock is faster than the hardware inside the GT tolerates, so the design can fail in hardware and the Power Analysis Report is inaccurate for that GT.

### Full Message Description property.

```
The GT pin <NETLIST_ELEMENT> has MIN_PERIOD or MIN_PULSE_WIDTH violations. The Power Analysis Report will be inaccurate for the GT instance
```
**Example:**
```
The GT pin gt_wiz/inst/gen_gtwizard_gthe4_top.gtwizard_ultrascale_0_gtwizard_gthe4_inst/gen_gtwizard_gthe4.gen_channel_container[0].gen_enabled_channel.gthe4_channel_wrapper_inst/channel_inst/gthe4_channel_gen.gen_gthe4_channel_inst[0].GTHE4_CHANNEL_PRIM_INST/DRPCLK has MIN_PERIOD or MIN_PULSE_WIDTH violations. The Power Analysis Report will be inaccurate for the GT instance
```

### Explanation
Pulse width and minimum period checks on GT clock pins (`DRPCLK`, `TXUSRCLK`, `RXUSRCLK2`, ...) protect silicon limits defined in the device data sheet (AC/DC characteristics). They are a silicon-level limitation and cannot be fixed with timing exceptions. The only resolutions are to reduce the clock frequency on the pin, or to correct a wrong clock definition that makes the clock appear faster than it is.

Common root causes:
* A `create_clock` period on the clock feeding the GT is too small (typo such as 2.0 instead of 20.0).
* A clock multiplied by an MMCM/PLL then divided incorrectly.
* The design intentionally runs the GT user clock/DRP clock above the data sheet maximum.

**DO**
✅  Check the period actually defined on the clock reaching the GT pin before concluding the frequency is real.
✅  Compare the clock frequency to the maximum for the speed grade/device family in the data sheet.

**DO NOT**
❌ Waive or modify constraints to hide a real min period violation.
❌ Reduce a clock period to "fix" it - frequency must go DOWN.
❌ Automate frequency changes; the user owns the system clocking.

## Generate Data

1. Report the pulse width/period violations on the GT pin:
```tcl
report_pulse_width -all_violators -return_string
```
2. Identify the clock on the pin and its definition:
```tcl
set clk [get_clocks -of_objects [get_pins <NETLIST_ELEMENT>]]
report_clocks -return_string   ;# lists every clock; find $clk in the output
get_property PERIOD $clk
```
3. Trace the clock back to its source with `<skill_dir>/references/CLOCK_TRACING.md` (call `methodology_check::trace_clock_back_to_source`) and locate the `create_clock`/`create_generated_clock` in `constraints.xdc`.
4. Capture the GT type: `get_property REF_NAME [get_cells -of_objects [get_pins <NETLIST_ELEMENT>]]`.

## Flow

1. Gather the data above; determine whether the frequency is a constraint error or a real design frequency.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The defined clock period is faster than the intended frequency (constraint typo).
* **Prompt**:
```
Clock <CLOCK_GROUP> reaching <NETLIST_ELEMENT> is defined with period <P> ns (<F> MHz) but the maximum permitted is <MAX> ns. Is the period constraint wrong? If so, what is the correct period?
```
Update the `create_clock` period to the confirmed value.

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Medium
* **When**: The frequency is real.
* **Prompt**:
```
The real clock frequency exceeds the GT data sheet limit. Do you want to:
a) Lower the clock frequency (change MMCM/PLL/BUFG_GT divider or the GT/IP configuration)
b) Choose a faster speed grade or device
c) Leave as is and create a waiver (justification required; the design could fail in hardware)
```

## Verification
* [ ] Methodology violation TIMING-43 is no longer present
* [ ] `report_pulse_width -all_violators` shows no violations on GT pins
* [ ] Clock periods on the GT pins match the intended frequencies
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Constraint / Setting | Period | GT Pin | Clock |
|--------|---------------------|--------|--------|-------|
| **Before** | `create_clock -period 2.000 -name gt_drp [get_ports drp_clk]` | 2.000 ns | gt_inst/DRPCLK | gt_drp |
| **After**  | `create_clock -period 20.000 -name gt_drp [get_ports drp_clk]` | 20.000 ns | gt_inst/DRPCLK | gt_drp |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-43
- Device data sheet, AC/DC switching characteristics (GT clock limits)
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
