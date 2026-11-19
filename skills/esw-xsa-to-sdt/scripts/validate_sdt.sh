#!/bin/bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# =============================================================================
# validate_sdt.sh — SDTGen Agent Output Validator
# =============================================================================
# Validates a generated SDT output directory independently, using its generated includes.
# Can be run standalone or called by gen_sdt.sh.
#
# Usage:
#   ./validate_sdt.sh <sdt_output_dir>
#
# Arguments:
#   sdt_output_dir  Path to the SDT output directory
# =============================================================================

set -uo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SDT_DIR="${1:-}"
ERRORS=0
WARNINGS=0
FLAT_DTS=""   # cpp-flattened system-top.dts, produced by the DTC check
DTC_WORKDIR=""

log_ok()    { echo -e "${GREEN}[PASS]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; ((WARNINGS++)); }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; ((ERRORS++)); }
log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }

# Each invocation owns its private temporary files; remove them on every exit.
# Invoked indirectly by the EXIT trap.
# shellcheck disable=SC2317
cleanup_dtc() {
    if [ -n "$DTC_WORKDIR" ] && [ -d "$DTC_WORKDIR" ]; then
        rm -f -- "$DTC_WORKDIR/check.dtb" "$DTC_WORKDIR/flattened.dts" "$DTC_WORKDIR/dtc.log"
        rmdir -- "$DTC_WORKDIR"
    fi
}
trap cleanup_dtc EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Validate arguments ---
if [ -z "${SDT_DIR}" ]; then
    echo "Usage: $0 <sdt_output_dir>"
    exit 1
fi

if [ ! -d "${SDT_DIR}" ]; then
    log_error "Directory not found: ${SDT_DIR}"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SDT Validation: ${SDT_DIR}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# --- Check 1: Required files ---
log_info "--- Checking required files ---"

check_required() {
    local file="$1"
    local desc="$2"
    if [ -f "${SDT_DIR}/${file}" ]; then
        local size
        size=$(du -h "${SDT_DIR}/${file}" | cut -f1)
        log_ok "${file} (${size}) — ${desc}"
        return 0
    else
        log_error "MISSING: ${file} — ${desc}"
        return 1
    fi
}

check_required "system-top.dts" "Top-level System Device Tree"
# Require pcw.dtsi only when the generated wrapper includes it.
if grep -Eq '^[[:space:]]*#include[[:space:]]*["<]pcw\.dtsi[">]' "${SDT_DIR}/system-top.dts"; then
    check_required "pcw.dtsi" "Peripheral Configuration Wizard"
else
    log_info "pcw.dtsi not generated"
fi
if grep -Eq '^[[:space:]]*#include[[:space:]]*["<]pl\.dtsi[">]' "${SDT_DIR}/system-top.dts"; then
    check_required "pl.dtsi" "PL/Soft-IP information"
fi

# --- Check 2: DTSI / clock presence (generic, no family-specific names) ---
log_info "--- Checking DTSI content ---"
dtsi_count=$(find "${SDT_DIR}" -maxdepth 1 -name "*.dtsi" 2>/dev/null | wc -l)
if [ "$dtsi_count" -gt 0 ]; then
    log_ok "DTSI files present: ${dtsi_count}"
else
    log_warn "No .dtsi files found in output"
fi

clk_count=$(find "${SDT_DIR}" -maxdepth 1 -name "*clk*.dtsi" 2>/dev/null | wc -l)
if [ "$clk_count" -gt 0 ]; then
    log_ok "Clock DTSI present ($(find "${SDT_DIR}" -maxdepth 1 -name "*clk*.dtsi" -exec basename {} \; | tr '\n' ' '))"
else
    log_info "No separate clock DTSI (clocks may be inlined)"
fi

# --- Check 3: Include directory ---
if [ -d "${SDT_DIR}/include" ]; then
    binding_count=$(find "${SDT_DIR}/include" -name "*.h" 2>/dev/null | wc -l)
    log_ok "include/ directory (${binding_count} header files)"
else
    log_warn "include/ directory not found"
fi

# --- Check 4: PDI artifacts (informational) ---
pdi_count=$(find "${SDT_DIR}" -maxdepth 1 -name "*.pdi" 2>/dev/null | wc -l)
if [ "$pdi_count" -gt 0 ]; then
    log_ok "PDI files: ${pdi_count} found"
    find "${SDT_DIR}" -maxdepth 1 -name "*.pdi" -exec ls -lh {} \; 2>/dev/null | \
        awk '{print "     " $NF " (" $5 ")"}'
