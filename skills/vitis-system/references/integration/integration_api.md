<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Integration Project API Reference (Vitis Unified, 2026.2)

Ground-truth for `client.create_integration_project()` and the helpers the
`vitis-system` skill relies on. Sourced from
`$XILINX_VITIS/cli/vitis/cli_client.py`, `project.py`, and the shipped examples
under `cli/examples/accelerated/`.

## What an integration project is

An **integration project** runs the **V++ link**: it combines AIE + HLS (and
optional user `.xo`, AIE archives, VSS) components against an extensible platform
/ hardware design and produces a **fixed XSA / VMA / VSS**. On disk it is
described by a JSON config plus the connectivity `.cfg`(s); the Python API
generates and populates these — drive everything through the API, not by
hand-editing the JSON.

**Both creation paths write `vitis-sys.json`** (there is no `vitis-int.json`; checked on 2026.2
and 2027.1). An integration project is identified by `"applicationFlow": "VITIS_INT_FLOW"` in
`vitis-sys.json` (`project_type` `VITIS_INT_FLOW` in `proj.report()`).

| Creation path | Skill mode |
|---------------|-----------|
| `create_integration_project(...)` (pure integration project) | Mode B (compose) |
| `create_system_and_integration_project(...)` (template one-shot) | Mode A (template) |

Mode A's template one-shot also scaffolds secondary system projects + host components
alongside the integration project. Either way the linked output is the same fixed
XSA/VMA/VSS and the driver locates it by extension.

This is the link step. Packaging into deployable images (SD/BOOT.BIN/
XCLBIN) is the system step (`references/system.md`).

## `client.create_integration_project(...)`

```python
proj = client.create_integration_project(
    name,                        # required: project name
    build_output_type='XSA',     # 'XSA' | 'VMA' | 'VSS'
    container_options={},        # from client.create_container_options(...)
)
```
Returns the integration project object. Build with `proj.build(target=...)`.

## `client.create_container_options(...)`

```python
container_options = client.create_container_options(
    container_name,              # required: container name
    hw_design='',                # XSA/VMA: extensible platform .xsa (required). VSS: leave EMPTY — non-empty is rejected
    kernels=None,                # HLS component names to link
    user_xo_kernels=None,        # prebuilt .xo files
    aie_component=None,          # AIE component names to link
    user_aie_archives=None,      # prebuilt libadf.a archives
    cfg_files=None,              # connectivity/link cfg files
    user_vss_path=None,          # VSS input(s)
)
```
Returns a dict:
`{'container_name': ..., 'kernels': ..., 'user_xo_kernels': ..., 'aie_component': ...,
'user_aie_archives': ..., 'cfg_files': ..., 'user_vss_path': ..., 'hw_design': ...}`

> **Know which cfg file the link reads.** A relative name (`cfg_files=['system.cfg']`) is
> `<proj>/system.cfg` (scaffolded with only the `nk` lines if it doesn't exist). An absolute path
> is **copied** to `<proj>/hw_link/<name>` (Vitis adds a header and the `nk` lines) and the link
> reads that copy, not your original — edit `hw_link/<name>`. See
> [`connectivity-cfg.md`](connectivity-cfg.md).

## Template one-shot

```python
tmpl = client.get_template(type='ACCL_APP', name='aie_sys_design', platforms=[xpfm])
client.create_system_and_integration_project(
    name, platform=xpfm, template=tmpl,
    embedded_component_paths={'sysroot': ..., 'kernel_image': ..., 'root_fs': ...})
proj = client.get_sys_project(name)
```
`create_system_and_integration_project` creates the integration project **plus**
secondary system projects (one per supported target) that consume the XSA. The
secondary system projects may want a sysroot; the integration link → XSA does
not.

## Component creation helpers

```python
# AIE (reuse the AIE-component skill; part- or platform-based)
aie = client.create_aie_component(name='aie_test', platform=xpfm)
aie.import_files(from_loc=src_dir, files=[...]); aie.update_top_level_file(top_level_file='graph.cpp')

# HLS kernel
hls = client.create_hls_component(name='mm2s')
hls.import_files(from_loc=src_dir, files=['mm2s.cpp'], dest_dir_in_cmp='src')
cfg = client.get_config_file('<ws>/mm2s/hls_config.cfg')
cfg.add_lines('hls', ['syn.file=./src/mm2s.cpp'])
cfg.add_lines('hls', ['syn.top=mm2s'])
# DO NOT add flow_target=vitis — create_hls_component already wrote it. Re-adding it makes a
# duplicate ini entry that HLS rejects and the kernel build fails. (Field-validated.)
# Set part to the EXTENSIBLE PLATFORM's silicon (xilinx_vck190_base_202620_1 -> xcvc1902-...):
cfg.set_value('', key='part', value='xcvc1902-vsva2197-2MP-e-S')
```

## Build & inspect

- `proj.build(target='hw' | 'hw_emu', ...)` — runs the V++ link. Returns
  SUCCESS/FAILURE. `build()` validates the target against the project's `supported_targets`
  and raises on an invalid one (`build_output_type='VSS'` → `'hw'` only; `'hw_emu'` raises
  `ValueError`).
- `proj.report()` — prints the project summary incl. valid targets.
- `client.get_sys_project(name)`, `client.list_sys_projects()`,
  `client.clone_sys_project(name, new_name)`.
- `client.get_templates(type='ACCL_APP')` — ACCL_APP templates (live 2026.2): `aie_sys_design`
  (Gen1), `aie-ml_sys_design` (Gen2/AIE-ML), `aie_dfx_design`, `vadd`, `empty_accelerated_application`.

## Targets

**VSS projects (`build_output_type='VSS'`): `hw` only** — no `hw_emu`.

| Target | What v++ link does | Output | Time |
|--------|--------------------|--------|------|
| `hw_emu` (default) | build the emulation model (RTL/SystemC, aiesim, QEMU) | emulation fixed XSA | minutes |
| `hw` | full Vivado synth + place-and-route + PDI | implemented fixed XSA | tens of min → hours |

## Output artifact

The linked output (fixed `*.xsa` / `*.vma` / `*.vss`) lands under
`<workspace>/<name>/` (build/export subtree). The driver globs for the extension
matching `build_output_type` and reports the path. Resource utilization is read
from the real v++ link report (`*link_summary*` / `*system_estimate*`) when
present — never estimated.

## Worked example (edge_use_case_1.py idiom, compose)

```python
client.set_workspace(path=ws); client.add_platform_repos(platforms_dir)   # headless: see integration/headless.md "Workspace setup" (non-empty dir without _ide/ needs update_workspace)
xpfm = client.find_platform_in_repos('^vck190_base$')   # anchored exact match (see skill: Resolve the platform by exact name)
proj = client.create_sys_project(name='p', platform=xpfm, template=client.get_template('accl_app','empty_accelerated_application', xpfm))
aie = client.create_aie_component(name='aie_test', platform=xpfm); aie.import_files(...); proj.add_component('aie_test')
hls = client.create_hls_component(name='mm2s'); hls.import_files(...)
proj.add_container('binary_container_1'); proj.add_component('mm2s', container_name=['binary_container_1'])
proj.add_cfg_files(name='binary_container_1', cfg_files=['system.cfg'])
proj.build(target='hw_emu')
```
The dedicated `create_integration_project` + `create_container_options` path is a
more direct way to express the same link when you don't need the surrounding
system project.
