<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create Boot Image

Turn a natural-language request into a bootable image (`BOOT.BIN` / `.pdi`) by
(1) **authoring a BIF** (the *create* step) and (2) **running Bootgen** to emit + validate the
image (the *build* step, run only when asked). By default author the BIF and stop; run Bootgen
when the user asks to build/generate the image (or asks to create *and* build in one go).

> **Headless / no IDE?** This is a shell skill — it already runs in any terminal with a
> sourced Vitis env (below); no wrapper needed. For running the API skills without the IDE,
> see [`boot-image/headless.md`](boot-image/headless.md).

> **Docs on tap (optional).** If a URL-based AMD documentation MCP (e.g. `vivado_doc_search`)
> is reachable, query it to decode an error/warning, debug unexpected behavior, or look up a
> device/BIF/AIE/API detail this skill's references don't cover. Prefer the bundled references
> for the common cases; reach for the doc MCP on errors and the long tail.

## Prerequisites

- Sourced Vitis environment (`source <VITIS_INSTALL>/Vitis/settings64.sh`) so
  `bootgen` is on `PATH`.
- A Python 3.6+ interpreter for the `create_boot_image.py` helper (standard library only). If
  the system `python3` is older/unavailable, run the helper with Vitis's bundled Python.
- The boot components the image needs (FSBL/PLM, optional PMUFW, app ELFs,
  bitstream/PDI). These come from `vitis-embedded`
  (FSBL/PLM/PMUFW) and `vitis-embedded` (application
  ELFs), or from the user. A prerequisite that is only **created-but-unbuilt** has no ELF/PDI to
  package — **offer to build it (ask + approve) before running Bootgen** (see step 3 / *Resolve*).
  See the shared chaining contract
  ([`chaining.md`](chaining.md)):
  this skill **consumes verified artifact PATHs**. Programming and on-target debug are
  optional follow-on work outside this skill.

> **This is a shell skill (stays shell, not in-console).** Bootgen is a standalone CLI, so
> this skill runs in a **terminal** — it is **not** ported into the Vitis Python Console and
> Bootgen is never run through `runVitisPython`. When a live Vitis session exists it *may*
> query the live `client` for component discovery (below), but the image itself is built by
> the shell `bootgen`. It only consumes on-disk paths produced by the producer skills.

## How it works: author a BIF (create), then run Bootgen (build)

A **BIF** (boot image format file) describes what goes into the image. The flow
is always the same — but it splits into a **create** step and a **build** step:

```
CREATE (default):   author a BIF   →   report it (image not built yet)
BUILD (when asked): run Bootgen on it (--bif)  →  validate the image
```

On a "create"/"author" request, author + save the BIF and **stop**; then offer to run Bootgen.
Run Bootgen only when the user asks to build/generate the image (or to create *and* build at once).

**Authoring the BIF (the normal way):** write it using the self-contained,
source-cited references —
**[boot-image/canonical-bifs.md](boot-image/canonical-bifs.md)** (28 golden BIFs;
start from the closest example, adapt paths, **pick the core for the silicon**)
and **[boot-image/platform-rules.md](boot-image/platform-rules.md)** (valid
auth/keys/cores/types per platform). Save it, then pass it to the driver with
`--bif`. This covers all **bootgen-authored** images — non-secure or secure
(RSA/ECDSA/AES/LMS/HSS), all Versal-family PDIs, HSM, SSIT, and Image Store (Ex.17).

**Boundary — what bootgen does NOT generate.** *Segmented Configuration*
(`boot.pdi` + `pl.pdi`) and *DFX partial* PDIs are produced by the **Vivado / v++
hardware flow** (`write_device_image` segmented / the DFX flow), **not** bootgen.
This skill's job for those is to **package or extend** a *pre-built* PDI into a boot
image; the runtime load of `pl_aie.pdi` (+ dtbo) is separate deployment work.
Don't expect bootgen to split/create the segments — feed
it the PDI the hardware flow already produced (as a partition, or via `bootgen
-image <bif> ... ` to extend an existing PDI).

