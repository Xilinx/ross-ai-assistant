#!/usr/bin/env bash
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Checks the requirements of this example on the Vivado machine, using the same environment
# variables as run_trial.sh (VIVADO_PATH, VIVADO_MCP_SERVER, LEMONADE_URL, MODEL_ID, OPENCODE).
# Prints OK or MISSING per requirement and exits 1 if anything required is missing. Reads only;
# starts no Vivado and sends no prompt (one GET of the model list).
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MODEL_ID=${MODEL_ID:-Gemma-4-26B-A4B-it-GGUF}
OPENCODE=${OPENCODE:-$(command -v opencode || true)}
missing=0
ok()   { printf 'OK       %-22s %s\n' "$1" "$2"; }
miss() { printf 'MISSING  %-22s %s\n' "$1" "$2"; missing=1; }

[ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] && ok "Linux x86-64" "$(uname -r)" || miss "Linux x86-64" "found $(uname -s)/$(uname -m)"
for tool in bash curl awk sed find timeout; do
    command -v "$tool" >/dev/null && ok "$tool" "$(command -v "$tool")" || miss "$tool" "not on PATH"
done
if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
    ok "python3 >= 3.8" "$(python3 --version 2>&1)"
else
    miss "python3 >= 3.8" "needed only for check/summarize.py"
fi
if [ -x "${VIVADO_PATH:-}" ]; then
    ok "Vivado" "$("$VIVADO_PATH" -version 2>/dev/null | head -1)"
else
    miss "Vivado" "set VIVADO_PATH to the vivado executable"
fi
if [ -x "${VIVADO_MCP_SERVER:-}" ]; then
    ok "Vivado MCP server" "$("$VIVADO_MCP_SERVER" --version 2>/dev/null | head -1)"
else
    miss "Vivado MCP server" "set VIVADO_MCP_SERVER to the vivado-mcp-server binary"
fi
if [ -x "$OPENCODE" ]; then
    ok "OpenCode" "$("$OPENCODE" --version 2>/dev/null) (tested with 1.18.35)"
else
    miss "OpenCode" "install it, or set OPENCODE to its path"
fi
if curl -sf -m 10 https://registry.npmjs.org/@opencode-ai%2fplugin >/dev/null; then
    ok "npm registry" "reachable (OpenCode downloads packages on each trial)"
else
    miss "npm registry" "https://registry.npmjs.org not reachable; OpenCode needs it"
fi
if [ -z "${LEMONADE_URL:-}" ]; then
    miss "model server" "set LEMONADE_URL, e.g. http://127.0.0.1:13305/api/v1"
elif models=$(curl -sf -m 10 "$LEMONADE_URL/models"); then
    if grep -q "\"$MODEL_ID\"" <<<"$models"; then
        ok "model" "$MODEL_ID at $LEMONADE_URL"
    else
        miss "model" "$LEMONADE_URL does not list $MODEL_ID"
    fi
else
    miss "model server" "no answer from $LEMONADE_URL/models"
fi
[ -f "$here/../../../skills/vivado-ip-configurator/SKILL.md" ] && ok "skill" "skills/vivado-ip-configurator" \
    || miss "skill" "run from a checkout of this repository"
exit $missing
