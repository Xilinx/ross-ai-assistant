# Resolution Guide - Oasys Synthesis Messages

This file maps **all 551 Oasys synthesis message IDs** from testcases to resolution patterns.

---

## Overview

Resolution guides are pre-written, validated fix templates for known message types.
They provide:

- **Consistency** - Same message type always gets the same fix pattern
- **Accuracy** - Fixes validated against IEEE standards and Xilinx documentation
- **Completeness** - Includes rationale, multiple fix options, and verification
- **Speed** - Pre-written templates reduce analysis time

---

## ID Coverage Summary

| Severity | Count | ID Range |
|----------|-------|----------|
| **Critical Warnings** | 8 | 4442, 5796, 5967, 6030, 6781, 6790, 6858, 13329 |
| **Errors** | 457 | 27-13331 |
| **Warnings** | 86 | 85-13374 |
| **Total** | **551** | |

---

## Resolution Pattern Categories

### Category 1: Declaration Issues
**Pattern**: Signal/variable/module not found or already declared

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 36 | not declared | Add declaration; check spelling and scope |
| 258 | duplicate association | Remove duplicate |
| 439 | module not found | Add file to project; fix typo |
| 470 | no interface found | Add interface definition |
| 478 | no matching signal for .* | Add matching signal |
| 485 | no port on instance | Fix port name |
| 493 | no such design unit | Add missing file |
| 660 | unable to resolve | Fix typo; check scope |

### Category 2: Constant Expression Issues
**Pattern**: Expression must be constant for synthesis

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 35 | not a constant | Use parameter/localparam |
| 196 | conditional not constant | Use constant condition |
| 206 | constant expression required | Replace variable with constant |
| 211 | could not evaluate expression | Simplify; use constants |
| 279 | generate expression not constant | Use parameter |
| 280 | expression must be constant | Use constant value |
| 521 | parameter not constant | Use compile-time constant |
| 561 | range expression not constant | Use parameter for bounds |

### Category 3: Width/Type Mismatch Issues
**Pattern**: Signal widths or types don't match

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 83 | packed union size mismatch | Pad to equal width |
| 281 | must be packed type | Add `packed` keyword |
| 421 | array size mismatch | Match dimensions |
| 509 | operand length mismatch | Resize operands |
| 549 | port width mismatch | Resize signal or add slice |
| 550 | instance array width mismatch | Match width to port x count |
| 658 | type mismatch for port | Fix type compatibility |
| 659 | type mismatch | Add cast or change type |
| 690 | width mismatch in assignment | Resize source/target |
| 689 | port connection width mismatch | Resize or slice |

### Category 4: Part-Select/Index Issues
**Pattern**: Array/vector indexing errors

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 248 | slice direction mismatch | Fix [MSB:LSB] direction |
| 324 | index out of range | Fix bounds |
| 523 | part-select mismatch | Match declaration direction |
| 524 | part-select out of range | Adjust bounds |
| 354 | integer overflow | Reduce array size |

### Category 5: Case Statement Issues
**Pattern**: Case/switch statement problems

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 151 | case item unreachable | Remove or reorder |
| 153 | case item never executed | Remove dead item |
| 426 | missing choices | Add missing cases or default |
| 517 | overlapping choice | Make values unique |
| 1875 | duplicate default | Remove extra default |

### Category 6: Latch Inference Issues
**Pattern**: Unintended latch inferred

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 90 | always_latch no latch | Check inference conditions |
| 264 | latch enable always disabled | Check enable; may be dead code |
| 327 | inferring latch | Add `else`/`default` clause |

### Category 7: Clock/Timing Issues
**Pattern**: Clock or event control problems

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 85 | no event control | Add `@(*)` or sensitivity list |
| 91 | ambiguous clock | Use single edge |
| 434 | mixed level/edge triggers | Separate blocks |
| 462 | no clock in event control | Add clock signal |
| 567 | signal not in sensitivity | Add to sensitivity list |
| 614 | signal not in sensitivity | Add to sensitivity list |

