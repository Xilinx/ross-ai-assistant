#!/usr/bin/env python3
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
"""
gen_matlab_instr.py
Auto-generate an instrumented MATLAB function from a design source file.

Two modes:

  --range     (default)  Track min/max of every internal variable across all loop
                         iterations and print RANGE lines.  Feeds Step 0b.3/0b.4
                         (integer-bit sizing, I).

  --quantize             Round every fractional signal-path variable to qF
                         fractional bits after each assignment.  The generated
                         function takes an extra trailing argument `qF`, so one
                         file serves an entire precision sweep.  Feeds Step 0b.2b
                         (fractional-bit sizing, F).

The two modes answer different questions and are NOT interchangeable:
  min/max  ->  I  (how big does it get?)      -- measurable from two numbers
  sweep    ->  F  (how fine must it be?)      -- requires running the algorithm

Algorithm (shared by both modes):
  1. Scan lines INSIDE the first outer for-loop only (pre-loop constants excluded).
  2. Detect every assignment: scalars (word = expr) and array elements (word(idx) = expr).
     MATLAB '...' line continuations are joined first, so multi-line statements are
     classified as one logical statement and injection lands AFTER the final line.
  3. Skip: loop counter variables, output arrays (coder.nullcopy), MATLAB keywords.
  4. For output arrays assigned a complex expression directly (no intermediate var),
     inject a named temp variable so the expression value is captured.
  5. Emit mode-specific instrumentation after each assignment.

Usage:
  python gen_matlab_instr.py <src.m> <dst.m> <prefix> [--range | --quantize]

  prefix — string used in RANGE lines, e.g. 'fir_i', 'hb1_q', 'dds'
           (ignored in --quantize mode)

Example:
  python gen_matlab_instr.py fir_compute_i.m fir_compute_i_instr.m fir_i
  python gen_matlab_instr.py hb_fir_q.m      hb_fir_q_instr.m      hb1_q
  python gen_matlab_instr.py fir_compute_i.m fir_compute_i_q.m     fir_i --quantize
"""

import re, sys

# ── constants ─────────────────────────────────────────────────────────────────

MATLAB_KW = {
    'function', 'persistent', 'if', 'else', 'elseif', 'end', 'for', 'while',
    'break', 'continue', 'return', 'coder', 'fprintf', 'disp', 'warning',
    'error', 'switch', 'case', 'otherwise', 'try', 'catch', 'global',
}

# Rounding helper injected into --quantize output.
# round() is round-half-away-from-zero in MATLAB, which is the closest single-line
# match to AP_RND_CONV for the purpose of a width sweep.  The half-way ties differ,
# but ties are measure-zero on real signal data and do not move the chosen F.
Q_HELPER = 'q_ = @(x_, F_) round(x_ .* 2^F_) ./ 2^F_;   % quantize to F_ frac bits\n'

# ── helpers ───────────────────────────────────────────────────────────────────

def get_indent(line):
    return len(line) - len(line.lstrip())

def strip_comment(s):
    """Remove a trailing MATLAB comment, ignoring % inside single-quoted strings."""
    out, in_str = [], False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "'":
            # transpose vs string-open is ambiguous; treat as string toggle only
            # when the previous non-space char cannot end an operand
            prev = out[-1] if out else ''
            if in_str or not (prev.isalnum() or prev in ")]}._'"):
                in_str = not in_str
        elif c == '%' and not in_str:
            break
        out.append(c)
        i += 1
    return ''.join(out)

def statement_end(lines, i):
    """
    Index of the LAST physical line of the logical statement starting at line i.
    Follows MATLAB '...' continuations.  Returns i when the statement is one line.
    """
    j = i
    while j < len(lines) - 1 and strip_comment(lines[j].rstrip('\n')).rstrip().endswith('...'):
        j += 1
    return j

def logical_line(lines, i, end):
    """Join lines i..end into one logical statement with '...' markers removed."""
    parts = []
    for k in range(i, end + 1):
        s = strip_comment(lines[k].rstrip('\n')).rstrip()
        if s.endswith('...'):
            s = s[:-3]
        parts.append(s.strip())
    return ' '.join(p for p in parts if p)

def find_loop_counters(lines):
    """Return set of variable names used as for-loop counters."""
    counters = set()
    for line in lines:
        m = re.match(r'\s*for\s+(\w+)\s*=', line)
        if m:
            counters.add(m.group(1))
    return counters

def find_output_arrays(lines):
    """Return set of names declared with coder.nullcopy or coder.const."""
    arrays = set()
    for line in lines:
        m = re.match(r'\s*(\w+)\s*=\s*coder\.(nullcopy|const)', line)
        if m:
            arrays.add(m.group(1))
    return arrays

