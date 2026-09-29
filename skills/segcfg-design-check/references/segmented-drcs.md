# Segmented Configuration Design Rule Checks (DRCs)

Design Rule Checks (DRC) can be called interactively for designs in Vivado. Several checks are specifically targeted to Segmented Configuration requirements, focusing on IO bank usage, clocking resources, and DDRMC sharing.

Run these DRCs on a **post-synthesis checkpoint** to analyze the design.

## Running DRCs

```tcl
# Run all Segmented Configuration DRCs
report_drc -checks {SEGCONFIG-1 SEGCONFIG-2 SEGCONFIG-3 SEGCONFIG-4 SEGCONFIG-5 SEGCONFIG-6 SEGCONFIG-7}

# Run a specific check
report_drc -checks {SEGCONFIG-1}

# Run via GUI: Reports > Report DRC, then select "Segmented Configuration" category
```

## DRC Check Details

### SEGCONFIG-1: Shared IO Bank

**Type:** Shared IO bank between boot and PLD partitions

**Message:** IO bank `<name>` is shared by the initial boot design and pld design. In Segmented Configuration flow, tiles and sites in the boot image will only be loaded once, this IO bank will not be reprogrammed when pld image is loaded.

**Impact:** When an IO bank contains IO for both PS and PL domains, the entire bank is configured during boot image delivery and is NOT reconfigured when the PL image loads. PL-connected IO in shared banks become active before the PL logic they connect to, and these IO cannot change functionality during PL Reload.

**Resolution:** Avoid mixed IO banks when possible. If unavoidable, understand that PL IO in shared banks is fixed for the lifetime of the boot image.

### SEGCONFIG-2: Non-LVCMOS PLD Port in Shared IO Bank

**Type:** IOSTANDARD violation for PLD ports in shared banks

**Message:** Pld port `<name>` is found in IO bank `<name>`, which contains another port `<name>` of the initial boot design. In a Segmented Configuration design, pld port in an IO bank that is shared with boot design must be configured as LVCMOS, please change IOSTANDARD property of `<name>` from `<current>` to LVCMOS.

**Impact:** PLD ports sharing an IO bank with boot design ports must use LVCMOS IOSTANDARD for compatibility.

**Resolution:** Change the IOSTANDARD property of the affected port(s) to LVCMOS:
```tcl
set_property IOSTANDARD LVCMOS18 [get_ports <port_name>]
```

### SEGCONFIG-3: Clock Resource in Boot Design

**Type:** Clocking tile used by boot design

**Message:** Clocking tile `<name>` is used by the initial boot design, this may lead to problem in a Segmented Configuration flow. Clocking resources are typically used by pld designs.

**Impact:** Clocking resources are typically part of the PLD partition. Having them in the boot partition can cause issues if they need to be reconfigured during PL reload.

**Resolution:** Review the design to ensure clocking resources are placed in the PLD partition unless they are genuinely needed for the boot partition.

### SEGCONFIG-4: Shared XPLL/CLK_PLL_AND_PHY Tile

**Type:** Shared clocking tile between boot and PLD partitions

**Message:** Tile `<name>` is shared by the initial boot design and pld design. XPLL and CLK_PLL_AND_PHY tiles should not be shared by the initial boot design and pld design.

**Impact:** These clocking tiles cannot be reliably shared between partitions. The boot partition programs them first; the PLD partition cannot reprogram them.

**Resolution:** Reorganize the design so that XPLL and CLK_PLL_AND_PHY tiles are used exclusively by one partition.

### SEGCONFIG-5: Master Bank X5IO Not in Boot Partition

**Type:** Used master bank X5IO not in boot partition (Gen 2 devices)

**Message:** Master bank X5IO port `<name>` in tile `<name>` is used in the secondary pld partition but not in the initial boot partition. In Segmented Configuration flow, used master bank X5IO must be in the boot partition or remain unused.

**Impact:** For Versal AI Edge Gen 2 and Prime Gen 2 devices, the master bank (bank 700, left-most bank along bottom of device) must either be in the boot partition or remain completely unused.

**Resolution:** Move the master bank usage to the boot partition, or leave the master bank unused entirely.

### SEGCONFIG-6: Reconfigurable Pblock Using Boot Resources

**Type:** Reconfigurable pblock contains boot partition resources

**Message:** Reconfigurable pblock `<name>` contains boot partition resources. Reconfigurable pblock cannot use boot partition resources. Please resize this pblock to remove boot partition tiles. To see the boot partition footprint, use command: `get_dfx_footprint -seg_config_boot`.

**Impact:** A reconfigurable pblock cannot overlap with boot partition resources, as these are not reprogrammed during PL reload.

**Resolution:** Resize the pblock to exclude boot partition tiles:
```tcl
# View the boot partition footprint
get_dfx_footprint -seg_config_boot

# Adjust pblock constraints accordingly
resize_pblock [get_pblocks <pblock_name>] -remove <boot_tiles>
```

### SEGCONFIG-7: DDRMC Subsystem Tile Sharing

**Type:** DDRMC subsystem tile shared between boot and PLD partitions

**Message:** DDRMC subsystem tile `<name>` at boot partition instance `<name>`, site `<name>` is shared with PLD partition instance `<name>`, site `<name>`. PLD can't load in this case. Please change design to avoid resources sharing of boot partition DDRMC subsystems and PLD partition DDRMC subsystems.

**Impact:** If a DDRMC subsystem tile is shared between partitions, the PLD image cannot be loaded. This is a critical error.

**Resolution:** Redesign the DDR connectivity so boot partition and PLD partition DDRMC subsystems use separate tiles. Ensure that each DDRMC instance is fully contained within one partition.
