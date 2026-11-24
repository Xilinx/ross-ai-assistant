# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write FUNCTIONS.md, the complete helper index, into each Vivado skill.

Usage: build_function_index.py [--check]

Tcl entries are exactly what `ipcfg::help` prints for each public proc (call
shape, purpose, ok/fail result tokens); Python entries are each script's module
docstring summary and its `--help`. With --check, exit 1 if any file is stale.
"""

import argparse
import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[3]
IPCFG = SKILLS / "vivado-ip-configurator/lib/ipcfg/ipcfg.tcl"
IPIASM = SKILLS / "vivado-ipi-assembler/lib/ipiasm.tcl"

SKILL_SCRIPTS = {
    "vivado-spec-generator": ["scripts"],
    "vivado-ip-configurator": [],
    "vivado-ipi-assembler": ["scripts"],
}
NAMESPACE = {"vivado-ip-configurator": "ipcfg", "vivado-ipi-assembler": "ipiasm"}

HEADER = """# Function index: {skill}

Generated from the code by `vivado-ipi-assembler/tests/support/build_function_index.py`;
do not edit by hand. This is the complete list of what this skill can call. Read it
instead of `lib/*.tcl` or `scripts/*.py`: the source adds nothing a caller needs.
"""


def tcl_entries(namespace):
    script = textwrap.dedent(f"""
        source {{{IPCFG}}}
        source {{{IPIASM}}}
        foreach p [lsort [info procs ::{namespace}::*]] {{
            if {{[string match _* [namespace tail $p]]}} continue
            puts "@@[string trimleft $p :]"
            puts [ipcfg::help [string trimleft $p :]]
        }}
    """)
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120, check=True)
    body = result.stdout.split("@@", 1)[1] if "@@" in result.stdout else ""
    entries = []
    for chunk in ("@@" + body).split("@@")[1:]:
        lines = chunk.rstrip("\n").split("\n")[1:]
        call, rest = lines[0], [line.strip() for line in lines[1:] if line.strip()]
        entries.append((call, rest))
    return entries


def api_steps():
    script = f"source {{{IPCFG}}}\nsource {{{IPIASM}}}\nputs @@[ipiasm::api]\n"
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120, check=True)
    return result.stdout.split("@@", 1)[1].rstrip()


def python_entries(folder):
    entries = []
    for path in sorted(folder.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = (ast.get_docstring(tree) or "").strip().split("\n\n")[0]
        usage = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, text=True,
                               timeout=60, cwd=folder)
        text = usage.stdout.strip() if usage.returncode == 0 else ""
        entries.append((path.name, " ".join(doc.split()), text))
    return entries


def render(skill):
    out = [HEADER.format(skill=skill)]
    namespace = NAMESPACE.get(skill)
    if namespace == "ipiasm":
        out += ["## Build flow (`ipiasm::api`)", "", "```text", api_steps(), "```", ""]
    if namespace:
        entries = tcl_entries(namespace)
        out += [f"## Tcl helpers: `{namespace}::` ({len(entries)})", "",
                "Load the library in the Vivado session first. `ipcfg::help <word>` prints the same entry live.", ""]
        if namespace == "ipiasm":
            out += ["`ipcfg::` helpers are in `../vivado-ip-configurator/FUNCTIONS.md`.", ""]
        for call, rest in entries:
            out.append(f"- `{call}`")
            out += [f"  {line}" for line in rest]
        out.append("")
    for folder in SKILL_SCRIPTS[skill]:
        entries = python_entries(SKILLS / skill / folder)
        out += [f"## Python scripts: `{folder}/` ({len(entries)})", "",
                "Run with the configurator's `lib/ipcfg/vivado_python.sh`; `--help` prints the same usage.", ""]
        for name, doc, usage in entries:
            out += [f"### {name}", ""]
            if doc:
                out += [doc, ""]
            if usage:
                out += ["```text", usage, "```", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if any FUNCTIONS.md is stale")
    args = parser.parse_args(argv)
    stale = []
    for skill in SKILL_SCRIPTS:
        target = SKILLS / skill / "FUNCTIONS.md"
        text = render(skill)
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(str(target))
        else:
            target.write_text(text, encoding="utf-8")
            print(f"FUNCTIONS:WROTE {target} bytes={len(text.encode())}")
    if stale:
        print("FUNCTIONS:STALE " + " ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
