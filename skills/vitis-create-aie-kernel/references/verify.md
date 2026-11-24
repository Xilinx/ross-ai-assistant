<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Formats, commands and behaviours below were observed with Vitis 2026.2 (x86simulator, aiesimulator)
     on xcvc1902-vsva2197-2MP-e-S (AIE) and xcve2802-vsvh1760-2MP-e-S (AIE-ML). -->
# Verify: Golden Model, Stimulus, Simulation, Comparison

Prove that the kernel implements the numeric contract (`algorithm.md` Step 2). The helper
`scripts/plio.py` writes, reads and compares PLIO text files. It uses only the standard library and
runs on Python ≥ 3.6.

## 1. Golden model (`golden.py` in the project directory)

- Implement the **contract**, not the kernel. Do the arithmetic on exact Python integers, then apply
  shift/round/saturate once, at the point where the contract says the kernel does it.
- Generate `ITER` graph iterations (= `run(ITER)` in the app). Model state across iterations: margin and
  delay lines start at zero, then carry over.
- Include corner values (max, min, ±1, 0) in the first samples. Use a fixed seed.
- numpy is optional. For float, round every intermediate result to float32 with `struct`.

```python
import random, struct, subprocess, sys
PLIO = "<skill>/scripts/plio.py"                    # path to this skill's scripts/plio.py
ITER, N, TAPS, S = 4, 256, [500, -1500, 2500, 9000, 12000, 3000, -2000, 1000], 15

def srs(v, s, lo=-32768, hi=32767):                 # shift, round (conv_even), saturate
    q, r = divmod(v, 1 << s)
    if s and (r > (1 << (s - 1)) or (r == (1 << (s - 1)) and q & 1)): q += 1
    return max(lo, min(hi, q))

f32 = lambda v: struct.unpack("f", struct.pack("f", v))[0]   # round to float32

random.seed(1)
x = [random.randint(-32768, 32767) for _ in range(ITER * N)]
x[:4] = [32767, -32768, 1, -1]
xp = [0] * (len(TAPS) - 1) + x                     # zero history before iteration 0
y = [srs(sum(TAPS[k] * xp[n + len(TAPS) - 1 - k] for k in range(len(TAPS))), S) for n in range(len(x))]

def write(vals, typ, plio, path):
    open("tmp.csv", "w").write(",".join(map(repr, vals)))
    subprocess.check_call([sys.executable, PLIO, "write", "--type", typ, "--plio", str(plio),
                           "--in", "tmp.csv", "--out", path])
write(x, "int16", 64, "data/sig_i.txt")              # one file per input port: data/<port>.txt
write(y, "int16", 64, "data/golden_o.txt")           # one golden per output port
```

Rounding modes other than `conv_even`: `floor` → `v >> s`. `symmetric_inf` → round half away from
zero. `positive_inf` → `(v + half) >> s`. Use the table in `algorithm.md`.

## 2. PLIO text format

One line = one PLIO beat. Values on a line are separated by spaces. A complex sample is `re im`.

| Type | plio_32 | plio_64 | plio_128 |
|---|---|---|---|
| int8 / uint8 | 4 per line | 8 | 16 |
| int16 | 2 | 4 | 8 |
| int32 | 1 | 2 | 4 |
| cint16 | 1 sample (`re im`) | 2 samples | 4 samples |
| cint32 | illegal | 1 sample | 2 samples |
| float | 1 | 2 | 4 |
| cfloat | illegal | 1 sample | 2 samples |
| bfloat16 (AIE-ML) | 2 | 4 | 8 |

- Integers as decimal. Floats as decimal (`repr()`, full precision). bfloat16 is written as a decimal
  float that is exactly representable in bfloat16 (`plio.py` rounds for you).
- The input file must hold exactly `ITER x NSAMP` samples per port. A **short file does not fail**:
  x86sim prints `x86simulator: Detected deadlock … (exhausted data file)`, still exits 0 and writes a
  truncated output. Always compare lengths (`plio.py compare` does).
- Paths in `input_plio::create(..., "data/<port>.txt")` are relative to the run directory. Outputs go to
  `x86simulator_output/data/<port>.txt` and `aiesimulator_output/data/<port>.txt`.

Output file differences:

| | x86simulator | aiesimulator |
|---|---|---|
| Values | same layout as input, trailing space | same |
| Floats | `%.9e` style (`-2.488856964e+02`) | same |
| Timestamps | none | `T 681600 ps` / `T 688 ns` line before each beat |
| TLAST | none for buffer ports | `TLAST` line before the last beat of each output buffer (one per iteration) |

`plio.py read` and `compare` drop `T …` and `TLAST` lines.

## 3. Build and run x86sim

From the project directory, with Vitis `settings64.sh` sourced:

```bash
make x86com x86sim    # x86sim compile into Work_x86/, then x86simulator; output in ./log
echo $?               # 0 = compiled and simulated without deadlock
```

