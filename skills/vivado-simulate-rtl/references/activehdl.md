<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Active-HDL Backend

Use this reference for Aldec Active-HDL with a Vivado project. Official Vivado
integration is Windows-only. Do not treat Riviera-PRO as a substitute. The
canonical `export_simulation` identifier is `activehdl`.

## Discovery triggers

Active-HDL, `activehdl`, `target_simulator ActiveHDL`,
`export_simulation -simulator activehdl`. Never select Active-HDL on Linux.

## Prerequisites

Discover or ask for:

- Active-HDL Windows install and executable-reported version
- license environment (`ALDEC_LICENSE_FILE` or site equivalent)
- version-matched AMD libraries from `compile_simlib -simulator activehdl`
- Windows host listed in UG973 for the installed Vivado release
- project, simulation set, top, mode, and finite runtime

Stop with an unsupported-platform rejection on Linux before changing
`target_simulator`. Do not guess paths. Do not run `compile_simlib` without
approval.

## Vivado integration

```tcl
set simset [get_filesets <simset>]
update_compile_order -fileset $simset
set_property target_simulator ActiveHDL [current_project]
set_property compxlib.activehdl_compiled_library_dir \
  <absolute-compiled-library-dir> [current_project]
```

Canonical IDs:

- `target_simulator`: `ActiveHDL`
- `export_simulation -simulator`: `activehdl`
- `compile_simlib -simulator`: `activehdl`

Query `activehdl.*` fileset properties. Qualification of Active-HDL is often
RTL-only; do not claim SystemC or every encrypted-IP flow without checking the
current UG900/UG973 notes.

## Execution

```tcl
export_simulation \
  -simulator activehdl \
  -of_objects $simset \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

On Windows, run the generated `.bat` compile/elaborate/simulate scripts.
Integrated `launch_simulation` is allowed when Active-HDL is configured in
Vivado. Bound the runtime.

Typical behavioral directory: `<project>.sim/<simset>/behav/activehdl/`

## Artifacts and verdict

Preserve transcript, generated scripts, library map, waveform from the
generated flow, seed, plusargs, and defines. Require compile/elaborate
success, a normal simulator exit, an explicit `TEST_PASS`, and no unexpected
error/fatal. Waveform presence is not PASS.

## Diagnostics

Linux host, missing Windows install, license failure, Riviera/Active-HDL
library mix-up, and timeout: environment or unsupported-platform. Design
failures still require the first assertion and simulation time.

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
