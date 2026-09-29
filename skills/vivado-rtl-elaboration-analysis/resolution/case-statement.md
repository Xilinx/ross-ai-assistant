# Case Statement Errors

**Covers IDs:** 150, 506, 507, 508, 746, 750, 814, 838, 849

Grouped fixes for case statement issues — duplicate, overlapping, unreachable, and
out-of-range choices.

---

## VLOG-150: Multiple Defaults

```
ERROR: [Synth 8-150] multiple defaults in case statement [/path/to/file.v:50]
```

**Fix:** Keep one `default` block, merge the logic:
```diff
  case (sel)
    2'b00: out = a;
    2'b01: out = b;
    default: out = c;
-   default: out = d;        // remove second default
  endcase
```

---

## VLOG-506: Case Item Never Executed

```
WARNING: [Synth 8-506] case item 'xx' is never executed [/path/to/file.v:55]
```

**Fix:** Remove the dead case item or fix its value:
```diff
  case (sel)     // sel is 2-bit
    2'b00: out = a;
    2'b01: out = b;
    2'b10: out = c;
    2'b11: out = d;
-   2'bxx: out = e;    // never matches in synthesis — remove
  endcase
```

For `casex`/`casez` patterns, `x`/`z` are wildcards but plain `case` treats them as
unmatchable values.

---

## VLOG-507: Unreachable Case Item

```
WARNING: [Synth 8-507] unreachable case item [/path/to/file.v:60]
```

**Fix:** A previous item already covers this value. Remove or reorder:
```diff
  casez (sel)
    4'b1???: out = a;    // matches everything starting with 1
-   4'b1100: out = b;    // unreachable — already matched above
    4'b0???: out = c;
  endcase
```

---

## VLOG-508: Overlapping Case Items

```
WARNING: [Synth 8-508] overlapping case item values detected [/path/to/file.v:65]
```

**Fix:** Make case items mutually exclusive:
```diff
  casez (sel)
-   4'b1???: out = a;
-   4'b11??: out = b;     // overlaps with 1???
+   4'b10??: out = a;     // non-overlapping
+   4'b11??: out = b;     // non-overlapping
    default: out = c;
  endcase
```

Or use `priority case` / `unique case` (SystemVerilog) for intended priority encoding.

---

## VHDL-746: Overlapping Case Choice

```
ERROR: [Synth 8-746] overlapping case choice [/path/to/file.vhd:70]
```

**Fix:** Remove or modify the overlapping choice:
```diff
  case sel is
    when "00" to "10" => out <= a;
-   when "01"         => out <= b;    -- overlaps with "00" to "10"
+   when "11"         => out <= b;    -- non-overlapping value
    when others       => out <= c;
  end case;
```

---

## VHDL-750 / VHDL-814: Duplicate Choice

```
ERROR: [Synth 8-750] duplicate choice in aggregate [/path/to/file.vhd:75]
ERROR: [Synth 8-814] previously used choice [/path/to/file.vhd:80]
```

**Fix:** Remove the duplicate choice value:
```diff
  case state is
    when IDLE => next <= RUN;
    when RUN  => next <= DONE;
-   when RUN  => next <= ERR;     -- duplicate — remove
    when DONE => next <= IDLE;
    when others => next <= IDLE;
  end case;
```

---

## VHDL-838 / VLOG-849: Choice/Width Out of Range

```
WARNING: [Synth 8-838] case choice value '16' is out of range [/path/to/file.vhd:85]
WARNING: [Synth 8-849] case expression and choice width mismatch [/path/to/file.vhd:90]
```

**Fix (838):** Adjust choice to be within the select expression's range:
```diff
  -- sel is integer range 0 to 7
  case sel is
    when 0 => out <= a;
-   when 16 => out <= b;     -- out of range (0 to 7)
+   when 7  => out <= b;     -- within range
    when others => out <= c;
  end case;
```

**Fix (849):** Match choice width to case expression width:
```diff
  -- sel is std_logic_vector(3 downto 0) — 4 bits
  case sel is
-   when "00000" => out <= a;     -- 5 bits, should be 4
+   when "0000"  => out <= a;     -- 4 bits — matches sel width
    when others => out <= c;
  end case;
```

---
