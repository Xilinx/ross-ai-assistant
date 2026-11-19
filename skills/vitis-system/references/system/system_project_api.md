<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# System Project API Reference (Vitis Unified, 2026.2)

Ground-truth for `client.create_sys_project()` and the build/package flow the
`vitis-system` skill relies on. Sourced from
`$XILINX_VITIS/cli/vitis/cli_client.py`, `project.py`, and the shipped examples
under `cli/examples/accelerated/` (`system_project.py`, `edge_use_case_1.py`).

## What a system project is

A **system project** is the umbrella that combines a hardware design — a binary
**container** of AIE + HLS kernels (or a shipped template) — with an optional
**PS/host application**, then runs the Vitis **package** step to emit a deployable
image (SD-card image / `BOOT.BIN` + `Image` + rootfs + `.xclbin`).

- The integration (link) step = `v++ --link` → fixed XSA.
- **This skill = the package step** → deployable image.
- Standalone boot-image generation (bootgen/BIF, low-level embedded) is a separate, lower-level flow — not this package step.

## `client.create_sys_project(...)`

```python
proj = client.create_sys_project(
    name,                        # required: project name
    platform,                    # required: extensible platform .xpfm (or name)
    template=None,               # default: empty_accelerated_application
    build_output_type='XSA',     # 'XSA' | 'VMA'
    packaging_mode='EDF',        # 'EDF' | 'PetaLinux'
    embedded_component_paths={}, # {'sysroot':..,'root_fs':..,'kernel_image':..,
                                 #  'sysroot_toolchain':..,'use_sysroot_toolchain':..}
)
```
Returns the system project object.

**`packaging_mode`** (`'EDF'` default | `'PetaLinux'`) must match the resolved platform's flow —
see [`packaging.md`](packaging.md) (selection table + custom-platform rules). **BMA metadata** is
**not** an `embedded_component_paths` key: pass it via `package/package.cfg` → `[package]` `bma=…`
(see [`packaging.md`](packaging.md)).

## Compose the project

```python
proj.add_container(name='binary_container_1')                       # a binary container
proj.add_component(name='mm2s', container_name=['binary_container_1'])  # assign a kernel
proj.add_component(name='aie_test', container_name=['binary_container_1'])
proj.add_component(name='host_app')                                 # PS/host app (no container)
proj.add_cfg_files(name='binary_container_1', cfg_files=['system.cfg'])  # connectivity
# packaging options: edit package/package.cfg on disk (already registered by default);
# add_cfg_files(name='PACKAGE'|'package') is rejected with NOT_FOUND
proj.add_precompiled_kernel(xo_file_path='./k.xo', containers=['binary_container_1'])
```

Components referenced by name (`aie_test`, `mm2s`, `host_app`) are expected to
**already exist** in the workspace — build them first with the AIE-component skill,
the integration step, or the app skill (approval-gated chaining). A shipped
`--template` (e.g. `vadd`) brings its own components, so no pre-creation is needed.

## Build + package

```python
proj.build(target=None, comp_name=None, build_package=False, build_comps=True, container_name=None)
# build_package=True: build ONLY the package step. build_comps: build dependent components
# if not built yet. Returns an int exit code (0 = success).
```
- **Build gotcha (`hw` and `hw_emu`, EDF projects):** don't pass `build_package=True`. It builds
  only the package step, and in an EDF project the `VppPackage` CMake target does not depend on
  `VppLink_<container>`, so `v++ -p` fails on the missing `build/<target>/hw_link/<container>.xsa`
  (`v++ 60-587`, `60-702`). Use `proj.build(target=..., build_comps=True, build_package=False)`,
  which links and packages (for `hw_emu`: `launch_hw_emu.sh` + emu `.xclbin`).
- `proj.report()` prints the summary + valid targets; `proj.clean(target=...)`.
- Lifecycle: `client.get_sys_project(name)`, `list_sys_projects()`,
  `clone_sys_project()`, `delete_sys_project()`.
- Templates (ACCL_APP, live 2026.2): `aie_sys_design` (Versal Gen1), `aie-ml_sys_design`
  (Gen2/AIE-ML), `aie_dfx_design`, `vadd`, `empty_accelerated_application`. List with
  `client.get_templates(type='ACCL_APP')`; fetch with `client.get_template('ACCL_APP', '<name>', platforms=[xpfm])`.

## Targets — see `packaging.md` for the full hw_emu vs hw split

| Target | Build | Package output | Runs where |
|--------|-------|----------------|-----------|
| `hw_emu` (default) | emulation model (mins) | emulation package (`launch_hw_emu.sh`, emu `.xclbin`) | this host (QEMU), no board |
| `hw` | Vivado impl (tens of min → hours) | deployable `sd_card.img` / `BOOT.BIN` + `Image` + rootfs + `.xclbin` | the board (build here, boot via the on-board debug/flash flow) |

## Output artifacts

Under `<workspace>/<name>/build/<target>/…` (and a `package/` subtree):
`sd_card.img`, `sd_card/BOOT.BIN`, `launch_hw_emu.sh` (emu), `*.xclbin`. The driver
globs for these and reports the best match + the package dir. Report package
contents from the real emitted tree — never invent partition/size numbers.

## Worked example (edge_use_case_1.py idiom)

```python
client.set_workspace(path=ws); client.add_platform_repos(platforms_dir)   # headless: see system/headless.md "Workspace setup" (non-empty dir without _ide/ needs update_workspace)
xpfm = client.find_platform_in_repos('^vck190_base$')   # anchored exact match (bare substrings pick the wrong flow)
proj = client.create_sys_project(name='p', platform=xpfm,
                                 template=client.get_template('accl_app','empty_accelerated_application', platforms=[xpfm]))
# add AIE + HLS components (created separately) to a container, add a host app, then:
proj.add_container('binary_container_1')
proj.add_component('mm2s', container_name=['binary_container_1'])
proj.add_component('host_app')
proj.add_cfg_files(name='binary_container_1', cfg_files=['system.cfg'])
proj.build(target='hw_emu', build_comps=True, build_package=False)   # hw_emu: full link+package (see build gotcha)
```
