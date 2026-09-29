<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Recorded Results and Method

These measurements accompany the [ISP skill comparison](../README.md). They are preserved observations, not promised results for a new run. [`measurements.json`](measurements.json) contains the prompt, exact model identifiers, timings, token counters, and every scored check, including failures. It omits machine-specific paths, credentials, and model reasoning. Source-record and transcript hashes identify the original evidence; the full transcripts are not bundled here.

## How the Recorded Trials Were Run

1. **Prepare an empty design.** A harness created a fresh Vivado 2026.1 session and an empty `benchmark_bd` on `xc2ve3558-sfva1440-2MP-e-S`. It checked the live version before starting the timed task. No RTL or configured ISP was supplied.
2. **Select the condition.** The skill condition staged `vivado-ip-configurator` and loaded its Tcl helper. The baseline had no staged skill or helper. Both had Vivado MCP and AMD documentation-search MCP access.
3. **Start a fresh agent.** Each used Codex CLI 0.157.1, a separate `CODEX_HOME`, a new conversation, and medium reasoning. The exact model was `gpt-5.6-luna` or `gpt-5.6-sol` through the AMD gateway. These were Codex CLI experiments, not Copilot CLI or Claude Code experiments.
4. **Submit the same task.** Only the skill-use instruction and fresh session identifier differed within each pair. No parameter names, expected configuration, result table, or previous conversation was supplied. Each model could query documentation and inspect Vivado.
5. **Allow one attempt to finish.** The agent could correct errors during that attempt. There was no coaching or second attempt based on its final score. Elapsed model time includes tool calls and recovery. It excludes setup and post-run evaluation.
6. **Grade independently.** The harness read effective values, including defaults, and actual interface pins. It checked the result against the original request, rather than trusting the model's self-report. Feedback from this independent grader was not returned to the model. All sessions were stopped after evaluation.

Both Luna runs selected `xilinx.com:ip:visp_ss:2.0`. Sol with the skill selected that subsystem; Sol without the skill selected `xilinx.com:ip:visp:2.0`, the lower-level hardened ISP. The prompt did not require the wrapper. The common checks accept either when it realizes the requested interface and configuration.

### Isolation

The trials ran sequentially on the same Linux host. Every completed trial had a separate agent workspace, conversation, and Vivado session.

For **Luna**, both the agent and its dedicated MCP/Vivado server were sandboxed with bubblewrap. The host filesystem was read-only, `/home` was hidden except for the trial's writable workspace and the skill mount in the enabled condition, and PID, temporary, and runtime directories were separated. Each trial had a separate server process and private discovery/session state. Environment variables were allowlisted, and only the server received a read-only license configuration file. Live Tcl probes confirmed that repository tests, the grader, and previous Sol results were inaccessible.

The earlier **Sol** pair had agent-side filesystem separation, fresh Vivado sessions, and a run-specific MCP server, but that server could access the host filesystem and was shared sequentially between the two conditions. Its recorded tool calls showed no answer-key or other-trial reads, but the setup did not prevent all such access. The Sol rows therefore are not a perfectly controlled isolation-matched comparison with Luna.

Neither setup was a separate VM: the host kernel, installed tools, and network were shared. These are documented experimental boundaries, not a claim of absolute isolation.

### Setup and Grading Corrections

- An early Sol skill attempt was interrupted when its MCP server received a termination signal. Shared discovery state had allowed IDE clients to connect. That attempt was excluded as an infrastructure abort, preserved, and replaced after fixing discovery isolation. There was one completed skill attempt used in the table.
- Luna's first preparation failed because the sandbox hid its license configuration. Mounting that file read-only fixed setup before either task-model attempt; no Luna task attempt was repeated.
- Sol's initial grader assumed the subsystem IP and its pin naming. It was corrected to recognize the lower-level ISP and its different data-pin names. Two wrapper-only default checks were removed from both Sol scores, leaving 113 common checks. Regrading used saved designs and captured evidence; the model designs were not repaired. Luna used this corrected contract unchanged from the start.

