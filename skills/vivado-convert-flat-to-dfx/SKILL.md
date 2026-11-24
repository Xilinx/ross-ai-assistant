---
name: vivado-convert-flat-to-dfx
description: >
  Convert a flat Vivado design to a DFX-ready project for a supported
  UltraScale+ or Versal part. The user supplies only the module instance
  name(s); detect Project/Non-Project mode, exact part/family, and whether each
  selected module is RTL or BD/BDC. Validate RP boundaries, convert selected
  modules, create the required DFX configuration structure, migrate timing
  and physical constraints into new static/RM-scoped XDCs without modifying
  source XDCs, and for Versal convert affected Modular NoC paths to an INI
  boundary and validate ownership. Do not create pblocks, implement, or run
  pr_verify. Use when asked to convert a flat Vivado design to DFX.
compatibility: >-
  Requires Vivado 2021.1+ (first production release of Project-mode DFX with
  block design containers and of Versal DFX); Tcl and NoC INI details
  validated on Vivado 2026.1. Targets supported UltraScale+ and Versal parts.
  Links to the segcfg-dfx and vivado-revision-control skills.
license: MIT
metadata:
  version: "1.0.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vivado Convert Flat Design to DFX

Converts a flat Vivado design into a DFX-ready project for supported
UltraScale+ and Versal parts. The user supplies the module instance name(s) to
make reconfigurable. The skill detects Project/Non-Project mode, target
part/family, hierarchy type (RTL or BD/BDC), and design structure; do not ask
the user to determine those facts manually when they are discoverable from
the design.

**Out of scope (do not perform):** pblock creation/sizing, implementation
(`opt_design`/`place_design`/`route_design`/`launch_runs`), and `pr_verify`.
The skill must validate selected RPs, create the DFX structure, migrate
constraints into new XDC files, and convert affected Versal Modular NoC paths
when present. It must leave source RTL/BD/XDC files untouched. The deliverable
is a DFX-ready project/flow with migration outputs and a report; do not claim
implementation signoff.

**Output:** `vivado_agentic_ai_reports/vivado-convert-flat-to-dfx/REPORT.md`,
written relative to the working directory you were invoked from — **not**
inside the new output/project copy created in Step 4, even when that copy has
its own directory name (e.g. `<design>_dfx/`). Contains actual mode/part/RP
paths, DFX objects, every new XDC and its migrated constraints,
unresolved/manual-review items, NoC topology/ownership checks when applicable,
and remaining steps (pblocks, implementation, `pr_verify`).
Write new static and per-RM XDCs under a new output/project area. Never modify
or overwrite the original flat project, BD/RTL, or XDC inputs.

**Prerequisites:**
- **Project mode** — a flat design (RTL or IP Integrator) that is elaborated
  or synthesized, with the target reconfigurable hierarchy already
  instantiated as a distinct cell (module boundary).
- **Non-Project mode** — a folder containing either the design's RTL sources
  or pre-synthesized checkpoint (`.dcp`) files, plus the top-level module
  name and target part.

DFX cannot be retrofitted onto a flattened netlist in either mode — the RM
boundary must exist as a hierarchical cell.

**Project-mode conversion is one-way.** Setting `PR_FLOW` (or generating a
DFX-enabled BDC) converts the project to a DFX project and cannot be undone in
place. Before doing this, explain the consequence, ask the user to confirm
both the conversion and that a non-DFX copy has been saved or archived. Do not
set `PR_FLOW` or generate a DFX-enabled block design until both are confirmed.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Vivado edition | No separate license required — DFX has been included in every Vivado edition (Standard, Design, System) since 2019.1 |
| Target family | UltraScale+ or Versal only. Check the exact part against the installed Vivado release's UG909 Supported Devices table; refuse 7 series, UltraScale, and any unsupported part before modifying files. |
| Design state | Elaborated or synthesized; target hierarchy is a distinct, un-flattened cell |
| Project mode input | A Vivado project (`.xpr`). Uses the **Project Mode** DFX Tcl API (`PR_FLOW`, `create_partition_def`, `create_reconfig_module`) |
| Non-Project mode input | A folder of RTL (`.v`/`.sv`/`.vhd`) or checkpoint (`.dcp`) files, plus the top-level module name and target part. Uses the **Non-Project batch** Tcl API (`HD.RECONFIGURABLE` property, `read_checkpoint -cell`, `link_design -reconfig_partitions`) — UG947 has example scripts for this flow |
| Vivado session | Connected via the MCP Vivado bridge (`vivado_connect`/`vivado_start`) |

---

## Efficiency Guidelines

