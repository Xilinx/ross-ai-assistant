#!/usr/bin/env bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <sdt-dir> [global options...] -- [parse-sdt options...]" >&2
  echo "       $0 <sdt-dir> [parse-sdt options...]  # legacy form" >&2
  exit 2
fi

sdt_dir=$1
shift
sdt_dir=$(readlink -f "$sdt_dir")

[[ -d "$sdt_dir" ]] || { echo "ERROR: SDT directory not found: $sdt_dir" >&2; exit 2; }
require_sdt_file() {
  [[ -s "$sdt_dir/$1" ]] || {
    echo "ERROR: missing or empty $sdt_dir/$1" >&2
    exit 2
  }
}

require_sdt_file system-top.dts
# PL-only SDTs do not generate pcw.dtsi. Check files referenced by the wrapper.
for required in pcw.dtsi pl.dtsi; do
  if grep -Eq "^[[:space:]]*#include[[:space:]]*[\"<]${required//./\\.}[\">]" "$sdt_dir/system-top.dts"; then
    require_sdt_file "$required"
  elif [[ "$required" == pcw.dtsi ]]; then
    echo "INFO: pcw.dtsi not referenced; not required"
  fi
done
[[ -n "${BUILDDIR:-}" ]] || {
  echo "ERROR: Yocto environment is not active (BUILDDIR is unset)" >&2
  exit 3
}
command -v gen-machine-conf >/dev/null || {
  echo "ERROR: gen-machine-conf is not on PATH; run esw-yocto-source-setup first" >&2
  exit 3
}

tool=$(command -v gen-machine-conf)

# Options before `--` belong to gen-machine-conf; options after it belong to
# parse-sdt. Preserve the old interface by treating all arguments as parse-sdt
# options when no separator is present.
global_args=()
parse_args=()
separator=false
for arg in "$@"; do
  if [[ "$arg" == -- && "$separator" == false ]]; then
    separator=true
    continue
  fi
  if [[ "$separator" == true ]]; then
    parse_args+=("$arg")
  else
    global_args+=("$arg")
  fi
done
if [[ "$separator" == false ]]; then
  parse_args=("${global_args[@]}")
  global_args=()
fi

# The agent decides which interpreter to use (see SKILL.md) and passes it via
# GEN_MACHINE_CONF_PYTHON. When unset, gen-machine-conf runs with its own shebang.
python="${GEN_MACHINE_CONF_PYTHON:-}"

echo "gen-machine-conf=$tool"
echo "BUILDDIR=$BUILDDIR"
if [[ -n "$python" ]]; then
  echo "python=$python"
  printf 'Running: %q %q --hw-description %q' "$python" "$tool" "$sdt_dir"
  if (( ${#global_args[@]} )); then
    printf ' %q' "${global_args[@]}"
  fi
  printf ' parse-sdt'
  if (( ${#parse_args[@]} )); then
    printf ' %q' "${parse_args[@]}"
  fi
  printf '\n'
  exec "$python" "$tool" --hw-description "$sdt_dir" "${global_args[@]}" parse-sdt "${parse_args[@]}"
fi
printf 'Running: gen-machine-conf --hw-description %q' "$sdt_dir"
if (( ${#global_args[@]} )); then
  printf ' %q' "${global_args[@]}"
fi
printf ' parse-sdt'
if (( ${#parse_args[@]} )); then
  printf ' %q' "${parse_args[@]}"
fi
printf '\n'
exec "$tool" --hw-description "$sdt_dir" "${global_args[@]}" parse-sdt "${parse_args[@]}"
