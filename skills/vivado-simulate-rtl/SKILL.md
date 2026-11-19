---
name: vivado-simulate-rtl
description: Load this skill first, before reading any log, source or project, for every FPGA or HDL simulation task, question or customer escalation involving Vivado, XSim (xvlog, xelab, xsim), Questa, ModelSim, VCS, Xcelium, Riviera-PRO or Active-HDL, including diagnosing already-recorded compile, elaborate or simulate logs; its scripts do each flow in one call. Covers running or debugging a testbench or simulation set (sim_1), proving PASS/FAIL, the first failure, approval-gated RTL repair, timing/SDF and gate-level runs, UVM, seeded regression and replay, assertions, code and functional coverage closure, SAIF/VCD, portable stimulus; checking whether a simulator, license, compiled library or host/OS/release is supported; simulator crashes and CR/Jira triage of recorded runs (design or tool bug, workaround); and methodology (release bisect, headless waveforms, speed, minimal reproducers, cross-simulator disagreement, host library failures, adding a backend).
license: MIT
compatibility: Requires Vivado 2025.2 or 2026.1 with the Vivado MCP server, and Python 3. Third-party simulators (Questa or ModelSim vsim/vlog, VCS vcs, Xcelium xrun, Riviera-PRO, Active-HDL) need their own install, license and compile_simlib libraries.
metadata:
  author: AMD Skill Developer
  version: "1.1.1"
  stage: beta
allowed-tools: Read Bash Write
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# FPGA RTL Simulation

A clean compile or simulator exit is not a PASS: the verdict comes from the
transcript's terminal marker or its first failure. `S` below is
`python3 <this skill's directory>/scripts/simrtl.py`. Each command does the
whole flow in one process, writes evidence under `--out`, and prints compact
JSON (`--contract` adds a draft of the final JSON). Read that, not raw logs.
`S sim` and `S run` can take minutes (implementation, timing): give the shell
call a 600000 ms timeout, and if it still goes to the background, wait for it
and read `<out>/last_result.json` (the same JSON), never a log tail.
Do not explore the workspace or the skill directory first.

