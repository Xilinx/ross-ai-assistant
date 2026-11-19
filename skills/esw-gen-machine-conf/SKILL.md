---
name: esw-gen-machine-conf
description: Generate and validate a Yocto machine configuration from an existing System Device Tree using gen-machine-conf parse-sdt, including machine naming, templates, output/config locations, Kconfig controls, PL overlays, domains, multiconfig, and local.conf updates. The SDT may be an unpacked directory, a local archive, or a URL, which is passed to --hw-description verbatim. Use when the user asks to run gen-machine-conf, convert an SDT directory, SDT tarball, or SDT download URL into Yocto machine metadata, inspect its global or parse-sdt options, customize generation, or validate generated output. Requires an already sourced Yocto environment; use $esw-yocto-source-setup when it is not active.
metadata:
  version: "1.0.0"
  stage: beta
allowed-tools: Read, Bash, Write
license: MIT
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

Consume an existing SDT and run `gen-machine-conf`. Do not create or source a Yocto
environment in this skill.

## Instructions

1. Resolve the SDT source from the user. It may be an unpacked SDT directory, a local
   archive (`.tar.gz`, `.tar.xz`, `.zip`, ...), or a URL. If absent, ask for it; do not
   search broadly.
   **Pass archives and URLs to `--hw-description` verbatim. Never download or extract them
   first.** `gen-machine-conf` routes any input containing `://`, and any local archive,
   through the BitBake fetcher, unpacks it under `${BUILDDIR}/.hw-description`, and records
   the original URI plus its checksum as `SDT_URI`/`SDT_URI[sha256sum]` in the generated
   machine conf. Hand-extracting produces a `file://` `SDT_URI` pointing at a local scratch
   path, which is not reproducible for anyone else building the machine.
   Append `;S=<subdir>` to the archive/URL when it holds more than one SDT directory.
2. Validate the input for its form:
   - Directory: confirm `system-top.dts` is nonempty. Require nonempty `pcw.dtsi`
     and `pl.dtsi` when referenced by its `#include` directives. PL-only SDTs normally
     omit `pcw.dtsi`; its absence is informational when it is not referenced.
   - Archive or URL: do not pre-extract. At most confirm a URL is reachable with a HEAD
     request; the tool reports fetch failures clearly on its own.
   - `.xsa` in any form: the tool rejects it. Route to `$esw-xsa-to-sdt` to produce an SDT first.
