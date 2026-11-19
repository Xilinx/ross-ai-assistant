<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Chaining Patterns Reference

VFS components are freely composable — the output `varray` from one component feeds directly as input to the next. This enables functional verification of multi-component pipelines (AIE + HLS, HLS + HDL IP, full RF chains).

---

## Basic Chaining Pattern

```python
import vfs
import varray as va
import numpy as np

# Instantiate each component
comp_a = vfs.aieGraph(input_file="./src/frontend.cpp", include_paths=["src/"])
comp_b = vfs.hlsKernel(input_files="./src/process.cpp", hls_function="process")
comp_c = vfs.aieGraph(input_file="./src/backend.cpp", include_paths=["src/"])

# Chain: output of one → input of next
stage1_out = comp_a.run(input_data)
stage2_out = comp_b.run(stage1_out)
final_out = comp_c.run(stage2_out)

# Compare final output against golden
result = np.asarray(final_out)
```

```matlab
comp_a = vfs.aieGraph(input_file="./src/frontend.cpp", include_paths="src/");
comp_b = vfs.hlsKernel(input_files="./src/process.cpp", hls_function="process");
comp_c = vfs.aieGraph(input_file="./src/backend.cpp", include_paths="src/");

stage1_out = comp_a.run(input_data);
stage2_out = comp_b.run(stage1_out);
final_out = comp_c.run(stage2_out);

result = double(final_out);
```

---

## AIE + HLS Pipeline (Data Mover Pattern)

A common Versal design pattern: HLS kernels act as data movers (PL) feeding an AIE graph.

```python
"""Functional verification: HLS data mover → AIE processing → HLS data mover"""
import vfs
import varray as va
import numpy as np

# HLS data mover (input side)
input_dm = vfs.hlsKernel(
    input_files="./pl/input_dma.cpp",
    hls_function="input_dma"
)

# AIE graph (core processing)
aie_core = vfs.aieGraph(
    input_file="./aie/signal_graph.cpp",
    include_paths=["./aie/"]
)

# HLS data mover (output side)
output_dm = vfs.hlsKernel(
    input_files="./pl/output_dma.cpp",
    hls_function="output_dma"
)

# Prepare test data
raw_input = va.array(np.random.randint(-32768, 32767, size=1024, dtype=np.int16))

# Execute pipeline
dm_out = input_dm.run(raw_input)
aie_out = aie_core.run(dm_out)
final = output_dm.run(aie_out)

# Verify
result = np.asarray(final)
print(f"Pipeline output: {len(result)} samples")
```

---

## RF Signal Chain (ADC → Processing → DAC)

Full RF loopback: ADC samples through baseband processing to DAC output. The converters exchange a per-channel, per-band dict, so process each band array between them.

```python
"""Functional verification: RF loopback (vrfADC → per-band processing → vrfDAC)"""
from vfs.vrfADC import vrfADC
from vfs.vrfDAC import vrfDAC
import varray as va
import numpy as np

adc = vrfADC(vrfADC.getDefaultConfig())
dac = vrfDAC(vrfDAC.getDefaultConfig())

# --- ADC input: one real va.double array per channel (14-bit signed range) ---
n = np.arange(4096)
samples = va.array(np.round(4000 * np.cos(2 * np.pi * 0.1 * n)), va.double)
baseband = adc.run({"chan": [samples] * 4})

# --- Baseband processing: replace each band with a processed va.cdouble array ---
for ch in baseband["chan"]:
    ch["band"] = [va.array(np.asarray(band) * 0.5, va.cdouble) for band in ch["band"]]

# --- DAC: one real va.double array per channel ---
analog_out = dac.run(baseband)
result = np.asarray(analog_out["chan"][0])
print(f"RF loopback: {len(result)} samples on channel 0")
```

---

## HDL IP Pipeline (FFT → Filter → IFFT)

Chain multiple HDL IPs for frequency-domain processing.

