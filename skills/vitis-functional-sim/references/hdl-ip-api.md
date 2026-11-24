<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# HDL IP API Reference

Every HDL IP is set up once with a configuration and then driven with `run()`. Most start from `getDefaultConfig()`, change a few fields, and pass the result to the constructor. `firCompiler` is built from `firCompiler.Config(...)` instead. `pbChannelizer`, `pbArbResampler` and `versalLDPC` accept either form, so you can pass only the fields you change to `Config(...)`.

The FFTs, the channelizers and the LDPC decoder also take a `Control` object with per-call settings. Most IPs return a status or overflow result next to the data. Start from the example for your IP, because the details differ.

---

## xFFT (Versal FFT v9.1)

xFFT supports fixed-point and floating-point data and offers four architectures: Radix-4 burst, Radix-2 burst, pipelined streaming and Radix-2 Lite.

### Configuration

```python
from vfs.xfft import xfft

config = xfft.getDefaultConfig()
config.c_nfft_max = 10              # log2(max FFT size), e.g. 10 → 1024-point
config.c_arch = 3                   # 1=Radix-4, 2=Radix-2, 3=Pipelined, 4=Radix-2 Lite
config.c_use_flt_pt = 1             # 0=fixed-point, 1=pseudo floating-point, 2=native floating-point
config.c_input_width = 32           # 32 for floating-point; 8-34 for fixed-point
config.c_twiddle_width = 24         # 24-25 for pseudo float, 32 for native float, 8-34 for fixed-point
config.c_has_nfft = 0               # 0=fixed size, 1=runtime configurable size

fft_model = xfft(config)
```

For fixed-point (`c_use_flt_pt = 0`), every real and imaginary input value must be in [-1.0, 1.0) and should be quantized to `c_input_width` bits. Scaling is set with `c_has_scaling` (0 unscaled, 1 scaled) and `c_has_bfp` (0 fixed schedule, 1 block floating-point). The default is scaled with a fixed schedule, and it divides the result by the FFT size. For a 1024-point FFT with 16-bit input, the output matches `np.fft.fft(x) / 1024` within 2 LSBs. Pseudo floating-point (`c_use_flt_pt = 1`) is unscaled and matches `np.fft.fft(x)` to about 1e-5.

### Control & Execution

```python
import varray as va
import numpy as np

# Prepare complex input
signal = np.cos(2 * np.pi * 30 * np.arange(1024) / 1024)
data_in = va.array(signal + 0j, va.cdouble)

# Run forward FFT. With c_has_nfft=0, nfft must equal c_nfft_max.
ctrl = xfft.Control(nfft=10, direction=1)  # 1=forward, 0=inverse
output, block_exp, overflow = fft_model.run(data_in, ctrl)

result = np.asarray(output, dtype=np.complex128)
```

`block_exp` (an `int`) is meaningful only with block floating-point scaling. `overflow` (a `bool`) is valid only with a fixed scaling schedule.

### MATLAB Example

```matlab
config = vfs.xfft.getDefaultConfig();
config.c_nfft_max = 10;
config.c_arch = 3;
config.c_use_flt_pt = 1;
config.c_input_width = 32;
config.c_twiddle_width = 24;

fft_model = vfs.xfft(config);

% Generate signal
N = 1024;
signal = cos(2*pi*30*(0:N-1)/N);
data_in = varray.cdouble(complex(signal, zeros(1, N)));

% Run
ctrl = vfs.xfft.Control('nfft', 10, 'direction', 1);
[output, block_exp, overflow] = fft_model.run(data_in, ctrl);

result = double(output);
fprintf('FFT output: %d points\n', length(result));
```

### Key Config Fields

| Field | Default | Description |
|-------|---------|-------------|
| `c_nfft_max` | 10 | log2(max FFT size) |
| `c_arch` | 1 | Architecture: 1=Radix-4 burst, 2=Radix-2 burst, 3=Pipelined streaming, 4=Radix-2 Lite |
| `c_use_flt_pt` | 0 | 0=fixed-point, 1=pseudo floating-point, 2=native floating-point |
| `c_input_width` | 16 | Input word width (bits) |
| `c_twiddle_width` | 16 | Twiddle factor width |
| `c_has_scaling` | 1 | 0=unscaled, 1=scaled |
| `c_has_bfp` | 0 | 0=fixed scaling schedule, 1=block floating-point |
| `c_has_nfft` | 0 | Runtime-configurable FFT size |

Input length should be a multiple of the FFT size. Extra samples are buffered until the next `run()` completes a frame.

---

## vrfFFT (VRF FFT v1.2)

Fixed-point FFT, 8–4096 points. Supports scaled and unscaled modes.

### Configuration

```python
from vfs.vrfFFT import vrfFFT

config = vrfFFT.getDefaultConfig()
config.data_width_in = 16           # Input width: 16 or 18 bits
config.data_width_out = 16          # Output width: 16, 18 (scaled) or 31 (unscaled)
config.reorder_output = 1           # 1=natural order, 0=bit-reversed (scaled mode only)

fft = vrfFFT(config)
```

`reorder_output` is a `Config` field, fixed when the instance is created. Passing it to `Control(...)` raises an error. To compare natural and bit-reversed output, create two instances. Unscaled mode (`data_width_out=31`) always returns bit-reversed order.

### Control & Execution

```python
import varray as va
import numpy as np

NFFT = 1024
SCALING = 1.0 / NFFT

# Generate complex input (fixed-point range [-1, 1))
signal = np.cos(2 * np.pi * 64 * np.arange(NFFT) / NFFT)
scale = 2 ** (16 - 1)
signal_q = np.round(signal * scale / 2) / scale
data_in = va.array(signal_q + 0j, va.cdouble)

# Control
ctrl = vrfFFT.Control(
    scaling_factor=SCALING,
    direction=1,              # 1=forward, 0=inverse
    point_size=int(np.log2(NFFT)),  # log2(N)
)

# Run
output, overflow = fft.run(data_in, ctrl)
result = np.asarray(output, dtype=np.complex128)
print(f"Overflow: {np.any(overflow)}")
```