fi
if [ -d "${SDT_DIR}/extracted" ]; then
    extracted_count=$(find "${SDT_DIR}/extracted" -type f 2>/dev/null | wc -l)
    log_ok "extracted/ directory (${extracted_count} files)"
fi

# --- Check 5: DTC compile ---
log_info "--- DTC Compile Check ---"
if command -v dtc &>/dev/null && command -v cpp &>/dev/null; then
    DTC_WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/sdt-validate.XXXXXX") || {
        log_error "Cannot create validation temporary directory under ${TMPDIR:-/tmp}"
        exit 1
    }
    # Make paths absolute before entering the SDT directory (TMPDIR may be relative).
    DTC_WORKDIR=$(readlink -f "$DTC_WORKDIR")
    dtc_output_file="$DTC_WORKDIR/dtc.log"
    FLAT_DTS="$DTC_WORKDIR/flattened.dts"
    dtc_exit=0

    cd "${SDT_DIR}" || exit 1
    if cpp -nostdinc -I include -undef -x assembler-with-cpp system-top.dts \
        > "${FLAT_DTS}" 2> "${dtc_output_file}"; then
        dtc -I dts -O dtb -b 0 -@ -o "$DTC_WORKDIR/check.dtb" "${FLAT_DTS}" \
            >> "${dtc_output_file}" 2>&1 || dtc_exit=$?
    else
        dtc_exit=1
        log_error "DTS preprocessing failed"
        FLAT_DTS=""
    fi
    cd - > /dev/null || exit 1

    error_count=$(grep -Eic '(^|[[:space:]])(fatal[[:space:]]+)?error([[:space:]]+\([^)]*\))?:' "${dtc_output_file}" || true); error_count=${error_count:-0}
    warning_count=$(grep -Ec 'Warning([[:space:]]+\([^)]*\))?:' "${dtc_output_file}" || true); warning_count=${warning_count:-0}

    if [ "$dtc_exit" -ne 0 ] || [ "$error_count" -gt 0 ]; then
        log_error "DTC compile FAILED (${error_count} errors, ${warning_count} warnings)"
        echo "  First 5 errors:"
        grep -Ei '(^|[[:space:]])(fatal[[:space:]]+)?error([[:space:]]+\([^)]*\))?:' "${dtc_output_file}" | head -5 | sed 's/^/    /'
    else
        log_ok "DTC compile PASSED (${warning_count} warnings)"
        if [ "$warning_count" -gt 10 ]; then
            log_warn "High DTC warning count — review compiler output below"
            head -20 "${dtc_output_file}"
        fi
    fi
else
    log_warn "dtc or cpp not available — skipping compile check; both are required"
fi

# --- Check 6: Content sanity ---
log_info "--- Content Sanity Check ---"
if [ -f "${SDT_DIR}/system-top.dts" ]; then
    # Count content after preprocessing when available.
    count_src="${SDT_DIR}/system-top.dts"
    count_scope="system-top.dts only — flattened tree unavailable"
    if [ -n "${FLAT_DTS}" ] && [ -s "${FLAT_DTS}" ]; then
        count_src="${FLAT_DTS}"
        count_scope="flattened tree"
    fi

    node_count=$(grep -c '{' "${count_src}" 2>/dev/null); node_count=${node_count:-0}
    line_count=$(wc -l < "${SDT_DIR}/system-top.dts")
    cpu_nodes=$(grep -Ec 'device_type[[:space:]]*=[[:space:]]*"cpu"[[:space:]]*;' "${count_src}" || true); cpu_nodes=${cpu_nodes:-0}
    memory_nodes=$(grep -c "memory@" "${count_src}" 2>/dev/null); memory_nodes=${memory_nodes:-0}

    log_info "system-top.dts: ${line_count} lines"
    log_info "  Nodes: ~${node_count} (${count_scope})"
    log_info "  CPU nodes: ${cpu_nodes}"
    log_info "  Memory nodes: ${memory_nodes}"

    # Tree size varies by platform; it is informational, not a completeness test.
    if [ -n "${FLAT_DTS}" ] && [ "$cpu_nodes" -eq 0 ]; then
        log_info "No CPU device nodes found (generation may be domain-specific)"
    fi
fi

# --- Summary ---
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    log_ok "ALL CHECKS PASSED"
    echo "  Generated SDT checks passed; downstream support must be checked separately"
    exit 0
elif [ $ERRORS -eq 0 ]; then
    log_warn "PASSED with ${WARNINGS} warnings"
    echo "  Review the warnings before downstream processing"
    exit 2
else
    log_error "FAILED: ${ERRORS} errors, ${WARNINGS} warnings"
    echo "  SDT generation needs investigation"
    exit 1
fi
