`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////
// AXI4 Master that generates a single NoC 4KB-boundary-crossing error.
//
// After reset it waits STARTUP_DELAY clock cycles, then issues ONE INCR write
// burst of BURST_LEN+1 beats (AWSIZE = 4 bytes) starting at TARGET_ADDR. The
// start address and length are chosen so the burst crosses a 4KB address
// boundary (per AR 000035129: AWLEN=15, AWSIZE=2, start at <BRAM base>+0xFC8 so
// the 16x4=64-byte burst spans 0xFC8..0x1008 and crosses +0x1000).
//
// An AXI burst is not permitted to cross a 4KB boundary, so the NoC flags an
// AXI rule violation (REG_ISR.axi_rules_wr) at the master unit. TARGET_ADDR is a
// VALID, mapped slave aperture, so the transaction is routed -- the only fault
// is the illegal 4KB crossing.
//////////////////////////////////////////////////////////////////////////////
module axi_4k_cross_master #(
    parameter ADDR_WIDTH        = 64,
    parameter DATA_WIDTH        = 32,
    parameter [63:0] TARGET_ADDR   = 64'h0000_0201_0000_0FC8, // mapped, +0xFC8 into BRAM
    parameter [7:0]  BURST_LEN     = 8'd15,                   // 16 beats -> crosses 4KB
    parameter [31:0] STARTUP_DELAY = 32'd1000                 // cycles after reset
)(
    input  wire                    aclk,
    input  wire                    aresetn,
    // AXI4 Write Address Channel
    output reg  [ADDR_WIDTH-1:0]   m_axi_awaddr,
    output reg  [7:0]              m_axi_awlen,
    output reg  [2:0]              m_axi_awsize,
    output reg  [1:0]              m_axi_awburst,
    output reg                     m_axi_awvalid,
    input  wire                    m_axi_awready,
    output reg  [15:0]             m_axi_awid,
    output reg  [3:0]              m_axi_awcache,
    output reg  [2:0]              m_axi_awprot,
    output reg                     m_axi_awlock,
    output reg  [3:0]              m_axi_awqos,
    // AXI4 Write Data Channel
    output reg  [DATA_WIDTH-1:0]   m_axi_wdata,
    output reg  [(DATA_WIDTH/8)-1:0] m_axi_wstrb,
    output reg                     m_axi_wlast,
    output reg                     m_axi_wvalid,
    input  wire                    m_axi_wready,
    // AXI4 Write Response Channel
    input  wire [1:0]              m_axi_bresp,
    input  wire                    m_axi_bvalid,
    output reg                     m_axi_bready,
    input  wire [15:0]             m_axi_bid,
    // AXI4 Read Address Channel (unused, tied off)
    output reg  [ADDR_WIDTH-1:0]   m_axi_araddr,
    output reg  [7:0]              m_axi_arlen,
    output reg  [2:0]              m_axi_arsize,
    output reg  [1:0]              m_axi_arburst,
    output reg                     m_axi_arvalid,
    input  wire                    m_axi_arready,
    output reg  [15:0]             m_axi_arid,
    output reg  [3:0]              m_axi_arcache,
    output reg  [2:0]              m_axi_arprot,
    output reg                     m_axi_arlock,
    output reg  [3:0]              m_axi_arqos,
    // AXI4 Read Data Channel (unused, tied off)
    input  wire [DATA_WIDTH-1:0]   m_axi_rdata,
    input  wire [1:0]              m_axi_rresp,
    input  wire                    m_axi_rlast,
    input  wire                    m_axi_rvalid,
    output reg                     m_axi_rready,
    input  wire [15:0]             m_axi_rid
);

    localparam IDLE    = 3'd0;
    localparam WR_ADDR = 3'd1;
    localparam WR_DATA = 3'd2;
    localparam WR_RESP = 3'd3;
    localparam DONE    = 3'd4;

    reg [2:0]  state;
    reg [31:0] delay_cnt;
    reg [7:0]  beat_cnt;

    always @(posedge aclk) begin
        if (!aresetn) begin
            state         <= IDLE;
            delay_cnt     <= 32'd0;
            beat_cnt      <= 8'd0;
            m_axi_awaddr  <= {ADDR_WIDTH{1'b0}};
            m_axi_awlen   <= 8'd0;
            m_axi_awsize  <= 3'd2;      // 4 bytes (32-bit) — legal size
            m_axi_awburst <= 2'd1;      // INCR
            m_axi_awvalid <= 1'b0;
            m_axi_awid    <= 16'd0;
            m_axi_awcache <= 4'd0;
            m_axi_awprot  <= 3'd0;
            m_axi_awlock  <= 1'b0;
            m_axi_awqos   <= 4'd0;
            m_axi_wdata   <= {DATA_WIDTH{1'b0}};
            m_axi_wstrb   <= {(DATA_WIDTH/8){1'b0}};
            m_axi_wlast   <= 1'b0;
            m_axi_wvalid  <= 1'b0;
            m_axi_bready  <= 1'b0;
            m_axi_araddr  <= {ADDR_WIDTH{1'b0}};
            m_axi_arlen   <= 8'd0;
            m_axi_arsize  <= 3'd2;
            m_axi_arburst <= 2'd1;
            m_axi_arvalid <= 1'b0;
            m_axi_arid    <= 16'd0;
            m_axi_arcache <= 4'd0;
            m_axi_arprot  <= 3'd0;
            m_axi_arlock  <= 1'b0;
            m_axi_arqos   <= 4'd0;
            m_axi_rready  <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    if (delay_cnt < STARTUP_DELAY) begin
                        delay_cnt <= delay_cnt + 1;
                    end else begin
                        state         <= WR_ADDR;
                        m_axi_awaddr  <= TARGET_ADDR[ADDR_WIDTH-1:0];
                        m_axi_awlen   <= BURST_LEN;   // 16-beat burst crosses 4KB
                        m_axi_awsize  <= 3'd2;        // legal 4-byte beats
                        m_axi_awburst <= 2'd1;        // INCR
                        m_axi_awvalid <= 1'b1;
                        m_axi_awid    <= 16'hBAD;
                    end
                end
                WR_ADDR: begin
                    if (m_axi_awready) begin
                        m_axi_awvalid <= 1'b0;
                        state         <= WR_DATA;
                        m_axi_wdata   <= 32'hDEAD_BEEF;
                        m_axi_wstrb   <= 4'hF;
                        m_axi_wlast   <= (BURST_LEN == 8'd0);
                        m_axi_wvalid  <= 1'b1;
                        beat_cnt      <= 8'd0;
                    end
                end
                WR_DATA: begin
                    if (m_axi_wready) begin
                        if (m_axi_wlast) begin
                            m_axi_wvalid <= 1'b0;
                            m_axi_wlast  <= 1'b0;
                            state        <= WR_RESP;
                            m_axi_bready <= 1'b1;
                        end else begin
                            beat_cnt    <= beat_cnt + 8'd1;
                            m_axi_wdata <= 32'hDEAD_0000 | {24'd0, (beat_cnt + 8'd1)};
                            m_axi_wstrb <= 4'hF;
                            m_axi_wlast <= ((beat_cnt + 8'd1) == BURST_LEN);
                        end
                    end
                end
                WR_RESP: begin
                    if (m_axi_bvalid) begin
                        m_axi_bready <= 1'b0;
                        state        <= DONE;
                    end
                end
                DONE: begin
                    state <= DONE;  // stay forever
                end
                default: state <= IDLE;
            endcase
        end
    end

endmodule
