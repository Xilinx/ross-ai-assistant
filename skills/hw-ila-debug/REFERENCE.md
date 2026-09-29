<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ILA Debug — Reference Data

## ILA CONTROL Properties (UG912 — HW_ILA)

| Property | Type | Values | Description |
|----------|------|--------|-------------|
| CONTROL.DATA_DEPTH | int | 1–MAX_DATA_DEPTH (power of 2) | Samples per window. DATA_DEPTH × WINDOW_COUNT = MAX_DATA_DEPTH |
| CONTROL.WINDOW_COUNT | int | 1–N | Number of capture windows (multiple trigger events) |
| CONTROL.TRIGGER_POSITION | int | 0–(DATA_DEPTH-1) | Position of trigger mark. 0=first sample, 512=mid for 1024 depth |
| CONTROL.TRIGGER_MODE | enum | BASIC_ONLY, BASIC_OR_TRIG_IN, ADVANCED_ONLY, ADVANCED_OR_TRIG_IN, TRIG_IN_ONLY | Trigger source |
| CONTROL.TRIGGER_CONDITION | enum | AND, NAND, OR, NOR | Boolean equation across participating probes |
| CONTROL.CAPTURE_MODE | enum | ALWAYS, BASIC | ALWAYS=every cycle, BASIC=only when capture condition true |
| CONTROL.CAPTURE_CONDITION | enum | AND, NAND, OR, NOR | Boolean for capture qualification |
| CONTROL.TSM_FILE | string | file path | Path to Trigger State Machine (.tsm) file |
| CONTROL.TRIG_OUT_MODE | enum | DISABLED, TRIGGER_ONLY, TRIG_IN_ONLY, TRIGGER_OR_TRIG_IN | TRIG_OUT port behavior |

## ILA STATIC Properties (read-only)

| Property | Type | Description |
|----------|------|-------------|
| STATIC.MAX_DATA_DEPTH | int | Maximum samples (set at design time) |
| STATIC.IS_ADVANCED_TRIGGER_MODE_SUPPORTED | bool | Advanced TSM available? |
| STATIC.IS_BASIC_CAPTURE_MODE_SUPPORTED | bool | Storage qualifier available? |
| STATIC.IS_TRIG_IN_SUPPORTED | bool | TRIG_IN port present? |
| STATIC.IS_TRIG_OUT_SUPPORTED | bool | TRIG_OUT port present? |
| STATIC.TSM_COUNTER_0_WIDTH–3_WIDTH | int | TSM counter bit widths |

## ILA STATUS Properties (read-only)

| Property | Type | Description |
|----------|------|-------------|
| STATUS.CORE_STATUS | string | IDLE, ARMED, WAITING_FOR_TRIGGER, TRIGGER_CAPTURED, FULL |
| STATUS.SAMPLE_COUNT | int | Number of samples captured so far |
| STATUS.IS_TRIGGER_AT_STARTUP | bool | Trigger-at-startup configured? |

## ILA Probe Properties (HW_PROBE, TYPE == ila)

| Property | Type | R/W | Description |
|----------|------|-----|-------------|
| TRIGGER_COMPARE_VALUE | string | R/W | Trigger match pattern (see encoding below) |
| CAPTURE_COMPARE_VALUE | string | R/W | Capture qual match pattern |
| COMPARATOR_COUNT | int | R/O | Number of match units on this probe |
| PROBE_PORT | int | R/O | Physical probe port number |
| PROBE_PORT_BIT_COUNT | int | R/O | Bit width |
| NAME | string | R/O | HDL net name (from .ltx) |

## Compare Value Encoding

Format: `<operator><width>'<radix><value>`

**Operators:**

| Operator | Meaning |
|----------|---------|
| `eq` | Equal (=) |
| `neq` | Not equal (!=) |
| `lt` | Less than (<) |
| `lteq` | Less than or equal (≤) |
| `gt` | Greater than (>) |
| `gteq` | Greater than or equal (≥) |

**Radix:** `b` (binary), `h` (hex), `o` (octal), `u` (unsigned), `s` (signed)

**Special bit values (binary only):**