### MATLAB Example

```matlab
config = vfs.vrfFFT.getDefaultConfig();
config.data_width_in = 16;
config.data_width_out = 16;
config.reorder_output = 1;   % natural order
fft_model = vfs.vrfFFT(config);

NFFT = 1024;
t = (0:NFFT-1).';
signal = cos(2*pi*64*t/NFFT);
in_scale = 2^(16-1);
signal_q = round(signal * in_scale / 2) / in_scale;
data_in = varray.cdouble(complex(signal_q, zeros(NFFT, 1)));

ctrl = vfs.vrfFFT.Control('scaling_factor', 1/NFFT, ...
    'direction', 1, 'point_size', log2(NFFT));
[output, overflow] = fft_model.run(data_in, ctrl);

fprintf('vrfFFT: %d points, overflow=%d\n', length(output), any(overflow));
```

### Batch & Partial Frame Behavior

- Input > N points: auto-processes as multiple N-point FFTs
- Input < N points: buffered internally, output empty until N samples accumulated
- Stateful: maintains internal buffer across `run()` calls

---

## vrfChannelizer (VRF Channelizer v2.0)

Polyphase filter bank with FFT, two input streams and 7 operating modes.

### Configuration

```python
from vfs.vrfChannelizer import vrfChannelizer

config = vrfChannelizer.getDefaultConfig()
config.mode = 0                    # 0=Rx, 1=Tx, 2=Filter, 3=OS-Rx, 4=OS-Tx, 5=SSR-Tx, 6=SSR-Rx
config.data_width_in = 18          # 16 or 18
config.data_width_out = 18         # 16 or 18
config.fir_max_asym_len = 16       # 8, 16, 32 or 64

chan = vrfChannelizer(config)
```

### Control & Execution

```python
import varray as va
import numpy as np

# Control for 8 channels, 64 samples per channel.
# Filter mode accepts 1-8 channels; every other mode requires exactly 8.
control = chan.createControl(num_channels=8, num_samples_per_channel=64)

# createControl() leaves the filter all zeros, so the output is zero until
# you load coefficients. They are integers: here a 128-tap prototype lowpass
# scaled by 2**17. Keep the shape of control.fir_coeff.
n = np.arange(128)
h = np.sinc((n - 64) / 8) * np.hamming(128)
control.fir_coeff = np.round(h / h.sum() * 2**17).reshape(control.fir_coeff.shape)
control.fft_scaling = np.array([1.0])

# Input samples are integers too (18-bit here). Shape each input to
# getInputDims(); a single-stream mode leaves stream 1 empty.
in0_shape, in1_shape = chan.getInputDims()
k = 2
tone = 0.4 * 2**17 * np.exp(2j * np.pi * k * np.arange(np.prod(in0_shape)) / 8)
data_in = va.array((np.round(tone.real) + 1j * np.round(tone.imag)).reshape(in0_shape), va.cdouble)

out0, out1, overflow0, overflow1 = chan.run(data_in, va.array([], va.cdouble), control)

# out0 has shape (8, 64), but its samples are interleaved by channel:
# flatten it and reshape to (samples, channels), so column c is channel c.
per_channel = np.asarray(out0).reshape(-1, 8)
power = np.mean(np.abs(per_channel[8:]) ** 2, axis=0)
others = np.delete(power, k)
assert np.argmax(power) == k   # a tone at k/8 lands in channel k
assert 10 * np.log10(power[k] / others.max()) >= 30

for status in (overflow0, overflow1):
    for field in ("ovs_front", "fir_srs", "fir_gain", "ft", "ovs_end"):
        assert not np.any(np.asarray(getattr(status, field))), field
```

- Call `getInputDims()` after `createControl()`, and reshape each input to its matching shape. Pass an empty varray for an unused stream.
- Data and coefficients are integer values at the configured width. Fractional input such as `0.4 * exp(...)` gives an all-zero output.
- With the filter above, a tone at k/8 cycles per sample lands in channel k, and the other channels are at least 46 dB lower.
- Start with the values `createControl()` returns, and change one field at a time. Some invalid settings end the Python or MATLAB process instead of raising an error. Known cases are an input of the wrong shape and `control.fft_scaling=0.5`; use 0 or 1 for that field.
- Call `createControl()` once per instance. Calling it again on the same instance and then running ends the process. Creating a new instance after an earlier one was deleted (for example by rebinding the same variable) also ends it. To rerun with the same control, call `reset()` and reuse the instance. For different controls, create all the instances up front and keep them alive.
- `overflow0` and `overflow1` report overflow for each stream, with one array per processing stage (`ovs_front`, `fir_srs`, `fir_gain`, `ft`, `ovs_end`). Check that every array is all zeros.

### Operating Modes

| Mode | Name | Description |
|------|------|-------------|
| 0 | Standard Rx | Channelize wideband → narrowband channels |
| 1 | Standard Tx | Synthesize narrowband channels → wideband |
| 2 | Filter Only | Polyphase FIR without FFT |
| 3 | Oversampled Rx | Rx with oversampled subbands |
| 4 | Oversampled Tx | Tx with oversampled subbands |
| 5 | SSR Tx | Super Sample Rate transmit |
| 6 | SSR Rx | Super Sample Rate receive |

---

## firCompiler (FIR Compiler v7.3)

Multi-tap, multi-channel FIR filter. Maintains persistent state across calls (streaming mode).

### Configuration

