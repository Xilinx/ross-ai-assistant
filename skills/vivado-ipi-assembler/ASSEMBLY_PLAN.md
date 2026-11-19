# Parameter Coverage

For a DesignSpec-driven workflow, compile with `--spec <design_spec.json>` in both
planning and the cell stage. The compiler also discovers an adjacent `design_spec.json`
without the flag. Managed execution includes this spec's hash in its compilation cache
and stage identity. A bound `binding_context.design_spec_sha256` (the SHA-256 of the spec
file's bytes, recorded by `bind_design_spec.py bind --spec`) must match the file; missing or
changed bound specs fail before execution. Bind against the same file you compile with.

The plan's `parameter_coverage` object covers every
leaf under every block's `parameters`, using JSON pointers into the supplied spec:

```json
{
  "parameter_coverage": {
    "/blocks/0/parameters/line_rate_mbps": {
      "value": 1500,
      "disposition": "hardware",
      "reason": "Receiver line rate in Mb/s",
      "checks": ["camera_config_readback"]
    }
  }
}
```

Each hardware entry references existing `ipiasm::check_*` call operation IDs.
For CONFIG requirements use `check_cell_config` with the exact property/value map.
Nested objects and arrays require leaf-level entries, not a blanket parent entry.
Empty objects and arrays are leaves too. A genuinely external parameter instead uses
`disposition: "external"`, a nonempty `owner`, and its source-backed `reason`.
External entries are declarations, not hardware verification. Review them against the
source requirements; do not classify missing hardware work as external.

When a spec value is not itself a configuration value, cover it where it is really proven:

- **A setting the IP keeps at its default** still needs its `CONFIG.*` key, with that value,
  in a `check_cell_config`. A proven configuration records only what was changed, so add
  that readback yourself.
- **A wiring fact**, such as which bits or ports connect where, is proven by the connect
  stage's structural checks (`check_required_ports`, `check_wrapper_boundary`,
  `check_irq_paths`). Map it there. A structural readback names no values, so it is not
  asked to carry one. It must still read back the thing in question: `check_wrapper_boundary`
  sees only the ports the RTL top uses, so a boundary port the RTL leaves alone is proven by
  listing it in `check_required_ports`.
- **A pin the design leaves unused on purpose** is declared in the `validate` stage with
  `ipiasm::declare_open {<pins>} <reason citing the DesignSpec>`, so `assembly_report`
  can reach `ASSEMBLY:CLEAN`.
- **A value whose hardware form differs**, such as an index the IP holds as an enable flag
  (`CONFIG.C_<n>_ENABLE true`) or a nominal frequency realized as an exact one, names that
  form in `asserted_as`: a `CONFIG.*` key/value object, or a scalar. One of the entry's
  checks must carry it.
- **A fact no plan operation sets**, such as a core count the device fixes, a value the
  board preset owns, or an address a user-supplied map applies after the plan, is genuinely
  external. Name that owner and, in the reason, the later check that observes it. This is
  not missing hardware work: nothing in the plan could set it.

```json
"/blocks/3/parameters/enabled_tiles/0": {
  "value": 0, "disposition": "hardware", "reason": "tile 0 is enabled",
  "checks": ["isp_readback"], "asserted_as": {"CONFIG.C_TILE0_ENABLE": "true"}
}
```

`--spec` reports every coverage problem at once, so one repair pass can fix them all.
`scripts/plan_requirements.py --coverage-out <file>` writes every leaf this map needs, with its
exact source value, before you write the template (proven leaves are left to `proven_to_plan.py`).

The checker rejects missing/unknown pointers, stale source values, missing rationales
and nonexistent readback operations. It does not prove the semantic correctness of a
mapping or that those operations ran. Execute the named checks at their owning stages
and preserve the receipts before claiming a stage pass.

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IP selection and assembly-plan format

IP selection (called binding by the scripts) resolves functional requirements to
IP available in the installed Vivado release. Run discovery in a scratch project
after requirement validation, before declaring the implementation plan ready.

## Binding intent

```json
{
  "schema_version": 1,
  "blocks": {
    "camera": {
      "spec_block": "camera_receiver",
      "vlnv_candidates": [
        "xilinx.com:ip:mipi_csi2_rx_subsystem"
      ],
      "interfaces": {
        "video_out": ["video_out", "M_AXIS_VIDEO"]
      },
      "pins": {
        "interrupt": ["csirxss_csi_irq", "interrupt"]
      },
      "properties": {
        "lanes": ["CONFIG.CMN_NUM_LANES", "CONFIG.C_DPHY_LANES"]
      }
    }
  }
}
```

