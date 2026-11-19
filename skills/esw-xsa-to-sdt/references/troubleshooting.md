<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Error Diagnosis & Recovery

## Common failure patterns and auto-diagnosis

`scripts/gen_sdt.sh`'s `diagnose_failure()` pattern-matches the
generation log against these signatures:

| Error Pattern | Root Cause | Fix |
|---|---|---|
| `[Hsi 55-1545]`, `[Hsi 55-1451]`, or `[HDF 64-4]` | XSA loading failed; inspect the full message for corruption or incompatibility | Check archive integrity and tool compatibility; re-export only if needed |
| `could neither be located` or board/user DTS `not found` | Wrong board DTS name/path or version mismatch | Run `gen_sdt.sh --list-boards` to see valid names (from `<repo>/device_tree/data/kernel_dtsi/<board>/linux/`) and re-run with a correct one |
| `-board_dts is deprecated` | Legacy flag on 2026.2+ | Harmless warning — the wrapper already emits `-user_dts` with `.dtsi` filenames; `-board_dts` is removed in future releases |
| `Permission denied` | Output dir not writable | Check permissions on outdir, or use `--outdir <writable_path>` |
| `wrong # args` | Tcl argument mismatch | Check the command syntax and tool compatibility |
| A missing/invalid `CUSTOM_SDT_REPO` path | Invalid custom repo path | `unset CUSTOM_SDT_REPO` or fix the path (expects `<repo>/device_tree/data/device_tree.tcl`) |
| Slow `hsi::open_hw_design` | Large XSA or slow storage | Inspect progress before treating elapsed time as failure |
| `No such file or directory: sdtgen` | Environment not set up | See Setup below |
| `could not read "/tmp/my"` (path truncated at a space) | Whitespace in an XSA/DTSI/outdir path; sdtgen re-splits its own argv | Symlink or copy to a whitespace-free path. The wrapper rejects this before launching |
| `No Zephyr board DTSI file ... found` | Zephyr board DTSI not in the repo | Informational for Linux targets. For Zephyr, pass `--zephyr-dts <file.dtsi>` (`-user_zephyr_dts`) |
| Run "succeeds" but output is wrong/incomplete | `sdtgen` printed `ERROR:` yet exited 0 | The wrapper now scans the log for `^ERROR:` and fails the run regardless of exit code |

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

If none resolves the tool, ask which installation to use. Record the canonical tool path reported by `--env-check`; daily-build symlinks can move.

Either `source <Vitis_install>/settings64.sh` first, or export the two
variables directly:
```bash
export XILINX_VIVADO=<path-to-vivado-install>/Vivado
export PATH="$XILINX_VIVADO/bin:$PATH"
```

## Error recovery strategies

| Scenario | Recommended action |
|----------|---------------------|
| XSA not found | Prompt the user for the XSA path via a question-card; do **not** auto-search the filesystem |
| sdtgen crashes mid-generation | Retry once with `--debug`, capture full log |
| Output dir already exists | Clean and regenerate (`--clean`), or confirm with user in interactive mode |
| Board DTS mismatch | Re-run `gen_sdt.sh --list-boards`, show the names, and ask the user to pick one (or supply `--custom-dts <path>`) |
| Slow hardware-design loading | Inspect progress and storage; elapsed-time messages alone are not errors |
| Custom SDT repo has errors | Fall back to the installed repo; report which file failed |

Diagnostic patterns are hints, not proof of a root cause or guaranteed messages in every release. Read the full tool log before recommending re-export or a version change. Exporting-tool version checks use `sysdef.xml` or `xsa.xml`; unavailable metadata is reported as a skipped check.
