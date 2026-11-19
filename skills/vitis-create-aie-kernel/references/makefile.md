<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel Makefile

This reference describes how to create the Makefile required for an AI Engine kernel

## Workflow Steps

1. Create Default Makefile

## Create Default Makefile

* Create the Makefile for a specific part that will be specified by the user. 
* Some example part names are `xcvc1902-vsva2197-2MP-e-S` and `xcve2802-vsvh1760-2MP-e-S`. 
* Assume part name specified by the user is USER_PART.

Create `Makefile` using the template below where KERNEL refers to the name of the AI Engine kernel.

Notes on the template:
* `SHELL` and `.SHELLFLAGS` make a failing `v++` or simulator fail the `make` target even though the output is piped through `tee`.
* The x86 flow builds into `Work_x86` and removes its archive, so `make` (hw, `Work`) and `make x86com` never mask each other.
* `x86simulator` exits 0 on a deadlock (for example when an input file is too short), so the `x86sim` target fails if the log reports one.

```
SHELL             := /bin/bash
.SHELLFLAGS       := -o pipefail -c
SIM_FIFO          := false
MY_APP            := KERNEL_app
MY_SOURCES        := ${MY_APP}.cpp KERNEL_graph.h KERNEL.hpp KERNEL.cpp
PART_USE          := USER_PART
CHECK_FIFO        := --aie.evaluate-fifo-depth --aie.Xrouter=disablePathBalancing
DSPLIB_OPTS 	  := --include=${DSPLIB_ROOT}/L2/include/aie \
		             --include=${DSPLIB_ROOT}/L1/include/aie \
		             --include=${DSPLIB_ROOT}/L1/src/aie
AIE_OUTPUT := libadf.a
AIE_FLAGS :=	${DSPLIB_OPTS} --part=${PART_USE} ${MY_APP}.cpp --aie.output-archive=${AIE_OUTPUT}
AIE_FLAGS :=    ${AIE_FLAGS} --aie.constraints=${MY_APP}.aiecst
AIE_SIM_FLAGS :=    --hang-detect-time=500000 --display-run-interval=1000

ifeq (${SIM_FIFO}, true)
	AIE_FLAGS := ${AIE_FLAGS} ${CHECK_FIFO}
endif

.PHONY: all clean x86com x86sim sim profile trace

all:		${AIE_OUTPUT}

${AIE_OUTPUT}:	${MY_SOURCES}
	v++ --compile --config aie.cfg --mode aie --target=hw ${AIE_FLAGS} |& tee log

x86com:
	v++ --compile --config aie.cfg --mode aie --target=x86sim ${AIE_FLAGS} --work_dir=./Work_x86 |& tee log
	rm -f ${AIE_OUTPUT}

sim:
	aiesimulator ${AIE_SIM_FLAGS} |& tee -a log

x86sim:
	x86simulator --pkg-dir=./Work_x86 |& tee -a log
	! grep -q "Detected deadlock" log

profile:
	aiesimulator ${AIE_SIM_FLAGS} --online -wdb -text --profile |& tee -a log

trace:
	aiesimulator ${AIE_SIM_FLAGS} --online -wdb -text |& tee -a log

clean:
	rm -rf log x86simulator_output aiesimulator_output .AIE_SIM_CMD_LINE_OPTIONS .Xil
	rm -rf Work Work_x86 ${AIE_OUTPUT} *.log *.wcfg *.wdb *.csv sol.db function_wdb_dir trdata.aiesim vfs_work
	rm -rf pl_sample_counts throughput_info.json .Xil ISS_RPC_SERVER_PORT
```
