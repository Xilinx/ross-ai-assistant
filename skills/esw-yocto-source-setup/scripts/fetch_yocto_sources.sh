#!/usr/bin/env bash
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT

# Fetch a Yocto source tree with the AMD/Xilinx `repo` manifest.
#
# Defaults (override via environment or flags):
#   URL      https://github.com/Xilinx/yocto-manifests
#   BRANCH   manifest repository's current default branch
#   MANIFEST default-edf.xml
#
# When the `repo` tool is not on PATH the script reports an error and exits;
# install `repo` yourself beforehand. Requires Python.
#
# Usage:
#   scripts/fetch_yocto_sources.sh [dest-dir]
#   YOCTO_MANIFEST_BRANCH=rel-v2025.2 scripts/fetch_yocto_sources.sh ./sources
#   scripts/fetch_yocto_sources.sh -u <url> -b <branch> -m <manifest> ./sources

set -euo pipefail

MANIFEST_URL="${YOCTO_MANIFEST_URL:-https://github.com/Xilinx/yocto-manifests}"
MANIFEST_BRANCH="${YOCTO_MANIFEST_BRANCH:-HEAD}"
MANIFEST_FILE="${YOCTO_MANIFEST_FILE:-default-edf.xml}"
DEST_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -u|--url)      MANIFEST_URL="$2"; shift 2 ;;
    -b|--branch)   MANIFEST_BRANCH="$2"; shift 2 ;;
    -m|--manifest) MANIFEST_FILE="$2"; shift 2 ;;
    -h|--help)
      cat <<'HELP'
Usage: fetch_yocto_sources.sh [options] [dest-dir]
Fetch an EDF checkout using the installed repo tool.

Options:
  -u, --url URL          Manifest repository (default: https://github.com/Xilinx/yocto-manifests)
  -b, --branch BRANCH    Manifest branch (default: HEAD, the repository default branch)
  -m, --manifest FILE   Manifest file (default: default-edf.xml)
  -h, --help            Show this help

Destination defaults to yocto-sources. YOCTO_MANIFEST_URL,
YOCTO_MANIFEST_BRANCH and YOCTO_MANIFEST_FILE override the defaults.
HELP
      exit 0 ;;
    -*)
      echo "ERROR: unknown option: $1" >&2; exit 2 ;;
    *)
      DEST_DIR="$1"; shift ;;
  esac
done

DEST_DIR="${DEST_DIR:-yocto-sources}"

# Resolve the `repo` tool. It must already be installed and on PATH — this
# script does not download external tooling.
if command -v repo >/dev/null; then
  REPO_BIN="$(command -v repo)"
else
  echo "ERROR: 'repo' not found on PATH." >&2
  echo "  Install the Android 'repo' tool before running this script, e.g.:" >&2
  echo "    - Debian/Ubuntu: apt-get install repo" >&2
  echo "    - Fedora:        dnf install repo" >&2
  echo "    - Or consult the git-repo project's README for manual install steps." >&2
  exit 1
fi

command -v python3 >/dev/null || command -v python >/dev/null || {
  echo "ERROR: 'repo' requires Python (python3 or python) on PATH" >&2
  exit 1
}

echo "Fetching Yocto sources:"
echo "  url      = $MANIFEST_URL"
echo "  branch   = $MANIFEST_BRANCH"
echo "  manifest = $MANIFEST_FILE"
echo "  dest     = $DEST_DIR"

SETUP_HELPER="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/setup_yocto_env.sh"

mkdir -p "$DEST_DIR"
cd "$DEST_DIR"

if ! "$REPO_BIN" init -u "$MANIFEST_URL" -b "$MANIFEST_BRANCH" -m "$MANIFEST_FILE"; then
  echo "ERROR: repo init failed; check the error above or pass -b <branch> explicitly." >&2
  exit 1
fi
if [[ "$MANIFEST_BRANCH" == HEAD ]]; then
  RESOLVED_BRANCH="$(git -C .repo/manifests config --get branch.default.merge)"
  echo "  resolved branch = ${RESOLVED_BRANCH#refs/heads/}"
fi
"$REPO_BIN" sync -j"$(nproc)"

echo "Sources ready in: $(pwd)"
printf "Next (in the shell that will run Yocto commands): source %q %q %q\n" \
  "$SETUP_HELPER" "$(pwd -P)/edf-init-build-env" "$(pwd -P)/build"
