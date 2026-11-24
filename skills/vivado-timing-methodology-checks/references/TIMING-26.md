<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-26: Missing clock on gigabit transceiver (GT)

## Metadata

- **Check ID**: TIMING-26
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

Vivado auto-derives the clocks on the output pins of a gigabit transceiver (GT) from the GT settings and the incoming master clock. This check reports that the output clock pin has no clock because the master clock on the GT input was never defined. Downstream logic clocked by the GT outputs is **not timed**.

### Full Message Description property.

```
The output clock pin <NETLIST_ELEMENT> does not have clock defined. Create a primary clock on the <NETLIST_ELEMENT> input port in order to let Vivado auto-derive the missing GT clocks
```
**Example:**
```
The output clock pin gt_wiz_0/inst/gen_gtwizard_gthe4_top.gtwizard_gthe4_inst/gen_gtwizard_gthe4.gen_channel_container[0].gen_enabled_channel.gthe4_channel_wrapper_inst/channel_inst/gthe4_channel_gen.gen_gthe4_channel_inst[0].GTHE4_CHANNEL_PRIM_INST/TXOUTCLK does not have clock defined. Create a primary clock on the gtrefclk_p input port in order to let Vivado auto-derive the missing GT clocks
```

### Explanation
* The first `<NETLIST_ELEMENT>` is the GT output clock pin (for example `TXOUTCLK`, `RXOUTCLK`, `TXOUTCLKPCS`) that has no clock.
* The second `<NETLIST_ELEMENT>` is the **input port Vivado recommends constraining** (normally the GT reference clock port feeding `GTREFCLK*` through an `IBUFDS_GTE*`).
* Without a primary clock there, no clock propagates through the GT, so every register on a GT output clock is unconstrained.

**DO**
✅  Confirm the recommended port really is a clock input (IBUFDS_GTE* reference clock, or a fabric clock feeding the GT `*CLK` inputs)
✅  Take the period from the IP/transceiver configuration or the board clock source, not from the GT output pin
✅  Define the clock **once** on the port; let Vivado auto-derive all GT output clocks

**DO NOT**
❌ Create a clock directly on the GT output pin (this is what auto-derivation replaces and will raise other TIMING checks)
❌ Guess the reference clock frequency
❌ Trace through GT* blocks with the clock tracing utility (see `CLOCK_TRACING.md`)

## Generate Data

1. Identify the GT output pin and the recommended port from the message (`<NETLIST_ELEMENT>` values).
2. Check whether a clock exists on the port and on the GT input pins:
```tcl
get_clocks -quiet -of_objects [get_ports <port_name>]
get_clocks -quiet -of_objects [get_pins -of_objects [get_cells <gt_cell>] -filter {DIRECTION==IN && IS_CLOCK}]
```
3. Check whether the port is constrained in a different (possibly scoped/IP) XDC: search `constraints.xdc` for `<port_name>` and for the IP `current_instance` scope (see `IP_CONSTRAINTS.md`).
4. Get the GT reference clock settings (IP `CONFIG.*REFCLK*FREQ`) when the GT is IP generated.
5. List all GT output pins with no clock:
```tcl
report_clocks
get_pins -quiet -hier -filter {REF_PIN_NAME =~ *OUTCLK* && IS_CLOCK && DIRECTION==OUT}
```

## Flow

1. **Gather Data** → Identify the GT output pin, the recommended port, existing clocks, and whether the port already has an unscoped/IP constraint.
2. **Analyze** → Determine why the port is unconstrained.
3. **Resolve** → Select a case.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The recommended port has no clock and the reference clock frequency is known (IP configuration, board schematic, or user answer)
* **Assumption**: None. The period is never inferred from the GT pin.

Ask the user for the period if it cannot be read from the IP configuration, then add:
```tcl
create_clock -name gtrefclk -period <period_ns> [get_ports <port_name>]
```
Place the constraint in the top-level XDC (not inside an IP-scoped file).

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: The recommended port is not the real clock source (the GT reference clock is driven from fabric, another GT's `OUTCLK`, or a different port)

Prompt the user:
```
Do you want to:
a) Create the primary clock on the recommended port <port_name>
b) Create the primary clock on the real clock source port (specify it)
c) Leave as is because the GT is not used and create a waiver
```

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: The GT channel is intentionally unused or its output clocks do not drive any logic
* **Explanation**: Offer a waiver `<USER> - <DATE> - <REASON>`
* **Examples**:
```
poldark 01/26/26 "GT channel is instantiated for loopback test only; no logic is clocked by TXOUTCLK"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_clocks` shows auto-derived clocks on the GT output pin(s)
* [ ] No new TIMING-1/2/25 warnings are created on the GT outputs

## Final Report
Print a table showing the added constraint:

| Aspect | Constraint | Period | Clock Definition Object |
|--------|-----------|--------|------------------------|
| **Before** | *(none)* | n/a | n/a |
| **After** | `create_clock -name gtrefclk -period 6.400 [get_ports gtrefclk_p]` | 6.400 ns | gtrefclk_p (port) |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-26
- [TIMING-25](./TIMING-25.md) - Invalid clock waveform on GT (related check)
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
