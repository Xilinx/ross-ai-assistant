#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Post-migration verification: compare a Classic workspace with the migrated Unified workspace.

    vitis -s verify_migration.py <classic_ws> <unified_ws> [--classic-embeddedsw DIR] [--config Debug]

Checks (each row PASS/FAIL):
  - every Classic platform and user domain exists in Unified
  - every Classic BSP library is present, and every user-set BSP parameter has the same value
  - every mapped Classic app build setting has the same value in <app>/src/UserConfig.cmake
  - every Classic app source file exists in Unified and is byte-identical (no data loss)
  - every Classic BSP source edit is present in the Unified BSP sources (with --classic-embeddedsw)
Not checked: system projects, Linux and boot (FSBL/PMUFW/PLM) domains, and library projects. Expected app settings and
BSP parameter names come from classic2unified.py's own mapping, so a mapping error there is not caught here.
Writes <unified_ws>/verification_report.md. Exit 0 when nothing FAILs. Run after migrate.py --build.
"""
import argparse
import difflib
import filecmp
import glob
import json
import os
import re
import shlex
import sys

if sys.version_info < (3, 10):
    sys.exit("Run with the Python bundled with Vitis: vitis -s verify_migration.py ...")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bsp_edits  # noqa: E402
import classic2unified  # noqa: E402

import yaml  # bundled with Vitis


def user_config(path: str) -> dict[str, list[str]]:
    """User-owned set(KEY ...) blocks before the generated tail of UserConfig.cmake."""
    text = open(path, encoding="utf-8").read()
    stop = text.find("###   END OF USER SETTINGS")
    if stop == -1:
        stop = text.find("DO NOT EDIT BEYOND THIS LINE")
    if stop != -1:
        text = text[:stop]
    out: dict[str, list[str]] = {}
    i = 0
    while (i := text.find("set(", i)) != -1:
        j, depth = i + 4, 1
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        parts = shlex.split(text[i + 4:j - 1].replace("\n", " "), posix=True)
        if parts:  # in order: a later set() replaces, and ${KEY} in it stands for the current value
            out[parts[0]] = [x for v in parts[1:] for x in (out.get(parts[0], []) if v == "${" + parts[0] + "}" else [v])]
        i = j
    return out


def norm_path_item(value: str, base: str) -> str:
    v = value.strip().strip('"')
    expanded = re.sub(r"\$\{CMAKE_SOURCE_DIR\}([^\s;]*)",
                      lambda m: os.path.normpath(os.path.join(base, m.group(1).lstrip("/") or ".")), v)
    if expanded != v:  # text around the fragment is kept as it is
        return expanded
    m = re.match(r"(\$(?:ENV)?\{[^}]*\})(.*)$", v)  # unresolved variable prefix: normalize only what follows it
    if m:
        lead, tail = ("/" if m.group(2).startswith("/") else ""), m.group(2).lstrip("/")
        rest = os.path.normpath(tail) if tail else ""  # as a relative path, so a leading ".." survives
        return m.group(1) + lead + ("" if rest == "." else rest)
    if "$" in v:
        return v
    return os.path.normpath(v) if os.path.isabs(v) else os.path.normpath(os.path.join(base, v))


def norm_path_values(values: list, base: str) -> set[str]:
    return {norm_path_item(v, base) for v in values}


def recorded_config(path: str) -> str | None:
    """Classic build configuration the generator recorded in migration_notes.md, if any."""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        m = re.search(r"^- Classic build configuration: (.+)$", fh.read(), re.M)
    return m.group(1).strip() if m else None


def unsupported_bsp_settings(path: str) -> set[str]:
    """LHS strings from migration_notes UNSUPPORTED rows (<platform>/<domain> option key)."""
    if not os.path.isfile(path):
        return set()
    out: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
        if "| UNSUPPORTED |" not in line:
            continue
        m = re.search(r"\|\s*UNSUPPORTED\s*\|\s*([^|]+?)\s*\|", line)
        if not m:
            continue
        detail = m.group(1).strip()
        if " = " in detail:
            out.add(detail.split(" = ", 1)[0])
    return out


def bsp_value(bsp: dict, section: str, owner: str, key: str) -> tuple[str | None, str | None]:
    """(Unified param name, value) for a Classic key in os_config/proc_config/lib_config."""
    block = (bsp.get(section) or {}).get(owner) or {}
    for name, entry in block.items():
        if name == key or name.lower().endswith("_" + key.lower()):
            return name, str(entry.get("value", "")).strip() if isinstance(entry, dict) else str(entry)
    return None, None


class Verifier:
    def __init__(self, cws: str, uws: str, esw: str | None, config: str):
        self.cws, self.uws, self.esw = cws, uws, esw
        self.mig = classic2unified.Migration(cws, uws, config, {})
        self.unsupported = unsupported_bsp_settings(os.path.join(uws, "migration_notes.md"))
        self.rows: list[tuple[str, str, str, str]] = []  # status, area, item, detail
        # the generator applies no BSP edit when the reference looks like another build (workspace-wide decision)
        self.suspect = bool(esw) and bsp_edits.reference_suspect(
            [it for name, kind, _ in self.mig.projects() if kind == "platform"
             for libsrc in bsp_edits.classic_libsrc_dirs(os.path.join(cws, name))
             for it in bsp_edits.detect(libsrc, esw)])

    def add(self, ok: bool, area: str, item: str, detail: str = ""):
        self.rows.append(("PASS" if ok else "FAIL", area, item, detail))

    def platform(self, name: str):
        pdir = os.path.join(self.cws, name)
        spr = json.load(open(os.path.join(pdir, "platform.spr"), encoding="utf-8"))
        device = spr.get("deviceType", "")
        domains = [d for s in spr.get("systems", [])[:1] for d in s.get("domains", []) if d.get("domType") != "bootDomain"]
        self.add(os.path.isfile(os.path.join(self.uws, name, "vitis-comp.json")), "platform", name)
        comp_json = os.path.join(self.uws, name, "vitis-comp.json")
        comp_text = open(comp_json, encoding="utf-8").read() if os.path.isfile(comp_json) else ""
        for dom in domains:
            dname = dom["domainName"]
            osn = (dom.get("os") or "").lower()
            if self.mig.os_name(dom) == "linux" or osn == "aie_runtime":
                self.add(f'"{dname}"' in comp_text, "domain", f"{name}/{dname}")
                continue
            hits = glob.glob(os.path.join(self.uws, name, "*", dname, "bsp", "bsp.yaml"))
            self.add(bool(hits), "domain", f"{name}/{dname}")
            if not hits:
                continue
            bsp = yaml.safe_load(open(hits[0], encoding="utf-8"))
            have = set((bsp.get("lib_info") or {}).keys())
            for lib in dom.get("addedLibs", []):
                lname = classic2unified.LIB_RENAMES.get(lib.split(":")[0], lib.split(":")[0])
                self.add(lname in have, "BSP library", f"{dname} {lname}")
            procs = list((bsp.get("proc_config") or {}).keys())
            for owner, opts in dom.get("libOptions", {}).items():
                if not isinstance(opts, dict):
                    continue
                if owner == dom.get("processors"):
                    section, uowner = "proc_config", procs[0] if procs else ""
                elif owner in (dom.get("os"), self.mig.os_name(dom)):
                    section, uowner = "os_config", self.mig.os_name(dom)
                else:
                    section, uowner = "lib_config", classic2unified.LIB_RENAMES.get(owner, owner)
                for key, val in opts.items():
                    if key == "libOptionNames" or not isinstance(val, (str, int, float, bool)):
                        continue
                    pname, uval = bsp_value(bsp, section, uowner, key)
                    if owner == dom.get("processors"):
                        tag = f"{name}/{dname} proc {key}"
                    elif owner in (dom.get("os"), self.mig.os_name(dom)):
                        tag = f"{name}/{dname} os {key}"
                    else:
                        tag = f"{name}/{dname} lib {uowner} {key}"  # the generator reports the renamed Unified library
                    if pname is None and not any(n.lower().endswith("_" + key.lower()) or n == key
                                                 for blk in (bsp.get(section) or {}).values() for n in (blk or {})):
                        # No Unified equivalent: migrate.py reports it UNSUPPORTED and does not set it.
                        self.rows.append(("SKIP", "BSP setting", f"{dname} {owner}.{key}", "no Unified equivalent"))
                        continue
                    if tag in self.unsupported:
                        self.rows.append(("SKIP", "BSP setting", f"{dname} {owner}.{key}",
                                          "UNSUPPORTED in migrate.py"))
                        continue
                    self.add(uval == str(val).strip(), "BSP setting", f"{dname} {owner}.{key}",
                             f"Classic '{val}' / Unified {pname or '(missing)'} '{uval}'")
        self.bsp_components(name, pdir)
        if self.esw:
            self.bsp_sources(name, pdir)

    @staticmethod
    def bsp_domain(libsrc: str, pdir: str) -> str:
        """Domain of a libsrc folder, in a Classic (<proc>/<domain>/bsp/<proc>/libsrc) or Unified (<proc>/<domain>/bsp/libsrc)
        platform; boot domains have no bsp folder (<boot>/<boot>_bsp/...)."""
        parts = os.path.relpath(libsrc, pdir).split(os.sep)
        return parts[parts.index("bsp") - 1] if "bsp" in parts else parts[1]

    def bsp_components(self, name: str, pdir: str):
        """WARN for each Classic BSP component whose name (version stripped) has no folder in the same domain of the
        built Unified BSP: the app can then fail to find its headers. Domains Unified did not build are not compared."""
        have: dict[str, set] = {}
        for u in glob.glob(os.path.join(self.uws, name, "**", "libsrc"), recursive=True):
            if "build" not in os.path.relpath(u, self.uws).split(os.sep):
                have.setdefault(self.bsp_domain(u, os.path.join(self.uws, name)), set()).update(
                    bsp_edits.split_version(f)[0] for f in os.listdir(u))
        for libsrc in bsp_edits.classic_libsrc_dirs(pdir):
            dom = self.bsp_domain(libsrc, pdir)
            for folder in sorted(os.listdir(libsrc)):
                comp = bsp_edits.split_version(folder)[0]
                # generic and cpu_* are placeholders Unified does not generate; a component absent from the reference is
                # user-added, which the generator already reports
                if (dom in have and comp not in have[dom] and os.path.isdir(os.path.join(libsrc, folder, "src"))
                        and comp != "generic" and not comp.startswith("cpu")
                        and not (self.esw and not bsp_edits.find_component(self.esw, folder))):
                    self.rows.append(("WARN", "BSP component", f"{dom}/{folder}", f"missing from the Unified BSP: {comp}; "
                                      "headers from it will not be found; add the driver via a local software repository "
                                      "or regenerate the XSA"))

    def bsp_sources(self, name: str, pdir: str):
        for libsrc in bsp_edits.classic_libsrc_dirs(pdir):
            for it in bsp_edits.detect(libsrc, self.esw):  # main() checked the reference release
                comp = it["comp"]
                if self.suspect:
                    if bsp_edits.edited_files(it):
                        self.rows.append(("SKIP", "BSP source edit", it["folder"], "not migrated: the reference embeddedsw looks "
                                          "like a different build than the one that created the workspace"))
                    continue
                targets = glob.glob(os.path.join(self.uws, name, "**", "libsrc", comp, "src"), recursive=True)
                for rel, cpath, opath in it["modified"]:
                    want = [l.strip() for op in difflib.ndiff(bsp_edits._read(opath), bsp_edits._read(cpath))
                            if op.startswith("+ ") for l in [op[2:]] if l.strip()]
                    files = [f for t in targets for f in glob.glob(os.path.join(t, "**", os.path.basename(rel)), recursive=True)]
                    have = {l.strip() for f in files for l in bsp_edits._read(f)}
                    missing = [w for w in want if w not in have]
                    self.add(bool(files) and not missing, "BSP source edit", f"{it['folder']}/{rel}",
                             f"{len(want) - len(missing)}/{len(want)} edited lines present" + (f" in {len(files)} BSP(s)" if files else "; file not found"))
                for rel, _ in it["added"]:
                    found = any(os.path.isfile(os.path.join(t, rel)) for t in targets)
                    self.add(found, "BSP added file", f"{it['folder']}/{rel}")

    def app(self, name: str, natures: set):
        csrc, usrc = os.path.join(self.cws, name, "src"), os.path.join(self.uws, name, "src")
        self.add(os.path.isdir(usrc), "application", name)
        if not os.path.isdir(usrc):
            return
        for r, _, fs in os.walk(csrc):
            for f in fs:
                rel = os.path.relpath(os.path.join(r, f), csrc)
                u = os.path.join(usrc, rel)
                self.add(os.path.isfile(u) and filecmp.cmp(os.path.join(r, f), u, shallow=False),
                         "app source", f"{name}/src/{rel}", "" if os.path.isfile(u) else "missing in Unified")
        cfg_path = next((p for p in (os.path.join(self.uws, name, "UserConfig.cmake"), os.path.join(usrc, "UserConfig.cmake"))
                         if os.path.isfile(p)), "")  # Linux apps keep it in the component folder
        ucfg = user_config(cfg_path) if os.path.isfile(cfg_path) else {}
        base = os.path.dirname(cfg_path) if cfg_path else usrc
        for key, val in self.mig.cproject_settings(name, os.path.join(self.cws, name), classic2unified.CC_NATURE in natures):
            got = ucfg.get(key, [])
            if key in classic2unified.PATH_KEYS and isinstance(val, list):
                ok = norm_path_values(val, base) <= norm_path_values(got, base)
            elif key in classic2unified.PATH_KEYS:
                ok = norm_path_item(str(val), base) == norm_path_item(" ".join(got), base) if got else not str(val)
            else:
                ok = set(val) <= set(got) if isinstance(val, list) else " ".join(got) == str(val)
            self.add(ok, "app setting", f"{name} {key}", f"Classic {val!r} / Unified {got!r}")

    def run(self) -> int:
        for name, kind, natures in self.mig.projects():
            if kind == "platform":
                self.platform(name)
            elif kind == "app":
                prj = self.mig.read_prj(os.path.join(self.cws, name), name)
                if prj is None or not self.mig.firmware_app(prj):  # boot-firmware apps are not migrated
                    self.app(name, natures)
        fails = [r for r in self.rows if r[0] == "FAIL"]
        skips = [r for r in self.rows if r[0] == "SKIP"]
        warns = [r for r in self.rows if r[0] == "WARN"]
        npass = len(self.rows) - len(fails) - len(skips) - len(warns)
        report = ["# Migration verification report", "",
                  f"Classic: `{self.cws}`  ", f"Unified: `{self.uws}`", "",
                  f"**Result: {'PASS' if not fails else 'FAIL'}** - {npass} passed, {len(fails)} failed, {len(warns)} warnings, {len(skips)} skipped"
                  + ("" if self.esw else " (BSP source edits not checked: no --classic-embeddedsw)"), "",
                  "Not covered by this check: system projects, Linux and boot domains, library projects. Expected values "
                  "come from the generator's own mapping (classic2unified.py).", "",
                  "| Status | Area | Item | Detail |", "|---|---|---|---|"]
        report += [f"| {s} | {a} | {i} | {d} |" for s, a, i, d in sorted(self.rows, key=lambda r: ("FAIL", "WARN").index(r[0]) if r[0] in ("FAIL", "WARN") else 2)]
        with open(os.path.join(self.uws, "verification_report.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
        for s, a, i, d in fails + warns:
            print(f"{s}: {a}: {i} {d}")
        print(f"{'PASS' if not fails else 'FAIL'}: {npass} passed, {len(fails)} failed, "
              f"{len(warns)} warnings, {len(skips)} skipped; "
              f"report: {self.uws}/verification_report.md")
        return 1 if fails else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("classic_ws")
    ap.add_argument("unified_ws")
    ap.add_argument("--classic-embeddedsw", metavar="DIR")
    ap.add_argument("--config", help="Classic build configuration (default: the one the migration was generated with)")
    a = ap.parse_args(argv)
    cws = os.path.abspath(a.classic_ws)
    esw = os.path.abspath(a.classic_embeddedsw) if a.classic_embeddedsw else None
    if esw:
        libsrcs = [l for d in os.listdir(cws) if os.path.isfile(os.path.join(cws, d, "platform.spr"))
                   for l in bsp_edits.classic_libsrc_dirs(os.path.join(cws, d))]
        release, _ = bsp_edits.classic_release(cws, libsrcs)
        ref = bsp_edits.reference_release(esw)
        if libsrcs and ref != release:
            print(f"ERROR: --classic-embeddedsw {esw} " + (
                f"is release {ref}, but the workspace is Classic {release}" if ref else
                "does not contain a versioned lib/bsp/standalone_vX_Y of a supported Classic release, so it is not an "
                "install's data/embeddedsw") + f"; pass the data/embeddedsw of the Classic {release} install that created "
                "the workspace")
            return 2
    uws = os.path.abspath(a.unified_ws)
    recorded = recorded_config(os.path.join(uws, "migration_notes.md"))
    if a.config and recorded and a.config != recorded:
        print(f"ERROR: --config {a.config} disagrees with the configuration the migration was generated with "
              f"({recorded}); omit --config or pass --config {recorded}")
        return 2
    config = a.config or recorded or "Debug"
    print(f"comparing Classic configuration {config}")
    print("PASS covers platforms, user domains, BSP libraries/parameters/source edits and apps; not system projects, "
          "Linux or boot domains, or library projects")
    return Verifier(cws, uws, esw, config).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
