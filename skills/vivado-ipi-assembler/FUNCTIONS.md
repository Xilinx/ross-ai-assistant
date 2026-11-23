# Function index: vivado-ipi-assembler

Generated from the code by `vivado-ipi-assembler/tests/support/build_function_index.py`;
do not edit by hand. This is the complete list of what this skill can call. Read it
instead of `lib/*.tcl` or `scripts/*.py`: the source adds nothing a caller needs.

## Build flow (`ipiasm::api`)

```text
STEPS (one notebook stage at a time; ipiasm::stage <name> first, ipiasm::checkpoint <tag> last)
 0  spec       -> SPEC_OK       Stage 2 only: preflight_connect sync_intf_freq_hz reconcile_spec,
                                then assemble_from_plan <abs-plan> <stage> for every later stage
 0a plan       -> PLAN_OK       scratch: binding probe, ipcfg::create_cell_cfg experiments,
                                ipcfg::export_proven; compile the plan to ASSEMBLY_PLAN:OK; close scratch;
                                then assemble_from_plan <abs-plan> opens the 'plan' gate (create_cell
                                and the build stages refuse until it is open)
 0b board      -> BOARD_OK      register_board_repo discover_board_parts require_board_preset
                                board_interfaces; create the processing system, apply its
                                board preset, ipcfg::preset_drift; attach_board_memory
 1  cells      -> CELLS_OK      create_cell region_selectors ensure_semantic_cell_name
                                check_board_hooks check_memory_regions check_cell_config
 1b inventory  -> INVENTORY_OK  gate_inventory check_ps_fabric_irqs set_irq_contracts
                                check_irq_contracts
 2  clocks     -> CLOCKS_OK     preflight_clock_plan wire_clk_rst_auto set_clock_contracts
                                save_clock_contracts load_clock_contracts
                                ensure_reset_domain wire_mem_noc_sysclk check_mem_noc_sysclk
                                check_interface_clock_domains set_interface_contracts
                                preflight_interface_contracts check_mipi_video_clocks
                                check_undriven_clocks create_port check_required_ports
 3  connect                     externalize_interface create_port connect_external_axi connect_axi_auto
                                check_ps_noc_masters name_port
                                build_control_fanout add_noc_si attach_aie_config
                                check_aie_config check_required_reachability check_irq_paths
                                check_camera_isp_format attach_noc_port_clocks
                                check_undriven_clocks
 3b qos        -> QOS_OK        bw_video bw_mipi bw_axi qos_classes set_unused_qos_routes
                                apply_qos_plan set_qos check_noc_pre_address check_memory_subsystem
 4  address    -> ADDRESS_OK    place_named_windows auto_address restrict_master
                                enforce_window_scope check_noc_post_address check_overmap
                                check_noc_legality check_visibility check_required_dram_masters
 5  validate   -> VALIDATE_OK   normalize_sim_model gate_inventory close_gaps declare_open
                                check_wrapper_boundary check_required_ports validate_bundle,
                                then check_clock_contracts (needs a validated design)
                                final_contract_gate_from_plan (plan-built) or
                                final_contract_gate; assembly_report
 6  deliver    -> DELIVERED     delivery_guard assert_delivery_context
Each plan stage carries the checks its own design needs: an IP check only when the design
has that IP, a contract check only when the spec states that contract, and never in a
stage before the one that does the work it checks. The plan compiler enforces this when
given the spec.
Status at any point: gate_status, timings, stage_execution_status.
ipiasm::api <word> for call shapes and results; the rules stay in SKILL.md.
```

## Tcl helpers: `ipiasm::` (151)

Load the library in the Vivado session first. `ipcfg::help <word>` prints the same entry live.

`ipcfg::` helpers are in `../vivado-ip-configurator/FUNCTIONS.md`.

- `ipiasm::add_addr_seg space seg name offset range`
  targeted segment: place ONE slave seg into a master's space Used for the spec's named windows (PCIe BAR2/BAR3, BRAM mailbox, ctrl regs).
  ok: SUCCESS:seg   fail: ADDR_FAIL
- `ipiasm::add_noc_si noc_cell dest ?master={}? ?rd_bw={}? ?wr_bw={}?`
  Add a NoC slave interface for one PL master and route it to dest with read/write bandwidth; the port gets its own aclk on the master's clock.
  ok: SUCCESS:noc   fail: NOC_FAIL
- `ipiasm::api ?pat={}?`
  The build flow on one screen: each stage's gate token and the helpers its SKILL.md section uses, in order. With a pattern, the matching ipiasm::
- `ipiasm::api_inventory ?pattern=*?`
  Dict of every public ipcfg::/ipiasm:: proc matching a glob to its argument names and defaults.
- `ipiasm::api_preflight calls`
  Check a list of {procedure argument-list} calls exist with legal arity before running any; returns a dict with status PASS/FAIL, checks and failures.
- `ipiasm::apply_qos_plan plan`
  apply a whole traffic specification plan rows: {si_pin dest read_bw write_bw ?class? ?burst?}
  ok: QOS:applied   fail: QOS_FAIL
- `ipiasm::assemble_from_plan plan_path ?stage={}?`
  Compile an assembly_plan.json and execute one named stage (or all), recording a capture receipt per operation.
  fail: ASSEMBLY_PLAN_FAIL
- `ipiasm::assembly_report ?validate_out={}?`
  assembly coverage report: what is NOT yet a working design Live-introspects the persistent BD for the gaps that matter for a buildable
  ok: ASSEMBLY:
- `ipiasm::assert_delivery_context expected_bd expected_project_dir`
  Prove that the session exposed to the caller still holds the intended final project and BD. A successful probe project is not a delivered system.
- `ipiasm::attach_aie_config aie noc_master ?noc_clk={}?`
  wire an AIE configuration slave from a caller-selected legal NoC master noc_master is a MASTER interface pin on an axi_noc. Do NOT point it at the
  ok: SUCCESS:AIE   fail: AIE_FAIL
- `ipiasm::attach_board_memory noc_cell ?want={}? ?regions={}?`
  Bind a memory NoC to the board's LPDDR interfaces, optionally selecting regions from DesignSpec {name base size} dicts.
  ok: SUCCESS:board_memory   fail: MEM_ATTACH_FAIL
- `ipiasm::attach_noc_port_clocks ?pattern={}?`
  give every wired NoC AXI port an aclk driven by its peer's clock The ingress builder attaches a clock as it creates each S*_AXI, so slave-side
  ok: NOC_CLK:OK   fail: NOC_CLK_FAIL
- `ipiasm::auto_address`
  automatic assignment of all unmapped masters assign_bd_address returns rc=0 whether or not it mapped anything, and the
  ok: SUCCESS:auto_address   fail: ADDR_FAIL
- `ipiasm::autofix domain_map ?windows={}? ?oos_pins={}?`
  Orchestrator: confident fixes + the escalation question set Runs the confident fixers, then scans for the design-specific sources that must
  ok: AUTOFIX:fixed
