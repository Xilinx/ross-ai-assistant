# CPM/PCIe Use Cases for Segmented Configuration

## Overview

Versal devices with CPM (Coherent Processing Module) blocks — CPM4, CPM5 (Gen 1), or MMI (Gen 2) — support PCIe endpoints as a method for delivering the PLD PDI image (QDMA-based loading).

Segmented Configuration **automatically** applies Tandem Configuration for CPM endpoints to meet the PCIe 120ms link training deadline.

## CPM Root Port Mode

- CPM Root Port is **not ready** until the PL domain is configured
- The CPM must be held in reset until the pld.pdi has been delivered
- The CPM must also be held in reset **during** any PL Reload event and released afterward
- Drivers must be aware of this startup sequence

## CPM Endpoint Mode (PCIe Device)

### Automatic Tandem Configuration
When CPM is configured as a PCIe endpoint, Segmented Configuration automatically structures boot.pdi as a Tandem PROM image:
- CPM and required elements are programmed and released first
- Remaining boot content loads subsequently
- This meets the 120ms link training goal without manual Tandem Configuration selection

**IMPORTANT**: Do NOT enable Tandem Configuration manually in CPM customization. An error is issued during `write_device_image` if both are selected simultaneously.

### QDMA-Based PLD Delivery

To load pld.pdi over the PCIe QDMA interface, PCIe must be declared as a secondary boot interface.

#### First-Generation Versal (CIPS IP — CPM4/CPM5)
Manual BIF edit is required:

1. After `write_device_image`, locate `<design>_boot.bif` in the implementation run directory
2. Open the file and find the line: `id = 0x2`
3. Insert `boot_device { pcie }` on the line **after** `id = 0x2`:
   ```
   ...
   id = 0x2
   boot_device { pcie }    <-- INSERT THIS LINE
   ...
   ```
4. Regenerate the boot PDI with the edited BIF:
   ```bash
   bootgen -arch versal -image <design>_boot.bif -w -o <design>_boot.pdi
   ```

Without this modification, PLM will report the following error when loading via QDMA:
```
/dev/qdma01000-MM-0, W off 0x102100000, 0x578730 failed -1. write file: Input/output error.
```

#### Second-Generation Versal (PS Wizard — MMI)
No manual BIF edit required. Configure in the PS Wizard IP:
- Navigate to: **Boot Mode (PLM)** category
- Set **Secondary Boot** pulldown to: **MMI_PCIe**

## CPM4/CPM5 Feature Restrictions Until PL Loads

The following PCIe features are NOT available until pld.pdi is loaded:
- PCIe Extended Configuration Space (requires PL logic)
- QDMA multi-function (uses PL mailbox, probed during driver load)

Drivers must be structured to defer probing for these capabilities until after the PL domain is configured.

## Design Reference

For CPM5 QDMA example design using Segmented Configuration, see:
- Vivado Example Designs: "Versal CPM5 QDMA Based Acceleration System Design"
- CEDStore: `https://github.com/Xilinx/XilinxCEDStore/tree/2025.2/ced/Xilinx/IPI/Versal_CPM_QDMA_Accel_Sys_Design`
