# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Conservative checks of explicit task values, independent of benchmark answers.

This is a coverage check, not an interpretation of architectural intent. Facts
remain unresolved when their source encodings cannot be established.
"""
import re
import math


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def facts(text):
    result = []
    # Natural-language singular quantities carry the same exact value as an
    # explicit ``1``.  Preserve the unit so discovery and coverage can bind a
    # one-bit width without turning unrelated Boolean enables into width.
    for m in re.finditer(r'\bsingle[ -](bit|lane|pixel)\b', text, re.I):
        result.append(dict(value='1', unit=m[1].lower(), text=m[0]))
    # Keep a number's units: an enable bit does not cover a memory size.
    for m in re.finditer(r'(?<![\w.+-])([+-]?(?:0x[0-9a-f]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?))\s*(?:-\s*)?'
                         r'(?:(?:(?:active|enabled)\s+)?(GHz|MHz|kHz|Hz|Gbps|Mbps|GB|MB|KB|bits?|lanes?|pixels?))?(?![\w.])', text, re.I):
        value, unit = m.groups()
        # An explicit parenthetical value refines an approximate clock/rate;
        # it does not ask for two simultaneous settings on that same clock.
        if unit and re.search(r'(?:~|\bapprox(?:imately)?\s*)$', text[:m.start()], re.I):
            refinement = re.match(r'[^0-9,;()]{0,64}\(\s*([+-]?\d+(?:\.\d+)?)\s*'+re.escape(unit)+r'\s*\)', text[m.end():], re.I)
            if refinement and math.isclose(number(value), number(refinement[1]), rel_tol=0.01):
                continue
        if not unit and (re.search(r'\b(?:clock|clk|channel|tile|port|output|input|core|bank|lane)\s+$', text[:m.start()], re.I)
                         or re.search(r'\b[A-Z][A-Z0-9]*-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\s+$',text[:m.start()])):
            continue  # Named instance indices are not requested setting values.
        result.append(dict(value=value, unit=(unit or '').lower(), text=m[0].strip()))
    for m in re.finditer(r'\b(?:Gen\s*\d+|x\d+|[A-Z][A-Z0-9]*-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*|'
                         r'[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b', text):
        if '_' in m[0] and re.match(r'\s*(?:=|:)\s*',text[m.end():]):
            continue  # An explicit source setting name is not an enum value.
        if ('_' in m[0] and not re.search(r'\d',m[0])
                and text[:m.start()].rstrip().endswith('(')
                and text[m.end():].lstrip().startswith(')')):
            continue  # Parenthesized source setting qualifier, not an enum.
        if m[0].startswith('Gen') and re.match(r'\s+(?:processing|device|architecture|family)', text[m.end():], re.I):
            continue  # Device identity is not a requested link-generation value.
        if '-' in m[0] and re.match(r'\s*\d\b',text[m.end():]):
            continue  # Protocol-qualified controller index, not an enum value.
        result.append(dict(value=m[0], unit='literal', text=m[0]))
    for m in re.finditer(r'\b[A-Z]{2,}\d+\b', text):
        result.append(dict(value=m[0], unit='identifier', text=m[0]))
    for m in re.finditer(r'\b(?:active-(?:low|high)|(?:input|output)-only)\b', text, re.I):
        result.append(dict(value=m[0], unit='literal', text=m[0]))
    # Avoid duplicate numeric fragments inside identifier facts.
    return [f for i, f in enumerate(result) if f not in result[:i]]


def missing_clauses(task, requirements, catalog_context=''):
    """Original source clauses with explicit values missing from the ledger."""
    covered = ' '.join(requirements)
    existing = {fact_identity(f) for f in facts(covered)}
    missing = []
    for clause in re.split(r'[,;]|:(?!\d)|\band\b|(?<=[A-Za-z)])\.\s+', task):
        clause = clause.strip(' .\n')
        if not clause or len(clause) > 600:
            continue
        # Numbered section labels identify the target cell in multi-cell
        # requests; the ordinal is not a configuration value.
        if re.fullmatch(r'\d+\)\s*[A-Za-z][A-Za-z0-9_]*', clause):
            continue
        absent = [f for f in facts(clause) if fact_identity(f) not in existing]
        # Protocol/product identifiers repeated by the requested catalog name
        # identify the IP; they are not additional configuration values.  A
        # real requested mode remains absent unless it is also part of that
        # caller-supplied catalog context.
        absent = [f for f in absent if f['unit'] not in ['literal', 'identifier']
                  or normalized(f['text']) not in normalized(catalog_context)]
        # A protocol/product-looking token can name the external signal being
        # driven rather than an IP setting.  When it sits inside an explicit
        # drive/read clause that ends in lines, pins, or signals, keep the
        # observable direction requirement and do not invent an enum ledger
        # row for the destination's name (for example, MSI-X enable lines).
        destination_clause = bool(re.search(
            r'\b(?:drive|drives|driving|read|reads|reading|capture|capturing|transmit)\b'
            r'.*\b(?:lines?|pins?|signals?)\b', clause, re.I))
        if destination_clause:
            absent = [f for f in absent if f['unit'] not in ['literal', 'identifier']]
        # Bare fragments of an indexed enumeration ("clocks 0, 1 and 2")
        # must not become new setting values when the supplied requirements
        # already preserve those source numbers and their context.
        raw_numbers = {number(m[0]) for m in re.finditer(r'(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])',covered)}
        raw_numbers.update(number(m[0]) for m in re.finditer(r'(?<=[A-Za-z_])\d+\b',covered))
        absent = [f for f in absent if f['unit'] or number(f['value']) not in raw_numbers]
        # Counts and bit widths do not need a separate unit setting when an
        # existing same-number requirement names the same behavioral role.
        # Storage and frequency units remain distinct because they may need a
        # scale/frequency conversion field.
        roles={'width','data','lane','lanes','pixel','pixels','port','ports','channel','channels'}
        clause_roles=set(re.findall(r'[a-z]+',clause.lower())) & roles
        def represented_count(fact):
            effective_roles = set(clause_roles)
            if fact['unit'] in {'bit', 'bits'}:
                effective_roles.add('width')
            if fact['unit'] in {'lane', 'lanes'}:
                effective_roles.add('lane')
            if fact['unit'] in {'pixel', 'pixels'}:
                effective_roles.add('pixel')
            if fact['unit'] not in {'bit','bits','lane','lanes','pixel','pixels'} or not effective_roles:
                return False
            wanted=number(fact['value'])
            return any(wanted==number(value) and effective_roles & (set(re.findall(r'[a-z]+',req.lower())) & roles)
                       for req in requirements
                       for value in re.findall(r'(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])',req))
        absent=[f for f in absent if not represented_count(f)]
        # Device/interface identity is often already represented by a longer
        # requirement with different punctuation; match literals canonically.
        absent = [f for f in absent if f['unit'] not in ['literal', 'identifier'] or normalized(f['text']) not in normalized(covered)]
        # Preserve an explicit negative multiplicity even when the caller's
        # ledger mentions only the item's width or size.  Keep the wording
        # generic and source-derived; discovery resolves the actual control.
        if re.search(r'\bnot\s+two\b', clause, re.I) and not re.search(
                r'\b(?:not\s+two|only\s+one|single\s+instance)\b', covered, re.I):
            owner = re.search(r'\bone\s+(?:\d+\s*-\s*bit\s+)?([A-Za-z][A-Za-z-]*)\b', task, re.I)
            noun = owner[1].lower() if owner else 'instances'
            missing.append('not two ' + (noun if noun.endswith('s') else noun + 's'))
            continue
        # Keep short, explicit nonnumeric setting clauses too. A compound
        # link requirement mentioning "endpoint" must not swallow the task's
        # separate "port type endpoint" assignment.
        purpose = re.search(r'\b(?:mode|type|direction|interface|filtering|bridge|channels?|enabled|disabled|on|off|'
                            r'drive|drives|driving|read|reads|receive|receiving|capture|capturing|transmit)\b', clause, re.I)
        aliases={'clocks':'clock','channels':'channel','enabled':'enable','disabled':'disable',
                 'drive':'output','drives':'output','driving':'output','transmit':'output',
                 'read':'input','reads':'input','reading':'input','receive':'input','receiving':'input',
                 'capture':'input','capturing':'input'}
        tokens = lambda s: {aliases.get(w,w) for w in re.findall(r'[a-z0-9]+',s.lower())} - {'the','a','an','and','is','to','for','leave'}
        if purpose and len(clause.split()) <= 12:
            atomic = any(tokens(clause) <= tokens(t) and len(facts(t)) <= len(facts(clause)) for t in requirements)
            # A short introductory clause often combines context already
            # split across several good atomic requirements. Compare only
            # observable behavior words across the whole ledger; product and
            # board nouns must not force a duplicate copy of the full clause.
            behavior={'mode','type','direction','interface','filtering','filter','bridge','channel',
                      'enable','disable','on','off','drive','drives','driving','read','reads','input','output','port','width','depth',
                      'speed','rate','format','polarity','lane','pixel','tile','clock','frequency'}
            wanted=tokens(clause) & behavior
            provided=tokens(covered) & behavior
            atomic = atomic or (bool(wanted) and wanted <= provided)
            if (re.search(r'\b(?:drive|drives|driving|transmit)\b', clause, re.I)
                    and {'output','outputs'} & tokens(covered)):
                atomic = True
            if (re.search(r'\b(?:read|reads|reading|receive|receiving|capture|capturing)\b', clause, re.I)
                    and {'input','inputs'} & tokens(covered)):
                atomic = True
            if not atomic and not absent:
                channel = re.search(r'\bchannel\s+\d+\b', clause, re.I)
                if channel and re.search(r'\b(?:read|reads|reading|receive|receiving|capture|capturing)\b', clause, re.I):
                    missing.append(channel[0] + ' input direction')
                elif channel and re.search(r'\b(?:drive|drives|driving|transmit)\b', clause, re.I):
                    missing.append(channel[0] + ' output direction')
                else:
                    missing.append(clause)
        if absent:
            # A long introductory clause can contain one omitted enum value;
            # retain that exact source token so discovery is not diluted by
            # board/device context already represented elsewhere.
            if len(clause.split()) > 8 and all(f['unit'] in ['literal', 'identifier'] for f in absent):
                missing.extend(clause[clause.index(f['text']):] if f['unit'] == 'identifier' else f['text'] for f in absent)
            else:
                missing.append(clause)
            existing.update(fact_identity(f) for f in facts(clause))
    return missing


def atomic_requirements(requirements):
    """Split an action plus measured setting into independent obligations.

    A Boolean enable cannot prove the accompanying size/rate/depth.  Keep the
    wording source-derived and only split the unambiguous ``with N unit``
    form, so ordinary architectural prose is unaffected.
    """
    result = []
    measured = re.compile(
        r'^\s*(enable|disable)\s+(.+?)\s+with\s+'
        r'([+-]?(?:0x[0-9a-f]+|\d+(?:\.\d+)?)\s*'
        r'(?:GHz|MHz|kHz|Hz|Gbps|Mbps|GB|MB|KB|bits?|lanes?|pixels?))'
        r'(?:\s+each)?\s*$', re.I)
    for text in requirements:
        # A shared prefix/suffix around a slash denotes two independently
        # observable settings (for example, "no AXI slave/master ports").
        # Give each source control its own ledger row so selecting one cannot
        # accidentally stand in for both.
        shared = re.fullmatch(
            r'\s*(no|zero)\s+(.+?)\s+([A-Za-z][A-Za-z0-9_-]*)/'
            r'([A-Za-z][A-Za-z0-9_-]*)\s+(.+?)\s*', text, re.I)
        if shared:
            negative, prefix, first, second, suffix = shared.groups()
            result.extend([
                f'{negative.lower()} {prefix} {first} {suffix}',
                f'{negative.lower()} {prefix} {second} {suffix}',
            ])
            continue
        match = measured.match(text)
        if not match:
            result.append(text)
            continue
        action, feature, value = match.groups()
        # A shared noun permits "instruction and data cache".  Expanding it
        # is safe only for two simple names and preserves the noun verbatim.
        pair = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*)\s+and\s+'
                            r'([A-Za-z][A-Za-z0-9_-]*)\s+(.+)', feature)
        features = ([pair[1] + ' ' + pair[3], pair[2] + ' ' + pair[3]]
                    if pair else [feature])
        for item in features:
            result.extend([action.lower() + ' ' + item, item + ' size ' + value])
    return list(dict.fromkeys(result))


def number(value):
    try:
        return int(str(value), 16) if str(value).lower().startswith('0x') else float(value)
    except (ValueError, TypeError):
        return None


def fact_identity(fact):
    if fact['unit'] in ['literal', 'identifier']:
        return (fact['unit'], normalized(fact['value']))
    return ({'bits':'bit','lanes':'lane','pixels':'pixel'}.get(fact['unit'],fact['unit']), number(fact['value']))


FREQUENCY_UNITS = {'hz':1, 'khz':1e3, 'mhz':1e6, 'ghz':1e9}


def unit_value(row, fact):
    """Convert only when a source-backed candidate declares its units."""
    unit = str(row.get('units','')).lower()
    requested = fact['unit']
    if unit in FREQUENCY_UNITS and requested in FREQUENCY_UNITS:
        return number(fact['value']) * FREQUENCY_UNITS[requested] / FREQUENCY_UNITS[unit]
    return None


def uncovered_values(text, selections):
    """Compare explicit values with selected values and source enum labels.

    selections are (source candidate, selected item) pairs. Parameter names are
    purpose evidence only: PS_MIO3's name cannot prove a PERST assignment.
    """
    rendered, numeric, semantic_facts = [], [], set()
    for row, item in selections:
        values = list(item.get('indices', {}).values()) or [item['value']]
        if row.get('shape') == 'dictionary':
            try:
                from structured_config import dict_tcl
                values = list(dict_tcl(item['value']).values())
                # IO ranges are scalar source values; retain their endpoint
                # facts without splitting their dictionary or pin identity.
                rendered += [f['text'] for v in values for f in facts(v)]
            except (TypeError,ValueError):
                pass
        for value in values:
            labels = [label for c in row.get('choices', []) if str(c.get('value')) == str(value)
                      for label in [str(c.get('label', ''))] + list(c.get('semantic_labels', []))]
            rendered += [str(value)] + labels
            semantic_facts.update(fact_identity(f) for c in row.get('choices', []) if str(c['value'])==str(value)
                                  for label in c.get('semantic_labels', []) for f in facts(label))
            purpose = ' '.join([row.get('subkey', row['key']), row.get('label', ''), row.get('help', '')]).lower()
            numeric.append((number(value), purpose, labels))
    out = []
    for fact in facts(text):
        value, unit = fact['value'], fact['unit']
        if any(unit_value(row,fact) is not None and number(item['value']) is not None
               and math.isclose(number(item['value']),unit_value(row,fact),rel_tol=1e-12,abs_tol=1e-9)
               for row,item in selections if not item.get('indices')):
            continue
        if fact_identity(fact) in semantic_facts:
            continue
        if any(fact_identity(fact) in {fact_identity(f) for s in item.get('covers', []) for f in facts(s)}
               and item.get('grounding') for _, item in selections):
            continue
        compact = lambda s: ''.join(str(s).lower().split())
        if any((normalized(fact['text']) == normalized(s) if unit in ['literal', 'identifier']
                else compact(fact['text']) == compact(s)) for s in rendered):
            continue
        if unit in ['literal', 'identifier']:
            # A value explicitly listed by source metadata may add a suffix
            # to the task's enum name (LPDDR5 -> LPDDR5_SDRAM). Require every
            # separator-delimited task token, so a shorter adjacent encoding
            # cannot cover omitted qualifiers such as a protocol suffix.
            enum_tokens = lambda value: set(re.findall(r'[a-z0-9]+', str(value).lower()))
            wanted_tokens = enum_tokens(fact['text'])
            encoded = any(
                wanted_tokens and wanted_tokens <= enum_tokens(item['value'])
                and any(str(choice.get('value')) == str(item['value']) for choice in row.get('choices', []))
                for row, item in selections)
            if encoded:
                continue
        if unit == 'identifier':
            if any(normalized(value) in normalized(row.get('subkey', row['key'])) for row, _ in selections):
                continue
            out.append(fact)
            continue
        if unit == 'literal':
            # A Boolean source field whose identifier explicitly names its
            # asserted polarity proves the inverse polarity when cleared.
            # This uses the published field identity plus the selected value;
            # it does not guess polarity from the prose alone.
            polarity = re.fullmatch(r'active-(low|high)', value, re.I)
            if polarity:
                wanted_low = polarity[1].lower() == 'low'
                proven = False
                for source, item in selections:
                    identity = ' '.join([source.get('subkey', source['key']), source.get('label', '')])
                    has_high = bool(re.search(r'(?<![A-Za-z])HIGH(?![A-Za-z])', identity, re.I))
                    has_low = bool(re.search(r'(?<![A-Za-z])LOW(?![A-Za-z])', identity, re.I))
                    selected = number(item.get('value'))
                    if selected in [0, 1] and ((has_high and bool(selected) != wanted_low)
                                                or (has_low and bool(selected) == wanted_low)):
                        proven = True
                        break
                if proven:
                    continue
            # A parenthesized source parameter name qualifies the numeric
            # assignment; it is not a second enum value. Pin/value literals
            # carrying an index remain explicit assignment facts.
            if '_' in value and not re.search(r'\d',value) and (re.search(r'\(\s*'+re.escape(value)+r'\s*\)',text)
                    or re.search(r'\b'+re.escape(value)+r'\s*(?:=|:)?\s+[+-]?(?:0x[0-9a-f]+|\d)',text,re.I)):
                if any(row.get('subkey',row['key'][7:])==value or row.get('subkey',row['key'][7:]).endswith('_'+value)
                       for row,_ in selections):
                    continue
            # "include FEATURE-NAME" asks for a Boolean control, not a
            # literal FEATURE-NAME string. Require both the matching source
            # purpose and its declared Boolean encoding.
            enabled = bool(re.search(r'\b(?:enable|enabled|include|including)\b',text,re.I))
            disabled = bool(re.search(r'\b(?:disable|disabled|exclude)\b',text,re.I))
            for row, item in selections:
                purpose = row.get('subkey',row['key'])+' '+row.get('label','')
                if normalized(value) not in normalized(purpose) or not re.search(r'(?:ENABLE|INCLUDE|USE)',purpose,re.I):
                    continue
                boolean = str(item['value']).lower() if row.get('type','').lower() in ['bool','boolean'] else next(
                    (str(c.get('label','')).lower() for c in row.get('choices',[]) if str(c['value'])==str(item['value'])), '')
                if enabled != disabled and boolean in (['1','true'] if enabled else ['0','false']):
                    break
            else:
                row = None
            if row is not None:
                continue
            # An interface name can qualify a setting's label ("A-PHY line
            # rate") without requesting a second, contradictory mode write.
            qualifier = re.search(re.escape(value) + r'\s+(line\s+rate|frequency|clock|interface|port)\b', text, re.I)
            if qualifier and any(normalized(value) in normalized(' '.join([row.get('label', ''), row.get('help', '')]))
                                 and any(w in (row.get('subkey', row['key']) + ' ' + row.get('label', '')).lower()
                                         for w in qualifier[1].lower().split()) for row, _ in selections):
                continue
            # A generation/width may be encoded as a bare number by a source
            # field whose name establishes that role.
            m = re.fullmatch(r'(Gen\s*|x)(\d+)', value, re.I)
            role = ('speed', 'gen') if m and m[1].lower().startswith('gen') else ('width', 'lane')
            if m and any(v == float(m[2]) and any(w in p for w in role) for v, p, _ in numeric):
                continue
            out.append(fact)
            continue
        wanted = number(value)
        if unit:
            role = {'mb': ('size', 'depth'), 'kb': ('size', 'depth'), 'gb': ('size', 'depth'),
                    'bit': ('width', 'format', 'data'), 'bits': ('width', 'format', 'data'),
                    'lane': ('lane',), 'lanes': ('lane',), 'pixel': ('pixel', 'ppc'), 'pixels': ('pixel', 'ppc')}.get(unit)
            role = role or (('freq', 'clock', 'clk') if unit.endswith('hz') else ('rate', 'speed'))
            if any(v == wanted and any(w in p for w in role) for v, p, _ in numeric):
                scales = {'kb': {'kb', 'kilobytes'}, 'mb': {'mb', 'megabytes'}, 'gb': {'gb', 'gigabytes'}}
                if unit not in scales or any(normalized(s) in scales[unit] for s in rendered):
                    continue
        elif any(v == wanted for v, _, _ in numeric):
            continue
        # Counts can be implemented by one explicit Boolean per requested item.
        if not unit and wanted and wanted == sum(str(item['value']).lower() in ['true', '1']
                                                 for row, item in selections):
            continue
        out.append(fact)
    return out
