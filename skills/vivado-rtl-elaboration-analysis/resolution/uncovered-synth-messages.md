# Resolution: Uncovered Synth Messages (Not in Dispatch Table)

## Scope

This resolution file applies to **any** `[Synth 8-XXX]` message whose ID is NOT
listed in the dispatch table in `message-handlers.md`. These are typically
synthesis-phase optimization messages (IDs > 1000) that occur after the Verific
elaboration front-end has completed.

**Rule**: Even though these messages lack a specific resolution file, they MUST
still be reported with the full format: `###` section, File link, Problematic Code
(diff block), and Recommended Fix (diff block) — one per CSV S.No.

---

## Common Patterns and Fix Templates

### Pattern 1: Unused Sequential Element Removed (ID 6014)

**Message**: `Unused sequential element <name>_reg was removed.`

**Cause**: A register is assigned but its output `q` is never read in the current
instantiation context. Synthesis optimizes it away.

**Analysis steps**:
1. Read source at reported file:line — find the register assignment
2. Trace the register's output — is it supposed to drive something?
3. Check if the module uses parameters that conditionally use the register

**Problematic Code template**:
```diff
  // register is assigned but output never consumed
  always @(posedge clk) begin
-   if (enable)
-     unused_reg <= data_in;  // <- output of unused_reg is never read downstream
  end
```

**Recommended Fix options** (choose based on analysis):

Option A — Register is intentionally unused (parameter-dependent path):
```diff
  // Suppress warning: register is used when REG_Q=1 parameter is set
  always @(posedge clk) begin
+   (* dont_touch = "true" *)
    if (enable)
      unused_reg <= data_in;
  end
```

Option B — Register output should be connected:
```diff
  // Connect the register output to its intended consumer
- assign result = intermediate;
+ assign result = unused_reg;
```

Option C — Register is truly dead code, remove it:
```diff
  always @(posedge clk) begin
-   if (enable)
-     dead_reg <= data_in;  // <- remove dead register
  end
```

**Decision guidance**:
- If module is parameterized and register is used in some configurations → Option A (waive)
- If register was meant to be connected but connection is missing → Option B (fix)
- If register serves no purpose in any configuration → Option C (remove)

---

### Pattern 2: Undeclared Symbol Assumed Wire (ID 11241)

**Message**: `undeclared symbol '<name>', assumed default net type 'wire'`

**Cause**: A signal is used (typically in a port connection) without an explicit
`wire` or `logic` declaration. Vivado implicitly creates a 1-bit wire.

**Risk**: If the signal should be wider than 1 bit, implicit declaration causes
silent truncation.

**Problematic Code template**:
```diff
  // Signal used without explicit declaration
  module_inst u_inst (
-   .o_data(undeclared_signal)  // <- no declaration; assumed 1-bit wire
  );
```

**Recommended Fix**:
```diff
+ wire [WIDTH-1:0] undeclared_signal;  // <- add explicit declaration with correct width
+
  module_inst u_inst (
    .o_data(undeclared_signal)
  );
```

**Analysis steps**:
1. Find the port connection using the undeclared signal
2. Look up the module definition to determine the port width
3. Add explicit wire declaration with matching width

---

### Pattern 3: Case Statement Not Full / No Default (ID 155)

**Message**: `case statement is not full and has no default`

**Cause**: A `case` statement in a combinational block does not cover all possible
input values and lacks a `default` branch. This infers a latch.

**Problematic Code template**:
```diff
  always_comb begin
    case (sel)
      2'b00: out = a;
      2'b01: out = b;
-     // missing 2'b10, 2'b11 and no default  // <- infers latch
    endcase
  end
```

**Recommended Fix**:
```diff
  always_comb begin
    case (sel)
      2'b00: out = a;
      2'b01: out = b;
+     default: out = '0;  // <- prevents latch inference
    endcase
  end
```

---

### Pattern 4: Informational / Progress Messages (IDs 6155, 6157, 7075, 7078, 7079, 3876)

**Messages**: "synthesizing module", "done synthesizing module", "Helper process",
"Multithreading enabled", "$readmem file read"

**Action**: These are purely informational. They are classified as INFO severity and
excluded from the report. No Problematic Code or Recommended Fix needed.

**Rule**: Only process INFO messages if they indicate a code quality issue
(e.g., 11241, 155). Progress/system INFOs are skipped.

---

## Reporting Format (MANDATORY)

For each non-INFO message whose ID is not in the dispatch table, write:

```markdown
### [Synth 8-XXXX] <message text>
**CSV S.No:** <number>
**File:** [filename.sv](CSV_File_path#Lnn)
**Message:** <full message text>
**Root Cause:** <explain why this message occurs based on reading the source>

**Problematic Code:**
\`\`\`diff
  context line from read_file
- problematic line  // <- explanation
  context line
\`\`\`

**Recommended Fix:**
\`\`\`diff
  context line
+ fixed/improved line  // <- what changed and why
  context line
\`\`\`

**Rationale:** <why this fix is correct, or why waiving is appropriate>
```

**NEVER skip the diff blocks.** Even if the recommendation is "waive this warning",
show the code and explain why waiving is appropriate using a suppress directive:
```diff
+ (* synthesis, keep *)  // Intentionally unused in this configuration
```

---

## Dispatch Table Addition

Add the following to `message-handlers.md` under a new section:

```markdown
### Synthesis Optimization Phase (Not Verific Front-End)

| ID | Severity | Short Name | Fix Strategy |
|----|----------|------------|--------------|
| 155 | info/warning | case not full | Add `default` branch → `resolution/uncovered-synth-messages.md` |
| 6014 | warning | unused register removed | Connect output or waive → `resolution/uncovered-synth-messages.md` |
| 11241 | info/warning | implicit wire | Add explicit declaration → `resolution/uncovered-synth-messages.md` |
```
