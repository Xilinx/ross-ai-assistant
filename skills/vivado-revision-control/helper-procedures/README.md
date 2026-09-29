<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# helper_scripts.tcl — recent fixes

## 2026-09-29

### Change: preserve-outputs now uses an external remote cache, not an in-repo full-tree copy

GP (design review) gave methodology feedback on the preserve-outputs feature
that the 2026-08-23–2026-09-21 design didn't match on three points:

1. XPR should never be revision-controlled, in any flow.
2. Rebuild should stay deterministic via Tcl, but for a preserved item do
   `read_ip`/`read_bd`/`add_files` (already true here) with only that item's
   own `.srcs`/`.gen` preserved — not the whole project's generated tree.
3. Only a **remote cache** (a directory outside the project) is supported
   for now; if that absolute path is missing at rebuild time, rebuild must
   error out.

The 2026-09-21 consolidation had actually moved in the opposite direction on
points 1 and 2: it copied the *entire* `<proj_name>.gen/.srcs/.xpr` — every
run, cache, and IP/BD output, plus the XPR itself — into `RevisionControl/`
(a git-controlled directory *inside* the exported project) whenever
preservation was used, then staged per-item pieces back out of that at
rebuild time into `.rc_preserve_staging/`.

**What changed:**

- `export_all_sources` no longer copies anything into `$export_dir`/
  `RevisionControl/` for preservation. Instead, each preserved IP's/BD's own
  `.srcs` directory (the whole directory, not just the `.xci`/`.bd` file —
  see the side-effect fix below) and matching `.gen` directory are copied
  directly into an external cache directory the user supplies, at
  `$cache_dir/<proj_name>/{ip,bd}/<name>/{srcs,gen}/`. `$cache_dir` must be
  an absolute path outside the project (`_rc_validate_cache_dir` enforces
  this); export creates it if missing (export is the producer).
- `prompt_preserve_outputs` (interactive) and `build_preserve_selection`
  (agentic) both gained a `cache_dir` parameter/prompt. An invalid/missing
  path degrades the whole selection to `enabled 0`, same as an
  empty/no-match IP/BD selection already did.
- `generate_build_script`'s old **Step 0** (staging pieces of the full-tree
  copy into `.rc_preserve_staging/` before `create_project -force` could
  overwrite them) is gone entirely — no longer needed, since the external
  cache isn't at risk from `create_project -force` in the first place.
- **Step 1c** now reads directly from the external cache
  (`preserved_cache_dir`, a literal absolute path baked into `build.tcl`) and
  is a **hard error**, not a graceful fallback: if the cache root is
  missing, or a specific preserved item's `srcs`/`gen` subfolders inside it
  are missing/incomplete, it raises a Tcl `error` and the whole rebuild
  stops. Confirmed with the user this should apply to *any* missing
  preserved item's cache data, not just a missing cache root.
- Removed the BD "fallback safety-net `write_bd_tcl`" (written at export
  time for every preserved BD, "just in case" its golden path went missing)
  — it existed specifically to paper over a missing preserved artifact,
  which now contradicts the hard-error intent. Steps 6/7's own "fall back to
  sourcing the Tcl script" branches are gone too, since Step 1c already
  guarantees the source exists before they run.
- Gitignore/README generation no longer varies based on preservation —
  `${proj_name}.gen/`/`.srcs/` are always ignored, `.rc_preserve_staging/`
  is gone, and the README instead documents the external cache path and its
  hard-error behavior when preservation was used.

**Side-effect fix:** copying the whole per-BD `.srcs` directory (not just
the lone `<bd>.bd` file, as the 2026-09-11/09-21 designs did) also captures
any nested per-instance `ip/<name>/<name>.xci` files customized only inside
that BD — closing the "Known limitation" documented in the 2026-09-21 and
2026-09-11 entries below (a rebuild could previously print
`[BD 41-2576] File ... could not be found` for those nested IPs).

**Initial verification (superseded by the follow-up pass below):** a plain
`tclsh` source-only smoke test (no live Vivado) confirmed the edited file
still sources cleanly, plus a standalone test of the new
`_rc_validate_cache_dir` helper's edge cases (empty, relative, nested-under-
project, and a valid external path). At this point the reasoning was
"`run_pipeline.tcl` always passes `preserve_selection={}`, so no full Vivado
regression re-run is needed" — **this reasoning was correct in spirit but
insufficient in practice: it doesn't cover a bug in code that runs
regardless of `preserve_selection`,** which is exactly what the follow-up
pass below found.

### Follow-up pass (same day): live validation surfaced 2 real bugs

GP sent 7 action items to close this out before sharing for testing:
reflect the clarified 2026.2 behavior; confirm XPR removal (done above);
verify/confirm partial-BD preservation is NOT supported; confirm multi-IP
selective preservation; add warn/error handling for preservation requested
on a never-generated IP/BD; validate against the existing checklist; send
an email/Confluence update. Working through these actually exercising
Vivado (not just `tclsh`) found what the initial pass missed:

**Bug 1 — `generate_build_script` crashed for ANY project with a
user-created BD, even with `preserve_selection={}`.** Running the full
9-project regression (`revision-control-validate`) failed all 9 projects
identically with:
```
can't read "preserved_bd_rel_path(<bd_name>)": no such element in array
```
Root cause: Step 7's per-BD dependency-order loop builds one `puts $fd
"..."` template per BD, covering both the preserved and non-preserved
cases. The line for the preserved case did an UNESCAPED
`$preserved_bd_rel_path($bd_name)` lookup inside that string — Tcl
evaluates unescaped `$var`/`[cmd]` inside a double-quoted string
immediately, at the OUTER proc's (i.e. `generate_build_script`'s own)
runtime, not later when the generated file is sourced. So this array
lookup ran for every BD in the project, preserved or not, and threw
whenever the BD wasn't actually in `preserved_bd_names` (the array element
only exists for actually-preserved BDs). This is a real regression from
this same 2026-09-29 change, not pre-existing — confirmed by the fact ALL
9 projects failed identically the first time, none of which use
preservation. **Fixed** by computing `_bd_is_preserved` and
`_preserved_bd_rel_dir_here` once per `bd_name` in an ordinary Tcl `if` at
the OUTER level, before any `puts $fd` line references them, so the array
is only ever touched when the lookup is guaranteed to succeed. Lesson for
future edits to this style of code: an unescaped array/var reference inside
a `puts $fd "..."` template needs the same existence guard a normal script
would need at its own runtime — it's easy to forget because most such
references are for values (a loop variable, a literal) that are always
safe to read.

**Bug 2 — a preserved BD's `validate_bd_design`/`save_bd_design` silently
no-op'd (pre-existing, first live-exercised by this pass's smoke test, not
introduced by 2026-09-29).** Live end-to-end test: preserved
`MBV_ipi_design`'s `microblaze_v_preset` BD via `build_preserve_selection` +
a real external cache dir, rebuilt in a clean directory. Rebuild printed
`Project recreated successfully!` but a full-text scan of the log showed:
```
ERROR: [BD 5-159] Cannot find design named microblaze_v_preset
ERROR: [BD 5-104] A block design must be open to run this command.
```
Root cause: `add_files`-ing a preserved `.bd` registers it in the fileset
but does NOT open it as a live BD design in the current session —
`validate_bd_design -design <name>` and `save_bd_design` both need
`open_bd_design` first. The non-preserved path gets this "for free" because
`source`-ing a `write_bd_tcl` script leaves the BD open as a side effect of
recreating it. Both calls were already wrapped in the existing `catch{}`
(added when this preserved-BD branch was first written), so the ERRORs
never failed the build or `verify_rebuild`'s structural checks — they just
silently no-op'd. **Fixed** by adding `catch {open_bd_design [get_files
${bd_name}.bd]}` right after `add_files`, before `validate_bd_design`, in
both the dependency-order loop and the glob-fallback loop. Re-ran the
rebuild after the fix: zero ERROR/CRITICAL WARNING lines in the log.

**Also added (item 5 of the 7):** `export_all_sources` now checks `.gen`
existence for every selected IP/BD itself (not just the interactive
prompt's advisory note, which an agentic `build_preserve_selection` caller
never sees) — if missing, it WARNs and falls back to standard export for
that one item, rather than writing an incomplete cache entry that would
only surface as a confusing hard error much later, at rebuild time. This
was a judgment call (warn-and-proceed vs. hard-block) surfaced to GP as an
open question, not yet confirmed either way.

**Full end-to-end verification actually performed this pass** (superseding
the "not live-verified" state of the initial pass):
1. Full 9-project regression, re-run after Bug 1's fix: **6 PASS / 3
   BLOCKED** (`ipi_bdc_dfx`, `modnoc-rtl-dfx`, `rtl_dfx`), identical split to
   the documented pre-existing baseline. All 3 blocked for their
   already-documented reasons EXCEPT `ipi_bdc_dfx`, which failed with a NEW
   signature (`ERROR: [BD 5-216] VLNV <xilinx.com:ip:clk_wizard:1.0> is not
   supported for the current part`) — Vivado IP-catalog version drift on
   this specific 2026.2 build (SW Build 6636727, 2026-09-28), unrelated to
   this skill or this change.
2. Dedicated remote-cache smoke test against `MBV_ipi_design`'s
   `microblaze_v_preset` BD (un-synthesized in the reference design, so
   `upgrade_ip [get_ips] -quiet` + `generate_target all` were needed first):
   requested preservation before generating → correctly warned + fell back
   (Bug-adjacent, confirms the new item-5 check); requested again after
   generating → cache got only that BD's own `srcs`/`gen` (confirmed the
   nested per-instance IP files ARE captured, live, not just reasoned);
   `RevisionControl_testB/` had no `.xpr`/`.gen`/`.srcs`; rebuild restored
   from cache and Vivado logged `Generated targets are already up-to-date
   ... hence not re-generating` (skip-resynthesis confirmed live); renaming
   the cache dir and re-running rebuild produced the expected hard error
   (`error "Cannot rebuild: ..."`, exit code 1, halted before touching
   project state).
3. **Not independently live-tested this pass:** the equivalent multi-IP
   mixed-selection path (some standalone IPs preserved, others not) — only
   confirmed via code review; structurally identical to the BD path that
   WAS live-tested. `MBV_ipi_design` only has one eligible item (the BD);
   a design with ≥2 standalone IPs (check `RTL_only_design`/`ipi_multi_bd`
   first) would be needed to close this out.

Smoke test scratch logs kept at
`agentic-ai-suite/tmp/preserve_smoke_test/*.log` (bulky Vivado working
directories deleted after use).

## 2026-09-21

### Change: consolidate preserve-outputs onto a single project_1.gen/.srcs/.xpr artifact

Removed the separate `.sources/IP|BD`, `.gen/<name>`, `.XPR/<proj>.xpr`
per-item mechanism (added 2026-08-23) that existed in parallel with the
full-tree `<proj>.gen/.srcs/.xpr` copy (added 2026-09-11) — the full-tree
copy is now the only preserve-outputs artifact `export_all_sources` creates.

**Why two mechanisms existed:** the per-item one was added first and is what
`generate_build_script` actually read from; the full-tree copy was added
later as a human-browsable reference and was explicitly documented as
"additive, not a replacement... making rebuild actually consume it directly
would be a separate, larger change, out of scope." This change is that
larger change.

**The collision this required solving:** `generate_build_script`'s
`create_project $proj_name . -force` runs before anything else touches
IPs/BDs. Investigated whether this collides with the full-tree copy at
rebuild time: under the *documented* invocation convention (cwd = a
directory containing `RevisionControl/` as a subdirectory, per SKILL.md's
"After Completion" section and `generate-build-script/REFERENCE.md`'s own
comment), it does not — confirmed via a live end-to-end rebuild, cwd puts
the freshly created project as a *sibling* of `RevisionControl/`, not at the
same path as the preserved copy inside it. But a different, very plausible
invocation (`cd RevisionControl; vivado -mode batch -source Scripts/build.tcl`)
*would* put `create_project -force` at the same path as the preserved copy,
destroying it before anything read it. Rather than depend on which
convention whoever runs `build.tcl` happens to use, `generate_build_script`
now emits a new **Step 0**: before `create_project` runs, it stages exactly
what's needed for each preserved IP/BD (that item's `.gen` output + `.xci`/
`.bd` source) out of the full-tree copy into `.rc_preserve_staging/`, and
Steps 1c/6/7 read from there instead of `.gen`/`.srcs` directly.
`.rc_preserve_staging/` is rebuild-time-only scratch — regenerated every
run, gitignored, never created by `export_all_sources` itself.

**A real bug found and fixed along the way:** the old per-item mechanism
(and my first pass at Step 0) assumed a preserved IP/BD's source always
lives under the `sources_1` fileset. Confirmed false on this skill's own
`modnoc-rtl-flat` example design — its `xlnoc` BD lives under
`sim_1/bd/xlnoc/xlnoc.bd`. Fixed by having `export_all_sources` record each
preserved item's actual fileset-relative path (`preserved_ip_rel_path`/
`preserved_bd_rel_path`, new globals) at export time — computed via the
already-live Vivado file object, never guessed — and having Step 0 stage
from that exact path. Also added a guard: if a selected item's source lives
outside `$proj_dir` entirely (Remote/Mixed scenario), it now falls back to
standard export instead of recording a path that could never resolve (the
full-tree copy only ever captures what's inside `$proj_dir`).

**Also fixed:** the generated `.gitignore`/`README.md` unconditionally
ignored `${proj_name}.gen/`/`.srcs/` — harmless for Classic Flow (external
siblings, a gitignore inside `RevisionControl/` can't reach them anyway) but
silently defeated Interactive Flow's entire purpose, since that's now the
one artifact that must be checked in. Both are now conditional on whether
anything was actually preserved.

**Known limitation, pre-existing, not introduced here:** BD preservation
stages only the top-level `<bd>.bd`, not a per-instance `ip/<name>/<name>.xci`
subdirectory that can sit alongside it for IPs customized only inside that
BD — confirmed the old `.sources/BD` mechanism had the identical gap (also
copied only the single `.bd` file). A rebuild can print `[BD 41-2576] File
... could not be found` for those nested IPs even though the BD's own `.bd`
restores correctly; not a hard failure, just incomplete preservation for
that BD's nested IPs. Worth a dedicated fix, not attempted here.

**Verified:**
- `tclsh` source-only smoke test after every edit (syntax/brace check).
- Live end-to-end test via a real pty (not simulated) on `modnoc-rtl-flat`:
  auto-prompt fires, answering `y`/`all` produces the consolidated structure
  (no `.sources`/`.gen`/`.XPR`), and — new this session — actually **ran the
  generated `build.tcl`** end-to-end: `.rc_preserve_staging/` staged
  correctly (including the `sim_1`-fileset `xlnoc` case), the preserved BD's
  `.bd` was found and added, `project_1.gen/.srcs/.xpr` survived the rebuild
  completely intact (byte-identical file count before/after), and the
  rebuild finished with "Project recreated successfully!" (with the known
  nested-IP limitation above producing warnings, not a failure).
- Classic Flow (non-preserve) sanity re-run on `MBV_ipi_design`:
  `REBUILD_RESULT=PASS`, confirmed zero `.sources`/`.gen`/`.XPR`/
  `.rc_preserve_staging` artifacts leak in when nothing was preserved.

### Change: `export_all_sources` now auto-prompts for Step 2.5 in a real interactive session

Previously, Step 2.5 (`prompt_preserve_outputs`) was opt-in in the strongest
sense: a human running the pipeline step-by-step had to already know it
existed and call it themselves before Step 3, or preservation was silently
skipped with no indication anything was even eligible.

**Fix:** new helper `_rc_stdin_is_tty` (`exec test -t 0`, fails closed — i.e.
returns "not a tty" — on any error) detects a real interactive terminal.
`export_all_sources` now checks, right after its preserve_selection
parameter defaults: if the caller left `preserve_selection` at its raw `{}`
default *and* stdin is a real tty *and* `list_preservable_outputs` finds at
least one eligible IP/BD, it calls `prompt_preserve_outputs` itself before
proceeding, instead of silently defaulting to no preservation.

This only changes behavior for a genuine interactive human session:
- Automated/CI callers (`run_pipeline.tcl`-style harnesses) never have a
  real tty on stdin (confirmed via `tclsh` smoke test with both `/dev/null`-
  and pipe-redirected stdin, matching how `revision-control-validate`'s
  `run_project.sh` actually invokes Vivado) — `_rc_stdin_is_tty` returns 0,
  so this code path is never reached and behavior is byte-for-byte
  unchanged.
- Agentic callers (an AI agent driving Vivado via Bash/batch-mode Tcl calls)
  also have no real tty on stdin — unaffected. They should keep using
  `build_preserve_selection` as documented.
- A human who already called `prompt_preserve_outputs` or
  `build_preserve_selection` themselves and passed a populated
  `preserve_selection` in is also unaffected — the auto-check only fires
  when the argument is still the untouched default.

Not verified against a live interactive Vivado GUI/tty session this
session (no such session was available to test from) — verified only that
the non-tty path is unaffected (via `tclsh`) and that the file still parses
cleanly. If this doesn't actually prompt in a real GUI Tcl console (Vivado's
console may not expose a conventional process stdin the same way a shell
does), that would need a follow-up fix specific to how Vivado's GUI console
handles `gets stdin`/`exec test -t 0`.

## 2026-09-16

Three fixes from a regression re-run of `revision-control-validate` (2026-09-16
vs. the 2026-08-10 baseline) against all 9 example designs. All in
`helper_scripts.tcl` unless noted.

### Fix: RM-owned IP/RTL parented outside the RM's own fileset was invisible to export

Both `export_all_sources`'s DFX RM export loop and `capture_project_settings`'s
`dfx_reconfig_modules` manifest capture discovered RM-owned files via
`get_files -quiet -of_objects [get_reconfig_modules $rm]` only. If an IP was
added to the project while a *different* fileset (typically `sources_1`) was
Vivado's "current" fileset — even though the IP is only instantiated from
inside that RM's own RTL hierarchy — that query never sees it. The file then
falls through to being recreated at project scope on rebuild (Step 6 of
`generate_build_script`), invisible to that RM's own out-of-context
synthesis, producing `[Synth 8-439] module 'X' not found`.

This is an ordinary, easy-to-hit real-world DFX authoring mistake (forgetting
to switch `current_fileset` before adding an IP via the IP Catalog/GUI), not
specific to a broken example — confirmed each RM also has its own named
fileset on disk (e.g. `rp1_rm1/`) that the export logic never queried.

**Fix:** new shared helper `_rc_get_rm_files {rm rm_name}` unions
`get_files -of_objects $rm` with `get_files -of_objects [get_filesets $rm_name]`
(dedup'd by normalized path), used identically at both call sites so a file
that's exported is also attached at rebuild time (or neither, never one
without the other).

**NOT actually verified — correction from the 2026-09-21 regression re-run:**
this fix was originally claimed to target `modnoc-rtl-dfx`'s six
module-not-found errors (`axi_dbg_hub_rm1/2`, `axis_vio_pl_master_to_ddr_rm1/2`,
`perf_axi_tg_pl_master_to_ddr_rm1/2`, first seen in the 2026-09-16 regression
report). A 2026-09-21 re-run against the current (post-fix) code reproduced
the identical errors, and direct comparison of the pre-fix (2026-09-16) and
post-fix (2026-09-21) export trees showed the `.xci` files for all six RMs
were **already present** in `Sources/RTL/` before this fix existed — i.e.
`get_files -of_objects $rm` alone already found them for this project, and
`_rc_get_rm_files`'s union added nothing. The real cause of these six errors
is an IP-catalog version lock: from the moment `modnoc-rtl-dfx` is opened,
every IP in the project (RM-owned and not) logs
`WARNING: [IP_Flow 19-2162] IP 'X' is locked ... current catalog revision is
'unknown'` — an environment/IP-version issue (the IP never regenerates
output products, so synth can't find its module), unrelated to fileset
visibility. This reproduces identically with or without this fix.

The union logic in `_rc_get_rm_files` is still plausible as a general
hardening (an IP added under the wrong `current_fileset` is a real class of
mistake), but it has **no confirmed real-world repro** as of 2026-09-21 —
treat it as code-reviewed-but-unverified until one is found, not as a fix
proven against `modnoc-rtl-dfx`.

**Explicitly NOT fixed, and not attempted:** `rtl_dfx`'s `axi_traffic_gen_N`
and `modnoc-rtl-dfx`'s `RP1_rm1`/`RP1_rm2` stale-absolute-path failures — both
already confirmed (2026-08-10 entry below) to be pre-existing defects in the
example designs themselves, reproducible even on a pristine, never-exported
scratch copy of the original `.xpr`. Re-litigating those was out of scope.

### Fix: `verify_rebuild` false-PASS gap (Issue #37)

`verify_rebuild` only checked live structural project state (BD/top/run
presence, IP count) — it never parsed `rebuild.log`, so a
generate_build_script step that caught a failure and printed `ERROR: ...`
without aborting (confirmed cases: BDC Monitor-mode's `BD 41-258` on
`ipi_bdc_flat_flow`, and a NoC PHY IP `generate_target` failure on
`segmented_config` — see below) still reported an overall PASS.

**Fix:** `verify_rebuild` now takes an optional second argument,
`{rebuild_log ""}`. If a path is given and the file exists, it's scanned for
`^ERROR:` lines; any found set `all_ok 0` (capped display: first 5 + a
count). No allowlist — every `ERROR:`-prefixed line seen so far is a genuine
failure; add one only if a real benign case turns up. Backward compatible:
existing single-arg callers are unaffected.

Also added, in the separate `revision-control-validate` validation harness
(`run_project.sh`, not part of this skill/repo): an additive
`grep -c '^ERROR:'` safety-net check that emits `PROJECT_RESULT=WARN` if the
rebuild log both reports `REBUILD_RESULT=PASS` and contains `ERROR:` lines —
independent of the Tcl-side fix, doesn't overwrite `REBUILD_RESULT`.

**Verified against:** dry-run grep of the 2026-09-16 report's existing
`rebuild.log` files. `ipi_bdc_flat_flow` — 0 pre-existing `ERROR:` lines
outside the 8 known `BD 41-258` ones (this fix correctly turns that PASS into
a FAIL). Unexpectedly also found `segmented_config` — reported as a clean
PASS in every prior run — actually contains 20 `ERROR:` lines (NoC PHY IP
`generate_target` failure, see the next entry) that no prior structural check
caught; this fix correctly surfaces that too.

### Investigated, NOT fixable: NoC memory-controller PHY IP `.elf`/simulation-output failures

Affects `modnoc-rtl-dfx` and `ipi_bdc_dfx` (`ERROR: [Common 17-275] File does
not exist [...bd_<hash>_MC0_ddrc_0_phy_ddrmc.elf]` at OOC-synth time) and
`segmented_config` (`generate_target` failing outright for the top-level BD,
previously masked by the Issue #37-style false-PASS gap above).

**Confirmed root cause: a Vivado *installation* gap, not an export/rebuild
bug.** The NoC memory-controller PHY IP (`noc_mc_ddr5_phy_v1_0`) generates
its simulation BFM output by copying numbered `map_read_<N>.sv`/
`pinout_<N>.sv` files from the Vivado install's own
`data/ip/xilinx/noc_mc_ddr5_phy_v1_0/hdl/bfm/PINOUT/` directory. On the
install used for this diagnosis (2026.2,
`/proj/xbuilds/CustTA_plus/9999.0_2026_0724_2226plus/...`), several indices
are missing entirely (confirmed by direct filesystem listing — `pinout_14`,
`pinout_19`/`map_read_19` do not exist, neighboring indices do), so
generation fails identically regardless of whether the skill is involved at
all. Also confirmed none of the 9 example designs ship a pre-generated
`.gen`/`.elf` for this IP, so there is no existing valid artifact anywhere to
preserve/copy through export (the fix pattern used for `preserve_selection`
does not apply — there's nothing to preserve).

**Not fixed.** No Tcl change in this skill can synthesize a Vivado IP-library
file that doesn't exist on disk. Documented in
[generate-build-script/REFERENCE.md](../generate-build-script/REFERENCE.md)'s
"Known Limitation" section, and the misleading debug-net-only hint in
`generate_build_script`'s `generate_target` catch block (Step 9) was broadened
to also name this failure class, since the old hint would have sent someone
chasing ILA/mark_debug connections instead of a missing install file.

## 2026-09-11

### Change: full `<proj_name>.gen/.srcs/.xpr` copy inside `RevisionControl/` for Interactive Flow

Team decision, reviewed against the skill's documented output structure: the
per-item preserve-outputs mechanism added 2026-08-23 (`.sources/`, `.gen/`,
`.XPR/` under the export dir, scoped to only the IPs/BDs a user chose to
preserve) is being extended, not replaced, for Interactive Flow. Whenever
`preserve_selection` (Step 2.5) selects at least one IP or BD,
`export_all_sources` now additionally copies the **entire** original project
directories — `$proj_dir/<proj_name>.gen`, `$proj_dir/<proj_name>.srcs`,
`$proj_dir/<proj_name>.xpr` — directly into `$export_dir` under the
project's own name (not dot-prefixed).

**Scope, deliberately:**

- Interactive Flow only (`preserve_any == 1`). Classic Flow
  (`preserve_selection` omitted/declined) is completely unchanged —
  `<proj_name>.gen/.srcs/.xpr` remain external, untouched siblings of
  `$export_dir`, exactly as before this change.
- Additive to, not a replacement for, the existing `.sources/IP|BD`,
  `.gen/<name>`, `.XPR/<proj>.xpr` per-item copies — `generate_build_script`'s
  rebuild logic still reads from those, unchanged by this patch.
- **Unfiltered:** unlike every other export category in this skill, this copy
  is not limited to RTL/constraints/IP-BD-scripts or even to the
  specifically-preserved items — it's the whole `.gen`/`.srcs` tree,
  including every synthesis/impl run and every IP/BD's generated output. This
  is an intentional, called-out exception to the "skip generated files, they
  shouldn't be version-controlled" rule that governs the rest of
  `export_all_sources` (see
  [export-all-sources/REFERENCE.md](../export-all-sources/REFERENCE.md)).
  Expect export size and downstream git repo size to scale with the whole
  project, not with the curated subset this skill otherwise tracks.

**Not changed:** `generate_build_script` does not consume this new top-level
copy — it still rebuilds via Tcl regeneration / the per-item golden-`.gen`
path. Making rebuild actually skip regeneration by reopening the copied
`.xpr` directly would be a separate, larger change, out of scope here.

**Verified:** `tclsh` source-only smoke test (file parses, no brace/bracket
errors introduced). **Not yet verified against a live Vivado export** — next
step is running `revision-control-validate` with preservation enabled on an
IP/BD-bearing example design and confirming `<proj_name>.gen/.srcs/.xpr`
actually land inside `RevisionControl/` with the expected contents.

## 2026-09-07

### Fix: preserved BD silently dropped (and its wrapper along with it) when the golden `.gen`/`.sources` path doesn't resolve

Team feedback validating "preserve generated outputs" (Step 2.5) on a real
design: a top-level BD wrapper that came from the original project's `.gen`
folder failed to import into the recreated project — both the BD *and* its
wrapper went missing — reproducing specifically when the exported
`RevisionControl/` tree's internal directory layout was changed after export.

**Root cause:** a BD selected for preservation skips `write_bd_tcl` entirely
at export time (by design — only `.gen` output + the `.bd` source are
copied, to avoid unnecessary regeneration). At rebuild time,
`generate_build_script` looks for that `.bd` at a single hardcoded path
(`$base_dir/.sources/BD/<name>.bd`); if it's not there, the code only
printed a `WARNING` and moved on — there was no fallback, because no Tcl
script had ever been written for that BD. With the BD never added to the
project, Step 8's wrapper creation (`get_files ${bd_name}.bd`) then also came
back empty and silently skipped `make_wrapper` too — one root cause, two
missing artifacts.

**Fix (all in `helper_scripts.tcl`):**

1. `export_all_sources` — preserved BDs now *also* get a `write_bd_tcl` Tcl
   script written to `Sources/BD/<name>.tcl`, in addition to the golden
   `.gen`/`.sources` copy. `write_bd_tcl` doesn't resynthesize anything, so
   this is cheap at export time and doesn't defeat the point of
   preservation — the golden path is still tried first at rebuild time.
2. `generate_build_script` (dependency-ordered BD sourcing) — if the
   preserved `.sources/BD/<name>.bd` isn't found at rebuild time, it now
   falls back to `source`-ing the new `Sources/BD/<name>.tcl` instead of
   leaving the BD (and its wrapper) out of the rebuild.
3. `generate_build_script` (legacy no-dependency-order fallback path) — same
   fallback added; also had to make the generic Tcl glob-loop in that path
   skip preserved BD names first, since they now have a `.tcl` file too and
   that loop would otherwise force a full regeneration on every rebuild,
   defeating preservation even when the golden path would have resolved
   fine.

Net effect: golden-`.gen` reuse is still the primary, fast path and still
skips resynthesis when it resolves; only when it can't resolve does the BD
fall back to full Tcl regeneration instead of disappearing.

**Verified:** `tclsh` source-only smoke test (file parses cleanly, both
edited procs still load) — **not yet verified against a live Vivado
rebuild.** Next step: run `revision-control-validate` against a BD-bearing
example design with preservation enabled, deliberately remove/rename
`.sources/BD` after export, and confirm the rebuild now falls back instead
of dropping the BD.

**Known related gap, not fixed here:** preserved standalone IPs (XCIs) have
the identical structural issue in the same file (`export_all_sources` around
the IP preserve branch, `generate_build_script`'s Step 6 preserved-IP
handling) — only the BD path was in scope for this fix.

## 2026-09-02

### Fix: Step 2.5 never actually asked anything in agentic use

Team feedback running the skill agentically (Claude driving Vivado, not a
human at a real tty): "no interaction related to O/Ps generation" ever
happened. Root cause: `prompt_preserve_outputs` only works via `gets stdin`,
which requires a real tty; the docs only covered "human at an interactive
tty" and "automated CI pipeline, skip entirely" and had no guidance for an
agent that *should* ask the human but can't do it through Tcl's stdin. An
agent following the old docs would either skip Step 2.5 as if it were CI, or
call `prompt_preserve_outputs` and get silent EOF -- either way, no prompt
ever reached the human.

**Fix:** added `build_preserve_selection {sel_ips sel_bds}` -- a stdin-free
version of `prompt_preserve_outputs` that validates an already-chosen
list of names against `list_preservable_outputs`. `SKILL.md` and
`helper-procedures/REFERENCE.md` now spell out three cases instead of two:
human-interactive (`prompt_preserve_outputs`), automated CI (skip, pass
`{}`), and agentic (call `list_preservable_outputs`, ask the human directly
in conversation, then call `build_preserve_selection` with the answer).

Purely additive -- `prompt_preserve_outputs`, `list_preservable_outputs`,
`export_all_sources`, and `generate_build_script` are unchanged, so both
pre-existing paths (interactive-tty and automated-CI) behave exactly as
before. Verified with a `tclsh` source-only smoke test (no brace/bracket
errors introduced).

## 2026-08-23

### New: "preserve generated outputs" (golden-project reuse), Step 2.5

Meeting-driven feature request: let a user reuse an existing, known-good
`.gen` output for a standalone IP or user-created BD across an export/rebuild
cycle instead of always regenerating it from Tcl.

**What was added:**

- `list_preservable_outputs` — enumerates eligible standalone IPs (XCIs
  outside any BD, via `IS_BD_CONTEXT`) and user-created BDs.
- `prompt_preserve_outputs` — new, optional Step 2.5. Interactive-only
  (blocks on `gets stdin`); lists eligible items and lets the user pick which
  to preserve, or `all`, or none. Returns a `{enabled ips bds}` dict.
- `export_all_sources` / `generate_build_script` both take an optional
  `preserve_selection` argument. For selected IPs/BDs: `write_ip_tcl` /
  `write_bd_tcl` are skipped entirely; the existing `.gen` output plus the
  `.xci`/`.bd` source are copied into new `.gen/` / `.sources/` / `.XPR/`
  directories under the export dir. These directories are created **only**
  when something is actually selected — never on a "No" answer, never
  expected in an older/existing export.
- On rebuild, `generate_build_script` restores the preserved `.gen` into the
  new project's own `.gen` tree (Step 1c) **before** `add_files`-ing the
  preserved source — Vivado's own out-of-date check then sees the IP/BD as
  current and skips real resynthesis/regeneration (standard Vivado
  incremental behavior, no special flag involved). If a preserved item's
  `.gen` is missing at rebuild time, it's logged and that one item falls back
  to normal Tcl regeneration — does not hard-fail the rebuild.

**Not exercised by automation:** `run_pipeline.tcl` (the driver used by
`revision-control-validate`) never calls `prompt_preserve_outputs`, so
`preserve_selection` is always `{}` in the automated 9-project pass — the
pre-existing fully-automated behavior is unchanged. Verified with a
`tclsh` source-only smoke test (confirms no brace/bracket syntax errors) and
by re-running the full 9-project regression, which came back with the same
6 PASS / 3 BLOCKED split as the 2026-08-10 baseline.

### Fix: `.csv` not recognized as a data file

`export_all_sources`'s Data-file export category (`data_exts`) only matched
`.mem .mif .coe .hex .elf`, so `.csv` files (e.g. coefficient/init tables)
were left out of both the `get_files`-based loop and the fallback
project-root glob. Added `.csv` to `data_exts` in both places.

**Not verified against a live project:** none of the 9 example designs
contain a `.csv` file, so this is confirmed by code inspection only, not by
an export run that actually picks one up (same limitation noted for the
other data extensions in the 2026-08-10 pass, below).

## Versal detection in `detect_project_flow` never fired

`$device` holds the actual part string (e.g. `xcvc1902-vsva2197-2MP-e-S`), not
a family/board name. The existing `string match` checks (`*versal*`, `vek*`,
`vck*`, `vmk*`, `vhk*`) only ever match board names, so `is_versal` was always
`0` — segmented-config handling for Versal designs was silently skipped.

**Fix:** added part-number prefix matches — `xcv*` for Gen1 (`xcvc`/`xcve`/
`xcvp`/`xcvm`) and `xc2v*` for Gen2 (`xc2ve`/`xc2vp`/`xc2vm`/`xc2vc`, e.g.
`xc2ve3858`) — alongside the original board-name patterns, which are kept as
a fallback in case a board string is ever passed instead of a part.

## Missing RM source files failed silently in `export_all_sources`

When a reconfig module's registered file path didn't resolve to a real file
on disk, the DFX export loop just `continue`d past it with no message. The
gap only surfaced later as a confusing rebuild-time error (e.g. a
partition/RM module-name mismatch) with no link back to the real cause.

**Fix:** the loop now prints `WARNING: RM '<name>' file not found at export
time, skipping: <path>` before skipping, so the stale/broken reference is
diagnosable at export time instead of at rebuild time.

## DFX reconfig-module rebuild failures (`rtl_dfx`, `modnoc-rtl-dfx`)

`generate_build_script`'s emitted `dfx_define_partition` proc had two
independent bugs, both verified live against Vivado 2026.2 (not inferred from
docs):

1. **`create_reconfig_module` never passed `-top`.** Without it, Vivado can't
   associate the RM with its partition def's module name — this surfaced as
   `modnoc-rtl-dfx`'s confusing `partition definition 'RP1' module name should
   match with 'top' name of any one of its reconfigurable module` error.
   Fixed by passing `-top $rm_module` (the RM's own top module name, already
   captured during export via `MODULE_NAME`, just never threaded through).

2. **RM file attachment used a command/argument combo that doesn't work.**
   `import_files -of_objects [get_reconfig_modules ...]` silently no-ops for
   freshly-copied IP (`.xci`) sources, and the existing `.xci` fallback
   (`get_filesets -of_objects` → `add_files -fileset`) never actually attaches
   anything either — `get_filesets -of_objects <reconfig_module>` returns a
   value that *looks* like a fileset name but isn't one `-fileset` can resolve
   by name. Trying `add_files -fileset [get_reconfig_modules ...]` directly
   errors outright with `Adding files to a fileset created by RM is not
   supported. Please use 'add_files <> -of_objects [get_reconfig_modules
   <>]'` — Vivado's own error message names the fix. Replaced both the
   primary attempt and the broken fallback with a single
   `add_files -norecurse -of_objects [get_reconfig_modules $rm_name] $file`,
   confirmed via a from-scratch repro to actually attach the file.

3. **Step 6 (`Recreate IP Cores`) didn't exclude RM-owned IPs.** Every IP's
   `write_ip_tcl` script was exported unconditionally, so Step 6 recreated
   RM-owned IPs (e.g. `axi_traffic_gen_1`) at the project/`sources_1` scope
   *before* Step 12 tried to attach the same IP's raw `.xci` copy to its RM —
   causing `IP name 'axi_traffic_gen_1' is already in use in this project`.
   Fixed by skipping IP export for any IP whose `.xci` tail is already in
   `dfx_rm_file_tails` (the same exclusion list Step 2's RTL import and Step
   7's BD sourcing already use), mirroring the existing BD-context skip.

**Residual, NOT fixed by the above (pre-existing bugs in the example designs,
not the skill):**

- `rtl_dfx`'s RM OOC synthesis (`rp1_rm1_synth_1` etc.) fails with
  `[Synth 8-439] module 'axi_traffic_gen_N' not found` **even on the
  pristine, never-exported original project** — confirmed by running
  `launch_runs synth_1` directly against a scratch copy of the untouched
  `.xpr`. This is an inherent defect in the reference design itself; no
  export/rebuild script change can fix it.
- `modnoc-rtl-dfx`'s `RP1_rm1`/`RP1_rm2` RMs reference their own top-level
  RTL (`RP1_rm1.v` etc.) via a stale absolute path baked into the original
  project (missing the `modnoc-rtl-dfx/project_mode.srcs/RP1_rm1/imports/...`
  path segment). Already correctly surfaced by the "Missing RM source files"
  fix above (`WARNING: RM '<name>' file not found at export time`), but not
  repairable from the skill side — the real file just isn't at the path the
  project's `.xpr` claims.

Both projects will still show `REBUILD_RESULT=BLOCKED` after these fixes —
for a correctly diagnosed reason instead of a confusing one, not because the
fixes are incomplete.
