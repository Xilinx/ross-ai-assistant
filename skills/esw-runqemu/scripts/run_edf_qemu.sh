#!/usr/bin/env bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: run_edf_qemu.sh --qemuboot <file> --rootfs <image.wic> [options]

Options:
  --dry-run       validate and print the command without launching QEMU
  --debug         pass --debug to runqemu
  --writable      persist guest writes (default: snapshot)
  --tap           use configured tap networking (default: slirp)
  --no-network    disable networking
  --extra <arg>   append one runqemu argument; repeat as needed
  -h, --help      show this help
EOF
  exit 2
}

qemuboot=""; rootfs=""; dry_run=false; debug=false; snapshot=true
network=slirp; extra=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --qemuboot) qemuboot=${2:?}; shift 2 ;;
    --rootfs) rootfs=${2:?}; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --debug) debug=true; shift ;;
    --writable) snapshot=false; shift ;;
    --tap) network=tap; shift ;;
    --no-network) network=none; shift ;;
    --extra) extra+=("${2:?}"); shift 2 ;;
    -h|--help) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

command -v runqemu >/dev/null || { echo "ERROR: runqemu is not on PATH; source the EDF initializer or EDF SDK environment" >&2; exit 3; }
[[ -f "$qemuboot" ]] || { echo "ERROR: qemuboot file not found: $qemuboot" >&2; exit 4; }
[[ -f "$rootfs" ]] || { echo "ERROR: rootfs not found: $rootfs" >&2; exit 4; }

qemuboot=$(readlink -f "$qemuboot")
# Preserve the caller-visible basename: runqemu uses suffixes such as
# .wic.qemu-sd to select the emulated storage transport. readlink -f would
# erase that suffix when the transport-specific name is a symlink.
rootfs_dir=$(cd "$(dirname "$rootfs")" && pwd -P)
rootfs="$rootfs_dir/$(basename "$rootfs")"
case "$rootfs" in
  *.wic.xz|*.xz) echo "ERROR: decompress the rootfs before runqemu: $rootfs" >&2; exit 4 ;;
  *.wic|*.wic.qemu-sd) ;;
  *) echo "WARNING: rootfs is not a .wic or .wic.qemu-sd image: $rootfs" >&2 ;;
esac

conf_dir=$(dirname "$qemuboot")
conf_value() {
  sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)$/\\1/p" "$qemuboot" | tail -1
}
machine=$(conf_value machine)
system_name=$(conf_value qb_system_name)
kernel=$(conf_value qb_default_kernel)
dtb=$(conf_value qb_dtb)
default_fstype=$(conf_value qb_default_fstype)

[[ -n "$machine" ]] || { echo "ERROR: qemuboot file has no machine entry" >&2; exit 4; }
[[ -n "$system_name" ]] || { echo "ERROR: qemuboot file has no qb_system_name" >&2; exit 4; }
for item in "$kernel" "$dtb"; do
  [[ -z "$item" || -e "$conf_dir/$item" ]] || {
    echo "ERROR: qemuboot dependency not found: $conf_dir/$item" >&2
    exit 4
  }
done

while IFS= read -r item; do
  item=${item#@DEPLOY_DIR_IMAGE@/}
  [[ -e "$conf_dir/$item" ]] || {
    echo "ERROR: qemuboot deploy dependency not found: $conf_dir/$item" >&2
    exit 4
  }
done < <(grep -oE '@DEPLOY_DIR_IMAGE@/[A-Za-z0-9_./+-]+' "$qemuboot" | sort -u)

if [[ "$default_fstype" == "wic.qemu-sd" ]]; then
  # SDK shells need no BUILDDIR. Discover build-shell mtools from the
  # evaluated component directory rather than assuming TMPDIR.
  if ! command -v mdir >/dev/null; then
    components_dir=""
    if [[ -n "${BUILDDIR:-}" ]] && command -v bitbake-getvar >/dev/null; then
      components_dir=$(bitbake-getvar --value COMPONENTS_DIR 2>/dev/null || true)
    fi
    mdir_path=""
    if [[ -n "$components_dir" && -d "$components_dir" ]]; then
      mdir_path=$(find "$components_dir" -path '*/mtools-native/usr/bin/mdir' -print -quit)
    fi
    [[ -n "$mdir_path" ]] || {
      echo "ERROR: mdir is unavailable; add SDK mtools to PATH or prepare mtools-native in a Yocto build shell" >&2
      exit 3
    }
    PATH="$(dirname "$mdir_path"):$PATH"
    export PATH
  fi
  command -v wic >/dev/null || {
    echo "ERROR: wic is unavailable; source the EDF SDK or Yocto environment providing wic" >&2
    exit 3
  }
  [[ "$rootfs" == *.wic.qemu-sd ]] || {
    echo "ERROR: qemuboot requires wic.qemu-sd; pass the raw WIC through a *.wic.qemu-sd name" >&2
    exit 4
  }
  # -L: the caller may pass a symlink whose name carries the .wic.qemu-sd
  # suffix; measure the target, not the link itself.
  size=$(stat -L -c %s "$rootfs")
  valid_size=false
  if (( size > 0 && size <= 2147483648 )); then
    (( (size & (size - 1)) == 0 )) && valid_size=true
    next_size=1
    while (( next_size < size )); do next_size=$(( next_size * 2 )); done
  else
    (( size > 2147483648 && size % 524288 == 0 )) && valid_size=true
    next_size=$(( (size + 524287) / 524288 * 524288 ))
    (( next_size > 0 )) || next_size=1
  fi
  [[ "$valid_size" == true ]] || {
    echo "ERROR: SD image size $size must be a positive power of two up to 2 GiB, or a multiple of 512 KiB above 2 GiB" >&2
    echo "       Preserve the source and extend a QEMU-only copy to $next_size bytes" >&2
    exit 4
  }
fi

args=("$qemuboot" "$rootfs" nographic)
case "$network" in
  slirp) args+=(slirp) ;;
  none) args+=(nonetwork) ;;
  tap) ;;
esac
[[ "$snapshot" == true ]] && args+=(snapshot)
[[ "$debug" == true ]] && args+=(--debug)
args+=("${extra[@]}")

echo "BUILDDIR=${BUILDDIR:-<unset (SDK environment)>}"
echo "machine=$machine"
echo "qemu=$system_name"
echo "fstype=$default_fstype"
echo "mdir=$(command -v mdir || true)"
echo "qemuboot=$qemuboot"
echo "rootfs=$rootfs"
echo "network=$network"
echo "disk_mode=$([[ "$snapshot" == true ]] && echo snapshot || echo writable)"
printf 'command='
printf '%q ' runqemu "${args[@]}"
printf '\n'

[[ "$dry_run" == true ]] && { echo "dry-run: OK"; exit 0; }

exec runqemu "${args[@]}"