Each role becomes one cell. A role that is the only one on its `spec_block`, or is named
like it, gets the block's name. When several roles serve one block (a controller and the
memory behind it), only one can carry that name: give it `"instance": "<cell>"`, and every
other role on the block is named for the role. Binding fails if two roles would create the
same cell.

Candidate VLNVs are always version-free. Alias lists are ordered preferences.
If none exists in the live snapshot, binding fails.
Candidates are tried in the supplied order; versions within a candidate use descending
Tcl dictionary order (so 1.10 precedes 1.9). Failed creation tries the next version or
candidate, recording the error. Catalog presence alone is not a successful selection.
Incomplete probe output and snapshots containing creation errors are rejected.

For capability checks, use an object instead of a name-only alias list:

```json
{
  "interfaces": {
    "control": {
      "aliases": ["S_AXI", "s_axi"],
      "vlnv": "xilinx.com:interface:aximm_rtl:1.0",
      "mode": "Slave"
    }
  },
  "pins": {
    "events": {"aliases": ["events"], "direction": "I", "width": 8}
  }
}
```

Values above illustrate the format, not defaults. Derive requirements from the current
design and documentation. Protocol VLNV, mode, direction, type and width are exact checks;
unknown keys fail instead of silently skipping a misspelled check. Legacy alias lists
remain supported, but prove names only. A candidate that creates successfully but fails
capability checks is rejected at binding; revise the candidate list from that evidence.

The generated probe observes default cells. Use `vivado-ip-configurator` scratch experiments
for configuration-dependent ports, creation-time parameters and connection-derived widths;
record those results separately in the plan. Do not declare configured capability from a
default snapshot or substitute manually invented snapshot values. Supplied RTL and
integrated functions require their own supported assembly operations; this catalog probe
does not package RTL or prove those operations exist.
The resolved instance name comes from the matching DesignSpec block's
`instance_name`, `instance`, or semantic `name` field. Binding intent does not
prescribe design instance names.

## Assembly-plan template

```json
{
  "schema_version": 1,
  "name": "camera_system",
  "stages": [
    {
      "name": "cells",
      "operations": [
        {
          "id": "create_camera",
          "op": "call",
          "proc": "ipiasm::create_cell",
          "args": [
            "${bind.blocks.camera.vlnv_unversioned}",
            "${bind.blocks.camera.instance}",
            {}
          ],
          "expect": ["SUCCESS:", "SKIP:"]
        }
      ],
      "checkpoint": true
    }
  ]
}
```

Placeholders resolve from:

- `${bind.blocks.<role>.vlnv}`
- `${bind.blocks.<role>.vlnv_unversioned}`
- `${bind.blocks.<role>.instance}`
- `${bind.blocks.<role>.interfaces.<semantic-name>}`
- `${bind.blocks.<role>.pins.<semantic-name>}`
- `${bind.blocks.<role>.properties.<semantic-name>}`
- `${env.vivado_version}`, `${env.part}`, `${env.board_part}`

A placeholder may also be embedded in a longer string, e.g.
`"${bind.blocks.camera.instance}/video_out"`. Every `${bind.…}` or `${env.…}` must resolve to
a scalar, or binding fails with `unresolved placeholder`; other `${…}` text is left alone.

### Proven configurations (`provenance`)

When Stage 2 exported proven cells (`proven/*.json` from `ipcfg::export_proven`),
`scripts/proven_to_plan.py` merges them into `proven_ip_config.json` and rewrites the
template's cell operations for each proven role before binding:

- `proven_create_<role>`: `ipiasm::create_cell` (only for `apply_via: create_cell`)
- `proven_configure_<role>`: `ipcfg::require <inst> <requested> <reason>` (same condition)
- `proven_check_<role>`: `ipiasm::check_cell_config <inst> <requested>`

