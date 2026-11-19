# Bundled ipcfg library

This skill's own copy of the `ipcfg` Tcl library. It started as a copy of `lib/` from the
original `vivado-ip-configurator` skill (copied 2026-11-07 from `vivado-ip-configurator_orig/lib`,
files dated 2026-10-07) and is maintained here; this skill replaces that original.

`lib/lite.tcl` sources `ipcfg.tcl` from here. `ipcfg.tcl` in turn sources `clock_search.tcl`,
`preset_drift.tcl` and `rule_options.tcl` from this directory and runs `ipcfg_cache.py` through
`vivado_python.sh` (`vivado_python.cmd` on Windows); lite redirects its learned-parameter cache
into the project's scratch directory.

## Changes from the original (2026-11-07, GitHub issue #498)

- `preset_drift.tcl`: `preset::parse` tracks nesting with a stack, so every leaf keeps its full
  path (`CONFIG.PS_PMC_CONFIG(PMC_SD1(CD_IO(PMC_MIO)))`); `preset::live_lookup` walks any depth of
  live value, including pin ranges (`{PMC_MIO 26 .. 36}`); `OUTPUT_DATA` on pins whose live
  `DIRECTION` is `in` is not reported as drift (the IP stores `default`). The VCK190 CIPS preset
  now parses 67 of 67 values and a matching design reports `PRESET:OK`.
- `ipcfg.tcl`: `ipcfg::capture` without `expect` records a result carrying a failure token
  (`*_FAIL`, `*_ERROR`, `PENDING`, `PARTIAL`) as `FAIL` (shared `ipcfg::failure_token`);
  `ipcfg::rule_for_vlnv` returns `RULE_ERROR:` for input with no RulesMap instead of `RULE:none`;
  `ipcfg::export_proven` records requested values in the form Vivado stores them when applied as
  one dict, so the proven replay check passes.
- `rule_options.tcl`: `automation_rule_options` accepts a pin as well as a cell.

Regression tests: the preset-drift regression suite (issue 498) (16 of 16 pass).
