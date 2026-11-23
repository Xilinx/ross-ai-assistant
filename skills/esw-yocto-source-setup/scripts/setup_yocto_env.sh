#!/usr/bin/env bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT

# Source this file so the initialized environment persists in the calling shell.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "ERROR: source this script; do not execute it" >&2
  echo "Usage: source ${BASH_SOURCE[0]} <path-to-edf-init-build-env> [build-dir]" >&2
  exit 2
fi

# Function scope keeps helper variables out of the caller on every return path.
__esw_setup_yocto_env() {
  unset -f __esw_setup_yocto_env
  if [[ -z "${1:-}" ]]; then
    echo "Usage: source ${BASH_SOURCE[0]} <path-to-edf-init-build-env> [build-dir]" >&2
    return 2
  fi
  local _esw_initializer=$1
  local _esw_build_dir
  if [[ ! -f "$_esw_initializer" ]]; then
    echo "ERROR: init-build-env not found: $_esw_initializer (pass its full path)" >&2
    return 2
  fi
  _esw_initializer=$(readlink -f "$_esw_initializer") || return
  if [[ $# -ge 2 ]]; then
    # Resolve a supplied relative path before the initializer changes directory.
    _esw_build_dir=$(readlink -m "$2") || return
  else
    _esw_build_dir="${_esw_initializer%/*}/build"
  fi
  echo "Sourcing $_esw_initializer"
  # shellcheck disable=SC1090
  source "$_esw_initializer" "$_esw_build_dir" || return

  [[ -n "${BUILDDIR:-}" ]] || { echo "ERROR: BUILDDIR was not set" >&2; return 1; }
  command -v bitbake >/dev/null || { echo "ERROR: bitbake is not on PATH" >&2; return 1; }

  echo "BUILDDIR=$BUILDDIR"
  echo "bitbake=$(command -v bitbake)"
  if command -v gen-machine-conf >/dev/null; then
    echo "gen-machine-conf=$(command -v gen-machine-conf)"
  else
    echo "WARNING: gen-machine-conf is not on PATH; verify the selected EDF initializer completed successfully and the checkout is complete" >&2
    return 3
  fi
}
__esw_setup_yocto_env "$@"
