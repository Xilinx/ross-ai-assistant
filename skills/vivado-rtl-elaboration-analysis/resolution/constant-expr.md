# Constant Expression Errors

**Covers IDs:** 530, 544, 545, 570, 579, 647, 648, 744

Grouped fixes for errors where a constant/parameter is required but a variable
or non-constant expression was used.

---

## VLOG-530: Constant Variable Reassigned

```
CRITICAL WARNING: [Synth 8-530] constant variable 'DEPTH' is being reassigned [/path/to/file.sv:20]
```

**Fix:** Remove the reassignment — constants/localparams can only be assigned once:
```diff
  localparam DEPTH = 16;
- DEPTH = 32;                // can't reassign a constant
```

If the value needs to change, use a variable instead:
```diff
- localparam DEPTH = 16;
+ int DEPTH = 16;            // use variable if reassignment is needed
```

**VHDL equivalent:**
```diff
  constant DEPTH : integer := 16;
- DEPTH := 32;               -- can't reassign a constant
```
Use a variable in a process if reassignment is needed:
```diff
- constant DEPTH : integer := 16;
+ variable depth : integer := 16;  -- variable allows reassignment
```

---

## VLOG-544 / VLOG-545: Replication Count Issues

```
WARNING: [Synth 8-544] negative replication count [/path/to/file.v:25]
WARNING: [Synth 8-545] zero replication count in concatenation [/path/to/file.v:30]
```

**Fix (544):** Ensure replication count is positive:
```diff
- assign data = {(WIDTH-16){1'b0}, value};  // WIDTH < 16 → negative count
+ assign data = {(WIDTH > 16 ? WIDTH-16 : 0){1'b0}, value};
```

Or fix the parameter values to ensure positive count.

**Fix (545):** Zero replication creates a zero-width result — remove it:
```diff
- assign data = {0{1'b0}, value};   // zero replication — empty
+ assign data = value;               // remove zero-width concat
```

---

## VLOG-570 / VLOG-579 / VHDL-744: Non-Constant Expression

```
ERROR: [Synth 8-570] generate expression is not constant [/path/to/file.v:35]
ERROR: [Synth 8-579] expression must be constant [/path/to/file.v:40]
ERROR: [Synth 8-744] constant required [/path/to/file.vhd:45]
```

These contexts require compile-time constants:
- `generate` conditions
- Array/vector sizes
- Parameter values
- Case expressions in `generate case`

**Fix:** Replace the variable with a `parameter` or `localparam`:
```diff
- integer depth;
- genvar i;
- generate
-   for (i = 0; i < depth; i = i+1) begin  // 'depth' is not constant
+ localparam DEPTH = 8;
+ genvar i;
+ generate
+   for (i = 0; i < DEPTH; i = i+1) begin  // constant — OK
```

**VHDL:**
```diff
- variable depth : integer := 8;
- for i in 0 to depth-1 generate    -- 'depth' is not constant
+ constant DEPTH : integer := 8;
+ for i in 0 to DEPTH-1 generate    -- constant — OK
```

---

## VLOG-647 / VLOG-648: Non-Constant Parameter Override

```
CRITICAL WARNING: [Synth 8-647] hierarchical name in parameter not resolved [/path/to/file.v:50]
CRITICAL WARNING: [Synth 8-648] non-constant value used as parameter override [/path/to/file.v:55]
```

**Fix (647):** Replace hierarchical references with constant values:
```diff
- sub_mod #(.WIDTH(top.u_other.WIDTH)) u1 (...);  // hierarchical — can't resolve
+ sub_mod #(.WIDTH(8)) u1 (...);                    // use constant value
```

Or pass through parameter chain:
```diff
+ localparam SUB_WIDTH = 8;
+ sub_mod #(.WIDTH(SUB_WIDTH)) u1 (...);
```

**Fix (648):** Replace variable with constant:
```diff
- integer width_var = get_width();
- sub_mod #(.WIDTH(width_var)) u1 (...);  // variable — not constant
+ localparam WIDTH_VAL = 8;
+ sub_mod #(.WIDTH(WIDTH_VAL)) u1 (...);  // constant — OK
```

**VHDL equivalent for non-constant generics:**
```diff
- u1 : entity work.sub_mod
-   generic map (WIDTH => some_signal)  -- signal is not constant
+ u1 : entity work.sub_mod
+   generic map (WIDTH => C_WIDTH)      -- use a constant or generic
```

---
