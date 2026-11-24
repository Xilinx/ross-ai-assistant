// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// nd_top: a small two-clock design for the vivado-netlist-debug-insert example.
// clk_a domain: an AXI-Stream producer (m_axis_*) driven by a packet state
//   machine (state, one-hot after synthesis), and a consumer that sums the data.
// clk_b domain: a 16-bit control counter (count) and a control register (ctrl)
//   whose upper four bits are constant zero, so synthesis removes them.
// Every register reaches an output through a reduction XOR, so synthesis keeps it.

module nd_top (
    input  wire       clk_a,
    input  wire       clk_b,
    input  wire       rst_n,
    output wire [3:0] led
);

    // ---------------- clk_a: AXI-Stream producer and consumer ----------------
    localparam [3:0] S_IDLE = 4'b0001, S_HEAD = 4'b0010, S_BODY = 4'b0100, S_TAIL = 4'b1000;

    (* fsm_encoding = "one_hot" *) reg [3:0] state;
    reg [31:0] m_axis_tdata;
    reg        m_axis_tvalid;
    reg        m_axis_tlast;
    reg        m_axis_tready;
    reg [3:0]  beat;
    reg [31:0] seq;
    reg [31:0] sum;

    wire fire = m_axis_tvalid & m_axis_tready;

    always @(posedge clk_a) begin
        if (!rst_n) begin
            state         <= S_IDLE;
            m_axis_tdata  <= 32'd0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
            beat          <= 4'd0;
            seq           <= 32'd0;
        end else begin
            seq <= seq + 32'd3;
            case (state)
                S_IDLE: begin
                    m_axis_tvalid <= 1'b0;
                    m_axis_tlast  <= 1'b0;
                    if (seq[4]) state <= S_HEAD;
                end
                S_HEAD: begin
                    m_axis_tdata  <= {16'hA55A, seq[15:0]};
                    m_axis_tvalid <= 1'b1;
                    m_axis_tlast  <= 1'b0;
                    beat          <= 4'd0;
                    if (fire) state <= S_BODY;
                end
                S_BODY: begin
                    if (fire) begin
                        m_axis_tdata <= seq ^ {beat, 28'h0};
                        beat         <= beat + 4'd1;
                        if (beat == 4'd6) begin
                            m_axis_tlast <= 1'b1;
                            state        <= S_TAIL;
                        end
                    end
                end
                S_TAIL: begin
                    if (fire) begin
                        m_axis_tvalid <= 1'b0;
                        m_axis_tlast  <= 1'b0;
                        state         <= S_IDLE;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end

    // Consumer: ready follows a pattern from the sequence counter; sums each beat.
    always @(posedge clk_a) begin
        if (!rst_n) begin
            m_axis_tready <= 1'b0;
            sum           <= 32'd0;
        end else begin
            m_axis_tready <= seq[1] | seq[3];
            if (fire) sum <= sum + m_axis_tdata + {31'd0, m_axis_tlast};
        end
    end

    // ---------------- clk_b: control counter and control register ----------------
    reg [15:0] count;
    reg [7:0]  ctrl;

    always @(posedge clk_b) begin
        if (!rst_n) begin
            count <= 16'd0;
            ctrl  <= 8'd0;
        end else begin
            count <= count + 16'd1;
            ctrl  <= {4'b0000, count[15:12]};
        end
    end

    assign led[0] = ^sum;
    assign led[1] = ^state ^ ^beat;
    assign led[2] = ^count;
    assign led[3] = ^ctrl;

endmodule
