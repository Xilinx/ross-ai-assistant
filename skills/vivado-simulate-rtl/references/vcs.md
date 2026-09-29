<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# VCS Backend

Use this reference for Synopsys VCS / VCS MX with a Vivado project. The
canonical `export_simulation` identifier is `vcs`. Help text may also mention
`vcs_mx`; do not invent a third identifier.

## Discovery triggers

VCS, VCS MX, `vcs`, `vlogan`, `simv`, `target_simulator VCS`,
`export_simulation -simulator vcs`. Never select VCS because XSim failed.

## Prerequisites

Discover or ask for:

- `VCS_HOME` and the executable-reported version
- license environment (`SNPSLMD_LICENSE_FILE` or site equivalent)
- GNU/compiler package required by that VCS install
- version-matched AMD libraries from `compile_simlib -simulator vcs`
- Linux host supported by the installed Vivado/UG973 pair
- project, simulation set, top, mode, and finite runtime

Current official Vivado integration is Linux-only. Do not guess paths. Do not
run `compile_simlib` without approval.

## Vivado integration

```tcl
set simset [get_filesets <simset>]
update_compile_order -fileset $simset
set_property target_simulator VCS [current_project]
set_property compxlib.vcs_compiled_library_dir \
  <absolute-compiled-library-dir> [current_project]
```

Canonical IDs:

- `target_simulator`: `VCS`
- `export_simulation -simulator`: `vcs`
- `compile_simlib -simulator`: `vcs`

Query `vcs.*` fileset properties from the installed release. Do not copy Questa
property names.

## Execution

```tcl
export_simulation \
  -simulator vcs \
  -of_objects $simset \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

Generated flow is typically `vlogan`/`vhdlan`, then `vcs` elaborate, then
`./simv`. Use generated scripts for mixed-language and encrypted AMD IP.
Integrated `launch_simulation` is allowed when VCS is on the Vivado session
PATH. Keep the run bounded; VCS DVE GUI is optional and may be absent.

On Ubuntu and other hosts where `/bin/sh` is dash, VCS wrappers fail at
elaboration with `Illegal option -h`. Synopsys scripts start with
`#!/bin/sh -h`; the kernel runs dash with `-h`, and dash does not implement
that flag. PATH trampolines and `LD_PRELOAD` cannot intercept kernel shebang
execution. If you cannot change the system `/bin/sh` and user namespaces are
blocked, point `VCS_HOME` at a writable overlay: copy `$VCS_HOME/bin` (and
`$VCS_HOME/linux64/bin`) scripts whose shebang is `#!/bin/sh`, rewrite the
interpreter to `/bin/bash` (keep `-h`; bash accepts it), and symlink the rest
of the install. Then put the overlay `bin` first on `PATH`.

Typical behavioral directory: `<project>.sim/<simset>/behav/vcs/`

## Artifacts and verdict

Preserve `vlogan`/`vcs`/`simv` logs, generated scripts, library map, waveform
database from the generated flow (often FSDB/VPD), seed, plusargs, and defines.
Vivado's generated batch `*_simulate.do` is typically just `run <time>` and
`quit`, so no waveform is written by default: report that the generated flow
produced none rather than naming a file that does not exist, and add dumping
only when waveforms are needed for the diagnosis or requested.
Require successful analysis and elaboration, a normal `simv` exit, an explicit
`TEST_PASS`, and no unexpected error/fatal. Waveform presence is not PASS.

## Diagnostics

License failure, missing `vcs`/`simv`, GCC/package mismatch, library map
errors, encrypted-IP compile failure, and SDF annotation errors are environment
or flow issues unless an assertion identifies the DUT. Timeouts are `timeout`.

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