- **Pass `session_id`** to every `vivado_execute` call when a Vivado session is active.
- **Write reports to file** — do not output full report content in chat; give a short summary only.
- **Do NOT** use `shell ls`, `shell find`, or `shell glob` to locate files. (Tcl `glob` run *inside* `vivado_execute` to discover RTL/DCP files for Non-Project mode is fine — that is a Vivado Tcl command, not an agent-side shell tool.)
- **Do NOT** use Vivado Tcl (`exec cat`, `open`, `read`) to read files. Use `grep`/`sed` via terminal or `read_file`.
- **Do NOT** retry a failed Tcl command with different syntax. Report the error and stop or proceed.
- **Do NOT** retry `vivado_doc_search` more than 2-3 times with reworded queries when it errors or returns nothing useful. Fall back to this skill's own documented guidance (e.g. the NoC `-mode` pairing rule in Step 8, or the Error Handling table) instead of spending further turns chasing an external confirmation that may be unavailable.
- **Do NOT** flatten or re-synthesize the design before the RM boundary is captured — that destroys the hierarchy `create_partition_def`/`HD.RECONFIGURABLE` needs.
- **Do NOT** create pblocks, run implementation, or run `pr_verify` — those are out of scope for this skill.
- **Do NOT** continue on 7 series, UltraScale, or a part absent from the current supported-device list; explain that this skill's DFX conversion path is limited to supported UltraScale+ and Versal parts.
- **Do NOT** set `PR_FLOW` in Non-Project mode — that property only exists for Project-mode DFX.
- **Do NOT** convert a Project-mode project to DFX without explicit user confirmation and a saved/archived non-DFX copy; conversion is irreversible.
- **Do NOT** select an RP by inference, example name, or presumed BDC. Always load the supplied project/design in Vivado, enumerate valid hierarchical module instances, show their exact paths to the user, and wait for the user to name the instance(s) before proceeding.
- **Do NOT** set `HD.RECONFIGURABLE`, `PR_FLOW`, or `CONFIG.ENABLE_DFX` until the user has selected the exact instance path(s) from the live Vivado design. If Vivado cannot be started or the design cannot be opened, stop without making DFX changes.
- **Do NOT** launch or implement a Configuration Run (`launch_runs`, `opt_design`, `place_design`, `route_design`); Standard DFX configuration creation is the only run-setup work in scope.
- Generating IP output products (`generate_target`) and, when elaboration or `validate_noc` needs IP stubs, running the per-IP out-of-context synthesis runs (`<ip>_synth_1`) is allowed. Never launch `synth_1`, an RM synthesis run, or any implementation run.
- **Do NOT** use Abstract Shell or Both modes; both are out of scope for ROSS-53.

---

## Decision Tree — Which DFX Flow Applies?

```
Step A: Detect the current design mode and target part/family from Vivado.
  If no design/project is open, inspect the supplied workspace for an .xpr,
  flat DCP, or RTL source set and infer the matching mode.
  Refuse unsupported targets before changing design state.
  ├─ Project mode
  │    └─ Is the reconfigurable hierarchy inside an IP Integrator block design?
  │          ├─ YES → Block Design Container (BDC) flow
  │          │         Convert the hierarchy to a BDC, set CONFIG.ENABLE_DFX true.
  │          │         See reference/bdc-flow.md.
  │          └─ NO  → RTL / netlist partition-definition flow
  │                    Use create_partition_def directly on the RTL cell.
  └─ Non-Project mode
      └─ Non-Project batch flow
            Load design from the given folder, set HD.RECONFIGURABLE on the
            chosen cell(s), read the matching RM checkpoint if one exists.
            BDC/IP Integrator conversion is not supported in this skill's
            Non-Project path — use Project mode for BDC-based designs.
```

---

## Workflow (Autonomous)

**⚠️ CRITICAL: Execute steps SEQUENTIALLY. Wait for each command to complete.**

**⚠️ The workflow is incomplete until REPORT.md exists.** Do not narrate
("Now converting...") before invoking the write tool — invoke it first, then
summarize.

```
Flat-to-DFX Progress:
- [ ] Step 1: Copy the input first, then detect mode, target part/family, and flat-design state against the copy only
- [ ] Step 2: Enumerate live Vivado RP candidates and get exact user-selected path(s)
- [ ] Step 3: Validate hierarchy, RP boundaries, and unsupported elements
- [ ] Step 4: Confirm one-way Project-mode conversion (copy already made in Step 1)
- [ ] Step 5: Convert RTL and BD/BDC modules to DFX structure
- [ ] Step 6: Create the Standard DFX configuration
- [ ] Step 7: Migrate timing/physical XDCs into new scoped files
- [ ] Step 8: Convert and validate affected Versal Modular NoC paths
- [ ] Step 9: Write REPORT.md with outputs, checks, and open items
```

### Step 1: Open the Design in Vivado and Detect Its State

**Before opening anything in Vivado, copy the entire supplied input
(project/RTL/DCP directory) to the new output location, and do every
remaining step — including this step's own elaboration/discovery — against
that copy only.** Never `open_project`/`open_bd_design` the original input
path in Vivado at all, even just to inspect it. This is not optional
housekeeping deferred to Step 4: elaboration commands this step needs
(`synth_design -rtl`, `generate_target`) write regenerated files back to disk
(e.g. BD metadata gets rewritten in place) as a normal side effect, so running
them against the original — even once, even before any DFX property is
touched — already violates "the original flat project... must remain
unchanged." If a copy already exists from an earlier attempt, reuse it
instead of recreating it.

Load the supplied design in a Vivado session before presenting RP candidates.
For Project mode, open the supplied `.xpr`. For Non-Project mode, load the
supplied flat DCP or RTL design using its inferred/known top and part. Detect
mode, target part/family, and Vivado version from the live session/design; do
not ask the user to repeat facts available there. Do not rely on static source
search alone to determine valid instance paths.

If the design cannot be opened in Vivado, stop before any DFX property or
project conversion change. Report the blocker and ask the user to resolve the
Vivado session/input issue.

If the exact target part is not supported for DFX in the installed release, or
is not an UltraScale+ or Versal part, stop before editing or converting
anything. Tell the user this workflow supports supported UltraScale+ and
Versal parts only.

Confirm that the input is a working elaborated/synthesized flat design and
that original sources are readable. For Non-Project inputs, infer top and
source set from the supplied RTL/DCP; ask only for missing information needed
to resolve an ambiguity.

### Step 2: Enumerate RP Candidates and Ask the User to Select

Query the live Vivado hierarchy and list valid candidate module instance paths
that could serve as RPs. For each candidate, show its exact hierarchical path,
RTL versus BD/BDC type, and basic boundary/interface summary. Include the
actual BDC cell when one exists, but do not assume it is the user's choice.

Ask the user: **"Which of these exact module instance path(s) should become
reconfigurable?"** Wait for an explicit selection. Never choose a candidate
on the user's behalf, even if there is only one apparent BDC or a path appears
obvious. If an instance name is ambiguous, show the matching paths and ask the
user to disambiguate. Support RTL-only, BD-only, mixed RTL/BD, and multi-BD
selections.

