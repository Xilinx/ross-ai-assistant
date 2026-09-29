<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Other Vivado-Supported Simulators

Release snapshot and index. Each backend has a dedicated reference linked from
`SKILL.md`. Treat versions as a snapshot, not a permanent promise. Before use,
query `Vivado:vivado_doc_search` for the installed release's UG900 supported
simulators and UG973 compatible third-party tools.

## Vivado 2026.1 snapshot

| Simulator | Minimum listed version | UG973 host support | Canonical ID | Reference |
|---|---:|---|---|---|
| Siemens ModelSim DE | 2025.3 | Red Hat Linux, Windows | `modelsim` | [modelsim.md](modelsim.md) |
| Synopsys VCS | X-2025.06-SP2 | Red Hat Linux | `vcs` | [vcs.md](vcs.md) |
| Cadence Xcelium | 25.09.001 | Red Hat Linux | `xcelium` | [xcelium.md](xcelium.md) |
| Aldec Riviera-PRO | 2024.10 | Red Hat Linux, Windows | `riviera` | [riviera.md](riviera.md) |
| Aldec Active-HDL | 16.0 | Windows | `activehdl` | [activehdl.md](activehdl.md) |

Questa remains [questa.md](questa.md). XSim remains [xsim.md](xsim.md).

These are minimum compatible versions in UG973; AMD states testing is limited
to current versions. A host OS supported by Vivado is not automatically a
supported host for every third-party simulator.

## Shared rules

For every third-party backend:

1. verify current simulator/version/platform support in UG900 and UG973;
2. ask for the site-specific installation and license setup;
3. validate the executable-reported version;
4. validate version-matched AMD simulation libraries from `compile_simlib`;
5. inspect project, simulation set, top, language mix, IP, and mode;
6. have Vivado generate scripts with the canonical ID;
7. run generated compile, elaborate, and simulate scripts in order;
8. require an explicit functional PASS and preserve transcript, waveform,
   scripts, library mapping, seed, defines, plusargs, and generics.

Never invent commands when generated scripts exist. Never silently fall back to
XSim after a requested backend fails. Do not copy Questa property prefixes to
another simulator.

```tcl
report_property [current_project]
report_property [get_filesets <simset>]
export_simulation -help
compile_simlib -help
```

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG900 third-party simulator setup](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Simulator-Settings-for-Third-Party-Tools)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
