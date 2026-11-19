<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSCT Tcl to Vitis Unified Python (2026.2)

Translate an XSCT project script into a Python script run headless with `vitis -s <script>.py`.
Signatures below were checked against `$XILINX_VITIS/cli/vitis/*.py` (2026.2). Anything marked
**UNCONFIRMED** was not verified there: emit it with a `# UNCONFIRMED: check against your Vitis release` comment and tell the user.

## Workflow

1. Read the whole Tcl. Sort each line into one of: project commands (tables below), debug/run commands
   (XSDB section), `hsi::` queries, or plain Tcl (variables, `file`, `exec`) that maps to Python stdlib, but translate
   `exec` only for bootgen and get the user's confirmation before running every side-effecting line (SKILL.md Step 2B).
2. Track state XSCT keeps implicitly: the active platform (`platform active`), the active domain
   (`domain active`), and the workspace. In Python these become explicit objects.
3. Count the embedded apps and apply the system project rule. Report any accelerated components as UNSUPPORTED.
4. Emit the skeleton, then translate the lines in order. Relative XSA, workspace and source arguments keep the base the Tcl
   used: the current working directory for ordinary relative arguments (as Tcl `file normalize` does), and the script's
   directory only where the Tcl derives the path from `[info script]`.

## Script skeleton (mandatory)

```python
import os, sys, subprocess
import vitis

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(HERE, "ws")                      # XSCT: setws ./ws
if os.path.isdir(WS) and os.listdir(WS):           # never rmtree / delete_workspace a user workspace
    sys.exit(f"ERROR: workspace '{WS}' exists and is not empty; choose an empty or new directory")

client = vitis.create_client()
try:
    client.set_workspace(path=WS)
    # ... translated commands ...
finally:
    vitis.dispose()
```

- Do not emit `shutil.rmtree` or `client.delete_workspace()`. Refuse a workspace that exists and is not empty, as shown above.
- `component.build()` (app/lib) returns an int exit code (0 = success). Check it: `if app.build() != 0: sys.exit(1)`.
- `platform.build()` returns a stream status, not 0/1. Confirm success by checking that the `.xpfm` exists.

## Workspace, platform, domain

| XSCT | Unified Python |
|---|---|
| `setws <p>` | `client.set_workspace(path=<p>)` (behind the non-empty guard) |
| `getws` | `client.get_workspace()` |
| `platform create -name N -hw X.xsa -proc C -os O` | `plat = client.create_platform_component(name=N, hw_design=X, cpu=C, os=O, domain_name=D)`. Always pass `domain_name` |
| `... -desc S` | `desc=S` |
| `... -arch 32-bit\|64-bit` | `architecture='32-bit'\|'64-bit'` (32-bit allowed only on A53 baremetal) |
| `... -fsbl-target psu_cortexr5_0` | `fsbl_target='psu_cortexr5_0'` (ZU+ only; default `psu_cortexa53_0`) |
| `... -no-boot-bsp` | `no_boot_bsp=True` (with `fsbl_path=`, `pmufw_Elf=` for prebuilt ELFs) |
| `... -xpfm P.xpfm` | `platform_xpfm_path=P` (create from an existing platform) |
| `... -hw_emu E.xsa` | `emu_design=E` |
| `... -out <dir>` | No equivalent. The component is always created at `<ws>/<N>`. Drop the option and note it. UNCONFIRMED: whether any override exists |
| `platform active N` | `plat = client.get_component(name=N)` |
| `platform generate` (also `-domains`) | `plat.build()` (builds every domain) |
| `platform clean` / `platform remove N` | `plat.clean()` / `client.delete_component(name=N)` |
| `platform config -updatehw X` | `plat.update_hw(hw_design=X)` |
| `platform config -desc S` | `plat.update_desc(S)` |
| `platform config -create-boot-bsp` / `-remove-boot-bsp` | `plat.generate_boot_bsp()` / `plat.remove_boot_bsp()` |
| `platform config -fsbl-elf F` / `-pmufw-elf F` | `plat.set_fsbl_elf(F)` / `plat.set_pmufw_elf(F)` |
| `platform list` | `client.list_platform_components()` |
| `domain create -name D -os O -proc C` | `dom = plat.add_domain(name=D, os=O, cpu=C)` (returns a Domain object) |
| `... -support-app <app>` / `-arch` / `-display-name` | `support_app='<template id>'` / `architecture=` / `display_name=` |
| `... -sd-dir P` (Linux) | `sd_dir=P` |
| `domain active D` | `dom = plat.get_domain(name=D)` |
| `domain remove D` | `plat.delete_domain(D)` |
| `domain config -qemu-args F` | `dom.set_qemu_args(qemu_option='PS', path=F)` |
| `domain config -pmuqemu-args F` | `dom.set_qemu_args(qemu_option='PMU', path=F)` (use `'PMC'` on Versal) |
| `domain config -qemu-data DIR` | `dom.set_qemu_data(path=DIR)` |
| `domain config -bif F` / `-generate-bif` | `dom.set_bif(path=F)` / `dom.generate_bif()` (Linux) |
| `domain config -boot DIR` | `dom.set_boot_dir(path=DIR)` |
| `domain config -image DIR` / `-sd-dir DIR` | `dom.set_sd_dir(path=DIR)`. UNCONFIRMED: Unified has a single SD/image directory, so check whether Classic's distinction between `-image` and `-sd-dir` still matters |
| `domain config -dtb F` | `dom.set_dtb(path=F)` |