## What the Score Measures

The 113 checks cover IP identity, enabled tiles, independent-input mode, live-input counts, disabled multi-pass settings, active input formats and pixels per clock, interface presence/protocol/direction, physical data-pin widths, shared input-3 settings, selected default output widths, and saved-design presence.

Some checks overlap: one missing interface causes its protocol, direction, and width checks to fail. The score is useful for comparing these runs, but is not a probability of correctness. A pass requires all checks; 84% is still a failed configuration.

The independent checks found:

- **Luna with skill: 18 failed checks.** Eight first-ISP paths on tiles 0 and 2 had incorrect 16-bit formats and eight corresponding ports were 64 instead of 48 bits wide. Two shared input-3 format checks also failed. All 15 interfaces were present.
- **Luna without skill: 46 failed checks.** All three tiles remained in Basic mode, nine independent input interfaces were absent, and active input formats/widths did not satisfy the requested RAW12 paths. The six visible inputs were all 64 bits wide, including tile 1 inputs reported by the model as RAW12.
- **Sol, both conditions: zero failed checks.** This does not mean the designs were identical. They used different IP layers and differed in additional defaults, including virtual-channel values and clock precision. Full-system wiring and hardware operation were not tested.

### Why the Skill's Checker Did Not Guarantee Success

The skill has error-recovery helpers and requires an intent audit. In the Luna skill trace, the agent used the helpers to recover from rejected configuration, but did not call `verify_intent` or `audit_intent`. It manually read values back and accepted the wrong per-tile interpretation of the user's requirement.

Even a correctly invoked checker relies on the agent supplying a correct requirement-to-parameter mapping. Comparing live values against incorrectly chosen expected values can pass. This example demonstrates both the benefit of guidance on port-enablement dependencies and the need for independent acceptance criteria.

## Time, Tool Calls, and Tokens

| Luna metric | With skill | Without skill |
|---|---:|---:|
| Model time | 241.44 s | 214.42 s |
| Setup through grading | 250.62 s | 223.62 s |
| Tcl batches | 13 | 26 |
| Nonzero Tcl results | 2 | 6 |
| Native Vivado ERROR lines | 6 | 22 |
| Documentation searches | 5 | 2 |
| Total input tokens | 1,662,423 | 1,626,525 |
| Cached input, included above | 1,580,728 | 1,562,459 |
| Non-cached input | 81,695 | 64,066 |
| Output tokens | 10,505 | 7,547 |

Token counts are cumulative CLI-reported usage over repeated model calls. They are not the size of one prompt; most input was cached. Reasoning output is included in the reported output total and is not added again. Native ERROR lines and failed Tcl batches are different measurements: one rejected operation can emit several messages, and helpers can capture a failure while the outer Tcl call returns successfully.

### Illustrative Model Cost

Using the repository's configured Luna rates at the time of analysis gives approximately **$0.0646 with the skill** and **$0.0563 without it**: about $0.0083, or 15%, more for the skill condition.

The rates used were $0.20 per million ordinary input tokens, $0.02 for cache reads, $0.25 for cache writes, and $1.20 for output. The calculation treats cache reads and writes as subsets of total input: `ordinary input = total input − cache reads − cache writes`. The recorded cache-write counts were 81,599 and 63,958 respectively.

These are **estimates from local configuration, not verified AMD gateway charges or a current price quote**. They exclude preflight, local compute, licenses, and MCP service costs. The rates and arithmetic inputs are preserved in `measurements.json`; substitute your actual billing terms before making a cost decision.

## Interpreting the Value

For Luna, the skill condition delivered the required independent ports and improved the score by about 25 percentage points with modest additional time and estimated model cost. It still failed the overall task. For Sol, direct MCP use already achieved 100% and was faster.

The demonstrated value is therefore specific to the model and requirement. Use this example to understand the workflow and failure modes, then measure repeated trials on your own tasks before making a reliability or cost claim.
