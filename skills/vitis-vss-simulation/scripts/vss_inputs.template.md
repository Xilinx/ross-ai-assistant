# vitis-vss-simulation — inputs file (template)
#
# Fill in the values, save a copy (e.g. vss_inputs.md), and pass its path to the
# skill. Lines are `KEY = VALUE`; blank lines and `#` comments are ignored.
# Leave a key blank (or omit it) to be prompted for just that one.

# COMPILED_OUT_DIRECTORY — absolute path where all build artifacts land (vss/, the
# Vivado project, logs). Created if missing.
COMPILED_OUT_DIRECTORY =

# SIMULATOR — which simulator to target.
#   xsim    = ships with Vitis (default; needs no extra SIM_* paths)
#   questa | vcs | riviera | xcelium = third-party (fill the SIM_* paths below)
SIMULATOR              = xsim

# SIM_* paths — ONLY for third-party simulators; the skill never hardcodes or
# derives these. Leave all as N/A when SIMULATOR = xsim.
#   SIM_LIB_MAP_PATH     precompiled Xilinx sim libraries (-lib_map_path)
#   SIM_GCC_INSTALL_PATH simulator gcc bin (-gcc_install_path; also VCS GCC_PATH)
#   SIM_INSTALL_PATH     simulator tool bin (-install_path)
#   SIM_TOOL_HOME        simulator install home (for the AIE c_rts compile)
SIM_LIB_MAP_PATH       = N/A
SIM_GCC_INSTALL_PATH   = N/A
SIM_INSTALL_PATH       = N/A
SIM_TOOL_HOME          = N/A

# WORK_DIR — absolute path to the already-compiled AIE work directory (the folder
# holding .target, which must read `hw`; e.g. <component>/build/hw/Work).
WORK_DIR               =

# VSS — absolute path to the already-generated .vss file.
VSS                    =

# PART — target part the AIE design was compiled for. Default shown.
PART                   = xcvc1902-vsva2197-2MP-e-S

# TB_TOP — path to the top testbench file (the HDL that instantiates the VSS
# wrapper).
TB_TOP                 =

# TB_TOP_MODULE — name of the top testbench module defined in TB_TOP (e.g. vss_tb).
TB_TOP_MODULE          =

# TB_SOURCES — the testbench's dependent files (every file the TB needs besides
# TB_TOP), space-separated; or none.
TB_SOURCES             = none

# SIM_RUNTIME — how long the simulation may run: `all` (default; runs until the
# testbench calls $finish or $stop, or no events are left; with a free-running clock
# and no $finish the run does not end) or a time such as 10us. Vivado's own default stops the
# run at 1000ns, which cuts off any design that needs longer.
SIM_RUNTIME            = all

# COMPARE_GOLDEN — yes | no. yes: after the simulation you are asked which output
# file pairs with which golden file (plain .txt data files created by the testbench).
COMPARE_GOLDEN         = no