- `ipiasm::autofix_address ?windows={}?`
  CONFIDENT address assignment; leftover unmapped -> escalate windows: optional list {space seg offset range space seg offset range ...} of
  ok: AUTOFIX_ADDR:placed
- `ipiasm::autofix_domains domain_map`
  CONFIDENT clock/reset wiring from a known domain map domain_map: {cell mhz cell mhz ...} (each functional cell's intended domain,
  ok: AUTOFIX_DOMAINS:fixed
- `ipiasm::begin_design`
  Start a fresh assembly context for the currently open project/BD, clearing all in-memory state from another design.
  fail: ASM_FAIL
- `ipiasm::board_interfaces ?pattern=*?`
  what the selected board file offers Each board interface names the IP it prefers (PREFERENCES) and the preset it
  ok: BOARD_INTFS: BOARD_INTFS:0
- `ipiasm::build_clocks domains ?wiz_vlnv=xilinx.com:ip:clk_wizard? ?rst_vlnv=xilinx.com:ip:proc_sys_reset? ?wiz_cell=ipiasm_clk_wiz?`
  build one clock wizard + one proc_sys_reset per domain domains: a list of {name freq_mhz ...} pairs OR {name freq reset_cell} triples
  ok: SUCCESS:clocks   fail: CLK_FAIL
- `ipiasm::build_control_fanout master slaves ?sc_cell=control_sc? ?sc_vlnv=xilinx.com:ip:smartconnect? ?clk_pin={}? ?rst_pin={}?`
  1b. Common control-plane pattern: one master -> N control slaves Validated against PG247 (SmartConnect): a single SI can fan out to many MIs
  ok: SUCCESS:control_fanout   fail: CTRL_FAIL
- `ipiasm::bw_axi freq_mhz data_width_bits ?utilization_pct=100?`
  bandwidth ceiling of an AXI port, in MB/s MB/s = clock_MHz * (data_width_bits / 8) * utilisation. Use this for the
- `ipiasm::bw_mipi lanes line_rate_mbps`
  bandwidth of a MIPI CSI-2 D-PHY link, in MB/s MB/s = lanes * line_rate_Mbps / 8. Use this when the spec names a camera but
- `ipiasm::bw_video w h fps bpp ?overhead_pct=10?`
  bandwidth of an uncompressed video flow, in MB/s The pixel-rate arithmetic behind every camera/ISP/display path:
- `ipiasm::camera_control_note`
  is there a sensor-control channel per camera? "Per-tile IIC and GPIO for sensor control" means one channel of each kind per
  ok: CAMCTRL: CAMCTRL:none
- `ipiasm::camera_retune_note`
  can software still retune the cameras once the design is running? Three switches make lanes, virtual channels and datatypes settable at runtime:
  ok: RETUNE: RETUNE:OK RETUNE:none
- `ipiasm::ced_fit out`
  does the recorded CED meet the request? Runs scripts/ced_fit.py on <out>/ced_reference.json and <out>/ced_checklist.json, writes
  fail: CED_FIT_FAIL
- `ipiasm::ced_reference ced out_dir ?options={}?`
  ground truth from a matching CED Instantiate one of the release's CEDs (the best match from reference_search) in its own
  ok: CED_REF:OK   fail: CED_REF_FAIL
- `ipiasm::check_aie_config`
  gate: is every AIE reachable, and reachable by a legal route? Cheap and structural: no validate, no NoC compiler. It catches the automation's
  ok: AIE:NA AIE:OK   fail: AIE_FAIL
- `ipiasm::check_board_hooks`
  was the design bound to the board it is being built for? `ipcfg::audit_board` holds a run to the preset it bound. This answers the
  ok: HOOKS:OK HOOKS:none   fail: HOOKS_FAIL
- `ipiasm::check_bram_backing`
  Check every AXI BRAM controller has connected backing memory of the required width and capacity.
  ok: BRAM:OK   fail: BRAM_FAIL
- `ipiasm::check_camera_geometry ?requirements={}?`
  is each ISP tile set up for the frame its camera delivers? requirements is {receiver width height ...} from the DesignSpec. A receiver
  ok: GEOMETRY:NA GEOMETRY:OK GEOMETRY:none   fail: GEOMETRY_FAIL
- `ipiasm::check_camera_isp_format`
  Check the camera receiver's stream format matches the ISP tile input it feeds (or will feed before wiring).
  ok: FORMAT:OK FORMAT:none   fail: FORMAT_FAIL
- `ipiasm::check_camera_line_buffer`
  is each camera's line buffer deep enough for the line it carries? CSI_BUF_DEPTH holds one line, so it has to reach the tile's horizontal
  ok: LINEBUF:OK LINEBUF:none   fail: LINEBUF_FAIL
- `ipiasm::check_cell_config cell desired`
  Read back a dict of CONFIG.* values on one cell and fail on any mismatch; CELL_CONFIG:OK.
  ok: CELL_CONFIG:OK   fail: CELL_CONFIG_FAIL
- `ipiasm::check_clock_contracts`
  Re-verify every registered clock frequency and reset domain on the live design; CLOCK_CONTRACTS:OK or CLOCK_CONTRACT_FAIL.
  ok: CLOCK_CONTRACTS:NA CLOCK_CONTRACTS:OK   fail: CLOCK_CONTRACT_FAIL
- `ipiasm::check_connect src dst`
  Pre-flight one connection. src/dst are "cell/PIN" (scalar or interface) paths. Returns LEGAL:1 | ILLEGAL:<reason> | NATIVE_NA | CC_ERR:<e>.
  ok: ILLEGAL: LEGAL:1   fail: CC_ERR
- `ipiasm::check_feasibility checks phase sources expected_fingerprint`
  Check scratch-proven feasibility entries owned by one phase against the live design and matching fingerprints.
  ok: FEASIBILITY:OK   fail: FEASIBILITY_FAIL
- `ipiasm::check_interface_clock_domains`
  Every connected SmartConnect AXI boundary with resolvable metadata must match its peer. A hierarchy boundary may legitimately omit interface metadata; that
  ok: CLOCK_DOMAIN:NA CLOCK_DOMAIN:OK   fail: CLOCK_DOMAIN_FAIL
- `ipiasm::check_interface_routes edges`
  Judge a list of interface edges in one pass and report every missing or wrong route, not just the first.
  ok: INTERFACE_ROUTES:OK   fail: INTERFACE_ROUTES_FAIL
- `ipiasm::check_irq_contracts`
  Check the interrupt paths registered by set_irq_contracts on the current design.
  ok: IRQ_CONTRACTS:NA   fail: IRQ_PATHS_FAIL
- `ipiasm::check_irq_paths expected`
  expected: {<sink> <want> ?<label>?} records. sink is <cell>/<pin> or <cell>/<pin>[<bit>]; want is <cell>/<pin>[<bit>], CONST:<0|1> or OPEN; label
  ok: IRQ_PATHS:OK   fail: IRQ_PATHS_FAIL
- `ipiasm::check_isp_resources`
  Check no ISP builds a PL I2C or GPIO core that nothing uses. IIC/GPIO Select = PL instantiates that core inside the ISP and brings its pins out
  ok: ISP_RESOURCES:NA ISP_RESOURCES:OK   fail: ISP_RESOURCES_FAIL
- `ipiasm::check_mem_noc_sysclk noc_cell`
  Check every memory NoC sys_clk pin is driven by the PS hsm reference clock, or, on a part whose processing system has no hsm*_ref_clk output (CIPS), by a top-le
  ok: MEM_SYSCLK:NA MEM_SYSCLK:OK   fail: MEM_SYSCLK_FAIL
- `ipiasm::check_memory_regions regions`
  does the memory NoC publish a DRAM segment for every DesignSpec region? regions: {<name> <size> ...}, or DesignSpec {name base size} dicts (then a failure
  ok: MEM_REGION:NA MEM_REGION:OK   fail: MEM_REGION_FAIL
- `ipiasm::check_memory_subsystem want_mc`
  Check the number of memory controllers (want_mc 0, 1, 2 or 4) and that memory responds.
  ok: MEM:OK   fail: MEM_FAIL
- `ipiasm::check_mipi_video_clocks ?margin_mhz=auto?`
  PG232 AXI-stream video_aclk throughput gate. The output packs PPC pixels of the configured format each cycle, so its minimum is aggregate lane payload
  ok: VIDEOCLK:NA VIDEOCLK:OK   fail: VIDEOCLK_FAIL
- `ipiasm::check_noc_legality ?force=1?`
  Run the NoC compiler (validate -force, skipped on an unchanged design) and report illegal NoC routes.
  ok: NOCLEGAL:NA NOCLEGAL:OK   fail: NOCLEGAL_FAIL
- `ipiasm::check_noc_post_address`
  Post-address gate: the published aperture must now be present in functional master address spaces. Never override this result; repair addressing instead.
  ok: NOC_POST:OK   fail: NOC_POST_FAIL
- `ipiasm::check_noc_pre_address ?want_mc=0?`
  Pre-address gate: prove the memory aperture exists and traffic declarations are legal, but do not require a master to hold the aperture before Address
  ok: NOC_PRE:OK   fail: NOC_PRE_FAIL
- `ipiasm::check_noc_solution ?want_memory=1?`
  Check the NoC has a legal routing solution; feeds half of the qos gate.
- `ipiasm::check_overmap ?max_windows={}?`
  did addressing hand the PL an aperture onto the whole processing system? assign_bd_address picks offsets; it does not decide visibility. It maps every
  ok: OVERMAP:NA OVERMAP:OK   fail: OVERMAP_FAIL
- `ipiasm::check_ps_fabric_irqs ps_cell required`
  PS fabric-interrupt capacity after PS automation, before IRQ wiring
  ok: IRQ_CAPACITY:OK   fail: IRQ_CAPACITY_FAIL
- `ipiasm::check_ps_noc_masters`
  Check every coherent PS NoC manager port reaches the NoC the same way. The PS drives its coherent (CCI) NoC ports in interleaving mode: if any is used,
  ok: PS_NOC:NA PS_NOC:OK   fail: PS_NOC_FAIL
- `ipiasm::check_qos ?allow={}?`
  Check every NoC route has declared, non-placeholder QoS; feeds half of the qos gate.
  ok: QOS:NA
- `ipiasm::check_reference_coverage spec_dir`
  does the design keep its reference design's functional IP? When ced_reference.json or example_reference.json sits beside the DesignSpec (or one level
  ok: REFERENCE_COVERAGE:NA   fail: REFERENCE_COVERAGE:FAIL
- `ipiasm::check_required_dram_masters patterns`
  Prove every DesignSpec role that requires external memory owns a mapped DRAM segment. Patterns name address spaces derived from bound semantic roles; this
  ok: DRAM_REACH:NA DRAM_REACH:OK   fail: DRAM_REACH_FAIL
- `ipiasm::check_required_ports ports`
  top-level ports whose names the requirements fix ports: list of dicts {name <port> [dir I|O|IO] [type clk|rst|...] [freq_hz <n>]
  ok: PORTS:OK   fail: PORTS_FAIL
- `ipiasm::check_required_reachability pairs`
  pre-address reachability, read from topology rather than the address map pairs: {<address-space glob> <target-segment glob | DRAM> ...}, one per DesignSpec
  ok: REACH:NA REACH:OK   fail: REACH_FAIL
- `ipiasm::check_source_files files top`
  Check RTL source files exist with the expected SHA-256 and the synthesis top is present.
  ok: SOURCE_FILES:OK   fail: SOURCE_FILES_FAIL
- `ipiasm::check_undriven_clocks`
  clock inputs with no driver, found before validate_bd_design (BD 41-758) A clock pin carried by a wired interface (a differential reference clock) is
  ok: UNDRIVEN_CLK:OK   fail: UNDRIVEN_CLK_FAIL
- `ipiasm::check_visibility slave_seg ?allowed_spaces={}?`
  Offset-free visibility assertion, read-only. check_window_scope proves a FIXED window sits in exactly the right spaces at exactly the right address; a slave
  ok: VISIBILITY:OK   fail: VISIBILITY_FAIL
- `ipiasm::check_window_scope slave_seg allowed_spaces offset range`
  Prove exact scope and placement after all address work. allowed_spaces may contain globs; each must resolve to concrete address spaces.
  ok: WINDOW_SCOPE:OK   fail: WINDOW_SCOPE_FAIL
- `ipiasm::check_wrapper_boundary rtl_file ?wrapper={}?`
  Check the block design's boundary against an RTL file that instantiates its wrapper (default <current BD>_wrapper): every named port connection must exist
  ok: BOUNDARY:NA BOUNDARY:OK   fail: BOUNDARY_FAIL
- `ipiasm::checkpoint tag`
  checkpoint: save the BD so a failed run resumes here instead of at zero From the clocks stage on, a checkpoint is refused while any clock input is undriven.
  ok: CHECKPOINT:   fail: CHECKPOINT_FAIL
- `ipiasm::clock_fingerprint out spec ?vlnvs={}?`
  Scratch: record the sources and feasibility fingerprint clock contracts are bound to, so the plan carries the scratch baseline (references/clock-planning.md) in
  ok: CLOCK_FINGERPRINT:OK   fail: CLOCK_FINGERPRINT_FAIL
- `ipiasm::clock_reset_msg_ids`
  Clock/reset defects are critical even when validate_bd_design returns rc=0.
- `ipiasm::close_gaps domain_map ?windows={}? ?oos_pins={}? ?max=3?`
  gap-closure orchestration loop: validate -> autofix -> re-validate Drives the confident fixers toward a clean design WITHOUT guessing: each pass
- `ipiasm::connect_automation pin rule ?config={}?`
  connection-automation: apply a connection rule discovered via the dump The rules dump (see ipcfg::rule_for_vlnv) also enumerates connection rules
  ok: SUCCESS:automation   fail: AUTO_FAIL
- `ipiasm::connect_axi_auto master slave ?rule={}? ?config={}?`
  AUTOMATION-FIRST: connect an AXI master to a slave Tries the axi4 connection-automation rule (auto-inserts smartconnect/NoC and
  ok: AUTO: MANUAL:
- `ipiasm::connect_board_intf board_intf ip_intf_pin`
  Connect a board interface to one IP interface pin through board automation.
  ok: SKIP:board_conn SUCCESS:board_conn   fail: BOARD_CONN_FAIL
- `ipiasm::connect_external_axi port slave ?rule={}? ?config={}? ?sc_cell=external_axi_sc? ?clk_pin={}? ?rst_pin={}?`
  connect a top-level AXI ingress to an internal slave Vivado's axi4 automation does not always admit a top-level port as Master.
  ok: EXT_AXI:OK   fail: EXT_AXI_FAIL
- `ipiasm::connect_intf src dst`
  connect two interface pins (idempotent) src/dst are "cell/PIN" strings (e.g. gmsl_camera_rx_0/pixel_out).
  ok: SKIP:already SUCCESS:intf   fail: INTF_FAIL
- `ipiasm::connect_net src dst`
  connect two scalar pins (idempotent), e.g. clk/reset/interrupt nets
  ok: SKIP:already SUCCESS:net   fail: NET_FAIL
- `ipiasm::context_identity`
  The open project directory, project name and BD name as a list; empty if none is open.
- `ipiasm::create_cell vlnv cell ?d={}?`
  gated cell creation: the board must be settled before the first cell
  fail: CONFIGURE_FAIL
- `ipiasm::create_module_ref module cell sources ?config={}?`
  Create an RTL module-reference cell from source files, with optional CONFIG properties.
  ok: SUCCESS:module   fail: CONFIGURE_FAIL
- `ipiasm::create_port name dir ?type={}? ?pin={}? ?properties={}?`
  create one exactly named scalar top-level port, optionally wired For spec-named clock, reset, interrupt and GPIO boundary ports that the RTL top
  ok: PORT:OK   fail: PORT_FAIL
- `ipiasm::declare_open pins reason`
  Record pins the design leaves open on purpose, so assembly_report lists them as declared instead of as wiring gaps. pins: cell interface pins or input clk/rst
  ok: OPEN:OK   fail: OPEN_FAIL
- `ipiasm::deliver out ?plan_path=auto? ?spec_path=auto?`
  Deliver a validated design in one call: final gate (taken from the plan), delivery guard, save, output products, wrapper, BD Tcl, XSA, guard again, delivery con
  fail: DELIVER_FAIL
- `ipiasm::deliver_ced out ced ?options={}?`
  deliver a CED that already meets the request: no DesignSpec, no plan For a request the release's CED meets as it is (EXACT) or with setting changes only
  ok: DELIVER_CED_REFUSED: DELIVER_CED_REFUSED:ced_reference DELIVER_CED_REFUSED:no   fail: DELIVER_FAIL
- `ipiasm::delivery_guard`
  Call immediately before save/export and again afterward. Any changed cell, property, net, or address mapping invalidates the final-contract evidence.
- `ipiasm::detect_fabric_coupling console ?documented_codes={}?`
  Report hardened-fabric coupling (ISP, AIE, NoC/DDRMC, PS PCIe) that makes cells depend on each other, from caller-supplied message codes.
  ok: FABRIC:coupled FABRIC:none   fail: FABRIC_FAIL
- `ipiasm::disconnect_intf_edge src dst ?force={}?`
  remove ONE interface edge, when that is a real topology change The mutation counterpart to the verdict above. Two refusals, both deliberate:
  ok: DISCONNECT_REFUSED: SKIP: SUCCESS:disconnected   fail: DISCONNECT_FAIL
- `ipiasm::discover_board_parts ?hint={}?`
  is there a board file for what we are building? `hint` is a board or part substring taken from the request. A non-empty hint
  ok: BOARDS: BOARDS:0
- `ipiasm::display_note`
  what the display path is, and where it can go Disclosure, like the memory gate's window report: a compositor has ONE video
  ok: DISPLAY: DISPLAY:none
- `ipiasm::enforce_window_scope name slave_seg allowed_spaces offset range`
  Idempotently make one fixed window's live Address Editor state equal the DesignSpec's exact allow-list. Unlike place_named_windows this closes both
  fail: WINDOW_SCOPE_FAIL
- `ipiasm::ensure_reset_domain cell clk_pin async_reset_pin reset_pins ?locked_pin={}?`
  Create one reset synchronizer for an explicit clock domain. Reset assertion may be asynchronous; release is synchronized by proc_sys_reset.
  ok: RESET_SYNC:WIRED   fail: RESET_SYNC_FAIL
- `ipiasm::ensure_semantic_cell_name cell role`
  Preserve a functional block's DesignSpec role in its live instance name. Independent readback cannot infer that an anonymous axi_bram_ctrl_0 is the
  ok: ROLE_NAME:OK   fail: ROLE_NAME_FAIL
- `ipiasm::example_reference vlnv out_dir ?config={}?`
  ground truth for "recreate the IP example design" requests Generate the Vivado example design of one IP (Open IP Example Design) beside the scratch
  ok: EXAMPLE_REF:OK   fail: EXAMPLE_REF_FAIL
- `ipiasm::externalize_interface spec`
  create or expose one top-level interface declared by DesignSpec spec is a dict:
  ok: EXTERNAL:OK   fail: EXTERNAL_FAIL
- `ipiasm::externalize_oos pins`
  3b. Externalize out-of-scope / boundary pins Validated against UG835 make_bd_intf_pins_external / make_bd_pins_external:
  ok: SUCCESS:external
- `ipiasm::fanout_domain mhz triples`
  fan a domain's clock+reset out to a list of cell clock/reset pins Connects clk_<mhz> from the wizard and the matching proc_sys_reset's
  ok: SUCCESS:domain   fail: RST_FAIL
- `ipiasm::feasibility_fingerprint sources`
  Tool, build, part, board part and SHA-256 of the spec/board/IP source files; the identity clock contracts are bound to.
- `ipiasm::final_contract_gate window_specs ?want_mc=0? ?ps_cell={}? ?required_irqs=0? ?force_noc=1? ?require_pinned=1? ?plan_path=auto? ?overmap_limit={}? ?required_dram_masters={}? ?spec_path=auto?`
  Recheck the exact state after all topology/address repairs. window_specs is a list of {name slave-segment {allowed-address-spaces} offset range} records
  fail: COVERAGE_FAIL
- `ipiasm::final_contract_gate_from_plan ?plan_path=auto? ?spec_path=auto?`
  final_contract_gate for a plan-built design, with every argument taken from the executed plan and its DesignSpec (scripts/final_gate_inputs.py): the plan's
- `ipiasm::gate_inventory wants`
  INVENTORY GATE wants: flat list {<ip-name-or-vlnv> <count> ...} derived by the agent FROM THE
  ok: INVENTORY:OK   fail: INVENTORY_FAIL
- `ipiasm::gate_override g reason`
  Open a build-order gate with the user's stated reason; never for routine recovery.
  ok: GATE:   fail: GATE_FAIL
- `ipiasm::gate_reset`
  Close all build-order gates (plan, board, inventory, qos).
  ok: GATES:reset
- `ipiasm::gate_status`
  Current state of the plan, board, inventory and qos gates with the reason each opened.
  ok: GATES:
- `ipiasm::illegal_msg_ids`
  documented compiler message ids that mean a route is not buildable
- `ipiasm::init`
  bring up: require ipcfg, switch to persistent assemble mode
  ok: SUCCESS:ipiasm   fail: ASM_FAIL
- `ipiasm::instantiate_ced ced ?options={}?`
  plan seed: start the delivery design from a matching CED Plan operation (cells stage, first): instantiate the release's CED into the open, empty
  ok: CED:OK SKIP:   fail: CED_FAIL
- `ipiasm::instantiate_n vlnv base n ?d={}?`
  instantiate + configure N copies with deterministic names Reuses ipcfg::create_cell_cfg for each copy. Names are <base>_0 .. <base>_{n-1}
  ok: SUCCESS:instances   fail: MULTI_FAIL
- `ipiasm::legal_intf_targets pin`
  Enumerate compatible INTERFACE destinations for an interface pin. Returns "INTF_TARGETS:<list of {path .. mode .. vlnv .. connected .. block ..}>" or
  ok: INTF_TARGETS:   fail: CC_ERR
- `ipiasm::legal_pin_targets pin`
  Enumerate compatible standalone PIN destinations (clk/rst/intr/data/ce), interface-members already filtered out by Vivado. Returns "PIN_TARGETS:<list>".
  ok: PIN_TARGETS:   fail: CC_ERR
- `ipiasm::load_clock_contracts path`
  Restore clock contracts saved by save_clock_contracts into this same design, without re-running the clock stage.
  ok: CLOCK_CONTRACTS:RESTORED   fail: CLOCK_CONTRACTS_LOAD_FAIL
- `ipiasm::memory_region_ranges noc_cell`
  Live, read-only: the selector ranges of <noc_cell>'s IP, from a scratch managed IP's parameter dump (the block design is not touched). Cached per VLNV.
- `ipiasm::name_port current wanted`
  Rename one exported top-level port, scalar or interface, to the name a boundary contract (RTL top, spec) expects. Automation names exported ports after their
  ok: PORT_NAME:OK   fail: PORT_NAME_FAIL
- `ipiasm::noc_channel_mbs raw ?chop_bytes=256? ?flit_bytes=16?`
  effective read bandwidth of one NoC channel, in MB/s Supply the raw channel rate and packetization geometry from current product
  fail: QOS_RATE_FAIL
- `ipiasm::noc_traffic_spec`
  dump the aggregate traffic specification Every NoC connection in the design, with the master that actually drives it
- `ipiasm::normalize_sim_model ?mode=tlm?`
  normalize SELECTED_SIM_MODEL across NoC/AIE cells A NoC created standalone is `rtl` while AIE-automation's internal axi_noc is
  ok: SIM_MODEL:
- `ipiasm::place_named_windows windows`
  place named address windows from the manifest (spec sizes -> ranges) Generic: each window's SIZE comes from the spec; the master address SPACE, the
  ok: NAMED_WINDOWS:placed
- `ipiasm::plan_execution_status ?plan_path={}?`
  Report whether an assembly plan reached ASSEMBLY_PLAN:PASS and still matches the plan on disk. A build run across Vivado processes passes when its newest receip
  ok: PLAN_EXECUTION:OK   fail: PLAN_EXECUTION_FAIL
- `ipiasm::plan_remove_noc_path noc_port ?destination={}?`
  Read-only preview of what remove_noc_path would disconnect and what would remain.
  ok: REMOVE_PLAN:port   fail: REMOVE_PLAN_FAIL
- `ipiasm::policy ?p={}?`
  policy: attended (default) asks the user on every escalation; unattended records the ASK lines in the report and continues where a
  ok: POLICY:   fail: POLICY_FAIL
- `ipiasm::preflight_clock_plan checks resets`
  Read-only. Answer, before any cell is created or configured, the two questions a clock stage otherwise discovers one failed source at a time: what frequency
  ok: CLOCK_PREFLIGHT:OK   fail: CLOCK_PREFLIGHT_FAIL
- `ipiasm::preflight_connect src dst ?opts={}?`
  pre-flight ONE edge, before anything mutates Stage 3 pre-flights edges with check_connect/legal_*_targets. This is that
  ok: PREFLIGHT:OK   fail: PREFLIGHT_FAIL
- `ipiasm::preflight_interface_contracts ?contracts=__registered__?`
  Check registered interface contracts against the live cells before connecting anything.
  ok: INTF_PREFLIGHT:NA INTF_PREFLIGHT:OK   fail: INTF_PREFLIGHT_FAIL
- `ipiasm::qos_classes`
  the legal traffic classes, per PG313/PG406
- `ipiasm::qos_owner pin`
  does this ingress OWN the QoS for its paths? PG313 (QoS Tab): "QoS is ignored when a NoC instance does not own the path...
- `ipiasm::qos_sweep plan scales ?force=0? ?channel_raw_mbs={}?`
  find the highest traffic declaration the compiler still routes cleanly Over-declaring is not the safe direction. Past the point where the routes stop
  ok: QOS_SWEEP:SKIP QOS_SWEEP:best   fail: QOS_SWEEP_FAIL
- `ipiasm::realize_clock_candidates plan source_pins evidence_dir ?prefix=clock_trial?`
  Scratch only: instantiate candidate clocking-wizard configurations from a plan_clocks.py candidate plan.
  fail: CLOCK_REALIZE_FAIL
- `ipiasm::reconcile_spec manifest_path expect_helper`
  reconcile the live BD against the manifest (read-only) manifest_path: the build manifest JSON (spec_to_manifest.py output)
  ok: RECONCILE:   fail: RECONCILE_FAIL
- `ipiasm::record_evidence label categories result`
  Keep a passing result from a check the final gate does not run itself (for example a supplied address-map checker). It counts only while the design is
  ok: EVIDENCE:OK   fail: EVIDENCE_FAIL
- `ipiasm::reference_search out_dir ?request={}?`
  reference-design search: which of the release's CEDs already builds this? Reads the running release's Configurable Example Designs (get_example_designs: names,
  fail: REFERENCE_SEARCH_FAIL
- `ipiasm::region_selectors noc_cell regions`
  Public: which selectors <noc_cell> needs for DesignSpec regions {{name base size} ...}.
  ok: REGION_SELECT:OK   fail: REGION_SELECT_FAIL
- `ipiasm::register_board_repo dir`
  make a shipped board-file directory visible to Vivado board.repoPaths is a Vivado PARAMETER (set_param), not a property:
  ok: BOARD_REPO:OK   fail: BOARD_REPO_FAIL
- `ipiasm::register_clock_contracts checks fingerprint_file`
  Plan operation (clocks stage, after the clock and reset wiring): register the DesignSpec's clock requirements, reading only the measured fields from the live de
  fail: CLOCK_CONTRACT_FAIL
- `ipiasm::remove_cell cell`
  Plan operation: remove a cell that a CED seed brought in and the DesignSpec does not ask for, with the nets that only it used and the external ports on those ne
  ok: SKIP:no SUCCESS:removed   fail: CONFIGURE_FAIL
- `ipiasm::remove_noc_path noc_port ?args...?`
  Usage: remove_noc_path <noc_port> ?<destination>?          ;# dry-run
  ok: REMOVE:OK   fail: REMOVE_FAIL
- `ipiasm::require_board_preset ?hint={}?`
  board gate wrapper: READY: opens the board gate, anything else keeps it shut
- `ipiasm::restrict_address_space space_path keep_targets`
  Exact target allow-list for a specific master address space, including a top-level AXI ingress that has no owning BD cell.
  ok: RESTRICT:OK   fail: RESTRICT_FAIL
- `ipiasm::restrict_master master_cell keep`
  keep a data-mover out of the register maps it has no business reaching assign_bd_address maps every slave a master can see. On a shared NoC that is
  ok: SUCCESS:   fail: RESTRICT_FAIL
- `ipiasm::restrict_window_to_spaces slave_seg allowed_spaces`
  Remove one slave window from every address space outside allowed_spaces. Call only after topology and automatic addressing are final; no later
  ok: WINDOW_SCOPE:RESTRICTED   fail: WINDOW_SCOPE_FAIL
- `ipiasm::save_clock_contracts path`
  Registered contracts live in an interpreter variable, so a new CLI turn with a new Vivado session loses them and a re-source trips the repair-the-hardware
  ok: CLOCK_CONTRACTS:SAVED   fail: CLOCK_CONTRACTS_SAVE_FAIL
- `ipiasm::scratch_bind out_dir ?spec={}? ?fragments={}?`
  one call per scratch round: binding probe -> snapshot -> plan requirements -> draft plan Measured on the GitHub-issue designs (2026-11-07): the PLAN stage took
  fail: SCRATCH_BIND_FAIL
- `ipiasm::set_clock_contracts checks resets sources fingerprint`
  Register the design's required clock frequencies and reset domains with their scratch fingerprint; immutable once set.
  ok: CLOCK_CONTRACTS:SET   fail: CLOCK_CONTRACT_FAIL
- `ipiasm::set_interface_contracts contracts`
  Register metadata-aware interface contracts (clock, width, protocol) for later preflight and checks.
  ok: INTF_CONTRACTS:SET   fail: INTF_CONTRACT_FAIL
- `ipiasm::set_irq_contracts expected`
  Register the interrupt table once, from the DesignSpec plus bindings, so the final gate re-traces it; a retry must repair wiring, not rewrite the table.
  ok: IRQ_CONTRACTS:SET   fail: IRQ_CONTRACT_FAIL
- `ipiasm::set_qos si_pin dest read_bw write_bw ?tclass={}? ?burst=16? ?initial_boot={}?`
  set QoS on one NoC connection si_pin   : the NoC slave interface pin carrying the master (noc/S03_AXI)
  ok: SUCCESS:qos   fail: QOS_FAIL
- `ipiasm::set_unused_qos_routes routes`
  gate: refuse to ship a design still on the default traffic spec The companion to check_memory_subsystem. NUM_MC=1 loses bandwidth at the
  ok: QOS_UNUSED:SET   fail: QOS_FAIL
- `ipiasm::size_to_range size`
  convert a human size ("512M", "128MB", "64K", "2G", "0x...") to a hex range Generic, IP-agnostic. Returns "" if the size cannot be parsed (caller escalates).
- `ipiasm::stage name`
  timing: call ipiasm::stage <name> at each stage boundary; ipiasm::timings prints where the wall clock went. Optimise from these numbers, not guesses.
  ok: STAGE:   fail: GATE_FAIL
- `ipiasm::stage_execution_status`
  Result dict of the last assemble_from_plan stage: status, operations, receipts and timings.
- `ipiasm::sync_intf_freq_hz source target`
  Keep propagated interface metadata coherent after a clock-domain choice. Either endpoint may be an internal interface pin or a top-level interface.
  ok: FREQ_SYNC:OK   fail: FREQ_SYNC_FAIL
- `ipiasm::timings`
  Seconds spent in each ipiasm::stage so far; TIMINGS:total=...
  ok: TIMINGS:total
- `ipiasm::validate`
  run validate_bd_design, classify the outcome validate_bd_design RAISES on a hard error; warnings/criticals are messages.
  ok: VALIDATE:PASS
- `ipiasm::validate_bundle ?want_mc=0? ?ps_cell={}? ?required_irqs=0? ?force_noc=1? ?overmap_limit={}?`
  One verdict bundle per repair iteration. This prevents a validate-only loop from fixing the next QoS/memory/AIE/clock defect one expensive pass at a time.
  ok: BUNDLE:OK IRQ_CAPACITY:NA   fail: BUNDLE_FAIL
- `ipiasm::wire_clk_rst_auto clk_pin rule ?config={}?`
  AUTOMATION-FIRST: build/clock a clock+reset off a source clock pin Applies the clkrst rule to a clock source pin (e.g. a PS/clock-wizard clk out).
  fail: CLK_FAIL
- `ipiasm::wire_hier_clk inner_pin src_pin ?bname=aclk_in?`
  drive a clock pin buried inside a subsystem cell that exposes no boundary Block automation can leave an internal clk pin (e.g. aie_ml_inference_ss/
  ok: SUCCESS:hier_clk   fail: CLK_FAIL
- `ipiasm::wire_mem_noc_sysclk noc_cell`
  Drive every memory NoC sys_clk pin from the PS hsm reference clock.
  ok: MEM_SYSCLK:DEFER MEM_SYSCLK:NA MEM_SYSCLK:OK   fail: MEM_SYSCLK_FAIL

## Python scripts: `scripts/` (18)

Run with the configurator's `lib/ipcfg/vivado_python.sh`; `--help` prints the same usage.

### await_result.py

Wait for a long ipiasm call whose MCP tool call timed out, and print its result.

```text
usage: await_result.py [-h] [--timeout TIMEOUT] out [procs ...]

positional arguments:
  out
  procs

options:
  -h, --help         show this help message and exit
  --timeout TIMEOUT
```

### bind_design_spec.py

Release-adaptive technology binding for DesignSpec assembly templates.

```text
usage: bind_design_spec.py [-h] {probe-tcl,parse-probe,bind} ...

positional arguments:
  {probe-tcl,parse-probe,bind}

options:
  -h, --help            show this help message and exit
```

### boundary_names.py

Match DesignSpec `top_level` interfaces to the port names the RTL top instance uses.

```text
usage: boundary_names.py [-h] {check,add} ...

Match DesignSpec `top_level` interfaces to the port names the RTL top instance uses.

The plan compiler's BOUNDARY check accepts, for each `top_level` interface, a
`check_required_ports` entry named after the interface, or after the bare name at
the other end of a required connection that uses it. When the RTL top names the
port differently (a renamed signal, or one port per bit of a grouped interface),
neither name can be a wrapper port, so the DesignSpec must carry the RTL name as
that bare connection end. Do it before binding: the plan records the spec hash.

  check --spec design_spec.json --rtl top.sv [--wrapper design_1_wrapper]
      BOUNDARY_NAMES:OK | BOUNDARY_NAMES_FAIL (each interface, its accepted names,
      and RTL ports that look related) | BOUNDARY_NAMES:NA (no wrapper instance)
  add --spec design_spec.json <block>.<interface>=<port>[,<port>...] ...
      Appends one required connection per RTL port, idempotently, and prints
      BOUNDARY_NAMES:ADDED <n>. Rerun the spec validators and bind afterwards.

positional arguments:
  {check,add}

options:
  -h, --help   show this help message and exit
```

### ced_fit.py

Does a recorded CED already meet the request?

```text
usage: ced_fit.py [-h] --reference REFERENCE --checklist CHECKLIST [--out OUT]

options:
  -h, --help            show this help message and exit
  --reference REFERENCE
  --checklist CHECKLIST
  --out OUT
```

### compile_assembly_plan.py

Validate a structured assembly plan and compile it to guarded Tcl.

```text
usage: compile_assembly_plan.py [-h] --out OUT [--stage STAGE] [--dispatch]
                                [--spec SPEC] [--roles-from ROLES_FROM]
                                [--role-table ROLE_TABLE]
                                plan

Compile assembly_plan.json to Tcl. --list-ops prints the plan format.

positional arguments:
  plan

options:
  -h, --help            show this help message and exit
  --out OUT
  --stage STAGE
  --dispatch
  --spec SPEC           Require complete parameter coverage against this
                        DesignSpec, and every check the design needs in its
                        stage
  --roles-from ROLES_FROM
                        Addressing script whose ::addrmap::required_roles
                        every role-table row must cover
  --role-table ROLE_TABLE
                        Markdown with '| role | `cell` |' rows (default:
                        plan.md beside the plan)
```

### contract_coverage.py

Map every required DesignSpec contract to the readback evidence that proves it.

```text
usage: contract_coverage.py [-h] {derive,check} ...

Map every required DesignSpec contract to the readback evidence that proves
it. `derive` lists the obligations; `check` compares them with the final
gate's evidence and fails on any required contract no passing check covers.
Contracts a block design cannot prove (software behaviour, host-side interrupt
delivery) are reported as deferred, never silently dropped.

positional arguments:
  {derive,check}

options:
  -h, --help      show this help message and exit
```

### draft_plan.py

Draft assembly_plan.template.json from the DesignSpec, binding intent and snapshot.

```text
usage: draft_plan.py [-h] --spec SPEC --intent INTENT --snapshot SNAPSHOT
                     [--fragments [FRAGMENTS ...]] [--ced-fit CED_FIT]
                     [--clock-fingerprint CLOCK_FINGERPRINT] --out OUT
                     [--todo TODO]

Draft assembly_plan.template.json from the DesignSpec, binding intent and snapshot.

options:
  -h, --help            show this help message and exit
  --spec SPEC
  --intent INTENT
  --snapshot SNAPSHOT
  --fragments [FRAGMENTS ...]
  --ced-fit CED_FIT     ced_fit.json; a STRUCTURAL fit seeds the plan with its
                        CED
  --clock-fingerprint CLOCK_FINGERPRINT
                        clock_fingerprint.tcldict from
                        ipiasm::clock_fingerprint
  --out OUT             assembly_plan.template.json to write
  --todo TODO           TODO list (default: <out without .json>.todo.md)

Usage: draft_plan.py --spec design_spec.json --intent binding_intent.json
                     --snapshot binding_snapshot.json [--fragments proven/*.json]
                     [--ced-fit ced_fit.json] [--clock-fingerprint clock_fingerprint.tcldict]
                     --out assembly_plan.template.json [--todo assembly_plan.todo.md]

Writes every operation the inputs determine, in the compiler's stage order, so the plan is
edited rather than written: the board stage (create_bd_design, require_board_preset, the
processing system, preset_drift), one create_cell per role, gate_inventory with the roles'
counts, connect_intf for each DesignSpec connection whose two interfaces the binding resolved,
check_required_ports for top-level interfaces, every check STAGE_CHECKS requires that needs no
design decision, the address and validate stages, and the parameter_coverage skeleton.
With a clock fingerprint (ipiasm::scratch_bind writes it), register_clock_contracts for the
DesignSpec's clock_reset targets.

With a STRUCTURAL --ced-fit, the plan starts from the CED its ced_reference.json records:
instantiate_ced, remove_cell for surplus units, create_cell only for blocks the CED lacks,
ipcfg::require for a clock rate or setting the CED does not meet, check_cell_config of each
kept cell's reference configuration, the interrupt table, reachability pairs and window
scopes the CED wires and maps (enforce_window_scope first for a window the CED maps at
another offset), and the parameter_coverage entries those operations prove.

What needs a decision (board preset automation config, clock/reset wiring and contracts, NoC
routes and QoS, reachability address spaces, parameter_coverage reasons) is listed, with what
the compiler will still demand, in the TODO file. The last line printed is
  DRAFT_PLAN:OK ops=<n> todo=<n> template=<path> todo_file=<path>
or DRAFT_PLAN_FAIL:<reason>. Then: fill the TODOs, run proven_to_plan.py (if there are proven
fragments), bind, and compile with --spec.
```

### draft_spec.py

Draft a DesignSpec from a recorded reference design.

```text
usage: draft_spec.py [-h] --reference REFERENCE --out OUT [--name NAME]
                     [--request REQUEST] [--part PART] [--board BOARD]

options:
  -h, --help            show this help message and exit
  --reference REFERENCE
  --out OUT
  --name NAME
  --request REQUEST
  --part PART
  --board BOARD
```

### final_gate_inputs.py

Derive ipiasm::final_contract_gate's arguments from an assembly plan and its DesignSpec.

```text
usage: final_gate_inputs.py [-h] --plan PLAN --spec SPEC

Derive ipiasm::final_contract_gate's arguments from an assembly plan and its
DesignSpec. Everything comes from what the plan already checks and what the
spec already states, so a plan-built design reaches the final gate without
hand-written glue: - windows: each plan `ipiasm::check_window_scope` op, named
after the DesignSpec fixed address window with the same offset and range (and,
when several share them, the same masters, block and target interface); -
bundle: the arguments of the plan's last `ipiasm::validate_bundle` op; -
dram_masters: the address spaces allowed into every window that a
`memory_reachability` contract requires and that a `memory_controller` block
owns (on-chip memories such as a BRAM mailbox are not DRAM), plus the patterns
of each plan `ipiasm::check_required_dram_masters` op (a spec with no fixed
window on its memory); - reachability: the pairs of the plan's last
`ipiasm::check_required_reachability` op; - visibility: the {slave segment,
allowed spaces} of each plan `ipiasm::check_visibility` op, which the gate
reruns and records as visibility_policy evidence (a plan op's own result is
not gate evidence, so without this a plan could not prove that category).
Prints a Tcl dict. Fails, naming the window, when a plan window matches no
spec window or more than one.

options:
  -h, --help   show this help message and exit
  --plan PLAN
  --spec SPEC
```

### manifest_expect.py

Convert a build manifest into the Tcl digest consumed by reconcile_spec.

```text
usage: manifest_expect.py [-h] manifest

positional arguments:
  manifest

options:
  -h, --help  show this help message and exit
```

### measure_workflow.py

Capture immutable source snapshots and offline regression measurements.

```text
usage: measure_workflow.py [-h] --out OUT --label LABEL [--test TEST]
                           [--load-probe] [--libraries LIBRARIES]
                           [--trials TRIALS]

Capture immutable source snapshots and offline regression measurements.

options:
  -h, --help            show this help message and exit
  --out OUT
  --label LABEL
  --test TEST
  --load-probe
  --libraries LIBRARIES
  --trials TRIALS
```

### plan_clocks.py

Generate bounded clock topology candidates; only live Vivado can prove them.

```text
usage: plan_clocks.py [-h] [--limit LIMIT] [--tcl] request

Generate bounded clock topology candidates; only live Vivado can prove them.
Assumptions: rates and tolerances are integer Hz from accepted requirements;
sources describe live-discovered capabilities for the selected part/IP
version. No PLL equations, family limits, phase guarantees or hardware
qualification are inferred here. Unknown contract fields fail rather than
silently losing intent.

positional arguments:
  request

options:
  -h, --help     show this help message and exit
  --limit LIMIT
  --tcl          Emit Tcl data, never executable commands
```

### plan_requirements.py

Print what compile_assembly_plan.py --spec will demand of this design, before you write the template.

```text
usage: plan_requirements.py [-h] --spec SPEC --intent INTENT --snapshot
                            SNAPSHOT [--roles-from ROLES_FROM]
                            [--fragments [FRAGMENTS ...]]
                            [--coverage-out COVERAGE_OUT]

Print what compile_assembly_plan.py --spec will demand of this design, before you write the template.

options:
  -h, --help            show this help message and exit
  --spec SPEC           design_spec.json
  --intent INTENT       binding_intent.json
  --snapshot SNAPSHOT   binding_snapshot.json
  --roles-from ROLES_FROM
                        Tcl script with ::addrmap::required_roles
  --fragments [FRAGMENTS ...]
                        proven/*.json from export_proven
  --coverage-out COVERAGE_OUT
                        write the parameter_coverage skeleton here

Run it once after binding_snapshot.json exists (and after export_proven, so proven
leaves are known). One call lists, from the DesignSpec, the binding intent and the
snapshot:

  ROLE <role> instance=<cell> vlnv=<vlnv> spec_block=<block>
      the cell each role binds to (${bind.blocks.<role>.instance})
  STAGE <stage> needs <proc> (<why>)
      a check that stage must call, after the work it checks (STAGE_GATE_FAIL otherwise)
  AFTER_VALIDATION <proc> (<why>)
      a check that belongs in a stage after qos, after ipiasm::validate_bundle
  BOUNDARY <block>.<interface> port=<name>[|<name>...]
      a top_level interface; one of these names goes into check_required_ports
  REACH <master>-><target> masters=<cells> targets=<cells|DRAM>
      a required contract that needs one check_required_reachability pair
  REQUIRED_ROLE <role>
      a row the plan.md `| role | cell |` table needs (with --roles-from)
  COVER <pointer> = <json value>
      a parameter leaf parameter_coverage must map; proven leaves are left to proven_to_plan.py

With --coverage-out, writes {"parameter_coverage": {...}} holding every COVER leaf
with its exact source value, "disposition": "hardware", an empty reason and no checks:
fill reason and checks (or make it external with an owner) and merge it into the template.

Last line: PLAN_REQ:OK roles=<n> stage_checks=<n> boundary=<n> reach=<n> cover=<n> proven=<n>
or PLAN_REQ_FAIL:<reason> (exit 1).
```

### proven_to_plan.py

Replay Stage 2's proven IP configurations into the Stage 4 assembly plan.

```text
usage: proven_to_plan.py [-h] --fragments FRAGMENTS [FRAGMENTS ...] --snapshot
                         SNAPSHOT --template TEMPLATE --merged MERGED
                         [--spec SPEC] [--out OUT]

Replay Stage 2's proven IP configurations into the Stage 4 assembly plan.

options:
  -h, --help            show this help message and exit
  --fragments FRAGMENTS [FRAGMENTS ...]
                        ipcfg::export_proven outputs, one per Stage 2 group
  --snapshot SNAPSHOT   binding_snapshot.json
  --template TEMPLATE   assembly_plan.template.json
  --merged MERGED       proven_ip_config.json to write
  --spec SPEC           design_spec.json; wires spec_pointers into
                        parameter_coverage
  --out OUT             template output (default: update --template in place)
```

### reference_coverage.py

Does the built design keep every functional IP of its reference design?

```text
usage: reference_coverage.py [-h] --built BUILT --spec-dir SPEC_DIR

options:
  -h, --help           show this help message and exit
  --built BUILT
  --spec-dir SPEC_DIR
```

### reference_search.py

Rank the running release's Configurable Example Designs (CEDs) against a design request.

```text
usage: reference_search.py [-h] --index INDEX --out OUT [--board BOARD]
                           [--part PART] [--text TEXT] [--spec SPEC]
                           [--roles ROLES]

options:
  -h, --help     show this help message and exit
  --index INDEX
  --out OUT
  --board BOARD
  --part PART
  --text TEXT
  --spec SPEC
  --roles ROLES
```

### timing_report.py

Show where a build's time went, from the timing trace the library records in Vivado.

```text
usage: timing_report.py [-h] [--top TOP] [--budget BUDGET]
                        [--format {md,json}]
                        trace [trace ...]

Show where a build's time went, from the timing trace the library records in
Vivado.

positional arguments:
  trace               trace .jsonl files or directories of them

options:
  -h, --help          show this help message and exit
  --top TOP
  --budget BUDGET
  --format {md,json}
```

### validate_feasibility.py

Bounded, offline validation of scratch capability evidence, never hardware PASS.

```text
usage: validate_feasibility.py [-h] --manifest MANIFEST --expected EXPECTED

Bounded, offline validation of scratch capability evidence, never hardware
PASS.

options:
  -h, --help           show this help message and exit
  --manifest MANIFEST
  --expected EXPECTED  Independent expectations and current fingerprint
                       values; never copied from manifest
```
