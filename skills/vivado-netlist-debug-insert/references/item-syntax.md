<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Names for `ila::plan` (for the AI only)

The user speaks plain English and never sees a name pattern, a flag or a code unless they ask how. This file is for you: how to turn a request into the names and arguments of `ila::plan {name ...} ?options?`.
Source: `scripts/ila_resolve.tcl` (`resolve_plain`, `resolve_filter`) and `scripts/ila_plan.tcl` (`plan`).
For the user, in plain words: `help.md`, section items.

A name is a Vivado name pattern. There are no prefixes, no kinds and no colon slice. The library decides whether a name is a register, a net or a combinational net.

## Rules V1 to V9

- **V1, matching.** A name is matched against the full hierarchical name, as `-filter {NAME =~ <name>}` does in Vivado: `*` and `?` match any characters including `/`, `[ ]` are literal, case-sensitive, anchored at both ends. A bare name (no `/`) is the top level only.
- **V2, paths.** A name with a path is that exact path from the top (`u_a/u_b/count`). A path with a wildcard matches by V1: `*/count` is `count` below the top at any depth, `u_a/*` is everything under `u_a`. For "in every instance" or "all of them" write `*/count`, plus a bare `count` when `count` also exists at the top (`*/count` cannot match a top-level `count`).
- **V3, order.** For each name, registers come first: register cells named `<name>_reg` or `<name>_reg[...]`, and their output nets. If none, nets named `<name>` or `<name>[...]`. A large named register is checked against the memory budget like any other.
- **V4, buses.** A bare bus name is every bit (`count` is `count_reg[*]` or `count[*]`). One bit is the literal name (`count[3]`). Several bits are listed by name (`count[0] count[1] count[2]`). There is no colon slice: a name with a colon range in it is a literal name and matches nothing.
- **V5, wildcards.** A name with `*` or `?` in its last part is the union of V3's two sets: register cells whose name matches (their output nets) plus nets whose name matches. Combinational nets it skipped are reported (N16). A whole module is `u_x/*`.
- **V6, not found.** Every match is returned. If nothing matches the refusal is N01, and when the same leaf name exists elsewhere it lists where. There is no auto-pick of a deeper match and no refusal because a name matches in several places. Ask the user, or plan again with a path.
- **V7, combinational.** An exact name (no `*` or `?`, with or without a path) whose net is combinational is accepted with warning W01. `-combinational {names or globs}` takes combinational nets for those names only, and a name listed there is not also in the name list. `-combinational {*}` is refused as too broad (N17). Only when the user asks for combinational nets. The words everything, all signals and all nets do not.
- **V8, filter.** `-filter <expr>` is a Vivado net filter expression. It adds every net `get_nets -hierarchical -filter <expr>` returns to the nets the names give; it never narrows the names. Marked nets: `-filter {MARK_DEBUG}`. Marked nets in one module: `-filter {MARK_DEBUG && (NAME =~ u_wrap/* || NAME =~ */u_wrap/*)}` (the `*/u_wrap/*` term is needed because the module may sit below the top). Marked nets may be combinational and get the warning. A request with only a filter and no names is valid.
- **V9, broad limit.** A name with a wildcard in its first path part is subject to `-broad_limit` (N17), except a path glob whose last part is a plain name (`*/x`, `u_a/*/x`). A bare `*` stays under the limit.

## Which words ask for what

| The user says | You plan |
|---|---|
| "the register count", "signal count" | `count` |
| "count in u_a/u_b/c" | `u_a/u_b/c/count` |
| "count in every instance" | `*/count` (and `count` if it exists at the top) |
| "bits 0 to 7 of count" | `count[0] count[1] ... count[7]`, or `count` when that is the whole bus |
| "the data registers in u_dma" | `u_dma/data_reg*` |
| "everything in module u_x" | `u_x/*` |
| "everything marked for debug" | `-filter {MARK_DEBUG}` |
| "everything" with no module or names | `*`, and let the library say too broad |
| "the combinational net u_alu/sum" | `u_alu/sum` (warning W01) |
| "all combinational nets in u_alu" | `-combinational {u_alu/*}` |

