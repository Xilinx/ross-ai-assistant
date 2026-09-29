#!/usr/bin/env python3

# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Print the first N values from an NPY file."""

import argparse
import sys

import numpy as np


def main():
    parser = argparse.ArgumentParser(description="Print values from an NPY file")
    parser.add_argument("npy_file", help="Path to the NPY file")
    parser.add_argument(
        "-n",
        "--num-values",
        type=int,
        default=500,
        help="Number of values to print (default: 500)",
    )
    parser.add_argument(
        "--shape", action="store_true", help="Also print the array shape and dtype"
    )
    args = parser.parse_args()

    try:
        data = np.load(args.npy_file)
    except FileNotFoundError:
        print(f"Error: File not found: {args.npy_file}")
        sys.exit(1)
    except Exception as e:
        print(f"Error loading file: {e}")
        sys.exit(1)

    if args.shape:
        print(f"Shape: {data.shape}")
        print(f"Dtype: {data.dtype}")
        print(f"Total elements: {data.size}")
        print("-" * 40)

    flat = data.flatten()
    n = min(args.num_values, len(flat))

    for i in range(n):
        print(f"[{i}] {flat[i]}")

    if n < len(flat):
        print(f"... ({len(flat) - n} more values)")


if __name__ == "__main__":
    main()
