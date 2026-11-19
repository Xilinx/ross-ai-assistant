# vitis-hardware-emulation — inputs file
#
# This example does not include a design. Fill every path before you run it.
#
# TEST_DIR is your design directory. These paths are relative to TEST_DIR
# and must exist there:
#   aie/src/graph.cpp     pure AI Engine graph, GMIO ports only, no PLIO
#   aie/aiecompiler.cfg   compiler configuration, or set AIE_COMPILE_CFG to none
#   system.cfg            link configuration for this GMIO design
#   src/host.cpp          embedded host for the VCK190 Linux image
#
# PLATFORM, SYSROOT, YOCTO_ENV, and QEMU_PREBUILT are absolute paths.
# Take all four from the same Vitis installation.
# Replace each /absolute/path/to value. Do not leave a placeholder.

DESIGN_TYPE            = aie

TEST_DIR               = /absolute/path/to/gmio-design

COMPILED_OUT_DIRECTORY = /absolute/path/to/gmio-design

GRAPH_SOURCE           = aie/src/graph.cpp

AIE_COMPILE_CFG        = aie/aiecompiler.cfg

PL_KERNELS             = N/A

PLATFORM               = /absolute/path/to/vck190_base.xpfm

SYSTEM_CFG             = system.cfg

SIMULATOR              = xsim

SIMULATOR_CFG          = N/A

HOST_SRC               = src/host.cpp

SYSROOT                = /absolute/path/to/sysroot

YOCTO_ENV              = /absolute/path/to/environment-setup-cortexa72-cortexa53-amd-linux

QEMU_PREBUILT          = /absolute/path/to/vck190_qemu_prebuilt
