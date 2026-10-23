<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Function index: vivado-ip-configurator

Generated from the Tcl helpers; do not edit by hand. This is the complete list of what this skill can call. Read it
instead of `lib/*.tcl` or `scripts/*.py`: the source adds nothing a caller needs.

## Tcl helpers: `ipcfg::` (92)

Load the library in the Vivado session first. `ipcfg::help <word>` prints the same entry live.

- `ipcfg::add_stub cell pin vlnv ?prop={}? ?val={}?`
  Generic connection-derived driver (replaces per-IP Phase 4 stubs) Attaches a boundary interface port to a cell's interface pin so that
  ok: SUCCESS:stub   fail: CONFIGURE_FAIL
- `ipcfg::api ?pat={}?`
  Configurator workflow index: numbered STEPS with no argument, matching glossary lines for a word.
  ok: API:no
- `ipcfg::apply_dict cell d ?allow_board=0?`
  Apply one CONFIG -dict to an existing cell atomically; refuses board-owned keys unless allow_board is 1.
  ok: SUCCESS:already SUCCESS:applied SUCCESS:no   fail: CONFIGURE_FAIL
- `ipcfg::attempt_call cell operation script`
  Run one create/configure/automation script against a cell, counted in the persistent per-cell attempt ledger.
- `ipcfg::audit_board ?cell={}?`
  did anything move a value the board owns? Run beside audit_requirements. A board-owned value that changed after the
  ok: BOARD:OK BOARD:none   fail: BOARD_FAIL
- `ipcfg::audit_intent cell reqmap ?tolerances={}?`
  Tolerance-aware audit that also REPORTS within-tolerance keys Like verify_intent but returns a structured triple.
  ok: EXACT:
- `ipcfg::audit_requirements ?cell={}?`
  Audit the FINAL design against everything pinned: exact, unless the requirement itself carries a tolerance. Run this last, after the design validates.
  ok: REQ:OK REQ:empty   fail: REQ_FAIL
- `ipcfg::autofix_apply cell d ?console={}? ?maxpass=2?`
  Error-driven AUTO-FIX apply (confidence-bounded, console-aware) Tries the whole -dict fast path first. On failure, isolates per key and applies
  ok: AUTOFIX:applied
- `ipcfg::automation_config_schema rule_file`
  Automation rule CONFIG schema discovery Block-automation rules (ps_wizard, axi_noc2, visp_ss, cips, ...) REQUIRE an
  ok: SCHEMA:keys   fail: SCHEMA_ERR
- `ipcfg::automation_rule_options cell rule`
  Public: RULE_OPTIONS:keys={..} defaults={k v ..} legal={k {..} ..} schema=<text>
  ok: RULE_OPTIONS:keys   fail: RULE_OPTIONS_ERROR
- `ipcfg::board_bind cell binding ?extra={}? ?reason=board preset?`
  bind board interfaces to a cell and record what the board then owns binding : {CONFIG.<X>_BOARD_INTERFACE <board-interface-name> ...}
  ok: BOARD:bound   fail: BOARD_FAIL
- `ipcfg::board_enablers cell`
  candidate enablers: board-ish CONFIG properties that are not hooks Some IP gate the hook behind a flag, and until it is on the preset's values
- `ipcfg::board_hooks cell`
  the hooks this cell exposes (IP-agnostic)
- `ipcfg::board_owned ?cell={}?`
  The keys the board wrote for this cell (names only), for callers and gates.
- `ipcfg::cache_dump`
  Dump the learned IP-parameter cache as JSON ({} when unavailable).
