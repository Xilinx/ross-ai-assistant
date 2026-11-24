<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-11: Inappropriate max delay with datapath only option

## Metadata

- **Check ID**: TIMING-11
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

A `set_max_delay -datapath_only` constraint has been applied between a startpoint and an endpoint that either belong to the same clock domain or belong to two clock domains that can safely be timed together. The `-datapath_only` option is only appropriate on paths between clocks that do **not** have a known phase relationship (true clock domain crossings). Using it on related clocks removes the clock-skew component from the analysis and can mask real timing problems.

### Full Message Description Property

```
A max delay constraint with -datapath_only has been applied between <NETLIST_ELEMENT> and
<NETLIST_ELEMENT>. The startpoint(s) and endpoint(s) either belong to the same clock domain or
belong to two clock domains that can safely be timed together (without valid synchronizer). It is
only recommended to use the -datapath_only option on paths between clocks that do not have a
known phase relationship. This DRC is waived when a synchronizer is found on the path endpoint
```

**Example:**
```
A max delay constraint with -datapath_only has been applied between reg_a_reg/C and reg_b_reg/D.
The startpoint(s) and endpoint(s) either belong to the same clock domain or belong to two clock
domains that can safely be timed together (without valid synchronizer)...
```

The two `<NETLIST_ELEMENT>`s are always pins: the clock pin (`/C`) of the startpoint register and the data pin (`/D`) of the endpoint register. There is one instance per start/endpoint pair, so one constraint can raise many instances.

### Explanation

The `-datapath_only` option tells the timing engine to ignore the clock path (source and destination clock latency/skew) and constrain only the data path delay. This is the correct behavior for asynchronous clock domain crossings, where the phase relationship between the launch and capture clocks is undefined and a synchronizer protects the endpoint.

When the constraint is applied between clocks that **do** have a known phase relationship (same clock, or synchronous/expandable clocks), the tool would normally include clock skew in the setup/hold calculation. Stripping that out with `-datapath_only`:

- ❌ Removes clock skew from the analysis, producing optimistic slack
- ❌ Can hide real setup/hold violations between related registers
- ❌ Overrides the default (and correct) related-clock analysis
- ❌ Is flagged because no synchronizer was found on the endpoint

The DRC is automatically **waived** when a recognized synchronizer (e.g. a 2-FF chain / `ASYNC_REG`) is found on the path endpoint, since that indicates a genuine CDC. An instance that is reported therefore has no synchronizer on its endpoint.

**DO**
✅ Identify the exact `set_max_delay -datapath_only` constraint that produced each instance
✅ Resolve per constraint (one resolution for all the instances it raised), not per instance
✅ Determine the clock domain of both the startpoint and the endpoint
✅ Confirm whether the two clocks have a known/expandable phase relationship
✅ Check whether other paths covered by the constraint do need a `set_max_delay -datapath_only`

**DO NOT**
❌ Blindly delete the constraint — first confirm the clock relationship
❌ Assume the crossing is asynchronous just because `-datapath_only` was used
❌ Modify constraints that come from an IP file without checking the IP source

## Flow

**DO NOT**
❌ Automate a fix when the constraint cannot be established (Step 1)
❌ Automate a fix when the constraint file name is null
❌ Automate a fix when the line number is null
❌ Automate a fix when the reported start/endpoints do not match the constraint
❌ Automate a fix when the constraint is an IP constraint (`is_ip` is true)
❌ Automate a fix when the constraint is present more than once (`shared_with` is not empty, Step 2). Report it (CASE 7)

### Step 1: Establish the constraint

The data is already gathered in Step 1 of `SKILL.md`: every instance is in the `details` of the TIMING-11 rule in `./methodology_results/methodology.json` (the index holds only a 5-item preview), and every constraint is in `./methodology_results/tmp/constraints_profile.json`, keyed by its ID.

For each TIMING-11 instance take the two NETLIST_ELEMENTs: the startpoint pin `<sp>` (`<reg>/C`) and the endpoint pin `<ep>` (`<reg>/D`).

