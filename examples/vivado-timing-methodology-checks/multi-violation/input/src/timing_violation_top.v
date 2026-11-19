// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// timing_violation_top.v — Dual-clock design with deliberate constraint issues
//
// This design has multiple clock domains and crossing logic that,
// combined with intentionally incorrect XDC constraints, triggers
// several timing methodology violations for the skill to resolve.

module timing_violation_top (
    input  wire        clk_a,       // 100 MHz primary clock
    input  wire        clk_b,       // 150 MHz secondary clock
    input  wire        rst_n,       // Active-low async reset
    input  wire [7:0]  data_in,
    output wire [7:0]  data_out_a,
    output wire [7:0]  data_out_b,
    output wire        sync_valid
);

    // =========================================================
    // Domain A: 100 MHz logic (simple pipeline)
    // =========================================================
    reg [7:0] pipe_a_0, pipe_a_1, pipe_a_2;

    always @(posedge clk_a or negedge rst_n) begin
        if (!rst_n) begin
            pipe_a_0 <= 8'd0;
            pipe_a_1 <= 8'd0;
            pipe_a_2 <= 8'd0;
        end else begin
            pipe_a_0 <= data_in;
            pipe_a_1 <= pipe_a_0;
            pipe_a_2 <= pipe_a_1;
        end
    end

    assign data_out_a = pipe_a_2;

    // =========================================================
    // Domain B: 150 MHz logic (simple pipeline)
    // =========================================================
    reg [7:0] pipe_b_0, pipe_b_1;

    always @(posedge clk_b or negedge rst_n) begin
        if (!rst_n) begin
            pipe_b_0 <= 8'd0;
            pipe_b_1 <= 8'd0;
        end else begin
            pipe_b_0 <= data_in;
            pipe_b_1 <= pipe_b_0;
        end
    end

    assign data_out_b = pipe_b_1;

    // =========================================================
    // Clock Domain Crossing: A → B (2-stage synchronizer)
    // Missing set_clock_groups in XDC will trigger TIMING-6
    // =========================================================
    reg [7:0] cdc_sync_0, cdc_sync_1;
    reg       cdc_valid_0, cdc_valid_1;

    always @(posedge clk_b or negedge rst_n) begin
        if (!rst_n) begin
            cdc_sync_0  <= 8'd0;
            cdc_sync_1  <= 8'd0;
            cdc_valid_0 <= 1'b0;
            cdc_valid_1 <= 1'b0;
        end else begin
            cdc_sync_0  <= pipe_a_2;     // CDC: clk_a → clk_b
            cdc_sync_1  <= cdc_sync_0;
            cdc_valid_0 <= |pipe_a_2;
            cdc_valid_1 <= cdc_valid_0;
        end
    end

    assign sync_valid = cdc_valid_1;

endmodule
