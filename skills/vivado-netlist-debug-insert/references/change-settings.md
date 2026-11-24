<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Change a setting of a core the skill inserted

`ila::change` changes a capture setting of an ILA this skill inserted, in place. Probe nets, clock and core name stay. Vivado rebuilds the core at the user's next `opt_design`. The skill never runs it.
Source: `scripts/ila_change.tcl` (`change`, `prove_built`, and the ownership record).

## What can change

| Setting | Option | UltraScale+ | Versal |
|---|---|---|---|
| Capture depth | `-depth N` | 1024 to 131072, powers of two | same; with UltraRAM 4096 and up |
| Capture memory | `-memory_type bram\|uram` | not offered (block RAM only) | block RAM or UltraRAM |
| Capture control (storage qualification) | `-strg_qual 0\|1` | on or off | on or off |
| Advanced trigger | `-adv_trigger 0\|1` | on or off | on or off |
| Comparators, one count for every probe | `-comparators N` | 1 to 16, only with advanced trigger or capture control | same |
| Input pipe stages | `-pipe_stages N` | 0 to 6 | 0 to 6 |
| Probe type of every probe port of the core | `-probe_type data\|trigger\|data_and_trigger` | data only, trigger only, or trigger and data | same |
| Probe type of the port that carries named nets (one port per signal) | `-probe_types {name type ...}` | same three types | same |

Not offered: trigger in and out (`-trigin`, `-trigout`; `ILA_OPTION_UNSUPPORTED`). On UltraScale+ Vivado reads the setting back as on and builds no trigger ports; on Versal it reads back off. Also not offered: more probe ports, a comparator count per probe.

## Probe type by net

AI only. The user says in words which signals should be trigger only, data only, or trigger and data. You write the Tcl and do not show it.

- `-probe_types {name type ...}`: each name is a plain name (a glob names every net it matches), each type is `data`, `trigger` or `data_and_trigger`. A list with an odd number of words is refused (`ILA_OPTION_RANGE`).
- Vivado has no net-to-port query, so the skill finds the port of a net by reading the nets of each probe port of the core.
- A net takes the type named for it, else the whole-core `-probe_type`, else the type its port has now.
- `-probe_type` alone sets every probe port of the core.
- A name that matches nothing in the design: `ILA_BAD_ITEM`. A name that matches nets, none of them probed by the core: `ILA_CHANGE_NET_NOT_ON_CORE`: "'<name>' matches nets in the design, but <core> does not probe any of them. Nothing was changed."
- A net given two types: `ILA_BAD_ITEM` ("Give each net one type").
- A port whose nets would get two types is refused (`ILA_CHANGE_SHARED_PORT`): "net <net> shares a port with <nets>; to give it its own type, plan again with replace. Nothing was changed. Do not plan again with replace unless the user asks: it deletes this core and its probes." Each signal has its own port, so this means bits of one signal (or a core built by an earlier version of the skill, with several signals on one port). Tell the user in words: those bits sit on one probe and cannot be split. Offer to plan the ILA again with replace. Do not change the core another way.

## Which cores

Only cores this skill inserted. `ila::apply` writes a record into the design (the design property `ILA_SKILL_CORES`, flat designs only): per core its name, the driver pin of its clock, its probe bit count and a CRC32 of its sorted probe net names. The record survives `write_checkpoint`, a new Vivado session, `opt_design` and a reopened checkpoint. It is not in the probes file. `ila::change` reads the core again and compares.

- `ILA_CHANGE_NOT_SKILL_CORE`: the core is not in the record (the user's own core, or the wizard's), or there is no debug core of that name.
- `ILA_CHANGE_CORE_EDITED`: the core is in the record, but its probes or its clock driver changed since the skill made it. Also raised when a set moved them.
- `ILA_CHANGE_NO_MARKER`: the design has no record (a design from an older skill, or a project whose record was lost).
- A record entry for a core that is gone is ignored, and said once.

Tell the user why it was refused. Never change the core with `set_property` yourself.

## Show

`ila::change <core>` with no option prints three lines: whether the skill made the core (clock driver, probe bits, fingerprint), its settings now, and the values each setting accepts. It changes nothing.

## Dry run

