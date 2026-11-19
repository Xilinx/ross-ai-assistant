---
name: esw-xsa-to-sdt
description: Generate a System Device Tree (SDT) from a Xilinx Support Archive (XSA) for AMD Zynq, ZynqMP, Versal, Versal Gen 2, Versal-Net, and MicroBlaze platforms. Use when the user asks to generate an SDT, run sdtgen, or turn an XSA exported by Vivado into a device tree. Produces the SDT directory consumed by the downstream gen-machine-conf step. For a runtime device tree overlay (.dtbo) to reload the PL, use /segcfg-petalinux-build or /segcfg-yocto-build instead.
metadata:
  version: "1.0.0"
  stage: beta
allowed-tools: Read, Bash, Write
license: MIT
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

Guide the user through generating a System Device Tree (SDT) from an XSA exported by Vivado, using the `sdtgen` tool. The generated SDT directory is the input to the downstream `gen-machine-conf` step.

## Instructions

1. **Resolve the XSA path.** Use an existing `.xsa` path supplied by the user. Otherwise ask one short free-text question: **"What is the XSA path? You can also include a board DTS name or custom DTSI path."** If a supplied path does not exist, name that path and ask for a valid one. Do not search the filesystem for XSAs. Distinguish `.xsa` from custom `.dtsi`/`.dtso` paths by extension.

2. **Offer a board DTS choice.**
   - Use an already supplied `--board-dts <name>` or `--custom-dts <path>` without asking again. If the user passes `--no-board-dts`, says "no board DTS", or requests defaults/non-interactive execution/"skip questions", generate without one unless they supplied a DTS choice. Treat `--no-board-dts` as a skill-level opt-out; omit it from the wrapper command because the wrapper does not accept that flag. A downstream answer such as "no template" does not resolve the board choice.
   - Otherwise read `SYSTEMINFO/@BOARD` and `SYSTEMINFO/@ARCH` from the XSA's `sysdef.xml` using `unzip` and `awk` as shown below. If either tool is unavailable or extraction fails, briefly report that and offer the full board list for manual selection. Then run `${CLAUDE_SKILL_DIR}/scripts/gen_sdt.sh --list-boards`.
   - Use the board component of a colon-delimited `BOARD` value to shortlist listed names case-insensitively: `xilinx.com:vpk360:1.2` → `vpk360`, `xilinx.com:vek385_1:1.1` → `vek385_1`. Prefer exact identifier matches. If none exist, show recognizable board-family matches such as `vek385` and label them as broader candidates. Use `ARCH` to organize architecture-related candidates when board matches are unavailable. If metadata is missing or unreadable, state that briefly and offer the available candidates or full list; do not guess from archive filenames or image entries.
   - Ask one short question: **"Which board DTSI should I use?"** Show the shortlist with the detected board and architecture as brief context. Use actual shortlisted names as choices, plus **"Show all boards"** and **"Without board DTS"**. Say **"You can also enter a custom DTSI path."** Avoid long explanations in the prompt; if needed, add only **"This supplies board-specific device nodes."**
   - Keep this question separate from downstream machine/template choices. Wait for the answer and use the selected name or custom path. For an unknown name, explain that it is not listed and ask again. If the user declines or has no preference, generate without a board DTS and mention that in the result.
   - Shortlisting is for display only. Never auto-select a DTSI, interpret `_1` or `1.1` as a hardware revision, or exclude Rev A/Rev B entries using an unverified mapping.

3. **Prerequisites**:
   - `XILINX_VITIS` or `XILINX_VIVADO` set (or exportable) so `sdtgen` is reachable — see `${CLAUDE_SKILL_DIR}/references/troubleshooting.md`.
   - `dtc` and `cpp` on `PATH` for full output validation (optional — the compile check is skipped if either is unavailable).

4. **Run the pipeline** with the main wrapper. Pick the mode that matches the target:
   ```bash
   # SDT without a board DTS
   ${CLAUDE_SKILL_DIR}/scripts/gen_sdt.sh <path_to_xsa> --outdir ./sdt_output

   # Known eval board — user-supplied board DTS name
   ${CLAUDE_SKILL_DIR}/scripts/gen_sdt.sh <path_to_xsa> --board-dts <name> --outdir ./sdt_output

   # Custom / non-eval board — user-supplied board DTSI
   ${CLAUDE_SKILL_DIR}/scripts/gen_sdt.sh <path_to_xsa> --custom-dts <board.dtsi> --outdir ./sdt_output
   ```
   `--custom-dts` accepts `.dtsi`/`.dtso`, not `.dts`. `--zephyr-dts` accepts a local `.dtsi` or a repository board filename; SDTGen can also copy a Zephyr board DTSI automatically from XSA metadata, even without a Linux board DTS. `--domain pmc` is the only supported domain restriction. `SDTGEN_OUTDIR` sets the default output directory; `--outdir` overrides it.

   Add `--debug` for trace output, `--clean` to wipe a previous output dir first, `--env-check` to only validate the environment, `--list-boards` to enumerate available board DTS names, or `--validate-only` to re-check an existing output.

5. **What the wrapper does** (three phases, all in `scripts/gen_sdt.sh`):
   1. **Environment check** — resolve `sdtgen` via `XILINX_VITIS` → `XILINX_VIVADO` → `PATH`.
   2. **Generate** — `sdtgen -xsa <xsa> -dir <outdir> [-user_dts <board.dtsi> [overlay.dtsi ...]] [-debug enable -trace enable]`. (The board DTS is passed via `-user_dts`; the legacy `-board_dts` is deprecated from 2026.2 and removed in future releases.)
   3. **Validate** — require `system-top.dts` and any `pcw.dtsi` or `pl.dtsi` it includes; report separate clock files and node/CPU counts as information, and compile with `cpp` and `dtc` when both are available.

