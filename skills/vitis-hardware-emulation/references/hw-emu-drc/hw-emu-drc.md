---
name: hw-emu-drc
description: >
  Pre-hw_emu Design Rule Checks (DRCs) for running hardware emulation on an
  already-built AIE + PL product. Validation-only: checks the values gathered by
  input.md, reports each as PASS / FAIL / WARN, then gives a final GO or NO-GO
  decision.
metadata:
  version: "2.1.0"
---

# HW Emulation DRC (validation-only)

You are a **pre-emulation gatekeeper** for hardware-emulation (`hw_emu`) runs of
already-built Versal AIE + PL products. Run the fixed DRCs below, report each as
PASS / FAIL / WARN, then give a final GO / NO-GO decision.

> **CHECK-ONLY.** All inputs are gathered up front by [../input.md](../input.md);
> this pass only **validates** them. If a required value is missing/invalid,
> record a FAIL and point the user back to `input.md` — do **not** search the
> filesystem or prompt.

**Inputs (from `input.md`):** `FLOW`, `COMPILED_OUT_DIRECTORY`, `PLATFORM`, `PACKAGE_DIR`,
`XCLBIN`, `HOST_EXE`, `RUN_SCRIPT`, `YOCTO_ENV`, `QEMU_PREBUILT`, `SIMULATOR`, `CAPTURE_VCD`, `AIE_WORK_DIR`.

Rows 04–08, 13 and 14 depend on `FLOW`. An input the chosen flow does not use is **N/A —
PASS**. If DRC-12 fails, those rows are N/A (DRC-12 reports it).

## DRC Execution Protocol