PS-wizard and board-preset cells keep your own create/configure operations; only their
readback is replaced. Each emitted operation carries
`"provenance": {"source": "proven_ip_config.json", "role": "<role>", "sha256": "<cell hash>"}`
and the plan records `"proven_ip_config": {"path", "sha256", "cells"}`, where `cells` embeds
each role's `role`, `apply_via`, `requested` and `sha256` so the plan compiles on its own.
The compiler fails with `PROVEN_CONFIG_FAIL` when that file exists but is not referenced,
when it or a cell changed or disagrees with the embedded cells,
when a proven dict is not applied verbatim, or when a proven role is created without
provenance. Values are changed by re-proving in scratch, never by editing the plan.

## Allowed operation types

`python3 scripts/compile_assembly_plan.py --list-ops` prints these shapes, every allowed
procedure and the default expected results; use it instead of reading the compiler.

- `call`: whitelisted `ipiasm::*` or `ipcfg::*` procedure.
- `set_property`: `CONFIG.*` properties on a typed cell/pin/port object. On a **cell** it runs
  through `ipcfg::apply_dict`, so a key the board preset owns is refused
  (`CONFIGURE_FAIL:BOARD_OWNED`); a deliberate spec-driven override needs `"allow_board": true`
  plus a `"reason"`. `CONFIG.*_BOARD_INTERFACE` keys on a cell run through `ipcfg::board_bind`,
  with the other keys applied in the same write. Pin and port properties are written directly.
  Success is `PROPERTY:OK <path>`.
- `create_bd_design`: create the named block design.
- `apply_automation`: invoke a Xilinx `bd_rule` with structured configuration. On a **cell** it
  runs through `ipcfg::run_automation`, which checks the options against the rule, applies it,
  and fails if the run overrode a board-preset value. Interface and pin targets call
  `apply_bd_automation` directly. Success is `AUTOMATION:OK`.

Raw Tcl, shell commands, and unregistered procedures are rejected.

Clock/reset contracts use whitelisted `ipiasm::set_clock_contracts` (four args:
checks, reset domains, source files, scratch fingerprint) and
`ipiasm::check_clock_contracts` (no args). `ipiasm::register_clock_contracts` (two args:
checks naming a `cell` or `subject` with `expected` Hz, and the
`clock_fingerprint.tcldict` that `scratch_bind` writes) builds the same registration from
the live design and stands in for `set_clock_contracts`. Require `CLOCK_CONTRACTS:SET` and
`CLOCK_CONTRACTS:OK` respectively; `NA` is not an acceptance result. See
[Clock And Reset Planning](references/clock-planning.md) for the schema, stage
ownership and explicit limits. The proposal JSON is not an assembly plan and
must not be passed directly to this compiler. Bind a live-proven candidate using
existing create/configure/connect operations; register requirements before its
clock checkpoint and keep them unchanged through delivery. Check them only after the
design validates (after `ipiasm::validate_bundle`, in a stage after `qos`): the check
reads values Vivado sets only on validation, and the compiler refuses it earlier.

Supplied RTL is supported by a whitelisted `call` to
`ipiasm::create_module_ref` with `args: [module_name, cell_name, [source_files], config]`.
The optional config object contains only `CONFIG.*` parameters. Supply all required
RTL dependencies as readable files; relative paths resolve from Vivado's working
directory. The helper requires the board gate, registers files in the current source
fileset, updates compile order, and creates a module reference (not catalog IP).
Source registration, creation and changed configuration use the existing persistent
per-cell attempt budget; unchanged repeats do not consume attempts. Existing cells
must reference the same module, and parameter readback must match. This does not
package RTL, refresh edited RTL, or prove inferred interfaces and hardware behavior;
verify those in a live scratch design before declaring the plan ready.

## Required greenfield artifacts

```text
design_spec.json
plan.md
binding_intent.json
binding_probe.tcl
binding_probe.out
binding_snapshot.json
proven/<group>.json        # ipcfg::export_proven, one per scratch group (+ review-only .tcl)
proven_ip_config.json      # proven_to_plan.py merge, referenced by the plan
assembly_plan.template.json
assembly_plan.json
```

The probe runs in a disposable project for the requested part/board. Preserve its records
outside that project, close it, and create the clean delivery project in the same Vivado
session. Never close an existing delivery project implicitly. Record environment and
repository fingerprints in the plan; rerun affected experiments when they change.

Do not inspect a reference `.bd` or exported BD Tcl to choose the implementation.
When the user supplies a golden design for comparison, keep that comparison separate
from plan generation. Never copy its topology, constants or pin assignments into shared
skills, discovery caches or generic tests. It does not override input requirements.

## Staged executor

