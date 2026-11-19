# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / Macro Placement Fix
# =============================================================================
# Spreads macros to reduce local congestion hotspots.
# =============================================================================

puts "\[FIX\] Applying macro spread strategy..."

# Use AltSpreadLogic_high to encourage macro spreading
# Combined with ReduceCongestion for placement
# Note: These are applied via place_design subdirectives in the TCL generator.
# This file is for explicit sourcing scenarios.
