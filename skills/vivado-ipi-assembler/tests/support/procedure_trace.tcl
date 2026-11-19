# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# The call tracer ships with the library (lib/procedure_trace.tcl) so builds can be timed;
# tests keep sourcing it from here. Set procedure_trace::* variables before sourcing.
uplevel #0 [list source [file join [file dirname [file normalize [info script]]] .. .. lib procedure_trace.tcl]]
