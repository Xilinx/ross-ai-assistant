<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Zynq-7000 firmware loading

Use `zynq` for Zynq-7000 Cortex-A9 boards such as ZC702, ZC706 and ZedBoard.
This is separate from Zynq UltraScale+ MPSoC (`zynqmp`). The helper loads a matching
ARM32 FSBL, an optional separate DTB and ARM32 U-Boot; there is no PMU firmware or TF-A.
It does not program `boot.bin` directly, configure PL, start Linux or interrupt autoboot.

## Resolve artifacts

Use the supplied directory or existing companions beside the supplied `boot.bin`,
including its canonical target directory and adjacent extracted directory. Apply the
[discovery and missing-BIF rules](firmware-discovery.md#when-the-bif-is-missing), selecting
Zynq FSBL/U-Boot rather than ZynqMP's four components. Honor explicit replacement paths.
Do not extract or rename raw boot-image partitions into ELFs.

Find the matching files in the supplied directory. Check readable, nonempty files
and `readelf -h -l` output: both ELFs must be little-endian ARM ELF32 for Cortex-A9.
Do not routinely inspect BIF or manifests, including on an initial load. Resolve
actual candidate ambiguity or known compatibility conflicts when they arise.

An optional separate DTB defaults to **`0x100000`** in the helper. Pass `DTB ADDRESS`
only for an explicit override. No BIF lookup is needed to obtain the default.
This shared APU-flow address is a convention, not a guarantee for every custom
memory map. Check known ELF/DTB overlaps before loading. A complete U-Boot ELF
may already contain its own DTB; distinguish that from a separate Linux DTB.

If the package needs a PL bitstream or other partitions, report that before loading.
Resolve the PL initialization flow when it is a boot prerequisite; this helper must not
claim full package coverage. A standalone `boot.bin` without companion ELFs needs those
artifacts supplied before this flow can proceed. A `ps7_init.tcl`-only or SPL-based flow
is outside this initial helper; do not silently substitute either for the FSBL.

## FSBL completion

The helper detects FSBL completion without a per-build address. After `dow` loads
the FSBL (and its symbols), it resolves `FsblHandoffJtagExit` with XSDB's
`print &FsblHandoffJtagExit`, sets a hardware breakpoint there, runs FSBL with a
60-second timeout, and checks the PC. The standard Zynq FSBL calls this function
only on its successful JTAG-mode exit paths: after PS/DDR initialization, clearing
its reboot-status mark and locking SLCR. Failures go to the FSBL fallback instead,
so they end in the timeout, not a false completion.

This needs an FSBL with its symbol table, which EDF builds keep. A stripped FSBL
fails with a clear symbol-resolution error before the breakpoint is set; supply
the unstripped ELF. Do not disassemble the FSBL or read ESW source for routine loads.

A timeout or unexpected stop prevents the DTB and U-Boot loads. No processor reset
is performed after successful FSBL initialization.

## Target and execution

Enumerate `targets -target-properties` and record the intended cable's exact
`jtag_cable_ctx`. The A9 #0 pattern is `*Cortex-A9*#0`, covering names such as
`ARM Cortex-A9 MPCore #0`. It must match exactly one processor on that cable.
The helper polls for asynchronous target discovery and refreshes selection after
one system reset. It retries stale `Invalid context` selection errors only; ambiguity,
other access errors and exhausted polling stop the flow without another reset.

The board must be in JTAG boot mode. Through the `APU` (DAP) target, the helper reads
Zynq-7000 SLCR `BOOT_MODE` at `0xF800025C`. Reading through A9 #0 fails with an MMU
translation fault when prior software left the MMU on; the DAP read is physical. It
checks `(value & 7) == 0` (`BOOT_MODES_MASK` and `JTAG_MODE` in the Zynq FSBL's `fsbl.h`)
and does not write the boot mode. Do not use ZynqMP's register address.

```text
xsdb  scripts/zynq_jtag_boot.tcl SERVER CABLE FSBL UBOOT [DTB [DTB_ADDR]]
```

Check readable, nonempty files, ARM ELF headers and DTB magic during artifact
validation before invoking XSDB. The helper checks argument syntax and performs
boot-mode checks and target selection on hardware. Capture the exact invocation and XSDB
log. `IMAGES_LOADED` means the supplied
components downloaded and U-Boot execution was resumed, not that a prompt or Linux boot
was observed. On failure, retain the log and report the last completed stage; do not
loop resets. A failed FSBL run can leave the processor running.

## Validation status

ZC702 (`xc7z020`), EDF 2026.2, through a remote hw_server: FSBL stopped at its JTAG
exit, then the DTB at `0x100000` and U-Boot loaded and resumed (`IMAGES_LOADED`). The helper resolved `FsblHandoffJtagExit`
to `0xad04` from the FSBL symbols, and FSBL stopped there with no address argument.
U-Boot console arrival was not checked.
