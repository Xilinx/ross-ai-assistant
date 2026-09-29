---
name: segcfg-design-check
description: Run Segmented Configuration DRCs and pr_verify design verification checks. Use when validating a design for IO bank conflicts, clocking issues, DDRMC sharing violations, or when comparing routed checkpoints for PL reload compatibility.
license: MIT
compatibility: Requires Vivado and routed design checkpoints for pr_verify comparisons.
metadata:
   version: "1.0.0"
   stage: beta
allowed-tools: Read Bash Write
---

Guide the user through running Segmented Configuration DRCs and pr_verify, and generate the corresponding Tcl commands.

## Instructions

1. Read `references/segmented-drcs.md` for the full DRC list with error messages.
   Read `references/pr-verify-checks.md` for pr_verify error codes and what they check.

2. Ask the user which checks they need:
   - **Interactive DRCs** (post-synthesis): Validate IO banks, clocking, DDRMC resource sharing
   - **pr_verify** (post-route): Confirm NoC solution compatibility between a golden and variant design for PL reload

3. For **DRC checks**, generate Tcl:
   ```tcl
   # Run all Segmented Configuration-specific DRCs on post-synthesis checkpoint
   report_drc -checks {SEGCONFIG-1 SEGCONFIG-2 SEGCONFIG-3 SEGCONFIG-4 SEGCONFIG-5 SEGCONFIG-6 SEGCONFIG-7}
   ```

4. Explain each DRC and guidance on resolution:

   | DRC | Checks | Resolution |
   |-----|--------|-----------|
   | SEGCONFIG-1 | Shared IO bank (boot + PLD use same bank) | IO will not be reprogrammed on PL Reload; accept or move PL IO to a dedicated bank |
   | SEGCONFIG-2 | Non-LVCMOS PLD port in a shared IO bank | Change PLD port IOSTANDARD to LVCMOS |
   | SEGCONFIG-3 | Clocking tile used by boot design | Move clocking resources out of boot domain; typically should be in PLD |
   | SEGCONFIG-4 | XPLL or CLK_PLL_AND_PHY tile shared by boot and PLD | Reorganize clock planning to dedicate tiles to one partition |
   | SEGCONFIG-5 | Used master X5IO bank not in boot partition (Gen 2) | Move master bank DDR connection to boot partition or leave unused |
   | SEGCONFIG-6 | Reconfigurable pblock contains boot partition resources | Resize pblock: `get_dfx_footprint -seg_config_boot` shows boot tile footprint |
   | SEGCONFIG-7 | DDRMC subsystem tile shared between boot and PLD partition | Redesign DDR connectivity to avoid cross-partition DDRMC subsystem sharing |

5. For **pr_verify** (PL Reload compatibility), generate Tcl:
   ```tcl
   # Compare golden (initial) design against a PL variant (additional)
   # The -initial DCP must be the golden design containing the superset of connectivity
   pr_verify -segcfg_only -initial <golden_design>_routed.dcp -additional <variant_design>_routed.dcp
   ```

6. Explain key pr_verify error codes from the reference file:
   - **HDPRVerify-01/02/03/37**: Part, package, speed grade, and SEGMENTED_CONFIGURATION property must match
   - **SegConfig-Validation-1/2**: Same number of boot paths and endpoints required
   - **SegConfig-Validation-3**: Boot path settings (protocol, dest ID, slave addresses, TC values) must match
   - **SegConfig-Validation-4**: Boot path routing (location, virtual channel, physical routes) must match
   - **SegConfig-Validation-10**: PS/CPM-to-PL interface connections — PLD cannot add new connections
   - **SegConfig-Validation-12**: Boot partition NMU configurations — NSU addresses and IDs must match
   - **SegConfig-Validation-13–16**: All NoC sites in boot partition must match
   - **SegConfig-Validation-17**: PLD cannot add new connections from a boot-path NMU
   - **SegConfig-Validation-18**: PLD cannot add new IO to boot-partition banks
   - **SegConfig-Validation-20**: Boot NMU remap entries cannot be added in PLD designs

7. If pr_verify fails with **SegConfig-Validation-12**, check that all NSU instances connected to boot NMUs have identical addresses and IDs between golden and variant designs. Use `read_noc_solution` to import the golden .ncr file before implementation.


8. If pr_verify fails with **SegConfig-Validation-18**, check that the PLD design has not added new IO usages in banks included in the boot partition. These banks are used for DDRMC and are not reprogrammed with the PLD image, so adding new IOs here would cause runtime errors. 
   - Read `references/x5io-bank-sharing-debug.md` for detailed diagnosing, guidance and resolving IO bank sharing issues in Segmented Configuration designs.

9. Remind the user that `pr_verify` is **required** for all PL Reload use cases and must be run on post-route checkpoints.

10. Examples of checks performed represented in below table:

| Type of Check | Conditions Required |
|----------|-------------|
| Boot path endpoints | The same number of booth paths and end points are required |
| Boot path settings​  |  Same endpoint type (master/slave)​, Same protocol used​, Same destination ID​, Same slave addresses​ |
| Boot path routing | Same endpoint location​, Same virtual channel​, Same physical routes |
| Boot path masters connections​ | The secondary PLD design cannot add new connection from a boot path NMU​ |
| Boot partition IO usage | The secondary PLD design cannot add new IO to banks included in the boot partition. |
| Boot partition IO usage |     These banks are used for DDRMC and are not reprogrammed with the PLD image |




