<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Connectivity / Link `.cfg` Reference

The V++ link needs to know how the component ports connect. This is expressed in
a connectivity `.cfg` (a `[connectivity]` section, plus optional link/debug
sections). The integration project consumes one or more cfg files
(`container_options.cfg_files`).

## Stream connections

Wire an output port of one kernel to an input port of another (or to the AI
Engine graph PLIO):

```
[connectivity]
stream_connect=mm2s_1.s:ai_engine_0.DataIn1
stream_connect=ai_engine_0.DataOut1:s2mm_1.s
```

Format: `stream_connect=<src_inst>.<src_port>:<dst_inst>.<dst_port>`.
- `<inst>` is `<kernel>_<cu_index>` (e.g. `mm2s_1` = first CU of the `mm2s`
  kernel); the AI Engine appears as `ai_engine_0`.
- AIE graph PLIO port names (e.g. `DataIn1`, `DataOut1`) come from the AIE graph's
  `input_plio`/`output_plio` `create("<name>", ...)` names.
- If the graph created its PLIOs **unnamed** (the 2-arg `create(plio_32_bits, "…")`, as the
  shipped `simple` example does), there is no stable name to target — either add explicit names
  in the graph source (`create("DataIn1", …)`) or read the compiler-generated name from the
  built graph (under `build/**/Work/**`) and use it verbatim. Names are case-sensitive.

The skill's `--connect src:dst` flag authors exactly these `stream_connect` lines
into a `connectivity.cfg` for you; `--cfg <file>` uses a ready-made one.

## Number of compute units

```
[connectivity]
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
```
`nk=<kernel>:<count>[:<name1>,<name2>,...]` instantiates CUs (CU names comma-separated). Default is 1 CU per
kernel named `<kernel>_1`.

## VSS output (`build_output_type='VSS'`)

When the integration project links with **`build_output_type='VSS'`** (Mode B —
`create_integration_project`; Mode A template one-shot is **XSA only**), the link cfg must
declare the subsystem with a **`vss=`** line under `[connectivity]` **before** `proj.build()`.
The tool does not add this from the shipped `aie_sys_design` template cfg (that file only has
`stream_connect` lines). **Leave `hw_design` empty** in `create_container_options` for VSS (the
link is part-based; a non-empty platform `.xsa` is rejected by the server).

```
[connectivity]
vss=<vendor>:<library>:<vss_name>:<version>:<inst1>,<inst2>,...
```

- Colon-separated **vendor : library : VSS component name : version**, then a **comma-separated**
  list of kernel/AIE **instance names** (same names as `nk` / default `<kernel>_1`, plus
  `ai_engine_0` for the AIE).
- Example (aie_sys_design–style mm2s + polar_clip + s2mm + AIE):

```
[connectivity]
nk=mm2s:1:mm2s_1
nk=polar_clip:1:polar_clip_1
nk=s2mm:1:s2mm_1
vss=amd.com:vitis:aie_sys_vss:1.0:ai_engine_0,mm2s_1,polar_clip_1,s2mm_1
stream_connect=mm2s_1.s:ai_engine_0.DataIn1
stream_connect=ai_engine_0.Data_clip_in0:polar_clip_1.input
stream_connect=polar_clip_1.output:ai_engine_0.Data_clip_out0
stream_connect=ai_engine_0.DataOut1:s2mm_1.s
```

Keep **`nk`**, **`stream_connect`**, and **`vss=`** in the on-disk `system.cfg` before build (same
cfg path / post-create rules as above). VSS link accepts **`proj.build(target='hw')` only** — not
`hw_emu`. Nested / hierarchical VSS uses additional `vss=` and `sc=` lines — see UG1701 *Linking a
VSS Component*.

## Debug / profiling (optional)

```
[connectivity]
# ...
[debug]
chipscope=mm2s_1
[profile]
data=all:all:all
```

## Save temps / advanced (optional)

```
save-temps=1

[advanced]
misc=solution_name=link
```

## Rules & gotchas

- **"No stream resources found …" means the link ran with NO connectivity — two causes:**
  1. **Edits went to a file the link doesn't read.** A relative `system.cfg` is
     `<proj>/system.cfg`. An absolute `/foo/system.cfg` is **copied** at create to
     `<proj>/hw_link/system.cfg` (Vitis adds a header and the `nk` lines; `vitis-sys.json`
     `cfgFiles` shows `hw_link/system.cfg`), and the link reads that copy — later edits to
     `/foo/system.cfg` are ignored. Prefer a relative bare name (`cfg_files=["system.cfg"]`);
     with an absolute path, edit `hw_link/<name>`.
  2. **`stream_connect` missing at build time.** `create_integration_project` scaffolds a
     `system.cfg` with only the `nk` lines, so connectivity written **before** create — or wiped
     by a **manual `cmake` reconfigure** — is gone by build time. Write the cfg **after** create,
     build via `proj.build()` (never hand-run cmake/make), and **read the on-disk `system.cfg`
     right before building to confirm it has BOTH the `nk` and the `stream_connect` lines.**
- Every AIE graph PLIO/GMIO and HLS stream port must be connected, or the link
  fails: `port should have one and only one valid connection`.
- Instance names must match the `nk` names (or the default `<kernel>_1`).
- AIE PLIO names are case-sensitive and must match the graph source.
- Comments use `#`.
- One `[connectivity]` section can hold many `stream_connect`/`nk` lines; connectivity lines from
  several cfg files add up. A single-value option set more than once keeps the **first** value v++
  reads: command line, then cfg files left to right, then top to bottom (UG1702).

## Minimal AIE + mm2s/s2mm example

```
[connectivity]
nk=mm2s:1:mm2s_1
nk=s2mm:1:s2mm_1
stream_connect=mm2s_1.s:ai_engine_0.DataIn1
stream_connect=ai_engine_0.DataOut1:s2mm_1.s
```
This feeds `mm2s` → AIE graph input `DataIn1`, and AIE output `DataOut1` → `s2mm`.
**Save this as `<integration-project-dir>/system.cfg`** and pass `cfg_files=["system.cfg"]`
(relative), per the cfg path rule above.
