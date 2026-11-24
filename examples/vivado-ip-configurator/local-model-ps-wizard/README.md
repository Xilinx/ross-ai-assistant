<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Configuring the Versal Gen2 PS with a Local Model

This example runs the [`vivado-ip-configurator` skill](../../../skills/vivado-ip-configurator/SKILL.md)
with **Gemma 4 26B**, served on your own machine by **Lemonade** and driven by **OpenCode**. No
prompt or design data leaves your machine. The task is the same with and without the skill, so
you can see what the skill adds.

**With the skill, Gemma configured the IP correctly in all 6 trials (3 on Vivado 2026.1, 3 on
2026.2), in 74 to 143 seconds each. Without it, Gemma failed all 5 trials:** it invented parameter
names, never wrote a PL clock setting, and spent up to the 100-step limit doing so. Results were
checked by reading the saved design in a separate Vivado, not by trusting the agent's report.

## The task

Turn on three of the Versal Gen2 processing system's PL fabric clocks at given frequencies, keep
the fourth off, and generate one PL fabric reset:

> Set up the Versal Gen2 processing system's PL fabric clocks: enable PL clocks 0, 1 and 2 at
> 100 MHz, 150 MHz and 134 MHz respectively, leave PL clock 3 disabled, and generate a single PL
> fabric reset.

This is harder than it reads. On `xc2ve3558-sfva1440-2MP-e-S` the processing system is the
`ps_wizard` IP (the older `versal_cips` does not support the part), and none of these settings
is a top-level IP parameter: all eight are keys inside one nested parameter,
`CONFIG.PS11_CONFIG`. An agent has to find the right IP, find the keys inside that parameter,
write them without disturbing the rest of it, and read them back. The full prompt is in
[`prompt.md`](prompt.md), with the expected settings.

## Requirements

Everything below was used for the recorded results. Lemonade and the model can run on a
different machine from Vivado and OpenCode, as they did here; the Vivado machine only needs to
reach the model server's URL.

### Vivado machine (runs Vivado, the MCP server, OpenCode and the scripts)

