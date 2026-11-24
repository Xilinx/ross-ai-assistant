#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

"""Lint AIE int8xint8 address parameters against the documented legality rules.

Run this on every parameter set before building. It is the only check that
rejects zstart > 15 (the hw compile only warns, x86sim is silent) and square
digits > 3 (no tool reports them). The granularity rules are also enforced by
the hw compile, but x86sim reports them only as a run-time chess_error and
still exits 0.

    python3 check_params.py --lanes 16 --xlen 64 \
        --xstart 0 --xoffsets 0x30303030 --xstep 4 --xsquare 0x3210 \
        --zstart 0 --zoffsets 0x43322110 --zstep 2 --zsquare 0x2110

Argument numbers in the messages are for mul*; add one for mac*/msc*.
Exit status is non-zero if any rule is violated.
"""
import argparse
import sys


def auto_int(s):
    return int(s, 0)


def x_addresses(lanes, xstart, xoffsets, xstep):
    """Pre-square, pre-modulo x addresses: closed form in references/addressing.md."""
    cols = 16 if lanes == 8 else 8
    o = lambda k: (xoffsets >> (4 * k)) & 0xF if k >= 0 else 0
    out = []
    for r in range(lanes):
        rx, rr = r // 2, r % 4
        off = o(rx) * 4 + rr % 2
        if rr >= 2:
            off += (o(rx - 1) + 1) * 4
        out += [xstart + off + (c // 2) * xstep + (c % 2) * 2 for c in range(cols)]
    return out


def z_addresses(lanes, zstart, zoffsets, zstep):
    """Pre-square, pre-modulo z addresses: closed form in references/addressing.md."""
    cols = 16 if lanes == 8 else 8
    out = []
    for r in range(lanes):
        rz = (r // 4) * 2 + r % 2
        off = ((zoffsets >> (4 * rz)) & 0xF) * 2
        out += [zstart + off + (c // 2) * zstep + (c % 2) for c in range(cols)]
    return out


def check(args):
    errs, warns = [], []

    if args.lanes not in (8, 16):
        errs.append(f"lanes={args.lanes}: must be 8 (mul8/mac8) or 16 (mul16/mac16)")
    if args.xlen not in (64, 128):
        errs.append(f"xlen={args.xlen}: xbuff must be v64int8 or v128int8")

    # xbuff addressing has 32-bit granularity.
    if args.xstart % 4:
        errs.append(f"xstart={args.xstart}: must be a multiple of 4 "
                    f"(xbuff granularity is 32-bit) -> "
                    f"'Argument number 2 of this operation must be a multiple of 4'")
    if args.xstep % 4:
        errs.append(f"xstep={args.xstep}: must be a multiple of 4 "
                    f"(xbuff granularity is 32-bit) -> "
                    f"'Argument number 4 of this operation must be a multiple of 4'")

    # zbuff addressing has 16-bit granularity.
    if args.zstart % 2:
        errs.append(f"zstart={args.zstart}: must be a multiple of 2 "
                    f"(zbuff granularity is 16-bit) -> "
                    f"'Argument number 7 of this operation must be a multiple of 2'")
    if not 0 <= args.zstart <= 15:
        errs.append(f"zstart={args.zstart}: only the 4 LSB are used, so it behaves as "
                    f"{args.zstart & 15}; the hw compile only warns and x86sim is "
                    f"silent. Reach higher z elements with zoffsets")
    if args.zstep % 2:
        errs.append(f"zstep={args.zstep}: must be a multiple of 2 "
                    f"(zbuff granularity is 16-bit) -> "
                    f"'Argument number 9 of this operation must be a multiple of 2'")

    for name, val in (("xsquare", args.xsquare), ("zsquare", args.zsquare)):
        if not 0 <= val <= 0xFFFF:
            errs.append(f"{name}=0x{val:x}: must be 4 hex digits")
            continue
        for pos in range(4):
            nib = (val >> (4 * pos)) & 0xF
            if nib > 3:
                errs.append(f"{name}=0x{val:04x}: digit {pos} is {nib}; each square "
                            f"digit selects one of 4 positions (0..3). It is masked to "
                            f"{nib & 3} with no diagnostic from any tool")

    for name, val in (("xoffsets", args.xoffsets), ("zoffsets", args.zoffsets)):
        if not 0 <= val <= 0xFFFFFFFF:
            errs.append(f"{name}=0x{val:x}: must be 8 hex digits")

    # Reach checks: warn rather than error, since wraparound is well defined and
    # steps have no range limit. The square only permutes within a lane/column
    # block, so pre-square bounds hold.
    xs = x_addresses(args.lanes, args.xstart, args.xoffsets, args.xstep)
    if min(xs) < 0 or max(xs) >= args.xlen:
        warns.append(f"x addresses span [{min(xs)}, {max(xs)}] with xlen={args.xlen}; "
                     f"addressing wraps modulo {args.xlen}")

    zs = z_addresses(args.lanes, args.zstart, args.zoffsets, args.zstep)
    if min(zs) < 0 or max(zs) >= 32:
        warns.append(f"z addresses span [{min(zs)}, {max(zs)}] but zbuff is v32int8; "
                     f"addressing wraps modulo 32")

    return errs, warns


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lanes", type=int, required=True, choices=(8, 16))
    p.add_argument("--xlen", type=int, required=True, choices=(64, 128))
    for n, d in (("xstart", 0), ("xstep", 4), ("zstart", 0), ("zstep", 2)):
        p.add_argument(f"--{n}", type=auto_int, default=d)
    for n, d in (("xoffsets", 0), ("zoffsets", 0)):
        p.add_argument(f"--{n}", type=auto_int, default=d)
    p.add_argument("--xsquare", type=auto_int, default=0x3210)
    p.add_argument("--zsquare", type=auto_int, default=0x3210)
    args = p.parse_args()

    errs, warns = check(args)
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"ERROR {e}")

    if errs:
        print(f"\n{len(errs)} rule violation(s).")
        return 1
    print("\nParameters satisfy the documented rules. Next: run the design in "
          "x86sim and compile it for hw (SKILL.md step 5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
