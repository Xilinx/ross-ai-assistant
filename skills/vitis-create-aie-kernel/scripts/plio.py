#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Validated with Vitis 2026.2 (v++/x86simulator/aiesimulator) on AIE (xcvc1902) and AIE-ML (xcve2802).
"""Write, read and compare AI Engine PLIO text files (x86simulator / aiesimulator).

Text format used by input_plio / output_plio data files:
  * one PLIO word per line; values on a line are separated by spaces
  * values per line = plio_bits / bits-per-number (a complex sample is two numbers: real then imag)
      int8     : 4 / 8 / 16 numbers per line for plio 32 / 64 / 128
      int16    : 2 / 4 / 8
      int32    : 1 / 2 / 4
      cint16   : 2 / 4 / 8 numbers  (1 / 2 / 4 samples)  "re im re im"
      cint32   : 1 / 2 / 4 numbers  -> a cint32 sample needs plio_64 or wider
      float    : 1 / 2 / 4
      cfloat   : 1 / 2 / 4 numbers  -> a cfloat sample needs plio_64 or wider
      bfloat16 : 2 / 4 / 8 (written as decimal floats, rounded to bfloat16 precision)
  * aiesimulator output files carry extra lines: "T <time> <unit>" timestamps and "TLAST";
    x86simulator output may carry "TLAST". `read` and `compare` drop them.

Sub-commands:
  write    values (CSV/whitespace file or --values) -> PLIO text file
  read     PLIO/sim output file -> one value (or "re im") per line on stdout
  compare  actual vs golden with abs/rel tolerance; exit status 0 = pass, 1 = fail
  selftest run the built-in checks

Examples:
  python3 plio.py write --type cint16 --plio 64 --in golden_in.csv --out data/sig_i.txt
  python3 plio.py read --type int16 x86simulator_output/data/sig_o.txt
  python3 plio.py compare --type int16 --abs-tol 1 x86simulator_output/data/sig_o.txt data/golden_o.txt
"""

import argparse
import math
import os
import re
import struct
import sys
import tempfile

# (bits per number, is_complex, is_float, min, max)
TYPES = {
    "int8": (8, False, False, -(2**7), 2**7 - 1),
    "uint8": (8, False, False, 0, 2**8 - 1),
    "int16": (16, False, False, -(2**15), 2**15 - 1),
    "int32": (32, False, False, -(2**31), 2**31 - 1),
    "cint16": (16, True, False, -(2**15), 2**15 - 1),
    "cint32": (32, True, False, -(2**31), 2**31 - 1),
    "float": (32, False, True, None, None),
    "cfloat": (32, True, True, None, None),
    "bfloat16": (16, False, True, None, None),
}

_SKIP_LINE = re.compile(r"^\s*(T\s+\d+(\.\d+)?\s*\w*|TLAST)\s*$")


def to_bfloat16(x: float) -> float:
    """Round a float to the nearest bfloat16 value (ties to even), returned as float."""
    bits = struct.unpack("<I", struct.pack("<f", x))[0]
    bits += 0x7FFF + ((bits >> 16) & 1)
    return struct.unpack("<f", struct.pack("<I", (bits & 0xFFFF0000) & 0xFFFFFFFF))[0]


def numbers_per_line(dtype: str, plio_bits: int) -> int:
    """Return how many numbers (real and imag counted separately) go on one PLIO line."""
    bits = TYPES[dtype][0]
    n = plio_bits // bits
    if TYPES[dtype][1] and n < 2:
        raise ValueError(f"{dtype} needs plio width >= {2 * bits} bits per sample")
    return n


def parse_values(text: str, dtype: str) -> list:
    """Parse comma/space separated numbers. Complex types take interleaved re,im numbers."""
    raw = [t for t in re.split(r"[,\s]+", text.strip()) if t]
    is_float = TYPES[dtype][2]
    out = []
    for tok in raw:
        out.append(float(tok) if is_float else int(round(float(tok))))
    if TYPES[dtype][1] and len(out) % 2:
        raise ValueError("complex data needs an even count of numbers (re im pairs)")
    return out


def fmt(v, dtype: str) -> str:
    if not TYPES[dtype][2]:
        lo, hi = TYPES[dtype][3], TYPES[dtype][4]
        if v < lo or v > hi:
            raise ValueError(f"value {v} out of range for {dtype}")
        return str(int(v))
    if dtype == "bfloat16":
        v = to_bfloat16(v)
    return repr(float(v)) if math.isfinite(v) else str(v)


def write_plio(values: list, dtype: str, plio_bits: int, path: str) -> None:
    """Write numbers to a PLIO text file. Pads nothing: the count must fill whole lines."""
    n = numbers_per_line(dtype, plio_bits)
    if len(values) % n:
        raise ValueError(f"{len(values)} numbers do not fill whole {plio_bits}-bit lines ({n} per line)")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        for i in range(0, len(values), n):
            f.write(" ".join(fmt(v, dtype) for v in values[i:i + n]) + "\n")


