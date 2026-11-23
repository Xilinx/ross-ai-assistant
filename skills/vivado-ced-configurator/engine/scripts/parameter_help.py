# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Selected top-level IP-XACT parameter help. Emits data as a Tcl dict, never code."""
import sys
import xml.etree.ElementTree as ET


def word(value):
    value = str(value)
    if not value:
        return '{}'
    escapes = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
    return ''.join(escapes.get(c, '\\' + c if c.isspace() or c in '\\{}[]$;"' else c)
                   for c in value)


def tcl(value):
    if isinstance(value, dict):
        return ' '.join(word(k) + ' ' + word(tcl(v) if isinstance(v, (dict, list)) else v)
                        for k, v in value.items())
    return ' '.join(word(tcl(v) if isinstance(v, (dict, list)) else v) for v in value)


def _parameters(path):
    root = ET.parse(path).getroot()
    ns = root.tag.split('}')[0] + '}' if '}' in root.tag else ''
    choices = {}
    for choice in root.findall(ns + 'choices/' + ns + 'choice'):
        choices[choice.findtext(ns + 'name')] = [
            {'value': e.text or '', 'label': e.get(ns + 'text', e.get('text', ''))}
            for e in choice.findall(ns + 'enumeration')]
    for p in root.findall(ns + 'parameters/' + ns + 'parameter'):
        v = p.find(ns + 'value')
        record = {
            'label': p.findtext(ns + 'displayName') or p.findtext(ns + 'name'),
            'help': p.findtext(ns + 'description') or '',
            'help_source': 'packaged_description',
            'type': v.get(ns + 'format', '') if v is not None else '',
            'default': v.text or '' if v is not None else '',
            'choices': choices.get(v.get(ns + 'choiceRef', ''), []) if v is not None else [],
        }
        attrs = {k: (v.get(ns + k) if v is not None else None) for k in ('resolve', 'minimum', 'maximum')}
        yield p.findtext(ns + 'name'), record, attrs


def selected(path, names):
    return {name: record for name, record, _ in _parameters(path) if name in names}


def user_rows(path, max_choices=6):
    """All user-resolved top-level parameters as candidate rows (packaged, not live)."""
    rows = []
    for name, record, attrs in _parameters(path):
        if attrs['resolve'] != 'user' or not name or name == 'Component_Name':
            continue
        if record['choices']:
            rng = 'RANGE_LIST:' + ' '.join(c['value'] for c in record['choices'][:max_choices])
        elif attrs['minimum'] is not None and attrs['maximum'] is not None:
            rng = 'RANGE_NUM:%s:%s' % (attrs['minimum'], attrs['maximum'])
        else:
            rng = 'RANGE_NONE'
        row = dict(key='CONFIG.' + name, label=record['label'], type=record['type'],
                   default=record['default'][:240], help=record['help'][:240],
                   help_source=record['help_source'] if record['help'] else 'unavailable',
                   range=rng, range_scope='packaged_not_live')
        if record['choices']:
            row.update(choices=record['choices'][:max_choices], choices_more=len(record['choices']) > max_choices,
                       choices_scope='packaged_choices_not_current_legal_values')
        rows.append(row)
    return rows


if __name__ == '__main__':
    print(tcl(selected(sys.argv[1], set(sys.argv[2:]))))