def find_first_outer_for(lines):
    """Return index of the first top-level 'for' statement."""
    for i, line in enumerate(lines):
        if re.match(r'\s*for\s+\w+\s*=', line):
            return i
    return len(lines) - 2

def classify_assignment(stripped):
    """
    Classify a stripped LOGICAL line as a variable assignment.
    Returns (varname, kind, is_integer) or None.
      kind = 'scalar'     — plain assignment:  var = expr
             'array_elem' — indexed assignment: var(idx) = expr
    is_integer = True when RHS uses intN(), uintN(), or bitsliceget().
    """
    if not stripped or stripped.startswith('%'):
        return None
    # First token must be an identifier, not a keyword
    first = re.match(r'^(\w+)', stripped)
    if not first or first.group(1).lower() in MATLAB_KW:
        return None

    # Array element: word(stuff) = (not ==)
    m = re.match(r'^(\w+)\s*\([^)=]*\)\s*=(?!=)\s*(.*)', stripped)
    if m:
        name, rhs = m.group(1), m.group(2).rstrip(';').strip()
        is_int = bool(re.search(r'\bint\d+\b|\buint\d+\b|\bbitsliceget\b', rhs))
        return (name, 'array_elem', is_int)

    # Scalar: word = (not ==)
    m = re.match(r'^(\w+)\s*=(?!=)\s*(.*)', stripped)
    if m:
        name, rhs = m.group(1), m.group(2).rstrip(';').strip()
        is_int = bool(re.search(r'\bint\d+\b|\buint\d+\b|\bbitsliceget\b', rhs))
        return (name, 'scalar', is_int)

    return None

def rename_function(line, suffix):
    """Append `suffix` to the function name in a function declaration line.
    Handles both 'function out = foo(...)' and 'function [a,b] = foo(...)'."""
    return re.sub(
        r'(function\s+(?:\[[^\]]*\]|\w+)\s*=\s*|function\s+)(\w+)',
        lambda m: m.group(1) + m.group(2) + suffix,
        line, count=1
    )

def add_param(line, param):
    """Append a trailing parameter to a MATLAB function declaration's arg list."""
    raw = line.rstrip('\n')
    m = re.match(r'^(\s*function\b[^(]*\()([^)]*)(\).*)$', raw)
    if m:
        head, params, tail = m.group(1), m.group(2).strip(), m.group(3)
        params = f'{params}, {param}' if params else param
        return f'{head}{params}{tail}\n'
    # No argument list at all: 'function [a,b] = foo'
    m = re.match(r'^(\s*function\b.*?\w)(\s*)$', raw)
    if m:
        return f'{m.group(1)}({param}){m.group(2)}\n'
    return line

# ── main ──────────────────────────────────────────────────────────────────────