Paths passed to `set_*` must exist when the script runs, otherwise the call raises an error.

### CPU instance names

- **Standalone and FreeRTOS domains** take a core instance with a `_N` suffix, for example `psu_cortexa53_0`, `psu_cortexr5_0`,
  `ps7_cortexa9_0`, `psv_cortexa72_0`, `microblaze_0`, and on Versal Gen 2 `cortexa78_0` (seen in the 2026.2 examples).
  If the XSCT script passes a bare cluster name such as `-proc psu_cortexa53` for a standalone domain, append `_0`.
- **Linux domains** take the cluster name without a suffix: `psu_cortexa53`, `psv_cortexa72`, `cortexa78`.
- Unified uses System Device Tree names. The authoritative list comes from `client.get_processor_os_list(xsa='<xsa>')`; call it
  whenever you are unsure. UNCONFIRMED: the exact SDT names for every Versal Gen 2 and SpartanUP part (for example a `psx_` prefix or not).
- Classic default domain names (created implicitly by `platform create -proc -os`) are UNCONFIRMED. If later lines refer to
  that domain by name, pass the same name as `domain_name`.

## BSP (`bsp ...` acts on the active domain, which becomes `dom`)

BSP settings exist only on standalone and FreeRTOS domains (the embeddedsw `create_bsp.py` handles only those two OSes).
On a Linux domain, drop `bsp setlib`/`bsp config` and tell the user: Linux libraries come from the sysroot or the PetaLinux/Yocto build.

XSCT `bsp config <param> <val>` works out the scope (OS, processor, or library) by itself. Unified needs `option=`, plus the
System Device Tree parameter name:

| Classic scope | `option=` | Unified param name rule | Example (verified) |
|---|---|---|---|
| standalone OS | `'os'` | `standalone_<classic>` | `stdin` becomes `standalone_stdin`, also `standalone_stdout`, `standalone_hypervisor_guest`, `standalone_enable_minimal_xlat_tbl` |
| FreeRTOS OS | `'os'` | `freertos_<classic>` | `freertos_stdin`, `freertos_tick_rate`, `freertos_total_heap_size` |
| processor | `'proc'` | `proc_<classic>` | `extra_compiler_flags` becomes `proc_extra_compiler_flags`, also `proc_compiler_flags`, `proc_archiver` |
| library | `'lib'` + `lib_name=` | `<LIBPREFIX>_<classic>`, where the prefix depends on the library | `XILFFS_use_lfn`, `XILTIMER_tick_timer`, `lwip220_<param>` |

- Values that name peripherals are SDT instance names, for example `psu_uart_1` on ZynqMP (from the 2026.2 example) or
  `uart0` (from the set_config docstring). UNCONFIRMED: whether every Classic HSI instance name survives unchanged in SDT. Check `possible_options`.
