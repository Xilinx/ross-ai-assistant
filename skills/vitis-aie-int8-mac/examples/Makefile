#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#

MY_APP  = int8_mac_test_app
AIE_SRC = int8_mac_test.cpp
PART   ?= xcvc1902-vsva2197-2MP-e-S

# pipefail is required below: without it make sees tee's status, not x86simulator's.
SHELL := /bin/bash

.PHONY: x86com x86sim x86all clean

x86com:
	v++ --compile \
	    --config aie.cfg \
	    --mode aie \
	    --target x86sim \
	    --part=$(PART) \
	    $(MY_APP).cpp \
	    --aie.output-archive=libadf.a

x86sim:
	set -o pipefail; x86simulator --pkg-dir=./Work 2>&1 | tee x86sim.log
	@! grep -q chess_error x86sim.log \
	  || (echo "ILLEGAL PARAMETERS (x86sim chess_error) -- see x86sim.log"; false)
	@grep -qF "Summary: 0 failure(s)" x86sim.log \
	  || (echo "EXAMPLES FAILED -- see x86sim.log"; false)

x86all: clean x86com x86sim

clean:
	rm -rf Work libadf.a x86simulator_output *.log .Xil ISS_RPC_SERVER_PORT
