# VLOG-689: Port Width Mismatch

## Message Format

```
WARNING: [Synth 8-689] width (N) of port connection '<port>' does not match port width (M) of module '<module>' [file:line]
```

## Root Cause

The signal connected to a port has a different bit-width than the port expects.
This is commonly caused by:

1. **Hardcoded signal dimensions** in a parameterized module — signal uses `[31:0]`
   instead of `[PARAM-1:0]`
2. **Parameter override at instantiation** — parent passes a smaller value than
   the default, making the submodule port narrower than the connecting signal
3. **Typed unpacked arrays** — signal is declared as `typedef [N:0] array` but
   submodule expects fewer elements

## Critical Analysis Steps

**MANDATORY: Trace parameters from the top-level instantiation downward.**

Do NOT assume parameter defaults are the elaborated values. The actual width depends
on the full hierarchy:

1. **Find the instantiation** of the reported module in the file at the reported line
2. **Read the parameter overrides** at instantiation (`.PARAM(value)`)
3. **Trace `value` upward** — is it a parameter of the parent? What does the
   grandparent pass?
4. **Compute the actual port width** using the traced parameter value
5. **Read the signal declaration** at the connection site
6. **If the signal uses a typedef**, find the typedef in the package and **count
   the actual bits** (sum all fields in a `struct packed`). Do NOT assume the
   width from Vivado's numbers alone — verify by reading the source.
7. **Check all consumers** of the signal — search for all occurrences in the module
   to see if other instantiations or assignments use the full range
8. **Compare** actual signal width vs actual port width

### Example: Parameter Tracing

```
ethernet_comms (HDLC_NO_OF_CHANNELS = 16)   ← top sets 16
  └─ ethernet_hdlc_top (HDLC_NO_OF_CHANNELS = 16)  ← inherited
       ├─ signal: ohif_sel [31:0]  ← HARDCODED 32 elements = 512 bits
       │    └─ typedef: eth_hdlc_ohif_sel_tdef (struct packed, 16 bits) ← VERIFY in pkg
       └─ hdlc_decoder_top_v4 (NB_CHANNELS = 16)
            └─ port: iv_ohif_sel [NB_CHANNELS-1:0][15:0] = 16×16 = 256 bits
```

Vivado reports: "width (512) does not match port width (256)" — **correct warning**.

## Common Pitfall: Typed Unpacked Arrays

When a signal is declared with a typedef:
```systemverilog
my_typedef [31:0] my_signal;   // 32-element unpacked array
```

You **cannot** use bit-slicing like `my_signal[255:0]`. This is an unpacked array,
not a flat bit vector. Valid operations:
- Array slice: `my_signal[15:0]` (elements 0–15)
- Single element: `my_signal[0]`

## Fix Strategy

### Case 1: Signal shared with other consumers (most common in practice)

Before changing the declaration, **check all consumers** of the signal. If another
module (e.g., MPI register interface) connects to all 32 elements individually,
the declaration MUST stay `[31:0]`. Fix at the port connection using an array slice:

```diff
- .iv_ohif_sel  (ohif_sel),
+ .iv_ohif_sel  (ohif_sel[HDLC_NO_OF_CHANNELS-1:0]),
```

**Real-world example:** `ethernet_hdlc_mpi` connects `ohif_sel[0]`..`ohif_sel[31]`
individually. The decoder/encoder only needs channels 0–15. Shrinking the array
would break MPI. The fix is an array slice at the decoder/encoder ports only.

### Case 2: Signal only used by the mismatched port

If the signal has NO other consumers that need the full range, parameterize
the declaration:

```diff
- eth_hdlc_ohif_sel_tdef  [31:0]  ohif_sel;
+ eth_hdlc_ohif_sel_tdef  [HDLC_NO_OF_CHANNELS-1:0]  ohif_sel;
```

**IMPORTANT:** Only do this after confirming no other module/port needs the full
range. Check all instances of the signal name in the module.

### Case 3: Signal too narrow (widen it)

```diff
- wire [15:0] fill_level;    // 16 bits
+ wire [16:0] fill_level;    // 17 bits to match FIFO port
```

## Verification

After applying fix:
1. Re-run elaboration (`synth_design -rtl`)
2. Confirm the [Synth 8-689] warning is gone for this port
3. Check no new errors introduced (e.g., if other connections to the same signal
   now have a mismatch)

## Related Messages

- [Synth 8-514] — Similar port width mismatch (positional connections)
- [Synth 8-7129] — Port unconnected or has no load (may appear after width fix
  if upper bits were the only load)