If the design is already DFX-enabled, first enumerate existing RPs and ask
whether the user wants to extend the current DFX structure; do not reinterpret
existing partitions as the requested selection without confirmation.

### Step 3: Validate the Candidate RPs

Before setting `PR_FLOW`, converting BDs, or changing files, validate every
user-selected module against the live Vivado design. Check that the selected
path resolves to exactly one cell and is hierarchical (not flattened or
eliminated), classify it as RTL or BD/BDC, and report the exact instance path.
Block conversion for nested or overlapping RPs, direct RP-to-RP paths,
configuration-only elements such as ICAP/STARTUP/BSCAN inside an RP, or
flattened boundaries. Warn on constant or unconnected RP ports and require
review of the interface consequences before continuing.

Use Vivado hierarchy/connectivity data to establish RP containment and
boundary relationships; do not infer legality from names alone. Enumerate the
ports/interfaces at each boundary and retain that inventory for the report.
Infer RP count from the explicit user selection. Ask whether partitions must
be independent only when the design connectivity does not make that clear.

### Step 4: Confirm Project Conversion

The output copy should already exist and already be the only thing you have
opened in Vivado, per Step 1 — this step is about **explicit user
confirmation**, not about creating the copy. The original flat project, RTL,
BD files, and XDC files must remain unchanged (verify this is still true
before continuing, e.g. nothing was accidentally opened/elaborated against
the original path). For Project mode, explain that `PR_FLOW`/DFX BDC
conversion is irreversible, confirm the separate flat copy is intact, and get
explicit confirmation before setting `PR_FLOW` or generating a DFX-enabled
BD. Refuse to proceed if confirmation/backup is missing.

Only after Step 2 has an explicit instance selection and Step 3 has validated
those same paths, set `PR_FLOW` in Project mode before enabling
reconfigurability. For a Non-Project DCP/RTL flow, set `HD.RECONFIGURABLE`
only on the user-selected, validated cells; do not set `PR_FLOW`.

### Step 5: Convert the Selected RTL and BD/BDC Modules

Create the partition definition/RM for each RTL module and convert each
selected BD hierarchy to a BDC with DFX enabled. Mixed RTL+BD and multi-BD
designs may use both conversion routes in the same project. Use
[reference/bdc-flow.md](reference/bdc-flow.md) and stop if Vivado's validation
shows that the selected topology is unsupported.

### Project-Mode RTL / Netlist Partition Definition

For each selected RP from plain RTL hierarchy (not inside a block
design):

```tcl
create_partition_def -name <partdef_name> -module <rm_module_name>; create_reconfig_module -name <rm_name> -partition_def [get_partition_defs <partdef_name>] -define_from <rm_module_name>; puts "Partition def [get_partition_defs <partdef_name>] created"
```

This captures the **current** implementation of `<rm_cell_path>` as the first
Reconfigurable Module for that partition. `-module` on `create_partition_def`
accepts the hierarchical cell or module that defines the partition boundary;
use the actual design object that represents the intended RP. `-define_from`
on `create_reconfig_module` is required for the **first** RTL RM of each
partition — it links the RM to the source module. Alternate RMs are outside
this skill's scope.

For a netlist-only RM (EDIF or DCP), do not use the RTL `-define_from` form.
UG909 documents creating the partition definition and then creating the RM
with `-top <rm_top> -gate_level`, followed by moving the netlist source into
the RM fileset. Verify the exact source/top and project flow in the current
Vivado release before applying that variant.
Additional alternate configurations for the same partition are outside this
skill's scope.

### Project-Mode BD / BDC Conversion

For each selected RP whose cell lives inside a block design, first
convert the hierarchy to a Block Design Container (BDC), then enable DFX on
it. The top BD must be validated first (`BD 41-1747`). On Versal, if a NoC
INI crosses the hierarchy boundary, set its INI strategy (Step 8, item 2)
**before** this conversion; with the default `auto` strategy
`create_bd_design -cell` fails with `BD 41-2560`.

```tcl
validate_bd_design; save_bd_design; create_bd_design -cell [get_bd_cells <hierarchy_path>] <bdc_name>; current_bd_design <top_bd>; set new_cell [create_bd_cell -type container -reference <bdc_name> <bdc_name>_temp]; replace_bd_cell [get_bd_cells <hierarchy_path>] $new_cell; delete_bd_objs [get_bd_cells <hierarchy_path>]; set_property name <bdc_name> $new_cell; set_property CONFIG.ENABLE_DFX {true} [get_bd_cells <bdc_name>]
```

See [reference/bdc-flow.md](reference/bdc-flow.md) for the full step-by-step
(hierarchy grouping, container conversion, NoC caveats on Versal).

### Non-Project Batch Flow — HD.RECONFIGURABLE + read_checkpoint

For each selected RP in a Non-Project design:

```tcl
set_property HD.RECONFIGURABLE true [get_cells <rm_cell_path>]; puts "HD.RECONFIGURABLE=[get_property HD.RECONFIGURABLE [get_cells <rm_cell_path>]]"
```

This is the Non-Project equivalent of establishing the RP boundary — it marks
`<rm_cell_path>` as a Reconfigurable Partition directly on the in-memory
design. Setting it implies `DONT_TOUCH` on the cell and its interface nets,
plus `EXCLUDE_PLACEMENT`/`CONTAIN_ROUTING` on its (not-yet-created) Pblock.

If a matching RM checkpoint exists (the cell must currently be a black box in
the loaded design):

```tcl
read_checkpoint -cell [get_cells <rm_cell_path>] <rm_dcp_path> -strict
```

Use `-strict` for a post-implementation checkpoint, or to enforce exact port
matching on a post-synthesis checkpoint. If no RM checkpoint is available
yet, leave the cell as a black box with `HD.RECONFIGURABLE` set — RM
synthesis/checkpoint creation is a follow-up step outside this skill's scope.

