# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Read literal XGUI labels and tooltips as data; never execute vendor Tcl."""
import re
from pathlib import Path


def words(text):
    """Conservative subset of Tcl words, with no substitution or evaluation."""
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text[i] == '{':
            depth, i = 1, i + 1
            start = i
            while i < len(text) and depth:
                if text[i] == '\\':
                    i += 2
                    continue
                depth += (text[i] == '{') - (text[i] == '}')
                i += 1
            if depth:
                return []
            result.append(text[start:i - 1])
        elif text[i] == '"':
            i += 1
            start = i
            while i < len(text) and text[i] != '"':
                if text[i] == '\\':
                    return []  # Escapes require a fuller parser; leave unavailable.
                i += 1
            if i == len(text):
                return []
            result.append(text[start:i])
            i += 1
        else:
            while i < len(text) and not text[i].isspace():
                i += 1
            result.append(text[start:i])
    return result


def literal(value):
    return bool(value) and not any(c in value for c in '$[]\\;')


def extract(path):
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        return {}
    text = path.read_text(errors='replace').replace('\\\n', ' ')
    handles, parameters, active = {}, {}, False
    for line in text.splitlines():
        proc = re.match(r'\s*proc\s+(\S+)\s', line)
        if proc:
            active = proc[1] == 'init_gui'
            handles = {}
        if not active:
            continue
        declaration = re.fullmatch(r'\s*set\s+(\w+)\s+\[\s*ipgui::add_(param|page|group|panel)\s+(.*?)\]\s*', line)
        if declaration:
            handle, kind, tail = declaration.groups()
            tokens = words(tail)
            options = {tokens[i]: tokens[i + 1] for i in range(len(tokens) - 1)
                       if tokens[i] in ['-name', '-parent']}
            name = options.get('-name', '')
            if not literal(name):
                continue
            parent = options.get('-parent', '')
            parent_handle = re.fullmatch(r'\$(\w+)|\$\{(\w+)\}', parent)
            context = list(handles.get(next((x for x in parent_handle.groups() if x), ''), {}).get('context', [])) if parent_handle else []
            if kind in ['page', 'group']:
                context.append(name)
            handles[handle] = dict(name=name, kind=kind, context=context)
            if kind == 'param':
                parameters.setdefault(name, {})['gui_context'] = context
            continue
        tooltip = re.fullmatch(r'\s*set_property\s+tooltip\s+(.+?)\s+(\$\w+|\$\{\w+\})\s*', line)
        if tooltip:
            values = words(tooltip[1])
            target = tooltip[2].lstrip('$').strip('{}')
            item = handles.get(target)
            if item and item['kind'] == 'param' and len(values) == 1 and literal(values[0]):
                parameters[item['name']]['help'] = ' '.join(values[0].split())
    return parameters


def update_dependencies(path):
    """Read literal update-procedure inputs and comparisons, without Tcl eval.

    These are source hints. They identify controllers and compared encodings;
    they do not prove which branch enables a field in the live configuration.
    """
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        return {}
    text = path.read_text(errors='replace')
    result = {}
    pattern = r'(?m)^\s*proc\s+update_PARAM_VALUE\.([A-Za-z0-9_]+)\s+\{([^}]*)\}\s+\{'
    for match in re.finditer(pattern, text):
        target, arguments = match.groups()
        depth, end = 1, match.end()
        while end < len(text) and depth:
            if text[end] == '\\':
                end += 2
                continue
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        if depth:
            continue
        body = text[match.end():end-1]
        if not re.search(r'set_property\s+enabled\s+', body):
            continue
        controls = [n for n in re.findall(r'PARAM_VALUE\.([A-Za-z0-9_]+)', arguments) if n != target]
        variables = dict(re.findall(r'set\s+(\w+)\s+\[get_property\s+value\s+\$\{PARAM_VALUE\.([A-Za-z0-9_]+)\}\s*\]', body))
        comparisons = []
        for condition in re.findall(r'(?:if|elseif)\s+\{([^{}]+)\}', body):
            for variable, name in variables.items():
                condition = re.sub(r'\$' + re.escape(variable) + r'\b', name, condition)
            if any(n in condition for n in controls):
                comparisons.append(condition.strip()[:160])
        if controls:
            result[target] = dict(controls=controls[:8], comparisons=comparisons[:6],
                                  scope='packaged_update_proc_not_live_enablement')
    return result


def numeric_transforms(path):
    """Read only a complete, literal three-statement multiply update.

    This is a source relationship, never evaluated Tcl or proof of live
    legality. Branches, extra statements and dynamic arithmetic stay unknown.
    """
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        return {}
    text = path.read_text(errors='replace')
    result, ambiguous = {}, set()
    pattern = r'(?m)^\s*proc\s+update_PARAM_VALUE\.([A-Za-z0-9_]+)\s+\{([^}]*)\}\s+\{'
    for match in re.finditer(pattern, text):
        target, arguments = match.groups()
        depth, end = 1, match.end()
        while end < len(text) and depth:
            if text[end] == '\\':
                end += 2
                continue
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        if depth:
            continue
        body = '\n'.join(line for line in text[match.end():end-1].splitlines()
                         if not line.lstrip().startswith('#')).strip()
        source = re.fullmatch(
            r'set\s+(\w+)\s+\[get_property\s+value\s+\$\{PARAM_VALUE\.([A-Za-z0-9_]+)\}\s*\]\s+'
            r'set\s+(\w+)\s+\[expr\s+\{int\(\$(\w+)\s*\*\s*(\d+(?:\.\d+)?)\)\}\s*\]\s+'
            r'set_property\s+value\s+\$(\w+)\s+\$\{PARAM_VALUE\.([A-Za-z0-9_]+)\}', body)
        if not source:
            continue
        variable, control, output, operand, factor, assigned, destination = source.groups()
        if variable != operand or output != assigned or destination != target or control == target:
            continue
        if not {target, control} <= set(re.findall(r'PARAM_VALUE\.([A-Za-z0-9_]+)', arguments)):
            continue
        factor = float(factor)
        if not 0 < factor <= 1e12:
            continue
        if control in result or control in ambiguous:
            result.pop(control,None)
            ambiguous.add(control)
            continue
        result[control] = dict(target='CONFIG.'+target, factor=factor, rounding='int',
                               scope='packaged_literal_update_not_live', source_file=str(path))
    return result
