<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Block Design Container (BDC) DFX Flow

Use this reference when the reconfigurable hierarchy lives inside an IP
Integrator block design rather than plain RTL. This is invoked from
[SKILL.md](../SKILL.md) Step 5.

## Why BDC is Required

DFX requires the Reconfigurable Module boundary to be a **container**, not a
plain hierarchy cell in the block design. A regular (non-container) BD
hierarchy cannot be marked `IS_RECONFIGURABLE` — attempting
`set_property CONFIG.ENABLE_DFX {true}` on a non-container cell fails or has
no effect.

Before enabling DFX on a BDC, make sure the project owner has explicitly
confirmed conversion to DFX and has saved or archived a flat copy. Generating
a design containing a DFX-enabled BDC can convert the project to DFX
automatically, and that conversion cannot be undone in place (UG909/UG947).

## Step-by-Step Conversion

### 1. Group the target IP blocks into a hierarchy

In the top-level block design, select the IP blocks that should be
reconfigurable, then create a hierarchy:

```tcl
group_bd_cells <hierarchy_name> [get_bd_cells <cell1> <cell2> ...]
```

### 2. Convert the hierarchy into a Block Design Container

The top BD must be validated before `create_bd_design -cell` (otherwise
`BD 41-1747`). On Versal, if the hierarchy has a NoC INI on its boundary, set
the INI strategy first (see step 5) — with `auto`, `create_bd_design -cell`
fails with `BD 41-2560` because the strategy cannot propagate into the child
BD.

```tcl
validate_bd_design
save_bd_design
create_bd_design -cell [get_bd_cells <hierarchy_path>] <bdc_name>
current_bd_design <top_bd>
set new_cell [create_bd_cell -type container -reference <bdc_name> <bdc_name>_temp]
replace_bd_cell [get_bd_cells <hierarchy_path>] $new_cell
delete_bd_objs [get_bd_cells <hierarchy_path>]
set_property name <bdc_name> $new_cell
```

Chain these as one semicolon-separated `vivado_execute` call for MCP
execution. Vivado creates a new child `.bd` source (e.g. `<bdc_name>.bd`) and
replaces the flat hierarchy in the top canvas with a BDC cell instance.

### 3. Enable DFX on the container

In the BDC's configuration, enable **Freeze the boundary of this container**
as well as DFX. The frozen boundary keeps ports, interfaces, widths, and
parameters consistent across variants. Validate and save the block design
after conversion. Do not guess a Tcl property name for the freeze setting;
use the BDC configuration UI or a release-verified API.

```tcl
set_property CONFIG.ENABLE_DFX {true} [get_bd_cells <bdc_name>]
```

This must run **after** `PR_FLOW` is enabled on the project, as described in
the skill's input-preservation/conversion step.
The tool now recognizes the BDC as a reconfigurable partition and will
generate the appropriate images/constraints for it during implementation.

In Vivado 2026.1 a container cell reports `TYPE hier`, like a plain
hierarchy. Identify containers by a non-empty `CONFIG.ACTIVE_SYNTH_BD`.

The partition definition and RM for a DFX-enabled BDC are created by Vivado
only when the top BD's output products are generated
(`generate_target all [get_files <top_bd>.bd]`; INFO `Project 1-1718`/
`1-1719`). Run that before `create_pr_configuration`, and use the generated
names (e.g. partition `design_1_<bdc_cell>`, RM `<bdc_name>_inst_0`) with the
RP cell path under the wrapper instance (e.g. `design_1_i/<bdc_cell>`).
Generating output products does not launch runs.

**Versal hierarchy constraint:** if NoC or SmartConnect IP is to be
dynamically reconfigured, use IP Integrator and BDCs. Do not put a block
design inside an RTL-instantiated partition definition; UG909 documents that
combination as unsupported.

### 4. Preserve boundary apertures and validate BD connectivity

Before converting the flat hierarchy, record the address base/range of every
static-to-RP interface. Preserve those values on the corresponding BDC
boundary; define an aperture for each boundary interface with the interface
pin property `APERTURES` (`CONFIG.APERTURES` is read-only on a BDC pin,
`BD 41-737`, and leaves `BD 41-2914` reported).
Ensure each RM variant exposes compatible interfaces, then validate:

```tcl
set_property APERTURES {{<base> <range>}} [get_bd_intf_pins /<bdc>/<intf>]
validate_bd_design
```

Require no address-aperture overlap and no unintended address-map changes.
Report any required but unsupported address/interface conversion rather than
silently changing the flat design's map.

### 5. NoC-connected RMs (Versal)

For every NoC path from a static NMU to an NSU in an RP:

1. Split the path at the RP boundary with a NoC INI. For the single-load
	strategy, keep the static NMU as the sole owner of the path. In Vivado
	2026.1 this is `CONFIG.INI_STRATEGY load` (UG909 "INI Ownership
	Concept": static NMU => Single Load, driver owns the path), set on both
	ends of the boundary INI **before** the BDC conversion:

	```tcl
	set_property CONFIG.INI_STRATEGY load [get_bd_intf_pins {<static_noc>/M00_INI <hier>/<rp_noc>/S00_INI}]
	```
2. Export each boundary endpoint once and expose matching virtual NoC
	interface names in static and RM designs.
3. Rewrite the NoC connection XDC to terminate at the virtual interface, and
	place RM-side NoC constraints in the RM XDC with `USED_IN synthesis_pre`
	and RM scoping.
4. For multiple RPs, preserve static ownership and avoid duplicate endpoint
	export/path ownership.
5. Run `validate_noc` and `report_noc`; inspect the ownership table and check
	that BD 41-2477, 41-2479, 41-2480, and 41-2577 are absent. `validate_noc`
	needs generated output products and `report_noc` needs an open design;
	before that, `validate_bd_design` runs the NoC compiler and reports these
	DRCs. Also resolve `BD 41-2914` (no manual aperture on a DFX container
	interface) by setting apertures equal to the flat design's address
	base/range.

NoC interface and strategy properties vary by Vivado release. Use the
installed release's IP Integrator API/UI and inspect the actual interfaces;
do not guess property names. If validation cannot establish a single owner,
matching static/RM interface names, or clean DRCs, stop and document the
remaining work.

## Common Failure: Referencing an Instance That Doesn't Exist

```
ERROR: [BD 41-1279] Block Container 'rp_container' is referencing
an instance 'rp1rm1' that does not exist in the design.
```

Cause: the RM's `.bd` Tcl script wasn't sourced before the static top-level
`.bd` script during project regeneration. Fix the source order (RM scripts
first, static/top BD script last) — see
[vivado-revision-control/dfx-revision-control](../../vivado-revision-control/dfx-revision-control/REFERENCE.md).

## After Conversion

The BDC is now a reconfigurable module for its partition. Pblock creation,
implementation, and `pr_verify` are out of scope for this skill and are left
as next steps for the user or a follow-up skill.
