> Relay only: this is the `scripts/guard_mcp.py` relay's route (see [../README.md](../README.md)). The skill on the stock MCP never writes this Tcl.

# Shared metadata and configuration operations

## Discovery

lite::inspect_for hint cell keywords ?limit=6? ?offset=0? combines captured default-cell creation, native discovery and live BD visibility. Candidates marked bd_property_exists=0 are metadata sources, not BD write/read properties. live_dict_candidates shows dictionary-shaped live values; documentation must establish their meaning. Sparse native metadata falls back to bounded live discovery.

For ambiguous human names, lite::catalog hint ?limit=6? ?offset=0? presents ranked live candidates. Ranking matches words in catalog names and never selects an approximate identity automatically. Choose using the request and docs.

`lite::inspect hint keywords ?limit=6? ?offset=0?` uses the current Vivado release/build, project part, and catalog VLNV to cache extracted metadata only within this run. Keywords match native parameter names, display labels and descriptions. Discovery trims accidental trailing commas from Tcl list items and reports normalized keywords; compound phrases are not expanded into semantic synonyms. It returns candidate counts, explicit paging, unmatched keywords, nested-key evidence and a selected-detail file. `lite::detail hint {CONFIG.<key> ...} ?subkeys? ?enum_offset=0?` returns up to four exact parameter summaries and twelve exact dictionary values. Enum labels are paged six at a time; large fields carry explicit truncation flags. Advance enum_offset when needed. Avoid reading large default dictionaries into context.

`lite::gates hint keywords ?limit=6? ?offset=0?` searches published port/interface names and reuses upstream direct-predecessor extraction. It returns controlling parameters, not the values needed to enable them: inspect their metadata/docs. A missing graph or no published gating remains unavailable, not proof that the feature is ungated. Parameter and nested discovery both report paging.

The JSON adapter calls this helper automatically during each `describe`, and when constructing a complete small catalog from the requirement text. It matches up to four ports and adds at most four new direct control candidates alongside the original candidates, without replacing them. Exact port/interface names rank before their signal variants; matches covering more query keywords rank before matches covering fewer. This prevents one family of signal variants from crowding another requested interface out of the result. Repeated keyword queries reuse current-cell graph results. Every returned control retains its port names and `dependency_scope=direct_native_predecessor`; `port_dependencies` refers to the ordinary candidate IDs, so the existing select/apply interface is used unchanged. Native selected describe supplies current types, defaults, ranges and paged enum labels, with exact same-IP database metadata filling available help. Only parameters present as live properties or uniquely mapped scalar dictionary fields can become controls; native application validates writes. Derived contributors, unresolved names, missing publication and truncation are explicit; no enable value is inferred or written automatically. `more`, `controls_more` and `keywords_more` require a more precise query when a requested port/control is missing. Boolean parameters may have no published enum/range; their native Boolean type is preserved and missing ranges remain `RANGE_NONE`. Native graph evidence takes precedence over identifier-family heuristics. Graph extraction and complete metadata remain outside the model context.

Use discovery to obtain exact keys, then call describe in batches of at most four before choosing values. Reuse its evidence path for requirements whose semantics are supported by the returned help. Use detail for dictionary subkeys and gates for published dependencies. Query docs for the remaining missing fact, rather than repeating a broad product search. A numeric range alone does not explain a parameter's meaning.

`lite::describe hint {CONFIG.<key> ...} ?enum_offset=0?` (also accepts separate exact keys, retaining the four-key limit) combines help with the existing native range/detail helper for up to four exact keys. `lite::detail` also returns `help` and `help_source`. Native descriptions take precedence; otherwise the helper reads selected top-level parameter descriptions from the live catalog's IP-XACT XML using Python 3. It does not open a GUI, load prior-run tooltip data, or add a model-facing tool. Use the resolved subcore VLNV for subcore help; no parent-to-subcore mapping is assumed.

If managed-IP metadata is unavailable, describe returns `DETAIL:PACKAGED`: packaged help, type, default and paged enum choices. `range_source=packaged_choices` is catalog evidence, not a configuration-dependent live range. Missing numeric bounds return `RANGE_NONE` / `range_source=unavailable`; never infer bounds from a default or type. `range_complete=0`, `more=1` or truncation requires another page/full selected evidence before treating a legal-value list as complete. `native_only=1` means the CONFIG-prefixed name is a metadata key, not a verified BD write property. Use live discovery/readback to establish the actual write mapping.

Help is capped at 240 characters inline and marked `help_truncated`; the selected evidence retains full help. Retrieve the full selected help if a condition may have been omitted. Empty help stays unavailable and requires docs or an available GUI path.

When an actual Tcl IP GUI will be opened, `lite::watch_gui_help hint live_ip_interpreter {CONFIG.<key> ...}` installs a selected-parameter tooltip watcher before first customization. The caller must supply the native interpreter for the same resolved IP instance; the returned instance name is provenance, not a parent-to-subcore mapping. Opening the actual GUI is a separate operation: the helper does not launch it, click controls, or assign tooltips. Then `lite::describe` / `lite::detail` join captured native getter text with the existing ranges, marking `help_source=gui_tooltip`, `help_instance`, and `help_scope=gui_instance_at_capture`. Only plain tagged text survives; the helper does not retain GUI object handles or query stale native scopes. A replacement GUI interpreter, absent setter trace, missing help or failed getter can leave help unavailable. It does not automate the experimental Java extraction needed for the tested stock PS11/PMC GUIs; use their verified packaged descriptions first. GUI-derived text remains a captured sample; ranges from the native helper remain marked as default-instance ranges.

