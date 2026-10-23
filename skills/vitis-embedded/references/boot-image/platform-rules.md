<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Platform Rules — Valid Value Tables

Detailed lookup tables for edge-case BIF generation.
All values cross-checked against UG1283 and Platform Facts Audit.

## Auth Types Per Platform

| Platform | Valid auth types | Notes |
|---|---|---|
| Versal | rsa, ecdsa-p384, ecdsa-p521 | P521 forbidden on bootloader + metaheader (BootROM limitation) |
| VersalNet | rsa, ecdsa-p384, ecdsa-p521, lms-sha256, lms-shake256, hss-sha256, hss-shake256 | P521 forbidden on bootloader + metaheader. LMS/HSS post-quantum supported (UG1283 §VersalNet-Auth; `versal_2vp/src/bootimage-versal_2vp.cpp:252-258`). |
| Versal 2VE/2VM | rsa, ecdsa-p384, lms-sha256, lms-shake256, hss-sha256, hss-shake256 | Post-quantum supported. **P521 source-only:** Bootgen accepts `ecdsa-p521` on non-PLM / non-metaheader partitions (`bootimage-versal_2ve_2vm.cpp:256, 1089-1091`), but UG1283 is silent — refuse by default, allow only on explicit advanced / source-only request. |
| SpartanUP (SU45+) | ecdsa-p384, hss-shake256, lms-shake256 | No RSA. |
| SpartanUP (SU10/25/35P) | hss-shake256 | ONLY hss-shake256. All others illegal. |
| ZynqMP | rsa | RSA-4096 ONLY. No ECDSA. (UG1283 §ZynqMP-Auth-Support) |
| Zynq | rsa | RSA-2048. |

## Key Sources Per Platform

### Versal / VersalNet

Bootloader + metaheader subset: `efuse_red_key`, `efuse_blk_key`, `bbram_red_key`, `bbram_blk_key`, `bh_blk_key`

Full set (application partitions): above plus `user_key0` through `user_key7`, `efuse_user_key0`, `efuse_user_key1`, `efuse_user_blk_key0`, `efuse_user_blk_key1`

**Grey keys** (`efuse_gry_key`, `bbram_gry_key`, `bh_gry_key`): Bootgen emits `LOG_ERROR` on **classic Versal Gen 1 only** (`bifoptions.cpp:1263-1271, 1352`; gate is `arch==VERSAL && !versalNetSeries`). **VersalNet and 2VE/2VM accept grey keys** — the deprecation does not apply.

### Versal 2VE/2VM

Same as Versal plus grey keys are valid (not deprecated): `efuse_gry_key`, `bbram_gry_key`, `bh_gry_key`, `efuse_user_gry_key0`, `efuse_user_gry_key1`

### SpartanUP

**UG1283-blessed set:** `efuse_red_key`, `efuse_blk_key`, `efuse_gry_key`, `bh_blk_key`, `bh_gry_key`

**Bootgen also accepts** (UG silent, source-only): `bbram_red_key`, `bbram_blk_key`, `bbram_gry_key` (`spartanup/src/bootheader-spartanup.cpp:458-519`). Default behavior: emit only the UG-blessed set; permit BBRAM keys only on explicit advanced / source-only request.

**Note:** The Bootgen-bundled `template_engine.py` (`product/resources/data/boot_assist/lnx64/src/templates/template_engine.py:124-125`) further restricts SpartanUP to `efuse_red_key` only. This is a boot_assist layer restriction, not a Bootgen C++ or UG restriction. It explains why Vitis's built-in boot assist generates `efuse_red_key` exclusively for SpartanUP. This skill follows the broader UG-blessed set.

### ZynqMP

Bracket-style `[keysrc_encryption]` values: `bbram_red_key`, `efuse_red_key`, `efuse_blk_key`, `efuse_gry_key`, `bh_blk_key`, `bh_gry_key`, `kup_key`

Note: `opt_key` is NOT a keysrc — it is a `[fsbl_config]` feature. `OPERATIONAL_KEY` does not exist as a BIF enum.

### Zynq

`efuse`, `bbram` (Zynq-7000 uses bare key sources via `-encrypt efuse|bbram`, not the ZynqMP `_red_key`/`_blk_key` variants — see canonical-bifs.md Example 23)

