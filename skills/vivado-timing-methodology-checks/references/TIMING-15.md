<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-15: Large hold violation

## Metadata

- **Check ID**: TIMING-15
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

A large clock skew between the source and destination registers of a path is producing a large hold timing violation. Large hold violations are difficult to close because the router must add datapath delay to fix them, and doing so during routing consumes routing resources and can degrade the setup slack on the same path — making overall timing closure harder.

### Full Message Description Property

```
There is a large clock skew of <MESSAGE_STRING> ns between <NETLIST_ELEMENT> (clocked by
<CLOCK_GROUP>) and <NETLIST_ELEMENT> (clocked by <CLOCK_GROUP>) that results in large hold timing
violation(s) of <MESSAGE_STRING> ns. Fixing large hold violations during routing might impact
setup slack and result in more difficult timing closure
```

**Example:**
```
There is a large clock skew of 1.253 ns between reg_a_reg (clocked by clk_a) and reg_b_reg
(clocked by clk_b) that results in large hold timing violation(s) of 0.874 ns...
```

### Explanation

Hold slack (simplified) is:

```
Hold slack = datapath delay(min) + hold requirement − clock skew − clock uncertainty − hold/removal time
Clock skew = destination clock delay − source clock delay   (measured after the common node)
```

A large clock skew directly eats into the hold margin. Skew becomes large when:

- ❌ The source and destination clocks do **not** share a common node (unbalanced or separate clock trees)
- ❌ One or both clocks use **local (fabric) routing** instead of dedicated global clock routing
- ❌ One or both clocks are **gated** (e.g. a LUT/BUFGCE on the clock, cf. TIMING-14)
- ❌ The two clocks come from **different sources** (e.g. an MMCM/PLL/DCM drives one clock but not the other) yet paths between them are still being timed
- ❌ The two clocks are, in reality, **asynchronous/unrelated** but no clock-group/false-path exception is set

There are two fundamentally different situations behind this check:

1. **The two clocks are not actually related** (asynchronous, or intentionally treated as unrelated). The path should not be timed at all — the correct fix is a timing exception, not a placement/routing change.
2. **The two clocks are synchronous/related** and the path is real. The skew must be reduced (balance the clock trees, remove gating/local routing) and, if a genuine large hold violation remains, it should be fixed **before** routing.

**DO**
✅ Confirm whether the two clocks are truly related (synchronous) before doing anything else
✅ For unrelated clocks, apply the correct exception (`set_clock_groups` / `set_false_path` / `set_max_delay -datapath_only`)
✅ For related clocks, reduce clock skew by balancing the clock trees and removing gating / local routing
✅ Fix any genuine large hold violation **before** routing (via `phys_opt_design`)

**DO NOT**
❌ Rely on the router to absorb a large hold violation — it costs setup slack and routing resources
❌ Add hold-fixing buffers to a path whose clocks should have been declared unrelated

## Generate Data

1. Identify the endpoints and clocks reported, and inspect the failing hold path.
```tcl
# Worst hold paths between the two clocks (adjust -from/-to/-group as needed)
report_timing -delay_type min -max_paths 10 -sort_by slack \
    -file ./methodology_results/timing15_hold_paths.rpt
```

2. Quantify the clock skew and clock relationship on the failing path.
```tcl
# The Source/Destination Clock Path and "clock pessimism"/skew rows show the common node
report_timing -delay_type min -from <src_reg> -to <dst_reg> \
    -file ./methodology_results/timing15_skew.rpt
# Are the two clocks defined as related?
report_clocks
report_clock_interaction -delay_type min \
    -file ./methodology_results/timing15_clock_interaction.rpt
```

3. Check whether the clocks share a common node / use dedicated clock routing.
```tcl
report_clock_networks -file ./methodology_results/timing15_clock_networks.rpt
# Look for local (fabric) routing or gating on either clock (see also TIMING-14)
```

## Flow

1. Determine whether the two clocks (`<CLOCK_GROUP>` source and destination) are **related/synchronous** or **unrelated/asynchronous**.

2. Select the resolution.

**CASE 1: The clocks are unrelated — declare the exception**
* **Type**: Auto Resolution
* **When**: The source and destination clocks are asynchronous or should not be timed together (no valid phase relationship, no common primary clock)

