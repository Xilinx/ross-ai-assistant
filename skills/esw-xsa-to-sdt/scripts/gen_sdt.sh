#!/bin/bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# =============================================================================
# SDTGen Agent - Wrapper Script
# =============================================================================
# Generates a System Device Tree from an XSA file.
#
# Usage:
#   ./gen_sdt.sh <xsa_path> [options]
#
# Options:
#   --board-dts <name>   Board DTS to apply (name or .dtsi); emitted as -user_dts
#   --custom-dts <path>  Additional custom/overlay DTSI; emitted as -user_dts
#   --zephyr-dts <dtsi>  Local or repository Zephyr DTSI; -user_zephyr_dts
#   --rm-xsa <path>      Reconfigurable-module XSA for DFX (repeatable); -rm_xsa
#   --domain pmc         Restrict generation to the PMC domain; emitted as -domain
#   --list-boards        List available board DTS names from the SDT repo and exit
#   --outdir <path>      Output directory (default: $SDTGEN_OUTDIR, else ./sdt_output)
#   --debug              Enable debug and trace output
#   --validate-only      Only validate existing SDT output (no sdtgen needed)
#   --env-check          Only check environment readiness
#   --clean              Clean output dir before generation
#   -h, --help           Show this help
#
# Environment:
#   Resolve sdtgen via XILINX_VITIS, XILINX_VIVADO, or PATH; settings64.sh
#   supplies the installation runtime environment when needed.
#   --list-boards needs CUSTOM_SDT_REPO, XILINX_VITIS, or XILINX_VIVADO
#   pointing to a repository or installation containing the board DTSIs.
#   SDTGEN_OUTDIR (optional) - default output directory; --outdir overrides it
#   CUSTOM_SDT_REPO (optional) - path to local system-device-tree-xlnx
# =============================================================================

set -euo pipefail

# Resolve this script's directory so the sibling validator can be reused
# instead of maintaining a second, weaker copy of the same checks here.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Color output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Defaults ---
XSA_PATH=""
BOARD_DTS=""
BOARD_DTSI=""            # BOARD_DTS normalised once to a .dtsi filename
CUSTOM_DTSI=""
ZEPHYR_DTSI=""
DOMAIN=""
RM_XSA=()
SDT_OUTDIR="${SDTGEN_OUTDIR:-./sdt_output}"
DEBUG_MODE=0
VALIDATE_ONLY=0
ENV_CHECK_ONLY=0
LIST_BOARDS=0
CLEAN_FIRST=0
SDTGEN_BIN=""
TOOL_VERSION=""
LOG_FILE=""

# --- Logging ---
log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[PASS]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[FAIL]${NC} $*"; }

# --- Parse arguments ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --board-dts)    BOARD_DTS="$2"; shift 2 ;;
            --custom-dts)   CUSTOM_DTSI="$2"; shift 2 ;;
            --zephyr-dts)   ZEPHYR_DTSI="$2"; shift 2 ;;
            --rm-xsa)       RM_XSA+=("$2"); shift 2 ;;
            --domain)       DOMAIN="$2"; shift 2 ;;
            --outdir)       SDT_OUTDIR="$2"; shift 2 ;;
            --debug)        DEBUG_MODE=1; shift ;;
            --validate-only) VALIDATE_ONLY=1; shift ;;
            --env-check)    ENV_CHECK_ONLY=1; shift ;;
            --list-boards)  LIST_BOARDS=1; shift ;;
            --clean)        CLEAN_FIRST=1; shift ;;
            -h|--help)      show_help; exit 0 ;;
            -*)             log_error "Unknown option: $1"; show_help; exit 1 ;;
            *)              XSA_PATH="$1"; shift ;;
        esac
    done

    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != pmc ]; then
        log_error "Invalid domain: $DOMAIN; only pmc is supported"
        exit 1
    fi
    for dts_input in "$BOARD_DTS" "$CUSTOM_DTSI"; do
        [ -n "$dts_input" ] || continue
        case "${dts_input,,}" in
            *.dts) log_error "User DTS inputs must use .dtsi or .dtso, not .dts"; exit 1 ;;
        esac
    done
    if [ -n "$CUSTOM_DTSI" ]; then
        case "${CUSTOM_DTSI,,}" in
            *.dtsi|*.dtso) : ;;
            *) log_error "Custom DTS input must use .dtsi or .dtso"; exit 1 ;;
        esac
    fi
    if [ -n "$ZEPHYR_DTSI" ] && [[ "${ZEPHYR_DTSI,,}" != *.dtsi ]]; then
        log_error "Zephyr DTS input must use .dtsi"; exit 1
    fi

    # Add the extension only to bare board names.
    if [ -n "${BOARD_DTS}" ]; then
        case "${BOARD_DTS,,}" in
            *.dtsi|*.dtso) BOARD_DTSI="${BOARD_DTS}" ;;
            *)             BOARD_DTSI="${BOARD_DTS}.dtsi" ;;
        esac
    fi
}

