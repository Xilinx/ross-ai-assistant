<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Build stages in detail

The stage-by-stage rules of the assembler, moved out of `SKILL.md` so they are read only for
the stage at hand: find your stage's heading (`Grep "^### 2\." references/build-stages.md`, then
`Read` that section) instead of reading this file whole. `SKILL.md` keeps the main path, the gates,
the output rules and delivery.

### 0. Requirements offline, implementation planning with Vivado
When the input is prose or a diagram rather than a validated DesignSpec, first read
`../vivado-spec-generator/SKILL.md`, invoke that skill, and write `<out>/design_spec.json`. Run its
`scripts/validate_spec.py` for `SPEC:SCHEMA_OK`, then its
`scripts/audit_spec.py` against the source manifest recorded during generation.
**Do not touch Vivado until the audit prints `SPEC: OK`**. No policy or independent
approval artifact is required for the lab gate. If validation
reports an unresolved functional choice, ask once now. Never defer it until cells exist.

When a DesignSpec JSON is supplied, run the same schema and audit gates
instead of regenerating it. Missing requirement source inputs fail the audit;
the JSON cannot serve as its own source evidence. The accepted JSON, not the original
prose, is the source for every later implementation gate.

**Write the input/output boundary down first**, as a list of the files you were given.
`design_spec.json`, `plan.md`, `scripts/` and every log are *outputs of this step or a later
one*. A gap in one of them is a gap in your own work; only the given files can support a
claim that the problem statement left something unspecified.

`SPEC: OK` means schema, consistency, references, source integrity and unresolved-intent
checks passed for that candidate and source hashes. It does not prove prose completeness
or hardware feasibility. `SPEC: FAILED` blocks planning until its findings are resolved.
Write a draft `<out>/plan.md` from the validated DesignSpec, then prove uncertain
implementation choices in a scratch project before declaring the plan executable:

1. Register the supplied board and IP repositories before creating the scratch project
   under `<out>/.ipiasm/<unique-run-token>_probe/`. Use the requested part and board.
   Record the Vivado build, part, board, repository paths and content fingerprints.
   Save `ipiasm::feasibility_fingerprint <sources>` to evidence while the scratch project
   is open: `set_clock_contracts` needs exactly this value, and it needs an open project.
   `ipiasm::scratch_bind` does this with a DesignSpec: it hashes the board file, its
   preset and the clocking IP definitions into `<out>/clock_fingerprint.tcldict`
   (`ipiasm::clock_fingerprint`), which `register_clock_contracts` reads.
   Never close or modify an existing delivery project to make room for a probe; report
   that context conflict and obtain approval for an isolated session if needed.
2. Inspect the live catalog, board interfaces, presets and physical pin mappings. A
   missing interface name does not prove a missing function; a GPIO associated with a
   peripheral does not prove an additional peripheral. Account for integrated functions
   and user-supplied RTL as well as catalog IP. Record which build stage supplies each
   function, including user-declared or documented link-time additions. When the
   user assigns an accelerator to `v++ --link`, that establishes stage ownership:
   do not require accelerator discovery, instantiation, configuration or presence
   in the base-design inventory or acceptance checks. Prepare only its platform-side
   control/memory interfaces, clocks, resets and PFM metadata for extensible-XSA
   export. Check accelerator integration after linking, not during base creation.
   Keep separately required platform adapters and fixed windows; an adapter named
   after an accelerator is not that accelerator. Do not defer a required base-design
   function merely because a build target has a suggestive name.
   Do not invent pin mappings or RTL.
   **Boundary names before binding.** When a supplied RTL top instantiates the wrapper,
   run `python3 scripts/boundary_names.py check --spec <out>/design_spec.json --rtl
   <top.sv> --wrapper <bd>_wrapper` now. For each interface `BOUNDARY_NAMES_FAIL` lists,
   read the RTL and `add` its port name(s) (one per bit when the RTL splits it), then
   rerun the spec validators. The plan compiler's `BOUNDARY_FAIL` accepts only those
   names, and fixing it after binding means rebinding on a changed spec hash.
3. Write `binding_intent.json` first and run the generated binding probe from
   `ASSEMBLY_PLAN.md`: it creates each candidate IP and records its default interfaces
   in one call, so do not hand-write per-IP creation probes. Give every catalog IP the
   plan will create its own role here, including helpers such as the memory behind a
   controller (its `spec_block` is the block it serves); the compiler refuses non-plumbing
   IP the probe never created.
4. Then reuse `vivado-ip-configurator` for what the default snapshot cannot show:
   configured interfaces, directions and widths, not only CONFIG values. Use small
   connection experiments for connection-derived properties. Each experiment records its
   question, expected observation, result and evidence. Put all experiments in **one**
   probe script, one `ipcfg::capture` per experiment, and **collect** each receipt instead
   of stopping at the first failure; print one summary line per experiment at the end.
   One rerun then repairs every failed experiment together. Do not synthesize or build an
   entire speculative system. Follow the shared repair budget in rule 8.
   Default-cell discovery alone does not prove configured capabilities. Record additional
   configured experiments and any unsupported plan operation explicitly; do not claim
   readiness with an unresolved required IP, RTL integration operation or board pin mapping.
   The plan format, allowed procedures and default expected results:
   `python3 scripts/compile_assembly_plan.py --list-ops` (do not read the compiler source).
   **Exact derived clocks are proven by search, not by a schema probe.** When the spec
   states fabric clock frequencies that a clock wizard must generate, run
   `ipcfg::search_clock_config` in the scratch project for those targets (call shape:
   `ipcfg::help search_clock_config`) inside an `ipcfg::capture`. Its `CLOCK_SEARCH:PASS`
   line is the feasibility proof; reading a wizard's parameter names or creating one
   default wizard proves nothing about the requested rates. When a search `FAIL` carries
   `near_miss_ppm=`, or a processing-system clock reads back a few ppm off its exact rate,
   run `ipcfg::reference_precision <MHz>` on the input reference that clock derives from; on
   `REF_PRECISION:ROUNDED` set the printed `full=` value with `ipcfg::require` on the source
   cell (if a 1 Hz residual remains, a legal reference the targets divide evenly), record it
   in `plan.md` as an assumption, rerun and continue. The spec's rates stay unchanged.
   **A dedicated port is proven by connecting to it, not by listing it.** When the spec
   names a specific interface or port that joins two IPs (for example a sensor stream into
   one named input tile of a larger IP), create both configured cells in the scratch BD and,
   inside an `ipcfg::capture`, run `puts [ipiasm::preflight_connect <src-cell>/<src-intf>
   <dst-cell>/<named-port>]` (read-only and not gated, so it works in scratch where
   `connect_intf` returns `GATE_FAIL`; do not fall back to a raw `connect_bd_intf_net`).
   The printed `PREFLIGHT:OK <src>-><dst-cell>/<named-port> ...` line, naming that exact
   port, is the feasibility proof; seeing the port in a default interface listing or in
   documentation proves nothing about the configured cells. A `PREFLIGHT_FAIL` is a finding
   to repair (configuration of either side), not to skip; when it only says neither
   associated clock pin is connected, drive both from one scratch clock source and rerun.
   **Scratch clock hookup is setup, not an experiment.** Before the preflight, connect
   both associated clock pins to one existing clock output pin with a raw `connect_bd_net`
   (the gated `ipiasm::connect_net` returns `GATE_FAIL` in scratch, and
   `ipiasm::sync_intf_freq_hz` only edits `FREQ_HZ`, which does not change the domain
   verdict). Guard every such scratch connect so it runs only when the pin has no net yet:
   `if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins $p]]] == 0} {connect_bd_net $clk $p}`.
   Keep this setup outside the experiment captures and out of the summary.
   **Reruns must be idempotent.** The scratch BD keeps everything earlier attempts
   created or wired, so a repair rerun either reruns only the failed experiments or relies
   on guarded creation and connects; an "already connected" or "already exists" error from
   a rerun is your script's fault, not a failed experiment, and does not use up a repair.
   **Never walk the whole hierarchy.** An unfiltered `get_bd_cells -hier` loop that reads
   properties per cell descends into large IP (ISP, PS wizard, AIE) and can hold the
   single interpreter for tens of minutes, stranding every later step. Query top-level
   cells, filter in the query (`get_bd_cells -hier -filter {VLNV =~ *clk_wiz*}`), or use
   the helpers; never iterate `get_bd_cells -hier` with `get_property` per cell.
   Run the clock search and every other Vivado experiment **before** writing the long
   planning artifacts. Then write and compile the plan with the scratch project still open
   (step 5), so a late refusal is repaired in place, and close it in step 6.
   **Export what you proved before closing.** While the proven cells still exist, run
   `ipcfg::export_proven <abs-out>/proven/g1.json <entries>` once (call shape:
   `ipcfg::help export_proven`). One entry per proven cell:
   `dict create cell <scratch-cell> role <binding key> apply_via <how> evidence
   [list <capture evidence files>] spec_pointers [list /blocks/<n>/parameters/...]`, where
   `apply_via` is `create_cell` for catalog IP you configured, `ps_wizard` for the PS and
   `board_preset` for a cell whose values the board preset owns. Require `EXPORT:OK`. It
   records the keys you set (`requested`) separately from values the IP derived, so do not
   copy derived values into requirements. The file is write-once; a rerun uses a new name.
