# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Compact direct port controls from the existing native gating helper."""
import re

from structured_config import dict_tcl, split_tcl


def keywords(query, limit=8):
    # Shared port terminology, never an IP or parameter-name recipe.
    aliases = {'clock': ('clk', 'clock'), 'clocks': ('clk', 'clock'),
               'reset': ('reset', 'rst'), 'resets': ('reset', 'rst'),
               'interrupt': ('irq', 'interrupt', 'intc'),
               'interrupts': ('irq', 'interrupt', 'intc')}
    ignored = set('add named configure configured configuration with without the and or '
                  'for from into output outputs input inputs enable enabled disable disabled '
                  'active low high frequency frequencies width bits byte bytes channel channels '
                  'at an to of in on is as a'.split())
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9_]{1,47}', query.lower())
    tokens = [t for t in tokens if '_' in t] + [t for t in tokens if '_' not in t]
    found = []
    for token in tokens:
        if token in ignored:
            continue
        for word in aliases.get(token, (token,)):
            if word not in found:
                found.append(word)
    return found if limit is None else found[:limit]


def parse(receipt):
    parts = split_tcl(receipt)
    if not parts or parts[0] not in ['GATES:OK', 'GATES:UNAVAILABLE']:
        raise ValueError('Unexpected native gating receipt')
    fields = dict(zip(parts[1::2], parts[2::2]))
    if parts[0] == 'GATES:UNAVAILABLE':
        return dict(status='unavailable', reason=fields.get('reason', 'Unknown')[:180], ports=[])
    ports = []
    for name, raw in list(dict_tcl(fields.get('dependencies', '')).items())[:4]:
        record = dict(name=name, parameters=[], status='unresolved')
        kind, _, tail = raw.partition(':' + name)
        if kind == 'PE_NONE' and not tail:
            record['status'] = 'no_direct_dependencies'
        elif kind in ['PE', 'PE_DERIVED'] and tail.startswith(':'):
            text = tail[1:]
            direct = re.split(r'\s+(?:DERIVED|UNMAPPED)\{', text)[0]
            names = direct.split()
            if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p) for p in names):
                raise ValueError('Invalid native controller identifier')
            record['status'] = 'direct' if kind == 'PE' else 'derived'
            record['parameters' if kind == 'PE' else 'derived_parameters'] = names[:32]
            record['parameters_more'] = len(names) > 32
            for marker, field in [('DERIVED', 'derived_parameters'), ('UNMAPPED', 'unmapped_nodes')]:
                match = re.search(marker + r'\{([^}]*)\}', text)
                if match:
                    values = match[1].split()
                    record[field] = values[:4]
                    record[field + '_more'] = len(values) > 4
        ports.append(record)
    return dict(status='available', matched=int(fields.get('matched', '0')),
                more=fields.get('more') == '1', ports=ports,
                evidence_path=fields.get('evidence', ''), scope='direct_native_predecessors')


def detail_fields(encoded):
    """Normalize native or packaged describe fields without interpreting values."""
    fields = dict_tcl(encoded)
    row = {}
    for native, public in [('Display Name', 'label'), ('Type', 'type'),
                           ('Default Value', 'default'), ('label', 'label'),
                           ('type', 'type'), ('default', 'default'), ('help', 'help'),
                           ('help_source', 'help_source'), ('range', 'range'),
                           ('range_scope', 'range_scope')]:
        if fields.get(native):
            row[public] = fields[native]
        if fields.get(native + '_truncated') == '1':
            row[public + '_truncated'] = True
    if fields.get('enums'):
        enums = dict_tcl(fields['enums'])
        if int(enums.get('count', '0')):
            choices = [split_tcl(item) for item in split_tcl(enums.get('shown', ''))]
            row['choices'] = [dict(value=item[0], label=item[1], truncated=False) for item in choices]
            row['choices_more'] = enums.get('more') == '1'
    return row