```python
from vfs import firCompiler

config = firCompiler.Config(
    coeff=list(coefficients),     # Filter coefficients (list of floats)
    num_coeffs=128,               # Number of taps
    coeff_width=16,               # Coefficient word length (bits)
    coeff_fract_width=15,         # Coefficient fraction length
    data_width=16,                # Data word length
    data_fract_width=14,          # Data fraction length
)

fir = firCompiler(config)
```

### Execution

```python
import varray as va
import numpy as np

# 31-tap lowpass (windowed sinc)
n = np.arange(31)
coeff = 0.25 * np.sinc(0.25 * (n - 15)) * np.hamming(31)

fir_hw = firCompiler(firCompiler.Config(
    coeff=list(coeff),
    num_coeffs=31,
    coeff_width=16,
    coeff_fract_width=15,
    data_width=16,
    data_fract_width=14
))

# Test signal on the 16-bit data grid (14 fractional bits)
x = np.round(np.random.default_rng(1).uniform(-1, 1, 512) * 2**14) / 2**14

y_hw = np.asarray(fir_hw.run(va.array(x, va.double))).flatten()

# With the coefficients rounded to 15 fractional bits, the output is bit-exact
coeff_q = np.round(coeff * 2**15) / 2**15
y_ref = np.convolve(x, coeff_q)[:len(x)]
assert np.array_equal(y_hw, y_ref), f"FAIL: max error {np.max(np.abs(y_hw - y_ref))}"
print("PASS: bit-exact")
```

### MATLAB Example

```matlab
%% FIR Compiler: VFS vs MATLAB filter()
n = 0:30;
a = 0.25 * (n - 15);
s = ones(size(a));
s(a ~= 0) = sin(pi * a(a ~= 0)) ./ (pi * a(a ~= 0));   % sinc without toolboxes
w = 0.54 - 0.46 * cos(2*pi*n/30);                       % Hamming window
coeff = 0.25 * s .* w;                                  % 31-tap lowpass

fir_hw = vfs.firCompiler(vfs.firCompiler.Config( ...
    'coeff', coeff, ...
    'num_coeffs', 31, ...
    'coeff_width', 16, ...
    'coeff_fract_width', 15, ...
    'data_width', 16, ...
    'data_fract_width', 14));

% Test signal on the 16-bit data grid (14 fractional bits)
rng(1);
x = round((2 * rand(1, 512) - 1) * 2^14) / 2^14;

y_hw = double(fir_hw.run(varray.double(x)));
y_hw = y_hw(:).';

% With the coefficients rounded to 15 fractional bits, the output is bit-exact
y_ref = filter(round(coeff * 2^15) / 2^15, 1.0, x);
max_err = max(abs(y_ref - y_hw));
fprintf('Max error: %.4e\n', max_err);
assert(max_err == 0, 'FAIL: error %.4e', max_err);
fprintf('PASS\n');
```

### Coefficient Sets and Reload

With `coeff_sets` greater than 1, `coeff` holds every set back to back. `configSend` picks the active set, and `reloadSend` replaces one set when the filter was built with `reloadable=1`:

```python
import numpy as np
import varray as va
from vfs import firCompiler

# Two 4-tap coefficient sets, stored back to back; set 1 can be reloaded at run time
fir = firCompiler(firCompiler.Config(
    coeff=[0.5, 0.25, 0, 0,   0, 0, 0.125, -0.25],
    num_coeffs=4, coeff_sets=2, reloadable=1,
    coeff_width=16, coeff_fract_width=15, data_width=16, data_fract_width=14,
))
impulse = va.array([0.5] + [0.0] * 7, va.double)
basic = firCompiler.Pattern.P_BASIC

y = np.asarray(fir.run(impulse))                      # set 0 is active at start
assert list(y[:2]) == [0.25, 0.125]

fir.configSend(firCompiler.ConfigPacket(chanpat=basic, fsel=[1]))
y = np.asarray(fir.run(impulse))                      # set 1 from the next run()
assert list(y[2:4]) == [0.0625, -0.125]

# A reload only takes effect after a configSend() that selects the set
fir.reloadSend(firCompiler.ReloadPacket(fsel=1, coeff=[0.75, 0, 0, 0]))
fir.configSend(firCompiler.ConfigPacket(chanpat=basic, fsel=[1]))
y = np.asarray(fir.run(impulse))
assert y[0] == 0.375
```

```matlab
fir = vfs.firCompiler(vfs.firCompiler.Config('coeff', [0.5 0.25 0 0  0 0 0.125 -0.25], ...
    'num_coeffs', 4, 'coeff_sets', 2, 'reloadable', 1, ...
    'coeff_width', 16, 'coeff_fract_width', 15, 'data_width', 16, 'data_fract_width', 14));
imp = [0.5 zeros(1, 7)];
P = vfs.firCompiler.Pattern();
y0 = double(fir.run(varray.double(imp))).';
fir.configSend(vfs.firCompiler.ConfigPacket('chanpat', P.P_BASIC, 'fsel', 1));
y1 = double(fir.run(varray.double(imp))).';
fir.reloadSend(vfs.firCompiler.ReloadPacket('fsel', 0, 'coeff', [0.75 0 0 0]));
fir.configSend(vfs.firCompiler.ConfigPacket('chanpat', P.P_BASIC, 'fsel', 0));
y2 = double(fir.run(varray.double(imp))).';
assert(isequal(y0(1:2), [0.25 0.125]) && isequal(y1(3:4), [0.0625 -0.125]) && y2(1) == 0.375);
```

- `configSend` takes effect on the next `run()`. `fsel` holds one set index, or one per channel with `config_method=1`.
- `reloadSend` changes nothing on its own. Follow it with a `configSend`, as on the hardware.
- Without `reloadable=1`, `reloadSend` is silently ignored and the old coefficients stay in use.
- `reset()` clears the filter history and makes set 0 active again. Reloaded coefficients stay loaded.
- `getConfig()` returns the configured coefficients after quantization, not reloaded ones.
- MATLAB returns column vectors, so transpose before comparing with a row.