## Empty and unknown

- An empty name (or one ending in a slash) is an error (`ILA_BAD_ITEM`). An empty name list with no `-filter` and no `-combinational` is a plain refusal.
- The old prefixed forms are gone. Do not write them.
- A signal that synthesis renamed (`count` became `count_0`) is found only by its new name. The register `<name>_reg` is tried first.

## Match-all and size limits

- A wildcard name with a wildcard in the first path part counts together with the others. More than 1024 probe nets (`-broad_limit`) and all of them are refused (N17), except `*/x` forms (V9).
- A wildcard scoped to a module or path (`u_dma/data_reg*`, `u_stream/*tdata*`) is not subject to `-broad_limit`. Only N10 (bits per signal, signals times comparators) and the block RAM budget limit it. Exact names never were.
- A single wildcard name that matches more than 4 times the limit is refused early.

## Plan options

| Option | Default | Notes |
|---|---|---|
| `-depth N` | 1024 | Valid depths are read from Vivado (1024 to 131072, powers of two). Others stop with `ILA_DEPTH_RANGE`. |
| `-name_prefix` | `u_ila_` | Letters, digits and underscore only; anything else stops with `ILA_OPTION_RANGE` and nothing is changed. A name in use stops with `ILA_NAME_IN_USE`. |
| `-max_probe_bits N` | 4096 | Per clock, all signals of the core together. The family limits stay: one signal (one probe port) at most 4096 bits on UltraScale and UltraScale+ (PG172) and 1024 on Versal (PG357; Vivado fails `IP_Flow 19-3458` above it), and at most 65,536 bits per UltraScale or UltraScale+ core. N10 names the family. |
| `-replace_existing 0\|1` | 0 | Delete old `u_ila*` cores first. Their nets do not count as already probed (N20). If the new cores cannot be built, the old ones are put back; if that fails, the error says to reopen the checkpoint. |
| `-allow_duplicate 0\|1` | 0 | Probe a net again that an existing debug core already probes (N20). Only with user confirmation. |
| `-pipe_stages N` | -1 | Auto: 1 when a probed net is combinational, else 0. Range -1 to 6. |
| `-strg_qual`, `-adv_trigger` | 0 | Storage qualification sets match units to 2 (it takes one comparator); advanced trigger alone uses 1. Measured, see `memory-budget.md`. |
| `-memory_type bram\|uram` | bram | Versal only (`C_MEMORY_TYPE` 0 or 1). `uram` needs depth 4096 or more and enough free URAM tiles. Other families stop with `ILA_OPTION_RANGE`. |
| `-trigin`, `-trigout` | 0 | Trigger in and out. Every property is read back after it is set. On Versal `-trigin 1` is accepted by Vivado and ignored, so `apply` stops with `ILA_PROPERTY_MISMATCH`. |
| `-broad_limit N` | 1024 | Probe bits an unscoped match-all may pick before N17. Scoped wildcards are exempt. |

A value outside the range stops with `ILA_OPTION_RANGE`.

## One ILA per clock

- Nets on the same physical clock share one ILA, even when the clock has different names in different modules.
- Each core gets a clock verdict: `free`, `gated` (planned with a warning) or `unknown` (warning).

## Names that overlap

- A net is skipped by a later name only when it is already PROBED. A net an earlier name refused (a combinational net under `u_x/*`) is tried again by a later name that names it. The probe set does not depend on the order of the names. The port order does: one port per signal, in the order the names were given.
- Two names of one net (an alias) are probed once, and the plan says so (`A01`). Nothing is said when the first name was refused.
- Every refused net is reported with its code. A net refused by one name and probed by another is not listed as refused.

## Existing debug cores

- A net that an existing debug core already probes is skipped as `N20`, with the core's name. The nets are found from the probe pins of the core cell, by every name of the net.
- `-allow_duplicate 1` probes them again. With `-replace_existing 1` the cores being replaced (`u_ila_*` cores Vivado lists) do not count.
- If every requested net is skipped, `apply` stops with `ILA_NOTHING_INSERTED` and the plan lists each `N20`.
