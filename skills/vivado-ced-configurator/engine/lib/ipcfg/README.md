# Bundled ipcfg library

This skill's own copy of the `ipcfg` Tcl library. It started as a copy of `lib/` from the
original `vivado-ip-configurator` skill (copied 2026-11-07 from `vivado-ip-configurator_orig/lib`,
files dated 2026-10-07) and is maintained here; this skill replaces that original.

`lib/lite.tcl` sources `ipcfg.tcl` from here. `ipcfg.tcl` in turn sources `clock_search.tcl`,
`preset_drift.tcl` and `rule_options.tcl` from this directory and runs `ipcfg_cache.py` through
`vivado_python.sh` (`vivado_python.cmd` on Windows); lite redirects its learned-parameter cache
into the project's scratch directory.

**Namespace:** this copy lives in `ipcfg_ced::` (and `lib/lite.tcl` lives in `lite_ced::`), not
the upstream `ipcfg::`/`lite::`. `vivado-ip-configurator` ships its own, independently-drifted
copy of this same library in `ipcfg::`/`lite::`; sourcing both in one Vivado session used to fail
with `LIBRARY_FAIL:lite changed` / `LIBRARY_FAIL:ipcfg changed` because they shared a namespace
(agentic-ai-suite#1777). If you re-sync from `vivado-ip-configurator`, re-apply the `ipcfg::` ->
`ipcfg_ced::` and `lite::` -> `lite_ced::` rename (including the bare `namespace eval ipcfg {`
headers) across `lib/lite.tcl`, everything in `lib/ipcfg/`, `engine/scripts/structured_config.py`,
`engine/scripts/merged_database.py`, `engine/scripts/export_database.tcl`, `../../lib/ced_op.tcl`
and `../../scripts/ced_session.py` -- otherwise the collision comes back.

## Changes from the original (2026-11-07, GitHub issue #498)

- `preset_drift.tcl`: `preset::parse` tracks nesting with a stack, so every leaf keeps its full
  path (`CONFIG.PS_PMC_CONFIG(PMC_SD1(CD_IO(PMC_MIO)))`); `preset::live_lookup` walks any depth of
  live value, including pin ranges (`{PMC_MIO 26 .. 36}`); `OUTPUT_DATA` on pins whose live
  `DIRECTION` is `in` is not reported as drift (the IP stores `default`). The VCK190 CIPS preset
  now parses 67 of 67 values and a matching design reports `PRESET:OK`.
- `ipcfg.tcl`: `ipcfg_ced::capture` without `expect` records a result carrying a failure token
  (`*_FAIL`, `*_ERROR`, `PENDING`, `PARTIAL`) as `FAIL` (shared `ipcfg_ced::failure_token`);
  `ipcfg_ced::rule_for_vlnv` returns `RULE_ERROR:` for input with no RulesMap instead of `RULE:none`;
  `ipcfg_ced::export_proven` records requested values in the form Vivado stores them when applied as
  one dict, so the proven replay check passes.
- `rule_options.tcl`: `automation_rule_options` accepts a pin as well as a cell.
- All of `lib/ipcfg/` (2026-11-17, GitHub issue #1777): renamed the `ipcfg::` namespace to
  `ipcfg_ced::` so this skill's copy no longer collides with `vivado-ip-configurator`'s own,
  differently-drifted `ipcfg::` copy when both are sourced in the same Vivado session.

Regression tests: the preset-drift regression suite (issue 498) (16 of 16 pass).
