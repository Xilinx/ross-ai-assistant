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
import shlex
import sys

if sys.version_info < (3, 10):
    sys.exit("Run with the Python bundled with Vitis: vitis -s verify_migration.py ...")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bsp_edits  # noqa: E402
import classic2unified  # noqa: E402

import yaml  # bundled with Vitis


def user_config(path: str) -> dict[str, list[str]]:
    """set(KEY v1 v2 ...) blocks of a UserConfig.cmake -> {KEY: [values]}."""
    out, text = {}, open(path, encoding="utf-8").read()
    i = 0
    while (i := text.find("set(", i)) != -1:
        j, depth = i + 4, 1
        while j < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            j += 1
        parts = shlex.split(text[i + 4:j - 1].replace("\n", " "), posix=True)
        if parts:
            out[parts[0]] = parts[1:]
        i = j
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
        self.rows: list[tuple[str, str, str, str]] = []  # status, area, item, detail

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
            if self.mig.os_name(dom) == "linux":  # Linux domains have no BSP; the platform's vitis-comp.json lists them
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
                    if pname is None and not any(n.lower().endswith("_" + key.lower()) or n == key
                                                 for blk in (bsp.get(section) or {}).values() for n in (blk or {})):
                        # No Unified equivalent: migrate.py reports it UNSUPPORTED and does not set it.
                        self.rows.append(("SKIP", "BSP setting", f"{dname} {owner}.{key}", "no Unified equivalent"))
                        continue
                    self.add(uval == str(val).strip(), "BSP setting", f"{dname} {owner}.{key}",
                             f"Classic '{val}' / Unified {pname or '(missing)'} '{uval}'")
        if self.esw:
            self.bsp_sources(name, pdir)

    def bsp_sources(self, name: str, pdir: str):
        for libsrc in bsp_edits.classic_libsrc_dirs(pdir):
            for it in bsp_edits.detect(libsrc, self.esw, versionless=True):  # main() checked the reference release
                comp = it["comp"]
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
        for key, val in self.mig.cproject_settings(name, os.path.join(self.cws, name), classic2unified.CC_NATURE in natures):
            got = ucfg.get(key, [])
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
        report = ["# Migration verification report", "",
                  f"Classic: `{self.cws}`  ", f"Unified: `{self.uws}`", "",
                  f"**Result: {'PASS' if not fails else 'FAIL'}** - {len(self.rows) - len(fails) - len(skips)} passed, {len(fails)} failed, {len(skips)} skipped"
                  + ("" if self.esw else " (BSP source edits not checked: no --classic-embeddedsw)"), "",
                  "Not covered by this check: system projects, Linux and boot domains, library projects. Expected values "
                  "come from the generator's own mapping (classic2unified.py).", "",
                  "| Status | Area | Item | Detail |", "|---|---|---|---|"]
        report += [f"| {s} | {a} | {i} | {d} |" for s, a, i, d in sorted(self.rows, key=lambda r: r[0] != "FAIL")]
        with open(os.path.join(self.uws, "verification_report.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(report) + "\n")
        for s, a, i, d in fails:
            print(f"FAIL: {a}: {i} {d}")
        print(f"{'PASS' if not fails else 'FAIL'}: {len(self.rows) - len(fails) - len(skips)} passed, {len(fails)} failed, "
              f"{len(skips)} skipped (no Unified equivalent); "
              f"report: {self.uws}/verification_report.md")
        return 1 if fails else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("classic_ws")
    ap.add_argument("unified_ws")
    ap.add_argument("--classic-embeddedsw", metavar="DIR")
    ap.add_argument("--config", default="Debug")
    a = ap.parse_args(argv)
    cws = os.path.abspath(a.classic_ws)
    esw = os.path.abspath(a.classic_embeddedsw) if a.classic_embeddedsw else None
    if esw:
        libsrcs = [l for d in os.listdir(cws) if os.path.isfile(os.path.join(cws, d, "platform.spr"))
                   for l in bsp_edits.classic_libsrc_dirs(os.path.join(cws, d))]
        release, _ = bsp_edits.classic_release(cws, libsrcs)
        ref = bsp_edits.reference_release(esw)
        if libsrcs and ref != release:
            print(f"ERROR: --classic-embeddedsw is release {ref or 'unknown'}, but the workspace is Classic {release}; "
                  f"pass the embeddedsw of release {release}")
            return 2
    print("PASS covers platforms, user domains, BSP libraries/parameters/source edits and apps; not system projects, "
          "Linux or boot domains, or library projects")
    return Verifier(cws, os.path.abspath(a.unified_ws), esw, a.config).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
