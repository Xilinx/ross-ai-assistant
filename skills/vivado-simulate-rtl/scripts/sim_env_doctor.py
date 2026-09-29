#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check the host prerequisites a Vivado simulation depends on.

Read-only. Reports each check as pass, warn, or fail with the evidence used,
and exits nonzero when a hard prerequisite is missing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

XSIM_TOOLS = ("xvlog", "xvhdl", "xelab", "xsim", "xsc", "xcrg")
KERNEL_LIBS = ("libxv_simulator_kernel.so", "librdi_simulator_kernel.so")
LICENSE_VARS = {
    "xsim": (),
    "questa": ("MGLS_LICENSE_FILE", "LM_LICENSE_FILE", "SALT_LICENSE_SERVER"),
    "modelsim": ("MGLS_LICENSE_FILE", "LM_LICENSE_FILE", "SALT_LICENSE_SERVER"),
    "vcs": ("SNPSLMD_LICENSE_FILE", "LM_LICENSE_FILE"),
    "xcelium": ("CDS_LIC_FILE", "CDS_LIC_WAN", "LM_LICENSE_FILE"),
    "riviera": ("ALDEC_LICENSE_FILE", "LM_LICENSE_FILE"),
    "activehdl": ("ALDEC_LICENSE_FILE", "LM_LICENSE_FILE"),
}
EXPECTED_CLIB_PREFIXES = ("unisim", "simprim", "secureip", "xpm", "unifast")


