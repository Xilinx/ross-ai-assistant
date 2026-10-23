<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Platform API Reference

Ground-truth details for `client.create_platform_component()` and the helpers the
`vitis-embedded` skill relies on (Vitis Unified, 2026.2).

> **Source:** `$XILINX_VITIS/cli/api_docs/build/html/vitis.html` (the official
> Vitis Python API reference, `vitis.html#vitis.platform_component.Platform`).
> The signatures below are verified verbatim against it.
>
> **Full API surface — introspect for the long tail.** This file documents the
> common platform operations, not every method. For anything not listed here,
> introspect the live objects instead of guessing:
>
> ```python
> import inspect
> print([m for m in dir(platform) if not m.startswith('_')])   # or dir(client)
> print(inspect.signature(platform.build)); print(inspect.getdoc(platform.build))
> ```
>
> or open `vitis.html` → `vitis.platform_component.Platform` / the `Embedded`
> client class.

## `create_platform_component(...)`

Full signature:

```python
client.create_platform_component(
    name,                 # required: platform component name
    hw_design='',         # XSA path OR board shortcut (vck190, zcu102, ...)
    desc=None,
    os=None,              # 'standalone' (default domain), 'linux', 'freertos'
    cpu=None,             # e.g. psv_cortexa72_0, psu_cortexa53, ps7_cortexa9_0
    domain_name=None,
    template=None,        # baremetal domain template ("Empty" default)
    no_boot_bsp=False,    # build without boot components
    fsbl_target=None,     # ZU+ only, e.g. psu_cortexa53_0
    fsbl_path=None,       # custom FSBL (with no_boot_bsp)
    pmufw_Elf=None,       # prebuilt PMUFW/FSBL elf
    emu_design='',        # emulation XSA path
    platform_xpfm_path='',# wrap an existing .xpfm
    is_pmufw_req=False,
    generate_dtb=True,    # device tree blob; required for Linux
    advanced_options={},  # from create_advanced_options_dict(...)
    architecture=None,    # '64-bit' / '32-bit'
    compiler=None,
    rp_info_args=[],      # DFX reconfigurable-partition info
    hw_boot_bin=None,
)
```

Returns a platform object. Required: `name` plus one of `hw_design`,
`emu_design`, or `platform_xpfm_path`.

## Platform object methods

- `platform.report()` — print platform summary.
- `platform.build()` — generate the platform (produces the `.xpfm`).
- `platform.clean()` — clean build outputs.
- `platform.add_domain(cpu, os='standalone', name=None, display_name=None, sd_dir=None, support_app=None, generate_dtb=True, dt_overlay=False, architecture=None, compiler=None, hw_boot_bin=None)`
  — add another domain after creation.
- `platform.get_domain(name)`, `platform.list_domains()`, `platform.delete_domain(name)`.
- `platform.generate_boot_bsp(target_processor=None)` / `platform.remove_boot_bsp(fsbl_path=None, pmufw_elf=None)`
  — manage the boot BSP / boot components.
- `platform.set_fsbl_elf(path)` / `platform.set_pmufw_elf(path)` /
  `platform.retarget_fsbl(target_processor=None, domain_name=None)` — customize boot binaries.
- `platform.update_hw(hw_design=None, emu_design=None)` / `platform.update_desc(desc)`
  — update the hardware design / description.
- `platform.import_files(from_loc, files=None, dest_dir_in_cmp=None)` / `platform.remove_files(files)`
  — manage files in the component.
- `domain.generate_bif()` — generate a BIF for the domain's boot.

## Domain object methods (post-create configuration)

`create_platform_component(...)` has no parameter for BSP libraries or OS/processor settings and
creates a single domain, so those are configured **post-create** on the domain object
(`domain = platform.get_domain("<name>")`), **before `platform.build()`** — changes take effect
when the BSP/platform is (re)generated. Signatures below are **verified on the 2026.1 release**.
Always **discover valid names first** and pass them verbatim.

Libraries (BSP):
- `domain.get_applicable_libs()` → `[{name, description, paths}]` — libraries you may add (e.g.
  `xilffs`, `lwip220`, `libmetal`, `xiltimer`). Use it to pick valid names before `set_lib`.
- `domain.get_libs()` → `[{name, current_path}]` — libraries currently in the domain.
- `domain.set_lib(lib_name, path=None)` — add a BSP library. `domain.remove_lib(lib_name)` — remove one.

