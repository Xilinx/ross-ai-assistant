# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Clock constraint for the Clocking Wizard 200 MHz output
# (The 100 MHz input comes from CIPS pl0_ref_clk which is auto-constrained)
create_generated_clock -name clk_200 -source [get_pins u_clk_wiz/clk_in1] \
    -multiply_by 2 [get_pins u_clk_wiz/clk_out1]

# False path on async reset
set_false_path -from [get_ports] -to [get_pins {count_reg[*]/CLR}]

# Output delay (example constraint for counter_out)
set_output_delay -clock clk_200 2.0 [get_ports counter_out[*]]
