# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tcl list text as data, in pure Python: parse it and write it the way Tcl 8.6 does.

The workflow reads Vivado's replies as Tcl lists and dicts and writes list-valued
parameters back. It used to shell out to a host `tclsh` for both, which Vivado does
not ship (only xtclsh) and which Vivado's bundled Python cannot assume. Nothing here
evaluates anything: braces, quotes and backslashes are interpreted only as Tcl's list
syntax does (Tcl_SplitList, TclScanElement/TclConvertElement), so a value is never a
command. tests/unit/test_tcl_data.py checks parity with a real tclsh when one exists.
"""

SPACE = ' \t\n\v\f\r'
SIMPLE_ESCAPES = {'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v'}
HEX = '0123456789abcdefABCDEF'


class TclListError(ValueError):
    pass


def backslash(text, i):
    """Substitute the backslash sequence at text[i] (a backslash); return (value, next index)."""
    if i + 1 >= len(text):
        return '\\', i + 1
    c = text[i + 1]
    if c in SIMPLE_ESCAPES:
        return SIMPLE_ESCAPES[c], i + 2
    if c == '\n':
        j = i + 2
        while j < len(text) and text[j] in ' \t':
            j += 1
        return ' ', j
    if c in 'xuU':
        limit = {'x': 2, 'u': 4, 'U': 8}[c]
        j, value = i + 2, 0
        while j < len(text) and j - (i + 2) < limit and text[j] in HEX:
            candidate = value * 16 + int(text[j], 16)
            if c == 'U' and candidate > 0x10FFFF:
                break
            value, j = candidate, j + 1
        if j == i + 2:
            return c, i + 2
        return chr(value), j
    if c in '01234567':
        j, value = i + 1, 0
        while j < len(text) and j - (i + 1) < 3 and text[j] in '01234567':
            value, j = value * 8 + int(text[j]), j + 1
        return chr(value & 0xFF), j
    return c, i + 2


def substitute(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == '\\':
            value, i = backslash(text, i)
            out.append(value)
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def split_list(text):
    """The elements of a Tcl list, as Tcl_SplitList returns them."""
    text = str(text)
    items, i, n = [], 0, len(text)
    while True:
        while i < n and text[i] in SPACE:
            i += 1
        if i >= n:
            return items
        if text[i] == '{':
            depth, j = 1, i + 1
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                raise TclListError('unmatched open brace in list')
            if j + 1 < n and text[j + 1] not in SPACE:
                raise TclListError('list element in braces followed by "%s" instead of space' % text[j + 1:j + 2])
            items.append(text[i + 1:j])
            i = j + 1
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            if j >= n:
                raise TclListError('unmatched open quote in list')
            if j + 1 < n and text[j + 1] not in SPACE:
                raise TclListError('list element in quotes followed by "%s" instead of space' % text[j + 1:j + 2])
            items.append(substitute(text[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in SPACE:
                if text[j] == '\\':
                    # A backslash-newline and the blanks after it stay in the element.
                    _, j = backslash(text, j)
                else:
                    j += 1
            items.append(substitute(text[i:min(j, n)]))
            i = j


def quote_element(value, first):
    """One element as Tcl's `list` writes it (TclScanElement + TclConvertElement).

    Some characters make Tcl prefer braces ([ $ ; whitespace, a backslash, a leading
    { " or #), others a backslash escape (] and "); braces are impossible when they
    would not balance or the element ends in, or escapes, a newline with a backslash.
    Balanced braces inside an element need no quoting at all, and stay unescaped when
    the element is escaped only for ] or ".
    """
    if value == '':
        return '{}'
    quote = prefer_brace = prefer_escape = require_escape = False
    if value[0] in '{"' or (first and value[0] == '#'):
        quote = prefer_brace = True
    depth, i, n = 0, 0, len(value)
    while i < n:
        c = value[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                require_escape = True
        elif c in ']"':
            quote = prefer_escape = True
        elif c in '[$;' or c in SPACE:
            quote = prefer_brace = True
        elif c == '\\':
            if i + 1 >= n or value[i + 1] == '\n':
                require_escape = True
            elif value[i + 1] in '{}\\':
                i += 1
            quote = prefer_brace = True
        i += 1
    if depth:
        require_escape = True
    if not require_escape:
        if not quote:
            return value
        if prefer_brace or not prefer_escape:
            return '{' + value + '}'
    out = []
    for k, c in enumerate(value):
        if c in '[]$;"\\ ' or (c in '{}' and require_escape):
            out.append('\\' + c)
        elif c in '\f\n\r\t\v':
            out.append('\\' + {'\f': 'f', '\n': 'n', '\r': 'r', '\t': 't', '\v': 'v'}[c])
        elif k == 0 and c == '#' and first:
            out.append('\\#')
        else:
            out.append(c)
    return ''.join(out)


def join_list(values):
    """Canonical Tcl list text for data values, identical to `puts [list ...]`."""
    return ' '.join(quote_element(str(v), k == 0) for k, v in enumerate(values))
