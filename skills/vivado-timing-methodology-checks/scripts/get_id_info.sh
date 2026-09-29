#!/bin/bash
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Script to extract timing ID information from SUPPORTED_IDS.md
# Usage: ./get_id_info.sh TIMING-16

# Check if ID argument provided
if [ -z "$1" ]; then
    echo "Usage: $0 <TIMING-ID>"
    echo "Example: $0 TIMING-16"
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Path to SUPPORTED_IDS.md (relative to script location)
SUPPORTED_IDS_FILE="$SCRIPT_DIR/../references/SUPPORTED_IDS.md"

# Check if file exists
if [ ! -f "$SUPPORTED_IDS_FILE" ]; then
    echo "Error: SUPPORTED_IDS.md not found at $SUPPORTED_IDS_FILE"
    exit 1
fi

# Grep the line for the given ID and parse into variables
line=$(grep "| $1 " "$SUPPORTED_IDS_FILE")

if [ -z "$line" ]; then
    echo "Error: ID $1 not found in SUPPORTED_IDS.md"
    exit 1
fi

# Parse the line into variables using IFS and read
IFS='|' read -r _ ID GROUP PRIORITY HAS_REFERENCE_FILE _ <<< "$line"

# Trim whitespace from variables
ID=$(echo "$ID" | xargs)
GROUP=$(echo "$GROUP" | xargs)
PRIORITY=$(echo "$PRIORITY" | xargs)
HAS_REFERENCE_FILE=$(echo "$HAS_REFERENCE_FILE" | xargs)

# Print the variables
echo "ID=$ID"
echo "GROUP=$GROUP"
echo "PRIORITY=$PRIORITY"
echo "HAS_REFERENCE_FILE=$HAS_REFERENCE_FILE"
