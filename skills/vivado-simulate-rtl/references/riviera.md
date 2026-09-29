<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Riviera-PRO Backend

Use this reference for Aldec Riviera-PRO with a Vivado project. Do not reuse an
Active-HDL install or library tree. The canonical `export_simulation`
identifier is `riviera`.

## Discovery triggers

Riviera-PRO, Riviera, Aldec `vsim` from a Riviera tree, `ALDEC_PATH`,
`target_simulator Riviera`, `export_simulation -simulator riviera`.

## Prerequisites

Discover or ask for:

- Riviera installation/`ALDEC_PATH` and the executable-reported version
- license environment (`ALDEC_LICENSE_FILE` or site equivalent)
- version-matched AMD libraries from `compile_simlib -simulator riviera`
- Red Hat Linux or Windows host supported by UG973 for the Vivado release
- project, simulation set, top, mode, and finite runtime

Do not guess paths. Do not run `compile_simlib` without approval.

## Vivado integration

```tcl
set simset [get_filesets <simset>]
update_compile_order -fileset $simset
set_property target_simulator Riviera [current_project]
set_property compxlib.riviera_compiled_library_dir \
  <absolute-compiled-library-dir> [current_project]
```

Canonical IDs:

- `target_simulator`: `Riviera`
- `export_simulation -simulator`: `riviera`
- `compile_simlib -simulator`: `riviera`

Query `riviera.*` fileset properties. Do not copy Active-HDL or Questa
prefixes.

## Execution

```tcl
export_simulation \
  -simulator riviera \
  -of_objects $simset \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

Run generated compile/elaborate/simulate scripts in order. Integrated
`launch_simulation` is allowed when Riviera is on PATH. Bound the run; do not
use `run -all` without a watchdog.

Typical behavioral directory: `<project>.sim/<simset>/behav/riviera/`

## Artifacts and verdict

Preserve transcript, generated scripts, library map, waveform from the
generated flow, seed, plusargs, and defines. Require compile/elaborate
success, a normal simulator exit, an explicit `TEST_PASS`, and no unexpected
error/fatal. Waveform presence is not PASS.

## Diagnostics

License failure, picking ModelSim/Questa `vsim` instead of Riviera, stale
CLIBS, encrypted-IP warnings that become errors, and timeout: classify as
environment/`timeout` unless a checker identifies the DUT.

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
