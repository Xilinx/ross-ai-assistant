<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-49: Unsafe enable or reset topology from parallel BUFGCE_DIV

## Metadata

- **Check ID**: TIMING-49
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

Parallel `BUFGCE_DIV` buffers with `BUFGCE_DIVIDE` > 1 do not share the same `CE` and `CLR` signals (or `CLR` is tied to power/ground). Their divided outputs can end up phase-shifted in hardware, so timing between them is unsafe.

### Full Message Description property.

```
To ensure safe timing between parallel BUFGCE_DIV cells where the BUFGCE_DIVIDE property is set to a value greater than 1, both buffers <NETLIST_ELEMENT> and <NETLIST_ELEMENT> must use the same enable signal (CE) and the same active clear signal (CLR). The CLR pin must not be driven by power or ground. Otherwise, the divided clock might become phase-shifted to one another in hardware. It is recommended to use the Safe Clock Startup reset circuitry to reset both BUFGCE_DIV at the same time.
```
**Example:**
```
To ensure safe timing between parallel BUFGCE_DIV cells where the BUFGCE_DIVIDE property is set to a value greater than 1, both buffers bufdiv_a and bufdiv_b must use the same enable signal (CE) and the same active clear signal (CLR). ...
```

### Explanation
A `BUFGCE_DIV` with `BUFGCE_DIVIDE` > 1 has internal divider state. Two parallel buffers fed by the same clock are only phase-aligned if they are released from reset on the same cycle. Different or constant `CLR` pins let the dividers start on different cycles, which gives an unknown clock relationship. This is a **netlist** (RTL/IP) topology fix, not a constraint fix.

**DO**
✅  Confirm both buffers have `BUFGCE_DIVIDE` > 1: `get_property BUFGCE_DIVIDE [get_cells <cell>]`.
✅  Compare the nets on `CE` and `CLR` of both buffers.
✅  Recommend the Safe Clock Startup reset circuitry (Clocking Wizard option).

**DO NOT**
❌ Waive without confirming that both buffers are reset by the same logic.
❌ Add `set_clock_groups` to hide the warning - it does not make the hardware safe.
❌ Edit RTL automatically.

## Generate Data

1. Buffer properties and control nets:
```tcl
foreach b {<buf1> <buf2>} {
  set c [get_cells $b]
  puts "$b DIV=[get_property BUFGCE_DIVIDE $c]"
  foreach p {CE CLR} {
    set pin [get_pins $b/$p]
    puts "  $p net=[get_nets -quiet -of_objects $pin] driver=[get_pins -quiet -leaf -filter {DIRECTION==OUT} -of_objects [get_nets -quiet -of_objects $pin]]"
  }
}
```
2. Find the common input clock: `get_nets -of_objects [get_pins <buf>/I]`.
3. Locate the reset logic driving `CLR` (e.g. `all_fanin -flat -only_cells [get_pins <buf>/CLR]`).
4. Check if the clocks between the buffers are timed together: `report_clock_interaction`.

## Flow

1. Gather the data; classify the failing condition (different CE, different CLR, CLR tied to constant).

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Any case; RTL/IP change is required.
* **Prompt**:
```
Parallel BUFGCE_DIV buffers <NETLIST_ELEMENT> and <NETLIST_ELEMENT> have BUFGCE_DIVIDE > 1 but <CE differs | CLR differs | CLR is tied to constant>. Do you want to:
a) Drive CE and CLR of both buffers from the same signals (RTL change); enable Safe Clock Startup in the Clocking Wizard
b) Treat the crossing between the divided clocks as asynchronous (add clock groups + CDC synchronizers) - the clocks are not phase related
c) Leave it and create a waiver (justification required; design may fail in hardware)
```
* Provide the user with the RTL guidance. This skill does not edit RTL.

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: The user chooses option (b).

Propose `set_clock_groups -asynchronous` between the two divided clocks, and flag that synchronization circuitry is required (see [TIMING-47](./TIMING-47.md) for the risk of masking synchronous crossings).

## Verification
* [ ] Methodology violation TIMING-49 is no longer present
* [ ] Both buffers share the same `CE` and `CLR` nets and `CLR` is not constant
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Buffer | BUFGCE_DIVIDE | CE | CLR |
|--------|--------|---------------|----|-----|
| **Before** | bufdiv_a | 2 | ce_a | GND |
| **Before** | bufdiv_b | 2 | ce_b | rst_b |
| **After**  | bufdiv_a | 2 | ce_common | rst_common |
| **After**  | bufdiv_b | 2 | ce_common | rst_common |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-49
- UltraFast Design Methodology Guide (UG949) - Safe Clock Startup
- [TIMING-51](./TIMING-51.md) - related clocks from parallel CMBs
