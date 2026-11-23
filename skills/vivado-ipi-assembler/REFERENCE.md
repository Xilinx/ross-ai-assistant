<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Clock Planning And Contracts

See [Clock And Reset Planning](references/clock-planning.md) for the bounded
proposal CLI, family documentation, explicit assumptions, live Tcl schemas and
qualification limits. Public additions are `ipiasm::set_clock_contracts {checks
resets sources fingerprint}` and `ipiasm::check_clock_contracts {}`. The former
registers design-local requirements; the latter performs fresh read-only checks.
Neither API creates/configures IP or proves routed timing.

# IPI Assembler Reference

This reference explains reusable mechanics behind `SKILL.md`. It contains no
board topology, target part, instance naming, address-map, or measured-throughput
policy. Those values come from the DesignSpec, selected board files, AMD product
documentation, and live Vivado properties.

## Local Tcl API inventory

This is the compact setup/requirements/gate index, not a native Vivado command
catalog. The complete **plan-call whitelist** is `ALLOWED_PROCS` in
[compile_assembly_plan.py](scripts/compile_assembly_plan.py); see
[ASSEMBLY_PLAN.md](ASSEMBLY_PLAN.md) for adapters and plan syntax. A public helper
listed here is not automatically permitted as a compiled plan operation. Preserve
the whitelist and full-plan provenance checks; do not wrap unsupported calls in raw Tcl.

Signatures below use Tcl formal-argument notation: `{arg default}` is optional,
`args` is variadic, and `{}` means no arguments. **R** reads data/design state;
**S** changes in-memory state; **M** writes hardware/project/files. Runtime calls
can launch child processes and populate a cache without changing the BD. Stage
names are assembler stages, not notebook cell numbers. A missing code gate is
not permission to bypass stage order, board ownership or the persistent attempt limit.

### Runtime and requirements