3. Confirm the current shell is initialized:
   ```bash
   test -n "$BUILDDIR"
   command -v gen-machine-conf
   ```
   If either check fails, stop and invoke or recommend `$esw-yocto-source-setup`. The setup and
   parse commands must run in the same shell/session.
   **The shell must be a clean Yocto environment — `XILINX_VITIS`/`XILINX_VIVADO` must not
   be set and the Vitis/Vivado `settings64.sh` must not have been sourced here.** Those
   settings clash with the Yocto/OE `PATH`, `LD_LIBRARY_PATH`, and Python and break
   `gen-machine-conf`. If `XILINX_VITIS` is set, stop and ask the user to re-run in a fresh
   shell that only sources the Yocto build environment.
   Before metadata queries or native-tool preparation, query the effective `TMPDIR`
   with `bitbake-getvar --value TMPDIR` (or the release's equivalent), then check
   `findmnt -T <path>` on it or its nearest existing parent. Do not infer it from
   the shell's `TMPDIR`. Preserve valid local storage and keep sanity checks enabled.
   On query failure or `nfs`/`nfs4`, stop and follow this skill's recovery steps in
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#tmpdir-on-nfs`.
4. Discover both levels of the installed interface. Tool releases differ; do not rely on a
   remembered option list:
   ```bash
   gen-machine-conf --help
   gen-machine-conf parse-sdt --help
   ```
   The first command shows global options and the second shows subcommand options. If the
   command's shebang interpreter lacks PyYAML, perform step 7 first and invoke both help
   commands with the selected interpreter.
5. Resolve a machine YAML template from the active layers when the user did not supply
   `--template`:
   - **Defer this question until the SDT exists.** When the SDT is still being produced
     upstream (`$esw-xsa-to-sdt`), do not ask about the template in the same message as that
     skill's board-DTS question. The two are separate gates: the board DTS changes the SDT,
     the template consumes it, and the SDT's actual domains and PL content are what make a
     template relevant or pointless. Bundling them also makes the reply ambiguous — an answer
     like "no template" resolves this question while leaving the board DTS unanswered.
   - Get the configured layer paths from `bitbake-layers show-layers`; search only their
     `conf/machineyaml/*.yaml` files for names matching the target board/device.
   - Inspect each matching YAML's `args`, `inherit`, `domain-file`, and
     `machine.description` to explain its software-domain scope. Do not classify
     `*-multidomain.yaml` as non-segmented: both `*-sdt-seg.yaml` and
     `*-multidomain.yaml` may be Segmented Configuration templates; the usual distinction
     is Linux-only versus multiple domains.
   - If exactly one compatible machine YAML is found, do not apply it automatically. Present it
     with its concrete deltas and ask the user whether to use it or build without a template.
     Proceed only after the user confirms.
   - If multiple compatible machine YAML files are found and the user did not identify one,
     stop before generation, list their exact paths and concise descriptions, and ask which
     machine YAML to use. Never choose among multiple matches from the filename, XSA, board
     DTS, or apparent domain count.
   - If no compatible machine YAML is found, continue without `--template` unless the user
     explicitly required a template.
   - Present the concrete deltas a template introduces - its `--domain-file` list, `-g` value,
     Kconfig entries, and `post` settings - not just its name, so the user can evaluate the
     choice. Offer "no template" as a real option: it yields a machine built purely from the SDT.
   - Know the merge semantics before recommending one. Values under the YAML `args` key become
     argparse **defaults**, so any option passed on the command line overrides the template.
     `inherit` merges the parent YAML with the inherited file taking priority.
   - **AMD BSP machine YAMLs commonly hard-code `--hw-description <url-to-released-sdt.tar.gz>`.**
     Using such a template without an explicit `--hw-description` silently builds the machine from
     AMD's released SDT instead of the user's hardware. Always pass the local SDT directory with
     `--template`, then confirm the generated conf's `SDT_URI` and `PDI_PATH` reference the user's
     artifacts.
   - A template usually supplies `--machine-name` too. When that name matches a machine already
     shipped by a layer, the tool warns about the conflict; the copy in `${BUILDDIR}/conf/machine`
     wins because the build conf directory precedes layers in `BBPATH`. Report the shadowing and
     offer a distinct name rather than leaving it ambiguous.
   - **A distinct `--machine-name` silently drops the BSP's machine-gated appends.** AMD BSP
     recipes enable board targets through override-keyed compatibility, for example
     `COMPATIBLE_MACHINE:versal-vrk160-revb-multidomain = "${MACHINE}"` in an `edf-ospi`,
     `edf-qspi`, `image-selector`, `imgrcvry`, or `uefi-capsule` bbappend. A machine named
     anything else matches none of them, and the failure surfaces only at build time as
     `ERROR: Nothing PROVIDES '<target>'` — not during generation.
     Keep both properties by adding the BSP machine as an override instead of renaming to it:
     ```bash
     gen-machine-conf ... --machine-name <custom-name> \
       --machine-overrides <bsp-machine-name> --force ...
     ```
     Verify `MACHINEOVERRIDES` in the generated conf lists the BSP name, then prove the target
     resolves with `MACHINE=<custom-name> bitbake -n <target>` before starting a real build.
     Present the trade-off explicitly: reuse the BSP name and shadow its conf, or keep a
     distinct name plus the override and leave the layer's machine untouched.
6. Select only user-relevant options that the installed help reports:
   - **Resolve segmented PL overlay mode before generation when no template is selected.**
     Inspect the supplied SDT directory for `*_pld.pdi` files, alongside its other PDI,
     BIF, and PL device-tree artifacts. If a `*_pld.pdi` is present and the user has not
     already explicitly selected `full` or `dfx`, stop and ask which PL overlay mode to use.
     This check is required even when the prompt says "leave optional PL overlay settings
     unset", "use defaults", or "skip questions"; those phrases do not resolve the
     segmented PL loading requirement. Show the discovered PDI paths and explain that this
     workflow must resolve how separately loaded PL devices are described instead of silently
     leaving them in the base DTS. Offer `full` for full PL configuration and `dfx` for a
     design with dynamic function exchange; allow the user to clarify the loading workflow
     if neither applies. Do not select a mode automatically or infer DFX from multiple PDIs.
     Wait for the answer before running `gen-machine-conf`, then pass the confirmed
     `--gen-pl-overlay full` or `--gen-pl-overlay dfx`. Resolve the intended PL payload
     separately if `--pl` is needed; never substitute the boot PDI for the PLD PDI.
     This directory inspection does not authorize downloading or extracting archive/URL
     inputs; retain the input-handling rules in steps 1–2 for those inputs.
   - Global controls commonly include `--template`, `--machine-name`, `--config-dir`,
     `--require-machine`, `--machine-overrides`, `--output`, `--native-sysroot`,
     `--menuconfig`, `--add-config`, `--debug`, and `--force`.
   - `parse-sdt` controls commonly include `--gen-pl-overlay`, `--domain-file`,
     `--psu-init-path`, `--pl`, `--localconf`, `--multiconfigfull`, and `--dts-path`.
   - **Never default `--gen-pl-overlay` / `-g` (e.g. `-g full`).** Only pass it when the
     user explicitly asks for or selects a PL device-tree overlay. If the segmented PL
     check above applies, obtain that selection first; otherwise omit it when unspecified.
     The same applies to `--pl`/`--multiconfigfull` — do not add them
     on the user's behalf.
   - **Never default `--output` or `--config-dir`.** Only pass either when the user explicitly
     specifies the corresponding directory; otherwise omit them and let the tool auto-set up its
     own output and config locations.
   - Let the tool auto-detect SoC family and variant. Do not ask about or pass
     `--soc-family` or `--soc-variant` unless the user explicitly requests an override to
     correct failed detection.
   - Validate supplied template, domain, PL/PDI, init, config, and sysroot paths before use.
     Do not infer a domain file, custom template, required base machine, machine overrides,
     or Kconfig settings.
   - Treat `--localconf` as a write to that file, `--menuconfig` as interactive, and
     `--force` as replacement of cached/generated output. Obtain approval when the user's
     request did not already authorize the corresponding mutation.
   - Apply Kconfig overrides non-interactively with `--add-config CONFIG_<macro>=y`; they take
     priority over the template's `kconfig` block. Read the generated `configs/Kconfig.*` to
     confirm the exact symbol before using it, and note that symbols in a `choice` deselect
     their siblings automatically, so one macro is enough.
     **Always regenerate after any Kconfig selection or generated `conf/config` change.** Pass
     the intended symbol through `--add-config` and pair it with `--force`; change detection
     hashes the hardware description, so an unchanged SDT otherwise makes the tool skip
     regeneration and silently leave previous output in place. Never directly edit
     `conf/config`, generated machine `.conf` files, generated domain DTS files, or other
     generated output and report the change as complete. If direct edits were made while
     diagnosing, treat them as temporary only: rerun `gen-machine-conf --force` with the
     corresponding `--add-config` override so the generator replaces them, then validate the
     regenerated values.
7. **Choose the Python interpreter** — `gen-machine-conf` needs the `PyYAML` (`yaml`)
   module. Probe the environment interpreter and fall back only if it is missing it:
   ```bash
   python3 -c 'import yaml'            # environment python3
   /usr/bin/python3 -c 'import yaml'   # fallback, only if the above fails
   ```
   - If the environment `python3` imports `yaml`, use it (leave `GEN_MACHINE_CONF_PYTHON` unset).
   - If it fails but `/usr/bin/python3` imports `yaml`, export
     `GEN_MACHINE_CONF_PYTHON=/usr/bin/python3` before running the wrapper.
   - If neither imports `yaml`, stop and report the missing dependency. Never install
     packages or create a virtual environment to satisfy it.
8. Run the tool. The checked wrapper accepts **only an SDT directory**; put global options
   before `--` and `parse-sdt` options after it, and it places them on the correct sides of
   the subcommand:
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_gen_machine_conf.sh <sdt-dir> [global options...] -- [parse-sdt options...]
   ```
   Example (the `parse-sdt` overlay options below are illustrative — include them only when
   the user asks for a PL overlay):
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_gen_machine_conf.sh ./sdt \
     --machine-name my-machine --force -- \
     --gen-pl-overlay full --pl ./sdt/design.pdi --multiconfigfull
   ```
   For backward compatibility, arguments supplied without `--` are treated as `parse-sdt`
   options. Do not use that form for global options.
   The wrapper honors `GEN_MACHINE_CONF_PYTHON` when set; otherwise it runs
   `gen-machine-conf` with its own interpreter.
   For an archive or URL the wrapper's directory check rejects the input, so bypass it and
   invoke the tool directly with the same argument order, using the interpreter chosen in
   step 7:
   ```bash
   gen-machine-conf --hw-description <archive-or-url> [global options...] parse-sdt [options...]
   ```
   Report that the wrapper was bypassed and why.
9. Capture the command, tool path/version output, and generated paths. Do not overwrite an
   existing machine configuration unless the user approves or the installed command offers
   a clearly safe update mode selected by the user.
10. Validate that the output reported by the tool exists and inspect generated `.conf`,
    `.inc`, and device-tree artifacts for empty files and unresolved template markers.
    For a Kconfig/config customization, verify both the selected symbol in `conf/config` and
    every affected emitted value in the machine `.conf` and final domain DTS. The successful
    generator exit and regenerated artifacts—not a manual edit—are the completion criterion.
   With `--template`, additionally prove the template applied to the intended hardware: check
   `SDT_URI` and `PDI_PATH` (must point at the user's SDT/PDI), `DEFAULT_HW_BOOT_MODE` (reflects
   the template's Kconfig boot mode), and the `post` settings the YAML declared. When the input
   was a URL, confirm `SDT_URI` is that exact URL and that `SDT_URI[sha256sum]` matches the
   server's checksum - that proves the template's own hard-coded `--hw-description` was
   overridden rather than silently used. A
   `conf/dts/<machine>/*-linux.dts` that differs from an untemplated run confirms the domain
   files took effect.
   **After any peripheral or console Kconfig override, verify the target node in the final
   `conf/dts/<machine>/*-linux.dts`, not just in `config` and the machine conf.** A template's
   `CONFIG_YOCTO_BBMC_*_DTSI` custom DTSI is applied last and can `/delete-property/` an alias
   or set `status = "disabled"` on the very node just selected, because BSPs reserve
   peripherals for other domains. Confirm the node is `status = "okay"` and its alias still
   exists. A `chosen` node that lost `stdout-path` while keeping `bootargs` is the signature of
   this conflict: `bootargs` is a plain string and survives, whereas `stdout-path` resolves
   through `aliases` and is pruned once the alias is deleted. Report the conflict instead of
   treating matching `SERIAL_CONSOLES`/`config` values as proof the change works.

Read `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` when parsing fails.

## Handoff

After success, report the exact `MACHINE` value and the configuration/layer path that must
be present in `BBLAYERS`. Do not start a BitBake build unless the user separately requests it;
when they do, hand off to `$esw-yocto-build` in the same initialized shell rather than invoking
`bitbake` here. Note that the generated `MACHINE` is a board machine, so it builds boot
artifacts; an EDF rootfs image is a separate build against a common machine.