The existing `compile_plan(plan)` and `ipiasm::assemble_from_plan plan_path`
remain full-plan APIs. Select exactly one named stage with
`compile_plan(plan, stage="cells")`, CLI `--stage cells`, or:

```tcl
set summary [ipiasm::assemble_from_plan /absolute/output/assembly_plan.json cells]
set details [ipiasm::stage_execution_status]
```

Managed execution compiles the whole plan with `--dispatch` once per plan path/raw
SHA-256 and compiler SHA-256, then selects the stage at execution time. The session-local
cache verifies the compiled artifact's SHA-256 before every reuse; missing or changed
artifacts are recompiled. Each invocation still reserves its own evidence paths.
The standalone `--stage` API remains available and does not use this managed cache.

The compiler validates the **whole plan**, including excluded operations, against
the existing operation/procedure whitelist. Stage names and operation IDs must be
unique across the plan. Omitted IDs remain `<stage>_<index>`; explicit IDs are
recommended. Empty stages and empty/malformed `expect` lists are rejected.
Stage selection does not run earlier or later operations. Binding-context checks
still run before any selected operation; an unset `BOARD_PART` passes (the board stage sets
it, and the board gate blocks cell creation until then), a different one fails.
`ipiasm::check_board_hooks` must come after the cells it inspects. A selected stage must have an open
project/BD. In a selected stage, `create_bd_design` becomes a read-only check that
the named BD is already open; a different or absent BD fails. Full mode retains
creation behavior. The executor never closes, reopens or recreates the delivery BD
for continuation.

Prerequisites are conservative and positional: **every earlier stage in the
`stages` array must have completed successfully under this executor**. There is
no dependency-DAG override or manual completion import. A manually built delivery
BD without matching stage evidence (in memory, or the newest executor receipt) cannot
jump to a later plan stage.
Run the reviewed initial stage to verify the existing BD and its declared board
checks before selecting later stages. This records only those actual checks; it
does not adopt unverified cell, connection or address state as completed work.

Completion is keyed by canonical plan hash, raw plan-file SHA-256/path (managed
execution), project directory/name, BD identity, Vivado release, part, board part,
and loaded library signatures. The CLI-generated script carries source-hash and
stage headers, checked before sourcing; the plan is hashed before compilation,
after compilation, and after execution. Changes invalidate all prior completion.
Repository/board-content changes outside these identities are not automatically
fingerprinted; record those fingerprints in the authoritative plan and rerun
affected feasibility/readback checks. `begin_design` clears in-memory completion on
transition; same-design initialization and identical library reloads preserve it.

**Across Vivado processes.** Every `assemble_from_plan` call writes its result
(`evidence_path`, in a `logs/` directory beside the plan, together with the compiled
plan and the per-operation captures) with the completion exactly as that call left it,
including completion a failure wiped. When a named stage starts with no matching
in-memory completion, for example in a new Vivado session, it resumes the
`completed_stages` of the **newest** receipt for that plan, and only if that receipt
records the same plan hash, plan path/SHA-256, DesignSpec SHA-256 and context. Older
receipts are never merged in. An unreadable newest receipt counts as a wipe. When
`plan_execution_status` observes a changed plan, spec or context, it writes an
`*.invalidated.result.tcldict` receipt so no later process resumes the discarded
completion. Do not create, edit or delete these files by hand. A delivery BD rebuilt
by hand under the same project and BD name is not detected by the receipts; rerun the
plan's stages after any such rebuild.

Before replay, completion for the selected stage and **all later stages** is
cleared, including on a failed replay. Earlier matching completion may remain.
There is no automatic rollback and **no operation-level skip based on the
ledger**. Previously successful `ipcfg::apply_dict` calls with identical operation
bodies are read back on selected-stage retry. Matching CONFIG values skip the write;
drift runs the original call through the attempt guard. This also applies to successful
operations preceding a later failure. All other selected operations execute again.
Rerun the selected stage with replay-safe helpers and explicit readback checks.
Raw `set_property`, automation,
and other non-idempotent operations retain their existing behavior and must not
be assumed safe to replay. Attempt budgets, board ownership, and existing gates
are unchanged. Once an earlier stage is rerun, rerun each dependent later stage.

A whole-plan call (no stage) in the same Vivado session, for the same plan and
context, on a BD that still has cells resumes after the stages already complete and
reports `resumed_after={...}`; on an emptied BD it starts over. In a new session
the plan's `create_bd_design` refuses a BD of its name that already exists
(`PLAN_ENV_FAIL:block design ... already exists`): run the incomplete stages by name.