## Valid Partition Types

| Type | Platforms | Description |
|---|---|---|
| bootloader | all | First-stage loader (PLM, FSBL) — exactly one per boot image |
| bootimage | Versal-family | PDI container |
| cdo | Versal-family | Configuration Data Object — **also the modern type for PL configuration partitions (`*.rcdo`, `*.rnpi`, `*.npi`)** |
| raw | all | Raw binary data |
| cfi | Versal-family (legacy) | Legacy PL-configuration keyword. Goldens now emit `type = cdo` for `*.rcdo` / `*.rnpi`. Do not emit `cfi` for new BIFs; accept it when parsing existing BIFs. |
| cfi-gsc | Versal-family (legacy) | Legacy global-clock-config keyword. Use `type = cdo` instead in new BIFs. |
| pmcdata | Versal-family (Versal Gen 1, VersalNet, 2VE/2VM) | PMC data CDO. Rejected on Zynq / ZynqMP (`bifoptions.cpp:632-637`). SpartanUP uses the alias `pldata`. |
| elf | all | Executable (used with `core` attribute) |
| slr-boot | Versal (SSIT) | SLR boot partition |
| slr-config | Versal (SSIT) | SLR configuration |
| slr-slave | Versal (SSIT) | Slave SLR |
| slr-slave-boot | Versal (SSIT) | Slave SLR boot |
| slr-slave-config | Versal (SSIT) | Slave SLR configuration |
| pmufw_image | ZynqMP | PMU firmware (bracket-style) |
| pldata | SpartanUP | SpartanUP alias for `pmcdata` |
| init | Zynq, ZynqMP | Register-initialisation partition (bracket-style `[init]`) |
| udf_bh | Zynq, ZynqMP | User-defined boot-header field |
| ~~imagestore~~ | Versal-family | **NOT a partition `type`** — it is an attribute: `imagestore = <id>` (bootgen 2026.2 rejects `type = imagestore`). See canonical-bifs.md Ex.17 and `bootgen -bif_help imagestore`. |

## Valid Core Values Per Platform

| Platform | Application cores | System cores |
|---|---|---|
| Versal | a72-0, a72-1 | r5-0, r5-1, r5-lockstep, psm, aie |
| VersalNet | a78-0..a78-3 | r5-lockstep, r52-0 / r52-1 (lexer aliases of r5-0 / r5-1), psm, aie. **No `r52-lockstep` token exists** — for R52 lockstep use `core = r52-0` + boolean `lockstep` attribute (`bif.l:399`; `bifoptions.cpp:2103-2110`). Optional `cluster = 0..3` (`bifoptions.cpp:1954-1963`). |
| Versal 2VE/2VM | a78-0 *(multi-APU 2VM silicon: a78-1..a78-3 accepted by Bootgen at parse time; advanced / source-only — see notes)* | r5-lockstep, r52-0 (alias r5-0), asu **and** psm (both map to PHT slot 8 — `DestinationCPU::PMU`), aie. Optional `cluster = 0..4` (`bifoptions.cpp:1964-1970`). **No `r52-lockstep` token.** |
| SpartanUP | (none — typical PLM-only) | (Bootgen accepts the same lexer tokens as Versal-class on `core =` if a design loads ELFs; advanced / source-only) |
| ZynqMP | a53-0..a53-3 | r5-0, r5-1, r5-lockstep, pmu |
| Zynq | (none — `destination_cpu` not supported; Bootgen emits `LOG_ERROR` if used, `bifoptions.cpp:1552-1555`) | (none — partition assignment is by PS / PL domain in the partition header and by load order; no CPU field in Zynq PHT) |

**Attribute synonyms (Versal-family):** `core = X` and `destination_cpu = X` are equivalent — the lexer maps both to the same `DEST_CPU` token (`bif.l:379-380`). The Versal-family canonical BIFs in this skill use `core =`; either form is valid.

**`a5x-*` aliases:** Bootgen also accepts `a5x-0`..`a5x-3` as lexer aliases of the `a53-*` / `a72-*` / `a78-*` enum slots on every Versal-family arch (`bif.l:381-394`). Skill emits the arch-canonical name (`a72`, `a78`, etc.).

## Optional Per-Partition Attributes (Versal-family)

