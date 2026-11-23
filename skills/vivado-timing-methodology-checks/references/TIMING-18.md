<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-18: Missing input or output delay

## Metadata

- **Check ID**: TIMING-18
- **Severity**: Warning

## Description

Missing input or output delay constraint on a port relative to clock edges.

## Message

```
An <MESSAGE_STRING> delay is missing on <NETLIST_ELEMENT> relative to the rising and/or falling clock edge(s) of <CLOCK_GROUP>.
```

**Example:**
```
An input delay is missing on data_in[7] relative to the rising and/or falling clock edge(s) of sys_clk.
```

## Explanation

This check identifies top-level I/O ports that lack proper `set_input_delay` or `set_output_delay` constraints. These constraints are essential for:

- Defining the timing relationship between external devices and the FPGA
- Allowing timing analysis of paths that cross the chip boundary
- Ensuring proper setup and hold times are met at I/O interfaces
- Enabling optimization of I/O path timing during implementation

Without these constraints:
- I/O paths will not be properly analyzed or optimized
- Timing violations may go undetected until hardware testing
- The design may fail to meet system-level timing requirements

Common scenarios requiring input/output delays:
- Synchronous interfaces with external devices
- Data buses synchronized to system clocks
- Control signals with defined timing relationships
- Any I/O that interacts with clocked logic

## Generate Data

Collect information about the unconstrained port:
1. Get port details: `report_property [get_ports <NETLIST_ELEMENT>]`
2. Check existing I/O constraints: `report_timing -from [get_ports <NETLIST_ELEMENT>]` or `report_timing -to [get_ports <NETLIST_ELEMENT>]`
3. List the unconstrained ports: they are named in the TIMING-18 messages (`An input|output delay is missing on <port> relative to ... <clock: <name>>`) in `./methodology_results/methodology.json`; `methodology_check::timing18_summary` counts them per clock. Ports have no `INPUT_DELAY`/`OUTPUT_DELAY` property, so `get_ports -filter` cannot find them. `check_timing -verbose` (`no_input_delay`, `no_output_delay`) is an alternative, but it skips ports that TIMING-18 reports (for example unloaded inputs).
4. List all related clocks: `report_clocks <CLOCK_GROUP>`
5. Check clock interactions: `report_clock_interaction`

### Summary Table

Present the user with a per-clock summary table that captures the clock information,
its period, the clock-tree (driver/root) information, and how many ports are missing an
input delay and missing an output delay for that clock domain. Call the
`methodology_check::timing18_summary` proc (loaded in Step 1); it prints the table and writes it to a report file.

```tcl
# Proc source: <skill_dir>/scripts/run_methodology_setup.tcl (loaded in Step 1, do not source or copy it)
methodology_check::timing18_summary
# or pick the methodology JSON and/or the report path explicitly:
methodology_check::timing18_summary -json ./methodology_results/methodology.json \
    -out ./methodology_results/timing18_summary.rpt
```

The #No InDelay / #No OutDelay counts are read from the TIMING-18 messages in the **methodology JSON
report**, not from the live design. By default the proc uses `./methodology_results/methodology_after_fix.json`
when it exists (after the Step 6 re-run) and otherwise `./methodology_results/methodology.json` (Step 1), and
it prints which file it used. So the counts match the methodology report exactly, waived violations (which
`report_methodology` leaves out of the JSON) are not counted, and no extra `report_methodology` run is needed.
The proc does not query the ports itself (there is no port delay property).
A clock named in the JSON that is no longer in the design (for example a redundant clock removed by a fix)
gets a `<clock> (not in design)` row, and a message that cannot be parsed shows in an `<unparsed message>`
row with a warning. The period, waveform and clock-tree columns still come from the open design.

Example rendered table:

```
| Clock                | Period(ns) | Waveform(r f)        | Clock Tree (driver/root)   | #No InDelay | #No OutDelay |
|----------------------|------------|----------------------|----------------------------|-------------|--------------|
| sys_clk              |      10.00 | {0.000 5.000}        | BUFGCE X2Y1                |           6 |            0 |
| clk_out1_mmcm        |       5.00 | {0.000 2.500}        | BUFGCE X2Y3                |           0 |            4 |
| old_clk (not in design) |       - | -                    | -                          |           2 |            1 |
```

## Flow

1. **Gather Data** → Collect port properties, identify related clocks, and generate the per-clock summary table (`methodology_check::timing18_summary`)
2. **Analyze** → Review the table to see which clock domains have missing input/output delays, and determine external device timing requirements
3. **Resolve** → Add appropriate input/output delay constraints or mark as asynchronous
4. **Verify** → Confirm constraints are applied and timing analysis includes I/O paths (after the single Step 6 `report_methodology` re-run has written `methodology_after_fix.json`, re-run `methodology_check::timing18_summary`; it reads that file by default, and the missing counts should drop to 0 for the resolved ports)

