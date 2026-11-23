<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-21: Invalid COMPENSATION property on MMCM

## Metadata

- **Check ID**: TIMING-21
- **Severity**: Warning

## Description

An MMCM's `COMPENSATION` property does not match how its feedback loop is connected. External feedback (leaving the FPGA) requires `EXTERNAL`; internal feedback requires `ZHOLD` (or `INTERNAL`/`BUF_IN`).

## Message

```
The MMCM <NETLIST_ELEMENT> has an invalid COMPENSATION property value relative to the connection of its feedback loop. If the feedback loop goes outside the FPGA the property should be set to EXTERNAL. If the feedback loop is internal to the FPGA the property should be set to ZHOLD
```

### Explanation
* The check has already decided that the property and the loop disagree, so the loop does not need to be classified again. The current property tells you which way it disagrees:
  * `COMPENSATION` is `EXTERNAL`: the loop is **internal**. The fix is `ZHOLD`.
  * `COMPENSATION` is anything else: the loop is **external**. The fix is `EXTERNAL`.
* `ZHOLD` compensates the delay from the MMCM input to the registers it clocks, so it is only valid when the MMCM input clock comes from outside the FPGA and the design constrains the arrival of the data on that clock. Check this before setting `ZHOLD` (CASE 1).
* `COMPENSATION` is a property of the netlist cell, not a timing constraint. `set_property` on the cell is accepted after synthesis and clears TIMING-21. The RTL value is not changed, so tell the user to correct the RTL as well.

### COMPENSATION settings by architecture

The legal values were read from Vivado 2026.1 with `list_property_value COMPENSATION <cell>` on each primitive. The `REF_NAME` of the cell in the netlist selects the row. The `_BASE` primitives are `_ADV` cells in the netlist (the 7 series `MMCME2_BASE` reads back as `MMCME2_ADV`).

| Architecture | Primitive (netlist `REF_NAME`) | Legal values | Default | `ZHOLD` legal | `EXTERNAL` legal |
|--------------|-------------------------------|--------------|---------|---------------|------------------|
| 7 series (Artix-7, Kintex-7, Virtex-7, Zynq-7000, Spartan-7) | `MMCME2_ADV` (`MMCME2_BASE`) | `BUF_IN`, `EXTERNAL`, `INTERNAL`, `ZHOLD` | `ZHOLD` (`ADV`), `INTERNAL` (`BASE`) | Yes | Yes |
| UltraScale | `MMCME3_ADV` (`MMCME3_BASE`) | `AUTO`, `BUF_IN`, `EXTERNAL`, `INTERNAL`, `ZHOLD` | `AUTO` | Yes | Yes |
| UltraScale+ (including Zynq UltraScale+ MPSoC/RFSoC) | `MMCME4_ADV` (`MMCME4_BASE`) | `AUTO`, `BUF_IN`, `EXTERNAL`, `INTERNAL`, `ZHOLD` | `AUTO` | Yes | Yes |
| Versal | `MMCME5` | `AUTO`, `BUF_IN`, `EXTERNAL`, `INTERNAL` | `AUTO` | **No** | Yes |

What each value means (UG472, UG1387):

| Value | Meaning |
|-------|---------|
| `ZHOLD` | The feedback is deskewed to give a negative hold time at the I/O registers. Used when the input clock comes from a pin. 7 series, UltraScale and UltraScale+ only |
| `EXTERNAL` | A network outside the FPGA is compensated (feedback leaves the FPGA). The board delay comes from `set_external_delay` between the FBOUT and FBIN ports (TIMING-22) |
| `INTERNAL` | The MMCM uses its own internal feedback. No delay is compensated and no feedback buffer is needed |
| `BUF_IN` | None of the other modes apply. Used when the input clock comes from a BUFG/BUFH/BUFR or a GT. Only the feedback clock buffer is compensated |
| `AUTO` | The tool selects the mode from the circuit topology. UltraScale and later only |

Notes:
* The library guides list `COMPENSATION` as informational and recommend leaving it at the default so the tool chooses the mode.
* **Versal has no `ZHOLD` value.** The ZHOLD feature of the DPLL is a separate property, and XPLLs and DPLLs have no `COMPENSATION` property. The ZHOLD fix in CASE 1 and CASE 3 cannot be used on `MMCME5`. See the Versal note in the Flow.
* The message text of the check always says `ZHOLD` for an internal loop. That text is architecture independent.

**DO**
✅ Take the direction of the fix from the current `COMPENSATION` value
✅ Check the MMCM input clock source and the `set_input_delay` constraints before setting `ZHOLD`
✅ Write the property to `TIMING-21.xdc` (see the Flow)

**DO NOT**
❌ Edit the RTL
❌ Set `ZHOLD` when the MMCM input clock comes from another MMCM, a GT or any other internal source (CASE 3)
❌ Set `ZHOLD` when no `set_input_delay` is related to the input clock (CASE 3)

## Generate Data

1. Read the property and the MMCM clock inputs:
```tcl
set mmcm [get_cells <NETLIST_ELEMENT>]
set comp [get_property COMPENSATION $mmcm]
set ref  [get_property REF_NAME $mmcm]   ;# MMCME2_ADV, MMCME3_ADV, MMCME4_ADV or MMCME5 (see the architecture table)
set legal [list_property_value COMPENSATION $mmcm]   ;# the values this cell accepts
# Inputs in use: CLKIN1 / CLKIN2 pins that are driven (not tied to a constant)
foreach p {CLKIN1 CLKIN2} {
    set pin [get_pins $mmcm/$p]
    if {[get_nets -quiet -of_objects $pin] eq ""} { continue }
    set clk  [get_clocks -quiet -of_objects $pin]
    lassign [methodology_check::trace_clock_back_to_source $pin] objs type
    puts "$p clock=$clk source=$objs type=$type"
}
```
`type` is `port` when the trace reaches a top-level port through buffers (`CLOCK_TRACING.md`). It is `pin` when the trace ends at an output pin with no timing arc, for example an MMCM/PLL output or a GT clock output. Ignore an input that is tied to a constant (an unused `CLKIN2`).

