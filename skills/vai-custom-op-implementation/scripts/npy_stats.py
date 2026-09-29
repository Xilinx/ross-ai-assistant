#!/usr/bin/env python3

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""
Script to display statistics about a .npy file.

Usage:
    python npy_stats.py <file.npy> [file2.npy ...]
    python npy_stats.py --compare file1.npy file2.npy
"""

import argparse
import sys
from pathlib import Path

import numpy as np


def get_stats(arr: np.ndarray) -> dict:
    """Compute statistics for a numpy array."""
    stats = {
        "shape": arr.shape,
        "dtype": str(arr.dtype),
        "size": arr.size,
        "nbytes": arr.nbytes,
    }

    # For numeric types, compute additional stats
    if np.issubdtype(arr.dtype, np.number):
        flat = arr.flatten()
        stats.update(
            {
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr)),
                "median": float(np.median(arr)),
                "sum": float(np.sum(arr)),
                "num_zeros": int(np.sum(arr == 0)),
                "num_nan": int(np.sum(np.isnan(arr)))
                if np.issubdtype(arr.dtype, np.floating)
                else 0,
                "num_inf": int(np.sum(np.isinf(arr)))
                if np.issubdtype(arr.dtype, np.floating)
                else 0,
                "num_negative": int(np.sum(arr < 0)),
                "num_positive": int(np.sum(arr > 0)),
            }
        )

        # Percentiles
        if arr.size > 0:
            stats["percentiles"] = {
                "1%": float(np.percentile(flat, 1)),
                "5%": float(np.percentile(flat, 5)),
                "25%": float(np.percentile(flat, 25)),
                "50%": float(np.percentile(flat, 50)),
                "75%": float(np.percentile(flat, 75)),
                "95%": float(np.percentile(flat, 95)),
                "99%": float(np.percentile(flat, 99)),
            }

    # For boolean types
    elif arr.dtype == np.bool_:
        stats.update(
            {
                "num_true": int(np.sum(arr)),
                "num_false": int(np.sum(~arr)),
                "pct_true": float(np.mean(arr) * 100),
            }
        )

    return stats


def print_stats(filepath: str, stats: dict) -> None:
    """Print statistics in a formatted way."""
    print(f"\n{'=' * 60}")
    print(f"File: {filepath}")
    print(f"{'=' * 60}")
    print(f"  Shape:  {stats['shape']}")
    print(f"  Dtype:  {stats['dtype']}")
    print(f"  Size:   {stats['size']:,} elements")
    print(f"  Bytes:  {stats['nbytes']:,} ({stats['nbytes'] / 1024:.2f} KB)")

    if "min" in stats:
        print("\n  Value Statistics:")
        print(f"    Min:    {stats['min']:.6g}")
        print(f"    Max:    {stats['max']:.6g}")
        print(f"    Mean:   {stats['mean']:.6g}")
        print(f"    Std:    {stats['std']:.6g}")
        print(f"    Median: {stats['median']:.6g}")
        print(f"    Sum:    {stats['sum']:.6g}")

        print("\n  Element Counts:")
        print(
            f"    Zeros:    {stats['num_zeros']:,} ({stats['num_zeros']/stats['size']*100:.2f}%)"
        )
        print(f"    Positive: {stats['num_positive']:,}")
        print(f"    Negative: {stats['num_negative']:,}")
        if stats["num_nan"] > 0:
            print(f"    NaN:      {stats['num_nan']:,} (WARNING!)")
        if stats["num_inf"] > 0:
            print(f"    Inf:      {stats['num_inf']:,} (WARNING!)")

        if "percentiles" in stats:
            print("\n  Percentiles:")
            for k, v in stats["percentiles"].items():
                print(f"    {k:>4}: {v:.6g}")

    elif "num_true" in stats:
        print("\n  Boolean Statistics:")
        print(f"    True:  {stats['num_true']:,} ({stats['pct_true']:.2f}%)")
        print(f"    False: {stats['num_false']:,} ({100 - stats['pct_true']:.2f}%)")


def compare_arrays(file1: str, file2: str) -> None:
    """Compare two npy files and show differences."""
    arr1 = np.load(file1)
    arr2 = np.load(file2)

    print(f"\n{'=' * 60}")
    print("Comparing:")
    print(f"  File 1: {file1}")
    print(f"  File 2: {file2}")
    print(f"{'=' * 60}")

    print(f"\n  Shape: {arr1.shape} vs {arr2.shape}")
    print(f"  Dtype: {arr1.dtype} vs {arr2.dtype}")

    if arr1.shape != arr2.shape:
        print("\n  ERROR: Shapes don't match, cannot compare values!")
        return

    # Cast to common dtype for comparison
    if arr1.dtype != arr2.dtype:
        common_dtype = np.promote_types(arr1.dtype, arr2.dtype)
        arr1 = arr1.astype(common_dtype)
        arr2 = arr2.astype(common_dtype)
        print(f"  (Promoted to {common_dtype} for comparison)")

    if np.issubdtype(arr1.dtype, np.number):
        diff = arr1.astype(np.float64) - arr2.astype(np.float64)
        abs_diff = np.abs(diff)

        # Handle potential division by zero for relative error
        with np.errstate(divide="ignore", invalid="ignore"):
            rel_diff = abs_diff / np.maximum(np.abs(arr2.astype(np.float64)), 1e-10)
            rel_diff = np.where(np.isfinite(rel_diff), rel_diff, 0)

        print("\n  Difference Statistics:")
        print(f"    Max abs diff:  {np.max(abs_diff):.6g}")
        print(f"    Mean abs diff: {np.mean(abs_diff):.6g}")
        print(
            f"    Max rel diff:  {np.max(rel_diff):.6g} ({np.max(rel_diff)*100:.4f}%)"
        )
        print(
            f"    Mean rel diff: {np.mean(rel_diff):.6g} ({np.mean(rel_diff)*100:.4f}%)"
        )

        num_exact = np.sum(arr1 == arr2)
        print(
            f"\n    Exact matches: {num_exact:,} / {arr1.size:,} ({num_exact/arr1.size*100:.2f}%)"
        )

        # Find location of max difference
        max_idx = np.unravel_index(np.argmax(abs_diff), abs_diff.shape)
        print(f"\n    Max diff at index {max_idx}:")
        print(f"      File 1: {arr1[max_idx]}")
        print(f"      File 2: {arr2[max_idx]}")
        print(f"      Diff:   {diff[max_idx]}")

        # Tolerance check
        for atol, rtol in [(1e-3, 1e-3), (1e-4, 1e-4), (1e-5, 1e-5), (1e-6, 1e-6)]:
            close = np.allclose(arr1, arr2, atol=atol, rtol=rtol)
            status = "PASS" if close else "FAIL"
            print(f"\n    allclose(atol={atol}, rtol={rtol}): {status}")

    elif arr1.dtype == np.bool_:
        matches = np.sum(arr1 == arr2)
        print("\n  Boolean Comparison:")
        print(
            f"    Matches: {matches:,} / {arr1.size:,} ({matches/arr1.size*100:.2f}%)"
        )
        print(f"    Mismatches: {arr1.size - matches:,}")

    else:
        matches = np.sum(arr1 == arr2)
        print("\n  Element Comparison:")
        print(
            f"    Matches: {matches:,} / {arr1.size:,} ({matches/arr1.size*100:.2f}%)"
        )


def main():
    parser = argparse.ArgumentParser(
        description="""Display statistics about .npy files

