<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Canonical BIF Examples

28 golden examples covering all platforms and security modes (Examples 1–16
+ Example 5b for Versal 2VE/2VM RSA + AES + Examples 17–21 for advanced
features + Examples 22–23 for Zynq 7000 HSM and AES encryption
+ Examples 24–28 filling remaining platform × auth-type gaps: VersalNet
RSA, Versal ECDSA-P384, 2VE/2VM ECDSA-P384, SpartanUP LMS, 2VE/2VM LMS).
Each BIF is verified against UG1283 and Bootgen source with audit
corrections, including the Versal/VersalNet/2VE secure-infrastructure
hardening (lpd_subsys+PSM, fpd_subsys, asufw ELF id=0x10) from the 4-way
testing rounds. Examples 17–18 cover advanced features and carry the
required `# src:… | ug:…` citation comments.

---

## Example 1: Versal — non-secure

Platform: Versal | Security: none | Cores: a72-0, psm | Includes: PL/NoC (`pl_cfi`), optional AIE

> **`pl_cfi` is required for any app that runs from DDR.** On Versal the NoC and DDR memory
> controllers are configured by the design's `.rnpi` partition, and the PL by its `.rcdo`.
> Standalone apps link into `noc_ddr_*` regions by default, so omitting `pl_cfi` yields a PDI
> that Bootgen accepts but that **cannot load the app** (the A72 can't reach DDR). Take the file
> names and partition ids from the XSA's own BIF
> (`<plat>/export/<plat>/hw/extracted/<design>/pdi_files/<board>.bif`); include `aie_subsys` only
> if that BIF has it (AIE devices such as VCK190/VEK280).

```
new_bif:
{
 id_code = 0x14CA8093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = pl_cfi
  id = 0x18700000
   partition
   {
       id = 0x03
       type = cdo
       file = path/to/your/design.rcdo
   }
   partition
   {
       id = 0x05
       type = cdo
       file = path/to/your/design.rnpi
   }
 }
 image
 {
  name = aie_subsys
  id = 0x421c005
   partition
   {
       id = 0x07
       type = cdo
       file = path/to/your/ai_engine_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a72-0
       file = path/to/your/apu_application.elf
   }
 }
}
```

---

## Example 2: Versal — RSA + AES encryption

Platform: Versal | Security: RSA, AES (efuse_blk_key) | Features: bh_auth_enable, metaheader

```
new_bif:
{
 image_config
 {
  bh_auth_enable
 }
 pskfile = path/to/your/rsa_psk.pem
 sskfile = path/to/your/rsa_ssk.pem
 id_code = 0x14CA8093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = rsa
       encryption = aes
       keysrc = efuse_blk_key
       aeskeyfile = path/to/your/plm_key.nky
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = rsa
       load = 0xF2000000
       encryption = aes
       keysrc = efuse_blk_key
       aeskeyfile = path/to/your/pmc_data_key.nky
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = rsa
  pskfile = path/to/your/rsa_psk.pem
  sskfile = path/to/your/rsa_ssk.pem
  encryption = aes
  keysrc = efuse_blk_key
  aeskeyfile = path/to/your/metaheader_key.nky
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = rsa
       encryption = aes
       keysrc = efuse_blk_key
       aeskeyfile = path/to/your/lpd_cdo_key.nky
       file = path/to/your/lpd_data.cdo
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = rsa
       encryption = aes
       keysrc = efuse_blk_key
       aeskeyfile = path/to/your/fpd_cdo_key.nky
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a72-0
       authentication = rsa
       encryption = aes
       keysrc = efuse_blk_key
       aeskeyfile = path/to/your/apu_key.nky
       file = path/to/your/apu_application.elf
   }
 }
}
```

---

## Example 3: VersalNet — non-secure

Platform: VersalNet | Security: none | Cores: a78-0

```
new_bif:
{
 id_code = 0x14D80093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       file = path/to/your/apu_application.elf
   }
 }
}
```

---

## Example 4: VersalNet — ECDSA-P384

Platform: VersalNet | Security: ECDSA-P384 | Features: metaheader (no bh_auth_enable)

```
new_bif:
{
 pskfile = path/to/your/ecdsa-p384_psk.pem
 sskfile = path/to/your/ecdsa-p384_ssk.pem
 id_code = 0x14D80093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = ecdsa-p384
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = ecdsa-p384
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = ecdsa-p384
  pskfile = path/to/your/ecdsa-p384_psk.pem
  sskfile = path/to/your/ecdsa-p384_ssk.pem
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = ecdsa-p384
       file = path/to/your/lpd_data.cdo
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = ecdsa-p384
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = ecdsa-p384
       file = path/to/your/apu_application.elf
   }
 }
}
```

---

## Example 5: Versal 2VE/2VM — non-secure

Platform: Versal 2VE/2VM | Security: none | Features: ASUFW, a78-0

```
new_bif:
{
 id_code = 0x04DB0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       file = path/to/your/lpd_data.cdo
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       file = path/to/your/apu_application.elf
   }
 }
 image
 {
  name = asufw
  id = 0x1c000002
   partition
   {
       id = 0x0F
       type = cdo
       file = path/to/your/asu_data.cdo
   }
   partition
   {
       id = 0x0B
       core = asu
       file = path/to/your/asufw.elf
   }
 }
}
```

> Non-secure 2VE/2VM may omit `lpd_subsys`/`fpd_subsys` in a minimal
> demo. The ASUFW elf can stay at `id = 0x0B` here because PSM is not
> present. The secure variant in Example 5b below adds the full
> infrastructure and moves the ASUFW elf to `0x10`.

---

## Example 5b: Versal 2VE/2VM — RSA + AES encryption (full secure structure)

Platform: Versal 2VE/2VM | Security: RSA, AES (`bbram_red_key`) | Features: metaheader, full lpd_subsys (lpd_data.cdo + PSM), fpd_subsys, asufw (ELF id=0x10 to avoid clash with PSM id=0x0B). 2VE/2VM does **not** use `image_config { bh_auth_enable }` — that is Versal Gen 1 only.

```
new_bif:
{
 pskfile = path/to/your/rsa_psk.pem
 sskfile = path/to/your/rsa_ssk.pem
 id_code = 0x04DB0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/plm_key.nky
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = rsa
       load = 0xF2000000
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/pmc_data_key.nky
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = rsa
  pskfile = path/to/your/rsa_psk.pem
  sskfile = path/to/your/rsa_ssk.pem
  encryption = aes
  keysrc = bbram_red_key
  aeskeyfile = path/to/your/metaheader_key.nky
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/lpd_cdo_key.nky
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/psm_key.nky
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/fpd_cdo_key.nky
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/apu_key.nky
       file = path/to/your/apu_application.elf
   }
 }
 image
 {
  name = asufw
  id = 0x1c000002
   partition
   {
       id = 0x0F
       type = cdo
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/asu_data_key.nky
       file = path/to/your/asu_data.cdo
   }
   partition
   {
       id = 0x10
       core = asu
       authentication = rsa
       encryption = aes
       keysrc = bbram_red_key
       aeskeyfile = path/to/your/asufw_key.nky
       file = path/to/your/asufw.elf
   }
 }
}
```

---

## Example 6: Versal 2VE/2VM — HSS-SHAKE256 (full secure structure)

Platform: Versal 2VE/2VM | Security: HSS-SHAKE256 (post-quantum) | Features: ppkfile/pskfile/spkfile/sskfile, metaheader, full lpd_subsys (lpd_data.cdo + PSM), fpd_subsys, asufw with ELF id=0x10 to avoid collision with PSM at id=0x0B.

```
new_bif:
{
 ppkfile = path/to/your/primary.hss.pub
 pskfile = path/to/your/primary.hss.prv
 spkfile = path/to/your/secondary.hss.pub
 sskfile = path/to/your/secondary.hss.prv
 id_code = 0x04DB0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = hss-shake256
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = hss-shake256
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = hss-shake256
  ppkfile = path/to/your/primary.hss.pub
  pskfile = path/to/your/primary.hss.prv
  spkfile = path/to/your/secondary.hss.pub
  sskfile = path/to/your/secondary.hss.prv
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = hss-shake256
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       authentication = hss-shake256
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = hss-shake256
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = hss-shake256
       file = path/to/your/apu_application.elf
   }
 }
 image
 {
  name = asufw
  id = 0x1c000002
   partition
   {
       id = 0x0F
       type = cdo
       authentication = hss-shake256
       file = path/to/your/asu_data.cdo
   }
   partition
   {
       id = 0x10
       core = asu
       authentication = hss-shake256
       file = path/to/your/asufw.elf
   }
 }
}
```

> **Why `id = 0x10` on `asufw.elf`** — `psm_fw.elf` already claims `id = 0x0B`
> in `lpd_subsys`, so the ASUFW elf must move to `0x10` to keep partition
> IDs unique across the BIF. The non-secure minimal Example 5 (no PSM)
> can safely reuse `0x0B`; secure 2VE/2VM BIFs always include PSM and
> therefore always use `0x10`.

---

## Example 7: SpartanUP — non-secure

Platform: SpartanUP | Security: none | Partitions: PLM, bitstream

```
new_bif:
{
 id_code = 0x04EA0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       file = path/to/your/spartanup_fw.elf
   }
   partition
   {
       id = 0x02
       type = pldata
       file = path/to/your/bitstream.rcdo
   }
 }
}
```

---

## Example 8: SpartanUP — ECDSA-P384

Platform: SpartanUP (SU45+) | Security: ECDSA-P384 | Features: no
metaheader, no `bh_auth_enable` (SpartanUP determines BH vs eFuse auth
from eFuse bits at runtime — `bifoptions.cpp:2784` emits `LOG_ERROR` if
`bh_auth_enable` is present in BIF).

```
# src:bifoptions.cpp:2784 (bh_auth_enable → LOG_ERROR on SpartanUP)
new_bif:
{
 pskfile = path/to/your/ecdsa-p384_psk.pem
 sskfile = path/to/your/ecdsa-p384_ssk.pem
 id_code = 0x04EA0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = ecdsa-p384
       file = path/to/your/spartanup_fw.elf
   }
   partition
   {
       id = 0x02
       type = pldata
       authentication = ecdsa-p384
       file = path/to/your/bitstream.rcdo
   }
 }
}

/* Bootgen CLI: bootgen -arch spartanup -image this.bif -o boot.bin */
```

---

## Example 9: ZynqMP — non-secure

Platform: ZynqMP | Security: none | Cores: a53-0

```
the_ROM_image:
{
  [bootloader, destination_cpu=a53-0] path/to/your/fsbl.elf
  [destination_cpu=pmu] path/to/your/pmufw.elf
  [destination_device=pl] path/to/your/design.bit
  [destination_cpu=a53-0] path/to/your/application.elf
}
```

---

## Example 10: ZynqMP — RSA

Platform: ZynqMP | Security: RSA-4096 | Features: [fsbl_config] bh_auth_enable

```
the_ROM_image:
{
  [fsbl_config] bh_auth_enable
  [auth_params] ppk_select=0
  [pskfile] path/to/your/rsa4096_psk.pem
  [sskfile] path/to/your/rsa4096_ssk.pem
  [bootloader, destination_cpu=a53-0, authentication=rsa] path/to/your/fsbl.elf
  [destination_cpu=pmu, authentication=rsa] path/to/your/pmufw.elf
  [destination_device=pl, authentication=rsa] path/to/your/design.bit
  [destination_cpu=a53-0, authentication=rsa] path/to/your/application.elf
}
```

---

## Example 11: Zynq — non-secure

Platform: Zynq | Security: none

```
the_ROM_image:
{
  [bootloader] path/to/your/fsbl.elf
  path/to/your/system.bit
  path/to/your/application.elf
}
```

---

## Example 12: Zynq — RSA

Platform: Zynq | Security: RSA-2048

```
# src:bif.y:764 ([auth_params] is ZYNQMP-only — forbidden on Zynq)
# src:authentication-zynq.cpp (RSA_2048_KEY_LENGTH)
# ug:UG1283 §Encrypting-Zynq-7000-Device-Partitions (standard RSA flow)
the_ROM_image:
{
  [pskfile] path/to/your/rsa2048_psk.pem
  [sskfile] path/to/your/rsa2048_ssk.pem
  [bootloader, authentication=rsa] path/to/your/fsbl.elf
  [authentication=rsa] path/to/your/system.bit
}

/* Bootgen CLI: bootgen -arch zynq -image this.bif -o boot.bin */
```

---

## Example 13: Versal — HSM (RSA, pre-signed)

Platform: Versal | Security: RSA HSM | Features: pre-signed, no private keys, bh_auth_enable, metaheader

```
new_bif:
{
 image_config
 {
  bh_auth_enable
 }
 ppkfile = path/to/your/rsa_ppk.pub
 spkfile = path/to/your/rsa_spk.pub
 spksignature = path/to/your/spk.sig
 id_code = 0x14CA8093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
  partition
  {
   id = 0x02
   type = bootimage
   authentication = rsa
   presign = path/to/your/bootimage.sig
   file = path/to/your/design.pdi
  }
  partition
  {
   id = 0x01
   type = bootloader
   authentication = rsa
   presign = path/to/your/plm.sig
   file = path/to/your/plm.elf
  }
  partition
  {
   id = 0x04
   core = psm
   authentication = rsa
   presign = path/to/your/psm.sig
   file = path/to/your/psm_fw.elf
  }
 }
 metaheader
 {
  authentication = rsa
  ppkfile = path/to/your/rsa_ppk.pub
  spkfile = path/to/your/rsa_spk.pub
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
  partition
  {
   id = 0x05
   core = a72-0
   authentication = rsa
   presign = path/to/your/apu.sig
   file = path/to/your/apu_app.elf
  }
 }
}
```

---

## Example 14: Versal — SSIT (multi-SLR, RSA authenticated)

Platform: Versal Gen 1 | Security: RSA | Features: SSIT, 2 SLRs, slr-boot/slr-config partition types

```
new_bif:
{
 id_code = 0x14CA8093
 extended_id_code = 0x01
 id = 0x1
 image_config
 {
  bh_auth_enable
 }
 pskfile = path/to/your/rsa_psk.pem
 sskfile = path/to/your/rsa_ssk.pem
 image
 {
  name = pmc_subsys
  id = 0x1c000001
  partition
  {
   id = 0x01
   type = bootloader
   authentication = rsa
   file = path/to/your/plm.elf
  }
  partition
  {
   id = 0x04
   core = psm
   authentication = rsa
   file = path/to/your/psm_fw.elf
  }
 }
 metaheader
 {
  authentication = rsa
  pskfile = path/to/your/rsa_psk.pem
  sskfile = path/to/your/rsa_ssk.pem
 }
 image
 {
  name = pl_cfi_subsys
  id = 0x18700000
  partition
  {
   id = 0x10
   type = slr-boot
   slr = 1
   authentication = rsa
   file = path/to/your/slr1_boot.bin
  }
  partition
  {
   id = 0x11
   type = slr-config
   slr = 1
   authentication = rsa
   file = path/to/your/slr1_config.bin
  }
  partition
  {
   id = 0x12
   type = slr-slave-boot
   slr = 2
   authentication = rsa
   file = path/to/your/slr2_boot.bin
  }
  partition
  {
   id = 0x13
   type = slr-slave-config
   slr = 2
   authentication = rsa
   file = path/to/your/slr2_config.bin
  }
 }
}
```

---

## Example 15: ZynqMP — HSM (RSA, pre-signed)

Platform: ZynqMP | Security: RSA HSM | Features: pre-signed, no private keys, bracket-style

```
the_ROM_image:
{
  [fsbl_config] bh_auth_enable
  [auth_params] ppk_select=0; spk_id=0x00000000
  [ppkfile] path/to/your/rsa_ppk.pub
  [spkfile] path/to/your/rsa_spk.pub
  [spksignature] path/to/your/spk.sig
  [bootloader, authentication=rsa, presign=path/to/your/fsbl.sig] path/to/your/fsbl.elf
  [destination_cpu=a53-0, authentication=rsa, presign=path/to/your/app.sig] path/to/your/app.elf
}

/* Bootgen CLI: bootgen -arch zynqmp -image this.bif -o boot.bin */
```

---

## Example 16: SpartanUP — HSM (HSS-SHAKE256, pre-signed)

Platform: SpartanUP (SU45+) | Security: HSS-SHAKE256 HSM | Features:
pre-signed, no private keys, no metaheader, no `bh_auth_enable`.

```
# src:bifoptions.cpp:2784 (bh_auth_enable → LOG_ERROR on SpartanUP)
new_bif:
{
 id_code = 0x04E88093
 extended_id_code = 0x01
 id = 0x1
 ppkfile = path/to/your/primary.hss.pub
 spkfile = path/to/your/secondary.hss.pub
 spksignature = path/to/your/spk.sig
 image
 {
  name = pmc_subsys
  id = 0x1c000001
  partition
  {
   id = 0x01
   type = bootloader
   authentication = hss-shake256
   presign = path/to/your/plm.sig
   file = path/to/your/plm.elf
  }
  partition
  {
   id = 0x02
   type = pldata
   authentication = hss-shake256
   presign = path/to/your/bitstream.sig
   file = path/to/your/bitstream.rcdo
  }
 }
}

/* Bootgen CLI: bootgen -arch spartanup -image this.bif -o boot.bin */
```

---

## Example 17: Versal — Image Store PDI (runtime PDI swap)

Platform: Versal | Security: none | Feature: `imagestore = <id>` **attribute**
(Versal-family) for runtime image-store PDI loading.

> **`imagestore` is a partition ATTRIBUTE, not a `type`.** `imagestore = <id>`
> auto-sets the partition type to IMAGE_STORE_PDI (`bif.y:529-530`), so
> `type = imagestore` is a syntax error. The image-store partition references a
> **separate `write_imagestore_pdi:` block via `section = <label>`** — NOT `file =`
> (UG1283 §imagestore, p.270; `bif.y:533-534`). `<id>` is a 32-bit numeric
> expression (hex or decimal), unique per stored PDI.

```
// Image Store PDI — UG1283 §imagestore (p.270). Two blocks:
//   write_imagestore_pdi: builds the stored PDI; master: references it via section=.
write_imagestore_pdi:
{
    id_code = 0x14CA8093
    extended_id_code = 0x01
    id = 0xb
    image
    {
        name = pl_noc, id = 0x18700000
        partition
        {
            id = 0xb05
            type = cdo
            file = path/to/your/imagestore.rnpi
        }
    }
}
master:
{
    id_code = 0x14CA8093
    extended_id_code = 0x01
    id = 0x2
    image
    {
        name = IMAGE_STORE, id = 0x18700000
        partition
        {
            id = 0xb15
            imagestore = 0x1
            section = write_imagestore_pdi
        }
    }
}
```

> The PLM stores the PDI (DDR) for runtime use (partial reconfiguration, subsystem
> restart) without an external boot device. `imagestore = <id>` tags the stored PDI
> (unique 32-bit id); `section = <block>` names the `write_imagestore_pdi:` block
> whose output becomes this partition. A complete boot image's `master:` block also
> carries the normal boot subsystems (pmc/lpd/fpd/apu); add them alongside the
> IMAGE_STORE image. Run with all `-image` blocks: `bootgen -arch versal -image this.bif -o design.pdi -w on`.

---

## Example 18: ZynqMP — `[init]` register-init partition + `[udf_bh]` user-defined boot header

Platform: ZynqMP | Security: none | Features: register initialisation +
user-defined boot-header field (advanced layout).

```
# src:bif.l:196-197 | ug:UG1283 §register-init §user-defined-BH
the_ROM_image:
{
  [init] path/to/your/regs.init
  [udf_bh] path/to/your/udf_bh.bin
  [bootloader, destination_cpu=a53-0] path/to/your/fsbl.elf
  [destination_cpu=pmu] path/to/your/pmufw.elf
  [destination_device=pl] path/to/your/design.bit
  [destination_cpu=a53-0] path/to/your/application.elf
}
```

> `[init]` injects up to 256 register writes into the boot header that
> the BootROM executes before the FSBL runs (used to override default
> peripheral / clocking settings).
> `[udf_bh]` populates the 19-word user-defined area of the boot header.
> Both are placed **before** `[bootloader]` so they land in the boot
> header region of `boot.bin`. ZynqMP / Zynq only — these tokens are not
> accepted on any Versal-family arch.

---

## Example 19: ZynqMP — per-partition `keysrc_encryption` override

Platform: ZynqMP | Security: RSA + AES with per-partition key-source
override | Feature: bracket-form `keysrc_encryption=` overrides the
top-level `[keysrc_encryption]` on a single partition.

```
# src:bif.l (keysrc_encryption: top-level and per-partition) | ug:UG1283 §encryption-key-sources
the_ROM_image:
{
  [fsbl_config] bh_auth_enable
  [auth_params] ppk_select=0
  [keysrc_encryption] efuse_red_key                                                       # global default
  [pskfile] path/to/your/rsa4096_psk.pem
  [sskfile] path/to/your/rsa4096_ssk.pem
  [bootloader, destination_cpu=a53-0, authentication=rsa, encryption=aes,
     aeskeyfile=path/to/your/fsbl_key.nky]                  path/to/your/fsbl.elf        # inherits efuse_red_key
  [destination_cpu=pmu, authentication=rsa, encryption=aes,
     aeskeyfile=path/to/your/pmufw_key.nky]                 path/to/your/pmufw.elf       # inherits efuse_red_key
  [destination_cpu=a53-0, authentication=rsa, encryption=aes,
     keysrc_encryption=kup_key,
     aeskeyfile=path/to/your/app_key.nky]                   path/to/your/application.elf # overrides → kup_key
}
```

> `[keysrc_encryption]` (top-level) sets the default AES key source for
> every encrypted partition. The same token, written
> `keysrc_encryption=<value>` inside a partition's bracket list,
> overrides that default on **that one partition only**. The pattern is
> not specific to `kup_key` — any keysrc valid for the platform
> (`platform-rules.md` §Key Sources Per Platform) can appear in either
> position. `kup_key` is the most common reason to override because it
> lets the FSBL hand-deliver a partition-specific user key at run-time;
> the global default typically stays on an eFUSE or BBRAM source.

---

## Example 20: ZynqMP — `[fsbl_config]` multi-flag list

Platform: ZynqMP | Security: RSA-4096 authentication | Feature:
multiple FSBL-behavior flags in one `[fsbl_config]` clause.

```
# src:bif.l (fsbl_config flag list) | ug:UG1283 §fsbl_config
the_ROM_image:
{
  [fsbl_config] bh_auth_enable, opt_key
  [auth_params] ppk_select=0
  [pskfile] path/to/your/rsa4096_psk.pem
  [sskfile] path/to/your/rsa4096_ssk.pem
  [bootloader, destination_cpu=a53-0, authentication=rsa] path/to/your/fsbl.elf
  [destination_cpu=pmu,    authentication=rsa]            path/to/your/pmufw.elf
  [destination_cpu=a53-0, authentication=rsa]             path/to/your/application.elf
}
```

> `[fsbl_config]` takes a **comma-separated list** of FSBL-behavior
> flags. The most common pair is shown above:
> - `bh_auth_enable` — enables Boot-Header-based authentication for the
>   FSBL (instead of FSBL-controlled auth);
> - `opt_key` — enables the operational-key key-wrapping path so the
>   FSBL can derive a runtime AES key from a wrapped operational key.
>
> Neither `bh_auth_enable` nor `opt_key` is a keysrc; both are flags
> consumed by the FSBL. Other documented `[fsbl_config]` flags (e.g.
> `pufhd_load`, `puf4kmode`) follow the same comma-separated syntax.
> This is ZynqMP-only — Versal-family uses
> `image_config { bh_auth_enable }` (block form) instead.

---

## Example 21: ZynqMP — `trustzone` per-partition bracket attribute

Platform: ZynqMP | Security: none (illustrates attribute placement
only) | Feature: TrustZone secure / non-secure marking on individual
partitions.

```
# src:bif.l (trustzone per-partition attr) | ug:UG1283 §TrustZone
the_ROM_image:
{
  [bootloader, destination_cpu=a53-0]                       path/to/your/fsbl.elf
  [destination_cpu=a53-0, trustzone=secure]                 path/to/your/secure_world.elf
  [destination_cpu=a53-1, trustzone=nonsecure]              path/to/your/nonsecure_world.elf
}
```

> `trustzone` is one of the documented per-partition optional attributes
> (`platform-rules.md` §Optional Per-Partition Attributes). It accepts
> `secure` or `nonsecure` and goes inside the partition's bracket list
> just like `destination_cpu` or `authentication`. The Bootgen FSBL
> uses the value to programme the TrustZone configuration for that
> partition's destination memory range before transferring control.
>
> This placement convention generalises: every entry in the Optional
> Per-Partition Attributes table (e.g. `core`, `partition_owner`,
> `boot_device`, `cluster`) is added to the bracket list the same way.
> A partition can carry several optional attributes simultaneously,
> separated by commas.

---

## Example 22: Zynq — HSM (RSA-2048, pre-signed)

Platform: Zynq | Security: RSA-2048 HSM | Features: pre-signed, no
private keys, bracket-style. Based on UG1283 §"Creating a Zynq-7000
SoC Device Boot Image using HSM Mode".

```
# src:authentication-zynq.cpp (RSA_2048_KEY_LENGTH, AuthHash::Sha2)
# src:bif.y:764 ([auth_params] is ZYNQMP-only)
# ug:UG1283 §Creating-a-Zynq-7000-SoC-Device-Boot-Image-using-HSM-Mode
the_ROM_image:
{
  [ppkfile] path/to/your/rsa2048_ppk.pub
  [spkfile] path/to/your/rsa2048_spk.pub
  [spksignature] path/to/your/spk.sha256.sig
  [bootloader, authentication=rsa, presign=path/to/your/fsbl.sha256.sig] path/to/your/fsbl.elf
  [authentication=rsa, presign=path/to/your/system.bit.sha256.sig] path/to/your/system.bit
}

/* Bootgen CLI: bootgen -arch zynq -image this.bif -o boot.bin -nonbooting */
```

> **Key differences from ZynqMP HSM (Example 15):**
>
> - **RSA-2048** keys (not 4096) — Zynq only supports RSA-2048
>   (`authentication-zynq.cpp:116`: `RSA_2048_KEY_LENGTH`)
> - **SHA-256** hashes (not SHA-384) — Zynq uses SHA-2
>   (`authentication-zynq.cpp:117`: `hashType = AuthHash::Sha2`)
> - **No `[auth_params]`** — `bif.y:764` emits `LOG_ERROR` if
>   `[auth_params]` (ppk_select, spk_select, spk_id) is used on
>   anything other than ZYNQMP
> - **No `[fsbl_config] bh_auth_enable`** — Zynq does not support
>   `bh_auth_enable` (Feature Guards table: `bh_auth_enable = no`)
> - **No `destination_cpu`** — Zynq does not support CPU targeting
>   (`bifoptions.cpp`: Bootgen emits `LOG_ERROR` if used on Zynq)
> - **No `bhsignature`** — boot-header authentication is Zynq
>   UltraScale+ only; Zynq 7000 has no BH signature field
> - Signature file extension convention: `.sha256.sig` (not `.sha384.sig`)
> - CLI adds `-nonbooting` for per-partition AC generation stages
>   (UG1283 Stage 5)

---

## Example 23: Zynq — AES encryption

Platform: Zynq | Security: AES-CBC encryption (no authentication) |
Features: global aeskeyfile, eFUSE key source.

```
# src:bifoptions.cpp:1126-1132 (keysrc: only efuse|bbram on Zynq)
# src:bootimage-zynq.cpp:360 (per-partition aeskeyfile NOT supported)
# ug:UG1283 §Encrypting-Zynq-7000-Device-Partitions
the_ROM_image:
{
  [aeskeyfile] path/to/your/aes_key.nky
  [keysrc_encryption] efuse
  [bootloader, encryption=aes] path/to/your/fsbl.elf
  [encryption=aes] path/to/your/application.elf
}

/* Bootgen CLI: bootgen -arch zynq -image this.bif -o boot.bin -encrypt efuse */
```

> **Key differences from ZynqMP / Versal AES encryption:**
>
> - **Global `[aeskeyfile]` only** — per-partition `aeskeyfile=` inside
>   bracket lists is not supported on Zynq
>   (`bootimage-zynq.cpp:360-362`: Bootgen emits `LOG_ERROR`)
> - **`[keysrc_encryption]` accepts only `efuse` or `bbram`** — no
>   `efuse_red_key`, `efuse_gry_key`, `efuse_blk_key`, `bh_gry_key`,
>   etc. (`bifoptions.cpp:1126-1132`)
> - **AES-CBC** with HMAC (not AES-GCM like ZynqMP/Versal)
> - **No `destination_cpu`** — Zynq does not support CPU targeting
> - CLI requires `-encrypt efuse` or `-encrypt bbram` on the command
>   line to specify key source

---

## Example 24: VersalNet — RSA

Platform: VersalNet | Security: RSA-4096 | Features: metaheader, no
`bh_auth_enable` (VersalNet determines BH vs eFuse auth from eFuse
bits — `bifoptions.cpp:2784` emits `LOG_ERROR`), core `a78-0` (not
`a72-0`), R5 core `r52-0` (not `r5-0`).

```
# src:bifoptions.cpp:2784 (bh_auth_enable → LOG_ERROR on VersalNet)
# src:template_engine.py:3252 (id_code = 0x14D80093)
# ug:UG1283 §VersalNet-Authentication
new_bif:
{
 pskfile = path/to/your/rsa4096_psk.pem
 sskfile = path/to/your/rsa4096_ssk.pem
 id_code = 0x14D80093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = rsa
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = rsa
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = rsa
  pskfile = path/to/your/rsa4096_psk.pem
  sskfile = path/to/your/rsa4096_ssk.pem
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = rsa
       file = path/to/your/lpd_data.cdo
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = rsa
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = rsa
       file = path/to/your/apu_application.elf
   }
 }
}

/* Bootgen CLI: bootgen -arch versalnet -image this.bif -o boot.bin */
```

> **Key differences from Versal Gen 1 RSA (Example 2):**
>
> - **No `image_config { bh_auth_enable }`** — VersalNet determines
>   authentication mode from eFuse bits, not from BIF flag
>   (`bifoptions.cpp:2784`)
> - **`id_code = 0x14D80093`** (not `0x14CA8093`)
> - **APU core `a78-0`** (not `a72-0`)
> - **R5 core `r52-0`** (not `r5-0`) when R5 partitions are present
> - **Grey keys accepted** — `efuse_gry_key` / `bbram_gry_key` are
>   valid on VersalNet (deprecated on Versal Gen 1 only,
>   `bifoptions.cpp:1263-1271`)
> - `-arch versalnet` on CLI (not `-arch versal`)

---

## Example 25: Versal — ECDSA-P384

Platform: Versal Gen 1 | Security: ECDSA-P384 | Features:
`image_config { bh_auth_enable }`, metaheader. Demonstrates ECDSA key
files vs RSA key files (same `pskfile`/`sskfile` BIF tokens, different
PEM content).

```
# src:bif.l:265-266 (ecdsa / ecdsa-p384 → Authentication::ECDSA)
# ug:UG1283 §Versal-ECDSA-Authentication
new_bif:
{
 pskfile = path/to/your/ecdsa-p384_psk.pem
 sskfile = path/to/your/ecdsa-p384_ssk.pem
 id_code = 0x14CA8093
 extended_id_code = 0x01
 id = 0x1
 image_config
 {
  bh_auth_enable
 }
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = ecdsa-p384
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = ecdsa-p384
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = ecdsa-p384
  pskfile = path/to/your/ecdsa-p384_psk.pem
  sskfile = path/to/your/ecdsa-p384_ssk.pem
 }
}

/* Bootgen CLI: bootgen -arch versal -image this.bif -o boot.bin */
```

> **Key differences from Versal RSA (Example 2):**
>
> - `authentication = ecdsa-p384` (not `rsa`)
> - Key files contain EC P-384 keys (not RSA-4096)
> - `ecdsa-p384` and `ecdsa` are synonyms in the parser
>   (`bif.l:265-266`: both map to `Authentication::ECDSA`)
> - ECDSA-P521 is also valid on Versal but **forbidden on bootloader
>   and metaheader** (BootROM limitation) — use P384 for those partitions

---

## Example 26: Versal 2VE/2VM — ECDSA-P384

Platform: Versal 2VE/2VM | Security: ECDSA-P384 | Features:
metaheader, full lpd_subsys, fpd_subsys, asufw (id=0x10). No
`bh_auth_enable` (2VE/2VM does not support it).

```
# src:bifoptions.cpp:2784 (bh_auth_enable → LOG_ERROR on VERSALGEN2)
new_bif:
{
 pskfile = path/to/your/ecdsa-p384_psk.pem
 sskfile = path/to/your/ecdsa-p384_ssk.pem
 id_code = 0x04DB0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = ecdsa-p384
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = ecdsa-p384
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = ecdsa-p384
  pskfile = path/to/your/ecdsa-p384_psk.pem
  sskfile = path/to/your/ecdsa-p384_ssk.pem
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = ecdsa-p384
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       authentication = ecdsa-p384
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = ecdsa-p384
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = asufw
  id = 0x1c000002
   partition
   {
       id = 0x0F
       type = cdo
       authentication = ecdsa-p384
       file = path/to/your/asu_data.cdo
   }
   partition
   {
       id = 0x10
       core = asu
       authentication = ecdsa-p384
       file = path/to/your/asufw.elf
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = ecdsa-p384
       file = path/to/your/apu_application.elf
   }
 }
}

/* Bootgen CLI: bootgen -arch versal_2ve_2vm -image this.bif -o boot.bin */
```

---

## Example 27: SpartanUP — LMS-SHAKE256

Platform: SpartanUP (SU45+) | Security: LMS-SHAKE256 | Features: no
metaheader, no `bh_auth_enable`. Structurally identical to HSS
(Example 8) except for `authentication` value — LMS uses a single
Merkle tree vs HSS's hierarchical tree, but the BIF syntax is the same.

```
# src:bif.l:268 (lms-shake256 → Authentication::LMS_SHAKE256)
# src:bif.y:795 (lms_key_params forbidden on Zynq/ZynqMP/Versal Gen 1)
# ug:UG1283 §authentication (SpartanUP LMS example)
new_bif:
{
 ppkfile = path/to/your/primary.lms.pub
 pskfile = path/to/your/primary.lms.prv
 spkfile = path/to/your/secondary.lms.pub
 sskfile = path/to/your/secondary.lms.prv
 id_code = 0x04EA0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = lms-shake256
       file = path/to/your/spartanup_fw.elf
   }
   partition
   {
       id = 0x02
       type = pldata
       authentication = lms-shake256
       file = path/to/your/bitstream.rcdo
   }
 }
}

/* Bootgen CLI: bootgen -arch spartanup -image this.bif -o boot.bin */
```

> **LMS vs HSS in BIF:**
>
> - Boot BIF syntax is identical — only the `authentication =` value
>   differs (`lms-shake256` vs `hss-shake256`)
> - Both use four key files: `ppkfile`, `pskfile`, `spkfile`, `sskfile`
> - The structural difference (single Merkle tree vs hierarchical) is
>   expressed in the `lms_key_params` block used during **key
>   generation** (`-generate_keys lms`), NOT in the boot BIF
> - `lms_key_params` is supported on 2VE/2VM and SpartanUP only
>   (`bif.y:795`: emits `LOG_ERROR` on Zynq / ZynqMP / Versal Gen 1)

---

## Example 28: Versal 2VE/2VM — LMS-SHAKE256

Platform: Versal 2VE/2VM | Security: LMS-SHAKE256 (post-quantum) |
Features: metaheader, full lpd_subsys, fpd_subsys, asufw (id=0x10).
Structurally identical to Example 6 (HSS-SHAKE256) except for the
`authentication` value.

```
# src:bif.l:268 (lms-shake256 → Authentication::LMS_SHAKE256)
new_bif:
{
 ppkfile = path/to/your/primary.lms.pub
 pskfile = path/to/your/primary.lms.prv
 spkfile = path/to/your/secondary.lms.pub
 sskfile = path/to/your/secondary.lms.prv
 id_code = 0x04DB0093
 extended_id_code = 0x01
 id = 0x1
 image
 {
  name = pmc_subsys
  id = 0x1c000001
   partition
   {
       id = 0x01
       type = bootloader
       authentication = lms-shake256
       file = path/to/your/plm.elf
   }
   partition
   {
       id = 0x09
       type = pmcdata
       authentication = lms-shake256
       load = 0xF2000000
       file = path/to/your/pmc_data.cdo
   }
 }
 metaheader
 {
  authentication = lms-shake256
  ppkfile = path/to/your/primary.lms.pub
  pskfile = path/to/your/primary.lms.prv
  spkfile = path/to/your/secondary.lms.pub
  sskfile = path/to/your/secondary.lms.prv
 }
 image
 {
  name = lpd_subsys
  id = 0x04210002
   partition
   {
       id = 0x0C
       type = cdo
       authentication = lms-shake256
       file = path/to/your/lpd_data.cdo
   }
   partition
   {
       id = 0x0B
       core = psm
       authentication = lms-shake256
       file = path/to/your/psm_fw.elf
   }
 }
 image
 {
  name = fpd_subsys
  id = 0x420c003
   partition
   {
       id = 0x08
       type = cdo
       authentication = lms-shake256
       file = path/to/your/fpd_data.cdo
   }
 }
 image
 {
  name = asufw
  id = 0x1c000002
   partition
   {
       id = 0x0F
       type = cdo
       authentication = lms-shake256
       file = path/to/your/asu_data.cdo
   }
   partition
   {
       id = 0x10
       core = asu
       authentication = lms-shake256
       file = path/to/your/asufw.elf
   }
 }
 image
 {
  name = apu_subsys
  id = 0x1c000003
   partition
   {
       id = 0x0a
       core = a78-0
       authentication = lms-shake256
       file = path/to/your/apu_application.elf
   }
 }
}

/* Bootgen CLI: bootgen -arch versal_2ve_2vm -image this.bif -o boot.bin */
```
