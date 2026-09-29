<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Recent changes (2026-09-29)

Methodology feedback from GP (design review) on the preserve-outputs feature
— see [helper-procedures/README.md](helper-procedures/README.md#2026-09-29)
for the full technical detail, or
[GP_FEEDBACK_RESOLUTION.md](GP_FEEDBACK_RESOLUTION.md) for a point-by-point
mapping of GP's 5 feedback items to what was implemented, with code
evidence. Replaces the 2026-08-23–2026-09-21 full-tree
`project_1.gen/.srcs/.xpr`-copy-inside-`RevisionControl/` design with a
per-item copy into an external **remote cache directory** the user supplies:

1. **XPR is never revision-controlled, in any flow** — the full `.xpr` copy
   that Interactive Flow used to make inside `RevisionControl/` is gone.
   `RevisionControl/`'s own shape no longer varies based on whether
   preservation was used at all.
2. **Only the selected item's own `.srcs`/`.gen` are preserved, not the whole
   project's generated tree** — `export_all_sources` now copies each
   preserved IP's/BD's own per-item directory straight into
   `<cache_dir>/<proj_name>/{ip,bd}/<name>/{srcs,gen}/`, where `cache_dir` is
   an absolute path outside the project (`prompt_preserve_outputs`/
   `build_preserve_selection` now also collect this path). Nothing is ever
   copied into `RevisionControl/` for preservation anymore.
3. **Rebuild hard-errors on a missing/incomplete cache** instead of silently
   falling back to Tcl regeneration — the old `.rc_preserve_staging/`
   staging step is gone (no longer needed: the external cache isn't at risk
   from `create_project -force`), and so is the BD "fallback safety-net
   `write_bd_tcl`" that used to paper over a missing preserved artifact.
4. **Side-effect fix:** copying the whole per-BD `.srcs` directory (not just
   the lone `<bd>.bd` file) also preserves nested per-instance
   `ip/<name>/<name>.xci` files customized only inside that BD — closing the
   "Known limitation" noted in the 2026-09-21 and 2026-09-11 entries below.

## Follow-up validation pass (same day) — 2 bugs found and fixed

GP sent 7 action items to close out before sharing this redesign for
testing (clarify 2026.2 behavior, confirm XPR removal, confirm whole-BD-only
preservation, confirm multi-IP selective preservation, add warn/error
handling for un-generated preserve targets, validate against the existing
checklist, communicate results). See
[helper-procedures/README.md](helper-procedures/README.md#2026-09-29) for
full detail. Closing this out surfaced two real bugs, both now fixed:

1. **`generate_build_script` crashed for ANY project with a user-created
   BD, even with preservation never used** — an unescaped
   `$preserved_bd_rel_path($bd_name)` array lookup inside a `puts $fd "..."`
   template string evaluated at template-generation time, for every BD, not
   just preserved ones. Caught by re-running the 9-project regression (all
   9 failed identically) before this could reach testing. Fixed by
   computing the preserved/not-preserved check and the array lookup in an
   outer `if` before the template lines.
2. **A preserved BD's `validate_bd_design`/`save_bd_design` silently
   no-op'd** (`ERROR: [BD 5-159] Cannot find design`, caught by an existing
   `catch{}` so it never failed the build) because `add_files`-ing a
   restored `.bd` doesn't open it as a live BD design — fixed by adding
   `open_bd_design` right after `add_files`, before `validate_bd_design`.

Also added: `export_all_sources` now warns and falls back to standard
export (not a hard block) if preservation is requested for an IP/BD with no
`.gen` output yet, and both the interactive prompt and the docs now say
explicitly that a BD is preserved as one whole unit (no partial
preservation of individual IPs inside a single BD).

**Verification:** full 9-project regression re-run after both fixes — 6
PASS / 3 BLOCKED, identical to the documented pre-existing baseline (no
regressions). Plus a dedicated live smoke test of the remote-cache flow
itself (export → cache contents → rebuild restores + skips resynthesis →
missing cache hard-errors) against `MBV_ipi_design`'s `microblaze_v_preset`
BD — all steps confirmed working after the fixes above.

# Recent changes (2026-09-21)

Two changes, both verified live against a real interactive Vivado session
(via a pty, not simulated) — see
[helper-procedures/README.md](helper-procedures/README.md#2026-09-21) for
the full detail on each:

1. **Consolidated preserve-outputs onto a single `project_1.gen/.srcs/.xpr`
   artifact.** Removed the separate `.sources/IP|BD`, `.gen/<name>`,
   `.XPR/<proj>.xpr` per-item mechanism — the full-tree copy is now the only
   thing `export_all_sources` creates, and `generate_build_script` stages
   what it needs out of that copy at rebuild time instead (new
   `.rc_preserve_staging/`, rebuild-scratch only, not checked in). Along the
   way, fixed a real bug (an IP/BD's source was assumed to always live under
   the `sources_1` fileset — false for at least one BD in this skill's own
   `modnoc-rtl-flat` example design) and a real gitignore bug (the preserved
   full-tree copy was being unconditionally gitignored, silently defeating
   the whole point of preserving it). One known, pre-existing limitation
   remains undocumented no longer: BD preservation only stages the
   top-level `.bd`, not nested per-instance IP files.
2. **`export_all_sources` auto-prompts for Step 2.5** (preserve generated
   outputs) when a human is running the pipeline directly at a real
   interactive terminal and hasn't already answered the question themselves
   — previously you had to already know Step 2.5 existed and call
   `prompt_preserve_outputs` yourself before Step 3, or preservation was
   silently skipped. Detected via a new `_rc_stdin_is_tty` helper;
   automated/CI and agentic callers (no real tty on stdin) are unaffected.

# Recent changes (2026-09-16)

Fixes from a regression re-run of `revision-control-validate` against the
2026-08-10 baseline — see
[helper-procedures/README.md](helper-procedures/README.md#2026-09-16) for the
full detail on each, including how they were verified.

1. **RM-owned IP/RTL parented outside the RM's own fileset can be invisible to
   export** — a real, generalizable DFX authoring gap in principle (the union
   logic itself is straightforward and still worth keeping as a hardening).
   **Correction (2026-09-21 regression re-run):** this was NOT actually
   verified against a real repro. `modnoc-rtl-dfx`'s `axi_dbg_hub_rm1/2`,
   `axis_vio_pl_master_to_ddr_rm1/2`, `perf_axi_tg_pl_master_to_ddr_rm1/2`
   module-not-found errors — originally cited here as evidence this fix
   worked — were confirmed to reproduce identically both before and after
   this fix (the `.xci` files were already visible to the plain
   `get_files -of_objects $rm` query pre-fix; the union added nothing for
   this project). Their real cause is an IP-catalog version lock (every IP
   in that project, RM-owned or not, shows `current catalog revision is
   'unknown'` from the moment the project opens) — an environment/IP-version
   issue, not a fileset-visibility gap. See
   [helper-procedures/README.md](helper-procedures/README.md#2026-09-16) for
   the full corrected writeup. Not a fix for `rtl_dfx`'s `axi_traffic_gen_N`
   or `modnoc-rtl-dfx`'s `RP1_rm1`/`RP1_rm2` — those remain the
   already-documented pre-existing example-design defects (see below).
2. **`verify_rebuild` false-PASS gap (Issue #37)** — it never parsed
   `rebuild.log`, only live structural project state, so a caught-and-logged
   failure (BDC Monitor-mode's `BD 41-258`, or a NoC PHY IP's `generate_target`
   failure) still reported PASS. `verify_rebuild` now takes an optional
   rebuild-log path and scans it for `ERROR:` lines.
3. **NoC memory-controller PHY IP `.elf`/simulation-output failures
   (`modnoc-rtl-dfx`, `ipi_bdc_dfx`, `segmented_config`) — investigated, not
   fixable from this skill.** Confirmed root cause: a Vivado *installation*
   gap (missing files under `data/ip/xilinx/noc_mc_ddr5_phy_v1_0/hdl/bfm/
   PINOUT/`), not an export/rebuild bug — see
   [generate-build-script/REFERENCE.md](generate-build-script/REFERENCE.md)'s
   new "Known Limitation" section for the full diagnosis.

# Recent changes (2026-09-11)

Team decision on the Interactive Flow (Step 2.5 preserve-outputs) directory
layout — see
[helper-procedures/README.md](helper-procedures/README.md#2026-09-11) for the
full detail. `export_all_sources` now also copies the complete original
`<proj_name>.gen/`, `<proj_name>.srcs/`, and `<proj_name>.xpr` directly inside
`RevisionControl/` whenever Step 2.5 preserved at least one IP/BD — additive
to, not a replacement for, the existing per-item `.sources/.gen/.XPR`
mechanism that `generate_build_script`'s rebuild still relies on. Classic
Flow (no preservation) is unchanged: those directories stay external,
untouched siblings of `RevisionControl/`.

# Recent changes (2026-09-02)

Fixed Step 2.5 ("preserve generated outputs") never producing any visible
interaction when the skill is run agentically (an AI agent driving Vivado,
not a human at a real tty) — see
[helper-procedures/README.md](helper-procedures/README.md#2026-09-02) for
the root cause and fix. New `build_preserve_selection` proc lets an agent
ask the human directly in conversation instead of relying on
`prompt_preserve_outputs`'s `gets stdin`, which an agent's batch/Bash-driven
Tcl calls can never actually reach. Purely additive; the human-interactive
and automated-CI paths are unchanged.

# Recent changes (2026-08-23)

Meeting-driven feature request, plus one small fix, both in
`helper-procedures/helper_scripts.tcl` — see
[helper-procedures/README.md](helper-procedures/README.md#2026-08-23) for the
full detail, including how each was verified.

1. **New "preserve generated outputs" step (Step 2.5, interactive-only)** —
   lets the user pick standalone IPs/BDs whose existing `.gen` output should
   be reused as-is on rebuild ("golden project" reuse) instead of being
   regenerated from Tcl. Off by default: `run_pipeline.tcl` (used by
   `revision-control-validate`) never calls it, so every existing automated
   run is unaffected.
2. **`.csv` wasn't recognized as a data file** — added to the Data export
   category alongside `.mem .mif .coe .hex .elf`.

A full 9-project regression pass on 2026-08-23/24 confirmed the automated
5-step path is unchanged by this patch: same 6 PASS / 3 BLOCKED split as
before, and the 3 blocked projects (`ipi_bdc_dfx`, `modnoc-rtl-dfx`,
`rtl_dfx`) are the same pre-existing example-design defects noted below, not
regressions from this change.

# Recent fixes (2026-08-10)

Found via a full validation pass over the 9 `vivado-revision-control` example
designs. All in `helper-procedures/helper_scripts.tcl` — see
[helper-procedures/README.md](helper-procedures/README.md) for the full
detail on each, including how they were verified.

1. **Versal detection never fired** — `detect_project_flow` matched board
   names (`vck*`, `vek*`, ...) against what's actually a part string; added
   part-number prefix matches (`xcv*`/`xc2v*`).
2. **Missing DFX RM source files failed silently** — export now warns
   instead of skipping a stale/broken file reference with no trace.
3. **`create_reconfig_module` never passed `-top`** — Vivado couldn't
   associate an RM with its partition def's module, surfacing as a confusing
   "module name should match" error at rebuild time.
4. **RM file attachment used a command combo that doesn't work** — replaced
   with `add_files -of_objects`, confirmed live against Vivado 2026.2 to be
   the only form that actually attaches a file to an RM's fileset.
5. **Step 6 didn't exclude RM-owned IPs** — they were recreated at project
   scope before Step 12 attached them to their RM, causing "IP name already
   in use."

**Known limitation, not fixable here:** `rtl_dfx` and `modnoc-rtl-dfx` still
show `REBUILD_RESULT=BLOCKED` after all of the above — confirmed via a
scratch-copy rebuild of the untouched original projects that this is a
pre-existing defect in those example designs, not in this skill.
