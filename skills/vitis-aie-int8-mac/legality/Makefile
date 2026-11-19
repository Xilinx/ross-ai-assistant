#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Parameter legality gate: compiles the parameter sets in legality.cpp for
# --target hw and fails on any chess_error or chess_warning. zstart > 15
# builds with only a warning, so the exit status alone is not enough.
#
#   make        compile for hw; non-zero exit means a parameter is illegal
#   make clean

MY_APP = legality_app
PART  ?= xcvc1902-vsva2197-2MP-e-S

# pipefail is required below: without it the gate sees tee's status, not v++'s.
SHELL := /bin/bash

.PHONY: check clean

check:
	rm -rf Work_hw
	mkdir -p data
	set -o pipefail; v++ --compile --config aie.cfg --mode aie --part=$(PART) $(MY_APP).cpp \
	    --target hw --work_dir=./Work_hw 2>&1 | tee compile_hw.log
	@! grep -qE "chess_error|chess_warning|must be a multiple|must be in (the )?range" compile_hw.log \
	  || (echo "ILLEGAL PARAMETERS -- see compile_hw.log"; false)
	@grep -qE "ERROR:0" compile_hw.log \
	  || (echo "COMPILE REPORTED ERRORS (or no summary) -- see compile_hw.log"; false)
	@echo "legality gate PASSED"

clean:
	rm -rf Work_hw data compile_hw.log .Xil
