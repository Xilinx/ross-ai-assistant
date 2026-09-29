<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# [CHECK-ID]: [Brief title]

## Metadata

- **Check ID**: [e.g., TIMING-1]
- **Severity**: [Critical Warning | Warning | Advisory]
- **Hierarchy Name**: [e.g., Timing.Bad Practice]
- **First Release**: [Vivado version, e.g., 2013.3]

## Description

[Brief description of the methodology check]

### Full Message Description property.

```
[Full message template with <PLACEHOLDERS>]
```
**Example:**
```
[Real violation message from actual design]
```
[Explanation of placeholders if needed]

### Explanation
[Detailed explanation of why this violation occurs and what it indicates]

**DO**
✅ [Required action or best practice]
✅ [Another required action]
✅ [Another required action]

**DO NOT**
❌ [Common mistake to avoid]
❌ [Another mistake to avoid]


## Flow
1. [First step with clear action]
2. [Second step - include TCL commands if relevant]
```tcl
[Example TCL command]
```

**DO NOT**
❌ [Specific caution for this step]
❌ [Another caution]

3. [Next step]

**DO**
✅ [Best practice for this step]
✅ [Reference to helper documents if applicable]

**DO NOT**
❌ [Caution]
❌ [Caution]

3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: [Automated Resolution | User Resolution]
* **Confidence**: [High | Medium | Low]
* **Usefulness**: [High | Medium | Low]
* **When**: [Conditions when this case applies]
1. [Resolution step 1]
2. [Resolution step 2]

**DO**
✅ [When to apply this case]

**DO NOT**
❌ [When NOT to apply this case]

**CASE 2:**
* **Type**: [Automated Resolution | User Resolution]
* **Confidence**: [High | Medium | Low]
* **Usefulness**: [High | Medium | Low]
* **When**: [Conditions when this case applies]
1. [Resolution step 1]
2. [Resolution step 2]

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: [High | Medium | Low]
* **Usefulness**: [Low | Medium | High]
* **Prompt**: 
```
[Prompt text to present to user with options a), b), c), etc.]
```
* **Explanation**: [Provide explanation format]
* **Examples**: 
```
[Example waiver or explanation format]
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] [Check-specific verification item]


## Final Report
[Description of what to include in the final report]

[If applicable, include a table showing before/after state]

| Aspect | [Column 1] | [Column 2] | [Column 3] | [Column 4] |
|--------|-----------|-------------|----------|------------------------|
| **Before** | [Value] | [Value] | [Value] | [Value] |
| **After** | [Value] | [Value] | [Value] | [Value] |


## References

- [UltraFast Design Methodology Guide (UG949)](references)
- [Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)](references)
- [Using Constraints (UG903)](references)
- [Related methodology documents if applicable](../references/RELATED_DOC.md)