# Print the header comment block. Derived from the block itself rather than a
# hardcoded line count, so adding an option cannot silently truncate the help.
show_help() {
    awk 'NR==1 && /^#!/ { next }
         /^#/ { sub(/^# ?/, ""); print; next }
         { exit }' "$0"
}

# =============================================================================
# List available board DTS names from the SDT repo
# =============================================================================
# Board DTSI files live under <repo>/device_tree/data/kernel_dtsi/BOARD/linux.
# The repo is resolved from CUSTOM_SDT_REPO, or the Vitis/Vivado install.
list_boards() {
    local repo=""
    if [ -n "${CUSTOM_SDT_REPO:-}" ]; then
        repo="${CUSTOM_SDT_REPO}"
    elif [ -n "${XILINX_VITIS:-}" ]; then
        repo="${XILINX_VITIS}/data/system-device-tree-xlnx"
    elif [ -n "${XILINX_VIVADO:-}" ]; then
        repo="${XILINX_VIVADO}/data/system-device-tree-xlnx"
    fi

    local base="${repo}/device_tree/data/kernel_dtsi"
    if [ -z "${repo}" ] || [ ! -d "${base}" ]; then
        log_error "Board DTSI directory not found: ${base:-<unset>}"
        log_info "  Set CUSTOM_SDT_REPO, or source Vitis/Vivado settings first"
        return 1
    fi

    log_info "Available board DTS (from ${base}):"
    local boards
    boards=$(find "${base}" -path "*/linux/*.dtsi" -exec basename {} .dtsi \; 2>/dev/null | sort -u)
    if [ -z "${boards}" ]; then
        # Fall back to any .dtsi under kernel_dtsi if no */linux/ layout
        boards=$(find "${base}" -name "*.dtsi" -exec basename {} .dtsi \; 2>/dev/null | sort -u)
    fi
    if [ -z "${boards}" ]; then
        log_warn "  No board DTSI files found under ${base}"
        return 1
    fi
    echo "${boards}"
}

# =============================================================================
# Tool version detection
# =============================================================================
# Scan for the version component because installation layouts place it at
# different depths, such as <root>/<ver>/Vitis/bin and <root>/Vitis/<ver>/bin.
detect_tool_version() {
    local v
    v=$(echo "${SDTGEN_BIN}" | grep -oE '/20[0-9]{2}\.[0-9]+(\.[0-9]+)?/' | head -1 | tr -d '/') || true
    echo "${v:-unknown}"
}

