# Range and Index Errors

**Covers IDs:** 547, 548, 549, 742, 745, 766, 769, 803, 806, 825, 827, 869

Grouped fixes for out-of-range indices, slice direction mismatches, null ranges,
and integer overflow errors.

---

## VLOG-547 / VHDL-742: Index Out of Range

```
WARNING: [Synth 8-547] index value '8' is out of range for 'data[7:0]' [/path/to/file.v:30]
ERROR: [Synth 8-742] index value '8' out of range for 'data' [/path/to/file.vhd:35]
```

**Fix:** Adjust the index to be within the declared range:
```diff
  // data is [7:0]
- assign bit = data[8];       // out of range
+ assign bit = data[7];       // within range
```

Or widen the declaration if the higher index is needed:
```diff
- wire [7:0] data;
+ wire [8:0] data;            // widen to accommodate index 8
```

---

## VLOG-548: Part-Select Out of Range

```
ERROR: [Synth 8-548] part-select [15:8] out of range for 'data[7:0]' [/path/to/file.v:35]
```

**Fix:** Adjust the slice to fit within declaration bounds:
```diff
  // data is [7:0]
- assign byte1 = data[15:8];    // out of range
+ assign byte1 = data[7:0];     // within range
```

Or widen the source:
```diff
- wire [7:0]  data;
+ wire [15:0] data;
```

---

## VLOG-549 / VHDL-745: Slice Direction Mismatch

```
ERROR: [Synth 8-549] part-select direction mismatch for 'data' [/path/to/file.v:40]
ERROR: [Synth 8-745] slice direction mismatch for 'data' [/path/to/file.vhd:45]
```

**Fix:** Match the slice direction to the declaration:

**Verilog** (declared `[7:0]` — descending):
```diff
- assign nibble = data[0:3];    // ascending — mismatch
+ assign nibble = data[3:0];    // descending — matches declaration
```

**VHDL** (declared `downto`):
```diff
- nibble <= data(0 to 3);       -- ascending — mismatch
+ nibble <= data(3 downto 0);   -- downto — matches declaration
```

---

## VHDL-766 / VHDL-803 / VHDL-806 / VHDL-825: Value Out of Range

```
ERROR: [Synth 8-766] expression out of range [/path/to/file.vhd:50]
ERROR: [Synth 8-803] integer value '300' out of type range [/path/to/file.vhd:55]
ERROR: [Synth 8-806] integer overflow [/path/to/file.vhd:60]
ERROR: [Synth 8-825] value out of allowable range [/path/to/file.vhd:65]
```

**Fix:** Clamp the value to the type's range or widen the type:
```diff
  -- signal counter : integer range 0 to 255
- counter <= 300;                 -- out of range
+ counter <= 255;                 -- clamp to max
```

Or widen the type:
```diff
- signal counter : integer range 0 to 255;
+ signal counter : integer range 0 to 511;
```

---

## VHDL-769: Null Range

```
CRITICAL WARNING: [Synth 8-769] null range detected [/path/to/file.vhd:70]
```

A null range occurs when the left bound equals or crosses the right bound in the
wrong direction (e.g., `7 to 0` for a `downto` type, or `0 downto 7` for a `to` type).

**Fix:** Correct the range direction:
```diff
- signal data : std_logic_vector(0 downto 7);   -- null range!
+ signal data : std_logic_vector(7 downto 0);   -- correct direction
```

Or fix swapped bounds:
```diff
- signal data : std_logic_vector(0 to 0);       -- only 1 bit? check intent
+ signal data : std_logic_vector(7 downto 0);   -- intended 8-bit signal
```

---

## VHDL-827: Aggregate Range Direction Mismatch

```
ERROR: [Synth 8-827] aggregate range direction mismatch [/path/to/file.vhd:80]
```

**Fix:** Match the aggregate range direction to the target type:
```diff
  -- target is std_logic_vector(7 downto 0)
- data <= (0 to 7 => '0');         -- 'to' doesn't match 'downto'
+ data <= (7 downto 0 => '0');     -- matches target direction
```

Or use positional/named association:
```diff
+ data <= (others => '0');          -- direction-agnostic
```

---

## VHDL-869: Out of Bounds Slice

```
ERROR: [Synth 8-869] slice is out of bounds [/path/to/file.vhd:85]
```

**Fix:** Adjust the slice to fit within the array bounds:
```diff
  -- data is std_logic_vector(7 downto 0)
- nibble <= data(11 downto 8);     -- out of bounds
+ nibble <= data(7 downto 4);      -- within bounds
```

---
