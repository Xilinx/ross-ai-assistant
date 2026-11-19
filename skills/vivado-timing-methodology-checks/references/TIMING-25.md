<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-25: Invalid clock waveform on gigabit transceiver (GT)

## Metadata

- **Check ID**: TIMING-25
- **Severity**: Critical Warning

## Description

A user clock defined on a GT output pin (or its net) is inconsistent with the transceiver settings. The auto-derived period computed from the GT configuration and its input clock does not match the user-defined period.

## Message

```
The waveform of the user clock <CLOCK_GROUP> defined on the transceiver output pin <NETLIST_ELEMENT> or on the net connected to that pin is not consistent with the transceiver settings. The auto-derived clock period is <MESSAGE_STRING> and the user-defined clock period is <MESSAGE_STRING>. The expected clock waveform is computed based on the GT settings and the incoming clock to the transceiver. It is recommended to check that there are no missing clocks to the GT and that all the transceiver settings are correct
```

## Flow

Verify the GT reference clocks are constrained and the transceiver settings are correct, then fix the clock definition to match the expected waveform.

* **Type**: User Resolution

1. Confirm every GT reference clock has a `create_clock`:
```tcl
create_clock -name gtrefclk -period <period> [get_ports <gtrefclk_port>]
```
2. Check the GT settings (divider/data width) against the auto-derived period reported in the message.
3. Correct the user clock definition on the GT output so its period matches the auto-derived value.

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-25)
- [UG903](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints) - Using Constraints
