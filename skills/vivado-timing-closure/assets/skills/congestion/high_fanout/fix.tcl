# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / High Fanout Fix
# =============================================================================
# Applies strategies to mitigate high-fanout induced congestion.
# =============================================================================

puts "\[FIX\] Applying high-fanout congestion mitigation..."

# Enable aggressive fanout optimization during physical optimization
# phys_opt_design will replicate drivers and insert buffers
phys_opt_design -fanout_opt