2. Find the `set_input_delay` constraints related to each input clock. Use the profile: entries with `type` = `set_input_delay` whose `clock.objects` contain the clock name. If the `clock` expression is not a literal name (it has `of_objects` or a wildcard), evaluate its `text` expression with `get_clocks` and compare the result.

## Flow

1. Read `comp`. Select `ZHOLD` (`comp` is `EXTERNAL`) or `EXTERNAL` (anything else).
2. For `ZHOLD` only: confirm that every driven MMCM clock input has `type` = `port` and has at least one `set_input_delay` related to its clock (Generate Data steps 1 and 2).
3. Select a case.

For a loop that must become `EXTERNAL` select CASE 2 (no further checks are needed). For `ZHOLD` select CASE 1 when step 2 passes, otherwise CASE 3.

**Versal (`REF_NAME` = `MMCME5`):** `ZHOLD` is not in `legal`, so `set_property COMPENSATION ZHOLD` fails. Do not apply CASE 1 as written. Check `legal` before every `set_property`. For an internal loop on `MMCME5` the legal choices are `AUTO` (tool decides), `INTERNAL` and `BUF_IN`; which one to use is a design decision, so treat it as CASE 3 and ask the user. CASE 2 (`EXTERNAL`) is legal on every architecture.

---

**CASE 1: Internal loop, set `ZHOLD` in `TIMING-21.xdc`**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: `COMPENSATION` is `EXTERNAL`, every driven clock input of the MMCM is sourced from a top-level port (`type` = `port`), and each of those clocks has a `set_input_delay` related to it
* **Assumption**: The loop is internal, as the check reports

Write the property to `./methodology_results/TIMING-21.xdc`:
```tcl
set_property COMPENSATION ZHOLD [get_cells <MMCM>]
```
Step 6 reads `TIMING-21.xdc` after `constraints_after_fix.xdc` (see `SKILL.md`, Step 6). Do not add the line to `constraints_after_fix.xdc`.

Report, for the resolution: `before` = `COMPENSATION=EXTERNAL` on `<MMCM>` (the RTL value; `n/a` in the XDC), `after` = the `set_property` line and the path `./methodology_results/TIMING-21.xdc`. State the input clock source port and the `set_input_delay` found. Tell the user the RTL still has the old value.

**CASE 2: External loop, set `EXTERNAL` in `TIMING-21.xdc`**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: `COMPENSATION` is not `EXTERNAL`
* **Assumption**: The loop leaves the FPGA, as the check reports

Write the property to `./methodology_results/TIMING-21.xdc`:
```tcl
set_property COMPENSATION EXTERNAL [get_cells <MMCM>]
```
Report it the same way as CASE 1. Add that an MMCM with external feedback also needs `set_external_delay` between its FBOUT and FBIN ports (TIMING-22, `TIMING-22.md`). If TIMING-22 is reported for the same MMCM, resolve it as well.

**CASE 3: `ZHOLD` is needed but the conditions are not met**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: `COMPENSATION` is `EXTERNAL` and either a driven clock input is not sourced from a top-level port (it comes from another MMCM/PLL, a GT or other logic), or a clock has no related `set_input_delay`
* **Prompt**:
```
The MMCM <MMCM> has COMPENSATION=EXTERNAL but an internal feedback loop, so ZHOLD would normally be set.
<Reason: input clock <clock> comes from <source> and not from a top-level port | no set_input_delay is related to <clock>>.
Do you want to:
a) Set ZHOLD anyway (the RTL value is not changed)
b) Provide the board input delay for <clock> (min/max in ns) and I will add set_input_delay and set ZHOLD   (only when the input clock is sourced from a port)
c) Leave as is and create a waiver
```
On Versal (`MMCME5`) `ZHOLD` is not legal. Replace options a) and b) with: a) set `COMPENSATION` to `AUTO` (the tool selects the mode), b) set it to `INTERNAL`, c) leave as is and create a waiver.

In a non-interactive run there is no recommended option (the input delay cannot be guessed). Take c) and waive. Name the reason in the waiver justification.

## Verification

```tcl
# Step 6 re-run reads TIMING-21.xdc after constraints_after_fix.xdc
get_property COMPENSATION [get_cells <MMCM>]
report_methodology -checks {TIMING-21} -file ./tmp/methodology_timing21_after_fix.rpt
```

* [ ] TIMING-21 violation is no longer present
* [ ] `./methodology_results/TIMING-21.xdc` holds the `set_property` line (CASE 1 and 2)
* [ ] For `ZHOLD`: the input clock source port and the related `set_input_delay` are named in the report
* [ ] TIMING-22 is resolved for the same MMCM when the property became `EXTERNAL`
* [ ] The RTL is not changed and the user is told the RTL value differs

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-21)
- [UG572](https://docs.amd.com/r/en-US/ug572-ultrascale-clocking) - UltraScale Clocking Resources
- [CLOCK_TRACING.md](./CLOCK_TRACING.md) - tracing a clock input to its source
