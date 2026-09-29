# pr_verify Checks for Segmented Configuration

Use `pr_verify` to compare routed checkpoints from different design runs within a project or between projects to check if the NoC solution is identical. This is **required** for PL reload use cases. **Always** use  -segcfg_only option with pr_verify.

## Syntax

```tcl
pr_verify -segcfg_only -initial <first_design>_routed.dcp -additional <second_design>_routed.dcp 
```

The first design checkpoint (`-initial`) must be the "golden" design that established the NoC solution reused in subsequent projects/runs. This initial run must contain the greatest usage of PS/PL and NoC boundary connections to establish the superset of possible connectivity.

## Check Details

### HDPRVerify-01, HDPRVerify-02, HDPRVerify-03, HDPRVerify-37: Part & Design Property

**Checks:**
- The same part/package/speedgrade must be targeted in both checkpoints
- The `SEGMENTED_CONFIGURATION` property must be present in both designs

### SegConfig-Validation-1, SegConfig-Validation-2: Boot Path Endpoints

**Checks:**
- The same number of boot paths are required in both designs
- The same number of end points are required

### SegConfig-Validation-3: Boot Path Settings

**Checks:**
- Same endpoint type (master/slave)
- Same protocol used
- Same destination ID
- Same slave addresses
- Same readTC/writeTC

### SegConfig-Validation-4: Boot Path Routing

**Checks:**
- Same endpoint location
- Same virtual channel
- Same physical routes

### SegConfig-Validation-10: PS & CPM to PL Interface

**Checks:** Netlist connections from/to PS and CPM cells
- The PLD design **cannot add** new connections from/to PS or CPM netlist cell
- Error checking is limited to pins ending with `*RCLKCLK` or `*PL*VALID`; changes to other pin names are reported as warnings
- The PLD design **can have fewer** connections from/to PS or CPM cell in secondary runs; this is tolerated as a warning

### SegConfig-Validation-12: Boot Partition NMU Configurations

**Checks:** Configuration of a NMU relies on address map and ID of its connected NSUs. For each NMU on a boot path:
- All connected NSUs must have the same addresses and IDs
- The secondary PLD design **cannot add** new connections to a boot path NMU
- The secondary PLD design **can have fewer** connections to a boot path NMU

**Known Issue:** `read_noc_solution` does not report an error if there are changes to NSU addresses connected to boot path NMU instances. If changes have been made, `pr_verify` will report `[Dfx 88-139] SegConfig-Validation-12`. Review NoC boot path connectivity to ensure all NSU instances have the same addresses and IDs.

### SegConfig-Validation-13, 14, 15, 16, 3: All NoC Sites in Boot Partition

**Checks:**
- Same number of NoC sites in boot and PLD designs
- Same NoC endpoint type, protocol, destination ID, slave addresses
- Same readTC/writeTC

### SegConfig-Validation-17: Boot Path Master Connections

**Checks:**
- The secondary PLD design **cannot add** new connections from a boot path NMU

### SegConfig-Validation-18: Boot Partition IO Usage

**Checks:**
- The secondary PLD design **cannot add** new IO to banks included in the boot partition
- These banks are used for DDRMC and are not reprogrammed with the PLD image

### SegConfig-Validation-20: Boot NMU Remap Consistency

**Checks:**
- New remap entries cannot be added in secondary PLD designs

## Known Limitations

- `pr_verify` does not check all possible mismatches in the processor domain by design. Users should ensure the same CIPS customization and non-PL NoC connectivity when using PL reload.
- An NMU included in an initial_boot path could have different destIDs between configurations, causing PLM error when loading a PLD PDI from a different design than the one that generated the resident Boot PDI.
- These checks will be modified in future releases to align with new flexibility and expanded to confirm consistency on PL interfaces as needed.

## Interpreting Results

A successful pr_verify run confirms that:
1. The NoC solutions are identical between the two designs
2. The PLD images may be safely interchanged
3. Boot partition resources are consistent

If pr_verify fails, review the specific error code against the table above and correct the design accordingly before generating PDI images.
