---
name: hls-pipe-decomp
description: 'Decompose a Vitis HLS pipelined region that accesses many streams into a linear chain of smaller sub-pipelines inside a dataflow region, to reduce pipeline-control complexity and improve timing. Keywords: pipeline, dataflow, stream, FIFO, timing, frequency, split pipeline, decompose'
license: MIT
argument-hint: "[<TOP_FUNCTION — function to decompose, or file:LOOP_LABEL>]"
metadata:
  author: "Zayn He"
  version: "1.0.0"
---
<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Skill: hls-pipe-decomp

Refactor a Vitis HLS pipelined region with many `hls::stream<>` / `m_axi` ports into a linear chain of smaller sub-pipelines under `#pragma HLS dataflow`. Each sub-pipeline has simpler pipeline-control logic and meets timing more easily.

Reference: Vitis HLS User Guide (UG1399) — Dataflow Optimization chapter

## When to use

Trigger on explicit `/hls-pipe-decomp` OR on phrases like "split this pipeline", "lots of streams in one pipelined loop", "control signals on critical path", "FIFO/MAXI status in logic levels". Input from the user: a function name + file, OR a `<file>:<LOOP_LABEL>` pair.

**Refuse and explain** if the candidate function:
- Is neither a pipelined function nor contains a pipelined inner loop (i.e. no `#pragma HLS PIPELINE` on the function and none on any inner loop).
- Touches ≤ 4 streaming ports total (decomposition unlikely to help).
- Contains a **feedback FIFO** (a local `hls::stream<>` both read AND written inside the pipelined region — function or loop body). HLS accepts this pattern, but this skill does not transform it.

## Invariants (MUST / MUST NOT)

1. **MUST** form a linear chain. No fork-join. Exactly one stream between each pair of adjacent stages.
2. **MUST** forward the full upstream packet through middle stages even if only part is consumed. Last stage may drop the forwarding output. Filtering in the middle introduces bubbles.
3. **MUST** preserve the outer function signature exactly. Callers must not need to change.
4. **MUST NOT** regress II. Each sub-pipeline's II ≤ the original. If you can't meet this, recurse (further-decompose the offending stage) instead of accepting the regression.
5. **MUST NOT** access the same RAM from two sub-stages. If detected, refuse the split and ask the user to choose which stage keeps the RAM.
6. Intermediate FIFO depth = **2** (the default RAW latency is 1). Do not enlarge unless the user explicitly asks.
7. Use a neutral typedef suffix (e.g. `_pkt`).
8. New helpers go in the **same file** as the original function (above its definition).

## Workflow

Confirm with the user at each phase boundary. Skip per-phase confirmation only if the user says `auto` or `proceed without asking`.

1. **Locate.** Read the file(s); find the target function.
2. **Analyze.** Enumerate every `hls::stream<>`, `m_axi`/pointer, RAM access, scalar used in addressing, the existing `#pragma HLS PIPELINE II=?`, and the trip count (static or stream/scalar-controlled). Classify each stream as **input** or **output**. **If any local stream is both read AND written inside the pipelined region (function or loop body), stop and refuse** (feedback FIFO; out of scope). Report inventory; get sign-off.
3. **Propose chain.** Default: Read → Process → Write. If Read or Write would still touch > ~8 narrow streams or > ~4 wide MAXI ports, propose further-decomposing that part (see *The core transform* below). Render the proposal as `A → (s1) → B → (s2) → C → … → Z` with the stream count each stage will touch. Get sign-off.
4. **Design packet.** For each intermediate stream, define a packet type carrying every field that crosses the stage boundary:
   - Prefer a `struct` with `#pragma HLS aggregate` (or `hls::vector<T, N>` when the payload is N homogeneous elements) — cleaner than manual bit-packing. Fall back to a packed `ap_uint<>` only when a struct is awkward.
   - Dynamic trip count seen by downstream → add a 1-bit end-of-stream flag field (e.g. a `bool` in the struct).
   Show the packet layout. Get sign-off.
5. **Generate helpers.** Insert new helper functions into the same file, above the original. Each helper: takes its intermediate streams by reference; inner loop marked `#pragma HLS PIPELINE II=1` (or matching original II); `#pragma HLS UNROLL` on per-stream inner loops, as the original did; **forwards** the upstream packet (except for the last stage in a chain). Use a template parameter (`<int start, int end>`, `<int bank>`, `<int GROUP_ID>`) when a stage handles a contiguous range/bank/group.
6. **Wire dataflow region.** Replace the original function body with `#pragma HLS dataflow` + intermediate stream declarations + the chain of helper calls. Preserve the outer signature exactly.
7. **Add stream-depth pragmas.** `#pragma HLS STREAM variable=<name> depth=2` on every intermediate stream. Do not enlarge unless the user asks.
8. **(Optional) Recurse.** If a sub-stage still has too many streams, apply steps 3-7 to it. A Process stage may itself become a `#pragma HLS dataflow` containing one sub-process per resource (e.g. one per DDR channel).
9. **Summary report.** Emit a manual-verification checklist: stages created, intermediate streams + depths, new typedefs, "outer signature preserved? (yes is expected)", "II preserved? (vitis_hls verifies)", "any RAM shared across new stages? (flag if yes)", "next: csim → csynth_design → check II → check timing", and a note that dataflow control logic itself may become the new bottleneck.

