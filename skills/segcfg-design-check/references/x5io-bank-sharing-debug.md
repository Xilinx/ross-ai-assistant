# Detecting X5IO bank sharing conflict between PL and Boot partition in Gen2 devices, used when PL is using OCTAD resources from the bank, where boot partition includes DDRMC

## Debug Instructions for Gen2 devices that are using X5IO OCTADs

> **IMPORTANT — Site vs Tile naming:**
> `SegConfig_BootTiles.tcl` contains **TILE** names (e.g. `CMPHY_OCTAD_CORE_X75Y0`).
> PLM error logs and register address maps report **SITE** names (e.g. `CMPHY_OCTAD_X52Y0`).
> Site X-indices and tile X-indices use **different numbering schemes** — they are NOT the same.
> Never construct a tile name by prepending `CMPHY_OCTAD_CORE_` to a site X-index, or vice versa.
> Always use the Vivado API to convert:
> - **Tile → Site**: `get_sites -of_objects [get_tiles $tile]`
> - **Site → Tile**: `get_tiles -of_objects [get_sites $site]`

---

### Step 1 — Get boot partition OCTAD tiles from SegConfig_BootTiles.tcl

`SegConfig_BootTiles.tcl` is in the `hd_visual/` folder of the impl run.
The grep returns **TILE** names.

```bash
grep PHY_OCTAD_CORE ./SegConfig_BootTiles.tcl
```

---

### Step 2 — For each boot OCTAD tile, find its IO bank via the RIU_CK0 network

CMPHY_OCTAD tiles do not have a direct `IOBANK` property. The correct path is:
tile → site → `XCV2RIU_CK0` pin → nodes → PIPs → nodes → connected X5IO tiles → sites → IO bank.

```tcl
# $tile is a TILE name from SegConfig_BootTiles.tcl (e.g. CMPHY_OCTAD_CORE_X75Y0)

# Step 2a: TILE -> SITE
set site [get_sites -of_objects [get_tiles $tile]]

# Step 2b: SITE -> connected X5IO tiles via RIU_CK0 network
set tileList [get_tiles -of_objects \
  [get_nodes -of_objects \
    [get_pips -of_objects \
      [get_nodes -of_objects \
        [get_site_pins $site/XCV2RIU_CK0]]]]]

# Step 2c: From those tiles -> sites -> IO bank
foreach item $tileList {
    set siteList [get_sites -of_objects [get_tiles $item]]
    foreach itemSite $siteList {
        puts [get_iobanks -of_objects [get_sites $itemSite]]
        break
    }
}
```

---

### Step 3 — Convert a PLM-reported site name to its tile (for partition membership check)

When a PLM error or register address map gives a **site** name (e.g. `CMPHY_OCTAD_X52Y0`),
use the API to get the corresponding **tile** before checking against the boot tile list:

```tcl
# site -> tile (correct — do NOT guess the tile name from the site X-index)
set tile [get_tiles -of_objects [get_sites CMPHY_OCTAD_X52Y0]]
# Example result: CMPHY_OCTAD_CORE_X75Y0  (X-index differs from site!)

# Check if that tile is in the boot partition tile list
set in_boot [expr {[lsearch $boot_octad_tiles $tile] >= 0}]
```

---

### Step 4 — Find banks of PL ports (e.g. MIPI)

MIPI ports are always in the PL partition. `IOBANK` is a direct property on IO ports.

```tcl
get_property IOBANK [get_ports MIPI*]
```

---

### Step 5 — Compare: MIPI port banks vs boot OCTAD banks

1. If both lists contain the same bank number → **conflict**: boot and PL partitions share the same DDR bank resources.
2. This usually triggers DRC violation `SegConfig-Validation-18`.

---

### Solution

Separate boot and PL logic so they do not share the same bank resources: X5PLL, OCTAD, I/O package pins, etc.
