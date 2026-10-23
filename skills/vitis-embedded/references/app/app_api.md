<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Application API Reference

Ground-truth details for `client.create_app_component()` and the app build flow
the `vitis-embedded` skill relies on (Vitis Unified, 2026.x).

> **Source:** `$XILINX_VITIS/cli/api_docs/build/html/vitis.html` (the official
> Vitis Python API reference). The signatures below are verified verbatim
> against it.
>
> **Full API surface — introspect for the long tail.** This file documents the
> common application operations, not every method. The app object is
> `vitis.component.HostComponent`, which inherits from **`Component`** (base) and
> **`BuildSettings`** (mixin) — so its real surface is the union of all three
> classes. For anything not listed here, introspect the live object instead of
> guessing:
>
> ```python
> import inspect
> print([m for m in dir(app) if not m.startswith('_')])   # all callable methods
> print(inspect.signature(app.build)); print(inspect.getdoc(app.build))
> ```
>
> or open `vitis.html` → `vitis.component.HostComponent` /
> `vitis.component.Component` / `vitis.component.BuildSettings`.

## `create_app_component(...)`

```python
client.create_app_component(
    name,                    # required: app component name
    platform,                # required: .xpfm path (or platform name)
    domain=None,             # required when the platform has multiple domains
    cpu=None,                # used when no platform is provided
    os=None,                 # used when no platform is provided
    template=None,           # EMBD_APP template (e.g. 'hello_world')
    sysroot_toolchain=None,  # sysroot toolchain path for host component
    use_sysroot_toolchain=None,
)
```

Returns a `HostComponent` (application) object.

## Application object methods

The app object is a `HostComponent`. Its methods come from three classes:

**`HostComponent` (build + sysroot):**
- `app.build(target=None)` — compile. For embedded apps the default target is
  **`hw`**; returns build status (SUCCESS/FAILURE) and raises on hard failure.
- `app.clean(target=None)` — clean build outputs.
- `app.generate_build_files()` — generate the build files (e.g. CMake) without
  building; useful to inspect the configured build before compiling.
- `app.get_ld_script(path=None)` — get (or export to `path`) the linker script;
  relevant for baremetal/standalone memory layout.
- `app.set_sysroot(sysroot)` / `app.get_sysroot()` — set / read the Linux
  cross-compile sysroot.
- `app.update_sysroot_toolchain(sysroot_toolchain)` /
  `app.use_sysroot_toolchain(use_sysroot_toolchain)` — manage the sysroot toolchain.

**`Component` (base — files + report):**
- `app.import_files(from_loc, files=None, files_to_exclude=None, dest_dir_in_cmp=None, is_skip_copy_sources=False)`
  — import source files (whole dir if `files` omitted).
- `app.remove_files(files)` — remove files from the component.
- `app.report()` — print the component summary.

**`BuildSettings` (mixin — build config):**
- `app.set_app_config(key, values)` / `get_app_config(key=None)` /
  `append_app_config(key, values)` / `remove_app_config(key, values)` —
  build settings (compiler flags, includes, C++ standard).
- `app.get_config_info(key)` — describe a build-config key.

**Client-level (session) helpers:**
- `client.get_template('EMBD_APP', '<name>')` / `get_templates('EMBD_APP')` —
  resolve / list templates.
- `client.get_component(name)` / `list_components()` / `delete_component(name)` /
  `clone_component(name, new_name)` — manage existing components.
- `client.create_static_library_component(name, platform, domain=None, template=None)`
  / `create_dynamic_library_component(name, domain)` — create library components
  an app can link against.
- `client.export_projects(...)` / `import_projects(...)` — move components between
  workspaces.

## Embedded app templates (EMBD_APP)

Baremetal / standalone:
`hello_world`, `empty_application`, `dhrystone`, `memory_tests`,
`peripheral_tests`, `lwip_echo_server`, `lwip_tcp_perf_client`,
`lwip_tcp_perf_server`, `lwip_udp_perf_client`, `lwip_udp_perf_server`,
`libmetal_echo_demo`, `openamp_echo_test`, `openamp_matrix_multiply`,
`openamp_rpc_demo`, `srec_bootloader`, `srec_spi_bootloader`.

FreeRTOS:
`freertos_hello_world`, `freertos_lwip_echo_server`,
`freertos_lwip_tcp_perf_client/server`, `freertos_lwip_udp_perf_client/server`.

Linux:
`linux_hello_world`, `linux_empty_application`.

Firmware/boot (special): `zynq_fsbl`, `zynqmp_fsbl`, `zynqmp_pmufw`,
`versal_plm`, `versal_psmfw`, `zynq_dram_test`, `zynqmp_dram_test`.

## Common build-setting keys (`set_app_config`)

- `USER_COMPILE_WARNINGS_AS_ERRORS` (TRUE/FALSE)
- `USER_COMPILE_DEBUG_LEVEL` (e.g. `-g1`)
- `USER_INCLUDE_DIRECTORIES`
- `USER_CMAKE_CXX_STANDARD`

## Sysroot (Linux)

A Linux app cross-compiles against a sysroot. **Vitis `set_sysroot` requires a
full PetaLinux/Yocto sysroot containing a top-level `/lib`** (alongside `usr/lib`),
e.g. `$PLATFORM_REPO_PATHS/sw/versal/xilinx-versal/sysroots/cortexa72-cortexa53-amd-linux/`.

The GCC toolchain sysroot bundled with the install
(`$XILINX_VITIS/gnu/aarch64/lin/aarch64-linux/cortexa72-cortexa53-amd-linux`) only
has `usr/lib` (no top-level `/lib`) and is **rejected** by `set_sysroot`:
`'... does not contain a /lib'`. Use a real PetaLinux/Yocto sysroot.

## Worked example (from `cli/examples/embedded/platform_uc1.py`)

```python
platform_xpfm = client.find_platform_in_repos('platform_test')
app = client.create_app_component(name='app1', platform=platform_xpfm, domain='linux_a53')
app.set_sysroot(sysroot_dir)
app.import_files(from_loc=src_dir, files=['helloworld.c'], dest_dir_in_cmp='src')
app.build()      # target defaults to hw for embedded apps
```

## Expected output

A compiled `.elf` under the component build tree, e.g.
`<workspace>/<app_name>/build/<app_name>.elf` (exact path varies by release;
the driver locates the largest `*.elf` under the component).