Examples:
    python npy_stats.py output.npy
    python npy_stats.py *.npy
    python npy_stats.py --compare expected.npy actual.npy
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("files", nargs="+", help="NPY file(s) to analyze")
    parser.add_argument(
        "--compare",
        "-c",
        action="store_true",
        help="Compare two files (requires exactly 2 files)",
    )
    parser.add_argument(
        "--head", "-n", type=int, default=0, help="Print first N elements"
    )
    parser.add_argument(
        "--tail", "-t", type=int, default=0, help="Print last N elements"
    )

    args = parser.parse_args()

    if args.compare:
        if len(args.files) != 2:
            print("Error: --compare requires exactly 2 files")
            sys.exit(1)
        compare_arrays(args.files[0], args.files[1])
        return

    for filepath in args.files:
        if not Path(filepath).exists():
            print(f"Error: File not found: {filepath}")
            continue

        try:
            arr = np.load(filepath)
            stats = get_stats(arr)
            print_stats(filepath, stats)

            if args.head > 0:
                print(f"\n  First {args.head} elements (flattened):")
                print(f"    {arr.flatten()[:args.head]}")

            if args.tail > 0:
                print(f"\n  Last {args.tail} elements (flattened):")
                print(f"    {arr.flatten()[-args.tail:]}")

        except Exception as e:
            print(f"Error loading {filepath}: {e}")


if __name__ == "__main__":
    main()
