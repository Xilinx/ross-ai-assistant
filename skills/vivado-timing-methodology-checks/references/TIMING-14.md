<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-14: LUT on the clock tree

## Metadata

- **Check ID**: TIMING-14
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

A LUT has been found on the clock tree. A LUT on a clock path forces the clock to be routed through general fabric routing resources instead of dedicated clock routing causing excess clock skew. It is typically introduced during synthesis for clock gating or clock inversion.

### Full Message Description Property

```
The LUT <NETLIST_ELEMENT> has been found on the clock tree. Run opt_design to optimize the clock
trees, then re-run report_methodology. If LUT(s) are still present in the clock trees, modify the
RTL or the synthesis options accordingly to move the clock gating logic to dedicated clock enable
resources. Keep in mind that post-opt_design database cannot be used for pin-planning tasks
```

**Example:**
```
The LUT clk_inv_i has been found on the clock tree. Run opt_design to optimize the clock trees,
then re-run report_methodology...
```

### Explanation

A LUT on the clock path:

- ❌ Routes the clock through general (fabric) routing instead of dedicated clock routing
- ❌ Adds excess clock skew and PVT sensitivity
- ❌ Is undesirable for reliable timing closure and hardware behavior

There are two very different situations behind this check:

1. **A single-input inversion `LUT1`.** An inversion `LUT1` on the clock is usually absorbed into the downstream SLICE/register by `opt_design` (the inversion is implemented by the register's built-in clock inversion). Once absorbed, there is no LUT on the clock tree and nothing to fix — **unless** a `DONT_TOUCH` (or similar) property on the connecting net segments or hierarchical pins prevents the optimization or a downstream object is missing an optional inverter.
2. **Any other LUT** (a real clock-gating / logic `LUT`). This must be fixed in the source: the gating logic should be moved so it feeds the clock buffer input rather than sitting on the clock net, and the clock should be driven through a global clock buffer (BUFG). Dedicated clock-enable resources (BUFGCE, register CE pins) should be used instead of gating the clock with a LUT.

**DO**
✅ Identify the LUT cell reported and how many inputs it has (`LUT1` vs. other)
✅ For a `LUT1`, check for `DONT_TOUCH` on the surrounding net segments / hierarchical pins
✅ For other LUTs, guide the user to move the LUT ahead of the BUFG in the RTL

**DO NOT**
❌ Attempt to auto-edit the netlist to move a clock-gating LUT (this is an RTL fix)
❌ Ignore a LUT on the clock tree that is not a cleanly-absorbable `LUT1`

## Generate Data

1. Identify the LUT and its INIT
```tcl
set lut [get_cells <reported_lut>]
puts "LUT: [get_property REF_NAME $lut]"   ;# e.g. LUT1, LUT2, ...
puts "LUT: [get_property INIT $lut]"   ;# e.g. 2'h1, LUT2, ...
```

2. For a `LUT1` with INIT = 2'h1, check whether anything blocks its absorption by `opt_design`.
   Use the helper script to scan every net segment and hierarchical pin the LUT's
   nets pass through for a `DONT_TOUCH`. It returns `1` if optimization is blocked
   (a `DONT_TOUCH` is present) or `0` if it is clear (safe to ignore).
The proc is `methodology_check::check_dont_touch_on_net`; it is already loaded by Step 1
(`run_methodology_setup.tcl`), so it is not sourced here.
```tcl
set blocked [methodology_check::check_dont_touch_on_net -from [get_cells <reported_lut>] \
                 -json ./methodology_results/timing14_dont_touch.json]
# blocked == 0  -> no DONT_TOUCH on net segments/hierarchical pins -> CASE 1 (ignore)
# blocked == 1  -> DONT_TOUCH blocks absorption -> do not ignore
```

3. Determine whether `opt_design` has **already** been run. The absorption of an
   inversion `LUT1` only happens during `opt_design`; if it has already run and the
   LUT is still on the clock tree, the LUT will **not** be absorbed by simply
   re-running it. Step 1's `run_methodology_setup` already captured the Tool Option
   Summary to `./methodology_results/tmp/command_history.rpt`; check it for an
   `opt_design` row with a regexp instead of eyeballing the table:
```tcl
set fh [open ./methodology_results/tmp/command_history.rpt r]
set command_history [read $fh]
close $fh
set opt_design_already_run [regexp -line -nocase {^\|\s*opt_design\s*\|} $command_history]
# opt_design_already_run == 1 -> an `opt_design` row is present -> opt_design has already run
# opt_design_already_run == 0 -> no `opt_design` row -> it has not run yet
```

## Flow

1. Determine the LUT type (`REF_NAME`) of the reported cell.

2. Select the resolution.

**CASE 1: Inversion `LUT1` with no DONT_TOUCH — ignore**
* **Type**: Automated Resolution
* **When**: The reported cell is a `LUT1`, there are **no** `DONT_TOUCH` (or equivalent) properties on the connected net segments or hierarchical pins, **and** `opt_design` has **not** yet been run

`opt_design` will absorb the inversion `LUT1` into the downstream SLICE, removing it from the clock tree. Since nothing blocks that optimization, this violation can be safely **ignored** — no fix is required.

> **Note:** If `opt_design_already_run == 1` (see Generate Data step 3) and the `LUT1` is
> still on the clock tree, it was **not** absorbed. Do not ignore it — treat it as CASE 2 (RTL fix).

**DO**
✅ Confirm the cell is a `LUT1` with INIT=2'h1
✅ Confirm no `DONT_TOUCH` on the surrounding net segments or hierarchical pins
✅ Confirm `opt_design` has not already run (`opt_design_already_run == 0`) — if it has and the LUT remains, go to CASE 2
✅ Mark the check as ignored (opt_design will absorb the LUT1)

**DO NOT**
❌ Ignore a `LUT1` when a `DONT_TOUCH` on the nets/hierarchical pins would prevent absorption
❌ Ignore a `LUT1` that survived a prior `opt_design` run

**CASE 2: Any other LUT — fix the RTL (put the LUT in front of a BUFG)**
* **Type**: User Resolution
* **When**: The reported cell is not an absorbable inversion `LUT1` (i.e. a real clock-gating/logic LUT on the clock net)

The LUT must not sit on the clock net. Recommend the user modify the RTL so the LUT (the clock-gating/combinational logic) feeds the **input** of a global clock buffer, and the clock is then distributed through the BUFG on dedicated clock routing:

- Move the gating/combinational logic **in front of** the `BUFG` so its output drives the BUFG input, and the BUFG output drives the clock tree.
- Prefer dedicated clock-enable resources — use `BUFGCE` or the flip-flop clock-enable (`CE`) pins — instead of gating the clock with a LUT.

```
BEFORE (LUT on the clock tree):
   clk ---> [ LUT (gate) ] ---> clock net ---> FF/CLK   (routed on fabric, excess skew)

AFTER (LUT ahead of the BUFG):
   clk ---> [ LUT (gate) ] ---> BUFG ---> clock net ---> FF/CLK   (dedicated clock routing)
   (or use BUFGCE / FF CE pins for clock enables)
```

**DO**
✅ Recommend moving the LUT ahead of the `BUFG` input in the RTL
✅ Recommend `BUFGCE` / register `CE` pins for clock enables instead of a clock-net LUT

**DO NOT**
❌ Attempt an automated netlist edit — this requires an RTL/source change by the user

## Additional Checks

- Run `opt_design` and re-run `report_methodology` — if the LUT is gone, no further action is needed (CASE 1)
- Note: the post-`opt_design` database cannot be used for pin-planning tasks

## Verification

```tcl
# Optimize clock trees and re-check
opt_design
report_methodology -checks {TIMING-14} -file ./tmp/methodology_timing14_after_fix.rpt
```

* [ ] TIMING-14 violation is no longer present after `opt_design` (CASE 1), or the RTL has been updated to move the LUT ahead of the BUFG (CASE 2)
* [ ] No LUT remains on the clock tree
* [ ] Clock is distributed through a global clock buffer on dedicated clock routing

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) — TIMING-14 / LUT on the Clock Tree
- UltraFast Design Methodology Guide (UG949) — Clocking resources; avoid local clocks
- [IP Constraints](./IP_CONSTRAINTS.md)
- `report_design_analysis -qor_summary` — Tool Option Summary command captured to
  `./methodology_results/tmp/command_history.rpt` by `run_methodology_setup.tcl` (Step 1)

## Examples

*To be documented with real-world examples from a TIMING-14 testcase as required.*
