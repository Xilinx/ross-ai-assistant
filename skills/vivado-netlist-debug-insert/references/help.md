<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

<!-- help-topic: what -->
## What this skill does

This is the guide for a person who has never used the skill. Show only the section that answers the question. `ila::help <topic>` prints one section from inside Vivado (the topics are what, where, inspect, glossary, items, process, options, refusals, clocks, limits, troubleshooting, next). The commands, options and codes in this file are for the AI. The user hears none of them.

Rules for every answer:

- Start by asking where the person is (see the `where` section). Offer 3 or 4 short choices. Ask for a path or a signal name only after that.
- The first time you use a term, say what it means in half a sentence, even if they did not ask. This holds for ILA, probe, trigger, capture depth, synthesized and every option. Never list bare terms.
- If they say "one step at a time", give only the first step, then ask. Do not list the later steps.
- Say only what this file says. Invent no fact.
- Speak plain. Describe in words. Show an item, a flag, a command or a code only when the person asks how or why. A refusal gives its reason in words and what they can do, never a bare code. The person talks plain English and you write the Tcl; never teach them syntax.
- Offer only what the skill can do now. Before a synthesized design exists it cannot plan, list candidate signals or show anything. Say it needs the synthesized design first. With a synthesized design open, you can list what can be probed (the `inspect` section).

- It adds debug probes to a design Vivado has already synthesized. You do not edit your source code. Synthesized means Vivado has turned your code into a netlist (a list of gates and registers) but has not yet placed it on the chip.
- A probe watches a signal. The probes sit in an ILA (Integrated Logic Analyzer), a small logic analyzer on the chip that records what the signals do. You read the recording later in Vivado Hardware Manager.
- What you get: the cores in your open design. Nothing is saved unless you say where, and the skill builds nothing on its own. If you ask for a probes file (it tells Hardware Manager the signal names), I write it. It is made from the built design, so the optimize step runs first when it has not run yet (AI: `opt_design`).
- It checks that the capture memory fits the chip before it changes anything.
- It never edits RTL (your source code), block designs, or your constraints file. It never runs synthesis. It saves only where you say, and writes a probes file only when you ask. It never moves a debug hub you already have. In a Vivado project the save runs only when you ask for it (AI: `save_constraints`): if the project has only one constraints file, Vivado can write the debug lines into it or rewrite it, so I copy it first and tell you.
- It adds ILA cores only. A VIO, JTAG-to-AXI Master, IBERT or System ILA must be in your source.
- It is not a timing or area tool. It does not tune implementation.

What I can do, in short (say only these when asked "what can you do?"):

- Add ILA cores to a synthesized design, on the signals you name: a register, a bus or some of its bits, the registers of a module, or everything marked for debug.
- Check that the capture memory fits the chip before anything changes.
- With the design open, list what can be probed: the marked nets, the main modules, the clocks and the debug cores already there.
- Change a setting (such as the capture depth) of a core I added.
- Write the probes file when you ask. It is made from the built design, so I run the optimize step first if it has not run yet.
- Give read-only advice on ILA timing in a routed design.

What I do not do on my own: optimize, place, route or build the image. If you ask for one of those steps, I run it as you asked.

What I do not do: run synthesis (the skill starts from a synthesized design); list every signal or register of a design; choose the signals, the depth or the options for you (the defaults stay unless you ask); add a VIO, JTAG-to-AXI Master, IBERT or System ILA.

<!-- help-topic: glossary -->
## 30-second glossary

