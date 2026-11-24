#!/usr/bin/env bash
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Runs one trial of the PL clock example with OpenCode and a local model, then checks the saved
# design in a separate batch Vivado.
#
#   ./run_trial.sh with-skill    <new_trial_dir>
#   ./run_trial.sh without-skill <new_trial_dir>
#
# Settings (environment variables):
#   VIVADO_PATH        Vivado executable                         (required)
#   VIVADO_MCP_SERVER  vivado-mcp-server binary                  (required)
#   LEMONADE_URL       OpenAI-compatible base URL of the model server, e.g.
#                      http://127.0.0.1:13305/api/v1             (required)
#   MODEL_ID           model id on that server                   (default Gemma-4-26B-A4B-it-GGUF)
#   OPENCODE           opencode executable                       (default: opencode on PATH)
#   TRIAL_TIMEOUT      seconds before the agent is stopped       (default 1800)
#
# Writes into <new_trial_dir>: opencode.json, proj/ (the design), transcript.jsonl (the agent's
# event stream), check.txt (the independent check) and summary.txt.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mode=${1:-}
trial=${2:-}
case "$mode" in with-skill|without-skill) ;; *)
    echo "usage: $0 with-skill|without-skill <new_trial_dir>" >&2; exit 2 ;;
esac
[ -n "$trial" ] || { echo "give a trial directory" >&2; exit 2; }
[ -e "$trial" ] && { echo "$trial exists; use a new directory for every trial" >&2; exit 2; }
: "${VIVADO_PATH:?set VIVADO_PATH to the Vivado executable}"
: "${VIVADO_MCP_SERVER:?set VIVADO_MCP_SERVER to the vivado-mcp-server binary}"
: "${LEMONADE_URL:?set LEMONADE_URL to the model server base URL, e.g. http://127.0.0.1:13305/api/v1}"
MODEL_ID=${MODEL_ID:-Gemma-4-26B-A4B-it-GGUF}
OPENCODE=${OPENCODE:-$(command -v opencode || true)}
[ -x "$OPENCODE" ] || { echo "opencode not found; set OPENCODE" >&2; exit 2; }
TRIAL_TIMEOUT=${TRIAL_TIMEOUT:-1800}
skill_src="$here/../../../skills/vivado-ip-configurator"

mkdir -p "$trial/proj"
trial=$(cd "$trial" && pwd)

# The model server must answer before the clock starts.
curl -sf -m 10 "$LEMONADE_URL/models" >/dev/null \
    || { echo "no model server at $LEMONADE_URL" >&2; exit 2; }

# Project config. The skill is staged, and allowed, only in the with-skill condition; everything
# else (model, sampling, tools, Vivado) is identical in both.
if [ "$mode" = with-skill ]; then
    mkdir -p "$trial/.opencode/skills"
    cp -R "$skill_src" "$trial/.opencode/skills/"
    skill_permission=allow
else
    skill_permission=deny
fi
sed -e "s|@MODEL_ID@|$MODEL_ID|g" -e "s|@LEMONADE_URL@|$LEMONADE_URL|g" \
    -e "s|@VIVADO_MCP_SERVER@|$VIVADO_MCP_SERVER|g" -e "s|@VIVADO_PATH@|$VIVADO_PATH|g" \
    -e "s|@TRIAL_DIR@|$trial|g" -e "s|@SKILL_PERMISSION@|$skill_permission|g" \
    "$here/setup/opencode.json.template" > "$trial/opencode.json"

# The prompt is the fenced text block of prompt.md, with the trial path filled in.
prompt=$(awk '/^```text$/{on=1; next} /^```$/{if (on) exit} on' "$here/prompt.md" \
         | sed "s|<TRIAL_DIR>|$trial|g")
if [ "$mode" = with-skill ]; then
    prompt+=$'\n\n'$(awk '/^```skill$/{on=1; next} /^```$/{if (on) exit} on' "$here/prompt.md")
fi

# Keep the user's own setup out of the trial: OpenCode merges ~/.config/opencode under the
# project config and also reads ~/.claude skills and instruction files. Private HOME and XDG
# directories inside the trial, plus the OPENCODE_DISABLE_CLAUDE_CODE switches, prevent that.
private="$trial/.private"
mkdir -p "$private"/{home,config,data,cache,state}
export HOME="$private/home" XDG_CONFIG_HOME="$private/config" XDG_DATA_HOME="$private/data" \
       XDG_CACHE_HOME="$private/cache" XDG_STATE_HOME="$private/state"
export OPENCODE_CONFIG="$trial/opencode.json" OPENCODE_DISABLE_CLAUDE_CODE=true \
       OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=true OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=true \
       OPENCODE_DISABLE_CLAUDE_CODE_MCP=true

echo "trial:  $trial ($mode)"
echo "model:  lemonade/$MODEL_ID at $LEMONADE_URL"
start=$(date +%s)
set +e
(cd "$trial" && timeout "$TRIAL_TIMEOUT" "$OPENCODE" run --format json --dir "$trial" \
    --model "lemonade/$MODEL_ID" "$prompt" > "$trial/transcript.jsonl" 2> "$trial/opencode.stderr")
agent_rc=$?
set -e
elapsed=$(( $(date +%s) - start ))
echo "agent finished in ${elapsed}s (exit $agent_rc)"

# Independent check: a fresh batch Vivado reads the saved design. The agent's own report is not
# used. If the project is not where the prompt asked, the first one found in the trial is
# checked and its location is reported, so a misplaced but correct design is still visible.
xpr="$trial/proj/proj.xpr"
location="as requested"
if [ ! -f "$xpr" ]; then
    xpr=$(find "$trial" -name '*.xpr' -not -path '*/.*' 2>/dev/null | sort | head -1)
    location="not where requested"
fi
set +e
{
    echo "PROJECT: ${xpr:-none} ($location)"
    "$VIVADO_PATH" -mode batch -nojournal -nolog -tempDir "$private/cache" \
        -source "$here/check/check_pl_clocks.tcl" -tclargs "$xpr" 2>&1 \
        | grep -E '^(CELL|CHECK|RESULT):'
} > "$trial/check.txt"
set -e
cat "$trial/check.txt"
{
    echo "mode=$mode"
    echo "model=lemonade/$MODEL_ID"
    echo "agent_seconds=$elapsed"
    echo "agent_exit=$agent_rc"
    grep '^PROJECT:' "$trial/check.txt"
    grep '^RESULT:' "$trial/check.txt" || echo "RESULT: ERROR check did not run"
} > "$trial/summary.txt"
