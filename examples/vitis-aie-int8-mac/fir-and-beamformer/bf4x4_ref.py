#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

"""Bit-exact reference model: 4-antenna, 4-layer receive beamformer, complex int8.

    y[l][t] = sat8( round( sum_{a=0..3} W[l][a] * x[a][t] / 2**7 ) )     l = 0..3

W and x are complex; the product is the full complex product, so the real part
is sum(Wre*xre - Wim*xim) and the imaginary part is sum(Wre*xim + Wim*xre).
Each part is accumulated exactly, divided by 2**7 rounding to nearest with ties
away from zero, then saturated to [-128, 127].

Sample files hold one 64-bit PLIO word per line, one time sample per line:
  input   x0re x0im x1re x1im x2re x2im x3re x3im     (antennas 0..3)
  output  y0re y0im y1re y1im y2re y2im y3re y3im     (layers 0..3)

    python3 bf4x4_ref.py gen   --n 1024 --seed 1 --input input.txt [--golden golden.txt]
    python3 bf4x4_ref.py check --input input.txt --output output.txt [--delay D]
"""
import argparse
import random
import sys

# Q7 complex weights W[layer][antenna] = (re, im).
WEIGHTS = [
    [(45, -12), (38, 27), (-20, 41), (9, -33)],
    [(-31, 22), (50, 6), (17, -44), (-8, 29)],
    [(12, 40), (-27, -19), (36, 15), (43, -7)],
    [(-6, -48), (21, 33), (-39, 10), (28, 25)],
]
SHIFT = 7
ANTENNAS = LAYERS = 4


def round_shift(acc, shift=SHIFT):
    q, r = divmod(abs(acc), 1 << shift)
    if r >= 1 << (shift - 1):
        q += 1
    return q if acc >= 0 else -q


def sat8(v):
    return max(-128, min(127, v))


def beamform(x):
    """x: list of time samples, each a list of 4 (re, im) antenna samples. Returns 4 (re, im) per time."""
    y = []
    for xt in x:
        out = []
        for l in range(LAYERS):
            re = sum(w[0] * s[0] - w[1] * s[1] for w, s in zip(WEIGHTS[l], xt))
            im = sum(w[0] * s[1] + w[1] * s[0] for w, s in zip(WEIGHTS[l], xt))
            out.append((sat8(round_shift(re)), sat8(round_shift(im))))
        y.append(out)
    return y


def read_plio(path):
    vals = [int(v) for line in open(path) for v in line.split()]
    if len(vals) % 8:
        raise ValueError(f"{path}: value count {len(vals)} is not a multiple of 8")
    return [[(vals[k + 2 * a], vals[k + 2 * a + 1]) for a in range(4)] for k in range(0, len(vals), 8)]


def write_plio(path, samples):
    with open(path, "w") as f:
        for st in samples:
            f.write(" ".join(str(v) for c in st for v in c) + "\n")


def gen(n, seed):
    rng = random.Random(seed)
    return [[(rng.randint(-128, 127), rng.randint(-128, 127)) for _ in range(ANTENNAS)] for _ in range(n)]


def check(x, out, delay=0):
    """Compare out[j] with y[j - delay]; y before the first sample is zero. Returns mismatch list."""
    y = beamform(x)
    if len(out) != len(x):
        return [("length", len(x), len(out))]
    zero = [(0, 0)] * LAYERS
    bad = []
    for j, got in enumerate(out):
        exp = y[j - delay] if j >= delay else zero
        if got != exp:
            bad.append((j, exp, got))
    return bad


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="write a random input file and, optionally, its golden output")
    g.add_argument("--n", type=int, default=1024, help="time samples")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--input", required=True)
    g.add_argument("--golden")
    c = sub.add_parser("check", help="compare an output file with the model")
    c.add_argument("--input", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--delay", type=int, default=0, help="output delay in time samples")
    a = p.parse_args(argv)

    if a.cmd == "gen":
        x = gen(a.n, a.seed)
        write_plio(a.input, x)
        if a.golden:
            write_plio(a.golden, beamform(x))
        return 0

    bad = check(read_plio(a.input), read_plio(a.output), a.delay)
    if not bad:
        print(f"PASS: bit-exact (delay {a.delay})")
        return 0
    print(f"FAIL: {len(bad)} mismatching time samples")
    for b in bad[:10]:
        print("  time", b[0], "expected", b[1], "got", b[2])
    return 1


if __name__ == "__main__":
    sys.exit(main())
