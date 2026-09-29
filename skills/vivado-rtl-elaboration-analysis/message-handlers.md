# Oasys Synthesis Message Handlers

Per-message-ID fix instructions for **Oasys synthesis messages** in Vivado.
Messages appear as `[Synth 8-XXX]` in Vivado logs.

> **Mandatory**: For every message, read the RTL source at the reported file:line using
> `read_file` before generating a fix. Never fabricate code.

---

## How Messages Appear in Vivado Logs

```
ERROR: [Synth 8-36] 'my_signal' is not declared [/path/to/file.v:42]
WARNING: [Synth 8-327] inferring latch for variable 'state_reg' [/path/to/file.v:100]
CRITICAL WARNING: [Synth 8-5796] RAM (ram_inst) has conflicting writes [/path/to/file.v:55]
```

The parser extracts: severity, ID (36/327/5796), message text, file path, line number.

---

## ID Coverage Summary

| Severity | Count | ID Range |
|----------|-------|----------|
| **Critical Warnings** | 8 | 4442, 5796, 5967, 6030, 6781, 6790, 6858, 13329 |
| **Errors** | 457 | 27-13331 |
| **Warnings** | 86 | 85-13374 |
| **Total** | **551** | |

---

## Dispatch Table - Critical Warnings (8 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 4442 | BlackBox module !%1! has unconnected pin !%2! | Connect the pin or mark as intentionally unconnected |
| 5796 | RAM (!%1!) has conflicting writes using multiple ports with same address | Ensure ports write to different addresses or add arbitration logic |
| 5967 | Detected mixed tristate reset assignment for register | Split vector; keep tristate assignments separate |
| 6030 | Inout pin read/written without tristate logic | Use explicit direction or tristate buffer |
| 6781 | Nested interface items may not be available | Pass interface items explicitly; flatten interface usage |
| 6790 | Constant variable cannot be reassigned | Remove assignment to constant or change to variable |
| 6858 | Multi-driven net connected to constant driver | Remove duplicate drivers; preserve intended driver only |
| 13329 | Null range not supported | Fix range bounds; ensure start <= end (or proper direction) |

---

## Dispatch Table - Errors (457 IDs)

### Range 27-99 (17 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 27 | !%1! not supported | Use synthesizable alternative construct |
| 34 | '!%1!' expects !%2! arguments | Add missing arguments to function/task call |
| 35 | '!%1!' is not a constant | Replace with parameter/localparam |
| 36 | '!%1!' is not declared | Add missing declaration for signal/variable |
| 78 | a value must be associated with generic !%1! | Provide value for generic parameter |
| 83 | all members of packed union must have same size | Pad union members to equal width |
| 91 | ambiguous clock in event control | Use single clock edge; restructure sensitivity |
| 99 | array type required | Change to array type or fix indexing |

### Range 120-285 (21 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 120 | call depth limit exceeded | Reduce recursion depth; flatten hierarchy |
| 196 | conditional expression could not be resolved to a constant | Replace with constant condition |
| 206 | constant expression required | Replace variable with constant/parameter |
| 211 | could not evaluate expression | Simplify or make constant |
| 248 | direction of slice does not match direction of prefix | Fix part-select direction [MSB:LSB] |
| 250 | disable statement must apply to surrounding named block | Add block label or fix disable target |
| 258 | duplicate association | Remove duplicate port/parameter association |
| 274 | error: !%1! | Fix syntax error at reported location |
| 277 | exponentiation is not supported | Use shift operations or lookup table |
| 279 | expression in generate should resolve to constant | Use parameter for generate index |
| 280 | expression must be constant | Replace variable with constant |
| 281 | expression must be of a packed type | Add `packed` to type definition |
| 285 | failed synthesizing module | Fix all errors in the module |

### Range 316-439 (25 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 316 | illegal module recursion detected | Remove circular instantiation |
| 317 | illegal recursive instantiation | Flatten hierarchy; remove recursion |
| 318 | illegal unconstrained array | Add range/size constraint to array |
| 349 | instance passed wrong connection count | Check module port list; fix connection count |
| 354 | integer overflow computing array size | Reduce array size; use smaller indices |
| 366 | invalid definition of precompiled function | Fix function signature |
| 403 | loop limit exceeded | Add iteration limit or restructure loop |
| 421 | mismatched array sizes in assignment | Match array dimensions on both sides |
| 422 | missing association | Add missing port/parameter association |
| 426 | missing choices in case statement | Add missing case items or default |
| 428 | missing elements in aggregate | Add missing array/struct elements |
| 434 | mixed level/edge triggered events | Separate into combo and sequential blocks |
| 436 | modport mismatch for interface port | Fix modport specification |
| 439 | module '!%1!' not found | Add missing file to project; fix typo |

