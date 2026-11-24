<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-24: Overridden max delay datapath only

## Metadata

- **Check ID**: TIMING-24
- **Severity**: Warning

## Description

A `set_clock_groups` (asynchronous) or `set_false_path` between two clocks overrides a `set_max_delay -datapath_only`. The broad exception disables the intended datapath-only budget between those clocks.

## Message

```
An asynchronous set_clock_groups or a set_false path (see constraint position <MESSAGE_STRING> in the Timing Constraints window in Vivado IDE) between clocks <CLOCK_GROUP> and <CLOCK_GROUP> overrides a set_max_delay -datapath_only (position <MESSAGE_STRING>). It is not recommended to override a set_max_delay -datapath_only constraint. Replace the set_clock_groups or set_false_path between clocks with point-to-point set_false_path constraints
```

## Flow

1. **Gather Data** → Run `report_exceptions -ignored` to find the overridden `set_max_delay -datapath_only` and the overriding `set_clock_groups`/`set_false_path`. Read the `-from`/`-to` of the max delay and every group of the `set_clock_groups`.
2. **Classify** → Pick the CASE from the shape of the two exceptions:

| Overridden `set_max_delay -datapath_only` | Overriding exception | CASE |
|---|---|---|
| clock to clock (`A` to `B`) | clock-level `set_false_path`, or `set_clock_groups` with exactly two groups of one clock each | 1 (automated) |
| pins/cells to pins/cells | clock-level `set_false_path`, or `set_clock_groups` with exactly two groups of one clock each | 2 (semi-automated) |
| anything else: 3+ groups, 2+ clocks in a group, or an exception that mixes clocks with cells/pins | any | 3 (user decision) |

3. **Resolve** → Apply the CASE.
4. **Verify** → Re-run `report_exceptions -ignored` and `report_methodology`.

A `set_clock_groups -asynchronous` cuts **every** crossing between its groups, in both directions. Deleting it, or removing clocks from it, times the crossings it used to cut, not only the budgeted one. So the replacement always keeps every other crossing cut with a point-to-point `set_false_path`.

**CASE 1: `-datapath_only` is clock-to-clock — Automated Resolution**
* **Type**: Automated Resolution
* **When**: The overridden `set_max_delay -datapath_only` is from clock `A` to clock `B`, and the overriding exception is either a clock-level `set_false_path`, or a `set_clock_groups` with exactly two groups that hold one clock each.

Keep the `set_max_delay -datapath_only` as-is. Vivado has no command that deletes one exception from the open design, so make the change in `./methodology_results/constraints_after_fix.xdc` (find the overriding constraint by its `# id:N` position, see `constraints_profile.json`). Step 6 then runs `reset_timing` and re-reads that file.

* Overriding exception is a clock-level `set_false_path -from A -to B`: delete that line. A reverse line `-from B -to A` stays, unless a matching reverse `set_max_delay` exists, then delete it too.
* Overriding exception is `set_clock_groups -asynchronous -group {A} -group {B}`: delete it and add `set_false_path -from B -to A`, so the reverse direction stays cut. If a reverse `set_max_delay -datapath_only` B to A exists, add nothing. The helper proc writes the file for you (dry run first). It never changes the design:

```tcl
# Dry run: prints the constraints
methodology_check::resolve_clock_groups_override -groups {{<A>} {<B>}} -budgets {{<A> <B>}}
# Write them for Step 6 to read (one file per override, named with the overriding constraint's # id:N)
methodology_check::resolve_clock_groups_override -groups {{<A>} {<B>}} -budgets {{<A> <B>}} \
    -out ./methodology_results/TIMING-24_<id>.xdc
```

Then delete the `set_clock_groups` from `constraints_after_fix.xdc`.

**CASE 2: any other `-datapath_only` (from/to pins or cells) — Semi-Automated Resolution**
* **Type**: Semi-Automated Resolution
* **When**: The `set_max_delay -datapath_only` endpoints are pins/cells (not clock-to-clock), and the overriding exception is a clock-level `set_false_path` or a `set_clock_groups` with exactly two groups of one clock each. If clocks are mixed with cells/pins, or the groups are larger, use CASE 3.