- Classic params whose name does not follow the rules above are UNCONFIRMED. In generated scripts, resolve them at run time
  with `dom.list_params('os'|'proc')` or `dom.list_params('lib', lib_name=L)` and read the `parameter_name` / `possible_options` keys.
- Boolean and enum value spellings (`true`, `1`, `ON`) are UNCONFIRMED. Copy the form that `list_params` reports.

| XSCT | Unified Python |
|---|---|
| `bsp config stdin psu_uart_1` | `dom.set_config(option='os', param='standalone_stdin', value='psu_uart_1')` |
| `bsp config extra_compiler_flags "-g -O0"` | `dom.set_config(option='proc', param='proc_extra_compiler_flags', value='-g -O0')` |
| `bsp config -append extra_compiler_flags -pg` | `cur = dom.get_config(option='proc', param='proc_extra_compiler_flags')['value']` then `dom.set_config(option='proc', param='proc_extra_compiler_flags', value=f'{cur} -pg'.strip())` |
| `bsp config use_lfn 1` (library param) | `dom.set_config(option='lib', param='XILFFS_use_lfn', value='1', lib_name='xilffs')` |
| `bsp setlib -name L [-ver V]` | `dom.set_lib(lib_name=L)`, or `dom.set_lib(lib_name=L, path=<lib dir>)` to pin a version |
| `bsp removelib -name L` | `dom.remove_lib(lib_name=L)` |
| `bsp setdriver -ip I -driver Dr -ver V` | No `set_driver` API in 2026.2 (it is commented out in domain.py). Closest: `dom.update_path(option='DRIVER', name=Dr, new_path=<driver dir>)`. UNCONFIRMED: equivalence. Swapping a driver for a different one is not possible, so flag it to the user |
| `bsp setosversion -ver V` | No `set_os_version` API (commented out). Closest: `dom.update_path(option='OS', name='standalone', new_path=<.../standalone_vX_Y>)`; `dom.get_os()['possibleOptions']` lists the paths. UNCONFIRMED |
| `bsp regenerate` | `dom.regenerate()` |
| `bsp listparams -os\|-proc\|-lib L` | `dom.list_params('os'\|'proc')` / `dom.list_params('lib', lib_name=L)` |
| `bsp getlibs` / `getdrivers` / `getos` | `dom.get_libs()` / `dom.get_drivers()` / `dom.get_os()` |
| `repo -libs` | `dom.get_applicable_libs()` |

Library renames: Classic `lwip211`/`lwip213` become `lwip220` (the only lwIP shipped in 2026.2). For any other library name,
check with `dom.get_applicable_libs()`. BSP changes take effect only after `plat.build()`.

## Applications, libraries, sources

