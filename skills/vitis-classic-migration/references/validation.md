<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Validation: build, debug readiness, report

Contents:

- [1. Build](#1-build)
- [2. Configuration and source verification (always): `scripts/verify_migration.py`](#2-configuration-and-source-verification-always-scriptsverify_migrationpy)
- [3. Debug readiness (always): `scripts/check_launch.py`](#3-debug-readiness-always-scriptscheck_launchpy)
- [4. Optional, not required: running on a board or QEMU](#4-optional-not-required-running-on-a-board-or-qemu)
- [5. Known limitations to report (UG1400 and known issues)](#5-known-limitations-to-report-ug1400-and-known-issues)
- [6. Report template](#6-report-template)

Migration only counts as successful after the target workspace has been checked as below and the evidence recorded.

## 1. Build

`migrate.py --build` builds every application and system project after creating them. To build by hand:

```python
plat = client.get_component(name="<plat>"); plat.build()            # returns stream status; check the .xpfm exists
rc = client.get_component(name="<app>").build()                      # int exit code, 0 = success
client.get_sys_project(name="<sys>").build()                         # embedded default target 'hw'
```

Check the result on disk, never from the return value alone:

| Check | Where |
|---|---|
| Platform exported | `<ws>/<plat>/export/<plat>/<plat>.xpfm` |
| App ELF | `<ws>/<app>/build/<app>.elf` |
| Classic build settings carried over | `<ws>/<app>/src/UserConfig.cmake` (`USER_COMPILE_DEFINITIONS`, levels, flags) |
| BSP libraries and parameters | `<ws>/<plat>/vitis-comp.json` and `<plat>/.../bsp` settings |

Known 2026.2 behaviour:
- `app.build()` can return a success value after CMake/ninja failed (seen with a `$` in `USER_COMPILE_OTHER_FLAGS`:
  `ninja: error: ... bad $-escape`). `migrate.py` therefore fails the step unless `<app>/build/<app>.elf` exists.
- Classic make-only flags (`-c`, `-MT"$@"`, `-MMD`, `-MP`, `-MF`) must not be carried into Unified flags; the generator drops them.
- Creating a **Versal** app headless pauses for about 3.5 minutes. The server waits up to 180 s for an XSDB server
  that cannot start without the GUI, then continues. This is expected; do not kill the run.
- Classic profiling builds (`-pg` in the BSP `extra_compiler_flags`) fail to link (`undefined reference to _mcount`)
  in Classic as well as Unified unless the app provides `_mcount`. Report it as a pre-existing issue.

## 2. Configuration and source verification (always): `scripts/verify_migration.py`

```bash
vitis -s scripts/verify_migration.py <classic_ws> <unified_ws> --classic-embeddedsw <dir> [--config <same as Step 2A>]
# PASS: 24 passed, 0 failed, 0 warnings, 0 skipped; report: <unified_ws>/verification_report.md
```

It re-reads the Classic workspace and compares each item with the Unified result. A Classic BSP parameter with no Unified equivalent is a SKIP row, not a failure (`migrate.py` reports it as UNSUPPORTED). Expected app settings and BSP parameter
names come from the generator's mapping. PASS does not cover system projects, Linux and boot domains, or library projects:

| Area | Classic source | Unified evidence | Rule |
|---|---|---|---|
| Platform / domain | `platform.spr` | `<plat>/vitis-comp.json`, `<plat>/<cpu>/<domain>/bsp/bsp.yaml` | exists |
| BSP library | `addedLibs` | `bsp.yaml` `lib_info` | present |
| BSP parameter | `libOptions` (user-set values) | `bsp.yaml` `os_config` / `proc_config` / `lib_config` `value` | exactly equal |
| App setting | mapped `.cproject` options | `<app>/src/UserConfig.cmake` | equal (lists: all Classic items present) |
| App source | every file in `<app>/src` | `<app>/src` | present and byte-identical |
| BSP source edit | diff vs original embeddedsw | built Unified BSP `libsrc/<comp>/src` | every edited line present; added files present |
| BSP component | `libsrc/<comp>_vX_Y/src` of each user domain | same domain's built `libsrc/<comp>` | WARN (non-fatal) if the component has no folder; `generic`, `cpu_*` and components absent from the reference are not reported |

Run it after `migrate.py --build`. A FAIL means a setting or file did not come across; the detail column shows both values. A WARN is non-fatal and does not change the exit code: a Classic BSP component is missing from the Unified BSP (typically a driver whose IP is not in the new BSP's design description), so headers from it will not be found; add the driver via a local software repository or regenerate the XSA.

**No Classic workspace (Step 2B XSCT script, Step 2C exported `migrate.py`).** `verify_migration.py` takes a Classic workspace, so it cannot
run on a script alone. If the user still has the original Classic workspace, pass it as `<classic_ws>`. If not, tell the user the
migration was checked against the script, not against Classic, and do this instead:
- Build and check the artifacts on disk (section 1).
- Read each item the script sets back from the new workspace, using the "Unified evidence" column above (`vitis-comp.json`, `bsp.yaml`,
  `<app>/src/UserConfig.cmake`), and compare it with the script: libraries, BSP parameters, app settings, imported sources.
- For Step 2C, also read `FAILED steps` and the per-setting table in `migrate.log` / `migration_notes.md`.
- Run `check_launch.py` (section 3); it works on any Unified workspace.
Report items you could not read back as unverified.

## 3. Debug readiness (always): `scripts/check_launch.py`

Unified writes `<comp>/_ide/launch.json` when an app component is created and refreshes its PDI, bitstream,
FSBL and psu_init references after a platform build. Run the checker with the Python bundled with Vitis:

```bash
vitis -s scripts/check_launch.py <unified_ws> [component ...]
# PASS: checked 1 component(s): testapp_a53       (exit 0)
# FAIL: hello: [hello_app_hw_1] targetSetup.downloadElf[0].elfFile -> missing ${workspaceFolder}/...
```

Run it after the build. Missing ELF means the app did not build; missing PDI/FSBL/psu_init means the platform did not build.

## 4. Optional, not required: running on a board or QEMU

Running the migrated app on hardware or QEMU is **not part of this release's migration checks**, and the agent should not
suggest it. Sections 1-3 are the acceptance criteria. If the user explicitly asks to run or debug, point them to the
Unified IDE: open the migrated workspace and start the app's generated launch configuration (`<app>/_ide/launch.json`,
for example `<app>_app_hw_1`), which programs the device, downloads the ELF and stops at `main`. The headless
equivalent is plain XSDB (`$XILINX_VITIS/bin/xsdb`, not XSCT); for a remote hw_server connect with
`connect -url TCP:<host>:3121 -symbols`.

## 5. Known limitations to report (UG1400 and known issues)

| Area | Limitation | What to tell the user |
|---|---|---|
| Platform | BSP source edits need `--classic-embeddedsw`, the `data/embeddedsw` of the Classic install that created the workspace (required when there are BSP sources; another release's tree is refused; if any component has more than 5 differing files, no edit is applied and all patches are kept in `bsp_edits/`); a change whose surrounding code changed in the new release is not applied | Apply the saved patch from `bsp_edits/` to the component in `sw_repo/` |
| Platform | Edits to files Classic regenerates (`*_g.c`, `xparameters.h`, `bspconfig.h`, `inbyte.c`/`outbyte.c`, `pm_cfg_obj.c`) are not migrated | They were overwritten in Classic too on regeneration; make the change in a BSP setting or a normal source |
| Platform | The local repository applies to the whole workspace; the same component edited differently in two domains keeps only the first | Merge the edits by hand |
| Platform | Classic Makefile edits are reported, not migrated (Unified builds with CMake) | Port them to the component's `src/CMakeLists.txt` |
| Platform | Boot-domain (FSBL/PMUFW) BSP settings are not migrated; the ones that differ from the Classic defaults (for example FSBL `stdin` = `psu_uart_1`) are listed as manual follow-ups | Re-apply them in the Unified boot domain |
| Scope | Accelerated projects and platforms from extensible XSAs are not supported | Recreate manually / export a fixed XSA and pass `--xsa` |
| Platform | Local or global SW repositories are not migrated | Migrate them to lopper, then `client.set_embedded_sw_repo(...)`. To use a newer or patched Lopper than the shipped one, `export VITIS_LOPPER_INSTALL_LOC=<path to Lopper>` before starting Vitis (UG1400 "External Lopper Support") |
| Platform | XSAs generated by Vivado older than 2023.2 can break the drivers (the generator warns, from the XSA's `generatedVersion`) | Regenerate the XSA, then `--xsa PLAT=<new.xsa>` or `platform.update_hw()` |
| Release | Only Classic 2023.2, 2024.1 and 2024.2 workspaces | The generator refuses others (release from the standalone BSP version, else `.metadata/version.ini`) |
| Platform | Classic QEMU args/data are not applied | Unified uses its own defaults |
| App | Boot-firmware apps (FSBL/PMUFW/PLM templates) are not migrated | Unified builds them in the platform's boot domain; port source edits there |
| App | Classic-generated lwIP template sources may not compile against lwip220 (seen on R5, A9 and FreeRTOS); the generator adds a follow-up | Port the app to the Unified lwIP template |
| Platform | Classic BSP parameters with no Unified equivalent (for example FreeRTOS `xil_interrupt`, `OS_NAME`) are not set | Reported as UNSUPPORTED in the run results |
| App | Platform outside the Classic workspace | Migrate that platform first; edit `platform=` in `migrate.py` |
| App | Device IDs in `xparameters.h` changed (SDT flow) | Port code per UG1647 |
| App | Only one build configuration (default `Debug`) | Choose another with `--config Release` |
| App | Debug configurations are not migrated | Unified creates `launch.json` automatically |
| Paths | Spaces in workspace, script or working-directory paths break `vitis -s <script>` and `vitis -w <workspace>` (and Classic make) | Use space-free paths |

## 6. Report template

```markdown
# Migration report: <classic workspace> -> <unified workspace>

## Result
<PASS | PARTIAL | FAIL> - <one sentence>

Verification PASS covers platforms, domains, BSP libraries and user-set parameters, mapped app settings, app sources, and
BSP source edits only. It does not cover system projects, Linux or boot domains, or library projects.

## Components
| Classic project | Type | Unified component | Built | Debug check |
|---|---|---|---|---|
| zcu102_platform | platform | zcu102_platform | yes (.xpfm) | - |
| testapp_a53 | app | testapp_a53 | yes (.elf) | verify PASS; check_launch PASS |

## Settings carried over
- BSP: libraries <...>, parameters <...>
- App: <key = value> (from UserConfig.cmake)

## Not migrated / manual follow-ups
- <from migration_notes.md and section 5>

## Evidence
- migrate.log, migration_notes.md, verification_report.md, check_launch output (paths)
```