The executor emits one compact final console summary, without duplicating raw
operation results. `ASSEMBLY_PLAN:PARTIAL` is not whole-plan PASS. Only when every
required stage is complete does it return `ASSEMBLY_PLAN:PASS` and record the
existing path/SHA provenance used by `plan_execution_status` and delivery gates.
That status also checks current project/BD/environment. Given a plan path and no
in-process PASS, it accepts a build run across Vivado sessions when the newest
receipt shows every stage complete and the same checks hold, and reports
`PLAN_EXECUTION:OK ... staged=1 receipt=<file>`. Otherwise it names the stages still
to run. Directly sourcing a
compiled full plan remains supported but does not record delivery provenance;
use `assemble_from_plan` for managed execution. A stage PASS means its declared
operations/checkpoint passed their result predicates, not that missing hardware
checks were inferred. Native validation and all final contract gates still apply.

`stage_execution_status` returns a Tcl dict (latest invocation only):

| Field | Meaning |
|---|---|
| `schema_version`, `status` | `1`; `PENDING` before execution, then `PASS`, `PARTIAL`, or `FAIL` |
| `stage`, `operation` | Last stage/operation reached; may be empty on preflight failure |
| `checks`, `completed_stages` | Accepted operation/checkpoint count; currently verified stages |
| `operations` | Dict by operation ID: stage, operation, status, raw return, Tcl catch options, warnings, capture receipt, elapsed milliseconds, mutation/attempt deltas |
| `warnings`, `error_info`, `error_code` | Retained warning lines and complete Tcl exception diagnostics |
| `warning_count`, `critical_count`, `error_count` | New native message counts across captured operations |
| `elapsed_ms`, `mutation_delta`, `attempt_delta` | Executor duration when run; deltas are `unknown`, never invented zeroes |
| `evidence_path` | Automatically persisted complete result as UTF-8 Tcl dict (`schema_version`, `data`); never source this data file |
| `compiled_path` | Unique per-invocation evidence stem under the plan's `logs/`; cache reuse does not create a new Tcl file here |
| `compiled_artifact`, `compilation_cache_hit` | Actual compiled Tcl file and whether it was reused after integrity verification |
| `skipped_operations` | Previously successful CONFIG calls verified unchanged during this invocation |
| `plan_hash`, `context` | Canonical hash and observed context when available |

Every managed invocation archives its result before returning, including compiler,
operation and preflight failures. A later call never overwrites earlier evidence.
Evidence-write failure revokes completion/provenance and returns FAIL. The result
remains in memory with archive diagnostics if disk storage fails. Checkpoints use reserved
result IDs `@checkpoint:<stage>`. Successful operation status is `PASS`; explicitly
expected `FAMILY:NA` remains `NA`. Broad `FAMILY:` accepts only recognized positive
tokens, not `FAIL`, `PENDING`, `PARTIAL`, `NA`, or malformed status tokens. Existing
free-form success families (`SUCCESS:`, `SKIP:`, `READY:`, `STAGE:`, `CHECKPOINT:`)
retain their detail strings. Wildcard acceptance is forbidden. Critical warnings
and failure statuses in returned text fail even when Tcl returned code zero.

Each managed operation uses `ipcfg::capture`, preserving raw results, complete
catch options and native `write_messages -severity OUTPUT` snapshots. Only the
known export header is stripped for comparison; the prior message history must
remain an exact prefix. Missing capture support, malformed exports, rewritten
history or failed reads stop execution. New native ERROR/CRITICAL WARNING messages
and ignored-write warnings fail the operation even with Tcl code zero. Ordinary
warnings remain counted; old session warnings do not contaminate the next call.
Bind `::ipcfg::native_log_path` to the exact MCP startup `log_file` once per process.
The capture also retains immutable `.native.log` byte-range slices, since validation
can remove earlier messages from the export. Log replacement/truncation and critical/error
counter discrepancies fail. Excess WARNING counter increments without log text are recorded
as `unprinted_warning_count`, not invented diagnostic lines. With no bound native log,
all count/export discrepancies fail conservatively. Do not capture across project changes.
Operation failure stops later operations. Direct unmanaged `_execute_plan`/compiled
script invocation does not provide this capture guarantee or delivery provenance.