- **ILA** (Integrated Logic Analyzer): a debug core on the chip that records signal values into memory, like a scope for digital signals.
- **Probe**: one signal (or a group of bits) the ILA watches.
- **Trigger**: the condition that starts a capture, for example "count equals 5".
- **Capture depth**: how many samples the ILA stores per capture. More depth shows a longer time but uses more memory.
- **Debug hub**: a small block that links all ILAs on the chip to the JTAG cable, so Hardware Manager can talk to them.
- **Synthesized checkpoint** (a `.dcp` file): a snapshot of your design after synthesis. This is what the skill needs.
- **Implementation**: place and route. Vivado chooses where each gate sits on the chip and wires them.
- **Bitstream or image**: the file you load on the board. UltraScale+ uses `.bit`, Versal uses `.pdi`.
- **Probes file** (`.ltx`): tells Hardware Manager which signal is which bit in the capture.
- **Hardware Manager**: the part of Vivado that connects to the board, loads the image and shows the captured waveform.
- **Clock domain**: all the logic that runs on one clock. One ILA core watches one clock domain.
- **DFX** (Dynamic Function eXchange, also called partial reconfiguration): swap a part of the chip while the rest keeps running.
- **Reconfigurable partition** and **module**: the partition is the swappable slot on the chip. A module is the logic that sits in the slot. Several modules can take turns in one partition.
- **Greybox**: a module with no logic in it. There is nothing to probe.
- **MARK_DEBUG**: a flag you put on a signal in your design to say "I want to debug this".
- **Net**: a wire between logic cells. Probes attach to nets.
- **Register**: a storage element (flip-flop) that holds a value between clock ticks.
- **Combinational**: logic with no register, whose output follows its inputs at once. Its value can show brief glitches, so the skill probes such nets only when you name them.

<!-- help-topic: where -->
## Where are you now?

Ask this first, with short choices: "Where are you now? (1) I only have source code. (2) I have a synthesized design. (3) I have a Vivado project. (4) I am not sure." Then take the matching line below. Do not ask for paths before they answer.

- **I have only source code (RTL).** The skill needs a synthesized design first. Run synthesis in Vivado (Run Synthesis, or `synth_design` in Tcl), save a checkpoint with `write_checkpoint`, and give it to me. I will not run synthesis myself.
- **I have a synthesized checkpoint, or a synthesized design is open in Vivado.** You are ready. Tell me which signals to watch, or ask "what can I probe?" and I will list what is in the design (the `inspect` section).
- **I have a Vivado project.** Open the synthesized run (`open_run synth_1`) in the project, then tell me which signals to watch. I add the cores in the open run and save nothing unless you ask. In a project the save is `save_constraints`; the implementation run builds the cores and writes the probes file. Details: `references/flat-project.md`.
- **My design is placed or routed.** I refuse it. The skill adds probes to the synthesized netlist, before place and route. Vivado accepts a debug core change in a placed or routed design without any message, but it drops part of the placement and routing, so `opt_design`, `place_design` and `route_design` must all run again. Vivado's own guide (UG908) says the same: to insert, delete or change debug cores in a design that was already implemented, open the synthesized checkpoint and insert there. Open the post-synthesis checkpoint instead. A checkpoint saved after `opt_design` is accepted with a warning that `opt_design` must run again (a tested case). If you want to change a placed or routed design as it is, tell me; I then check it with state reset allowed (AI: `ila::check_input -allow_state_reset 1`) and pass its warning on to you. That never works in the implementation run of an open project: close it and open the synthesized design.
- **My design already has debug cores.** I list them for you and keep them. I add new cores next to them. In a flat design I can replace a core named like `u_ila_0` that I could list, if you ask me to. I skip a net that an existing core already probes, so it is not captured twice; if you want it probed again, tell me and I probe it again (AI: `-allow_duplicate 1`). Debug cores that come from your source stay as they are.
- **My design uses DFX (partial reconfiguration).**
  - Versal: supported from Vivado 2026.1, in a project, one configuration at a time. Your design must already contain the debug hubs. I never add a hub.
  - UltraScale+ on Vivado 2026.2 and later: supported only when the design has no debug cores, no debug hub and no debug ports yet. The ILA goes in the static part of the design.
  - UltraScale+ on Vivado 2026.1 and earlier: Vivado itself refuses to insert once the partition is flagged as reconfigurable. Only a limited route is left: static nets, from a checkpoint saved before the partition was flagged (`references/routes.md`). For nets inside a module, your source must instantiate the ILA.
  - A DFX design that already has debug is refused. Measured on Vivado 2026.2: adding a core makes Vivado rebuild the existing hub without its 12 connections into the partition, and the build fails later. So I refuse early and tell you what your source must change.
- **I am not sure what I need.** Answer three questions and I will take it from there:
  1. Which signals do you want to see (names, or "the counter in module u_a")?
  2. How long a capture do you need (how many clock ticks around the event)?
  3. Which clock do those signals run on?

<!-- help-topic: inspect -->
## What can I probe in my design?