### Range 462-561 (16 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 462 | no clock signal in event control | Add clock after `posedge`/`negedge` |
| 465 | no constraint on function return | Add return type constraint |
| 470 | no interface found | Add interface definition or fix reference |
| 476 | no matching instance for defparam | Fix defparam path; use parameter override |
| 478 | no matching signal for .* connection | Add matching signal declaration |
| 485 | no port on instance | Fix port name or add to module |
| 493 | no such design unit | Add missing file or fix reference |
| 509 | operands have different lengths | Resize operands to match |
| 517 | overlapping choice in case | Fix case values to be unique |
| 521 | parameter could not resolve to constant | Use compile-time constant value |
| 522 | parsing error | Fix syntax error at reported location |
| 523 | part-select does not match declaration | Fix part-select to match signal direction |
| 524 | part-select out of range | Adjust part-select bounds |
| 549 | port width mismatch | Resize signal or add explicit slice |
| 550 | port width mismatch in instance array | Match signal width to (port_width x instance_count) |
| 561 | range expression not constant | Use parameter for range bounds |

### Range 647-734 (12 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 647 | too many elements in subexpression | Split complex expression |
| 650 | too many ports for instance | Remove excess port connections |
| 658 | type mismatch for port | Fix port/signal type compatibility |
| 659 | type mismatch | Add cast or change declaration |
| 660 | unable to resolve '!%1!' | Search for correct identifier; fix typo |
| 685 | variable should not be in output port | Change to net type or use procedural block |
| 690 | width mismatch in assignment | Resize source/target to match |
| 734 | Invalid parameter | Fix parameter name or value |

### Range 837-983 (8 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 837 | assignment operator only in blocking context | Use `=` in combinational, `<=` in sequential |
| 859 | too many parameter overrides | Remove excess parameter overrides |
| 944 | multiple operator definitions match | Disambiguate operator usage |
| 981 | statement illegal in always_comb/latch | Move statement outside combinational block |
| 982 | statement illegal in always_comb/latch/ff | Use appropriate always block type |
| 983 | statement illegal in compilation scope | Move to appropriate scope |

### Range 1067-1958 (21 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 1067 | statement is illegal here | Move statement to appropriate location |
| 1101 | .* token can only appear once | Remove duplicate .* in port list |
| 1560 | actual of formal must be correct type | Fix type of actual parameter |
| 1561 | actual and formal for ref must be equivalent | Match types exactly |
| 1576 | aliasing only on net types | Change to wire/net type |
| 1621 | assigning type to string requires cast | Add explicit cast |
| 1697 | base value must be 2-16 | Fix number base |
| 1718 | cannot assign type to type | Fix type compatibility |
| 1819 | choice must be discrete range | Use discrete range in case |
| 1859 | constant not allowed here | Use variable instead |
| 1875 | default case should appear only once | Remove duplicate default |
| 1892 | digits must follow decimal point | Fix real number format |
| 1925 | elements must both be signed or unsigned | Match signedness |
| 1956 | error in use clause | Fix VHDL use clause syntax |
| 1958 | event expressions must be singular type | Simplify event expression |

### Range 2043-2948 (54 IDs)

2043, 2056, 2068, 2088, 2105, 2112, 2114, 2119, 2121, 2132, 2148, 2158, 2179, 2195, 2203, 2210, 2212, 2254, 2291, 2340, 2371, 2409, 2415, 2458, 2481, 2496, 2506, 2512, 2543, 2548, 2549, 2551, 2558, 2572, 2577, 2590, 2599, 2666, 2680, 2691, 2716, 2757, 2784, 2798, 2856, 2901, 2902, 2908, 2910, 2913, 2914, 2916, 2918, 2948

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 3302-4760 (18 IDs)

3302, 3380, 3391, 3492, 3493, 3522, 3892, 3918, 3942, 4169, 4374, 4556, 4564, 4675, 4678, 4687, 4752, 4760

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 5535-6902 (26 IDs)

5535, 5549, 5551, 5731, 5732, 5743, 5765, 5767, 5826, 5832, 5838, 6036, 6038, 6043, 6046, 6058, 6062, 6073, 6156, 6716, 6735, 6777, 6799, 6828, 6901, 6902

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 7028-8986 (32 IDs)

7028, 7051, 7054, 7055, 7068, 7136, 7213, 8742, 8863, 8879, 8882, 8891, 8892, 8895, 8896, 8899, 8902, 8910, 8922, 8923, 8925, 8926, 8927, 8936, 8937, 8943, 8945, 8958, 8960, 8983, 8985, 8986

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 9101-9963 (94 IDs)

9101, 9109, 9111, 9112, 9114, 9116, 9117, 9121, 9123, 9143, 9173, 9174, 9179, 9182, 9185, 9195, 9210, 9212, 9222, 9223, 9224, 9248, 9249, 9250, 9263, 9280, 9287, 9288, 9312, 9315, 9339, 9372, 9380, 9382, 9386, 9397, 9398, 9400, 9404, 9414, 9429, 9448, 9469, 9480, 9481, 9486, 9492, 9493, 9502, 9510, 9563, 9565, 9570, 9571, 9587, 9597, 9606, 9610, 9652, 9653, 9654, 9664, 9696, 9699, 9714, 9721, 9729, 9735, 9736, 9747, 9764, 9767, 9771, 9775, 9842, 9843, 9844, 9871, 9872, 9873, 9875, 9891, 9901, 9910, 9913, 9915, 9921, 9923, 9926, 9932, 9934, 9960, 9963

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 10021-11899 (156 IDs)

