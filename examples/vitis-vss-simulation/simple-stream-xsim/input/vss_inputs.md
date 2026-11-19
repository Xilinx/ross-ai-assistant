# vitis-vss-simulation — filled inputs file (non-interactive)
#
# Change to this directory (input/) before you read this file.
# Every path below is relative to input/.
# TEST_DIR == COMPILED_OUT_DIRECTORY == "." so the path checks and the compile
# use the same directory. The graph names the kernel as aie/src/kernel.cpp,
# which resolves here. Build artifacts are written here.

DESIGN_TYPE            = aie

# SIMULATOR — target simulator. xsim ships with Vitis and needs no SIM_* paths.
SIMULATOR              = xsim

# SIM_* paths — only for third-party simulators; N/A for xsim.
SIM_LIB_MAP_PATH       = N/A
SIM_GCC_INSTALL_PATH   = N/A
SIM_INSTALL_PATH       = N/A
SIM_TOOL_HOME          = N/A

TEST_DIR               = .

COMPILED_OUT_DIRECTORY = .

GRAPH_SOURCE           = aie/src/graph.cpp

PL_KERNELS             = N/A

AIE_COMPILE_TARGET     = xcvc1902-vsva2197-2MP-e-S

AIE_COMPILE_CFG        = none

VSS_CFG                = vss.cfg

TB_TOP                 = vss_tb.v

TB_SOURCES             = send_data.v