This skill does NOT run `vitis_hls`, `csim`, `csynth`, or any synthesis tool. The user verifies II and timing.

## The core transform

Pack the inputs into a packet, then build a linear chain of stages connected by
exactly one stream each. Every stage reads a packet, handles only its own subset
of the work, and forwards the full packet downstream; the last stage does not
forward. Two independent choices specialize each stage:

- **Subset selection** — a static range of ports (`for j in [START,END]` unrolled),
  OR a runtime tag carried in the packet (`if (tag's group == THIS_STAGE)`).
- **Termination** — a static loop bound when the trip count is known, OR a 1-bit
  end-of-stream flag field in the packet when it is dynamic.

A sub-stage that still has too many ports is decomposed the same way recursively
(this is how a 2D banks × slots array is handled — range-split applied twice).

Example 1 — *static range, fan-in*: each stage reads a range of input streams and packs them.
```cpp
struct AddrPkt { unsigned addr[N]; };   // #pragma HLS aggregate variable=...
template <int START, int END>
void read_range(hls::stream<unsigned> in[N],
                hls::stream<AddrPkt>& prev,   // omit on the first stage
                hls::stream<AddrPkt>& out) {
  for (int i = 0; i < TRIP; i++) {
#pragma HLS pipeline II=1
    AddrPkt p = prev.read();                  // forward upstream packet
    for (int j = START; j <= END; j++) {
#pragma HLS unroll
      p.addr[j] = in[j].read();
    }
    out.write(p);
  }
}
// chain: read_range<0,7>(in, s0); read_range<8,15>(in, s0, s1); ...
```

Example 2 — *runtime tag, fan-out*: each stage reads one packet stream and routes to a subset of the output streams by a runtime id.
```cpp
struct Pkt { ap_uint<64> data; ap_uint<6> id; };   // #pragma HLS aggregate variable=...
template <int GROUP>
void route_group(hls::stream<Pkt>& in,
                 hls::stream<Pkt>& out,            // omit on the last stage
                 hls::stream<ap_uint<64> > lane[4]) {
  for (int i = 0; i < TRIP; i++) {
#pragma HLS pipeline II=1
    Pkt p = in.read();                 // single input stream
    if ((p.id >> 2) == GROUP)          // does this packet belong to my group?
      lane[p.id & 3].write(p.data);    // route to one of my 4 output lanes
    out.write(p);                      // forward ALL packets — do not filter (last stage drops this)
  }
}
// chain: pack(ddr, s0); route_group<0>(s0,s1,lane); route_group<1>(s1,s2,lane); ...
```

For a dynamic trip count, drive the loop from the packet's end-of-stream flag
instead of a fixed bound.

## Common failure modes

| Situation | Response |
|---|---|
| Function not found in file | Ask user to confirm name/path; offer fuzzy matches. |
| Function already in dataflow | Show current structure; ask if user wants to further-decompose a sub-stage. |
| Target is a pipelined loop nested in a larger function | Extract it into its own function first, then proceed. |
| Feedback FIFO in the pipelined region (stream read AND written inside the function or loop body) | Refuse; out of scope. |
| Stream type uses an opaque custom typedef | Read the header(s); if still ambiguous, ask user for the underlying width. |
| Dynamic trip count and downstream needs it | Use a 1-bit end-of-stream flag in the packet; call it out in the packet design. |
| Same RAM accessed from two intended stages | Refuse the split; ask user which stage keeps the RAM. |
| Original signature has unusual types (e.g. `T (&arr)[N]`) | Preserve verbatim. Do not "improve" them. |

## Success criteria

Done when: the diff was applied, outer signature unchanged, the chain matches what the user signed off on, no fork-join, no RAM shared across stages, and the summary checklist was emitted. II/timing verification is the user's responsibility — if they report II regression, deadlock, or mismatch after `csynth_design`, walk back through steps 4-7 with them; the usual bugs are missing forwarding, mis-sized intermediate FIFO, or accidentally-shared RAM.

## Machine-Readable Verdict

When you have completed the pipeline decomposition, end your response with:
PIPEDECOMP_VERDICT: DECOMPOSED
