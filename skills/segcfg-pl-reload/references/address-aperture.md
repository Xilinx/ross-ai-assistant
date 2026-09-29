# Address Aperture Flexibility for Segmented Configuration PL Reload

## Problem

Traditional NoC designs use static NMU decode and fixed address apertures. When using PL Reload with different PL images, aperture mismatches between the initial and reload designs can cause verification failures or routing incompatibilities.

## Solution: Address Aperture Flexibility

Segmented Configuration introduces controlled aperture adjustment across PL reload variants:

- The **golden design** defines the **maximum aperture** for all anticipated reload cases
- **Variant designs** may use a **smaller subset** of that reserved range
- Variant designs may NOT exceed the golden aperture
- This enables flexibility without breaking `pr_verify` checks

## Key Capabilities

| Capability | Description |
|-----------|-------------|
| Dynamic Aperture Management | Reload designs define apertures fitting their PL slave address space, within the golden's pre-allocated range |
| Static Decode Preservation | Base address and NMU decode remain consistent (similar to DFX schemes) |
| Traffic Spec Integration | Aperture constraints annotated in traffic spec for consistent address assignment |
| User-Specified Apertures | Explicit aperture definition on INI ports to prevent overwrite during auto address assignment |

## Implementation

### Step 1: Golden Design — Define Maximum Aperture
In the initial design, set the largest possible aperture for each PL slave interface. This reserves the full address range needed by any reload variant.

Example: Reserve 256K for an AXI BRAM controller that may only need 64K in some variants.

```
Golden design address map:
  /axi_bram_ctrl_0/S_AXI: base = 0xa4000000, aperture = 256K (0x40000)
```

### Step 2: Aperture Propagation
The aperture settings propagate through the NoC solution file (`.ncr`) to subsequent designs.
Apertures for boot-path NMU instances are locked — they remain consistent across all designs.

### Step 3: Variant Design — Use Subset Aperture
In the reload variant, the aperture can be reduced to match the actual PL slave size:

```
Variant design address map:
  /axi_bram_ctrl_0/S_AXI: base = 0xa4000000, aperture = 64K (0x10000)
```

The base address and NMU decode remain the same; only the aperture size shrinks.

### Step 4: Verify with pr_verify
`pr_verify` checks that variant apertures do not exceed golden apertures:
```tcl
pr_verify -segcfg_only -initial <golden>_routed.dcp -additional <variant>_routed.dcp
```

If the variant aperture is within the golden range, `pr_verify` passes.
If the variant aperture exceeds the golden range, `pr_verify` reports an error.

## Tcl / GUI Options

### Set INI Aperture via Tcl
```tcl
# Set explicit aperture on an INI (NoC Interface) to prevent auto-overwrite
set_property -dict {CONFIG.APERTURES {0xa4000000 256K}} \
    [get_bd_intf_pins /axi_noc_0/S00_INI]
```

### GUI
In the AXI NoC IP's address editor, you can set the aperture size directly on AXI slave ports.

## Example Scenario

```
Golden:  BRAM aperture = 256K → covers future 64K, 128K, 256K variants
Variant A: 64K BRAM   → aperture = 64K  (within 256K → pr_verify PASSES)
Variant B: 128K BRAM  → aperture = 128K (within 256K → pr_verify PASSES)
Variant C: 512K BRAM  → aperture = 512K (EXCEEDS 256K → pr_verify FAILS)
```

For Variant C, the golden design must be updated to define the maximum required aperture (512K), and all other PDIs must be recompiled.

## Notes

- Aperture flexibility applies to PL slave address spaces; PS/DDR address apertures must remain identical across all designs
- This feature is tracked by `pr_verify` — verify before deploying to hardware
- For more details on Vivado NoC address assignment, refer to UG1387 (Versal Development Methodology Guide)
