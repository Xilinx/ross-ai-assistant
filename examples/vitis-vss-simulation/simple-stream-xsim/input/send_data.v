// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 05/23/2024 03:21:19 PM
// Design Name: 
// Module Name: send_data
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module send_data #(
    parameter NUM_VAL = 16 //no of input values
)  (
input clk,
input reset,

input  wire        tready,

output wire        tvalid,
output wire [31:0] tdata1,
output wire        tlast
    );


wire [15:0]t_data1;
wire [15:0]t_data2;

wire [15:0] cntr;
reg [15:0] cntr_r;

always@(posedge clk) begin
if (~reset) begin
        cntr_r <= 0;
end else if(tready & (cntr_r<= NUM_VAL)) begin
        cntr_r <= cntr +1;
end
end

assign cntr = cntr_r ;

assign t_data1 = (cntr_r+1);
assign t_data2 = -1 *(cntr_r+1); 
assign tdata1  = {t_data2,t_data1};
assign tvalid = (cntr_r  < NUM_VAL) ;
assign tlast  = (cntr_r  == NUM_VAL-1) ;



endmodule