Ask this once a synthesized design is open. I look (AI: run `ila::inspect`). It reads the design, changes nothing and writes no file. (AI: it is loaded with the library, `scripts/ila_insert.tcl`, or by itself with `source <skill folder>/scripts/ila_help.tcl`. `ila::help inspect` prints this section.) With no design open it says so and tells you to open the synthesized checkpoint.

What it lists, in plain words:

- **Marked nets.** Nets someone flagged for debug: `MARK_DEBUG` in the source, or the Debug tag on a net in a block design. They are grouped by the module they sit in, and a bus is one entry with its width. You can ask for all of them, or for the ones in one module. If none are found it says so. If this session's `config_flows -mark_debug` setting is `disable` or `synthesis_only`, that is a likely reason the marks are gone, and it says so; the checkpoint alone cannot confirm it.
- **Top-level modules.** The main blocks directly under the top of the design, each with the module it is built from. You can probe everything a block registers ("the whole instance"), or one signal in it. A flat design has no such blocks and it says so.
- **Clocks.** Each clock with how many clock pins it drives (a block RAM has two). An ILA records on one clock, so signals on the same clock share one ILA and a signal on another clock gets its own. When the library is loaded, each clock also says whether it keeps running (fine for an ILA) or can stop (an ILA on it is planned with a warning).
- **Debug cores already in the design.** Each with its type. A new ILA is added next to them.

What it does not do:

- It does not list every signal. A design can hold millions of nets, so it lists the marked nets, the modules and the clocks, and cuts long lists with "and N more". For a signal that is not listed, name it (see `items`).
- It does not promise a listed net can be probed. The plan decides, and refuses a net that cannot be probed, with a reason in words (see `refusals`).

<!-- help-topic: items -->
## How to tell me what to probe

Plain English is enough. You never type or learn any special form. Examples: "the counter in u_a", "the 32-bit register count", "everything marked for debug", "all the registers in module u_fifo", "bits 0 to 7 of count".

- **One signal or register:** say its name. A name with no path means the top level. Give the path to be exact ("count inside u_a/u_b/u_c"). Say "in every instance" or "all of them" for every place the name appears.
- **A bus:** say the bus name for every bit, or say which bits you want ("bits 0 to 7 of count"). I list the bits by name.
- **A family of registers:** describe it ("the data registers in u_dma"). `*` is not something you have to type.
- **Everything marked for debug,** or marked for debug in one module.
- **A whole module:** its registers. Combinational nets it skipped are named as left out.
- **Combinational nets** (logic with no register): only when you ask for them, by name. Such a net can glitch, so I warn you. The words everything, all signals and all nets mean registers, not combinational nets.
- **Too broad:** a request for everything with no module or names is refused: too many signals for one request. Ask for a module, a register, or a list of names.
- **A name I cannot find:** I say where the same name exists, if it does, and ask which you mean.

I say in words what I chose to probe. I show the exact form only if you ask how.

AI only, never shown unless the user asks how (rules V1 to V9, detail in `references/item-syntax.md`): you write names for `ila::plan`. A name is a Vivado name pattern matched against full hierarchical names, as `get_nets -filter {NAME =~ <name>}` does: `*` any characters including `/`, `?` one character, brackets literal, case-sensitive, anchored. A bare name is the top level only; a path is that exact one; `*/count` is any depth below the top. Registers first (`<name>_reg`), then nets, then combinational nets only by `-combinational {names}` or an exact name. A bare bus name is every bit; bits are listed by name; there is no colon slice. Marked nets are `-filter {MARK_DEBUG}`. A whole module is `u_x/*`. A request for everything is `*`, and the library answers too broad itself.

<!-- help-topic: process -->
## The process, step by step

1. **Check the input.** I open your checkpoint and check it is synthesized (not placed), read the chip family, and list any debug cores already there. Versal needs a CIPS or PS Wizard (the processor block, PS9 or PS11) with a PL reset enabled.
2. **Plan.** I turn your request into a plan: which nets are accepted, which are refused and why, one ILA per clock. Nothing is changed yet.
3. **Report the cost.** The plan prints how many block RAM tiles the capture memory needs and how many are free. If it does not fit, I stop and tell you the largest depth that does.
4. **Apply.** I create the cores, connect the clock and the probes, and read the connections back.
5. **After apply.** The cores are in your open design only. I save only where you say, and I run no build step on my own: the optimize step builds the cores and the hub (AI: `opt_design`).
6. **Report.** Probed nets, clock per core, refusals with reasons, memory line, and what was saved or built (nothing, unless you asked).