The path should not be timed for hold at all. Declare the correct timing exception so the tool stops trying to close a meaningless hold path:

- Use `set_clock_groups -asynchronous` for clocks that are fully unrelated.
- Use `set_false_path` for individual paths that must not be timed.
- Use `set_max_delay -datapath_only` when the transfer is handled by a synchronizer/CDC circuit and only the datapath delay (not the skew) needs bounding.

```tcl
# Mark clocks as asynchronous
set_clock_groups -asynchronous \
    -group [get_clocks <clk1>] \
    -group [get_clocks <clk2>]
```

Provide alternative `set_max_delay -datapath_only` constraints
```tcl
# datapath_only constraints
set_max_delay -datapath_only -from [get_clocks <clk1>] -to [get_clocks <clk2>]
set_max_delay -datapath_only -from [get_clocks <clk2>] -to [get_clocks <clk1>]
```

**DO:**
✅ Document why these clocks are asynchronous
✅ Flag a warning that this will hide timing failures between related clocks from the user. 
✅ When setting `set_clock_groups` recommend that a user runs `report_cdc` after to verify cross clock paths
✅ Use `report_clock_interaction` to capture how many paths will now no longer be timed between the specfied clocks. Capture both directions and add it, break it out for setup and hold.

**CASE 2: The clocks are related — reduce skew, then fix hold before routing**
* **Type**: User Resolution
* **When**: The clocks are synchronous/related and the path is real

First **correct the clocking topology**, then reduce the remaining clock skew, and only then fix any genuine hold violation prior to routing. Getting the topology right is the single most important step — an incorrect topology is the root cause of most large inter-clock skew, and no amount of hold fixing will compensate for it.

1. **Correct the clocking topology (do this first — it is important).** The two clocks in the reported path should be generated so their common node is as close as possible to the leaf clock pins. Ask the user to implement the optimal topology for their device and clock relationship:

   - **Prefer a single Multi-Clock Buffer (MBUFG) where possible (Versal).** When the source and destination clocks are integer power-of-2 divides (÷1, ÷2, ÷4, ÷8) of a common clock, replace the parallel clock buffers with a single MBUFG (`MBUFGCE` / `MBUFGCE_DIV`, or `MBUFG_GT` for GT clocks). The MBUFG routes one global clock and performs the division at the leaf `BUFDIV_LEAF` level, so the common node is right at the loads — this minimizes skew for synchronous CDC paths and saves global routing. Requirements: phase shift 0°, 50% duty cycle, and each buffer driving > 50 loads (opt_design can also convert existing parallel BUFGs to MBUFG in some cases, or set `MBUFG_GROUP` on the buffer output net segments).

   - **Use a single MMCM when the two clocks can come from one MMCM but are currently generated by two.** If the design currently uses two separate MMCMs/PLLs (or two separate primary sources) to produce clocks that are actually related, consolidate them so both clocks are derived from a **single** clock modifying block. Clocks that do not share a common source cannot share a common node and will always exhibit large skew.

   - **Use `BUFGCE_DIV` when MBUFG is not available and the clocks need dividing.** On UltraScale/UltraScale+ (no MBUFG), when you need a clock and a divided version of it, generate the divided clock with a `BUFGCE_DIV` (using the `BUFGCE_DIVIDE` property) driven from a **single** MMCM output — instead of using two separate MMCM outputs. This removes the extra clock uncertainty between two MMCM outputs. When parallel `BUFGCE_DIV` buffers with `BUFGCE_DIVIDE > 1` are used, they must share common `CE`/`CLR` control signals (see TIMING-49) to avoid phase ambiguity.

   - **Balance parallel buffers that must remain (CLOCK_DELAY_GROUP).** When parallel clock buffers cannot be merged, apply `CLOCK_DELAY_GROUP` on the driver net segments so placement/routing balances the two clock networks and matches their roots. The buffers must be driven by the same cell for the constraint to be honored. (`report_qor_suggestions` RQS_CLOCK-1 applies this automatically.)

   Consult UG949 (Using Synchronous Clocks / OSERDESE3 Clocking) and UG1388/UG1387 (Improving Skew / MBUFG Primitives) for the recommended topology figures and pick the optimal one for the design. **These topology changes are important — ask the user to implement them.**

