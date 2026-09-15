<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Support

## Getting Help

As an Early Access participant, you have direct access to the engineering team and a private community of fellow EA users.

### EA Community Forum (Recommended)

**[AMD-Embedded Agentic AI - EA Forum](https://adaptivesupport.amd.com/s/group/0F9Pd00000091UjKAI/vivado-ai-assistant)**

We **strongly encourage** using the private EA forum as your primary support channel. Because this is an AI-powered tool, every user's experience is unique — sharing your questions, issues, and discoveries helps the entire EA community:

- **Post questions and issues** — other EA participants and AMD engineers can help
- **Share tips and workflows** — your creative use of the agent may inspire others
- **Stay informed** — AMD will broadcast new features, releases, and best practices here
- **Help AMD prioritize** — seeing common patterns across users helps us address issues faster

### Email Support

[embedded_agentic_ai@amd.com](mailto:embedded_agentic_ai@amd.com)

A dedicated support alias monitored by the engineering team. Use this for issues you'd prefer not to post publicly, or for sharing sensitive design details.

> **Tip:** Even if you start with email, consider posting a summary on the forum — your experience likely helps others facing similar challenges.

### Reporting Issues

When reporting an issue (via forum or email), please include:

1. **Tool and version** — IDE/CLI tool name and version, MCP server version, extension version
2. **Vivado/HLS version** — Output of `version` in the Vivado Tcl console, or `vitis-run --version`
3. **Steps to reproduce** — What you asked the agent and what happened
4. **Logs** — Relevant logs from:
    - Vivado Tcl console output or HLS build log
    - MCP server output (check your IDE's Output panel > Vivado MCP)
    - Agent conversation transcript
5. **Design context** — Target device, design size, which skill was used

## Known Limitations

| Limitation | Workaround |
|------------|------------|
| LSF tool (`vivado_lsf`) only works inside the AMD network | Use `vivado_ssh` for remote deployment outside AMD. LSF support for external customers is planned for a future release |
| GUI mode may open on a virtual/VNC display instead of your native display | Tell the agent your display value, e.g. *"use display :0"* or *"use my $DISPLAY"*. Run `echo $DISPLAY` in your terminal to find it |
| Long-running Vivado commands may cause the chat to stop waiting for a response | Ask the agent to *"check status"* or *"continue"*. This is an AI infrastructure limitation, not specific to the MCP server |
| The agent may interpret "visualize the design" as taking GUI screenshots via `vivado_computer_use` | Disable the `vivado_computer_use` tool if you do not need automated GUI interaction |
| Some LLMs may not automatically call `vivado_doc_search` for knowledge queries | Add a system prompt instructing the agent to *"always use vivado_doc_search for AMD/Xilinx documentation questions"*, or explicitly state in your query: *"use vivado_doc_search to look up..."*. More capable models generally handle tool selection reliably; smaller or older models may need this guidance |

## Providing Feedback

Your feedback directly shapes the product. We're especially interested in:

- **New skill ideas** — What workflows would you like automated?
- **Accuracy** — Did the agent give correct recommendations?
- **Usability** — Was the setup process smooth? What was confusing?
- **Missing features** — What capabilities are you missing?

You can also submit feedback directly from the chat terminal using the **`vivado_feedback`** MCP tool. Simply type in the chat:

> *#vivado_feedback the timing closure workflow worked great, rated good*

The agent will call the `vivado_feedback` tool with your rating (`good`, `neutral`, or `poor`) and comments, which are recorded and sent to the AMD engineering team. No forms to fill out — just tell the agent what you thought.

Post feedback on the [EA Forum](https://adaptivesupport.amd.com/s/group/0F9Pd00000091UjKAI/vivado-ai-assistant) or email [embedded_agentic_ai@amd.com](mailto:embedded_agentic_ai@amd.com).

---
