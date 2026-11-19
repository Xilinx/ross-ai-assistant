---
name: hwemu-edf-runner-reference
description: >
  EDF flow — the reference runner script the skill writes for the user when it asks for
  RUN_SCRIPT. It is a starting point to copy and edit; it is never staged or run by the skill.
metadata:
  version: "1.0.0"
---

# HW Emulation (EDF flow) — Reference runner

When `FLOW` is `edf` and `RUN_SCRIPT` is being asked, **first write this reference runner** to
`<COMPILED_OUT_DIRECTORY>/run_app_hw_emu_reference.sh` (only if `COMPILED_OUT_DIRECTORY` exists),
and tell the user: "I wrote a reference runner to `<path>`. Copy it, edit it for your app and give
me your script as `RUN_SCRIPT` — or give me this file if it is right as it is." The skill never
stages or runs the reference; only `RUN_SCRIPT` is staged and run.

Fill the file names from the inputs and select the branch by board (from `PLATFORM`):

| `PLATFORM` | `fpgautil` line in the runner |
|---|---|
| **vek385**, **vrk160** | `fpgautil -b <PDI file name> -o <DTBO file name>` first (the `.pdi` and `.dtbo` in `PACKAGE_DIR`) |
| **vck190**, **vek280** | none — the PDI is already in `BOOT.bin` |

What the runner does, and what any `RUN_SCRIPT` must keep:
- sets `XCL_EMULATION_MODE=hw_emu` (the guest needs it for the host);
- waits up to 60 seconds for the zocl render node (`/dev/dri/by-path/platform*zyxclmm_drm-render`), because
  in the `hw_emu` guest the device can show up well after login and a host started too early aborts
  with `No such device with index '0'` (on the OSPI boards it only appears after `fpgautil`);
- runs `xrt-smi examine`, prints its last 10 lines and says whether XRT reports a device;
- runs `./<HOST_EXE file name> ./<XCLBIN file name>` — the names are those of the files `wic cp`
  puts in the rootfs;
- prints `ERROR: Embedded host run failed, RC=<rc>` when the return code is non-zero, then always
  `INFO: Embedded host run completed.` — these markers make `launch_hw_emu.sh` exit and are what the
  launch stage watches for;
- does **not** power the guest off.

```bash
cd <COMPILED_OUT_DIRECTORY>

# vck190 / vek280 (SD) is the active branch; for vrk160 / vek385 use the commented one instead.

# --- vck190 / vek280 (SD): NO fpgautil ---
cat > run_app_hw_emu_reference.sh <<'EOF'
#!/bin/bash

export XCL_EMULATION_MODE=hw_emu
# Wait up to 60 s for the zocl render node, then check that XRT sees a device,
# before starting the host.
for i in {1..60}; do
        ls /dev/dri/by-path/platform*zyxclmm_drm-render >/dev/null 2>&1 && break
        sleep 1
done
ls /dev/dri/by-path/platform*zyxclmm_drm-render >/dev/null 2>&1 \
        && echo "INFO: zocl render node found." \
        || echo "WARNING: zocl render node not found after 60 s."
xrt_out=$(xrt-smi examine 2>&1)
echo "$xrt_out" | tail -10
echo "$xrt_out" | grep -qi "no device" && echo "WARNING: XRT reports no device." \
        || echo "INFO: XRT device check done."
# Command to launch application. Format: <host_exe_path> <cmd_line_args>
./<HOST_EXE file name> ./<XCLBIN file name>
return_code=$?
if [ $return_code -ne 0 ]; then
        echo "ERROR: Embedded host run failed, RC=$return_code"
fi
echo "INFO: Embedded host run completed."
EOF
chmod +x run_app_hw_emu_reference.sh

# --- VRK160 / VEK385 (OSPI): fpgautil loads the PDI + dtbo overlay ---
# cat > run_app_hw_emu_reference.sh <<'EOF'
# #!/bin/bash
#
# fpgautil -b <PDI file name> -o <DTBO file name>
#
# export XCL_EMULATION_MODE=hw_emu
# # Wait up to 60 s for the zocl render node, then check that XRT sees a device,
# # before starting the host.
# for i in {1..60}; do
#         ls /dev/dri/by-path/platform*zyxclmm_drm-render >/dev/null 2>&1 && break
#         sleep 1
# done
# ls /dev/dri/by-path/platform*zyxclmm_drm-render >/dev/null 2>&1 \
#         && echo "INFO: zocl render node found." \
#         || echo "WARNING: zocl render node not found after 60 s."
# xrt_out=$(xrt-smi examine 2>&1)
# echo "$xrt_out" | tail -10
# echo "$xrt_out" | grep -qi "no device" && echo "WARNING: XRT reports no device." \
#         || echo "INFO: XRT device check done."
# # Command to launch application. Format: <host_exe_path> <cmd_line_args>
# ./<HOST_EXE file name> ./<XCLBIN file name>
# return_code=$?
# if [ $return_code -ne 0 ]; then
#         echo "ERROR: Embedded host run failed, RC=$return_code"
# fi
# echo "INFO: Embedded host run completed."
# EOF
# chmod +x run_app_hw_emu_reference.sh
```