The Makefile (`makefile.md`) uses `pipefail`, so `make` exits non-zero on a compile error, and
`x86sim` fails when the log contains `Detected deadlock`. Still read the log before comparing:

```bash
grep -nE "ERROR|error:" log | grep -v "Executing Cmd" | head     # first compile error, if any
ls x86simulator_output/data/                                     # one file per output port
```

## 4. Compare

```bash
python3 <skill>/scripts/plio.py compare --type int16 --abs-tol 0 \
    x86simulator_output/data/sig_o.txt data/golden_o.txt
```

The exit status is 0 on PASS and 1 on FAIL. It prints the first mismatches (sample index, re/im), the
count, and the max absolute error.

| Contract | Tolerance | Flags |
|---|---|---|
| Integer, no rounding shift (add, select, copy, layout) | exact | `--abs-tol 0` |
| Integer with a shift, golden models the same rounding mode | exact (observed) | `--abs-tol 0` |
| Integer with a shift, golden rounds differently / unknown mode | ≤ 1 LSB | `--abs-tol 1` |
| float, AIE | relative 1e-5 | `--rel-tol 1e-5 --abs-tol 1e-6` |
| float mul on AIE-ML (bfloat16 emulation) | relative 1e-5 (observed about 1e-7) | `--rel-tol 1e-5 --abs-tol 1e-6` |
| bfloat16 output, golden rounds with RNE and the kernel sets `conv_even` | print precision only | `--rel-tol 1e-8` |
| bfloat16 with the rounding mode left at power-up (`floor`) | 1 bfloat16 ULP | `--rel-tol 0.008` |

A tolerance never hides a contract error. If integers differ by more than 1 LSB, or by exactly a power
of 2, it is a bug.

## 5. Self-repair loop (at most 5 attempts)

```text
attempt = 1
loop:
  build (make x86com) → on ERROR: look up the first error in troubleshooting.md, fix the source, attempt++
  sim   (make x86sim) → on deadlock / missing output: troubleshooting.md "Simulation", fix, attempt++
  compare            → on FAIL: classify the mismatch (table below), fix the kernel or the contract, attempt++
  PASS → done
  attempt > 5 → stop and report the last error, what was tried, and the current max error
```

| Mismatch pattern | Likely cause |
|---|---|
| Length mismatch | short input file, wrong `run(ITER)`, NSAMP not divisible by LANES, wrong output `dimensions` |
| All wrong from sample 0 | wrong algorithm/indices, reversed coefficients, wrong port order |
| Off by exactly 1 at ties | rounding mode differs between kernel and golden (`set_rounding` missing → `floor`) |
| Large values wrapped (sign flips near full scale) | saturation not set, `acc32` overflow (AIE-ML), plain vector add instead of accumulator |
| Error = 2^k pattern | wrong `SHIFT` / `from_vector` shift |
| First NTAPS-1 outputs wrong only | margin/delay-line initial state or offset (`d_start`) |
| Only on AIE-ML (passes on AIE) | `acc32` bit-cast, `abs_square` shift, lane count assumptions |
| Complex re/im swapped or interleaved wrong | `vector_cast` / zip order, golden writes `im re` |
| Every Nth block wrong | iterator advanced twice, or `lo/hi` sliding update missing |

Fix the kernel or the contract. Change the golden model only if the *contract* was wrong, and say so
in the summary.

## 6. Optional: hw compile, aiesim, cycle counts

```bash
make            # hw compile (v++ --target=hw) into Work/ and libadf.a; non-zero exit on error
make sim        # aiesimulator; output in aiesimulator_output/data/
```

- The x86 flow uses `Work_x86/` and the hw flow uses `Work/`, so you can run them in either order without
  `make clean`. `make sim` before `make` fails with "The work directory './Work' is not generated for
  target hw".
- The hw compile catches what x86sim does not: memory overflow, stream/cascade port limits, placement,
  DMA descriptor limits, illegal intrinsics.
- Compare the aiesim output with the same `plio.py compare` command (it drops T/TLAST lines).
- Throughput: the end of the aiesim log has a table `| plio | PLIO_<port> | IN/OUT | Throughput(MBps) |`.
- Per-iteration period: the difference between consecutive `T` stamps that precede `TLAST` in an output
  file. Example: 256 int16 per iteration, 243.2 ns between TLASTs = 1.05 GSa/s, PLIO-bound at 64 bit.
- Per-kernel cycles: `make profile` (aiesimulator `--profile`) writes `aiesimulator_output/profile_funct_<col>_<row>.txt`
  (cycles per function, including `run`) and `profile_instr_<col>_<row>.txt`. Loop II: the compiler's per-tile log
  under `Work/aie/<col>_<row>/`.
- Memory reports: `Work/reports/report_stack.txt`, `report_heap.txt`, `report_pm.txt`, `DMA_report.txt`,
  `FIFO_guidance_report.txt`.