5. **With the scratch project still open**, first run `python3 scripts/plan_requirements.py
   --spec <out>/design_spec.json --intent <out>/binding_intent.json --snapshot
   <out>/binding_snapshot.json --fragments <out>/proven/*.json [--roles-from <roles.tcl>]
   --coverage-out <out>/.ipiasm/<token>_coverage.json` → `PLAN_REQ:OK`. One call lists
   everything the compiler's `--spec` checks will demand: each role's cell name, the
   checks each stage must carry, accepted boundary port names, the reachability pairs, the
   role-table rows, and every parameter leaf to map (the skeleton file holds each with its
   exact value; fill `reason` and `checks`, or make it `external` with an `owner`). Build
   the template from that list instead of deriving it from the compiler, then,
   **before** binding, replay the proven configurations into it:
   `python3 scripts/proven_to_plan.py --fragments <out>/proven/*.json --snapshot
   <out>/binding_snapshot.json --template <out>/assembly_plan.template.json --merged
   <out>/proven_ip_config.json --spec <out>/design_spec.json` → `PROVEN_PLAN:OK`. It rewrites
   the create/configure/readback ops of every proven role, tags them with `provenance`, and
   wires their checks into `parameter_coverage`. Then bind and compile → `ASSEMBLY_PLAN:OK`.
   A refusal here (a board-owned key in a fragment, a role without a cell, a changed hash)
   is repaired in the scratch design that is still open: fix it, prove it again, export to
   a new fragment name, rerun. Closing scratch first turns each such refusal into a full
   scratch rebuild. Never hand-edit a proven op: the compiler rejects a changed hash.
6. Only after `ASSEMBLY_PLAN:OK`: preserve the evidence outside the scratch project, then
   `close_project` and delete the owned `<out>/.ipiasm/<token>_probe/` directory
   (`file delete -force <absolute-dir>`), touching nothing else. No `*.xpr` may remain
   anywhere under `<out>`. Keep the Vivado process for the clean delivery project. Recheck
   environment fingerprints before using the plan; changed tool, board/preset or IP
   repository content requires renewed affected discovery.

