#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

"""Diff the spreadsheet's post-square lane maps against maps measured in x86sim.

    python3 read_spreadsheet.py mac16_int8xint8.xlsx mac8_int8xint8.xlsx --json sheet.json
    make -C probe run
    python3 compare_maps.py sheet.json probe/probe.log

Only the probe cases named `*_stored` are compared automatically: those are the
parameter sets the workbooks were last saved with, so Excel's cached tables and
the probe describe the same configuration. Other probe cases are printed for
manual comparison against the int8xint8 reference design's README tables.
"""
import json
import re
import sys

# probe case name -> (workbook suffix, sheet)
STORED = {
    "mac16_xbuff_64_stored":  ("mac16_int8xint8.xlsx", "xbuff_64"),
    "mac16_xbuff_128_stored": ("mac16_int8xint8.xlsx", "xbuff_128"),
    "mac8_xbuff_64_stored":   ("mac8_int8xint8.xlsx", "xbuff_64"),
    "mac8_xbuff_128_stored":  ("mac8_int8xint8.xlsx", "xbuff_128"),
    "mac16_zbuff_32_stored":  ("mac16_int8xint8.xlsx", "zbuff_32"),
    "mac8_zbuff_32_stored":   ("mac8_int8xint8.xlsx", "zbuff_32"),
}

BEGIN = re.compile(r"^BEGIN (XMAP|ZMAP) (\S+) lanes=(\d+) cols=(\d+)")
ROW = re.compile(r"^ROW\s+(\d+)\s*:(.*)$")


def parse_probe(path):
    cases, cur = {}, None
    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        m = BEGIN.match(line)
        if m:
            cur = {"kind": m.group(1), "name": m.group(2),
                   "lanes": int(m.group(3)), "cols": int(m.group(4)), "rows": {}}
            continue
        if cur is not None:
            m = ROW.match(line)
            if m:
                cur["rows"][int(m.group(1))] = [int(v) for v in m.group(2).split()]
                continue
            if line.startswith("END"):
                cases[cur["name"]] = {
                    "lanes": cur["lanes"], "cols": cur["cols"],
                    "rows": [cur["rows"][r] for r in sorted(cur["rows"])]}
                cur = None
    return cases


def shape_ok(rows, lanes, cols):
    return len(rows) == lanes and all(len(row) == cols for row in rows)


def main():
    sheet_json, probe_log = sys.argv[1], sys.argv[2]
    sheets = json.load(open(sheet_json))
    probe = parse_probe(probe_log)

    by_wb = {}
    for path, d in sheets.items():
        by_wb[path.rsplit("/", 1)[-1]] = d

    total_bad = 0
    failed = []
    print("=" * 74)
    print("SPREADSHEET (Excel cached)  vs  X86SIM (measured)")
    print("=" * 74)

    for case, (wb, sheet) in STORED.items():
        if case not in probe:
            print(f"\n[FAIL] {case}: not present in probe log")
            failed.append(case)
            continue
        if wb not in by_wb:
            print(f"\n[FAIL] {case}: {wb} not in sheet json")
            failed.append(case)
            continue
        ws = by_wb[wb][sheet]
        exp = ws["table"]
        got = probe[case]["rows"]
        if not (shape_ok(exp, ws["lanes"], ws["cols"])
                and shape_ok(got, ws["lanes"], ws["cols"])
                and (probe[case]["lanes"], probe[case]["cols"]) == (ws["lanes"], ws["cols"])):
            print(f"\n[FAIL] {case}: shape mismatch, expected {ws['lanes']}x{ws['cols']}")
            failed.append(case)
            continue
        side = ws["side"]
        p = ws["params"]
        params = " ".join(f"{side}{n}={p[side + n]}"
                          for n in ("start", "offsets", "step", "square"))

        bad = []
        for r, (er, gr) in enumerate(zip(exp, got)):
            for c, (e, g) in enumerate(zip(er, gr)):
                if e != g:
                    bad.append((r, c, e, g))

        status = "MATCH" if not bad else f"MISMATCH ({len(bad)} cells)"
        print(f"\n--- {case}  [{wb}:{sheet}]\n    {params}\n    {status}")
        for r, c, e, g in bad[:16]:
            print(f"      lane {r:2d} col {c:2d} : sheet {side}[{e}]  "
                  f"x86sim {side}[{g}]")
        if len(bad) > 16:
            print(f"      ... {len(bad) - 16} more")
        total_bad += len(bad)

    other = [k for k in probe if k not in STORED]
    if other:
        print("\n" + "=" * 74)
        print("Measured, no cached spreadsheet counterpart (compare to README):")
        for k in other:
            print(f"\n--- {k}")
            for r, row in enumerate(probe[k]["rows"]):
                print(f"    lane[{r:2d}] " + " ".join(f"{v:4d}" for v in row))

    print("\n" + "=" * 74)
    print(f"TOTAL MISMATCHED CELLS: {total_bad}")
    print(f"MISSING OR MALFORMED CASES: {len(failed)}")
    return 1 if total_bad or failed else 0


if __name__ == "__main__":
    sys.exit(main())
