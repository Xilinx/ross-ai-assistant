#!/usr/bin/env bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
# Guarded BitBake wrapper for AMD EDF Yocto builds.
# Validates the shell, host compilers, the MACHINE, and free disk before invoking bitbake.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_edf_build.sh [options] <target> [<target>...]

Options:
  -m, --machine <name>   MACHINE for this build (default: local.conf MACHINE)
  -D, --distro <name>    DISTRO override (e.g. amd-edf-small)
  -k, --continue         pass -k to bitbake (keep going after a failed task)
  -n, --dry-run          pass -n to bitbake (parse + plan only, no tasks run)
  -j, --detach           compatibility alias for the default tmux launch
      --tmux-session N   session name (default: edfbuild-<machine>-<timestamp>)
      --log <path>       log file (default: $BUILDDIR/edf-build-<machine>-<ts>.log)
      --min-free <GB>    required free space under BUILDDIR (default: 50)
      --env-check        validate the environment and exit
      --list-targets     print EDF targets/machines from conf-notes.txt and exit
  -h, --help             this message

A real build launches in detached tmux when available, otherwise it falls back
to setsid/nohup. Dry runs remain in the foreground.
EOF
  exit 2
}

machine=""; distro=""; extra=(); logfile=""; detach=false; tmux_session=""
min_free=50; env_check=false; list_targets=false; dry_run=false; targets=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--machine)   machine=${2:?}; shift 2 ;;
    -D|--distro)    distro=${2:?}; shift 2 ;;
    -k|--continue)  extra+=(-k); shift ;;
    -n|--dry-run)   extra+=(-n); dry_run=true; shift ;;
    -j|--detach)    detach=true; shift ;;
    --tmux-session) tmux_session=${2:?}; shift 2 ;;
    --log)          logfile=${2:?}; shift 2 ;;
    --min-free)     min_free=${2:?}; shift 2 ;;
    --env-check)    env_check=true; shift ;;
    --list-targets) list_targets=true; shift ;;
    -h|--help)      usage ;;
    --)             shift; while [[ $# -gt 0 ]]; do targets+=("$1"); shift; done ;;
    -*)             echo "ERROR: unknown option: $1" >&2; usage ;;
    *)              targets+=("$1"); shift ;;
  esac
done

# --- Environment gates -------------------------------------------------------
[[ -n "${BUILDDIR:-}" ]] || {
  echo "ERROR: BUILDDIR is unset - the Yocto environment is not active in this shell." >&2
  echo "       Source the initializer first (see \$esw-yocto-source-setup)." >&2
  exit 3
}
[[ -d "$BUILDDIR" ]] || { echo "ERROR: BUILDDIR does not exist: $BUILDDIR" >&2; exit 3; }

# Vitis/Vivado settings inject a host PATH, LD_LIBRARY_PATH and Python that
# corrupt an OE build. This is a hard failure, not a warning.
for var in XILINX_VITIS XILINX_VIVADO; do
  if [[ -n "${!var:-}" ]]; then
    echo "ERROR: $var is set (${!var})." >&2
    echo "       Vitis/Vivado settings conflict with the Yocto build environment." >&2
    echo "       Re-run in a clean shell that only sources the EDF initializer." >&2
    exit 3
  fi
done

command -v bitbake >/dev/null || {
  echo "ERROR: bitbake is not on PATH; the environment is not initialized." >&2
  exit 3
}

# A host compiler can be present and compile simple integer code while crashing in
# optimizer paths used by native recipes.  DBL_MAX constant folding is a fast probe
# for the incompatible Pandora GCC seen on some build hosts.  Check PATH before OE
# creates hosttools, and check every existing main/multiconfig hosttools tree because
# those symlinks persist independently across later PATH changes.
compiler_probe() {
  local compiler=$1 label=$2 probe_src probe_obj resolved version
  probe_src=$(mktemp "${TMPDIR:-/tmp}/edf-gcc-probe.XXXXXX.c")
  probe_obj=${probe_src%.c}.o
  printf '#include <float.h>\ndouble f(void){return DBL_MAX;}\n' > "$probe_src"
  resolved=$(readlink -f "$compiler" 2>/dev/null || printf '%s' "$compiler")
  version=$($compiler --version 2>/dev/null | head -1 || true)
  echo "$label gcc              = $resolved${version:+ ($version)}"
  if ! "$compiler" -O2 -c "$probe_src" -o "$probe_obj" >/dev/null 2>&1; then
    rm -f "$probe_src" "$probe_obj"
    echo "ERROR: $label compiler failed the DBL_MAX optimizer probe: $resolved" >&2
    echo "       Native recipes may fail with 'internal compiler error: Illegal instruction'." >&2
    echo "       Fix PATH or repoint only tmp*/hosttools/{gcc,g++,cpp,gcc-ar}, then retry." >&2
    return 1
  fi
  rm -f "$probe_src" "$probe_obj"
}

path_gcc=$(command -v gcc || true)
[[ -n "$path_gcc" ]] || { echo "ERROR: gcc is not on PATH." >&2; exit 3; }
compiler_probe "$path_gcc" "PATH"

shopt -s nullglob
hosttool_dirs=("$BUILDDIR"/tmp/hosttools "$BUILDDIR"/tmp-*/hosttools)
shopt -u nullglob
for hosttools in "${hosttool_dirs[@]}"; do
  [[ -d "$hosttools" ]] || continue
  for tool in gcc g++ cpp gcc-ar; do
    [[ -x "$hosttools/$tool" ]] || {
      echo "ERROR: missing or non-executable host tool: $hosttools/$tool" >&2
      exit 3
    }
  done
  compiler_probe "$hosttools/gcc" "$hosttools"
