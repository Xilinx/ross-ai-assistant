---
name: aiesim-options
description: >
  Reference table of aiesimulator command-line options (UG1076, cross-checked against
  aiesimulator --help-list): switch, argument, default and notes.
metadata:
  version: "1.1.0"
---

# aiesimulator Options — Reference (UG1076)

Reference table of `aiesimulator` command-line options.

- **Source:** **UG1076** *AI Engine Tools and Flows User Guide*, 2026.2 (release 2026-11-11),
  "Simulator Options", cross-checked against `aiesimulator --help-list`
  (tool version 2026.2, SW build 2026-11-07). Where the two differ, the row says so.
- **Refresh:** re-run `aiesimulator --help-list` and diff it against this table
  when the Vitis version changes; update the version stamp above.

## Options

| Option (long / short) | Arg (default) | Purpose |
|---|---|---|
| `--pkg-dir` | `<dir>` | Set the package directory (the compiled `Work/`). e.g. `--pkg-dir=Work`. |
| `--input-dir` / `-i` | `<dir>` (`.`) | Input data directory (PLIO inputs resolved relative to it). e.g. `--input-dir=aie`. |
| `--output-dir` / `-o` | `<dir>` (`aiesimulator_output`) | Output data directory. It receives the output PLIO files (output PLIO paths are written relative to it), `aiesim_options.txt` and the run summary `default.aierun_summary`; with it set, no `aiesimulator_output/` folder is created. |
| `--dump-vcd` / `-w` | `<name>` | Dump VCD to a file (the simulator appends `.vcd`). e.g. `--dump-vcd=foo`. **Cannot be combined with `--online`.** |
| `--profile` | `arg` | Generate profiling data — all used cores, or `--profile="(col,row)(col,row)..."` for specific cores. Also enables kernel `printf` output on the console and may slow the sim slightly. Writes `profile_funct_<col>_<row>` and `profile_instr_<col>_<row>` files (`.txt` and `.xml`) per tile to the output folder (`<row>` is relative to the AIE array, so it can differ from the row the log prints). Not supported in hw_emu. |
| `--hang-detect-time` | `<ns>` (help shows `=0`; effective default `1000000`) | Exit once all active cores have been stalled for the given time (ns). Lower it to fail fast. e.g. `--hang-detect-time=10`. |
| `--simulation-cycle-timeout` | `<cycles>` (`0`) | Hard-stop the simulator after N cycles. e.g. `--simulation-cycle-timeout=10000`. **Unit:** AI Engine cycles per the options page; one spot in the simulator overview page says ns — use cycles. |
| `--display-run-interval` | `<ns>` (`0`) | Periodically print a time stamp every N ns of sim time (progress / liveness). e.g. `--display-run-interval=10`. |
| `--enable-memory-check` | `[=1]` (disabled) | Runtime program/data memory boundary access check. A violation prints as `[ERROR]` in the 2026.2 run log (the `--help-list` text says `[WARNING]`); treat it as an ERROR. Adds a Memory Violations tab to the run summary. |
| `--online` | — | Call `vcdanalyze` to parse VCD on-the-fly; optionally produce TEXT (`-text`, event.txt) or WDB (`-wdb`). **Cannot be combined with `--dump-vcd`.** |
| `--output-time-stamp` | `[=uniform_time_value]` | Control time values in the output file: `--output-time-stamp=no` (none) or `--output-time-stamp` (uniform ns). |
| `--options-file` / `-f` | `<file>` | Options file. Per UG1076 it selects which modules' data is dumped as VCD (use with `--dump-vcd`) and can carry `AIE_SIM_TIMEOUT=<cycles>`. e.g. `--options-file=sim_opts.txt`. |
| `--sim-mode` | `CRTS \| CERT \| CERT_PATCH` | Select PS wrapper simulation mode. **In the 2026.2 `--help-list` only; not in UG1076 2026.2.** |
| `--help-list` / `-h` | — | Display the list of available options. |
