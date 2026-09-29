# Vivado Hardware Manager Guide for Segmented Configuration

## Overview

Vivado Hardware Manager provides a GUI and Tcl-accessible interface for programming Versal devices and configuration flash memory. For Segmented Configuration, it supports programming Boot and PLD PDIs sequentially, selective Boot-only or PLD-only programming, and flash programming with offset control.

## Prerequisite: Enable Segmented Configuration Mode (Gen 1 Only)

For first-generation Versal devices, the Segmented Configuration programming dialog requires a parameter:

```tcl
# Run in the Vivado Tcl Console before opening Hardware Manager
set_param labtools.enableSegmentedConfigFlow 1
```

This is NOT required for second-generation Versal devices (Segmented Configuration is always active).

## Connecting to Hardware

1. Open Hardware Manager: **Flow Navigator > Program and Debug > Open Hardware Manager**
2. Click **Open Target > Auto Connect**
3. The JTAG chain is scanned; the Versal device appears in the Hardware window

```tcl
# Tcl equivalent:
open_hw_manager
connect_hw_server
open_hw_target
```

## Programming the Device (JTAG)

### GUI Method
1. Right-click the device in the Hardware window → **Program Device**
2. In the Program Device dialog:
   - Check **Enable segmented configuration mode** (first-gen devices)
   - **Boot PDI File**: Browse to `<design>_boot.pdi`
   - **Boot LTX Probe File**: Browse to `<design>_boot.ltx` (optional, for ChipScope debug)
   - **PLD PDI File**: Browse to `<design>_pld.pdi`
   - **PLD LTX Probe File**: Browse to `<design>_pld.ltx` (optional)
   - Click **Program**

### Tcl Method
```tcl
# Get the hardware device handle
set hw_device [get_hw_devices]

# Program Boot PDI only
set_property PROGRAM.FILE {impl_1/<design>_boot.pdi} $hw_device
program_hw_devices $hw_device

# Program PLD PDI (after boot is loaded)
set_property PROGRAM.FILE {impl_1/<design>_pld.pdi} $hw_device
program_hw_devices $hw_device
```

## Boot-Only Programming

To load only the boot image (test PS domain in isolation):
- In the Program Device dialog, fill in only the Boot PDI field
- Leave the PLD PDI field empty
- Click Program

This enables isolated testing of the PS domain, DDR, and hard blocks.

## PLD-Only Programming (PL Reload)

To load only the PLD image (boot must already be resident):
- In the Program Device dialog, fill in only the PLD PDI field
- Leave the Boot PDI field empty
- Click Program

Hardware Manager loads the PLD image; PLM handles isolation and PL reconfiguration automatically.

## Flash Programming (Configuration Memory)

### Setup
1. Right-click the device → **Add Configuration Memory Device**
2. Select the appropriate flash part for your board:
   - VCK190: MT25QU02GCBB (Micron 2Gb SPI Quad)
   - VEK280: check board documentation
3. Right-click the memory device → **Program Configuration Memory Device**

### Offset Configuration
When programming a Segmented Configuration design to flash:
- **At least one offset must be non-zero** (Boot PDI and PLD PDI must be at different locations)
- Offsets must be **multiples of the flash sector size**: 64KB (0x10000)
- Ensure enough space for Boot PDI before declaring PLD offset

```
Example layout for a ~10MB boot PDI:
  Boot PDI offset:  0x00000000  (start of flash)
  PLD PDI offset:   0x00A00000  (10MB, 64KB-aligned)
```

### GUI Fields
- **Bitstream file**: `<design>_boot.pdi` with offset `0x00000000`
- **Second bitstream file**: `<design>_pld.pdi` with appropriate offset

## ChipScope Debug with Segmented Configuration

- **Boot partition** ILA/VIO cores: activated when boot PDI loads → use `<design>_boot.ltx`
- **PLD partition** ILA/VIO cores: activated when PLD PDI loads → use `<design>_pld.ltx`
- Refresh the Hardware window (right-click device → Refresh Hardware) after each PDI load
- New debug cores appear automatically after refresh

## Reference

[Vivado Design Suite User Guide: Programming and Debugging (UG908)](https://docs.amd.com/r/en-US/ug908-vivado-programming-debugging)