### Category 8: Loop/Recursion Issues
**Pattern**: Infinite or unsupported iteration

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 120 | call depth exceeded | Reduce recursion; flatten |
| 316 | module recursion | Remove circular instantiation |
| 317 | recursive instantiation | Flatten hierarchy |
| 403 | loop limit exceeded | Add iteration limit |

### Category 9: Port Connection Issues
**Pattern**: Instance port connection problems

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 34 | wrong argument count | Add missing arguments |
| 349 | wrong connection count | Fix port count |
| 422 | missing association | Add missing connection |
| 476 | no matching instance | Fix defparam path |
| 650 | too many ports | Remove excess |
| 685 | variable in output port | Change to net or use procedural |

### Category 10: Unsupported Constructs
**Pattern**: Feature not supported for synthesis

| Oasys ID | Issue | Fix Pattern |
|----------|-------|-------------|
| 27 | not supported | Use synthesizable alternative |
| 277 | exponentiation not supported | Use shifts or LUT |
| 312 | unsynthesizable construct | Replace with supported construct |
| 589 | case/wildcard equality | Use == or != |

---

## Critical Warning Resolution (8 IDs)

| ID | Issue | Resolution |
|----|-------|------------|
| 4442 | BlackBox unconnected pin | Connect pin or document as intentional |
| 5796 | RAM conflicting writes | Use different addresses or add arbitration |
| 5967 | Mixed tristate reset | Split vector; separate tristate assignments |
| 6030 | Inout without tristate | Use explicit direction or tristate buffer |
| 6781 | Nested interface items | Pass items explicitly; flatten usage |
| 6790 | Constant reassigned | Remove assignment or change to variable |
| 6858 | Multi-driven with constant | Remove duplicate drivers |
| 13329 | Null range | Fix range bounds and direction |

---

## Complete ID List by Severity

### Critical Warnings (8 IDs)
4442, 5796, 5967, 6030, 6781, 6790, 6858, 13329

### Errors (457 IDs)

**27-99**: 27, 34, 35, 36, 78, 83, 91, 99

**120-285**: 120, 196, 206, 211, 248, 250, 258, 274, 277, 279, 280, 281, 285

**316-439**: 316, 317, 318, 349, 354, 366, 403, 421, 422, 426, 428, 434, 436, 439

**462-561**: 462, 465, 470, 476, 478, 485, 493, 509, 517, 521, 522, 523, 524, 549, 550, 561

**647-734**: 647, 650, 658, 659, 660, 685, 690, 734

**837-983**: 837, 859, 944, 981, 982, 983

**1067-1958**: 1067, 1101, 1560, 1561, 1576, 1621, 1697, 1718, 1819, 1859, 1875, 1892, 1925, 1956, 1958

**2043-2948**: 2043, 2056, 2068, 2088, 2105, 2112, 2114, 2119, 2121, 2132, 2148, 2158, 2179, 2195, 2203, 2210, 2212, 2254, 2291, 2340, 2371, 2409, 2415, 2458, 2481, 2496, 2506, 2512, 2543, 2548, 2549, 2551, 2558, 2572, 2577, 2590, 2599, 2666, 2680, 2691, 2716, 2757, 2784, 2798, 2856, 2901, 2902, 2908, 2910, 2913, 2914, 2916, 2918, 2948

**3302-4760**: 3302, 3380, 3391, 3492, 3493, 3522, 3892, 3918, 3942, 4169, 4374, 4556, 4564, 4675, 4678, 4687, 4752, 4760

**5535-6902**: 5535, 5549, 5551, 5731, 5732, 5743, 5765, 5767, 5826, 5832, 5838, 6036, 6038, 6043, 6046, 6058, 6062, 6073, 6156, 6716, 6735, 6777, 6799, 6828, 6901, 6902

**7028-8986**: 7028, 7051, 7054, 7055, 7068, 7136, 7213, 8742, 8863, 8879, 8882, 8891, 8892, 8895, 8896, 8899, 8902, 8910, 8922, 8923, 8925, 8926, 8927, 8936, 8937, 8943, 8945, 8958, 8960, 8983, 8985, 8986