**Caution (UG909):** any Tcl variable referencing design objects becomes
invalid after a `read_checkpoint -cell` call — re-run `get_cells`/`get_pins`
after every such call instead of reusing variables saved before it.

Once all `HD.RECONFIGURABLE` cells are set and any available RM checkpoints
are loaded, assemble the full DFX design with:

```tcl
link_design -reconfig_partitions [get_cells -hierarchical -filter {HD.RECONFIGURABLE==1}]
```

This step is required to connect the static and RM netlists into a single
linked design before proceeding to pblock creation and implementation.
Skip it only if no RM checkpoint was loaded (pure black-box case where RM
synthesis is still a follow-up step).

### Step 6: Create the Standard DFX Configuration (Project Mode)

For every selected RP, create one project-mode partition definition/RM (RTL)
or DFX BDC. Create a single Standard DFX configuration that selects one RM
for every RP. Abstract Shell and Both modes are explicitly out of scope for
ROSS-53; do not ask the user to choose a mode or set `DFX_MODE` to those
values. Use the existing parent implementation run if present; otherwise
create or associate the Standard DFX configuration using the active Vivado
release's project-mode flow. Do not launch a run. For Non-Project flow, use
`HD.RECONFIGURABLE` and do not create project run objects.

```tcl
create_pr_configuration -name <config_name> -partitions [list <rp_cell_1>:<rm_name_1> <rp_cell_2>:<rm_name_2>]
set_property PR_CONFIGURATION <config_name> [get_runs <parent_impl_run>]
```

The actual `PR_CONFIGURATION` attachment target depends on the existing run
structure. Inspect and preserve existing parent/child runs; do not invent or
overwrite run names based on placeholder examples.

For DFX-enabled BDCs, Vivado creates the partition definition and RM only
when the top BD's output products are generated
(`generate_target all [get_files <top_bd>.bd]`, which does not launch runs).
Generate first, then read the names from `get_partition_defs`/
`get_reconfig_modules` and use the RP cell path under the wrapper instance
(for example `design_1_i/<bdc_cell>`) in `create_pr_configuration`.

### Step 7: Migrate Timing and Physical Constraints (Required)

Constraint migration is mandatory, not an optional analysis. Read every XDC
used by the source design, classify each constraint as static, boundary, or
RM, and generate new files in the new output/project area. Never modify the
source XDC files or original flat project.

#### Classification and Transformation

Inspect the flat design's existing XDC and classify constraints into these
DFX categories:

- **Static** — references only static objects; remains global.
- **Boundary** — crosses static ↔ RP boundary and must be expressed against
  RP interface pins, not RM internals.
- **RM** — logically belongs to the RM; write it into that RM's new XDC with
  `SCOPED_TO_REF`. For multiple instances of the same RM where physical
  constraints differ, put each instance's constraints in a separate XDC
  scoped with `SCOPED_TO_CELLS <rp_instance>` only — combined with
  `SCOPED_TO_REF <rm_module>`, the cell is looked up inside the module and
  Vivado skips the file (`Designutils 20-1275`). Include `LOC`, `PACKAGE_PIN`, and I/O constraints that
  target RM-owned objects in that same per-RM XDC; package pins on static
  top-level ports stay in the static XDC.
- **Ambiguous / manual review required** — constraints whose correct DFX form
  cannot be inferred safely from object references alone.

#### Required Outputs and Rules

- **Do not assume "referencing an RM object" means the constraint should
  simply be rescoped.** First determine whether the constraint is RM-local,
  boundary-crossing, physical, or flow-stage-specific.
- Write a trimmed static XDC containing only static constraints and
  common-RM constraints intended to remain at the top level.
- Write a new XDC for every RM, scoped with `SCOPED_TO_REF`; when the same
  module is instantiated in multiple RPs and physical constraints differ,
  write a separate per-instance XDC scoped with `SCOPED_TO_CELLS` only.
- In Project mode, attach RM XDCs with
  `add_files -norecurse <rm.xdc> -of_objects [get_reconfig_modules <rm_name>]`
  (`add_files -fileset <rm_name>` is rejected, and RM filesets are not listed
  by `get_filesets`), then set `SCOPED_TO_REF` explicitly:
  `set_property SCOPED_TO_REF <rm_source_module> [get_files <rm.xdc>]`.
  **`<rm_source_module>` is the real RTL/HDL module name being constrained
  (the same module passed to `-define_from`/`-module` in Step 5) — never the
  RM's own management name** (e.g. `rp_filter`, not `rp_filter_rm`). Vivado
  resolves `SCOPED_TO_REF` by looking for cells of that exact module/reference
  inside each RM instance; scoping to a name that isn't a real synthesized
  module silently fails to match anything. Disable the original flat XDC in
  the output project (`IS_ENABLED false`) once its content is migrated.
- Apply the generated XDCs in the new Project-mode project or corresponding
  Non-Project flow. Do not leave them as un-applied suggestions.
- Preserve a migration report of every source constraint's category,
  destination file, transformation, and result.
- **Do not silently drop or guess-rewrite constraints.** For ambiguous object
  mappings, preserve the source unchanged and list the constraint as an open
  item; the new project remains not fully ready until that item is resolved.
- **Do not convert a top-level constraint that reaches into RM internals into
  another top-level constraint with the same internal endpoint.** If the
  constraint should survive RM carving, it usually must be rewritten against
  RP interface pins.
- **Do not auto-approve physical constraints** (`LOC`, `PACKAGE_PIN`,
  pblock-related constraints, region placement, embedded I/O constraints) for
  multi-instantiation designs without explicit instance review.
- **Do not assume child runs, OOC synthesis, or reused RM checkpoints see the
  same constraints as the original flat implementation run.** Note when a
  constraint may need run-specific application.

#### Minimum Safe Automation