6. **On failure**, read `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` and pattern-match the log against the known error signatures (XSA load failures, missing board files, permission errors, and Tcl argument errors).

7. **Available scripts**:

   | Script | Purpose |
   |--------|---------|
   | `scripts/gen_sdt.sh` | Main wrapper — generate → validate pipeline |
   | `scripts/validate_sdt.sh` | Standalone output validator using generated includes |
   | `scripts/gen_sdt.tcl` | Tcl template: generation via `-user_dts <board.dtsi> [overlay.dtsi ...]` (plus `-zocl` to create a Zocl node); omit `-user_dts` to generate without a board DTS |

   `gen_sdt.sh` is the default entry point; the `.tcl` file is a standalone alternative that drives `sdtgen` via its `set_dt_param`/`generate_sdt` API.

8. **Output and handoff** — a successful run produces `system-top.dts` and its included files, with `include/` and hardware artifacts where applicable. MicroBlaze platforms normally put processor, peripheral, and clock nodes in `pl.dtsi`, without `pcw.dtsi`. PS output varies by family; separate SoC/clock DTSIs are not universal. A Linux flow requires downstream device-tree processing; SDT generation and DTC compilation do not establish Linux boot support: confirm the target processor and downstream tool support before promising a Linux image. The `esw-gen-machine-conf` wrapper accepts PL-only output and requires nonempty `pcw.dtsi` or `pl.dtsi` only when included by `system-top.dts`. The next step is `$esw-gen-machine-conf`, which **requires a sourced Yocto build environment first** — so hand off to `$esw-yocto-source-setup` before generating, in the same shell:
   ```bash
   # Full chain:  $esw-xsa-to-sdt          (XSA → SDT, here; needs XILINX_VITIS)
   #           →  $esw-yocto-source-setup  (fetch sources + source the Yocto env in THIS shell)
   #           →  $esw-gen-machine-conf    (SDT → machine config)
   #           →  $esw-yocto-build     (bitbake targets → images/boot artifacts)
   # The Yocto stages share one clean shell; $esw-xsa-to-sdt must use a separate shell
   # because Vitis/Vivado settings corrupt the OE environment.
   gen-machine-conf parse-sdt --hw-description ./sdt_output ...    # only after the env is sourced
   ```

## Gotchas

- **`board_dts` is offered but never auto-selected** — read the XSA board/architecture metadata to shortlist names after `--list-boards`, then let the user choose. This is not hardware-revision validation. A wrong name may surface as a `could neither be located` error (see `troubleshooting.md`).
- **Generating without a board DTS fails silently, not loudly** — `sdtgen` still succeeds and
  validation still passes, but board-level nodes are simply absent. The signature is a PS
  peripheral left `status = "okay"` with nothing to attach to: e.g. `gem*` enabled while the
  DTS contains zero `phy-handle`/`ethernet-phy` nodes, so Ethernet never comes up. After a
  no-board-DTS run, grep the generated DTS for that pattern and report it, rather than
  reporting only that validation passed.
- **`-board_dts` is deprecated (2026.2+)** — the wrapper emits `-user_dts` with `.dtsi` filenames instead (e.g. `-user_dts zcu102-rev1.0.dtsi ./overlay.dtsi`); the `--board-dts <name>` flag still works and is translated to `-user_dts <name>.dtsi`.
- **`grep -c PATTERN file || echo 0` double-counts on a zero match** — `grep -c` prints `0` and still exits 1, so the fallback fires anyway and corrupts the value. Use `|| true` or `${var:-0}`.
- Required files follow the generated `#include` directives. Missing `pcw.dtsi` is normal when it is not included; a referenced file must exist. Clocks may be inlined, and small trees or domain-specific output are not necessarily incomplete.
- **Read board and architecture from `sysdef.xml`** — the standard XSA `SYSTEMINFO` attributes can be extracted with `unzip` and `awk`; Python and third-party modules are unnecessary. For the already resolved XSA path:
  ```bash
  set -o pipefail
  unzip -p "$XSA_PATH" sysdef.xml | awk '
    BEGIN { RS = ">" }
    /<([[:alnum:]_.-]+:)?SYSTEMINFO[[:space:]]/ && !found {
      for (i = 1; i <= 2; i++) {
        key = (i == 1 ? "BOARD" : "ARCH")
        value = ""
        pattern = "[[:space:]]" key "[[:space:]]*=[[:space:]]*[\"\047][^\"\047]*[\"\047]"
        if (match($0, pattern)) {
          value = substr($0, RSTART, RLENGTH)
          sub(/^[^=]*=[[:space:]]*[\"\047]/, "", value)
          sub(/[\"\047]$/, "", value)
        }
        print key "=" value
      }
      found = 1
    }
    END { if (!found) exit 1 }
  '
  ```
  This handles multiline tags and single- or double-quoted attributes. Missing
  attributes produce empty values; an unavailable tool, missing XML, or missing
  `SYSTEMINFO` means metadata is unavailable, so offer the full board list.
  `BOARD="xilinx.com:vek385_1:1.1"` and `ARCH="versal"` identify listing hints;
  neither `_1` nor `1.1` establishes Rev B without a verified mapping.

## When NOT to Use

- Parsing an existing SDT into a machine config → that's the downstream `$esw-gen-machine-conf` step (after `$esw-yocto-source-setup`).
- Running a BitBake build → that's `$esw-yocto-build`, after a machine config exists.
- Generating a device tree overlay (`.dtbo`) for runtime PL reload → use `/segcfg-petalinux-build`, `/segcfg-yocto-build`, or `/segcfg-linux-runtime`.

## References

- `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` — failure signatures, environment setup, recovery.
