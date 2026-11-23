<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Troubleshooting

- `BUILDDIR` unset or command missing: return to `$esw-yocto-source-setup`; do not source a guessed
  checkout inside the parsing workflow.
- Required SDT files absent: regenerate the SDT with `$esw-xsa-to-sdt` or correct the supplied
  directory.
- `Unable to fetch <uri>`: the BitBake fetcher could not retrieve the archive/URL. Verify the
  URL resolves (HEAD request) and that the environment reaches the host; do not fall back to
  downloading and extracting by hand, which discards the reproducible `SDT_URI`.
- `Only System Device-tree directory is supported, given file <archive>`: the fetch/unpack path
  was skipped and the archive itself got validated, which happens when BitBake is unavailable in
  the sourced environment. Fix the environment rather than pre-extracting the archive.
- `XSA files are not supported`: the tool rejects `.xsa` in every form. Produce an SDT with
  `$esw-xsa-to-sdt` and pass that.
- Archive unpacks to several SDT directories: append `;S=<subdir>` to the archive/URL so the
  fetcher selects the right source directory.
- Unknown option: inspect both `gen-machine-conf --help` and
  `gen-machine-conf parse-sdt --help` from the active environment. Pass wrapper global
  options before `--` and subcommand options after it; syntax varies by release.
- Unsupported processor/device: align the XSA/SDT generator and Yocto layer branches, then
  regenerate the SDT before retrying.
- Existing output collision: preserve it and ask whether to choose another output/name or
  explicitly replace it.
- Generated machine is not visible to BitBake: report the layer containing its `conf/machine`
  directory and add that layer through the user's normal layer-management workflow.
- Long silence after `Constructing ... recipe sysroot`: the first run builds
  `kconfig-frontends-native` and `esw-conf-native` through BitBake and can take 10-20 minutes.
  Run the wrapper detached with its output in a log file and poll it; do not treat the pause as
  a hang or restart the command.
- `ModuleNotFoundError: No module named 'yaml'`: the Yocto environment `python3` lacks PyYAML.
  Re-run with `GEN_MACHINE_CONF_PYTHON=/usr/bin/python3` (step 7); never install packages into
  the build environment to fix it.
- `[WARNING] pl.dtso not found ... Skipping pl overlay copy` after `-g full`: benign when the
  SDT's `pl.dtsi` holds an empty `amba_pl` node, i.e. the design has no PL peripherals. Confirm
  by reading `pl.dtsi` and report it as expected instead of re-running.
- `[ERROR] Zephyr linker assist failed` with `SRAM range ... resolved to 3 nodes` means the
  SDT exposes overlapping or duplicate R5 TCM nodes (for example `psv_r5_0_atcm@0` plus a
  CIPS-prefixed R5 ATCM at address zero), so the assist cannot choose a unique SRAM owner.
  Read `output/<machine>/gen-machine-conf.log` for the exact nodes. Do not hand-edit generated
  DTS files or silently drop the domain. Ask whether to use a Linux-only template and
  `xilinx-bootbin`, regenerate with an explicitly approved R5 bare-metal Kconfig choice plus
  `--force`, or stop for an upstream SDT/lopper fix. Replacing Zephyr with bare metal is a
  material platform change.
- Template appears ignored or the wrong hardware was used: read the wrapper's `Running:` line to
  see the exact argument order, then check `SDT_URI` in the generated machine conf. A template's
  `--hw-description` is only a default and is overridden by an explicit one.
- `A machine <name> was found in ... this may conflict`: the template's default machine name
  collides with a layer's machine conf. The generated copy under `${BUILDDIR}/conf/machine` takes
  precedence, but surface the collision and offer a unique `--machine-name`.