The skill may perform the following when enough information is available:

1. Enumerate XDC files currently used by the project or Non-Project flow.
2. For each constraint or file, determine whether referenced objects are:
   - static-only,
   - entirely within one future RP,
   - crossing static ↔ RP boundary,
   - physical / placement related,
   - unresolved after the DFX boundary is introduced.
3. Produce a constraint-by-constraint destination and transformation plan:
  static constraints remain in the trimmed static XDC; boundary constraints
  are rewritten against RP pins; RM constraints move into the correct
  scoped RM XDC; ambiguous items become explicit open items.
4. Generate the static and per-RM XDC files, apply them in the output design,
   and verify their scoping and object resolution. Write only under the new
   output/project area.

#### What the Skill Must Not Claim

The skill must **not** claim that constraint migration is complete or correct
merely because:

- the original flat constraint resolved successfully before conversion,
- a path can be textually shortened under `SCOPED_TO_REF`,
- or a file has been moved into an RM fileset.

Correctness depends on whether the constrained objects continue to exist in
the relevant DFX stage (static synthesis, RM synthesis, parent
implementation, child implementation, OOC synthesis, checkpoint reuse).

#### Heuristics for Candidate Classification

- If a constraint references **only static objects**, keep it **global/static**.
- If a constraint references **only RM-local logical objects** and is not
  instance-specific, it is a candidate for **General RM** with
  `SCOPED_TO_REF`.
- If a constraint is **physical** (`LOC`, `PACKAGE_PIN`, pblock range, or
  similar) and applies to one RP instance, it is a candidate for
  **RP-specific RM** with `SCOPED_TO_CELLS`.
- If a constraint crosses from static logic into RM logic, classify it as
  **Boundary** unless proven otherwise, and flag any endpoint that targets an
  RM-internal object for rewrite against the RP interface.
- If the design instantiates the same RM module more than once, treat shared
  physical constraints as **ambiguous** unless the intended instance mapping is
  explicit.
- If a constraint is needed during RM OOC synthesis, note that top-level
  `SCOPED_TO_CELLS` alone is insufficient; the XDC may need to be attached to
  the OOC run's constraint set.

#### Deliverables

If this step is run, write:

- `vivado_agentic_ai_reports/vivado-convert-flat-to-dfx/constraint-migration-report.md`
  with:
  - the list of XDC files inspected,
  - each source constraint classified as static / boundary / RM, its target
    output XDC, and whether it was rewritten or moved,
  - any unresolved object references,
  - any constraints likely to break after black-box carving,
  - explicit manual-review items.
- `static.xdc` (or an unambiguous static filename) and one RM XDC per RM,
  added/applied in the output design with `SCOPED_TO_REF`, plus per-instance
  XDCs scoped with `SCOPED_TO_CELLS` when required.

Flag unresolved constraints as open items; never imply the design is ready
for implementation signoff while any required timing/physical constraints
remain unmigrated.

### Step 8: Convert Versal Modular NoC (Versal Only)

Run this step when a selected RP contains a NoC endpoint or a NoC path crosses
the future static/RP boundary. Do not modify unrelated NoC paths.

1. Inventory the flat BD NoC path, source NMU, endpoint NSU, connection
  attributes (including bandwidth/traffic class), and all NoC constraints.
2. Split each affected BD path at the RP boundary using a NoC INI. For the
  ROSS-53 single-load case where the NMU remains static, configure the valid
  single-load strategy and keep the static side as the sole path owner
  (UG909 "INI Ownership Concept"; in 2026.1:
  `set_property CONFIG.INI_STRATEGY load` on both ends of the boundary INI).
  Do this before the Step 5 BDC conversion. Inspect
  `list_property` on the INI pin in other releases rather than guessing.
  Export each boundary endpoint exactly once, and ensure the static and RM
  projects expose matching virtual NoC interface names.
3. Rewrite the boundary `create_noc_connection` constraints to terminate on
  the corresponding virtual NoC interface. Move RM-side NoC constraints to
  the new RM XDC, scope to the RM, and mark that XDC as used in
  `synthesis_pre`. For multi-RP designs, retain static ownership and do not
  duplicate endpoint export or path ownership.
4. Preserve every BD-to-BDC interface address base and range from the flat
  design. Define the aperture on each boundary interface and check that no
  apertures overlap. Use the interface pin property `APERTURES` (not
  `CONFIG.APERTURES`, which is read-only on a BDC pin: `BD 41-737`, and
  leaves `BD 41-2914` reported), for example
  `set_property APERTURES {{0x202_0000_0000 64K}} [get_bd_intf_pins /<bdc>/<intf>]`.
5. Validate the BDs and NoC after the transformation. Run
  `validate_bd_design`, `validate_noc`, and `report_noc`; inspect the NoC
  ownership table and require the static owner for static NMU paths. Check
  for BD 41-2477, 41-2479, 41-2480, and 41-2577. Resolve errors before
  claiming conversion success.

Vivado Modular NoC interfaces and INI strategy options are release-sensitive.
Use the active release's IP Integrator/NoC API or GUI and inspect generated
objects before applying constraints; do not guess property names. If the tool
cannot verify one-owner topology, matching virtual interfaces, preserved
address apertures, or clean validation, stop that conversion and record the
specific open item in the report.

#### RTL Modular NoC (XPM NMU/NSU + XDC)

When the NoC endpoints are `xpm_nmu_*`/`xpm_nsu_*` XPMs in RTL and
connectivity lives in an XDC (`get_noc_interfaces`, `create_noc_connection`,
`USED_IN synthesis_pre`), split each path that crosses the selected RP with a
virtual NoC interface instead of an INI (UG909 RTL Modular NoC flow;
Vivado-Design-Tutorials `NoC_Design_Flows/RTL/DFX_Foundational`). Supported
for an NSU in the RP fed by a static NMU, and for an NMU in the RP targeting a
static NSU.