def gen_instr(src_path, dst_path, prefix, mode='range'):
    if mode not in ('range', 'quantize'):
        raise ValueError(f"mode must be 'range' or 'quantize', got {mode!r}")

    with open(src_path) as f:
        lines = f.readlines()

    loop_counters = find_loop_counters(lines)
    output_arrays = find_output_arrays(lines)
    first_for     = find_first_outer_for(lines)
    skip_names    = loop_counters | output_arrays | {'ans'}

    # ── Pass 1: scan loop body to find trackable variables ────────────────────
    # (Pre-loop constants such as coefficient arrays and fi offsets are excluded.)

    tracked       = {}   # name -> {is_array, is_int}   (insertion-ordered)
    output_direct = {}   # start_line_index -> (tmp_varname, lhs, rhs)
    tmp_counter   = [0]

    def make_tmp(base):
        tmp_counter[0] += 1
        return f'rng_{base}_{tmp_counter[0]}'

    i = first_for
    while i < len(lines):
        end = statement_end(lines, i)
        s   = logical_line(lines, i, end)
        r   = classify_assignment(s)
        if not r:
            i = end + 1
            continue
        name, kind, is_int = r

        # Output array element — check for output_direct injection BEFORE skip_names.
        # (output_arrays ⊆ skip_names, so the skip_names guard must come after this.)
        if name in output_arrays:
            if kind == 'array_elem':
                m = re.match(r'^(\w+\s*\([^)=]*\))\s*=(?!=)\s*(.*)', s)
                lhs = m.group(1).strip() if m else ''
                rhs = m.group(2).rstrip(';').strip() if m else ''
                # Complex RHS (not just a single variable) → inject a named temp
                if rhs and not re.match(r'^\w+$', rhs):
                    tmp = make_tmp(name)
                    output_direct[i] = (tmp, lhs, rhs)
                    tracked.setdefault(tmp, {'is_array': False, 'is_int': is_int})
            i = end + 1
            continue

        # Skip loop counters, keywords, etc.
        if name in skip_names or not name.isidentifier():
            i = end + 1
            continue

        # Regular tracked variable
        if name not in tracked:
            tracked[name] = {'is_array': kind == 'array_elem', 'is_int': is_int}
        elif kind == 'array_elem':
            tracked[name]['is_array'] = True  # promote to array
        i = end + 1

    # ── Pass 2: emit the instrumented file ────────────────────────────────────
    suffix = '_instr' if mode == 'range' else '_q'
    out    = []

    def inject(name, sp):
        """Mode-specific instrumentation emitted after an assignment to `name`."""
        info = tracked[name]
        if mode == 'range':
            if info['is_array']:
                return [f'{sp}mn_{name} = min(mn_{name}, min(double({name}(:))));  '
                        f'mx_{name} = max(mx_{name}, max(double({name}(:))));\n']
            return [f'{sp}mn_{name} = min(mn_{name}, double({name}));  '
                    f'mx_{name} = max(mx_{name}, double({name}));\n']
        # quantize: integer-valued variables are already on an exact grid
        if info['is_int']:
            return []
        return [f'{sp}{name} = q_({name}, qF);\n']

    # Pre-loop block: written as-is, function declaration rewritten on first line
    for idx, line in enumerate(lines[:first_for]):
        if idx == 0:
            out.append(f'% AUTO-GENERATED by gen_matlab_instr.py  '
                       f'[mode={mode}  prefix={prefix}]\n')
            line = rename_function(line, suffix)
            if mode == 'quantize':
                line = add_param(line, 'qF')
        out.append(line)

    # Mode-specific preamble, inserted just before the outer loop
    if mode == 'range':
        out.append('\n% ── Range tracking (auto-generated) ───────────────────────────────\n')
        for name in tracked:
            out.append(f'mn_{name} = inf;  mx_{name} = -inf;\n')
    else:
        out.append('\n% ── Precision sweep (auto-generated) ──────────────────────────────\n')
        out.append(Q_HELPER)
    out.append('\n')

    # Loop body: copy each logical statement, then inject after its LAST line
    i = first_for
    while i < len(lines):
        end  = statement_end(lines, i)
        s    = logical_line(lines, i, end)
        sp   = ' ' * get_indent(lines[i])

        # Output-direct injection: hoist the complex RHS into a named temp.
        # The statement is re-emitted joined (continuations collapsed) so that
        # the hoist is always syntactically valid.
        if i in output_direct:
            tmp, lhs, rhs = output_direct[i]
            out.append(f'{sp}{tmp} = {rhs};\n')
            out.extend(inject(tmp, sp))
            out.append(f'{sp}{lhs} = {tmp};\n')
            i = end + 1
            continue

        for k in range(i, end + 1):
            out.append(lines[k])

        r = classify_assignment(s)
        if r and r[0] in tracked:
            out.extend(inject(r[0], sp))
        i = end + 1

    # ── RANGE print block (range mode only), before the final 'end' ───────────
    if mode == 'range':
        last_end = None
        for k in range(len(out) - 1, -1, -1):
            if out[k].strip() == 'end':
                last_end = k
                break

        range_block = ['\n% ── Print captured ranges ─────────────────────────────────────────\n']
        for name, info in tracked.items():
            int_flag = 1 if info['is_int'] else 0
            range_block.append(
                f"fprintf('RANGE {prefix}_{name:<22s}"
                f"  min=%14.8f  max=%14.8f  integer={int_flag}\\n', "
                f"mn_{name}, mx_{name});\n"
            )

        if last_end is not None:
            out = out[:last_end] + range_block + [out[last_end]]
        else:
            out.extend(range_block)

    with open(dst_path, 'w') as f:
        f.writelines(out)

    # ── Summary ───────────────────────────────────────────────────────────────
    int_vars = [n for n, v in tracked.items() if v['is_int']]
    arr_vars = [n for n, v in tracked.items() if v['is_array']]
    print(f'Generated : {dst_path}   [mode={mode}]')
    print(f'Tracked   : {len(tracked)} variables — {list(tracked)}')
    print(f'  integers: {int_vars}')
    print(f'  arrays  : {arr_vars}')
    print(f'  excluded loop counters : {sorted(loop_counters)}')
    print(f'  excluded output arrays : {sorted(output_arrays)}')
    if output_direct:
        print(f'  injected temps (output-direct): {[v[0] for v in output_direct.values()]}')
    if mode == 'quantize':
        qv = [n for n in tracked if not tracked[n]['is_int']]
        print(f'  quantized ({len(qv)}): {qv}')
        print(f'  NOTE: call as <func>_q(..., qF) — qF is the fractional-bit count.')

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    argv = sys.argv[1:]
    mode = 'range'
    for flag, name in (('--quantize', 'quantize'), ('--range', 'range')):
        if flag in argv:
            mode = name
            argv.remove(flag)
    if len(argv) != 3:
        print(__doc__)
        sys.exit(1)
    gen_instr(argv[0], argv[1], argv[2], mode)
