# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition / Unbalanced Utilization Fix
# =============================================================================
# Applies balanced SLR placement directives to spread logic more evenly.
# =============================================================================

puts "\[FIX\] Applying balanced SLR placement strategy..."

# Use SSI_SpreadLogic_high to force balanced distribution across SLRs
# This is applied as a place_design directive override — the TCL generator
# handles this via the subdirective mechanism. This file is for explicit sourcing.

# NOTE: `get_runs impl_1` only resolves to a real object in PROJECT MODE
# (a managed `impl_1` run). This whole framework operates in NON-project-
# mode -- a standalone Tcl session with `place_design -directive ...`
# called directly (the real SSI_SpreadLogic_High application for this
# skill happens via flows/tcl_generator.py's subdirective mechanism, as
# the comment above already says) -- so in that mode `get_runs impl_1`
# returns an empty list, `-quiet` silently swallows the resulting no-op,
# and sourcing this file standalone (its own stated purpose) currently
# does nothing. Not fixed here: making it take effect outside project
# mode would mean calling `place_design -directive SSI_SpreadLogic_High`
# directly from a "fix" script, which is a different mechanism/timing
# contract than every other skill's fix.tcl in this codebase (all of
# which only set constraints/properties, never invoke a placement step
# themselves) -- a design decision, not a mechanical bug fix.
set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE SSI_SpreadLogic_High [get_runs impl_1] -quiet