| Requirement | Tested with | Notes |
|---|---|---|
| Linux, x86-64 | Ubuntu 24.04 | |
| Vivado | 2026.1 and 2026.2 | With a license that covers `xc2ve3558-sfva1440-2MP-e-S`. No board is needed. 2026.2 adds the IP database ([below](#vivado-20262-and-the-ip-database)); we tested a pre-release 2026.2 build. |
| Vivado MCP server | `vivado-mcp-server` 2026.10.1 | From the EA release page; see [Vivado MCP setup](../../../docs/getting-started/vivado-mcp.md). Nothing to register: each trial starts its own copy. |
| OpenCode | 1.18.35 | See [step 2](#2-opencode). |
| `vivado-ip-configurator` skill | 1.0.2 | From this repository; `run_trial.sh` copies it into each trial. Earlier versions fail this task when the agent creates the project folder itself. |
| bash, curl, awk, sed, find, coreutils `timeout` | | Used by `run_trial.sh`. |
| Python 3.8 or later | 3.12 | Only for `check/summarize.py`; standard library only. |
| npm registry access | | On every trial OpenCode downloads its plugin package and the OpenAI-compatible provider (about 65 MB) into the trial's private folder. No prompt or design data is sent. |
| Disk | | About 270 MB per trial (project, Vivado logs, OpenCode packages). |

**No Python packages to install, so there is no `requirements.txt`.** The skill runs inside the
Python that ships with Vivado and uses only its standard library, and so do this example's
scripts. (The skill's `scripts/requirements-database.txt` is for an optional vector database
that this example does not use.)

### Model machine (runs Lemonade and the model)

| Requirement | Tested with | Notes |
|---|---|---|
| Lemonade | 2026.46.2 | Any OpenAI-compatible server with tool calling works; set `LEMONADE_URL` and `MODEL_ID`. |
| Model | `Gemma-4-26B-A4B-it-GGUF`: unsloth `gemma-4-26B-A4B-it` Q4_K_M | About 17 GB of model files. |
| Context | 131,072 tokens, q8_0 KV cache | The largest single request was 24 K tokens with the skill and 51 K without it. |
| Hardware | AMD Ryzen AI MAX+ 395, 128 GB unified memory, Radeon 8060S (ROCm) | We did not measure the minimum. |

### Setup used for the recorded results

| | Vivado machine | Model machine |
|---|---|---|
| Processor | AMD Ryzen AI MAX+ 395 (16 cores, 32 threads) | AMD Ryzen AI MAX+ 395 (16 cores, 32 threads) |
| Memory | 128 GB | 128 GB, shared with the integrated Radeon 8060S GPU (gfx1151) |
| OS | Ubuntu 24.04 LTS | Ubuntu Core 24 (Lemonade installed as a snap) |
| Software | Vivado 2026.1 and a pre-release 2026.2 build, `vivado-mcp-server` 2026.10.1, OpenCode 1.18.35, Python 3.12 | Lemonade 2026.46.2 with its ROCm `llama.cpp` backend |
| Role | Ran `run_trial.sh`: OpenCode, one MCP server and one Vivado per trial, then the batch-mode check | Served Gemma 4 26B A4B (Q4_K_M, 128 K context, q8_0 KV cache, one request at a time) |

The two machines were on the same local network. Trials ran one at a time. We did not measure peak Vivado memory or the smallest machine that
works.

### Check the setup

[`check_setup.sh`](check_setup.sh) checks each requirement on the Vivado machine and that the
model server lists the model, and prints what is missing:

```bash
export VIVADO_PATH=/path/to/Vivado/2026.1/bin/vivado
export VIVADO_MCP_SERVER=/path/to/vivado-mcp-server
export LEMONADE_URL=http://<model-host>:13305/api/v1
./check_setup.sh
```

## Set up

### 1. Lemonade and the model

Install Lemonade for your platform from the
[Lemonade installation guide](https://lemonade-server.ai/docs/guide/install/), then download the
model and load it with a 128K context (see the [Lemonade CLI guide](https://lemonade-server.ai/docs/guide/cli/)):

```bash
lemonade pull Gemma-4-26B-A4B-it-GGUF
lemonade load Gemma-4-26B-A4B-it-GGUF --ctx-size 131072
```

Then find your server's OpenAI-compatible base URL and check that it lists the model. Current
Lemonade releases listen on port 13305 by default; the server we tested with listened on 8000 at
`/api/v1`:

```bash
export LEMONADE_URL=http://127.0.0.1:13305/api/v1      # adjust host, port and path to your server
curl -s "$LEMONADE_URL/models" | grep -o '"Gemma-4-26B-A4B-it-GGUF"'
```

For a machine with no Internet access, follow [Local LLM deployment](../../../docs/local-kb/local-llm.md)
instead.

### 2. OpenCode

Install OpenCode by following the [OpenCode documentation](https://opencode.ai/docs/), then
check the version:

```bash
opencode --version
```

This example was tested with OpenCode **1.18.35**. OpenCode's current installer installs version
2; check its configuration reference if a setting in the template is rejected.

### 3. The skill

Nothing to install: `run_trial.sh` copies [`skills/vivado-ip-configurator`](../../../skills/vivado-ip-configurator/)
from this repository into the trial's `.opencode/skills/` folder, where OpenCode finds project
skills.

## The OpenCode settings that matter for a local model

[`setup/opencode.json.template`](setup/opencode.json.template) is the whole configuration. Use it
for your own projects too. A 26B model does much better with a small, fixed setup than with
OpenCode's defaults:

- **Only five tools.** Everything is denied except `read`, `skill`, and the Vivado MCP's
  `vivado_start`, `vivado_execute` and `vivado_stop`. OpenCode then leaves the other tools out of
  the request, so the model is not choosing among dozens of tools it does not need. The MCP
  resource tools are switched off for the same reason.
- **Thinking off** (`chat_template_kwargs.enable_thinking: false`) and **a 100-step limit.**
  Long reasoning between tool calls cost time and did not help on this task.
- **Gemma's recommended sampling**: temperature 1.0, top-p 0.95, top-k 64, min-p 0.
- **One Vivado.** The MCP server runs in-process (`--stdio`) with a fixed `--vivado-path` and
  `--allow-path-override=never`, so the model cannot start a different Vivado.
- **No outside configuration.** `run_trial.sh` gives OpenCode private HOME and XDG directories and
  sets the `OPENCODE_DISABLE_CLAUDE_CODE*` switches, so your `~/.config/opencode`, `~/.claude`
  skills and instruction files do not leak into the run.

## Run it

```bash
cd examples/vivado-ip-configurator/local-model-ps-wizard
export VIVADO_PATH=/path/to/Vivado/2026.1/bin/vivado
export VIVADO_MCP_SERVER=/path/to/vivado-mcp-server
export LEMONADE_URL=http://127.0.0.1:13305/api/v1    # your Lemonade server, as in step 1

./run_trial.sh with-skill    ~/ps-wizard-trials/with-1
./run_trial.sh without-skill ~/ps-wizard-trials/without-1
```

Each trial needs a new, empty directory. Run trials one at a time: the model serves one request
at a time. A trial ends with the independent check:

```text
CHECK: PASS PS_USE_PMCPL_CLK0              expected 1     got 1
CHECK: PASS PMC_CRP_PL0_REF_CTRL_FREQMHZ   expected 100   got 100
...
RESULT: PASS
```

The check is [`check/check_pl_clocks.tcl`](check/check_pl_clocks.tcl). It opens the saved
project in a fresh batch Vivado and reads the eight settings from the design itself, so the
agent's own report plays no part in the result. You can run it on any project:

```bash
"$VIVADO_PATH" -mode batch -nojournal -nolog -source check/check_pl_clocks.tcl -tclargs <trial>/proj/proj.xpr
```

Each trial directory keeps the agent's event stream (`transcript.jsonl`), the project, the check
output and a one-line summary.

## Results

Recorded on 2026-11-15 with the setup above, one trial at a time on the same machines. Every
row is one trial; "PL settings wrong" counts the eight checked settings. Time is the model's,
from its first to its last event; "new input" is the prompt tokens the model had to process,
not counting the part served from its cache.

| Vivado | Skill | Trial | Result | Time | Tool calls | New input tokens | Cached input tokens |
|---|---|---|---|---:|---:|---:|---:|
| 2026.1 | with | 1 | **PASS** | 131 s | 11 | 21.6 K | 169 K |
| 2026.1 | with | 2 | **PASS** | 143 s | 13 | 23.6 K | 219 K |
| 2026.1 | with | 3 | **PASS** | 74 s | 9 | 19.6 K | 134 K |
| 2026.1 | without | 1 | FAIL: 7 of 8 wrong | 437 s | 99 | 30.0 K | 1,942 K |
| 2026.1 | without | 2 | FAIL: no ps_wizard cell | 702 s | 99 | 38.9 K | 2,340 K |
| 2026.1 | without | 3 | FAIL: 7 of 8 wrong | 527 s | 99 | 31.6 K | 2,077 K |
| 2026.2 | with | 1 | **PASS** | 91 s | 9 | 20.4 K | 137 K |
| 2026.2 | with | 2 | **PASS** | 116 s | 12 | 21.3 K | 191 K |
| 2026.2 | with | 3 | **PASS** | 125 s | 14 | 22.1 K | 234 K |
| 2026.2 | without | 1 | FAIL: no ps_wizard cell (stopped at 30 min) | 1,264 s | 42 | 51.0 K | 741 K |
| 2026.2 | without | 2 | FAIL: 7 of 8 wrong | 840 s | 99 | 41.1 K | 2,237 K |

The one setting the failed trials "got right" is PL clock 3 off, which is the IP's default: no
setting was written. Every no-skill trial also saved the project one folder deeper than asked.
`check/summarize.py <trial> ...` prints these columns for your own trials.

**What went wrong without the skill.** Gemma created the processing system cell (in two trials it
did not even get that far), then tried `set_property config {pl_clk0_freq 100; ...}`, names that
do not exist, and spent most of its remaining steps re-reading the cell's properties without
finding where the PL clock settings live (`CONFIG.PS11_CONFIG`).

**What the skill did.** It mapped "Versal Gen2 processing system" to `ps_wizard` on this part,
listed the matching keys inside `PS11_CONFIG` for each requirement with their legal ranges, wrote
the eight values the model selected, and read them back from the saved design before reporting.
The model made 9 to 14 tool calls and wrote no Tcl of its own.

## Vivado 2026.2 and the IP database

Vivado 2026.2 ships an IP knowledge database, `data/ip/ipdb/ipdb.sqlite` in the Vivado install
(74 MB). It describes 369 IPs: their 66,000 parameters with full-text search, 10,000 keys inside
nested parameters, 247,000 dependency links between settings, when each port is present, and
which of 6,578 parts each IP supports. For `ps_wizard` it holds all 3,542 keys of its two nested
parameters, each with its default and the `ps11` sub-core parameter it maps to; the eight keys
in this example are among them.

The skill uses the database when Vivado has it (its `begin` reply says `"discovery":"database"`)
and otherwise finds the same facts in the live Vivado session (`"discovery":"live"`, Vivado
2026.1). Nothing has to be installed or configured: the database comes with Vivado.

What it changed here, measured per workflow step over the three with-skill trials on each release:

| Step | 2026.1 (live) | 2026.2 (database) |
|---|---|---|
| Choosing the IP (`begin`) | 10 to 60 s | 10 to 13 s |
| Listing settings (`describe`) | 1 to 2 s | 10 to 15 s |
| Whole trial | 74 to 143 s | 91 to 125 s |

Choosing the IP is the step that improves. On 2026.1 the skill probes Vivado's catalog to learn
that `versal_cips` does not support this part and which IP takes its place, and how long that
takes varies; on 2026.2 it reads the answer from the database. On this one task the whole trial
takes about as long on either release and both pass every time: the skill reaches the same
configuration either way. Across our 32-case `vivado-ip-configurator` test suite (Claude Haiku
5.5), the database cut agent time by about a fifth and left token use about the same; the gain
is largest on IPs whose part support, replacements or nested settings are expensive to discover
live.

## Limits

- These are a few trials of one task on one pair of machines, not a pass rate. A local model varies from
  run to run; run several trials of your own task before relying on it.
- The check covers the eight requested settings. It does not check that the rest of the
  processing system configuration is unchanged.
- The example configures one IP. It does not connect the processing system to anything,
  synthesize it, or run it on hardware.
