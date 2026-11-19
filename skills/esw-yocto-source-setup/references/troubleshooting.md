<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Troubleshooting

- `edf-init-build-env` missing: request the exact Yocto checkout or initializer path. Do
  not substitute a Vitis `settings64.sh`; these initialize different toolchains.
- Environment disappears: source the initializer and run dependent commands in the same
  shell. A standalone script cannot modify its parent shell.
- `bitbake` missing: the initializer failed or the checkout is incomplete.
- `gen-machine-conf` missing: confirm the selected release's environment initializer
  was sourced successfully in the current shell. A correctly initialized EDF
  environment adds `gen-machine-conf` to `PATH`. Check with
  `command -v gen-machine-conf`; if it is still missing, inspect the initializer's
  output and verify the checkout is complete and matches the selected release.
  Do not infer that an additional layer is required.
- Layer mismatch: use branches aligned to the selected Yocto/AMD release. Avoid mixing a
  moving daily toolchain with pinned layers when reproducibility matters.
- Existing build directory: preserve its `conf/local.conf` and `conf/bblayers.conf`; do not
  replace them merely to repair command discovery.
- Background fetch dies silently: a `nohup ... &` job started from a shell that then exits can
  be killed with its process group, leaving an empty destination and a truncated log. Launch the
  sync detached and stdin-free (`setsid <script> ... > <log> 2>&1 < /dev/null &`), then poll the
  log.
- `repo` not on PATH: the script does not download it; it reports an error and exits. Install
  the Android `repo` tool (e.g. `apt-get install repo`) before running.
- Environment `python3` without PyYAML: expected on some hosts. Do not repair it here; report it
  so `$esw-gen-machine-conf` can run under `GEN_MACHINE_CONF_PYTHON=/usr/bin/python3`.

## TMPDIR on NFS

OE's sanity checker rejects an NFS-backed Yocto `TMPDIR`. `gen-machine-conf` may invoke
BitBake to prepare `kconfig-frontends-native` and `esw-conf-native`, so this can prevent
machine generation even when no image build was requested. A missing native sysroot or
`mconf` after the NFS sanity error can be a consequence of that blocked preparation;
address the first error before diagnosing a separate dependency failure.

Use the preflight in `SKILL.md` to check the effective `TMPDIR`, including any existing
custom setting, and the filesystem containing it. The sources, build configuration and
SDT can remain on NFS; only relocate the rejected work directory. Keep the sanity checker
enabled.

When relocation is needed:

- Offer a writable local scratch directory with enough capacity. `/tmp` is an option only
  after checking its filesystem, capacity and cleanup policy; persistent local scratch is
  preferable for build state that must survive cleanup or reboot. Use a path unique to the
  user and build to avoid sharing one work directory across independent build trees.
- For a persistent setting, obtain authorization to update the existing build configuration
  unless the user already supplied it. Set the selected path in `$BUILDDIR/conf/local.conf`,
  for example `TMPDIR = "/local-scratch/<user>/<build>/tmp"`. Preserve unrelated settings
  and account for existing assignments rather than appending conflicting values blindly.
- A session-only alternative must use the release's supported BitBake environment
  passthrough/configuration mechanism. A shell `export TMPDIR=...` alone does not prove
  BitBake uses that path; existing metadata assignments can override it. Verify the evaluated
  value and explain that later shells need the same configuration.
- Changing `TMPDIR` selects a different work tree and can require native tools to be rebuilt.
  Preserve the old tree; do not delete or copy generated build state as part of this fix.
  Re-query the effective value and check the selected filesystem and free space before retrying.

Report the effective `TMPDIR`, filesystem, and any authorized configuration change before
handoff. If recovering a failed generation, retry with the previously approved SDT, template,
machine name, overrides and PL options and report the log, exit status and generated paths.
Native-tool preparation is part of generation; it does not authorize an image build.

## Host OS compatibility

After initialization and the TMPDIR preflight, report `/etc/os-release` and query
this checkout's host validation settings:

```bash
cat /etc/os-release
bitbake-getvar --value NATIVELSBSTRING
bitbake-getvar --value SANITY_TESTED_DISTROS
bitbake -p
```

If `bitbake-getvar` is unavailable, use the release's configuration query (for
example `bitbake -e` and inspect the assignments). `bitbake -p` parses metadata
without executing tasks; it may create caches and takes longer than command
availability checks. Retain its warnings and exit status. A zero exit status does
not erase an unvalidated-host warning or establish official AMD support.

`SANITY_TESTED_DISTROS` belongs to the selected release/configuration; do not use
an allowlist copied from another release. An empty list does not establish that
all hosts are supported. Buildtools or uninative may affect the reported native
host identity and validation behavior. For example, `NATIVELSBSTRING` can be
`universal` with uninative; it is not the host identifier to match against the
list. This checkout's sanity checker obtains that identifier through
`oe.lsb.distro_identifier()` (using `/etc/os-release` first). Rely on the release's
sanity logic rather than matching `NATIVELSBSTRING` against the list. If the list
is unset or empty, explicitly report that distribution validation is not configured.

An unlisted host typically produces a warning that the distribution has not been
validated. Report it and explain the compatibility risk; stop when the release
reports a fatal error. If parsing fails for another reason, such as an invalid
machine, do not claim that host validation completed. Keep sanity checks enabled
and check the selected AMD EDF release documentation for official host support.