2. **Balance the clock trees.** After the topology is corrected, ensure both clocks still share a common node and are distributed on dedicated global clock routing (BUFG family), not local/fabric routing. Remove any clock gating on the clock net (see [TIMING-14](./TIMING-14.md)) and avoid mixed sources driving one clock but not the other.
3. **Review placement.** Large physical separation between the source and destination registers (or the clock buffers) can create skew; review placement constraints so related registers and their clock roots are placed to minimize skew. If a synchronous path crosses a resource column or clock region, use a Pblock to confine the source and destination.


**DO**
✅ Correct the clocking topology first — MBUFG where possible, a single MMCM when the related clocks were split across two, `BUFGCE_DIV` (from one MMCM output) when MBUFG is unavailable and division is needed
✅ Ask the user to implement the topology changes — they are the root-cause fix and are important
✅ Use `CLOCK_DELAY_GROUP` to balance any parallel buffers that must remain
✅ Balance the clock trees and remove gating / local routing (skew is the root cause)
✅ Review placement of the source/destination registers and clock roots
✅ Fix the remaining large hold violation **before** routing with `phys_opt_design`
✅ Confirm setup slack is not degraded after hold fixing

**DO NOT**
❌ Fix hold before correcting the clocking topology and verifying minimal skew — you would be masking the real problem
❌ Generate related clocks from two separate MMCMs/outputs when one MMCM (with MBUFG / BUFGCE_DIV) would give a common node
❌ Defer a large (> ~0.4 ns) hold violation to the router

## Additional Checks

- Cross-check for a LUT / gating element on either clock ([TIMING-14](./TIMING-14.md)) — gating is a common skew source.
- Verify both clocks are distributed on dedicated global clock routing (BUFG/BUFGCE), not local routing.
- Confirm the two clocks share a common node in the clock network; if they cannot, they are likely unrelated (CASE 1).
- Check whether the two related clocks can be produced from a single MMCM/CMB with an MBUFG (Versal) or `BUFGCE_DIV` (UltraScale/UltraScale+) instead of two separate outputs/MMCMs.
- For parallel `BUFGCE_DIV` with `BUFGCE_DIVIDE > 1`, confirm common `CE`/`CLR` control signals (TIMING-49).
- Run `report_qor_suggestions` — it can automatically apply `CLOCK_DELAY_GROUP` to balance parallel synchronous clock buffers.

## Verification

```tcl
# Re-check hold and methodology after the fix
report_timing -delay_type min -max_paths 10 -sort_by slack \
    -file ./tmp/timing15_hold_after_fix.rpt
report_methodology -checks {TIMING-15} -file ./tmp/methodology_timing15_after_fix.rpt
```

* [ ] The two clocks are correctly declared (related, or unrelated via a clock group / false path)
* [ ] The clocking topology is optimal (MBUFG / single MMCM / `BUFGCE_DIV` as appropriate) so related clocks share a common node
* [ ] Clock skew on the reported path is minimized (clocks balanced, on dedicated clock routing, no gating)
* [ ] Any remaining large hold violation is fixed before routing, without degrading setup slack
* [ ] TIMING-15 violation is no longer present after re-running `report_methodology`

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) — TIMING-15 / Large hold violation; TIMING-49 (BUFGCE_DIV common control)
- UltraFast Design Methodology Guide (UG949) — Using Synchronous Clocks (common node); OSERDESE3 Clocking (single MMCM output + `BUFGCE_DIV`)
- Versal ACAP / UltraScale Design Methodology (UG1388 / UG1292) — Improving Skew in Versal Devices; Fixing Large Hold Violations Prior to Routing; Resolving Hold Violations
- Versal Hardware, IP, and Platform Development Methodology Guide (UG1387) — MBUFG Primitives (leaf-level division, single global route)
- Vivado Design Suite User Guide: Implementation (UG904) — MBUFG Optimization (`-mbufg_opt`, `MBUFG_GROUP`)
- Vivado Design Suite User Guide: Using Constraints (UG903) — `set_clock_groups`, `set_false_path`, `set_max_delay -datapath_only`, `CLOCK_DELAY_GROUP`
- [Clock Tracing](./CLOCK_TRACING.md)
- [TIMING-14](./TIMING-14.md) — LUT on the clock tree (a common skew source)

## Examples

*To be documented with real-world examples from a TIMING-15 testcase as required.*
