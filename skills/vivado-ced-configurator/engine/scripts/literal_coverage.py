#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Offline prototype of the T5 literal-value coverage check.

For each completed agent run, take the task text the model passed to `begin`
and the saved readback returned by `finish`, extract literal values from the
task, and decide whether each literal is accounted for by the written
configuration.  The check is generic: no IP names, CONFIG keys or case ids.
"""
import json
import re
import sys
from fractions import Fraction

NUMBER_WORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
    'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
    'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'single': 1,
}
# Unit scale factors relative to the value a parameter might store.
UNIT_SCALES = {
    'ghz': [1, 1000, 10**9], 'mhz': [1, 10**6, Fraction(1, 1000)], 'khz': [1, 1000],
    'hz': [1], 'gbps': [1, 1000], 'mbps': [1, Fraction(1, 1000)],
    'gb': [1, 1024], 'mb': [1, 1024], 'kb': [1, 1024],
}

HEX = re.compile(r'\b0x[0-9a-fA-F]+\b')
NUM = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)(?:\s*-?\s*([A-Za-z]+))?')
WORD = re.compile(r'\b(' + '|'.join(NUMBER_WORDS) + r')\b', re.I)
TOKEN = re.compile(r'\b(?=[A-Za-z0-9_:\-]*[A-Za-z])(?=[A-Za-z0-9_:\-]*\d)[A-Za-z0-9][A-Za-z0-9_:\-]*[A-Za-z0-9]\b')


def norm(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def to_number(text):
    text = str(text).strip()
    try:
        if text.lower().startswith('0x'):
            return Fraction(int(text, 16))
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def value_numbers(value):
    """Every number inside a saved value (scalars, comma lists, dict strings)."""
    out = set()
    for piece in re.split(r'[\s,]+', str(value)):
        n = to_number(piece)
        if n is not None:
            out.add(n)
        for sub in re.findall(r'0x[0-9a-fA-F]+|\d+(?:\.\d+)?', piece):
            m = to_number(sub)
            if m is not None:
                out.add(m)
    return out


def extract_literals(task):
    literals = []
    spans = []
    for m in HEX.finditer(task):
        literals.append({'text': m.group(0), 'kind': 'hex', 'value': to_number(m.group(0))})
        spans.append(m.span())
    for m in TOKEN.finditer(task):
        if any(a <= m.start() < b for a, b in spans):
            continue
        tok = m.group(0)
        if re.fullmatch(r'\d+(?:\.\d+)?-?[A-Za-z]+', tok):  # "24-bit", "100MHz": numeric, handled below
            continue
        literals.append({'text': tok, 'kind': 'token', 'value': norm(tok)})
        spans.append(m.span())
    for m in NUM.finditer(task):
        if any(a <= m.start() < b for a, b in spans):
            continue
        unit = (m.group(2) or '').lower()
        literals.append({'text': m.group(0).strip(), 'kind': 'number',
                         'value': Fraction(m.group(1)), 'unit': unit})
    for m in WORD.finditer(task):
        literals.append({'text': m.group(0), 'kind': 'number',
                         'value': Fraction(NUMBER_WORDS[m.group(1).lower()]), 'unit': ''})
    return literals


def accounted(lit, written, identity=''):
    """Return the reason a literal is accounted for, or None."""
    keys = list(written)
    values = [str(v) for v in written.values()]
    ident = norm(identity)
    if ident and norm(lit['text']) and norm(lit['text']) in ident:
        return 'identity'
    if lit['kind'] == 'token':
        t = lit['value']
        if any(t in norm(v) for v in values):
            return 'value'
        if any(t in norm(k) for k in keys):
            return 'key'
        return None
    target = lit['value']
    scales = UNIT_SCALES.get(lit.get('unit', ''), [1])
    candidates = {target * s for s in scales}
    for v in values:
        if candidates & value_numbers(v):
            return 'value'
    if lit['kind'] == 'number' and target.denominator == 1:
        n = int(target)
        # Index: the number appears glued to letters inside a written key name.
        for k in keys:
            if re.search(r'(?<=[A-Za-z])%d(?!\d)' % n, k):
                return 'index'
        # Count: n written keys of one numbered family carry a truthy value.
        families = {}
        for k, v in written.items():
            fam = re.sub(r'\d+', '#', k)
            if fam != k and str(v).strip().lower() in ('1', 'true', 'enable', 'enabled'):
                families[fam] = families.get(fam, 0) + 1
        if n in families.values():
            return 'count'
        # Count of truthy entries inside one comma-list value.
        for v in values:
            parts = [x.strip().lower() for x in str(v).split(',')]
            if len(parts) > 1 and sum(x in ('1', 'true', 'enable', 'enabled') for x in parts) == n:
                return 'count'
        # Truthy flag for "single"/"one"-style counts is not inferable; leave unaccounted.
    return None


def check(task, written, identity=''):
    lits = extract_literals(task)
    rows = []
    for lit in lits:
        reason = accounted(lit, written, identity)
        rows.append({'text': lit['text'], 'kind': lit['kind'], 'accounted': reason})
    return rows
