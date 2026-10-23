#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Create a Vitis boot image (BOOT.BIN / PDI) with Bootgen, from CLI arguments.

The flow is always: author/obtain a BIF -> run Bootgen -> validate. Pass --bif-only
to stop after authoring the BIF (the CREATE step; running Bootgen is the BUILD step).
Two ways to supply the BIF:
  Authored (normal)  --bif <path>   use a BIF authored from
                         references/canonical-bifs.md + platform-rules.md — works
                         for ANY case (non-secure, secure AES/RSA/HSM, Versal PDI,
                         DFX, ...). This is the general way.
  Auto shortcut      simple mode    let the driver assemble a ZynqMP/Zynq BIF from
                         components (convenience). Non-secure by default; optional
                         RSA auth (--authenticate rsa --psk --ssk) and/or AES
                         encryption (--encrypt aes --aes-key [--key-src]) inject the
                         security attributes on every partition (models Ex.10/12/19).
                         Baremetal: --bootloader (+ --pmufw --bitstream --app)
                         Linux:     add --atf/--bl31 and --u-boot (ZynqMP).
                         Versal-family / Zynq-7000 AES / advanced secure: author the BIF (--bif).

Then runs:  bootgen -arch <arch> -image <bif> -o <output> -w on -log error
and validates the produced boot image.

Run with the Vitis launcher's environment sourced (bootgen on PATH), e.g.:
    python3 create_boot_image.py --arch zynqmp --bootloader fsbl.elf \
        --app hello.elf --output BOOT.BIN
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

# Default id_code per Versal-family arch (see references/platform-rules.md).
_ID_CODE = {
    "versal": "0x14CA8093",
    "versalnet": "0x14D80093",
    "versal_2ve_2vm": "0x04DB0093",
}
_VERSAL_FAMILY = {"versal", "versalnet", "versal_2ve_2vm", "spartanup"}
_BRACKET = {"zynqmp", "zynq"}


def default_output(arch):
    return "BOOT.BIN" if arch in _BRACKET else "design.pdi"


