<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Sub-agent prompt template for a single optimization attempt

Use this template when launching a sub-agent for one optimization attempt.
Fill in all `[bracketed]` fields before sending.

---

Task: Apply [specific technique] to `src/*.cpp`.

Goal: [state the optimization criteria, e.g. "minimize true latency in seconds"].

Baseline:
- cosim latency: [X] cycles
- post-route clock: [Y] ns
- true latency: [Z] ms  (= X × Y)
- LUT: [A], FF: [B], DSP: [C], BRAM: [D]

Hypothesis:
- What you will change: [describe exactly what pragma or algorithmic change to apply and where]
- What you expect to happen: [e.g. "II on the k-loop should drop from 5 to 2 because we now have 2 independent memory ports for C"]
- What would confirm the hypothesis: [e.g. "csynth loop table shows the hot loop's II reduced from N to M"]
- What would falsify it: [e.g. "II unchanged, meaning the bottleneck is a recurrence rather than a memory dependency"]

Steps:

### 1. Read the current kernel

Read `src/*.cpp`. Understand the current implementation before touching anything.
Also run:
```bash
python3 .claude/skills/synth/scripts/report_summary.py $TOP_FUNCTION/hls/syn/report/$TOP_FUNCTION_csynth.rpt
```
to confirm you understand the baseline loop structure and bottlenecks.

### 2. Apply the change

Make **only** the change described above. Do not fix, refactor, or optimize anything else
while you are in here — even if you notice an opportunity. One change per attempt.

### 3. Run csim

```bash
vitis-run --mode hls --config $CONFIG --work_dir $TOP_FUNCTION --csim
```

Read `$TOP_FUNCTION/hls/csim/report/$TOP_FUNCTION_csim.log`. If it ends with `FAIL` or a non-zero error count,
fix the logic error (if trivial) or abort and report why. Do not proceed to csynth after a
failing csim.

### 4. Run csynth

```bash
v++ --mode hls --config $CONFIG --work_dir $TOP_FUNCTION --compiler
```

Then investigate **thoroughly**:

#### 4a. Quick summary
```bash
python3 .claude/skills/synth/scripts/report_summary.py $TOP_FUNCTION/hls/syn/report/$TOP_FUNCTION_csynth.rpt
```

Check:
- Did II change on the loop you targeted?
- Did II change on any other loop (side effect)?
- Did total latency change? By the expected factor?
- Did resource usage change? By how much?

#### 4b. Violation Report
Open `$TOP_FUNCTION/hls/syn/report/$TOP_FUNCTION_csynth.rpt` and read the `Violation Report` section completely.
Does the remaining violation match your hypothesis about what is limiting II?
If the violation is different from what you expected, note it — the tool is telling you
the actual bottleneck.

#### 4c. Inferred directives
Read `$TOP_FUNCTION/hls/syn/inferred_directives.ini`. Did HLS auto-apply any directive that interacts
with your change (e.g. auto-partition, auto-flatten, auto-pipeline)?

#### 4d. Warnings in the synthesis log
```bash
grep -i "warning\|violation" $TOP_FUNCTION/hls/syn/report/$TOP_FUNCTION_csynth.rpt | head -40
```

#### 4e. IR inspection (when verifying compiler behavior)
When you want to confirm the compiler actually applied the transformation you intended
(e.g. that an array was partitioned, or a loop was unrolled in the IR):

```bash
VITIS_BASE=$(dirname $(dirname $(which vitis-run)))
export LD_LIBRARY_PATH="$VITIS_BASE/lib/lnx64.o:$VITIS_BASE/lnx64/lib/lnx64.o${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$VITIS_BASE/lnx64/tools/clang-3.9-csynth/bin/llvm-dis" $TOP_FUNCTION/hls/.autopilot/db/a.g.bc -o - 2>/dev/null | grep -A30 "<pattern of interest>"
```

Things to look for:
- **After `ARRAY_PARTITION`**: multiple GEP chains or multiple `alloca`s for the partitioned array.
- **After `UNROLL factor=N`**: N repetitions of the loop body in the IR.
- **After `LOOP_FLATTEN`**: single induction variable instead of nested loop counters.

#### 4f. Log the QoR
```bash
python3 .claude/skills/synth/scripts/qor_log.py --label "[technique-applied]" --csv qor_history.csv
```

### 5. Assess vs. hypothesis

Write down explicitly:
- Did the result match the hypothesis?
- If yes: what does this confirm about the design?
- If no: what does the actual result reveal? Is there a secondary bottleneck?

This assessment determines the next step.

### 6. Decide whether to escalate

| csynth result | Action |
|---|---|
| Latency improved ≥ 10% OR II reduced on dominant loop | Run `run cosim` |
| Latency unchanged, but hypothesis confirmed (e.g. "acknowledge II" change) | Commit; move to next idea |
| Latency got worse | Investigate. Do not immediately revert — understand why first. Then decide. |
| csim FAIL | Fix or revert. |

#### If running cosim:
```bash
vitis-run --mode hls --config $CONFIG --work_dir $TOP_FUNCTION --cosim
```
Read `$TOP_FUNCTION/hls/sim/report/$TOP_FUNCTION_cosim.rpt`. Compare cosim cycles to csynth estimate.
Note any discrepancy (cosim includes stall cycles; csynth does not).

#### If running impl:
```bash
vitis-run --mode hls --config $CONFIG --work_dir $TOP_FUNCTION --impl
```
Read `$TOP_FUNCTION/hls/reports/hls_impl_pnr.rpt` for the post-route clock period.
Compute: `true latency = cosim_cycles × period_ns`.

### 7. Report back

Provide a concise summary:

```
Technique applied: [exact pragma or code change]
File changed: src/*.cpp lines [N-M]

Hypothesis: [what you expected]
Confirmed: [yes/no/partial]
Reason: [why the result matched or didn't match the hypothesis]

csynth result:
  Latency: [baseline] → [new] cycles (csynth estimate)
  II (targeted loop): [baseline] → [new]
  II (other loops): [any unexpected changes]
  Resources: LUT [A]→[A'], FF [B]→[B'], DSP [C]→[C'], BRAM [D]→[D']
  Timing: [estimated] ns (target: [X] ns)
  Violations: [list any new or resolved violations]
  Inferred directives: [any surprises?]
  IR observation: [what you saw in a.g.bc, if inspected]

cosim result (if run):
  Latency: [X'] cycles (vs csynth estimate [Y'] — delta: [Z%])

impl result (if run):
  Post-route clock: [Y'] ns
  True latency: [Z'] ms (vs baseline [Z] ms)

Recommendation: [keep / revert / investigate further]
```