### Key Properties

- **Stateful**: Filter state persists across `run()` calls (streaming mode)
- **Call `reset()`** between independent test vectors to clear state
- **Multi-channel**: set `num_channels` (and `num_paths`). Pass an `(N, num_channels)` array, or a dict keyed by channel ID (by `(path, channel)` with several paths); `run()` then returns a dict with the same keys. MATLAB uses a struct with fields `ch0`, `ch1` and so on, or `p0_ch0` with several paths. `pack_stream` and `unpack_stream` (`packStream` and `unpackStream` in MATLAB) show the interleaving that `run()` applies.
- **Filter types**: Single-rate, interpolation, decimation, Hilbert transform
- **Input type**: `va.double`, or `va.cdouble` with `data_coefficient_type=1`

---

## vrfADC (VRF Data Converter v1.3: ADC)

Converts real samples on 4 channels to complex baseband, with 2 bands per channel.

### Configuration

```python
from vfs.vrfADC import vrfADC

config = vrfADC.getDefaultConfig()
config.tile_type = "quad"   # "quad" (4 x 8 GSPS) or "single" (1 x 32 GSPS)
config.afe_en = 0           # 0: 14-bit signed input (+/-8191); 1: analog model, input +/-1.0
# Per-channel datapath settings: config.digital_dp[0..3]

adc = vrfADC(config)
```

### Execution

```python
import numpy as np
import varray as va

# Input: a dict with one real va.double array per channel (4 channels)
n = np.arange(4096)
samples = va.array(np.round(4000 * np.cos(2 * np.pi * 0.1 * n)), va.double)
data_in = {"chan": [samples, samples, samples, samples]}

# Output: data_out["chan"][c]["band"][b] is a complex va.cdouble array
data_out = adc.run(data_in)
band0 = np.asarray(data_out["chan"][0]["band"][0])
```

Bands that the configuration does not use come back empty.

---

## vrfDAC (VRF Data Converter v1.3: DAC)

Converts complex baseband (2 bands per channel, 4 channels) to real samples.

### Configuration

```python
from vfs.vrfDAC import vrfDAC

config = vrfDAC.getDefaultConfig()
config.afe_en = 0
# Per-channel datapath settings: config.digital_dp[0..3]

dac = vrfDAC(config)
```

### Execution

```python
# Input: data_in["chan"][c]["band"][b] is a complex va.cdouble array
tone = va.array(0.1 * np.exp(2j * np.pi * 0.01 * np.arange(512)), va.cdouble)
data_in = {"chan": [{"band": [tone, tone]} for _ in range(4)]}

# Output: data_out["chan"][c] is a real va.double array
data_out = dac.run(data_in)
analog = np.asarray(data_out["chan"][0])
```

The ADC output dict has the same layout as the DAC input, so `dac.run(adc.run(data_in))` is a loopback. With both default configurations, each DAC output channel equals its ADC input sample for sample, and band 1 of every ADC channel is empty.

### Reconfiguring the Data Converters

`configure(config)` replaces the settings of an existing `vrfADC` or `vrfDAC`. The change applies from the next `run()`:

```python
import numpy as np
import varray as va
from vfs.vrfADC import vrfADC

adc = vrfADC(vrfADC.getDefaultConfig())
n = np.arange(8192)
x = va.array(np.round(3000 * np.cos(2 * np.pi * 0.07 * n)), va.double)

cfg = vrfADC.getDefaultConfig()                     # start from a fresh config
for dp in cfg.digital_dp:
    dp.decim_rate = [4, 4]
adc.configure(cfg)                                  # applies from the next run()

band0 = np.asarray(adc.run({"chan": [x] * 4})["chan"][0]["band"][0])
assert band0.size == n.size // 4
spectrum = np.abs(np.fft.fft(band0[128:]))
peak = abs(np.fft.fftfreq(band0.size - 128)[np.argmax(spectrum)])
assert abs(peak - 4 * 0.07) < 1e-3                  # the tone moves to 0.28 after decimating by 4
```

```matlab
adc = vfs.vrfADC(vfs.vrfADC.getDefaultConfig());
n = (0:8191).';
x = varray.double(round(3000 * cos(2 * pi * 0.07 * n)));
cfg = vfs.vrfADC.getDefaultConfig();
for c = 1:numel(cfg.digital_dp)
    cfg.digital_dp(c).decim_rate = [4 4];
end
adc.configure(cfg);                                 % applies from the next run()
[data_in, ~] = vfs.vrfADC.initializeArrays();
data_in.chan(:) = {x};                              % same signal on all four channels
data_out = adc.run(data_in);
band0 = double(data_out.chan{1}.band{1});
assert(numel(band0) == numel(n) / 4);
s = band0(129:end);
[~, k] = max(abs(fft(s)));
f = (k - 1) / numel(s);
f = min(f, 1 - f);                                  % fold to [0, 0.5]
assert(abs(f - 4 * 0.07) < 1e-3);                   % the tone moves to 0.28
```

- **Rates:** `decim_rate` (ADC) and `interp_rate` (DAC) hold one rate per band. A rate of r divides the ADC output length by r, or multiplies the DAC output length by r.
- **ADC input length:** For a decimation rate r greater than 1, pass a multiple of r samples to each `vrfADC.run()`. Concatenated outputs then match one `run()` of the same samples. A length that is not a multiple does not fail the call. The cmodel prints `Warning: Incorrect number of samples passed to DDC: Length N must be a multiple of the rate r`, and `run()` still returns data. Later outputs from that instance can differ from one `run()` of the whole stream. A streaming check should split on a multiple of r, and should not require an unaligned chunk to match a single run. Channels may still have different lengths from each other.
- **Level:** with `afe_en=0`, the ADC output is 4 times the 14-bit input, and the DAC scales it back.
- **Configs:** in Python, `getConfig()` returns the config object the converter holds, not a copy. Build a new config with `getDefaultConfig()` rather than editing that one in place.
- **Templates:** `vrfADC.initializeArrays()` and `vrfDAC.initializeArrays()` return `(data_in, data_out)` templates with the right nesting and empty arrays. Fill them in rather than building the dicts by hand.
- **MATLAB:** the input template is a struct whose `chan` field is a 1-by-4 cell array, and the output is read as `data_out.chan{c}.band{b}`.

