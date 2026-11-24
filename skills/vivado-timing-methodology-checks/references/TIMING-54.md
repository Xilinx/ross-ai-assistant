<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-54: Scoped false path, clock group or max delay datapath only constraint between clocks

## Metadata

- **Check ID**: TIMING-54
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2020.2

## Description

A `set_false_path`, `set_clock_groups` or `set_max_delay -datapath_only` is defined between clocks inside a scoped (module/IP) XDC, so its effect is global but its author expected a local effect.

### Full Message Description property.

```
A scoped <MESSAGE_STRING> timing constraint is set between clocks <CLOCK_GROUP> and <CLOCK_GROUP> (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). It is not recommended to define such scoped constraints between clocks as the constraint impacts timing paths outside of the scope.
```
**Example:**
```
A scoped set_false_path timing constraint is set between clocks clk_axi and clk_core (see constraint position 12 ...). ...
```
* `<MESSAGE_STRING>` (first) = constraint type: false path, clock group or max delay datapath only

### Explanation
Clocks propagate globally and cannot be limited to a module. An exception written `-from [get_clocks a] -to [get_clocks b]` inside a scoped XDC (written after `current_instance` in `constraints.xdc`) affects every path between `a` and `b` in the whole design, not only the module's paths.

**DO**
✅ Locate the scope with `current_instance` in `constraints.xdc` and check `is_ip` of the constraint (the "constraint position" in the message) in `constraints_profile.json`, or whether the scope is listed in its `ip_instances`, to see whether the scope is IP or user RTL
✅ List the paths outside the scope that are also covered (`report_exceptions`, `report_timing -from/-to` on the clock pair)

**DO NOT**
❌ Edit constraints that belong to IP (see [IP_CONSTRAINTS.md](./IP_CONSTRAINTS.md)). Create a top-level constraint or recommend a netlist change instead
❌ Move the constraint to top level without confirming the global coverage is intended

## Flow
1. Find the constraint in `constraints.xdc`, record the original file, line and the `current_instance` scope.
2. Check how many paths outside the scope it hits.
```tcl
report_exceptions -ignored
report_clock_interaction -clocks [get_clocks {<clk_a> <clk_b>}]
```
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Constraint is user-written and global coverage is the intent
* **Prompt**: Move the constraint to the top-level XDC (outside `current_instance`) and confirm with the user. Show before/after.

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: Constraint is only meant for paths inside the scope
* **Prompt**: Rewrite with explicit pin/cell `-from`/`-to` objects instead of clocks:
```tcl
set_false_path -from [get_cells <scoped_src_cells>] -to [get_cells <scoped_dst_cells>]
```
or ask the user to change the topology.

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Constraint comes from IP or is intentionally scoped
* **Prompt**: Waiver `<USER> - <DATE> - <REASON>`

## Verification
* [ ] Methodology violation is no longer present
* [ ] Paths between the clocks are covered as the user intended
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Constraint | Scope |
|--------|-----------|-------|
| **Before** | `set_false_path -from [get_clocks clk_axi] -to [get_clocks clk_core]` | scoped to `u_blk` |
| **After** | `set_false_path -from [get_clocks clk_axi] -to [get_clocks clk_core]` | top level |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-54
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [IP_CONSTRAINTS.md](./IP_CONSTRAINTS.md)
- [TIMING-47](./TIMING-47.md) - related exception-between-clocks check
