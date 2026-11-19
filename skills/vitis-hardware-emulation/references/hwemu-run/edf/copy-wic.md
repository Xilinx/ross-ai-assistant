---
name: hwemu-edf-copy-wic
description: >
  EDF flow step 3 — wic cp the user's runner script and the run artifacts into
  the rootfs .wic. Consumes YOCTO_ENV, RUN_SCRIPT,
  HOST_EXE, XCLBIN, DTBO, PDI.
metadata:
  version: "2.1.0"
---

# HW Emulation (EDF flow) — Copy files into the rootfs .wic

Uses: `YOCTO_ENV`, `RUN_SCRIPT`, `HOST_EXE`, `XCLBIN`, `DTBO`, `PDI`.

The user supplies the in-guest runner script as `RUN_SCRIPT`. The skill does not edit it: it
sources the SDK env (for the `wic` tool) and `wic cp`s the script and the run artifacts into
the rootfs `.wic` partition, and the launch runs the script by its file name. The script must be
executable (check with `[ -x "<RUN_SCRIPT>" ]`; if it is not, tell the user to `chmod +x` it and stop).

The reference runner the user was given at STEP 0 is in [runner-reference.md](runner-reference.md);
it is not staged.

**Three bits vary by `PLATFORM`** — select per the table:

| `PLATFORM` | Runner `fpgautil` | `wic cp` sector size | Also copy `<PDI>` + `<DTBO>` to wic |
|---|---|---|---|
| **VEK385** | `fpgautil -b <PDI> -o <DTBO>` | `--sector-size=4096` (UFS rootfs `*.wic.ufs`) | yes |
| **VRK160** | `fpgautil -b <PDI> -o <DTBO>` | plain `wic cp` (default) | yes |
| **vck190**, **vek280** | **none — no `fpgautil`** | plain `wic cp` (default) | no |

**The runner script must not power the guest off, and it must print the end markers** ([runner-reference.md](runner-reference.md) shows how). After the host it prints
`ERROR: Embedded host run failed, RC=<rc>` when the return code is non-zero, then
always prints `INFO: Embedded host run completed.` — these markers are what make
`launch_hw_emu.sh` exit, and what the launch stage watches for.

**The runner should check device readiness before the host starts** (the reference runner does). In the `hw_emu` guest the
device can show up well after login, and a host started too early aborts with
`No such device with index '0'`. On the OSPI boards the device only appears after
`fpgautil` has loaded the overlay. The runner therefore waits up to 60 seconds for the
zocl render node (`/dev/dri/by-path/platform*zyxclmm_drm-render`), then runs
`xrt-smi examine`, prints its last 10 lines (the device list is at the end) and says whether
XRT reports a device, and only
then starts the host. If the node or the device is missing, the runner prints a
`WARNING:` and still runs the host, so the failure stays visible in the log. The same
wait is used on the OSPI and the SD boards. In the reference runner it also sets
`XCL_EMULATION_MODE=hw_emu`, which the guest needs for the host.

The runner script calls the host executable, the `.xclbin` and (on VRK160/VEK385) the PDI and
DTBO by the file names they have in the rootfs, which are the file names of
`<HOST_EXE>`, `<XCLBIN>`, `<PDI>` and `<DTBO>`, because `wic cp` keeps the source file
name.

**`fpgautil` runs only on the OSPI platforms** (VRK160/VEK385) to load the PDI +
DT overlay at runtime. On **vck190/vek280 the runner does NOT call `fpgautil`** —
the PDI is already in `BOOT.bin`, so those platforms neither run it nor copy the
`<PDI>`/`<DTBO>` into the rootfs.

Run from `COMPILED_OUT_DIRECTORY`:

```bash
cd <COMPILED_OUT_DIRECTORY>
[ -x "<RUN_SCRIPT>" ] || { echo "RUN_SCRIPT is not executable: <RUN_SCRIPT>"; exit 1; }

# unset required — YOCTO_ENV aborts if LD_LIBRARY_PATH is set; see rules.md "Toolchain env"
unset LD_LIBRARY_PATH
source <YOCTO_ENV>

# wic cp flags: select from PLATFORM. VEK385's rootfs is a UFS image (*.wic.ufs)
# and needs --sector-size=4096; other platforms use *.wic.qemu-sd, default size.
WIC_CP="wic cp"                              # VEK385: WIC_CP="wic cp --sector-size=4096"
ROOTFS_WIC=$(readlink -f base_camp_images/*.rootfs.wic.*)   # the dated rootfs image the undated link points to
WIC="$ROOTFS_WIC":2                          # partition 2
$WIC_CP <RUN_SCRIPT>        $WIC
$WIC_CP <HOST_EXE>          $WIC
$WIC_CP <XCLBIN>            $WIC
# OSPI (VRK160 / VEK385) ONLY — the runtime fpgautil needs these in the rootfs;
# vck190 / vek280 skip them (the PDI is already in BOOT.bin):
# $WIC_CP <PDI>             $WIC
# $WIC_CP <DTBO>            $WIC
```

**Select `WIC_CP` from `PLATFORM`**: a **VEK385** platform uses a UFS
rootfs (`*-rootfs.wic.ufs`) and every `wic cp` must pass `--sector-size=4096` —
omitting it corrupts the copy; other platforms (vck190/vek280/…) use
`*-rootfs.wic.qemu-sd` with the default sector size (plain `wic cp`). `ROOTFS_WIC` is the
**dated** rootfs image that the undated `*.rootfs.wic.*` link points to (the same file
`update-bootbin` writes to). If any source file is missing
the copy fails — confirm the product was built and packaged (this skill does not build it).