---

## pbChannelizer (Poly Block Channelizer v1.0)

`pbChannelizer` has three modes. Forward mode (`mode=0`) splits one complex stream into eight frequency channels. Inverse mode (`mode=1`) combines eight channels into one stream. Multi-channel filter mode (`mode=2`) filters eight channels independently. This example checks that a tone lands in the right channel:

```python
import numpy as np
import varray as va
import vfs

ch = vfs.pbChannelizer(vfs.pbChannelizer.Config(mode=0))

n = np.arange(8 * 64)
tone = 0.4 * np.exp(2j * np.pi * 3 * n / 8)      # centred on channel 3
out, status = ch.run(va.array(tone, va.cdouble))

frames = np.asarray(out).reshape(-1, 8)           # row i = frame i, column c = channel c
power = np.mean(np.abs(frames[16:]) ** 2, axis=0)  # skip the filter start-up frames
assert np.argmax(power) == 3
assert status == vfs.pbChannelizer.Status()        # all flags False
```

```matlab
ch = vfs.pbChannelizer(vfs.pbChannelizer.Config('mode', 0));
n = 0:8*64-1;
[out, status] = ch.run(varray.cdouble(0.4 * exp(2i*pi*3*n/8)));
frames = reshape(double(out), 8, []).';          % column c+1 = channel c
power = mean(abs(frames(17:end, :)).^2, 1);      % skip the start-up frames
[~, peak] = max(power);
assert(peak - 1 == 3);
assert(isequal(status, vfs.pbChannelizer.Status()));
delete(ch);
```

- **Input:** a `va.cdouble` vector, with each real and imaginary part in `[-1.0, 1.0)`. Another type raises `TypeError`, and a value outside the range raises `ValueError`.
- **Output shape:** Python returns a 2D `(1, N)` varray, so flatten or reshape it before indexing. MATLAB returns a `1-by-N` row.
- **Frames:** the channelizer works on groups of eight input samples. Normally each complete group gives eight outputs, one per channel. In forward mode with `is_oversampled=1`, it gives 16. An unfinished group waits for the next call: a call with 5 samples returns nothing, and a following call with 3 samples returns 8.
- **Other modes:** inverse and multi-channel filter modes need a `Control` with one `chan_id` per input sample. `ch.createControl(num_samples)` builds a valid one for the configured mode. Multi-channel filter mode also needs a multiple of 8 samples per call.
- **Status:** every status field is a boolean flag. Comparing with `pbChannelizer.Status()` checks that none is set.
- **State:** the channelizer keeps filter history between calls. `reset()` clears it and keeps the settings.
- **Version:** `vfs.pbChannelizer.getVersion()` returns the model version, for example `"1.0"`.

### Multi-channel Filter Mode and Run-time Updates

In multi-channel filter mode, each input sample carries a channel ID and the coefficient set to use. With `has_coeff_interface=1`, you can also replace coefficient sets and the output gain between calls:

```python
import numpy as np
import varray as va
import vfs

P = vfs.pbChannelizer
taps = 16
h = np.zeros((8, taps))
for s in range(8):
    h[s, s] = 0.5                                   # set s: gain 0.5, delay of s frames

ch = P(P.Config(mode=2, data_type=1, coeff_source=1, coefficients=h.ravel(),
                coeff_per_chan=taps, has_coeff_interface=1))

def run(x):
    ctl = ch.createControl(x.size)                  # chan_id repeats 0..7
    ctl.coeff_set_re[:] = ctl.chan_id               # channel c uses set c
    ctl.coeff_set_im[:] = ctl.chan_id
    out, status = ch.run(va.array(x, va.cdouble), ctl)
    return np.asarray(out).reshape(-1, 8), status    # row = frame, column = channel

x = np.zeros(8 * 16, complex)
x[:8] = 0.25                                        # one impulse on every channel
y, status = run(x)
assert all(y[c, c] == 0.125 for c in range(8))

# Replace set 3 with a single tap of -0.75; the shift must match the configured one
shift = int(ch.getConfig().coeff_shift[0])
ch.updateCoeff(P.CoeffUpdate(set=3, shift=shift, coefficients=[-0.75] + [0.0] * (taps - 1)))
y, status = run(x)
assert y[0, 3] == -0.1875 and y[2, 2] == 0.125     # only channel 3 changed
assert status == P.Status()
```

```matlab
taps = 16;
h = zeros(8, taps);
for s = 0:7
    h(s + 1, s + 1) = 0.5;                          % set s: gain 0.5, delay of s frames
end
ch = vfs.pbChannelizer(vfs.pbChannelizer.Config('mode', 2, 'data_type', 1, ...
    'coeff_source', 1, 'coefficients', reshape(h.', 1, []), ...
    'coeff_per_chan', taps, 'has_coeff_interface', 1));
x = zeros(1, 8 * 16);
x(1:8) = 0.25;                                      % one impulse on every channel
ctl = ch.createControl(numel(x));
ctl.coeff_set_re = ctl.chan_id;                     % channel c uses set c
ctl.coeff_set_im = ctl.chan_id;
[out, status] = ch.run(varray.cdouble(complex(x)), ctl);
y = reshape(double(out), 8, []).';                  % row = frame, column c+1 = channel c
assert(all(diag(y(1:8, :)) == 0.125));
cfg = ch.getConfig();
ch.updateCoeff(vfs.pbChannelizer.CoeffUpdate('set', 3, 'shift', cfg.coeff_shift(1), ...
    'coefficients', [-0.75 zeros(1, taps - 1)]));
[out, status] = ch.run(varray.cdouble(complex(x)), ctl);
y = reshape(double(out), 8, []).';
assert(y(1, 4) == -0.1875 && y(3, 3) == 0.125);    % only channel 3 changed
assert(isequal(status, vfs.pbChannelizer.Status()));
delete(ch);
```

