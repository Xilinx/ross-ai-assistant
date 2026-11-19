---
name: vivado-ipi-assembler
description: >
  Use this skill to ASSEMBLE a full Vivado IP-Integrator block design from a system
  specification — not just configure one IP, but create + configure MULTIPLE cells and
  then CONNECT them, build clock/reset domains, assign the address map, and validate the
  result. Trigger whenever the user wants to "build the block design", "assemble the
  system", "wire up the IPs", "stand up the Versal design", turn a spec/diagram/manifest into
  a working BD, or connect/clock/address/validate an existing multi-cell design. This skill
  depends on and orchestrates the vivado-ip-configurator skill (ipcfg::*) for the per-cell work
  and adds the assembly layer (ipiasm::*). It is AUTOMATION-FIRST: apply_bd_automation is
  always tried before manual wiring.
license: MIT
compatibility: Requires Vivado 2026.1 or newer, Vivado MCP, AMD documentation search MCP and the sibling vivado-ip-configurator skill. Python 3.10+ is resolved automatically through vivado-ip-configurator.
metadata:
   version: "1.0.0"
   stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IPI Assembler — Spec-to-Block-Design System Assembly

`vivado-ip-configurator` owns single-cell configuration and readback; its assembly mode
preserves the cell. This skill builds a **persistent,
multi-cell, connected, addressed, validated** block design by orchestrating it. Everything
per-cell (`ipcfg::create_cell_cfg`, doc grounding, `verify_intent`, `audit_intent`,
`discover_params`, the learned cache, `run_automation`) is reused as-is; the
`ipiasm::*` layer adds only what lives BETWEEN cells.

This file is the *contract*: rules, gates, stage order, and the one proc to call per stage.
Mechanics and evidence live in `REFERENCE.md`; read the pointed section when a rule seems
optional before deciding to skip it.

