<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-13: Timing paths ignored due to path segmentation

## Metadata

- **Check ID**: TIMING-13
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

A `set_max_delay` or `set_min_delay` constraint specifies an **invalid** startpoint (via `-from`) or endpoint (via `-to`), which forces the timing engine to break the timing arcs going through that pin so the pin becomes a valid start/endpoint. This is called **path segmentation**. As a result, some timing paths are no longer reported/analyzed as intended, clock skew computation on the segmented path is broken, and other paths passing through the same node can also be affected — leading to inaccurate timing sign-off and possible hardware failures.

### Full Message Description Property

```
Some timing paths are not reported due to path segmentation on pin(s) <NETLIST_ELEMENT>. To
prevent path segmentation, all the Min and Max delay constraints should be defined with a list of
valid startpoints and endpoints
```

**Example:**
```
Some timing paths are not reported due to path segmentation on pin(s) FD1/Q. To prevent path
segmentation, all the Min and Max delay constraints should be defined with a list of valid
startpoints and endpoints
```

### Explanation

Only `set_max_delay` and `set_min_delay` accept invalid start/endpoints in their `-from`/`-to` lists. When an invalid pin is supplied, the timing engine stops timing propagation through that node to promote it to a valid start/endpoint. Valid start/endpoints are:

- **Valid startpoints**: a clock, a clock pin, a sequential cell (implies its valid startpoint pins), or an input/inout port
- **Valid endpoints**: a clock, a non-clock input pin of a sequential cell, a sequential cell (implies its valid endpoint pins), or an output/inout port

Anything else (for example a register `Q` output pin, or an internal `LUT/O` pin) is invalid and triggers segmentation.

- ❌ Breaks clock skew computation on the segmented path (no launch clock insertion delay), producing large/incorrect skew
- ❌ Removes the default setup **or** hold analysis on the segmented path
- ❌ Can break **more** paths than the one being constrained (every path through the node)
- ❌ Results in inaccurate timing sign-off and possible hardware failures

A common intent behind segmentation is to exclude clock skew by targeting a register `Q` pin. That intent is better expressed with `-datapath_only` from the register **clock** pin.

**DO**
✅ Identify the exact `set_max_delay`/`set_min_delay` constraint and the segmented pin(s) reported
✅ Determine what the user intended to constrain (a datapath bound? a real CDC?)
✅ Replace invalid start/endpoints with valid ones (clock pin, data pin, port, sequential cell)
✅ Prefer `-datapath_only` from the clock pin when the goal was to exclude clock skew

**DO NOT**
❌ Leave the constraint pointing at an invalid pin (`Q`, `LUT/O`, internal net, etc.)
❌ Assume hold is still covered after a `set_max_delay` segmentation (it is not) — add `set_min_delay` if hold is required
❌ Modify a constraint sourced from an IP without checking the IP source

## Generate Data

1. Identify the segmented pin(s) and the offending exception
```tcl
# Show the min/max delay exceptions that touch the reported pin(s)
report_exceptions -from [get_pins <segmented_pin>] -file ./tmp/timing13_exceptions.rpt
report_exceptions -to   [get_pins <segmented_pin>] -file ./tmp/timing13_exceptions.rpt -append
```

2. Confirm whether the reported pin is a valid startpoint/endpoint
```tcl
# Inspect the pin: is it a clock pin / data pin / port, or an invalid pin (Q, LUT/O, ...)?
report_property [get_pins <segmented_pin>]
get_cells -of_objects [get_pins <segmented_pin>]
```

## Flow

1. Search `./tmp/constraints.xdc` for the `set_max_delay` / `set_min_delay` constraint that references the reported segmented pin(s).

2. Classify each `-from` / `-to` object as a **valid** or **invalid** start/endpoint using the generated data.

**DO NOT**
❌ Automate a fix when the constraint file name is null
❌ Automate a fix when the line number is null
❌ Automate a fix when the reported pin does not match the constraint

3. Select the resolution.

