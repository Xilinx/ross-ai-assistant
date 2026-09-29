<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Learned-config cache

`learned_params.json` is an **earned, regenerable** cache of parameter-DISCOVERY
facts the skill builds as it runs (idea #3). It exists to stop re-deriving the
same `feature → param` mapping every run (which caused found-then-lost variance)
and to cut MCP calls on repeat features.

## What it stores (and what it must never store)
Each entry is keyed `"<ip>|<normalized feature phrase>"` and holds only:
`param`, `shape`, `enabler`, `value_src` (`user`/`default`), `doc` (the citation
that established it), and `ip_version`.

It stores **no concrete parameter values** — there is no `value` field, and the
engine (`../lib/ipcfg_cache.py`) strips any `value`/`expected*` field on write.
This is the blind-integrity guardrail: a benchmark run may consult and populate
the cache freely because it cannot leak an expected answer.

## Why this is not the forbidden pre-built database
- Entries are **earned at runtime** from doc search + Vivado feedback, not shipped.
- Each entry is **version-stamped** (`ip_version`) and **regenerable** — delete the
  file and the skill rebuilds it.
- It records *where a feature lives*, never *what value the prompt wanted*.

To reset: `echo '{}' > learned_params.json` (or just delete it).
