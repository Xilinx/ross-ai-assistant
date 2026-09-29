#!/usr/bin/env python3
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
"""
gen_matlab_instr.py
Auto-generate an instrumented MATLAB function that tracks min/max of every
internal variable across all loop iterations.

Algorithm:
  1. Scan lines INSIDE the first outer for-loop only (pre-loop constants excluded).
  2. Detect every assignment: scalars (word = expr) and array elements (word(idx) = expr).
  3. Skip: loop counter variables, output arrays (coder.nullcopy), MATLAB keywords.
  4. For output arrays assigned a complex expression directly (no intermediate var),
     inject a named temp variable so the expression value is captured.
  5. Emit: tracker init block before the outer loop, inline tracking after each
     assignment, and fprintf RANGE lines before the final 'end'.

Usage:
  python gen_matlab_instr.py <src.m> <dst_instr.m> <prefix>

  prefix — string used in RANGE lines, e.g. 'fir_i', 'hb1_q', 'dds'

Example:
  python gen_matlab_instr.py fir_compute_i.m fir_compute_i_instr.m fir_i
  python gen_matlab_instr.py hb_fir_q.m      hb_fir_q_instr.m      hb1_q
  python gen_matlab_instr.py dds_compute.m   dds_compute_instr.m   dds
  python gen_matlab_instr.py mix.m           mix_instr.m           mix
"""

import re, sys

# ── constants ─────────────────────────────────────────────────────────────────

MATLAB_KW = {
    'function', 'persistent', 'if', 'else', 'elseif', 'end', 'for', 'while',
    'break', 'continue', 'return', 'coder', 'fprintf', 'disp', 'warning',
    'error', 'switch', 'case', 'otherwise', 'try', 'catch', 'global',
}

# ── helpers ───────────────────────────────────────────────────────────────────

def get_indent(line):
    return len(line) - len(line.lstrip())

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
    Classify a stripped line as a variable assignment.
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

def rename_function(line):
    """Append _instr to the function name in a function declaration line."""
    return re.sub(
        r'(function\s+(?:\w+\s*=\s*)?)(\w+)(\s*\()',
        lambda m: m.group(1) + m.group(2) + '_instr' + m.group(3),
        line, count=1
    )

# ── main ──────────────────────────────────────────────────────────────────────

def gen_instr(src_path, dst_path, prefix):
    with open(src_path) as f:
        lines = f.readlines()

    loop_counters = find_loop_counters(lines)
    output_arrays = find_output_arrays(lines)
    first_for     = find_first_outer_for(lines)
    skip_names    = loop_counters | output_arrays | {'ans'}

    # ── Pass: scan only loop body lines to find trackable variables ──────────
    # (Pre-loop constants such as coefficient arrays and fi offsets are excluded.)

    tracked = {}      # name -> {is_array, is_int}   (ordered: insertion order)
    output_direct = {}  # line_index -> (tmp_varname, rhs_expr)
    tmp_counter   = [0]

    def make_tmp(base):
        tmp_counter[0] += 1
        return f'rng_{base}_{tmp_counter[0]}'

    for i, line in enumerate(lines[first_for:], start=first_for):
        s = line.strip()
        r = classify_assignment(s)
        if not r:
            continue
        name, kind, is_int = r

        # Output array element — check for output_direct injection BEFORE skip_names.
        # (output_arrays ⊆ skip_names, so skip_names guard must come after this block.)
        if name in output_arrays:
            if kind == 'array_elem':
                m = re.match(r'^\w+\s*\([^)=]*\)\s*=(?!=)\s*(.*)', s)
                rhs = m.group(1).rstrip(';').strip() if m else ''
                # Complex RHS (not just a single variable) → inject a named temp
                if rhs and not re.match(r'^\w+$', rhs):
                    tmp = make_tmp(name)
                    output_direct[i] = (tmp, rhs)
                    tracked.setdefault(tmp, {'is_array': False, 'is_int': is_int})
            continue   # never track the output array variable itself

        # Skip loop counters, keywords, etc.
        if name in skip_names or not name.isidentifier():
            continue

        # Regular tracked variable
        if name not in tracked:
            tracked[name] = {'is_array': kind == 'array_elem', 'is_int': is_int}
        elif kind == 'array_elem':
            tracked[name]['is_array'] = True  # promote to array

    # ── Generate instrumented file ────────────────────────────────────────────
    out = []

    # Pre-loop block: write as-is, rename function on first line
    for i, line in enumerate(lines[:first_for]):
        if i == 0:
            out.append(f'% AUTO-GENERATED by gen_matlab_instr.py  [prefix={prefix}]\n')
            line = rename_function(line)
        out.append(line)

    # Tracker initialisation (inserted just before outer loop)
    out.append('\n% ── Range tracking (auto-generated) ─────────────────────────────────\n')
    for name in tracked:
        out.append(f'mn_{name} = inf;  mx_{name} = -inf;\n')
    out.append('\n')

    # Loop body: write each line, insert tracking after assignments
    for i, line in enumerate(lines[first_for:], start=first_for):
        s    = line.strip()
        sp   = ' ' * get_indent(line)

        # Output-direct injection: replace complex RHS with tmp var
        if i in output_direct:
            tmp, rhs = output_direct[i]
            # Rewrite the original assignment to use tmp in RHS
            new_line = re.sub(r'(=(?!=)\s*)(.+?)(;?[ \t]*$)',
                              lambda m: m.group(1) + tmp + ';',
                              line.rstrip('\n'), count=1) + '\n'
            out.append(f'{sp}{tmp} = {rhs};\n')
            out.append(f'{sp}mn_{tmp} = min(mn_{tmp}, double({tmp}));  '
                       f'mx_{tmp} = max(mx_{tmp}, double({tmp}));\n')
            out.append(new_line)
            continue

        out.append(line)

        r = classify_assignment(s)
        if not r:
            continue
        name, kind, _ = r
        if name not in tracked:
            continue

        if tracked[name]['is_array']:
            out.append(f'{sp}mn_{name} = min(mn_{name}, min(double({name}(:))));  '
                       f'mx_{name} = max(mx_{name}, max(double({name}(:))));\n')
        else:
            out.append(f'{sp}mn_{name} = min(mn_{name}, double({name}));  '
                       f'mx_{name} = max(mx_{name}, double({name}));\n')

    # Insert RANGE fprintf block before the final 'end'
    last_end = None
    for i in range(len(out) - 1, -1, -1):
        if out[i].strip() == 'end':
            last_end = i
            break

    range_block = ['\n% ── Print captured ranges ───────────────────────────────────────────\n']
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

    # Summary
    int_vars = [n for n, v in tracked.items() if v['is_int']]
    arr_vars = [n for n, v in tracked.items() if v['is_array']]
    print(f'Generated : {dst_path}')
    print(f'Tracked   : {len(tracked)} variables — {list(tracked)}')
    print(f'  integers: {int_vars}')
    print(f'  arrays  : {arr_vars}')
    print(f'  excluded loop counters : {sorted(loop_counters)}')
    print(f'  excluded output arrays : {sorted(output_arrays)}')
    if output_direct:
        print(f'  injected temps (output-direct): {[v[0] for v in output_direct.values()]}')

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    gen_instr(sys.argv[1], sys.argv[2], sys.argv[3])
