# Elaboration Resolution Guides

Per-message fix references for Vivado synthesis elaboration (`[Synth 8-XXX]`).
Each guide provides the Vivado message pattern, root cause analysis, fix options
with before/after code examples, and verification steps.

## How to Use

When processing an elaboration message:

1. Look up the `msg_id` in the table below
2. Load the matching guide with `read_file`
3. Follow fix instructions, replacing placeholders with actual design names
4. Include the rationale in the generated report

## Individual Guides

Complex messages with multiple root cause patterns warranting dedicated files.

| Guide | Message IDs | Severity | Description |
|-------|------------|----------|-------------|
| [VLOG-689](VLOG-689.md) | 689, 7129 | WARNING | Port width mismatch |

## Grouped Guides

Messages sharing a common fix pattern, collected into a single file.

| Guide | Message IDs | Description |
|-------|------------|-------------|
| [case-statement](case-statement.md) | 506 | Case statement issues |
| [range-index](range-index.md) | 547, 549 | Range and index errors |
| [constant-expr](constant-expr.md) | 647 | Constant expression errors |
| [clock-edge](clock-edge.md) | 561 | Clock edge and event control issues |

## Adding New Guides

Create `<VLOG-XXX>.md` or `<VHDL-XXX>.md` (individual) or `<topic>.md` (grouped) with:

1. **Vivado Message** — exact `[Synth 8-XXX]` pattern
2. **Root Cause** — why Vivado emits this
3. **Fix Options** — before/after code in diff format

See any existing guide as a template.
