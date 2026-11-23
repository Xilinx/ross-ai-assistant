<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Inspecting `debug_ip_layout` with xclbinutil

The `DEBUG_IP_LAYOUT` section of an xclbin lists every debug / profiling IP instance
in the loaded design. XRT uses it at runtime to locate AM, AIM, ASM, trace offload,
and deadlock-detector registers.

**Product reference:** Monitors are inserted at link with **`v++ --profile.*`** —
**UG1702** [--profile Options](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/profile-Options),
**UG1700** [Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking).
If layout is empty, use **`vitis-pl-profile`**. Live counter reads on a loaded
design: **UG1700** [Using the xrt-smi Utility](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Using-the-xrt-smi-Utility)
(`debug-ip-status`). See [user_guides.md](user_guides.md).

## Extract the section

`xclbinutil` is built as part of the XRT repository and installed with XRT
(`$XILINX_XRT/bin/xclbinutil` on Linux, `%XILINX_XRT%\bin\xclbinutil.exe` on Windows).

```bash
xclbinutil --input <design.xclbin> \
  --dump-section DEBUG_IP_LAYOUT:JSON:debug_ip_layout.json
```

Equivalent short input flag: `-i <design.xclbin>`.

**Outcomes**

| Result | Interpretation |
|--------|----------------|
| Command succeeds, JSON has `debug_ip_layout.m_count` > 0 | Profiling IP is present — parse entries below |
| `m_count` is 0 or array empty | Section exists but **no monitors** — user needs **`vitis-pl-profile`** |
| Error: section not found | **No profiling IP** in this xclbin — user needs **`vitis-pl-profile`** |

Optional: `xclbinutil -i <xclbin> --info` lists sections at a high level; use the
dump command above for structured analysis.

## JSON shape

```json
{
  "debug_ip_layout": {
    "m_count": "N",
    "m_debug_ip_data": [
      {
        "m_type": "ACCEL_MONITOR",
        "m_index": "0",
        "m_properties": "5",
        "m_major": "1",
        "m_minor": "1",
        "m_base_address": "0x...",
        "m_name": "kernel_name/CU_0"
      }
    ]
  }
}
```

`m_name` is the primary key for mapping a monitor to a **compute unit**, **memory port**
(e.g. `my_kernel_1/m_axi_gmem0`), or **stream connection**.

## `m_type` values (profiling-relevant)

| `m_type` | Role | Needed for |
|----------|------|------------|
| `ACCEL_MONITOR` | CU execution / occupancy / stalls | Kernel overlap, pipelining, hang (CU idle vs active) |
| `AXI_MM_MONITOR` | Memory interface counters + trace | HBM/DDR/PLRAM bytes per port |
| `AXI_STREAM_MONITOR` | Stream counters + trace | AXI-Stream data volume between kernels |
| `AXI_TRACE_FUNNEL` | Multiplexes trace streams | Device timeline trace |
| `AXI_MONITOR_FIFO_LITE` / `AXI_MONITOR_FIFO_FULL` | Trace buffering | Device timeline trace |
| `TRACE_S2MM` / `TRACE_S2MM_FULL` | Trace DMA to host memory | `device_trace` timeline offload |
| `ACCEL_DEADLOCK_DETECTOR` | Detects CU AXI deadlocks | `pl_deadlock_detection=true` |
| `AXI_NOC` | Platform NOC visibility | System-level bandwidth (not CU-local) |
| `LAPC` / `AXI_STREAM_PROTOCOL_CHECKER` | Protocol checking | Debug, not primary perf |

Types like `ILA`, `AXI_DMA`, `HSDP_TRACE` are specialized debug — mention only if
present and relevant to the user's question.

## `m_properties` bit masks (AM / AIM / ASM)

XDP interprets compile-time properties when loading the xclbin:

| Mask | Value | Applies to | Meaning |
|------|-------|------------|---------|
| Trace enabled | `0x1` | AM, AIM, ASM | Monitor supports **trace** (not counters-only). Required for meaningful `device_trace` timelines on that monitor. |
| AM stall | `0x4` | AM only | **Stall trace** compiled in — runtime `stall_trace` can emit stall events. |

**Examples**

- `m_properties: "1"` — trace enabled, no AM stall bit.
- `m_properties: "5"` — trace (`0x1`) + AM stall (`0x4`).
- `m_properties: "0"` — typically **counters only** at compile time.

## Quick inspection checklist

When reviewing `debug_ip_layout.json`, report:

1. **Count** of each type: AM, AIM, ASM, TRACE_S2MM, funnel/FIFO, ADD.
2. **Trace readiness**: any AM/AIM/ASM with `m_properties & 1`?
3. **Stall readiness**: any AM with `m_properties & 4`?
4. **Offload path**: TRACE_S2MM (+ funnel/FIFO) present for timeline capture?
5. **Coverage**: do AIM/ASM `m_name` entries cover the user's kernel ports / streams?

If (1) is zero for AM/AIM/ASM → **`vitis-pl-profile`** before runtime ini
will help.

If (4) is missing but user needs `device_trace` timelines → rebuild with trace
infrastructure via **`vitis-pl-profile`**.

## Alternative: loaded device sysfs

On a running system with the xclbin loaded, `debug_ip_layout` is also exposed under
the device sysfs tree (see XRT `sysfs.rst`). Prefer **xclbinutil** when the user
has only the xclbin file or has not yet deployed to hardware.
