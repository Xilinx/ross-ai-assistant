# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Carry user edits of Classic BSP sources (drivers, libraries, OS) into a Unified local software repository.

Stdlib only. Used by classic2unified.py (to build the repo) and verify_migration.py (to check it).

Detection : diff each Classic BSP component (<plat>/.../libsrc/<comp>_vX_Y/src) against the same <comp>_vX_Y in the
            reference embeddedsw (a 2023.2-2024.2 install's data/embeddedsw, or the embeddedsw git tree at the workspace's
            xilinx_v<release> tag). A component without an exact match is reported as not checked, never diffed.
Rebase    : apply each changed hunk to the new release's version of the component, located by its context lines.
Repo      : <unified_ws>/sw_repo/<category>/<comp>/ (no version suffix, so it wins over the install).
"""
import difflib
import filecmp
import os
import re
import shutil
import stat
import subprocess

CATEGORIES = ["XilinxProcessorIPLib/drivers", "lib/sw_services", "ThirdParty/sw_services", "lib/bsp", "ThirdParty/bsp"]
BUILD_OUTPUTS = (".o", ".d", ".a")
# Regenerated from the hardware design / BSP settings in Classic and in Unified; edits there are not portable.
GENERATED = re.compile(r"(_g\.c|Config\.cmake|_supplylist\.h)$|^xparameters\.h$|^Makefile_dep\w*$|^(bspconfig\.h|xmem_config\.h|xtimer_config\.h|"
                       r"config\.make|inbyte\.c|outbyte\.c|FreeRTOSConfig\.h|pm_cfg_obj\.c)$")
BUILD_FILES = {"CMakeLists.txt", "Makefile", "makefile"}
CONTEXT = 3
SUPPORTED_RELEASES = ("2023.2", "2024.1", "2024.2")
# Newest standalone BSP of each supported Classic release (a workspace uses its own release's newest).
STANDALONE_RELEASE = {"standalone_v9_0": "2023.2", "standalone_v9_1": "2024.1", "standalone_v9_2": "2024.2"}


def classic_release(cws: str, libsrcs: list[str]) -> tuple[str | None, str]:
    """(release, evidence) of a Classic workspace: .metadata/version.ini, else its standalone BSP version."""
    ini = os.path.join(cws, ".metadata", "version.ini")
    if os.path.isfile(ini):
        with open(ini, encoding="utf-8", errors="replace") as fh:
            m = re.search(r"com\.xilinx\.tools\.version=(\d{4}\.\d)", fh.read())
        if m:
            return m.group(1), f"{ini} (com.xilinx.tools.version)"
    for libsrc in libsrcs:
        for folder in os.listdir(libsrc):
            if folder.startswith("standalone_v"):
                return STANDALONE_RELEASE.get(folder, f"unknown ({folder})"), f"BSP {os.path.join(libsrc, folder)}"
    return None, "no .metadata/version.ini and no standalone BSP"


def reference_release(esw: str) -> str | None:
    """Release of an embeddedsw tree: install path <rel>/data/embeddedsw, or git tag xilinx_v<rel>."""
    m = re.search(r"[/\\](\d{4}\.\d)[/\\](?:Vitis[/\\])?data[/\\]embeddedsw$", os.path.normpath(esw))
    if m:
        return m.group(1)
    try:
        tag = subprocess.run(["git", "-C", esw, "describe", "--tags", "--exact-match"], capture_output=True,
                             text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.fullmatch(r"xilinx_v(\d{4}\.\d)", tag)
    return m.group(1) if m else None


def split_version(folder: str) -> tuple[str, tuple]:
    m = re.match(r"(.+)_v(\d+)_(\d+)$", folder)
    return (m.group(1), (int(m.group(2)), int(m.group(3)))) if m else (folder, ())


def find_component(root: str, folder: str, newest: bool = False, versionless: bool = False) -> tuple[str, str] | None:
    """(category, component dir) for <comp>_vX_Y (exact) or the newest version with SDT metadata (newest=True).

    versionless: also accept the versionless git layout (<comp>/); only valid for a tree of the workspace's release.
    """
    comp, _ = split_version(folder)
    for cat in CATEGORIES:
        base = os.path.join(root, cat)
        if not os.path.isdir(base):
            continue
        if not newest:
            for name in (folder, comp) if versionless else (folder,):  # versioned install layout, then git layout
                if os.path.isdir(os.path.join(base, name, "src")):
                    return cat, os.path.join(base, name)
            continue
        cands = [d for d in os.listdir(base) if split_version(d)[0] == comp
                 and os.path.isfile(os.path.join(base, d, "data", comp + ".yaml"))]
        if cands:
            return cat, os.path.join(base, max(cands, key=lambda d: split_version(d)[1]))
    return None


def _index(src: str) -> dict[str, list[str]]:
    by_name: dict[str, list[str]] = {}
    for r, _, fs in os.walk(src):
        for f in fs:
            by_name.setdefault(f, []).append(os.path.join(r, f))
    return by_name


def _candidates(src: str, rel: str, by_name: dict) -> list[str]:
    """Classic flattens per-architecture source folders (standalone, xilpm, ...); match by file name as fallback."""
    direct = os.path.join(src, rel)
    if os.path.isfile(direct):
        return [direct]
    name = os.path.basename(rel)
    stem, ext = os.path.splitext(name)
    # Classic's R5 BSP is generated with the _r5 suffix dropped (mpu_r5.c -> mpu.c, xil_mpu_r5.{c,h} -> xil_mpu.{c,h}; identical
    # content). Only the _r5 form is verified (2024.2 Versal); without this they were reported as user-added files.
    return by_name.get(name) or by_name.get(stem + "_r5" + ext, [])


_TREE_INDEX: dict[str, dict[str, list[str]]] = {}


def _copied_from_tree(path: str, root: str) -> bool:
    """True if an identical file exists anywhere in the reference tree (Classic copies e.g. standalone headers into
    the FreeRTOS BSP); such a file is not a user addition."""
    if root not in _TREE_INDEX:
        _TREE_INDEX[root] = {}
        for cat in CATEGORIES:
            for r, _, fs in os.walk(os.path.join(root, cat)):
                for f in fs:
                    _TREE_INDEX[root].setdefault(f, []).append(os.path.join(r, f))
    return any(filecmp.cmp(path, c, shallow=False) for c in _TREE_INDEX[root].get(os.path.basename(path), []))


def _closest(path: str, cands: list[str]) -> str | None:
    """Identical candidate if any, else the most similar one."""
    for c in cands:
        if filecmp.cmp(path, c, shallow=False):
            return c
    if not cands:
        return None
    text = "".join(_read(path))
    return max(cands, key=lambda c: difflib.SequenceMatcher(None, text, "".join(_read(c)), autojunk=False).quick_ratio())


def _portable_sources(csrc: str) -> list[str]:
    return [os.path.join(r, f) for r, _, fs in os.walk(csrc) for f in fs
            if not f.endswith(BUILD_OUTPUTS) and not GENERATED.search(f) and f not in BUILD_FILES]


def detect(libsrc: str, orig_root: str, versionless: bool = False) -> list[dict]:
    """Per Classic component with user edits: {folder, comp, category, modified, added, build_files, no_original}.

    Generated files (GENERATED) are skipped: Classic regenerates them too, so edits there do not persist.
    """
    out = []
    for folder in sorted(os.listdir(libsrc)):
        csrc = os.path.join(libsrc, folder, "src")
        if not os.path.isdir(csrc):
            continue
        found = find_component(orig_root, folder, versionless=versionless) if orig_root else None
        item = {"folder": folder, "comp": split_version(folder)[0], "category": found[0] if found else None,
                "modified": [], "added": [], "build_files": [], "no_original": not found}
        if not found:
            if _portable_sources(csrc):  # placeholder components (e.g. generic) have nothing to compare
                out.append(item)
            continue
        osrc = os.path.join(found[1], "src")
        by_name = _index(osrc)
        for r, _, fs in os.walk(csrc):
            for f in fs:
                cpath = os.path.join(r, f)
                rel = os.path.relpath(cpath, csrc)
                if f.endswith(BUILD_OUTPUTS) or GENERATED.search(f):
                    continue
                opath = _closest(cpath, _candidates(osrc, rel, by_name))
                if f in BUILD_FILES:
                    if f != "CMakeLists.txt" and opath and not filecmp.cmp(cpath, opath, shallow=False):
                        item["build_files"].append(rel)
                    continue
                if not opath:
                    if not _copied_from_tree(cpath, orig_root):
                        item["added"].append((rel, cpath))
                elif not filecmp.cmp(cpath, opath, shallow=False):
                    item["modified"].append((rel, cpath, opath))
        if item["modified"] or item["added"] or item["build_files"]:
            out.append(item)
    return out


def _read(path: str) -> list[str]:
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read().splitlines(keepends=True)


def rebase(orig: list[str], classic: list[str], new: list[str]) -> tuple[list[str], int, int]:
    """Apply the orig->classic hunks to new. Returns (patched, applied, failed)."""
    result = list(new)
    applied = failed = 0
    pos = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, orig, classic, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        before, after = orig[i1:i2], classic[j1:j2]
        if not before and i1 == len(orig):  # pure addition at end of file
            if result and not result[-1].endswith("\n"):
                result[-1] += "\n"
            result.extend(after)
            applied += 1
            continue
        placed = False
        for ctx in (CONTEXT, 1, 0):
            pre, post = orig[max(0, i1 - ctx):i1], orig[i2:i2 + ctx]
            needle = pre + before + post
            if not needle:
                break
            hits = [k for k in range(pos, len(result) - len(needle) + 1) if result[k:k + len(needle)] == needle]
            if len(hits) == 1:
                k = hits[0] + len(pre)
                result[k:k + len(before)] = after
                pos = k + len(after)
                applied += 1
                placed = True
                break
            if len(hits) > 1:
                break  # ambiguous; a smaller context will not help
        if not placed:
            failed += 1
    return result, applied, failed


def _add_to_cmake(cmake: str, fname: str) -> bool:
    if not os.path.isfile(cmake):
        return False
    text = open(cmake, encoding="utf-8").read()
    kind = "PROJECT_LIB_HEADERS" if fname.endswith((".h", ".hpp")) else "PROJECT_LIB_SOURCES"
    line = f"collect ({kind} {fname})\n"
    if line in text:
        return True
    if "collector_list" in text:
        text = text.replace("collector_list", line + "collector_list", 1)
    else:
        text += line
    open(cmake, "w", encoding="utf-8").write(text)
    return True


def build_repo(items: list[dict], new_root: str, repo: str, evidence: str) -> list[tuple[str, str, str]]:
    """Create patched new-release components under repo; returns (status, what, detail) rows."""
    rows: list[tuple[str, str, str]] = []
    done: dict[str, str] = {}
    for it in items:
        name = it["folder"]
        if it["no_original"]:
            rows.append(("UNSUPPORTED", f"BSP {name}", f"not checked: the reference embeddedsw has no {name}, so user edits "
                         "cannot be detected; pass the embeddedsw of the workspace's Classic release"))
            continue
        for rel in it["build_files"]:
            rows.append(("UNSUPPORTED", f"BSP {name}/src/{rel}", "Classic Makefile edited; Unified builds with CMake - "
                         f"review the change in {evidence}/{name} and port it to src/CMakeLists.txt"))
        if not (it["modified"] or it["added"]):
            continue
        for rel, cpath, opath in it["modified"]:  # evidence first: nothing is lost even if the rebase fails
            dst = os.path.join(evidence, name, rel + ".patch")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.writelines(difflib.unified_diff(_read(opath), _read(cpath), f"original/{rel}", f"classic/{rel}"))
        for rel, cpath in it["added"]:
            dst = os.path.join(evidence, name, "added", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(cpath, dst)
        target = find_component(new_root, name, newest=True)
        if not target:
            rows.append(("UNSUPPORTED", f"BSP {name}", f"component '{it['comp']}' not in the new release; edits kept in "
                         f"{evidence}/{name} - port them to its replacement"))
            continue
        cat, newdir = target
        comp_dir = os.path.join(repo, cat, it["comp"])
        if it["comp"] in done:
            rows.append(("UNSUPPORTED", f"BSP {name}", f"also edited in {done[it['comp']]}; the local repo is workspace-wide, "
                         "so only the first copy is used - merge the edits by hand"))
            continue
        done[it["comp"]] = name
        if not os.path.isdir(comp_dir):
            shutil.copytree(newdir, comp_dir)
            for r, ds, fs in os.walk(comp_dir):  # install copies are read-only
                for p in [os.path.join(r, x) for x in ds + fs]:
                    os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
        nsrc = os.path.join(comp_dir, "src")
        by_name = _index(nsrc)
        for rel, cpath, opath in it["modified"]:
            npath = _closest(opath, _candidates(nsrc, rel, by_name))
            if not npath:
                rows.append(("UNSUPPORTED", f"BSP {name}/src/{rel}", f"file not in {os.path.basename(newdir)}; edit kept in "
                             f"{evidence}/{name}/{rel}.patch"))
                continue
            patched, ok, bad = rebase(_read(opath), _read(cpath), _read(npath))
            if bad:
                rows.append(("UNSUPPORTED", f"BSP {name}/src/{rel}", f"{bad} of {ok + bad} changes did not apply to "
                             f"{os.path.basename(newdir)} (code changed); file left unpatched - apply "
                             f"{evidence}/{name}/{rel}.patch by hand in {comp_dir}"))
                continue
            with open(npath, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.writelines(patched)
            rows.append(("MIGRATED", f"BSP {name}/src/{rel}", f"{ok} change(s) applied to {os.path.basename(newdir)}"))
        for rel, cpath in it["added"]:
            dst = os.path.join(nsrc, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(cpath, dst)
            listed = _add_to_cmake(os.path.join(os.path.dirname(dst), "CMakeLists.txt"), os.path.basename(rel))
            rows.append(("MIGRATED" if listed else "UNSUPPORTED", f"BSP {name}/src/{rel} (user-added file)",
                         f"copied into {os.path.basename(newdir)}" + ("" if listed else
                         "; no CMakeLists.txt in its folder - add it to the component build by hand")))
    return rows


def classic_libsrc_dirs(platform_dir: str) -> list[str]:
    """All BSP libsrc folders of a Classic platform project (user and boot domains), excluding export/."""
    dirs = []
    for r, ds, _ in os.walk(platform_dir):
        if os.path.relpath(r, platform_dir).split(os.sep)[0] in ("export", "hw", "resources", "logs"):
            ds[:] = []
            continue
        if os.path.basename(r) == "libsrc":
            dirs.append(r)
            ds[:] = []
    return sorted(dirs)
