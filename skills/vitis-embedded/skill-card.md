<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis Embedded (platform · app · boot image)

The Vitis embedded software lifecycle in one skill — create an embedded
hardware platform from an XSA, create a standalone/FreeRTOS/Linux application on it, and
package a bootable image (BOOT.BIN / PDI via Bootgen). In-IDE via the Vitis Python API
(`runVitisPython`) or headless via `vitis -s`; create-only by default, build/package on request.

## Tested clients and models

| Client | Models Tested | Date |
| --- | --- | --- |
| Claude Code | Claude Opus 5.5, Claude Sonnet 5.5, Claude Haiku 5.5 | 2026-11-19 |