Run **ALL** checks (don't stop early); collect every result, print the summary
table, then give the verdict. For each DRC, run only the documented command
against the captured value — no prompting, no filesystem hunting.

| DRC | Check | Validation command | PASS | FAIL → fix |
|---|---|---|---|---|
| 01 | Vitis env | `echo "XILINX_VITIS=${XILINX_VITIS:-<unset>}"` | `XILINX_VITIS` set | Unset → **HARD GATE**, see below |
| 02 | Build dir exists | `ls -d "<COMPILED_OUT_DIRECTORY>"` | dir exists | Missing dir → re-run `input.md` |
| 03 | Platform `.xpfm` exists | `ls "<PLATFORM>"` | `PLATFORM` set and the `.xpfm` file exists | Empty/missing → re-run `input.md` with a valid `.xpfm` (selects the QEMU boot-mode/device branch in the run stages) |
| 04 | Package dir complete | `ls "<PACKAGE_DIR>/launch_hw_emu.sh"`, then by `FLOW`: **edf** — `ls "<PACKAGE_DIR>/BOOT.bin"`, and for **VRK160** and **VEK385** also `ls "<PACKAGE_DIR>"/*.pdi "<PACKAGE_DIR>"/*.dtbo`; **petalinux** — `ls "<PACKAGE_DIR>/sd_card.img"` and a boot image named `BOOT.BIN` **in any letter case** (`ls "<PACKAGE_DIR>" \| grep -ix 'boot.bin'`); **baremetal** — `ls "<PACKAGE_DIR>"/*.pdi`, and no boot image named `boot.bin` in **any letter case** (same `grep -ix`). The `.pdi` and `.dtbo` file names can be anything, only the extensions are fixed | **edf:** `launch_hw_emu.sh` and `BOOT.bin` exist (exact name — the staging command copies it); on VRK160/VEK385 also one `.pdi` and one `.dtbo` (vck190 and vek280 need neither — the PDI is already in `BOOT.bin`). **petalinux:** `launch_hw_emu.sh`, `sd_card.img` and a `BOOT.BIN` (any case) exist. **baremetal:** `launch_hw_emu.sh` and one `.pdi` exist, and there is no boot image named `boot.bin` in any letter case | Any missing (or a boot image present for baremetal) → the product is not built/packaged for this flow; build and package it first (this skill does not), then re-run `input.md`. More than one `.pdi` or `.dtbo` → ask which one to use |
| 05 | `.xclbin` exists | `ls "<XCLBIN>"` | `edf`, `petalinux`: `XCLBIN` set and file exists; `baremetal`: N/A — PASS | Empty/missing → re-run `input.md` (copied into the rootfs `.wic`) |
| 06 | Host executable / runner script exists | `ls "<HOST_EXE>"`; for `edf` and `petalinux` also `ls "<RUN_SCRIPT>"` | `HOST_EXE` is set and exists (the host, or the ELF for baremetal); for `edf` and `petalinux` `RUN_SCRIPT` is set and exists too | Empty/missing → for `HOST_EXE` build the host (or ELF) first; for `RUN_SCRIPT` supply the runner script; then re-run `input.md` |
| 07 | QEMU prebuilt dir exists | `ls -d "<QEMU_PREBUILT>"` | **edf** and **baremetal:** the dir exists (it holds `combined.qemuboot.conf`, the boot images, `qemu-ospi.bin` or the rootfs `.wic`, etc.). **petalinux:** N/A — PASS | Empty/missing → re-run `input.md` with the `..._qemu_prebuilt` dir |
| 08 | SDK env-setup script exists | `ls "<YOCTO_ENV>"` | **edf:** `YOCTO_ENV` set and the file exists. **baremetal:** only on the SD boards (`vck190`, `vek280`, from the `PLATFORM` file name) `YOCTO_ENV` is set and exists; on the OSPI boards (`vek385`, `vrk160`) N/A — PASS. **petalinux:** N/A — PASS | Empty/missing → re-run `input.md` (sourced before `wic cp` for the `wic` tool) |
| 09 | Vitis install matches platform | the command below | `PLATFORM` is under `<install>/Vitis/base_platforms/…` and `<install>/Vitis` is the same install as `XILINX_VITIS` once symbolic links are resolved — or `PLATFORM` is not under a `…/Vitis/base_platforms/` path (**skip**, cannot tell) | **WARN only, never FAIL:** the platform comes from a different Vitis install than the sourced one (e.g. a different build/date) → say both paths (as given and resolved) and ask the user to confirm that is intended; a mismatched tool/platform pair can break the emulation. Resolve the two paths only — do not search the filesystem |
| 10 | Files named in `launch_hw_emu.sh` exist | the command below, run against `<PACKAGE_DIR>/launch_hw_emu.sh` | Every `$PWD/...` path in the script (the cosim `-qemu-dtb`, `-aie-sim-config`, `-noc-memory-config`, `-pl-sim-dir`) exists under `<PACKAGE_DIR>` — the script changes into its own directory, so `$PWD` is `PACKAGE_DIR` | Any missing, or no `-qemu-dtb` line → FAIL; **do not re-ask** — report each missing file; the package is incomplete or damaged, so rebuild and package it (this skill does not) |
| 11 | VCD inputs | the command below | `CAPTURE_VCD` is `no` (N/A — PASS), or `yes` with `AIE_WORK_DIR` an existing directory whose `.target` reads `hw` | `CAPTURE_VCD` is not `yes` or `no`, or `AIE_WORK_DIR` is given but missing or not an `hw` build → FAIL; **do not re-ask** — report for correction. `CAPTURE_VCD` is `yes` and `AIE_WORK_DIR` blank → **WARN only**: the VCD is still written, but the run summary has no AIE compiler summary and no GMIO throughput |
| 12 | Flow selected | `FLOW` is `edf`, `petalinux` or `baremetal` | one of the three | Empty or anything else → FAIL; **do not re-ask** — re-run `input.md` and choose one of the three |
| 13 | Platform fits the flow | the command below, from `<PLATFORM>` | **petalinux:** the platform is a base platform of a Vitis install (its path is under `…/Vitis/base_platforms/`) and its file name is a plain `xilinx_vck190_base…` or `xilinx_vek280_base…` (**not** a `_dfx` platform). **edf** and **baremetal:** the board (`vrk160`, `vek385`, `vck190` or `vek280`) is in the file name, and for a base platform of a Vitis install the file name is a plain `vck190_base…`, `vek280_base…`, `vrk160_base…` or `vek385_base…` (**no** `xilinx_` prefix, **not** `_dfx`) | **petalinux:** a platform outside `…/Vitis/base_platforms/`, or not a plain `xilinx_vck190_base…` / `xilinx_vek280_base…` → FAIL: "petalinux supports only the plain xilinx_vck190_base / xilinx_vek280_base platforms of a Vitis install". **edf** / **baremetal:** no board in the file name → FAIL; an install base platform with a `xilinx_` prefix or `_dfx` → FAIL: "`<flow>` supports only the plain vck190_base / vek280_base / vrk160_base / vek385_base platforms (no xilinx_ prefix, not dfx)". Re-run `input.md` with a suitable platform (the EDF stages pick the boot mode, `fpgautil` step, rootfs device and PDI/DTBO files by board) |
| 14 | Package matches the flow | the command below, on `<PACKAGE_DIR>/launch_hw_emu.sh` | **edf:** the script has `-enable-edf`. **petalinux:** it does not. **baremetal:** N/A — PASS (a baremetal launch script also has `-enable-edf`, so it is not a marker; DRC-04 tells baremetal apart: a `.pdi` and no `BOOT.bin`) | Mismatch → FAIL: "this package looks like the `<other>` flow; pick that flow or use the right package". It relies on the package being built the way the flow expects (a PetaLinux package is built with `--package.generate_sdcard`) |

DRC-09 command (exit 0 = PASS or skip, 1 = WARN). A Vitis install is often reached through a link such as
`…/2026.2_daily_latest/…`, so compare the **resolved** paths, not the strings:

```bash
case "<PLATFORM>" in
  */Vitis/base_platforms/*) ;;
  *) echo "skip: PLATFORM is not under .../Vitis/base_platforms/"; exit 0;;
esac
p="<PLATFORM>"
a=$(readlink -f "${XILINX_VITIS:-/nonexistent}"); b=$(readlink -f "${p%%/base_platforms/*}")
echo "XILINX_VITIS: ${XILINX_VITIS:-<unset>} -> $a"; echo "PLATFORM's Vitis: $b"
[ "$a" = "$b" ] && echo "OK: same install" && exit 0
echo "different Vitis installs"; exit 1
```

DRC-10 command (prints one `MISSING` line per absent file):

```bash
P="<PACKAGE_DIR>"
grep -q -- '-qemu-dtb' "$P/launch_hw_emu.sh" || echo "NO -qemu-dtb LINE"
grep -oE '\$PWD/[^ ]+' "$P/launch_hw_emu.sh" | sort -u | while read -r f; do
  [ -e "$P/${f#\$PWD/}" ] || echo "MISSING ${f#\$PWD/}"
done
```

PASS only when the command prints nothing.

DRC-11 command (prints the reason; exit 0 = PASS, 2 = WARN, 1 = FAIL):

```bash
case "<CAPTURE_VCD>" in
  no) echo "N/A: no VCD capture"; exit 0;;
  yes) ;;
  *) echo "CAPTURE_VCD must be yes or no"; exit 1;;
esac
[ -n "<AIE_WORK_DIR>" ] || { echo "AIE_WORK_DIR blank: VCD without the run-summary link"; exit 2; }
test -d "<AIE_WORK_DIR>" || { echo "MISSING DIR: <AIE_WORK_DIR>"; exit 1; }
[ "$(cat "<AIE_WORK_DIR>/.target" 2>/dev/null)" = hw ] || { echo ".target is not hw: <AIE_WORK_DIR>"; exit 1; }
echo "OK: <AIE_WORK_DIR>"
```

DRC-13 command (exit 0 = PASS, 1 = FAIL):

```bash
f=$(basename "<PLATFORM>" | tr 'A-Z' 'a-z')
case "<FLOW>" in
  petalinux)
    case "<PLATFORM>" in
      */Vitis/base_platforms/*) ;;
      *) echo "petalinux needs a base platform of a Vitis install (path under .../Vitis/base_platforms/): <PLATFORM>"; exit 1;;
    esac
    case "$f" in
      *_dfx*) echo "petalinux does not support dfx platforms (plain xilinx_vck190_base / xilinx_vek280_base only): $f"; exit 1;;
      xilinx_vck190_base*|xilinx_vek280_base*) echo "OK: petalinux on $f"; exit 0;;
      *) echo "petalinux supports only the plain xilinx_vck190_base / xilinx_vek280_base platforms: $f"; exit 1;;
    esac;;
esac
b=""; for x in vrk160 vek385 vck190 vek280; do case "$f" in *$x*) b=$x; break;; esac; done
[ -n "$b" ] || { echo "NO BOARD IN PLATFORM FILE NAME: $f"; exit 1; }
# the edf / baremetal naming rule applies only to the base platforms of a Vitis install
case "<FLOW>:<PLATFORM>" in
  edf:*/Vitis/base_platforms/*|baremetal:*/Vitis/base_platforms/*)
    case "$f" in
      *_dfx*|xilinx_*) echo "<FLOW> supports only the plain vck190_base / vek280_base / vrk160_base / vek385_base platforms (no xilinx_ prefix, not dfx): $f"; exit 1;;
      vck190_base*|vek280_base*|vrk160_base*|vek385_base*) ;;
      *) echo "<FLOW> supports only the plain vck190_base / vek280_base / vrk160_base / vek385_base platforms: $f"; exit 1;;
    esac;;
esac
echo "OK: <FLOW> on $b ($f)"
```

DRC-14 command (exit 0 = PASS, 1 = FAIL):

```bash
n=$(grep -c -- '-enable-edf' "<PACKAGE_DIR>/launch_hw_emu.sh" 2>/dev/null)
case "<FLOW>" in
  edf) [ "${n:-0}" -ge 1 ] || { echo "no -enable-edf: not an EDF package"; exit 1; };;
  petalinux) [ "${n:-0}" -eq 0 ] || { echo "-enable-edf present: not a PetaLinux package"; exit 1; };;
  baremetal) echo "N/A: no marker defined"; exit 0;;
esac
echo "OK"
```

**DRC-01 HARD GATE — do NOT proceed on FAIL.** Stop, print the report with a
NO-GO verdict, and tell the user to source their Vitis settings (see
[../../DEPENDENCIES.md](../../DEPENDENCIES.md)) so `XILINX_VITIS` is set, then
re-run.

## Output Format

After running all checks, print:

```
============================================================
  HW Emulation DRC Report
  Build dir: <COMPILED_OUT_DIRECTORY>
  Platform : <PLATFORM>
============================================================

  DRC-01  Vitis env              [PASS|FAIL|WARN]  <detail>
  DRC-02  Build dir exists       [PASS|FAIL|WARN]  <detail>
  DRC-03  Platform .xpfm         [PASS|FAIL|WARN]  <detail>
  DRC-04  Package dir complete   [PASS|FAIL|WARN]  <detail>
  DRC-05  .xclbin exists         [PASS|FAIL|WARN]  <detail>
  DRC-06  Host executable        [PASS|FAIL|WARN]  <detail>
  DRC-07  QEMU prebuilt dir      [PASS|FAIL|WARN]  <detail>
  DRC-08  SDK env-setup script   [PASS|FAIL|WARN]  <detail>
  DRC-09  Vitis matches platform [PASS|WARN]       <detail (WARN when the platform is from a different Vitis install)>
  DRC-10  launch_hw_emu.sh files [PASS|FAIL|WARN]  <detail>
  DRC-11  VCD inputs             [PASS|FAIL|WARN]  <detail>
  DRC-12  Flow selected          [PASS|FAIL|WARN]  <detail>
  DRC-13  Platform fits the flow [PASS|FAIL|WARN]  <detail>
  DRC-14  Package matches flow   [PASS|FAIL|WARN]  <detail>

------------------------------------------------------------
  FAILs: <count>   WARNs: <count>
------------------------------------------------------------
```

Then the verdict:

**0 FAILs:**
```
  VERDICT: GO — all hard checks passed. Safe to proceed.
  Recorded: PLATFORM, PACKAGE_DIR, XCLBIN, HOST_EXE, SIMULATOR, QEMU_PREBUILT, YOCTO_ENV.
  <list any WARNs the user should be aware of>
```

**Any FAILs:**
```
  VERDICT: NO-GO — fix the following before running hw_emu:

  [FAIL] DRC-XX — <one-sentence fix (usually: re-run input.md to supply/correct the value, or build the missing product first)>
  ...
```

On a NO-GO the flow stops here, and the report stage (STEP 6) writes `hw-emu_report.txt` with status
NO-GO and the failing rows (see [../report/report.md](../report/report.md)). If `COMPILED_OUT_DIRECTORY`
is missing (DRC-02), there is nowhere to write it: report in chat only.