1. **Shortlist** the candidates in the profile: the constraints with `type` = `set_max_delay` and `"-datapath_only": true` in `options`. Their `from`/`to` `classes` tell at which level the constraint is written (`clock`, or `cell`/`pin`/`port`).
2. **Confirm** which one covers this pair with `report_exceptions -return_string`. `report_exceptions -from/-to` only matches an exception written with the same objects, so ask at each level the shortlist has, with the objects of the constraint:
```tcl
set sp_pin [get_pins <startpoint_pin>]
set ep_pin [get_pins <endpoint_pin>]
set sp_cell [get_cells -of_objects $sp_pin]
set ep_cell [get_cells -of_objects $ep_pin]
# clocks of the two registers: a /D pin has no clock, so take the clock from the register's clock pin
set sp_clk [get_clocks -of_objects [get_pins -of_objects $sp_cell -filter {IS_CLOCK}]]
set ep_clk [get_clocks -of_objects [get_pins -of_objects $ep_cell -filter {IS_CLOCK}]]

# Cell level  : constraint written with [get_cells ...]
puts [report_exceptions -from $sp_cell -to $ep_cell -return_string]
# Pin level   : constraint written with [get_pins ...]. Use the pins the constraint names (profile from/to objects), e.g. <reg>/Q
puts [report_exceptions -from $sp_pin  -to $ep_pin  -return_string]
# Clock level : constraint written with [get_clocks ...]
puts [report_exceptions -from $sp_clk  -to $ep_clk  -return_string]
```
   A matching constraint is a row of the `Exceptions Report` table whose `Setup` column is `max_dpo=<ns>`. Its `Position` is the constraint ID, so `constraints["<Position>"]` in the profile gives the file, line, delay value (`value`), `from`/`to` objects and `is_ip`:
```
Position  From                  Through  To                    Setup      Hold  Status
4         [get_cells r11a_reg]  *        [get_cells r11b_reg]  max_dpo=4  -
```
   `No valid timing exceptions found.` at a level means no constraint is written with those objects at that level. Try the next level. A `Status` such as `Partially overridden path by DPO <n>` means another `-datapath_only` constraint (ID `<n>`) takes part of the paths. Establish that one too and report both.
3. Group the instances by the constraint ID found. Everything below is done once per ID.
4. Record for the ID: the file and line, the delay `value`, the level (`from`/`to` `classes`), whether it has `through`, and `is_ip`.

If no level returns a `max_dpo` row, or the row does not match any shortlisted constraint, the constraint is **not established**: record the instances as User Decision Required, do not edit any constraint.

### Step 2: Is the constraint present more than once?

Do this before any clock analysis. A scoped XDC file (an IP or a module constraint file applied to several instances of the module) is written once per instance. Each copy has the same original `file` and the same text, under its own `current_instance` `scope`, and each copy has its own ID. The cell names in the text are relative to the scope, so one line of the file constrains different registers in every copy. A copy can be on the same clock while another copy is on a different clock.

For each constraint ID from Step 1, read `shared_with` in its profile entry (the top-level `shared_constraints` lists every group).

- **`shared_with` is empty**: the constraint is unique. Continue with Step 3.
- **`shared_with` lists other IDs**: check the same constraint on every other copy before deciding anything.
  1. For each copy (this ID and every ID in `shared_with`) record the `scope`, `is_ip`, and the TIMING-11 instances whose `report_exceptions` `Position` (Step 1) is that ID. A copy with no TIMING-11 instance is still recorded: its clocks decide whether it is flagged, and it changes if the shared line is edited.
  2. For each copy get the clocks of its start/endpoints as in Step 1 (`$sp_clk`, `$ep_clk`; for a copy with no instance take the registers named by its `from`/`to`) and whether they are the same clock.
  3. Go to CASE 7. Do not run CASE 1, 4, 5 or 6 for any copy.

### Step 3: Confirm the clock relationship

Compare `$sp_clk` and `$ep_clk` from Step 1 (names and `PERIOD`; `report_clock_interaction -delay_type min_max` gives the relationship of two different clocks).

- **Same clock** (the start and the endpoint register are on the same clock) → continue with Step 4.
- **Different clocks with a known/expandable phase relationship** (synchronous) → CASE 5.
- **Several clocks in one `-from`/`-to` group** (a mix of async and related pairs) → CASE 6.
- **Asynchronous clocks**: the instance should have been waived by the tool when a synchronizer is on the endpoint. The constraint is intended. Report it as a User Decision (CASE 2 or CASE 3).

### Step 4: Is the constraint at the clock level or at the cell/pin level?

