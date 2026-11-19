> Relay only: this is the `scripts/guard_mcp.py` relay's route (see [../README.md](../README.md)). The skill on the stock MCP never writes this Tcl.

Use one generic workflow for the catalog. The model maps user intent to discovered metadata; helpers perform discovery, data encoding, application, and verification. No per-IP helper or recipe registry is used. Catalog-wide correctness and latency remain to be evaluated.

The harness supplies an existing scratch project directory, target part, and guarded MCP. Raw lite_meta_*.txt dumps are helper internals: the optimized read profile denies them. Selected receipts are bounded; use describe/detail/gates to retrieve missing information. Only skill, read, Vivado start/execute/stop, and one doc-search tool belong in the model profile.

0. Choose the retrieval route before starting Vivado. If the harness declares a matching database, your first execute call uses `session_id="ipdb"`, `command='{"op":"info"}'`. Then discover the requested IP with `command='{"op":"ips","query":"<requested IP or function>","build":"<target SW build>"}'`. Use JSON, not Tcl, for this reserved session. Read [database.md](database.md) for selected parameter queries. Retrieve candidates for each requested feature, then exact-key receipts in batches of at most four. Keep the returned guard `evidence_path` for the requirement ledger. Without a database, start directly at step 1. A failed or mismatched database lookup falls back to live discovery.

1. Start a non-GUI Vivado session in the provided project directory. Use the configured installation. Pass the exact target part from the prompt and the exact start response's `log_file`:

   ```tcl
   source -notrace {<skill>/lib/lite.tcl}
   puts [lite::begin {<part>} {<log_file>}]
   ```

   Upstream library code is deployed beside this skill. Initialization derives scratch from the MCP log location and establishes a persistent BD, native capture, and an empty cache. On `INIT_FAIL`, correct the helper arguments and retry it; do not bypass initialization with project commands.
2. Extract all requested features, retaining the original prompt fragments. Inspect only their metadata neighborhood:

   ```tcl
   puts [lite::inspect_for {<catalog-IP-hint>} <requested-cell> {<feature-keywords>}]
   ```

   Use a catalog name or VLNV as the hint and a Tcl list of short feature words without numeric values or compound prose (space-separated, without commas), rather than the whole request sentence. Ambiguity returns bounded candidates; choose an identity explicitly and page with lite::catalog when needed. This resolves the live VLNV, creates only the requested default cell, and returns candidate properties, display labels, types, defaults, ranges, unmatched keywords, and a selected-detail evidence path. bd_property_exists distinguishes live BD properties from native-only metadata. live_dict_candidates lists dictionary-shaped BD values; neither proves semantic mappings. Full native dumps stay in scratch. Use explicit paging when `more` is true. Inline enum labels show the published encodings. Use `lite::describe` for selected parameter semantics, `lite::detail` for exact dictionary subkeys, and `lite::gates` for port/interface dependencies (see the operation reference); never load the whole catalog or dependency dump. Initialization and the first inspection can share one execute call.
3. Before mapping values, use exact-key database receipts already retrieved, or retrieve live help for candidate keys in batches of at most four:

   ```tcl
   puts [lite::describe {<resolved-VLNV>} {CONFIG.<candidate-key> CONFIG.<other-key>}]
   ```

   Do not repeat live describe for complete database help. Use live describe/detail/gates for missing semantics, truncated information or configuration-dependent ranges. Live inspect_for still establishes BD writability. This joins purpose, defaults, ranges and enum labels without opening a GUI. Use the help to distinguish candidates and the published encodings to choose values; cite the returned evidence path in the requirement ledger. Read full selected help only when `help_truncated` may hide a relevant condition. Page enums when incomplete. `DETAIL:PACKAGED` is useful semantic evidence, but its choices are not configuration-dependent live ranges and native-only keys are not BD write properties. If discovery missed a feature, search another short term from its meaning or inspect published dependencies; do not drop the feature. Query the docs MCP only for missing help, ambiguous mappings, dependencies, units or architectural constraints, with limit=3 initially. A label or a numeric range alone does not establish purpose. Leave unresolved features explicit. For missing native metadata and dictionary operations, use [operations.md](operations.md).
4. Construct a configuration dict and a nonempty requirements list covering every original feature. See [operations.md](operations.md) for the shared scalar/vector/dictionary contract. The fields are `id`, `text`, `key`, `wanted`, and `doc`; nested requirements also use `subkey`. All keys and encodings come from runtime metadata/docs.

   ```tcl
   set requirements [list [dict create id feature_1 text {<original prompt fragment>} \
       key CONFIG.<discovered-key> wanted {<encoded requested value>} \
       doc {<selected metadata receipt path or document citation>}]]
   set config [dict create CONFIG.<discovered-key> {<encoded requested value>}]
   puts [lite::configure {<IP-hint>} <cell> $config $requirements]
   ```

   Build the complete list before this first call. wanted is the requested encoded value, not a pass/fail flag; doc cites grounding, not a copy of the request. The helper pins intent and baseline, captures native diagnostics, applies and verifies. Use generic vector operations to preserve length and unchanged positions. Dictionary updates preserve unrelated known subkeys. Do not generate catalog filters, guess keys, or construct discovery scripts.
5. On `CFG:OK`, save and verify:

   ```tcl
   puts [lite::finish <cell>]
   ```

   FULL requires complete mapped intent, successful capture, verified readback, and successful save. Keep the session open for the next cell; stop it with `close_vivado=true` only when the user is done with the design. Configuration verification does not establish a connected or timing-closed system.
6. On `CFG:FIX`, inspect only failed requirements or named current-run evidence. Correct the configuration without weakening pinned intent. Two identical failures prevent another unchanged attempt; configure recovery stops after three attempts. Finish partial at the wall budget or `CFG:STOP`. See [FALLBACK.md](fallback.md).

Keep the same generic path for every family. Read only the relevant metadata, operation reference, or current-run diagnostic. Never seed answers from previous runs, reload changed code, or rename/recreate cells to evade retry limits. Omit doc-search's optional tool filter or pass an array. The guard rejects incomplete Tcl before forwarding; it is not a full syntax or semantic validator.