OS / processor / library settings:
- `domain.list_params(option, lib_name=None)` — `option` is `"os"`, `"proc"`, or `"lib"` (`lib`
  needs `lib_name`) → `[{parameter_name, description, default_value, value, possible_options, datatype, permission}]`.
- `domain.get_config(option, param, lib_name=None)` / `domain.set_config(option, param, value, lib_name=None)`
  — read/set one parameter by its **full** `parameter_name` (standalone OS params are prefixed
  `standalone_*`, e.g. `standalone_stdout`; processor params `proc_*`, many `readonly`) — **not** a
  short alias like `stdout`. Resolve the exact name via `list_params("os")` first.
- `domain.get_os()` → current OS details; `domain.get_drivers()` → IP/driver bindings.

Linux device tree:
- `domain.set_dtb(path)` — replace the DTB with a custom one; `domain.recompile_dtb()` — rebuild the
  DTB after DTS/overlay changes.

> After any `set_lib`/`remove_lib`/`set_config`, **rebuild the platform** (`platform.build()`); apps
> built against it and the boot image must be rebuilt too (the BIF itself is unchanged).
> There is **no** `get_lib_info` method — use `get_applicable_libs()` for library descriptions.

## Client helpers

- `client.set_workspace(path=...)` — create/select the workspace.
- `client.add_platform_repos(<dir>)` — register a platform repository
  (commonly `$PLATFORM_REPO_PATHS`).
- `client.find_platform_in_repos(<name_or_regex>)` — return the first matching
  `.xpfm` path (use after `build()`); raises if none found.
- `client.get_processor_os_list(xsa=<path>|platform=<name>)` — enumerate
  processor/OS options for auto-detecting `cpu`.
- `client.create_advanced_options_dict(sdt_repo=None, board_dtsi=None, user_dtsi=None, dt_overlay=None, dt_zocl=None, custom_dtsi=None)`
  — build the `advanced_options` dict.
- `client.get_component(name=...)`, `client.list_platform_components()`,
  `client.delete_component(name)`.
- `vitis.create_client(port=None, host='localhost', workspace=None)` / `vitis.dispose()`
  — session lifecycle. Omit `port` to **spawn** a headless server; pass `port=` (with
  `host='localhost'`) to **attach** to a running IDE's Vitis server so components land
  in the IDE's open workspace. Optional `workspace=` selects the workspace at connect
  time. Call `vitis.dispose()` only for a spawned session — never dispose an attached
  IDE session.
- `client.get_workspace()` — the workspace path the (attached) session currently has open.

## Worked examples (from `$XILINX_VITIS/cli/examples/embedded`)

Standalone (Versal board shortcut):

```python
platform = client.create_platform_component(
    name='platform_test', hw_design='vck190', os='standalone',
    cpu='psv_cortexa72_0', domain_name='standalone_psv_cortexa72_0')
platform.report(); platform.build()
xpfm = client.find_platform_in_repos('platform_test')
```

Linux with device tree:

```python
platform = client.create_platform_component(
    name='platform_test', hw_design='vck190', os='linux',
    cpu='psv_cortexa72', domain_name='linux_psv_cortexa72', generate_dtb=True)
```

ZynqMP Linux from a repo XSA, then add a domain and build:

```python
client.add_platform_repos(os.environ['PLATFORM_REPO_PATHS'])
platform = client.create_platform_component(
    name='platform_vck', hw_design='vck190', no_boot_bsp=True,
    generate_dtb=True, architecture='64-bit', desc='base vck190 platform')
platform = client.get_component(name='platform_vck')
platform.add_domain(cpu='psv_cortexa72', os='linux', name='a72_domain', dt_overlay=True)
platform.build()
```

## Device family cheat-sheet

| CPU prefix | Family | Boot components |
|------------|--------|-----------------|
| `psv_`, `psx_` | Versal | PLM (no separate FSBL) |
| `psu_` | ZynqMP | FSBL + PMUFW |
| `ps7_` | Zynq-7000 | FSBL |
| `microblaze` | MicroBlaze | bootloader varies |

## Expected workspace layout after build

```
<workspace>/
└── <platform_name>/
    └── export/<platform_name>/<platform_name>.xpfm
```
