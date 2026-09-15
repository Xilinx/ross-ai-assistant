// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// lint_violation_top.v — Design with intentional RTL lint violations
//
// This module contains 10 deliberate lint issues for the rtl-lint skill to detect:
//   1. ASSIGN-1  : Arithmetic overflow (result wider than output)
//   2. ASSIGN-2  : Mixed signed/unsigned arithmetic
//   3. ASSIGN-3  : Shift amount >= signal width
//   4. ASSIGN-5  : Signal used but never assigned (undriven)
//   5. ASSIGN-6  : Signal assigned but never read (dead code)
//   6. ASSIGN-14 : Duplicate case branches
//   7. INFER-1   : Latch inferred (missing else)
//   8. INFER-2   : Incomplete case (no default)
//   9. CLOCK-1   : Mixed clock edges
//  10. RESET-2   : Incomplete async reset coverage

module lint_violation_top (
    input  wire        clk,
    input  wire        rst,
    input  wire [7:0]  data_a,
    input  wire [7:0]  data_b,
    input  wire signed [7:0] data_signed,
    input  wire [1:0]  sel,
    input  wire        enable,
    input  wire [3:0]  mode,
    output reg  [7:0]  result,
    output wire [7:0]  sum_out,
    output reg  [7:0]  mux_out,
    output reg  [7:0]  case_out,
    output reg  [7:0]  neg_reg
);

    // =========================================================
    // BUG 1 (ASSIGN-1): Arithmetic overflow
    // Adding two 8-bit values needs 9 bits, but output is 8 bits
    // =========================================================
    assign sum_out = data_a + data_b;  // 8+8 can produce 9-bit result

    // =========================================================
    // BUG 2 (ASSIGN-2): Mixed signed/unsigned arithmetic
    // data_a is unsigned, data_signed is signed — mixed operation
    // =========================================================
    wire [8:0] mixed_result;
    assign mixed_result = data_a + data_signed;  // signed + unsigned without cast

    // =========================================================
    // BUG 3 (ASSIGN-3): Shift amount >= signal width
    // data_a is 8 bits, shift by 10 is always >= width
    // =========================================================
    parameter SHIFT_AMT = 10;
    wire [7:0] shifted;
    assign shifted = data_a >> SHIFT_AMT;  // shift 10 on 8-bit signal

    // =========================================================
    // BUG 4 (ASSIGN-5): Signal used but never assigned
    // phantom_sig is declared and used but never driven
    // =========================================================
    wire [7:0] phantom_sig;
    wire [7:0] phantom_use;
    assign phantom_use = phantom_sig ^ data_a;  // phantom_sig has no driver

    // =========================================================
    // BUG 5 (ASSIGN-6): Signal assigned but never read
    // dead_signal is driven but nobody reads it
    // =========================================================
    wire [7:0] dead_signal;
    assign dead_signal = data_a & data_b;  // assigned but never used

    // =========================================================
    // BUG 6 (ASSIGN-14): Duplicate case branches
    // Two case items have the same value
    // =========================================================
    always @(*) begin
        case (mode)
            4'b0000: case_out = 8'h01;
            4'b0001: case_out = 8'h02;
            4'b0010: case_out = 8'h03;
            4'b0000: case_out = 8'hFF;  // DUPLICATE of first branch!
            default: case_out = 8'h00;
        endcase
    end

    // =========================================================
    // BUG 7 (INFER-1): Latch inferred — missing else
    // Combinational always block without full coverage
    // =========================================================
    always @(*) begin
        if (enable)
            mux_out = data_a;
        // NO else clause — latch inferred for mux_out
    end

    // =========================================================
    // BUG 8 (INFER-2): Incomplete case — no default
    // Only 2 of 4 possible sel values handled
    // =========================================================
    reg [7:0] sel_out;
    always @(*) begin
        case (sel)
            2'b00: sel_out = data_a;
            2'b01: sel_out = data_b;
            // Missing 2'b10, 2'b11, and no default!
        endcase
    end

    // =========================================================
    // BUG 9 (CLOCK-1): Mixed clock edges
    // Same clock used with posedge AND negedge
    // =========================================================
    always @(negedge clk) begin
        if (!rst)
            neg_reg <= 8'd0;
        else
            neg_reg <= data_b;
    end

    // Result uses posedge (combined with neg_reg using negedge = CLOCK-1)
    // =========================================================
    // BUG 10 (RESET-2): Incomplete async reset
    // reg_a is reset but reg_b and reg_c are NOT reset
    // =========================================================
    reg [7:0] reg_a, reg_b, reg_c;

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            reg_a <= 8'd0;
            // reg_b and reg_c are NOT reset — RESET-2 violation
        end else begin
            reg_a <= data_a;
            reg_b <= data_b;
            reg_c <= reg_a + reg_b;
        end
    end

    always @(posedge clk) begin
        result <= reg_c + sel_out + phantom_use + shifted + mixed_result[7:0];
    end

endmodule
