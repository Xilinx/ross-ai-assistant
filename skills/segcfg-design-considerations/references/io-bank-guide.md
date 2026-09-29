# IO Bank and Clocking Constraints for Segmented Configuration

## Mixed IO Banks

An IO bank is "mixed" (shared) when it contains pins used by both the PS/boot domain and the PL domain.

### Behavior
- The entire IO bank is programmed when **boot.pdi** is loaded (full-bank granularity in 2025.2)
- IO pins connected to PL logic become **electrically active** before the PL domain is configured
- These pins **cannot change functionality** during PL Reload — they remain active until a full device reconfiguration

### Design Guidance
- Minimize or eliminate mixed IO banks when PL Reload is planned
- Accepted: IO in mixed banks can be used as simple GPIO before PL loads
- Not recommended: complex PL logic behind IO in a shared bank if PL Reload is planned

### DRC Checks
- **SEGCONFIG-1**: Reports any shared IO bank (warning)
- **SEGCONFIG-2**: Error if a PL port in a shared bank uses non-LVCMOS IOSTANDARD

### Resolution for SEGCONFIG-2
```tcl
# Change IOSTANDARD of the offending PL port to LVCMOS
set_property IOSTANDARD LVCMOS18 [get_ports <port_name>]
```

## X5IO Bank Granularity (Gen 2 Devices Only)

In Versal AI Core Gen 2 and Prime Gen 2 devices, X5IO banks can be selectively shared.

### Supported Split Configuration
The third X5IO bank from a DDR site can be allocated to the PL partition when:
- Only two half-banks of DDR5 are used in the BOOT partition
- Supported DDR configurations:
  - Comp(DDR5) x4: 16-bit or 20-bit data, 16/17/18-bit address
  - Comp(LPDDR5) x16: 16-bit data, any address width
  - Comp(LPDDR5) x32: 32-bit data, any address width

### Master Bank Requirement (Gen 2)
- Master bank (bank 700 — leftmost along device bottom) must be in the boot partition if used
- **DRC SEGCONFIG-5**: Reports if the master X5IO bank is used in PLD but not in boot
- Workaround: Use the master bank for DDR in boot, or leave it completely unused
- AR39028: Additional restrictions on DDRMC5 usage with PL Reload (see Known Issues)

## Clocking Constraints

### SEGCONFIG-3: Clock resource in boot design
Clocking tiles (BUFGCE, MMCM, PLL, etc.) used by the boot domain are reported as a warning.
In most designs, clocking resources should reside in the PLD domain.

**Resolution**: Move clocking resources out of the boot partition. Ensure no CIPS-related logic drives CLK resources that are shared with PL.

### SEGCONFIG-4: Shared XPLL or CLK_PLL_AND_PHY tile
XPLL and CLK_PLL_AND_PHY tiles must not be shared between boot and PLD partitions.

**Resolution**: Reorganize clock planning so each tile is dedicated to one partition.

## General IO Planning Recommendations

1. Dedicate full IO banks to either boot (PS/DDR) or PL — avoid mixed banks
2. Assign DDR IO first; remaining banks available for PL
3. Use the `get_dfx_footprint -seg_config_boot` command to see the boot partition tile footprint:
   ```tcl
   get_dfx_footprint -seg_config_boot
   ```
4. Use this footprint to define pblock constraints that keep PL logic out of boot-partition tiles