- `ipcfg::cache_get ip feature`
  Learned-config cache (idea #3): consult-first / write-back Stores earned DISCOVERY facts only (param/shape/enabler/value_src/doc/version),
- `ipcfg::cache_put ip feature param shape enabler value_src doc ipver`
  Record one learned ip/feature -> parameter fact in the cache; returns CACHE_ERR on failure.
  fail: CACHE_ERR
- `ipcfg::capture path script ?expect={}?`
  Run a Tcl body once and write a .tcldict receipt with its result and native Vivado messages; the evidence record for experiments.
- `ipcfg::cell_dict_keys cell dictparam ?pattern={}?`
  nested-dict introspection straight off a BD CELL (no create_ip, no dump) The documented native path (param_deps -> param_dict) needs a MANAGED IP
  ok: CDK: CDK_NONE:   fail: CDK_ERR
- `ipcfg::check_enablers cell d`
  Phase 3 verification: detect INERT writes (gated attribute, flag still off) The failure verify_stuck cannot see. An attribute parameter whose feature is
- `ipcfg::check_value file param want`
  PRE-flight value check: validate BEFORE writing, not after Saves the whole failed-set round trip, and catches the class of bug where Vivado
  ok: VAL_OK: VAL_OUT_OF_RANGE: VAL_UNCHECKED:
- `ipcfg::classify_change want base cur ?validset={}? ?tolerance={}?`
  Exact-vs-miss classification Classify what happened to ONE key after an apply, using its pre-apply default.
- `ipcfg::cleanup cell ?orig_part={}?`
  Cleanup: remove STUB_* ports, delete the cell, restore part if needed
  ok: SKIP:cleanup SUCCESS:cleanup
- `ipcfg::clock_placement_status ?search={}?`
  Public: are the cells placed by the last (or a given) clock search at their required rates in the caller's design? Returns CLOCK_PLACEMENT:OK|PENDING|FAIL|ERROR
  ok: CLOCK_PLACEMENT:ERROR CLOCK_PLACEMENT:FAIL CLOCK_PLACEMENT:OK CLOCK_PLACEMENT:PENDING
- `ipcfg::config_diff cell baseline ?reqmap={}?`
  Full-config diff with change classification (idea #1 + #2) Compare a cell's current full config to a baseline snapshot. Optionally pass a
- `ipcfg::configure_feature cell vlnv feature intent`
  THE front door: identity -> discovery -> apply -> verify, in one call The agent supplies INTENT; this supplies the PROCEDURE. Every step that used
  ok: CF:FAIL CF:OK CF:PARTIAL
- `ipcfg::coverage_report ledger`
  Coverage report (partial-configuration disclosure, REQUIRED) Turns the Step 1.5 requirement ledger into a verdict + a list of prompt parts
- `ipcfg::create_cell vlnv cell`
  Create one BD cell from a VLNV if absent, refusing a VLNV this part does not support; counted as an attempt.
  ok: SUCCESS:created   fail: CONFIGURE_FAIL
- `ipcfg::create_cell_cfg vlnv cell d`
  Single-call create + configure (perf) Creates and parameterizes in ONE call via the create_bd_cell -set_param fast
  ok: SUCCESS:created+configured   fail: CONFIGURE_FAIL
- `ipcfg::dict_find file keyword`
  keyword search ACROSS dict sub-keys (the discover_params of nested dicts) This is the call that answers "what is the PCIe port-type key actually called".
  ok: DF: DF:none
- `ipcfg::dict_keys file ?dictparam={}?`
  dict SUB-KEY NAMES from the graph -- the names param_dict cannot see param_dict parses the RESOLVED value of a dict param, so it only ever reports
  ok: DK: DK_DICTS: DK_MISS:
- `ipcfg::discover_params vlnv keyword ?tmpdir=/tmp/ipcfg_disc?`
  Scoped standalone introspection (idea #4): COMPLETE param neighborhood On a throwaway standalone `create_ip` cell, return the CONFIG.* params whose
  ok: DISCOVER: DISCOVER_IPI_ONLY:   fail: DISCOVER_ERR
- `ipcfg::dump_params file ?keyword={}?`
  keyword neighborhood FROM THE DUMP (the dump-side discover_params) This is what lets the 2026.1 dump path REPLACE `discover_params` instead of
  ok: DUMP_PARAMS:CONFIG DUMP_PARAMS:none   fail: GR_ERR
- `ipcfg::enablement_ports file ?pat=*?`
  companion: list every port/interface whose gating the graph publishes Use when you know the behaviour but not how the IP spells the port, and to tell
  ok: PE_PORTS: PE_PORTS:none
- `ipcfg::ensure_part target ?force=0?`
  Phase 0: part swap / restore (close + reopen the bd around the change) Guarded: a part swap is destructive to a design-in-progress (existing IP may be
  ok: NOSWAP: SWAP: WARN:SWAP_BLOCKED
- `ipcfg::export_proven out_json entries`
  Write each proven cell's configuration to one JSON fragment plus a review-only Tcl copy beside it (<out>.tcl, never sourced). IP-agnostic.
  ok: EXPORT:OK   fail: EXPORT_FAIL
- `ipcfg::feature_flags cell words`
  Intent-side companion: which feature flags does this request need? check_enablers only fires when an ATTRIBUTE was written, so it cannot catch a
  ok: FLAGS: FLAGS:none
- `ipcfg::find_disabled output`
  Disabled/ignored scan: catch gated params that catch{} returns 0 for In BD mode a gated/disabled parameter is often NOT a hard error: Vivado emits
  ok: DISABLED:
- `ipcfg::find_params cell keyword`
  unified feature discovery on a BD cell: flat params AND nested sub-keys Answers the two questions that drive every configure in one call: what are the
  ok: FP:FLAT FP:NESTED FP_NONE:nothing   fail: FP_ERR
- `ipcfg::forget_board ?cell={}?`
  Clear the board-ownership ledger for one cell, or for all cells with no argument.
- `ipcfg::forget_requirements ?cell={}?`
  Drop the ledger for one cell (or all). Call between prompts in benchmark mode.
- `ipcfg::get_mode`
  Return the current configurator mode (for example assemble).
- `ipcfg::has_native feature`
  Convenience gate: ipcfg::has_native dump_param_deps | can_connect -> 0/1
- `ipcfg::help ?pattern=*?`
  Call shape, purpose and ok/fail result tokens for every ipcfg::/ipiasm:: helper matching a word.
  ok: HELP:none
- `ipcfg::ip_availability candidates ?part={}? ?maxparts=8?`
  Phase 1 (gate): part-aware availability over a CANDIDATE SHORTLIST vlnv_ok is a validator (is the name I already picked present?); this is a
  ok: IPAVAIL:   fail: IPAVAIL_ERR
- `ipcfg::is_system_intent text`
  System-intent heuristic (idea #5) Does the prompt describe a SUBSYSTEM realized through block automation / a
  ok: SYSTEM:
- `ipcfg::ledger_clear`
  Drop the current BD's entry: a new design, or one the plan just created.
- `ipcfg::ledger_entry`
  The current BD's recorded registries, or {} when none match this project and BD.
- `ipcfg::ledger_load`
  Restore this BD's board ownership, board overrides and requirement pins into a process that holds none of them. Idempotent per process and BD.
  ok: LEDGER:current LEDGER:loaded LEDGER:none
- `ipcfg::ledger_record section value`
  Persist one registry (section) for the current BD.
- `ipcfg::native_caps`
  Detect+cache native capabilities once per session. Returns a dict: version <s> dump_param_deps <0|1> can_connect <0|1>
- `ipcfg::param_block file param`
  targeted read: pull ONE param's block (Range/Enabled/Disabled/Default) Returns just that param's block (token-cheap), never the whole dump. Blocks in
  ok: PB_MISS:   fail: PB_ERR
- `ipcfg::param_deps ipname ?file={}? ?vlnv={}? ?tmpdir=/tmp/ipcfg_pd?`
  dump_param_deps wrapper: resolved ranges + Enabled/Disabled + dep graph Writes the (large) dump to a FILE; returns a one-line handle. The agent then
  ok: PARAM_DEPS: PARAM_DEPS_NO_MANAGED_IP:   fail: PARAM_DEPS_ERR
- `ipcfg::param_dict file dictparam ?subkey={}?`
  nested-dict introspection (PS/CIPS-class subsystem IPs) dump_param_deps emits per-param Range/Enabled/Disabled metadata only for
  ok: PD: PD_KEYS: PD_MISS:   fail: PD_ERR
- `ipcfg::param_format cell key`
  Value-format introspection: read a param back to learn its shape Use when a key exists but your value is rejected/ignored (e.g. a param that
- `ipcfg::param_range file param`
  the legal values for ONE param, straight out of the dump Blocks carry the range in one of two shapes:
  ok: RANGE_LIST: RANGE_NUM:
- `ipcfg::parse_range err`
  Parse a numeric legal range out of a Vivado error message Handles the common shapes:
- `ipcfg::pinned_drift cell`
  Pinned requirements on <cell> that no longer hold, as data: {items {<key>=<current>(want <value>) ...} restore {<key> <value> ...}}.
- `ipcfg::pinned_reset cell`
  Every pinned requirement on <cell> that no longer holds (typically after a board preset landed), with one restore dict so a single ipcfg::require repairs all.
  ok: PINNED_RESET:
- `ipcfg::plan file ?args...?`
  ONE-CALL discovery: the whole pre-write protocol against an existing dump Replaces the sequence the agent otherwise has to orchestrate by hand
  ok: PLAN:   fail: PLAN_ERR
- `ipcfg::port_enablers file port`
  AUTHORITATIVE gating: which params decide whether a port/interface EXISTS This is the ground truth that check_enablers can only GUESS at by name, and that
  ok: PE_MISS: PE_NONE:
- `ipcfg::preset_drift cell`
  Compare a board-bound cell's live values with its board preset; PRESET:OK or PRESET_DRIFT listing each changed key.
  ok: PRESET:OK PRESET_DRIFT:   fail: PRESET_CHECK_ERROR
- `ipcfg::probe_subkey_range cell dictparam subkey ?sentinel=999999?`
  per-sub-key RANGE via set-and-observe (the gap dump_param_deps can't fill) PROVEN on 2026.1_released ps_wizard: dump_param_deps gives sub-key NAMES+VALUES but
- `ipcfg::python_exe`
  Path of the Python interpreter the skills use (Vivado's bundled one when available).
- `ipcfg::python_exec ?args...?`
  Run the skills' Python with the given arguments and return its stdout; errors on a nonzero exit.
- `ipcfg::python_reset`
  Forget the cached Python runtime so the next call re-resolves it.
  ok: PYTHON:RESET
- `ipcfg::python_runtime`
  Resolve and cache the Python runtime dict (executable, root, source, version); raises PYTHON_RUNTIME_FAIL.
- `ipcfg::python_script script ?args...?`
  Run an absolute .py script path with arguments through python_exec.
- `ipcfg::python_status`
  One-line PYTHON:OK report of the resolved runtime, or the resolution error.
  ok: PYTHON:OK
- `ipcfg::range_from_log console subkey`
  stateless parser: pull a sub-key's legal range out of a captured console Pair with probe_subkey_range run under capture_log=true. Returns
  ok: RANGE: RANGE_NONE:
- `ipcfg::reconcile_disabled cell d`
  Disabled/gated param reconciliation (simpler than enabler-hunting) For params reported disabled/gated, decide which are already satisfied instead
  ok: OMIT:
- `ipcfg::require cell d ?reason={}? ?tolerances={}? ?board_override={}?`
  Requirement ledger: pin a spec value, write it, audit it at the end. A board-file value is refused unless board_override quotes the spec statement.
  ok: REQ:pinned+applied   fail: REQ_ERR REQ_FAIL
- `ipcfg::requirements ?cell={}?`
  Display the pinned requirements (per cell or all); use requirements_data for machine reads.
  ok: REQ:empty
- `ipcfg::requirements_data ?cell={}?`
  Dump the ledger (one line per requirement) for the named cell, or all cells.
- `ipcfg::resolve_subkeys cell userparam override ?internalparam={}? ?wantkeys={}?`
  GATING/derivation via set-and-observe: apply legal inputs, read resolved dict PROVEN on 2026.1_released: requesting {PS_USE_PMCPL_CLK0 1 PMC_CRP_PL0_REF_CTRL_FR
  ok: RSK: RSK_KEYS:   fail: RSK_ERR RSK_FAIL
- `ipcfg::resolve_vlnv hint`
  catalog-grounded IP identity (IP names change between releases) `vlnv_ok` only validates a VLNV you already picked, so it cannot rescue a name
  ok: VLNV: VLNV_CANDIDATES: VLNV_NONE:
- `ipcfg::restore_part orig ?force=0?`
  Guarded in the mirror image of ensure_part: restoring a part that cannot support a cell built on the swapped part silently DROPS that cell. The IP you
  ok: NOSWAP: RESTORED: WARN:RESTORE_BLOCKED
- `ipcfg::result_status raw prefixes`
  Classify a helper result string as PASS or FAIL against the expected success prefixes.
- `ipcfg::rule_for_vlnv dump vlnv`
  Automation rule enumeration (idea #5) The loaded Designer-Assistance rules ARE enumerable via the internal command
  ok: RULE:none RULE:xilinx
- `ipcfg::run_automation cell rule ?config={}? ?baseline={}?`
  Apply one bd_rule to a cell after checking the -config against the rule's live option schema; fills omitted keys with defaults.
  ok: AUTOMATION:OK SKIP:board   fail: CONFIGURE_FAIL
- `ipcfg::script_check path`
  Commands a script calls that do not exist in this session, found without running it: every ipcfg::/ipiasm:: name, and every lowercase word opening a [command su
  ok: SCRIPT_CHECK:OK   fail: SCRIPT_CHECK_FAIL
- `ipcfg::search_clock_config vlnv source targets evidence_dir ?args...?`
  Search clocking-wizard configurations until the exact target rates are proven; CLOCK_SEARCH:PASS with evidence under evidence_dir.
  ok: CLOCK_SEARCH:ERROR
- `ipcfg::set_bd_file f`
  Optionally pin the bd file name explicitly (else it is auto-detected).
  ok: SUCCESS:bd_file
- `ipcfg::set_mode m`
  Operating-mode control (benchmark vs assemble) In "assemble" mode the persistent design is being built, so destructive
  ok: SUCCESS:mode   fail: ERR
- `ipcfg::snapshot cell keys`
  Baseline snapshot: capture default values BEFORE configuring Call right after create_cell, passing the keys you are about to set.
- `ipcfg::snapshot_all cell`
  Full-config snapshot (idea #1) Capture the ENTIRE CONFIG.* dict of a cell in one pass -> {key val ...}.
- `ipcfg::source_reported path`
  Source a script; on failure print the message and its file/line trace before re-raising, so the caller sees where it failed, not only what was raised.
- `ipcfg::try_automation cell rule ?config={}?`
  Generic block automation (Designer Assistance) rule:   xilinx.com:bd_rule:<name> (from doc search; e.g. cips, microblaze,
  ok: SUCCESS:automation   fail: CONFIGURE_FAIL
- `ipcfg::verify_intent cell reqmap ?flag_no_change=1? ?tolerances={}?`
  Intent audit: catch a wrong-but-valid param that produced NO change verify_stuck proves a value persisted; verify_intent proves the value is
- `ipcfg::verify_stuck cell d`
  Phase 3 verification: detect keys that did NOT stick (silent revert) Returns "" if all stuck, else a list of offending keys with detail.
- `ipcfg::vlnv_ok vlnv`
  Phase 1: VLNV pre-validation (version-free prefix, e.g. xilinx.com:ip:axi_gpio) CATALOG MEMBERSHIP ONLY -- this is NOT a part-availability test. `get_ipdefs`
- `ipcfg::write_evidence path data`
  Write a data dict to a new absolute evidence file (refuses to overwrite).
