<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Error Diagnosis & Recovery

## Common failure patterns and auto-diagnosis

`diagnose_failure()` checks the generation log for XSA load errors, board or user
DTS lookup failures, permission errors, missing repository entry points,
hardware-family detection failures, and Tcl argument errors. Use the other
rows when reading the log directly.

| Error Pattern | Root Cause | Fix |
|---|---|---|
| `[Hsi 55-1545]`, `[Hsi 55-1451]`, or `[HDF 64-4]` | XSA loading failed; inspect the full message for corruption or incompatibility | Check archive integrity and tool compatibility; re-export only if needed |
| `could neither be located` or board/user DTS `not found` | Wrong board DTS name/path or version mismatch | Run `gen_sdt.sh --list-boards` to see valid names (from `<repo>/device_tree/data/kernel_dtsi/BOARD/linux/`) and re-run with a correct one |
| `'-board_dts' option is going to be deprecated` | Legacy flag on 2026.2+ | Harmless warning — the wrapper already emits `-user_dts` with `.dtsi` filenames; `-board_dts` is removed in future releases |
| `can't create directory ...: permission denied` | Output dir not writable | Check permissions on outdir, or use `--outdir <writable_path>` |
| `wrong # args` | Tcl argument mismatch | Check the command syntax and tool compatibility |
| `CUSTOM_SDT_REPO set but path invalid` or `Failed in finding .../device_tree.tcl path` | Invalid custom repo path | `unset CUSTOM_SDT_REPO` or fix the path (expects `<repo>/device_tree/data/device_tree.tcl`) |
| `Couldn't determine the hardware family` | SDTGen could not identify the hardware family | Check the XSA for a supported processor and verify tool compatibility |
| `hsi::open_hw_design: Time (s): ...` | Routine timing report | Large XSAs can take longer to open |
| `sdtgen not found!` or `sdtgen: command not found` | Environment not set up | See Setup below |
| `could not read "/tmp/my"` (path truncated at a space) | Whitespace in an XSA/DTSI/outdir path; sdtgen re-splits its own argv | Symlink or copy to a whitespace-free path. The wrapper rejects this before launching |
| `No Zephyr board DTSI file ... found` | Zephyr board DTSI not in the repo | Informational for Linux targets. For Zephyr, pass `--zephyr-dts <file.dtsi>` (`-user_zephyr_dts`) |
| Run "succeeds" but output is wrong/incomplete | `sdtgen` printed `ERROR:` yet exited 0 | The wrapper fails the run if any log line begins with `ERROR:`, regardless of SDTGen’s exit code. Read the log for the cause. |

## Board shortlist metadata

Read `SYSTEMINFO/@BOARD` and `SYSTEMINFO/@ARCH` from the supplied XSA's `sysdef.xml`
as described in `SKILL.md`. `--list-boards` still enumerates all installed DTSI names;
the agent uses the XML values to display a shortlist. Extraction uses `unzip` and
`awk`, with no Python dependency. If either tool is unavailable, extraction fails,
or metadata is missing, report that and use the available attribute or offer the
full list for manual selection.
A board-family fallback (e.g. `vek385_1` → `vek385`) offers broader candidates, not a
revision mapping. Do not interpret board identifier suffixes or board-definition
version tokens as hardware revisions.

## Environment setup

The wrapper resolves `sdtgen` from an installation variable or `PATH`; both are not required. Source `settings64.sh` when the tool needs its runtime environment. Full compilation also requires `cpp` and `dtc` on `PATH`.

Priority order used by `scripts/gen_sdt.sh`:

1. `${XILINX_VITIS}/bin/sdtgen`
2. `${XILINX_VIVADO}/bin/sdtgen`
3. `sdtgen` already on `PATH`

If none of the lookup methods finds `sdtgen`, ask which installation to use. A readable path or shared build area alone is insufficient to select an installation. After setting the installation variable, run `--env-check` and record or pin the canonical path it reports. Daily-build symlinks can move.

Source the Vitis or Vivado installation's `settings64.sh` when runtime setup is
needed. If the runtime is already available, setting `XILINX_VIVADO` to the
installation root lets the wrapper find its `bin/sdtgen` directly.

`--list-boards` locates the board DTSI repository through `CUSTOM_SDT_REPO`,
`XILINX_VITIS`, or `XILINX_VIVADO`, in that order. If your setup exposes
`sdtgen` through `PATH` alone, set `CUSTOM_SDT_REPO` to the local
`system-device-tree-xlnx` repository before listing boards.

## Error recovery strategies

| Scenario | Recommended action |
|----------|---------------------|
| XSA not found | Follow [step 1 of SKILL.md](../SKILL.md#instructions) to request a valid XSA path. |
| sdtgen crashes mid-generation | Retry once with `--debug`, capture full log |
| Output dir already exists | Clean and regenerate (`--clean`), or confirm with user in interactive mode |
| Board DTS mismatch | Re-run `gen_sdt.sh --list-boards`, show the names, and ask the user to pick one (or supply `--custom-dts <path>`) |
| Slow hardware-design loading | Inspect progress and storage; elapsed-time messages alone are not errors |
| Custom SDT repo has errors | Fall back to the installed repo; report which file failed |

Diagnostic patterns are hints, not proof of a root cause or guaranteed messages in every release. Read the full tool log before recommending re-export or a version change. Exporting-tool version checks use `sysdef.xml` or `xsa.xml`; unavailable metadata is reported as a skipped check.
