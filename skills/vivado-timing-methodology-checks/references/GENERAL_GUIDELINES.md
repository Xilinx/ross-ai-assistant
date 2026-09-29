<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# General Methodology Check Resolution Guidelines

This document provides general guidance for resolving timing methodology violations when a specific reference document (e.g., `TIMING-X.md`) does not exist for the check ID.

**Priority**: Specific check reference files (`TIMING-X.md`) take precedence over these general guidelines when they exist.

---

## Table of Contents
- [Understanding Methodology Violations](#understanding-methodology-violations)
- [General Resolution Process](#general-resolution-process)
- [Common Violation Categories](#common-violation-categories)
- [Agent Understanding Verification Checklist](#agent-understanding-verification-checklist)
- [Resolution Patterns](#resolution-patterns)
- [When to Ask for User Input](#when-to-ask-for-user-input)

---

## Understanding Methodology Violations

Methodology violations indicate potential timing issues, constraint errors, or design practices that may lead to:
- Incorrect timing analysis results
- Functional failures in hardware
- Unreliable behavior across PVT (Process, Voltage, Temperature) corners
- Incorrect clock domain crossing handling

**Key Principle**: Every violation exists for a reason. Understanding the root cause and resolving correctly is more important than simply clearing the violation count.

---

## General Resolution Process

### 1. Analyze the Violation

**Extract and review:**
- `RULE` - The check ID (e.g., TIMING-16)
- `NAME` - The specific violation instance (e.g., TIMING-16#1)
- `SEVERITY` - Critical Warning, Warning, Info, Advisory
- `DETAILS` - Full description from Vivado
- `NETLIST_ELEMENT` - The specific object(s) causing the violation

**Understand context:**
- What timing constraint is involved?
- What design element is flagged?
- What is the design intent for this element?
- Are there related constraints that might conflict?

### 2. Determine Root Cause

Ask yourself:
- Is this a constraint error (wrong value, applied in the wrong place, missing constraint, conflicting constraint)?
- Is this a design issue (incorrect clock topology, improper CDC handling)?
- Is this expected behavior that should be waived?
- Does the violation indicate missing design information?

### 3. Identify Resolution Type

Classify as:
- **AUTO**: Can be fixed automatically based on clear rules
  - Redundant constraints
  - Clearly incorrect values (e.g., negative period, clock pessimism is turned off)
  - Missing insertion delay
  
- **USER**: Requires user decision, can not be certain what the user intends
  - Ambiguous clock relationships
  - Choice between multiple valid solutions
  - Not clear which value the user intends
  - Fundamental design changes
  - Waiver justification

### 4. Verify Understanding Before Acting
- Print understanding information to `agent.log` in a clear readable format using tables
- Make an assessment of confidence level `high||med||low` for each section
- Make an overall assessment of confidence level.

**CRITICAL**: Before proposing or applying any fix, complete the [Agent Understanding Verification Checklist](#agent-understanding-verification-checklist) below.

---

## Common Violation Categories

### Clock Definition Issues
**Examples**: Missing clocks, incorrect periods, wrong sources

**General Resolution**:
1. Trace the clock network to identify the true source
2. Verify the intended frequency
3. Check if user generated clocks are needed for clock modifying blocks or can the auto definition be used
4. For USER decisions: Present options with tradeoffs

### Clock Relationship Issues
**Examples**: Missing asynchronous relationships, incorrect synchronous grouping

**General Resolution**:
1. Analyze clock domain crossing paths
2. Determine if clocks are truly asynchronous or related
3. For asynchronous clocks: `set_clock_groups -asynchronous`
4. For related clocks: Verify master/derived relationship
5. For USER decisions: Ask about design intent for the clock domains

### Constraint Conflicts
**Examples**: Over-constrained paths, contradictory requirements

**General Resolution**:
1. Identify all constraints affecting the flagged path/object
2. Determine which constraint represents true design intent. Always ask the user if it is not clear.
3. Remove or modify conflicting constraints
4. For AUTO: Remove clearly redundant constraints
5. For USER: Present the conflict and ask which constraint is correct

### Missing Constraints
**Examples**: Unconstrained paths, missing I/O delays

**General Resolution**:
1. Identify what constraint type is missing
2. Determine if the path/object should be constrained
3. For USER: Always ask for timing requirements (never assume)
4. Collect: clock domain, delay values, interface type

---

## Agent Understanding Verification Checklist

Before proposing or implementing any resolution, verify your understanding by documenting the following in `agent.log`:

```
=== VIOLATION UNDERSTANDING: <CHECK_ID>#<N> ===

[ ] PROBLEM IDENTIFICATION
    What specific constraint or design element is problematic?
    → 

[ ] ROOT CAUSE ANALYSIS
    Why is this flagged as a violation?
    → 
    
    What rule or timing requirement is being violated?
    → 

[ ] DESIGN INTENT COMPREHENSION
    What is the designer trying to achieve with this element?
    → 
    
    Is this violation expected/acceptable given the design intent?
    → 

[ ] RESOLUTION UNDERSTANDING
    What is the correct resolution?
    → 
    
    Why is this resolution correct?
    → 
    
    What assumptions have been made to make this decision?
    → 

    What timing/functional impact does this resolution have?
    → 

[ ] ALTERNATIVES EVALUATION (if multiple options exist)
    What other resolutions were considered?
    → 
    
    Why is the selected resolution better than alternatives?
    → 

[ ] UNINTENDED CONSEQUENCES CHECK
    Could this fix negatively impact other constraints or design elements?
    → 
    
    Are there related violations that might be affected?
    → 

[ ] CLASSIFICATION
    Resolution type: [ ] AUTO  [ ] USER  [ ] WAIVER
    Justification for classification:
    → 

=== END VERIFICATION ===
```

**Rule**: If you cannot confidently fill out this checklist, classify the resolution as **USER** and ask for guidance.

---

## Resolution Patterns

### Pattern 1: Removing Redundant Constraints
**When**: Same constraint applied multiple times to the same object

**AUTO Criteria**:
- Constraints are byte-for-byte identical
- Applied to identical targets
- No variation in options/parameters

**Actions**:
1. Identify the duplicate lines in the XDC
2. Keep the first occurrence
3. Comment out subsequent duplicates with explanation
4. Document which file each duplicate came from

**Example**:
```tcl
# From timing_constraints.xdc (line 45) - KEPT
create_clock -period 10.000 [get_ports REF_CLK]

# From imported_constraints.xdc (line 12) - REMOVED: Duplicate of timing_constraints.xdc line 45
# create_clock -period 10.000 [get_ports REF_CLK]
```

### Pattern 2: Correcting Clock Periods
**When**: Clock period doesn't match actual frequency

**USER Criteria** (always ask):
- "What is the correct frequency for clock `X`?"
- "Should this be `Y` MHz or `Z` MHz?"
- Present current value and ask for confirmation

**AUTO Criteria** (rare):
- Only if documentation or IP core parameters explicitly state the frequency
- And violation shows a clear mismatch

**Actions**:
1. Get user confirmation of correct frequency
2. Calculate correct period (ns = 1000 / MHz)
3. Modify the `create_clock` command
4. Document the change with frequency conversion

### Pattern 3: Establishing Clock Relationships
**When**: Paths between clocks are not properly characterized

**USER Criteria** (default):
- "Are clocks `A` and `B` asynchronous to each other?"
- "Is clock `B` derived from clock `A`?"

**AUTO Criteria**:
- Only if netlist clearly shows MMCM/PLL generating one from another
- And no existing clock relationship constraints contradict this

**Actions**:
1. Trace clock sources using `all_fanin -flat -only_cells`
2. Identify relationship (asynchronous, generated, related)
3. For asynchronous: Add `set_clock_groups -asynchronous`
4. For generated: Convert to `create_generated_clock`
5. For related: Verify existing constraints are correct

### Pattern 4: Creating Waivers
**When**: Violation is expected and acceptable

**USER Criteria** (always):
- Must get explicit waiver justification from user
- Must document design intent
- Must confirm violation does not impact functionality

**Actions**:
1. Ask: "This violation appears to be `<description>`. Is this expected behavior?"
2. If yes, ask: "Please provide justification for waiving (will be recorded in design):"
3. Collect: Reason, any compensating design measures, verification performed
4. Generate waiver command with full documentation
5. Add tags for related waivers if part of a pattern

**Example Exchange**:
```
Agent: "TIMING-17 flags that clock CLK_100 has no input delay constraints. 
        Is this clock intended to be unconstrained?"
User:  "Yes, it's a free-running test clock, not used in functional paths"
Agent: "I'll create a waiver. Please provide justification:"
User:  "Test clock for debug only, isolated from functional logic"
```

---

## When to Ask for User Input

### Always Ask (USER) For:

1. **Design Intent Questions**
   - Clock frequencies and relationships
   - Whether paths are critical or can be ignored
   - Interface timing requirements
   - Whether violations are expected behavior

2. **Choice Between Valid Alternatives**
   - Multiple clocks could be the source
   - Either asynchronous or synchronous grouping is valid
   - Different constraint formulations achieve same goal

3. **Waivers**
   - Justification for accepting a violation
   - Confirmation that violation doesn't affect functionality

4. **Modifications with Broad Impact**
   - Changes that affect multiple constraints
   - Modifications to primary clock definitions
   - Anything that could impact timing closure

### Can Proceed Automatically (AUTO) When:

1. **Clear Constraint Errors**
   - Duplicate constraints (identical)
   - Constraints on non-existent objects
   - Syntax errors in constraint commands

2. **Obvious Redundancies**
   - Multiple identical timing exceptions
   - Superfluous constraints that are subsumed by others

3. **Netlist-Derived Information**
   - Generated clocks where source is unambiguous
   - Clock relationships evident from BUFG/MMCM topology

**Golden Rule**: When in doubt, classify as USER and ask.

---

## Output to agent.log

For every violation being resolved, output your verification checklist to `./results/agent.log`:

```bash
cat >> ./results/agent.log << 'EOF'

=== VIOLATION UNDERSTANDING: TIMING-16#1 ===

[ ✓ ] PROBLEM IDENTIFICATION
    What specific constraint or design element is problematic?
    → create_clock constraint on port CLK_100 with period 5.000 ns
    
[ ✓ ] ROOT CAUSE ANALYSIS
    Why is this flagged as a violation?
    → The actual clock frequency is 100 MHz (10 ns period), but constraint specifies 200 MHz (5 ns)
    
    What rule or timing requirement is being violated?
    → Clock period does not match the actual frequency delivered by the external source
    
... (continue with full checklist) ...

=== END VERIFICATION ===

EOF
```

This creates a record of your reasoning and ensures thorough understanding before action.

---

## Summary

**Remember**:
1. Read specific check reference files first (`TIMING-X.md`)
2. These general guidelines apply when no specific reference exists
3. Complete the understanding verification checklist before acting
4. When in doubt, ask the user
5. Document all reasoning in `agent.log`
6. Prioritize understanding over quick fixes

**The goal is correct resolution, not just clearing violation counts.**
