#!/bin/bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# detect_loops.sh - Extract loop labels from HLS C/C++ source files
#
# Usage: detect_loops.sh <source_file1> <source_file2> ...
#
# Finds labeled for loops matching the pattern: <label>: for (...)
# Examples:
#   Row_Loop: for (int r = 0; r < ROWS; r++)
#   Col_Loop: for (int c = 0; c < COLS; c++)
#
# Output: List of loop labels, one per line

if [ $# -eq 0 ]; then
    echo "Usage: $0 <source_file1> <source_file2> ..." >&2
    echo "Extracts labeled for loops from HLS C/C++ source files" >&2
    exit 1
fi

# Find all labeled for loops
# Pattern: optional whitespace, identifier, colon, optional whitespace, "for", opening paren
grep -Ph "^\s*\w+:\s*for\s*\(" "$@" 2>/dev/null | \
    sed -E 's/^\s*(\w+):\s*for.*/\1/' | \
    sort -u

exit 0
