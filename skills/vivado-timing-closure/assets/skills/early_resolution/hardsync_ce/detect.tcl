# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# Shim -- canonical copy: assets/skills/clocking/hardsync_ce/detect.tcl
# Kept at this path so existing callers keep working; edit the canonical file.
# Sourced in the caller's scope, so variables it sets remain visible to the caller.
source [file normalize [file join [file dirname [file normalize [info script]]] .. .. clocking hardsync_ce detect.tcl]]