- `stdout-path` missing from the generated `chosen` node, or `bootargs` naming a console whose
  node is `status = "disabled"`: a template `CONFIG_YOCTO_BBMC_*_DTSI` custom DTSI disabled that
  peripheral and deleted its alias after the console selection was applied. Diff the `chosen`
  node across `output/<machine>/work/*-serial-chosen.dtsi`, `work/*-serial.dts`,
  `conf/dts/<machine>/system-top-no-pl.dts`, and the final `*-linux.dts` to find the stage that
  drops it; the custom DTSI is applied in the last step. Treat it as a reserved peripheral, not
  a tool bug - the BSP usually assigns that UART to a firmware/RTOS domain. Either keep the
  BSP's console or override `CONFIG_YOCTO_BBMC_*_DTSI` with an edited copy, and tell the user
  the two domains would then share one port.
- A config-only re-run produced no change: change detection hashes the hardware description, so
  an unchanged SDT skips regeneration. Re-run with `--force`.

## TMPDIR on NFS

An NFS-backed Yocto `TMPDIR` blocks the native-tool preparation used by
`gen-machine-conf`. A missing native sysroot or `mconf` after the NFS sanity error
may be a consequence; address the first error before diagnosing missing tools.

1. Query `bitbake-getvar --value TMPDIR` (or the release's equivalent) in the
   initialized Yocto shell. Use `findmnt -T <path>` on that path or its nearest
   existing parent. Do not infer it from `$BUILDDIR/tmp` or the shell's `TMPDIR`.
2. Preserve valid local storage. If the query fails, inspect the active configuration
   before retrying. For `nfs`/`nfs4`, stop preparation; keep the sanity checker enabled.
   Sources and build configuration may remain on NFS.
3. Agree on writable local storage unique to this user/build, with enough free space.
   Prefer persistent scratch; use `/tmp` only after checking its cleanup policy,
   filesystem and capacity.
4. With existing or new authorization, set `TMPDIR = "<local-path>"` in
   `$BUILDDIR/conf/local.conf`, preserving unrelated settings and resolving existing
   assignments. For a session-only setting, use the release's supported passthrough
   mechanism; `export TMPDIR` alone may be overridden and must be reapplied later.
5. Preserve the old work tree; do not delete or copy generated state. Relocation may
   rebuild native tools. Re-query `TMPDIR`, verify its filesystem and free space,
   and report the path and configuration change before retrying authorized work.

Report the evaluated path, filesystem and authorized configuration change, then
retry generation with the previously approved SDT, template, machine name, overrides
and PL options. Report the log, exit status and generated paths. Native-tool preparation
is part of generation and does not authorize an image build.

## U-Boot DTB exceeds its early-memory allowance

If downstream `xilinx-bootbin` fails in U-Boot's `init_sp_bss_offset_check` with
`u-boot.dtb is larger than 1 quarter of (CONFIG_SYS_INIT_SP_BSS_OFFSET - CONFIG_SYS_MALLOC_F_LEN)`,
inspect the compiled DTB size and both values in the failed U-Boot build's `.config`.
Successful DTS compilation alone does not check this U-Boot memory-layout limit.

For the observed VEK385 Rev A design on the internal `rel-v2026.2` checkout, generation
without an overlay produced a 170,943-byte U-Boot DTB against a 163,840-byte allowance
(`0x220000` stack/BSS offset and `0x180000` early malloc reservation). The SDT contained
separate `*_boot.pdi` and `*_pld.pdi` artifacts. After the user selected `full`, regeneration
with `--force parse-sdt --gen-pl-overlay full` separated the PL nodes into `pl.dtso`.
The subsequent U-Boot DTB was 115,475 bytes and `xilinx-bootbin` built successfully without
changing either memory setting. This was build validation, not a hardware boot test.

For a matching segmented PL workflow, follow the overlay-selection gate in `SKILL.md`,
then regenerate and verify the overlay's `firmware-name` references the intended PLD PDI.
Rebuild only when requested. Do not infer DFX from multiple PDIs, assume every size failure
needs an overlay, or increase the memory allowance without checking the intended boot and
memory layout. Keep fixes in generator inputs or persistent recipe configuration, not in
generated DTS or `tmp/work` files.