| XSCT | Unified Python |
|---|---|
| `app create -name A -platform P -domain D -template "T"` | `app = client.create_app_component(name=A, platform=<xpfm>, domain=D, template='<id>')` |
| `app create ... -sysproj S` | Drop `-sysproj`; there is no such parameter. See the system project rule |
| `app create -name A -proc C -os O` (no platform) | Baremetal (standalone/FreeRTOS) apps need the platform `.xpfm` and domain (`platform=None` is rejected: uses the Embedded Accel flow, which is not supported for the baremetal template). For `app create -hw X`, create the platform first (`create_platform_component` + `plat.build()`) and pass its xpfm |
| `importsources -name A -path DIR` | `app.import_files(from_loc=DIR, dest_dir_in_cmp='src')`. Leaving out `files` imports the whole folder. Always pass `dest_dir_in_cmp='src'`, because the default is the component root |
| `importsources ... -soft-link` | add `is_skip_copy_sources=True` (application components only) |
| `importsources ... -target-path T` | `dest_dir_in_cmp=T` |
| `app build -name A` / `app clean -name A` | `client.get_component(name=A).build()` / `.clean()` |
| `app remove A` / `app list` | `client.delete_component(name=A)` / `client.list_components()` (returns dicts with `name`, `location`, `type`) |
| `library create -name L -type static -platform P -domain D` | `lib = client.create_static_library_component(name=L, platform=<xpfm>, domain=D)` |
| `library create -name L -type shared ...` (Linux) | `client.create_dynamic_library_component(name=L, domain=D)` |
| `importprojects <dir>` | No equivalent. `client.import_projects(src=)` accepts only a **Unified export zip**. Recreate the component and `import_files` its `src/`, or use this skill's Classic-workspace path |
| `projects -build` | Build in this order: `plat.build()` for each platform, then each library and app, then `client.get_sys_project(S).build()` (embedded only; HLS/AIE are not supported) |
| `projects -clean` | `.clean()` on each component or system project |
| `sysproj build -name S` | `client.get_sys_project(name=S).build()` (`target='hw'` by default for embedded) |
| `sysproj list` / `sysproj remove S` | `client.list_sys_projects()` / `client.delete_sys_project(name=S)` |
| `repo -set P` | `client.set_embedded_sw_repo(level='LOCAL', path=P)` (`''` resets it) |
| `repo -add P` | `client.add_embedded_sw_repo(level='LOCAL', path=P)` |
| `repo -get` / `repo -scan` | `client.get_embedded_sw_repo()` / `client.rescan_embedded_sw_repo()` |
| `repo -apps` | `client.get_templates(type='EMBD_APP')` |
| `exec bootgen -arch A -image F.bif -o BOOT.BIN -w` | `subprocess.run(['bootgen', '-arch', A, '-image', F, '-o', 'BOOT.BIN', '-w'], check=True)`, or `app.create_boot_image(arch=A, bif_path=F, output_image_path=...)` |

### `app config -name A <op> <key> <val>`

Keys come from `$XILINX_VITIS/vitis-server/scripts/cmake/user_cmake.json` and are case-sensitive, so use upper case.
`-set` maps to `app.set_app_config(key, values)`, which replaces the value. `-add` maps to `app.append_app_config(key, values)`, which works only on
multi-value keys and raises an error otherwise. `-remove` maps to `app.remove_app_config`, also multi-value only. `-get` maps to `app.get_app_config(key)`, and `-info` to `app.get_config_info(key)`.

| Classic key | Unified key | Multi-value |
|---|---|---|
| `define-compiler-symbols` | `USER_COMPILE_DEFINITIONS` (`FOO=1` becomes `-DFOO=1`) | yes |
| `undef-compiler-symbols` | `USER_UNDEFINED_SYMBOLS` where `user_cmake.json` lists it (2027.1+); on 2026.2 builds so far `set_app_config` ignores the listed `USER_UNDEFINE_SYMBOLS`, so append `-U<symbol>` to `USER_COMPILE_OTHER_FLAGS` | yes |
| `include-path` | `USER_INCLUDE_DIRECTORIES` (relative paths resolve against the folder holding `UserConfig.cmake` (`<app>/src` for standalone/FreeRTOS apps); use `../<dir>` or an absolute path) | yes |
| `library-search-path` | `USER_LINK_DIRECTORIES` | yes |
| `libraries` | `USER_LINK_LIBRARIES` (bare names such as `m`, without `-l`) | yes |
| `compiler-misc` | `USER_COMPILE_OTHER_FLAGS` | no |
| `compiler-optimization` | `USER_COMPILE_OPTIMIZATION_LEVEL`, taking one of `-O0 -O1 -O2 -O3 -Os` (strip Classic labels like `Optimize more (-O2)` down to the flag) | no |
| `linker-misc` | `USER_LINK_OTHER_FLAGS` | no |
| `linker-script` | `USER_LINKER_SCRIPT` (file path). To inspect the default script, use `app.get_ld_script()` | no |
| `build-config debug\|release` | No build-config concept in the CLI. UNCONFIRMED approximation: release becomes `USER_COMPILE_OPTIMIZATION_LEVEL='-O2'` plus `USER_COMPILE_DEBUG_LEVEL=''`; debug becomes `'-O0'` plus `'-g'` | no |

