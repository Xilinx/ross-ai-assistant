#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Measures the mul8 / mul16 lane x column -> index maps in x86simulator.
#   make run     build for x86sim, run, tee probe.log
#   make clean
#
# This project is x86sim-only by design: the measurement loops are fully
# unrolled per parameter set and overflow one tile's program memory under
# --target hw. Parameter legality is checked by ../legality instead.

MY_APP = probe_app
PART  ?= xcvc1902-vsva2197-2MP-e-S
COMMON = --compile --config aie.cfg --mode aie --part=$(PART) $(MY_APP).cpp

# pipefail is required below: without it make sees tee's status, not x86simulator's.
SHELL := /bin/bash

.PHONY: run clean

run:
	mkdir -p data
	v++ $(COMMON) --target x86sim --work_dir=./Work_x86 > compile_x86.log 2>&1
	set -o pipefail; x86simulator --pkg-dir=./Work_x86 2>&1 | tee probe.log
	@! grep -q chess_error probe.log \
	  || (echo "ILLEGAL PARAMETERS (x86sim chess_error) -- see probe.log"; false)
	@grep -q "PROBE COMPLETE" probe.log \
	  || (echo "PROBE DID NOT COMPLETE -- see probe.log"; false)

clean:
	rm -rf Work_x86 Work_hw x86simulator_output data compile_x86.log \
	       compile_hw.log probe.log .Xil