**Planning is done when** these exist and hold, plus anything the user's plan names:
`plan.md` (when the user names a role list, such as a Tcl `required_roles` proc, a `| role | cell |`
table with one row per listed role: the role name exactly as listed in the first column and, in the
second, the backticked instance name of the cell the plan creates for it, never a VLNV, and each
role mapped to a different cell), the
[required artifacts](../ASSEMBLY_PLAN.md#required-greenfield-artifacts), `assembly_plan.json` compiling to
`ASSEMBLY_PLAN:OK` (the compiler checks every call's argument count against the helper; when a
role list is named, pass it with `--roles-from <abs-path-to-the-roles-tcl>` and, if `plan.md`
is not beside the plan, `--role-table <out>/plan.md`, so the compiler checks the role table
against the cells the plan creates; fix the table or the cells on `ROLE_TABLE_FAIL`), the
experiment receipts (including a captured `CLOCK_SEARCH:PASS` when the spec pins derived
fabric clocks, and a captured `PREFLIGHT:OK <src>-><cell>/<port>` for each dedicated port the
spec names), `proven/*.json` and `proven_ip_config.json` referenced by the plan, and the scratch project closed with its probe directory deleted (`close_project`
in the log, no `*.xpr` under `<out>`). Then stop. Do not rerun the binding probe
unless the environment changed, repeat documentation searches already made, write your own
checker scripts or summary files, or read `lib/*.tcl` (`ipcfg::help` answers call shapes).

**Each plan stage proves its own result.** Put in each stage the checks its design needs,
after the work they check, so running that stage alone shows whether it succeeded. A check is
needed only when the design has what it checks; a design without a NoC is never asked for a
NoC check. With `--spec`, the compiler refuses a stage that lacks a needed check
(`STAGE_GATE_FAIL`, naming the reason):

| Stage | Check | Needed when |
|---|---|---|
| `board` | `require_board_preset` | the plan targets a board |
| `board` | `ipcfg::preset_drift <ps>`, after creating the processing system and applying its board preset | the plan targets a board and creates a processing system |
| `board` | `attach_board_memory`, after creating its NoC | the plan attaches board memory to a NoC |
| `cells` | `gate_inventory` | always |
| `clocks` | `check_undriven_clocks` | always |
| `clocks` | `set_clock_contracts`, `check_interface_clock_domains` | the spec has clock/reset contracts |
| `clocks` | `check_mipi_video_clocks` | the plan creates a MIPI CSI-2 receiver |
| `clocks`, `qos` | `check_mem_noc_sysclk`, `check_memory_subsystem` | the plan attaches board memory to a NoC |
| `connect` | `check_undriven_clocks` | always |
| `connect` | `check_irq_paths` | the spec has interrupt contracts |
| `qos` | `check_required_reachability` (NoC routes are written with their QoS here) | the spec has reachability contracts |
| `connect` | `check_camera_isp_format` | the plan creates a MIPI CSI-2 receiver and an ISP |
| `connect` | `attach_noc_port_clocks`, before `check_undriven_clocks` | the plan creates a NoC |
| `qos` | `check_noc_pre_address` | the plan creates a NoC |

`check_clock_contracts` is the exception: it reads values Vivado sets only when the design
validates (a clock wizard's input frequency, a reset synchronizer's `C_EXT_RESET_HIGH`), and
no build stage reaches a valid design. Put it in a stage after `qos`, after
`validate_bundle`; the compiler refuses it anywhere earlier. `final_contract_gate` runs it too.

The `board` stage leaves what the board owns: the processing system with its preset applied,
and the memory NoC attached to board memory. Other cells start in `cells`. Every readback
of a cell comes after the operation that creates it; the compiler refuses it otherwise.

Only stages the plan has are checked. A check is also refused in a stage that runs before
the one in this table: `check_memory_subsystem` in `clocks` judges a DRAM window `qos` has
not opened yet, so it can only fail there.

The plan contains:
- **inventory**: one line per IP the spec names, `<vlnv-or-name> <count>`; note the phrase
  that gave each count. Do not list what automation will add (SmartConnect, resets) unless
  the spec pins those numbers. **Interconnect partitioning is the exception**: how many NoC
  instances the traffic is split across is a design decision with consequences at
  implementation, and nothing downstream makes it for you — `add_noc_si` attaches masters to
  a NoC you already chose. Record the count and the reasoning, or record that one NoC is the
  default you took for lack of a partitioning statement.
- **roles**: which cell is the PS, memory NoC, camera/ISP/codec/AIE, control masters.
- **built-in peripherals**: a hardened IP that can build its own peripheral does so by
  default. An ISP instance's IIC/GPIO Select defaults to PL, which builds an I2C and a GPIO
  core inside the ISP (PG432, Resources tab). When the plan drives those sensor-control pins
  from discrete cells, set the ISP's selects to Disable in the cell stage.
  `ipiasm::check_isp_resources` reports any left on PL with nothing connected.
- **functional contracts**: every required connection, control master→target reachability,
  interrupt source→processor obligation, memory master→memory path, visibility rule, and
  software-visible device.
- **clock/reset behavior**, **address windows** (offsets only when listed under
  `fixed_address_windows`), and **out of scope** (host software, off-chip devices).
- **derived clock infrastructure**: a scratch-proven generator partition/output
   order and reset-bearing domains, separately from spec-pinned inventory counts.
   Use [clock planning](clock-planning.md); a failed shared generator is
   not proof that the part cannot meet the requirement. Never weaken a frequency
   requirement to accept a legal but out-of-tolerance wizard result.
- **questions**: unresolved functional choices, physical mappings and software requirements.
   Resolve implementation-only choices with evidence rather than asking the user to choose
   arbitrary topology. Ask one consolidated question for genuinely unresolved requirements.
- **evidence and remaining checks**: link each selected capability to live observations;
   identify the full-system QoS, address, clock/reset and timing checks that must run later.
   Scratch results never replace independent readback in the delivery design.

If a manifest JSON is also supplied, `ipiasm::reconcile_spec <manifest> <expect_helper>` can
diff the BD against it later; it is optional and needs both files to exist. A manifest is an
implementation plan and cannot replace the functional contracts in DesignSpec.

**Default greenfield path: compiled plan plus staged repairs.** Translate `plan.md`
into a whitelisted `assembly_plan.json`, compile it as described in
`ASSEMBLY_PLAN.md`, and execute it with `ipiasm::assemble_from_plan`. Any repair
then uses one uniquely named `<out>/.ipiasm/<run-token>_<stage>.tcl` file. Each
script must:

- start with `ipiasm::stage <name>`;
- use documented `ipiasm::*`/`ipcfg::*` operations for writes;
- batch all writes and the stage's read-only checks;
- print one structured stage result as its final line;
- be sourced once with `verbosity: full`.

Do not issue dozens of one-line writes interactively. If a stage fails, use focused queries
to identify the violated contract, **edit that same stage script**, and re-source it **once**.
Do not create `repaired_*`, `*_v2`, or additional stage files for the same gate. Do not
`close_bd_design`, `open_bd_design`, or `open_project` as part of a repair — the BD stays
open; fix the script and rerun it. Record the repair and its evidence in `plan.md`.

`ASSEMBLY_PLAN.md` documents technology binding and the operation whitelist.
Create and execute `assembly_plan.json` before direct topology mutation.
`BIND_FAIL` blocks operations whose technology remains unresolved; it does not
license bypassing the executor. Producing binder artifacts after the design is
already built is not execution evidence. Successful execution records the
plan's normalized path and SHA-256; editing it invalidates that evidence.
When binding is used, run its disposable probe before creating the final project, close the
probe project but retain the session, and never reopen it after `final_contract_gate`. The final
project/BD must be the only delivery context left observable to the caller.

### 0b. BOARD GATE
```tcl
ipiasm::stage board
ipiasm::register_board_repo <abs board_files dir>  ;# only when the request ships board files: BOARD_REPO:OK boards={..}
ipiasm::discover_board_parts <hint>    ;# BOARDS:n {..}
ipiasm::require_board_preset <hint>    ;# READY:board_part=.. opens the gate | ASK(board):..
ipiasm::board_interfaces               ;# BOARD_INTFS:n {name vlnv= ip= preset=; ..}
ipiasm::checkpoint board
```
When the inputs ship a board-file directory, register it with `register_board_repo` before
`require_board_preset`. `board.repoPaths` is a Vivado parameter, so `set_property
board.repoPaths` fails. On an open project the parameter alone does not refresh the catalog,
so the helper also appends the project's `BOARD_PART_REPO_PATHS`, then proves that
`get_board_parts` lists a board from that directory.
`<hint>` is optional and only breaks ties between several boards for the same silicon. Pass a
board name fragment if the request names a board, the part if that is all you have, or nothing
at all — the project's PART is what actually selects the board file, and a hint that matches
nothing is ignored rather than treated as "no board". If a board file is registered for the
part, this gate resolves itself: one match is taken, and so is the newest of several
file_versions of one board.

`ASK(board)` therefore means the board really is not there (or two different boards fit the
silicon), and it is an escalation: put it to the user with the options the line lists and wait.
Building blind is the one failure that cannot be recovered later. Only after the user says
"build without a board" do you `gate_override board "<their words>"`.

Board-owned implementation defaults are not unresolved functional requirements. If the
DesignSpec does not constrain boot source, debug transport, or another implementation-only
PS option, retain the board preset or the automation schema's documented default and record
it in `plan.md`. Do not stop an unattended build to ask for a value that does not change a
DesignSpec contract.

### 1. Cells — hardened first, board-served next, then the rest
```tcl
ipiasm::stage cells
```
Order, in ONE script: **PS wizard** (`board_preset Yes` via the subsystem automation),
**AI Engine**, then every IP a `BOARD_INTFS` line names (create with `ipiasm::create_cell`,
bind with `connect_board_intf`, memory with `attach_board_memory <noc> <n> <DesignSpec regions>`
— pass the spec's memory regions as `{name <n> base <addr> size <bytes>}` dicts and the
helper picks each `MC_CHAN_REGION` selector from the IP's own legal address ranges, in the
same bind (explicit `{MC_CHAN_REGION<i> <value> ...}` pairs are also accepted). The board
preset owns DDRMC5_CONFIG. `ipiasm::region_selectors <noc> <regions>` previews the choice
(`REGION_SELECT:OK selectors={..} ranges={..}`)), then the remaining
spec'd IPs with `create_cell` / `instantiate_n` (config intent fed verbatim to
vivado-ip-configurator). Pin spec values with `ipcfg::require`. A value the board file
set is refused unless the 5th argument `board_override` quotes the spec statement that
names it (`ipcfg::require $cell $d $reason {} {spec ...}`); never override the board
with a value you derived yourself. A board binding that
overwrites pinned values reports `PINNED_RESET ... restore={..}` — pass that whole dict to one
`ipcfg::require` with `board_override`, never one key per repair. `connect_board_intf` needs a real interface
pin whose bus type matches the board interface: never guess a pin name. Some IP (the PS
wizard, for one) take a board interface through a `CONFIG.*_BOARD_INTERFACE` hook rather than a
pin, and those are bound with `ipcfg::board_bind`. On a missing pin, `BOARD_CONN_FAIL` lists the
cell's interface pins, the pins of the matching bus type, and its board hooks. Use one of those.
Use `ipcfg::verify_intent` once per cell at the end of the script, not
per property. Then:
```tcl
ipiasm::ensure_semantic_cell_name <cell> <DesignSpec-block-role>
ipiasm::check_board_hooks              ;# HOOKS:OK n cell(s) bound | HOOKS_FAIL:<cell> configured by hand
ipiasm::checkpoint cells
```
DDR windows appear only once a NoC input is routed to an MC port, so the memory-region
check belongs after the first memory route (connect stage, and in the Stage 2 scratch
probe before the plan is written):
```tcl
ipiasm::check_memory_regions <DesignSpec regions>   ;# MEM_REGION:OK|FAIL|NA
```
Pass the same `{name base size}` dicts. A failure with wrong selectors names the memory
NoC's current selectors, the selectors the missing regions need with their address ranges,
and the rebuild call. Apply that one fix; do not set the selectors with a later `set_property`.
If the selectors are already right, the failure says so and lists where each NoC input routes
(`S00_AXI->{M00_AXI}` means no input reaches an MC). The fix is then routing (`set_qos
<input> MC_<n> ..` or `add_noc_si`), not a rebuild.
The plan compiler rejects a plan that attaches board memory without a later
`check_memory_regions` call, or whose region selectors cannot cover the regions it lists.
Pass every memory region the DesignSpec states, in whatever form the spec recorded it;
`{}` reports NA. Each region must be held by its own published DRAM segment at least as
large as the region. A missing segment means a controller region is not enabled.
`CELLS_OK` requires both the right instances and the required configuration. Inventory alone
does not prove configuration. For every DesignSpec parameter, resolve its live property,
apply it through vivado-ip-configurator, and require `verify_intent`/`audit_intent` success before
checkpointing. A supported cell left at unverified defaults is an incomplete cell stage. Preserve each
functional block's semantic role in its instance name; catalog-default names are not enough
for independent readback to distinguish two cells with the same VLNV.

