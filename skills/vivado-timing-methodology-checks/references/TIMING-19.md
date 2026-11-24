<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-19: Inverted generated clock waveform on ODDR

## Metadata

- **Check ID**: TIMING-19
- **Severity**: Critical Warning

## Description

A forwarded clock (output port driven by an ODDR/ODDRE1) has a `create_generated_clock` waveform that is inverted relative to the incoming clock.

## Message

```
The waveform of the generated clock <CLOCK_GROUP> is inverted compare to the waveform of the incoming clock <CLOCK_GROUP>
```

## Flow

Redefine the forwarded clock with the `-invert` option so it matches the ODDR output.

* **Type**: Automated Resolution

```tcl
create_generated_clock -name <fwd_clk> \
    -source [get_pins <ODDR>/C] -divide_by 1 -invert [get_ports <fwd_clk_port>]
```

## References

- [UG903](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints/Example-Six-Forwarded-Clock-Driven-by-ODDR) - Forwarded Clock Driven by ODDR
- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Description) - Design Analysis and Closure Techniques (TIMING-19)