10021, 10033, 10039, 10042, 10043, 10047, 10059, 10093, 10095, 10097, 10140, 10149, 10157, 10180, 10219, 10221, 10275, 10287, 10298, 10300, 10302, 10304, 10307, 10314, 10320, 10327, 10334, 10344, 10366, 10387, 10388, 10438, 10440, 10458, 10478, 10487, 10491, 10495, 10507, 10511, 10515, 10530, 10531, 10551, 10561, 10571, 10592, 10604, 10606, 10615, 10616, 10617, 10631, 10632, 10636, 10656, 10661, 10672, 10681, 10690, 10694, 10703, 10712, 10713, 10714, 10729, 10738, 10752, 10768, 10790, 10791, 10797, 10813, 10826, 10829, 10848, 10872, 10882, 10886, 10890, 10911, 10929, 10940, 10945, 10949, 10976, 10992, 11001, 11055, 11057, 11062, 11065, 11066, 11067, 11077, 11095, 11098, 11112, 11117, 11119, 11121, 11137, 11145, 11152, 11155, 11160, 11161, 11166, 11182, 11187, 11212, 11213, 11214, 11218, 11229, 11234, 11242, 11259, 11261, 11262, 11287, 11290, 11303, 11323, 11324, 11325, 11356, 11365, 11495, 11501, 11534, 11536, 11537, 11558, 11560, 11574, 11584, 11587, 11615, 11635, 11772, 11803, 11815, 11817, 11899

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 12188-13331 (10 IDs)

12188, 12189, 12523, 12526, 13076, 13099, 13162, 13322, 13324, 13331

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

---

## Dispatch Table - Warnings (86 IDs)

### Range 85-693 (23 IDs)

| ID | Message | Fix Strategy |
|----|---------|--------------|
| 85 | always block has no event control | Add `@(*)` or explicit sensitivity list |
| 90 | always_latch did not result in a latch | Check latch inference conditions |
| 98 | array of blackboxes: assuming ports one bit | Provide module definition or port widths |
| 151 | case item is unreachable | Remove or reorder case item |
| 153 | case item will never be executed | Remove dead case item |
| 264 | enable of latch is always disabled | Check enable condition; may be dead code |
| 290 | fork/join as sequential block | Review parallel intent |
| 302 | global signal treated as local | Review signal scope; make explicit |
| 308 | ignoring empty port | Remove empty port or add connection |
| 311 | ignoring non-constant in initial | Use constant for initial values |
| 312 | ignoring unsynthesizable construct | Replace with synthesizable alternative |
| 324 | index out of range | Fix array/vector index bounds |
| 327 | inferring latch for variable | Add `else`/`default` for combinational logic |
| 330 | inout connections inferred for interface | Specify modport explicitly |
| 445 | multiply driven output in instance array | Fix driver conflicts |
| 506 | null port ignored | Remove null port or add connection |
| 547 | port direction mismatch | Fix input/output/inout direction |
| 567 | signal should be on sensitivity list | Add signal to `@(...)` or use `@(*)` |
| 589 | replacing case/wildcard equality | Use `==`/`!=` instead of `===`/`!==` |
| 613 | shared variable as local | Review variable scope |
| 614 | signal not in sensitivity list | Add to sensitivity list |
| 689 | port connection width mismatch | Resize signal or add slice |
| 693 | zero replication count | Fix replication expression |

### Range 2887-3936 (15 IDs)

2887, 2897, 2898, 3301, 3330, 3332, 3514, 3819, 3848, 3917, 3919, 3936

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 4179-5974 (16 IDs)

4179, 4446, 4557, 4767, 5411, 5639, 5640, 5730, 5733, 5753, 5837, 5863, 5928, 5974

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 6014-7257 (25 IDs)

6014, 6026, 6040, 6060, 6090, 6104, 6426, 6430, 6774, 6778, 6849, 6850, 6880, 6896, 7023, 7032, 7043, 7071, 7129, 7134, 7137, 7143, 7186, 7193, 7257

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

### Range 11357-13374 (12 IDs)

11357, 11376, 11581, 11585, 12181, 13159, 13161, 13234, 13327, 13328, 13373, 13374

> Look up message in `oasys_severity_lists.csv`; read RTL at reported location

---

## Notes

- Message patterns use `!%1!`, `!%2!` etc. as placeholders for dynamic values
- For IDs not in tables above, look up the full message in `oasys_severity_lists.csv`
- Testcases for each ID are in `synth_code_8_xxx_testcase/{error,warnings,critical_warning}/8_XXX/`
- Always verify the fix by re-running synthesis

---

## Reference Files

- **CSV**: `oasys_severity_lists.csv` - Full message catalog
- **Testcases**: `synth_code_8_xxx_testcase/` - RTL samples for each ID
- **Resolution Guides**: `resolution/` - Detailed fix templates for common patterns