Before accepting Stage 2 and again before the cell-stage checkpoint, compile the whole plan
with `compile_assembly_plan.py <plan> --spec <current-design-spec> --out <checked-tcl>`
through the configured Python launcher. Managed execution automatically supplies the adjacent
`design_spec.json` and includes its hash in compilation and stage identities. The compiler
also discovers an adjacent spec when `--spec` is omitted; a bound spec hash without its file
fails closed. Generic plans with no spec remain supported, but are not DesignSpec coverage
evidence. Maintain `parameter_coverage` as documented in
ASSEMBLY_PLAN.md. Missing parameters, stale source values and missing readback operations
fail compilation. Review the mapping against the requirement source: structural coverage
does not prove that a chosen property implements the intended function.

Use `ipiasm::check_cell_config <cell> {CONFIG.KEY value ...}` in the cell stage for actual
readback. It returns `CELL_CONFIG:OK` or `CELL_CONFIG_FAIL`; empty maps, nonexistent properties
and drift fail. Defaults that match the requirement are valid. Register required values
with `ipcfg::require` so the final requirement audit also detects later drift.
Non-IP responsibilities need an explicit external owner and rationale, never an unexplained
deferral of a hardware setting. Later-stage clock or routing checks must name their owning
stage; they are not cell-stage readback evidence.

Every instantiated AXI BRAM controller must have its exposed BRAM ports connected to memory
before inventory can pass, so connect them in the cells stage, before `gate_inventory`, with
`connect_intf <controller>/BRAM_PORTA <memory>/BRAM_PORTA`. A BRAM-to-BRAM connection is the one
`connect_intf` makes before the inventory gate. `check_bram_backing` is called by `gate_inventory`; a controller
alone, an external port or a hierarchy shell does not count as memory. This check establishes
connectivity, not capacity or module-reference behavior.

A memory behind a BRAM controller runs in memory-controller mode: its size, depth and port
widths are generated by the controller, from its data width and the address range assigned to
it, when the design is validated (PG326, PG078). Set only what the memory owns (memory type,
primitive) and the controller's data width. Never set the memory's size or widths: before
validation they read back as defaults, so a match proves nothing, and validation then
overwrites them. Read the required byte capacity back with `check_cell_config` on the
memory after the address map is applied and the design validated. A module-based memory also
needs its own behavioral evidence. An aperture with no memory behind it is not memory.

### 1b. INVENTORY GATE
```tcl
ipiasm::gate_inventory {<ip> <count> ... from plan.md inventory}
#  INVENTORY <ip> built=n want=n OK|SHORT|OVER   (one per line)
#  INVENTORY:OK n ip(s)   opens the gate  |  INVENTORY_FAIL:short={..} over={..}
```
`SHORT`: build the missing cells now. `OVER`: delete the duplicate. Nothing wired, declared or
addressed against the wrong cell set is reusable, so do not proceed and "add them later".
After `ipiasm::init`, clock/connect/external-interface helpers enforce this gate in code.
Immediately after PS automation, run
`ipiasm::check_ps_fabric_irqs <ps_cell> <required interrupt outputs>`; do not begin IRQ
wiring until it reports `IRQ_CAPACITY:OK`. Capacity is only a count. After wiring, register
the DesignSpec interrupt order as a sink-to-source table and trace it:
```tcl
ipiasm::set_irq_contracts {
  {<ps>/<irq_pin> <source_cell>/<irq_pin> <spec_block>.<spec_output>}
  {<intc>/<intr_pin>[<n>] <source_cell>/<irq_pin> <spec_block>.<spec_output>}
  {<ps>/<reserved_pin> CONST:0}   {<ps>/<unused_pin> OPEN}
}                                  ;# IRQ_CONTRACTS:SET
ipiasm::check_irq_contracts        ;# IRQ_PATHS:OK n=.. covered={labels} | IRQ_PATHS_FAIL
```
The trace goes through concat, slice and constant cells and across hierarchy. A failure names
each wrong slot, the constants that stand in for real sources, the swapped pairs, and any
concat whose output drives nothing. An undriven interrupt input is tied low without a
validation error, so the trace is the only readback that shows it. Give every source slot the
DesignSpec label `<source>.<output>`. The final gate re-traces the table, and its coverage
check requires every output of every in-design interrupt contract to appear in `covered`.