def read_plio(path: str, dtype: str) -> list:
    """Read numbers from a PLIO/sim output file, dropping timestamp and TLAST lines."""
    vals = []
    with open(path) as f:
        for line in f:
            if not line.strip() or _SKIP_LINE.match(line):
                continue
            for tok in line.split():
                if tok == "TLAST":
                    continue
                vals.append(float(tok) if TYPES[dtype][2] else int(tok))
    return vals


def compare(actual: list, golden: list, abs_tol: float, rel_tol: float, dtype: str, max_report: int):
    """Return (ok, report_lines). Complex values are compared per component."""
    lines = []
    ok = True
    if not golden:
        ok = False
        lines.append("GOLDEN IS EMPTY")
    if len(actual) != len(golden):
        ok = False
        lines.append(f"LENGTH MISMATCH: actual {len(actual)} numbers, golden {len(golden)}")
    step = 2 if TYPES[dtype][1] else 1
    max_err = 0.0
    bad = 0
    for i in range(min(len(actual), len(golden))):
        a, g = actual[i], golden[i]
        err = abs(a - g)
        if math.isnan(a) or math.isnan(g):
            err = 0.0 if (math.isnan(a) and math.isnan(g)) else math.inf
        max_err = max(max_err, err)
        if err > abs_tol + rel_tol * abs(g):
            bad += 1
            ok = False
            if bad <= max_report:
                part = ("re", "im")[i % 2] if step == 2 else ""
                lines.append(f"MISMATCH sample {i // step}{' ' + part if part else ''}: "
                             f"actual {a} golden {g} err {err}")
    lines.append(f"compared {min(len(actual), len(golden))} numbers, mismatches {bad}, max abs err {max_err}")
    lines.append("PASS" if ok else "FAIL")
    return ok, lines


def _selftest() -> int:
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "a.txt")
    write_plio([1, -2, 3, -4, 5, 6, 7, 8], "int16", 64, p)
    assert open(p).read() == "1 -2 3 -4\n5 6 7 8\n"
    write_plio([1, 2, 3, 4], "cint16", 32, p)
    assert open(p).read() == "1 2\n3 4\n"
    write_plio([1.5, -2.25], "cfloat", 64, p)
    assert read_plio(p, "cfloat") == [1.5, -2.25]
    try:
        write_plio([1, 2], "cint32", 32, p)
        raise AssertionError("cint32 on plio_32 must be rejected")
    except ValueError:
        pass
    try:
        write_plio([40000], "int16", 32, p)
        raise AssertionError("range check")
    except ValueError:
        pass
    with open(p, "w") as f:
        f.write("T 1600 ns\n1 2\nT 1603200 ps\nTLAST\n3 4\n")
    assert read_plio(p, "int16") == [1, 2, 3, 4]
    assert compare([1, 2, 3], [1, 2, 4], 1, 0, "int16", 5)[0]
    assert not compare([1, 2, 3], [1, 2, 5], 1, 0, "int16", 5)[0]
    assert not compare([1, 2], [1, 2, 3], 1, 0, "int16", 5)[0]
    assert not compare([], [], 0, 0, "int16", 5)[0]
    assert compare([1.0, 2.0], [1.0, 2.0 * (1 + 1e-6)], 0, 1e-5, "float", 5)[0]
    assert to_bfloat16(1.0 + 2**-9) == 1.0 and to_bfloat16(3.140625) == 3.140625
    print("selftest PASS")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True  # Python 3.6 compatible

    w = sub.add_parser("write", help="write a PLIO text file")
    w.add_argument("--type", required=True, choices=sorted(TYPES))
    w.add_argument("--plio", type=int, default=64, choices=(32, 64, 128), help="PLIO width in bits")
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument("--in", dest="infile", help="CSV/whitespace file of numbers (complex: re,im interleaved)")
    g.add_argument("--values", help="numbers inline, e.g. '1,2,3,4'")
    w.add_argument("--out", required=True)

    r = sub.add_parser("read", help="print values, dropping T/TLAST lines")
    r.add_argument("--type", required=True, choices=sorted(TYPES))
    r.add_argument("file")

    c = sub.add_parser("compare", help="compare actual vs golden")
    c.add_argument("--type", required=True, choices=sorted(TYPES))
    c.add_argument("--abs-tol", type=float, default=0.0, help="absolute tolerance (LSBs for integer types)")
    c.add_argument("--rel-tol", type=float, default=0.0, help="relative tolerance, e.g. 1e-5 for float")
    c.add_argument("--max-report", type=int, default=10)
    c.add_argument("actual")
    c.add_argument("golden")

    sub.add_parser("selftest", help="run built-in checks")

    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return _selftest()
    if a.cmd == "write":
        text = open(a.infile).read() if a.infile else a.values
        write_plio(parse_values(text, a.type), a.type, a.plio, a.out)
        return 0
    if a.cmd == "read":
        v = read_plio(a.file, a.type)
        step = 2 if TYPES[a.type][1] else 1
        for i in range(0, len(v), step):
            print(" ".join(str(x) for x in v[i:i + step]))
        return 0
    for f in (a.actual, a.golden):
        if not os.path.isfile(f):
            print(f"MISSING FILE: {f}\nFAIL")
            return 1
    ok, lines = compare(read_plio(a.actual, a.type), read_plio(a.golden, a.type),
                        a.abs_tol, a.rel_tol, a.type, a.max_report)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
