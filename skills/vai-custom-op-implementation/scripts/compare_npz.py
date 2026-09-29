#!/usr/bin/env python3

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Compare two NPZ files and report differences."""

import argparse
import sys

import numpy as np


def compare_npz(
    file1: str,
    file2: str,
    rtol: float = 0.01,
    atol: float = 0.015,
    verbose: bool = False,
):
    """Compare two NPZ files and report differences.

    Args:
        file1: Path to first NPZ file
        file2: Path to second NPZ file
        rtol: Relative tolerance for comparison
        atol: Absolute tolerance for comparison
        verbose: Print detailed information

    Returns:
        True if files match, False otherwise
    """
    try:
        npz1 = np.load(file1)
        npz2 = np.load(file2)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading files: {e}")
        return False

    keys1 = set(npz1.keys())
    keys2 = set(npz2.keys())

    all_match = True

    # Check for missing keys
    only_in_1 = keys1 - keys2
    only_in_2 = keys2 - keys1

    if only_in_1:
        print(f"Arrays only in {file1}: {only_in_1}")
        all_match = False
    if only_in_2:
        print(f"Arrays only in {file2}: {only_in_2}")
        all_match = False

    # Compare common arrays
    common_keys = keys1 & keys2

    for key in sorted(common_keys):
        arr1 = npz1[key]
        arr2 = npz2[key]

        if verbose:
            print(f"\nComparing '{key}':")
            print(f"  File 1: shape={arr1.shape}, dtype={arr1.dtype}")
            print(f"  File 2: shape={arr2.shape}, dtype={arr2.dtype}")

        # Check shape
        if arr1.shape != arr2.shape:
            print(
                f"[MISMATCH] '{key}' (shape {arr1.shape} vs {arr2.shape}): Shape mismatch"
            )
            all_match = False
            continue

        # Check dtype
        if arr1.dtype != arr2.dtype:
            print(
                f"[WARNING] '{key}' (shape {arr1.shape}): dtype mismatch - {arr1.dtype} vs {arr2.dtype}"
            )

        # Compare values
        try:
            if np.issubdtype(arr1.dtype, np.floating) or np.issubdtype(
                arr2.dtype, np.floating
            ):
                # assert_allclose raises if not close, returns None if close
                try:
                    np.testing.assert_allclose(
                        arr1, arr2, rtol=rtol, atol=atol, equal_nan=True
                    )
                    close = True
                except AssertionError:
                    close = False
            else:
                close = np.array_equal(arr1, arr2)

            if close:
                print(f"[MATCH] '{key}' (shape {arr1.shape})")
            else:
                diff = np.abs(arr1.astype(float) - arr2.astype(float))
                max_diff = np.max(diff)
                mean_diff = np.mean(diff)
                num_diff = np.sum(
                    ~np.isclose(arr1, arr2, rtol=rtol, atol=atol, equal_nan=True)
                )

                print(
                    f"[MISMATCH] '{key}' (shape {arr1.shape}): max_diff={max_diff:.6e}, mean_diff={mean_diff:.6e}, "
                    f"num_different={num_diff}/{arr1.size} ({100*num_diff/arr1.size:.2f}%)"
                )
                all_match = False
        except Exception as e:
            print(f"[ERROR] '{key}': Could not compare - {e}")
            all_match = False

    npz1.close()
    npz2.close()

    return all_match


def main():
    parser = argparse.ArgumentParser(description="Compare two NPZ files")
    parser.add_argument("file1", help="First NPZ file")
    parser.add_argument("file2", help="Second NPZ file")
    parser.add_argument(
        "--rtol",
        type=float,
        default=0.01,
        help="Relative tolerance (default: 0.01 = 1%%)",
    )
    parser.add_argument(
        "--atol", type=float, default=0.015, help="Absolute tolerance (default: 0.015)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    print(f"Comparing:\n  {args.file1}\n  {args.file2}\n")
    print(f"Tolerances: rtol={args.rtol}, atol={args.atol}\n")

    match = compare_npz(args.file1, args.file2, args.rtol, args.atol, args.verbose)

    if match:
        print("\nSUCCESS: Files match!")
        sys.exit(0)
    else:
        print("\nERROR: Files differ!")
        sys.exit(1)


if __name__ == "__main__":
    main()