For `-add` on a single-value key, read the current value, join it with the new one, and set the result. `get_app_config`
returns a protobuf repeated container whose single value comes back quoted, so `-add` means read, strip quotes, append, set:
`app.set_app_config('USER_COMPILE_OTHER_FLAGS', ' '.join([v.strip('"') for v in app.get_app_config('USER_COMPILE_OTHER_FLAGS')] + ['-pg']))`.

## Template names (`-template "Title Case"` becomes an id)

Rule: the template id is the folder name under `$XILINX_VITIS/data/embeddedsw/lib/sw_apps/`. For most templates that is the
display name lower-cased with spaces replaced by underscores. List the ids at run time with `client.get_templates(type='EMBD_APP')`.
Linux ids are hard-coded in the server (`EmbeddedTemplateProvider`).

| Classic display name | id | | Classic display name | id |
|---|---|---|---|---|
| Hello World | `hello_world` | | Zynq MP FSBL | `zynqmp_fsbl` |
| Empty Application / Empty Application(C) | `empty_application` | | Zynq FSBL | `zynq_fsbl` |
| Memory Tests | `memory_tests` | | ZynqMP PMU Firmware | `zynqmp_pmufw` |
| Peripheral Tests | `peripheral_tests` | | Zynq MP DRAM tests | `zynqmp_dram_test` |
| Dhrystone | `dhrystone` | | Zynq DRAM tests | `zynq_dram_test` |
| FreeRTOS Hello World | `freertos_hello_world` | | versal PLM | `versal_plm` |
| lwIP Echo Server | `lwip_echo_server` | | versal PSM Firmware | `versal_psmfw` |
| FreeRTOS lwIP Echo Server | `freertos_lwip_echo_server` | | Image Recovery | `img_rcvry` |
| lwIP TCP/UDP Perf Client/Server | `lwip_{tcp,udp}_perf_{client,server}` | | DDR self refresh | `ddr_self_refresh` |
| FreeRTOS lwIP TCP/UDP Perf ... | `freertos_lwip_{tcp,udp}_perf_{client,server}` | | RSA Authentication App | `rsa_auth_app` |
| SREC Bootloader / SREC SPI Bootloader | `srec_bootloader` / `srec_spi_bootloader` | | Libmetal AMP Demo | `libmetal_echo_demo` |
| OpenAMP echo-test | `openamp_echo_test` | | OpenAMP matrix multiplication Demo | `openamp_matrix_multiply` |
| OpenAMP RPC Demo | `openamp_rpc_demo` | | Linux Hello World | `linux_hello_world` |
| Linux Empty Application | `linux_empty_application` (the on-disk folder is `linux_empty_app`; the server id is `linux_empty_application`) | | Empty Application (C++) | UNCONFIRMED: no C++ template folder; use `empty_application` and import `.cpp` sources |

If the imported sources define their own `main()`, use `empty_application` rather than `hello_world` to avoid a duplicate `main`.

## Platform reference and system project rule

- A built platform is exported to `<ws>/<plat>/export/<plat>/<plat>.xpfm`. Pass that path as `platform=`. Passing the `plat`
  object works for `create_app_component` and `create_static_library_component`; `create_sys_project` needs the xpfm path.
- XSCT always creates a `-sysproj`; Unified makes it **optional**. Decision rule (embedded only):
  - One embedded app: no system project.
  - Several apps that XSCT grouped in one `-sysproj`, or a boot image or package built across them: create one.
- `sys = client.create_sys_project(name=S, platform=<xpfm>)` for embedded (no `template`: `empty_application` is an app
  template and the call fails with `NOT_FOUND`). Then call `sys.add_component(name=A)` for each app. Do not call
  `client.get_template('app', ...)`: `'app'` is not a valid template type.

### Accelerated XSCT commands (not supported)

Acceleration flows (`v++`, managed HW kernels, AIE components, `empty_accelerated_application` system projects, host apps
with the acceleration natures, and similar) are not supported by this skill. Report them as UNSUPPORTED and keep the
embedded command rows above. `exec bootgen` remains allowed.

## Debug and run commands go to XSDB, not XSCT

