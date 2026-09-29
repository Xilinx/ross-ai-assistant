<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Xcelium Backend

Use this reference for Cadence Xcelium Parallel Simulator with a Vivado
project. The canonical `export_simulation` identifier is `xcelium`.

## Discovery triggers

Xcelium, `xrun`, `xmvlog`, `CDS_INST_DIR`, `target_simulator Xcelium`,
`export_simulation -simulator xcelium`. Do not select Xcelium as a silent
fallback.

## Prerequisites

Discover or ask for:

- `CDS_INST_DIR` and the executable-reported `xrun` version
- license environment (`CDS_LIC_FILE` / `CDS_LIC_WAN` or site equivalent)
- version-matched AMD libraries from `compile_simlib -simulator xcelium`
- Linux host supported by the installed Vivado/UG973 pair
- project, simulation set, top, mode, and finite runtime

Current official Vivado integration is Linux-only. Do not guess paths. Do not
run `compile_simlib` without approval.

## Vivado integration

```tcl
set simset [get_filesets <simset>]
update_compile_order -fileset $simset
set_property target_simulator Xcelium [current_project]
set_property compxlib.xcelium_compiled_library_dir \
  <absolute-compiled-library-dir> [current_project]
```

Canonical IDs:

- `target_simulator`: `Xcelium`
- `export_simulation -simulator`: `xcelium`
- `compile_simlib -simulator`: `xcelium`

`export_simulation -single_step` is Xcelium-specific. Query `xcelium.*`
fileset properties from the installed release.

## Execution

```tcl
export_simulation \
  -simulator xcelium \
  -of_objects $simset \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

Use the generated `xrun`/multi-step scripts. Do not invent `xrun` command
lines for IP-heavy designs. Integrated `launch_simulation` is allowed when
Xcelium is on PATH. Bound the runtime; prefer batch over GUI for unattended
runs.

Typical behavioral directory: `<project>.sim/<simset>/behav/xcelium/`

## Artifacts and verdict

Preserve `xrun` logs, generated scripts, library mapping, waveform/SHM from
the generated flow, seed, plusargs, and defines. Require compile/elaborate
success, a normal `xrun` exit, an explicit `TEST_PASS`, and no unexpected
`*E` / `*F` fatals. A waveform database is not functional PASS.

## Diagnostics

License failure, missing `xrun`, INCA/Xcelium library path errors, encrypted
IP rejection, and timeout are environment/`timeout` unless an assertion names
the DUT mismatch.

In timing mode, `xmsdfc` errors followed by `*W,SDFCNC ... skipping
annotation` or `This SDF System Task will be Ignored` mean the netlist ran
with zero delay: the run is not a timing result even if it prints
`TEST_PASS`. See `diagnostics.md` (post-implementation timing).

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
