---
name: hls-flattenable
description: 'Analyze whether a C/C++ loop nest is flattenable for Vitis HLS, and rewrite a non-flattenable loop nest into a flattenable one. Use when the user asks if loops can be flattened, wants to make/convert/restructure/fix a loop nest so it flattens (or so the inner pipeline does not restart per outer iteration), before adding LOOP_FLATTEN, or when HLS reports the nest was not flattened (HLS 200-957, 200-1991, 200-1992). Keywords: loop flatten, LOOP_FLATTEN, flattenable, nested loop, loop nest, perfect loop nest, merge loops, collapse loops, rewind'
license: MIT
compatibility: Requires Vitis 2026.1+
metadata:
  stage: beta
  author: "Alex Li, Yi Gao"
  version: "1.0.1"
---
<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Agent Skill: HLS Flattenable Loop Nest Analysis

## Modes

- **Analyze** (default): the user asks whether a loop nest is flattenable, or HLS reports it was not flattened (`HLS 200-957`, `200-1991`, `200-1992`). Apply the rules and report in the [Output Format](#output-format).
- **Make flattenable**: the user wants a non-flattenable nest rewritten so it flattens. Do the analysis, then follow [Making a Nest Flattenable](#making-a-nest-flattenable).

**Strictly follow the rules below. Do not infer, assume, or add criteria beyond these rules.**

---

## Definitions

- **Remaining loop**: a `for`, `while`, or `do-while` loop that is still a loop after HLS unrolling. A loop is **not** a remaining loop (it becomes straight-line code) if either:
  - **(a)** it has `#pragma HLS UNROLL` with **no** `factor=` and **no** `off=true`, and its trip count is a compile-time constant; or
  - **(b)** it is reached from inside the **innermost loop body** of the nest (Rule 7 only), its trip count is a compile-time constant, and it has no `#pragma HLS UNROLL factor=...` / `off=true` and no `#pragma HLS PIPELINE off`. HLS auto-pipelines the innermost loop, and pipelining implies full unroll of loops inside it (`HLS 214-291 ... complete unroll implied by the pipeline pragma`). This assumes default settings: if automatic loop pipelining is disabled for the design (`syn.compile.pipeline_loops=0`), apply (a) only.
- Any loop whose trip count is **not** a compile-time constant (it depends on a function argument, a runtime variable, or data) is **always** a remaining loop.
- **Loop counter** (also called **induction variable**): the variable declared or initialized in the `for` header and updated in its increment expression, e.g. `k` in `for (int k = 0; k < N; k++)`. It is **not** any other variable that happens to change on every iteration, such as an accumulator or a pointer advanced in the body.
- **Compile-time constant**: a literal, `#define`, `const`/`constexpr` value, template parameter, or an expression built only from these.

---

## Rules

### Rule 1 — Identify All Nested Loops
- Scan the code for all nested loop constructs.
- **1.1** Reject if any loop is a `do-while` loop. → **Not flattenable.**
- **1.2** Every loop **must** be a `for` loop. → If any loop is `while`, **not flattenable.**
- **1.3** The loop step (increment/decrement expression) **must not** be a runtime variable. Constants and compile-time expressions are allowed. → If the step is a variable, **not flattenable.**
- **1.4** No loop's **loop counter** (the variable in its `for` header, e.g. `k` in `for (int k = 0; k < N; k++)`) may be written **inside the loop body**. The `for` header's own increment does not count. Writes include `k--`, `k++`, `k += n`, `k = ...`, and passing `k` by non-const reference or pointer. Writing the counter in the body makes the trip count data-dependent, even if the `for` header looks constant. Writes to other variables, such as accumulators, are allowed. → If any loop counter is written in a body, **not flattenable.**
  - Typical trap, the "retry on empty" idiom: `if (s.empty()) { k--; } else { ... s.read() ... }`

### Rule 2 — No control flow between outer and inner loops
- If there is intervening control-flow structure (another block, conditional, etc.) between the outer loop body and the inner loops, → **Not flattenable.**

### Rule 3 — No Control Flow Before Inner Loop
- In the region **after the outer loop's opening brace** and **before the inner loop**, the following statements are **prohibited**:
  - `if`, `switch`, `break`, `continue`, `goto`
- If any of these appear, → **Not flattenable.**

### Rule 4 — Inner Loop Bounds Independence
- The inner loop's **start**, **end**, and **step** expressions must **not** depend on the outer loop's loop counter.
- If any bound references the outer loop variable, → **Not flattenable.**

### Rule 5 — Function Calls Before Inner Loop Are Allowed (With Constraint)
- Function calls **are permitted** in the region before the inner loop.
- **5.1** If the function's definition is visible, inspect it, including functions it calls: it **must not contain any remaining loop**. Only definition (a) applies here, because this region is not pipelined. If it does, → **Not flattenable.**
- If the definition is not available, assume it is acceptable (do not reject).

### Rule 6 — Innermost Loop Body: Conditionals Allowed (With Exceptions)
- Inside the **innermost** loop body, conditional statements (`if`, `switch`, ternary) **are allowed**.
- However, `break` and `goto` inside the innermost loop are **prohibited**. → If found, **Not flattenable.**

### Rule 7 — Innermost Loop Body: Function Calls Allowed (With Constraint)
- Function calls inside the **innermost** loop body **are allowed**.
- If the function's definition is visible, inspect it, including functions it calls: it **must not contain any remaining loop**. Definitions (a) and (b) both apply. If it does, → **Not flattenable.**
- Loops written directly in the innermost body are judged the same way. A non-remaining loop there is fine; a remaining loop there means the "innermost" loop is not really innermost.
- If the definition is not available, assume it is acceptable.

### Rule 8 — No Control Flow After Inner Loop
- In the region **after the inner loop** and **before the outer loop's closing brace**, the following statements are **prohibited**:
  - `if`, `switch`, `break`, `continue`, `goto`
- If any of these appear, → **Not flattenable.**

### Rule 9 — Function Calls After Inner Loop Are Allowed (With Constraint)
- Function calls **are permitted** in the region after the inner loop.
- **9.1** If the function's definition is visible, inspect it, including functions it calls: it **must not contain any remaining loop**. Only definition (a) applies here. If it does, → **Not flattenable.**
- If the definition is not available, assume it is acceptable.

---

## Output Format

Respond with a structured analysis in this exact format:

```
### Flattenable Loop Nest Analysis

**Loop Structure Identified:**
- <list each loop with its line number, type, and nesting level>

**Rule-by-Rule Evaluation:**

| Rule | Description                                              | Result               |
| ---- | -------------------------------------------------------- | -------------------- |
| 1    | All `for`, constant step, no counter writes in body      | PASS / FAIL (reason) |
| 2    | Direct nesting                                           | PASS / FAIL (reason) |
| 3    | No control flow before inner loop                        | PASS / FAIL (reason) |
| 4    | Inner bounds independent of outer var                    | PASS / FAIL (reason) |
| 5    | Calls before inner loop (no remaining loops inside)      | PASS / FAIL / N/A    |
| 6    | Innermost body: conditionals OK, no break/goto           | PASS / FAIL (reason) |
| 7    | Innermost body: calls/loops (no remaining loops)         | PASS / FAIL / N/A    |
| 8    | No control flow after inner loop                         | PASS / FAIL (reason) |
| 9    | Calls after inner loop (no remaining loops inside)       | PASS / FAIL / N/A    |

**Verdict:** ✅ Flattenable / ❌ Not Flattenable
**Reason (if not flattenable):** <first failing rule and explanation>
```

---

## Making a Nest Flattenable

Use this section when the user asks to **fix**, **make flattenable**, **convert**, **restructure**, or **rewrite** a loop nest so that HLS can flatten it:

1. Run the analysis above and list **every** failing rule, not just the first.
2. Apply the minimal fix for each failing rule **while preserving behavior**. After each fix, re-check all rules, because a fix can introduce a new violation (e.g. code moved into the innermost body must not add a `break`). Recommended fixes:

   | Failing rule | Fix |
   |---|---|
   | 1.4 retry-on-empty (`if (s.empty()) k--; else { x = s.read(); ... }`) | **Preferred:** drop the `empty()` test and use a blocking `s.read()`. Inside a pipelined loop a blocking read stalls the pipeline until data arrives, which is the same behavior. Keep the non-blocking form only if the loop must do other work while the stream is empty; in that case use the manual flatten below. |
   | 1.4 (other counter writes), or any rule where the outer loop must stay data-driven | **Manual flatten:** replace the nest with one `for (int it = 0; it < OUTER*INNER; it++)` loop and derive the old indices (`i = it / INNER; j = it % INNER`, or shift/mask when `INNER` is a power of two). A single loop needs no flattening, so a counter write such as `it--` is then acceptable. Note that the result is a single loop, not a "flattenable nest". |
   | 5 / 7 / 9: callee loop with a constant trip count | Add `#pragma HLS UNROLL` (no factor) to that loop, if the area of full unrolling is acceptable. |
   | 5 / 7 / 9: callee loop with a variable trip count | Rewrite with a constant maximum bound and a guard (`for (t = 0; t < MAX; t++) { if (t < n) ... }`) and fully unroll it, or hoist the loop out of the nest. |
   | 3 / 8: control flow in the outer body | Move it into the innermost body as a condition on the inner index (e.g. `if (j == 0) ...` / `if (j == INNER-1) ...`). |
   | 4: inner bound depends on outer index | Use the constant maximum bound and guard the body with `if (j < bound(i))`. |
   | 1.1 / 1.2: `while` / `do-while` loop | Rewrite as `for (int t = 0; t < MAX; t++)` with a compile-time `MAX` equal to the worst-case trip count, and guard the body with the original condition. |
   | 1.3: variable step | Iterate with step 1 over the constant maximum range and guard the body, e.g. `if (j % step == 0)`, or precompute the index as `j * step` with a guarded bound. |
   | 2: block or conditional wrapping the inner loop | Remove the wrapper. Move a condition into the innermost body as a guard (see 3 / 8). |
   | 6: `break` / `goto` in the innermost body | Replace with a `done` flag set instead of `break`, and guard the rest of the body with `if (!done)`. Reset the flag where the inner loop used to start (`if (j == 0) done = false;`). |

3. Re-run the full rule table on the rewritten code; every rule must PASS (or N/A) before you report success.
4. Output a short list of changes, one per failing rule (rule number → what you changed and why it preserves behavior), then the **complete fixed function** (not just the changed lines) in a fenced code block:
   ````
   ```cpp
   // fixed code here
   ```
   ````
5. Then output the machine-readable verdict marker:
   ```
   FLATTENABLE_VERDICT: FIXED_FLATTENABLE
   ```
   or if you cannot fix it:
   ```
   FLATTENABLE_VERDICT: COULD_NOT_FIX
   ```

**Confirm with synthesis when possible.** A successful flatten logs
`INFO: [HLS 200-2259] Flattening a loop nest with inner loop '<inner>' and outer loop '<outer>' ... successfully.`
and the csynth loop table shows one merged loop named `<outer>_<inner>`. If neither appears, the nest was not flattened.

---

## Behavioral Constraints
- **Do not** add extra rules or heuristics beyond the rules and definitions above.
- **Do not** speculate about optimization intent or suggest code changes unless the user asked to fix, convert, or make the nest flattenable (see [Making a Nest Flattenable](#making-a-nest-flattenable)).
- Evaluate rules **in order** (1 → 9) and **stop at the first failure** for the verdict reason, but still report all rules in the table.
- When multiple loop nests exist, analyze **each nest independently**.
- For loop nests deeper than 2 levels, apply Rules 2–9 to **every adjacent pair** of outer/inner loops in the nest hierarchy, and apply Rule 1 to every loop.
