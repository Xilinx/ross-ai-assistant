#!/bin/bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT

cd ../../scripts
python3 -m hls_dataflow.dataflow_info_print "$1"
