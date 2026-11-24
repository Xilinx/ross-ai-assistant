<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# EDF build troubleshooting

- [Environment faults](#environment-faults)
- [Host toolchain faults](#host-toolchain-faults)
- [Configuration faults](#configuration-faults)
- [Fetch and mirror faults](#fetch-and-mirror-faults)
- [Resource faults](#resource-faults)
- [WIC modification faults](#wic-modification-faults)
- [Reading a failure](#reading-a-failure)

## Environment faults

**`bitbake: command not found` / `BUILDDIR` unset.** The environment was sourced in a child
shell, so it did not survive into this one. Source the initializer and run the build in the
*same* shell invocation. See `$esw-yocto-source-setup`.

**Build fails in native tasks with odd `PATH`, `LD_LIBRARY_PATH` or Python errors.** A
Vitis/Vivado `settings64.sh` was sourced in the build shell. The wrapper hard-fails on
`XILINX_VITIS`/`XILINX_VIVADO` for this reason. Use a separate shell for `sdtgen`
(`$esw-xsa-to-sdt`) and a clean one for BitBake.

**The build dies the moment the calling tool call returns.** A plain `&` background job is
killed with its process group when the shell exits. Use `--detach` (which uses `setsid`) and
poll the log file.

## Host toolchain faults

**`internal compiler error: Illegal instruction` in a `-native` recipe.** The *host* compiler
crashed; the target toolchain is not involved. Causes are a compiler built for a newer
microarchitecture than the host CPU supports, or a genuinely buggy install. Confirm in seconds
with a trivial file rather than re-running the recipe:

```bash
printf '#include <float.h>\ndouble f(void){return DBL_MAX;}\n' > /tmp/t.c
<suspect-gcc> -O2 -c /tmp/t.c -o /tmp/t.o     # ICE here proves the compiler, not the recipe
```

`DBL_MAX` is a good probe because constant folding of large doubles reaches optimizer paths
that expose this class of fault; such a compiler can still build thousands of other files
successfully, which makes the failure look recipe-specific when it is not.

Query the effective `TMPDIR` and `HOSTTOOLS_DIR` for the main configuration and each
enabled multiconfig. Set `yocto_hosttools` to the evaluated directory for the configuration
being checked; do not substitute the shell's `TMPDIR` or assume `$BUILDDIR/tmp`.

Run the probe against the compiler on `PATH` before the first build and against every existing
host-tools tree. Multiconfigs have independent trees and can retain a bad compiler after the
main tree is repaired:

```bash
for h in "<main HOSTTOOLS_DIR>" "<multiconfig HOSTTOOLS_DIR>"; do
    test -d "$h" || continue
    ls -l "$h"/{gcc,g++,cpp,gcc-ar}
    printf '#include <float.h>\ndouble f(void){return DBL_MAX;}\n' |
        "$h/gcc" -x c - -O2 -c -o /tmp/gcc-probe.o
done
```

**Fixing `PATH` does not fix the build.** This is the critical, non-obvious part. OE does not
invoke the compiler from `PATH`; it resolves host tools through `${yocto_hosttools}/`,
a directory of symlinks that OE creates **only when an entry is missing**. An entry created by
the first build therefore survives every later `PATH` change, so the build keeps using the
broken compiler while `which gcc` reports the good one. Inspect the real target:

```bash
ls -l "${yocto_hosttools}"/{gcc,g++,cpp,gcc-ar}
```

Repoint the stale entries, then verify with the single failing recipe before restarting the
whole build:

```bash
ln -sf /usr/bin/gcc "${yocto_hosttools}/gcc"      # also g++, cpp, gcc-ar
# Repeat for each affected evaluated HOSTTOOLS_DIR.
bitbake -c compile <failing-native-recipe>
```

Repoint only the compiler entries. The remaining `hosttools` links (coreutils, binutils) are
unrelated and swapping them wholesale risks new mismatches. Note these symlinks are
regenerated from `PATH` if `tmp/` is ever removed, so a wipe reintroduces the fault unless
`PATH` is corrected first.

Do this check up front whenever a build is starting on an unfamiliar host, rather than waiting
for a `-native` recipe to fail hours in: resolve `${yocto_hosttools}/gcc`, compare it with
`command -v gcc`, and run the probe against whichever one `hosttools` actually points at.

**`relocation R_X86_64_32 ... can not be used when making a PIE object` right after fixing the
compiler.** This is the follow-on failure, not a new fault. The recipe's build tree still holds
object files produced by the previous compiler, and the two disagree on PIE defaults (a
distribution GCC usually defaults to PIE, a hand-built one often does not). `make` recompiles
only the file that failed, then links new PIE objects against the old non-PIE ones.

Clearing the one recipe (`bitbake -c cleansstate <recipe>`) fixes that recipe, but every other
`-native` recipe already built by the bad compiler carries the same mismatch and can fail later
at link time. When the affected build is young — the first native recipes only, no target
packages — it is cheaper and more predictable to correct the compiler first, then discard
`tmp/` and `sstate-cache/` and rebuild than to clean recipes one at a time. Confirm the elapsed
work first, and treat this as approval-worthy whenever hours of cached output exist.

Order matters when wiping: fix `PATH` **before** deleting `tmp/`, because OE regenerates
`tmp/hosttools` from `PATH` on the next build and will otherwise re-link the broken compiler.
Verify through the link itself afterwards, not through `PATH`:

```bash
"${yocto_hosttools}/g++" -O2 -x c++ -c /tmp/probe.c -o /tmp/probe.o
```

## Configuration faults

**`ERROR: Nothing PROVIDES '<target>'`.** Either the recipe is not in a configured layer, or
the target is being built against the wrong machine class. Check the two-machine split in
[targets.md](targets.md): `xilinx-bootbin` against a common machine, or
`edf-linux-disk-image` against a board machine, both misfire. Confirm the target exists with
`--list-targets`.

**`ERROR: Machine <name> not found`.** No `conf/machine/<name>.conf` in `$BUILDDIR/conf` or
any layer. Generate it with `$esw-gen-machine-conf`, or choose one from `conf-notes.txt`.

**A machine name resolves to the wrong file.** `$BUILDDIR/conf/machine` precedes layers in
`BBPATH`, so a generated machine shadows a BSP machine of the same name. This is usually
intended when building from your own SDT, but confirm which file is in play — the wrapper
prints the resolved `machine conf` path on every run.

**Image builds but omits boot files.** Expected on a common machine: they set
`HW_BOOT_MODE = ""` and `IMAGE_BOOT_FILES = ""`. Build the boot artifacts separately against
the board machine.

**`COMPATIBLE_MACHINE` errors.** The recipe is restricted to a SoC family or architecture the
current machine does not match. Check the recipe's `COMPATIBLE_MACHINE:<override>` lines
against the machine's `SOC_FAMILY`/`MACHINEOVERRIDES`.

## Fetch and mirror faults

EDF `local.conf` normally sets `SOURCE_MIRROR_URL` and `SSTATE_MIRRORS` to a shared
path, plus `INHERIT += "own-mirrors"`.

**Every fetch is slow, or sstate is never reused.** Verify the mirror paths in `local.conf`
actually exist and are readable from this host. A missing mount degrades silently into
building everything from source — slow, but not an error.

**Upstream fetch failures with mirrors present.** `own-mirrors` restricts fetching; a recipe
whose source is absent from the mirror fails rather than falling through to upstream. Report
the missing artifact instead of disabling the mirror configuration unilaterally.

## Resource faults

**`No space left on device`, or `BB_DISKMON_DIRS` halts the build.** EDF `local.conf` installs
`HALT` monitors at 100M on `TMPDIR`, `DL_DIR`, `SSTATE_DIR` and 10M on `/tmp`. A full image
build consumes tens of GB in the evaluated `TMPDIR`. The wrapper's `--min-free` gate
(default 50 GB) checks `BUILDDIR`; separately check free space on `TMPDIR`, `DL_DIR`, and
`SSTATE_DIR` when they use other filesystems. The monitors also watch `/tmp`.

**Out-of-memory during compile/link.** Reduce parallelism for the invocation without editing
`local.conf`, for example:

```bash
BB_NUMBER_THREADS=8 PARALLEL_MAKE="-j8" MACHINE=<machine> bitbake <target>
```

## WIC modification faults

**`wic.WicError: Can't find executable 'mdir'`.** The native sysroot supplying `wic` does not
necessarily include mtools. In the clean Yocto shell, run `bitbake mtools-native`, locate
`<evaluated COMPONENTS_DIR>/*/mtools-native/usr/bin`, and prepend that directory along
with the selected native sysroot's `usr/bin` for every `wic` FAT operation. Keep using the
selected native sysroot in `--native-sysroot`; do not substitute a host-installed `wic`.

**An AMD Account `.wic.xz` URL hangs at zero bytes or returns HTML.** The
`xef.html?filename=` endpoint is an authenticated/export-control form, not always a direct
anonymous artifact URL. Stop bounded zero-byte transfers, ignore partial output, and request
the path to a browser-authenticated download. Confirm candidates with `file`, `xz -t`, and
`xz --robot -l` before decompression.

**The image has two FAT partitions and `boot.bin` placement is unclear.** Inspect both labels
and the release's WKS. Standard EDF disk images use partition 1 (`esp`/`/efi`) for `boot.bin`;
partition 2 (`storage`) is user data. Copy the inserted file back out and compare its checksum
with the source before recompressing.

**The rootfs and boot artifacts have different timestamps or build IDs.** Compare the WIC's
`edf-version.txt` with the deploy directory's `bootbin-version.txt` and
`boot.bin.manifest.json`. Confirm the same EDF/Xilinx release, compatible common-machine SoC
family and board machine, and intended hardware boot mode. Timestamp differences are not proof
of incompatibility, but disclose them and distinguish integrity validation from an actual board
boot test.

**Decompression or recompression runs out of space.** Use `xz --robot -l` before extraction to
obtain the uncompressed byte count. Allow roughly twice that amount for a conservative workflow
that retains the working WIC while creating a replacement compressed image. Keep the original
source immutable and do not remove the uncompressed working copy without the user's request.

## Reading a failure

BitBake prints the failing task and its logfile path:

```
ERROR: <recipe>-<version> do_<task>: Execution of '<script>' failed
ERROR: Logfile of failure stored in: .../temp/log.do_<task>.<pid>
```

Read that `log.do_*` file — the console output is a summary and usually omits the real cause.
`ERROR: Task (...) failed with exit code '1'` alone is never enough to diagnose.

Useful follow-ups, none of which mutate the build:

```bash
bitbake -e <recipe> | grep -E '^(MACHINE|DISTRO|SOC_FAMILY)='   # resolved variables
bitbake-layers show-layers                                       # configured layers
bitbake-layers show-recipes <target>                             # which layer provides it
bitbake -g <target>                                              # dependency graph
```

Never patch files under `tmp/work` to make a failed task pass. Those files are generated and
the change is neither durable nor reviewable. If a source change is genuinely needed, stop,
report the diagnosis, and propose an explicitly approved patch or `.bbappend` in a persistent
layer. Re-run the least destructive task necessary; do not use `cleansstate`, `cleanall`, or
delete `tmp/` without approval.

## TMPDIR on NFS

OE's sanity checker rejects NFS-backed `TMPDIR`; resolve this before starting a
build rather than disabling the checker or disk monitors.

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

Repeat the path and filesystem checks for every enabled multiconfig. Re-query
`HOSTTOOLS_DIR` and `DEPLOY_DIR_IMAGE` after relocation, and probe the compiler in
each evaluated host-tools tree before retrying the previously requested build.
Report the effective paths, filesystem and authorized configuration changes.
