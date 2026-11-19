<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-5: Invalid inverted waveform on a clock tree

## Metadata

- **Check ID**: TIMING-5
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Invalid waveform redefinition on a clock tree.

### Full Message Description Property

```
Invalid inverted waveform on a clock tree. The generated clock <CLOCK_GROUP> is defined downstream of clock <CLOCK_GROUP> and has an inverted waveform definition compare to the incoming clock
```

**Example:**
```
Invalid inverted waveform on a clock tree. The generated clock clk_out is defined downstream of clock clk_in and has an inverted waveform definition compare to the incoming clock
```

### Explanation

A generated clock's `-invert` switch does not match the clock path: either the path inverts and `-invert` was omitted, or `-invert` was applied where the tool sees no inversion. The methodology check has already resolved the real clock-tree behavior, so the reported mismatch is authoritative.

**Policy**: trust the tool. Toggle `-invert` on the offending `create_generated_clock`: add it if absent, remove it if present. No netlist tracing is needed.

## Data

Read `./methodology_results/tmp/constraints_profile.json` (see IP_CONSTRAINTS.md). In `constraints`, find the entry with `type` `create_generated_clock` and `options["-name"]` equal to the generated clock in the message (or use the constraint position ID as the key). From it:

* `options["-invert"]`: `true` means `-invert` is present; a missing key means it is absent.
* `text`: the constraint as written; `file`, `line`: where it is defined.
* `is_ip` / `ip_instance`: whether the constraint comes from an IP.

Do not automate the fix if there is no matching entry or the entry has no `file`/`line`. If the constraint is IP (`is_ip`), it may need to be edited at the IP source rather than the top-level XDC.

## Resolution

* **Type**: Automated Resolution

Edit the existing constraint in place (never add a second `create_generated_clock`) and change only `-invert`. Keep the clock name, `-source`, `-master_clock`, divide/multiply factors and edges.

```tcl
# -invert absent: add it
create_generated_clock -name <generated_clock_name> -source <src_pin> -divide_by 1 -invert [get_pins <gen_clk_pin>]
# -invert present: remove it
create_generated_clock -name <generated_clock_name> -source <src_pin> -divide_by 1 [get_pins <gen_clk_pin>]
```

**Verify**: after the fix, `constraints_after_fix.xdc` (or `report_clocks`) must show the clock with `-invert` in the opposite state from the profile, and the rest of the constraint unchanged.

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-5
- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Using Constraints (UG903) - Section on Generated Clocks
- AMD Adaptive Support 32543 - Incorrect Clock Analysis after Inverting a Clock with a BUFIO2 or an INV
- [IP Constraints](./IP_CONSTRAINTS.md)

## Examples

*To be documented with real-world examples from TIMING-5 testcase as required.*