| Char | Meaning |
|------|---------|
| `X` | Don't care — probe does NOT participate in condition |
| `R` | Rising edge (0→1) |
| `F` | Falling edge (1→0) |
| `B` | Both edges (any transition) |
| `N` | No transition (stable) |
| `L` | Opposite of R |
| `S` | Opposite of F |

**Examples:**
- `eq1'b1` — probe equals 1
- `eq4'hA` — 4-bit probe equals 0xA
- `eq1'bR` — rising edge on 1-bit probe
- `neq8'hFF` — 8-bit probe not equal to 0xFF
- `gt16'u1000` — 16-bit unsigned probe > 1000
- `eq2'bXX` — don't care (probe not participating)

## Trigger State Machine (TSM) Syntax

TSM programs define multi-state advanced triggers for `ADVANCED_ONLY`/
`ADVANCED_OR_TRIG_IN` mode. The same language and rules apply on both
routing paths this skill uses: vivado-mcp's `.tsm` file
(`CONTROL.TSM_FILE`, compile-checked with `run_hw_ila -compile_only`) and
chipscope-mcp's `chipscope_ila_core(action="arm", trigger_state_machine=<program text>)`.

### Grammar

Reconciled from two sources that disagree in three places (see inline notes
below the block): the official
[Vivado UG908 "Trigger State Machine Language Grammar"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Trigger-State-Machine-Language-Grammar)
page, and chipscope-mcp's internal chipscopy LLM trigger-generation prompt
template (`chipscopy_llm/prompt_builder.py`).

```
'THING' = THING is a terminal
{<thing>} = 0 or more thing
[<thing>] = 0 or 1 thing

<program>           ::= <state_list>
<state_list>        ::= <state_list> <state> | <state>
<state>             ::= 'STATE' <state_label> ':' <if_condition> | <action_block>
<action_block>      ::= <action_list> 'GOTO' <state_label> ';'
                       | <action_list> 'TRIGGER' ';'
                       | 'GOTO' <state_label> ';'
                       | 'TRIGGER' ';'
<action_list>       ::= <action_statement> | <action_list> <action_statement>
<action_statement>  ::= 'SET_FLAG' <flag_name> ';'
                       | 'CLEAR_FLAG' <flag_name> ';'
                       | 'INCREMENT_COUNTER' <counter_name> ';'
                       | 'RESET_COUNTER' <counter_name> ';'
<if_condition>      ::= 'IF' '(' <condition> ')' 'THEN' <action_block>
                         ['ELSEIF' '(' <condition> ')' 'THEN' <action_block>]
                         'ELSE' <action_block>
                         'ENDIF'
<condition>         ::= <probe_match_list> | <counter_match> | <probe_counter_match>
<probe_counter_match> ::= '(' <probe_counter_match> ')'
                       | <probe_match_list> <boolean_logic_op> <counter_match>
                       | <counter_match> <boolean_logic_op> <probe_match_list>
<probe_match_list>  ::= '(' <probe_match> ')' | <probe_match>
<probe_match>       ::= <probe_match_list> <boolean_logic_op> <probe_match_list>
                       | <probe_name> <compare_op> <constant>
                       | <constant> <compare_op> <probe_name>
<counter_match>     ::= '(' <counter_match> ')'
                       | <counter_name> <counter_compare_op> <constant>
                       | <constant> <counter_compare_op> <counter_name>
<constant>          ::= <integer_constant> | <hex_constant> | <binary_constant>
<compare_op>        ::= '==' | '!=' | '>' | '>=' | '<' | '<='
<counter_compare_op> ::= '==' | '!='
<boolean_logic_op>  ::= '&&' | '||'

<probe_name>        ::= [A-Z_\[\]<>/\.][A-Z_0-9\[\]<>/\.]+
<state_label>       ::= [A-Z_][A-Z_0-9]+
<flag_name>         ::= \$FLAG[0-3]
<counter_name>      ::= \$COUNTER[0-3]
<hex_constant>      ::= <integer>*'h<hex_digit>+
<binary_constant>   ::= <integer>*'b<binary_digit>+
<integer_constant>  ::= <integer>*'u<integer_digit>+
<integer>           ::= <digit>+
<hex_digit>         ::= [0-9ABCDEFBN_]
<binary_digit>      ::= [01XRFBN_]
<digit>             ::= [0-9]
```

