# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Attach documented protocol meanings to matching published enum values.

This adds explanations, never candidate keys, recipes, legal ranges, defaults,
or writes. Native validation and saved readback remain authoritative.
"""
import json
import re
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parents[1] / 'references/value-semantics.json').read_text())['rules']


def requested_labels(row, text):
    """Exact source-label spellings, allowing separators between word parts."""
    matches = []
    for choice in row.get('choices', []):
        label = str(choice.get('label', ''))
        if not label or label.lower() in ['true','false','yes','no','on','off']:
            continue
        parts = re.findall(r'[A-Za-z]+|\d+', label)
        if not parts or len(''.join(parts)) < 3:
            continue
        pieces = list(label) if re.fullmatch(r'[A-Za-z0-9]+',label) else parts
        pattern = r'(?<![A-Za-z0-9])'+r'[ _-]*'.join(re.escape(p) for p in pieces)+r'(?![A-Za-z0-9])'
        if re.search(pattern,text,re.I):
            matches.append(choice)
    return matches


def annotate(row):
    description = row.get('subkey', row['key']) + ' ' + row.get('label', '')
    purpose = set(re.findall(r'[a-z]+', description.lower()))
    result = dict(row)
    choices = [dict(c) for c in row.get('choices', [])]
    for rule in RULES:
        if not set(rule['purpose_tokens']) <= purpose:
            continue
        if 'boolean_width' in rule:
            boolean = {str(c['value']).lower() for c in choices}
            if not boolean or not boolean <= {'0','1','false','true'} or not re.search(r'(?<!\d)'+str(rule['boolean_width'])+r'[ _-]*bit\b',description,re.I):
                continue
        for choice in choices:
            if 'boolean_width' in rule:
                label = rule['labels'].get(str(choice['value']).lower())
            else:
                value = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)[ _]*' + re.escape(rule['unit']), str(choice['value']), re.I)
                if value is None:
                    continue
                label = next((text for rate, text in rule['labels'].items() if float(rate) == float(value[1])), None)
            if label:
                choice.update(semantic_labels=[label], semantics_source=rule['source'],
                              semantics_scope='documented_protocol_meaning_not_live_legality')
    if choices:
        result['choices'] = choices
    return result