The final summary includes message counts and its evidence path. No follow-up log
fetch is required on success. Native output is not globally suppressed: keep the
Vivado log and use MCP digest mode for large native reports. Snapshot files include
session history and therefore grow with the session; capture has I/O overhead.
No automatic log deletion, message-suppression changes or attempt resets occur.

Native Vivado 2026.1 fixture: `tests/integration/test_output_capture_live.tcl`.
It refuses an open project and must run in an approved disposable session. It
checks a full three-operation stage, native warning/error behavior, durable
exception recovery and compiler failure. Its repeated return payload is synthetic,
not hardware acceptance. Measurements and limits are recorded in
`tests/evidence/workflow_benchmark/IP_CONFIGURATOR_OUTPUT.md`.

## Read-only feasibility gates

The companion `lib/feasibility.tcl` is loaded by the assembler. Its generic checker
has offline positive/negative coverage and a passing native Vivado 2026.1 fixture:
`tests/integration/test_feasibility_live.tcl`. That fixture verifies IRQ22 propagation,
both NoC apertures, independent delivery rebuilds, application-master address windows,
wrong/stale expectation rejection, and disappearing-warning capture (57 assertions).
This is boundary qualification, not a full-system build, timing, or hardware-board test.

`ipiasm::feasibility_fingerprint sources` requires a Tcl dict mapping `spec`, `board`
and `ip` to lists of existing absolute source files. Include the complete relevant
spec, board/preset and selected IP-definition inputs. It records their SHA-256 values
plus the live tool version, part and board part. Preserve that dict with scratch evidence.
Missing inputs fail; omitting a relevant dependency from the supplied lists is not
automatically detected. Hash matching does not authenticate evidence provenance.

`ipiasm::check_feasibility checks phase sources expected_fingerprint` is a whitelisted,
read-only plan call. Use `expect: ["FEASIBILITY:OK"]`. Each check is a Tcl dict with
`id`, `kind`, exact `subject`, independently derived `expected`, requirement `source`
and owning delivery `stage`. Supported kinds:

- `config`: cell plus `property` and scalar `type` (`integer`, `boolean`, `string`).
- `pin_width`: connected pin width; owning stage must be connect or later.
- `interface`: expected dict containing exact `VLNV` and `MODE`.
- `aperture_capacity`: exact published address segment, minimum byte count and a
  `selector` dict containing `cell`, `property`, `value` for live CONFIG readback.

Phase `scratch` checks all entries after the experiment's required setup. Delivery
phases check their owned entries against fresh objects and recomputed fingerprints;
empty acceptance, stale fingerprints, missing properties and mismatches fail closed.
Place a call in every owning compiled stage, after configuration/connections/propagation.
The gate never performs that setup itself or installs checks into a plan automatically.
It does not prove assigned address windows: retain independent base/range readback in
the address stage. The offline JSON manifest format below is separate; there is not
yet an automatic manifest-to-native-check adapter. The supplied native fixture uses
separately generated checks derived from the existing DesignSpec and installed IP metadata.

## Bounded offline capability feasibility

`scripts/validate_feasibility.py` validates recorded scratch evidence against a
separately supplied expected contract. It does not call Vivado, change a gate,
execute Tcl, configure hardware, or authenticate observations. Exit 0 means only
`OFFLINE_EVIDENCE_OK`; every result retains `hardware_status: UNVERIFIED` and
enumerates `pending_live`. Exit 1 means `OFFLINE_EVIDENCE_FAIL`. Never use either
an offline result or synthetic fixture as a hardware PASS or delivery checkpoint.

From the workspace root, generate and validate the reusable synthetic RAVE2 fixture:

```sh
python3 .claude/skills/vivado-ipi-assembler/tests/unit/test_feasibility.py --write-fixture tests/evidence/workflow_benchmark/20260925-feasibility/fixture
python3 .claude/skills/vivado-ipi-assembler/scripts/validate_feasibility.py --expected tests/evidence/workflow_benchmark/20260925-feasibility/fixture/expected.json --manifest tests/evidence/workflow_benchmark/20260925-feasibility/fixture/manifest.json
python3 -m unittest discover -s .claude/skills/vivado-ipi-assembler/tests/unit -p test_feasibility.py -v
```

