<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/amd-ross-lockup-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/amd-ross-lockup-light.png">
    <img alt="AMD Ross" src="docs/images/amd-ross-lockup-light.png" width="280">
  </picture>
</p>

# AMD Ross™ Agentic AI Assistant

![Agent Skills](https://img.shields.io/badge/Agent_Skills-Standard-7B2D8E)
[![GitHub Copilot](https://img.shields.io/badge/GitHub_Copilot-Compatible-007ACC)](https://github.com/features/copilot)
[![Cursor](https://img.shields.io/badge/Cursor-Compatible-000000)](https://cursor.com)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Compatible-F07535)](https://www.anthropic.com/claude-code)
[![OpenAI Codex](https://img.shields.io/badge/OpenAI_Codex-Compatible-412991)](https://openai.com/codex/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

Agent skills for AMD FPGA/SoC development.

**New here?** Start with the [Getting Started](docs/getting-started/) guide. See [docs](docs/) for FAQ, MCP reference, and support.

## Quick Start

Claude Code and Codex can install the plugin directly from `Xilinx/ross-ai-assistant`, without a manual clone. ChatGPT supports importing the same repository into a workspace. For clients that need a local folder, individual skill installation, or the docs and examples, clone the repository and replace `/path/to/ross-ai-assistant` below with its path.

### Prerequisites

Install **one** of the following from the [AMD Ross Agentic AI Assistant download page](https://www.amd.com/en/support/downloads/ross-agentic-ai.html):

| Component | Description | Follow-up installation |
|-----------|-------------|------------------------|
| Vivado AI Extension (VS Code / Cursor) | Includes the Vivado MCP server, the skills in this repository, and the `amd-doc-search` MCP configuration | None |
| Vivado MCP Server (Linux or Windows) | The Vivado MCP server: Tcl command execution, project management, and synthesis/implementation | Configure the [`amd-doc-search` MCP server](#install-the-amd-doc-search-mcp-server), then install the skills in this repo as a [plugin](#install-as-a-plugin) or [individually](#install-skills-interactive) |

Use the MCP server for CLI clients, or the VS Code / Cursor extension for IDE setups — not both.

### Install the amd-doc-search MCP server

The `amd-doc-search` MCP server searches AMD FPGA and Adaptive SoC documentation: user guides and device collaterals, and tool usage for Vivado, Vitis, embedded system software, and related products.

The Vivado AI Extension already includes this configuration. With the standalone Vivado MCP Server, add the server yourself.

For detailed installation instructions, see [https://ross.amd.com/chat#setup](https://ross.amd.com/chat#setup).

> **Local knowledge base.** If you want documentation search to stay on your own machine, use the [local knowledge base](docs/local-kb/README.md) instead of the hosted `amd-doc-search` service.

### Install as a plugin

This repository is also an agent plugin, which adds every skill in one step. Install from the public GitHub marketplace in Claude Code or Codex, import it into a ChatGPT workspace, or use a local folder in supported clients. Updates depend on the client: refresh the marketplace and update the installed plugin, sync the ChatGPT workspace import, or update the folder and reload the client.

Once installed, invoke the skills by name, for example `/hls-optimize`, and they call each other by those same names. Some clients list them under the plugin's name, `amd-ross-agentic-ai-assistant`.

> **Choose one install method.** If you install the plugin, do not also install the skills with `npx skills add` or by copying them. Every skill would then be loaded twice.

The plugin contains skills only. The Vivado MCP Server or the Vivado AI Extension from [Prerequisites](#prerequisites) is still installed separately.

See [Install as a plugin](docs/getting-started/install-plugin.md) for the steps in your AI client and how to update.

### Install skills (interactive)

Install [Node.js](https://nodejs.org/) to use the `npx skills` commands below.

Point `npx skills add` at your local clone (or an extracted release package):

```bash
npx skills add /path/to/ross-ai-assistant
```

This installs skills into the current workspace. Add `--global` to install into your home directory (`~/.claude/skills/`) so they are available across all workspaces.

### Install all skills

```bash
npx skills add /path/to/ross-ai-assistant --all
```

### Install all skills globally

```bash
npx skills add /path/to/ross-ai-assistant --all --global
```

### Install a specific skill

```bash
npx skills add /path/to/ross-ai-assistant --skill hls-optimize
```

### List available skills

```bash
npx skills add /path/to/ross-ai-assistant --list
```

### Manual installation (no Node.js)

Copy desired skill folders to your agent's skills directory:

```bash
cp -r /path/to/ross-ai-assistant/skills/hls-optimize ~/.claude/skills/
```

## Skills

### Vivado

These skills require the [Vivado MCP Server](docs/reference/vivado-mcp-tools.md) for live Vivado interaction.

| Skill | Description | Examples |
|-------|-------------|----------|
| [vivado-spec-generator](skills/vivado-spec-generator/) | Turn a natural-language FPGA/SoC request into a structured design specification | — |
| [vivado-ced-configurator](skills/vivado-ced-configurator/) | Find and configure a Vivado Configurable Example Design for a board or device | — |
| [vivado-ip-configurator](skills/vivado-ip-configurator/) | Create and configure a Vivado IP cell from a natural-language description | [isp-skill-comparison](examples/vivado-ip-configurator/isp-skill-comparison/), [local-model-ps-wizard](examples/vivado-ip-configurator/local-model-ps-wizard/) |
| [vivado-ipi-assembler](skills/vivado-ipi-assembler/) | Assemble an IP Integrator block design, connect cells, assign addresses, and validate it | — |
| [vivado-simulate-rtl](skills/vivado-simulate-rtl/) | Run and diagnose RTL simulation across XSim and supported third-party simulators | — |
| [vivado-rtl-lint](skills/vivado-rtl-lint/) | Run Vivado's RTL linter and report design issues with prioritized, code-level fixes | [multi-violation](examples/vivado-rtl-lint/multi-violation/) |
| [vivado-rtl-elaboration-analysis](skills/vivado-rtl-elaboration-analysis/) | Diagnose synthesis elaboration errors and warnings and propose RTL fixes | — |
| [vivado-timing-methodology-checks](skills/vivado-timing-methodology-checks/) | Analyze timing methodology violations and generate XDC fixes or waivers | [multi-violation](examples/vivado-timing-methodology-checks/multi-violation/) |
| [vivado-timing-closure](skills/vivado-timing-closure/) | Run and supervise Versal implementation sweeps, diagnose timing failures, and propose fixes | — |
| [vivado-convert-flat-to-dfx](skills/vivado-convert-flat-to-dfx/) | Convert a flat UltraScale+ or Versal design into a DFX-ready project | — |
| [vivado-netlist-debug-insert](skills/vivado-netlist-debug-insert/) | Insert ILA debug cores on selected nets in a synthesized design and check capture memory usage | [vivado-netlist-debug-insert](examples/vivado-netlist-debug-insert/) |
| [vivado-revision-control](skills/vivado-revision-control/) | Manage Vivado project files under revision control | [export-ipi-project](examples/vivado-revision-control/export-ipi-project/) |

### Vitis HLS

Start with `hls-matlab-to-cpp`, `hls-architect`, or `hls-optimize`. These primary entry points invoke the build and helper skills below as needed. You can also invoke `hls-run-flow` directly to build an HLS component.

| Skill | Description | Examples |
|-------|-------------|----------|
| [hls-matlab-to-cpp](skills/hls-matlab-to-cpp/) | Convert MATLAB sample-based code to C++ frame-based loops and verify against MATLAB results | [edge-detection](examples/edge-detection/) |
| [hls-architect](skills/hls-architect/) | Convert input code to a multi-stage HLS dataflow architecture | [hls-intro-matmul](examples/hls-intro-matmul/), [edge-detection](examples/edge-detection/) |
| [hls-optimize](skills/hls-optimize/) | Iteratively optimize an HLS kernel against performance and resource targets | [globaltonemapping](examples/globaltonemapping/) |
| [hls-run-flow](skills/hls-run-flow/) | Run C simulation, synthesis, C/RTL cosimulation, and implementation | [HLS examples](examples/README.md#vitis-hls) |
| [hls-array-to-stream](skills/hls-array-to-stream/) | Check whether C/C++ code follows the HLS array-to-stream coding pattern | — |
| [hls-burst-inference](skills/hls-burst-inference/) | Analyze whether memory accesses can use AXI burst transfers | — |
| [hls-component-basic-info](skills/hls-component-basic-info/) | Extract component sources, testbench files, include paths, and the top function | — |
| [hls-cosim-report](skills/hls-cosim-report/) | Extract HLS cosimulation report information | — |
| [hls-dataflow](skills/hls-dataflow/) | Check whether a C/C++ region follows canonical HLS dataflow rules | — |
| [hls-dataflow-infos](skills/hls-dataflow-infos/) | Extract HLS dataflow process and channel information | — |
| [hls-flattenable](skills/hls-flattenable/) | Check and restructure loop nests so HLS can flatten them | — |
| [hls-ii-diagnose](skills/hls-ii-diagnose/) | Diagnose initiation interval violations from synthesis evidence | — |
| [hls-impl-report](skills/hls-impl-report/) | Extract post-route timing, resource utilization, and implementation report information | — |
| [hls-line-buffer](skills/hls-line-buffer/) | Guide line/window buffer draining and verification for stencil kernels | — |
| [hls-perf-pragma](skills/hls-perf-pragma/) | Translate throughput targets into performance pragma values and placement | — |
| [hls-pipe-decomp](skills/hls-pipe-decomp/) | Split stream-heavy pipelines into smaller dataflow stages to improve timing | — |
| [hls-pipeline](skills/hls-pipeline/) | Guide pipeline pragma placement and initiation interval, rewind, and style settings | — |
| [hls-pragma-scope](skills/hls-pragma-scope/) | Apply HLS pragma scope rules when analyzing or generating code | — |
| [hls-stencil-pattern](skills/hls-stencil-pattern/) | Identify code that can use HLS stencil pattern optimization | — |
| [hls-synth-report](skills/hls-synth-report/) | Extract synthesis and pragma reports and relevant synthesis log messages | — |
| [hls-synthesizable](skills/hls-synthesizable/) | Check whether C/C++ code is synthesizable for HLS | — |

### Vitis

These skills cover embedded applications, AI Engine development, system integration, simulation, and profiling. Component and system builds can run in the Vitis IDE or through `vitis -s`; platform annotation operates on a Vivado block design.

| Skill | Description | Examples |
|-------|-------------|----------|
| [vitis-embedded](skills/vitis-embedded/) | Create an embedded platform, application, and boot image | [platform](examples/vitis-embedded/platform-standalone-vck190/), [application](examples/vitis-embedded/app-hello-vck190/), [boot image](examples/vitis-embedded/boot-image-versal-pdi/) |
| [vitis-platform](skills/vitis-platform/) | Annotate a validated Vivado block design with Vitis platform properties | [extensible-vck190](examples/vitis-platform/extensible-vck190/) |
| [vitis-system](skills/vitis-system/) | Link AIE and HLS components and package a deployable system image | [system-vadd-hw-emu](examples/vitis-system/system-vadd-hw-emu/), [integration-compose-aie-hls](examples/vitis-system/integration-compose-aie-hls/) |
| [vitis-aie](skills/vitis-aie/) | Create and build an AI Engine component or graph | [simple-vck190](examples/vitis-aie/simple-vck190/) |
| [vitis-create-aie-kernel](skills/vitis-create-aie-kernel/) | Write a vectorized AI Engine kernel and graph, build them, and verify with x86 simulation | — |
| [vitis-aie-int8-mac](skills/vitis-aie-int8-mac/) | Configure AI Engine int8 vector MAC intrinsics for filters, beamformers, and matrix operations | [fir-and-beamformer](examples/vitis-aie-int8-mac/fir-and-beamformer/) |
| [vitis-aie-simulation](skills/vitis-aie-simulation/) | Run simulation on an already-compiled AI Engine hardware build and compare results | [simple-stream](examples/vitis-aie-simulation/simple-stream/) |
| [vitis-vss-simulation](skills/vitis-vss-simulation/) | Run Vitis Subsystem simulation for AIE and PL designs | [simple-stream-xsim](examples/vitis-vss-simulation/simple-stream-xsim/) |
| [vitis-functional-sim](skills/vitis-functional-sim/) | Generate Python, MATLAB, or C++ functional testbenches for HLS, AIE, and IP designs | [hls-and-fft](examples/vitis-functional-sim/hls-and-fft/), [aie-packet-and-ips](examples/vitis-functional-sim/aie-packet-and-ips/), [cpp-chain-and-rf-ips](examples/vitis-functional-sim/cpp-chain-and-rf-ips/) |
| [vitis-hardware-emulation](skills/vitis-hardware-emulation/) | Run hardware emulation on an already-built Versal AIE and PL design | [gmio-vck190](examples/vitis-hardware-emulation/gmio-vck190/) |
| [vitis-aie-profile](skills/vitis-aie-profile/) | Configure AI Engine counters and event traces to investigate throughput and stalls | [aie-throughput](examples/vitis-aie-profile/aie-throughput/) |
| [vitis-pl-profile](skills/vitis-pl-profile/) | Configure PL profiling and traces and interpret kernel performance and stalls | [stream-deadlock](examples/vitis-pl-profile/stream-deadlock/) |
| [vitis-host-profile](skills/vitis-host-profile/) | Configure host profiling and analyze transfers, kernel overlap, and host/device pipelines | [host-transfer-timeline](examples/vitis-host-profile/host-transfer-timeline/) |
| [vitis-classic-migration](skills/vitis-classic-migration/) | Migrate Vitis Classic embedded workspaces and XSCT scripts to Vitis Unified IDE | [classic-workspace-embedded](examples/vitis-classic-migration/classic-workspace-embedded/), [xsct-script-to-python](examples/vitis-classic-migration/xsct-script-to-python/) |

### Hardware Debug

These skills debug live FPGA/SoC hardware through Vivado Hardware Manager or ChipScoPy MCP tools, depending on the skill.

| Skill | Description | Examples |
|-------|-------------|----------|
| [hw-ila-debug](skills/hw-ila-debug/) | Trigger, capture, and export waveform data from ILA debug cores | [axi-protocol-capture](examples/hw-ila-debug/axi-protocol-capture/) |
| [hw-vio-debug](skills/hw-vio-debug/) | Read input probes and drive output probes through VIO | [axi-register-rw](examples/hw-vio-debug/axi-register-rw/) |
| [hw-ddrmc-debug](skills/hw-ddrmc-debug/) | Check Versal DDR memory controller calibration, margins, and 2D eye scans | [vck190-ddr-health](examples/hw-ddrmc-debug/vck190-ddr-health/) |
| [hw-ibert-gt-debug](skills/hw-ibert-gt-debug/) | Debug serial transceiver links and run eye or YK scans | [gty-loopback-eye-scan](examples/hw-ibert-gt-debug/gty-loopback-eye-scan/) |
| [hw-noc-debug](skills/hw-noc-debug/) | Diagnose Versal NoC decode errors, timeouts, and AXI protocol failures | [write-decode-error](examples/hw-noc-debug/write-decode-error/), [write-timeout](examples/hw-noc-debug/write-timeout/) |
| [hw-noc-perfmon](skills/hw-noc-perfmon/) | Measure NoC bandwidth and latency on live Versal hardware | [vck190-noc-bandwidth](examples/hw-noc-perfmon/vck190-noc-bandwidth/) |
| [hw-pcie-link-debug](skills/hw-pcie-link-debug/) | Diagnose CPM5 PCIe link bring-up, training, and LTSSM state on Versal Premium | [cpm5-link-status](examples/hw-pcie-link-debug/cpm5-link-status/) |

### Vitis AI

These skills compile and optimize ONNX models for AMD NPU hardware with the VAIML compiler. Start with `vai-quantization-guide`, `vai-flag-configuration`, or `vai-custom-op`; they load specialized skills and [worker subagents](agents/) as needed.

| Skill | Description | Examples |
|-------|-------------|----------|
| [vai-quantization-guide](skills/vai-quantization-guide/) | Mixed-precision quantization for AMD NPU (VINT8, BF16, hybrid) | [resnet50-quantize-vint8-bf16](examples/vai-quantization-guide/resnet50-quantize-vint8-bf16/) |
| [vai-flag-configuration](skills/vai-flag-configuration/) | Iteratively tune `vitisai_config.json` compiler flags | — |
| [vai-custom-op](skills/vai-custom-op/) | Orchestrate parallel custom AIE operator development | [multi-input-multi-node-custom-op](examples/vai-custom-op/multi-input-multi-node-custom-op/) |
| [vai-custom-op-candidates](skills/vai-custom-op-candidates/) | Identify candidate custom-operator chains and their ONNX node boundaries | — |
| [vai-custom-op-implementation](skills/vai-custom-op-implementation/) | Develop, simulate, and integrate a custom AIE operator into a VAIML model | — |
| [vai-aie-kernel-development](skills/vai-aie-kernel-development/) | Guide AIE kernel coding, architecture-level optimization, and result analysis | — |
| [vai-aie-compiler-oriented-optimizations](skills/vai-aie-compiler-oriented-optimizations/) | Optimize custom AIE kernels for the Peano compiler and interpret compiler feedback | — |
| [vai-dequantize-model](skills/vai-dequantize-model/) | Remove ONNX quantization to produce a floating-point model for re-quantization | — |
| [vai-ffn-quantization](skills/vai-ffn-quantization/) | Identify feed-forward linear layers in ONNX models and apply per-layer INT8 quantization | — |
| [vai-fe-args](skills/vai-fe-args/) | Select compiler arguments for a model's mixed-precision quantization pattern | — |
| [vai-partition-analysis](skills/vai-partition-analysis/) | Analyze VAIML NPU partitions and propose changes to reduce fragmentation | — |
| [vai-perf-analysis](skills/vai-perf-analysis/) | Analyze compiled-model timing, operator costs, and bottlenecks with AI Analyzer | — |
| [vai-peak-bandwidth-analysis](skills/vai-peak-bandwidth-analysis/) | Use DDR DMA profiling to identify compute or memory bottlenecks and recommend tiling or QoS changes | — |

### Embedded Software

Build and customize boot firmware and embedded Linux images from a Vivado XSA using the AMD Embedded Development Framework (EDF) Yocto flow. These skills cover source setup, device tree and machine configuration, image builds, QEMU emulation, JTAG boot, and flash programming.

| Skill | Description | Examples |
|-------|-------------|----------|
| [esw-yocto-source-setup](skills/esw-yocto-source-setup/) | Fetch Yocto sources and initialize an EDF build environment | — |
| [esw-xsa-to-sdt](skills/esw-xsa-to-sdt/) | Generate a System Device Tree from a Vivado Xilinx Support Archive with `sdtgen` | — |
| [esw-gen-machine-conf](skills/esw-gen-machine-conf/) | Generate and validate Yocto machine configuration from a System Device Tree | — |
| [esw-yocto-build](skills/esw-yocto-build/) | Build boot firmware and Linux disk images with BitBake and inspect WIC images | — |
| [esw-runqemu](skills/esw-runqemu/) | Boot EDF images in QEMU and troubleshoot emulation startup and networking | — |
| [esw-jtag-boot](skills/esw-jtag-boot/) | Load and run board boot artifacts over JTAG | — |
| [esw-program-flash](skills/esw-program-flash/) | Program a board's QSPI or OSPI boot flash over JTAG | — |

### Segmented Configuration

These skills guide Versal Segmented Configuration design, validation, programming, and PL reload workflows.

| Skill | Description | Examples |
|-------|-------------|----------|
| [segcfg-overview](skills/segcfg-overview/) | Explain Segmented Configuration, its two-phase boot model, and supported devices | — |
| [segcfg-design-considerations](skills/segcfg-design-considerations/) | Guide IO bank, CPM/PCIe, and device-specific design constraints | — |
| [segcfg-design-check](skills/segcfg-design-check/) | Run design rule checks and verify PL reload compatibility with `pr_verify` | — |
| [segcfg-aie](skills/segcfg-aie/) | Guide AI Engine integration, XSA export, and PDI packaging with Segmented Configuration | — |
| [segcfg-dfx](skills/segcfg-dfx/) | Explain how Segmented Configuration interacts with Dynamic Function eXchange | — |
| [segcfg-hw-manager](skills/segcfg-hw-manager/) | Program segmented boot and PL images through Vivado Hardware Manager | — |
| [segcfg-pl-reload](skills/segcfg-pl-reload/) | Create and validate independently compiled PL images while keeping the boot image fixed | — |