Use the `from`/`to` `classes` of the constraint from Step 1.

- **Clock level**: `classes` is `["clock"]`. One constraint covers every path between the clocks, so it can cause many TIMING-11 instances.
- **Cell/pin level**: `classes` is `cell`, `pin` or `port`. The constraint targets specific registers.

| Level | Resolution | Type |
|---|---|---|
| Clock | CASE 1: replace the clock constraint with cell-level constraints (scripted) | AUTO |
| Clock | CASE 2: waive | USER |
| Clock | CASE 3: the user adds a CDC circuit | USER |
| Cell/pin | CASE 4: remove the specific constraint | AUTO |

CASE 1 to CASE 6 apply only to a constraint that is unique (Step 2). A constraint present more than once is CASE 7.

For a clock-level constraint select CASE 1 unless the user has said the constraint is intended (CASE 2) or the paths are real crossings that need a synchronizer (CASE 3). In a non-interactive run take CASE 1. For a cell/pin-level constraint select CASE 4.

---

### Clock level

**CASE 1: Clock-level constraint — replace with cell-level constraints in `TIMING-11.xdc`**
* **Type**: Automated Resolution
* **When**: The constraint is at the clock level (`from`/`to` `classes` = `["clock"]`), both clocks are the same clock, `is_ip` is false, `shared_with` is empty, and the constraint is a plain `set_max_delay -datapath_only <ns> -from <clock> -to <clock>` (the profile has no `through`)

The clock-level constraint is removed and replaced by constraints on the individual start/endpoint pairs that TIMING-11 reported. The replacement is a plain `set_max_delay` with the **same delay value** and **without** `-datapath_only`, so the delay bound is kept and clock skew is analyzed again. Every pair TIMING-11 reported for that constraint is covered, so every instance it caused is cleared.

A clock-level constraint can produce thousands of pairs, so do not write them by hand. Run the script. It reads every TIMING-11 pair from `methodology.json`, groups the pairs so each constraint covers exactly one set of startpoints × one set of endpoints (by endpoint or by startpoint, whichever needs fewer constraints), and collapses bus pins to `name[*]/pin` wherever that matches exactly the same pins.

1. Take from the profile entry of the constraint ID: the delay `value` (`<ns>`), the `from.objects` clock(s) and the `to.objects` clock(s).
2. Generate the file. It is written to `./methodology_results/TIMING-11.xdc`:
```tcl
methodology_check::timing11_cell_constraints -delay <ns> -from_clocks {<from clocks>} -to_clocks {<to clocks>}
```
   The proc prints `<P> pair(s) -> <C> constraint(s), <S> synchronizer endpoint(s)`. It also writes, for any `ASYNC_REG` register endpoint on the `-to` clock, `set_max_delay -datapath_only <ns> -from [get_clocks ...] -to [get_cells {...}]`. These are real synchronizers the clock-level constraint was probably meant for. TIMING-11 does not report them. Use `-keep_cdc 0` to leave them out.
3. In `constraints_after_fix.xdc` remove the clock-level `set_max_delay -datapath_only` line (the `line` of the profile entry, a **modify**, not a comment-out) and put this comment where it was:
```tcl
# TIMING-11: clock-level set_max_delay -datapath_only removed; replaced by ./methodology_results/TIMING-11.xdc
```
4. The Step 6 re-run reads `./methodology_results/TIMING-11.xdc` after `constraints_after_fix.xdc` (see `SKILL.md`, Step 6).
5. Report, for the resolution: the removed clock-level constraint as `before`; as `after` the path `./methodology_results/TIMING-11.xdc`, the number of pairs and constraints from step 2, and the first lines of the file. Tell the user the bound now includes clock skew (it no longer uses `-datapath_only`).

```tcl
# BEFORE (constraints_after_fix.xdc, clock level; one instance per start/endpoint pair):
set_max_delay -datapath_only 4.000 -from [get_clocks clk] -to [get_clocks clk]

# AFTER (./methodology_results/TIMING-11.xdc, cell level; same pairs, 4 constraints for 67 pairs):
set_max_delay 4.000 -from [get_pins {r11a_reg/C}] -to [get_pins {r11b_reg/D}]
set_max_delay 4.000 -from [get_pins {r13a_reg/C}] -to [get_pins {r13b_reg/D}]
set_max_delay 4.000 -from [get_pins {r14s_reg/C}] -to [get_pins {r14d_reg/D}]
set_max_delay 4.000 -from [get_pins {{ra_reg[*]/C} {rb_reg[*]/C}}] -to [get_pins {{rs_reg[31]/D}}]
```

