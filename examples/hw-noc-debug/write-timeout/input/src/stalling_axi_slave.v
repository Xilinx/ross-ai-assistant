`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////
// Stalling AXI4 Slave — accepts AW/W but NEVER returns B (write response)
// This causes a NoC timeout when NoC timeouts are enabled.
// Used for NoC Debug Tutorial Scenario 4 (Timeout Error).
//////////////////////////////////////////////////////////////////////////////
module stalling_axi_slave #(
    parameter ADDR_WIDTH = 64,
    parameter DATA_WIDTH = 32
)(
    input  wire                    aclk,
    input  wire                    aresetn,
    // AXI4 Write Address Channel — accept everything
    input  wire [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  wire [7:0]              s_axi_awlen,
    input  wire [2:0]              s_axi_awsize,
    input  wire [1:0]              s_axi_awburst,
    input  wire                    s_axi_awvalid,
    output wire                    s_axi_awready,
    input  wire [15:0]             s_axi_awid,
    // AXI4 Write Data Channel — accept everything
    input  wire [DATA_WIDTH-1:0]   s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  wire                    s_axi_wlast,
    input  wire                    s_axi_wvalid,
    output wire                    s_axi_wready,
    // AXI4 Write Response Channel — NEVER respond
    output wire [1:0]              s_axi_bresp,
    output wire                    s_axi_bvalid,
    input  wire                    s_axi_bready,
    output wire [15:0]             s_axi_bid,
    // AXI4 Read Address Channel — accept everything
    input  wire [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  wire [7:0]              s_axi_arlen,
    input  wire [2:0]              s_axi_arsize,
    input  wire [1:0]              s_axi_arburst,
    input  wire                    s_axi_arvalid,
    output wire                    s_axi_arready,
    input  wire [15:0]             s_axi_arid,
    // AXI4 Read Data Channel — NEVER respond
    output wire [DATA_WIDTH-1:0]   s_axi_rdata,
    output wire [1:0]              s_axi_rresp,
    output wire                    s_axi_rlast,
    output wire                    s_axi_rvalid,
    input  wire                    s_axi_rready,
    output wire [15:0]             s_axi_rid
);

    // NEVER accept any transaction — back-pressures NSU → NoC fabric → NMU timeout
    assign s_axi_awready = 1'b0;  // Never accept write address
    assign s_axi_wready  = 1'b0;  // Never accept write data
    assign s_axi_arready = 1'b0;  // Never accept read address

    // No responses either
    assign s_axi_bvalid  = 1'b0;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bid     = 16'd0;

    assign s_axi_rvalid  = 1'b0;
    assign s_axi_rdata   = {DATA_WIDTH{1'b0}};
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rlast   = 1'b0;
    assign s_axi_rid     = 16'd0;

endmodule
