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

Download the MCP binaries for your version from its [release notes](https://github.com/Xilinx/ross-ai-assistant/releases), which also list version-specific requirements. MCP binaries are also available from the [AMD Ross Agentic AI Assistant download page](https://www.amd.com/en/support/downloads/ross-agentic-ai.html).

Choose **one** of the two Vivado options below, and add ChipScope MCP Server when required by your hardware-debug workflow.

| Component | Description | Follow-up installation |
|-----------|-------------|------------------------|
| Vivado AI Extension (VS Code / Cursor) | Includes the Vivado MCP server, the skills in this repository, and the `amd-doc-search` MCP configuration | Install the extension with the [Vivado AI Extension setup guide](docs/getting-started/vivado-mcp.md#vs-code--cursor); no other installation needed |
| Vivado MCP Server (Linux or Windows) | The Vivado MCP server: Tcl command execution, project management, and synthesis/implementation | Register the server in your client with the [Vivado MCP Server setup guide](docs/getting-started/vivado-mcp.md), configure the [`amd-doc-search` MCP server](#install-the-amd-doc-search-mcp-server), then install the skills in this repo as a [plugin](#install-as-a-plugin) or [individually](#install-skills-interactive) |
| ChipScope MCP Server (Linux or Windows) | Hardware debug: ILA capture, IBERT/GT scans, DDR calibration and margins, NoC analysis, system monitoring, and device/memory access | Follow the [ChipScope MCP Server setup guide](docs/getting-started/chipscope-mcp.md) for prerequisites, installation, and client configuration |

Use the standalone Vivado MCP Server for CLI clients or the Vivado AI Extension for IDE setups. ChipScope MCP Server can be installed alongside either option. It launches `cs_server`, usually from a local Vivado installation; see the [ChipScope MCP Server setup guide](docs/getting-started/chipscope-mcp.md#prerequisites).

### Install the amd-doc-search MCP server

The `amd-doc-search` MCP server searches AMD FPGA and Adaptive SoC documentation: user guides and device collaterals, and tool usage for Vivado, Vitis, embedded system software, and related products.

The Vivado AI Extension already includes this configuration. With the standalone Vivado MCP Server, add the server yourself.

For detailed installation instructions, see [https://ross.amd.com/chat#setup](https://ross.amd.com/chat#setup).

> **Local knowledge base.** If you want documentation search to stay on your own machine, use the [local knowledge base](docs/local-kb/README.md) instead of the hosted `amd-doc-search` service.

### Install as a plugin

This repository is also an agent plugin, which adds every skill in one step. Install from the public GitHub marketplace in Claude Code or Codex, import it into a ChatGPT workspace, or use a local folder in supported clients. Updates depend on the client: refresh the marketplace and update the installed plugin, sync the ChatGPT workspace import, or update the folder and reload the client.

Once installed, invoke the skills by name, for example `/hls-optimize`, and they call each other by those same names. Some clients list them under the plugin's name, `amd-ross-agentic-ai-assistant`.

> **Choose one install method.** If you install the plugin, do not also install the skills with `npx skills add` or by copying them. Every skill would then be loaded twice.

The plugin contains skills only. Install and configure the MCP servers required by your selected skills separately; see [Prerequisites](#prerequisites) for the Vivado and ChipScope options.

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

The **Examples** column links to tasks you can run with each skill. [All examples](examples/README.md) lists them by tool, with what each one needs.

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
| [vivado-convert-flat-to-dfx](skills/vivado-convert-flat-to-dfx/) | Convert a flat UltraScale+ or Versal design into a DFX-ready project | — |
| [vivado-netlist-debug-insert](skills/vivado-netlist-debug-insert/) | Insert ILA debug cores on selected nets in a synthesized design and check capture memory usage | [vivado-netlist-debug-insert](examples/vivado-netlist-debug-insert/) |
| [vivado-revision-control](skills/vivado-revision-control/) | Manage Vivado project files under revision control | [export-ipi-project](examples/vivado-revision-control/export-ipi-project/) |

### Vitis HLS

Start with `hls-matlab-to-cpp`, `hls-architect`, or `hls-optimize`. These are the primary entry points for HLS development. You can also invoke `hls-run-flow` directly to build an HLS component.

| Skill | Description | Examples |
|-------|-------------|----------|
| [hls-matlab-to-cpp](skills/hls-matlab-to-cpp/) | Convert MATLAB sample-based code to C++ frame-based loops and verify against MATLAB results | [edge-detection](examples/edge-detection/) |
| [hls-architect](skills/hls-architect/) | Convert input code to a multi-stage HLS dataflow architecture | [hls-intro-matmul](examples/hls-intro-matmul/), [edge-detection](examples/edge-detection/) |
| [hls-optimize](skills/hls-optimize/) | Iteratively optimize an HLS kernel against performance and resource targets | [globaltonemapping](examples/globaltonemapping/), [hls-intro-matmul](examples/hls-intro-matmul/), [edge-detection](examples/edge-detection/) |
| [hls-run-flow](skills/hls-run-flow/) | Run C simulation, synthesis, C/RTL cosimulation, and implementation | [HLS examples](examples/README.md#vitis-hls) |

The other `hls-*` skills are helpers. The skills above, or your agent, load them as needed: report extraction (`hls-component-basic-info`, `hls-synth-report`, `hls-cosim-report`, `hls-impl-report`, `hls-dataflow-infos`), code checks (`hls-synthesizable`, `hls-dataflow`, `hls-array-to-stream`, `hls-burst-inference`, `hls-stencil-pattern`, `hls-line-buffer`), diagnosis and restructuring (`hls-ii-diagnose`, `hls-pipe-decomp`, `hls-flattenable`), and pragma guidance (`hls-perf-pragma`, `hls-pipeline`, `hls-pragma-scope`). You don't need to invoke them yourself.

### Vitis

These skills cover embedded applications, AI Engine development, system integration, simulation, and profiling. Component and system builds can run in the Vitis IDE or through `vitis -s`; platform annotation operates on a Vivado block design.

| Skill | Description | Examples |
|-------|-------------|----------|
| [vitis-embedded](skills/vitis-embedded/) | Create an embedded platform, application, and boot image | [standalone platform](examples/vitis-embedded/platform-standalone-vck190/), [Linux platform](examples/vitis-embedded/platform-linux-zcu102/), [application](examples/vitis-embedded/app-hello-vck190/), [boot image](examples/vitis-embedded/boot-image-versal-pdi/) |
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
| [hw-ila-debug](skills/hw-ila-debug/) | Trigger, capture, and export waveform data from ILA debug cores | [axi-protocol-capture](examples/hw-ila-debug/axi-protocol-capture/), [all-tools](examples/chipscope-mcp/all-tools/) |
| [hw-vio-debug](skills/hw-vio-debug/) | Read input probes and drive output probes through VIO | [axi-register-rw](examples/hw-vio-debug/axi-register-rw/), [all-tools](examples/chipscope-mcp/all-tools/) |
| [hw-ddrmc-debug](skills/hw-ddrmc-debug/) | Check Versal DDR memory controller calibration, margins, and 2D eye scans | [vck190-ddr-health](examples/hw-ddrmc-debug/vck190-ddr-health/) |
| [hw-ibert-gt-debug](skills/hw-ibert-gt-debug/) | Debug serial transceiver links and run eye or YK scans | [gty-loopback-eye-scan](examples/hw-ibert-gt-debug/gty-loopback-eye-scan/) |
| [hw-noc-debug](skills/hw-noc-debug/) | Diagnose Versal NoC decode errors, timeouts, and AXI protocol failures | [write-decode-error](examples/hw-noc-debug/write-decode-error/), [write-timeout](examples/hw-noc-debug/write-timeout/), [axsize-violation](examples/hw-noc-debug/axsize-violation/), [burst-4k-crossing](examples/hw-noc-debug/burst-4k-crossing/) |
| [hw-noc-perfmon](skills/hw-noc-perfmon/) | Measure NoC bandwidth and latency on live Versal hardware | [vck190-noc-bandwidth](examples/hw-noc-perfmon/vck190-noc-bandwidth/) |
| [hw-pcie-link-debug](skills/hw-pcie-link-debug/) | Diagnose CPM5 PCIe link bring-up, training, and LTSSM state on Versal Premium | [cpm5-link-status](examples/hw-pcie-link-debug/cpm5-link-status/) |

### Vitis AI

These skills compile and optimize ONNX models for AMD NPU hardware with the VAIML compiler. Start with one of the three workflow skills below. Each one loads helper skills and [worker subagents](agents/) as needed. For setup, board access, and how each workflow runs, see [AI-Assisted Workflows](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/index.html) in the Vitis AI documentation.

| If you want to | Skill | Workflow | Manual procedure | Examples |
|----------------|-------|----------|------------------|----------|
| Implement a custom operator to extend what the NPU can run, or to improve how it runs | [vai-custom-op](skills/vai-custom-op/) | [Custom operator workflow](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/custom_op.html) | [Custom operators](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/additional_information/custom_ops/custom_ops_introduction.html) | [multi-input-multi-node-custom-op](examples/vai-custom-op/multi-input-multi-node-custom-op/) |
| Quantize a model, including per-layer mixed precision | [vai-quantization-guide](skills/vai-quantization-guide/) | [Model quantization workflow](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/quantization_guide.html) | [Model Quantization](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_quantization/model_quantization.html), [Mixed Precision Compilation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/mixed_precision_compilation.html) | [resnet50-quantize-vint8-bf16](examples/vai-quantization-guide/resnet50-quantize-vint8-bf16/) |
| Increase NPU offload, reduce inference time, or get a model to compile | [vai-flag-configuration](skills/vai-flag-configuration/) | [Compiler options workflow](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/ai_workflows/flag_configuration.html) | [Vitis AI EP Options](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-options.html) | — |

Helper skills (`vai-custom-op-candidates`, `vai-custom-op-implementation`, `vai-aie-kernel-development`, `vai-aie-compiler-oriented-optimizations`, `vai-partition-analysis`, `vai-fe-args`, `vai-dequantize-model`, `vai-ffn-quantization`, `vai-perf-analysis`, `vai-peak-bandwidth-analysis`) are loaded by the workflow skills, by the workers in `agents/`, or by your agent when a task needs them.

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

---

**Need help?** See the [FAQ](docs/faq.md) for common questions. For support and how to report issues, see the [Support](docs/support.md) page, which lists [known issues and limitations](docs/support.md#known-limitations).
