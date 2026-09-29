<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Adding a Simulator Backend

Add a backend only after the installed Vivado release officially supports it.
Do not infer support from a compatible HDL command line.

## 1. Verify support

Search current AMD documentation with `Vivado:vivado_doc_search`:

- UG900 Supported Simulators
- UG900 `export_simulation`
- UG900 third-party simulator setup
- UG973 Compatible Third-Party Tools

Record:

```text
Vivado release:
Simulator product and executable-reported version:
Canonical export_simulation identifier:
Supported host platforms:
Integrated launch support:
Behavioral / post-synthesis / post-implementation support:
Source URLs:
```

Stop if the backend is absent or the version/platform is unsupported. Do not
describe experimental command compatibility as supported integration.

## 2. Implement the backend contract

Create a direct reference linked from `SKILL.md` with these sections:

### File organization

Give a fully supported backend its own `references/<simulator>.md` when it has
distinct setup, launch, artifact, coverage, or diagnostic behavior. Keep
closely related, lightly supported backends in
`references/other-vivado-simulators.md` only while each remains concise and
unambiguous. Every reference must be linked directly from `SKILL.md`; do not
make one reference the only route to another.

### Discovery triggers

Product names, aliases, executable names, and explicit user phrases that select
the backend. Never select it merely because another backend is unavailable.

### Prerequisites

- installation and executable version check
- license environment
- supported host
- version-matched `compile_simlib` output
- required compiler/runtime environment
- supported HDL, encrypted IP, and mixed-language constraints

Site-specific paths must come from the user or existing project configuration.

### Vivado integration

- exact `target_simulator` value
- canonical `export_simulation -simulator` identifier
- project and simulation-fileset properties
- `compile_simlib` simulator identifier
- supported launch/export modes

Verify each name against command help or official docs for the target release.

### Execution

- integrated launch behavior
- generated-script directory and entry points
- compile, elaborate/optimize, and simulate order
- bounded runtime and safe termination
- environment that must be active in the execution shell
- timeout and cancellation behavior

Prefer Vivado-generated scripts over handwritten compiler commands.

### Artifacts

- per-stage logs
- waveform/database format
- generated scripts and library map
- coverage support and format, if any
- seed, plusargs, generics, parameters, and defines

### Verdict mapping

Document exact evidence for:

- analysis failure
- elaboration failure
- runtime assertion/error/fatal
- timeout
- normal simulator exit
- explicit functional PASS

Normal exit alone must not map to functional PASS.

### Diagnostics

Include backend-specific license, library, encrypted-IP, mixed-language,
optimization, SDF, waveform, and exit-code failure signatures.

## 3. Add evaluation coverage

Add scenarios to the skill's evaluation suite for:

1. missing installation or license;
2. missing/stale compiled AMD libraries;
3. behavioral PASS with transcript and waveform;
4. assertion failure classification;
5. one supported netlist/timing mode, if applicable;
6. unsupported platform/version rejection.

Fixtures must be deterministic and self-checking. Record expected artifacts and
forbidden fallback behavior.

## 4. Validate

Before claiming support:

- validate frontmatter, links, and canonical identifiers;
- run generated-script smoke tests on every claimed host/backend combination
  available to the team;
- test at least one AMD primitive or IP requiring compiled libraries;
- verify timeout handling;
- verify an assertion produces FAIL and a terminal marker produces PASS;
- confirm artifacts exist at reported paths;
- test skill discovery on the intended model classes.

Mark combinations without an installed/licensed simulator as `UNVERIFIED`.

## 5. Keep release data out of the core

Do not add version numbers, host matrices, environment variables, or
backend-specific commands to the common workflow unless they are universal.
Keep them in the backend reference so a release update changes one file and its
evaluations.

When AMD changes support:

1. update the release snapshot and source links;
2. rerun backend evaluations;
3. preserve obsolete guidance only in a clearly labeled legacy section when
   users still need it;
4. remove the support claim if validation no longer passes.