**9101-9963**: 9101, 9109, 9111, 9112, 9114, 9116, 9117, 9121, 9123, 9143, 9173, 9174, 9179, 9182, 9185, 9195, 9210, 9212, 9222, 9223, 9224, 9248, 9249, 9250, 9280, 9287, 9288, 9312, 9315, 9339, 9372, 9380, 9382, 9386, 9397, 9398, 9400, 9404, 9414, 9429, 9448, 9469, 9480, 9481, 9486, 9492, 9493, 9502, 9510, 9563, 9565, 9570, 9571, 9587, 9597, 9606, 9610, 9652, 9653, 9654, 9664, 9696, 9699, 9714, 9721, 9729, 9735, 9736, 9747, 9764, 9767, 9771, 9775, 9842, 9843, 9844, 9871, 9872, 9873, 9875, 9891, 9901, 9910, 9913, 9915, 9921, 9923, 9926, 9932, 9934, 9960, 9963

**10021-11899**: 10021, 10033, 10039, 10042, 10043, 10047, 10059, 10093, 10095, 10097, 10140, 10149, 10157, 10180, 10219, 10221, 10275, 10287, 10298, 10300, 10302, 10304, 10307, 10314, 10320, 10327, 10334, 10344, 10366, 10387, 10388, 10438, 10440, 10458, 10478, 10487, 10491, 10495, 10507, 10511, 10515, 10530, 10531, 10551, 10561, 10571, 10592, 10604, 10606, 10615, 10616, 10617, 10631, 10632, 10636, 10656, 10661, 10672, 10681, 10690, 10694, 10703, 10712, 10713, 10714, 10729, 10738, 10752, 10768, 10790, 10791, 10797, 10813, 10826, 10829, 10848, 10872, 10882, 10886, 10890, 10911, 10929, 10940, 10945, 10949, 10976, 10992, 11001, 11055, 11057, 11062, 11065, 11066, 11067, 11077, 11095, 11098, 11112, 11117, 11119, 11121, 11137, 11145, 11152, 11155, 11160, 11161, 11166, 11182, 11187, 11212, 11213, 11214, 11218, 11229, 11234, 11242, 11259, 11261, 11262, 11287, 11290, 11303, 11323, 11324, 11325, 11356, 11365, 11495, 11501, 11534, 11536, 11537, 11558, 11560, 11574, 11584, 11587, 11615, 11635, 11772, 11803, 11815, 11817, 11899

**12188-13331**: 12188, 12189, 12523, 12526, 13076, 13099, 13162, 13322, 13324, 13331

### Warnings (86 IDs)

**85-693**: 85, 90, 98, 151, 153, 264, 290, 302, 308, 311, 312, 324, 327, 330, 445, 506, 547, 567, 589, 613, 614, 689, 693

**2887-3936**: 2887, 2897, 2898, 3301, 3330, 3332, 3514, 3819, 3848, 3917, 3919, 3936

**4179-5974**: 4179, 4446, 4557, 4767, 5411, 5639, 5640, 5730, 5733, 5753, 5837, 5863, 5928, 5974

**6014-7257**: 6014, 6026, 6040, 6060, 6090, 6104, 6426, 6430, 6774, 6778, 6849, 6850, 6880, 6896, 7023, 7032, 7043, 7071, 7129, 7134, 7137, 7143, 7186, 7193, 7257

**11357-13374**: 11357, 11376, 11581, 11585, 12181, 13159, 13161, 13234, 13327, 13328, 13373, 13374

---

## Mandatory Workflow (Per Message)

```
1. Parse log - extract [Synth 8-XXX] messages (Oasys synthesis)
2. For each Oasys message ID:
   a. Check Resolution Pattern Categories above
   b. If pattern matches, apply the fix pattern
   c. If no pattern match, look up message in oasys_severity_lists.csv
   d. Read testcase in synth_code_8_xxx_testcase/*/8_XXX/ for example RTL
3. Always read the RTL source at reported file:line before generating a fix
4. Never fabricate code - only propose fixes based on actual source context
```

---

## Reference Files

- **CSV**: `oasys_severity_lists.csv` - Full message catalog (5000+ entries)
- **Testcases**: `synth_code_8_xxx_testcase/` - 551 testcase folders with RTL samples
- **Message Handlers**: `message-handlers.md` - Per-ID fix strategies
