# Function index: vivado-spec-generator

Generated from the code by `vivado-ipi-assembler/tests/support/build_function_index.py`;
do not edit by hand. This is the complete list of what this skill can call. Read it
instead of `lib/*.tcl` or `scripts/*.py`: the source adds nothing a caller needs.

## Python scripts: `scripts/` (6)

Run with the configurator's `lib/ipcfg/vivado_python.sh`; `--help` prints the same usage.

### address_contracts.py

Generate DesignSpec address contracts from a supplied address-map CSV.

```text
usage: address_contracts.py [-h] --csv CSV [--skeleton] [--bind BIND]
                            [--out OUT]

Generate DesignSpec address contracts from a supplied address-map CSV.

options:
  -h, --help   show this help message and exit
  --csv CSV    supplied address-map CSV (master, target, base, size)
  --skeleton   print the binding JSON to fill in, then stop
  --bind BIND  filled binding JSON: blocks, memory, groups
  --out OUT    write the contracts JSON here (default: stdout)
```

### audit_spec.py

Deterministic Stage 1 checks with optional, non-authoritative model advice.

```text
usage: audit_spec.py [-h] --spec SPEC --source-root SOURCE_ROOT --sources
                     SOURCES --out OUT [--policy POLICY] [--advisory]
                     [--model MODEL] [--timeout TIMEOUT] [--repair]
                     [--exhaustive]

Deterministic DesignSpec checks with optional non-authoritative model advice.

options:
  -h, --help            show this help message and exit
  --spec SPEC
  --source-root SOURCE_ROOT
  --sources SOURCES
  --out OUT
  --policy POLICY       Optional advanced rules and fidelity review; not
                        required for the lab gate
  --advisory            Request model advice without changing verdict or
                        candidate
  --model MODEL         Model for optional advisory review; never called by
                        default
  --timeout TIMEOUT
  --repair              Retired: model-proposed automatic repair is prohibited
  --exhaustive          Retired: model review cannot grant acceptance
```

### check_design_spec.py

Generic, evidence-backed DesignSpec checks and machine-readable repair feedback.

```text
usage: check_design_spec.py [-h] --spec SPEC --policy POLICY --source-root
                            SOURCE_ROOT --validator VALIDATOR --report REPORT
                            [--stage {spec,implementation}]
                            [--repair-command REPAIR_COMMAND]
                            [--max-repairs MAX_REPAIRS]
                            [--repair-timeout REPAIR_TIMEOUT]

Generic, evidence-backed DesignSpec checks and machine-readable repair
feedback.

options:
  -h, --help            show this help message and exit
  --spec SPEC
  --policy POLICY
  --source-root SOURCE_ROOT
  --validator VALIDATOR
  --report REPORT
  --stage {spec,implementation}
  --repair-command REPAIR_COMMAND
                        JSON argv array; {spec} and {feedback} placeholders;
                        no shell
  --max-repairs MAX_REPAIRS
  --repair-timeout REPAIR_TIMEOUT
```

### spec_to_svg.py

spec_to_svg.py – Convert a JSON DesignSpec to a grouped SVG block diagram.

```text
usage: spec_to_svg.py [-h] [--dark] [--sources SOURCES] input [output]

Convert a JSON hardware spec to an SVG block diagram.

positional arguments:
  input              Input JSON spec file
  output             Output SVG file

options:
  -h, --help         show this help message and exit
  --dark             Use dark background theme
  --sources SOURCES  Source manifest (default: spec_sources.json beside the
                     input, if present)
```

### stage_report.py

Record a lab stage's execution time and gate evidence under outputs/.

```text
usage: stage_report.py [-h] --stage STAGE --out OUT [--audit AUDIT]
                       [--gate GATE] [--status {done,failed}]
                       [--blocker BLOCKER] [--note NOTE]
                       {start,finish}

Record a lab stage's execution time and gate evidence under outputs/.

positional arguments:
  {start,finish}

options:
  -h, --help            show this help message and exit
  --stage STAGE         stage number or name, e.g. 1
  --out OUT             the run's outputs/ directory
  --audit AUDIT         spec audit directory whose report.json must PASS
  --gate GATE           TOKEN=FILE: TOKEN must appear in FILE
  --status {done,failed}
                        force failed with --blocker; done is derived
  --blocker BLOCKER     what stopped the stage, when it failed
  --note NOTE           one line for the report
```

### validate_spec.py

Validate a DesignSpec's structure and implementation-independent contracts.

```text
usage: validate_spec.py [-h] [--canonicalize-out CANONICALIZE_OUT] spec

positional arguments:
  spec

options:
  -h, --help            show this help message and exit
  --canonicalize-out CANONICALIZE_OUT
                        write a copy with only lossless canonical alias
                        migrations applied
```
