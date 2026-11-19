<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Discover component firmware for an APU JTAG load

Search only the user-supplied directory, its canonical target and existing adjacent
extracted directories. Resolve aliases; do not extract or rename raw partitions.

For ZynqMP locate PMU firmware (`pmufw*.elf` or `pmu-firmware*.elf`), A53 FSBL
(`fsbl*.elf`), U-Boot (`u-boot*.elf`) and TF-A (`bl31.elf`). Prefer canonical targets
of standard aliases. Use `devicetree/cortexa53-linux.dtb` for the separate Linux DTB
when present. Otherwise identify a unique appropriate DTB. Skip R5 applications.
For Zynq-7000 locate its ARM32 FSBL and U-Boot instead.

Check existence, readability, nonempty files and ELF architecture with standard
shell commands (`readlink -e`, `file -L`, `readelf -h -l`). Do not routinely read BIF,
manifests or ESW source, or require hash matching. This applies to initial loads too.

## When candidates are ambiguous

Deduplicate symlinks before treating candidates as distinct. Do not substitute
nodtb and complete U-Boot variants blindly. Consult available metadata only when
it resolves an actual ambiguity or compatibility concern; otherwise ask for the
missing component identity. Missing BIF or manifest is not itself a blocker.

## When the BIF is missing

No BIF is required. ZynqMP ELF load addresses and entry points come from the ELFs.
The ZynqMP helper defaults the separate DTB address to `0x100000`; honor an explicit
user override. No R5 core selection is needed for this APU-only flow. Versal complete
images contain their own partition loading information. Zynq-7000 uses the same `0x100000` default for an optional separate DTB,
with an explicit address override, and finds FSBL completion from the FSBL symbol table.

Continue with the relevant family helper once required files are resolved. Do not
request paths already found, or build/fetch firmware merely to inspect existing files.