**CASE 1: Intent was to exclude clock skew — use `-datapath_only` from the clock pin**
* **Type**: Automated Resolution
* **When**: The invalid startpoint is a register output (e.g. `FD1/Q`) targeted to drop launch clock skew. Different clocks on the source and destination cells. The two clocks are not covered by a `set_false_path` or `set_clock_groups` constraint.

Rewrite the constraint so it starts at the register **clock** pin and adds `-datapath_only`, which excludes clock skew without segmenting the path.
```tcl
# BEFORE (segments the path at FD1/Q):
set_max_delay 5 -from [get_pins FD1/Q] -to [get_pins REGB/D]

# AFTER (no segmentation; clock skew excluded via -datapath_only):
set_max_delay 5 -from [get_pins FD1/C] -to [get_pins REGB/D] -datapath_only
```

**CASE 2: Intent was to set a max delay between two registers**
* **Type**: Automated Resolution
* **When**: The same clock on both the source and the destination cells when the path segmentation is removed.

Rewrite the constraint so it starts at the register **clock** pin, removing path segmentation but keeping the max delay value
```tcl
# BEFORE (segments the path at FD1/Q):
set_max_delay 5 -from [get_pins FD1/Q] -to [get_pins REGB/D]

# AFTER (no segmentation):
set_max_delay 5 -from [get_pins FD1/C] -to [get_pins REGB/D] 
```

**CASE 3: Invalid pin used unintentionally — replace with through**
* **Type**: User Resolution
* **When**: The `-from`/`-to` list references invalid pins (register `Q`, internal `LUT/O`, net, etc.) and the goal was simply to bound the path delay

Replace the invalid objects with valid startpoints/endpoints (clock pin, sequential cell, data pin, or port) so the constraint covers the intended path without segmentation.
```tcl
# BEFORE (LUTA/O is not a valid startpoint -> segmentation):
set_max_delay 6 -from [get_pins LUTA/O] -to [get_pins REGB/D]

# AFTER (start at the driving register's clock pin -> valid startpoint):
# Propose Option 1:
set_max_delay 6 -through [get_pins LUTA/O] -to [get_pins REGB/D]

```
If a `set_max_delay` previously segmented the path, its default hold analysis was lost — add a matching `set_min_delay` (or vice-versa) if that analysis is required.

**CASE 4: Segmentation is intentional (expert use) — document and complete the analysis**
* **Type**: User Resolution
* **When**: The designer deliberately segmented the path and understands the consequences

Path segmentation is an advanced technique and may be intended. In that case, keep the constraint but:
1. Confirm the complementary analysis is covered — after a `set_max_delay` segmentation the default hold requirement is gone, so add `set_min_delay` for the same path if hold matters (and the reverse for `set_min_delay`).
2. Document why segmentation is required so the warning is understood to be an accepted exception.
3. Ask the user to generate a waiver

**DO**
✅ Ensure both setup (`set_max_delay`) and hold (`set_min_delay`) are constrained if both matter
✅ Record the justification for the intentional segmentation

**DO NOT**
❌ Silently accept segmentation without confirming the complementary (setup/hold) analysis

## Additional Checks

- Verify whether the constraint originates from an IP before modifying — see [IP Constraints](./IP_CONSTRAINTS.md)
- Use `report_exceptions` to confirm no *other* paths through the segmented node were unintentionally broken
- After a `set_max_delay` segmentation, confirm hold coverage (and vice-versa for `set_min_delay`)

## Verification

* [ ] TIMING-13 violation is no longer present
* [ ] The `-from`/`-to` lists use only valid startpoints/endpoints
* [ ] No new unresolved methodology warnings created

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) — TIMING-13 / Path Segmentation
- Vivado Design Suite User Guide: Using Constraints (UG903) — Path Segmentation; `set_max_delay`, `set_min_delay`, `-datapath_only`
- UltraFast Design Methodology Guide (UG949) — Avoiding Path Segmentation; Valid Startpoints and Endpoints
- [IP Constraints](./IP_CONSTRAINTS.md)

## Examples

*To be documented with real-world examples from a TIMING-13 testcase as required.*
