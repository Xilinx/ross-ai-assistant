<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RTL Lint Report — Example (Clean Design)

```markdown
# RTL Lint Report

## Summary
- **Project:** my_design
- **Top Module:** top
- **Part Number:** xc7k70tfbg676-2
- **Analysis Status:** ✅ **PASSED**
- **Total Critical Warnings:** 0
- **Total Warnings:** 0
- **Total Errors:** 0

## Vivado Lint Results

✅ **Vivado Output:** "Total of 0 linter message(s) generated"

The design passes all Vivado lint checks. The recommendations below are
best-practice coding guidelines from UG901.

## Best Practice Recommendations (Optional)

1. **Use Non-Blocking Assignments in Sequential Logic**
   - Always use `<=` in clocked always blocks
   - Reference: UG901 Chapter 4 "Sequential Logic Coding"

2. **Add Explicit Reset Signals**
   - Include synchronous or asynchronous reset for predictable power-on behavior
   - Reference: UG901 Reset Coding Guidelines

3. **Document Module Interfaces**
   - Add header comments describing functionality, I/O, and timing

## Next Steps

- Design is lint-clean — proceed with synthesis or further analysis
- Consider applying best-practice recommendations for production code
```
