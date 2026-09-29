# Clock Edge and Event Control Issues

Resolution guide for clock/edge-related elaboration messages.

## Covered Messages

| ID | Severity | Message Pattern |
|----|----------|----------------|
| 560 | ERROR | mixed level and edge expression list |
| 561 | ERROR | no clock in event control |
| 562 | ERROR | ambiguous clock in event control |
| 563 | WARNING | more than one event control statement — only the first is synthesizable |

---

## [Synth 8-560] Mixed level and edge expression list

### Vivado Message
```
ERROR: [Synth 8-560] mixed level and edge expression list [file:line]
```

### Root Cause
The sensitivity list mixes edge-triggered signals (e.g., `posedge clk`) with
level-sensitive signals (e.g., `data`). This is not synthesizable — an always
block must be **either** sequential (all edges) or combinational (all levels).

### Fix Options

**Option A — Sequential block (remove level signals):**
```diff
- always @(posedge clk or data) begin  // <- mixed edge + level
+ always @(posedge clk) begin          // <- edge-only (sequential)
    if (data)
      q <= d;
  end
```

**Option B — Async reset pattern (edge for both clock and reset):**
```diff
- always @(posedge clk or rst) begin   // <- 'rst' is level, not edge
+ always @(posedge clk or posedge rst) begin  // <- edge for both
    if (rst)
      q <= '0;
    else
      q <= d;
  end
```

**VHDL equivalent:**
```diff
- process(clk, data)   -- level-sensitive data mixed with clocked logic
+ process(clk)         -- sequential: clock only
  begin
    if rising_edge(clk) then
      if data = '1' then
        q <= d;
      end if;
    end if;
  end process;
```

### Rationale
Synthesis requires deterministic register inference. Mixed level/edge prevents
the tool from deciding whether to create a flip-flop or a latch.

---

## [Synth 8-561] No clock in event control

### Vivado Message
```
ERROR: [Synth 8-561] no clock in event control [file:line]
```

### Root Cause
An always block uses edge notation (`posedge`/`negedge`) in the sensitivity list,
but no identifiable clock signal was found. Typically caused by an empty or
malformed sensitivity list.

### Fix Options

**Option A — Add clock signal:**
```diff
- always @(posedge) begin              // <- no signal name after posedge
+ always @(posedge clk) begin          // <- specify the clock signal
    q <= d;
  end
```

**Option B — Convert to combinational if no clock intended:**
```diff
- always @(posedge) begin
+ always @(*) begin                    // <- combinational, no clock needed
    q = d;                             // <- blocking assignment for comb
  end
```

### Rationale
An edge-triggered block requires a clock signal for register inference. Without
a clock, the synthesis tool cannot determine what drives the registers.

---

## [Synth 8-562] Ambiguous clock in event control

### Vivado Message
```
ERROR: [Synth 8-562] ambiguous clock in event control [file:line]
```

### Root Cause
Multiple signals with `posedge`/`negedge` in the sensitivity list, and the tool
cannot determine which is the clock vs. async control. This happens when:
- Two or more edge events with no clear if/else structure to disambiguate
- Missing priority check for the async signal

### Fix Options

**Option A — Use standard async reset pattern (reset checked first in if):**
```diff
  always @(posedge clk or posedge rst) begin
-   q <= d;                            // <- no if/else, ambiguous
+   if (rst)                           // <- async signal checked FIRST
+     q <= '0;
+   else
+     q <= d;                          // <- clocked path in else
  end
```

**Option B — Remove extra edge (keep single clock):**
```diff
- always @(posedge clk or posedge enable) begin  // <- two edges, ambiguous
+ always @(posedge clk) begin                    // <- single clock
    if (enable)
      q <= d;
  end
```

**VHDL equivalent:**
```diff
  process(clk, rst)
  begin
-   q <= d;                         -- ambiguous: no rising_edge/reset check
+   if rst = '1' then               -- async reset checked first
+     q <= '0';
+   elsif rising_edge(clk) then     -- clock in elsif
+     q <= d;
+   end if;
  end process;
```

### Rationale
When multiple edges are present, the synthesis tool expects a standard async
reset pattern: the async signal is checked first (highest priority `if`), and
the clock path is in the `else`/`elsif rising_edge()` branch. Any other structure
creates ambiguity about which signal is the clock.

---

## [Synth 8-563] More than one event control statement

### Vivado Message
```
WARNING: [Synth 8-563] more than one event control statement in this 'always' block — only the first is synthesizable [file:line]
```

### Root Cause
The always block contains multiple `@(...)` event controls. Only the first
determines the sensitivity. The rest are simulation-only constructs (delays,
wait statements) that synthesis ignores.

### Fix Options

**Option A — Remove internal event controls:**
```diff
  always @(posedge clk) begin
    a <= b;
-   @(posedge clk);                 // <- second event control (ignored)
-   c <= d;                         // <- this assignment is unpredictable
+   c <= d;                         // <- moves to same clock edge
  end
```

**Option B — Split into separate always blocks for multi-cycle:**
```diff
- always @(posedge clk) begin
-   a <= b;
-   @(posedge clk);                 // <- waiting for next clock (sim only)
-   c <= a;
- end
+ always @(posedge clk) begin
+   a <= b;
+ end
+
+ // Pipeline stage 2: one cycle later
+ always @(posedge clk) begin
+   c <= a;                          // <- a is already registered, so c = b delayed 2 cycles
+ end
```

### Rationale
Synthesis flattens all logic into a single clock domain evaluation. Multiple event
controls within one always block are a simulation timing construct with no hardware
equivalent. Use separate always blocks or registered pipelines for multi-cycle behavior.

---

## References

- UG901 Ch.4 — Combinational vs Sequential Coding
- IEEE 1364-2005 §9.7.5 — Event Control
- IEEE 1800-2017 §9.4.2 — Event Control
- IEEE 1076-2008 §11.3 — Process Sensitivity
