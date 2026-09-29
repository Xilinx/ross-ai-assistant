<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-12: Unconnected/Tri-State Port Connection

**Rule:** ASSIGN-12 | **Severity:** CRITICAL WARNING | **Standard:** IEEE 1364-2005 §12.3.3

## Vivado Message
```
CRITICAL WARNING: [Synth 8-XXXX] [ASSIGN-12]
Port 'port_name' of instance 'instance_name' is connected to tri-state value (1'bz).
```

## Root Cause

Module instance port connected to `1'bz` (high-impedance). Tri-state is only valid for bidirectional I/O pads — never for internal logic ports.

**Common sources:** Copy-paste placeholder, incomplete instantiation, misunderstanding of tri-state usage

## Fix Options

### Option 1: Tie to Constant (Recommended)

```verilog
// Before
sub u2 (.clk(clk), .din(1'bz), .dout(dout[1]));     // Tri-state

// After
sub u2 (.clk(clk), .din(1'b0), .dout(dout[1]));     // Tied to logic 0
```
Choose `1'b0` or `1'b1` based on sub-module reset/default requirements.

### Option 2: Connect to Signal (Multi-instance designs)

```verilog
genvar i;
generate
   for (i = 0; i < 2; i = i + 1) begin : channel
      sub u (.clk(clk), .din(din[i]), .dout(dout[i]));
   end
endgenerate
```

### Option 3: Remove Unused Instance

If the instance itself is unnecessary, remove it entirely and adjust the module interface.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-12 count = 0
```

## References
**Xilinx:** UG901 Ch.2 "Port Connection Rules", UG906 Appendix B  
**IEEE:** 1364-2005 §12.3.3, 1800-2017 §23.3.2
