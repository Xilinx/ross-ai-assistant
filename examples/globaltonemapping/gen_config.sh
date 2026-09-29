#!/bin/bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# Generate hls_config.cfg from template with OpenCV paths
# Headers are organized in ./include/ subdirectory

if [ -z "$OPENCV_INCLUDE" ] || [ -z "$OPENCV_LIB" ]; then
    echo "Error: OPENCV_INCLUDE and OPENCV_LIB environment variables must be set"
    echo "Example:"
    echo "  export OPENCV_INCLUDE=/usr/include/opencv4"
    echo "  export OPENCV_LIB=/usr/lib64"
    exit 1
fi

echo "Generating hls_config.cfg..."
echo "  OPENCV_INCLUDE = $OPENCV_INCLUDE"
echo "  OPENCV_LIB = $OPENCV_LIB"
echo "  Local headers = ./include/"

# Set absolute path for test image
export PROJECT_DIR=$(pwd)
echo "  PROJECT_DIR = $PROJECT_DIR"

envsubst < hls_config.tmpl > hls_config.cfg

echo "Done. hls_config.cfg generated successfully."
