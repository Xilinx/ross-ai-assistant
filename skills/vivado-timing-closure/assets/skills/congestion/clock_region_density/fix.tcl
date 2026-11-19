# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / Clock Region Density Fix
# =============================================================================
# Forces logic spreading across clock regions to reduce density hotspots.
# =============================================================================

puts "\[FIX\] Applying clock region density reduction strategy..."

# SpreadLogic directives are applied via the TCL generator's subdirective mechanism.
# This file handles explicit fix sourcing scenarios.
# The main action is: place_design with ForceSpreading and ReducePinDensity subdirectives.
