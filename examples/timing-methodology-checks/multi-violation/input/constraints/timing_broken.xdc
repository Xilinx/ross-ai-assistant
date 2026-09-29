# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# timing_broken.xdc — Deliberately incorrect constraints for methodology tutorial
#
# This file contains INTENTIONAL errors that trigger timing methodology
# violations. The timing-methodology-checks skill should detect and fix them.

# ============================================================
# CORRECT: Primary clock on clk_a at 100 MHz
# ============================================================
create_clock -period 10.000 -name clk_a [get_ports clk_a]

# ============================================================
# BUG 1 (TIMING-18): Duplicate create_clock on same port with different name
# The skill should detect this redundant clock definition.
# ============================================================
create_clock -period 10.000 -name clk_a_dup [get_ports clk_a]

# ============================================================
# BUG 2 (TIMING-2): create_clock on wrong object
# clk_b should be constrained on the PORT, but here we (intentionally)
# try to constrain a non-existent generated clock source pin.
# This is commented-out because it would error; instead we simply
# OMIT the constraint for clk_b entirely, causing unconstrained paths.
# ============================================================
# (clk_b constraint intentionally missing — causes methodology warnings)

# ============================================================
# BUG 3 (TIMING-17): set_false_path with non-existent clock
# References a clock name that doesn't exist (typo).
# ============================================================
set_false_path -from [get_clocks clk_a] -to [get_clocks clk_b_typo]

# ============================================================
# BUG 4: No set_clock_groups for async crossing (TIMING-6 area)
# clk_a and clk_b are asynchronous but no relationship is defined.
# The CDC path from domain A → domain B has no timing exception.
# ============================================================
# (intentionally missing: set_clock_groups -asynchronous -group clk_a -group clk_b)

# ============================================================
# Missing I/O constraints (TIMING-9, TIMING-10 area)
# No set_input_delay or set_output_delay for data ports.
# ============================================================
# (intentionally missing: set_input_delay, set_output_delay)
