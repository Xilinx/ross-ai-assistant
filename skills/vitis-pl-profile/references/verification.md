<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Verifying PL profiling in the xclbin

After `v++ --link`, confirm that compile-time instrumentation matches the user's goal
**before** recommending a profiled application run.

## Dump `DEBUG_IP_LAYOUT`

```bash
xclbinutil --input <design.xclbin> \
  --dump-section DEBUG_IP_LAYOUT:JSON:debug_ip_layout.json
```

Tool location: `$XILINX_XRT/bin/xclbinutil` (built from the XRT repository).

## Expected entries by build flags

| Build flags used | Expect in `m_debug_ip_data` |
|------------------|----------------------------|
| `--profile.exec …` **or** `--profile.data … :all` | `ACCEL_MONITOR` (data trace includes execution trace) |
| `--profile.data …` on memory ports | `AXI_MM_MONITOR` with `m_name` containing `m_axi_*` |
| `--profile.data …` on streams | `AXI_STREAM_MONITOR` |
| `--profile.data … :all` (not `:counters`) | AIM/ASM **and AM** with **`m_properties` bit 0** set (`& 1`) |
| `--profile.stall` (compile+link) | AM entries with **`m_properties` bit 2** set (`& 4`) |
| Trace mode (`:all`) | `TRACE_S2MM` or `TRACE_S2MM_FULL`, often `AXI_TRACE_FUNNEL`, `AXI_MONITOR_FIFO_*` |
| Platform / link deadlock overlay | `ACCEL_DEADLOCK_DETECTOR` (optional; not all builds add it) |

## Failure diagnosis

| Observation | Likely cause | Fix |
|-------------|--------------|-----|
| Section missing / dump error | No profile options on link | Add `--profile.data` and/or `--profile.exec` (exec-only); re-link |
| `m_count` is 0 | Empty layout | Re-link with profile flags |
| AM present, no AIM | No `--profile.data` | Add `--profile.data` for relevant ports |
| Monitors present, `m_properties` = 0 | Used `:counters` only | Change to `:all` if trace needed |
| No `TRACE_S2MM` | Counters-only build or trace infra omitted | Use `:all`; check platform supports trace offload |
| Stall trace empty at runtime | AM lacks `0x4` property | Add `--profile.stall` on **compile** + HLS `syn.rtl.kernel_profile=1` |
| Link fails on `trace_memory` | Name not on platform | Use valid bank from `platforminfo` / platform docs — names vary (`DDR`, `LPDDR1`, …) |
| `pl_deadlock_detection` no-op | No `ACCEL_DEADLOCK_DETECTOR` | Rely on stall/exec trace; hw_emu `compiler.deadlockDetection` |

## Optional: build metadata

Profile options may also be recorded in build metadata:

```bash
xclbinutil --input <design.xclbin> \
  --dump-section BUILD_METADATA:JSON:build_metadata.json
```

Useful for confirming which `v++` profile switches were in effect when the user did
not save the link command.

## Optional: compare before / after

Keep the uninstrumented xclbin (or dump) and diff monitor counts after adding profile
flags — helps confirm a rebuild actually changed the layout.

## Next step

When verification passes, continue to **runtime `xrt.ini` configuration** (Workflow §5) for:

- `xrt.ini` `[Debug]` recommendations
- Run artifact analysis
- Live `xrt-smi examine --report debug-ip-status` during hangs
