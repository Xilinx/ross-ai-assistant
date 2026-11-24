---
name: hwemu-petalinux-runner-reference
description: >
  PetaLinux flow — the reference runner script the skill writes for the user when it asks for
  RUN_SCRIPT. It is a starting point to copy and edit; it is never staged or run by the skill.
metadata:
  version: "1.0.0"
---

# HW Emulation (PetaLinux flow) — Reference runner

When `FLOW` is `petalinux` and `RUN_SCRIPT` is being asked, **first write this reference runner** to
`<COMPILED_OUT_DIRECTORY>/run_app_hw_emu_reference.sh` (only if `COMPILED_OUT_DIRECTORY` exists),
and tell the user: "I wrote a reference runner to `<path>`. Copy it, edit it for your app and give
me your script as `RUN_SCRIPT` — or give me this file if it is right as it is." The skill never
stages or runs the reference; only `RUN_SCRIPT` is run, and it must already be in the package's SD image.

Fill in the host and `.xclbin` **file names** from `HOST_EXE` and `XCLBIN` (the names they have in the SD image).

**What the launcher already does, so the runner does not need to:** `launch_hw_emu.sh` logs in, mounts
the SD partition on `/mnt`, changes into `/mnt` and runs the `-run-app` value as `./<RUN_SCRIPT file name>`.
It also prints `INFO: Embedded host run completed.` when the script ends. So the runner has no mount, no
`cd` and no end marker. It must be **executable** in the SD image, and it must not power the guest off.

**What the runner does:**
- sets `XCL_EMULATION_MODE=hw_emu`, which the guest needs for the host, and the XRT environment;
- runs `./<HOST_EXE file name> ./<XCLBIN file name>`. The pass or fail comes from the host's own
  `TEST PASSED` / `TEST FAILED` line.

**The package must match the host.** If the host starts the AI Engine graph itself (it calls the graph run
API), the package must be built with `--package.defer_aie_run`, so that the host enables the cores. Without
it, the boot image enables the cores at PDI load, and the host stalled in `graph::end()` with no error in runs
on two boards. The pre-launch check in [../common/monitor.md](../common/monitor.md) ("the AIE cores are enabled
twice") warns about this before the launch. A package built with `--package.enable_aie_debug` is
a separate case, and the same file warns about it too.

The runner below, with a graph-based host, ran to `TEST PASSED` on a vck190 and a vek280 (one design). The
`XRT_INI_PATH` file does not have to exist.

```bash
cd <COMPILED_OUT_DIRECTORY>

cat > run_app_hw_emu_reference.sh <<'EOF'
#!/bin/bash

export XILINX_VITIS=/run/media/mmcblk0p1
export XILINX_XRT=/usr
export XCL_EMULATION_MODE=hw_emu
export XRT_INI_PATH=/run/media/mmcblk0p1/hw_emu_xrt.ini
export LD_LIBRARY_PATH=/run/media/mmcblk0p1:/tmp:$LD_LIBRARY_PATH

# Command to launch application. Format: <host_exe_path> <cmd_line_args>
./<HOST_EXE file name> ./<XCLBIN file name>
EOF
chmod +x run_app_hw_emu_reference.sh
```
