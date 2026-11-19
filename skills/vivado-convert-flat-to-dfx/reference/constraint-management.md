<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Constraint Management: Flat vs. DFX (UG909)

Constraint migration is a required part of this skill, even though pblock
creation and implementation are out of scope. Read the active flat-design
XDCs, classify constraints, create new static and per-RM XDC files, apply them
to the new DFX output, and record every move/rewrite/open item in the report.
Never modify source XDCs or the original flat project. Source: **UG909**,
"Managing Constraints for a DFX Design" and "Design Criteria" /
"Constraints and Properties" chapters.

## Why This Matters

In a flat design, all constraints are applied once, globally, against a
single netlist. In a DFX design, the static logic and every Reconfigurable
Module (RM) are synthesized and (re-)assembled independently per
configuration. A constraint written the "flat" way — referencing objects
inside an RM directly — can silently stop applying once that RM is carved
into a black box (`update_design -cells <rp> -black_box`), because the
objects it references no longer exist at that point in the flow. This is the
single most common constraint-migration mistake when converting a flat
design to DFX.

## Global vs. Scoped Constraints

| Type | Applies to | Typical use |
|---|---|---|
| **Global** | Full-hierarchy object references, evaluated once against the whole design | Static-only constraints |
| **Scoped** | A module or a specific instance, applied automatically wherever the RM is used | RM constraints |

Scoping is set on the constraint **file**, not the individual constraint:

```tcl
set_property SCOPED_TO_REF   <rm_module_name>        [get_files <rm.xdc>]
set_property SCOPED_TO_CELLS <rp_instance_path>       [get_files <rm.xdc>]
```

- `SCOPED_TO_REF` — ties the file to a module name; applies to **every**
  instantiation of that RM. All hierarchical references inside the XDC can
  omit the full top-level path — they start at the RM top.
- `SCOPED_TO_CELLS` — ties the file to one specific hierarchical instance;
  required whenever a constraint is instance/location-specific (physical
  constraints on a partition that is instantiated more than once) — otherwise
  `SCOPED_TO_REF` would apply the same physical constraint to every instance.