1. In a new static NoC XDC, create the virtual interface under the RP
   instance and connect the static endpoint to it. Keep the original
   connection's bandwidth/burst properties and apertures here so the static
   region owns the path:
   ```tcl
   set vif [create_noc_interface -mode vnsu -type AXIMM <rp_inst>/<vif_name>]   ;# vnmu when the NMU is in the RP
   set c [create_noc_connection -source <static_nmu> -target $vif]              ;# or -source $vif -target <static_nsu>
   set_property -dict [list READ_BANDWIDTH ... WRITE_BANDWIDTH ...] $c
   set_property APERTURES [list {<base>:<high>}] $vif
   ```
   **`APERTURES` is mandatory on every virtual NoC interface you create here —
   including an NMU-side (`vnmu`) virtual interface — even when the flat
   design's original NMU path had no aperture of its own.** Vivado's DFX NoC
   validation (`DRC MODNOC-25`) requires an explicit address aperture on any
   addressable NoC interface at an RP boundary; omitting it is not optional
   just because nothing in the flat design forced you to think about it.

   **The aperture must be a genuinely free address range, never copied
   verbatim from another live endpoint.** Before choosing it, enumerate every
   existing NoC endpoint's aperture in the design (`report_noc`, or
   `get_property APERTURES`/`CONFIG.APERTURES` on each `get_noc_interfaces`/
   `get_bd_intf_pins` NoC object) and pick a base/range that does not overlap
   any of them (`ERROR: [BD 41-2322] NoC endpoints ... have address
   overlaps`). If the flat design already assigned this exact path a
   range, reuse that range unchanged; only invent a new one when the flat
   design had none, and never reuse a *different* endpoint's range just
   because it is nearby or convenient.
2. In a new RM NoC XDC, create the same `<vif_name>` with the **same
   `-mode`** as the static side (path relative to the RM): `vnsu` on both
   sides when the NSU is in the RP, `vnmu` on both sides when the NMU is in
   the RP. Connect it to the RM's own XPM endpoint and repeat the aperture.
   A name match with a different mode does not connect (`ModularNoC 90-3`).

   **When the NSU is in the RP (`-mode vnsu`), setting `APERTURES` on the
   virtual interface alone is not enough — also set it directly on the real
   underlying NSU object** (`set rp_nsu [get_noc_interfaces
   <xpm_nsu_instance>/M_AXI_nsu]; set_property APERTURES [list {<base>:<high>}]
   $rp_nsu`, same range as the virtual interface). The virtual interface's
   aperture satisfies the "must specify an aperture" DRC (`MODNOC-25`) but
   does not reliably constrain the real NSU's address assignment; without an
   aperture on the real NSU too, Vivado falls back to auto-assigning one,
   which can collide with an unrelated existing endpoint
   (`ERROR: [BD 41-2322] ... address overlaps`) even though the virtual
   interface's own range was correct and non-overlapping. This extra
   real-endpoint aperture is not needed for the NMU-in-RP (`vnmu`) case — an
   NMU does not occupy address space itself, only the virtual interface
   needs one there.
   Attach it with `add_files ... -of_objects [get_reconfig_modules <rm>]`.

   **CRITICAL: `-mode` is never split across the two sides.** `-mode`
   describes which endpoint type (NSU or NMU) lives inside the RP — it is a
   property of the boundary, not of which file (static vs RM XDC) you are
   currently editing. Pairing `vnsu` on one side with `vnmu` on the other for
   the *same* `<vif_name>` is always wrong, regardless of which endpoint is
   static and which is in the RP, and will fail NoC merge/validation. If
   uncertain which single mode applies, re-derive it from which endpoint
   (NMU or NSU) is inside the RP — do not guess a different mode per file,
   and do not burn retries on `vivado_doc_search` to confirm this: the rule
   above is the complete answer.
3. **Mandatory, easy to forget:** set `USED_IN` to include `synthesis_pre` on both new NoC XDCs (`set_property USED_IN {synthesis implementation synthesis_pre} [get_files <static_or_rm_xdc>]`). The default `USED_IN` omits `synthesis_pre`, silently leaving the NoC compiler unaware of the split and allowing the traffic checksum to drift from the flat design. Disable the original flat NoC XDC in the output project (`set_property IS_ENABLED false [get_files <original_noc_xdc>]`), and ensure the static XDC does not reference XPM instances inside the RP.
4. Run `generate_target` on the top BD, then `validate_noc -force`.
   `validate_noc` returns success even when the split is wrong, so read its
   messages: `CRITICAL WARNING [ModularNoC 90-3]` (virtual interface missing
   on one side) or `[DRC MODNOC-2]` (unresolved DFX region) means the
   conversion failed. `WARNING [Project 1-5087] Failed to merge RM module`
   also appears for correct project-mode designs, including the AMD
   tutorial, and is not by itself a failure. Compare the
   `NoC TrafficSpec | Checksum` with the flat design's `validate_noc`; a
   change means connection attributes were lost or altered.

### Step 9: Write REPORT.md

**Action:** Call the write tool to create
`vivado_agentic_ai_reports/vivado-convert-flat-to-dfx/REPORT.md` (relative to
your original working directory, not the output project directory created in
Step 4) containing:

- Which mode was detected (Project/Non-Project), Vivado version, exact part,
  and source project/input location
- Whether the design is single-RP or multi-RP, and how many partitions/cells
  were converted
- For each RP: the RM cell path, and either the partition definition/RM name
  plus flow used (RTL or BDC, Project mode), or confirmation that
  `HD.RECONFIGURABLE` is set and whether a matching RM checkpoint was loaded
  (Non-Project mode)
- Project mode only: the Standard DFX Configuration name and the parent run
  association, noting run objects were not launched
- Every generated static/per-RM XDC path, each input constraint's category,
  destination, and rewrite/move action, plus all unresolved/manual-review
  items. Confirm original flat RTL/BD/XDC files were not modified.
