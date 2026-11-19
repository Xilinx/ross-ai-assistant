---
name: esw-yocto-source-setup
description: Fetch and initialize a Yocto/OpenEmbedded build environment, including AMD/Xilinx layer availability and the gen-machine-conf command. Use when the user asks to get/clone Yocto sources, source edf-init-build-env, prepare a Yocto shell, select or create a build directory, check required layers, or make gen-machine-conf and bitbake available. Do not parse an SDT or generate a machine configuration; hand that work to $esw-gen-machine-conf. Do not run bitbake; hand builds to $esw-yocto-build in the same initialized shell.
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

Prepare a Yocto checkout for subsequent commands. Do not run
`gen-machine-conf parse-sdt` in this skill.

## Instructions

0. Obtain the sources when no Yocto checkout exists. Use the AMD/Xilinx `repo`
   manifest, with these stable defaults:
   - URL: `https://github.com/Xilinx/yocto-manifests`
   - Branch: use the user's release when supplied via natural language, `-b`, or
     `YOCTO_MANIFEST_BRANCH` (CLI overrides the environment). Otherwise resolve the
     manifest repository's current default branch by passing `-b HEAD` to
     `repo init`. Do not pin a release or invent a fallback. If initialization
     fails, report the error and suggest an explicit `-b <branch>`.
   - Manifest: `default-edf.xml`

   Map user-supplied repository, branch/release, and manifest overrides to `-u`,
   `-b`, and `-m`. Echo the resolved values before syncing. With no branch supplied,
   let the fetch script pass `-b HEAD` and report the resolved branch after initialization.

   Confirm the destination directory with the user, then fetch:
   ```bash
   "${CLAUDE_SKILL_DIR}/scripts/fetch_yocto_sources.sh" <dest-dir>
   # override defaults when the user asks for a different source/branch/manifest:
   "${CLAUDE_SKILL_DIR}/scripts/fetch_yocto_sources.sh" -u <url> -b <branch> -m <manifest> <dest-dir>
   ```

   **Skip the confirmation prompt when the user opts out.** If the user says "skip
   questions", "don't ask", "non-interactive", "just fetch", "use defaults", or supplies
   an explicit destination with an instruction to proceed, fetch immediately with the
   resolved values — still echo them first, but do not wait for approval. Only fall back
   to prompting when the destination is missing and cannot be derived.

   Skip this whole step when the user already has a checkout containing `edf-init-build-env`.
1. Resolve the checkout root and initializer before changing directories.
   - After fetching, use the absolute destination from Step 0 as the checkout root;
     the fetch script's `cd` does not change the caller's directory. For an existing
     checkout, use the user's supplied root. Resolve explicit relative paths from
     the caller's current directory.
   - Accept the user's selected initializer name or file path. With a known checkout,
     resolve an initializer name beneath that root; resolve an explicit relative file
     path from the caller's directory. Default to the checkout's `edf-init-build-env`
     only when no initializer is named. List available initializers when the checkout
     uses an internal manifest; do not substitute the public initializer for a named
     internal one.
   - With only an initializer file supplied, identify the checkout root from that file
     and the checkout layout. If the root cannot be established, ask for it before
     choosing a default build directory. With no checkout or initializer supplied,
     check the current directory, then ask for the path if missing. Do not scan broad
     shared filesystems or select a moving release symlink without authorization.
   - Pass an absolute initializer file path to the helper, not a checkout directory.
     Verify it exists and report the selected checkout and initializer.
2. Resolve the build directory before sourcing. Preserve an absolute supplied path;
   resolve an explicit relative path from the caller's current directory. Otherwise
   use `<absolute-checkout-root>/build`. Report the resolved absolute build directory.
3. Initialize **in the same shell that will run later Yocto commands**:
   ```bash
   source "${CLAUDE_SKILL_DIR}/scripts/setup_yocto_env.sh" "<absolute-initializer-path>" "<absolute-build-dir>"
   ```
   The helper sources the initializer itself; do not source it separately first.
   Executing the helper in a child shell does not update the caller. The fetch script's
   completion command uses the default initializer and build directory; adjust it
   when the user selects different paths.
   **Do not export `XILINX_VITIS`/`XILINX_VIVADO` or source Vitis/Vivado
   `settings64.sh` in this shell.** Their host-tool environment can conflict with
   Yocto/OE. Run Vitis-dependent work, such as `sdtgen` via `$esw-xsa-to-sdt`, in a
   separate shell.