`connect`, `targets`, `fpga`, `dow`, `con`, `stop`, `stp`, `rst`, `mrd`, `mwr`, `rrd`, `rwr`, `bpadd`, `jtag ...`,
`source psu_init.tcl; psu_init`, `loadhw`, `program_flash` are not project commands and are not translated into the Vitis
Python API. Move them **unchanged** into an XSDB Tcl script (`$XILINX_VITIS/bin/xsdb run.tcl`). Alternatively, use the Python
`xsdb` package (`$XILINX_VITIS/cli/xsdb`): `import xsdb; s = xsdb.start_debug_session(); s.connect(...)`, where the session
and target methods mirror the Tcl names (`connect`, `targets`, `fpga`, `dow`, `con`, `stop`, `mrd`, `rst`). UNCONFIRMED:
the exact target-selection idiom, and whether a `source` equivalent exists in Python; keep `psu_init` in Tcl if unsure.
XSCT is not needed for any of this.

## `hsi::` commands

XSCT is disabled in Unified releases (`bin/xsct` prints `XSCT is disabled` and runs nothing), so no XSCT Tcl step may
remain. Translate `hsi::` queries to the Python `hsi` package available in `vitis -s`:
`from hsi import HwManager; design = HwManager.open_hw_design('<xsa>'); cells = design.get_cells(hierarchical='true')`.
Properties are attributes (`c.VLNV`, not `get_property`). `client.get_processor_os_list(xsa=...)` covers processor and OS
discovery. Fallback: `$XILINX_VITIS/bin/sdtgen` and read the `.dts`.

## Worked example: ZynqMP standalone

```tcl
setws ./ws
platform create -name zcu102_plat -hw ./design_1_wrapper.xsa -proc psu_cortexa53_0 -os standalone
domain active standalone_domain
bsp setlib -name xilffs
bsp config -append extra_compiler_flags "-Wall"
platform generate
app create -name hello -platform zcu102_plat -domain standalone_domain -template "Hello World"
app config -name hello define-compiler-symbols EXAMPLE_SYMBOL
importsources -name hello -path ./src
app build -name hello
```

```python
import os, sys
import vitis

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(HERE, "ws")
if os.path.isdir(WS) and os.listdir(WS):
    sys.exit(f"ERROR: workspace '{WS}' exists and is not empty; choose an empty or new directory")

PLAT, DOM, APP = "zcu102_plat", "standalone_domain", "hello"
client = vitis.create_client()
try:
    client.set_workspace(path=WS)                                        # setws
    plat = client.create_platform_component(                             # platform create
        name=PLAT, hw_design=os.path.join(HERE, "design_1_wrapper.xsa"),
        cpu="psu_cortexa53_0", os="standalone", domain_name=DOM)
    dom = plat.get_domain(name=DOM)                                      # domain active
    dom.set_lib(lib_name="xilffs")                                       # bsp setlib (standalone only)
    cur = dom.get_config(option="proc", param="proc_extra_compiler_flags")["value"]
    dom.set_config(option="proc", param="proc_extra_compiler_flags",     # bsp config -append
                   value=f"{cur} -Wall".strip())
    plat.build()                                                         # platform generate
    xpfm = os.path.join(WS, PLAT, "export", PLAT, f"{PLAT}.xpfm")
    if not os.path.isfile(xpfm):
        sys.exit(f"ERROR: platform build did not produce {xpfm}")
    app = client.create_app_component(name=APP, platform=xpfm,           # app create (no -sysproj:
                                      domain=DOM, template="hello_world")  # single app, no system project)
    app.append_app_config(key="USER_COMPILE_DEFINITIONS",                # app config define-compiler-symbols
                          values=["EXAMPLE_SYMBOL"])
    app.import_files(from_loc=os.path.join(HERE, "src"), dest_dir_in_cmp="src")  # importsources
    if app.build() != 0:                                                 # app build
        sys.exit("ERROR: app build failed")
finally:
    vitis.dispose()
```

Run it with `source $XILINX_VITIS/settings64.sh && vitis -s convert_zcu102.py`. If `./src` contains its own `main()`,
switch to `template="empty_application"`.