**DO**
✅ Keep the original delay value
✅ Use the script. Do not write pair constraints by hand
✅ Put every generated constraint in `TIMING-11.xdc`, and only those
✅ Confirm in the re-run that TIMING-11 no longer reports any of these pairs
✅ Check the path slack with clock skew included (`report_timing -from ... -to ...`) and report any path that now fails

**DO NOT**
❌ Add `-datapath_only` to the pair constraints (they would raise TIMING-11 again)
❌ Leave the clock-level constraint in `constraints_after_fix.xdc`
❌ Use this case when the profile shows `through`. The script does not reproduce it. Use CASE 2 or ask the user.

**CASE 2: Clock-level constraint — waive the instances**
* **Type**: User Resolution
* **When**: The user states the clock-level constraint is intended and must stay as it is, or `is_ip` is true

Do not change the constraint. Ask for the justification, then create the waiver as in `SKILL.md` ("Creating Waivers") for each reported TIMING-11 instance of the constraint ID, and write `waivers_after_fix.rpt`.
```tcl
create_waiver -of_objects [get_methodology_violations TIMING-11#1] \
    -user <user> -description "<justification>"
```

**DO**
✅ Record the user's justification in the waiver description and in the report
✅ Warn that a waived TIMING-11 hides optimistic slack: clock skew stays excluded on these paths

**DO NOT**
❌ Waive without a justification from the user

**CASE 3: Clock-level constraint — the user adds a CDC circuit**
* **Type**: User Resolution
* **When**: The paths the constraint covers are real crossings (data that is asynchronous to the capturing register even though the clock name is the same), so `-datapath_only` is the right constraint but a synchronizer is missing

This is an RTL change, which this skill does not make. Recommend that the user add a recognized synchronizer on each endpoint (a chain of at least two flip-flops marked `ASYNC_REG`, or a CDC macro such as `XPM_CDC_SINGLE` / `XPM_CDC_ARRAY_SINGLE`). TIMING-11 is waived by the tool when it finds a synchronizer on the endpoint, so the constraint can then stay unchanged.

**DO**
✅ List the endpoints that need a synchronizer (from the TIMING-11 instances)
✅ Report the resolution as User Decision Required or Not Supported, with the RTL recommendation

**DO NOT**
❌ Edit the RTL
❌ Change the constraint while waiting for the RTL change

---

### Cell/pin level

**CASE 4: Cell/pin-level constraint — remove the specific constraint**
* **Type**: Automated Resolution
* **When**: The constraint's `from` and `to` classes are `cell`, `pin` or `port`, the registers are on the same clock, `is_ip` is false, `shared_with` is empty, and other paths do not need the constraint

The `-datapath_only` exception is unnecessary and hides clock skew. Remove the one constraint that caused the instances (the entry `constraints["<ID>"]`, at its `file` and `line`), and nothing else.

1. Remove that `set_max_delay -datapath_only ...` line from `constraints_after_fix.xdc` (a **modify**).
2. If a bound on the delay is still needed, say so in the report and offer the plain `set_max_delay` below as an opt-in. Do not add it yourself.
```tcl
# BEFORE:
set_max_delay -datapath_only 4.000 -from [get_cells r11a_reg] -to [get_cells r11b_reg]

# AFTER: line removed. Optional, only if the user wants a bound (clock skew included):
# set_max_delay 4.000 -from [get_pins r11a_reg/C] -to [get_pins r11b_reg/D]
```

**DO**
✅ Confirm no other paths rely on this same constraint before removing it. When `from`/`to` have several `objects` or `wildcard` is true, the constraint may also cover a pair that ends at a synchronizer (not reported). If it does, remove only the reported pairs and keep the rest
✅ Remove only the constraint that was reported

**DO NOT**
❌ Silently re-add `-datapath_only`
❌ Remove other constraints on the same registers

---

### Other clock relationships

**CASE 5: Synchronous clocks (known phase relationship) — remove the constraint**
* **Type**: Automated Resolution
* **When**: The two clocks are different but have a known/expandable phase relationship, and a max delay bound is genuinely intended

