---
description: HLS design workspace manager — creates the canonical directory tree for a design at each pipeline stage. Called by /matlab-to-cpp and /architect.
argument-hint: design_name=<name> stage=<golden|sample_based|frame_based|rearchitect_v1|rearchitect_v2|show>
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Design Layout

Manages the canonical directory workspace for an HLS design as it progresses through the
conversion pipeline. Called by `/matlab-to-cpp` and `/hls-architect` — never run manually
unless inspecting or recovering a workspace.

---

## Canonical Tree

```
design_name/
├── golden/               ← MATLAB source files + matlab_input.bin + matlab_golden.bin
├── sample_based/         ← refactor_1: plain C++ kernel + testbench
└── frame_based/          ← refactor_2: frame-based C++ kernel + testbench (OUTPUT from matlab-to-cpp)
```

**Note:** The `rearchitect/` directory is created by `/hls-architect` after matlab-to-cpp completes. See `/hls-architect/reference/design-layout.md` for that structure.

`design_name` = MATLAB script name, snake_case, no `.m` extension.

---

## Stage Definitions

| `stage` arg | Directories created | Extra files written |
|---|---|---|
| `golden` | `design_name/golden/` | none (caller copies .m + .bin files in) |
| `sample_based` | `design_name/sample_based/` | none (caller writes kernel.cpp + testbench.cpp) |
| `frame_based` | `design_name/frame_based/` | none (caller writes kernel.cpp + testbench.cpp) |
| `show` | (nothing created) | prints expected tree |

---

## Behavior

### For any stage (except `show`)

1. Receive `design_name` and `stage` from the calling skill's context
2. Look up the stage in the table above
3. Create directories with parents:
   ```bash
   mkdir -p <path>
   ```

4. Print confirmation — example for `frame_based`:
   ```
   [design-layout] Created design_name/frame_based/
     ← write kernel.cpp and testbench.cpp here
   ```

5. Return the root path of the created stage directory to the calling skill.

### For `stage=show`

Print the full expected tree without creating anything:

```
[design-layout] Workspace for design_name (matlab-to-cpp stages):
design_name/
├── golden/          ← MATLAB sources + golden bins
├── sample_based/    ← Refactor 1: line-by-line C++ port
└── frame_based/     ← Refactor 2: frame-based C++ (OUTPUT → handed to /hls-architect)
```

---

## Callers

| Skill | When it calls | Stage |
|---|---|---|
| `/matlab-to-cpp` | Before Step 0 | `show` (orientation) |
| `/matlab-to-cpp` | End of Step 0 | `golden` |
| `/matlab-to-cpp` | End of Step 2 | `sample_based` |
| `/matlab-to-cpp` | End of Step 4 | `frame_based` |

---

## Rules

- **Never create directories outside `design_name/`**
- **Never overwrite existing directories** — if the directory already exists, print a warning and skip creation; do not delete existing content