The broad exception covers `S_big × E_big`; the `-datapath_only` covers a subset `S_small × E_small`. Rewrite the broad exception as point-to-point `set_false_path` constraints that cover **everything except** the datapath_only pins, leaving **no overlap**:

$$\text{keep} = (S_\text{big} \times E_\text{big}) \setminus (S_\text{small} \times E_\text{small}) = \big(S_\text{big} \times (E_\text{big} \setminus E_\text{small})\big) \cup \big((S_\text{big} \setminus S_\text{small}) \times E_\text{small}\big)$$

That yields two `set_false_path` commands. Enumerate the start/endpoint pins per clock net or per cell:

```tcl
# Startpoints/endpoints from a clock net
set S [all_fanout -flat -endpoints_only [get_nets -of [get_clocks <clk>]]]
set E [get_pins -filter {IS_CLOCK==0 && DIRECTION==IN} -of [get_cells -of $S]]

# Startpoints/endpoints from a cell
set S [get_pins -filter {IS_CLOCK} -of $cell]
set E [get_pins -filter {IS_CLOCK==0 && DIRECTION==IN} -of $cell]
```

Use the helper proc, which enumerates coverage, marks the overlap and generates the carve-out constraints.
The proc is `methodology_check::resolve_datapath_only_override`; it is already loaded by Step 1
(`run_methodology_setup.tcl`), so it is not sourced here. It never changes the open design.

```tcl
# Dry run: prints the markup and the generated constraints
methodology_check::resolve_datapath_only_override \
    -big_from <clkA> -big_to <clkB> -big_type false_path \
    -dpo_from <dpo_from_pins/cells> -dpo_to <dpo_to_pins/cells>
# Once the dry run looks correct, write the carve-outs for Step 6 to read.
# Use a different <id> (the overriding constraint's `# id:N`) for each override: the file is overwritten, and Step 6 reads every TIMING-*.xdc
methodology_check::resolve_datapath_only_override ... -out ./methodology_results/TIMING-24_<id>.xdc
```

Then remove the broad exception from `constraints_after_fix.xdc`: delete the clock-level `set_false_path`, or delete the `set_clock_groups`. When the broad exception is a `set_clock_groups`, pass `-big_type clock_groups`: the proc then also writes the reverse false path (`-from B -to A`), because deleting the groups would otherwise time the reverse direction. `-big_from` and `-big_to` must each be one clock.

**CASE 3: larger or mixed exceptions — User Resolution**
* **Type**: User Resolution
* **When**: Any of these:
  * The `set_clock_groups` has 3 or more groups, or any group holds 2 or more clocks.
  * A `set_false_path` or the `set_max_delay -datapath_only` lists several clocks in `-from`/`-to` and cannot be split per clock pair.
  * An exception mixes clocks with cells/pins, in one list or across `-from`/`-to` (for example `-from [get_clocks A] -to [get_cells x]`).

Do not edit the constraints. Both procs stop with an error that starts `TIMING-24 CASE 3 (user decision)`. Ask the user:

1. Show the overriding exception, the `set_max_delay -datapath_only`, and the crossings the override cuts today.
2. For the `set_clock_groups` shapes, show the proposal. `methodology_check::resolve_clock_groups_override -groups {{<A> <C>} {<B>} {<D>}} -budgets {{<A> <B>}}` prints the point-to-point false paths it would generate (every cut crossing except the budgeted ones) and stops. Nothing is written.
3. Ask which is intended: (a) accept the proposal, (b) keep the override and drop the max delay, (c) split the exception another way, or (d) waive TIMING-24 with a justification.
4. In a non-interactive run, do not apply the proposal. Report the check as **User Decision Required** with the proposal and the question in the remaining actions.

**Verify (CASE 1 and 2):**
1. After Step 6, `report_exceptions -ignored` must be **empty** — the max delay is no longer overridden.
2. Re-run `report_methodology` and confirm TIMING-24 no longer reports.
3. `report_clock_interaction` shows the replaced crossings as "False Path" instead of "Asynchronous Groups". That is expected.

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-24)
- [UG903](https://docs.amd.com/r/en-US/ug903-vivado-using-constraints) - Using Constraints (timing exceptions)
