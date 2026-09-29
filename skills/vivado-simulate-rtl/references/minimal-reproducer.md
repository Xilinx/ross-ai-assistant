<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Minimal Reproducer and Defect Package

A tool defect is only actionable with a small, self-contained case and an
exact command. Building that case is usually the expensive part, and it is
work this skill can do.

## Invariant

The failure signature is the thing being preserved: the same message
identifier, at the same stage, from the same tool. Record it before reducing
anything.

If the signature changes during reduction, you have moved into a different
problem. Undo the last step.

## Rules

Work on a copy. Never reduce the user's golden project in place. Reduction
deletes design content by intent, so it is destructive by nature.

Keep a written ablation record: what was removed, and whether the signature
survived. A reproducer without that record cannot be called minimal.

## Reduction ladder

Work coarse to fine and rerun after each step:

1. Remove unrelated simulation sets, tests, and top-level stimulus.
2. Cut the hierarchy to the smallest subtree that still fails.
3. Replace IP with a stub only if the signature survives. For SecureIP,
   GT, or encrypted models the IP is often essential to the bug; say so
   instead of stubbing it away.
4. Drop files not referenced by the surviving subtree.
5. Shrink parameters, generics, widths, and array bounds.
6. Inline or trim packages down to the declarations actually used.
7. For an elaboration crash, remove the testbench body entirely. Elaboration
   failures need no oracle.

Stop when removing any remaining element makes the signature disappear.

## Oracle requirement

An elaboration or compile crash reproduces on the command alone.

A wrong-results case is different: the package must contain a self-checking
oracle that states the expected value and prints an unambiguous terminal
marker. Without it, the reader cannot tell a tool defect from a disagreement
about intent.

## Package contents

- reduced sources and any required data files;
- the exact `xvlog` / `xvhdl` / `xelab` / `xsim` commands, or the generated
  scripts;
- tool version banner as printed by the tool, not from memory;
- host OS and distribution, and whether the host is supported;
- library mapping and `-lib_map_path` used;
- complete stage logs, not tails;
- the cross-simulator matrix from
  [cross-simulator-arbitration.md](cross-simulator-arbitration.md);
- research findings from
  [known-issue-research.md](known-issue-research.md), including the Answer
  Record checked and whether it applied;
- workaround status and what it changes;
- a single runnable script that reproduces the failure from a clean directory.

Verify the package by running that script in a fresh directory before handing
it over. An unverified package wastes a triage cycle.

## Confidentiality

Reduce proprietary RTL to the minimum that still reproduces the signature, and
tell the user what remains in the package. Do not upload, attach, or transmit
customer sources anywhere. Preparing a package is not permission to file it.

## Report

```text
Signature preserved:
Original size -> reduced size (files, lines, hierarchy depth):
Ablation record:
Reproduce command:
Tool version / host:
Cross-simulator matrix:
Known-issue research result:
Workaround and its cost:
Package path and clean-directory verification:
```
