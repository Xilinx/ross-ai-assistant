<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-51: No common phase between related clocks from parallel CMBs

## Metadata

- **Check ID**: TIMING-51
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

Two clocks from parallel Clock Modifying Blocks (MMCM/PLL/XPLL) are timed together but have no phase relationship because at least one input divider is not 1.

### Full Message Description property.

```
The clocks <CLOCK_GROUP> and <CLOCK_GROUP> are timed together but have no phase relationship. The design could fail in hardware. The clocks originate from two parallel Clock Modifying Blocks and at least one of the MMCM or <MESSAGE_STRING> clock dividers is not set to 1. To be safely timed, all MMCMs or <MESSAGE_STRING> involved in parallel clocking must have the clock divider set to 1.
```
**Example:**
```
The clocks clkout0_mmcm_a and clkout0_mmcm_b are timed together but have no phase relationship. ... at least one of the MMCM or PLL clock dividers is not set to 1. ...
```

### Explanation
Two CMBs fed by the same input clock only keep a deterministic phase relationship when `DIVCLK_DIVIDE` is 1 on every one of them. Any other value loses the relationship between the parallel CMBs because the internal divider can start in different phases. Paths between the two clock domains are then effectively asynchronous even though the timer times them as synchronous.

**Why this matters in hardware, not just in simulation:** an MMCM/PLL's internal divider does not lock at a guaranteed, repeatable edge relative to its reference — the edge it locks to on power-up/reset depends on physical conditions (temperature, voltage, routing, exact reset timing) that differ chip to chip and power-cycle to power-cycle. Time zero in simulation is not representative of time zero in hardware. The practical effect: the two CMB outputs can come up **in phase** on one power-up and **out of phase** on the next — the same bitstream, the same board, can behave differently run to run. A design that only validated correct behavior in simulation (where the two dividers are implicitly in phase) is not proven safe in hardware.

**DO**
✅ Identify both CMBs (the clocks' source cells) and read `DIVCLK_DIVIDE` on each
✅ Recommend the design be updated (CASE 1) as the default — this is the only option that removes the phase ambiguity outright
✅ If the user wants to waive instead of fixing the design, confirm which (if any) of the three CASE 2 justifications genuinely applies — never assume one holds

**DO NOT**
❌ Add `set_clock_groups -asynchronous` or a waiver without asking. Either is only valid under one of the CASE 2 justifications below
❌ Change `DIVCLK_DIVIDE` without checking that VCO range is still legal
❌ Accept "it can be considered asynchronous" as a justification without the user confirming a full CDC analysis has been (or will be) done

## Flow
1. Find the CMB cell behind each clock and read the dividers.
```tcl
foreach clk {<clk_a> <clk_b>} {
  set src [get_property SOURCE_PINS [get_clocks $clk]]
  set cmb [get_cells -of [get_pins -of_objects [get_nets -of $src] -filter {DIRECTION==OUT}]]
}
foreach c [get_cells -hier -filter {REF_NAME=~MMCM* || REF_NAME=~PLL* || REF_NAME=~XPLL*}] {
  puts "$c DIVCLK_DIVIDE=[get_property DIVCLK_DIVIDE $c]"
}
report_clock_interaction -clocks [get_clocks {<clk_a> <clk_b>}]
```
Use `<skill_dir>/references/CLOCK_TRACING.md` (call `methodology_check::trace_clock_back_to_source`) to go back from each clock to its CMB.

2. Present exactly two options to the user: **update the design** (CASE 1) or **waive** (CASE 2). Lead with CASE 1 as the recommended default — it is the only option that removes the phase ambiguity itself rather than relying on a justification for tolerating it.

**CASE 1: Update the design**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: User agrees to fix the design (default recommendation)
* **Assumption**: VCO frequency stays in range after the change, so `CLKFBOUT_MULT` and `CLKOUTn_DIVIDE` need re-computation
* **Prompt**: Recommend setting `DIVCLK_DIVIDE` to 1 on every parallel CMB (via the clocking wizard IP or cell property) and re-balancing multiply/divide so the output frequencies do not change. Explain the hardware-vs-simulation lock-phase risk (see Explanation) as the reason a fix is preferred over a waiver.

**CASE 2: Waive**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: User does not want to change `DIVCLK_DIVIDE`
* **Prompt**: A waiver is only acceptable if the user confirms **at least one** of the following three justifications is actually true for this design — ask explicitly which one applies, do not assume:
  1. **Phase detector circuit.** The design contains a phase detector that measures the phase difference between the two clocks and controls the transition/switchover between them, so the ambiguous lock phase is handled in hardware, not assumed away.
  2. **Static signals.** The signals crossing between the two clock domains are static (not actively toggling during normal operation), so phase ambiguity between the clocks cannot cause a functional failure.
  3. **Genuinely asynchronous, timing ignored.** The crossing can be treated as asynchronous and timing between the clocks ignored. This justification is **not** valid on its own assertion — it requires the user to perform (or confirm they have already performed) a **full CDC analysis** of every signal crossing between `<clk_a>` and `<clk_b>`. Defer that analysis per [TIMING-9](./TIMING-9.md)/[TIMING-10](./TIMING-10.md); do not accept the waiver until the user confirms the CDC analysis is complete. If accepted, also apply:
```tcl
set_clock_groups -asynchronous -group [get_clocks <clk_a>] -group [get_clocks <clk_b>]
```
  If none of the three justifications hold, do not offer the waiver — go back to CASE 1.
* Once a justification is confirmed, generate the waiver: `<USER> - <DATE> - <REASON>`, with `<REASON>` recording which of the three justifications was confirmed.

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_clock_interaction` shows the intended relationship (timed or asynchronous) for the clock pair
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Change | clk_a | clk_b |
|--------|--------|-------|-------|
| **Before** | `DIVCLK_DIVIDE` | 2 | 1 |
| **After** | `DIVCLK_DIVIDE` (or async group) | 1 | 1 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-51
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
- [TIMING-6](./TIMING-6.md), [TIMING-7](./TIMING-7.md) - related clock relationship checks
