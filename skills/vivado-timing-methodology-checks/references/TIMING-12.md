<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-12: Clock Reconvergence Pessimism Removal disabled

## Metadata

- **Check ID**: TIMING-12
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Clock Reconvergence Pessimism Removal (CRPR) has been disabled. CRPR removes the artificial pessimism introduced by using the max delay on one clock path and the min delay on the other along the common portion of the clock network. With CRPR disabled, timing analysis is over-pessimistic and closure can look impossible even when the design would meet timing.

### Full Message Description Property

```
The Clock Reconvergence Pessimism Removal (CRPR) mode has been disabled. It is not recommended to
perform timing analysis in this mode as over-pessimistic clock tree delays could result in
impossible timing closure
```

## Flow

CRPR should be enabled for accurate timing analysis. Re-enable it (per UG906):

```tcl
config_timing_pessimism -enable
```

* **Type**: Automated Resolution

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) — Clock Pessimism Removal; TIMING-12
- Vivado Design Suite Tcl Command Reference Guide (UG835) — `config_timing_pessimism`
