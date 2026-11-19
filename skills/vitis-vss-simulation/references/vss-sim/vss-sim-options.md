---
name: vss-sim-options
description: >
  The AIE options-file directives that work in the VSS flow (the AIE model runs inside
  the RTL simulator), and the switches that do not.
metadata:
  version: "1.0.0"
---

# AIE options-file directives — VSS flow reference

In the VSS flow the AIE array is simulated by a model inside the RTL simulator that
`simulate.sh` launches. No `aiesimulator` process runs, so its command-line switches do not
apply. The model reads an **options file**, `aiesim_options.txt` (one `KEY=VALUE` directive per
line), pointed to by the **`AIESIM_OPTIONS`** environment variable, live in the same shell that
runs `simulate.sh`. Only the `KEY=VALUE` directives below take effect; anything else in the
file, such as `--dump-vcd=bar`, is ignored without any message. Creating the file and setting
the variable is described in [aie-sim-env.md](aie-sim-env.md).

## Directives

| Directive | Purpose |
|---|---|
| `AIE_DUMP_VCD=foo` | Dump the VCD to `foo.vcd` (the model appends `.vcd`). |
| `AIE_DUMP_VCD_ROW=(r0,r1)` / `AIE_DUMP_VCD_COL=(c0,c1)` | Narrow the VCD to a core row / column range. |
| `AIE_DUMP_VCD_CORE` / `_SHIM` / `_MEM` / `_IO` / `_DMA` `=true|false` | Include/exclude those module classes in the VCD (UG1076). `_MEM` had no effect in a test run on a VC1902 (AIE1) design. |
| `AIE_DUMP_VCD_INTERVAL=(start,end)` | Restrict VCD capture to a time interval. |

The `AIE_DUMP_VCD_*` directives only **narrow** what a VCD dump captures; they do
not turn the dump on by themselves.

**No cycle timeout.** `AIE_SIM_TIMEOUT=<cycles>` has no effect in this flow: a run with
`AIE_SIM_TIMEOUT=1000` still ran to the end of the testbench (2 us). The length of the run is
set by the simulator time (`SIM_RUNTIME`, see [../input.md](../input.md)).

---

Directive semantics for `AIE_DUMP_VCD_*` are from **UG1076** (AI Engine environment).