**Start here for calls:** [local Tcl API inventory](REFERENCE.md#local-tcl-api-inventory)
lists exact public signatures, return shapes, gates, mutation effects and stage owners.
The compiler's [plan whitelist](scripts/compile_assembly_plan.py) defines which helpers
may appear in compiled operations. Runtime/setup diagnostics are a separate surface.

Use FPGA terminology in plans and progress reports: **IP selection** (binding),
**function in the design** (semantic role), **required check** (gate),
**requirement** (contract), and **record automation changes** (`ipcfg::run_automation`).
Keep procedure names and structured status codes unchanged. Explain a failure as
expected hardware, observed hardware, and next action, followed by its status code.

**Genericness.** Nothing in this skill names a board, a part, a cell, or an instance count.
Every quantity, name and window comes from the user's spec or from what the board file
selected in Vivado reports. If a number is not in the spec and Vivado cannot supply it, it is
escalated, never invented.

## Minimum successful path

Keep this state card in `plan.md` and update it after each stage:

`SPEC_OK -> PLAN_OK -> BOARD_OK -> CELLS_OK -> INVENTORY_OK -> CLOCKS_OK -> QOS_OK -> ADDRESS_OK -> VALIDATE_OK -> DELIVERED`

`PLAN_OK` means `assembly_plan.json` compiled to `ASSEMBLY_PLAN:OK`. The delivery design is then built by
`ipiasm::assemble_from_plan`; stage scripts only repair what it built. The `plan` gate enforces this:
after `ipiasm::init`, `create_cell`, `instantiate_n`, `create_module_ref` and the build stages return
`GATE_FAIL:... requires the 'plan' gate` until a compiled plan runs, and `final_contract_gate`
refuses a `none` plan. A plan runs only beside its `design_spec.json` (`PLAN_SPEC_FAIL` otherwise),
and the final gate refuses spec `none`: generate the DesignSpec first.

For a greenfield build, do exactly this:

0. **CED first.** In a scratch project (board and part set) run
   `ipiasm::reference_search <out> {<request>}`, and read the board's reference document's
   clocking section. If the best CED is `STRONG`, record it with `ipiasm::ced_reference`. Then
   write `<out>/ced_checklist.json` from the request and the document, and run
   `ipiasm::ced_fit <out>`.
   - **`EXACT` or `CONFIG_ONLY`:** in the delivery project run `ipiasm::deliver_ced <out> <ced> <options>`.
     You are done: no DesignSpec, no plan. The state card is `CED_FIT -> DELIVERED`.
   - **`STRUCTURAL`:** continue with step 1, seeding the plan with `ipiasm::instantiate_ced`.
1. Generate and validate the DesignSpec. Repair every `SPEC_FAIL`; do not reinterpret one
   as permission to continue.
   In unattended/benchmark runs, stay in the current agent mode: do not call `SwitchMode`,
   `CreatePlan`, or an interactive planning tool. `plan.md` is a file deliverable, not a
   request to enter product Plan mode.
2. Start one Vivado session and confirm its working directory, part, and board. In a scratch
   project, write `binding_intent.json`, run `ipiasm::scratch_bind` (one call: probe, snapshot,
   drafted plan template), and prove the configurations the snapshot cannot show (see [planning steps](references/build-stages.md#0-requirements-offline-implementation-planning-with-vivado) and
   `ASSEMBLY_PLAN.md`). The binding probe and `ipcfg::create_cell_cfg` experiments are not
   blocked by the `plan` gate.
3. Complete the drafted `assembly_plan.template.json` (its TODO list says what is left) from the
   DesignSpec and the scratch evidence, then bind and compile it
   (`compile_assembly_plan.py`) until it reports `ASSEMBLY_PLAN:OK` -> `PLAN_OK`. Close the
   scratch project and create the clean delivery project in the same session.
4. Build the delivery design with `ipiasm::assemble_from_plan <absolute-plan>` (it opens the
   `plan` gate). Then repair with one Tcl script per stage. Use `ipiasm::*`/`ipcfg::*`; direct
   commands are diagnostic probes, not the primary build.
5. Do not advance past a failed gate. Repair that stage, rerun its checks once, checkpoint,
   and update the state card.
6. On the first `VALIDATE:PASS`, immediately create all deliverables before optional audits
   or compile work. Never claim an artifact that does not exist.

**Reference designs are requirements, not optional cross-checks.** Before writing the DesignSpec,
look for a reference design in two places:
- **The release's example designs (CEDs).** In the scratch project (board and part set) run
  `puts [ipiasm::reference_search <out> {<the request>}]`. It ranks every CED in the running release
  by board/part support and by the functions they cover, writes `<out>/reference_search.json` and
  prints the top three with their options (`STRONG` = supported and covers the request). For a
  `STRONG` match run `puts [ipiasm::ced_reference <ced> <out> {<option> <value>}]`. It instantiates
  the CED in its own scratch project and records its cells, CONFIG values, connections and address
  map in `<out>/ced_reference.json`.

  **When the CED already meets the request, deliver it; do not plan it.** Write
  `<out>/ced_checklist.json` from the request and the board's reference document. It lists only
  what they require: `{"ip_counts":{"<ip>":n}, "clocks_mhz":{"<ip>":mhz},
  "config":{"<ip>":{"CONFIG.K":"v"}}, "exclusive":false}`. Then, in the delivery project (board
  set, empty block design), run `puts [ipiasm::deliver_ced <out> <ced> {<option> <value>}]`.
  - **EXACT or CONFIG_ONLY:** it instantiates the CED, applies the setting changes (clock-wizard
    outputs, CONFIG values), validates, re-checks the delivered design against the checklist and
    writes the XSA. No DesignSpec, no plan: about 2 minutes in Vivado.
  - **STRUCTURAL** (counts differ, IP missing or extra): it refuses. Build through the plan
    instead, starting from the CED: the first cells-stage operation is
    `ipiasm::instantiate_ced <ced> {<option> <value>}`.

  After the seed, `ipiasm::remove_cell` removes only cells that conflict with the request: more units than the requested counts, or a
  function the request excludes. A unit the request just does not mention (a timer, an interrupt
  controller) stays. The plan then adds the cells, configuration and connections the spec adds.
  `assemble_from_plan` refuses a plan with no `reference_search.json` beside its DesignSpec.
- **Documents, always, CED or not.** Use doc search to find a TRD or XD user guide, or an IP
  product guide's example-design chapter, for the board and function. A CED is a generic starting
  point: the board's reference design can set different clocks or add units. The SCU35 I/O TRD
  (XD332), for example, runs the MicroBlaze V CED's subsystem at 200 MHz. Read the reference's
  clocking, interrupt and processor-configuration sections, not only its overview, and cite it
  with its `url`. Where the document and the CED differ, the document wins.
  `assemble_from_plan` refuses sources whose only reference is a CED.

**Reference drafts and coverage.** `ced_reference` and `example_reference` also write
`design_spec.draft.json` and `reference_digest.txt`. Start the DesignSpec from that draft. The final
gate then requires every functional IP of the reference to be built, or explained in
`reference_deviations.json`. Details: [references/reference-designs.md](references/reference-designs.md).

When a reference matches, the request's words only set its parameters (counts, widths, rates).
Everything else in it is a requirement: its subsystems and their clock frequencies, interrupt
wiring and processor configuration. Put them in the DesignSpec with the citation, build them,
and explain every difference in the report. A count in the request ("4 GPIO") counts the
reference's own units (four AXI GPIO blocks), not pins.
- **Recreating an IP's example design:** in the scratch project run
  `puts [ipiasm::example_reference <vlnv> <out>/example_ref ?config?]`. It generates the IP's Vivado
  example design (Open IP Example Design) and records its IPs and CONFIG values, block-design
  cells, interface connections, address map and constraints in
  `<out>/example_ref/example_reference.json`, then returns to the scratch project. Build the
  DesignSpec and the plan from that file; instantiating the IP alone is not its example. An RTL
  example (`bd_cells=0`) still gets a draft: one block per IP instance with its user-set CONFIG
  values; its connections, clocks and ports come from the example's top module. On a board it
  applies the board interfaces the IP takes (`board_interfaces=`); config `custom` skips them.
- **MicroBlaze V subsystems:** build them with the MicroBlaze V block automation
  (`xilinx.com:bd_rule:microblaze_riscv`), choosing the `preset` that matches the processor's role
  (`Microcontroller` for bare-metal I/O control), with `axi_intc 1` whenever any interrupt is used
  and the interrupt wiring the reference describes. A processor with no interrupt path when the
  reference has one is a defect, not a simplification.

**Keep tool output short.** Every tool result stays in the conversation and is re-read on every
later turn, so a long result slows the rest of the build. Measured on the GitHub-issue designs:
95 model turns re-read 15.7M tokens of context; the largest items were whole documentation files
and multi-kilobyte Vivado printouts.
- Print status lines, not data: `puts [ipiasm::...]` of a helper that returns one line, or a
  count and the path of a file that holds the rest. Never `puts` a whole dictionary, a rule dump,
  a property list or a report into the tool output; write it to a file under `<out>/logs/` and
  `grep` the line you need.
- Look up a helper with `ipcfg::help <word>` (one entry), not by reading `FUNCTIONS.md`,
  `REFERENCE.md` or `ASSEMBLY_PLAN.md` whole; when a document is needed, read the section
  (`Grep` with a few lines of context, or `Read` with offset/limit). Never `cat` a document.
- Batch related queries into one `vivado_execute` that prints one line per answer.
- Run long helpers (`assemble_from_plan`, `final_contract_gate*`, `validate_bundle`, exports) with
  `vivado_execute` `verbosity: "digest"`: the structured status they print last stays in the
  result's tail, and Vivado's console messages go to the log file instead of the conversation.

**One call per scratch round.** In the scratch project, after writing `<out>/binding_intent.json`,
run the whole binding round in one call:
`puts [ipiasm::scratch_bind <out> <out>/design_spec.json [glob -nocomplain <out>/proven/*.json]]`.
It generates and runs the binding probe, writes `binding_snapshot.json`, and drafts
`assembly_plan.template.json` from the DesignSpec (`scripts/draft_plan.py`), returning one
line per role, the draft's TODO items and `SCRATCH_BIND:OK|PARTIAL`. It also writes the
clock-feasibility fingerprint (`<out>/clock_fingerprint.tcldict`) that the drafted
`ipiasm::register_clock_contracts` op reads. With a `STRUCTURAL` `ced_fit.json` beside it, the
draft starts from the CED: `instantiate_ced`, `remove_cell` for surplus units, requirement pins
and readbacks per kept cell, clock and interrupt contracts and fixed-window scopes, so few TODOs
remain. Put all configuration
experiments for the round in one script of `ipcfg::capture` calls and run it in one call.
Edit the drafted template (its TODO list is `<out>/assembly_plan.template.todo.md`) instead of
writing the plan from scratch; rerun `scratch_bind` only when the binding intent changes.

The technology binder is optional when live discovery already resolves every
operation, but plan execution is mandatory: run the normalized operation list
through `ipiasm::assemble_from_plan` before manual repair.
The plan is required for the first build so it can be replayed; it does not restrict later
work. After it runs, inspect and repair the design with any `vivado_execute` command. Prefer
`ipcfg::apply_dict` / `ipcfg::board_bind` for configuration fixes so board-owned values stay
guarded, and rerun the affected checks; the final gate re-checks the live design either way.
Invoke that top-level executor directly in `vivado_execute` so the Vivado
journal records `ipiasm::assemble_from_plan`; its compiled operations remain
batched internally. This is the sole exception to the script-per-stage rule.

In an unattended run, an absent connection or unapplied parameter already required by the
validated DesignSpec is **work remaining, not a reason to stop**. Build it with the documented
helper and verify it. Stop only when the board/catalog lacks a required capability or Vivado
offers multiple functionally different choices that the DesignSpec cannot distinguish.
A closed gate blocks operations *after* that gate; it never blocks the earlier cell,
clock, or connection work needed to open it.

## Standing rules (always in force)

1. **One live MCP session; never `vivado -mode batch`.** Never launch a second Vivado.
   At the start of every stage, call `vivado_list_sessions` and continue in the session it
   lists; it still holds what earlier stages left open. Start one only when none is listed,
   and never call `vivado_stop` during a build.

   Hand-off and polling rules: REFERENCE § *MCP session mechanics*. `reference_search`,
   `ced_reference`, `example_reference`, `assemble_from_plan` and `deliver` can outlast a
   client's 60 s tool call; Vivado finishes them. After such a timeout run
   `python3 <skill>/scripts/await_result.py <out>`: it waits for the call and prints its
   result. Never resend the call or guess a `sleep`.
   **Do the work yourself; do not delegate to sub-agents.**
2. **Script per stage, not command per turn.** Each stage below is ONE `source` of a script
   you wrote under the run workspace
   (`<out>/.ipiasm/<unique-run-token>_<stage>.tcl`) whose last line prints the stage's
   structured return. Never use shared names such as `/tmp/ipiasm_cells.tcl`: concurrent
   agents can overwrite them between write and source. Round trips are the dominant cost; a
   stage with 30 cells is still one call. Per-command calls are for diagnosis only.

   Read your own return from the tool result — `verbosity: full`, and
   `vivado_log_messages` to recover a truncated one. Same REFERENCE section.
3. **Automation-first.** `apply_bd_automation` before any manual `set_property`/
   `connect_bd_*`. Discover the rule (`::bd::util_cmd rules dump` once per session →
   `ipcfg::rule_for_vlnv`), read its live `-config` schema and context-legal values
   (`ipcfg::automation_rule_options <cell> <rule>`), run it and read the changes it reports. Manual wrappers are the fallback
   when no rule applies or it errors. **Sole exception: the AI Engine** — `attach_aie_config`
   + `check_aie_config` (REFERENCE § *The AI Engine*).
   Try a failing automation rule once with its complete schema. If it then fails inside the
   rule (for example, an unset internal Tcl variable), record the error, keep any valid
   cells it produced, and use the documented manual wrapper. Do not retry the same broken
   rule with speculative option combinations. If `automation_rule_options` shows the needed
   value is not legal (e.g. a controller count offered only as `None`), skip the rule and use the
   board path (`attach_board_memory`) — that is the board automation for NoC memory.
4. **Respect hardened coupling.** Supply only currently documented Vivado
   diagnostics to `detect_fabric_coupling`; `FABRIC:coupled` means discover and
   use the matching subsystem automation rather than prescribing NMU/NSU/INI
   wiring from another design.
5. **Board file first.** The board file is the source of every parameter it owns
   (`*_BOARD_INTERFACE`, presets, memory controller count/speed grade, pin LOCs). Bind the
   hook, then stop writing those parameters. A board-supported part with zero
   `BOARD_INTERFACE` properties is a defect. Any board Vivado has registered is fine — the
   skill is not tied to one. Read board data in Vivado (`get_board_part_interfaces`,
   `get_board_part_pins -of`); never search the filesystem for board files (`find /` timed out).
6. **Memory contract.** `NUM_MC` on the memory NoC must equal what the spec/board declares
   (`check_memory_subsystem <n>` → `MEM:OK`). Responder cells are models, not controllers.
   Run this check after functional NoC masters are connected. Before connectivity,
   `MEM_FAIL` saying that no functional master or DRAM aperture exists is expected progress:
   continue through cells and connections; do not stop the build or enter addressing.
7. **NoC traffic contract.** Every NoC connection starts on Vivado's 500/500 MB/s seed.
   Size the routes the requirements name a rate for — declare real bandwidth + class via
   `apply_qos_plan` — and gate on `check_qos` + `check_noc_solution`. Leaving a route the
   requirements are silent about at the seed is a legitimate choice: `check_qos` lists
   those routes as a note on a passing gate, not a failure. Classify each owning route as active
   or intentionally unused from requirements before sizing it. Unused but connected routes
   require explicit `set_unused_qos_routes` intent and a 1/1 MB/s `BEST_EFFORT` reservation,
   not 0/0 or port-capacity reservations. Native validation remains mandatory.
   Never hand-write `CONFIG.CONNECTIONS`.
8. **Choose implementation details; ask about requirement changes.** Select supported
   interconnect topology and adapters autonomously when all requirements remain intact;
   record the reason and readback evidence. Ask before changing external pin assignments,
   fixed software addresses, functionality, or required performance. A legal rounded value
   is acceptable only within the requirement's tolerance. Exception, never a question: an
   exact clock that misses by a few ppm only because a default input reference is stored
   rounded is fixed by setting that reference at full precision (or a legal reference the
   targets divide evenly) and recording an assumption in `plan.md`; see *Exact derived
   clocks* in the build flow. Allow at most 10 total attempts
   per IP, including the initial attempt, shared across assembler and configurator.
   Parameter configuration, automation and stub recovery share this count; nested helpers
   and resumed work do not restart it. Record the count and diagnostics in the run output.
   After the tenth failed attempt, stop mutating that IP and ask for user intervention.
   Configurator mutation helpers and assembler connection automation share the persistent
   project ledger described in `../vivado-ip-configurator/references/ipcfg-library.md` under *Persistent attempt
   enforcement*. An explicitly selected independent-audit controller may instead use
   its guarded ten-corrective-transaction policy described there; missing requirements
   trigger repair and recheck, not immediate terminal failure. Neither policy permits
   resetting exhausted legacy ledgers. `init` does not reset it. Connection automation must return exhaustion
   without falling through to manual wiring. Direct infrastructure writes are not globally
   intercepted, except typed `create_bd_cell` and `write_hw_platform` (see *Gates are code*);
   never use them to bypass a reported limit.
9. **Never downgrade a VLNV silently.** If `vlnv_ok` fails, `get_ipdefs` and ask.
10. **Instrument.** `ipiasm::stage <name>` at every boundary; call `ipiasm::checkpoint <tag>`
    only when the stage gate **passes** — it persists the already-open BD with
    `save_bd_design` and does not close or reopen anything. `ipiasm::timings` in the final
    report. Optimise from those numbers.
11. **Verify session identity before declaring contamination.** A tool response can include
    server log history or diagnostics from another process. That text is not proof that this
    design read another workspace. Check the current Vivado session id, MCP working
    directory, `current_project`, and project `DIRECTORY`. Continue when those all point to
    the requested workspace and no file was opened or sourced from elsewhere. Stop only on
    concrete cross-workspace file access or a project/session identity mismatch.
12. **One open block design for the whole live assembly session.** After the delivery
    `create_project` / `create_bd_design`, keep that BD open in memory for every stage.
    **Never** call `close_bd_design`, `open_bd_design`, or `open_project` during live
    assembly — not between stages, not after validate, not to "refresh state." The only
    in-session persistence is `ipiasm::checkpoint` at gate PASS (rule 10). Do not call
    `save_bd_design` directly except where a delivery stage explicitly requires it.
    `open_bd_design` is allowed **only** when reconnecting a **new** MCP session after a
    crash or deliberate stop: `open_project` once, then `open_bd_design`, then read
    `ipiasm::gate_status` to see which gate to resume from. The binding-probe disposable
    project is the sole exception: create it, probe, `close_project`, delete it, then create
    the delivery project once.

## Gates are code, not advice

Four gates are held as state in `ipiasm.tcl`. Procs that would build past an unmet gate
return `GATE_FAIL:<stage> requires the '<gate>' gate -> <how to open it>` and do nothing.

| Gate | Opened by | Blocks |
|---|---|---|
| `plan` | `assemble_from_plan` once the compiled plan is verified; `plan_execution_status` → `PLAN_EXECUTION:OK` after a restart (or `gate_override plan <user's reason>`) | after `init`: `create_cell`, `instantiate_n`, `create_module_ref`, and `stage cells`/`inventory`/`clock*`/`connect*`/`qos*`/`address*`/`validate*`/`deliver*`; `final_contract_gate` refuses plan `none` without the override |
| `board` | `require_board_preset` → `READY:` (or `gate_override board <user's reason>`) | `create_cell`, `instantiate_n` |
| `inventory` | `gate_inventory {ip want …}` with every line `OK` | `auto_address`, `add_addr_seg`, `place_named_windows`, `autofix_address` |
| `qos` | `check_qos` **and** `check_noc_solution` both `OK` (both `NA` on a NoC-less part) | the same address procs, when the design has a NoC |

Once `ipiasm.tcl` is loaded, Vivado's own `create_bd_cell` and `write_hw_platform` called from a
typed command or from your own script (however it is sourced) are held to the same rules, and
before the plan gate opens no cell is created outside a scratch project under
`<out>/.ipiasm/<token>_probe/`, by any helper: `create_bd_cell` returns the `plan` gate's `GATE_FAIL` until
the plan gate is open, and `write_hw_platform` returns `GATE_FAIL` until `ipiasm::delivery_guard`
passes. The skill's helpers, the generated binding probe and Vivado's automation rules are not
affected. Build cells through the plan and deliver after the final gate; do not look for another
way to create cells or export the platform.

`ipiasm::gate_status` prints all four with the reason each opened. `gate_override` is only
for a bypass the **user** approved in this conversation; record their words as the reason.

## Setup and lifecycle

Load each helper once per process, after opening the authorized project/BD. On first
setup only (replace placeholders with absolute paths):

```tcl
source -notrace <vivado-ip-configurator>/lib/ipcfg/ipcfg.tcl
source -notrace <vivado-ipi-assembler>/lib/ipiasm.tcl
set ::ipcfg::native_log_path <absolute-log_file-from-MCP-startup-receipt>
ipiasm::init                 ;# SUCCESS:ipiasm ready (ipcfg mode=assemble) GATES:... policy=attended
ipiasm::policy unattended    ;# benchmark/harness runs only: ASK lines are recorded, not blocking
```
Before Stage 2 scratch mutations, run and record the
[Python preflight](../vivado-ip-configurator/references/ipcfg-library.md#python-quick-reference-and-local-api):
`python_status`, then `python_exec -c` with required stdlib imports. Reuse its cached
runtime and environment-restoring wrapper for later compiler/binder calls. Stage 1
spec generation/audit remains offline; no Vivado session is needed for that Python work.

**Loading is not initialization or resume.** Current libraries guard repeated sourcing:
the same normalized source path/content returns `SKIP:... already loaded`; changed
content/path raises `LIBRARY_FAIL` instead of hot-reloading. This protection does not
retrofit an older live session. Never source changed helpers into a live old session,
remove its guard, or assume that a same-named proc proves the same library revision.
Record source paths/fingerprints at first load and verify identity plus the
[required-procedure preflight](REFERENCE.md#runtime-and-requirements) at later stage entry.

**Same-design continuation:** keep the delivery project/BD open. `init` now returns
`SUCCESS:ipiasm resumed ...` without resetting a bound design's gates or ledgers.
An implicit context change returns `ASM_FAIL`; do not ignore it. Read `gate_status`,
recheck the current stage's required evidence, and continue within the stop point.
A new process does not recover those in-memory registries from `gate_status`; reconstruct
and verify required evidence through the approved recovery flow, never waive it.

Resume must preserve board-bound memory. Never clear board hooks to `Custom`, delete
memory interfaces or nets, or call `forget_board` to make a replay succeed. Check the
existing hooks, controller count, region selectors, preset equality and exported interface
connections first; a matching configuration is a no-op. Repair actual drift through
`attach_board_memory` with every required region supplied together, keeping the board
presets authoritative. Recheck the result and confirm that a second resume spends no
configuration attempts. Attempt-budget recovery requires explicit user authorization,
a preserved original ledger and a cell-scoped bounded recovery record; it is not resume logic.

**Scratch-to-delivery is an explicit context transition**, not same-design resume.
Preserve scratch evidence, close only the authorized scratch project, then create the
clean delivery project/BD in the same process as described in build flow 0. Scratch
requirements, ownership, gates and contracts must not count as delivery evidence.
Call `ipiasm::begin_design` after opening the new delivery BD (the plan's `create_bd_design`
then adopts that BD while it is empty, or creates it when none is open). It binds the current
project directory/project name/BD identity and clears the previous design's in-memory ledgers,
gates, completion and timing. It never resets persistent attempt counts. Calling it
on the same bound context resumes without clearing anything. Same-project BD transitions,
resume and source rejection passed three native Vivado 2026.1 trials. Different-project
transitions also passed three native trials. Context tuples are frozen text, not native
handles that become invalid when a project closes.
Never close an existing delivery design to make room for scratch or hot-reload new code
to acquire a missing API. Obtain approval for any required process/context replacement.

`policy unattended` never invents a design decision; it permits a documented default
and lists every open ASK in the final report.

**Look up helpers in the index, not in the source.** [FUNCTIONS.md](FUNCTIONS.md) lists
every `ipiasm::` helper and script with its call shape, purpose and success and failure
results (for example `ok: BOARD:bound   fail: BOARD_FAIL`); `ipcfg::` helpers are in
[the configurator's FUNCTIONS.md](../vivado-ip-configurator/FUNCTIONS.md). Both are generated
from the code and tested to be current, and need no Vivado session. In the session,
`ipcfg::help <word>` prints the same entry, and Python scripts answer `--help`. Do not read
`lib/*.tcl` or `scripts/*.py` to learn a call: the index already holds everything a caller needs. `ipiasm::api` prints the whole build flow in one call:
each stage's gate token and its helpers. Run it at the start of a stage instead of
re-reading this file to find a helper.
Record `ipiasm::api_inventory` once in Stage 2; query only the needed pattern later.
Use `ipcfg::requirements_data` for machine reads, not the display text from
`requirements`. Plan stage names must align with notebook stop points. Run one named
stage with `ipiasm::assemble_from_plan <absolute-plan> <stage>` and retain
`ipiasm::stage_execution_status` as evidence. See [staged execution](ASSEMBLY_PLAN.md#staged-executor).
Managed execution caches one integrity-checked whole-plan compilation across stages.
Selected-stage retries may skip a previously successful `ipcfg::apply_dict` write only
after fresh matching CONFIG readback; other operations still execute. Ship the complete
library directory, including `feasibility.tcl`, whose content is covered by the load guard.

For clock/reset planning, load [Clock And Reset Planning](references/clock-planning.md).
Use its bounded proposal script and live contract checker; proposals never prove
feasibility. Ship `clocks.tcl` with the library; its content is load-guarded too.

Managed stages automatically persist complete results and native message snapshots.
Use the returned status, message counts and evidence path; do not dump the full
status dict or fetch full logs on success. Capture failure is a stage failure,
not permission to bypass the wrapper. Native critical/ignored-write warnings fail
even when the underlying Tcl call returned zero. Keep final hardware readbacks.

## Build flow

Print the stage's structured return as the last line of every stage script.

The rules for each stage are in [references/build-stages.md](references/build-stages.md). Read only
the section for the stage you are in (`Grep` its heading, then `Read` that section), not the file:

- **[0. Requirements and planning](references/build-stages.md#0-requirements-offline-implementation-planning-with-vivado)**:
  DesignSpec through the vivado-spec-generator skill (`validate_spec`, `audit_spec`); a scratch project under
  `<out>/.ipiasm/<token>_probe/`; `ipiasm::scratch_bind` (binding probe, snapshot, drafted plan); configured
  experiments in one `ipcfg::capture` script; complete and compile the plan; close the scratch project.
- **[0b. Board gate](references/build-stages.md#0b-board-gate)**: `require_board_preset` → `READY:`
  (or `gate_override board <the user's words>`); the processing system from the board preset; `ipcfg::preset_drift`.
- **[1. Cells](references/build-stages.md#1-cells--hardened-first-board-served-next-then-the-rest)**:
  hardened first, board-served next, then the rest, all created by the plan.
- **[1b. Inventory gate](references/build-stages.md#1b-inventory-gate)**: `gate_inventory` with the spec's counts.
- **[2. Clocks / resets](references/build-stages.md#2-clocks--resets-automation-first)**: automation first;
  clock contracts carry the spec's tolerances (never invent one).
- **[3. Connectivity](references/build-stages.md#3-connectivity-automation-first)**: automation first;
  required ports, interrupts and reachability checked.
- **[3b. NoC traffic gate](references/build-stages.md#3b-noc-traffic-gate-versal-with-a-noc-na-elsewhere--say-so)**:
  QoS plan, `check_qos` and `check_noc_solution` (NA without a NoC).
- **[4. Address map](references/build-stages.md#4-address-map-automation-first)**: automation first, named
  windows placed, overmap checked.
- **[5. Normalize, reconcile, validate, report](references/build-stages.md#5-normalize--reconcile--validate--report)**:
  `validate_bundle`, then the final gate (`ipiasm::deliver` runs it from the plan).

### 6. Deliverables — the moment `VALIDATE:PASS` first appears
**One call does all of this:** `puts [ipiasm::deliver <out>]` (with `verbosity: "digest"`) runs the
final gate from the plan, `delivery_guard`, the exports below, the guard again, the delivery
context check and the timing report, and returns `DELIVERED xsa=... bd_tcl=... timing=...` or
`DELIVER_FAIL:<step>`. Fix what a `DELIVER_FAIL` names and call it again; the steps it runs are:

Require `ipiasm::delivery_guard` → `DELIVERY_GUARD:OK` immediately before:
`save_bd_design`; `generate_target all`; `make_wrapper -top` + add it; `write_bd_tcl
-no_ip_version <out>_bd.tcl`; write `final_status.json` with the gate status, timings, every
structured return from stage 5 and the open ASK list. Leave the BD open if the user asked for
it. Do these before any optional probe, audit, or compile so a timeout still leaves the
artifacts behind. Afterward, verify each required path exists and is non-empty; a successful
Vivado command is not evidence that every file was written. Call `delivery_guard` again
after export; if it fails, rerun `final_contract_gate` before claiming delivery. Finally call
`ipiasm::assert_delivery_context <bd-name> <project-directory>` and require
`DELIVERY_CONTEXT:OK`. Do not send the final response while a binding/probe project is
current or while any disposable probe session remains open.

**Build timing.** Inside Vivado the library records where the build's time goes
(`ipiasm::trace_status` shows the log; one file per Vivado process under
`<vivado-working-dir>/.ipiasm/trace/`). Before the final response, run
`scripts/timing_report.py <vivado-working-dir>/.ipiasm/trace` with the configurator's
`lib/ipcfg/vivado_python.sh` (or `ipcfg::python_script`), write its output to
`<out>/timing_report.md`, and include its first table (wall, library, native Vivado commands,
time outside Vivado) in the final report. It changes nothing in the design.

### 7. Compile (optional)
`launch_runs synth_1 -jobs N; wait_on_run synth_1` — synthesis is the expected unattended
stop for a spec-only build. Implementation needs the board constraint set; see REFERENCE
§ *Reaching full implementation*.

## Speed rules and structured returns
Both live in `REFERENCE.md`: § *Speed rules* (what to batch, and the one place allowed to
force a validate) and § *Structured returns* (the token vocabulary every stage prints — parse
these, never eyeball them). Read both before writing the first stage script.

## Out of scope (state it explicitly)
Host software, off-chip devices, custom RTL not supplied as an input, board XDC for a
spec-only build, and any block the spec marks inactive. These are reported, not built.

## Where the depth is
`REFERENCE.md` — generic QoS, addressing, board-source, hardened-interface, and
clock/reset invariants.