### 2. Clocks / resets (automation-first)
```tcl
ipiasm::stage clocks
ipiasm::preflight_clock_plan <frequency-checks> <reset-domains>   ;# CLOCK_PREFLIGHT:OK|FAIL, read-only, run FIRST
ipiasm::wire_clk_rst_auto <clk_source_pin> <clkrst_rule>      ;# preferred
ipiasm::set_clock_contracts <bound-frequency-checks> <bound-reset-domains> <source-files> <scratch-fingerprint>
ipiasm::register_clock_contracts {{id <id> cell /<cell> expected <hz>} ...} <out>/clock_fingerprint.tcldict
                                              ;# same registration; drivers, references and reset domains traced live
ipiasm::check_clock_contracts                 ;# CLOCK_CONTRACTS:OK, only after the design validates (validate stage)
ipiasm::save_clock_contracts <absolute-path>  ;# CLOCK_CONTRACTS:SAVED, after the gate passes
ipiasm::load_clock_contracts <absolute-path>  ;# CLOCK_CONTRACTS:RESTORED in a later session
ipiasm::ensure_reset_domain <reset-cell> <clock-pin> <async-reset-pin> {<target-reset-pins>} [<locked-pin>]   ;# source must be a reset pin with POLARITY; wired directly
ipiasm::wire_mem_noc_sysclk <memory_noc>       ;# internal DDR/LPDDR sys_clk* <- PS hsm*_ref_clk
ipiasm::check_mem_noc_sysclk <memory_noc>      ;# MEM_SYSCLK:OK
ipiasm::sync_intf_freq_hz <clocked_source_intf> <connected_intf_or_top_port>
ipiasm::check_interface_clock_domains          ;# CLOCK_DOMAIN:OK|FAIL, every connected interface pair
ipiasm::set_interface_contracts {<contract-dict> ...}
ipiasm::preflight_interface_contracts          ;# INTF_PREFLIGHT:OK | INTF_PREFLIGHT_FAIL | INTF_PREFLIGHT:NA
ipiasm::preflight_connect <src> <dst> [{allow_cdc 1 src_clk .. dst_clk ..}]   ;# PREFLIGHT:OK|PREFLIGHT_FAIL, read-only
ipiasm::disconnect_intf_edge <src> <dst> [<justification>]                    ;# SUCCESS|SKIP|DISCONNECT_REFUSED|DISCONNECT_FAIL
ipiasm::check_mipi_video_clocks                ;# VIDEOCLK:OK|FAIL, configured throughput
ipiasm::check_undriven_clocks                  ;# UNDRIVEN_CLK:OK|FAIL, BD 41-758 before validate
ipiasm::create_port <name> <I|O> [clk|rst|intr|data] [<cell/pin>] [{CONFIG.FREQ_HZ <n>}]   ;# PORT:OK|PORT_FAIL, exact-named scalar port
ipiasm::check_required_ports {{name <port> dir O type clk freq_hz <n>} ...}   ;# PORTS:OK|PORTS_FAIL, spec-named top-level ports
```
A top-level port name the DesignSpec gives (a clock, reset or interface named in the
request) is a contract with the RTL top or software: create that exact port with
`ipiasm::create_port` (`externalize_oos` auto-names ports `<pin>_0`). Naming a
clock after its frequency applies only to clocks the spec leaves unnamed. Run
`check_required_ports` for every spec-named port in the stage that creates it.
Every DesignSpec interface with `scope: top_level` is such a port. It takes the
interface's name, or the bare name at the other end of a required connection that
uses it, and the plan compiler refuses a plan whose `check_required_ports` does not
list it (`BOUNDARY_FAIL`). When the RTL top names that port differently (renamed, or
one port per bit), no wrapper port can satisfy both, so put the RTL name into the
DesignSpec **before binding**: `python3 scripts/boundary_names.py check --spec <spec>
--rtl <top.sv> --wrapper <bd>_wrapper` lists each unmatched interface with related RTL
ports; `boundary_names.py add --spec <spec> <block>.<intf>=<port>[,<port>...]` appends
the bare-ended required connections (`BOUNDARY_NAMES:ADDED`). Rerun the spec validators,
then list those RTL names in `check_required_ports`. A port driven by some bits of a wider pin (one GPIO bit
as a reset output) goes through a bit-select cell, with the port wired to its output.
`check_undriven_clocks` needs no DesignSpec input. Clear every pin it lists on cells that
exist at this stage; each line names the clock its interfaces' peers run on. Before
`connect`, a pin whose interfaces are all still unwired, or a NoC `aclk` with no port yet,
is listed as *deferred*, not failed: its driver follows a peer that does not exist yet.
Deferral ends at `connect`, so a pin still listed there is undriven. A pin that carries no
interface is never deferred; drive it at `clocks`. From the
clocks stage on, `ipiasm::checkpoint` runs it and returns `CHECKPOINT_FAIL` without saving
until it passes. Repair the stage script as rule 4 says; do not skip the checkpoint.
`preflight_connect` is stricter than `check_connect` alone, which only answers
bus compatibility and will call an AXI-Stream edge legal across two unrelated
clocks. It shares one same-domain verdict with `preflight_interface_contracts`
and `disconnect_intf_edge`, so a contract check and a topology change cannot
disagree about the same edge.
Derive reset synchronizers from reset-bearing consumers grouped by clock identity,
reset source/polarity and release/lock policy, never by rounded frequency alone.
Clock-only consumers do not require a fabric reset controller. Generated clocks
must qualify `dcm_locked` from their clock resource. When producer and consumer stream clocks
cannot share one legal rate, insert an AXI4-Stream clock converter.

Run `preflight_clock_plan` on the planned checks and resets **before creating or
configuring anything**. It is read-only and answers, in one pass, the two facts a
clock stage otherwise learns one failed `source` at a time: the frequency each
driver actually reports, and the polarity each reset source actually carries.
Pins that do not exist yet count as `pending`, not defects, so it is safe on a
partly built design. A driver reporting 99999001 Hz never satisfies a contract
written for 100000000 Hz however the sink is configured — find that here, not
after four cells have been created against it.

`set_clock_contracts`, `check_clock_contracts` and `check_interface_routes`
report **every** defect they find in one pass, prefixed `N defect(s):`. Fix the
whole list before re-sourcing; re-running to see the next one wastes a cycle per
defect.

Registered contracts live in an interpreter variable and do not survive a new
Vivado session. After the clock gate passes, `save_clock_contracts` next to the
stage evidence; in a later session `load_clock_contracts` instead of re-sourcing
the clock script, which otherwise trips
`contracts already registered; repair implementation, not requirements`.

