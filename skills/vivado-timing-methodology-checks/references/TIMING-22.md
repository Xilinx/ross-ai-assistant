<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-22: Missing external delay on MMCM

## Metadata

- **Check ID**: TIMING-22
- **Severity**: Warning

## Description

An MMCM with an external feedback loop has no `set_external_delay` between its `FBOUT` and `FBIN` ports, so the board delay in the feedback path is unmodeled.

## Message

```
The MMCM <NETLIST_ELEMENT> has an external feedback loop but no external delay has been specified between FBOUT and FBIN. It is recommended to specify an external delay with set_external_delay between the two ports connected to the pins FBOUT and FBIN with an external feedback loop
```

## Flow

Add a `set_external_delay` between the FBOUT and FBIN ports using the actual board delay. Query the user for the delay value.

* **Type**: User Resolution

```tcl
set_external_delay -from [get_ports <FBOUT_port>] \
    -to [get_ports <FBIN_port>] <delay_ns>
```
Add a recommendation of 180ps per inch. Set a default value of 2 inches.

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-22)
- [UG903](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints) - Using Constraints (set_external_delay)
