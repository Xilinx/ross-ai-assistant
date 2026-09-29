<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Fully Air-Gapped Local LLM Deployment (AMD ROCm)

**Deploy a local, tool-calling-capable LLM alongside the RAG database for
a fully air-gapped, zero-outbound-network deployment end to end.**

> **Deployment model:** Lemonade is a model manager and can download a ROCm
> `llama.cpp` backend; it is not the backend itself. The reproducible,
> air-gapped procedure in this document stages that complete backend and runs
> `llama-server` directly. Lemonade server mode remains optional and must use
> the exact cache layout for the staged Lemonade version.

> **Prerequisite:** deploy the RAG database first — see
> [RAG Database Setup](README.md). This document
> assumes `amd-embedded-doc-search` is already running and reachable at
> `http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search`.

## Who It's For

Use this document if your environment requires that **no traffic ever
leaves the machine** — not even to a frontier model's cloud API. This
requires GPU-class hardware capable of running a modern, tool-calling LLM
at usable speed: a supported AMD GPU running ROCm, with enough VRAM for
your chosen model.

> **Note on the host machine.** The host that runs the *answering LLM* is
> a separate x86 machine with a supported AMD GPU — it is unrelated to the
> AMD FPGA or Adaptive SoC you are designing for. Adjust the paths, ports,
> and flags below to match your machine.

If you don't have hardware like this (e.g.
a standard laptop), see
[Frontier Model](frontier-model.md)
instead — it reuses the same RAG database with no local-LLM hardware
required, at the cost of not being fully air-gapped.

---

## 1. Transfer Bundle

