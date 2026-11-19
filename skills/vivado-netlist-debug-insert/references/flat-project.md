<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Flat design in an open Vivado project

For a user who works in a Vivado project (a project with runs), not in a checkpoint session. The skill adds the cores in the open synthesized run and saves nothing. The user runs the save and the implementation run. Facts below were measured on Vivado 2026.1 and 2026.2 in a flat KCU116 project, software only, with a script that ran `save_constraints`, the same Vivado command the user runs now.

## Steps

1. The user opens the synthesized run: `open_run synth_1`.
2. `ila::check_input`, `ila::plan`, `ila::apply`, as in a checkpoint session.
3. `ila::apply` prints the facts; `ila::next_steps` prints them again: the cores are in the open run only, nothing was saved or built, and the save keeps them. An AI-only note names the commands, for you, when the user asks how or names a step. The skill runs none of them:
   - `save_constraints` (the save)
   - `reset_run impl_1`, then `launch_runs impl_1 -to_step write_bitstream` (the run builds the cores and writes the probes file beside the image)
   - in the implemented design, `write_debug_probes -force <file>.ltx` (or `ila::write_probes <file>`)
4. Do not list, recommend or offer these steps: pass on the facts (the cores are in the open run, nothing was saved or built). If the project has one constraint file, `ila::next_steps` also prints a WARNING: `save_constraints` rewrites it (the debug lines go in next to the user's own). Pass that warning on.

## How a project is told from a checkpoint

`current_project` is not empty in a checkpoint session (it returns `project_<checkpoint>`). A project is open only when `get_runs` is not empty. `ila::next_steps` uses that test to choose between the project facts and the checkpoint facts.

## What save_constraints did (flat UltraScale+ KCU116 project, one user XDC, `impl_1` complete before)

| | Vivado 2026.1 | Vivado 2026.2 |
|---|---|---|
| Where the debug lines went | appended to the user's own XDC (`kcu116.xdc`, 1124 to 2250 bytes) | a new file `p1_top_debug.xdc` (USED_IN implementation, 1350 bytes) |
| The user's XDC | debug lines added, own lines kept | rewritten with single blanks (1124 to 1121 bytes), no debug lines, no change of meaning |
| `synth_1` after the save | up to date (NEEDS_REFRESH 0) | up to date |
| `impl_1` after the save | Out-of-date (NEEDS_REFRESH 1) | Out-of-date |
| After close and reopen | same | same |

- With a separate debug file as the constraint set target (USED_IN implementation), 2026.1 wrote the debug lines into it. 2026.2 wrote them to a new `p1_top_debug.xdc` and rewrote the target file (20 to 21 bytes, still empty of debug lines).
- A rerun of `impl_1` after the save (to `write_bitstream`) completed on both versions. `synth_1`'s checkpoint kept its time stamp. The implemented design lists `u_ila_0` and `dbg_hub`, and `write_debug_probes` there writes a probes file that names the probed bus.

## Order: save right after apply

Measured on both versions, same project:

- `opt_design` alone in the open synthesized design: `synth_1` and `impl_1` stay up to date.
- `save_constraints` first, then `opt_design`: `synth_1` up to date, `impl_1` out of date.
- `opt_design` first, then `save_constraints`: `synth_1` and `impl_1` both out of date. Synthesis would rerun.

So the user saves right after `ila::apply`, and runs no `opt_design` in the session before it. The skill itself runs no `opt_design`.

## The single constraint file edge case

A project with one XDC and no debug file has nowhere else to put the debug lines. On 2026.1 `save_constraints` appends them to the user's own XDC. On 2026.2 it rewrites the user's XDC (spacing only in the measured file) and adds a new debug file. Vivado does this, not the skill.

- Tell the user, in the report: the save may change their own XDC, they should keep a copy first and look at the difference before they commit the file.
- To avoid the change to their own file, the user can add a separate empty XDC as the constraint set target before the save. 2026.1 then writes into that file. 2026.2 still rewrote the target file and wrote a new debug file (measured, 20 to 21 bytes).
- The library does not back up the file.

## Not measured

- A flat Versal project.
- A project with more than one constraint set, and a project whose debug file is used in synthesis, through a full run.
- A design with an existing debug core in the project.
- What a rerun of synthesis does with a core in a synthesis-used file.
- The steps as `ila::next_steps` prints them, run by a user after the skill (the measured runs ran the save from a script).
