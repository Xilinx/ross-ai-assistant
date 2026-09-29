<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# GP methodology feedback (2026-09-29) — resolution status

GP (design review) sent feedback on the "preserve generated outputs" (Step
2.5) feature of this skill, covering 5 points. This doc maps each point to
what was implemented, with code evidence, so it can be confirmed closed out
without re-reading the whole diff. See
[helper-procedures/README.md](helper-procedures/README.md#2026-09-29) for
the full technical changelog and the 2 bugs found+fixed while verifying
this.

## 1. XPR in revision control vs. recreate via Tcl

**GP's guidance:** don't revision-control the XPR (too verbose, not
revision-control friendly) — recommend rebuilding via Tcl instead.

**Status: ✅ Done.** No `.xpr` copy logic exists anywhere in the
export/preserve path. The only remaining `.xpr` reference is an explicit
comment noting it's intentionally excluded:
```
# helper_scripts.tcl:3105
Note: <proj_name>.xpr is intentionally NOT part of this export or check-in --
```

## 2. Behavior when "preserve output products" is selected

**GP's guidance:** still rebuild the project deterministically via script.
For preserved XCIs/BDs, the script should do `read_ip`/`read_bd`/`add_files`
instead of sourcing `ip.tcl`/`bd.tcl` — as long as `.srcs`/`.gen` are
available, Vivado will pick up the generated outputs from `.gen`
automatically.

**Status: ✅ Done.** Both preserved-item code paths use `add_files` (one of
the three methods GP named) instead of sourcing an exported `ip.tcl`/
`bd.tcl` script:
- **Preserved IP:** `add_files -norecurse [...]` then
  `catch {generate_target all [get_ips $name]}`.
- **Preserved BD:** `add_files -norecurse [...] {${bd_name}.bd}` then
  `generate_target all [get_files ${bd_name}.bd]`, plus `open_bd_design`
  (a bug fix from this same pass — `add_files` alone doesn't open a BD as
  a live design, which broke `validate_bd_design`/`save_bd_design`
  afterward) before `validate_bd_design`/`save_bd_design`.

The cache restore (point 4 below) runs *before* this step, so `.srcs`/
`.gen` are already in place when `add_files`/`generate_target` run —
confirmed live: `generate_target` logs "Generated targets are already
up-to-date ... hence not re-generating" instead of resynthesizing.

## 3. Handling mixed scenarios (partial preservation)

**GP's guidance:** most users want to preserve a *selected* subset, not
all-or-nothing — list all XCIs (outside any BD) and BDs, let the user pick.
Finer-grained picking (e.g. individual IPs inside one BD) is a Q4
enhancement, not required now.

**Status: ✅ Already implemented** (pre-dates this pass, untouched by it):
`list_preservable_outputs` / `prompt_preserve_outputs` (interactive) /
`build_preserve_selection` (agentic) list every standalone XCI and every
BD, and let the caller pick a subset of each. Matches GP's description
exactly. The one known limitation — preservation is whole-BD-only, no
cherry-picking individual IPs *inside* a single BD — is now explicit in
the docs/prompts rather than an undocumented side effect, and lines up
with GP's own note that finer granularity is a Q4 item, not a now-gap.

## 4. Cache: remote only, hard error if missing

**GP's guidance:** OK to support only a remote cache (directory outside the
project) for now. If that absolute path is missing at rebuild time, the
rebuild should error out.

**Status: ✅ Done.** `_rc_validate_cache_dir` rejects anything that isn't an
absolute path outside the project. At rebuild time (Step 1c):
```tcl
if {![file isdirectory $preserve_cache_dir]} {
    error "Cannot rebuild: remote cache directory for preserved IP/BD outputs is missing ..."
}
```
The same hard-error check runs again per preserved item (missing `srcs/`
or `gen/` for that specific IP/BD also halts the whole build). Live-verified
by renaming the cache dir and confirming rebuild stopped before touching
any project state.

## 5. Generated artifacts inclusion rules

**GP's guidance:** if a BD or XCI is chosen for preservation, preserve both
its `.srcs` and its `.gen`.

**Status: ✅ Done.** Cache restore copies both
`<cache>/<proj>/{ip,bd}/<name>/srcs/` and `.../gen/` into the rebuilt
project's `${proj_name}.srcs/`/`${proj_name}.gen/` — both directories, not
just the bare `.xci`/`.bd` file. This also fixed a pre-existing gap:
copying the whole `.srcs` directory (not just the `.bd`) now captures
nested per-instance IPs customized only inside a preserved BD.

## Verification performed

Full 9-project regression re-run after the fixes above: 6 PASS / 3 BLOCKED,
identical to the documented pre-existing baseline (no regressions from this
redesign). Plus a dedicated hand-run smoke test of the remote-cache flow
itself against `MBV_ipi_design`'s `microblaze_v_preset` BD — export, cache
contents, rebuild-restores-and-skips-resynthesis, and missing-cache
hard-error all confirmed working.

## Out of scope for this feedback thread

These are pre-existing, unrelated gaps — not part of GP's 5 points above,
and not affected by this pass:
- Confluence checklist items #1 (property gaps), #10 (data files), #37
  (BDC Monitor-mode), #52 (doc review), #65 (unknown) remain open/
  unverified.
- Additional checks A–D (property-level diffing, double-run safety
  regression, Git LFS tracking, encrypted-IP handling) remain
  not-yet-automated.

## Status as of 2026-09-29

All changes described here are **uncommitted** working-tree edits in
`agentic-ai-suite` (baseline: commit `04312979`, 2026-09-21). Not yet
pushed/shared.
