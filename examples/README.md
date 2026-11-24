<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Examples

Each example is a small, complete task that you give to your AI agent. Every example folder has:

- **`README.md`**: what the example shows, what you need, and the result to expect
- **`prompt.md`**: the request to paste into your agent. Some examples include this request in the README instead.
- **`input/`**: the design or files that the request uses, when the example has them

## Run an example

1. Install the skills. See [Quick Start](../README.md#quick-start).
2. Pick an example below. Check the **You need** column first. Some examples need a board or a specific tool release.
3. Open the example's README. Copy the example folder to a working directory if the README asks you to.
4. Paste the request into your agent and compare the result with the README.

## Vivado

| Example | What you do | Skill | You need |
|---------|-------------|-------|----------|
| [multi-violation](vivado-rtl-lint/multi-violation/) | Lint one Verilog file with planted violations and get a fix for each | [vivado-rtl-lint](../skills/vivado-rtl-lint/) | Vivado MCP server. No project or board. |
| [multi-violation](vivado-timing-methodology-checks/multi-violation/) | Run the timing methodology checks on a dual-clock design with wrong constraints and get a fix or waiver for each violation | [vivado-timing-methodology-checks](../skills/vivado-timing-methodology-checks/) | Vivado MCP server and Vivado 2026.1 or later. No board. |
| [export-ipi-project](vivado-revision-control/export-ipi-project/) | Export a project with RTL, IP, a block design and constraints for version control | [vivado-revision-control](../skills/vivado-revision-control/) | Vivado MCP server. No board. |
| [isp-skill-comparison](vivado-ip-configurator/isp-skill-comparison/) | Configure an ISP from a short request, with and without the skill | [vivado-ip-configurator](../skills/vivado-ip-configurator/) | Vivado 2026.1, Vivado MCP server, `amd-doc-search` MCP server |
| [local-model-ps-wizard](vivado-ip-configurator/local-model-ps-wizard/) | Configure the Versal Gen2 PS with a model that runs on your own machine | [vivado-ip-configurator](../skills/vivado-ip-configurator/) | Vivado, a local model server, OpenCode |
| [vivado-netlist-debug-insert](vivado-netlist-debug-insert/) | Add ILA probes to a synthesized design without editing the RTL | [vivado-netlist-debug-insert](../skills/vivado-netlist-debug-insert/) | Vivado 2026.1 or later. No board. |

## Vitis HLS

Each HLS example is one design that runs several skills in order. Start with the skill named first; it calls the others. Every example builds with [hls-run-flow](../skills/hls-run-flow/), and the helper skills run when the design needs them. The request to type is in each example's README.

| Example | You start from | Skills it runs | Target | You need |
|---------|----------------|----------------|--------|----------|
| [hls-intro-matmul](hls-intro-matmul/) | A C++ matrix multiply written as three nested loops | [hls-architect](../skills/hls-architect/), then [hls-optimize](../skills/hls-optimize/) | 15 GOPS at 3.3 ns | Vitis HLS |
| [edge-detection](edge-detection/) | A MATLAB RGB edge detector | [hls-matlab-to-cpp](../skills/hls-matlab-to-cpp/), then [hls-architect](../skills/hls-architect/) and [hls-optimize](../skills/hls-optimize/) | 4400 FPS at 3.3 ns | Vitis HLS, MATLAB |
| [globaltonemapping](globaltonemapping/) | An unoptimized Vitis Vision tone-mapping kernel | [hls-optimize](../skills/hls-optimize/) | Half the latency, at most 1.5x the resources | Vitis HLS, OpenCV |

The edge-detection README names the first skill `/matlab-to-cpp`. Type it as `/hls-matlab-to-cpp`.

## Vitis

| Example | What you do | Skill | You need |
|---------|-------------|-------|----------|
| [platform-standalone-vck190](vitis-embedded/platform-standalone-vck190/) | Create and build a standalone platform for the VCK190 | [vitis-embedded](../skills/vitis-embedded/) | Vitis. No board. |
| [platform-linux-zcu102](vitis-embedded/platform-linux-zcu102/) | Create a Linux platform for the ZCU102, with a device tree | [vitis-embedded](../skills/vitis-embedded/) | Vitis. No board. |
| [app-hello-vck190](vitis-embedded/app-hello-vck190/) | Create and build a hello-world application | [vitis-embedded](../skills/vitis-embedded/) | Vitis, a built platform |
| [boot-image-versal-pdi](vitis-embedded/boot-image-versal-pdi/) | Write a BIF and run Bootgen to make a Versal boot PDI | [vitis-embedded](../skills/vitis-embedded/) | Vitis, a built application |
| [extensible-vck190](vitis-platform/extensible-vck190/) | Export an extensible platform from your open block design | [vitis-platform](../skills/vitis-platform/) | Vivado with your VCK190 block design open |
| [system-vadd-hw-emu](vitis-system/system-vadd-hw-emu/) | Build and package the vadd system for hardware emulation | [vitis-system](../skills/vitis-system/) | Vitis, the VCK190 extensible base platform |
| [integration-compose-aie-hls](vitis-system/integration-compose-aie-hls/) | Link an AI Engine component with HLS data movers | [vitis-system](../skills/vitis-system/) | Vitis, the VCK190 extensible base platform |
| [simple-vck190](vitis-aie/simple-vck190/) | Create, build and simulate an AI Engine component | [vitis-aie](../skills/vitis-aie/) | Vitis |
| [simple-stream](vitis-aie-simulation/simple-stream/) | Compile a small AI Engine graph and check its simulation output | [vitis-aie-simulation](../skills/vitis-aie-simulation/) | Vitis |
| [simple-stream-xsim](vitis-vss-simulation/simple-stream-xsim/) | Simulate an AI Engine graph with a Verilog testbench in XSim | [vitis-vss-simulation](../skills/vitis-vss-simulation/) | Vivado and XSim |
| [fir-and-beamformer](vitis-aie-int8-mac/fir-and-beamformer/) | Map int8 filters and a beamformer onto AI Engine MAC intrinsics | [vitis-aie-int8-mac](../skills/vitis-aie-int8-mac/) | Vitis 2026.1 or later, an AIE (not AIE-ML) device |
| [hls-and-fft](vitis-functional-sim/hls-and-fft/) | Write Python or MATLAB testbenches for HLS kernels and the RF FFT | [vitis-functional-sim](../skills/vitis-functional-sim/) | Vitis 2026.2 or later |
| [aie-packet-and-ips](vitis-functional-sim/aie-packet-and-ips/) | Write testbenches for packet-switched AI Engine graphs and DSP IPs | [vitis-functional-sim](../skills/vitis-functional-sim/) | Vitis 2026.2 or later |
| [cpp-chain-and-rf-ips](vitis-functional-sim/cpp-chain-and-rf-ips/) | Write C++ and Python testbenches for the FFT, FIR and RF IPs | [vitis-functional-sim](../skills/vitis-functional-sim/) | Vitis 2026.2 or later |
| [gmio-vck190](vitis-hardware-emulation/gmio-vck190/) | Run your GMIO design in hardware emulation | [vitis-hardware-emulation](../skills/vitis-hardware-emulation/) | Your design, a VCK190 platform, sysroot and QEMU image |
| [aie-throughput](vitis-aie-profile/aie-throughput/) | Choose how to measure the throughput of one output PLIO | [vitis-aie-profile](../skills/vitis-aie-profile/) | Nothing to install. Question only. |
| [stream-deadlock](vitis-pl-profile/stream-deadlock/) | Find the cause of a streaming deadlock from an emulation log | [vitis-pl-profile](../skills/vitis-pl-profile/) | Nothing to install. Question only. |
| [host-transfer-timeline](vitis-host-profile/host-transfer-timeline/) | Check whether host transfers overlap with kernel runs | [vitis-host-profile](../skills/vitis-host-profile/) | Nothing to install. Question only. |
| [classic-workspace-embedded](vitis-classic-migration/classic-workspace-embedded/) | Move a Vitis Classic embedded workspace to the Unified IDE | [vitis-classic-migration](../skills/vitis-classic-migration/) | Vitis Unified 2026.2 or later, a Classic workspace |
| [xsct-script-to-python](vitis-classic-migration/xsct-script-to-python/) | Convert an XSCT Tcl script to a `vitis -s` Python script | [vitis-classic-migration](../skills/vitis-classic-migration/) | No XSCT install. Vitis to run the result. |

## Hardware Debug

These examples program a board. Start `hw_server` on the board host first.

| Example | What you do | Skill | You need |
|---------|-------------|-------|----------|
| [axi-protocol-capture](hw-ila-debug/axi-protocol-capture/) | Build an AXI design with a System ILA, then trigger, capture and export | [hw-ila-debug](../skills/hw-ila-debug/) | Vivado MCP server, a Versal board |
| [axi-register-rw](hw-vio-debug/axi-register-rw/) | Drive AXI register reads and writes from VIO probes | [hw-vio-debug](../skills/hw-vio-debug/) | Vivado MCP server, a Versal board |
| [vck190-ddr-health](hw-ddrmc-debug/vck190-ddr-health/) | Check DDR calibration and margins, then run a 2D eye scan | [hw-ddrmc-debug](../skills/hw-ddrmc-debug/) | ChipScoPy MCP server, a VCK190 with DDR4 fitted |
| [vck190-noc-bandwidth](hw-noc-perfmon/vck190-noc-bandwidth/) | Find the NoC master that carries traffic and measure its bandwidth | [hw-noc-perfmon](../skills/hw-noc-perfmon/) | ChipScoPy MCP server, a VCK190 with DDR4 fitted |
| [write-decode-error](hw-noc-debug/write-decode-error/) | Find the cause of a NoC write address-decode error | [hw-noc-debug](../skills/hw-noc-debug/) | Vivado and ChipScoPy MCP servers, a Versal board |
| [write-timeout](hw-noc-debug/write-timeout/) | Find the cause of a NoC write timeout | [hw-noc-debug](../skills/hw-noc-debug/) | Vivado and ChipScoPy MCP servers, a Versal board |
| [axsize-violation](hw-noc-debug/axsize-violation/) | Find the cause of an illegal AxSIZE protocol error | [hw-noc-debug](../skills/hw-noc-debug/) | Vivado and ChipScoPy MCP servers, a Versal board |
| [burst-4k-crossing](hw-noc-debug/burst-4k-crossing/) | Find a write burst that crosses a 4 KB boundary | [hw-noc-debug](../skills/hw-noc-debug/) | Vivado and ChipScoPy MCP servers, a Versal board |
| [gty-loopback-eye-scan](hw-ibert-gt-debug/gty-loopback-eye-scan/) | Make a GTY loopback link, read its BER and run a 2D eye scan | [hw-ibert-gt-debug](../skills/hw-ibert-gt-debug/) | Vivado MCP server, a VCK190 |
| [cpm5-link-status](hw-pcie-link-debug/cpm5-link-status/) | Read the CPM5 PCIe link state and LTSSM history | [hw-pcie-link-debug](../skills/hw-pcie-link-debug/) | ChipScoPy MCP server, a VPK120 |
| [all-tools](chipscope-mcp/all-tools/) | Use every ChipScoPy MCP tool in one session | [hw-ila-debug](../skills/hw-ila-debug/), [hw-vio-debug](../skills/hw-vio-debug/) | Vivado and ChipScoPy MCP servers, a VCK190 |

## Vitis AI

| Example | What you do | Skill | You need |
|---------|-------------|-------|----------|
| [resnet50-quantize-vint8-bf16](vai-quantization-guide/resnet50-quantize-vint8-bf16/) | Quantize ResNet50 to VINT8 and BF16, and compare it with the BF16 design | [vai-quantization-guide](../skills/vai-quantization-guide/) | Vitis AI environment, PyTorch, CIFAR-10 |
| [multi-input-multi-node-custom-op](vai-custom-op/multi-input-multi-node-custom-op/) | Replace a nine-node ONNX subgraph with one generated AI Engine operator | [vai-custom-op](../skills/vai-custom-op/) | Vitis AI environment. A VE2 board for the board steps. |