# =============================================================================
# Phase 1: Environment Validation
# =============================================================================
check_environment() {
    log_info "=== Phase 1: Environment Validation ==="
    local status=0

    # Check sdtgen binary
    if [ -n "${XILINX_VITIS:-}" ] && [ -x "${XILINX_VITIS}/bin/sdtgen" ]; then
        SDTGEN_BIN="${XILINX_VITIS}/bin/sdtgen"
        log_ok "sdtgen found at: ${SDTGEN_BIN} (via XILINX_VITIS)"
    elif [ -n "${XILINX_VIVADO:-}" ] && [ -x "${XILINX_VIVADO}/bin/sdtgen" ]; then
        SDTGEN_BIN="${XILINX_VIVADO}/bin/sdtgen"
        log_ok "sdtgen found at: ${SDTGEN_BIN} (via XILINX_VIVADO)"
    elif command -v sdtgen &>/dev/null; then
        SDTGEN_BIN="$(command -v sdtgen)"
        log_ok "sdtgen found on PATH: ${SDTGEN_BIN}"
    else
        log_error "sdtgen not found!"
        log_info "  XILINX_VITIS, XILINX_VIVADO and PATH all failed to resolve it."
        log_info "  Fix: source <Vitis_install>/settings64.sh"
        log_info "   OR: export XILINX_VITIS=<install_root>/Vitis"
        log_info "  Ask the user which install to use — do NOT guess an install"
        log_info "  root, and do not assume a shared//proj build is the intended"
        log_info "  toolchain. See references/troubleshooting.md."
        status=1
    fi

    # Report the version and, when the install is reached through symlinks,
    # the canonical path. Floating pointers such as "<ver>_daily_latest" move
    # between runs, so the resolved path is what makes a run reproducible.
    if [ $status -eq 0 ]; then
        TOOL_VERSION="$(detect_tool_version)"
        log_info "Tool version: ${TOOL_VERSION}"
        local real_bin
        real_bin="$(readlink -f "${SDTGEN_BIN}" 2>/dev/null || echo "${SDTGEN_BIN}")"
        if [ "${real_bin}" != "${SDTGEN_BIN}" ]; then
            log_info "Resolved install (canonical): ${real_bin}"
            log_info "  Pin this path if you need a reproducible rebuild."
        fi
    fi

    # Check custom SDT repo (optional)
    if [ -n "${CUSTOM_SDT_REPO:-}" ]; then
        if [ -d "${CUSTOM_SDT_REPO}" ] && [ -f "${CUSTOM_SDT_REPO}/device_tree/data/device_tree.tcl" ]; then
            log_ok "CUSTOM_SDT_REPO: ${CUSTOM_SDT_REPO}"
        else
            log_error "CUSTOM_SDT_REPO set but path invalid: ${CUSTOM_SDT_REPO}"
            status=1
            log_info "  Expected: <repo>/device_tree/data/device_tree.tcl"
        fi
    else
        log_info "CUSTOM_SDT_REPO not set (using installed repo)"
    fi

    # Check dtc for validation
    if command -v dtc &>/dev/null; then
        log_ok "dtc available for validation: $(command -v dtc)"
    else
        log_warn "dtc not found — output validation will be limited"
    fi

    # Check cpp for preprocessing
    if command -v cpp &>/dev/null; then
        log_ok "cpp available for DTS preprocessing"
    else
        log_warn "cpp not found — DTC compile validation will be skipped"
    fi

    return $status
}

# =============================================================================
# Whitespace preflight
# =============================================================================
# sdtgen's launcher flattens its argv into a single Tcl string
# (dt_params="${RDI_ARGS[*]}"; set_dt_param $dt_params), so any path containing
# whitespace is re-split *inside the tool* and fails as:
#   could not read "/tmp/sp": no such file or directory
# Quoting on this side cannot prevent that, so reject it up front with an
# actionable message instead of surfacing a Tcl stack trace.
check_no_whitespace() {
    local label="$1" value="$2"
    case "${value}" in
        *[[:space:]]*)
            log_error "${label} contains whitespace: ${value}"
            return 1
            ;;
    esac
    return 0
}