```python
"""Functional verification: Frequency-domain filtering (FFT → multiply → IFFT)"""
from vfs.vrfFFT import vrfFFT
import varray as va
import numpy as np

NFFT = 1024

# --- Forward FFT ---
# Scaled mode with natural-order output, so bin indices match np.fft.
# (Unscaled mode, data_width_out=31, always returns bit-reversed order.)
fft_config = vrfFFT.getDefaultConfig()
fft_config.data_width_in = 16
fft_config.data_width_out = 16
fft_config.reorder_output = 1
fwd_fft = vrfFFT(fft_config)

# --- Inverse FFT (separate instance) ---
ifft_config = vrfFFT.getDefaultConfig()
ifft_config.data_width_in = 16
ifft_config.data_width_out = 16
ifft_config.reorder_output = 1
inv_fft = vrfFFT(ifft_config)

# --- Input signal ---
t = np.arange(NFFT)
signal = np.cos(2 * np.pi * 64 * t / NFFT) + 0.5 * np.cos(2 * np.pi * 200 * t / NFFT)
scale = 2 ** 14
signal_q = np.round(signal * scale) / (2**15)
data_in = va.array(signal_q + 0j, va.cdouble)

# --- Forward FFT ---
fwd_ctrl = vrfFFT.Control(
    scaling_factor=1.0/NFFT,
    direction=1,
    point_size=int(np.log2(NFFT)),
)
freq_domain, _ = fwd_fft.run(data_in, fwd_ctrl)

# --- Apply frequency-domain mask (zero out high frequencies) ---
freq_data = np.asarray(freq_domain, dtype=np.complex128)
mask = np.zeros(NFFT)
mask[50:75] = 1.0  # Keep only bins 50-74
mask[NFFT-75:NFFT-50] = 1.0  # Symmetric
filtered_freq = va.array(freq_data * mask, va.cdouble)

# --- Inverse FFT ---
inv_ctrl = vrfFFT.Control(
    scaling_factor=1.0,
    direction=0,  # Inverse
    point_size=int(np.log2(NFFT)),
)
time_domain, _ = inv_fft.run(filtered_freq, inv_ctrl)

result = np.asarray(time_domain, dtype=np.complex128)
print(f"Filtered output: {len(result)} samples, peak={np.max(np.abs(result)):.4f}")
```

---

## Streaming / Stateful Pipeline

Process data in chunks while components maintain state across calls.

```python
"""Functional verification: Streaming pipeline with state persistence"""
import vfs
import varray as va
import numpy as np

# Components
fir = vfs.hlsKernel(input_files="./src/fir.cpp", hls_function="fir_stream")
detector = vfs.hlsKernel(input_files="./src/detect.cpp", hls_function="peak_detect")

# Process in chunks (simulates real-time streaming)
CHUNK_SIZE = 256
NUM_CHUNKS = 10
all_outputs = []

for i in range(NUM_CHUNKS):
    # Generate chunk (e.g., from file or continuous source)
    chunk = va.array(np.random.randn(CHUNK_SIZE).astype(np.float32))

    # Pipeline processes incrementally; state persists between chunks
    filtered = fir.run(chunk)
    detected = detector.run(filtered)
    all_outputs.append(np.asarray(detected))

# Concatenate all chunks
full_output = np.concatenate(all_outputs)
print(f"Processed {NUM_CHUNKS} chunks → {len(full_output)} total samples")

# For a new independent test, create new instances (hlsKernel has no reset())
fir = vfs.hlsKernel(input_files="./src/fir.cpp", hls_function="fir_stream")
detector = vfs.hlsKernel(input_files="./src/detect.cpp", hls_function="peak_detect")
```

---

## Complete MATLAB Example: AIE + HLS Chained Pipeline

```matlab
%% Functional verification: AIE preprocessing → HLS postprocessing
aie_front = vfs.aieGraph( ...
    input_file="./aie/front_end.cpp", ...
    include_paths="./aie/");

hls_back = vfs.hlsKernel( ...
    input_files="./pl/back_end.cpp", ...
    hls_function="back_end_process");

% Generate test data
N = 512;
input_signal = varray.cint16(int16(randi([-1000 1000], 1, N)), ...
                             int16(randi([-1000 1000], 1, N)));

% Chain execution
aie_out = aie_front.run(input_signal);
final_out = hls_back.run(aie_out);

% Verify
result = double(final_out);
assert(length(result) > 0, 'Pipeline produced no output');
fprintf('PASS: Pipeline output %d samples\n', length(result));
```

---

## Integration with Existing Reference Model

When the user has an existing Python/MATLAB reference model, inject VFS components alongside:

### Python: Add VFS to Existing Script