- **Coefficients:** `coefficients` holds sets 0 to 7 back to back, `coeff_per_chan` values each, and every value must be in [-1.0, 1.0).
- **`updateCoeff`:** takes effect on the next `run()`. It needs exactly `coeff_per_chan` values and the shift from `getConfig().coeff_shift`. A different shift or length raises `ValueError`.
- **`updateGain(g)`:** sets the output gain, from 0.0 to 7.99. It only changes the output with `has_output_gain=1`, and it clears the filter history of every channel.
- **`flush(ids)`:** clears the history of the listed channels only. It is not available with `is_symmetric` set.
- **Requirements:** all three methods need `mode=2`, and `updateCoeff` and `updateGain` also need `has_coeff_interface=1`. Otherwise they raise `ValueError`.
- **After `reset()`:** updates stay applied, but `getConfig()` still reports the original coefficients.
- **`loadVector(path)`:** returns `[coefficients, shift]` from a CSV file to pass as `coefficients` and `coeff_shift`. Each row is one filter set with comma-separated values in [-1.0, 1.0). Write complex values as `0.5+0.25i`, with no spaces. Other layouts raise `RuntimeError` (`INVALID_CSV`).

## pbArbResampler (Poly Block Arbitrary Resampler)

`pbArbResampler` raises the sample rate of a complex stream by a factor from 1.0 to 2.0. Set the factor with `phase_inc_value`: the output rate is `24 / phase_inc_value` times the input rate, so `16.0` gives 1.5. This example checks the rate over 20 calls:

```python
import numpy as np
import varray as va
import vfs

r = vfs.pbArbResampler(vfs.pbArbResampler.Config(phase_inc_value=16.0))  # ratio 1.5
total = 0
for _ in range(20):
    out, status = r.run(va.array(np.full(256, 0.3 + 0.1j), va.cdouble))
    total += out.size
assert total == 20 * 256 * 24 // 16
assert not (status.ovf_filter or status.ovf_gain or status.ovf_interp)
```

```matlab
r = vfs.pbArbResampler(vfs.pbArbResampler.Config('phase_inc_value', 16.0));
total = 0;
for k = 1:20
    [out, status] = r.run(varray.cdouble(repmat(complex(0.3, 0.1), 1, 256)));
    total = total + numel(out);
end
assert(total == 20 * 256 * 24 / 16);
assert(~(status.ovf_filter || status.ovf_gain || status.ovf_interp));
```

- **Input:** a non-empty `va.cdouble` vector whose length is a multiple of `input_ssr` (default 1).
- **Output:** the length varies from call to call, so check the ratio over many calls rather than one. Output is 1D in Python and a column in MATLAB.
- **Settings:** `phase_inc_value` must be from 12.0 to 24.0, or the constructor raises `ValueError`. It defaults to 16.0. To change the rate, create a new instance.
- **Status:** `ovf_filter`, `ovf_gain` and `ovf_interp` report overflow. If `ovf_gain` is set, lower `output_gain` or the input level.
- **State:** `reset()` restores the initial state, so the same input gives the same output again.

To shift the phase once, call `updatePhase` before the next `run()`. That run's status then shows `pacc_update=True`:

```python
ops = vfs.pbArbResampler.PhaseOperation
r.updatePhase(vfs.pbArbResampler.PhaseControl(op=ops.PHASE_DELTA, value=0.5, use_trigger=1))
```

```matlab
ops = vfs.pbArbResampler.PhaseOperation;
r.updatePhase(vfs.pbArbResampler.PhaseControl('op', ops.PHASE_DELTA, 'value', 0.5, 'use_trigger', 1));
```

- **Effect:** the shift is permanent. From that run on, the output differs from an unshifted instance fed the same input, while the output length stays the same. `pacc_update` is `True` only on the first run after the call.
- **Limits:** only `PHASE_DELTA` is supported, and `phase_inc_value + value` must stay from 12.0 to 24.0. `PHASE_INCREMENT` and out-of-range deltas raise `ValueError`.
- **Reset:** `reset()` undoes the shift along with the rest of the state.

## versalLDPC (Versal LDPC Decoder v1.1)

`versalLDPC` decodes one codeword per `run()` call. Its input is a log-likelihood ratio (LLR) per code bit, not the bits themselves. It supports 5G NR, Wi-Fi 802.11, DOCSIS 3.1, DVB-S2/S2X and custom codes. This example corrects 30 channel errors in a Wi-Fi codeword:

```python
import numpy as np
import varray as va
import vfs

dec = vfs.versalLDPC(vfs.versalLDPC.Config(standard="wifi"))
ctl = vfs.versalLDPC.Control(
    code=vfs.versalLDPC.getCodeNum("wifi802_11_cr1_2_648"),
    hard_op=True, term_on_parity=True, max_iterations=8,
)
p = dec.getCodeParams(ctl)                     # p.n = 648 inputs, p.k = 324 outputs

# All-zero codeword sent; negative LLR = bit 0. Flip 30 LLRs to model channel errors.
llr = np.full(p.n, -2.0)
llr[np.random.default_rng(1).choice(p.n, 30, replace=False)] = 1.0
hard, soft, status = dec.run(va.array(llr, va.double), ctl)

assert hard.size == p.k and not np.any(np.asarray(hard))   # all errors corrected
assert status.parity_pass
```