Sources: [ipcfg.tcl](../vivado-ip-configurator/lib/ipcfg/ipcfg.tcl) and the
[Python quick reference](../vivado-ip-configurator/references/ipcfg-library.md#python-quick-reference-and-local-api).

| Public signature | Return shape | Gate, effects and owner |
|---|---|---|
| `ipiasm::check_bram_backing {}` | `BRAM:OK` or `BRAM_FAIL` | R; cells stage; called by inventory. Checks every exposed AXI BRAM controller port reaches memory across hierarchy. Capacity and module behavior need separate evidence. |
| `ipiasm::check_cell_config {cell desired}` | `CELL_CONFIG:OK` or `CELL_CONFIG_FAIL` | R; cells stage; nonempty CONFIG dict, exact live readback with matching defaults accepted. Does not pin intent; use `ipcfg::require` for the final audit. |
| `ipiasm::check_source_files {files top}` | `SOURCE_FILES:OK` or `SOURCE_FILES_FAIL` | R; delivery stage; `files` is a nonempty flat dict of absolute source paths to expected SHA256 hashes. Checks file existence, exact bytes, native `get_files -quiet` project inclusion and `get_property top [current_fileset]` equality. Rejects malformed inputs and query/hash errors. Binds source-reviewed RTL to those files; does NOT independently prove HDL semantics, parse RTL, or replace source review plus native design validation. Planned delivery evidence is not proof of earlier-stage wrapper inclusion. |
| `ipcfg::python_status {}` | `PYTHON:OK ...` or returned `PYTHON_RUNTIME_FAIL:...` text | No BD/gate; setup diagnostic; may resolve/probe/cache. |
| `ipcfg::python_runtime {}` | Dict: `executable root source version score`; error on failure | No BD/gate; setup runtime/cache. |
| `ipcfg::python_exe {}` | Path string; error on failure | No BD/gate; path only, not the environment wrapper. |
| `ipcfg::python_exec {args}` | Child output string; Tcl error on failure | No BD/gate; script effects depend on arguments. Use the wrapper for every invocation. |
| `ipcfg::python_script {script args}` | Child output string; `PYTHON_SCRIPT_FAIL` error for invalid path | No BD/gate; checked absolute script path, same environment handling as `python_exec`. |
| `ipcfg::write_evidence {path data}` | Dict: `status RECORDED`, `bytes`, `evidence_path`; I/O errors raise | Writes a new absolute UTF-8 Tcl-dict file with `schema_version` and full `data`. Refuses overwrite; no BD changes and no implication of hardware PASS. |
| `ipcfg::capture {path script {expect {}}}` | Compact dict: status OK/FAIL, code, message counts, paths, excerpt, elapsed time; reservation/I/O errors may raise | Executes once in caller scope; writes full return/catch options and native snapshots. `path` must be absolute, new and end in `.tcldict`. Requires Vivado `write_messages`; no retries or suppression. Optional typed expected prefixes; OK alone is not hardware PASS. |
| `ipcfg::result_status {raw prefixes}` | PASS/FAIL/NA | R; shared typed predicate. Explicit NA only; failure, pending, partial and malformed statuses are rejected. |
| `ipcfg::set_mode {m}` / `ipcfg::get_mode {}` | `SUCCESS:mode=...` or `ERR:...` / bare mode string | S / R; setup; `m` is `benchmark` or `assemble`. |
| `ipcfg::api {{pat ""}}` | Workflow text or matching help lines; `API:no proc matching ...` | R; discovery only, not a machine-readable signature registry. |
| `ipcfg::require {cell d {reason ""} {tolerances {}} {board_override ""}}` | `REQ:pinned+applied ...`, `REQ_ERR:...` or `REQ_FAIL:...`; may be multiline | S+M; cells/configuration; pins intent **and applies it**, retaining pins on write failure. No assembler gate; uses guarded configuration attempts. A value the board file set is refused (`REQ_FAIL ... BOARD_OWNED`) unless `board_override` gives the spec statement that names it, e.g. `ipcfg::require $cell $d "camera0 format" {} {spec camera0: RAW12 at 1500 Mbps}`; the override is recorded and `ipcfg::audit_board` lists it. The audit is exact; `tolerances` `{CONFIG.KEY <abs>\|<n>% ...}` is only for a tolerance the DesignSpec states. |
| `ipcfg::requirements {{cell ""}}` | Newline-delimited display text, or `REQ:empty` | R; any loaded stage; empty cell selects all. **Not a dict/list of records**: do not split on whitespace or parse reasons/values as Tcl. |
| `ipcfg::requirements_data {{cell ""}}` | List of dicts: `cell key expected base reason`; empty list when absent | R; structured ledger access, preserving spaces/braces. No hardware writes. |
| `ipcfg::audit_requirements {{cell ""}}` | `REQ:OK n/n`, `REQ_FAIL:...`, or `REQ:empty ...` | R; readback after configuration and at final validation; empty is not PASS. |
| `ipcfg::apply_dict {cell d {allow_board 0}}` | `SUCCESS:...` or `CONFIGURE_FAIL:...` | M; cells/repair; board guard and attempt policy apply. Never use `allow_board=1` to bypass ownership without an explicit requirement. |
| `ipcfg::verify_stuck {cell d}` | List of missing/reverted keys; empty means all stuck | R; cells/repair readback; no assembler gate. |
| `ipcfg::verify_intent {cell reqmap {flag_no_change 1} {tolerances {}}}` | List of intent findings; empty means no findings | R; cells/repair; `reqmap` is flat `{key want baseline ...}`. Exact unless `tolerances` names the key. Use a true pre-write baseline; confirm documented defaults before disabling no-change flags. |

`ipcfg::api` currently supplies hand-maintained configurator workflow/help (including
native-capability topics), not an exhaustive inventory of local Tcl procedures.
Native Vivado help also does not establish local helper signatures. Before a stage,
check only its required public procedures with `info procs`, `info args` and
`info default`; do not dump bodies or call private underscore helpers:

```tcl
foreach command {::ipcfg::python_status ::ipcfg::python_exec ::ipcfg::requirements ::ipiasm::gate_status ::ipiasm::gate_inventory} {
   if {[llength [info procs $command]] != 1} { error "API_FAIL:missing $command" }
   set defaults [dict create]
   foreach argument [info args $command] {
      if {[info default $command $argument value]} { dict set defaults $argument $value }
   }
   puts [dict create proc $command args [info args $command] defaults $defaults]
}
```

This checks existence/signatures, **not** mutation semantics or library identity.
Match the recorded loaded-library path/fingerprint and project/BD identity too;
see [setup and lifecycle](SKILL.md#setup-and-lifecycle).

### Assembly and gates

Source: [ipiasm.tcl](lib/ipiasm.tcl). Status text is not a Tcl dict unless explicitly
listed as one. Inspect the exact verdict and retained diagnostics; `catch == 0`
does not mean PASS. `NA`, empty, partial and `*_FAIL` are not interchangeable.

| Public signature | Return shape | Gate, effects and owner |
|---|---|---|
| `ipiasm::init {}` | `SUCCESS:ipiasm ready ...`, `SUCCESS:ipiasm resumed ...` or `ASM_FAIL:...` | S at first setup; preserves same-bound-design state on repeat; refuses an implicit context switch. |
| `ipiasm::begin_design {}` | Same success shapes as `init`, or `ASM_FAIL:...` | S; explicit new-project/BD transition clears previous in-memory state, never the disk attempt ledger. Same context resumes. |
| `ipiasm::context_identity {}` | List: normalized project directory, project name, BD name; empty if unavailable | R; identity query, not a hardware audit. |
| `ipiasm::api_inventory {{pattern *}}` | Dict: public proc name to list of argument dicts (`name`, optional `default`) | R; glob-filtered local signatures. Mutation semantics remain in this table, not inferred from names. |
| `ipiasm::check_wrapper_boundary {rtl_file {wrapper ""}}` | `BOUNDARY:OK ports=N`, `BOUNDARY:NA ...` or `BOUNDARY_FAIL:` with each missing name and suggested renames | R; every named connection of the RTL instance of `<BD>_wrapper` must exist on the wrapper (scalar ports, interface members, or a buffered `X_io`). |
| `ipiasm::name_port {current wanted}` | `PORT_NAME:OK ...` or `PORT_NAME_FAIL:...` | M; renames one exported scalar or interface port in place. Re-running is safe; refuses when both names exist. |
| `ipiasm::check_ps_noc_masters {}` | `PS_NOC:OK coherent_ports=N`, `PS_NOC:NA ...` or `PS_NOC_FAIL:` listing each problem | R; connect and final stages. Every PS NoC manager port marked `CONFIG.MY_CATEGORY ps_cci` must connect to one NoC instance with identical CONNECTIONS once any is used (PG450, PG352). Also run inside `final_contract_gate`. |
| `ipiasm::create_port {name dir {type ""} {pin ""} {properties {}}}` | `PORT:OK name=... dir=... [pin=...]` or `PORT_FAIL:...` | M; after the inventory gate. Creates one exactly named scalar top-level port (dir I/O; type clk/rst/intr/data/ce), applies CONFIG properties and optionally connects it to one cell pin. Reuses an existing port with the same direction; refuses a direction mismatch. |
| `ipiasm::api {{pat ""}}` | Text: numbered build-flow steps with gate tokens and helper names; with a pattern, `ipcfg::help` output for matching `ipiasm::` helpers | R; an index for finding a helper, not the rules — those stay in SKILL.md. |
| `ipiasm::api_preflight {calls}` | Dict: `status checks failures`; status PASS/FAIL | R; calls are a list of `{procedure argument-list}` pairs. Checks existence and positional/optional/variadic arity; does not execute calls or validate hardware semantics. |
| `ipiasm::policy {{p ""}}` | `POLICY:...` or `POLICY_FAIL:...` | R if omitted, S otherwise; setup; `attended`/`unattended`. |
| `ipiasm::gate_status {}` | `GATES:plan=... board=... inventory=... qos=...` with reasons | R; any stage; reports in-memory state, not persisted recovery evidence. |
| `ipiasm::gate_override {g reason}` | `GATE:...` or `GATE_FAIL:...` | S; only a user-approved bypass with a nonempty reason, never routine recovery. |
| `ipiasm::stage {name}` | `STAGE:...` or `GATE_FAIL:...` | S; stage boundary/timing; initialized clock/connect/qos/external/address/visibility/validate/deliver stages require inventory. Does not execute that stage. |
| `ipiasm::timings {}` | `TIMINGS:total=...` text | S; report; updates current stage accounting, not a pure read or agent-time measurement. |
| `ipiasm::checkpoint {tag}` | `CHECKPOINT:...` or `CHECKPOINT_FAIL:...` | M+S; only after stage PASS; saves the open BD and updates timing. Clock-or-later tags check undriven clocks; never closes/reopens the BD. |
| `ipiasm::register_board_repo <dir>` | `BOARD_REPO:OK repo=.. boards={..} new_parts={..} match_current_part={..}` or `BOARD_REPO_FAIL:...` | M+S; appends absolute board-file `<dir>` to `set_param board.repoPaths` and the open project's `BOARD_PART_REPO_PATHS`; fails unless `get_board_parts` lists a board named by a `board.xml` under it. |
| `ipiasm::require_board_preset {{hint ""}}` | `READY:...` or `ASK(board):...` | M+S; board stage; may select project `BOARD_PART`; sets board gate from result. |
| `ipiasm::board_interfaces {{pattern *}}` | `BOARD_INTFS:n {...}` display text | R; board discovery; selected board needed for meaningful results. |
| `ipiasm::check_irq_paths {expected}` | `IRQ_PATHS:OK n=.. covered={labels}` or `IRQ_PATHS_FAIL:k/n ...` | R; after interrupt wiring; `expected` is `{sink want ?label?}` records: sink `<cell>/<pin>` or `<cell>/<pin>[<bit>]`, want a source pin, top-level port, `CONST:<v>` or `OPEN`. Traces through concat/slice/constant and hierarchy. |
| `ipiasm::check_interface_routes {edges}` | `INTERFACE_ROUTES:OK n=...` or `INTERFACE_ROUTES_FAIL:...` | R; after interface wiring; nonempty list of two-element exact interface-pin/port path pairs selected from the resolved plan. Resolves ports before pins, rejects glob metacharacters, and compares full paths with surrounding slashes trimmed. Proves each target is a native `find_bd_objs -relation connected_to -thru_hier` peer of its source, not just on a matching net name. Rejects malformed, empty, missing or disconnected endpoints and query errors. Direct endpoint provenance only, not general reachability through IP, NoC routing, address visibility or QoS. Whitelisted for later-stage plan checks; no gate or hardware mutation. |
| `ipiasm::set_irq_contracts {expected}` | `IRQ_CONTRACTS:SET slots=n` or `IRQ_CONTRACT_FAIL:...` | S; registers the table once per design; a different table is refused. |
| `ipiasm::check_irq_contracts {}` | `IRQ_PATHS:...` or `IRQ_CONTRACTS:NA not registered` | R; re-traces the registered table; the final gate runs it. |
| `ipiasm::record_evidence {label categories result}` | `EVIDENCE:OK recorded ...` or `EVIDENCE_FAIL:...` | S; keeps a passing result for DesignSpec contract categories the final gate does not check itself; stale once the design changes. |
| `ipiasm::create_cell {vlnv cell {d {}}}` | `SUCCESS:...`, `CONFIGURE_FAIL:...` or `GATE_FAIL:...` | M; cells; requires board gate; uses configurator attempt guard. |
| `ipiasm::instantiate_n {vlnv base n {d {}}}` | `SUCCESS:instances={...}`, `MULTI_FAIL:...` or `GATE_FAIL:...` | M; cells; requires board gate; creates/configures `${base}_0` through `${base}_${n-1}`. Failure need not undo earlier instances. |
| `ipiasm::gate_inventory {wants}` | `INVENTORY:OK ...` or `INVENTORY_FAIL:...`, plus per-IP lines in return and stdout | R+S; inventory stage; flat `{ip count ...}` from spec. Opens/closes inventory on count comparison; malformed input returns failure, never treat a prior gate as proof. |
| `ipiasm::set_interface_contracts {contracts}` | `INTF_CONTRACTS:SET ...` or `INTF_CONTRACT_FAIL:...` | S; after cells, before connecting; replaces registry with a list of dicts (`src`, `dst`, clock/reset metadata). No hardware write. |
| `ipiasm::preflight_interface_contracts {{contracts __registered__}}` | `INTF_PREFLIGHT:OK`, `INTF_PREFLIGHT_FAIL:...` or `INTF_PREFLIGHT:NA`, with details | R; configured cells before connect; default reads registry; no contracts means NA, not coverage. |
| `ipiasm::check_qos {{allow {}}}` | `QOS:OK`, `QOS_FAIL:...` or `QOS:NA` | R+S; qos stage; updates QoS subgate, does not configure traffic. Do not use `allow` to waive unused-route declarations. |
| `ipiasm::check_noc_solution {{want_memory 1}}` | `NOC:OK`, `NOC_FAIL:...` or `NOC:NA` | R+S; qos/address checks; updates NoC subgate. `0` omits mapped-memory requirement for pre-address checks only. |
| `ipiasm::check_noc_pre_address {{want_mc 0}}` | `NOC_PRE:OK` or `NOC_PRE_FAIL:...`, with child results | R+S; qos before address; checks traffic, published memory and solution, updates subgates. Pass spec controller count; not a final address check. |
| `ipiasm::auto_address {}` | `SUCCESS:auto_address ...`, `ADDR_FAIL:...` or `GATE_FAIL:...` | M; address stage; requires inventory and, with NoC, QoS; assigns addresses and checks DRAM reachability. |
| `ipiasm::assemble_from_plan {plan_path {stage ""}}` | `ASSEMBLY_PLAN:PASS ...` plus provenance, `ASSEMBLY_PLAN:PARTIAL ...`, or `ASSEMBLY_PLAN_FAIL:...` | M+S; empty stage runs the full plan; named stage respects ordered prerequisites and the open BD, resuming them from the newest receipt in a new Vivado session. No automatic rollback or ledger-only operation skips. |
| `ipiasm::stage_execution_status {}` | Dict: status, stage, operation, checks, raw operation results, diagnostics, timing and evidence path | R; latest execution only; retain it before the next call. See the complete schema in ASSEMBLY_PLAN.md. |
| `ipiasm::plan_execution_status {{plan_path ""}}` | `PLAN_EXECUTION:OK ...` or `PLAN_EXECUTION_FAIL:...` | R plus Python hash/cache use; provenance check; omitted path uses the recorded executed plan. With a path and no in-process PASS, a staged build passes as `PLAN_EXECUTION:OK ... staged=1` when the newest receipt shows every stage complete; a failure names the stages still to run. |
| `ipiasm::feasibility_fingerprint {sources}` | Dict of tool, part, board part and source-file hashes; errors on missing sources | R; sources maps spec/board/ip roles to absolute file lists. Include every relevant definition file. |
| `ipiasm::check_feasibility {checks phase sources expected_fingerprint}` | `FEASIBILITY:OK ...` or `FEASIBILITY_FAIL:...` | R; typed exact-object readbacks with stage ownership and fingerprint matching. Native IRQ22/NoC boundary fixture qualified on Vivado 2026.1; not full-system acceptance. |

For list-safe calls, keep each value/reason as one Tcl argument:

```tcl
set wanted [list CONFIG.PARAM_A {value with spaces}]
puts [ipcfg::require $cell $wanted {DesignSpec requirement with [literal] characters}]
puts [ipcfg::requirements $cell]
puts [ipcfg::audit_requirements $cell]
```

Replace the illustrative CONFIG key with the documented live property. The first
call **writes**; do not run it as inventory discovery. Readback of pinned values
does not replace interface, board or stage acceptance checks.

Already-satisfied configuration is checked before reserving a mutation attempt.
Board replay preserves recorded ownership; an already-bound cell without ownership
evidence returns `BOARD_FAIL:OWNERSHIP_UNKNOWN`, not a fabricated board audit PASS.
Read-only checks on satisfied exhausted cells do not authorize further writes.

The compiled executor runs `api_preflight` for all selected-stage calls before
any operation. For manual stage drivers use the same check explicitly:

```tcl
set calls [list [list ipcfg::require [list $cell $wanted {requirement reason}]]]
set check [ipiasm::api_preflight $calls]
if {[dict get $check status] ne "PASS"} {error "API_FAIL:[dict get $check failures]"}
```

## NoC traffic ownership

For each NoC path, identify the ingress that owns QoS by reading
`CONFIG.HAS_QOS`, `CONFIG.COMPUTED_STRATEGY`, and `INI_STRATEGY`. A value written
to a non-owning ingress may be accepted but ignored by the compiler.

Declare each master's total requirement; do not divide it by the number of
interleaved memory controllers. Use:

```tcl
ipiasm::bw_video <width> <height> <fps> <bits_per_pixel> <overhead_percent>
ipiasm::bw_mipi <lanes> <line_rate_mbps>
ipiasm::bw_axi <clock_mhz> <data_width_bits> <utilization_percent>
```

The arguments must come from the DesignSpec or configured IP. Apply them with
`ipiasm::apply_qos_plan` or `ipiasm::set_qos`; do not hand-write
`CONFIG.CONNECTIONS`. Run `check_qos`, `check_noc_pre_address`, and
`check_noc_solution` before assigning addresses.

Per-destination NoC values must retain the nested list shape required by the IP.
The helpers preserve that shape and merge sibling destinations. A successful
property write is not proof that the compiler built the requested route.

Set `initial_boot` exactly as the requirements state. `[BD 41-3285]` ("initial boot
should not be enabled for paths to or from ISP/VCU") is a warning whose documented
meaning is limited to segmented configuration (Vivado Segmented Configuration
tutorial: otherwise ignored by `write_device_image`). It is not grounds to drop the
requirement or stop a stage; see SKILL.md 3b.

## Addressing and visibility

Place only windows explicitly fixed by software, boot, or hardware contracts.
Then let `assign_bd_address` place everything else. Automatic placement decides
offsets but not which masters should see a slave.

Use `restrict_master`, `restrict_window_to_spaces`, and `check_window_scope` to
implement the DesignSpec visibility policy. `check_overmap` requires a
spec-derived limit; it must not compare against a remembered reference count.

`check_window_scope` needs an offset and a range, so it can only assert a *fixed*
window. For a slave placed automatically, assert the rule with `check_visibility`,
which compares the set of master spaces that actually reach the slave against an
exact allow-list and needs no address. It treats "never mapped" and "mapped then
excluded" as the same answer, because they are: a design can satisfy a visibility
rule with zero exclusions, and an exclusion count is not evidence either way.

After changing NoC connectivity or QoS, repeat address assignment and all
visibility checks because Vivado can invalidate existing mappings.

## Board-owned configuration

Resolve a requested board through `get_board_parts`, select `BOARD_PART`, and
inspect `get_board_part_interfaces`. Bind every applicable board interface
before overriding any IP property.

Board files and their XDC data own:

- processing-system presets and fixed IO;
- external-memory controller topology and physical interfaces;
- package pins, banks, and differential clock locations;
- board-connected protocol selections and interface presets.

If the requested board is absent or ambiguous, stop with `ASK(board)`. Do not
select another board that shares the silicon, recover values from an unrelated
design, or invent a preset.

Functional settings not guaranteed by board metadata belong in the DesignSpec.
Examples include camera operating point, application bandwidth, software-fixed
addresses, endpoint identity, and required peripheral behavior.

## Hardened interfaces

Hardened blocks can have physical connectivity constraints that ordinary PL
interfaces do not. Discover the relevant subsystem automation from the current
catalog and inspect its schema before applying it. If automation creates a
topology that violates the DesignSpec or fails a structural gate, remove that
topology and choose a legal connection using live interface direction,
`can_connect`, clock association, and graph reachability.

For an AI Engine configuration slave:

1. discover a legal NoC master from the live design;
2. reject a path that loops the engine's own master back to its slave;
3. set required endpoint categories from product documentation;
4. associate the NoC clock with the selected interface;
5. verify the resulting net with `check_aie_config`.

No instance name, NoC port number, address, or hierarchy boundary is implied by
this procedure.

## Clock and reset domains

Every active interface must have one resolved clock domain. A raw asynchronous
reset may assert asynchronously, but its release must be synchronized to the
clock that drives the associated logic.

For generated clocks, use one `proc_sys_reset` per domain and qualify
`dcm_locked` from the generating clock resource when a lock output exists.
Crossing AXI4-Stream between unrelated clocks requires an explicit CDC bridge;
matching nominal frequencies alone does not prove the domains are synchronous.

The MIPI clock gate derives throughput from the configured line rate, lane
count, pixel format, and pixels per clock. Device-family margin comes from
documented architecture metadata, never an exact part string.

## Validation evidence

`validate_bd_design` can return success while emitting critical warnings or
while a compiler-specific route is unresolved. Delivery therefore requires:

- structural clock/reset and interface-domain checks;
- NoC solution and memory reachability checks when a NoC is present;
- configuration requirement audit;
- exact fixed-window visibility checks;
- generated targets, wrapper, rebuild Tcl, and a non-empty status artifact;
- a final design fingerprint after all mutations.

Classify diagnostics by documented message meaning and current graph evidence.
Do not preserve a one-design warning allowlist or corrective wiring recipe.

## Hierarchy is out of reach

`ipiasm::*` has no hierarchy-creation operation and never calls `group_bd_cells`:
every cell it makes lands in the flat top-level namespace. A design whose
reference implementation nests cells under hierarchies is therefore not
reproducible name-for-name, and two cells that share a leaf name in different
hierarchies (`/video/axi_gpio_0` and `/ctrl/axi_gpio_0`) cannot both exist here.
Give each one a distinct semantic name instead (standing rule in SKILL § *Cells*)
and record the flattening as a divergence rather than trying to emulate it.

## Speed rules (in priority order)

1. One `source` per stage; batch every read-only check into the stage script's tail.
   The cost of a `vivado_execute` is the model turn around it, not the Tcl: a stage
   whose calls average under ~2 s spent nearly all of its wall clock waiting on
   inference, so **a call that returns in a second is a call that should have been
   part of the previous one**. Group every query that does not depend on the result
   of the one before it. Print one structured line per check rather than raw
   `report_*` output, so a batched tail stays parseable and small.

   **Never call `source` with `verbosity: full`.** Vivado echoes *every command a
   sourced script executes* back into the captured console, each prefixed `# `, so
   sourcing the two skill libraries returns both files in full — measured at
   **530 KB**, which the client then spills to a temp file and you pay turns
   reading back. This is not under the script's control: `echo: false` does not
   suppress it and neither does `capture_log: false` (both measured 2026-09-29).
   The only levers are `verbosity: digest` or `errors`, which truncate server-side,
   and keeping `source` to the stage script rather than the libraries. Load the
   libraries once per session and check `info commands` afterwards instead of
   re-sourcing to find out whether they are loaded.
2. Consolidate unresolved requirement questions during planning. Resolve implementation
   choices with bounded scratch experiments; ask later if new evidence reveals a genuine
   requirement conflict. Never hide such a conflict to meet a timing target.
3. Cache-first per cell: `ipcfg::cache_get` before doc search. Seed
   `cache/learned_params.json` with verified discovery facts only, not design values or
   reference wiring. Recheck applicability to the tool build, device, IP version and
   relevant repository/preset content before reuse. An existing parameter name alone
   does not prove unchanged behavior. Keep run-specific evidence under the output directory.
4. **Force a validate once, and never write `validate_bd_design -force` yourself.**
   Use `ipiasm::validate` (unforced) for verdicts and let `check_noc_legality` be
   the one place that forces, because forcing re-runs the NoC compiler.
   `check_noc_legality` fingerprints the design and skips the force when nothing
   has been added, wired or mapped since the last forced pass, so calling it again
   is cheap — but a hand-written `-force` bypasses that and pays full price every
   time. `force 0` reads what was already emitted and re-runs nothing. Also
   `verify_intent` once per cell, not per property.

   Two related traps in the same area: the server silently **skips a write command
   that already succeeded within the last 2 minutes**, so a repeated forced
   validate can report success having re-run nothing. And it asks you *not* to wrap
   commands in defensive `if {[catch {...}]}` — it already returns `exit_code`, the
   error text and a `recovery_hint`. Use `catch` only for genuine fallback logic.
5. Checkpoint after every **gate PASS** via `ipiasm::checkpoint` only — never reopen
   the BD to persist state. A failed run in a **new** session resumes at
   `open_project` once, `open_bd_design` once, then `gate_status`; that is recovery
   only, not normal stage flow.
6. Reconcile (cheap queries) before validate (expensive), and never validate a
   design whose inventory gate is shut. `check_overmap` belongs here: half a second
   of reading segment names, and what it catches otherwise surfaces as a
   SmartConnect route conflict out of a full validate.
7. Record elapsed time, MCP round trips, full validations and repairs per stage. Compare
   cold and warm discovery separately under the same environment. Claim a speedup only
   after measurement. Check changed interfaces/configuration locally, then rerun all
   required downstream checks affected by clocks, connectivity, QoS or addressing.
   Keep the independent final validation and delivery guard; fewer checks is not success.

## Structured returns (parse these; never eyeball)

`SUCCESS:` · `SKIP:` · `AUTO:`/`MANUAL:` · `*_FAIL:<detail>` ·
`GATE_FAIL:<stage> requires '<gate>'` · `GATES:board=0|1 inventory=0|1 qos=0|1` ·
`INVENTORY:OK n` / `INVENTORY_FAIL:short={} over={}` · `STAGE:<name>` · `TIMINGS:total=..` ·
`CHECKPOINT:<tag>` · `POLICY:attended|unattended` ·
`BOARDS:n` · `READY:board_part=..` / `ASK(board):..` · `BOARD_INTFS:n` · `HOOKS:OK|HOOKS_FAIL` ·
`RESET_SYNC:WIRED|RESET_SYNC_FAIL` · `CLOCK_DOMAIN:OK|FAIL` · `VIDEOCLK:OK|FAIL` ·
`CLOCK_PREFLIGHT:OK proven=N pending=N` / `CLOCK_PREFLIGHT_FAIL:<n> defect(s): ..` ·
`CLOCK_CONTRACTS:SAVED|RESTORED` / `CLOCK_CONTRACTS_SAVE_FAIL|CLOCK_CONTRACTS_LOAD_FAIL:<detail>` ·
`PREFLIGHT:OK|PREFLIGHT_FAIL:<reason>` · `INTF_PREFLIGHT:OK|INTF_PREFLIGHT_FAIL:<detail>|INTF_PREFLIGHT:NA` ·
`DISCONNECT_REFUSED:<reason>|DISCONNECT_FAIL:<detail>` ·
`FABRIC:coupled|none` · `MEM:OK|MEM_FAIL` · `QOS:OK|QOS_FAIL|QOS:NA` · `NOC:OK|NOC_FAIL|NOC:NA` ·
`NOCLEGAL:OK|NOCLEGAL_FAIL|NOCLEGAL:NA` · `AIE:OK|AIE_FAIL|AIE:NA` ·
`SUCCESS:auto_address` / `ADDR_FAIL` · `NAMED_WINDOWS:placed=N ESCALATE={..}` ·
`WINDOW_SCOPE:OK required=N present=N extra=0 moved=0` ·
`VISIBILITY:OK slave=.. allowed=N` / `VISIBILITY_FAIL:extra={..}` ·
`FINAL_CONTRACT:PASS` / `FINAL_CONTRACT_FAIL` ·
`PLAN_EXECUTION:RECORDED|OK` / `PLAN_EXECUTION_FAIL` ·
`DELIVERY_GUARD:OK` / `DELIVERY_GUARD_FAIL` ·
`DELIVERY_CONTEXT:OK` / `DELIVERY_CONTEXT_FAIL` ·
`CLOSED`/`NEEDS_USER` · `VALIDATE:PASS|FAIL` · `ASSEMBLY:CLEAN|GAPS|BLOCKED` · `REQ:OK|REQ_FAIL` ·
`RECONCILE:MATCH|DIVERGED` · `Q(<kind>): <gap> | options: a ; b ; Other` (one per AskQuestion).

## MCP session mechanics

**Do not sleep after a call that has already returned.** `vivado_execute`
hands the command off *only* when both of these hold: it runs past **5
minutes**, and it is one of `synth_design`, `place_design`, `route_design`,
`opt_design`, `phys_opt_design`, `write_bitstream`, `open_checkpoint`,
`read_checkpoint`, `open_project`, `source`, `wait_on_run`. Everything else
returns its result in the tool call, however long it took.
`validate_bd_design` is not on that list, so a NoC compile inside
`check_noc_legality` comes back in the result — waiting on it buys nothing.

**The client can give up first.** A client may end a tool call after a fixed time
(Claude Code: 60 s) with an error and no result while Vivado keeps running the
command (`vivado_status` shows `is_detached=true`). Measured on 2026.2, these calls
can take longer than that:

| Call | Typical time | Why |
|---|---|---|
| `ipiasm::reference_search`, `ipiasm::ced_reference` | 60–70 s, whichever runs first | the first `get_example_designs` in a Vivado process loads the CED catalog (about 60 s, however filtered); later calls take milliseconds |
| `ipiasm::example_reference` | 60–120 s | generates the IP's example project |
| `ipiasm::assemble_from_plan` (whole plan) | 20–100 s | `instantiate_ced` alone takes 7–15 s once the catalog is loaded |
| `ipiasm::deliver` | 25–60 s; 4–5 min on Versal with NoC/AI Engine | output products (about 90 s there) and XSA (about 120 s) |

Each of these, and `scratch_bind`, marks itself running in `<out>/logs/<proc>.running`
and writes its return value to `<out>/logs/<proc>.result.txt` when it ends
(`assemble_from_plan`: beside the plan). After a client timeout, do not send the call
again and do not guess a sleep (runs that slept 75–340 s spent a quarter of their time
waiting). Run `python3 <skill>/scripts/await_result.py <out>` in the shell (it also reads
`<out>/*/logs`, so `deliver <out>/delivery` is covered): it returns
as soon as the call ends and prints its result, or prints `STILL_RUNNING` after 100 s;
run it again then. Work that does not need Vivado (`vivado_doc_search`, the DesignSpec)
can go first. Wrap one long call per tool call: a Tcl proc that runs several stages in
one call leaves the waiter only the last stage's result.

In this skill only two things can therefore hand off: a `source` of a stage
script big enough to run past 5 minutes (cell creation on a large spec will; stage-4
addressing at ~150–200 s will not), and synthesis. **When you do get a hand-off** — the response says `status: running` — sleep for the work you think is
left, then call `vivado_status` (`action='session'`) **once** and read
`is_command_running` / `last_completed_code` (0 = success). Do not poll in a
loop: the server tracks poll frequency and answers throttled from cache, so a
tight loop reads staler data than one well-timed check. At 30–60 s
granularity, sleep again and check again. `vivado_status` is safe to call
while `vivado_execute` is busy, so the check never queues behind the command.

**Read your own return, and read it from the tool result.** `vivado_execute`
defaults to `verbosity: digest`, which on a large output returns counts, ERROR
lines and a `log_file` path *instead of* the output — your stage's status line
included — and sets `output_truncated`. Pass **`verbosity: full`** on a stage
`source` so the structured return comes back intact. If a result does come
back truncated, recover it with the **`vivado_log_messages`** tool (`filter`,
`max_messages`; safe to call while `vivado_execute` is busy), not by shelling
out to `grep` the log file. Reaching for bash to read a Vivado log means you
truncated your own output and then paid a second round trip to undo it.

## Interface contracts

Register interface contracts **after configuring the actual IP instances and
before connecting them**. Each contract names `src`, `dst`, `src_clk`,
`dst_clk`, `src_rst`, and `dst_rst`; add `src_vlnv`/`dst_vlnv` patterns when a
reference design determines the IP choice. Example:

```tcl
ipiasm::set_interface_contracts [list [dict create \
  src camera/video_out dst isp/TILE0_ISP_MIPI_VIDIN0 \
  src_metadata_intf camera/mipi_csi2_rx_subsyst_0/video_out \
  src_vlnv xilinx.com:ip:mipi_csi2_rx_subsystem:* \
  dst_vlnv xilinx.com:ip:visp_ss:* \
  src_clk camera/video_aclk dst_clk isp/tile0_pl_isp_vidin0_clk \
  src_rst camera/video_aresetn dst_rst isp/tile0_pl_isp_rstn]]
puts [ipiasm::preflight_interface_contracts]
```
For a hierarchy boundary, `src_metadata_intf`/`dst_metadata_intf` names the
underlying configured IP interface that owns the metadata while `src`/`dst`
remain the connectable edge. The preflight reads `ASSOCIATED_BUSIF`,
`ASSOCIATED_RESET`, `FREQ_HZ`, `CLK_DOMAIN`, and the live nets from those
configured IPs. A bound explicit clock or reset path fills metadata omitted at
a hierarchy boundary; contradictory IP metadata still fails. Same clock/reset
nets mean a direct synchronous edge. Different domains fail unless the
contract explicitly describes a real CDC bridge with `allow_cdc 1`. Never
infer CDC from interface type or cache a legal-target result across a topology
or metadata mutation. `ipiasm::validate` runs all registered contracts before
invoking expensive forced validation.

Enforcement is in the mutation helpers, not only this instruction:
`connect_intf` refuses a clocked AXI/AXI-Stream edge with missing or
contradictory metadata. `disconnect_intf_edge` protects a proven same-domain
edge from speculative CDC insertion. Its force argument is reserved for a
separately justified non-CDC topology change, not for overriding the same-domain
verdict.
