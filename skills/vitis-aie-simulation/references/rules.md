---
name: aiesim-rules
description: >
  Cross-cutting rules for every AIE simulation stage: orchestration, scope, input
  checks, error reporting, search and paths.
metadata:
  version: "1.0.0"
---

# vitis-aie-simulation — Rules (apply throughout the flow)

Cross-cutting rules that apply to **every** stage of the flow. Consult this
first. Each stage's own specifics live in that stage's file; this file holds only
what applies everywhere.

> ## ⚠️ NEVER HALLUCINATE
> Report only what commands and tools **actually output**. Never invent tool
> names, options, flags, paths, error causes, or results. If something is
> unknown or not shown, **say so** — do not guess or fabricate.

| Area | Rule |
|---|---|
| Orchestration | The bundled `references/` copies are the authoritative, self-contained stages — follow them directly. This skill does not depend on any external stage skill. |
| Orchestration | Inputs are gathered up front; downstream stages **consume** them — never re-prompt for a value already captured. **Exceptions:** `aiesim-compare` asks for the output-to-golden file pairing after the sim, because the output files only exist then. |
| Scope | Simulation only: never compile or modify the design; the user supplies an existing compiled `hw` `WORK_DIR`. |
| Paths | **Everything is an absolute path.** Never resolve a user's path (`~` or relative): ask the user for the absolute path. Every path the skill builds is derived from the absolute inputs and is itself absolute. **Working directory:** a command that writes into the current directory runs after an explicit `cd <COMPILED_OUT_DIRECTORY>` in the same command (each command starts a fresh shell). After that `cd`, files inside `COMPILED_OUT_DIRECTORY` may be named relative to it or as `${PWD}/...`. |
| Input checks | Run the input checks before every simulation launch, including reruns on unchanged inputs. Never skip them on the grounds that they passed earlier. |
| Error reporting | On any error, report: **(1)** hit an issue — problem + which step; **(2)** failure reason — quote the actual error, explain plainly; **(3)** suggestions — concrete next steps, recommended one marked. Then stop and wait, unless the fix is one of this skill's always-on default flags. |
| Search | **STRICT: never search outside the workspace.** The workspace is only the folders the user gave in this run — `WORK_DIR`, `INPUT_DIR` and `COMPILED_OUT_DIRECTORY` — plus this skill's own folder, whose files are opened at the paths given in `SKILL.md`. Do not run `find`, `locate`, `ls -R` or a recursive `grep` on `/`, on a home or project area, on the Vitis/`aietools` install area (not even for alternates), or on any other folder outside the workspace. If something is missing or a file cannot be opened, say so in one line and ask the user for its path; never go looking for it, and never guess a path (see **NEVER HALLUCINATE** above). |
