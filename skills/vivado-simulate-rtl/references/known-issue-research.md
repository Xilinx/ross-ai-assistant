<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Known-Issue Research

Turn a reproducible failure into a documented cause and a verified workaround
before calling it an unfixable tool defect. Classification alone leaves the
user blocked.

Use `Vivado:vivado_doc_search`. It searches AMD/Xilinx Answer Records, known
issues, user guides, wikis, and GitHub, and returns source URLs.

## When to research

Always, for:

- a simulator crash, fatal, or internal error;
- an unsupported-construct or not-yet-supported message;
- a library, link, DPI, or host-environment failure;
- a result that differs between XSim and another simulator;
- a failure that appeared after a tool-version change.

Research before declaring `environment` final, before filing a defect, and
before telling the user there is no workaround.

## Build the query

- Quote the exact message identifier and text, for example
  `XSIM 43-3316 Signal SIGSEGV received`.
- Add the distinguishing construct, not just the signature: `mixed language
  hierarchical reference`, `GT cell VHDL and Verilog`, `VHDL 2008 nested
  record`, `unpacked array concatenation`.
- Name the stage: `xvlog`, `xvhdl`, `xelab`, `xsim`, `xsc`, `xcrg`,
  `compile_simlib`, `export_simulation`.
- Scope with `tool` and `version` when release behavior matters. Omit `family`
  unless the failure is device-specific.
- Run two or three differently phrased queries before concluding that nothing
  is documented. A signature-only query and a construct-only query return
  different results.

## Read the results

Answer Records and Known Issues are authoritative. Record the article number,
affected versions, fixed-in release, mechanism, and workaround. User guides
establish supported behavior. Wiki, blog, and GitHub results are corroboration
only.

One signature can have several documented mechanisms. `XSIM 43-3316` alone
maps to at least mixed-language hierarchical references, a GT cell
instantiated from both VHDL and Verilog, and stack overflow from an oversized
concatenation. The construct in the failing source, not the signal name,
selects which one applies.

Check version applicability before trusting a fix claim. An Answer Record from
an older release may still describe the correct mechanism while its "fixed in"
statement no longer matches the installed version, especially when the current
failure is a regression.

## Verify before reporting

A documented workaround is a hypothesis until it runs.

1. Extract the candidate workaround.
2. Apply it to a copy. Do not modify the user's project without approval.
3. Rerun the same bounded test with the same contract.
4. Compare against the original first causal error.
5. Record whether it resolved, partially resolved, or did not apply.

State plainly when a workaround changes semantics, for example forcing a
single-language flow, disabling an optimization, or splitting an expression.
The user needs to know what they are trading away.

## Prohibited

- Presenting an Answer Record workaround as verified without rerunning it.
- Downloading or applying a tactical patch or tarball without explicit
  approval.
- Claiming no known issue exists after a single query.
- Letting an older Answer Record override evidence observed in the installed
  release.
- Inventing an article number or URL. Cite only what the search returned.

## Report

```text
Signature and stage:
Search queries used:
Answer Record / document (id + URL):
Affected versions / fixed-in:
Documented mechanism:
Applies to this case: yes | no | partially
Workaround tried:
Result after rerun:
Semantics changed by the workaround:
Remaining risk:
```

## Official sources

- [UG900 Logic Simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation)
- [Xilinx Simulation Solution Center (AR 58795)](https://adaptivesupport.amd.com/s/article/58795?language=en_US)
- [compile_simlib design assistant (AR 63904)](https://adaptivesupport.amd.com/s/article/63904?language=en_US)