Configure and connect the scratch-proven architecture using existing configurator
and assembler helpers after automation. Do not use the legacy `build_clocks` /
`fanout_domain` shortcut for new exact-frequency plans. Native propagation and
validation are required before checking registered contracts. Clock checkpoints,
QoS/address stage entry, final contract validation and delivery guards recheck
them; a stale or missing registered requirement is not repaired by changing it.

Register interface contracts **after configuring the actual IP instances and before
connecting them**, then run `preflight_interface_contracts`. Different clock domains fail
unless the contract explicitly describes a real CDC bridge with `allow_cdc 1`; never infer
CDC from interface type. The dict schema, the hierarchy-boundary keys, and what the
mutation helpers refuse on their own: REFERENCE § *Interface contracts*.

### 3. Connectivity (automation-first)
```tcl
ipiasm::stage connect
ipiasm::externalize_interface {name <port> mode <Master|Slave> vlnv <interface-vlnv> properties {...}}
ipiasm::create_port pl_fpd_irq1 O intr <cell/irq-pin>   ;# one spec-named scalar port per bit, e.g. interrupts
ipiasm::check_ps_noc_masters                    ;# PS_NOC:OK|PS_NOC_FAIL|PS_NOC:NA, coherent PS NoC ports
ipiasm::check_wrapper_boundary <rtl-top> [wrapper] ;# BOUNDARY:OK|BOUNDARY_FAIL|BOUNDARY:NA, names the RTL instance uses
ipiasm::name_port <current> <wanted>           ;# PORT_NAME:OK|PORT_NAME_FAIL, rename an exported port or interface
ipiasm::connect_external_axi <top_port> <internal_slave> <axi4_rule> <config> <fallback_sc> <clk> <reset>
ipiasm::connect_axi_auto <master_pin> <slave_pin> <axi4_rule>   ;# falls back to connect_intf
ipiasm::build_control_fanout <M_AXI> {<S_AXI_LITE> ...} <sc_cell>  ;# one control master → many slaves
ipiasm::add_noc_si <noc> <dest> <master_pin> <rd_MBs> <wr_MBs>    ;# grow a NoC for a PL master; gives the port its own aclk on the master's clock
ipiasm::attach_aie_config <aie> <noc_master>; ipiasm::check_aie_config   ;# AIE:OK|AIE_FAIL|AIE:NA
ipiasm::check_required_reachability {<space-glob> <segment-glob|DRAM> ...}   ;# REACH:OK|FAIL|NA
ipiasm::check_irq_paths {{<sink> <want> ?<label>?} ...}   ;# IRQ_PATHS:OK, when the spec has interrupt contracts
ipiasm::check_camera_isp_format                ;# FORMAT:OK, only when a camera receiver feeds an ISP
ipiasm::attach_noc_port_clocks                 ;# NOC_CLK:|NOC_CLK_FAIL, every wired NoC port on its peer's clock
ipiasm::check_undriven_clocks                  ;# UNDRIVEN_CLK:OK
ipiasm::checkpoint connect
```
Each NoC AXI port must be listed in the `ASSOCIATED_BUSIF` of an `aclk` driven by the clock
its peer runs on, and ports whose peers run on different clocks never share one `aclk`
(PG406). `add_noc_si` does this for the port it creates. A plan that sizes a NoC directly
(`NUM_SI`, `NUM_MI`, `NUM_CLKS`) owns that mapping, so wire the ports first, then run
`attach_noc_port_clocks`: it reads each wired port's peer and gives the port an `aclk`
on that clock. Listing every port on `aclk0` fails `validate_bd_design` with
"Error resolving clock-association", and the NoC never elaborates.
The connection stage's requirements are the DesignSpec, the plan and any RTL top the user
names. Read those directly; do not search the workspace or the skills for them.
The PS drives its coherent (CCI) NoC manager ports in interleaving mode, so if one is used
all of them must connect to the same NoC instance with the same routes (PG450, *PS NoC
Interfaces*; PG352, *NoC Interfaces*). The PS marks these ports `CONFIG.MY_CATEGORY ps_cci`;
connect each one as you connect the first, and require `PS_NOC:OK`. Vivado accepts a partial
set with only `BD 41-2670` incomplete-path warnings, so never explain those warnings away.

When an RTL file instantiates the block design's wrapper, its named port connections are the
boundary contract. Automation names exported ports after their board interface or pin, so
run `ipiasm::check_wrapper_boundary` once the boundary exists, apply each rename it lists
with `ipiasm::name_port`, create any port it reports missing, and
require `BOUNDARY:OK` before export.

Build the reachability pairs from the DesignSpec `memory_reachability` (target `DRAM`)
and `control_reachability` contracts by resolving each semantic master and target to its
bound address spaces and segments. Every address space a pair's glob matches must reach the
target, so name the master's own spaces, never a whole processor (`<ps>/*`). With `--spec`,
the compiler refuses a plan with a required contract that no pair covers
(`REACHABILITY_FAIL`, listing them). The check reads topology, not the address map, so it
needs no address values and works whatever form the address requirements took. Rerun it
at the NoC traffic gate; the address stage must not be the first place a missing path shows.
On 2026.1+ pre-flight edges with `check_connect` / `legal_intf_targets` / `legal_pin_targets`
only after actual IP metadata and clocks/resets are applied (the wrappers do it
automatically; `INTF_FAIL:illegal:<reason>` is machine-readable).
`add_noc_si` copies the connected master's `DATA_WIDTH` onto the new SI; never repair it by
port index after the fact. A NoC SI's `FREQ_HZ` is read-only and comes from its associated
clock. When moving ordinary interfaces or top-level ports to another clock domain, rerun
`sync_intf_freq_hz` before validation.

**Stage-script budget:** reuse and edit the one script for a failed stage. The
independent readback fails Case 6 when more than 24 `.ipiasm/*.tcl` scripts are
created. A repair does not get a new file; update the failed stage's script so one
rerun checks the complete stage contract. Run each script with
`ipcfg::source_reported <file>`: before running anything it refuses a script that calls a
command that does not exist in this session (`SCRIPT_CHECK_FAIL`, naming them), and on a
failure it prints the message and file/line trace. `ipcfg::script_check <file>` runs the
same check without sourcing.
When Vivado rejects a value because another parameter must change with it (the message
names the other parameter, for example IP_Flow 19-3478), set both in one `-dict`.
When a script checks readbacks, collect every mismatch before failing (or use
`ipcfg::audit_requirements`), so one repair can fix them all.

**Updating an existing NoC design.** Never delete only an interface net: the logical
`CONFIG.CONNECTIONS`/QoS route, Address Editor mappings, and `ASSOCIATED_BUSIF` clock
ownership can survive it. Plan first, then apply:
```tcl
ipiasm::plan_remove_noc_path <noc/Snn_AXI> [<destination>]
ipiasm::remove_noc_path <noc/Snn_AXI> [<destination>] -apply
```
Naming a destination removes only that route and keeps the ingress/net when other
destinations remain. Omitting it removes the whole ingress path. PS/VISP/AIE/VCU hardened
endpoints are refused unless the caller repeats with `-force` after the functional contract
authorizes removal. The operation checkpoints first, preserves NoC port numbering, clears
stale QoS/clock/address state, and closes the QoS gate; rerun the pre-address gate, address
stage, post-address gate, and validation before delivery.

