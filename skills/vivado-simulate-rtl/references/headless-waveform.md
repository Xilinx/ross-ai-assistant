<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Headless Waveform Evidence

Produce and compare waveform evidence without a GUI. This keeps diagnosis
working on batch hosts with no display, and it keeps a viewer or rendering
defect from blocking a verdict.

## Capture

Log a focused object set before advancing simulation, then run the bounded
test. `log_wave` controls what enters the database; `add_wave` only controls
display and is unnecessary in batch.

For a portable, tool-independent trace, dump VCD instead of or alongside the
native database:

```tcl
open_vcd <absolute-path>/<target>.vcd
log_vcd [get_objects <focused-signal-set>]
run <bounded-duration>
flush_vcd
close_vcd
```

Verify every artifact exists and is non-empty before citing it. A waveform
path is not evidence until it has been opened or parsed.

## Compare programmatically

VCD is plain text, so two runs can be compared directly: baseline versus
current, one simulator versus another, or one tool release versus another.

Compare like for like. The signal set, hierarchy names, timescale, and time
window must match, and the runs must use the same seed and stimulus. Report
the first divergence time and the signal that diverged; later differences are
usually consequences of it.

Expect benign differences that are not defects: unlogged versus logged
objects, X-propagation before initialization, and delta-cycle ordering within
the same simulation time. Compare at settled points rather than mid-delta
unless the question is specifically about scheduling.

## When the GUI is the problem

If a waveform viewer, zoom rendering, or ILA import misbehaves, do not accept
it as a simulation failure. Re-derive the same fact from the batch transcript
and the dumped trace. A display defect that does not change the logged values
is cosmetic, and the functional verdict stands on the transcript and terminal
marker.

Value queries also work without a viewer:

```tcl
get_value -radix hex <object>
report_objects <scope>
```

## Uses

- Regression evidence in CI where no display exists.
- Golden-versus-current comparison after a tool upgrade, alongside
  [version-bisect.md](version-bisect.md).
- Cross-simulator value comparison feeding
  [cross-simulator-arbitration.md](cross-simulator-arbitration.md).

## Prohibited

- Presenting a waveform as proof of correctness. A trace shows behavior; the
  checker and terminal marker decide PASS.
- Recursively dumping the entire design by default. It is slow, large, and
  obscures the causal signals.
- Comparing traces captured with different logging scope and calling the
  differences a regression.
