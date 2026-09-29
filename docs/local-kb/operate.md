<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Operate & Troubleshoot the RAG Stack

Command reference for the `amd-embedded-doc-search` CLI, plus validation and
troubleshooting for the local RAG database. For first-time deployment, see
[RAG Database Setup](README.md).

Examples use the command name alone. From the extracted package directory,
run `./amd-embedded-doc-search` instead, unless that binary is already on
your `PATH`.

## Prerequisites

Install Docker Desktop (Windows) or Docker Engine + Compose plugin
(Linux): <https://docs.docker.com/get-started/get-docker/>

```console
$ docker ps
$ docker compose version
```

| Requirement | Why |
|-------------|-----|
| Docker Engine + Compose plugin | All three services run as containers |
| ~8 GB free disk | Weaviate snapshot + model + container images |
| An answering model (frontier or local) | Generates answers from retrieved docs |
| An MCP client (VS Code or OpenCode) | User interface for chat |

## Environment Check

```console
$ amd-embedded-doc-search doctor
```

Checks the Docker CLI, daemon, and Compose plugin; prints an exact
remediation command for anything missing. Safe to run any time — installs
nothing.

## Quick Start

```console
$ amd-embedded-doc-search doctor       # 1. check environment
$ amd-embedded-doc-search configure    # 2. generate deployment files (interactive)
$ amd-embedded-doc-search start        # 3. bring the stack up
```

Or in one step: `amd-embedded-doc-search configure --start`.

> **First start takes time.** `start` (and `configure --start`) runs
> `docker compose up -d`, which returns almost immediately — but the
> containers keep initializing in the background afterward: Weaviate has
> to import the pre-built snapshot (the full AMD Embedded documentation
> corpus) and llama.cpp has to load the embedding model. This can take a
> few minutes depending on disk speed, even though the images themselves
> are already pre-loaded from `images/`. Run `amd-embedded-doc-search ps` or
> `amd-embedded-doc-search logs --follow` to confirm all three containers are healthy
> before connecting a client — querying too early can return empty or
> failed results.

## Configure

1. **Collection** — the document snapshot to import (the AMD Embedded
   documentation corpus package supplied to you)
2. **Embedding model** — the vectorizer to use for semantic search
3. **MCP port** — host port for the MCP server (defaults to the first free
   port at or above `8080`)

For embedding models served via the OpenAI API you'll also be asked for a
**base URL** and **API key** — these are stored in `.env` and never echoed
to the terminal. Weaviate's own API key is auto-generated on first run and
reused automatically on subsequent `configure` runs; you never need to
provide it.

All generated files are written to `~/.config/amd-embedded-doc-search/`:

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | stack definition |
| `class-config.json` | vectorizer config mounted by the importer container |
| `.env` | persisted settings (model, ports, API keys) |

Key flags:

| Flag | Default | Description |
| --- | --- | --- |
| `--gpu` | `false` | Enable AMD ROCm GPU acceleration for the embedding model |
| `--llama-image` | `ghcr.io/ggml-org/llama.cpp:server` | llama.cpp image (self-hosted models only) |
| `--mcp-image` | `vivado.amd.com/doc-search-mcp-server:0.9.0` | MCP server image |
| `--mcp-port` | first free port ≥ `8080` | Host port for the MCP server |
| `--start` | `false` | Start the stack after writing configuration files |
| `--weaviate-image` | `cr.weaviate.io/semitechnologies/weaviate:1.38.2` | Weaviate image |

## Lifecycle

| Command | What it does |
|---------|-------------|
| `amd-embedded-doc-search doctor` | Read-only health check of Docker environment |
| `amd-embedded-doc-search configure [--start]` | Generate/update deployment files (interactive) |
| `amd-embedded-doc-search start [--follow]` | Start the stack (`docker compose up -d`) |
| `amd-embedded-doc-search stop` | Stop containers, keep data volumes |
| `amd-embedded-doc-search ps` | Show container status |
| `amd-embedded-doc-search logs [--follow]` | View container logs |
| `amd-embedded-doc-search stats` | Live CPU/memory/IO per container |
| `amd-embedded-doc-search purge [--yes]` | **Destructive**: remove all data and config |

