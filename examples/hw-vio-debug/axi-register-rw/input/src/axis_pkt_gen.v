// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// axis_pkt_gen.v — VIO-controlled AXI-Stream packet generator
//
// On start_stream pulse, generates one packet of pkt_length beats.
// TDATA increments from 0. TLAST asserts on the final beat.
// VIO outputs provide: start_stream, pkt_length
// VIO inputs  observe: stream_busy, pkt_count

module axis_pkt_gen #(
    parameter DATA_WIDTH = 32
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // VIO control
    input  wire                    start_stream,
    input  wire [7:0]              pkt_length,    // 1-255 beats per packet
    output reg                     stream_busy,
    output reg  [15:0]             pkt_count,

    // AXI-Stream Master
    output reg  [DATA_WIDTH-1:0]   m_axis_tdata,
    output reg                     m_axis_tvalid,
    output reg                     m_axis_tlast,
    output wire [DATA_WIDTH/8-1:0] m_axis_tkeep,
    input  wire                    m_axis_tready
);

    assign m_axis_tkeep = {(DATA_WIDTH/8){1'b1}};

    reg [7:0] beat_cnt;
    reg [7:0] pkt_len_reg;
    reg start_stream_d;

    wire start_pulse = start_stream & ~start_stream_d;

    always @(posedge aclk) begin
        if (!aresetn)
            start_stream_d <= 1'b0;
        else
            start_stream_d <= start_stream;
    end

    localparam IDLE = 1'b0, SENDING = 1'b1;
    reg state;

    always @(posedge aclk) begin
        if (!aresetn) begin
            state        <= IDLE;
            stream_busy  <= 1'b0;
            pkt_count    <= 16'd0;
            m_axis_tdata <= {DATA_WIDTH{1'b0}};
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
            beat_cnt     <= 8'd0;
            pkt_len_reg  <= 8'd0;
        end else begin
            case (state)
                IDLE: begin
                    m_axis_tvalid <= 1'b0;
                    m_axis_tlast  <= 1'b0;
                    stream_busy   <= 1'b0;
                    if (start_pulse && (pkt_length != 8'd0)) begin
                        state        <= SENDING;
                        stream_busy  <= 1'b1;
                        pkt_len_reg  <= pkt_length;
                        beat_cnt     <= 8'd0;
                        m_axis_tdata <= {DATA_WIDTH{1'b0}};
                        m_axis_tvalid <= 1'b1;
                        m_axis_tlast  <= (pkt_length == 8'd1);
                    end
                end

                SENDING: begin
                    if (m_axis_tready && m_axis_tvalid) begin
                        if (m_axis_tlast) begin
                            // Packet done
                            m_axis_tvalid <= 1'b0;
                            m_axis_tlast  <= 1'b0;
                            stream_busy   <= 1'b0;
                            pkt_count     <= pkt_count + 16'd1;
                            state         <= IDLE;
                        end else begin
                            beat_cnt     <= beat_cnt + 8'd1;
                            m_axis_tdata <= m_axis_tdata + 1;
                            // Assert TLAST on the beat before the last
                            if (beat_cnt + 8'd2 == pkt_len_reg)
                                m_axis_tlast <= 1'b1;
                        end
                    end
                end
            endcase
        end
    end

endmodule