**Recorded logs you were given (customer escalation, "is it our code or the
tool?", any simulator):** your first command is
`S logs <each given dir> --out outputs/<case>`, before any find, ls, Read or
grep. It lists the files, counts errors per source, quotes the failing line
and its declaration, and prints `next`: do that next.

**Sources and a run script (`run.sh`) for an escalation ("our bug or
XSim's?"):** run the script once into `outputs/<case>/`, then simulate each
supplied variant with `S xsim-files <its files> --out outputs/<case> --name
<variant>`, not xvlog/xelab by hand. A control counts only at `control:
COMPLETE`; if every variant fails, copy one without the suspected construct
(ending `$display("TEST_PASS"); $finish;`) and run that the same way.

**Methodology request with no design in the workspace (a package to file,
a release bisect ("it passed last year and fails now, which release broke
it?", topic `bisect`), a speed-up, headless waveforms, a known-issue search, "add support
for a new simulator backend" (topic `new-backend`), simulator arbitration):** do not answer from memory, never stop to ask
for the design or logs (the method report is the deliverable), and do not list or read this skill's references or scripts
first (the command quotes what you need). Your first command is
`S advise <topic> --out outputs/<case>/report.md --request "<ask>" --contract --backend XSim`;
Use the exact `outputs/<case>/` path the request or task names, character
for character; never invent a folder name. Then do its
`next`: the report it writes is the deliverable, so never rewrite it with
Write; answer in a few lines and end with its contract.

**`SIP_<CELL>` undefined after compile_simlib:** your first command is
`S simlib-cells --family <f> --cells SIP_<CELL> --out outputs/<case>`; do not
search `$XILINX_VIVADO` or component.xml by hand. Name every cell in its
`answer_must_name` and give its `control`.

## Route the request

| Request | Do |
|---|---|
| recorded logs, escalation, CR triage | `S logs <dirs> --out outputs/<case>`, then its `next`; XSim control on a reduced file: `S xsim-files <reduced>.sv --out outputs/<case>/xsim_control` |
| run / prove PASS (XSim) | MCP build step below, then `S sim --out outputs/<case> --xpr <project> --simset <s> --contract` |
| exported design or archive (Model Composer, System Generator, IP export) | unpack, `find <dir> -name '*.xpr'`, and run the shipped project's simset with the row above; compiling its HDL by hand misses generated packages (`conv_pkg`), which is a setup error, not evidence |
| diagnose a failure | same, plus `--wcfg <s>_fail --trace`; propose the fix, but simulate no fixed copy of the DUT unless asked (its TEST_PASS reads as a false pass) |
| assertions / UVM | plus `--assert-pass` / `--uvm-test <test>` |
| seeds + replay | plus `--seeds <seed>,<seed> --replay-failures`, each seed copied digit for digit from the request |
| coverage, closure | plus `--cov [--tb tb_<dut>_closure.sv]`: the new test is a new file, the given testbench stays untouched; two builds: `--xpr a,b --cov` |
| SAIF / VCD window | plus `--saif /tb/dut --window A:B` or `--vcd "clk ..." --window A:B`, with the exact signal names declared in the testbench top |
| port capture + replay, `generate_vcd_ports` | plus `--ports /<tb top>/<dut instance> --expect <port>=<v> --replay-marker <NAME>` in the first run (not `--vcd`): writes the port VCD and the checked replay in its own simset, which prints `<NAME>=<v>` then TEST_PASS. `<NAME>` is the requested line's text before `=`, unchanged (for `FOO_SUM=17`: `--expect sum=17 --replay-marker FOO_SUM`, not `FOO`) |
| simulator missing or unlicensed | `S backends --check <sim> --out outputs/<case> --contract`; never launch |
| unsupported host, OS or release | same, then `Vivado:vivado_doc_search` "UG973 compatible third-party tools <sim>" |
| cross-simulator disagreement | XSim `sim` run, then `S backends --check <sims> --out outputs/<case> --matrix` |
| host check, library load failure | `python3 <skill>/scripts/sim_env_doctor.py`, see [environment-preflight](references/environment-preflight.md) |
| `SIP_*` / SecureIP model missing after compile_simlib | `S simlib-cells --family <f> --cells SIP_<CELL> --out outputs/<case>`; no recompile; name every skipped cell the IP also uses (GT channel/common) and give its `control` |
| methodology question, no design given: a package to file, a backend proposal, a bisect or speed plan | `S advise <topic> --out outputs/<case>/report.md --request "<ask>" --contract [--backend XSim]`; topic: bisect, reproducer, performance, headless, known-issue, new-backend, arbitration; add findings with `--append`, write no other files |
| crash, known issue, new backend | also `Vivado:vivado_doc_search` with the signature or "UG900 supported simulators" |
| run Questa, VCS, Xcelium, ModelSim, Riviera here (also guided repair) | MCP build step, then `S run --backend <sim> --xpr <project> --simset <s> --out outputs/<case> --contract [--name <s>_fail]`; never `S sim` (XSim only) |
| timing / SDF, gate-level (any simulator) | `S run --backend <sim> ... --mode post-implementation --type timing` (implements first if needed); XSim: `S sim ... --mode post-implementation --type timing` |
| step-by-step repair, approval pause | diagnose row, then stop without reading RTL: contract JSON with the repair / more evidence / stop question in `notes`; an approved repair changes one DUT line, no comments; for a multi-target final report run `S findings --out outputs/<case>` and paste its sections into the final message, filling each `Requirement N: <title>`; [guided repair](references/guided-interactive-repair.md) |

MCP build step (required before every first `S sim` or `S run`, also for an
existing `.xpr`; both refuse to start until a Vivado MCP session exists in
this workspace): `Vivado:vivado_start`, then one `Vivado:vivado_execute` that builds
or opens the project and ends with `close_project`:

```tcl
set ::EVAL_FIXTURE <f>; set ::EVAL_OUTPUT_DIR <abs-ws>/outputs/<case>/<f>; source -notrace <abs-ws>/<gen>.tcl; puts "top=[get_property TOP [get_filesets sim_1]]"; close_project
```

For an existing project use `open_project <abs>.xpr`; `--xpr` takes the
`.xpr` or its directory.

## Rules

- One `S sim` call covers every seed, testbench and replay. Do not split it
  or tee its JSON into `.log` files: it writes each run's transcript as
  `<run>.simulate.log` with a manifest.
- Report `backend` as one of XSim, Questa, ModelSim, VCS, Xcelium, Riviera,
  ActiveHDL. Never substitute another simulator, change `target_simulator`
  for a blocked backend, or edit the DUT to make a test pass.
- `failure_class` (design, testbench, environment, timeout, tool_defect,
  none) follows [diagnostics](references/diagnostics.md): the printed class
  is a suggestion. Timing PASS needs a positive SDF annotation message.
  A Vivado-written SDF with unbalanced parentheses is `tool_defect`: Vivado
  is the tool that mishandled legal input (the routed design), even though
  the simulator correctly rejects the file.
- The violated requirement is the property the failing check asserts, not
  one inferred from the DUT code. Before the first `TEST_PASS` it is always
  the reset requirement, whatever property the message names. After that,
  only when the failing message itself says "model"
  (`... does not match the reference model`) does it belong to the test
  that was running (the test after the last `TEST_PASS`), whatever the
  model is named: N tests passed means requirement N+1 when the tests follow
  the numbered requirements, even if the message or the fix names another.
  Otherwise a message that names a property maps to that property's
  requirement, even if the testbench computes the expected value with a
  model. The `requirement` line `S sim` prints applies these rules in this
  order; report what it names. Name exactly one requirement. A repair changes exactly one line. When
  the same term is missing wherever a signal is used, edit the one line that
  defines the signal (`wire sof = ...;`), never each use and never a new
  signal. Re-run with the same
  `--out` and `--simset`: `S sim` keeps the original failure as
  `<run>.first_fail.simulate.log`, and the initial fields come from it.
- Find tools via `PATH` and their environment variables; never search `/`,
  home or network directories, and never look for other copies of this skill.
- The shell keeps its working directory between commands, so `cd` only
  inside a subshell (`(cd outputs/<case>/repro && ./run.sh)`); a bare `cd`
  makes later relative paths such as `outputs/<case>/report.md` land in a
  nested `outputs/` folder.
- Final answer: the contract JSON (`backend` is exactly one of `XSim`,
  `Questa`, `ModelSim`, `VCS`, `Xcelium`, `Riviera`, `ActiveHDL`: the
  simulator the request is about, even when nothing was launched; never null) and
  `dut_modified` true only when a supplied design file was edited in place
  (reproducers, workarounds and controls written as copies keep it false), with
  `notes` that quote the first causal message verbatim with its simulation time (or the terminal marker), name
  the transcript and waveform paths, cite doc-search results, and say what the
  evidence does not prove. The JSON ends every final message, including one
  that stops at a question or approval gate. A methodology ask with no design
  attached is answered with the `advise` report, not by asking for sources.
  A customer escalation keeps the original failure and runs at least one
  control through simulation with `S xsim-files <variant files> --out
  outputs/<case> --name <variant>` (a variant that only elaborates is not
  a control). If every supplied variant fails, add a copy without the
  suspected construct and run it to `TEST_PASS`, so one XSim control passes
  and isolates the trigger; a pass on another simulator does not count. Run
  extra xvlog/xvhdl/xelab/xsim or synthesis steps only inside
  `outputs/<case>/<variant>/`: a PASS log left in the workspace root is
  not evidence. A failure seen only on a third-party simulator gets an XSim
  differential control on the same (or reduced) sources (`S xsim-files`),
  saved under `outputs/<case>/`. Only `control: COMPLETE` shows how XSim
  treats the construct; a `HARNESS_ERROR` is fixed and rerun, never reported.
  For log triage the notes also give each control's log path and checked
  values, the trigger the variants isolate, what was not re-run here, and
  that no workaround is verified without a functional run.

## References

Full method and report template: [workflow](references/workflow.md).
XSim: [xsim](references/xsim.md), [coverage closure](references/xsim-coverage-closure.md),
[UVM/seeds/assertions](references/xsim-advanced-verification.md),
[SAIF/VCD/stimulus](references/xsim-activity-capture.md),
[error signatures](references/xsim-error-signatures.md).
Third-party: [questa](references/questa.md), [modelsim](references/modelsim.md),
[vcs](references/vcs.md), [xcelium](references/xcelium.md),
[riviera](references/riviera.md), [activehdl](references/activehdl.md),
[shared rules](references/other-vivado-simulators.md),
[adding a backend](references/adding-a-simulator.md).
Diagnosis: [known issues](references/known-issue-research.md),
[arbitration](references/cross-simulator-arbitration.md),
[reproducer](references/minimal-reproducer.md),
[bisect](references/version-bisect.md),
[performance](references/simulation-performance.md),
[headless waveform](references/headless-waveform.md).
