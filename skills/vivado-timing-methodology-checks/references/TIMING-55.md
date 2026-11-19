<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-55: Multiple clocks reaching a CMB deskew pin

## Metadata

- **Check ID**: TIMING-55
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2020.2

## Description

Versal only. More than one clock propagates to a deskew pin (`CLKIN*_DESKEW`/`CLKFB*_DESKEW`) of an MMCM/XPLL/DPLL.

### Full Message Description property.

```
Multiple clocks propagate to the deskew pin <NETLIST_ELEMENT> and will cause timing analysis to be inaccurate and could potentially result in HW failures. The timing constraints should be updated so that a single clock propagates to the deskew pin. The list of clocks reaching the pin is: <CLOCK_GROUP>
```
**Example:**
```
Multiple clocks propagate to the deskew pin clk_gen/mmcm_i/CLKIN1_DESKEW ... The list of clocks reaching the pin is: clk_a clk_b
```

### Explanation
Deskew compares the phase of the input clock at the CMB with the fed-back clock. With more than one clock on the pin the phase reference is ambiguous, so signoff timing is inaccurate. Typical causes are a clock mux ahead of the deskew pin, or two `create_clock` constraints reaching the same net.

**DO**
✅ List the clocks on the pin and trace each one back (`<skill_dir>/references/CLOCK_TRACING.md`)
✅ Check whether a clock mux (BUFGMUX/BUFGCTRL) is in front of the deskew pin

**DO NOT**
❌ Delete a clock definition without asking. Each clock may be needed elsewhere
❌ Assume which clock is the intended one

## Flow
1. Show clocks on the pin and their sources.
```tcl
set pin [get_pins <NETLIST_ELEMENT>]
get_clocks -of_objects $pin
all_fanin -flat -startpoints_only $pin
```
2. Search `constraints.xdc` for the `create_clock` / `create_generated_clock` constraints of each clock.
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: A clock mux (or similar) merges alternatives into the deskew pin
* **Prompt**: Ask which clock should reach the pin. Options:
a) Make the clocks exclusive so only one reaches the pin at a time:
```tcl
set_clock_groups -physically_exclusive -group [get_clocks <clk_a>] -group [get_clocks <clk_b>]
```
b) Change the clocking topology so a single clock drives the deskew pin (RTL change)
c) Waiver `<USER> - <DATE> - <REASON>`

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: Duplicate/accidental clock definitions put a second clock on the pin
* **Prompt**: Show both definitions, ask which to keep, then propose removing the other.

## Verification
* [ ] Methodology violation is no longer present
* [ ] `get_clocks -of_objects <pin>` returns exactly one clock
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Clocks on deskew pin | Constraint |
|--------|----------------------|------------|
| **Before** | clk_a, clk_b | - |
| **After** | clk_a | `set_clock_groups -physically_exclusive ...` or removed definition |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-55
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
- [TIMING-56](./TIMING-56.md), [TIMING-53](./TIMING-53.md)