### 3b. NoC TRAFFIC GATE (Versal with a NoC; NA elsewhere — say so)
```tcl
ipiasm::stage qos
ipiasm::bw_video 1920 1080 60 12  ;# w h fps bpp [overhead%=10] -> 206 MB/s
ipiasm::bw_mipi  <lanes> <line_rate_Mbps>
ipiasm::bw_axi   300 128          ;# clk_MHz width_bits [util%=100] -> 4800 MB/s
ipiasm::qos_classes               ;# BEST_EFFORT | ISOCHRONOUS | LOW_LATENCY
ipiasm::set_unused_qos_routes {<si_pin>/<dest> {<requirement-based reason>}}
ipiasm::apply_qos_plan {<si_pin> <dest> <rd> <wr> <class> ...}      ;# the whole plan
ipiasm::set_qos <si_pin> <dest> <rd> <wr> [<class>] [<burst>=16]    ;# one targeted merge
ipiasm::check_noc_pre_address <spec NUM_MC>   ;# NOC_PRE:OK opens QoS gate without requiring mapped DDR
ipiasm::check_required_reachability {...}     ;# REACH:OK, same pairs as the connect stage
```
The three `bw_*` are arithmetic, not design queries: MB/s in, MB/s out, nothing read from
the design. What is design specific is where the arguments come from — frame geometry from
the spec, `CMN_NUM_LANES` × `DPY_LINE_RATE` off the CSI-2 RX, a port's clock and width.
`bw_video` pads 10% for blanking and rounds up, so it over-declares rather than starving a
path; `bw_mipi` is a link ceiling, not a measurement, so prefer `bw_video` whenever the
resolution and frame rate are actually known. Active-route bandwidth must come from the
workload/spec. The explicit idle reservation below is not a workload estimate.
An interface's capacity is not its workload demand. `bw_axi` defaults to 100% utilization;
use that only when the workload requires full-rate traffic, never because a port exists.
For each intentionally unused but connected owning route, record its exact
`<si_pin>/<dest>` and reason with `set_unused_qos_routes`, then apply
`ipiasm::set_qos <si_pin> <dest> 1 1 BEST_EFFORT` (or the equivalent `apply_qos_plan`).
This reserves 1 MB/s in each direction, not 1 MB of storage. These declarations replace
the previous unused-route set and close the
QoS gate. They are scoped to the current project directory and BD, are not stored in the
BD, and must be replayed from the assembly plan after restarting/resuming a session.
Passing `{}` clears them. No wildcards or blanket `check_qos` allow-list bypasses are valid
for unused traffic. The checker verifies ownership, route existence, exact 1/1 bandwidth,
and both traffic classes as `BEST_EFFORT`; it includes the reservation in bandwidth totals.
Declared-unused routes at 0/0, other bandwidths, or other classes fail.
Keep configuration/register-access routes distinct from unused data-master routes.
This is the user-approved idle-connectivity convention, validated on retained NoC2 DDR
routes in Vivado 2026.1, not an AMD-documented minimum or a guarantee for active traffic.

**`initial_boot` and `[BD 41-3285]`.** Set a route's `initial_boot` exactly as the
requirements state (`set_qos … <burst> true`). Vivado then warns
`[BD 41-3285] Initial boot should not be enabled for paths to or from ISP` (or VCU). This
is a WARNING, not a legality failure: native validation still passes. Per AMD's Vivado
Segmented Configuration tutorial, `initial_boot` marks paths that must exist before PL
configuration and only matters when the project's segmented-configuration property is
enabled; otherwise it "is carried through the implementation flow but ignored by
`write_device_image`". So never drop or change a stated `initial_boot` requirement because
of this warning, and never stop a stage over it. Record it as an expected advisory warning
in the stage evidence, and raise it as a question only if the requirements also enable
segmented configuration.
Confirm the unused source has no required initialization, background, or runtime workload;
otherwise size that traffic explicitly. Before activating it, remove its unused declaration,
provision workload bandwidth, and rerun the QoS/NoC checks and native validation.
Traffic class applies to every destination on an ingress; check for active sibling routes
before changing it to `BEST_EFFORT`, and escalate conflicting class requirements.
PG406 states that zero disables a traffic direction and later accesses hang. The live
0/0 trial failed with BD 41-2249; the 1/1 `BEST_EFFORT` trial passed. Require native
validation for each design; do not suppress diagnostics or infer closure from ignored paths.
Preserve required connectivity and address contracts. Removing unused routes changes
memory visibility and needs requirement approval. Disconnected routes need no reservation.
`LOW_LATENCY` is read-only. `set_qos` rejects it when `write_bw` is nonzero; use
`BEST_EFFORT` or `ISOCHRONOUS` for bidirectional/write paths.
Declare traffic before addressing: `CONFIG.CONNECTIONS` is a connectivity change and applying
it after stage 4 can unmap DDR from every master at once on a design that still validates.
`check_noc_pre_address` deliberately checks that DDR is published and the traffic declaration
is legal without requiring a master to hold DDR before Address Editor has run. Never use
`gate_override qos` to break that ordering; the pre-address check is the supported operation.

### 4. Address map (automation-first)
```tcl
ipiasm::stage address
ipiasm::place_named_windows {<name> <space> <seg> <offset> <size> ...}   ;# pinned windows FIRST
ipiasm::auto_address                          ;# SUCCESS | ADDR_FAIL (believe it; do not re-run)
ipiasm::restrict_master <dma_cell> {<seg-globs>}   ;# say what a data mover is for
ipiasm::enforce_window_scope <name> <slave_seg> {<exact allowed spaces>} <offset> <range>
# WINDOW_SCOPE:OK required=N present=N extra=0 moved=0
ipiasm::check_noc_post_address                ;# NOC_POST:OK — functional masters now hold DDR
ipiasm::check_overmap <spec_max_ps_windows>   ;# explicit DesignSpec visibility limit
ipiasm::check_noc_legality                    ;# NOCLEGAL:OK (forces validation when needed)
ipiasm::checkpoint address
```
**Place the offsets software pins, then derive the rest.** Only addresses something
hard-codes — a driver constant, a boot ROM, a fixed BAR — have to land anywhere in
particular; place those with `place_named_windows` before `auto_address`, and they survive
it. Every other offset reaches software through the generated device tree, so let
`assign_bd_address` choose it and do not treat a differing offset as an error.
Automatic assignment may choose different offsets while preserving required reachability.
Treat only spec-declared software or hardware windows as fixed.

**Addressing chooses offsets; it does not decide visibility.** `assign_bd_address` maps
every slave into every master that can reach it, so each PL master comes out holding an
aperture onto the processing system's internals. Deciding what a master has no business
reaching stays manual — `restrict_master`, then `check_overmap` to confirm. Skipping it is
not cosmetic: the same experiment on a design carrying no exclusions left its PL masters
addressing many unintended processing-system windows and can cause a
SmartConnect route conflict.

