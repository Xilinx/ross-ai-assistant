#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

"""Bit-exact reference model: 16-tap FIR, real int8 samples, real int8 taps.

    y[n] = sat8( round( sum_{t=0..15} TAPS[t] * x[n-t] / 2**7 ) )

The accumulation is exact; the division by 2**7 rounds to nearest with ties
away from zero and the result saturates to [-128, 127]. Samples before the
first input are zero.

Sample files hold one 64-bit PLIO word per line: 8 int8 samples.

    python3 rfir16_ref.py gen   --n 4096 --seed 1 --input input.txt [--golden golden.txt]
    python3 rfir16_ref.py check --input input.txt --output output.txt [--delay D]
"""
import argparse
import random
import sys

# Q7 coefficients, h[0] first.
TAPS = [7, -12, 21, 38, 49, 66, 57, 35, 19, 6, -5, -14, -9, 4, 13, -8]
SHIFT = 7


def round_shift(acc, shift=SHIFT):
    q, r = divmod(abs(acc), 1 << shift)
    if r >= 1 << (shift - 1):
        q += 1
    return q if acc >= 0 else -q


def sat8(v):
    return max(-128, min(127, v))


def rfir16(x):
    """x: list of int8 samples. Returns the filtered list, same length."""
    y = []
    for n in range(len(x)):
        acc = sum(h * x[n - t] for t, h in enumerate(TAPS) if n - t >= 0)
        y.append(sat8(round_shift(acc)))
    return y


def read_plio(path):
    return [int(v) for line in open(path) for v in line.split()]


def write_plio(path, samples):
    if len(samples) % 8:
        raise ValueError("sample count must be a multiple of 8 (one 64-bit word = 8 samples)")
    with open(path, "w") as f:
        for k in range(0, len(samples), 8):
            f.write(" ".join(str(v) for v in samples[k:k + 8]) + "\n")


def gen(n, seed):
    rng = random.Random(seed)
    return [rng.randint(-128, 127) for _ in range(n)]


def check(x, out, delay=0):
    """Compare out[j] with y[j - delay]; y before the first sample is zero. Returns mismatch list."""
    y = rfir16(x)
    if len(out) != len(x):
        return [("length", len(x), len(out))]
    bad = []
    for j, got in enumerate(out):
        exp = y[j - delay] if j >= delay else 0
        if got != exp:
            bad.append((j, exp, got))
    return bad


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="write a random input file and, optionally, its golden output")
    g.add_argument("--n", type=int, default=4096, help="samples (multiple of 8)")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--input", required=True)
    g.add_argument("--golden")
    c = sub.add_parser("check", help="compare an output file with the model")
    c.add_argument("--input", required=True)
    c.add_argument("--output", required=True)
    c.add_argument("--delay", type=int, default=0, help="output delay in samples")
    a = p.parse_args(argv)

    if a.cmd == "gen":
        x = gen(a.n, a.seed)
        write_plio(a.input, x)
        if a.golden:
            write_plio(a.golden, rfir16(x))
        return 0

    bad = check(read_plio(a.input), read_plio(a.output), a.delay)
    if not bad:
        print(f"PASS: bit-exact (delay {a.delay})")
        return 0
    print(f"FAIL: {len(bad)} mismatching samples")
    for b in bad[:10]:
        print("  sample", b[0], "expected", b[1], "got", b[2])
    return 1


if __name__ == "__main__":
    sys.exit(main())
