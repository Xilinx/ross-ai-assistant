#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classic Vitis IDE embedded workspace (2023.2, 2024.1, 2024.2) -> Vitis Unified 2026.2 migration script generator.

Stdlib only; needs neither Classic Vitis nor XSCT. Reads the Classic project files directly
(.project, platform.spr, <proj>.prj, <proj>.sprj, .cproject), mirroring the 2024.2 built-in
exporter (see references/classic-workspace.md), and writes into a NEW empty Unified workspace:

    <unified_ws>/migrate.py          run: cd <unified_ws> && vitis -s migrate.py [--build]
    <unified_ws>/migration_notes.md  what was found, mapped, and what needs manual follow-up

Usage (Python bundled with Vitis):
    vitis -s classic2unified.py <classic_ws> <unified_ws> --classic-embeddedsw <dir> [--config Debug] [--xsa PLAT=path.xsa]
The Classic workspace is only read, never modified. Accelerated projects are reported as not supported.
"""
import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

if sys.version_info < (3, 10):
    sys.exit("Run with the Python bundled with Vitis: vitis -s classic2unified.py ...")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bsp_edits  # noqa: E402

# ---------------------------------------------------------------- project detection (ref section 1)
PLATFORM_NATURES = {"com.xilinx.sdx.scw.platformProject", "com.xilinx.sdk.scw.project.ScwProjectNature"}
SDK_NATURE = "com.xilinx.sdx.sdk.core.SdkProjectNature"
HOST_NATURE = "com.xilinx.ide.accel.core.hostApplicationNature"
AIE_NATURE = "com.xilinx.sdx.aie.core.aieproject"
KERNEL_NATURE = "com.xilinx.ide.accel.core.hwKernelNature"
SYSTEM_NATURE = "com.xilinx.sdx.system.systemprojectnature"
CC_NATURE = "org.eclipse.cdt.core.ccnature"

# ---------------------------------------------------------------- .cproject -> set_app_config (ref section 3)
OPTION_KEYS = {
    "compiler.symbols.defined": "USER_COMPILE_DEFINITIONS",
    "compiler.symbols.undefined": "USER_UNDEFINED_SYMBOLS",
    "compiler.dircategory.includes": "USER_INCLUDE_DIRECTORIES",
    "compiler.option.warnings.allwarn": "USER_COMPILE_WARNINGS_ALL",
    "compiler.option.warnings.toerrors": "USER_COMPILE_WARNINGS_AS_ERRORS",
    "compiler.option.warnings.syntax": "USER_COMPILE_WARNINGS_CHECK_SYNTAX_ONLY",
    "compiler.option.warnings.pedantic": "USER_COMPILE_WARNINGS_PEDANTIC",
    "compiler.option.warnings.pedantic.error": "USER_COMPILE_WARNINGS_PEDANTIC_AS_ERRORS",
    "compiler.option.warnings.nowarn": "USER_COMPILE_WARNINGS_INHIBIT_ALL",
    "compiler.option.optimization.level": "USER_COMPILE_OPTIMIZATION_LEVEL",
    "compiler.option.optimization.flags": "USER_COMPILE_OPTIMIZATION_OTHER_FLAGS",
    "compiler.option.debugging.level": "USER_COMPILE_DEBUG_LEVEL",
    "compiler.option.debugging.other": "USER_COMPILE_DEBUG_OTHER_FLAGS",
    "compiler.misc.verbose": "USER_COMPILE_VERBOSE",
    "compiler.misc.ansi": "USER_COMPILE_ANSI",
    "compiler.misc.other": "USER_COMPILE_OTHER_FLAGS",
    "c.link.option.nostart": "USER_LINK_NO_START_FILES",
    "c.link.option.nodeflibs": "USER_LINK_NO_DEFAULT_LIBS",
    "c.link.option.nostdlibs": "USER_LINK_NO_STDLIB",
    "c.link.option.strip": "USER_LINK_OMIT_ALL_SYMBOL_INFO",
    "c.link.option.libs": "USER_LINK_LIBRARIES",
    "c.link.option.paths": "USER_LINK_DIRECTORIES",
    "c.linker.option.lscript": "USER_LINKER_SCRIPT",
    "c.link.option.ldflags": "USER_LINK_OTHER_FLAGS",
}
BOOL_FLAGS = {
    "USER_COMPILE_WARNINGS_ALL": "-Wall", "USER_COMPILE_WARNINGS_AS_ERRORS": "-Werror",
    "USER_COMPILE_WARNINGS_CHECK_SYNTAX_ONLY": "-fsyntax-only", "USER_COMPILE_WARNINGS_PEDANTIC": "-pedantic",
    "USER_COMPILE_WARNINGS_PEDANTIC_AS_ERRORS": "-pedantic-errors", "USER_COMPILE_WARNINGS_INHIBIT_ALL": "-w",
    "USER_COMPILE_VERBOSE": "-v", "USER_COMPILE_ANSI": "-ansi", "USER_LINK_NO_START_FILES": "-nostartfiles",
    "USER_LINK_NO_DEFAULT_LIBS": "-nodefaultlibs", "USER_LINK_NO_STDLIB": "-nostdlib",
    "USER_LINK_OMIT_ALL_SYMBOL_INFO": "-s",
}
ENUMS = {
    "gnu.c.optimization.level.none": "-O0", "gnu.c.optimization.level.optimize": "-O1",
    "gnu.c.optimization.level.more": "-O2", "gnu.c.optimization.level.most": "-O3",
    "gnu.c.optimization.level.size": "-Os", "gnu.c.debugging.level.none": "",
    "gnu.c.debugging.level.minimal": "-g1", "gnu.c.debugging.level.default": "-g",
    "gnu.c.debugging.level.max": "-g3",
}
PATH_KEYS = {"USER_INCLUDE_DIRECTORIES", "USER_LINK_DIRECTORIES", "USER_LINKER_SCRIPT"}
MAKE_ONLY_FLAGS = {"-c", "-MMD", "-MP", "-MD"}
# On 2026.2 builds the key list names USER_UNDEFINE_SYMBOLS, which set_app_config accepts but ignores, while
# UserConfig.cmake (and set_app_config in later builds) use USER_UNDEFINED_SYMBOLS. Ask the running install, as the references say to.
def undefined_symbols_settable() -> bool:
    path = os.path.join(os.environ.get("XILINX_VITIS", ""), "vitis-server", "scripts", "cmake", "user_cmake.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return '"USER_UNDEFINED_SYMBOLS"' in fh.read()
    except OSError:
        return False  # no install to ask: the -U flag route works on every build


LIB_RENAMES = {"lwip211": "lwip220", "lwip213": "lwip220"}
# Unified accepts the short Versal names (psv_*). Classic's own Utils.getSimplifiedVersalProc lists the R5 cores as psu_cortexr5*
# (ZynqMP naming), so it leaves versal_cips_0_pspmc_0_psv_cortexr5_0 unchanged and Unified rejects it; use psv_cortexr5*.
VERSAL_PROCS = ["psv_cortexa72", "psv_cortexa72_0", "psv_cortexa72_1", "psv_cortexr5",
                "psv_cortexr5_0", "psv_cortexr5_1", "psv_pmc_0", "psv_psm_0"]
ACCEL_KINDS = {"host", "aie", "kernel", "hwlink"}
# Options Classic 2023.2-2024.2 sets itself in each boot domain; anything else there is a user change that Unified drops.
BOOT_DEFAULTS = {
    "zynqmp_fsbl": {"standalone": {"zynqmp_fsbl_bsp": "true"}, "xilffs": {"read_only": "true", "use_mkfs": "false"},
                    "xilsecure": {"tpm_support": "true"}},
    "zynqmp_pmufw": {"psu_pmu_0": {"extra_compiler_flags": "-g -ffunction-sections -fdata-sections -Wall -Wextra "
                                                           "-fno-tree-loop-distribute-patterns  -Os -flto -ffat-lto-objects"}},
    "zynq_fsbl": {},
}
FIRMWARE_PROCS = {"psu_pmu_0", "psu_psm_0", "psv_pmc_0", "psv_psm_0", "psx_pmc_0", "psx_psm_0"}


class Migration:
    def __init__(self, classic_ws: str, unified_ws: str, config: str, xsa_overrides: dict):
        self.cws, self.uws = classic_ws, unified_ws
        self.config, self.xsa_overrides = config, xsa_overrides
        self.release: str | None = None       # Classic release of the workspace (bsp_edits.classic_release)
        self.skipped_platforms: set[str] = set()
        self.project_dirs: dict[str, set[str]] = {}  # project folders outside src/ that mapped paths point into
        self.classic_esw: str | None = None   # original sources of the Classic release (for BSP edit detection)
        self.bsp_items: list[dict] = []
        self.bsp_rows: list[tuple[str, str, str]] = []
        self.lines: list[str] = []
        self.unmapped: list[tuple[str, str, str]] = []  # (app, Classic option, enum value) with no Unified mapping
        self.notes: list[str] = []      # manual follow-ups, also printed by migrate.py
        self.found: list[str] = []      # inventory for migration_notes.md

    # ------------------------------------------------------------ helpers
    def emit(self, line: str = "", indent: int = 0):
        self.lines.append("    " * indent + line if line else "")

    def note(self, text: str):
        if text not in self.notes:
            self.notes.append(text)

    def rel_to_ws(self, path: str) -> str | None:
        path = os.path.normpath(path)
        if path == self.cws or path.startswith(self.cws + os.sep):
            return os.path.relpath(path, self.cws).replace(os.sep, "/")
        # Classic stores absolute paths; a copied/moved workspace keeps the old ones. Match by project folder.
        parts = re.split(r"[\\/]+", path)
        for i, part in enumerate(parts[:-1]):
            if part and os.path.isfile(os.path.join(self.cws, part, ".project")):
                return "/".join(parts[i:])
        return None

    def ws_expr(self, path: str) -> str:
        """Python expression for a Classic path: workspace-relative paths follow the new workspace."""
        rel = self.rel_to_ws(path)
        return f"os.path.join(WS, {rel!r})" if rel else repr(path)

    def rewrite_path(self, proj: str, value: str) -> str:
        """Classic CDT path -> CMake path for UserConfig.cmake (CMAKE_SOURCE_DIR = <component>/src)."""
        v = value.strip().strip('"')
        v = re.sub(r"\$\{env_var:([^}]+)\}", r"$ENV{\1}", v)
        m = re.match(r"\$\{workspace_loc:/?(?:\$\{ProjName\}|" + re.escape(proj) + r")/?(.*)\}(.*)$", v)
        if m:
            v = m.group(1) + m.group(2)
            if v.startswith("src/"):
                return "${CMAKE_SOURCE_DIR}/" + v[4:]
            if v.split("/")[0] not in ("", "src"):  # src/ is always copied
                self.project_dirs.setdefault(proj, set()).add(v.split("/")[0])
            return "${CMAKE_SOURCE_DIR}/../" + v
        m = re.match(r"\$\{workspace_loc:/?([^/}]+)/?(.*)\}(.*)$", v)
        if m:
            self.note(f"{proj}: path '{value}' points into project '{m.group(1)}'; mapped to the sibling component folder")
            return f"${{CMAKE_SOURCE_DIR}}/../../{m.group(1)}/{m.group(2)}{m.group(3)}".rstrip("/")
        if v.startswith("../src/"):
            return "${CMAKE_SOURCE_DIR}/" + v[len("../src/"):]
        if v.startswith("../"):
            if v[3:].split("/")[0] not in ("", "src"):
                self.project_dirs.setdefault(proj, set()).add(v[3:].split("/")[0])
            return "${CMAKE_SOURCE_DIR}/../" + v[3:]
        if os.path.isabs(v) and self.rel_to_ws(v):
            return "${CMAKE_SOURCE_DIR}/../../" + self.rel_to_ws(v)
        return v

    # ------------------------------------------------------------ discovery
    def projects(self) -> list[tuple[str, str, set]]:
        found = []
        for name in sorted(os.listdir(self.cws)):
            pdir = os.path.join(self.cws, name)
            dot = os.path.join(pdir, ".project")
            if name.startswith(".") or not os.path.isfile(dot):
                continue
            natures = {n.text.strip() for n in ET.parse(dot).getroot().iter("nature") if n.text}
            if "org.eclipse.rse.ui.remoteSystemsTempNature" in natures:
                continue  # Eclipse Remote Systems scratch project, not a user project
            if natures & PLATFORM_NATURES or os.path.isfile(os.path.join(pdir, "platform.spr")):
                kind = "platform"
            elif SDK_NATURE in natures:
                prj = self.read_prj(pdir, name)
                kind = "library" if prj is not None and (prj.get("isStaticLib") == "true" or prj.get("isSharedLib") == "true") else "app"
            elif HOST_NATURE in natures:
                kind = "host"
            elif AIE_NATURE in natures:
                kind = "aie"
            elif KERNEL_NATURE in natures:
                kind = "kernel"
            elif SYSTEM_NATURE in natures or os.path.isfile(os.path.join(pdir, f"{name}.sprj")):
                kind = "system"
            elif any("hwLinkNature" in n for n in natures):
                kind = "hwlink"
            else:
                kind = "unknown"
            found.append((name, kind, natures))
        order = {"platform": 0, "system": 2, "hwlink": 2}
        return sorted(found, key=lambda p: order.get(p[1], 1))

    @staticmethod
    def read_prj(pdir: str, name: str):
        path = os.path.join(pdir, f"{name}.prj")
        return ET.parse(path).getroot() if os.path.isfile(path) else None

    def platform_arg(self, xpfm: str) -> str:
        if not xpfm:
            return "None"
        m = re.match(r"(.+)_(\d{6})_(\d+)\.xpfm$", os.path.basename(xpfm))
        if not self.rel_to_ws(xpfm) and m:
            # Released platforms carry the release in the name (xilinx_vck190_base_202420_1); use the installed one.
            self.note(f"platform {os.path.basename(xpfm)} is mapped to the newest installed '{m.group(1)}_<release>' platform")
            return f"find_platform({m.group(1)!r}, {xpfm!r})"
        return self.ws_expr(xpfm)

    # ------------------------------------------------------------ platform (ref 2.1)
    def platform(self, name: str):
        pdir = os.path.join(self.cws, name)
        libsrcs = bsp_edits.classic_libsrc_dirs(pdir)
        if self.classic_esw:
            for libsrc in libsrcs:  # main() checked that the reference is the workspace's release
                self.bsp_items += bsp_edits.detect(libsrc, self.classic_esw)
        spr = json.load(open(os.path.join(pdir, "platform.spr"), encoding="utf-8"))
        device = spr.get("deviceType", "")
        domains = [d for s in spr.get("systems", [])[:1] for d in s.get("domains", [])]
        user = [d for d in domains if d.get("domType") != "bootDomain"]
        boot = [d for d in domains if d.get("domType") == "bootDomain"]
        xsa = self.xsa_overrides.get(name)
        if not xsa:
            handoff = spr.get("platIntHandOff", "").replace("<platformDir>", pdir)
            xsa = handoff if os.path.isfile(handoff) else os.path.join(pdir, spr.get("platHandOff", ""))
            exported = sorted(glob.glob(os.path.join(pdir, "export", name, "hw", "*.xsa")))
            if not os.path.isfile(xsa) and exported:
                self.note(f"{name}: XSA {xsa} not found; using the copy in the platform's export folder, {exported[0]}")
                xsa = exported[0]
        self.found.append(f"platform `{name}`: device {device}, XSA `{xsa}`, user domains "
                          f"{[d['domainName'] for d in user]}, boot domains {[d['domainName'] for d in boot]}")
        if not os.path.isfile(xsa):
            self.note(f"{name}: XSA not found at {xsa}; platform not migrated. Re-run with --xsa {name}=<path>")
            self.skipped_platforms.add(name)
            return
        meta = xsa_meta(xsa)
        if meta.get("expandablePlatform") == "true":
            self.note(f"{name}: XSA {xsa} is an extensible (acceleration) platform, which Unified creates as an "
                      f"accelerated platform that embedded apps cannot use; platform not migrated. Export a fixed XSA "
                      f"from the design in Vivado and re-run with --xsa {name}=<fixed.xsa>")
            self.skipped_platforms.add(name)
            return
        tool = meta.get("generatedVersion", "")
        if re.match(r"\d{4}\.\d", tool) and tool[:6] < "2023.2":
            self.note(f"{name}: XSA was generated by Vivado {tool}; older XSAs can break the Unified drivers. "
                      f"If the platform fails to build, regenerate it in a current Vivado and re-run with --xsa {name}=<new.xsa>")
        for dom in boot:
            defaults = BOOT_DEFAULTS.get(dom["domainName"], {})
            for owner, opts in dom.get("libOptions", {}).items():
                if not isinstance(opts, dict):
                    continue
                for key, val in opts.items():
                    if key != "libOptionNames" and defaults.get(owner, {}).get(key) != val:
                        self.note(f"{name}/{dom['domainName']}: boot-domain setting {owner} {key} = {val} is not migrated "
                                  f"(Unified regenerates boot domains); set it in the Unified {dom['domainName']} domain")
        if not user:
            self.note(f"{name}: no user domain found; platform skipped")
            self.skipped_platforms.add(name)
            return
        if any(d.get("swRepo") for d in domains):
            self.note(f"{name}: domains use local SW repositories; migrate them to lopper first (a newer Lopper is selected with "
                      f"export VITIS_LOPPER_INSTALL_LOC=<path> before starting Vitis) and add "
                      f"client.set_embedded_sw_repo(level='LOCAL', path=...) to migrate.py")

        args = [f"name={name!r}", f"hw_design={xsa!r}"]
        if spr.get("platformDesc"):
            args.append(f"desc={spr['platformDesc']!r}")
        first = user[0]
        args += [f"cpu={self.cpu(first, device)!r}", f"os={self.os_name(first)!r}",
                 f"domain_name={first['domainName']!r}"]
        if spr.get("platIsNoBootBsp") == "true":
            args.append("no_boot_bsp=True")
        elif device == "zynqMP" and any("fsbl" in d["domainName"] and d.get("processors", "").lower() == "psu_cortexr5_0" for d in boot):
            args.append("fsbl_target='psu_cortexr5_0'")

        self.emit(f"# ---- platform '{name}' (Classic {os.path.join(pdir, 'platform.spr')})")
        self.emit(f"def platform_{self.ident(name)}():")
        self.emit(f"print({'Creating platform component ' + name!r}, flush=True)", 1)
        self.emit(f"plat = client.create_platform_component({', '.join(args)})", 1)
        for dom in user[1:]:
            extra = f", support_app={dom['compatibleApp']!r}" if "fsbl" in dom.get("compatibleApp", "") else ""
            self.emit(f"plat.add_domain(cpu={self.cpu(dom, device)!r}, os={self.os_name(dom)!r}, "
                      f"name={dom['domainName']!r}, display_name={dom.get('domainDispName', dom['domainName'])!r}{extra})", 1)
        for dom in user:
            self.domain(name, pdir, dom)
        self.emit("plat.build()", 1)
        self.emit(f"xpfm = os.path.join(WS, {name!r}, 'export', {name!r}, {name + '.xpfm'!r})", 1)
        self.emit("if not os.path.isfile(xpfm):", 1)
        self.emit("raise RuntimeError(f'platform build did not produce {xpfm}')", 2)
        self.emit()
        self.emit(f"step('platform {name}', platform_{self.ident(name)})")
        self.emit()
        self.platforms.append(name)

    def domain(self, plat: str, pdir: str, dom: dict):
        dname, osn = dom["domainName"], self.os_name(dom)
        self.emit(f"dom = plat.get_domain(name={dname!r})", 1)
        default_qemu = f"<platformDir>/resources/{plat}/{dname}/"
        for key in ("qemuArgs", "pmuQemuArgs", "qemuData"):
            if dom.get(key) and not str(dom[key]).startswith(default_qemu):
                api = "set_qemu_data(path=...)" if key == "qemuData" else "set_qemu_args(qemu_option='PS'|'PMC'|'PMU', path=...)"
                self.note(f"{plat}/{dname}: Classic {key} ({dom[key]}) not applied; Unified 2026.2 uses its own QEMU defaults; "
                          f"apply it with the domain's {api}")
        if osn == "linux":
            for key, call in (("bootBifFile", "set_bif"), ("sysBootDir", "set_boot_dir"), ("imageLocation", "set_sd_dir"),
                              ("sdDir", "set_sd_dir")):
                val = dom.get(key, "").replace("<platformDir>", pdir)
                if val:
                    self.emit(f"dom.{call}(path={self.ws_expr(val)})", 1)
            if dom.get("addedLibs") or dom.get("libOptions"):
                self.note(f"{plat}/{dname}: Linux domain BSP settings are not applicable in Unified; skipped")
            return
        for lib in dom.get("addedLibs", []):
            lname = lib.split(":")[0]
            lname = LIB_RENAMES.get(lname, lname)
            self.emit(f"add_lib(dom, {lname!r})", 1)
        for owner, opts in dom.get("libOptions", {}).items():
            if not isinstance(opts, dict):
                continue
            if owner == dom.get("processors"):
                option, lib = "proc", None
            elif owner in (dom.get("os"), osn):
                option, lib = "os", None
            else:
                option, lib = "lib", LIB_RENAMES.get(owner, owner)
            for key, val in opts.items():
                if isinstance(val, (str, int, float, bool)) and key != "libOptionNames":
                    self.emit(f"set_param(dom, {option!r}, {key!r}, {str(val)!r}, lib={lib!r}, plat={plat!r})", 1)
        if dom.get("mssFile"):
            self.note(f"{plat}/{dname}: Classic MSS file {dom['mssFile']} and any local BSP source edits are not migrated")

    @staticmethod
    def os_name(dom: dict) -> str:
        osn = (dom.get("os") or "standalone").lower()
        return "freertos" if osn.startswith("freertos") else osn

    def cpu(self, dom: dict, device: str) -> str:
        proc = dom.get("processors", "")
        if device.lower().startswith("versal"):
            for simple in VERSAL_PROCS:
                if proc.endswith(simple):
                    if proc != simple:
                        self.note(f"cpu instance '{proc}' converted to '{simple}'")
                    return simple
        return proc

    def firmware_app(self, prj) -> bool:
        """Boot-firmware app (FSBL/PMUFW/PLM template or firmware CPU); Unified builds these in the boot domain."""
        tmpl = prj.find("template")
        name = tmpl.get("appTemplateName", "") if tmpl is not None else ""
        return bool(re.search(r"fsbl|pmufw|plm|psmfw", name, re.I)) or \
            self.cpu({"processors": prj.get("cpuInstance", "")}, "versal") in FIRMWARE_PROCS

    @staticmethod
    def ident(name: str) -> str:
        return re.sub(r"\W", "_", name)

    # ------------------------------------------------------------ embedded app (ref 2.2)
    def app(self, name: str, natures: set):
        pdir = os.path.join(self.cws, name)
        prj = self.read_prj(pdir, name)
        if prj is None:
            self.note(f"{name}: no {name}.prj; skipped")
            return
        osn, cpu_inst = (prj.get("os") or "").lower(), prj.get("cpuInstance", "")
        tmpl_el = prj.find("template")
        orig_tmpl = tmpl_el.get("appTemplateName", "") if tmpl_el is not None else ""
        if self.firmware_app(prj):
            self.note(f"{name}: Classic boot firmware application ({orig_tmpl or cpu_inst}) is not migrated; Unified builds "
                      f"the FSBL/PMUFW/PLM in the platform's boot domain. Port any source edits there")
            return
        template = "linux_empty_application" if osn == "linux" else "empty_application"
        if "lwip" in orig_tmpl.lower() and (osn.startswith("freertos") or re.search(r"cortexr5|cortexa9", cpu_inst)):
            self.note(f"{name}: Classic-generated lwIP template sources ({orig_tmpl}) are copied as they are and may not "
                      f"compile against lwip220 on this processor/OS; if the build fails, port the app to the Unified "
                      f"lwIP template")
        if re.search(r"libmetal|openamp", orig_tmpl, re.I):
            self.note(f"{name}: Classic libmetal/OpenAMP template sources ({orig_tmpl}) are copied as they are and may not "
                      f"compile against the Unified libmetal; if the build fails, port the app from the Unified "
                      f"libmetal/OpenAMP template")
        xpfm = prj.get("platform", "")
        plat = (self.rel_to_ws(xpfm) or "").split("/")[0]
        if plat in self.skipped_platforms:
            self.note(f"{name}: not migrated because its platform {plat} was not migrated (see above)")
            return
        if not self.rel_to_ws(xpfm):
            self.note(f"{name}: platform {xpfm} is outside the Classic workspace; migrate that platform first "
                      f"and edit the platform= argument in migrate.py")
        self.found.append(f"app `{name}`: domain `{prj.get('cpu')}` on `{cpu_inst}`, os {osn}, "
                          f"Classic template `{orig_tmpl}` -> `{template}`")
        self.emit(f"# ---- application '{name}'")
        self.emit(f"def app_{self.ident(name)}():")
        self.emit(f"print({'Creating application component ' + name!r}, flush=True)", 1)
        self.emit(f"app = client.create_app_component(name={name!r}, platform={self.platform_arg(xpfm)}, "
                  f"domain={prj.get('cpu')!r}, template={template!r})", 1)
        self.emit(f"shutil.copytree(src={os.path.join(pdir, 'src')!r}, dst=os.path.join(app.component_location, 'src'), "
                  f"dirs_exist_ok=True)", 1)
        if osn == "linux":
            # The Linux template builds only what USER_COMPILE_SOURCES lists (CMAKE_SOURCE_DIR is the component folder).
            srcs = sorted(os.path.relpath(os.path.join(r, f), pdir).replace(os.sep, "/")
                          for r, _, fs in os.walk(os.path.join(pdir, "src")) for f in fs
                          if f.endswith((".c", ".cc", ".cpp", ".cxx", ".S", ".s")))
            if srcs:
                self.emit(f"app.set_app_config(key='USER_COMPILE_SOURCES', values={['${CMAKE_SOURCE_DIR}/' + x for x in srcs]!r})", 1)
        settings = self.cproject_settings(name, pdir, CC_NATURE in natures)
        for sub in sorted(self.project_dirs.get(name, ())):
            if os.path.isdir(os.path.join(pdir, sub)):
                self.emit(f"shutil.copytree(src={os.path.join(pdir, sub)!r}, dst=os.path.join(app.component_location, {sub!r}), "
                          f"dirs_exist_ok=True)", 1)
            else:
                self.note(f"{name}: a build setting points into {sub}/, which does not exist in the Classic project")
        for _, base, val in (u for u in self.unmapped if u[0] == name):
            self.emit(f"report('UNSUPPORTED', {f'{name} {base} = {val}'!r}, 'Classic enumerated value has no Unified "
                      f"mapping; not set, set the equivalent in {name}/src/UserConfig.cmake')", 1)
        for key, values in settings:
            self.emit(f"app.set_app_config(key={key!r}, values={values!r})", 1)
            self.emit(f"report('MIGRATED', {f'{name} {key} = {values!r}'!r})", 1)
        self.emit()
        self.emit(f"step('application {name}', app_{self.ident(name)})")
        self.emit()
        self.built.append((name, "app"))

    def cproject_settings(self, proj: str, pdir: str, is_cpp: bool) -> list[tuple[str, object]]:
        path = os.path.join(pdir, ".cproject")
        if not os.path.isfile(path):
            return []
        root = ET.parse(path).getroot()
        configs = [c for c in root.iter("configuration") if c.get("name") and c.find(".//tool") is not None]
        if not configs:
            return []
        cfg = next((c for c in configs if c.get("name") == self.config), configs[0])
        if cfg.get("name") != self.config:
            self.note(f"{proj}: build configuration '{self.config}' not found; used '{cfg.get('name')}'")
        lang = ".cxx." if is_cpp else ".c."
        out: dict[str, object] = {}
        undefined: list[str] = []   # Classic -U symbols that this install cannot set as a key (see undefined_symbols_settable)
        for tool in cfg.iter("tool"):
            sc = tool.get("superClass", "")
            assembler = "assembler" in sc
            if lang not in sc and not assembler:
                continue
            for opt in tool.findall("option"):
                base = opt.get("superClass", "")
                items = [l.get("value", "") for l in opt.findall("listOptionValue")]
                val = opt.get("value", opt.get("defaultValue"))  # defaultValue: the configuration's own default, e.g. Release -O2
                if val is None and not items:
                    continue  # option left at the Classic plugin default
                suffix = None if assembler else next((s for s in OPTION_KEYS if base.endswith("." + s) and
                                                      (base.startswith("xilinx.gnu.") or base.startswith("xilinx.armcc."))), None)
                if not suffix:
                    if "inferred" not in base:  # inferred.* options come from the platform and are regenerated
                        self.note(f"{proj}: Classic option {base} = {val if val is not None else items} has no Unified "
                                  f"mapping and was not migrated; set it in {proj}/src/UserConfig.cmake "
                                  f"(for example USER_COMPILE_OTHER_FLAGS / USER_LINK_OTHER_FLAGS)")
                    continue
                key = OPTION_KEYS[suffix]
                if key == "USER_UNDEFINED_SYMBOLS" and items and not undefined_symbols_settable():
                    undefined += [i.strip('"') for i in items if i.strip('"') not in undefined]
                    continue
                if key in BOOL_FLAGS:
                    out[key] = BOOL_FLAGS[key] if val == "true" else ""
                elif val in ENUMS:
                    out[key] = ENUMS[val]
                elif opt.get("valueType") == "enumerated":
                    if (proj, base, val) not in self.unmapped:
                        self.unmapped.append((proj, base, val))
                elif items:
                    vals = [self.rewrite_path(proj, i) if key in PATH_KEYS else i for i in items]
                    out[key] = sorted(set(out.get(key, [])) | set(vals)) if isinstance(out.get(key), list) else vals
                else:
                    if key in ("USER_COMPILE_OTHER_FLAGS", "USER_LINK_OTHER_FLAGS"):
                        # Drop Classic make plumbing (-c, dependency files, specs); Unified CMake adds its own.
                        val = " ".join(t for t in val.split() if t not in MAKE_ONLY_FLAGS
                                       and not t.startswith(("-specs=", "-MT", "-MF", "-MQ")))
                        if not val:
                            continue
                    if key in PATH_KEYS:
                        val = self.rewrite_path(proj, val)
                        if key == "USER_LINKER_SCRIPT" and val == "${CMAKE_SOURCE_DIR}/lscript.ld":
                            continue  # same as the Unified default
                    out[key] = val
        if undefined:
            other = str(out.get("USER_COMPILE_OTHER_FLAGS", ""))
            out["USER_COMPILE_OTHER_FLAGS"] = " ".join(filter(None, [other] + [f"-U{u}" for u in undefined
                                                                               if f"-U{u}" not in other.split()]))
            self.note(f"{proj}: this Vitis cannot set USER_UNDEFINED_SYMBOLS; the Classic undefined symbols "
                      f"{undefined} are migrated as -U flags in USER_COMPILE_OTHER_FLAGS")
        if out:
            self.found.append(f"app `{proj}` build settings ({cfg.get('name')}): " +
                              ", ".join(f"{k}={v!r}" for k, v in out.items()))
        return list(out.items())

    # ------------------------------------------------------------ system project
    def system(self, name: str):
        pdir = os.path.join(self.cws, name)
        try:
            root = ET.parse(os.path.join(pdir, f"{name}.sprj")).getroot()
        except (OSError, ET.ParseError) as exc:
            self.note(f"{name}: system project could not be read ({exc}); recreate it manually")
            return
        cfgs = [c for c in root.iter("configuration")]
        cfg = next((c for c in cfgs if c.get("name") == self.config), cfgs[0] if cfgs else None)
        apps = []
        if cfg is not None:
            opts = cfg.find("configBuildOptions")
            apps = [a.get("name") for a in (opts.findall("applications") if opts is not None else [])]
        if any(self.kinds.get(a) in ACCEL_KINDS for a in apps):
            self.note(f"{name}: accelerated system project is not supported; recreate it manually")
            return
        plat = (self.rel_to_ws(root.get("platform", "")) or "").split("/")[0]
        if plat in self.skipped_platforms:
            self.note(f"{name}: not migrated because its platform {plat} was not migrated (see above)")
            return
        built = {n for n, _ in self.built}
        if not built & set(apps):
            self.note(f"{name}: system project not migrated: none of its applications {apps} was migrated")
            return
        self.found.append(f"system project `{name}`: applications {apps}")
        self.emit(f"# ---- system project '{name}'")
        self.emit(f"def system_{self.ident(name)}():")
        self.emit(f"sysp = client.create_sys_project(name={name!r}, platform={self.platform_arg(root.get('platform', ''))})", 1)
        for a in apps:
            if a in built:
                self.emit(f"sysp.add_component(name={a!r})", 1)
            else:
                self.note(f"{name}: application {a} was not migrated; add it to the system project manually")
        self.emit()
        self.emit(f"step('system project {name}', system_{self.ident(name)})")
        self.emit()
        self.systems.append(name)

    # ------------------------------------------------------------ driver
    def run(self) -> str:
        self.built, self.systems, self.platforms = [], [], []
        projects = self.projects()
        self.kinds = {n: k for n, k, _ in projects}
        self.found.insert(0, f"projects: " + ", ".join(f"{n} ({k})" for n, k, _ in projects))
        body_start = len(self.lines)
        for name, kind, natures in projects:
            if kind == "platform":
                self.platform(name)
            elif kind == "app":
                self.app(name, natures)
            elif kind in ACCEL_KINDS:
                self.note(f"{name}: accelerated projects ({kind}) are not supported; recreate it manually")
            elif kind == "system":
                self.system(name)
            else:
                self.note(f"{name}: project type '{kind}' is not migrated")
        body = self.lines[body_start:]
        self.lines = self.lines[:body_start]
        if self.bsp_items:
            body = self.bsp_repo() + body
        return self.render(body)

    def bsp_repo(self) -> list[str]:
        """Rebase Classic BSP source edits into <uws>/sw_repo; return the migrate.py lines that register it."""
        new_root = os.path.join(os.environ.get("XILINX_VITIS", ""), "data", "embeddedsw")
        if not os.path.isdir(new_root):
            self.note("BSP source edits found but $XILINX_VITIS/data/embeddedsw is missing; run this script with vitis -s")
            return []
        repo, evidence = os.path.join(self.uws, "sw_repo"), os.path.join(self.uws, "bsp_edits")
        self.bsp_rows = bsp_edits.build_repo(self.bsp_items, new_root, repo, evidence)
        if not os.path.isdir(repo):
            return []
        return ["# ---- local software repository with the user's Classic BSP source edits (before any platform)",
                "client.set_embedded_sw_repo(level='LOCAL', path=os.path.join(WS, 'sw_repo'))",
                "report('MIGRATED', 'local software repository sw_repo registered (patched BSP components)')", ""]

    def render(self, body: list[str]) -> str:
        head = f'''#!/usr/bin/env python3
"""Generated by vitis-classic-migration/scripts/classic2unified.py (do not run with Classic Vitis).