The two clocks are related, so the `-datapath_only` exception is inappropriate. Remove the
`set_max_delay -datapath_only` constraint so the path is timed normally (with clock skew) as a
related-clock path. Call out the period difference between the two clocks so the user understands
what the default related-clock analysis will now enforce.

1. Remove the offending `set_max_delay -datapath_only ...` constraint.
2. Report the period difference of the two related clocks:
```
Removed the -datapath_only max delay between <startpoint> and <endpoint>.
The clocks are related:
  <sclk>: period=<period1> ns
  <eclk>: period=<period2> ns  (difference: <|period1-period2|> ns)
The path is now timed as a related-clock path (clock skew included).
```

3. If the user still wants to bound the datapath delay, they can re-add a plain `set_max_delay`
   (without `-datapath_only`) — provide this example:
```tcl
# Optional: re-add a bounded max delay WITHOUT -datapath_only (clock skew included)
set_max_delay 5.0 -from [get_pins reg_a_reg/C] -to [get_pins reg_b_reg/D]
```

**DO**
✅ Report the period difference between the related clocks
✅ Offer the plain `set_max_delay` example only as an opt-in for the user
✅ Verify the path still meets timing once it is analyzed as a related-clock path

**DO NOT**
❌ Silently re-add `-datapath_only`
❌ Keep the exception when the clocks are related

**CASE 6: Multi-clock `-from`/`-to` group — split the constraint**
* **Type**: Automated Resolution
* **When**: The constraint uses a broad `-from`/`-to` (e.g. `[get_clocks {...}]`, a wildcard, or a large cell/pin collection) that spans **multiple clock pairs**, and only *some* of the covered pairs are true asynchronous crossings while others are same-domain or synchronous

A single `set_max_delay -datapath_only` applied to a large from/to group is often too coarse: the group mixes genuine CDC pairs (which correctly need `-datapath_only`) with related-clock pairs (which should keep clock skew). The DRC fires on the related pairs. Deleting or blanket-editing the constraint would wrongly change the CDC pairs, so the constraint must be **broken up** so that `-datapath_only` applies only to the asynchronous pairs, and the related pairs are dropped from the exception entirely (timed normally, as in CASE 5).

1. Enumerate every clock pair covered by the constraint's `-from`/`-to` groups.
```tcl
# Expand the from/to groups and classify each pair
set from_clks [get_clocks -of_objects [get_pins <from_group>]]
set to_clks   [get_clocks -of_objects [get_pins <to_group>]]
report_clock_interaction -delay_type min_max -file ./tmp/timing11_group_interaction.rpt
# For each (from, to) pair, decide: asynchronous (keep -datapath_only) vs related (remove it)
```

2. Partition the pairs into two sets:
   - **Asynchronous / no known phase relationship** → keep `-datapath_only`
   - **Same domain or synchronous** → remove from the exception (timed normally with clock skew)

3. Replace the single broad constraint with a single scoped constraint that keeps only the asynchronous pairs. The synchronous/related pairs are removed entirely (no `set_max_delay`), and their period differences are reported per CASE 5:
```tcl
# BEFORE (single broad constraint — fires TIMING-11 on the related pairs):
set_max_delay 5.0 -datapath_only \
    -from [get_clocks {clk_async clk_sync}] -to [get_clocks {clk_cap}]

# AFTER (only the asynchronous pairs keep -datapath_only; related pairs dropped):
set_max_delay 5.0 -datapath_only \
    -from [get_clocks {clk_async}] -to [get_clocks {clk_cap}]
# clk_sync -> clk_cap is a related pair: removed from the exception, now timed normally.
# Report the period difference (see CASE 5).
```

**DO**
✅ Scope the kept constraint to only the asynchronous clocks (or pins)
✅ Preserve the original delay value(s), `-through` conditions, and min/max intent on the asynchronous half
✅ Report the period difference for each related pair that was removed (see CASE 5)
✅ Re-run the methodology check to confirm every offending pair is reclassified and no async pair lost its `-datapath_only`

**DO NOT**
❌ Re-add a plain `set_max_delay` for the synchronous/related pairs
❌ Drop `-datapath_only` from the asynchronous half
❌ Leave a related pair covered by a `-datapath_only` constraint