**Auto shortcut (optional convenience):** for the **ZynqMP/Zynq** case ("package
these ELFs into a BOOT.BIN"), the driver writes the BIF *for* you from flags
(`--bootloader/--pmufw/--app/--bitstream`, plus `--bl31/--u-boot` for Linux). You can
also make it **secure**: `--authenticate rsa --psk <psk.pem> --ssk <ssk.pem>` and/or
`--encrypt aes --aes-key <key.nky> [--key-src efuse_red_key|bbram_red_key|kup_key]`
inject the security attributes onto every partition (models canonical Ex.10/12/19).
**Versal-family and advanced secure layouts are still authored via `--bif`.**

## Workflow (create = steps 1–4; build = steps 5–6, only when asked)

```
# CREATE (default)
- [ ] 1. Parse request -> device_family, boot components, Linux vs baremetal, security; note whether the user asked to BUILD/generate the image
- [ ] 2. Determine BIF template + the required-components manifest (which files the BIF needs)
- [ ] 3. Resolve components from the workspace: scan, fill found real paths, ask the user for any missing; a created-but-unbuilt prerequisite (app/FSBL/bitstream) has no artifact yet -> offer to build it (ask + approve)
- [ ] 4. Get the BIF: author from references (pick core for the silicon; required for Versal-family/advanced secure), or use the auto shortcut for Zynq/ZynqMP with `--bif-only` (writes the BIF, no Bootgen). Save it and report it (image not built).
# BUILD (only when asked)
- [ ] 5. Run the driver (bootgen) to produce BOOT.BIN / .pdi
- [ ] 6. Validate the image and report its path + any warnings
```

> **Syntax-check tip:** even with stand-in paths you can validate BIF
> *structure* before delivery — `bootgen -arch <arch> -image x.bif -o /tmp/x -w on`.
> A `cannot open file` error means the BIF parsed OK; a `syntax error` means it is
> malformed and must be fixed.

### 1. Parse the request (spec inputs)

| Input | Required | Source | Example |
|-------|----------|--------|---------|
| `device_family` / `arch` | Y | Inferred from XSA or prompt | `versal`, `zynqmp`, `zynq` |
| `pdi_or_xsa` | Y (Versal) | Base PDI/XSA path | `/path/design.xsa` |
| `bootloader` (FSBL/PLM) | Y (auto for Zynq/ZynqMP) | Platform or path | `fsbl.elf` / `plm.elf` |
| `pmufw` / PSM | N | Platform | `pmufw.elf` |
| `app` (app_elfs) | N | `vitis-embedded` / user | `hello.elf` |
| `atf` / `bl31` | N (Linux) | Path | `bl31.elf` |
| `u_boot` | N (Linux) | Path | `u-boot.elf` |
| `dtb` | N | Path | `system.dtb` (SD-side on ZynqMP) |
| `security` | N | Parsed from prompt ("encrypted", "authenticated") | ZynqMP/Zynq: `--authenticate rsa` (+`--psk/--ssk`) / `--encrypt aes` (+`--aes-key`) in simple mode; Versal-family / advanced → author the BIF |

Output default: `BOOT.BIN` for every family (SD/eMMC boot — the common case). For
Versal-family the PDI extension is cosmetic, so use `design.pdi` or another name only when the
user targets QSPI/OSPI or JTAG (see below).

> **Versal — a PDI regardless of extension; name it for the boot medium.** `bootgen -arch versal`
> always emits a **PDI**; the file extension is cosmetic (renaming does not change the format).
> **SD/eMMC boot → name it `BOOT.BIN`** — the BootROM searches the FAT partition for `boot.bin`
> (`boot0001.bin`, `boot0002.bin`, … for MultiBoot/fallback), per UG1304. **QSPI/OSPI / JTAG →
> `.pdi`** is fine — it's passed to `program_flash` / `device_program` by path, so the name is
> arbitrary. Don't rename a PDI to `.pdi` assuming the BootROM requires it: for SD boot it wants
> `BOOT.BIN`.

### 2. Template + required-components manifest

Select the per-family BIF template (closest [canonical example](boot-image/canonical-bifs.md)).
Its partitions define the **required-components manifest** — the files this BIF
needs (e.g. ZynqMP non-secure → `fsbl.elf` + optional `pmufw.elf`/`app.elf`/`.bit`;
Versal PDI → `plm.elf` + `pmc_data.cdo` + `psm_fw.elf` + `lpd/fpd .cdo` + PL/NoC
`<design>.rcdo`/`<design>.rnpi` (`pl_cfi`) + `ai_engine_data.cdo` (if the design has AIE) + app;
Linux adds `bl31.elf`/`u-boot.elf`; secure adds key files/`.nky`).

> **Versal-family: base the BIF on the XSA's own BIF.** A platform built from an XSA ships the
> design's BIF at `<plat>/export/<plat>/hw/extracted/<design>/pdi_files/<board>.bif` — it already
> lists every partition the hardware needs (PMC, LPD/PSM, FPD, `pl_cfi`, AIE) with the correct
> file names and ids. Use it as the base (rewrite its relative `file =` paths to absolute), then
> append the `apu_subsys` (and any R5) application partitions. **Any app that runs from DDR
> requires the `pl_cfi` (`.rcdo`/`.rnpi`) partitions** — without them the PDI builds but cannot
> bring up DDR, so the app never loads.

### 3. Resolve components from the workspace (YOU do this — not the driver)

The driver is a deterministic executor; **component discovery is your job** (you
handle fuzzy names/layouts and asking far better than a script).

> **Fast path — when the IDE is open, ask the live console FIRST (do this before any
> file crawling).** One `runVitisPython` round-trip gives you every component's name
> **and on-disk location**; a single shell `find` then locates the deep boot artifacts.
> This is dramatically faster than walking the tree by hand:
> ```python
> # 1 call: names + locations of everything in the open workspace
> for c in client.list_components():
>     print(c.name, "->", getattr(c, "path", getattr(c, "directory", "?")))
> # then, for the app ELF / platform export root:
> print(client.get_component(name="<app>").report())
> ```
> Then one shell `find <platform_export_root> -type f \( -name 'plm.elf' -o -name
> '*.cdo' -o -name 'psm_fw.elf' -o -name '*.pdi' -o -name '*.rcdo' -o -name '*.rnpi'
> -o -name '*.bif' \)` to grab the PLM/CDO/PSM/PDI, the PL/NoC partitions, and the XSA's BIF.
> **Do NOT** enumerate the workspace with repeated `getWorkspaceFileList` calls or hunt
> for boot files with `findFilesByPattern` — the pattern search is slow and a per-directory
> crawl wastes many round-trips. Live-`client` query (paths) + one shell `find` (artifacts)
> is the whole discovery step.

Components come from three sources (often mixed):
- **(1) Workspace** — auto-discover from a built platform/app (scan, below).
- **(2) User-provided paths** — if the user gives file paths directly, use them in
  the BIF immediately (no scan needed for those).
- **(3) Neither found nor given** — leave a `path/to/your/…` stand-in for that
  required file, list it for the user, and fill + build once they supply it.

For each manifest entry:

1. **Scan the workspace.** **If a live Vitis session is available** (the agent runs in the
   IDE console), prefer resolving real artifact paths through the live `client` via
   `runVitisPython` — it knows the open workspace and what is actually built:
   `client.list_components()` (what exists), `client.get_component(name).report()` (an app's
   `.elf`), `client.find_platform_in_repos(name)` (the platform `.xpfm` + boot components).
   **Otherwise (headless)** use `getWorkspaceDirectoryStructure` or `find`. Either way, match
   by **role + conventional location** — this works whether the components came from
   `vitis-embedded`/`-app` (or their `-console` variants), the Vitis GUI, Vivado, or
   PetaLinux (the Vitis component layout is the same):
   - bootloader → `…/<plat>/export/<plat>/sw/boot/fsbl.elf` (ZynqMP) or the PLM (Versal)
   - PMUFW/PSM → `…/sw/qemu/pmufw.elf` / `psm_fw.elf`
   - app ELF → `…/<app>/build/<app>.elf`
   - **Versal-family** (under `<plat>/export/<plat>/hw/extracted/<design>/pdi_files/`): PLM +
     PMC/LPD/FPD/AIE CDOs in `gen_files/`, PSM `static_files/psm_fw.elf`, PL/NoC
     `<design>.rcdo`/`<design>.rnpi`, and the design's base BIF `<board>.bif`
   - CDOs / bitstream / PDI → XSA `gen_files` / Vivado export
2. **Fill found real paths** into the BIF (replace the `path/to/your/…` stand-ins).
3. **Auto-fill structural defaults** (no need to ask) and report them: `id_code`
   per family, `extended_id_code = 0x01`, APU core (**a72** Versal · **a78**
   VersalNet/2VE-2VM · **a53** ZynqMP), `pmcdata load = 0xF2000000`, canonical
   partition ids, output name (`BOOT.BIN`/`.pdi`).
4. **Report found vs missing and ask for the rest.** Say which files you found and
   added, then list the missing ones and ask the user to provide paths — do **not**
   tell them to "go build a platform." Keys/`.nky`/Vivado outputs are usually not in
   the workspace → ask for those. **Never generate, derive, or substitute keys or other
   security material yourself** — if a secure image is requested but no keys are supplied, leave
   the key paths as `path/to/your/…` stand-ins and stop at the BIF; generate keys only when the
   user explicitly asks for *development* keys (the ZynqMP simple-mode `.nky` auto-generation
   shortcut below is the one documented exception). Example:
   > Found and added: `plm.elf`, `pmc_data.cdo`, `psm_fw.elf`. Defaults applied:
   > id_code `0x14CA8093`, core `a72-0`, output `design.pdi`. **Missing — please
   > provide paths:** `lpd_data.cdo`, `fpd_data.cdo`, app `.elf`.
5. **BIF ready when resolved.** Once every required file is real, the BIF is complete — save it
   and report it. **Run the driver (Bootgen) only when the user asks to build/generate the
   image**; otherwise offer to ("Run Bootgen to build the image now?"). If some files are still
   missing, hand back the partially-filled BIF + the missing list (never hard-fail) so the user
   can supply them and re-run.

**If a prerequisite is missing, or only created-but-unbuilt** (and the user gave no ready path),
don't dead-end: **offer to build the platform / app first** (ask + approve), then take the
produced components. **REQUIRED SUB-SKILL:** ask the user, **get approval**, then **load and
follow the sibling skill** (don't hand-author FSBL/PLM/app sources from memory) and take the
produced `.xpfm`/`.elf` **path**. The sibling skills (`vitis-embedded` /
`vitis-embedded`) run in the live IDE session via `runVitisPython`.

This skill itself is IDE-agnostic (it runs the standalone `bootgen` CLI — no Vitis
server, no workspace lock). The skills stay independent in code (this only takes the
sibling's produced artifact path) and the chain runs only with the user's approval.

For **Linux** add ATF (`bl31`) and `u-boot` (ZynqMP); the DTB/Image normally live
on the SD card, not in BOOT.BIN.

### 4. Get the BIF (the CREATE step)

This is the **create** step — produce the BIF and stop; run Bootgen (step 5) only when the user
asks to build the image.

- **Authored (normal):** write the BIF from
  [canonical-bifs.md](boot-image/canonical-bifs.md) (closest example) +
  [platform-rules.md](boot-image/platform-rules.md) (valid values); save it and
  pass `--bif <file>` at build time. Pick the core for the actual silicon (**a72** Versal ·
  **a78** VersalNet/2VE/2VM · **a53** ZynqMP). Required for secure + Versal-family.
- **Auto shortcut (Zynq/ZynqMP):** pass components as flags with **`--bif-only`** and the driver
  writes the BIF **without running Bootgen**. Linux: add `--atf/--bl31` and `--u-boot`. Secure
  (optional): `--authenticate rsa --psk --ssk` and/or `--encrypt aes --aes-key [--key-src]`.

### 5. Run the driver (the BUILD step — only when the user asks to build/generate the image)

Run Bootgen when the user asks to build/generate the image (or asked to create *and* build at
once); otherwise stop after the BIF and offer to run it. To **author the BIF only** (create, no
Bootgen), add `--bif-only`:

```bash
# CREATE only — author the BIF (auto shortcut), do NOT run Bootgen:
python3 references/boot-image/scripts/create_boot_image.py --arch zynqmp \
  --bootloader path/to/fsbl.elf --pmufw path/to/pmufw.elf \
  --app path/to/app.elf --bif-only --output-dir /path/to/out

# Authored BIF (the normal way — any platform, secure or non-secure):
python3 references/boot-image/scripts/create_boot_image.py --arch versal \
  --bif path/to/design.bif --output design.pdi --output-dir /path/to/out

# Zynq-7000 with AES encryption — needs the key source on the bootgen CLI:
python3 references/boot-image/scripts/create_boot_image.py --arch zynq \
  --bif zynq_secure.bif --output BOOT.BIN --bootgen-args "-encrypt efuse"

# Auto shortcut — non-secure ZynqMP BOOT.BIN, baremetal app:
python3 references/boot-image/scripts/create_boot_image.py --arch zynqmp \
  --bootloader path/to/fsbl.elf --pmufw path/to/pmufw.elf \
  --app path/to/app.elf --output-dir /path/to/out --output BOOT.BIN

# Auto shortcut — ZynqMP Linux BOOT.BIN (fsbl + pmufw + bitstream + ATF + u-boot):
python3 references/boot-image/scripts/create_boot_image.py --arch zynqmp \
  --bootloader fsbl.elf --pmufw pmufw.elf --bitstream design.bit \
  --bl31 bl31.elf --u-boot u-boot.elf --output-dir /path/to/out

# Auto shortcut — SECURE ZynqMP BOOT.BIN (RSA auth + AES encryption, models Ex.10/19):
python3 references/boot-image/scripts/create_boot_image.py --arch zynqmp \
  --bootloader fsbl.elf --pmufw pmufw.elf --app app.elf \
  --authenticate rsa --psk rsa4096_psk.pem --ssk rsa4096_ssk.pem \
  --encrypt aes --aes-key fsbl_key.nky --key-src efuse_red_key --output-dir /path/to/out
```

It runs `bootgen -arch <arch> -image <bif> -o <output> -w on -log error` and
prints `BOOT_RESULT {json}` with `output`, `size`, `bootgen_rc`, `succeeded`.

### 6. Validate and report

**After CREATE (BIF only, e.g. `--bif-only`):** report the saved BIF path (`BIF_RESULT`) and the
resolved/missing components, and offer to build ("Run Bootgen to build the image now?") — do not
claim an image exists.
**After BUILD:** trust `BOOT_RESULT.succeeded` (Bootgen returned 0 and a non-trivial image was
written). Report the image path; on failure, show the Bootgen error.

## Boot-image basics by family

| Family | Image | BIF root | Minimal non-secure contents |
|--------|-------|----------|------------------------------|
| ZynqMP | `BOOT.BIN` | `the_ROM_image:` | `[bootloader] fsbl.elf` (+ `[destination_cpu=pmu] pmufw.elf`, apps, `[destination_device=pl] .bit`) |
| Zynq | `BOOT.BIN` | `the_ROM_image:` | `[bootloader] fsbl.elf` (+ bitstream, app) |
| Versal / VersalNet / 2VE-2VM / SpartanUP | `.pdi` | `new_bif:` | PLM bootloader + PMC data + subsystem CDOs (see canonical-bifs.md Ex.1/3/5) |

See [boot-image/platform-rules.md](boot-image/platform-rules.md) +
[boot-image/canonical-bifs.md](boot-image/canonical-bifs.md) for the full BIF
tables/examples (all platforms × security modes), and
[boot-image/bootgen.md](boot-image/bootgen.md) for the Bootgen CLI and image
validation.

## Edge cases / notes (all authored, then `--bif`)

Author the BIF from the references (start from the closest
[canonical example](boot-image/canonical-bifs.md)) and pass it via `--bif`; this
skill then runs Bootgen on it:

- **Secure boot** (RSA/ECDSA/AES/LMS/HSS, red/black/grey keys, HSM): canonical-bifs.md
  Ex.2 (Versal RSA+AES), Ex.10 (ZynqMP RSA), Ex.13/15 (HSM), Ex.24 (VersalNet RSA);
  per-platform allowlists in platform-rules.md.
- **Versal-family PDIs**: PLM + PMC data + subsystem CDOs — Ex.1 (Versal), Ex.3
  (VersalNet), Ex.5 (2VE/2VM). **Pick the APU core for the silicon** (a72 vs a78).
- **SSIT (multi-SLR)**: Ex.14 (Versal Gen1 only).
- **imagestore / DFX runtime PDI swap**: Ex.17.
- **Register-init / user boot-header**: Ex.18 (ZynqMP `[init]`/`[udf_bh]`).

**Extra bootgen CLI flags (`--bootgen-args`):** some flows need a bootgen flag
that is *not* in the BIF. Pass them verbatim via `--bootgen-args "<flags>"`:
- **Zynq-7000 AES encryption — REQUIRED:** `--bootgen-args "-encrypt efuse"` (or
  `bbram`). The Zynq key source is a CLI arg, not a BIF attribute (Ex.23). ZynqMP
  and Versal put the key source in the BIF, so they don't need this.
- **Advanced (any family, optional):** `-nonbooting` (HSM staging), `-p <part>`,
  `-process_bitstream`, etc.

## Common issues

- **`bootgen` not found**: source `settings64.sh`.
- **Bootgen error on a component**: usually a missing/incorrect file path or an
  arch/auth mismatch — re-check against platform-rules.md (auth/cores/keys) and
  the matching canonical example.
- **Wrong APU core** (e.g. `a72` on VersalNet): use a78 for VersalNet/2VE/2VM,
  a72 for Versal Gen1, a53 for ZynqMP (platform-rules.md §Valid Core Values).
- **`Invalid character (0x23)`**: the BIF contains a `#` comment — bootgen only
  accepts `//` and `/* */`. The `# src:.. | ug:..` citation lines in the canonical
  examples are **documentation only**; do not put them in the BIF you run. (The
  driver auto-strips `#` lines into a sanitized copy as a safety net, but keep them
  out of authored BIFs.)