done

notes="$BUILDDIR/conf/conf-notes.txt"

if [[ "$list_targets" == true ]]; then
  [[ -r "$notes" ]] || { echo "ERROR: not found: $notes" >&2; exit 4; }
  cat "$notes"
  exit 0
fi

# --- Machine resolution ------------------------------------------------------
if [[ -z "$machine" ]]; then
  machine=$(sed -nE 's/^[[:space:]]*MACHINE[[:space:]]*[?:]*=[[:space:]]*"([^"]+)".*/\1/p' \
            "$BUILDDIR/conf/local.conf" | tail -1 || true)
  [[ -n "$machine" ]] || { echo "ERROR: no MACHINE given and none set in local.conf" >&2; exit 4; }
  echo "MACHINE (from local.conf) = $machine"
else
  echo "MACHINE (override)        = $machine"
fi

# A generated machine lives in $BUILDDIR/conf/machine; a BSP machine lives in a layer.
search_root=${EDF_LAYER_ROOT:-$(dirname "$BUILDDIR")}
machine_conf=""
if [[ -f "$BUILDDIR/conf/machine/$machine.conf" ]]; then
  machine_conf="$BUILDDIR/conf/machine/$machine.conf"
else
  machine_conf=$(find "$search_root" -maxdepth 6 -type f -path "*/conf/machine/$machine.conf" \
                 -print -quit 2>/dev/null || true)
fi
if [[ -z "$machine_conf" ]]; then
  echo "ERROR: no conf/machine/$machine.conf found under $BUILDDIR/conf or $search_root" >&2
  echo "       Generate it with \$esw-gen-machine-conf, or pick a machine from --list-targets." >&2
  exit 4
fi
echo "machine conf              = $machine_conf"

# --- Disk guard --------------------------------------------------------------
free_gb=$(df -BG --output=avail "$BUILDDIR" | tail -1 | tr -dc '0-9')
echo "free space under BUILDDIR = ${free_gb}G (required ${min_free}G)"
if (( free_gb < min_free )); then
  echo "ERROR: insufficient free space. A full EDF image build needs tens of GB." >&2
  echo "       Free space or lower the bar with --min-free <GB>." >&2
  exit 5
fi

if [[ "$env_check" == true ]]; then
  echo "env-check: OK"
  exit 0
fi

[[ ${#targets[@]} -gt 0 ]] || { echo "ERROR: no build target given" >&2; usage; }

# --- Invoke ------------------------------------------------------------------
ts=$(date +%Y%m%d-%H%M%S)
logfile=${logfile:-$BUILDDIR/edf-build-$machine-$ts.log}
deploy="$BUILDDIR/tmp/deploy/images/$machine"

env_prefix=(env "MACHINE=$machine")
[[ -n "$distro" ]] && env_prefix+=("DISTRO=$distro")

echo "targets                   = ${targets[*]}"
echo "log                       = $logfile"
echo "deploy dir (on success)   = $deploy"
echo "command                   = ${env_prefix[*]} bitbake ${extra[*]:-} ${targets[*]}"

# Real builds always run in tmux so the user can attach to the live BitBake UI.
# Keep --detach as a compatibility alias; both paths now use tmux.
if [[ "$dry_run" != true ]]; then
  if command -v tmux >/dev/null; then
    [[ -n "$tmux_session" ]] || tmux_session="edfbuild-${machine}-${ts}"
    tmux_session=${tmux_session//[^a-zA-Z0-9_.-]/-}
    if tmux has-session -t "$tmux_session" 2>/dev/null; then
      echo "ERROR: tmux session already exists: $tmux_session" >&2
      exit 6
    fi

    printf -v build_cmd '%q ' "${env_prefix[@]}" bitbake "${extra[@]}" "${targets[@]}"
    printf -v quoted_builddir '%q' "$BUILDDIR"
    printf -v quoted_logfile '%q' "$logfile"
    shell_cmd="cd $quoted_builddir && ${build_cmd}2>&1 | tee $quoted_logfile; rc=\${PIPESTATUS[0]}; echo; echo \"BitBake exit code: \$rc\"; exec bash"
    tmux new-session -d -s "$tmux_session" -x 200 -y 50 bash -lc "$shell_cmd"
    echo "launch mode               = tmux"
    echo "tmux session              = $tmux_session"
    echo "attach                    = tmux attach -t $tmux_session"
    echo "detach                    = Ctrl-b d"
    echo "capture                   = tmux capture-pane -p -S -2000 -t $tmux_session"
    echo "poll log                  = tail -n 30 $logfile"
  else
    echo "WARNING: tmux is unavailable; falling back to detached setsid/nohup." >&2
    setsid nohup "${env_prefix[@]}" bitbake "${extra[@]}" "${targets[@]}" \
      > "$logfile" 2>&1 < /dev/null &
    pid=$!
    disown || true
    echo "launch mode               = setsid/nohup fallback"
    echo "detached pid              = $pid"
    echo "poll log                  = tail -n 30 $logfile"
  fi
  exit 0
fi

"${env_prefix[@]}" bitbake "${extra[@]}" "${targets[@]}" 2>&1 | tee "$logfile"
rc=${PIPESTATUS[0]}
if [[ $rc -eq 0 ]]; then
  if [[ "$dry_run" == true ]]; then
    echo "DRY-RUN OK - plan resolved, no tasks executed and no artifacts written"
  else
    echo "BUILD OK - artifacts in $deploy"
  fi
else
  echo "BUILD FAILED (rc=$rc) - see $logfile" >&2
fi
exit "$rc"