4. Check the helper's exit status and its reported `BUILDDIR`, `bitbake`, and
   `gen-machine-conf` paths before continuing. On failure, follow
   `references/troubleshooting.md`. A missing command is a checkout or environment
   problem; verify the selected initializer and command location before proposing
   changes to layers.
5. **Check the effective Yocto `TMPDIR` after initialization and before layer queries,
   machine generation or builds.** Query it with `bitbake-getvar --value TMPDIR` when available;
   use the installed release's equivalent configuration query otherwise. Do not assume
   it is `$BUILDDIR/tmp` or use the shell's `TMPDIR` as proof of the BitBake value.
   Check the resolved path's filesystem with `findmnt -T <path>`; if the directory does
   not exist, check its nearest existing parent. An NFS source/build workspace is usable
   when the effective `TMPDIR` is on suitable local storage. Preserve an existing valid
   setting. If `TMPDIR` is on `nfs`/`nfs4`, resolve a writable local location before
   proceeding; check available space and obtain the user's path/configuration choice
   unless already authorized. Do not automatically choose `/tmp` or disable the Yocto
   sanity checker. Read
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#tmpdir-on-nfs` for configuration
   and recovery details. If the query itself fails, report that failure and inspect the
   active configuration rather than claiming the default path is the evaluated value.
6. **Check host OS compatibility against the initialized Yocto release.** Read
   `/etc/os-release` to report the host distribution and version. Query
   `NATIVELSBSTRING` and `SANITY_TESTED_DISTROS` with `bitbake-getvar --value <variable>`
   when available, or the release's equivalent configuration query. Run `bitbake -p`
   in the initialized shell to parse metadata and invoke the release's sanity checks
   without executing build tasks. Preserve and inspect its output and exit status.
   Report any host-distribution validation warning; do not treat an unlisted host as
   a fatal error unless the release's checks require stopping. A fatal sanity or parse
   error means setup validation is incomplete: report the first error and resolve it
   before handing off. Do not hardcode supported Ubuntu versions, suppress sanity
   checks, or claim compatibility when the checks could not complete. Yocto validation
   is separate from AMD EDF's official host support; consult the selected EDF release's
   documentation when official support is required. Read
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#host-os-compatibility` for details.
7. Inspect `BBLAYERS` via `bitbake-layers show-layers` when available. Report whether
   `meta-xilinx-core`, `meta-xilinx-standalone`, and `meta-xilinx-standalone-sdt`
   are present by checking the layer paths (displayed names can be `xilinx`,
   `xilinx-standalone`, and `xilinx-standalone-sdt`). These are sublayers of
   `sources/meta-xilinx`; the repository itself is not a layer. Do not mutate
   `bblayers.conf` unless explicitly requested.
8. Record the resolved initializer, build directory, Yocto release (when discoverable),
   path to `gen-machine-conf`, the evaluated `TMPDIR` with its filesystem, host OS,
   and the host compatibility check result (including any warning or incomplete check).
9. Hand off SDT parsing to `$esw-gen-machine-conf` in the same initialized shell.
10. Hand off BitBake builds to `$esw-yocto-build` in that same shell. The environment does not
   survive into a new shell or a later tool call, so the build must run where the initializer
   was sourced. This applies whether the user asked to build immediately after setup or after
   generating a machine.

Read `${CLAUDE_SKILL_DIR}/references/troubleshooting.md` when setup or command
discovery fails.

## Guardrails

- Never combine the Vitis/Vivado environment with the Yocto build environment in one
  shell or command — do not export `XILINX_VITIS`/`XILINX_VIVADO` or source `settings64.sh`
  in the shell where `edf-init-build-env` is sourced. Use separate shells.
- Default source fetches to `https://github.com/Xilinx/yocto-manifests` and manifest
  `default-edf.xml`; use the explicit branch or discover the remote default, and fail
  clearly with guidance to pass `-b` if discovery fails. Confirm the destination directory before
  syncing unless the user opts out ("skip questions", "non-interactive", "just fetch").
- Do not download layers or alter an existing build configuration without approval.
- Do not claim environment persistence across separate tool calls or terminal sessions.
- Use configuration queries for the TMPDIR preflight and parse-only BitBake for host
  sanity checks; do not execute build tasks merely to validate shell setup.
- Treat shared `daily_latest` paths as non-reproducible and report their canonical target.
