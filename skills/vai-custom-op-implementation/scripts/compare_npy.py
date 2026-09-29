#!/usr/bin/env python3

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Compare two numpy files and report differences."""

import argparse
import sys

import numpy as np


def compare_npy(
    file1: str,
    file2: str,
    rtol: float = 0.01,
    atol: float = 0.015,
    verbose: bool = False,
):
    """Compare two .npy files and report differences.

    Args:
        file1: Path to the first .npy file
        file2: Path to the second .npy file
        rtol: Relative tolerance for floating point comparison
        atol: Absolute tolerance for floating point comparison
        verbose: If True, print more detailed information

    Returns:
        True if arrays are equal (within tolerance), False otherwise
    """
    arr1 = np.load(file1)
    arr2 = np.load(file2)

    print(f"File 1: {file1}")
    print(f"  Shape: {arr1.shape}, Dtype: {arr1.dtype}")
    print(f"File 2: {file2}")
    print(f"  Shape: {arr2.shape}, Dtype: {arr2.dtype}")
    print()

    # Check shapes
    if arr1.shape != arr2.shape:
        print(f"ERROR: Shape mismatch: {arr1.shape} vs {arr2.shape}")
        return False

    # Check dtypes
    if arr1.dtype != arr2.dtype:
        print(f"WARNING: Dtype mismatch: {arr1.dtype} vs {arr2.dtype}")
        print("         Comparing values anyway...")

    # Compute differences
    diff = arr1.astype(np.float64) - arr2.astype(np.float64)
    abs_diff = np.abs(diff)

    max_abs_diff = np.max(abs_diff)
    mean_abs_diff = np.mean(abs_diff)
    num_different = np.sum(abs_diff > atol)

    print("Comparison Results:")
    print(f"  Max absolute difference:  {max_abs_diff}")
    print(f"  Mean absolute difference: {mean_abs_diff}")
    print(
        f"  Elements different (> {atol}): {num_different} / {arr1.size} ({100*num_different/arr1.size:.4f}%)"
    )

    # For floating point, use allclose
    if np.issubdtype(arr1.dtype, np.floating) or np.issubdtype(arr2.dtype, np.floating):
        is_close = np.allclose(arr1, arr2, rtol=rtol, atol=atol)
        print(f"  np.allclose (rtol={rtol}, atol={atol}): {is_close}")
    else:
        is_close = np.array_equal(arr1, arr2)
        print(f"  np.array_equal: {is_close}")

    if verbose and num_different > 0:
        print("\nFirst 10 differences:")
        diff_indices = np.argwhere(abs_diff > atol)[:10]
        for idx in diff_indices:
            idx_tuple = tuple(idx)
            print(
                f"  {idx_tuple}: {arr1[idx_tuple]} vs {arr2[idx_tuple]} (diff: {diff[idx_tuple]})"
            )

    if is_close:
        print("\nSUCCESS: Arrays are equal (within tolerance)")
    else:
        print("\nERROR: Arrays are NOT equal")

    return is_close


def main():
    parser = argparse.ArgumentParser(description="Compare two numpy (.npy) files")
    parser.add_argument("file1", help="Path to the first .npy file")
    parser.add_argument("file2", help="Path to the second .npy file")
    parser.add_argument(
        "--rtol",
        type=float,
        default=0.01,
        help="Relative tolerance (default: 0.01 = 1%%)",
    )
    parser.add_argument(
        "--atol", type=float, default=0.015, help="Absolute tolerance (default: 0.015)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed differences"
    )

    args = parser.parse_args()

    try:
        result = compare_npy(args.file1, args.file2, args.rtol, args.atol, args.verbose)
        sys.exit(0 if result else 1)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(2)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
