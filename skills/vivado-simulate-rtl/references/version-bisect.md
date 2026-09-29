<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Tool Version Bisect

Identify the release where a working simulation started failing. A regression
with a named first-bad version is far more actionable than "it broke", and the
last-good release is an immediate fallback for the user.

## When to bisect

- The user says it worked in an earlier Vivado release.
- The failure signature appeared after an upgrade.
- An Answer Record claims a fix in a release where the failure still occurs.

Bisect needs at least two installed releases. If only one is installed, say
so; do not ask the user to install tools as a precondition without approval.

## Controlled variables

Hold everything except the tool version constant:

- the same source set, defines, generics, include paths, and top;
- the same bounded runtime, seed, and terminal marker;
- the same mode, behavioral or post-implementation;
- freshly compiled third-party libraries per release, because
  `compile_simlib` output is version-specific;
- a separate working directory per release, so stale `xsim.dir` artifacts
  cannot leak between runs.

## Procedure

1. Confirm the signature in the current release.
2. Find one release where the test passes. Check the oldest available install
   first; if it also fails, the regression is older than the installed set and
   the bisect cannot conclude.
3. Order the installed releases and bisect between last-good and first-bad.
4. Rerun the identical bounded test at each step and compare the signature,
   not just pass or fail.
5. Report the first bad release, the last good release, and the exact delta in
   behavior.

## Confounders

Project upgrade prompts, regenerated IP output products, and migrated
constraints can change the design between releases. Prefer a non-project
source list or generated scripts so the sources are identical. When a project
upgrade was unavoidable, state it; the result is then a release-plus-upgrade
comparison, not a pure tool bisect.

An IP version change between releases is a different root cause from a
simulator change. Record the IP versions used in each run.

## After the bisect

Feed the first-bad release into
[known-issue-research.md](known-issue-research.md). A regression with a known
release boundary often matches an existing Answer Record or a fixed-in note,
and it strengthens a new defect report considerably.

## Report

```text
Signature under test:
Releases evaluated (in order):
Last good / first bad:
Delta observed at the boundary:
Sources held constant: yes | no (explain)
IP or project upgrade involved:
Fallback recommendation:
Research result for the first bad release:
```