def check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def run(
    cmd: list[str], timeout: int = 60, env: dict[str, str] | None = None
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout or ""


def resolve_vivado(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    found = shutil.which("vivado")
    return Path(found) if found else None


def check_vivado(vivado: Path | None) -> list[dict[str, str]]:
    if vivado is None:
        return [
            check(
                "vivado",
                "fail",
                "vivado not found on PATH; pass --vivado <path-to-vivado>",
            )
        ]
    results = [check("vivado", "pass", f"found {vivado}")]
    bin_dir = vivado.parent
    missing = [tool for tool in XSIM_TOOLS if not (bin_dir / tool).exists()]
    if missing:
        results.append(
            check("xsim-tools", "fail", f"missing in {bin_dir}: {', '.join(missing)}")
        )
    else:
        results.append(check("xsim-tools", "pass", f"all present in {bin_dir}"))

    code, out = run([str(bin_dir / "xvlog"), "-version"])
    banner = next((line for line in out.splitlines() if line.strip()), "")
    if code == 0 and banner:
        results.append(check("xsim-version", "pass", banner.strip()))
    else:
        results.append(
            check("xsim-version", "warn", f"could not read version banner: {out[:200]}")
        )
    return results


def check_kernel_libraries(vivado: Path | None) -> list[dict[str, str]]:
    if vivado is None:
        return []
    lib_dir = vivado.parent.parent / "lib" / "lnx64.o"
    if not lib_dir.is_dir():
        return [check("kernel-libs", "warn", f"library directory not found: {lib_dir}")]
    kernel = next((lib_dir / name for name in KERNEL_LIBS if (lib_dir / name).is_file()), None)
    if kernel is None:
        return [check("kernel-libs", "warn", f"simulator kernel not found in {lib_dir}")]
    if shutil.which("ldd") is None:
        return [check("kernel-libs", "warn", "ldd unavailable; cannot resolve dependencies")]
    # XSim prepends its own lib directory at launch, so resolve siblings the
    # same way instead of reporting them as missing.
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        filter(None, [str(lib_dir), env.get("LD_LIBRARY_PATH", "")])
    )
    _, out = run(["ldd", str(kernel)], env=env)
    unresolved = [line.strip() for line in out.splitlines() if "not found" in line]
    if unresolved:
        return [
            check(
                "kernel-libs",
                "fail",
                f"{kernel.name} has unresolved dependencies: {'; '.join(unresolved)}",
            )
        ]
    return [check("kernel-libs", "pass", f"{kernel.name} dependencies resolve")]


def check_library_path() -> list[dict[str, str]]:
    raw = os.environ.get("LD_LIBRARY_PATH", "")
    if not raw:
        return [check("ld-library-path", "pass", "not set")]
    missing = [entry for entry in raw.split(os.pathsep) if entry and not Path(entry).is_dir()]
    if missing:
        return [
            check(
                "ld-library-path",
                "warn",
                f"entries do not exist: {', '.join(missing)}",
            )
        ]
    return [check("ld-library-path", "pass", "all entries exist")]


def check_toolchain() -> list[dict[str, str]]:
    results = []
    for tool in ("gcc", "g++"):
        path = shutil.which(tool)
        if path is None:
            results.append(
                check(f"toolchain-{tool}", "warn", f"{tool} not on PATH; DPI and xsc will fail")
            )
            continue
        _, out = run([path, "--version"], timeout=30)
        first = next((line for line in out.splitlines() if line.strip()), path)
        results.append(check(f"toolchain-{tool}", "pass", first.strip()))
    return results


def check_shell() -> list[dict[str, str]]:
    sh = Path("/bin/sh")
    if not sh.exists():
        return [check("bin-sh", "warn", "/bin/sh not present")]
    target = os.path.realpath(sh)
    if "dash" in target:
        return [
            check(
                "bin-sh",
                "warn",
                f"/bin/sh resolves to {target}; Synopsys wrappers using '#!/bin/sh -h' will fail",
            )
        ]
    return [check("bin-sh", "pass", f"/bin/sh resolves to {target}")]


def check_clibs(clibs: str | None) -> list[dict[str, str]]:
    if not clibs:
        return [check("compiled-libraries", "warn", "no --clibs directory given; not checked")]
    root = Path(clibs)
    if not root.is_dir():
        return [check("compiled-libraries", "fail", f"directory not found: {root}")]
    entries = [item.name.lower() for item in root.iterdir()]
    if not entries:
        return [check("compiled-libraries", "fail", f"directory is empty: {root}")]
    found = sorted({
        prefix
        for prefix in EXPECTED_CLIB_PREFIXES
        if any(name.startswith(prefix) for name in entries)
    })
    if not found:
        return [
            check(
                "compiled-libraries",
                "warn",
                f"{root} has no unisim/simprim/secureip/xpm libraries; verify compile_simlib output",
            )
        ]
    return [check("compiled-libraries", "pass", f"{root} contains {', '.join(found)}")]


def check_license(simulator: str) -> list[dict[str, str]]:
    variables = LICENSE_VARS.get(simulator)
    if variables is None:
        return [check("license", "fail", f"unknown simulator id: {simulator}")]
    if not variables:
        return [check("license", "pass", f"{simulator} needs no third-party license variable")]
    present = [name for name in variables if os.environ.get(name)]
    if not present:
        return [
            check(
                "license",
                "fail",
                f"{simulator}: none of {', '.join(variables)} are set",
            )
        ]
    return [check("license", "pass", f"{simulator}: {', '.join(present)} set")]


def check_workdir(workdir: str) -> list[dict[str, str]]:
    path = Path(workdir)
    results = []
    if not path.is_dir():
        return [check("workdir", "fail", f"not a directory: {path}")]
    results.append(
        check(
            "workdir",
            "pass" if os.access(path, os.W_OK) else "fail",
            f"{path} writable={os.access(path, os.W_OK)}",
        )
    )
    free_gb = shutil.disk_usage(path).free / 1024**3
    results.append(
        check(
            "disk-space",
            "pass" if free_gb >= 5 else "warn",
            f"{free_gb:.1f} GiB free at {path}",
        )
    )
    return results


def check_display() -> list[dict[str, str]]:
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if display:
        return [check("display", "pass", f"display available: {display}")]
    return [check("display", "warn", "no display; batch simulation is fine, GUI is not")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vivado", default=None, help="path to the vivado executable")
    parser.add_argument("--clibs", default=None, help="compiled simulation library directory")
    parser.add_argument(
        "--simulator",
        default="xsim",
        choices=sorted(LICENSE_VARS),
        help="backend whose license configuration should be checked",
    )
    parser.add_argument("--workdir", default=".", help="directory the simulation will write to")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    vivado = resolve_vivado(args.vivado)
    results: list[dict[str, str]] = []
    results += check_vivado(vivado)
    results += check_kernel_libraries(vivado)
    results += check_library_path()
    results += check_toolchain()
    results += check_shell()
    results += check_clibs(args.clibs)
    results += check_license(args.simulator)
    results += check_workdir(args.workdir)
    results += check_display()

    failures = [item for item in results if item["status"] == "fail"]
    if args.json:
        print(json.dumps({"results": results, "failures": len(failures)}, indent=2))
    else:
        for item in results:
            print(f"[{item['status']:>4}] {item['check']}: {item['detail']}")
        print(f"\n{len(failures)} hard failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
