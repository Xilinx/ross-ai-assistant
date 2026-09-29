<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Using the RAG Database with a Frontier Model (GitHub Copilot)

**Point a frontier cloud model at your locally-deployed AMD Embedded RAG
database — it runs on the everyday laptop or workstation you already have,
since the answering model is hosted in the cloud and only the retrieval
layer runs locally.**

> **Prerequisite:** deploy the RAG database first — see
> [RAG Database Setup](README.md). This document
> assumes `amd-embedded-doc-search` is already running and reachable at
> `http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search`.

## Who It's For

This path suits teams that want to run the answering model in the cloud
rather than host it locally. If you have a standard laptop or workstation
and already have access to a frontier model — most commonly **GitHub
Copilot** in VS Code — you can get fully grounded, cited answers against the
entire AMD Embedded documentation corpus (Vivado, Vitis, PDM, ChipScope,
System Software, example designs, Wiki, Answer Records). The RAG database
(embedding model, vector search, MCP server) still runs 100% locally; only
the final answer-generation step is handled by the cloud model you already
have a subscription/sign-in for.

> **Already using the Vivado MCP Server's built-in `vivado_doc_search`
> tool?** That tool queries AMD's hosted knowledge base live over the
> internet, so with an internet connection it's already current and needs
> no local deployment. This path isn't about replacing it — see
> [Why use local RAG with a cloud model?](#why-local-rag)
> for where it helps.

## Not Air-Gapped

Use this path only where a policy-approved outbound connection to GitHub
Copilot is acceptable. The documentation **retrieval** stays local, but
**answer generation** does not.

| | RAG database ([core doc](README.md)) | This document (frontier model) |
|---|---|---|
| Runs locally, no outbound calls | ✅ Always | ✅ Retrieval side only |
| Overall workflow air-gapped | ✅ If paired with a [local LLM](local-llm.md) | ❌ **No** — your question and the retrieved passages are sent to GitHub Copilot to generate the answer |
| Requires GPU / local-LLM hardware | — | ❌ No — any machine that runs VS Code and Copilot works |

If you need a fully air-gapped, zero-outbound deployment, use the
[Local LLM](local-llm.md) path instead.

## Why Local RAG

The key idea is **decoupling**: this solution keeps *where documentation is
retrieved* (always local — the RAG database) separate from *which model
writes the answer* (your choice — cloud or local). Because the two halves
are independent, you can mix and match them, and swap either one later
without redoing the other. Pairing the **local** RAG database with a
**cloud** frontier model is useful in two common cases:

- **Evaluation / validation.** Before investing in GPU hardware and a local
  LLM, confirm that the local documentation snapshot and its retrieval
  quality are good — using a model you already trust (Copilot). You
  validate the retrieval half once, independently of whichever LLM you
  eventually pair with it.
- **IT policy.** If your network blocks AMD's documentation endpoints but
  permits your LLM provider, the local RAG database still gives the
  assistant grounded AMD documentation: the docs come from your machine,
  and only answer generation goes to the (allowed) cloud model.

> **Note vs. the hosted tool.** With general internet access, the Vivado
> MCP Server's hosted `vivado_doc_search` queries AMD's knowledge base
> live, so this local database isn't a fresher data source that replaces
> it — the value is the decoupling above. And once a machine is fully
> disconnected, the hosted tool is unreachable, so a
> [local LLM](local-llm.md) becomes the way to keep documentation search
> working.

## Setup

### Step 1 — Confirm RAG

```bash
amd-embedded-doc-search ps
```

All three containers (Weaviate, llama.cpp, MCP server) should show as
running. Note the port from `~/.config/amd-embedded-doc-search/.env`
(`DOCSEARCH_MCP_PORT`, default `8080`).

### Step 2 — VS Code

- Download: <https://code.visualstudio.com/download>
- Sign in with your GitHub account that has Copilot access (the built-in
  Copilot Chat experience — no extra extension install needed on current
  VS Code versions).
- Open the Chat view and confirm a Copilot model (e.g. GPT or Claude,
  depending on what your Copilot plan/model picker offers) is selected.

This is the standard, already-familiar Copilot sign-in flow — nothing
RAG-specific here. If your organization already has GitHub Copilot rolled
out, you likely have nothing to do in this step.

### Step 3 — Add MCP

Create `.vscode/mcp.json` in your workspace (or your user `mcp.json` to
make it available everywhere):

```json
{
  "servers": {
    "amd-embedded-doc-search": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp/doc-search"
    }
  }
}
```

Replace `8080` with your actual `DOCSEARCH_MCP_PORT` if you configured a
different port. Reload VS Code when prompted.

### Step 4 — Verify

1. Open Chat, switch to **Agent** mode.
2. Confirm your usual Copilot model is selected (GPT/Claude/etc., whatever
   your plan offers — tool calling is supported natively by all current
   Copilot models).
3. Confirm the `amd-embedded-doc-search` tool is listed in the tools picker (🛠 icon).
4. Ask: *"Search the documentation for AXI SmartConnect."* (or any Vivado,
   Vitis, PDM, ChipScope, System Software, example-design, Wiki, or Answer
   Record topic)

Expected: Copilot calls the `vivado_doc_search` tool and answers using the RAG
database, typically citing the source document(s).

### Step 5 — Custom Agent

By default, VS Code's Agent mode exposes its **full built-in tool belt**
(terminal, file edit, search, etc.) in every request, alongside whatever
MCP tools you've added. For RAG-only doc-search queries, none of those
extra tools are needed, but their schemas are still sent to the model on
every turn.

VS Code supports **custom agents** (`.agent.md` files) that restrict which
tools are available for a given persona. Create:

```text
~/.copilot/agents/amd-embedded-doc-search.agent.md
```

(A per-VS Code-profile user location; `.github/agents/amd-embedded-doc-search.agent.md`
in a workspace is the team-shared equivalent if you'd rather commit it to
a repo.)

```markdown
---
name: "AMD Embedded Doc Search — Adaptive"
description: "Fast AMD documentation RAG lookup. Use when the user asks about Vivado, Vitis, FPGA design, timing closure, IP configuration, hardware debugging, or any AMD Embedded topic."
tools: [amd-embedded-doc-search/*]
---
You are a documentation search assistant for AMD Embedded (Vivado, Vitis, Power Design Manager, ChipScope, System Software, example designs, Wiki pages, and Answer Records).

## Rules

1. IMMEDIATELY call `vivado_doc_search` with the user's question — do not ask clarifying questions first.
2. Start with `limit: 5` for every question, including a simple A-versus-B comparison. Use `limit: 10` only when the user explicitly requests a broad/deep answer, the request has three or more distinct subtopics, or the first search does not contain enough evidence. Prefer a second focused search over retrieving a large, loosely relevant result set.
3. Scale the answer to the task, not to the model:
    - For a simple fact or direct how-to question, be concise (about 150 words is a target, not a hard cap).
    - For a multi-part, diagnostic, design, or explicitly detailed request, provide the complete supported explanation. Use headings, steps, tables, and short code or Tcl examples when they improve clarity.
    - Never omit a necessary qualification, prerequisite, limitation, or troubleshooting step merely to meet a word target.
4. Do NOT fabricate information. Ground factual claims in the search results and cite the relevant source URL with the claim. Do not add a specific command, constraint type, parameter, implementation pattern, or rationale unless a returned source explicitly supports it. If a source only says to update constraints, report that without inferring how; state when the retrieved material does not provide the detail.
5. If the first results do not support a complete answer, are ambiguous, or conflict, run one or more narrower follow-up searches before responding.
6. If the tool returns no relevant results, say so and suggest a rephrased query.
7. Do not assume the active model's identity or capability. This workflow must work with both local and frontier models by favoring relevance and evidence over a fixed output limit.

## Output Format

- Answer sized to the user's question, with key facts and actionable guidance
- Source URLs next to the claims they support when practical
- A mandatory short **Sources** list at the end containing every URL used; do not finish the answer until the list is present
```

The `tools: [amd-embedded-doc-search/*]` line is the important part — it restricts this
agent to **only** the doc-search MCP server's tools, dropping the
terminal, file-edit, and other built-in tool schemas from the prompt
entirely.

Select it from the agents dropdown in the Chat view
(`@amd-embedded-doc-search`) instead of the default Agent mode when you
only need documentation lookups.

#### Measuring Speedup

If you've noticed `@amd-embedded-doc-search` responding faster than asking the same
question in default Agent mode, here's the general idea, then the direct
measurement behind it:

**The general idea.** A custom agent with `tools: [amd-embedded-doc-search/*]` calls
**only** the tool(s) matching that pattern — nothing else is ever placed
in front of the model. Default Agent mode, by contrast, hands the model
*everything* your IDE/CLI knows about on every single turn: its own full
built-in tool belt (terminal, file edit, search, etc.) **plus every tool
from every other MCP server you happen to have configured** — regardless
of whether your question needs any of them. A workspace may have no other
MCP servers configured, or several (other product servers, language
servers, or your own tools). A general prompt pays for *all of them* on
every turn, while a scoped custom agent pays for none of them.

**1. Retrieval itself is not the bottleneck — it's fast and constant.**
Timing five real `tools/call` round trips straight against the `amd-embedded-doc-search`
MCP server (bypassing any client/model, `curl` directly to
`http://127.0.0.1:8080/mcp/doc-search`) gives:

| Query | Round-trip time |
|---|---|
| "AXI SmartConnect configuration" | 31 ms |
| "hold time violation clock domain crossing" | 23 ms |
| "incremental compile UG904" | 20 ms |
| "report_utilization BRAM URAM" | 22 ms |
| "GT link training debug ILA" | 21 ms |

This ~20-30 ms embed-and-search step happens identically no matter which
agent or model calls it. **The speed difference you're seeing is not
retrieval — it's how much gets sent to the model *before* your question,
on every single turn.**

**2. The tool-schema overhead is real, and it can be large.** Every MCP
server you have configured — not just `amd-embedded-doc-search` — advertises its full
set of tool schemas (and often a block of usage instructions) to the model
on every single turn, whether or not your question needs any of them.
Scoping an agent to `tools: [amd-embedded-doc-search/*]` means only `amd-embedded-doc-search`'s own
(small) schema is ever sent; default Agent mode pays the token cost of
*every* configured tool, every time, regardless of what else happens to be
installed in a given workspace. The more tools/MCP servers you have
configured, the bigger that gap gets.

**3. Why fewer input tokens means a measurably faster response, even for
a cloud frontier model.** Every transformer-based LLM — local or cloud —
processes its entire input (system prompt + tool schemas + conversation +
your question) in a "prefill" pass before generating the first output
token, and prefill cost scales with input token count. This is
architecture, not a local-LLM quirk — the same mechanism measured directly
against a local model in
[Local LLM §4.1 (step A.6)](local-llm.md#41-vs-code)
(-73% prompt tokens → -56% total request time) is what's driving the
speedup you're seeing in Copilot. A larger tool belt also gives the model
more to reason about, occasionally triggering an unnecessary tool call
before it settles on `vivado_doc_search` — each detour costs a full extra network
round trip, not just tokens.

> **Note:** GitHub Copilot's cloud endpoint doesn't expose per-request
> prompt-token/timing logs the way a self-hosted `llama-server` does, so
> an exact "X seconds vs. Y seconds" Copilot-side number isn't available
> the same way the local-model number above was obtained. If you want one,
> the reliable way is a manual side-by-side: ask the same question via
> `@amd-embedded-doc-search` and via default Agent mode, several times
> each, and compare elapsed time.

## Validation

**MCP server:** `amd-embedded-doc-search ps` — all three containers up.

**Client:** ask in VS Code Agent mode (with a Copilot model selected):

```text
Search UG904 for incremental compile.
```
- ✅ The `vivado_doc_search` tool is invoked.
- ✅ Documentation is returned, with source citations.

**This path is not air-gapped (expected):**

- The machine needs a network path to GitHub Copilot. A successful
  `ping` is normal here.
- Documentation search itself stays on this machine, but the question and
  the retrieved passages are sent to GitHub Copilot to generate the answer.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `vivado_doc_search` tool not offered | Chat must be in **Agent** mode; confirm `.vscode/mcp.json` is valid and reload VS Code. |
| MCP server not reachable | Run `amd-embedded-doc-search ps` and `amd-embedded-doc-search doctor` (see [Operate & Troubleshoot](operate.md)); confirm the URL matches `http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search`. |
| Copilot answers without calling the tool | Some prompts don't clearly signal the model needs current/internal info — rephrase to explicitly ask it to search the documentation, or use the `@amd-embedded-doc-search` custom agent (Step 5) which forces an immediate tool call. |
| Copilot chat unavailable / no models in picker | This is a GitHub Copilot sign-in/licensing issue unrelated to `amd-embedded-doc-search` — confirm your GitHub account has an active Copilot plan. |

## Next Steps

- Need the RAG database steps again? →
  [RAG Database Setup](README.md)
- Need a fully air-gapped deployment instead (no outbound calls at all)? →
  [Local LLM](local-llm.md)

---

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