In an UltraScale+ DFX design (Vivado 2026.2 and later) work in a checkpoint session. The cores go in the static part. `opt_design` builds the debug hub, and the hub needs the clock constraints already read to constrain the crossing to its own clock. With the constraints read after `opt_design`, one design showed a parent WNS of -11.542 ns, against +2.889 ns on that crossing when they were read first. A partition with no pblock stops place with DRC HDPR-30; I add no floorplan. I save only where you say and run no build step on my own. The flow after insertion passed on KCU116, VCU108 and VCU128 boards (a full image and five partial swaps). I do not call `save_constraints` there: that is the Versal project step, and nobody has shown it works in an UltraScale+ checkpoint session.

What you do next in Vivado Hardware Manager:

1. Open Hardware Manager and connect to your board.
2. Program the device with the image (`.bit` or `.pdi`) and point it at the probes file (the `.ltx` file). On Versal with Vivado 2026.2, first run `write_debug_probes -force <file>.ltx` in the routed design: the file written before place and route carries a provisional hub id, and Hardware Manager then finds no ILA.
3. Pick the ILA and set its trigger, the condition that starts a capture. In its trigger setup, add the probe, set the condition (for example `==` and the value 5) and choose where in the window the trigger sits (samples before and after it).
4. Arm it with Run Trigger and let it capture. Run Trigger Immediate captures at once, without a condition. It is a quick check that the signals move.
5. Read the waveform. The signal names come from the probes file.