- For Versal Modular NoC conversions: changed paths, INI strategy, path owner,
  exported virtual-interface names, preserved address apertures, validation
  command outcomes, ownership-table result, and the four DRC checks.
- A **Next Steps** section listing, without executing them, that the design
  still needs pblock creation/sizing for each RM, then implementation
  (`opt_design`/`place_design`/`route_design`/`launch_runs`), and `pr_verify`
  before it can be built or bitstreamed. Do not state constraint migration is
  optional or still pending if it was completed. For Non-Project mode where no RM
  checkpoint was loaded, also note that RM synthesis/checkpoint creation is
  required first.

**Order:** Invoke the write tool first, then give a short summary. Do not
output the report as chat text.

---

## ⚠️ MANDATORY: Design-Specific Fix Rules

**All commands MUST use ACTUAL names from the design. NO generic placeholders.**

| Rule | ❌ WRONG | ✅ CORRECT |
|------|----------|------------|
| RM cell path (instance in hierarchy) | `<rm_cell_path>` | `u_top/filter_chain_0` |
| Partition-defining cell/module (for `create_partition_def -module`) | `<partition_module_or_cell>` | `filter_chain` |
| RTL RM source module (for `create_reconfig_module -define_from`) | `<rm_source_module>` | `filter_chain` |
| Partition def name | `<partdef_name>` | `filter_chain_partition` |
| RM name | `<rm_name>` | `filter_chain_rm1` |
| BDC name | `<bdc_name>` | `rp_container` |
| Configuration name | `<config_name>` | `filter_chain_config1` |
| Non-Project source folder | `<folder_path>` | `/proj/designs/my_design/dfx_src` |
| Non-Project static/top DCP | `<static_top_dcp_path>` | `/proj/designs/my_design/dfx_src/top_static.dcp` |
| Non-Project RM DCP | `<rm_dcp_path>` | `/proj/designs/my_design/dfx_src/filter_chain_rm1.dcp` |
| Target part | `<part>` | `xcku040-fbva676-1-c` |

---

## Error Handling

