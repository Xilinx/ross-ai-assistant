<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis System (integration link · system package)

Compose a Vitis system in one skill — V++ link AIE + HLS components into a
fixed XSA/VMA/VSS (integration), then package the binary containers into a deployable image
such as an SD-card image, BOOT.BIN, or XCLBIN (system). In-IDE via the Vitis Python API
(`runVitisPython`) or headless via `vitis -s`; compose-only by default, link/package on request.

## Tested clients and models

| Client | Models Tested | Date |
| --- | --- | --- |
| Claude Code | Claude Opus 5.5, Claude Sonnet 5.5, Claude Haiku 5.5 | 2026-11-19 |
