<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Quick Start — Deploy the Local RAG Database

The fastest way to stand up the Local RAG documentation database on a
**connected machine** (internet available) — three steps. If you need a
fully air-gapped install using a transfer bundle, use
[RAG Database Setup](README.md) instead.

> **What you'll end up with**
>
> A local `amd-embedded-doc-search` stack (vector database + embedding model + MCP
> server) running in Docker, exposing a `vivado_doc_search` tool your AI
> assistant can call — with no documentation queries leaving your machine.

## a. Install Docker

Install Docker Desktop (Windows) or Docker Engine + Compose plugin
(Linux): <https://docs.docker.com/get-started/get-docker/>

Verify it's working:

```bash
docker ps
docker compose version
```

## b. Download and extract the package

Download **one** variant of the AMD Embedded Local Knowledge Base package
supplied for your entitlement (the two variants differ only by the bundled
embedding model). The archive name changes from build to build, so extract
the file you downloaded:

```bash
tar xzf <package>.tar.gz
```

This unpacks the `amd-embedded-doc-search` CLI, the embedding `models/`, and the
pre-loaded Docker `images/` into the current directory.

## c. Deploy

From the extracted folder:

```bash
./amd-embedded-doc-search doctor              # confirm Docker is ready
./amd-embedded-doc-search configure --start   # generate config and bring the stack up
```

`configure` is interactive — the defaults are fine:

1. **Collection** — the document snapshot to import (name depends on the package build)
2. **Embedding model** — the bundled model
3. **MCP port** — the first free port at or above `8080`

> **Optional: GPU acceleration**
>
> If the machine has a supported AMD GPU (ROCm), add `--gpu` to
> `configure` to run the embedding model on the GPU, which speeds up the
> one-time import.

> **First start often takes about 10 minutes**
>
> On first run the stack imports the documentation snapshot and loads the
> embedding model. The `mcp` container can stay in `Created` until that
> import finishes. Follow progress with `./amd-embedded-doc-search logs --follow`, and
> confirm all containers are healthy with `./amd-embedded-doc-search ps` before
> connecting a client — querying too early can return empty results.

Confirm the stack is up:

```bash
./amd-embedded-doc-search ps
```

## Connect your AI assistant

Note the MCP port (printed at the end of `configure`, and stored in
`~/.config/amd-embedded-doc-search/.env` as `DOCSEARCH_MCP_PORT`, default `8080`). Add
this to your MCP client configuration — for VS Code, create
`.vscode/mcp.json`:

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

Replace `8080` with your actual port if different, then reload the client.
In **Agent mode**, ask a Vivado / Vitis / PDM / ChipScope question and
confirm the assistant calls the `vivado_doc_search` tool.

## Everyday commands

| Command | What it does |
|---|---|
| `./amd-embedded-doc-search ps` | Show container status |
| `./amd-embedded-doc-search logs --follow` | Stream container logs |
| `./amd-embedded-doc-search stop` | Stop containers (data is preserved) |
| `./amd-embedded-doc-search start` | Start the stack again |
| `./amd-embedded-doc-search purge --yes` | Remove all data and config (start over) |

## Where to go next

- **Fully air-gapped install** (transfer bundle, two phases) → [RAG Database Setup](README.md)
- **Use it with a cloud model** (no GPU needed) → [Frontier Model (Cloud)](frontier-model.md)
- **Use it with a local, offline LLM** → [Local LLM (Air-Gapped)](local-llm.md)

---

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
