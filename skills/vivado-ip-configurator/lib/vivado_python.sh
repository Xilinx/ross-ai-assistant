#!/bin/sh
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Launch Python without requiring a host installation. Keep this policy aligned
# with ipcfg::python_runtime in ipcfg.tcl.
set -eu

run_candidate() {
    executable=$1
    root=$2
    shift 2
    unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT
    if [ -n "$root" ]; then
        LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        export LD_LIBRARY_PATH
    fi
    exec "$executable" "$@"
}

probe_candidate() {
    executable=$1
    root=$2
    unset PYTHONSTARTUP PYTHONINSPECT
    if [ -n "$root" ]; then
        LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            PYTHONHOME= PYTHONPATH= "$executable" -c \
            'import argparse, base64, hashlib, json, pathlib, re, sys, typing; raise SystemExit(sys.version_info < (3, 10))' \
            >/dev/null 2>&1
    else
        PYTHONHOME= PYTHONPATH= "$executable" -c \
            'import argparse, base64, hashlib, json, pathlib, re, sys, typing; raise SystemExit(sys.version_info < (3, 10))' \
            >/dev/null 2>&1
    fi
}

if [ "${IPCFG_PYTHON:-}" != "" ]; then
    case "$IPCFG_PYTHON" in
        */bin/python|*/bin/python3|*/bin/python3.*)
            candidate_root=${IPCFG_PYTHON%/bin/*}
            [ -d "$candidate_root/lib" ] || candidate_root=
            ;;
        *) candidate_root= ;;
    esac
    if ! probe_candidate "$IPCFG_PYTHON" "$candidate_root"; then
        echo "PYTHON_RUNTIME_FAIL: IPCFG_PYTHON is not compatible: $IPCFG_PYTHON" >&2
        exit 2
    fi
    run_candidate "$IPCFG_PYTHON" "$candidate_root" "$@"
fi

vivado_root=${IPCFG_VIVADO_ROOT:-${XILINX_VIVADO:-}}
if [ "$vivado_root" = "" ] && [ "${VIVADO_PATH:-}" != "" ]; then
    case "$VIVADO_PATH" in
        */bin/vivado) vivado_root=${VIVADO_PATH%/bin/vivado} ;;
        *) vivado_root=$VIVADO_PATH ;;
    esac
fi
if [ "$vivado_root" = "" ]; then
    vivado_command=$(command -v vivado 2>/dev/null || :)
    case "$vivado_command" in
        */bin/vivado) vivado_root=${vivado_command%/bin/vivado} ;;
    esac
fi

compatible=
compatible_root=
count=0
if [ "$vivado_root" != "" ]; then
    for executable in "$vivado_root"/tps/lnx64/python-*/bin/python3; do
        [ -x "$executable" ] || continue
        root=${executable%/bin/python3}
        if probe_candidate "$executable" "$root"; then
            compatible=$executable
            compatible_root=$root
            count=$((count + 1))
        fi
    done
fi

if [ "$count" -eq 1 ]; then
    run_candidate "$compatible" "$compatible_root" "$@"
fi
if [ "$count" -gt 1 ]; then
    echo "PYTHON_RUNTIME_FAIL: multiple compatible Vivado Python bundles; set IPCFG_PYTHON" >&2
    exit 2
fi

if [ "${IPCFG_REQUIRE_BUNDLED:-0}" = "1" ]; then
    echo "PYTHON_RUNTIME_FAIL: no compatible Vivado Python bundle; set IPCFG_VIVADO_ROOT to the installed Vivado root" >&2
    exit 2
fi

if command -v python3 >/dev/null 2>&1; then
    host_python=$(command -v python3)
    if probe_candidate "$host_python" ""; then
        run_candidate "$host_python" "" "$@"
    fi
fi
if command -v python >/dev/null 2>&1; then
    host_python=$(command -v python)
    if probe_candidate "$host_python" ""; then
        run_candidate "$host_python" "" "$@"
    fi
fi

echo "PYTHON_RUNTIME_FAIL: no Python 3.10+; vivado_root=${vivado_root:-<unset>}" >&2
exit 2
