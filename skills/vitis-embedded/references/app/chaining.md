<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Chaining contract (embedded flow)

How the embedded steps compose end to end:

**Embedded flow:**
```
vitis-embedded → vitis-embedded → vitis-embedded → board programming (XSDB or SD boot)
        (.xpfm)                            (.elf)                    (BOOT.BIN/.pdi)        (on target)
```

Skills are **independent in code** and linked only by **artifacts on disk** (or, within an
in-console hop, by **component name**). This is the single source of truth for the contract;
each skill's `SKILL.md` has a short **Chaining** section that states just its own edges and
points here.

**Create is separate from build.** Every producer skill **creates (scaffolds/composes) by
default and builds only when the user asks** — creation and building are distinct steps. The
artifacts in the diagram above (`.xpfm`, `.elf`, `BOOT.BIN` / `.pdi`) exist **only
after the build step runs**; a created-but-unbuilt component has a scaffold but no build output.
So a downstream skill that needs a *built* input must check **build state, not just existence**,
and **offer to build** an unbuilt prerequisite (see *Three behaviors* below) — never auto-build
silently, never dead-end.

## Artifact handoff

| Skill | Consumes | Produces | Runs via |
|-------|----------|----------|----------|
| `vitis-embedded` | board shortcut / `.xsa` | **`.xpfm`** (built) | `runVitisPython` (in-console) |
| `vitis-embedded` | **`.xpfm`** (built) + domain | **`.elf`** (+ `build/.buildstatus`) | `runVitisPython` (in-console) |
| `vitis-embedded` | FSBL/PLM + app **`.elf`** + bitstream/PDI | **`BOOT.BIN` / `.pdi`** | **shell** (Bootgen) |
| board programming (XSDB or SD boot) | **`.elf`**, **`BOOT.BIN`/`.pdi`**, package | running target | **shell** (xsdb / program_flash) |

**In-console builds vs the 3600s ceiling.** The producer skills run in the live IDE via
`runVitisPython`. `hw_emu` builds run in-console; a full **`hw`** implementation build can
exceed the console's **3600s (1h)** hard ceiling, so it runs headless via `vitis -s` outside
the console instead.

**Shell skills stay shell (never in-console).** `vitis-embedded` (Bootgen) and
board programming (XSDB or SD boot) (xsdb / `program_flash`) drive external CLIs — Bootgen is a plain shell
tool, and xsdb's `interactive()`/`connect`/`dow` are the console's blocking hang class. They
run in a **terminal**, not through `runVitisPython`; they only **consume artifact paths** from
the producers (regardless of how those artifacts were produced).
`vitis-embedded` *may* query a live `client` for component discovery, but still builds via
the shell `bootgen`.

## The decisive rule: hand off the PATH, not the REPL object

The chain **crosses execution contexts**: platform and app run in-console (a live `client`
+ persistent REPL), but boot-image and board programming (XSDB or SD boot) are **shell** skills with no
`client` and no view of the REPL. So the only handoff that survives the whole chain is the
**on-disk artifact path**.

- Within the in-console hop (platform → app) you *may* reuse a REPL object, but prefer
  re-fetching by name (`client.get_component(name=...)`) so it survives a console restart.
- Across into a shell skill (app → boot-image), you **must** pass the artifact **path**
  (the `.elf`), never the live `app` object.

Therefore every skill: **ends by emitting its concrete artifact path (verified), and begins
by consuming a path.**

## Three behaviors every skill implements

1. **Backward-resolve a missing OR unbuilt input (approval-gated).** If the required upstream
   artifact is **absent**, offer to run its producer skill; if it exists but is only
   **created-but-unbuilt** (scaffold present, no build output), **offer to build it (ask +
   approve)** — either way, don't dead-end and don't silently auto-build. Then take the
   producer's reported artifact and continue. (e.g. app on a created-but-unbuilt `.xpfm` → offer
   to build the platform; boot-image on a created-but-unbuilt app → offer to build the app.) Skills only *invoke* their
   upstream; they stay independent in code.

2. **Verify before handing off (core invariant).** A created-but-unbuilt component is a valid
   state, **not** something to pass downstream — a silently-unbuilt platform breaks the app; a
   missing `.elf` breaks the boot image. Before a downstream skill consumes an input, confirm it
   is **built** (offer to build it if not, per behavior 1). Gate each step on its artifact:
   platform → fresh `.xpfm` + export `.buildstatus == SUCCESS`; app → `.elf` present +
   `build/.buildstatus` written. The chain is only as sound as its weakest verify.

3. **Forward-offer the next step (one line, not automatic).** After **creating** (not building),
   offer to build it ("build it now?"); after **building** and verifying its artifact, offer the
   next hop and keep composition user-driven: platform → "create an app on it?"; app → "package a
   boot image?"; boot-image → "download to target?". Creation and building are separate prompts.
