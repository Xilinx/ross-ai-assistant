<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ModelSim Backend

Use this reference for Siemens EDA ModelSim with a Vivado project. Keep
ModelSim distinct from Questa even though the command family overlaps. The
canonical `export_simulation` identifier is `modelsim`.

## Discovery triggers

ModelSim, ModelSim DE, `vsim` from a ModelSim install, `target_simulator
ModelSim`, `export_simulation -simulator modelsim`. Do not select ModelSim
because Questa is missing.

## Prerequisites

Discover or ask for:

- ModelSim installation/`MODEL_TECH` and the executable-reported version
- license environment (`MGLS_LICENSE_FILE` or `LM_LICENSE_FILE` as used on site)
- version-matched AMD libraries from `compile_simlib -simulator modelsim`
- a UG973-supported host for the installed Vivado release
- project, simulation set, top, mode, and finite runtime

Do not guess paths. Do not run `compile_simlib` without approval.

## Vivado integration

```tcl
set simset [get_filesets <simset>]
update_compile_order -fileset $simset
set_property target_simulator ModelSim [current_project]
set_property compxlib.modelsim_compiled_library_dir \
  <absolute-compiled-library-dir> [current_project]
```

Query properties after setting them. Canonical IDs:

- `target_simulator`: `ModelSim`
- `export_simulation -simulator`: `modelsim`
- `compile_simlib -simulator`: `modelsim`

Query `list_property $simset` for `modelsim.*` runtime and log options. Do not
copy `questa*` prefixes.

## Execution

Prefer Vivado-generated scripts:

```tcl
export_simulation \
  -simulator modelsim \
  -of_objects $simset \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

Or `launch_simulation -scripts_only` / integrated `launch_simulation` when the
Vivado session has ModelSim on PATH. Run compile, then elaborate/optimize, then
simulate. Do not replace generated scripts with handwritten `vlog`/`vsim` for
IP, mixed-language, encrypted, or netlist work.

Typical behavioral directory: `<project>.sim/<simset>/behav/modelsim/`

## Artifacts and verdict

Preserve transcript/log, generated scripts, library map/`modelsim.ini`, WLF,
seed, plusargs, generics, and defines. Require compile/elaborate success, a
normal simulator exit, an explicit `TEST_PASS` (or framework equivalent), and
no unexpected `** Error` / `** Fatal`. A WLF is not functional PASS.

## Diagnostics

License checkout failure, missing `vsim`, wrong ModelSim vs Questa install,
stale `compile_simlib` output, encrypted-IP rejection, and timeout: classify as
environment unless the transcript shows a design or testbench assertion.

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