The exporter refuses an existing directory; choose a fresh path for another run.
Fixture construction lives in `tests/unit/test_feasibility.py:rave2_fixture`.
The generated JSON is the complete schema example, including every coverage ID.
Values are RAVE2-specific test data, not generic defaults. Width 22 and region
requirements come from `outputs/design_spec.json` blocks `interrupt` and `lpddr5`;
its `source` labels identify semantic block paths, not executable JSON Pointers.
The medium selector is recorded in
`outputs/.ipiasm/20260925_stage4_functional_cells.tcl` as
`CONFIG.MC_CHAN_REGION1=DDR_CH0_MED`. The legacy selector, physical subject names,
all observations, setup evidence and fingerprints in this fixture are synthetic
and require independent live discovery. No recorded delivery file is modified.

Schema version 1 is closed: unknown keys, absent evidence, unsupported capabilities,
duplicate JSON keys/coverage IDs, invalid types, and stale fingerprints fail.
JSON inputs are capped at 1 MiB each, 128 checks/setup entries, 1024 coverage IDs,
and 2048 characters per string. Numeric values are JSON integers, not Tcl/hex
strings or booleans; ranges use nonnegative `base` and positive `bytes`, within
a 64-bit address space. Supported value types are `integer`, `string`, `boolean`,
`interface` (`vlnv`, `mode`) and `range` (`base`, `bytes`).

| Document | Required fields |
|---|---|
| Expected root | `schema_version`, `fingerprints`, `coverage_ids`, `setup`, `checks` |
| Manifest root | `schema_version`, `origin` (`synthetic` or `recorded`), `fingerprints`, `setup`, `observations` |
| Expected check, keyed by ID | `kind`, `subject`, `expected: {type, value}`, `source`, `derivation`, `setup_requirements`, `delivery: {stage, owner, coverage_ids}` |
| Observation, same ID | `supported: true`, `subject`, `type`, `value`, nonempty `evidence` |
| Expected setup, keyed by ID | `kind` (`connection`, `configuration`, `board`), `description` |
| Observed setup, same ID | `completed: true`, nonempty `evidence` |
| Aperture additions | Expected and observed `selector: {property, value, source}`; expected `assigned_window: {type, value, source, stage, owner, coverage_id}` |

`kind` is `config`, `interface`, `pin_width`, or `aperture_capacity`. CONFIG and
interface values and pin widths compare exactly. Aperture base compares exactly;
published capacity must be **at least** the expected region size. Assigned windows
are separate, must fit inside the required aperture, and remain pending at
`address`, with their own explicit owner. They are never inferred from capacity.
Connection-derived checks require completed connection setup and defer delivery
readback to `connect` or later, never `cells`/`clocks`. Every check ID, delivery ID,
and assigned-window ID must occur exactly once in the independent `coverage_ids`.

Fingerprint maps must match exactly, including keys. Require lowercase SHA-256
values for at least `spec`, `tool`, `part`, `board`, and `ip`; additional dependency
keys are allowed but must match too. Compute expected values independently from
current spec bytes, exact tool build/part identity, and deterministically recorded
board/preset and IP repository contents. Do not copy them from the manifest.
Changing any entry invalidates reuse; this bounded implementation rejects the
whole manifest rather than guessing which probes remain valid. Source/evidence
strings are provenance references only, not file-content or authenticity checks.

RAVE2 coverage is five scratch checks (`irq.count`, `irq.width`, `irq.interface`,
`noc.legacy`, `noc.medium`), their five `.delivery` IDs, and
`noc.legacy.assigned_window`/`noc.medium.assigned_window`: 12 IDs in total.
Still required live: prove a connected 22-bit source propagates to AXI INTC CONFIG
count and `intr` width; discover/read its control interface; apply board memory
setup and confirmed selectors, connect a minimal functional memory route, and
read both target apertures (2 GiB legacy and at least 14 GiB medium). Repeat these
readbacks in delivery at the declared owners/stages, then verify exact software
windows and master visibility at addressing. Full clock/reset, QoS, NoC legality,
address-map and native design validation remain later gates, not scratch demands.

Baseline, implementation and repeated treatment logs are preserved under
`tests/evidence/workflow_benchmark/20260925-feasibility/` by `scripts/measure_workflow.py`.
Those measurements are offline Python tests only, even though the shared runner's
generic metadata mentions Tcl mocks. Null live/tool/agent metrics are unknown,
not zero; these runs establish no live timings, speedup or hardware feasibility.