---

### Constraint present more than once

**CASE 7: Scoped constraint present on several instances — report to the user**
* **Type**: User Resolution
* **When**: Step 2 found the constraint more than once (`shared_with` is not empty): the same line of the same original file is applied under several scopes

One line of the file is applied to every instance of the module, so a change to it changes every copy. A copy can be on the same clock (the constraint is inappropriate) while another copy is between two different clocks (the constraint is correct, or is a real CDC missing a synchronizer). No single edit is right for all of them, and the skill does not edit a scoped file. Do not edit, remove or replace the constraint, and do not run `timing11_cell_constraints`. Report the issue to the user with the evidence from Step 2:

```
TIMING-11: the constraint at <file> (constraints.xdc line <line>) is present <N> times, once per instance of the scoped file.
  set_max_delay -datapath_only <ns> -from <from> -to <to>

  ID   Scope (current_instance)   IP    TIMING-11 instances   Start clock -> end clock    Same clock
  <id> <scope>                    <y/n> <count>               <sclk> -> <eclk>            <yes/no>
  ...

Editing the shared line changes all <N> copies. Nothing was changed.
```

Then offer the options, and let the user choose:
1. **Same clock on every copy that raises TIMING-11**: edit the source file once to remove `-datapath_only` (or use a plain `set_max_delay`). It applies to all copies; confirm that the copies without an instance are intended to change as well.
2. **Mixed clocks**: make the constraint instance-specific. Remove it from the scoped file and add it only where it is wanted: top-level `set_max_delay -datapath_only` for the copies between different clocks, a plain `set_max_delay` or nothing for the same-clock copies.
3. **Waive** the instances (CASE 2) when the shared constraint is intended.
4. **Add a CDC circuit** (CASE 3) where a copy is a real crossing.

In a non-interactive run take no option: record the resolution as User Decision Required (the report states the evidence above) and do not modify any file.

**DO**
✅ Show every copy, including the ones that raise no TIMING-11, with its scope and clocks
✅ State that a change to the shared line applies to all copies
✅ Report an IP-scoped constraint (`is_ip` true) as not editable (see [IP Constraints](./IP_CONSTRAINTS.md))

**DO NOT**
❌ Resolve one copy and leave the others unchecked
❌ Edit a scoped file, or `constraints_after_fix.xdc`, for this case
❌ Pick an option for the user

## Additional Checks

- Verify whether the constraint originates from an IP (clocking wizard, third-party IP) before modifying: `is_ip` in the profile, see [IP Constraints](./IP_CONSTRAINTS.md)
- Check for other exceptions (`set_false_path`, `set_clock_groups`) already covering the same crossing: the `Status` column of `report_exceptions`, and the other `timing_exception` / `clock_relationship` entries of the profile
- Confirm the clocks' common primary clock / common period using `report_clock_interaction`

## Verification

```tcl
# Re-run the methodology check for TIMING-11 (CASE 1 reads TIMING-11.xdc after constraints_after_fix.xdc)
report_methodology -checks {TIMING-11} -file ./tmp/methodology_timing11_after_fix.rpt

# The removed constraint is no longer reported for the pair (no max_dpo row at the level it was written)
puts [report_exceptions -from $sp_cell -to $ep_cell -return_string]

# Confirm the path is now timed as intended
report_timing -from [get_pins <startpoint_pin>] -to [get_pins <endpoint_pin>] \
    -file ./tmp/timing11_path_after_fix.rpt
```

* [ ] TIMING-11 violation is no longer present
* [ ] CASE 1: `./methodology_results/TIMING-11.xdc` exists, covers every reported pair, and the clock-level constraint is gone from `constraints_after_fix.xdc`
* [ ] Path is analyzed with correct clock skew (CASE 1, 4, 5), or the broad constraint is split so `-datapath_only` applies only to async pairs (CASE 6)
* [ ] CASE 7: every copy of the shared constraint is listed with its scope and clocks, and no file was changed
* [ ] No new unresolved methodology warnings created

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)
- Vivado Design Suite User Guide: Using Constraints (UG903) — `set_max_delay`, `-datapath_only`
- [IP Constraints](./IP_CONSTRAINTS.md)

## Examples

*To be documented with real-world examples from a TIMING-11 testcase as required.*