If you're building the transfer bundle described in
[RAG Database Setup §3.1](README.md#31-phase-1),
add a local-LLM folder before leaving the connected machine:

```text
transfer/
└── Lemonade/                 (only if the target has no local LLM)
    ├── lemonade-installer-<version>.<package>
    ├── llamacpp-rocm/         (complete ROCm inference runtime — see below)
    ├── MANIFEST.sha256
    └── models/
        ├── Qwen3.6-35B-A3B-GGUF/
        │   ├── Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf
        │   └── mmproj-F16.gguf
        └── gemma-4-26B-A4B-it-GGUF/
            ├── gemma-4-26B-A4B-it-UD-Q4_K_M.gguf
            └── mmproj-F16.gguf
```

**Download the Lemonade installer package** for the target's exact Linux
distribution, architecture, and Lemonade version. The
[Lemonade installation guide](https://lemonade-server.ai/docs/guide/install/)
selects the package for each platform; it is not itself an installer binary.
Record the package URL and version in the transfer manifest.

**Download the GPU inference backend (ROCm).** The Lemonade installer does
**not** include the GPU inference engine. Lemonade downloads the ROCm
`llama.cpp` backend (`llama-server` and its ROCm libraries) from the internet
the first time it runs a model — which fails on an air-gapped host. Capture it
now, on a connected staging machine with the same Linux distribution,
architecture, ROCm compatibility, and GPU family as the target:

1. Install Lemonade and load any model **once** so it fetches the ROCm backend
   into its cache, then locate the downloaded `llamacpp/rocm` backend folder
   (its path depends on the install method — check Lemonade's cache/bin dir).
2. Copy the **complete contents** of that `rocm/` folder — `llama-server`, its
   shared libraries, and subdirectories — into
   `transfer/Lemonade/llamacpp-rocm/`. Do not copy only the executable.
3. Confirm the staged binary can start and record its version:
   ```bash
   transfer/Lemonade/llamacpp-rocm/llama-server --version
   ```

> **Confirm the backend location**
>
> Where Lemonade caches the downloaded backend depends on the Lemonade
> release and install method. Do not invent a Lemonade cache path on the
> target. This guide installs the staged runtime independently at
> `/opt/local-llm/runtime/` for direct `llama-server` use (see
> [§3.1](#31-runtime)). If you choose Lemonade server mode, transfer and
> restore its cache tree in the exact layout produced by the matching
> Lemonade release.

Lemonade runs **GGUF** model files hosted on Hugging Face — you download
the files on the connected machine; Lemonade picks them up on the
air-gapped machine afterward.

The listed multimodal model variants include the model itself plus a small
`mmproj` companion. Download exactly these files into
`transfer/Lemonade/models/` (keep each model in its own subfolder, since
the `mmproj-F16.gguf` files share the same name across repos):

| Model | Repo | File | Size |
|---|---|---|---|
| **Qwen3.6-35B-A3B** (recommended) | [`unsloth/Qwen3.6-35B-A3B-GGUF`](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) | `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` + `mmproj-F16.gguf` | ~22 GB + ~0.9 GB |
| **Gemma-4-26B-A4B MoE** (recommended) | [`unsloth/gemma-4-26B-A4B-it-GGUF`](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF) | `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` + `mmproj-F16.gguf` | ~16 GB + ~1.2 GB |

> **Why not a coder model?** A code-focused model tends to fabricate
> answers instead of calling the search tool — see
> [RAG Database Setup §2.3](README.md#23-components).

Before leaving the connected machine, also confirm:
- ✅ `transfer/Lemonade/` — versioned Lemonade installer, complete ROCm backend
  (`llamacpp-rocm/`), and `.gguf` model files
- ✅ The staged `llama-server --version` command succeeds
- ✅ `MANIFEST.sha256` covers every staged runtime and model file. Create it
  after all files are copied:
  ```bash
  (cd transfer/Lemonade && find llamacpp-rocm models -type f -print0 \
    | sort -z | xargs -0 sha256sum) > MANIFEST.sha256
  ```

---

## 2. Installation

Skip this whole section if the air-gapped machine already has a working
OpenAI-compatible LLM.

> **Does "OpenAI-compatible" mean this needs Internet access? No.** It
> refers only to the **API schema** — the same request/response format
> popularized by OpenAI (`/v1/chat/completions`, `/v1/models`, etc.). The
> local `llama-server` (or Lemonade, if selected) implements that same schema
> **locally**, on `127.0.0.1` — no OpenAI account, API key, or cloud
> connection is involved. It's the same reason
> `curl http://127.0.0.1:8000/v1/models` works entirely offline.

1. Verify the transfer bundle before installing or running anything:
   ```bash
   (cd transfer/Lemonade && sha256sum -c MANIFEST.sha256)
   ```
2. Install the delivered Lemonade package only if you intend to use Lemonade's
   own server or model-management commands. It is not needed to run the staged
   runtime directly. Follow the package's platform-specific offline install
   procedure; all OS package dependencies must already be available on the
   target.
3. Install the staged runtime and models in stable locations for direct,
   fully offline serving:
   ```bash
   sudo install -d -m 0755 /opt/local-llm/runtime /var/lib/local-llm/models
   sudo cp -a transfer/Lemonade/llamacpp-rocm/. /opt/local-llm/runtime/
   sudo cp -a transfer/Lemonade/models/. /var/lib/local-llm/models/
   sudo chmod -R a-w /opt/local-llm/runtime
   sudo /opt/local-llm/runtime/llama-server --version
   ```

   The service user used in [§7](#7-systemd-persistence) must be able to read
   `/var/lib/local-llm/models/` and access the ROCm device nodes. Configure
   those permissions according to the target platform's ROCm policy.

4. **Optional: Lemonade server mode.** Restore the complete Lemonade cache
   tree captured from the matching staging installation; do not substitute the
   direct-runtime path above for Lemonade's cache. After the Lemonade service
   is running, register the local models and prohibit all fetches:
   ```bash
   lemonade config set host=localhost offline=true no_fetch_executables=true \
     extra_models_dir=/var/lib/local-llm/models
   lemonade status
   curl http://127.0.0.1:13305/api/v1/models
   ```

   `13305` is Lemonade's default port; use the configured port reported by
   `lemonade status` if it differs. `offline=true` prevents model downloads,
   while `no_fetch_executables=true` prevents a missing backend from being
   fetched. Keep the default loopback binding unless you explicitly require
   remote access and have configured a firewall and API authentication.

   Lemonade registers `.gguf` files in `extra_models_dir` as `extra.<name>`.
   Expected: a JSON response listing your models. **Note the exact model
   `id` values** — needed for client configuration.

   > For AMD ROCm hardware, also see
   > [§3](#3-deployment) and
   > [§6](#6-vram-planning) for tuned launch flags,
   > multi-model VRAM budgeting, and the recommended 2-model configuration
   > used in this deployment. In short: `--flash-attn on`,
   > `--cache-type-k q8_0 --cache-type-v q8_0`, and `--parallel 1` for
   > Gemma models — see those sections for the full picture.

> **Which port will my client use?** Lemonade's built-in server defaults to
> port `13305` and serves one model at a time — fine for a quick
> single-model start. The tuned configuration in
> [§3](#3-deployment) instead runs
> `llama-server` directly on port `8000` (and `8001` for a second model),
> which is exactly what the client configuration in
> [§4](#4-client-setup) points at. Use `13305`
> for the simple Lemonade default, or `8000`/`8001` if you follow the tuned
> two-model launch in §3.

Then connect your client — [§4](#4-client-setup).

---

## 3. Deployment

> **Reference platform.** The launch flags, context sizes, and tuning in
> this section were developed and validated on an AMD Ryzen AI Max
> "Strix Halo" APU — a unified-memory x86 platform with an integrated AMD
> GPU, running ROCm. Treat the values below as a known-good starting point
> and adjust the GFX version, ports, and paths to match your own supported
> AMD GPU.

### 3.1 Runtime

- **Server**: `llama-server` (ROCm build), staged at
  `/opt/local-llm/runtime/llama-server` (see [§1](#1-transfer-bundle)).
- **Runtime environment:** `LD_LIBRARY_PATH` makes the staged ROCm libraries
  discoverable. `HSA_OVERRIDE_GFX_VERSION` is **not** universally required;
  set it only when the ROCm support guidance for the target GPU requires an
  override. The value below is the reference Strix Halo value, not a default
  for other GPUs:
  ```bash
  # Reference platform only; omit unless your target requires this override.
  export HSA_OVERRIDE_GFX_VERSION=11.5.1
  export LD_LIBRARY_PATH=/opt/local-llm/runtime:/opt/local-llm/runtime/hipblaslt:/opt/local-llm/runtime/rocblas
  ```
- **Hardware**: a supported AMD GPU running ROCm. On unified-memory APUs,
  "VRAM" is shared with system RAM; confirm the available budget via
  `rocm-smi --showmeminfo vram`.

### 3.2 Models

| Model | Notes | Weights + mmproj |
|---|---|---|
| **Qwen3.6-35B-A3B** (MoE, ~3B active) | Very large context window at low KV-cache cost; reliable tool calling | ~22GB + 0.9GB |
| **Gemma-4-26B-A4B-it** (MoE, ~3.8B active) | Compact footprint; reliable tool calling | ~16GB + 1.2GB |

### 3.3 Launch Flags

Both models share this baseline, tuned for the AMD ROCm backend:

```
--n-gpu-layers 99 --reasoning off --batch-size 512 --ubatch-size 256 \
--cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on
```

Gemma models additionally need `--parallel 1` (fixes sliding-window-attention
checkpoint-cache thrashing observed with the default parallel-slot count).

**Actual launch commands used in this deployment:**

```bash
# Qwen3.6-35B-A3B — loopback port 8000, ctx 131072
/opt/local-llm/runtime/llama-server \
  --model /var/lib/local-llm/models/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  --mmproj /var/lib/local-llm/models/Qwen3.6-35B-A3B-GGUF/mmproj-F16.gguf \
  --host 127.0.0.1 --port 8000 --ctx-size 131072 --n-gpu-layers 99 --reasoning off \
  --batch-size 512 --ubatch-size 256 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on

# Gemma-4-26B-A4B-MoE — loopback port 8001, ctx 32768
/opt/local-llm/runtime/llama-server \
  --model /var/lib/local-llm/models/gemma-4-26B-A4B-it-GGUF/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf \
  --mmproj /var/lib/local-llm/models/gemma-4-26B-A4B-it-GGUF/mmproj-F16.gguf \
  --host 127.0.0.1 --port 8001 --ctx-size 32768 --n-gpu-layers 99 --parallel 1 --reasoning off \
  --batch-size 512 --ubatch-size 256 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on
```

Only pass `--mmproj` when the downloaded model release includes a compatible
multimodal projector and you need vision support. Remove that option for a
text-only model.

Verify each server:

```bash
curl -s http://127.0.0.1:8000/v1/models
curl -s http://127.0.0.1:8001/v1/models
```

---

## 4. Client Setup

Both clients need the **local LLM** above and the **amd-embedded-doc-search MCP
server** ([Operate & Troubleshoot](operate.md)).

```text
        Local LLM (llama-server / Lemonade)
        http://127.0.0.1:8000-8001/v1  (this deployment)
        or http://127.0.0.1:13305/api/v1  (single-model Lemonade default)
                  |
             OpenAI API
                  |
        +---------+---------+
        |                   |
        v                   v
   VS Code (BYOK)       OpenCode
        |                   |
        +---------+---------+
                  |
                  v
        amd-embedded-doc-search MCP server
        http://127.0.0.1:8080/mcp/doc-search
```

### 4.1 VS Code

VS Code's built-in chat supports **Bring Your Own Key (BYOK)** models, so
you can use your local model with **no GitHub account, no Copilot plan,
and fully offline**.

**A.1 — Install VS Code**

- Windows: run `VSCodeSetup.exe` · Ubuntu/Debian: `sudo dpkg -i code.deb` · RHEL/Fedora: `sudo rpm -i code.rpm`

**A.2 — Add your local model (BYOK, no sign-in)**

1. Open the Chat view → model picker → **Manage Language Models** (gear icon), or run **Chat: Manage Language Models** from the Command Palette.
2. Select **Add Models → Custom Endpoint**.
3. Name the group (e.g. `Lemonade`) and give an API key value of `lemonade` (ignored, but required).
4. VS Code opens `chatLanguageModels.json` — configure it:

```json
[
  {
    "name": "Lemonade",
    "vendor": "customendpoint",
    "apiKey": "lemonade",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        "name": "Qwen3.6 35B A3B (Local)",
        "url": "http://127.0.0.1:8000/v1/chat/completions",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 129024,
        "maxOutputTokens": 2048
      },
      {
        "id": "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "name": "Gemma 4 26B A4B MoE (Local)",
        "url": "http://127.0.0.1:8001/v1/chat/completions",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 32000,
        "maxOutputTokens": 2048
      }
    ]
  }
]
```

- Set `id`/`url` to match the exact server and model from `curl .../v1/models` (§3.3) — one entry per running `llama-server` port (8000 = Qwen3.6-35B-A3B, 8001 = Gemma-4-26B-A4B MoE, per §3.2). The sample token limits reserve 2048 output tokens from the configured server context; reduce them if you choose a smaller context size.
- **`toolCalling` must be `true`** — the model only appears in Agent mode (required for MCP) if it supports tool calling.
- Save, then restart VS Code if the models don't appear right away.

**A.3 — Enable utility models (required for offline BYOK)**

Without a GitHub sign-in, VS Code's background helpers (title generation,
etc.) need a local model too. In **Settings (JSON)**:

```json
{
  "chat.utilityModel": "Lemonade",
  "chat.utilitySmallModel": "Lemonade"
}
```

**A.4 — Add the MCP server**

Create `.vscode/mcp.json` (or your user `mcp.json`):

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

Reload VS Code when prompted.

**A.5 — Verify**

1. Open Chat, switch to **Agent** mode.
2. Select your `Lemonade` model.
3. Confirm the `amd-embedded-doc-search` tool is listed in the tools picker.
4. Ask: *"Search the documentation for AXI SmartConnect."* (or any Vivado,
   Vitis, PDM, ChipScope, System Software, example-design, Wiki, or Answer
   Record topic)

Expected: the model calls the `vivado_doc_search` tool and answers using the
documentation database.

**A.6 — (Optional) The `amd-embedded-doc-search` custom agent — reduce prompt overhead**

By default, VS Code's Agent mode exposes its **full built-in tool belt**
(terminal, file edit, search, etc.) in every request, alongside whatever
MCP tools you've added. For RAG-only doc-search queries, none of those
extra tools are needed, but their schemas are still sent to the model on
every turn — the same fixed-overhead problem described for OpenCode's
default `build` agent in [§4.2.B3](#42-opencode).

VS Code supports **custom agents** (`.agent.md` files) that restrict which
tools are available for a given persona — the equivalent of OpenCode's
`docsearch` agent. Create:

```text
~/.copilot/agents/amd-embedded-doc-search.agent.md
```

(A per-VS Code-profile user location;
`.github/agents/amd-embedded-doc-search.agent.md` in this workspace is the
team-shared equivalent if you'd rather commit it to a repo.)

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
agent to **only** the doc-search MCP server's tools, dropping the terminal,
file-edit, and other built-in tool schemas from the prompt entirely (the
same principle as OpenCode's minimal `docsearch` agent in §4.2.B3, just
expressed via VS Code's own custom-agent frontmatter instead of OpenCode's
`agent.tools` JSON).

Select it from the agents dropdown in the Chat view
(`@amd-embedded-doc-search`) instead of the default Agent mode when you
only need documentation lookups.

**Why it helps.** Scoping the agent to `tools: [amd-embedded-doc-search/*]` sends only
the doc-search tool schema to the model each turn instead of VS Code's full
built-in tool belt. That trims the fixed prompt overhead paid before your
question is processed, which lowers response latency — the same effect as
OpenCode's minimal `docsearch` agent (§4.2.B3).

### 4.2 OpenCode

Use this if you prefer a terminal-based client.

**B.1 — Install OpenCode**

```bash
tar xzf opencode.tar.gz
opencode --version
```

**B.2 — Provider and MCP configuration**

`~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "webfetch": "deny",
    "websearch": "deny"
  },
  "provider": {
    "lemonade-qwen36": {
      "name": "Lemonade - Qwen3.6 (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:8000/v1", "apiKey": "none" },
      "models": {
        "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf": {
          "name": "Qwen3.6 35B A3B (Lemonade)",
          "contextLength": 131072,
          "maxTokens": 2048
        }
      }
    },
    "lemonade-gemma26moe": {
      "name": "Lemonade - Gemma 4 26B MoE (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:8001/v1", "apiKey": "none" },
      "models": {
        "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf": {
          "name": "Gemma 4 26B A4B MoE it (Lemonade)",
          "contextLength": 32768,
          "maxTokens": 2048
        }
      }
    }
  },
  "mcp": {
    "amd-embedded-doc-search": {
      "type": "remote",
      "url": "http://127.0.0.1:8080/mcp/doc-search",
      "enabled": true
    }
  }
}
```

> Note each provider points at a **different port** — one llama-server process
> per model, run simultaneously (VRAM budget permitting; see §6).

**B.3 — The `docsearch` agent — minimize prompt overhead**

OpenCode's default `build` agent exposes its full
built-in tool belt (bash, edit, read, grep, glob, list, lsp, task, todowrite,
question, skill, webfetch, websearch) in every system prompt. For this
RAG-only workflow, most of those tools are never used, but their tool-schema
definitions still cost a large, fixed block of overhead per request —
paid before the model even sees the user's question.

**What did *not* work:**
- `permission: {webfetch: "deny", websearch: "deny"}` — blocks execution
  correctly but does **not** shrink the prompt (schemas are still sent).
- `agent.build.tools: {webfetch: false, websearch: false}` — same result;
  webfetch/websearch are a small fraction of the total tool-schema overhead.

**What worked:** a dedicated, minimal agent with every non-essential tool
disabled:

```json
{
  "agent": {
    "docsearch": {
      "description": "Minimal agent for fast Vivado doc-search RAG lookups only. All non-essential tools disabled to reduce prompt overhead and latency.",
      "prompt": "You are a documentation search assistant for AMD Embedded (Vivado, Vitis, Power Design Manager, ChipScope, System Software, example designs, Wiki, and Answer Records). Immediately call vivado_doc_search with the user's question; do not ask clarifying questions first. Start every search with limit 5; use limit 10 only when the user explicitly requests a broad/deep answer, the request has three or more distinct subtopics, or the first search lacks enough evidence — prefer a second focused search over a large, loosely relevant result set. Scale the answer to the task, not the model: be concise (about 150 words) for a simple fact or how-to; give the complete supported explanation with headings, steps, tables, and short code or Tcl examples for multi-part, diagnostic, or explicitly detailed requests; never omit a necessary qualification, prerequisite, limitation, or troubleshooting step to meet a word target. Do not fabricate: ground every claim in the search results and cite the source URL with the claim, and do not add a command, constraint type, parameter, implementation pattern, or rationale unless a returned source explicitly supports it. If results are insufficient, ambiguous, or conflicting, run narrower follow-up searches before answering. If there are no relevant results, say so and suggest a rephrased query. Do not assume the active model's identity or capability. End every answer with a short Sources list of every URL used.",
      "tools": {
        "webfetch": false, "websearch": false, "bash": false, "edit": false,
        "read": false, "grep": false, "glob": false, "list": false,
        "lsp": false, "task": false, "todowrite": false, "question": false, "skill": false
      },
      "permission": {
        "webfetch": "deny", "websearch": "deny", "bash": "deny", "edit": "deny"
      }
    }
  }
}
```

Disabling individual tools only removes their own schema cost.
The dominant fixed cost comes from the *entire* built-in tool set — you must
build a dedicated minimal agent (not tweak permissions on the
general-purpose agent) to see a meaningful reduction.

Invoke it with:

```bash
opencode run --agent docsearch --model lemonade-qwen36/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf "<question>"
```

**B.4 — Verify**

```text
Search Vivado docs for AXI SmartConnect.
```

Expected: OpenCode invokes the `vivado_doc_search` MCP tool and returns documentation results.

---

## 5. Model Comparison

Both recommended models — Qwen3.6-35B-A3B and Gemma-4-26B-A4B MoE — handle
the amd-embedded-doc-search workflow reliably. In testing across a range of
FPGA-engineering questions (design architecture, RTL lint, utilization
analysis, timing closure, and hardware debug), both consistently called the
`vivado_doc_search` tool immediately, answered concisely, and cited real source
documents (User Guides, Product Guides, Answer Records) rather than
fabricating content.

Choose based on your constraints:

- **Qwen3.6-35B-A3B** — a very large context window at low KV-cache cost,
  useful if you expect long conversations or large retrieved contexts.
- **Gemma-4-26B-A4B MoE** — a more compact footprint, useful when VRAM is
  tighter or you want to run it alongside other models.

If you're unsure, either works well for documentation lookups — try both on
your own hardware and pick the one that best fits your memory and latency
budget.

---

## 6. VRAM Planning

The main driver of GPU memory use is the **model weights**; the KV cache
(which grows with context length) is a comparatively small part of the
total. In practice that means:

- Context-size tuning saves relatively little memory — the bigger lever is
  simply which model(s) you load.
- Qwen3.6 uses a hybrid architecture with a recurrent-state component, so
  its KV cache stays small even at long context lengths — you can keep a
  large context window without a large memory penalty.
- Gemma-4-26B-A4B has a more compact footprint overall.

### 6.1 Context Sizes

| Model | Recommended ctx | Rationale |
|---|---|---|
| Qwen3.6 | 131072 | KV cost stays low; no reason to shrink |
| Gemma 26B MoE | 32768 | Comfortably covers typical RAG workloads |

### 6.2 VRAM Footprint

To help you size hardware, these are the approximate amounts of GPU memory
each model used to load at the recommended context size on the
[reference platform](#3-deployment).
Actual usage varies with GPU, driver, quantization, and context length —
treat these as planning estimates, not exact figures:

| Loaded | Approx. VRAM |
|---|---|
| Qwen3.6-35B-A3B (ctx 131072) | ~23 GB |
| Gemma-4-26B-A4B MoE (ctx 32768) | ~17 GB |
| Both models loaded at once | ~40 GB |

Model weights account for most of this; the KV cache is a relatively small
part, so shrinking the context window frees comparatively little memory.

### 6.3 Running Models

Whether you can run a single model or both simultaneously depends on your
GPU's available memory. Load one model first and check usage with
`rocm-smi --showmeminfo vram` before adding a second. On unified-memory
APUs, the VRAM allocation is carved out of system RAM and is often
adjustable in the BIOS if you need more headroom.

---

## 7. systemd Persistence

Two unit files enable persistent launch of the models across reboots.

**`llama-qwen.service`:**

```ini
[Unit]
Description=Llama.cpp Server - Qwen3.6-35B-A3B (tuned: q8_0 KV cache + flash-attn)
After=local-fs.target

[Service]
Type=simple
User=local-llm
# Reference Strix Halo only; omit or replace for another supported GPU.
# Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
Environment="LD_LIBRARY_PATH=/opt/local-llm/runtime:/opt/local-llm/runtime/hipblaslt:/opt/local-llm/runtime/rocblas"
ExecStart=/opt/local-llm/runtime/llama-server \
  --model /var/lib/local-llm/models/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf \
  --mmproj /var/lib/local-llm/models/Qwen3.6-35B-A3B-GGUF/mmproj-F16.gguf \
  --host 127.0.0.1 --port 8000 --ctx-size 131072 --n-gpu-layers 99 --reasoning off \
    --batch-size 512 --ubatch-size 256 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`llama-gemma.service`:**

```ini
[Unit]
Description=Llama.cpp Server - Gemma-4-26B-A4B MoE (tuned)
After=local-fs.target

[Service]
Type=simple
User=local-llm
# Reference Strix Halo only; omit or replace for another supported GPU.
# Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
Environment="LD_LIBRARY_PATH=/opt/local-llm/runtime:/opt/local-llm/runtime/hipblaslt:/opt/local-llm/runtime/rocblas"
ExecStart=/opt/local-llm/runtime/llama-server \
  --model /var/lib/local-llm/models/gemma-4-26B-A4B-it-GGUF/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf \
  --mmproj /var/lib/local-llm/models/gemma-4-26B-A4B-it-GGUF/mmproj-F16.gguf \
  --host 127.0.0.1 --port 8001 --ctx-size 32768 --n-gpu-layers 99 --parallel 1 --reasoning off \
  --batch-size 512 --ubatch-size 256 --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Before enabling either unit, create a dedicated service account, make it the
model-data owner, and confirm it has the ROCm device permissions required by
your distribution:

```bash
sudo useradd --system --home-dir /var/lib/local-llm --shell /usr/sbin/nologin local-llm
sudo chown -R local-llm:local-llm /var/lib/local-llm/models
```

Install with:

```bash
# Save the two complete units above as these filenames first.
sudo cp llama-qwen.service llama-gemma.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llama-qwen.service llama-gemma.service
```

---

## 8. Validation

**Local LLM:**

```bash
curl http://127.0.0.1:8000/v1/models   # or :13305/api/v1/models for a single-model Lemonade default
```
- ✅ Returns your downloaded models.

**MCP server:**

```bash
amd-embedded-doc-search ps
```
- ✅ The `amd-embedded-doc-search` containers are up.

**Client:** ask in VS Code Agent mode or in OpenCode:

```text
Search UG904 for incremental compile.
```
- ✅ The `vivado_doc_search` tool is invoked.
- ✅ Documentation is returned.

**Air-gap confirmation:**

- ✅ An enforced egress policy or no default route prevents outbound traffic;
  a failed `ping` alone is not proof of an air gap because ICMP may simply be
  blocked.
- ✅ For Lemonade server mode, `offline=true` and
  `no_fetch_executables=true` are set before loading any model.
- ✅ `ss -ltnp` shows the local LLM listening only on `127.0.0.1` unless a
  secured remote-access policy explicitly requires another bind address.
- ✅ `curl http://127.0.0.1:8000/v1/models` succeeds (local LLM works).
- ✅ Documentation search works in your chosen client.

The environment is now fully functional without Internet connectivity.

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Model not listed by `curl .../v1/models` | Confirm the server is running and that the URL and configured port match. A loopback (`127.0.0.1`/`localhost`) bind is expected for a same-machine client. Use `0.0.0.0` only for a deliberately secured remote-access deployment. |
| Model files not detected | Confirm `extra_models_dir` points to the folder holding the `.gguf` files: `lemonade config set extra_models_dir=/path/to/models`, then restart. |
| Model missing from VS Code picker | `toolCalling` not set to `true`, wrong `id`, or VS Code needs a restart. |
| VS Code warns about utility models | Set `chat.utilityModel` and `chat.utilitySmallModel` to your local model (§4.1, A.3). |
| MCP tool not offered | Chat must be in **Agent** mode; confirm `.vscode/mcp.json` is valid and reload VS Code. |
| MCP server not reachable | Run `amd-embedded-doc-search ps` and `amd-embedded-doc-search doctor` (see [Operate & Troubleshoot](operate.md)); confirm the client URL matches `http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search`. |
| Search fails with `vector lengths don't match` (e.g. `768 vs 1024`) | You switched embedding-model packages using `amd-embedded-doc-search stop` instead of `amd-embedded-doc-search purge`, so the old package's vectors are still in the data volume. Run `amd-embedded-doc-search purge --yes` then `amd-embedded-doc-search configure --start` to reimport cleanly with the new model. See [RAG Database Setup §1.5](README.md#15-embedding-packages). |
| MCP client gets `Connection reset by peer` right after a fresh import finishes | Expected and harmless: the `amd-embedded-doc-search-mcp-1` container restarts itself once the importer finishes populating Weaviate, and needs ~15-30 seconds to come back up. Wait briefly (`docker ps --filter name=amd-embedded-doc-search-mcp-1` until `STATUS` shows it's been up for at least 15-30s) and retry the request. |
| OpenCode ignores the model | Model key must match the exact id from the server's `/v1/models`; restart OpenCode after editing config. |
| Model gives fewer retries than expected on a RAG query | Check the retrieved chunks — RAG corpus contamination can cause a model to give up early on a poor-quality search result; this is a data issue, not necessarily a model issue. |
| VRAM exhausted / OOM when loading multiple models | Check `rocm-smi --showmeminfo vram`; see §6 for VRAM planning guidance and the BIOS VRAM allocation option. |

## Next Steps

- Need the RAG database steps again? →
  [RAG Database Setup](README.md)
- Don't have GPU-class hardware after all? →
  [Frontier Model](frontier-model.md)

---

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