preflight_paths() {
    local bad=0 f
    check_no_whitespace "XSA path" "${XSA_PATH}" || bad=1
    check_no_whitespace "Output dir" "${SDT_OUTDIR}" || bad=1
    if [ -n "${CUSTOM_DTSI}" ]; then
        check_no_whitespace "Custom DTSI" "${CUSTOM_DTSI}" || bad=1
    fi
    if [ -n "${ZEPHYR_DTSI}" ]; then
        check_no_whitespace "Zephyr DTSI" "${ZEPHYR_DTSI}" || bad=1
    fi
    if [ ${#RM_XSA[@]} -gt 0 ]; then
        for f in "${RM_XSA[@]}"; do
            check_no_whitespace "RM XSA" "${f}" || bad=1
        done
    fi
    if [ ${bad} -ne 0 ]; then
        log_info "  sdtgen re-splits its own arguments and cannot handle whitespace."
        log_info "  Copy or symlink to a whitespace-free path and retry, e.g.:"
        log_info "    ln -s '<path with spaces>' /tmp/design.xsa"
        exit 1
    fi
}

# =============================================================================
# XSA / tool version preflight
# =============================================================================
# Read exporting-tool metadata without assuming every archive has sysdef.xml.
check_xsa_tool_version() {
    command -v unzip &>/dev/null || { log_info "unzip unavailable — XSA version check skipped"; return 0; }
    local xsa_ver="" entry
    for entry in sysdef.xml xsa.xml; do
        xsa_ver=$(unzip -p "${XSA_PATH}" "$entry" 2>/dev/null | sed -n -e 's/.*TOOL_VERSION[^>]*Version="\([^"]*\)".*/\1/p' -e 's/.*GenAppInfo[^>]*Name="Vivado"[^>]*Version="\([^"]*\)".*/\1/p' | head -1) || true
        [ -z "$xsa_ver" ] || break
    done
    if [ -z "$xsa_ver" ]; then
        log_info "Exporting Vivado version unavailable — XSA version check skipped"
        return 0
    fi

    log_info "XSA exported by Vivado ${xsa_ver}"

    if [ -n "${TOOL_VERSION}" ] && [ "${TOOL_VERSION}" != "unknown" ]; then
        case "${xsa_ver}" in
            "${TOOL_VERSION}"*) : ;;
            *)
                log_warn "Version skew: sdtgen ${TOOL_VERSION} vs XSA ${xsa_ver}"
                log_info "  Re-export the XSA from a matching Vivado, or switch tools."
                log_info "  See references/troubleshooting.md for diagnosis guidance."
                ;;
        esac
    fi
}

