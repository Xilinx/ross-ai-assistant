# vitis-hardware-emulation — inputs file (template)
# Version: 1.0.0
#
# Fill in the values, save a copy (e.g. hwemu_inputs.md), and pass its path to the
# skill. Lines are `KEY = VALUE`; blank lines and `#` comments are ignored.
# Leave a key blank (or omit it) to be prompted for just that one.
# Keys that your FLOW does not use are ignored (see the notes on each key).
#
# This skill only RUNS the emulation — the product below must already be built
# (AIE compile, PL compile, link, package, host cross-compile).

# FLOW — the software flow of the built product: edf | petalinux | baremetal. No default.
FLOW                   =

# COMPILED_OUT_DIRECTORY — build directory holding the built product. Staging
# (base_camp_images/) and the run log (embedded_run.log) land here. Absolute path.
COMPILED_OUT_DIRECTORY =

# PLATFORM — hardware .xpfm the product was built for. Site-specific. The board (vrk160,
# vek385, vck190, vek280) is read from the file name. edf and baremetal: for a base platform
# of a Vitis install (under .../Vitis/base_platforms/) a plain vck190_base... / vek280_base...
# / vrk160_base... / vek385_base... (no xilinx_ prefix, not _dfx). petalinux: only the plain
# xilinx_vck190_base... / xilinx_vek280_base... base platforms of a Vitis install (not the
# _dfx ones).
PLATFORM               =

# PACKAGE_DIR — packaged hw_emu dir holding launch_hw_emu.sh. edf: BOOT.bin (and, on
# VRK160/VEK385, a .pdi and a .dtbo; any file names); petalinux: sd_card.img;
# baremetal: a .pdi and no BOOT.bin. Absolute path, no default.
PACKAGE_DIR            =

# XCLBIN — packaged .xclbin (any file name). edf and petalinux only. Absolute path.
XCLBIN                 =

# HOST_EXE — edf, petalinux: cross-compiled embedded host executable; baremetal: the ELF
# (any file name). Absolute path, no default.
HOST_EXE               =

# RUN_SCRIPT — edf and petalinux: the runner script the launch runs in the guest (absolute
# path on the host; its file name is the name it has in the image). edf: it must be
# executable. No default.
RUN_SCRIPT             =

# YOCTO_ENV — SDK environment-setup script, sourced before `wic cp`. edf, and baremetal on the
# SD boards (vck190, vek280); not used for baremetal on vek385 / vrk160. Site-specific.
YOCTO_ENV              =

# QEMU_PREBUILT — QEMU prebuilt directory. edf and baremetal. Site-specific.
QEMU_PREBUILT          =

# SIMULATOR — simulator the product was built for. Leave as xsim (built-in
# default) unless third-party: xsim | questa | vcs | riviera | xcelium.
SIMULATOR              = xsim

# CAPTURE_VCD — yes | no. yes: capture the AIE waveform (VCD) during the run. Blank = ask.
CAPTURE_VCD            = no

# AIE_WORK_DIR — only used when CAPTURE_VCD = yes. Absolute path of the AIE compile
# Work directory of the built product (its name and location vary; the folder holding
# `.target`, which reads `hw`). Optional: leave blank to capture the VCD without it; the
# run summary then has no AIE compiler summary and no GMIO throughput.
AIE_WORK_DIR           =
