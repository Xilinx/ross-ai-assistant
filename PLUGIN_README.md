<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# AMD Ross™ Agentic AI Assistant

Agent skills for AMD FPGA/SoC development.

**New here?** Start with the [Getting Started](https://github.com/Xilinx/ross-ai-assistant/tree/main/docs/getting-started) guide. See the [docs](https://github.com/Xilinx/ross-ai-assistant/tree/main/docs) for the FAQ, MCP reference, and support.

## Requirements

The Vivado skills need the Vivado MCP server, either installed on its own or through the Vivado AI Extension for VS Code / Cursor. Both are available from the [AMD Ross Agentic AI Assistant download page](https://www.amd.com/en/support/downloads/ross-agentic-ai.html).

## Using the skills

Invoke a skill by name, for example `/hls-optimize`. Skills call each other by those same names. Some clients list them under the plugin's name, `amd-ross-agentic-ai-assistant`.

Install these skills one way only. If you also installed them with `npx skills add` or by copying them, every skill is loaded twice; see [Troubleshooting](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/getting-started/install-plugin.md#troubleshooting).

## More

- [Skills and examples](https://github.com/Xilinx/ross-ai-assistant#skills)
- [Vivado MCP tools reference](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/reference/vivado-mcp-tools.md)
- [Vitis AI worker subagents](https://github.com/Xilinx/ross-ai-assistant/tree/main/agents)
- [FAQ](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/faq.md) and [support](https://github.com/Xilinx/ross-ai-assistant/blob/main/docs/support.md)