These attributes apply to a single partition (or, for `lockstep`, modify how
a CPU core attribute is interpreted) and are gated on `-arch`. Use them only
when the design explicitly requires the corresponding silicon feature.

| Attribute | Syntax | Allowed on | Forbidden on | Source |
|---|---|---|---|---|
| `lockstep` (boolean) | `lockstep` (bare flag, sets to true) | VersalNet, 2VE/2VM | classic Versal Gen 1, ZynqMP, Zynq | `bif.l:83`, `bif.y:598`, `bifoptions.cpp:2103-2110` |
| `cluster = N` | `cluster = <int>` | VersalNet (N=0..3), 2VE/2VM (N=0..4) | classic Versal Gen 1, ZynqMP, Zynq | `bif.y:639`, `bifoptions.cpp:1954-1978` |
| `tcmboot` (boolean) | `tcmboot` (bare flag) | VersalNet, 2VE/2VM | classic Versal Gen 1, ZynqMP, Zynq | `bifoptions.cpp:2206-2216` |
| `tcm_a_region = <hex>` | `tcm_a_region = 0x…` | VersalNet only (R5 dest cpus) | all other arches | `bifoptions.cpp:2114-2149` |
| `tcm_b_region = <hex>` | `tcm_b_region = 0x…` | VersalNet only (R5 dest cpus) | all other arches | `bifoptions.cpp:2114-2149` |
| `tcm_c_region = <hex>` | `tcm_c_region = 0x…` | VersalNet only (R5 dest cpus) | all other arches | `bifoptions.cpp:2114-2149` |
| `aarch32_mode` (boolean) | `aarch32_mode` | ZynqMP (A53), 2VE/2VM (A78 — source-only) | classic Versal Gen 1, Zynq | `bif.y:721` |
| `exception_level = el-<N>` | `exception_level = el-0\|el-1\|el-2\|el-3` | ZynqMP, all Versal-family | Zynq (Bootgen errors) | `bif.l:414-417`, `bifoptions.cpp:1856-1861` |
| `trustzone[ = secure\|nonsecure]` | `trustzone` or `trustzone = secure\|nonsecure` | ZynqMP, all Versal-family | Zynq (Bootgen errors) | `bif.l:419-421`, `bifoptions.cpp:1886-1892` |

### `lockstep` vs `r5-lockstep`

These are two **different** mechanisms:

- **`core = r5-lockstep`** — selects the pseudo-CPU at PHT slot 7 (Bootgen's
  built-in lockstep slot). Available on ZynqMP, all Versal-family.
  Source: `bif.l:399`.
- **`lockstep` (bare boolean)** — modifies a normal R5/R52 destination so
  the runtime treats the two cores as a lockstep pair (PHT lockstep
  bitfield). VersalNet+ only. Use this when targeting `r5-0` / `r52-0`
  and you want the lockstep encoding without the separate PHT slot.

Both can appear; choose based on whether your PLM / runtime expects the
dedicated lockstep slot or the bitfield-encoded pair.

### TCM region encoding (VersalNet)

`tcm_a_region` / `tcm_b_region` / `tcm_c_region` are 32-bit hex values that
configure which TCM banks the loaded ELF owns. Per-region semantics live
in UG1304 (VersalNet PLM). When emitting these, call `User_Guide` with
`versalnet tcm_region encoding` to fetch the canonical bit layout for
the target silicon.

### `cluster` upper bounds

| `-arch` | Valid `cluster` range | Source |
|---|---|---|
| `versalnet` | `0..3` | `bifoptions.cpp:1954-1963` |
| `versal_2ve_2vm` | `0..4` | `bifoptions.cpp:1964-1970` |
| classic `versal`, `zynqmp`, `zynq`, `spartanup` | not supported | — |

Out-of-range values are a Bootgen `LOG_ERROR`.

## Advanced / Source-Only Features

Bootgen accepts the following tokens / values; the Vitis User Guide
corpus is silent on them. **Default skill behaviour: refuse**. Emit
only when the user explicitly opts in to advanced / source-only
generation and the emitted BIF carries the
`# src:… | ug:silent | advanced/source-only` citation comment.

Before refusing, call `User_Guide` once with the broad query
(`<arch> <feature>`) to confirm UG silence — silence is the expected
outcome for everything in this section.