If Hardware Manager says it found no ILA probes, the probes file is not set on the device. Set it (the device's probes file property) and refresh.

This skill builds no image on its own: the image comes from the implementation of the design you saved, run by you or by me when you ask. Do not synthesize again.

<!-- help-topic: options -->
## Options you can choose

Defaults are fine for a first look. I change an option only when you ask. When you mention an option, say what it does in the same sentence. The flags in brackets, marked AI, are for the AI to write. Never show them unless the user asks how.

- **Capture depth** (AI: `-depth`): samples stored per capture. Default 1024. Valid values are powers of two from 1024 to 131072. More depth means a longer window and more block RAM. If you ask for something Vivado does not accept, I ask you or use the largest valid depth and say so.
- **Pipeline stages** (AI: `-pipe_stages`): extra registers between the probe and the ILA's capture logic. Vivado documents them as making timing closure easier when the ILA goes into a fast design. Default is automatic: 1 when a probed net is combinational, else 0.
- **Storage qualification** (AI: `-strg_qual`): the ILA stores a sample only when a capture condition you set in Hardware Manager is true, so the memory is not spent on samples you do not care about. Default off.
- **Advanced trigger** (AI: `-adv_trigger`): a trigger written as a small state machine (up to 16 states, with counters and flags), for example "A, then B, then C". You write it in Hardware Manager. Default off.
- **Trigger in and out** (AI: `-trigin`, `-trigout`): a trigger input and output let another ILA or a processor start a capture, or learn that one happened (cross triggering). The skill does not offer them and says so. Measured: on UltraScale+ Vivado accepts the setting, reads it back as on, and builds no trigger ports; on Versal it reads back off. Vivado's guide (UG908) says to use HDL instantiation when you need them wired.
- **Memory type** (AI: `-memory_type`): Versal only. Block RAM is the default. UltraRAM stores the capture in UltraRAM and needs depth 4096 or more (AI: `bram` or `uram`).
- **Replace existing** (AI: `-replace_existing`): remove the old `u_ila` cores first, then insert. If the new cores cannot be built, the old ones are put back as they were. If they cannot be put back, the error says so: reopen the checkpoint.
- **Allow duplicate probes** (AI: `-allow_duplicate`): probe a net again that an existing debug core already probes. Default is to skip it. Only if you ask for it.
- **Comparators** (AI: `-comparators`): how many compare units each probe has for trigger and capture conditions, 1 to 16, one count for every probe. Vivado's netlist insertion cannot give different probes different counts (it builds one per probe whatever is asked). Two cores on the same clock can do it, each with its own count; this skill plans one core per clock, so it does not build them. Say that plainly when asked. Offered only with advanced trigger or storage qualification, as in the Vivado wizard. Storage qualification needs at least 2, so I raise 1 to 2 and say so. Default is automatic.
- **Probe type** (AI: `-probe_type` for the whole core, `-probe_types` per signal): Both data and trigger (the default) stores the probe and lets you trigger on it. Data only stores it. Trigger only lets you trigger on a signal without storing it. Different signals can have different types: each signal has its own probe port. At least one signal must be stored, so trigger only on every signal of a core is refused (AI: `data_and_trigger`, `data` or `trigger`).
- **Change a setting later**: for a core I inserted, also after you saved and reopened the design, I change depth, memory type, storage qualification, advanced trigger, comparators, pipeline stages and probe type in place (AI: `ila::change`). Probes and clock stay. I show the change first, and I refuse a core I did not insert. Detail: `references/change-settings.md`.

<!-- help-topic: refusals -->
## If something is refused

Each refused net has a code and a reason. The code is for the AI, to find the row. Tell the person the reason and what they can do, in words. Give the code only if they ask. Nothing refused is inserted. The accepted nets still go in unless the whole plan is refused.

| Code | What it means | What you can do |
|---|---|---|
| `N01` | The name is not in the netlist. | Check the spelling. I show close names ("Did you mean"). Synthesis can rename or remove a signal: a register that holds a constant or drives nothing is removed, and logic between gates can be merged away. |
| `N02` | The net has no driver. | Probe a signal that is driven. |
| `N03` | It is not a plain signal. | Probe a normal signal or register. |
| `N04` | It is a clock net. | Pick a data signal. |
| `N05` | It is a constant. | A constant never changes, so nothing to see. Probe the signal that sets it. |
| `N06` | It is inside a debug core. | Probe your own logic instead. |
| `N07` | No capture clock was found. | Probe a register output that runs on a clock. |
| `N08` | It is driven from more than one clock. | Probe a register on one clock instead. |
| `N10` | Too much on one clock for one ILA: a signal wider than one probe port (4096 bits on UltraScale and UltraScale+, 1024 on Versal), more than 65,536 bits on UltraScale and UltraScale+, or too many signals for the comparators (each signal is a probe port: at most 1024 at one comparator, 512 with capture control, 64 at 16). | Probe fewer bits or fewer signals on that clock, or ask for fewer comparators. |
| `N12` | The net goes straight to an output port. | Probe the register or logic that feeds the port. |
| `N13` | The net sits between synchronizer registers (Vivado refuses it). | Probe the last stage. |
| `N15` | A hardened connection with no fabric route (CIPS to NoC). | Probe a register in the fabric logic. |
| `N16` | A combinational net, skipped by a wildcard. | Name it if you really want it. |
| `N17` | The request is too broad. | Ask for a module, a register, or a list of names. |
| `N18` | The DFX slot holds no logic (greybox). | Nothing to insert. This is fine. |
| `N19` | Versal DFX: the clock has no name inside the partition. | Tell me which clock inside the partition to use. |
| `N20` | An existing debug core already probes this net. I skip it, so it is not captured twice. | Nothing to do: the old core has it. To probe it again anyway, tell me, and I probe it again. |
| `N99` | Vivado silently skipped the net. | I drop it and build the core with the rest. |

Other stops:

| Code | What it means | What you can do |
|---|---|---|
| `ILA_REFUSED_PLACED` | Your design is already placed or routed. | Give me the synthesized version, or tell me to change it as it is. |
| `ILA_REFUSED_IMPL_RUN` | This is the implementation run of the open project. | Open the synthesized design (`open_run synth_1`). |
| `ILA_BLOCKED_NO_CIPS` | Versal design with no CIPS and no PS Wizard (the processor block, PS9 or PS11). | Your source needs a CIPS or PS Wizard with a PL reset. I add nothing. |
| `ILA_ADVISE_NOT_ROUTED` | The design is not routed, so there is no timing to advise on (read-only advice). | Open the routed checkpoint and ask again. |
| `ILA_OVER_BUDGET` | The capture memory does not fit. | Lower the depth, or probe fewer bits. The plan gives the largest depth that fits. On Versal, UltraRAM is another choice (depth 4096 or more). I change none of them until you pick one. |
| `ILA_DEPTH_RANGE` | Vivado does not accept that depth. | Use a power of two from 1024 to 131072. |
| `ILA_OPTION_RANGE` | An option value is not accepted. A `-name_prefix` may hold only letters, digits and underscore. | Use a value in the range the message names. |
| `ILA_PROBES_NOT_READY` | You asked for the probes file before `opt_design` ran in your session (Vivado 12-5829: the cores are not built yet). | The probes file can be written only after the optimize step. Nothing was written. (AI: when the user asked for the probes file, run `opt_design`, then write the file.) |
| `ILA_PROBES_EXISTS`, `ILA_PROBES_NO_DIRECTORY`, `ILA_PROBES_FAILED` | The probes file already exists (I never overwrite it unless you say so), the folder for it does not exist, or Vivado could not write it. | I name the file or folder and what Vivado said. Nothing was overwritten or written. |
| `ILA_PROPERTY_MISMATCH` | Vivado accepted an option and kept the old value. | Turn the option off. |
| `ILA_OPTION_UNSUPPORTED` | You asked for trigger in or trigger out. Measured: on UltraScale+ Vivado builds no trigger ports from the setting; on Versal it reads back off. | Nothing was planned or changed. Vivado's guide (UG908) says to use HDL instantiation when you need them wired. |
| `ILA_CHANGE_NOT_SKILL_CORE`, `ILA_CHANGE_CORE_EDITED`, `ILA_CHANGE_NO_MARKER` | A setting change was asked for a core this skill cannot prove it made: not in its record of this design (the design property `ILA_SKILL_CORES`), probes or clock changed since, or no record at all (an older design, or a project reopened after `save_constraints`). | I change only my own cores. Nothing was changed. |
| `ILA_CHANGE_NEEDS_DATA_PROBE` | Trigger only on every probe port of the core: no probe would store data, and Vivado would fail at `opt_design`. | Keep data, or data and trigger. Nothing was changed. |
| `ILA_CHANGE_SHARED_PORT` | Part of a signal (some bits of a bus) was given its own probe type, but those bits share one probe with the rest of the signal. A core built by an earlier version of the skill can also have several signals on one probe. | Plan the ILA again with replace. Nothing was changed. |
| `ILA_CHANGE_NET_NOT_ON_CORE` | The name matches signals in the design, but this core does not probe any of them. | Name a signal the core probes. Nothing was changed. |
| `ILA_CHANGE_NO_PROBES_FILE` | The probes file for the proof of a change cannot be read. | The check needs a probes file written after `opt_design`. Nothing was checked. |
| `ILA_CHANGE_COMPARATOR_LIMIT` | Probe ports times comparators would be over 1024, which Vivado refuses at `opt_design`. | Ask for fewer comparators. Nothing was changed. |
| `ILA_CHANGE_DFX` | A setting change on a design with a reconfigurable partition (DFX). Not measured, so not done. | Change the setting in the module source. Nothing was changed. |
| `ILA_CHANGE_NOT_BUILT` | The proof of a change was asked before `opt_design` built the core. | The check can run only after `opt_design` has built the core. Nothing was checked. |
| `ILA_NOTHING_INSERTED` | Nothing could be probed: every net was refused, and at least one of them because an existing core already probes it (N20). | Read the reasons above and change the request. |
| `ILA_BAD_ITEM` | A name in the request is empty. | Say which net, register or signal you mean. |
| `ILA_NO_PLAN` | Apply or next steps was called with no accepted plan in this session. | Make a plan first. Nothing was run. |
| `ILA_NOT_APPLIED` | Next steps or the probes file was asked for, but apply has created no core in this session (or the cores were removed again). | Run apply first. Nothing was run. |
| `ILA_NO_INPUT_CHECK` | A step needs the family of your part (UltraScale, UltraScale+ or Versal) and no design is open, or Vivado gave no part. | Open the design; the AI then checks it again. Nothing was run. |
| `ILA_APPLY_FAILED` | Next steps was called after an apply that failed, so the design holds only some of the planned cores. | Remove the cores this session added and begin again (AI: `ila::reset`), fix the cause, then plan and apply again. Nothing was run. |
| `ILA_BAD_OPTION` | An option name the AI wrote is not known, or an option has no value. | The AI fixes the option from the list the message gives. Nothing was planned. |
| `ILA_NAME_IN_USE` | A core with that name exists. | Remove the cores this session added and begin again, or pick another core name prefix (AI: `ila::reset`). |
| `ILA_CREATE_FAILED`, `ILA_OPTION_REFUSED`, `ILA_CONNECT_MISMATCH` | Vivado refused a step. | I quote Vivado's own text. |
| `ILA_HUB_CORE_LIMIT` | Versal: one hub connects at most 64 cores. | Plan fewer cores (fewer clocks). |
| `ILA_PLAN_TIMEOUT` | The plan took too long. | Ask for fewer or more specific items. |
| `ILA_BLOCKED_DFX_NO_HUB` | Versal DFX: no debug hub in that module, or none in the static part (also for a module-only request). | Your source needs an AXI Debug Hub there. |
| `ILA_BLOCKED_DFX_NO_BSCAN` | UltraScale+ DFX: the partition lacks the debug ports, or the skill refuses the design (see the DFX line in the Where are you now section). | Your source must change. I add nothing. |
| `ILA_BLOCKED_DFX_UNSUPPORTED` | UltraScale+ DFX: Vivado refuses, or the design already has debug. | Instantiate the ILA in the module source, or give me the design before any debug was added. |
| `ILA_BLOCKED_DFX_STATIC_IN_CHILD` | Versal DFX: static probes asked while a child configuration is open. | Open the parent configuration for static probes. |
| `ILA_REFUSED_FLAGGED`, `ILA_REFUSED_NO_BLACKBOX`, `ILA_REFUSED_STATIC_FIRST_VERSAL`, `ILA_STATIC_FIRST_NOT_CHECKED`, `ILA_STATIC_FIRST_CORE_MISSING`, `ILA_NO_APPLY`, `ILA_BAD_OPTION` | The UltraScale+ DFX static-first route refused. | See `references/routes.md`. |

Warnings and notes you may see:

| Code | What it means |
|---|---|
| `W01` | You named a combinational net. It was accepted, but its value can glitch. |
| `W02` | The memory budget was not checked, because the part reports no block RAM count, or a DFX partition has no pblock to check against. Nothing was refused. Vivado may fail later if the capture memory does not fit. |
| `A01` | Two names for one net. It is probed once. |
| `A02` | A probed net has other names. The shortest one is shown. |
| `M01` | State of the `config_flows -mark_debug` setting. MARK_DEBUG nets may be gone if it is off. |
| `M02` | MARK_DEBUG is on every bit of a bus. Marking one bit marks all. |
| `M03` | A marked net sits in a partly driven vector. |
| `M04` | A probed net is on a DFX partition boundary. |
| `M05` | UltraScale+ DFX: probing nets inside a module means only that module links later. Probe boundary nets if a second module must link, and give each such net a load in the static part (one board run linked a second module only after that). |
| `C01` | A clock comes from an MMCM or PLL. Check its locked signal. |
| `C02` | Hub clock report. |
| `C03` | A debug hub that was already there sits on a clock that may stop. I leave it alone. |
| `D01` | UltraScale+ DFX on Vivado 2026.2 or later: the skill goes on. |

<!-- help-topic: clocks -->
## Clocks and the debug hub

- One ILA watches one clock. If your signals run on three clocks, you get three ILAs, and each core shows whether its clock is free (always running), gated (can stop) or unknown.
- A clock is free only when I can trace it back to an input pin or the processor clock and nothing on the way can be switched off by your logic. That covers the enable, clear, reset, power-down and select pins of clock buffers, MMCMs and PLLs. If one of those pins is driven by your logic, or by an input pin of the design (a reset button or switch stops the clock while it is held), the clock is gated. I still plan the probes and warn you.
- If I cannot tell, the clock is unknown and I warn you. That happens for a cell I do not know, for a clock made by a LUT or a register, and for a pin that sets the clock speed or source and is driven by logic. I never call a clock free when I could not trace it.
- Signals that sit on the same physical clock share one ILA, even if the clock has different names in different modules.
- The debug hub needs a clock that keeps running and is stable (UG908). If the clock stops, Hardware Manager reports that it found no debug hub and you cannot read the ILA.
- If I create the hub, Vivado chooses its clock: the clock of the first ILA whose clock is connected. I connect an ILA on a free clock first, so the hub lands on a free clock when there is one. I never move the hub, and I tell you which clock it got.
- If your design already has a hub, I never move it. I warn you when its clock may stop.
- A gated clock is not refused. I plan the probes and warn you, naming the clock buffer, the pin and the net that can stop it. The check reads the netlist and cannot prove the clock stops, so check the clock before hardware.
- Versal: I do not move the hub clock. The one exception is Vivado 2026.2 in a checkpoint session, when the hub clock pin has no driver: I connect it to the ILA clock and warn you.

<!-- help-topic: limits -->
## Limits and versions

Proven (agent runs, board runs, software flow):

- UltraScale and UltraScale+ flat designs: agent trials, plus board runs on KCU116, VCU128 and VCU108.
- Versal flat designs: agent trials, plus board runs on VMK180.
- Versal DFX: a software flow, plus one board run on a VCK190 on Vivado 2026.1 (a full image, then one partial image; the ILA still captured and triggered after the swap).
- UltraScale+ DFX on Vivado 2026.2, for a design with no debug before insertion: a software flow on one KU5P design, plus board runs on KCU116, VCU108 and VCU128 (a full image and up to five partial swaps); the ILA captured and triggered after each swap. All on 2026.2 daily builds, not a release. Other parts are not proven. A design with existing debug is refused.

Not proven or not done:

- Versal project mode beyond the tested design, timing or area reports from this skill's normal flow.
- Capture memory was measured to depth 131072 on UltraScale+ and Versal (Vivado 2026.1).
- The memory budget is a formula calibrated on measured builds, compared with the block RAM the part has left (RAMB and FIFO cells in the design, and ILA cores that are there but not built yet, are counted as used). It is exact only for the cases measured. When the part reports no block RAM count, I say so instead of guessing.
- Only the clock pins listed in the clocks section are checked. A pin I do not check (for example the enable of a GT reference clock buffer) can still stop a clock that I call free.

Vivado versions: the skill needs Vivado 2026.1 or later with the Vivado MCP server. UltraScale+ DFX insertion needs 2026.2 or later.

<!-- help-topic: troubleshooting -->
## Troubleshooting

1. **"I only have RTL."** Run synthesis yourself, save a checkpoint, give it to me.
2. **"Design is placed".** Open the post-synthesis checkpoint instead. In the implementation run of an open project, open the synthesized design with `open_run synth_1`.
3. **A signal is not found.** Synthesis may have renamed it (for example `count` can become `count_0`) or removed it. I try the register `<name>_reg` first. If you use `config_flows -mark_debug` set to disable or synthesis_only, marked nets can be gone.
4. **"Too broad".** Name a module or give signal names.
5. **Does not fit in memory.** Lower the depth or probe fewer bits. The plan says the largest depth that fits.
6. **Vivado takes a long time.** `opt_design` takes minutes, and a Versal full flow about 25 minutes. I keep waiting and checking. Nothing is wrong.
7. **Versal: "no CIPS".** The design needs a CIPS or PS Wizard (the processor block, PS9 or PS11) with a PL reset enabled. This is a source change.
8. **DFX design refused.** Read the section on limits. The message names the change your source needs.
9. **Hardware Manager finds no ILA probes, or no debug hub.** The first means the probes file (the `.ltx` file) is not set on the device. The second means the debug hub clock is not running (see the clocks section).
10. **I loaded the library twice.** Nothing is lost: loading the library again keeps the plan, the facts, the cores this session made and the log. To begin again on purpose, the AI runs `ila::reset`: it removes the cores this session made and forgets the facts and the log. Checking the input again starts a new design.

<!-- help-topic: next -->
## What to ask me next

Copy and adjust:

- "I have never used this. How does ILA insertion work and what do I need to give you?"
- "Add an ILA on the 32-bit register count in inputs/design.dcp and write the probes file to outputs/."
- "Probe everything marked for debug in u_dma, depth 4096."
- "Probe all registers in module u_fifo."
- "What can I probe in this design?"
- "What are my options for capture depth?"
- "Why was my signal refused?"
- "My design uses DFX. What can you do?"
- "What do I do in Hardware Manager now?"
