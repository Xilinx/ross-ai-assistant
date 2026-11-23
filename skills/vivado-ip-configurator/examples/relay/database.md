> Relay only: this is the `scripts/guard_mcp.py` relay's route (see [../README.md](../README.md)). The skill on the stock MCP never writes this Tcl.

# Optional IP database

This is a declared, versioned source-metadata cache, not a solved-test corpus. Supply `--database-config <snapshot-runtime.json>` to `scripts/guard_mcp.py` and mount the snapshot, sqlite-vec dependency and pinned embedding model read-only. The structured workflow retrieves this automatically on the owned live session. `--profile structured` exposes compact start/execute/stop schemas; the legacy profile retains the original tool interfaces and manual lookup route below. Without a matching snapshot, use live discovery.

## Release database shipped with Vivado (2026.2 and later)

Vivado 2026.2 ships its own database at `$XILINX_VIVADO/data/ip/ipdb/ipdb.sqlite`. When no `--database-config` is given, the structured workflow adopts that file automatically once `begin` has reported the session's install; nothing needs to be mounted. An explicit `--database-config` snapshot still takes precedence. Releases that ship no such file (2026.1 and earlier) use live discovery, exactly as without a database.

This file has a different layout (schema `1.7-merged`) and is read by `scripts/merged_database.py`. The ops and reply fields below are the same, with these differences:

- **Release gate, not build gate.** The file is extracted from an earlier build of its release than the install that carries it, so it is accepted when its `vivado_version` matches the running release. A build difference is reported as `build_scope`/`live_build` in the reply, never as `SNAPSHOT_MISMATCH`.
- **Lexical only.** It has no embeddings: `hybrid` runs as FTS5 lexical ranking (the reply says `mode: lexical`) and `semantic` is refused. No sqlite-vec or model is loaded.
- **Ranges are not stored.** `range` is always `RANGE_NONE`; live readback supplies ranges.
- **Recorded prerequisites.** Where the release recorded which assignment enables a value (for example the ps_wizard `MMI_CONFIG` XGUI rules), `parameters`, `lookup` and `field_hints` add `enablement_requires` with `enablement_source_scope`. Treat it as a suggestion confirmed by live readback, never as legality proof.
- `field_hints` resolves a dictionary field (such as a `MMI_CONFIG` key) through the subcore parameter that carries its help and enum, when the request includes the parent `vlnv`.

For at most 24 source parameters, `begin` can return the complete live-cell candidate table when its compact representation fits 10 KB. This deterministic lookup bypasses ranked search and embedding inference. Larger tables are explicitly unavailable as a complete receipt and use bounded discovery. This threshold is generic and does not dispatch on IP names.

## Query before launching Vivado

Use the existing Vivado execute tool with reserved `session_id="ipdb"`. Its command is **JSON data**, not Tcl. The guard handles it locally and never forwards it to Vivado. No Vivado start is required for these lookups.

Get the snapshot identity:

```json
{"op":"info"}
```

This reports the database build; it does not prove that build is the installed or requested target. Use the build specified by the target environment/prompt and check compatibility before using its metadata. A mismatch requires another matching snapshot or live fallback.

If the user describes a function, discover IP candidates first:

```json
{"op":"ips","query":"<requested function>","build":"<target SW build>","mode":"hybrid"}
```

The response has at most three candidates. Exact names resolve deterministically; hybrid search combines FTS5 lexical ranking with normalized MiniLM embeddings via sqlite-vec cosine search. IP discovery searches catalog purpose, interfaces and a bounded sample of source parameter labels. It does not search thousands of parameter hits as competing IP candidates. Similarity is not suitability or probability. Resolve interface/device ambiguity explicitly; compatibility is currently marked unvalidated for the requested part.

After choosing an exact VLNV, find candidate parameters:

```json
{"op":"parameters","vlnv":"<chosen exact VLNV>","query":"<one requested feature>","build":"<target SW build>","part":"<target part>"}
```

For exact keys:

```json
{"op":"lookup","vlnv":"<chosen exact VLNV>","keys":["CONFIG.<key>"],"build":"<target SW build>","part":"<target part>"}
```

At most four parameter receipts are returned. Inline help/default/range fields are capped at 240 characters and choices at six. Truncation and missing evidence are explicit. The returned evidence IDs refer to source records in the declared database. Query exact keys after semantic selection; never write a guessed key or accept a similarity score as meaning or legality proof.

## Scope and fallback

Native range records are default-instance metadata and apply only to their recorded part. For another part, lookup uses packaged information and marks `packaged_not_live`. GUI tooltips are captured-instance samples with part and extraction provenance; full historical capture configuration fingerprints were not recorded. They help explain candidate parameters but do not establish a configuration's live range or enablement. Missing help remains unavailable.

The builder also reads a conservative subset of literal `ipgui::add_*` and `set_property tooltip` declarations in packaged XGUI Tcl. It never evaluates that Tcl. This adds source group/page context and tooltips with scope `packaged_gui_literal_not_live`; dynamic, encrypted or unsupported definitions remain unavailable. These declarations are distinct from tooltip text captured through a live GUI getter. Source paths and hashes are retained, and live ranges/readback remain authoritative.

Start the ordinary live Vivado session for actual configuration. Use inspect_for to establish live BD writability, describe/detail/gates for missing or dependent evidence, the requirements ledger for all requested features, and finish plus independent saved readback for validation. Use docs for architectural constraints and missing semantics. The database never returns configuration recipes or a final acceptance verdict.

## Build and command-line use

The generic `scripts/ip_database.py` builder reads a live catalog/default-metadata TSV export and packaged IP-XACT XML. Optional GUI imports are an explicit manifest of `{vlnv, part, configuration, parameters}` records containing only verified tooltip text and getter provenance. Every imported key must exist in the snapshot. Source SHA-256 fingerprints, build, scope, model snapshot/weight fingerprint and extension version are retained.

Runtime JSON supplies three absolute paths: `database`, `dependencies` (the pinned sqlite-vec Python package directory), and `model` (an already-local MiniLM snapshot). Database construction refuses outputs under /home and existing snapshots. Search opens SQLite read-only, loads only the configured extension, validates model/extension identity and performs no model downloads. The plugin is sqlite-vec, not an additional MCP schema.

```text
python3 scripts/ip_database.py --config <runtime.json> build --catalog <catalog.tsv> --captures <gui-captures.json>
python3 scripts/ip_database.py --config <runtime.json> query '<JSON request>'
python3 scripts/ip_database.py --config <runtime.json> check
```

Set TMPDIR, Python caches and HF_HOME to scratch. Package/update dependencies separately with a pinned version and a scratch pip target/cache. Refresh to a new snapshot after source/build/model changes; do not silently reuse another build or replace an immutable benchmark input. Isolated benchmarks must explicitly declare and mount this source-metadata database; exclude all prior run answers and grader files.

Generic export (create output directory first and set HOME/TMP/cache to scratch):

```text
vivado -mode batch -nojournal -log <root>/native.log -source scripts/export_database.tcl -tclargs <root> <part> <IP-name-or-exact-VLNV> ...
```

Output is `<root>/catalog.tsv`. Catalog-visible versions/hidden definitions are retained; failed native extraction remains unavailable. Keep target identities outside runtime helper code.