| Error | Symptom | Action |
|-------|---------|--------|
| Unsupported target part | The detected part is 7 series, UltraScale, or absent from the installed release's supported DFX list | Stop before mutation; this skill supports supported UltraScale+ and Versal parts only |
| Project conversion not backed up/confirmed | User has not saved a flat copy or confirmed one-way conversion | Stop before `PR_FLOW` or DFX-enabled BD generation; preserve originals |
| Original input modified by elaboration | Original BD/RTL files differ from their pristine state (e.g. after `synth_design`/`generate_target` regenerated BD metadata) even though no DFX property was ever set | This means the copy step was skipped or done too late — Step 1 must copy the input and do all elaboration against the copy before opening anything in Vivado. Discard the dirtied original-path session, recreate the copy from a pristine source, and restart Step 1 against the copy only |
| No project open | `ERROR: [Common 17-69] Command failed: current_project is not valid` | Detect and use the supplied Non-Project RTL/DCP flow; ask for missing design inputs only when they cannot be inferred |
| Candidate RP invalid | Selected path is missing/flattened, nested/overlapping, has direct RP-to-RP paths, or contains ICAP/STARTUP/BSCAN | Block conversion; report exact cell/path and reason. Do not silently force hierarchy preservation or bypass validation |
| Constant or unconnected RP ports | One or more boundary ports have constant/unconnected drivers | Warn, list port names and connections, and continue only after documenting user disposition |
| Flat constraint resolves pre-conversion but not after DFX carving | Constraint references RM internals that disappear when the RP is black-boxed | Reclassify as boundary or RM-scoped as appropriate; do not assume textual re-scoping is sufficient |
| Multi-instantiated RM with physical constraints | `SCOPED_TO_REF` would incorrectly apply one physical constraint to multiple RP instances | Put each instance's physical constraints in its own XDC scoped with `SCOPED_TO_CELLS` only, or mark for manual review. Do not combine `SCOPED_TO_REF <rm_module>` with `SCOPED_TO_CELLS <rp_instance>` — the cell is searched inside the module and the file is skipped (`Designutils 20-1275`) |
| Generated RM XDC not applied to required stage | Constraint exists in output files but is not in the RM's synthesis/implementation constraint set | Attach the generated XDC to the RM/required OOC or run constraint set for the active Vivado release; verify `USED_IN`/scoping and report the result |
| OOC synthesis misses RM constraint | Constraint attached at top-level does not affect the RM's own OOC synthesis run | Add the XDC directly to the OOC run's constraint set |
| BDC referencing missing RM instance | `ERROR: [BD 41-xxx]` Block Container references an instance that does not exist (exact code varies by version) | Ensure the BDC's referenced RM `.bd`/design is sourced before the static design (see [vivado-revision-control dfx-revision-control](../vivado-revision-control/dfx-revision-control/REFERENCE.md)) |
| Non-Project: no black box for cell | `read_checkpoint -cell` fails because the target cell doesn't exist or isn't a black box | Confirm `HD.RECONFIGURABLE` was set on that exact cell path first, and that the loaded static/top design actually has a black box there |
| Non-Project: stale Tcl object variable | Wrong-cell references or a crash after a second `read_checkpoint -cell` call | Re-run `get_cells`/`get_pins` after every `read_checkpoint -cell` call instead of reusing variables captured before it (UG909 caution) |
| Constraint migration incomplete | One or more source XDC constraints are ambiguous or do not resolve in the output design | Preserve source files; record each unresolved constraint and object in `REPORT.md`; do not claim migration complete or silently drop it |
| Versal NoC ownership invalid | `validate_noc`/`report_noc` reports multiple/no owners, unmatched virtual interfaces, or DRC 41-2477/41-2479/41-2480/41-2577 | Stop NoC conversion success; inspect INI strategy and endpoint export, retain static ownership for static NMU, fix and revalidate |
| BD address aperture changed/overlaps | BDC interface base/range differs from flat design or apertures overlap | Restore original base/range on each boundary, rerun `validate_bd_design`, and report unresolved interfaces |
| RTL Modular NoC aperture overlaps another endpoint | `ERROR: [BD 41-2322] NoC endpoints that share the same network have address overlaps: <endpoint A> ... <endpoint B> ...` during `validate_noc` | If the chosen range genuinely duplicates another endpoint's, enumerate existing apertures (`report_noc`) and pick a free range instead. If the range was already correct and non-overlapping but the error still names an NSU you didn't expect, you set `APERTURES` only on the virtual interface — for an NSU-in-RP (`vnsu`) split, also set it directly on the real NSU object in the RM XDC (see Step 8 RTL Modular NoC item 2); rerun `validate_noc -force` |
| No manual aperture on a DFX BDC interface | `WARNING: [BD 41-2914] ... has no manual apertures set`; setting `CONFIG.APERTURES` gives `CRITICAL WARNING: [BD 41-737] ... read-only` | `set_property APERTURES {{<base> <range>}} [get_bd_intf_pins /<bdc>/<intf>]` with the flat design's base/range, then `validate_bd_design` |
| BDC conversion blocked by INI strategy | `ERROR: [BD 41-2560] The NoC INI Strategy is set to auto, and cannot be inferred by topology` during `create_bd_design -cell` | Set `CONFIG.INI_STRATEGY load` (static NMU owns the path) on both ends of the boundary INI, validate the top BD, then redo the conversion |
| BDC conversion on unvalidated BD | `ERROR: [BD 41-1747] Current design is not validated` | Run `validate_bd_design` and `save_bd_design` on the top BD first |
| No partition definition for a DFX BDC | `get_partition_defs` is empty after `CONFIG.ENABLE_DFX true` | Run `generate_target all` on the top BD (creates partition def + RM, launches no runs), then create the PR configuration |
| RM XDC cannot be added | `ERROR: [Common 17-69] Adding files to a fileset created by RM is not supported` | Use `add_files <xdc> -of_objects [get_reconfig_modules <rm_name>]` |
| RTL Modular NoC split mismatched | `validate_noc` succeeds but reports `CRITICAL WARNING [ModularNoC 90-3]` / `[DRC MODNOC-2]`, or the NoC TrafficSpec checksum differs from the flat design | Use the same virtual NoC interface name (`create_noc_interface -mode vnsu\|vnmu`) in the static XDC (under `<rp_inst>/`) and the RM XDC; keep both `USED_IN synthesis_pre`; rerun `validate_noc -force` |
| RTL Modular NoC virtual interface missing aperture | `CRITICAL WARNING: [DRC MODNOC-25] ... Addressable NOC interface '<vif>' on RP instance '<rp_inst>' must specify one or more address apertures for DFX verification` | Set `APERTURES` on that virtual interface in both the static and RM XDC (mirror the connected static endpoint's address window) — required even for an NMU-side `vnmu` virtual interface whose original flat-design path had no aperture |
| PR_FLOW not set first | Project-mode `create_partition_def`/`CONFIG.ENABLE_DFX` fails or has no effect | After explicit conversion and backup confirmation, set `PR_FLOW` before enabling RPs; never set it in Non-Project mode |

---

## Validation

**Project mode:**

```tcl
puts "PR_FLOW=[get_property PR_FLOW [current_project]]"; puts "Partitions=[llength [get_partition_defs]]"; puts "RMs=[llength [get_reconfig_modules]]"; puts "PR_CONFIGURATION=[get_property PR_CONFIGURATION [get_runs impl_1]]"
```

**Non-Project mode:**

```tcl
foreach c [list <actual_rm_cell_path_1> <actual_rm_cell_path_2>] { puts "$c HD.RECONFIGURABLE=[get_property HD.RECONFIGURABLE [get_cells $c]]" }
```

Success: the part is supported UltraScale+ or Versal; every requested RP was
validated and has a DFX definition/BDC or `HD.RECONFIGURABLE`; the Standard
DFX configuration exists in Project mode; generated static and per-RM XDCs
are applied and all source constraints are classified as migrated or explicit
open items; original project/RTL/BD/XDC inputs remain unchanged. For affected
Versal NoC, `validate_bd_design` and `validate_noc` pass, `report_noc` shows the
expected single static owner, required address apertures match without
overlap, and the four listed DRCs are absent. Pblock creation, implementation,
and `pr_verify` remain for a follow-up flow.

---

## References

- [reference/bdc-flow.md](reference/bdc-flow.md) — Block Design Container conversion steps
- [reference/constraint-management.md](reference/constraint-management.md) — How RM/boundary/static constraints and scoping (`SCOPED_TO_REF`/`SCOPED_TO_CELLS`) differ from a flat design's constraint set
- **UG909**: Vivado Design Suite User Guide — Dynamic Function eXchange
- **UG909 Supported Devices**: verify the exact target part and flow support for the installed Vivado release
- **UG994**: Vivado Design Suite User Guide — IP Integrator (Block Design Container)
- **UG947**: Vivado Design Suite Tutorial — Partial Reconfiguration
- **UG835**: Vivado Design Suite Tcl Command Reference Guide — `create_pr_configuration`, `create_partition_def`, `create_reconfig_module`
- **ROSS-53**: Convert Flat Vivado Project to DFX Vivado Project (UltraScale+ and Versal)
- Related skill: [segcfg-dfx](../segcfg-dfx/) — Segmented Configuration + DFX interaction (Versal)
- Related skill: [vivado-revision-control/dfx-revision-control](../vivado-revision-control/dfx-revision-control/) — RM ordering, pblocks, and Git LFS for DFX projects under revision control