## Example Constraints

When adding the missing constraints, use **XDC variables** for the timing budget and **wildcards** on bus ports so the user only edits a few numbers instead of one line per bit. Set the clock period and I/O budget once as variables, then apply them to whole buses with `[get_ports {bus[*]}]`.

```tcl
# --- Edit these few values only ------------------------------------------------
set clk        [get_clocks sys_clk]
set tclk       [get_property PERIOD $clk]     ;# clock period (ns), read from the design
set in_max     [expr {0.60 * $tclk}]          ;# input  budget: max external delay
set in_min     [expr {0.10 * $tclk}]          ;# input  budget: min external delay
set out_max    [expr {0.60 * $tclk}]          ;# output budget: max external delay
set out_min    [expr {0.10 * $tclk}]          ;# output budget: min external delay
# ------------------------------------------------------------------------------

# Input delays — one line covers the whole bus via the [*] wildcard
set_input_delay  -clock $clk -max $in_max  [get_ports {data_in[*]}]
set_input_delay  -clock $clk -min $in_min  [get_ports {data_in[*]}]

# Output delays — same pattern for output buses
set_output_delay -clock $clk -max $out_max [get_ports {data_out[*]}]
set_output_delay -clock $clk -min $out_min [get_ports {data_out[*]}]

# Scalar control ports can be grouped with a wildcard on a common prefix
set_input_delay  -clock $clk -max $in_max  [get_ports {ctrl_*}]
set_input_delay  -clock $clk -min $in_min  [get_ports {ctrl_*}]
```

To constrain **every** unconstrained port of one clock in one shot, take the port names from that clock's TIMING-18 messages:

```tcl
set clk    sys_clk                        ;# clock name as it appears in the message
set tclk   [get_property PERIOD [get_clocks $clk]]
set imax [expr {0.60 * $tclk}]; set imin [expr {0.10 * $tclk}]
set omax [expr {0.60 * $tclk}]; set omin [expr {0.10 * $tclk}]

# Ports named in the TIMING-18 messages for this clock (kind is "input" or "output")
proc timing18_ports {kind clk} {
    set fh [open ./methodology_results/methodology.json r]; set txt [read $fh]; close $fh
    set ports {}
    # Match only the fixed text, capture the port and the clock name, then compare the
    # clock name as a plain string (it can contain "/" written as "\/" in JSON, or "+", "[", ".").
    set re "An $kind delay is missing on (\\S+) relative to the rising and\\\\?/or falling clock edge\\(s\\) of <clock: (\[^>\]+)>"
    foreach {all port name} [regexp -all -inline $re $txt] {
        # Undo the JSON escaping of "/" and "\" before comparing or calling get_ports
        set port [string map {"\\/" "/" "\\\\" "\\"} $port]
        set name [string map {"\\/" "/" "\\\\" "\\"} $name]
        if {$name eq $clk} { lappend ports $port }
    }
    return $ports
}
set in_ports  [timing18_ports input  $clk]
set out_ports [timing18_ports output $clk]
if {[llength $in_ports]} {
    set_input_delay  -clock $clk -max $imax [get_ports $in_ports]
    set_input_delay  -clock $clk -min $imin [get_ports $in_ports]
}
if {[llength $out_ports]} {
    set_output_delay -clock $clk -max $omax [get_ports $out_ports]
    set_output_delay -clock $clk -min $omin [get_ports $out_ports]
}
```

> **Note:** The 60%/10% split is a placeholder budget so paths are analyzed — replace the factors (or `in_max`/`out_max` values) with real numbers from the external device datasheet or interface specification. If a port is genuinely asynchronous, use `set_false_path` instead of an I/O delay.

## Verification

* [ ] Port has appropriate input_delay or output_delay constraint applied
* [ ] I/O paths appear in timing reports
* [ ] Setup and hold requirements are analyzed
* [ ] No new unresolved methodology warnings are created
* [ ] Asynchronous paths are properly identified with false_path constraints if applicable
* [ ] The `methodology_check::timing18_summary` table shows 0 ports missing input/output delay for the resolved clock domains

## Final Report Guidance

Document the resolution:
- Include the per-clock `methodology_check::timing18_summary` table (clock, period, clock tree, #ports missing input delay, #ports missing output delay) as the summary of the finding
- If I/O delays were added: Include the specific XDC constraints with values and justification
- If marked as asynchronous: Document why timing doesn't apply and include false_path constraint
- Reference external device datasheets or interface specifications
- List all affected ports and their constraints
- Note any assumptions made about external timing

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) - Using Constraints  
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology
