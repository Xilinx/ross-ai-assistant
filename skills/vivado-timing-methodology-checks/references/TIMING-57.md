<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-57: Unsupported configuration with PHASESHIFT_MODE and digital deskew

## Metadata

- **Check ID**: TIMING-57
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2022.1

## Description

Versal only. A clock modifying block with digital deskew has `PHASESHIFT_MODE=WAVEFORM`, which the timer cannot honor.

### Full Message Description property.

```
The clock modifying block <NETLIST_ELEMENT> is configured for digital deskew and has PHASESHIFT_MODE=WAVEFORM. This combination is unsupported, and timing analysis will proceed by treating it as if PHASESHIFT_MODE=LATENCY. Change the specified cell configuration to PHASESHIFT_MODE=LATENCY and ensure that no timing constraints are written against the expectation of PHASESHIFT_MODE=WAVEFORM.
```
**Example:**
```
The clock modifying block clk_gen/mmcm_i is configured for digital deskew and has PHASESHIFT_MODE=WAVEFORM. ...
```

### Explanation
With digital deskew, pin phase shift can only be modeled as latency through the CMB, not as a shifted waveform. The tool already applies LATENCY modeling, so sign-off accuracy is not affected. The warning remains until the property is set to match, and any exception written for WAVEFORM (for example multicycle paths) may now be wrong. See [TIMING-31](./TIMING-31.md) for the related phase-shift multicycle check.

**DO**
✅ Confirm the cell uses deskew (`CLKIN*_DESKEW`/`CLKFB*_DESKEW` connected) and read `PHASESHIFT_MODE`
✅ Search for multicycle paths and clock-edge based exceptions that depend on WAVEFORM

**DO NOT**
❌ Leave WAVEFORM-based multicycle constraints in place after the change without review

## Flow
1. Inspect the cell.
```tcl
set cmb [get_cells <NETLIST_ELEMENT>]
get_property PHASESHIFT_MODE $cmb
get_nets -quiet -of [get_pins -quiet -filter {REF_PIN_NAME=~*DESKEW*} -of $cmb]
```
2. Search `constraints.xdc` for `set_multicycle_path` involving clocks of this CMB.
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: No multicycle path or other exception references clocks of this CMB
* **Assumption**: Timing already treats the cell as LATENCY, so setting the property makes the cell match the timer's behaviour and does not change timing results

```tcl
set_property PHASESHIFT_MODE LATENCY [get_cells <NETLIST_ELEMENT>]
```

1. Apply the property (RTL attribute / IP parameter / `set_property`)
2. Report that no exceptions depend on WAVEFORM
3. State the assumption

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Multicycle paths or other exceptions exist on clocks of this CMB
* **Prompt**: Show the exceptions, set `PHASESHIFT_MODE=LATENCY`, then ask the user whether each exception should be removed or kept.

## Verification
* [ ] Methodology violation is no longer present
* [ ] `get_property PHASESHIFT_MODE` returns LATENCY
* [ ] No stale WAVEFORM-based exceptions remain
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Cell | PHASESHIFT_MODE |
|--------|------|-----------------|
| **Before** | clk_gen/mmcm_i | WAVEFORM |
| **After** | clk_gen/mmcm_i | LATENCY |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-57
- [TIMING-31](./TIMING-31.md), [TIMING-53](./TIMING-53.md), [TIMING-55](./TIMING-55.md)