`ila::change <core> -depth 8192 -dry_run 1` runs every check and prints what it will set, from and to, the memory line, and that Vivado rebuilds the core at the next `opt_design`. It changes nothing. Relay it.

Checks, in this order, all before any set:

- Option values (`ILA_BAD_OPTION`, `ILA_DEPTH_RANGE`, `ILA_OPTION_RANGE`, `ILA_OPTION_UNSUPPORTED`).
- No reconfigurable partition (`ILA_CHANGE_DFX`: not measured, so not done).
- No placed cells (`ILA_REFUSED_PLACED`). A set on a placed or routed design drops the core's placement without a message, and a Versal design then cannot be placed again. `ila::change` counts placed cells itself: `-allow_state_reset 1` of `ila::check_input` does not apply.
- The core is the skill's own (see "Which cores").
- UltraRAM is Versal only and needs depth 4096 or more (`ILA_OPTION_RANGE`). A depth under 4096 on an UltraRAM core moves it to block RAM first, and says so.
- Comparators only with advanced trigger or capture control (`ILA_OPTION_RANGE`). Capture control needs at least 2 comparators: the skill raises the count to 2 first and says so (UltraScale+ fails `opt_design` with 1; Versal builds and reads 1).
- Probe ports times comparators at most 1024 (`ILA_CHANGE_COMPARATOR_LIMIT`). Each signal is a probe port, so a core with more than 64 signals cannot take 16 comparators.
- Probe type trigger on the only probe port (`ILA_CHANGE_NEEDS_DATA_PROBE`): no probe would store data, and `opt_design` would fail.
- Memory for a new depth or memory type: the free tiles, plus the core's own, minus the modelled tiles of the other cores not yet built. Over budget: `ILA_OVER_BUDGET` with the largest depth that fits. A part that gives no count prints `WARNING W02`: tell the user it was not checked.

Ask the user first only when the request is ambiguous (several skill cores and no name), or when the dry run adds a change they did not ask for (comparators raised to 2, memory moved to block RAM). Else go on.

## Change

The same call without `-dry_run`. The sets run in the order Vivado needs: depth before UltraRAM on, block RAM before a depth under 4096, comparators before capture control. Each value is read back; a mismatch stops with `ILA_PROPERTY_MISMATCH` and says what was already set. Then the probes and clock are read again.

It prints, for example:

```text
ILA: CHANGED u_ila_0: depth 1024 -> 8192 (read back 8192). Probes and clock unchanged: 16 nets, clock driver u_bufg/O.
ILA: NOT DONE: nothing was saved, written or built. Vivado builds u_ila_0 with these settings at the next opt_design. To keep the change, save the design before this session closes (AI only: write_checkpoint -force <path>, only to a path the user named).
```

Relay the CHANGED line and the NOT DONE facts. Do not list, recommend or offer a build step. Asking for the settings the core already has prints "nothing to change".

## Prove

After the user's `opt_design`, `ila::prove_built <core>` checks the built core, not the core object (a set can read back and still build otherwise). Read only.

- UltraScale+: every setting in the built cell's `DEBUG_CORE_INFO`, and the block RAM count under the core.
- Versal: the memory count only. `DEBUG_CORE_INFO` is missing on 2026.2 and stale on 2026.1 after a change, so it is never read. The other settings are read back only: say so.
- The count is PROVEN only when it is inside the model range of the new depth and outside the range of the depth before the change. When the two ranges overlap (1024 and 2048 at narrow widths) it prints NOT PROVABLE from the memory count. FAILED names the setting or count that is wrong.
- `ila::prove_built <core> -ltx <file>` also reads the probe type of each port from the probes file the user wrote with `write_debug_probes` (UltraScale+ and Versal). It reports whether the file agrees with the plan. A file that cannot be read: `ILA_CHANGE_NO_PROBES_FILE` ("the probes file <file> cannot be read (<error>). Write it with write_debug_probes after opt_design, then ask again. Nothing was checked."). A file that does not list the core or a port fails the proof. On Versal the PROBE_TYPE column of the core report is checked too; without `-ltx` the probes file is not checked, and the proof line says so.
- Before `opt_design` it refuses (`ILA_CHANGE_NOT_BUILT`). Tell the user the proof needs a design that `opt_design` has built.

Report the PROVE line as printed.
