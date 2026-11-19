# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# Clocks of the UltraScale+ build of nd_top. Synthesis only: no pins are placed.
create_clock -name clk_a -period 4.000 [get_ports clk_a]
create_clock -name clk_b -period 10.000 [get_ports clk_b]
