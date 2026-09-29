<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Environment Preflight

Catch host, library, license, and toolchain problems before spending a long
simulation on them. Many reported simulation failures never reach the design.

## Run the doctor

The doctor ships with this skill at `scripts/sim_env_doctor.py`, next to
this `references/` directory. Run it from the skill directory's location in
the current agent's install; there is no need to search for it.

```bash
python3 <skill-dir>/scripts/sim_env_doctor.py --json
```

Useful options:

- `--vivado <path>` to check a specific install instead of `PATH`;
- `--clibs <dir>` to validate a compiled-library directory;
- `--simulator <id>` to check a third-party backend's license variables.

The script is read-only. It exits nonzero when a hard failure is found and
prints each check as `pass`, `warn`, or `fail` with the evidence it used.

## What it checks

- Vivado and XSim executables resolve, and the reported version banner.
- Shared-library closure of the simulator kernel, so a missing `.so` is named
  before launch rather than at runtime.
- `LD_LIBRARY_PATH` entries that exist and are readable.
- Host C/C++ toolchain presence and version for DPI and `xsc`.
- `/bin/sh` implementation, which breaks Synopsys wrappers when it is dash.
- Compiled-library directory existence, contents, and any recorded tool
  version, so a `compile_simlib` mismatch is visible.
- License environment variables for the selected third-party simulator.
- Writable working directory and free space for waveform and coverage output.
- Display availability, which matters only for GUI requests.

## Interpreting results

A `fail` is an `environment` classification with evidence attached. Report the
exact missing prerequisite. Do not edit RTL, and do not substitute another
simulator to route around it.

A compiled-library mismatch is the most common silent cause of strange
elaboration errors. Libraries must be recompiled after any Vivado version or
update change, and after installing a new third-party simulator.

When a check fails in a way that is not obviously a local misconfiguration,
research it with [known-issue-research.md](known-issue-research.md) before
concluding it is site-specific.

## Manual checks the script cannot make

Ask the user, or confirm in Vivado:

```tcl
report_property [current_project]
get_property compxlib.<simulator>_compiled_library_dir [current_project]
export_simulation -help
compile_simlib -help
```

Never guess site paths, license servers, or module names. Never run
`compile_simlib` without approval; it is long and it writes to shared areas.

## Official sources

- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
- [How to compile Vivado simulation libraries (AR 64083)](https://adaptivesupport.amd.com/s/article/64083?language=en_US)
- [compile_simlib design assistant (AR 63904)](https://adaptivesupport.amd.com/s/article/63904?language=en_US)