Classic workspace : {self.cws}
Unified workspace : this folder
Run               : cd <this folder> && vitis -s migrate.py [--build]
"""
import glob
import os
import re
import shutil
import sys
import traceback

import vitis

WS = os.path.dirname(os.path.abspath(__file__))
BUILD = "--build" in sys.argv[1:]
NOTES = {self.notes!r}
FAILED = []
REPORT = []

_mine = {{"migrate.py", "migration_notes.md", "migrate.log", "__pycache__", "sw_repo", "bsp_edits"}}
_other = [f for f in os.listdir(WS) if f not in _mine and not f.startswith(".")]
if _other:
    sys.exit(f"ERROR: {{WS}} already contains {{_other}}; migrate into a new, empty folder")


def step(title, fn):
    try:
        fn()
        print(f"OK: {{title}}", flush=True)
    except Exception as exc:
        FAILED.append(title)
        print(f"FAILED: {{title}}: {{exc}}", flush=True)
        traceback.print_exc()


def find_platform(stem, classic_xpfm):
    """Newest installed release of a Classic platform, matched by exact name stem (e.g. xilinx_vck190_base)."""
    roots = [os.path.join(os.environ.get("XILINX_VITIS", ""), "base_platforms")]
    roots += os.environ.get("PLATFORM_REPO_PATHS", "").split(os.pathsep)
    pattern = re.compile(re.escape(stem) + r"_\\d{{6}}_\\d+$")
    found = sorted((p for r in roots if r for p in glob.glob(os.path.join(r, stem + "_*", stem + "_*.xpfm"))
                    if pattern.match(os.path.basename(os.path.dirname(p)))), key=lambda p: os.path.basename(os.path.dirname(p)))
    if not found:
        print(f"WARNING: no installed {{stem}}_<release> platform; using the Classic one {{classic_xpfm}}", flush=True)
        return classic_xpfm
    print(f"  platform {{os.path.basename(classic_xpfm)}} -> {{found[-1]}}", flush=True)
    return found[-1]


def report(status, what, detail=""):
    """Per-setting outcome: MIGRATED, CHANGED (value differs on purpose or by necessity) or UNSUPPORTED (with guidance)."""
    REPORT.append((status, what, detail))
    print(f"  {{status}}: {{what}}{{' - ' + detail if detail else ''}}", flush=True)


def add_lib(dom, name):
    try:
        if not any(lib.get("name") == name for lib in dom.get_libs()):
            dom.set_lib(lib_name=name)
        report("MIGRATED", f"{{dom.domain_name}} library {{name}}")
    except Exception as exc:
        report("UNSUPPORTED", f"{{dom.domain_name}} library {{name}}",
               f"not available in this Vitis release ({{exc}}); pick a replacement from dom.get_applicable_libs()")


def set_param(dom, option, key, value, lib=None, plat=""):
    where = f"{{plat}}/{{dom.domain_name}}"
    try:
        _set_param(dom, option, key, value, lib, where)
    except Exception as exc:
        report("UNSUPPORTED", f"{{where}} {{option}} {{lib + ' ' if lib else ''}}{{key}} = {{value}}",
               f"{{exc}}; find the Unified name with dom.list_params({{option!r}}) and set it with dom.set_config")


def _set_param(dom, option, key, value, lib=None, where=""):
    """Set a BSP parameter, resolving the Unified (System Device Tree) name for a Classic key."""
    try:
        params = dom.list_params(option, lib_name=lib) if lib else dom.list_params(option)
        names = [p.get("parameter_name") or p.get("name") for p in params] if isinstance(params, list) else list(params or [])
    except Exception:
        names = []
    guesses = [n for n in names if n == key or (n and n.lower().endswith("_" + key.lower()))]
    if not guesses and not (option == "proc" and not names):  # every Unified proc_config parameter is proc_<key>
        # Setting an unknown name is accepted here but aborts the platform build later, and the casing differs per library.
        report("UNSUPPORTED", f"{{where}} {{option}} {{lib + ' ' if lib else ''}}{{key}} = {{value}}",
               "no Unified equivalent for this Classic parameter; not set (see dom.list_params)" if names else
               f"could not find the Unified parameter for {{lib or option}}.{{key}}; set it in the BSP settings")
        return
    param = guesses[0] if guesses else f"proc_{{key}}"
    current = None
    try:
        current = dom.get_config(option=option, param=param, lib_name=lib) if lib else dom.get_config(option=option, param=param)
    except Exception:
        pass
    cur = str(current.get("value", "")).strip() if isinstance(current, dict) else None
    what = f"{{where}} {{option}} {{param}} = {{value}}"
    if cur is not None and cur == value.strip():
        report("MIGRATED", what, "already the Unified value")
        return
    if lib:
        dom.set_config(option=option, param=param, value=value, lib_name=lib)
    else:
        dom.set_config(option=option, param=param, value=value)
    dropped = [t for t in (cur or "").split() if t not in value.split()] if param.endswith("flags") else []
    if dropped:
        # The exact Classic value is kept; Classic kept some of these (e.g. -O2) in other parameters.
        report("CHANGED", what, f"Unified default flags removed to match Classic: {{' '.join(dropped)}}; "
                                f"add them back with dom.set_config if they are needed")
    else:
        report("MIGRATED", what)


client = vitis.create_client()
client.update_workspace(WS)
print(f"Unified workspace: {{client.get_workspace()}}", flush=True)

'''
        tail = f'''

def build_app(name):
    comp = client.get_component(name=name)
    rc = comp.build()
    if rc:  # build() returns a non-zero exit code on failure instead of raising
        raise RuntimeError(f"build of {{name}} returned {{rc}}")
    out = os.path.join(comp.component_location, "build")
    # build() can report success after a failed CMake/ninja run; trust only the artifact on disk.
    artifacts = [f for f in (name + ".elf", name) if os.path.isfile(os.path.join(out, f))]
    if not artifacts:
        raise RuntimeError(f"build of {{name}} produced no artifact in {{out}}")
    print(f"  artifact: {{os.path.join(out, artifacts[0])}}", flush=True)


def build_system(name):
    rc = client.get_sys_project(name=name).build()
    if rc:  # build() returns a non-zero exit code on failure instead of raising
        raise RuntimeError(f"system project build returned {{rc}}")


if BUILD:
    for name, _ in {self.built!r}:
        step(f"build {{name}}", lambda n=name: build_app(n))
    for name in {self.systems!r}:
        step(f"build system project {{name}}", lambda n=name: build_system(n))

vitis.dispose()
print("\\nManual follow-ups:")
for n in NOTES:
    print(f"  - {{n}}")
with open(os.path.join(WS, "migration_notes.md"), "a", encoding="utf-8") as fh:
    fh.write("\\n## Run results (migrate.py)\\n\\n| Status | Setting | Detail |\\n|---|---|---|\\n")
    fh.writelines(f"| {{s}} | {{w}} | {{d}} |\\n" for s, w, d in REPORT)
    fh.writelines(f"| FAILED | step {{t}} | see migrate.log |\\n" for t in FAILED)
if FAILED:
    print(f"\\nFAILED steps: {{FAILED}}")
    sys.exit(1)
print("\\nMigration script finished.")
'''
        return head + "\n".join(body) + tail

    def notes_md(self) -> str:
        if not self.classic_esw:
            bsp = "No BSP sources in the Classic workspace.\n"
        elif not self.bsp_items:
            bsp = "No user edits found in the Classic BSP sources.\n"
        else:
            bsp = ("Patched components are in `sw_repo/`; the original Classic edits are kept in `bsp_edits/`.\n\n"
                   "| Status | File | Detail |\n|---|---|---|\n" + "".join(f"| {s} | {w} | {d} |\n" for s, w, d in self.bsp_rows))
        return ("# Migration notes\n\n"
                f"Classic workspace: `{self.cws}`\n\n## Found\n\n" + "\n".join(f"- {f}" for f in self.found) +
                "\n\n## BSP source edits\n\n" + bsp +
                "\n## Manual follow-ups\n\n" + ("\n".join(f"- {n}" for n in self.notes) or "- none") + "\n")


def xsa_meta(xsa: str) -> dict:
    """Top-level xsa.json fields of an XSA (expandablePlatform, generatedVersion, ...); {} if unreadable."""
    try:
        with zipfile.ZipFile(xsa) as z:
            return {k: v for k, v in json.loads(z.read("xsa.json")).items() if isinstance(v, str)}
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("classic_ws")
    ap.add_argument("unified_ws")
    ap.add_argument("--config", default="Debug", help="Classic build configuration to migrate (default Debug)")
    ap.add_argument("--xsa", action="append", default=[], metavar="PLAT=XSA",
                    help="use a regenerated XSA for platform PLAT")
    ap.add_argument("--classic-embeddedsw", metavar="DIR",
                    help="data/embeddedsw of the Classic install that created the workspace; required when the "
                         "workspace has BSP sources, to find user edits to them")
    args = ap.parse_args(argv)
    cws, uws = os.path.abspath(args.classic_ws), os.path.abspath(args.unified_ws)
    if not os.path.isdir(cws) or not os.path.isdir(os.path.join(cws, ".metadata")) and not any(
            os.path.isfile(os.path.join(cws, d, ".project")) for d in os.listdir(cws)):
        print(f"ERROR: {cws} does not look like a Classic Vitis workspace")
        return 2
    generated = ("migrate.py", "migration_notes.md", "sw_repo", "bsp_edits")
    if os.path.isdir(uws) and [f for f in os.listdir(uws) if f not in generated]:
        print(f"ERROR: {uws} is not empty; choose a new folder")
        return 2
    for sub in ("sw_repo", "bsp_edits"):  # our own output from an earlier generation; start clean
        if os.path.isdir(os.path.join(uws, sub)):
            import shutil
            shutil.rmtree(os.path.join(uws, sub))
    if " " in uws:
        print(f"ERROR: '{uws}' contains spaces; Vitis tools fail on such paths")
        return 2
    overrides = dict(x.split("=", 1) for x in args.xsa)
    libsrcs = [l for d in os.listdir(cws) if os.path.isfile(os.path.join(cws, d, "platform.spr"))
               for l in bsp_edits.classic_libsrc_dirs(os.path.join(cws, d))]
    release, evidence = bsp_edits.classic_release(cws, libsrcs)
    if release and release not in bsp_edits.SUPPORTED_RELEASES:
        print(f"ERROR: unsupported Classic workspace: release {release}, from {evidence}; only Classic "
              f"{', '.join(bsp_edits.SUPPORTED_RELEASES)} workspaces are supported")
        return 2
    if not release:
        print(f"WARNING: could not tell the workspace's Classic release ({evidence}); supported: "
              f"{', '.join(bsp_edits.SUPPORTED_RELEASES)}")
    if libsrcs and not args.classic_embeddedsw:
        print("ERROR: the workspace has BSP sources; pass --classic-embeddedsw <dir> with the embeddedsw of its Classic "
              f"release ({release or 'unknown'}) so user edits to them are found: the data/embeddedsw of the Classic "
              "install that created the workspace")
        return 2
    mig = Migration(cws, uws, args.config, {k: os.path.abspath(v) for k, v in overrides.items()})
    mig.release = release
    mig.found.append(f"Classic release: {release or 'unknown'} ({evidence})")
    mig.found.append(f"Classic build configuration: {args.config}")  # read back by verify_migration.py
    if args.classic_embeddedsw:
        esw = os.path.abspath(args.classic_embeddedsw)
        if not any(os.path.isdir(os.path.join(esw, c)) for c in bsp_edits.CATEGORIES):
            print(f"ERROR: {esw} does not look like an embeddedsw tree (no XilinxProcessorIPLib/drivers, lib/bsp, ...)")
            return 2
        mig.classic_esw = esw
        ref = bsp_edits.reference_release(esw)
        if libsrcs and ref != release:
            # Component folders keep their _vX_Y name across releases while their sources change, so any other
            # release's tree reports invented edits.
            print(f"ERROR: --classic-embeddedsw {esw} " + (
                f"is release {ref}, but the workspace is Classic {release}" if ref else
                "does not contain a versioned lib/bsp/standalone_vX_Y of a supported Classic release, so it is not an "
                "install's data/embeddedsw") + f"; pass the data/embeddedsw of the Classic {release} install that created "
                "the workspace")
            return 2
        mig.found.append(f"reference embeddedsw: `{esw}` (release {ref or 'unknown'})")
    script = mig.run()
    if not mig.built and not mig.systems and not mig.platforms:
        print(mig.notes_md())
        print("ERROR: nothing in this workspace can be migrated (see the manual follow-ups above)")
        return 2
    os.makedirs(uws, exist_ok=True)
    with open(os.path.join(uws, "migrate.py"), "w", encoding="utf-8") as fh:
        fh.write(script)
    with open(os.path.join(uws, "migration_notes.md"), "w", encoding="utf-8") as fh:
        fh.write(mig.notes_md())
    print(mig.notes_md())
    print(f"Wrote {uws}/migrate.py\nNext: cd {uws} && vitis -s migrate.py --build")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