A fixed window's `masters` contract is an **exact allow-list**, not a minimum. After automatic
assignment, call `restrict_window_to_spaces`, then `check_window_scope`; checking only the
required spaces misses extra mappings. A visibility rule on a slave with **no** fixed window
has no offset to compare, so assert it with `ipiasm::check_visibility <slave_seg> {<spaces>}`
instead — it counts a mapping that was never made and one that was excluded as equally
invisible. Every `visibility rule` recorded in `plan.md` needs one of these two checks named
against it; an unchecked one is an untested contract, not a satisfied one. Once those checks
pass, do not add/remove/reconnect a cell and do not run addressing again — topology changes
can recreate excluded mappings.

If a reference address map is *supplied as an input*, apply it with the tool the user
supplied, dry-run first, resolving duplicate-VLNV ambiguities by role and connectivity.
The supplied tool replaces the placement calls above, not the checks: run this section's
post-address checks on its result (`check_noc_post_address` when the design has a NoC).
Absent one, do not go looking for a reference design to copy offsets from.

### 5. Normalize → reconcile → validate → report
```tcl
ipiasm::stage validate
ipiasm::normalize_sim_model tlm                ;# align SELECTED_SIM_MODEL across NoC/AIE
ipiasm::gate_inventory {...same list...}       ;# still OK after automation? (cheap)
ipiasm::check_memory_regions <spec regions>    ;# when the design attaches board memory: QoS and addressing can reset selectors
ipiasm::close_gaps <domain_map> <windows> <oos_pins> 3   ;# validate→autofix→validate; CLOSED | NEEDS_USER
ipiasm::final_contract_gate {
  {<window-name> <slave-seg> {<exact allowed spaces>} <offset> <range>}
  ...
} <NUM_MC> <ps_cell> <required_irq_outputs> 1 1 auto <spec_max_ps_windows> {<required_dram_master_patterns>} auto ;# FINAL_CONTRACT:PASS
ipcfg::audit_requirements                      ;# REQ:OK n/n — the design still carries what `ipcfg::require` pinned
ipiasm::assembly_report                        ;# ASSEMBLY:CLEAN
ipiasm::gate_status; ipiasm::timings
```
**A plan-built design calls `ipiasm::final_contract_gate_from_plan` instead**, after the
plan's `validate` stage has run. It takes every argument from the executed plan and its
DesignSpec: the plan's `check_window_scope` ops, each named after the spec window with the
same offset and range; the plan's `validate_bundle` arguments; and as DRAM masters, the
spaces allowed into each window a `memory_reachability` contract requires on a
`memory_controller` block, plus the patterns of each plan `check_required_dram_masters` op
(the way to prove memory reachability when the spec fixes no window on the memory;
`FINAL_GATE_GAP:memory` names it). It rechecks and records the plan's required reachability
and reruns each plan `check_visibility` op, recording it as `visibility_policy` evidence,
so a spec with visibility rules needs those ops in the plan's address stage (the
compiler prints `FINAL_GATE_GAP:visibility` when they are missing). Record evidence for
any other category first (a supplied address-map checker:
`ipiasm::record_evidence <label> {<category>} <result>`). Do not write a script to
assemble the gate's arguments.

The DRAM role list is exhaustive: derive one pattern for every DesignSpec block
that reads or writes external memory. Omitting a role is not permission to leave
it unreachable.

**`ASSEMBLY:CLEAN` means every pin is wired or declared open.** An interface used signal
by signal (a GPIO's `gpio_io_o`, an interrupt's `irq`) counts as wired, and so does a clock
or reset carried inside a wired interface. A port the design leaves unused on purpose (a
tile with no source, a UART the spec reserves without pins, a processing-system port the
board preset enables and nothing uses) is declared in the plan's `validate` stage with
`ipiasm::declare_open {<pins>} <reason>`, and the reason cites the DesignSpec. The report
lists declared pins with their reasons, not as gaps. A declaration cannot hide a used pin
(a wired pin is refused) or a missing boundary port (`check_required_ports` fails on that).
`ASSEMBLY:GAPS` does not fail the final gate, but it is not a clean delivery: classify
each listed pin, then wire it or declare it.
Reconcile *before* the expensive validate loop, not after it. Every `ASK:` line goes to the
user (or the report under `policy unattended`). Do not claim a working design while an ASK is
open or any gate reads 0.
Use one `final_contract_gate` per repair iteration rather than validating after each
individual edit. It runs the validation bundle, then rechecks every fixed window's exact
allow-list after all topology/address work, audits pinned configuration requirements, and
captures a full design fingerprint. `VALIDATE:PASS` or `BUNDLE:OK` alone is not delivery
evidence. Fix the whole returned failure before rerunning the gate.
The gate also requires a non-empty `REQ:OK` audit by default. Its final
`require_pinned` argument may be set to `0` only when diagnosing a design reopened from disk,
because the in-memory requirement registry is not persisted; never disable it for delivery.
It automatically discovers `assembly_plan.json` under the workspace/output directory. When
one exists, `PLAN_EXECUTION:OK` for that exact content hash is mandatory; an unexecuted or
subsequently edited plan makes the gate return `FINAL_CONTRACT_FAIL`. A missing plan fails
too. Plan `none` and spec `none` are accepted only after `ipiasm::gate_override plan <the user's
reason>`, and spec `none` is refused while a `design_spec.json` exists.
A plan run stage by stage across Vivado sessions passes as `PLAN_EXECUTION:OK ... staged=1`.
If it fails, run the stages the failure names with `assemble_from_plan`. Never pass `none`
for a plan-built design, and never edit a stage script to get past the gate.

**Every DesignSpec contract needs a passing readback.** The gate's last argument is the
DesignSpec path (`auto` finds `design_spec.json` in the workspace or `outputs/`; `none` only
when there is no DesignSpec). The gate maps each required contract to the evidence that proves it
and fails on any it cannot find. Run `python3 scripts/contract_coverage.py derive --spec
<spec>` to see that list before the gate:
- clock/reset contracts and clock domains: `CLOCK_CONTRACTS:OK` (`NA` fails);
- in-design interrupts: `IRQ_PATHS:OK` covering every `<source>.<output>` label;
- fixed address windows: a `<name> WINDOW_SCOPE:OK` window spec for each, or recorded evidence;
- memory reachability: `DRAM_REACH:OK`; connections: `PLAN_EXECUTION:OK`;
- control reachability and visibility: recorded evidence.

For checks the gate does not run itself (`check_required_reachability`, `check_visibility`, a
supplied address-map checker), record the passing result just before the gate:
`ipiasm::record_evidence <label> {<categories>} <result>`. Recorded evidence counts only while
the design is unchanged since it was recorded. Software behaviour and interrupts delivered
outside the design are listed as `deferred`, not dropped.
Interrupts and fixed address windows are checked one by one (`checked_each`); every other
category passes on one readback for the whole category (`checked_by_category`,
`categories={name(count)}`). Report it that way; don't claim each of those was checked.