### Source-only authentication

| Arch | Token | Source | UG status |
|---|---|---|---|
| `versal_2ve_2vm` | `ecdsa-p521` on non-PLM / non-metaheader partitions | `bootimage-versal_2ve_2vm.cpp:256, 1089-1091` | UG1283 silent |

### Source-only key sources

| Arch | Tokens | Source | UG status |
|---|---|---|---|
| `spartanup` | `bbram_red_key`, `bbram_blk_key`, `bbram_gry_key` | `spartanup/src/bootheader-spartanup.cpp:458-519` | UG1283 silent |
| `versalnet` | `efuse_user_gry_key0`, `efuse_user_gry_key1` (grey user keys) | `bifoptions.cpp:1267-1271, 1352` | UG1283 silent |

### Source-only cores

| Arch | Tokens | Source | UG status / silicon gate |
|---|---|---|---|
| `versal_2ve_2vm` | `a78-1`, `a78-2`, `a78-3` (extra APU cores on multi-APU 2VM silicon) | `readimage-versal_2ve_2vm.cpp:1502-1509`; `bif.l:385-394` | UG1283 lists `a78-0` only; silicon variant must support quad-APU. Refuse unless user supplies the part number. |
| `spartanup` | `core = a72-0`, `r5-0`, etc. on optional ELF partitions | `bootimage-spartanup.cpp:925` (no Zynq-style block) | UG1283 documents SpartanUP as PLM-only; source allows extra ELFs |
| All Versal-family | `a5x-0`..`a5x-3` (generic APU aliases) | `bif.l:381-394` | Lexer alias — emit the arch-canonical name (`a72`, `a78`) instead unless the user supplies a literal `a5x-*` token |

### `core = aie` usage notes

Bootgen accepts `aie` (alias `me`, deprecated with warning) on every
Versal-family arch. The partition must be either:

- An **AIE ELF** (a64-aie elf with AIE-specific load segments), or
- A **CDO containing AIE programming commands** (with `type = cdo`).

Per UG1283 the AIE programming flow is described in UG1076 (AI Engine
Tools) rather than UG1283 itself; `User_Guide` queries about
`aie elf format` route there. Use `core = aie` only when the user
explicitly provides an AIE ELF / CDO file; do not auto-emit on cores
listed in the platform catalog.

| Arch | `core = aie` accepted | PHT slot |
|---|---|---|
| classic `versal` | yes | 9 (`readimage-versal.cpp:1212-1237`) |
| `versalnet` | yes | 9 |
| `versal_2ve_2vm` | yes | 9 (`readimage-versal_2ve_2vm.cpp:1495-1516`) |
| `spartanup`, `zynqmp`, `zynq` | no (lexer parses, but no AIE silicon) | — |

### Source-only partition types

| Type | Arch(es) | Source | UG status |
|---|---|---|---|
| `imagestore = <id>` (attribute, **not** a `type`) | Versal-family | `bootgen -bif_help imagestore` | UG1283 §image_store_PDI. Use `imagestore = <id>` on the partition; `type = imagestore` is a syntax error in bootgen 2026.2. |
| `udf_bh` | Zynq, ZynqMP | `bif.l:197` | UG1283 covered as user-defined boot header field; advanced layout only |
| `init` | Zynq, ZynqMP | `bif.l:196` | Bracket form `[init] regs.init` documented in UG1283 §register-init |

### Always-refuse (Bootgen rejects)

These are not "source-only" — they are outright Bootgen `LOG_ERROR`
conditions that no opt-in flag can bypass. Refuse with the cited error.

| Combination | Refusal source |
|---|---|
| `destination_cpu` on Zynq | `bifoptions.cpp:1552-1555` |
| `destination_device = pmufw` (use `destination_cpu = pmu` instead) | `bifoptions.cpp:1567-1571` |
| `destination_device = xip` (use `xip_mode` instead) | `bifoptions.cpp:1573-1578` |
| `boot_device` block on Zynq | parser-level reject |
| Grey keys on classic Versal Gen 1 | `bifoptions.cpp:1267-1271` |
| `ecdsa` on ZynqMP | `authentication-zynqmp.cpp:129` |
| `ecdsa-p521` on bootloader or metaheader (any arch) | per-arch `bootimage-*.cpp:969-971` |
| SSIT on VersalNet or 2VE/2VM | monolithic-only check |
| HSM on VersalNet | UG1283 §Using-HSM-Mode: "HSM mode is not currently supported for Versal AI Edge Series Gen 2, and Versal Prime Series Gen 2" |
| HSM on Versal 2VE/2VM | UG1283 §Using-HSM-Mode |