```matlab
dec = vfs.versalLDPC(vfs.versalLDPC.Config('standard', 'wifi'));
ctl = vfs.versalLDPC.Control('code', vfs.versalLDPC.getCodeNum('wifi802_11_cr1_2_648'), ...
    'hard_op', true, 'term_on_parity', true, 'max_iterations', 8);
p = dec.getCodeParams(ctl);
llr = -2 * ones(p.n, 1);
llr(1:21:end) = 1.0;                              % 31 channel errors
[hard, soft, status] = dec.run(varray.double(llr), ctl);
assert(status.parity_pass && all(double(hard) == 0));
```

- **Input:** exactly `n` LLRs (from `getCodeParams`) in a `va.double` array. A negative LLR means bit 0, and zero or a positive LLR means bit 1. A plain NumPy array or the wrong length raises `ValueError`.
- **LLR range:** values are rounded to steps of 0.25. Keep them within ±31.75, because `run()` does not check the range and larger values can wrap. Clip with `np.clip(llr, -31.75, 31.75)`.
- **Iterations:** set `max_iterations` from 1 to 63. The default of 0 is rejected when you call `run()`.
- **Output:** with `hard_op=True`, `hard` holds `k` decoded bits as `varray.uint8` and `soft` is empty. With `hard_op=False`, `soft` holds updated LLRs as `varray.double` and `hard` is empty. `include_parity_op=True` returns all `n` values instead of `k`.
- **Status:** `parity_pass` tells you whether the decoded codeword passes its parity check, and `dec_iterations` gives the iteration count.
- **Messages:** creating the decoder prints notes about default scale values for each built-in code. They do not indicate a problem.
- **Cleanup:** in Python, `with vfs.versalLDPC(config) as dec:` releases the decoder when the block ends.

For 5G NR, use `Config(standard="5g")` and select the code through `Control` fields rather than `code`. Set `z_set` and `z_j` to choose the lifting size, `bg` to choose the base graph, and `mb` to choose the number of parity blocks. The lifting size is `Z = [2, 3, 5, 7, 9, 11, 13, 15][z_set] * 2**z_j`. The first `2 * Z` code bits are punctured, so set their LLRs to 0.0.

### Custom Codes

`generateLdpcSpec` turns a quasi-cyclic parity-check matrix into a YAML code spec that the decoder can load:

```python
import numpy as np
import varray as va
import vfs

L = vfs.versalLDPC
Z = 16                                              # submatrix (lifting) size
base = np.array([[0, 3, 7, -1, 1, 0, -1, -1],       # shift of each Z x Z identity block,
                 [5, -1, 2, 9, -1, 0, 0, -1],       # -1 for an all-zero block
                 [-1, 11, 4, 6, 1, -1, 0, 0],
                 [8, 2, -1, 13, 0, -1, -1, 0]])
block = lambda s: np.zeros((Z, Z)) if s < 0 else np.roll(np.eye(Z), s, axis=1)
H = np.block([[block(s) for s in row] for row in base])   # 64 x 128 parity-check matrix

L.generateLdpcSpec(va.array(H, va.double), Z, "my_code.yml")
assert np.array_equal(np.asarray(L.generateParityCheckMatrix("my_code.yml")), H)

with L(L.Config(standard="custom", init_code="my_code.yml")) as dec:   # code 0
    ctl = L.Control(code=0, hard_op=True, term_on_parity=True, max_iterations=20)
    p = dec.getCodeParams(ctl)                      # n = 128, k = 64
    llr = np.full(p.n, -4.0)                         # all-zero codeword
    llr[[5, 40, 99]] = 2.0                           # three channel errors
    hard, soft, status = dec.run(va.array(llr, va.double), ctl)
    assert status.parity_pass and not np.any(np.asarray(hard))
```

```matlab
Z = 16;
base = [0 3 7 -1 1 0 -1 -1; 5 -1 2 9 -1 0 0 -1; -1 11 4 6 1 -1 0 0; 8 2 -1 13 0 -1 -1 0];
H = zeros(size(base) * Z);
for r = 1:size(base, 1)
    for c = 1:size(base, 2)
        if base(r, c) >= 0
            H((r-1)*Z + (1:Z), (c-1)*Z + (1:Z)) = circshift(eye(Z), base(r, c), 2);
        end
    end
end
vfs.versalLDPC.generateLdpcSpec(varray.double(H), Z, 'my_code.yml');
assert(isequal(double(vfs.versalLDPC.generateParityCheckMatrix('my_code.yml')), H));
dec = vfs.versalLDPC(vfs.versalLDPC.Config('standard', 'custom', 'init_code', 'my_code.yml'));
ctl = vfs.versalLDPC.Control('code', 0, 'hard_op', true, 'term_on_parity', true, 'max_iterations', 20);
p = dec.getCodeParams(ctl);                          % n = 128, k = 64
llr = -4 * ones(p.n, 1);
llr([6 41 100]) = 2.0;                               % three channel errors
[hard, soft, status] = dec.run(varray.double(llr), ctl);
assert(status.parity_pass && numel(hard) == p.k && all(double(hard) == 0));
delete(dec);
```

- **Matrix:** `H` must be a `va.double` matrix made of `Z`-by-`Z` blocks, each all zeros or a cyclically shifted identity. Any other block makes `generateLdpcSpec` raise `RuntimeError` ("Non-identity sub-matrix found"). `generateParityCheckMatrix` reads a spec back into the matrix.
- **Loading:** `Config(standard="custom", init_code=path)` loads the spec as code 0. A custom decoder without `init_code` fails at the first call with "code number not defined". `reset()` drops the `init_code` spec, so call `addLdpcCode(0, path)` after each `reset()`.
- **`addLdpcCode(number, path)`:** adds a spec at code number 0 to 127 on any decoder, replacing a built-in code with that number. Added codes survive `reset()`.
- **`configure(config)`:** replaces the configuration and rebuilds the decoder, so codes added with `addLdpcCode` are lost. Add them again afterwards.
- **Cleanup:** `close()` frees the decoder; later calls raise `RuntimeError`, and closing twice is harmless. The `with` block calls it for you. MATLAB has no `close`; use `delete(dec)`.
- **Version:** `vfs.versalLDPC.getVersion()` returns the model version, for example `"1.1"`.