def simple_bif_bracket(arch, bootloader, pmufw, apps, bitstream, app_cpu, atf, u_boot,
                       auth=None, psk=None, ssk=None, ppk_select=0,
                       encrypt=None, aes_key=None, key_src="efuse_red_key"):
    """ZynqMP/Zynq BIF (the_ROM_image bracket format), non-secure or secure.

    Supports baremetal (fsbl + app) and Linux (fsbl + pmufw + bitstream + ATF/bl31 +
    u-boot). Optional RSA authentication and/or AES encryption inject the security
    attributes onto every partition, modeled on canonical Examples 10 (ZynqMP RSA),
    12 (Zynq RSA) and 19 (ZynqMP RSA+AES keysrc). Non-secure when auth/encrypt are None.
    """
    cpu = app_cpu or "a53-0"

    def _key_for(tag):
        """Per-partition AES key file. ZynqMP uses AES-256-GCM, where each partition
        MUST have a unique key+IV — reusing one .nky across partitions is a GCM
        nonce-reuse vulnerability that bootgen rejects. So derive a distinct .nky per
        partition from the --aes-key base (bootgen generates each missing one).
        Zynq-7000 uses a single device key via `-encrypt efuse`/`bbram`, so keep the
        one file."""
        if not aes_key or arch == "zynq":
            return aes_key
        base, ext = os.path.splitext(aes_key)
        return f"{base}_{tag}{ext or '.nky'}"

    def _sec(attrs, tag):
        """Append the per-partition security attributes (order: dest, auth, encrypt).
        tag names the partition so AES gets a unique per-partition key file."""
        if auth == "rsa":
            attrs.append("authentication=rsa")
        if encrypt == "aes":
            attrs.append("encryption=aes")
            key = _key_for(tag)
            if key:
                attrs.append(f"aeskeyfile={key}")
        return attrs

    lines = ["the_ROM_image:", "{"]
    # Top-level secure blocks (order matches the canonical secure examples).
    if auth == "rsa" and arch == "zynqmp":
        lines.append("  [fsbl_config] bh_auth_enable")
        lines.append(f"  [auth_params] ppk_select={ppk_select}")
    if encrypt == "aes":
        lines.append(f"  [keysrc_encryption] {key_src}")
    if auth == "rsa":
        lines.append(f"  [pskfile] {psk}")
        lines.append(f"  [sskfile] {ssk}")
    bl = ["bootloader"]
    if arch == "zynqmp":
        bl.append(f"destination_cpu={cpu}")
    lines.append(f"  [{', '.join(_sec(bl, 'bootloader'))}] {bootloader}")
    if pmufw and arch == "zynqmp":
        lines.append(f"  [{', '.join(_sec(['destination_cpu=pmu'], 'pmufw'))}] {pmufw}")
    if bitstream:
        if arch == "zynqmp":
            lines.append(f"  [{', '.join(_sec(['destination_device=pl'], 'pl'))}] {bitstream}")
        elif auth or encrypt:  # zynq secure: attrs only, no ZynqMP-only destination_device
            lines.append(f"  [{', '.join(_sec([], 'pl'))}] {bitstream}")
        else:  # zynq non-secure: plain .bit line
            lines.append(f"  {bitstream}")
    if atf:  # ARM Trusted Firmware (bl31) — EL3 secure monitor (aarch64/ZynqMP only)
        lines.append(f"  [{', '.join(_sec([f'destination_cpu={cpu}', 'exception_level=el-3', 'trustzone'], 'atf'))}] {atf}")
    if u_boot:  # u-boot: EL2 on ZynqMP; a plain (optionally secured) partition on Zynq
        if arch == "zynqmp":
            lines.append(f"  [{', '.join(_sec([f'destination_cpu={cpu}', 'exception_level=el-2'], 'uboot'))}] {u_boot}")
        elif auth or encrypt:  # zynq secure: attrs but no ZynqMP-only fields
            lines.append(f"  [{', '.join(_sec([], 'uboot'))}] {u_boot}")
        else:  # zynq non-secure
            lines.append(f"  {u_boot}")
    for i, app in enumerate(apps):
        if arch == "zynqmp":
            lines.append(f"  [{', '.join(_sec([f'destination_cpu={cpu}'], f'app{i}'))}] {app}")
        elif auth or encrypt:  # zynq secure: attrs but no destination_cpu
            lines.append(f"  [{', '.join(_sec([], f'app{i}'))}] {app}")
        else:  # zynq non-secure
            lines.append(f"  {app}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def simple_bif_versal(arch, bootloader):
    """Minimal best-effort non-secure Versal-family BIF — bootloader (PLM) only.

    Truly bootloader-only: application partitions are NOT emitted here because the
    APU core name is arch-specific (a72-0 on Versal Gen1, a78-0 on VersalNet/2VE-2VM)
    and a full boot PDI usually needs pmc_data.cdo + subsystem CDOs. For anything
    beyond the bootloader, author the BIF per references/canonical-bifs.md (Ex.1/3/5)
    and pass it via --bif (enforced in parse_args).
    """
    id_code = _ID_CODE.get(arch)
    if id_code is None:
        raise SystemExit(
            f"error: no built-in id_code for arch '{arch}' (e.g. SpartanUP); author a BIF "
            f"with the correct id_code per references/canonical-bifs.md and pass it via --bif")
    lines = ["new_bif:", "{", f"    id_code = {id_code}",
             "    extended_id_code = 0x01", "    id = 0x2",
             "    image", "    {", "        name = pmc_subsys, id = 0x1c000001",
             f"        {{ type = bootloader, file = {bootloader} }}",
             "    }", "}"]
    return "\n".join(lines) + "\n"


def sanitize_bif(bif_path, out_dir):
    """Strip '#' comments, which Bootgen rejects (Invalid character 0x23).

    Valid BIF comments are '//' and '/* */'. Some authored/canonical BIFs carry
    '# src:.. | ug:..' citation comments (documentation only) — those break
    bootgen on EVERY arch. Write a sanitized copy and run that, leaving the
    caller's original BIF untouched. Returns (path_to_run, n_lines_stripped).
    """
    with open(bif_path) as fh:
        text = fh.read()
    if "#" not in text:
        return bif_path, 0
    cleaned, stripped = [], 0
    for line in text.splitlines():
        if "#" in line:
            before = line[:line.index("#")]
            stripped += 1
            if before.strip() == "":
                continue                       # whole line was a '#' comment
            cleaned.append(before.rstrip())    # trailing '#' comment
        else:
            cleaned.append(line)
    san_path = os.path.join(out_dir, "bootgen_input.bif")
    with open(san_path, "w") as fh:
        fh.write("\n".join(cleaned) + "\n")
    return san_path, stripped


def parse_args(argv):
    p = argparse.ArgumentParser(description="Create a boot image (BOOT.BIN/PDI) via Bootgen")
    p.add_argument("--arch", required=True,
                   choices=["versal", "versalnet", "versal_2ve_2vm", "spartanup",
                            "zynqmp", "zynq"])
    p.add_argument("--bif", default=None, help="Existing BIF to use (skips simple mode)")
    p.add_argument("--bif-only", action="store_true", dest="bif_only",
                   help="CREATE step only: author/resolve the BIF and STOP, without running "
                        "Bootgen. Emits BIF_RESULT json. Re-run without --bif-only (or pass "
                        "--bif <that file>) to BUILD the image.")
    p.add_argument("--output", default=None, help="Output image (default BOOT.BIN / design.pdi)")
    p.add_argument("--output-dir", default=None, help="Directory for the BIF/output (default: cwd)")
    p.add_argument("--bootgen-args", dest="bootgen_args", default=None,
                   help="Extra tokens appended verbatim to the bootgen command "
                        "(any arch). REQUIRED for Zynq-7000 AES: --bootgen-args "
                        "\"-encrypt efuse\". Also for advanced flows: -nonbooting, "
                        "-p <part>, -process_bitstream, etc.")
    # Simple-mode components (used when --bif is not given).
    p.add_argument("--bootloader", default=None, help="FSBL (ZynqMP/Zynq) or PLM (Versal) ELF")
    p.add_argument("--pmufw", default=None, help="PMU firmware ELF (ZynqMP)")
    p.add_argument("--app", action="append", default=[], help="Application ELF (repeatable)")
    p.add_argument("--bitstream", default=None, help="Bitstream for PL (ZynqMP/Zynq)")
    p.add_argument("--app-cpu", default=None, help="destination_cpu for apps (ZynqMP, default a53-0)")
    # Linux boot components (ZynqMP/Zynq).
    p.add_argument("--atf", "--bl31", dest="atf", default=None,
                   help="ARM Trusted Firmware bl31.elf (Linux boot, EL3)")
    p.add_argument("--u-boot", dest="u_boot", default=None,
                   help="u-boot.elf (Linux boot, EL2)")
    p.add_argument("--dtb", default=None,
                   help="Device tree blob. NOTE: for ZynqMP/Zynq the DTB normally "
                        "lives on the SD card (not in BOOT.BIN); accepted for "
                        "record/Versal use. For secure/complex layouts author the BIF (--bif).")
    # Secure-boot convenience (simple mode, ZynqMP/Zynq only; models canonical Ex.10/12/19).
    # For Versal-family or advanced secure layouts, author the BIF and pass --bif.
    sec = p.add_argument_group("secure boot (simple mode, ZynqMP/Zynq)")
    sec.add_argument("--authenticate", default=None, dest="auth", choices=["rsa"],
                     help="RSA-authenticate every partition (needs --psk and --ssk)")
    sec.add_argument("--psk", default=None, help="Primary secret key (PEM) for RSA auth")
    sec.add_argument("--ssk", default=None, help="Secondary secret key (PEM) for RSA auth")
    sec.add_argument("--ppk-select", type=int, default=0, dest="ppk_select",
                     help="ppk_select for [auth_params] (ZynqMP RSA; default 0)")
    sec.add_argument("--encrypt", default=None, choices=["aes"],
                     help="AES-encrypt every partition (needs --aes-key)")
    sec.add_argument("--aes-key", default=None, dest="aes_key",
                     help="AES key file (.nky) for encryption")
    sec.add_argument("--key-src", default="efuse_red_key", dest="key_src",
                     choices=["efuse_red_key", "bbram_red_key", "kup_key", "efuse_gry_key",
                              "bbram_gry_key", "efuse_blk_key", "bbram_blk_key"],
                     help="[keysrc_encryption] AES key source (default efuse_red_key)")
    args = p.parse_args(argv)
    if (args.auth or args.encrypt) and args.arch not in _BRACKET:
        p.error("--authenticate/--encrypt convenience flags are ZynqMP/Zynq only; for "
                "Versal-family or advanced secure layouts author the BIF and pass --bif "
                "(see references/canonical-bifs.md).")
    if args.encrypt == "aes" and args.arch == "zynq" and not args.bif:
        p.error("simple-mode AES is not supported for Zynq-7000: Bootgen needs a single "
                "global [aeskeyfile] with keysrc efuse|bbram (not per-partition keys). "
                "Author the BIF per references/canonical-bifs.md (Example 23) and pass --bif.")
    if args.arch in _VERSAL_FAMILY and args.app and not args.bif:
        p.error("simple-mode Versal-family BIF is bootloader-only; application partitions "
                "need an authored BIF (APU core differs by arch: a72-0 Versal, a78-0 "
                "VersalNet/2VE-2VM). Author per references/canonical-bifs.md and pass --bif.")
    if args.atf and args.arch != "zynqmp":
        p.error("--atf/--bl31 (ARM Trusted Firmware, aarch64 EL3) applies to ZynqMP only; "
                "Zynq-7000 (Cortex-A9) has no bl31. Omit it, or author the BIF (--bif).")
    if args.auth == "rsa" and not (args.psk and args.ssk):
        p.error("--authenticate rsa needs --psk <psk.pem> and --ssk <ssk.pem>.")
    if args.encrypt == "aes" and not args.aes_key:
        p.error("--encrypt aes needs --aes-key <key.nky>.")
    if args.dtb and args.arch in _BRACKET:
        print("[warn] --dtb is typically placed on the SD card for ZynqMP/Zynq, "
              "not inside BOOT.BIN; ignoring for the BIF. Author the BIF (--bif) for "
              "a layout that embeds it.")
    return args


def main(argv):
    args = parse_args(argv)
    out_dir = args.output_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    output = args.output or default_output(args.arch)
    if not os.path.isabs(output):
        output = os.path.join(out_dir, output)

    # Resolve the BIF.
    if args.bif:
        bif_path = os.path.abspath(args.bif)
        if not os.path.isfile(bif_path):
            print(f"[error] --bif '{bif_path}' not found")
            return 2
        print(f"[info] using provided BIF: {bif_path}")
    else:
        if not args.bootloader:
            print("[error] simple mode needs --bootloader (or pass --bif). For secure/complex "
                  "BIFs, author one per references/canonical-bifs.md and pass --bif.")
            return 2
        if args.arch in _BRACKET:
            bif_text = simple_bif_bracket(args.arch, args.bootloader, args.pmufw,
                                          args.app, args.bitstream, args.app_cpu,
                                          args.atf, args.u_boot,
                                          auth=args.auth, psk=args.psk, ssk=args.ssk,
                                          ppk_select=args.ppk_select, encrypt=args.encrypt,
                                          aes_key=args.aes_key, key_src=args.key_src)
        else:
            bif_text = simple_bif_versal(args.arch, args.bootloader)
            print("[warn] simple-mode Versal BIF is bootloader-only/best-effort; for a full "
                  "boot PDI author one per references/canonical-bifs.md and pass --bif.")
        bif_path = os.path.join(out_dir, "boot.bif")
        with open(bif_path, "w") as fh:
            fh.write(bif_text)
        print(f"[info] generated BIF: {bif_path}\n{bif_text}")

    if args.bif_only:
        # CREATE step: BIF authored/resolved; do NOT run Bootgen (that is the BUILD step).
        result = {
            "arch": args.arch,
            "bif": bif_path,
            "output": output,        # where the image will be written at build time
            "built": False,
            "succeeded": True,       # the create step succeeded (BIF is ready)
        }
        print("BIF_RESULT " + json.dumps(result))
        print(f"[ok] BIF authored (image not built): {bif_path}")
        print(f"[next] build the image with: bootgen -arch {args.arch} -image {bif_path} "
              f"-o {output} -w on  (or re-run this driver without --bif-only)")
        return 0

    bootgen = shutil.which("bootgen")
    if not bootgen:
        print("[error] 'bootgen' not on PATH; source <install>/Vitis/settings64.sh")
        return 2

    # Bootgen rejects '#' comments on every arch; sanitize into a temp copy.
    run_bif, n_stripped = sanitize_bif(bif_path, out_dir)
    if n_stripped:
        print(f"[warn] stripped {n_stripped} '#' comment line(s) from the BIF "
              f"(bootgen rejects '#'; use '//'); running sanitized copy {run_bif}")

    cmd = [bootgen, "-arch", args.arch, "-image", run_bif, "-o", output, "-w", "on",
           "-log", "error"]
    if args.bootgen_args:
        cmd += shlex.split(args.bootgen_args)
    print("[info] " + " ".join(cmd))
    # stdout/stderr=PIPE + universal_newlines keep this runnable on Python 3.6
    # (capture_output=/text= are 3.7+). Prefer running via Vitis's own Python.
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    print(proc.stdout[-3000:])
    if proc.stderr:
        print(proc.stderr[-1500:])

    exists = os.path.isfile(output)
    size = os.path.getsize(output) if exists else 0
    succeeded = proc.returncode == 0 and exists and size > 1024

    result = {
        "arch": args.arch,
        "bif": bif_path,
        "output": output,
        "size": size,
        "bootgen_rc": proc.returncode,
        "succeeded": succeeded,
    }
    print("BOOT_RESULT " + json.dumps(result))
    if succeeded:
        print(f"[ok] boot image: {output} ({size} bytes)")
    else:
        print(f"[error] boot image not produced (rc={proc.returncode}, exists={exists}, "
              f"size={size}). See bootgen output above.")
    return 0 if succeeded else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