- **Both together** — `SCOPED_TO_CELLS` is resolved *inside* the module
  named by `SCOPED_TO_REF` (UG903: "applied to each cell of the
  SCOPED_TO_CELLS list, located inside the module specified by
  SCOPED_TO_REF"). So `SCOPED_TO_REF <rm_module>` + `SCOPED_TO_CELLS
  <rp_instance>` does **not** target that RP instance: Vivado 2026.1 skips
  the file with `CRITICAL WARNING [Designutils 20-1275] Could not find cell
  '<rp_instance>' within module '<rm_module>'`.
- **Per-instance physical constraints** (multi-instantiated RM) — set only
  `SCOPED_TO_CELLS <rp_instance_path>` (no `SCOPED_TO_REF`) on one XDC per
  RP instance, or set `SCOPED_TO_REF` to the RP's *parent* module with
  `SCOPED_TO_CELLS` relative to it. Verify the result with
  `get_property LOC` on the target cell after loading constraints.

In the GUI, the equivalent action is: **right-click the XDC in the Partition
Definitions view → Move to Reconfigurable Module**, then set the
`SCOPED_TO_REF`/`SCOPED_TO_CELLS` property in Source File Properties.
`PROCESSING_ORDER` can be set to `LATE` on these RM constraint files to
ensure they are applied after all top-level constraints have been processed.

## The Constraint Categories AMD Recommends

| Category | Scope | When applied | Re-applied per configuration? |
|---|---|---|---|
| **Static** | Global — static-only objects | Once, at the first (parent) configuration's static synthesis/implementation | **No** — baked into the locked static checkpoint (`lock_design -level routing`); must not be reapplied |
| **Boundary** | Global, but must reference the RP's **interface pins**, not internal RM objects | Once, at the first configuration | **No**, if written correctly against pin-level objects (see below) |
| **General RM** | Scoped (`SCOPED_TO_REF`) — applies regardless of which RP instance | At RM synthesis | Baked into the RM checkpoint; travels with the RM automatically |
| **RP-specific RM** (physical: `LOC`, package/I/O constraints that target RM-owned ports or objects) | Scoped to the RP instance with `SCOPED_TO_CELLS` only (not combined with `SCOPED_TO_REF` of the RM module — see above) | At RM implementation for that RP | **Yes** — retain in that RM's per-instance XDC |
| **Embedded I/O** | Scoped, physical | At RM implementation | **Yes** — IOB-related constraints are wiped during carving; reapply per RM. Set `IO_BUFFER_TYPE=NONE` at the top RTL if the RM's IP already instantiates its own I/O buffers |

### Writing boundary constraints correctly

```tcl
# Correct — boundary exception terminates on the RP interface pin
set_false_path -from [get_pins rst_reg/C] -through [get_pins u_top/filter_0/rst]

# Wrong — reaches inside the RM; the internal object disappears once the
# RM is carved into a black box, silently dropping the constraint
set_false_path -from [get_pins rst_reg/C] -to [get_pins u_top/filter_0/acc_reg/D]
```

## Where RM Constraints Live (Project Mode)

Add each generated RM constraint file to the corresponding Reconfigurable
Module fileset in the new DFX project rather than leaving it only in the
top-level constraints fileset:

```tcl
add_files -norecurse <rm_constraints.xdc> -of_objects [get_reconfig_modules <rm_name>]
set_property SCOPED_TO_REF <rm_module_name> [get_files <rm_constraints.xdc>]
```

`add_files -fileset <rm_name>` is rejected ("Adding files to a fileset
created by RM is not supported"), and RM filesets are not returned by
`get_filesets`; list RM sources with
`get_files -of_objects [get_reconfig_modules <rm_name>]`. Vivado does not set
`SCOPED_TO_REF` automatically on files added this way — set it explicitly.

In the GUI this is **Partition Definitions view → right-click the RM →
Move to Reconfigurable Module**. After moving the file, set
`SCOPED_TO_REF`/`SCOPED_TO_CELLS` and optionally `PROCESSING_ORDER LATE`
in the Source File Properties panel (or via `set_property` as shown above).

### OOC Synthesis Constraints (IP Integrator / BDC)

For RM blocks synthesized Out-of-Context inside IP Integrator, constraints
scoped via `SCOPED_TO_CELLS` at the top-level do **not** reach into the OOC
synthesis run. Add the XDC directly to the OOC run's constraint set instead:

```tcl
add_files -fileset [get_property CONSTRSET [get_runs <ooc_synth_run>]] <rm_constraints.xdc>
```

This is required for CDC or timing constraints that must be enforced during
the RM's own OOC synthesis pass (see UG909 "Scoping Constraints to the
Module Reference Block in the IP Integrator").

## Applying Generated Constraint Files

In Vivado 2024.2 and earlier, child implementation runs do **not** reapply
constraints unless explicitly told to; enable with:

```tcl
set_property APPLY_CONSTRSET <constrs_set_name> [get_runs <child_run>]
```

In Vivado 2025.1 and later, RM-level XDC sources can be added directly to each
RM under the Partition Definition. Verify in the active release that the new
files are applied to the required synthesis/implementation stages. Where
child runs or OOC synthesis do not inherit the appropriate constraint set,
attach the generated files directly or configure `APPLY_CONSTRSET` as
appropriate for that release.

Without either mechanism, a child run inherits only what is already baked into
the static + RM checkpoints — it does not re-source the top-level constraints
fileset. This differs from a flat design, where every implementation run
always re-applies the full constraint set.

## Reusing an Already-Routed RM Checkpoint

If an RM is reused via `read_checkpoint -cell <rp> <rm.dcp>` after a prior
`route_design`:

- Physical constraints are already resolved in the checkpoint.
- **Timing** constraints scoped to that RM, and any boundary constraints
  referencing RM objects, are **not** stored in the DCP and must still be
  reapplied from XDC.

## Required Migration Deliverables

- A trimmed static XDC containing static constraints and only those common-RM
  constraints intentionally kept at top level.
- A new XDC per RM containing that RM's logical and physical constraints,
  including `LOC`, `PACKAGE_PIN`, and I/O constraints that target objects
  owned by the RM. Package constraints for static top-level ports remain in
  the static XDC; `LOC`, `PACKAGE_PIN`, and I/O constraints on RM-owned objects
  belong in the per-RM XDC.
  Scope with `SCOPED_TO_REF`; when the same RM is instantiated multiple
  times and constraints are RP-instance-specific, put them in a separate XDC
  per instance scoped with `SCOPED_TO_CELLS` only.
- Boundary constraints rewritten to use RP interface pins. Do not leave a
  timing exception terminating on an RM-internal object in the static XDC.
- XDC files applied to the new DFX project or Non-Project flow. Source flat
  XDC files and project remain untouched.
- A report mapping every source constraint to its output file, classifying it
  as static, boundary, RM, or open/manual review. Unresolved constraints are
  not counted as completed migration.

## Quick Comparison Summary

| | Flat design | DFX design |
|---|---|---|
| Constraint sets | One, applied once | Split by category (static / boundary / general RM / RP-specific RM / embedded I/O) |
| Re-application | Always, every run | Static/boundary: once only. RM physical + I/O: every configuration |
| Scoping mechanism | None needed | `SCOPED_TO_REF` (per-module) or `SCOPED_TO_CELLS` (per-instance) |
| Object references | Any object, any time | Constraints reaching into an RM's internals are dropped once that RM is carved to a black box — must reference RP boundary pins instead |
| Physical (LOC/pblock) constraints | Persist for the life of the run | Cleared on every carve; must live in the RM's own XDC, not the top-level one |

## References

- **UG909**: "Managing Constraints for a DFX Design" (Ch. 5), "Constraints
  and Properties" (Ch. 3), "Adding Reconfigurable Module Constraints" (Ch. 4),
  "Scoping Constraints to the Module Reference Block in the IP Integrator"
- **UG903**: Vivado Design Suite User Guide — Using Constraints,
  "XDC File Scoping Properties" (`SCOPED_TO_REF`, `SCOPED_TO_CELLS`)