```python
"""
EXISTING reference model script — VFS integration added below.
Original code generates golden_output from reference algorithm.
"""
import os
import numpy as np
# ... existing reference model code ...
# golden_output = reference_algorithm(input_signal)

# ===== VFS INTEGRATION (added) =====
import vfs
import varray as va

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Wrap the implementation under test
kernel = vfs.hlsKernel(
    input_files=os.path.join(SCRIPT_DIR, "src/my_kernel.cpp"),
    hls_function="my_kernel"
)

# Use SAME input stimulus as reference model
vfs_input = va.array(input_signal, dtype=va.int16)
vfs_output = kernel.run(vfs_input)
hw_result = np.asarray(vfs_output).flatten()

# Compare implementation vs reference in whole LSBs, real and imaginary apart
# (see "Comparison Logic Templates" in SKILL.md)
MAX_LSB_TOLERANCE = 0  # 0 = require bit-exact
FRAC_BITS = 0          # output fraction length: 0 for int/cint, F for fi(s, W, F)

def to_lsb(x, check_whole=False):
    x = np.asarray(x).flatten().astype(np.complex128) * 2.0 ** FRAC_BITS
    if check_whole and not np.allclose(x, np.round(x), rtol=0, atol=1e-6):
        raise AssertionError("FAIL: output is not on whole LSBs; set FRAC_BITS to its fraction length")
    return np.round(x.real).astype(np.int64), np.round(x.imag).astype(np.int64)

ref_re, ref_im = to_lsb(golden_output)
act_re, act_im = to_lsb(hw_result, check_whole=True)
assert act_re.size and act_re.size == ref_re.size, \
    f"FAIL: expected {ref_re.size} outputs, got {act_re.size}"
max_err_lsb = int(max(np.max(np.abs(act_re - ref_re)), np.max(np.abs(act_im - ref_im))))
print(f"Max mismatch: {max_err_lsb} LSBs")
assert max_err_lsb <= MAX_LSB_TOLERANCE, \
    f"FAIL: {max_err_lsb} LSBs exceeds tolerance ({MAX_LSB_TOLERANCE})"
print("PASS: Implementation matches reference model")
```

### MATLAB: Add VFS to Existing Script

```matlab
%% EXISTING reference model — VFS integration added below
% ... existing reference code ...
% golden_output = reference_algorithm(input_signal);

%% ===== VFS INTEGRATION (added) =====
script_dir = fileparts(mfilename('fullpath'));
kernel = vfs.hlsKernel( ...
    input_files=fullfile(script_dir, 'src/my_kernel.cpp'), ...
    hls_function="my_kernel");

% Use SAME stimulus
vfs_input = varray.int16(int16(input_signal));
vfs_output = kernel.run(vfs_input);

% Compare in whole LSBs, real and imaginary apart (see SKILL.md)
MAX_LSB_TOLERANCE = 0;  % 0 = require bit-exact
FRAC_BITS = 0;          % output fraction length: 0 for int/cint, F for fi(s, W, F)
scale = 2^FRAC_BITS;
reference = double(golden_output(:)) * scale;
actual = double(vfs_output(:)) * scale;
assert(~isempty(actual) && numel(actual) == numel(reference), ...
    sprintf('FAIL: expected %d outputs, got %d', numel(reference), numel(actual)));
assert(all(abs(actual - round(actual)) <= 1e-6), ...
    'FAIL: output is not on whole LSBs; set FRAC_BITS to its fraction length');
err = [round(real(actual)) - round(real(reference)); ...
       round(imag(actual)) - round(imag(reference))];   % not "error": that hides error()
max_err_lsb = max(abs(err));

fprintf('Max mismatch: %d LSBs\n', max_err_lsb);
assert(max_err_lsb <= MAX_LSB_TOLERANCE, ...
    sprintf('FAIL: %d LSBs exceeds tolerance (%d)', max_err_lsb, MAX_LSB_TOLERANCE));
fprintf('PASS: Implementation matches reference\n');
```

---

## Key Rules for Chaining

1. **varray is the glue** — components take and return varrays (vrfADC and vrfDAC wrap them in a per-channel dict)
2. **Type compatibility** — ensure the output type of component N matches the expected input type of component N+1 (use `getOutputSpec()` / `getInputSpec()`)
3. **Buffer alignment** — if component B has a buffer interface expecting 1024 samples, ensure component A's output is a multiple of 1024. For `vrfADC`, each `run()` input length should be a multiple of `decim_rate`: an unaligned length warns and still returns data, and its later outputs can differ from one `run()` of the whole stream (see [hdl-ip-api.md](./hdl-ip-api.md))
4. **State management** — start each independent test run from fresh state: create new `hlsKernel`, `aieGraph`, `xfft` and `vrfFFT` instances, and call `reset()` on IPs that provide it (`firCompiler`, `vrfChannelizer`, `vrfADC`, `vrfDAC`, `pbChannelizer`, `pbArbResampler`, `versalLDPC`). A `versalLDPC` created with `init_code` loses that code on `reset()`, so call `addLdpcCode(0, path)` again before the next decode (see [hdl-ip-api.md](./hdl-ip-api.md#custom-codes))
5. **Order matters** — chain components in the same order as the hardware dataflow
