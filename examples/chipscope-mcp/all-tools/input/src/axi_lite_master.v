// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// axi_lite_master.v — VIO-controlled AXI4-Lite master
//
// Drives single write or read transactions on an AXI4-Lite interface.
// VIO outputs provide: start_wr, start_rd, wr_addr, wr_data, rd_addr
// VIO inputs  observe: rd_data, busy, done

module axi_lite_master #(
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32
)(
    input  wire                    aclk,
    input  wire                    aresetn,

    // VIO control interface
    input  wire                    start_wr,
    input  wire                    start_rd,
    input  wire [ADDR_WIDTH-1:0]   wr_addr,
    input  wire [DATA_WIDTH-1:0]   wr_data,
    input  wire [ADDR_WIDTH-1:0]   rd_addr,
    output reg  [DATA_WIDTH-1:0]   rd_data,
    output reg                     busy,
    output reg                     done,

    // AXI4-Lite Master interface
    output reg  [ADDR_WIDTH-1:0]   m_axi_awaddr,
    output reg                     m_axi_awvalid,
    input  wire                    m_axi_awready,
    output reg  [DATA_WIDTH-1:0]   m_axi_wdata,
    output reg  [DATA_WIDTH/8-1:0] m_axi_wstrb,
    output reg                     m_axi_wvalid,
    input  wire                    m_axi_wready,
    input  wire [1:0]              m_axi_bresp,
    input  wire                    m_axi_bvalid,
    output reg                     m_axi_bready,
    output reg  [ADDR_WIDTH-1:0]   m_axi_araddr,
    output reg                     m_axi_arvalid,
    input  wire                    m_axi_arready,
    input  wire [DATA_WIDTH-1:0]   m_axi_rdata,
    input  wire [1:0]              m_axi_rresp,
    input  wire                    m_axi_rvalid,
    output reg                     m_axi_rready
);

    localparam [2:0]
        IDLE      = 3'd0,
        WR_AW_W   = 3'd1,
        WR_RESP   = 3'd3,
        RD_ADDR   = 3'd4,
        RD_DATA   = 3'd5,
        DONE_ST   = 3'd6;

    reg [2:0] state;
    reg start_wr_d, start_rd_d;
    reg aw_done, w_done;  // track AW/W handshakes independently

    // Edge detect on start signals
    wire start_wr_pulse = start_wr & ~start_wr_d;
    wire start_rd_pulse = start_rd & ~start_rd_d;

    always @(posedge aclk) begin
        if (!aresetn) begin
            start_wr_d <= 1'b0;
            start_rd_d <= 1'b0;
        end else begin
            start_wr_d <= start_wr;
            start_rd_d <= start_rd;
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            state         <= IDLE;
            busy          <= 1'b0;
            done          <= 1'b0;
            rd_data       <= {DATA_WIDTH{1'b0}};
            m_axi_awaddr  <= {ADDR_WIDTH{1'b0}};
            m_axi_awvalid <= 1'b0;
            m_axi_wdata   <= {DATA_WIDTH{1'b0}};
            m_axi_wstrb   <= {(DATA_WIDTH/8){1'b0}};
            m_axi_wvalid  <= 1'b0;
            m_axi_bready  <= 1'b0;
            m_axi_araddr  <= {ADDR_WIDTH{1'b0}};
            m_axi_arvalid <= 1'b0;
            m_axi_rready  <= 1'b0;
            aw_done       <= 1'b0;
            w_done        <= 1'b0;
        end else begin
            done <= 1'b0; // pulse

            case (state)
                IDLE: begin
                    busy <= 1'b0;
                    if (start_wr_pulse) begin
                        state         <= WR_AW_W;
                        busy          <= 1'b1;
                        m_axi_awaddr  <= wr_addr;
                        m_axi_awvalid <= 1'b1;
                        m_axi_wdata   <= wr_data;
                        m_axi_wstrb   <= {(DATA_WIDTH/8){1'b1}};
                        m_axi_wvalid  <= 1'b1;
                        aw_done       <= 1'b0;
                        w_done        <= 1'b0;
                    end else if (start_rd_pulse) begin
                        state         <= RD_ADDR;
                        busy          <= 1'b1;
                        m_axi_araddr  <= rd_addr;
                        m_axi_arvalid <= 1'b1;
                    end
                end

                WR_AW_W: begin
                    // Track AW and W handshakes independently
                    if (m_axi_awready && m_axi_awvalid) begin
                        m_axi_awvalid <= 1'b0;
                        aw_done <= 1'b1;
                    end
                    if (m_axi_wready && m_axi_wvalid) begin
                        m_axi_wvalid <= 1'b0;
                        w_done <= 1'b1;
                    end
                    // Both accepted (could be same cycle or different)
                    if ((aw_done || (m_axi_awready && m_axi_awvalid)) &&
                        (w_done  || (m_axi_wready  && m_axi_wvalid))) begin
                        m_axi_bready <= 1'b1;
                        state <= WR_RESP;
                    end
                end

                WR_RESP: begin
                    if (m_axi_bvalid) begin
                        m_axi_bready <= 1'b0;
                        state <= DONE_ST;
                    end
                end

                RD_ADDR: begin
                    if (m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        m_axi_rready  <= 1'b1;
                        state <= RD_DATA;
                    end
                end

                RD_DATA: begin
                    if (m_axi_rvalid) begin
                        rd_data      <= m_axi_rdata;
                        m_axi_rready <= 1'b0;
                        state <= DONE_ST;
                    end
                end

                DONE_ST: begin
                    done  <= 1'b1;
                    busy  <= 1'b0;
                    state <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
