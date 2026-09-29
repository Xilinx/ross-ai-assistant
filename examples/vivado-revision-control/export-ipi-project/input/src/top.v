// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// top.v — RTL top module with XCI IP instantiation + BD wrapper + counter
//
// Demonstrates a mixed-source project:
//   - Standalone XCI IP (Clocking Wizard) instantiated in RTL
//   - Block Design wrapper instantiated in RTL
//   - Local RTL logic (counter)
//   - XDC constraints (separate file)

module top (
    output wire [31:0] counter_out
);

    // Wires from BD subsystem
    wire pl0_ref_clk;
    wire pl0_resetn;

    // Clocking Wizard output (200 MHz from 100 MHz input)
    wire clk_200;
    wire clk_locked;

    // --- Block Design instantiation (CIPS provides clock + reset) ---
    bd_subsystem_wrapper u_bd (
        .pl0_ref_clk (pl0_ref_clk),
        .pl0_resetn  (pl0_resetn)
    );

    // --- XCI IP: Clocking Wizard (100 MHz → 200 MHz) ---
    clk_wiz_0 u_clk_wiz (
        .clk_in1  (pl0_ref_clk),
        .resetn   (pl0_resetn),
        .clk_out1 (clk_200),
        .locked   (clk_locked)
    );

    // --- Local RTL logic: 32-bit counter on 200 MHz domain ---
    reg [31:0] count_reg;

    always @(posedge clk_200) begin
        if (!clk_locked)
            count_reg <= 32'd0;
        else
            count_reg <= count_reg + 32'd1;
    end

    assign counter_out = count_reg;

endmodule