# =============================================================================
# Phase 2: SDT Generation
# =============================================================================
generate_sdt() {
    log_info "=== Phase 2: SDT Generation ==="

    if [ ! -f "${XSA_PATH}" ]; then
        log_error "XSA file not found: ${XSA_PATH}"
        exit 1
    fi

    log_info "XSA: ${XSA_PATH} ($(du -h "${XSA_PATH}" | cut -f1))"
    preflight_paths
    check_xsa_tool_version

    # Clean if requested
    if [ ${CLEAN_FIRST} -eq 1 ] && [ -d "${SDT_OUTDIR}" ]; then
        log_info "Cleaning previous output: ${SDT_OUTDIR}"
        rm -rf "${SDT_OUTDIR}"
    fi

    # Preserve argument boundaries.
    local -a cmd=( "${SDTGEN_BIN}" -xsa "${XSA_PATH}" -dir "${SDT_OUTDIR}" )

    # -board_dts is deprecated from 2026.2 and removed in future releases;
    # use -user_dts with .dtsi filenames instead. Assemble a single -user_dts
    # list from the board DTS (by .dtsi filename) plus any custom/overlay DTSI,
    # e.g. -user_dts zcu102-rev1.0.dtsi ./overlay.dtsi
    local -a user_dts=()
    if [ -n "${BOARD_DTSI}" ]; then
        user_dts+=( "${BOARD_DTSI}" )
    fi

    if [ -n "${CUSTOM_DTSI}" ]; then
        if [ ! -f "${CUSTOM_DTSI}" ]; then
            log_error "Custom DTSI not found: ${CUSTOM_DTSI}"
            exit 1
        fi
        user_dts+=( "${CUSTOM_DTSI}" )
    fi

    if [ ${#user_dts[@]} -gt 0 ]; then
        cmd+=( -user_dts "${user_dts[@]}" )
    fi

    if [ -n "${ZEPHYR_DTSI}" ]; then
        if [[ "${ZEPHYR_DTSI}" == */* ]] && [ ! -f "${ZEPHYR_DTSI}" ]; then
            log_error "Zephyr DTSI not found: ${ZEPHYR_DTSI}"
            exit 1
        fi
        cmd+=( -user_zephyr_dts "${ZEPHYR_DTSI}" )
    fi

    if [ ${#RM_XSA[@]} -gt 0 ]; then
        local rm_file
        for rm_file in "${RM_XSA[@]}"; do
            if [ ! -f "${rm_file}" ]; then
                log_error "RM XSA not found: ${rm_file}"
                exit 1
            fi
        done
        cmd+=( -rm_xsa "${RM_XSA[@]}" )
    fi

    if [ -n "${DOMAIN}" ]; then
        cmd+=( -domain "${DOMAIN}" )
    fi

    if [ ${DEBUG_MODE} -eq 1 ]; then
        cmd+=( -debug enable -trace enable )
    fi

    log_info "Executing: $(printf '%q ' "${cmd[@]}")"

    # The output dir may not exist yet, so log alongside it during the run and
    # copy the log inside afterwards, so it ships with the handoff artefact.
    LOG_FILE="${SDT_OUTDIR%/}_build.log"
    rm -f "${LOG_FILE}"

    local start_time
    start_time=$(date +%s)
    local rc=0

    "${cmd[@]}" 2>&1 | tee "${LOG_FILE}" || rc=$?

    local duration=$(( $(date +%s) - start_time ))

    # sdtgen does not reliably exit non-zero on every failure, so treat logged
    # ERROR lines as failures too rather than trusting the exit code alone.
    local logged_errors=0
    if [ -f "${LOG_FILE}" ]; then
        logged_errors=$(grep -c '^ERROR:' "${LOG_FILE}" 2>/dev/null) || true
        logged_errors=${logged_errors:-0}
    fi

    if [ ${rc} -ne 0 ] || [ "${logged_errors}" -gt 0 ]; then
        log_error "SDT generation FAILED after ${duration}s (exit ${rc}, ${logged_errors} logged ERROR lines)"
        diagnose_failure "${LOG_FILE}"
        exit 1
    fi

    log_ok "SDT generation completed in ${duration}s"

    if [ -d "${SDT_OUTDIR}" ]; then
        cp -f "${LOG_FILE}" "${SDT_OUTDIR}/sdtgen_build.log" 2>/dev/null || true
    fi
}

# =============================================================================
# Phase 3: Output Validation
# =============================================================================
# Delegate output checks to the standalone validator.
# validate_sdt.sh exit codes: 0 = pass, 1 = fail, 2 = pass with warnings.
validate_output() {
    log_info "=== Phase 3: Output Validation ==="

    if [ ! -d "${SDT_OUTDIR}" ]; then
        log_error "Output directory not found: ${SDT_OUTDIR}"
        exit 1
    fi

    local validator="${SCRIPT_DIR}/validate_sdt.sh"
    if [ ! -f "${validator}" ]; then
        log_error "Validator not found: ${validator}"
        exit 1
    fi

    local rc=0
    bash "${validator}" "${SDT_OUTDIR}" || rc=$?

    # Board DTSI presence is a wrapper-level concern: validate_sdt.sh is board
    # agnostic and cannot know which board DTS was requested.
    if [ -n "${BOARD_DTSI}" ]; then
        if [ -f "${SDT_OUTDIR}/${BOARD_DTSI}" ]; then
            log_ok "Board DTSI applied: ${BOARD_DTSI}"
        else
            log_warn "Board DTSI not in output: ${BOARD_DTSI} (may be included inline)"
        fi
    fi

    echo ""
    case ${rc} in
        0)
            log_ok "Validation PASSED — generated SDT checks passed"
            ;;
        2)
            log_warn "Validation PASSED WITH WARNINGS — review the items above"
            ;;
        *)
            log_error "Validation FAILED (validate_sdt.sh exit ${rc})"
            exit 1
            ;;
    esac
    log_info "Next step: gen-machine-conf parse-sdt --hw-description ${SDT_OUTDIR}"
}

# =============================================================================
# Error Diagnosis
# =============================================================================
diagnose_failure() {
    local log_file="$1"
    log_info "=== Diagnosing Failure ==="

    if [ ! -f "${log_file}" ]; then
        log_warn "No log file to diagnose"
        return
    fi

    # Pattern matching for known errors
    if grep -qE "Hsi 55-(1545|1451)|HDF 64-4" "${log_file}"; then
        log_error "Diagnosis: XSA loading failed — inspect the full message for corruption or incompatibility"
        log_info "  Check the archive and tool compatibility before re-exporting"
    elif grep -qE "(board_dts|user_dts|board file).*not found|could neither be located" "${log_file}"; then
        log_error "Diagnosis: Specified board/user DTS was not found in the SDT repo"
        log_info "  Fix: Check the --board-dts name (run --list-boards) or the --custom-dts path"
    elif grep -qi "permission denied" "${log_file}"; then
        log_error "Diagnosis: Cannot write to output directory"
        log_info "  Fix: Check permissions on ${SDT_OUTDIR} or use --outdir <writable_path>"
    elif grep -q 'Failed in finding .*device_tree[.]tcl path' "${log_file}"; then
        log_error "Diagnosis: SDT repository entry point could not be found"
        log_info "  Fix: Check CUSTOM_SDT_REPO for device_tree/data/device_tree.tcl or unset it to use the installed repo"
    elif grep -qF "Couldn't determine the hardware family" "${log_file}"; then
        log_error "Diagnosis: SDTGen could not determine the hardware family"
        log_info "  Check the XSA for a supported processor and verify tool compatibility"
    elif grep -q "wrong # args" "${log_file}"; then
        log_error "Diagnosis: Tcl argument mismatch — inspect the command and tool compatibility"
        log_info "  Check argument syntax and the tool log before changing versions"
    else
        log_warn "Unknown failure — check full log: ${log_file}"
        if ! grep -m5 '^ERROR:' "${log_file}"; then
            log_info "Last 10 lines of log:"
            tail -10 "${log_file}"
        fi
    fi
}

# =============================================================================
# Main
# =============================================================================
main() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  SDTGen Agent — System Device Tree Generator"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    parse_args "$@"

    # List available board DTS and exit
    if [ ${LIST_BOARDS} -eq 1 ]; then
        list_boards
        exit $?
    fi

    # Environment check only
    if [ ${ENV_CHECK_ONLY} -eq 1 ]; then
        check_environment
        exit $?
    fi

    # Validate-only mode needs dtc/cpp, not sdtgen, so it must not be gated
    # behind the sdtgen lookup — re-validating an existing SDT on a machine
    # without a Vitis install is a legitimate use.
    if [ ${VALIDATE_ONLY} -eq 1 ]; then
        validate_output
        exit $?
    fi

    # Validate environment
    check_environment || exit 1

    # Normal flow requires XSA
    if [ -z "${XSA_PATH}" ]; then
        log_error "XSA path required. Usage: $0 <xsa_path> [options]"
        show_help
        exit 1
    fi


    # Full pipeline
    generate_sdt
    validate_output

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_ok "SDTGen Agent completed successfully"
    log_info "Output: ${SDT_OUTDIR}"
    log_info "Next:   gen-machine-conf parse-sdt --hw-description ${SDT_OUTDIR} ..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

main "$@"
