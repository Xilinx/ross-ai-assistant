#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

"""Read address parameters and post-square lane maps from the baseline workbooks.

Reads Excel's *cached* values. openpyxl cannot evaluate formulas, and these
workbooks use Excel-365-only functions (LET/TOCOL/UNIQUE) that no Python
formula engine supports, so a table read back here reflects the parameters the
workbook was last saved with in Excel -- not any parameters written since.

Usage:
    python3 read_spreadsheet.py <workbook.xlsx> [...] [--json out.json]
"""
import json
import sys

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

# family -> sheet -> (param cells in xstart/xoffsets/xstep/xsquare order,
#                     post-square table range, lanes, cols)
GEOMETRY = {
    "mac16": {
        "xbuff_64":  (("M9", "M10", "M11", "M12"), "P11:W26", 16, 8),
        "xbuff_128": (("M13", "M14", "M15", "M16"), "P15:W30", 16, 8),
        "zbuff_32":  (("N8", "N9", "N10", "N11"), "Q11:X26", 16, 8),
    },
    "mac8": {
        "xbuff_64":  (("T9", "T10", "T11", "T12"), "W11:AL18", 8, 16),
        "xbuff_128": (("T13", "T14", "T15", "T16"), "W15:AL22", 8, 16),
        "zbuff_32":  (("U9", "U10", "U11", "U12"), "X11:AM18", 8, 16),
    },
}

PARAM_NAMES = ("start", "offsets", "step", "square")


def family_of(path):
    name = path.rsplit("/", 1)[-1]
    return "mac8" if name.startswith("mac8") else "mac16"


def cell_index(value):
    """Table cells hold labels like 'x12' / 'z3'; return the integer index."""
    if value is None:
        return None
    s = str(value).strip()
    if s and s[0] in "xz":
        s = s[1:]
    try:
        return int(s)
    except ValueError:
        return None


def read_table(ws, rng, lanes, cols):
    min_c, min_r, max_c, max_r = range_boundaries(rng)
    got_rows, got_cols = max_r - min_r + 1, max_c - min_c + 1
    if (got_rows, got_cols) != (lanes, cols):
        raise ValueError(f"{ws.title}: range {rng} is {got_rows}x{got_cols}, "
                         f"expected {lanes}x{cols}")
    return [[cell_index(ws.cell(row=r, column=c).value)
             for c in range(min_c, max_c + 1)]
            for r in range(min_r, max_r + 1)]


def read_workbook(path):
    fam = family_of(path)
    wb_v = load_workbook(path, data_only=True)
    wb_f = load_workbook(path, data_only=False)
    out = {}
    for sheet, (cells, rng, lanes, cols) in GEOMETRY[fam].items():
        wsv, wsf = wb_v[sheet], wb_f[sheet]
        side = "z" if sheet.startswith("zbuff") else "x"
        params = {}
        for name, coord in zip(PARAM_NAMES, cells):
            raw = wsf[coord].value
            params[side + name] = raw
            params[side + name + "_cell"] = coord
        out[sheet] = {
            "family": fam, "side": side, "lanes": lanes, "cols": cols,
            "params": params, "table_range": rng,
            "table": read_table(wsv, rng, lanes, cols),
        }
    return out


def fmt(v):
    return v if isinstance(v, str) else str(v)


def main():
    argv = sys.argv[1:]
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        json_out = argv[i + 1]
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]

    result = {}
    for path in args:
        result[path] = read_workbook(path)
        print(f"\n########## {path}")
        for sheet, d in result[path].items():
            p = d["params"]
            side = d["side"]
            print(f"\n--- {sheet}  ({d['lanes']} lanes x {d['cols']} cols, "
                  f"table {d['table_range']})")
            print("    " + "  ".join(
                f"{side}{n}={fmt(p[side + n])} [{p[side + n + '_cell']}]"
                for n in PARAM_NAMES))
            for r, row in enumerate(d["table"]):
                print(f"    lane[{r:2d}] " +
                      " ".join(f"{side}[{v:3d}]" if v is not None else "  ??  "
                               for v in row))

    if json_out:
        with open(json_out, "w") as fh:
            json.dump(result, fh, indent=1)
        print(f"\nwrote {json_out}")


if __name__ == "__main__":
    main()