> **Switching between embedding-model packages
> ([RAG Database Setup §1.5](README.md#15-embedding-packages))
> on the same machine? Use `purge`, not `stop`.** `amd-embedded-doc-search stop`
> intentionally preserves the Weaviate data volume, so if you re-run
> `amd-embedded-doc-search configure` pointed at a *different* embedding-model package
> (e.g. going from `embeddinggemma-300m` to `qwen3-embedding-0.6b`), the
> importer sees the collection already exists, skips loading the new
> snapshot, and the old vectors are reused as-is. Because the two models
> produce vectors of different lengths (EmbeddingGemma-300M = 768 dims,
> Qwen3-Embedding-0.6B = 1024 dims), any subsequent search fails with an
> error like `vector lengths don't match`. Run `amd-embedded-doc-search purge --yes`
> before `amd-embedded-doc-search configure` whenever you change embedding-model
> packages, so the stack starts from a clean volume and imports the correct
> data. On a clean volume, first-time import re-embeds the full document
> corpus using the bundled model and can take several minutes (observed:
> ~10 minutes for ~714k chunks on CPU) — this is expected, one-time work,
> not a hang.

## Global Flags

| Flag | Description |
| --- | --- |
| `--accessible` | Use accessible (screen-reader-friendly) TUI forms — pass before any subcommand, e.g. `amd-embedded-doc-search --accessible configure` |

## Connect a Client

> **Not automatic.** `amd-embedded-doc-search configure`/`start`/`ps` only manage the
> Docker stack itself — they never touch VS Code's or OpenCode's config
> files. You still add this block yourself to the client's MCP config
> (`.vscode/mcp.json`, etc.) — see
> [Frontier Model](frontier-model.md)
> or [Local LLM](local-llm.md) for the exact
> client steps for your chosen model option.

```json
"amd-embedded-doc-search": {
  "type": "http",
  "url": "http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search"
}
```

Port defaults to `8080`, stored in `~/.config/amd-embedded-doc-search/.env` as
`DOCSEARCH_MCP_PORT`.

## Validation

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

This confirms the RAG database itself is healthy, independent of which
answering model you're using. If this works but the *overall* answer
quality or connectivity has problems, check the model-specific validation
checklist in
[Frontier Model](frontier-model.md) or
[Local LLM](local-llm.md).

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| MCP tool not offered | Chat must be in **Agent** mode; confirm `.vscode/mcp.json` (or equivalent) is valid and reload the client. |
| MCP server not reachable | Run `amd-embedded-doc-search ps` and `amd-embedded-doc-search doctor`; confirm the client URL matches `http://127.0.0.1:<DOCSEARCH_MCP_PORT>/mcp/doc-search`. |
| Search fails with `vector lengths don't match` (e.g. `768 vs 1024`) | You switched embedding-model packages ([RAG Database Setup §1.5](README.md#15-embedding-packages)) using `amd-embedded-doc-search stop` instead of `amd-embedded-doc-search purge`, so the old package's vectors are still in the data volume. Run `amd-embedded-doc-search purge --yes` then `amd-embedded-doc-search configure --start` to reimport cleanly with the new model. |
| MCP client gets `Connection reset by peer` right after a fresh import finishes | Expected and harmless: the `amd-embedded-doc-search-mcp-1` container restarts itself once the importer finishes populating Weaviate, and needs ~15-30 seconds to come back up. Wait briefly (`docker ps --filter name=amd-embedded-doc-search-mcp-1` until `STATUS` shows it's been up for at least 15-30s) and retry the request. |
| Model gives fewer retries than expected on a RAG query | Check the retrieved chunks — RAG corpus contamination can cause a model to give up early on a poor-quality search result; this is a data issue, not necessarily a model issue. |

For model-connection issues specifically (model not listed, tool calling
not working, utility models, VRAM, etc.), see the troubleshooting section
in [Frontier Model](frontier-model.md)
or [Local LLM](local-llm.md).

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