Before closing the dialog, call `lite::selected_gui_help hint` to snapshot the selected text into the main current-run cache; then describe can join it even if native range extraction replaces the IP interpreter. Detail snapshots available help before invoking native extraction too. New watches replace old snapshots, and build/part/VLNV changes exclude stale entries. This is mechanically captured help, not cross-run baseline seeding.

When native extraction is unavailable:

```tcl
puts [lite::open_cell {<catalog-hint>} <cell>]
puts [lite::inspect_cell <cell> {<feature-keywords>}]
```

This creates the requested default cell and finds relevant live properties/subkeys. It does not invent missing ranges/dependencies. Use documentation and report unresolved mappings if necessary.

## Requirements and values

`lite::nested_schema hint cell` extracts scalar fields of live dictionary properties and their exact same-name `<parent>_INTERNAL` resolved dictionaries into a current-run evidence file. The model receives only container counts and up to four ranked fields per JSON `describe`, with opaque IDs bound to the parent and subkey. Full metadata stays outside the model context. Exact interface identifiers rank ahead of broad help matches. Whole-container replacements are rejected by the JSON adapter; selected fields become a merged partial dictionary with one pinned native readback requirement per field.

Fields already present in a live dictionary have `mapping_scope=live_dictionary`. A field found only in its same-name native resolved dictionary has `mapping_scope=native_same_name_candidate`: this naming convention is provisional, and is checked against fresh native metadata before the write and against capture/readback afterward. `lite::native_field_mapping` rejects differently named native containers. It does not establish a general mapping to arbitrary subcores, renamed dictionaries or deeper nested values. A container named `CONFIG.<NAME>_CONFIG` may also take candidate fields from a catalog definition named `<name>` (`lite::sibling_definition`), but only when at least 30% of that definition's user parameters already appear in the container; these rows have `mapping_scope=sibling_definition_candidate` and `source_vlnv`, packaged defaults/help, and `range_scope=sibling_definition_not_parent_live_range`. Before writing, `lite::sibling_field` re-checks that the same definition still resolves and packages the field; readback then decides success. Small inner dictionaries (`shape=dictionary`, `fields`) merge the wanted keys into the current value, and readback compares only those keys. The CIPS and ps_wizard scratch probes confirm their tested same-name containers and resulting pins; other IPs still require successful native validation.

SQLite `field_hints` joins exact field names to packaged labels, help, types and published ranges from other definitions, for at most sixteen names. These hints improve field selection but retain `hint_scope=exact_name_in_other_definitions_not_parent_binding` and `range_scope=standalone_definition_not_parent_live_range`; they cannot create a parent write binding or establish configuration-dependent parent constraints. No additional MCP tool schema is required.

`lite::configure hint cell config requirements` accepts only existing CONFIG properties. Each requirement is a dict:

```tcl
set requirements [list [dict create id feature_1 text {<original prompt fragment>} \
    key CONFIG.<discovered-name> wanted <documented-value> doc {<metadata/doc grounding>}]]
set config [dict create CONFIG.<discovered-name> <documented-value>]
```

Values are grounded in the parameter's type, units and enum display labels. The helper reuses published native scalar ranges/enums for preflight; absent ranges and nested/vector semantics still require grounding. Pin the complete request before the first write. Empty desired strings are allowed, but IDs, prompt fragments and grounding must be nonempty. Unmapped features use an empty `key` and cannot pass final coverage.

When one key aborts an otherwise legal batch, the adapter may call `lite::configure_subset` internally. The helper retains the complete immutable ledger, proves that every subset entry belongs to it, and transactionally applies/readbacks only the subset keys. A successful subset is saved before a deferred-key retry. If all deferred writes later pass, `lite::verify_pinned` promotes the result only after the complete original ledger reads back; otherwise final coverage remains partial. These are internal recovery APIs and do not add model-facing tools or permit requirements to be dropped.

For comma-separated vectors, preserve the existing length and update explicit zero-based positions:

```tcl
set current [get_property CONFIG.<discovered-vector> [get_bd_cells <cell>]]
set changed [lite::vector $current {0 <wanted-value> 2 <wanted-value>}]
```

Use optional encoding `boolean` only when metadata/docs establish a Boolean vector. It translates 0/1/true/false to true/false. Other encodings remain explicit documented values. Bounds are checked. An enable mask requires mapping every requested enabled/disabled position; writing frequencies does not establish that those positions are enabled.

For dictionaries, write the partial subkey dict and use one requirement for each requested subkey. `lite::configure` validates known subkeys and merges updates without losing unrelated entries. A subkey absent from the current value must be published by the native dictionary graph; an ungrounded key is rejected. Where documentation/metadata establish a derived readback container or renamed normalized subkey, pin `read_key` and `read_subkey` as well. A mapping to a different property must be grounded, not inferred to make verification pass. If the write subkey is absent from both the current write dictionary and its published graph, provide write_doc with a retrieved citation establishing the write mapping. Use native_key and optional native_subkey for a known resolved entry in managed-IP metadata, or read_key/read_subkey for a known derived entry actually present on the BD. The native_subkey field defaults to subkey. A native_key is evidence only: readback uses the written BD property. Do not copy a native-only name into read_key. Readability alone does not establish writability. The helper checks that entry and verifies the result after the write; it does not interpret a citation's semantics for the model.

`lite::finish cell` independently reads pinned requirements and saves the project. It returns FULL only with successful capture and save and no failed requirement. An unmapped or missing setting remains PARTIAL. Do not hand-author a coverage verdict.
