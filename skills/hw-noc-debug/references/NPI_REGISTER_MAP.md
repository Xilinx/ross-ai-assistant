<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NPI Register Map Reference

> **Important:** `sysdbg_noc(action="analyze")` already reads and decodes these
> registers. This file is human-reference background only. Do not use it as a
> required step in the standard workflow, do not re-decode `ERR_NUM` from it,
> and do not override analyzer `corrective_action` guidance when it is present with manual
> interpretation from this file.

> **Use this file only for:** understanding register layout, checking offsets
> mentioned in design collateral, and interpreting `findings[].error_registers`
> when `sysdbg_noc(..., verbose=true)` is saved for extra context.

## Register Offset Summary

**CRITICAL:** NMU and NSU have DIFFERENT register offsets! Always check module type.

| Register Name | Offset (NMU) | Offset (NSU) | Purpose |
|--------------|--------------|--------------|---------|  
| REG_ISR | +0x30 | +0x44 | Interrupt Status (error indicator) |
| REG_IMR0 | +0x38 | +0x48 | Interrupt Mask Register 0 |
| REG_IER0 | +0x3C | +0x4C | Interrupt Enable Register 0 |
| REG_IDR0 | +0x40 | +0x50 | Interrupt Disable Register 0 |
| REG_1ST_ERR_NUM | +0x814 | +0x1FC | Error descriptor (category, direction) |
| REG_1ST_ERR_INFO_0 | +0x818 | +0x23C | Transaction ID (dst, src, tag) |
| REG_1ST_ERR_INFO_1 | +0x81C | +0x240 | Packet type, write strobe |
| REG_1ST_ERR_INFO_2 | +0x820 | +0x244 | VC, poison, DBI, tracker index |
| REG_1ST_ERR_INFO_3 | +0x824 | +0x248 | AXI attributes (cache, lock, size, len, id) |
| REG_1ST_ERR_INFO_4 | +0x828 | +0x24C | QoS, burst, prot, user |
| REG_1ST_ERR_INFO_5 | +0x82C | +0x250 | Address lower 32-bit |
| REG_1ST_ERR_INFO_6 | +0x830 | +0x254 | Address upper 32-bit |
| REG_1ST_ERR_INFO_7 | +0x834 | +0x258 | Credit overflow/underflow, addr parity |
| REG_TBASE_TRK_TIMEOUT | +0x864 | +0x128 | Timeout configuration |

---

## Base Address Discovery

Base addresses are design-specific and must be obtained from Vivado using:
```tcl
report_npi_addresses -of [get_sites NOC_*]
```
Or by running `report_noc -json` (built-in Vivado 2026.1+ command).

### Example NMU at base 0xF6E90000 (design-specific):

| Register | Address | Offset |
|----------|---------|--------|
| REG_ISR | 0xF6E90030 | +0x30 |
| REG_1ST_ERR_NUM | 0xF6E90814 | +0x814 |
| REG_1ST_ERR_INFO_0 | 0xF6E90818 | +0x818 |
| REG_1ST_ERR_INFO_1 | 0xF6E9081C | +0x81C |
| REG_1ST_ERR_INFO_2 | 0xF6E90820 | +0x820 |
| REG_1ST_ERR_INFO_3 | 0xF6E90824 | +0x824 |
| REG_1ST_ERR_INFO_4 | 0xF6E90828 | +0x828 |
| REG_1ST_ERR_INFO_5 | 0xF6E9082C | +0x82C |
| REG_1ST_ERR_INFO_6 | 0xF6E90830 | +0x830 |
| REG_1ST_ERR_INFO_7 | 0xF6E90834 | +0x834 |

### Example NSU at base 0xF6CF2000 (design-specific):

| Register | Address | Offset |
|----------|---------|--------|
| REG_ISR | 0xF6CF2044 | +0x44 |
| REG_1ST_ERR_NUM | 0xF6CF21FC | +0x1FC |
| REG_1ST_ERR_INFO_0 | 0xF6CF223C | +0x23C |
| REG_1ST_ERR_INFO_1 | 0xF6CF2240 | +0x240 |
| REG_1ST_ERR_INFO_2 | 0xF6CF2244 | +0x244 |
| REG_1ST_ERR_INFO_3 | 0xF6CF2248 | +0x248 |
| REG_1ST_ERR_INFO_4 | 0xF6CF224C | +0x24C |
| REG_1ST_ERR_INFO_5 | 0xF6CF2250 | +0x250 |
| REG_1ST_ERR_INFO_6 | 0xF6CF2254 | +0x254 |
| REG_1ST_ERR_INFO_7 | 0xF6CF2258 | +0x258 |

---

## ISR (Interrupt Status Register)

**Address:** Base + offset (NMU: +0x30, NSU: +0x44)

**Bits:**
- Bit 0: First error captured
- Bit 1-31: Various error types

**Write-1-to-clear:** Writing 0xFFFFFFFF clears all bits

**Critical:** NoC captures **only the first error** until ISR cleared.

In `sysdbg_noc` output, ISR data appears in `findings[].isr.active_bits`.

---

## REG_1ST_ERR_NUM (Error Descriptor)

**CRITICAL: NMU and NSU use DIFFERENT bit field layouts!**

`sysdbg_noc` decodes these automatically into `findings[].err_num.decode`.

**NMU Layout:**
```
[22]    valid  - Error log valid bit
[21:20] etype  - Error type (0=unknown, 1=write, 2=read)
[19:16] comp   - Component (1=NMU, 2=NSU)
[15:12] categ  - Category number
[11:8]  subc0  - Sub-category 0
[7:4]   subc1  - Sub-category 1
[3:0]   enumb  - Error number
```

**NSU Layout:**
```
[21:20] etype  - Error type (0=unknown, 1=write, 2=read)
[19:16] enumb  - Error number
[15:12] subc1  - Sub-category 1
[11:8]  subc0  - Sub-category 0
[7:4]   comp   - Component (1=NMU, 2=NSU)
[3:0]   categ  - Category number
```

---

## REG_1ST_ERR_INFO_7 (Credit/Parity - NMU only)

**Bit Fields (NMU):**
- [31:16]: Credit overflow/underflow indicators
- [15:0]: Address parity error flags

**Note:** For AXID extraction, use ERR_INFO_3 (bits[31:20]) for NMU, not ERR_INFO_7.

---

## References

- **AM019/AM033:** NoC NPI Register Database
- **PG313:** NoC Product Guide (Gen1)
- **PG406:** NoC Product Guide (Gen2)