## Feature Guards

| Feature | Versal | VersalNet | 2VE/2VM | SpartanUP | ZynqMP | Zynq |
|---|---|---|---|---|---|---|
| bh_auth_enable | YES (image_config) | no | no | no | YES (fsbl_config) | no |
| metaheader | YES | YES | YES | NO | NO | NO |
| dpacm_enable | YES | YES | YES | YES | no | no |
| key_rolling (blocks) | YES | YES | YES | YES | YES | no |
| PUF | YES | YES | YES | YES | YES | no |
| imagestore | YES | no | no | no | no | no |
| SSIT | YES | no | no | no | no | no |
| HSM | YES | NO | NO | YES | YES | YES |
| ASUFW | no | no | YES | no | no | no |
| opt_key | no | no | no | no | YES | no |
| trustzone | YES | YES | YES | no | YES | no |

## Subsystem Composition (VersalNet / Versal 2VE/2VM)

These rules come from the 4-way 2VE/2VM testing rounds (Q2, Q4, Q6 goldens).

### Mandatory subsystems for secure BIFs

| Security mode | Mandatory subsystems |
|---|---|
| Non-secure (minimal) | `pmc_subsys` (others optional) |
| Authenticated (RSA / ECDSA / LMS / HSS) | `pmc_subsys` + **`lpd_subsys`** + **`fpd_subsys`** + `apu_subsys` (+ `asufw` on 2VE/2VM) |
| Encrypted (AES) | same as authenticated — `lpd_subsys` and `fpd_subsys` are part of the chain |
| Authenticated + Encrypted | same as authenticated |

### lpd_subsys partition layout

`lpd_subsys` (id `0x04210002`) always emits `lpd_data.cdo` as the **first**
partition. Additional partitions appear only when present in the design.

| Order | Partition | id | Type / core | Required when |
|---|---|---|---|---|
| 1 | `lpd_data.cdo` | `0x0C` | `type = cdo` | always |
| 2 | `psm_fw.elf` | `0x0B` | `core = psm` | PSM is included in the design |
| 3 | `dap.cdo` | (next free) | `type = cdo` | DAP CDO is provided |

### asufw partition-ID rule (2VE/2VM)

`asufw` (id `0x1c000002`) has two partitions: an ASU data CDO and the
ASU firmware ELF.

| Partition | id | Notes |
|---|---|---|
| `asu_data.cdo` | `0x0F` | `type = cdo` — same across secure/non-secure |
| `asufw.elf` | `0x0B` *when PSM is absent* / `0x10` *when PSM is present* | `core = asu`. Because partition IDs are unique BIF-wide and `psm_fw.elf` claims `0x0B`, any BIF that includes both PSM and ASUFW must move the ASUFW ELF to `0x10`. Secure BIFs always include PSM, so they always use `0x10`. |

## Bootgen CLI

`-arch` selects the parser/encoder; each Versal-family arch is distinct.

| Platform | Command |
|---|---|
| Versal Gen 1 | `bootgen -arch versal -image design.bif -o design.pdi` |
| VersalNet | `bootgen -arch versalnet -image design.bif -o design.pdi` (alias: `psx`) |
| Versal 2VE/2VM | `bootgen -arch versal_2ve_2vm -image design.bif -o design.pdi` (deprecated alias: `telluride`) |
| SpartanUP (SU45+) | `bootgen -arch spartanup -image design.bif -o design.pdi` (aliases: `spartanuplus`, `lassen_dl9`, `kintexupg2`) |
| SpartanUP (SU10/25/35P) | `bootgen -arch lassen -image design.bif -o design.pdi` (selects `dl9Series=false` small-part path; deprecated — prefer `-arch spartanup` plus a small-part `id_code`) |
| ZynqMP | `bootgen -arch zynqmp -image boot.bif -o boot.bin` |
| Zynq | `bootgen -arch zynq -image boot.bif -o boot.bin` |
