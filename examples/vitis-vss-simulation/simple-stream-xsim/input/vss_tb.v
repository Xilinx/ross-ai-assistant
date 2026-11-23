// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 07/14/2024 03:22:41 PM
// Design Name: 
// Module Name: vss_tb
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



`timescale 1 ns/ 1 ps


module vss_tb;


 wire  [31:0] AI_ENGINE_0_VSS_SS_DIN1_tdata       ;
 wire  [0:0]  AI_ENGINE_0_VSS_SS_DIN1_tlast       ;
 wire [0:0]  AI_ENGINE_0_VSS_SS_DIN1_tready      ;
 wire  [0:0]  AI_ENGINE_0_VSS_SS_DIN1_tvalid      ;
 wire [31:0] AI_ENGINE_0_VSS_SS_DOUT1_tdata      ;
 wire [3:0]  AI_ENGINE_0_VSS_SS_DOUT1_tkeep      ;
 wire [0:0]  AI_ENGINE_0_VSS_SS_DOUT1_tlast      ;
 reg  [0:0]  AI_ENGINE_0_VSS_SS_DOUT1_tready     ;
 wire [0:0]  AI_ENGINE_0_VSS_SS_DOUT1_tvalid     ;
 reg         ai_engine_0_vss_ss_din1_aclk        ;
 reg         ai_engine_0_vss_ss_din1_aresetn     ;
 reg         ai_engine_0_vss_ss_dout1_aclk       ;
 reg         ai_engine_0_vss_ss_dout1_aresetn    ;

 integer f1;

vss_ss_wrapper UUT
    (.AI_ENGINE_0_DIN1_tdata    (AI_ENGINE_0_VSS_SS_DIN1_tdata    ),
     .AI_ENGINE_0_DIN1_tlast    (AI_ENGINE_0_VSS_SS_DIN1_tlast    ),
     .AI_ENGINE_0_DIN1_tready   (AI_ENGINE_0_VSS_SS_DIN1_tready   ),
     .AI_ENGINE_0_DIN1_tvalid   (AI_ENGINE_0_VSS_SS_DIN1_tvalid   ),
     .AI_ENGINE_0_DOUT1_tdata   (AI_ENGINE_0_VSS_SS_DOUT1_tdata   ),
     .AI_ENGINE_0_DOUT1_tkeep   (AI_ENGINE_0_VSS_SS_DOUT1_tkeep   ),
     .AI_ENGINE_0_DOUT1_tlast   (AI_ENGINE_0_VSS_SS_DOUT1_tlast   ),
     .AI_ENGINE_0_DOUT1_tready  (AI_ENGINE_0_VSS_SS_DOUT1_tready  ),
     .AI_ENGINE_0_DOUT1_tvalid  (AI_ENGINE_0_VSS_SS_DOUT1_tvalid  ),
     .ai_engine_0_din1_aclk     (ai_engine_0_vss_ss_din1_aclk     ),
     .ai_engine_0_din1_aresetn  (ai_engine_0_vss_ss_din1_aresetn  ),
     .ai_engine_0_dout1_aclk    (ai_engine_0_vss_ss_dout1_aclk    ),
     .ai_engine_0_dout1_aresetn (ai_engine_0_vss_ss_dout1_aresetn )
    );

initial 
begin
  ai_engine_0_vss_ss_din1_aclk  = 0;
  forever begin
  #5 ai_engine_0_vss_ss_din1_aclk  = ~ai_engine_0_vss_ss_din1_aclk;
  end
end

initial 
begin
  ai_engine_0_vss_ss_dout1_aclk = 0;
  forever begin
  #5 ai_engine_0_vss_ss_dout1_aclk = ~ai_engine_0_vss_ss_dout1_aclk;
  end
end

initial 
begin
	ai_engine_0_vss_ss_din1_aresetn  = 0;
        ai_engine_0_vss_ss_dout1_aresetn = 0;
	AI_ENGINE_0_VSS_SS_DOUT1_tready  = 0;
	#10;
	ai_engine_0_vss_ss_din1_aresetn  = 1;
        ai_engine_0_vss_ss_dout1_aresetn = 1;
	#5;
	AI_ENGINE_0_VSS_SS_DOUT1_tready  = 1;
end


send_data #(
    .NUM_VAL (8) //no of input values
    )UUT1 (
    .clk   (ai_engine_0_vss_ss_din1_aclk           ),
    .reset (ai_engine_0_vss_ss_din1_aresetn        ),
    .tready(AI_ENGINE_0_VSS_SS_DIN1_tready         ),
    .tvalid(AI_ENGINE_0_VSS_SS_DIN1_tvalid         ),
    .tdata1(AI_ENGINE_0_VSS_SS_DIN1_tdata          ),
    .tlast (AI_ENGINE_0_VSS_SS_DIN1_tlast          )
    );



initial
begin
    f1=$fopen("output.txt","w");
end

always@(*)
begin
    @(posedge ai_engine_0_vss_ss_dout1_aclk)
    begin

      if(AI_ENGINE_0_VSS_SS_DOUT1_tvalid) begin
            $fdisplay(f1,"%h",AI_ENGINE_0_VSS_SS_DOUT1_tdata);
      end
  end
end

initial #2000 $finish;

endmodule
