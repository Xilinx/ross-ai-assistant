# Bundled IP-configuration engine

Phase 2 runs the IP-configuration engine of the `vivado-ip-configurator` skill on cells
the example design created. A copy of that engine lives here so this skill is self-contained
and does not depend on a sibling skill folder or its name:

- `scripts/` -- the engine's Python (guard_mcp, structured_config, the release-database
  reader and their helpers), imported by `examples/ced_guard.py` and `scripts/ced_session.py`.
- `lib/lite.tcl` and `lib/ipcfg/` -- the Tcl it sources in Vivado (ipcfg started as a copy
  of the original vivado-ip-configurator library; see `lib/ipcfg/README.md` for the
  namespace rename that keeps it from colliding with that original).
- `references/*.json` -- data files the engine reads at run time.
- `CONTRACT.md` -- the engine's request/reply contract (the IP skill's SKILL.md body).

Copied from `vivado-ip-configurator` on 2026-11-07 (re-synced 2026-11-08 after the part-support memo, preset and Kria fixes, and again for native-log discovery). To update, copy those same paths from
the IP skill again, then re-apply the `lite::` -> `lite_ced::` / `ipcfg::` -> `ipcfg_ced::`
namespace rename (`lib/ipcfg/README.md` lists every file it touches) -- a straight copy
reintroduces the `LIBRARY_FAIL` collision with `vivado-ip-configurator`'s own copy
(agentic-ai-suite#1777). Setting `CED_LITE_SKILL` to another engine root overrides this copy.