The language is case-insensitive. `#` starts a comment; everything from `#`
to end of line is ignored.

**Where this reconciles two disagreeing sources:**
- UG908's published grammar page writes `<actionblock>` (no underscore)
  inside `<if_condition>` but `<action_block>` everywhere else — the same
  nonterminal, an inconsistency in AMD's own published page. Normalized to
  `<action_block>` here.
- UG908's grammar page shares the full 6-operator `<compare_op>` for
  `<counter_match>`, but UG908's own separate "Counter Conditions" page
  states only `==`/`!=` are valid for counters — matching chipscopy's
  internal grammar's dedicated `<counter_compare_op>`, used here since it
  matches the documented enforced behavior, not the raw grammar page.
- UG908's `<probe_name>` omits `.` from the allowed charset; chipscopy's
  internal grammar includes it. The wider (chipscopy) charset is used here
  so a hierarchical probe name containing `.` is not misread as invalid.

### Rules that actually cause rejections

These are the specific failure modes chipscope-mcp#1289 reproduced on real
hardware (6 distinct rejected attempts, one real `arm()` call each) plus the
grammar's own structural constraints. Each rule names its source.

- **`state <name>:` is mandatory** — a bare `<name>:` without the `state`
  keyword is rejected (`mismatched input '<name>' expecting STATE`).
  *(Source: chipscope-mcp#1289 failure #1.)*
- **Only non-entry states need an incoming `goto` to be reachable.** The
  first-declared (entry) state needs no incoming `goto` — it is where
  execution starts. A single-state program, or a state that only ever
  `goto`s itself, is valid — see the first two Examples below, one of which
  is hardware-proven in chipscope-mcp's own MCP test suite. A *non-entry*
  state with no path back to it from the entry state is rejected
  (`The following states are not reachable: <name>.`). *(Source:
  chipscope-mcp `tests/mcp/test_mcp_ila.py::test_advanced_trigger_state_machine`,
  hardware-proven on vmk180/vpk120/vck190, plus chipscopy's own canonical
  single-state trigger-immediate example. chipscope-mcp#1289's failure #3
  — "trigger state declared first, nothing goes to it later" — is this rule
  applied to a non-entry state.)*
- **At least one state must contain a `trigger;` action.** A program with no
  reachable `trigger;` is rejected explicitly
  (`The state machine need at least one trigger action.`). *(Source:
  chipscope-mcp#1289 failures #2 and #6.)*
- **A `goto` naming an undeclared state is rejected by name**
  (`Unknown state "<name>".`) — this includes forward-declaring a state with
  `state X;` (no colon, no body): that's not valid syntax at all
  (`no viable alternative at input 'state X;'`). *(Source: chipscope-mcp#1289
  failures #2, #5, #6.)*
- **At most one `elseif` per `if`.** *(Source: chipscopy `prompt_builder.py`,
  stated explicitly — "Only a single elseif is allowed in the if statement" —
  and confirmed by the grammar's own `[...]` optional-and-singular slot.)*
- **No nested `if`.** An `<action_block>` (what follows `then`/`else`/
  `elseif`) can only be `goto`, `trigger`, or a counter/flag action — never
  another `if`. *(Source: chipscopy `prompt_builder.py`, stated explicitly —
  "No nested if statements are allowed" — and confirmed by the grammar
  having no `if`-producing symbol inside `<action_block>`.)*
- **Counter conditions support only `==`/`!=`.** Probe conditions
  additionally support `<`, `<=`, `>`, `>=`. *(Source: chipscopy
  `prompt_builder.py`, and UG908's "Counter Conditions" page — see the
  grammar-reconciliation note above for why this differs from UG908's raw
  grammar page.)*
- **Each counter has exactly one comparator** — a given `$counterN` can
  appear in a counter condition only once in the whole program. *(Source:
  UG908 "Counter Conditions" page.)* **Each PROBE port has a compile-time
  comparator limit, and a probe attached to that port can appear in a probe
  condition only that many times total.** Two sources disagree on the
  limit: UG908's "Debug Probe Conditions" page says 1-16; chipscopy's
  internal prompt text says 1-4. Both are stated here rather than picking
  one — the design's actual limit is queryable via this same file's `ILA
  Design-Time Core Properties` table (`C_ALL_PROBE_SAME_MU_CNT`, range
  1-16, agreeing with UG908).
- **A condition's `<bit_width>` must equal the actual width of the probe or
  counter it compares.** Counters are always 16 bits
  (`$counter0 == 16'u100`, not `$counter0 == 8'u100`). *(Source: UG908
  "Debug Probe Conditions" page — "bit width is the width of the probe.")*
- **Probe vector indices (`probe[0]`, `probe[1]`) are not valid TSM operands.**
  Collapse to a single multi-bit compare against the whole probe instead:
  `(slot_0_r_dout[0] == 1'b1) && (slot_0_r_dout[1] == 1'b1)` must be written
  `(slot_0_r_dout == 2'b11)`. *(Source: chipscopy `prompt_builder.py` — this
  rule is not stated in UG908's pages found during research; flagged here as
  a practical rule from chipscope-mcp's own internal tooling, not an
  official grammar citation.)*
- **Prefer don't-care (`X`) bits over `&&`/`||` of the same probe** when only
  some bits matter: `(sig == 3'bX11)` over
  `((sig == 3'b011) || (sig == 3'b111))`. *(Source: chipscopy
  `prompt_builder.py`, stated explicitly with a BAD/GOOD example pair.)*

### Optional: enumerated compare values

UG908's "Advanced Trigger" page shows a form that compares against a
design-declared enumeration name instead of a numeric literal, e.g.
`fast_ila_slice5_fb == 5'eFIFTEEN`. This is **not** part of the primary
grammar above (neither UG908's own grammar page nor chipscopy's internal
grammar defines an `'e` radix or enum production) — it only works when the
target signal has enumerated values defined in the design's waveform/bus
configuration. Most probes should use the ordinary bit/hex/decimal literal
forms in the primary grammar instead; reach for this only if a design is
known to declare enum values for the probe in question.

```
state my_state0:
  if (fast_ila_slice5_fb == 5'eFIFTEEN) then
    set_flag $flag1;
    goto my_state0;
  elseif (fast_ila_slice5_fb == 5'eTWELVE) then
    trigger;
  else
    clear_flag $flag1;
    goto my_state0;
  endif
```

*(Source: UG908 "Advanced-Trigger" page, byte-exact.)*

### Examples

**Trigger immediately** (single state, no condition, no `goto` at all —
valid: the entry state needs no incoming `goto`):
```
state state0:
  trigger;
```
*(Source: chipscopy `prompt_builder.py`, byte-exact.)*

**Single state with a self-loop** — hardware-proven on real hardware
(vmk180, vpk120, vck190) via chipscope-mcp's own MCP test suite, confirming
a single-state program with a conditional and a self-referencing `goto` is
valid:
```
state s0:
  if (<probe> == 1'b1) then
    trigger;
  else
    goto s0;
  endif
```
*(Source: chipscope-mcp `tests/mcp/test_mcp_ila.py::test_advanced_trigger_state_machine`,
`success: true, mode: "advanced"`.)*

**One-way branch** (unconditional `goto`, no `if`):
```
state my_state0:
  goto my_state1;
state my_state1:
  trigger;
```
*(Source: first line byte-exact from chipscopy `prompt_builder.py`; the
second state's body is added here to make the fragment a complete, runnable
two-state program.)*

**Confirmed working on real hardware** — vmk180, ILA core
`chipscopy_i/counters/ila_slow_counter_0`, chipscope-mcp#1289 (`arm()`
returned `mode: "advanced"`, trigger fired on the 2nd state's threshold, not
the 1st, verified against uploaded sample data):
```
state wait_first:
  if (chipscopy_i/counters/slow_counter_0_Q_1 > 32'hb2d05e00) then
    goto wait_second;
  else
    goto wait_first;
  endif

state wait_second:
  if (chipscopy_i/counters/slow_counter_0_Q_1 > 32'hee6b2800) then
    trigger;
  else
    goto wait_second;
  endif
```
*(Source: chipscope-mcp#1289 issue body, byte-exact.)*

**Counter + trigger on the 4th rising edge of a probe:**
```
state wait_for_4th_rising_edge_of_abc:
  if ((abc == 1'bR) && ($counter0 == 16'h0003)) then
    reset_counter $counter0;
    trigger;
  elseif (abc == 1'bR) then
    increment_counter $counter0;
    goto wait_for_4th_rising_edge_of_abc;
  else
    goto wait_for_4th_rising_edge_of_abc;
  endif
```
*(Source: chipscopy `prompt_builder.py`, byte-exact.)*

**AXI write of specific data to a specific address** (two-phase: address
then data; hierarchical probe names, don't-care bits for handshake signals):
```
state wait_for_write_addr:
  if ((slot_0_aw_dout == 2'b11) && (chipscopy_i/noc_tg_bc/noc_bc_axis_ila_0/SLOT_0_AXI_awaddr == 64'h80000000)) then
    goto wait_for_write_data;
  else
    goto wait_for_write_addr;
  endif

state wait_for_write_data:
  if ((slot_0_w_dout == 3'b11X) && (chipscopy_i/noc_tg_bc/noc_bc_axis_ila_0/SLOT_0_AXI_wdata == 32'h12345678)) then
    trigger;
  else
    goto wait_for_write_data;
  endif
```
*(Source: rewritten from chipscopy `prompt_builder.py` for readability,
collapsing indexed-bit ANDs into single multi-bit compares per the
don't-care rule above. Address phase: source constrains both
`slot_0_aw_dout[0]` and `[1]` to `1'b1`, no free bit — `2'b11`, unchanged
from source. Data phase: source constrains `slot_0_w_dout[1]` and `[2]` to
`1'b1`, bit `[0]` free — `3'b11X` (bit2=1, bit1=1, bit0=X).)*

### Rejected patterns seen in practice (chipscope-mcp#1289)

Six distinct real `arm()` calls against live hardware, each rejected with a
different parser error — recognize these exact strings and fix the
corresponding rule above instead of guessing a new variant. "What differed"
is quoted verbatim from the issue; it describes what the reporter changed,
not a proven root cause in every row.

| What differed from a working program (quoted from #1289) | Server error |
|---|---|
| No `state` keyword before the label | `mismatched input 'wait_a' expecting STATE` |
| Final state was bare `trigger;` with no `if/endif` wrapper | `Unknown state "wait_b".` + `The state machine need at least one trigger action.` |
| Reordered states, trigger state declared first | `The following states are not reachable: wait_b.` |
| Single-state version | `The following states are not reachable: wait_a.` |
| Added bare `state X;` forward declarations | `no viable alternative at input 'state wait_b;'` |
| Renamed states, final state still bare `trigger;` | `Unknown state "phase_two".` + `The state machine need at least one trigger action.` |

**Note on the "Single-state version" row:** this reference's own Examples
section above shows two hardware-proven single-state programs (one with no
`goto` at all, one with a self-loop), so a single state is not
categorically rejected. The reporter's exact source for this specific
attempt is not available, so its precise cause is not restated as a general
rule here — treat it as an open question if it resurfaces, not as
"single-state programs fail."

### Sources

- [UG908 "Trigger State Machine Language Grammar"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Trigger-State-Machine-Language-Grammar)
- [UG908 "Conditional Branching"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Conditional-Branching)
- [UG908 "Counters"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Counters) / [UG908 "Counter Conditions"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Counter-Conditions)
- [UG908 "Flags"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Flags)
- [UG908 "Debug Probe Conditions"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Debug-Probe-Conditions)
- [UG908 "Advanced Trigger"](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging/Advanced-Trigger)
- chipscope-mcp's internal chipscopy LLM trigger-generation prompt template (`chipscopy_llm/prompt_builder.py`)
- chipscope-mcp `tests/mcp/test_mcp_ila.py::test_advanced_trigger_state_machine` (hardware-proven)
- chipscope-mcp#1289 (confirmed-working example, 6-failure table)

## ILA Design-Time Core Properties (for netlist insertion reference)

| Property | Values | Description |
|----------|--------|-------------|
| C_DATA_DEPTH | 1024–131072 | Sample buffer depth |
| C_NUM_OF_PROBES | 1–1024 | Number of probe ports |
| C_PROBE\<n\>_WIDTH | 1–4096 | Width of probe port n |
| C_ADV_TRIGGER | true/false | Enable advanced trigger / TSM |
| C_EN_STRG_QUAL | true/false | Enable capture condition (storage qualifier) |
| C_TRIGIN_EN | true/false | Enable TRIG_IN port |
| C_TRIGOUT_EN | true/false | Enable TRIG_OUT port |
| C_INPUT_PIPE_STAGES | 0–6 | Pipeline stages on probe inputs (timing) |
| C_ALL_PROBE_SAME_MU_CNT | 1–16 | Match units per probe |
| C_MEMORY_TYPE | 0 (BRAM), 1 (URAM) | Storage primitive (Versal only) |

## Tcl Command Quick Reference — ILA

| Command | Purpose |
|---------|---------|
| `get_hw_ilas` | List all ILA debug cores on current device |
| `current_hw_ila` | Get/set the current ILA |
| `get_hw_probes -of_objects $ila` | List probes on an ILA |
| `set_property CONTROL.* $ila` | Configure trigger/capture settings |
| `set_property TRIGGER_COMPARE_VALUE <val> $probe` | Set trigger match on a probe |
| `run_hw_ila $ila` | Arm ILA for trigger event |
| `run_hw_ila -trigger_now $ila` | Trigger immediately (aliveness check) |
| `run_hw_ila -compile_only $ila` | Compile-check TSM without arming |
| `run_hw_ila -file <path> $ila` | Export trigger config for startup |
| `wait_on_hw_ila $ila` | Block until capture complete |
| `upload_hw_ila_data $ila` | Pull captured data from device |
| `write_hw_ila_data -csv_file <file> $data` | Export as CSV |
| `write_hw_ila_data -vcd_file <file> $data` | Export as VCD |
| `write_hw_ila_data <file> $data` | Export as native .ila |
| `read_hw_ila_data <file>` | Load previously saved .ila file |
| `list_hw_samples $probe` | List sample values for a probe |
| `create_hw_probe -map {...} <name> $ila` | Create custom probe from physical bits |
| `reset_hw_ila $ila` | Reset all CONTROL properties to defaults |

## report_data.json Schema

```json
{
  "metadata": {
    "skill": "hw-ila-debug", "version": "1.0.0",
    "mode": "ila", "timestamp": "<ISO8601>",
    "device": "<part>", "ila_core": "hw_ila_1"
  },
  "ila_config": {
    "data_depth": 1024, "max_depth": 4096,
    "trigger_mode": "BASIC_ONLY", "trigger_condition": "AND",
    "trigger_position": 512, "window_count": 1,
    "capture_mode": "ALWAYS"
  },
  "probes": [
    { "name": "axi_arvalid", "width": 1, "type": "ila",
      "trigger_compare": "eq1'b1", "port": 3 }
  ],
  "capture": {
    "status": "TRIGGER_CAPTURED", "sample_count": 1024,
    "export_file": "capture_data.csv", "export_format": "csv"
  },
  "observations": ["axi_arvalid asserted at sample 512", "..."],
  "recommendations": []
}
```

## References

- **UG908**: Vivado Programming and Debugging — hw_ila, hw_probe Tcl commands
- **UG912**: Vivado Properties Reference — HW_ILA, HW_PROBE properties
- **UG835**: Vivado Tcl Commands Reference — run_hw_ila, write_hw_ila_data, etc.
- **UG936**: Vivado Tutorial: Programming and Debugging — step-by-step ILA lab
- **PG172**: Integrated Logic Analyzer v6.2 Product Guide — ILA core parameters, ports
- **PG357**: ILA with AXI4-Stream Interface Product Guide — Versal ILA
- **UG908 "Trigger State Machine Language Grammar"**: full TSM BNF — see this file's "Trigger State Machine (TSM) Syntax" section for the reconciled grammar
- **chipscope-mcp's internal chipscopy LLM trigger-generation prompt template**: source for several TSM examples and rules above
- **chipscope-mcp `tests/mcp/test_mcp_ila.py::test_advanced_trigger_state_machine`**: hardware-proven single-state TSM example, vmk180/vpk120/vck190