---

## Complete Python Example: vrfFFT vs NumPy Reference

```python
"""VFS functional verification: vrfFFT compared against NumPy FFT"""
from vfs.vrfFFT import vrfFFT
import varray as va
import numpy as np

# --- Configuration ---
NFFT = 1024
DATA_WIDTH_IN = 16
DATA_WIDTH_OUT = 16
SCALING = 1.0 / NFFT

# --- Generate test signal: 2 tones ---
t = np.arange(NFFT)
tone1 = np.cos(2 * np.pi * 64 * t / NFFT)
tone2 = 0.5 * np.cos(2 * np.pi * 200 * t / NFFT)
signal = tone1 + tone2

# Quantize to fixed-point range
in_scale = 2 ** (DATA_WIDTH_IN - 1)
signal_q = np.round(signal * in_scale / 2) / in_scale
signal_q = np.clip(signal_q, -1.0, 1.0 - 1.0 / in_scale)

# --- VFS vrfFFT ---
config = vrfFFT.getDefaultConfig()
config.data_width_in = DATA_WIDTH_IN
config.data_width_out = DATA_WIDTH_OUT
config.reorder_output = 1  # natural order, to match np.fft.fft
fft_model = vrfFFT(config)

data_in = va.array(signal_q + 0j, va.cdouble)
ctrl = vrfFFT.Control(
    scaling_factor=SCALING,
    direction=1,
    point_size=int(np.log2(NFFT)),
)
fft_out, overflow = fft_model.run(data_in, ctrl)
result = np.asarray(fft_out, dtype=np.complex128)

# --- NumPy Reference ---
ref = np.fft.fft(signal_q) / NFFT
# Quantize reference to match output precision
out_scale = 2 ** (DATA_WIDTH_OUT - 1)
ref_q = (np.round(ref.real * out_scale) + 1j * np.round(ref.imag * out_scale)) / out_scale

# --- Compare ---
error = result - ref_q
max_err = np.max(np.abs(error))
lsb = 1.0 / 2 ** (DATA_WIDTH_OUT - 1)
max_err_lsb = max_err / lsb

print(f"Max error: {max_err:.6e} ({max_err_lsb:.1f} LSBs)")
print(f"Overflow: {np.any(overflow)}")
assert max_err_lsb <= 1.0, f"FAIL: {max_err_lsb:.1f} LSBs exceeds 1.0"
print("PASS")
```

## Complete MATLAB Example: vrfFFT

```matlab
%% VFS functional verification: vrfFFT vs MATLAB fft()
NFFT = 1024;
DATA_WIDTH_IN = 16;
DATA_WIDTH_OUT = 16;
SCALING = 1/NFFT;

% Generate signal: 2 tones
t = (0:NFFT-1).';
tone1 = cos(2*pi*64*t/NFFT);
tone2 = 0.5 * cos(2*pi*200*t/NFFT);
signal = tone1 + tone2;

% Quantize
in_scale = 2^(DATA_WIDTH_IN - 1);
signal_q = round(signal * in_scale / 2) / in_scale;
signal_q = max(min(signal_q, 1.0 - 1.0/in_scale), -1.0);

% Configure vrfFFT
config = vfs.vrfFFT.getDefaultConfig();
config.data_width_in = DATA_WIDTH_IN;
config.data_width_out = DATA_WIDTH_OUT;
config.reorder_output = 1;   % natural order, to match fft()
fft_model = vfs.vrfFFT(config);

% Run
data_in = varray.cdouble(complex(signal_q, zeros(NFFT, 1)));
ctrl = vfs.vrfFFT.Control('scaling_factor', SCALING, ...
    'direction', 1, 'point_size', log2(NFFT));
[fft_out, overflow] = fft_model.run(data_in, ctrl);

% Reference
ref = fft(signal_q) / NFFT;
out_scale = 2^(DATA_WIDTH_OUT - 1);
ref_q = (round(real(ref)*out_scale) + 1j*round(imag(ref)*out_scale)) / out_scale;

% Compare
result = double(fft_out);
err = result - ref_q;
max_err = max(abs(err));
lsb = 1.0 / out_scale;
max_err_lsb = max_err / lsb;

fprintf('Max error: %.1f LSBs, Overflow: %d\n', max_err_lsb, any(overflow));
assert(max_err_lsb <= 1.0, sprintf('FAIL: %.1f LSBs', max_err_lsb));
fprintf('PASS\n');
```

---

## General Notes

- **State:** every HDL IP keeps internal buffers and history between `run()` calls. For independent test vectors, call `reset()` on `firCompiler`, `vrfChannelizer`, `vrfADC`, `vrfDAC`, `pbChannelizer`, `pbArbResampler` and `versalLDPC`. On `versalLDPC`, codes added with `addLdpcCode` survive `reset()`, but the `init_code` spec does not (see [Custom codes](#custom-codes)). `xfft` and `vrfFFT` have no `reset()`, so create a new instance instead.
- **FFT frames:** for the FFTs, an input longer than the point size is processed as several frames. A shorter input is kept, and the output stays empty until a full frame arrives.
- **Status:** check the overflow or status result that an IP returns with its data.
- **Input types:** the FFTs, both channelizers and the resampler take `va.cdouble`. `versalLDPC` takes `va.double` LLRs. `firCompiler` takes `va.double`, or `va.cdouble` in complex mode. `vrfADC` takes a dict of `va.double` arrays, and `vrfDAC` a dict of `va.cdouble` arrays.
