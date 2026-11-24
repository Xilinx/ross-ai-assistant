# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Host-only nested field selection. No IP identities or parameter recipes."""
import re
from pathlib import Path

from structured_config import dict_tcl


def load_schema(path, run_root):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(run_root).resolve() / 'evidence') or path.stat().st_size > 3_000_000:
        raise ValueError('Nested schema must be bounded current-run evidence')
    envelope = dict_tcl(path.read_text())
    if envelope.get('schema_version') != '1':
        raise ValueError('Unsupported native evidence schema')
    record = dict_tcl(envelope['data'])
    rows = []
    for key, encoded in dict_tcl(record['parameters']).items():
        if not re.fullmatch(r'CONFIG\.[A-Za-z0-9_]+', key):
            raise ValueError('Invalid native dictionary property')
        data = dict_tcl(encoded)
        for subkey, fields in dict_tcl(data['fields']).items():
            item = dict_tcl(fields)
            scope = item['mapping_scope']
            if scope not in ['live_dictionary', 'native_same_name_candidate']:
                raise ValueError('Unknown native field mapping scope')
            native_key = data['native_key']
            if scope == 'native_same_name_candidate' and native_key != key + '_INTERNAL':
                raise ValueError('Resolved native field must retain its exact parent name')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', subkey):
                raise ValueError('Invalid native dictionary field')
            row = dict(key=key, subkey=subkey, native_key=native_key,
                       mapping_scope=scope, default=item['default'], type='string',
                       label=subkey.replace('_', ' '), help='', help_source='unavailable',
                       range='RANGE_NONE', range_scope='unavailable', schema_evidence=str(path))
            if item.get('shape') == 'dictionary':
                row.update(shape='dictionary', type='dictionary', fields=list(dict_tcl(item['default'])))
            rows.append(row)
    return rows


def terms(text):
    # Shared engineering abbreviations, not IP-specific parameter aliases.
    aliases = dict(clk='clock', freq='frequency', rst='reset', num='number',
                   en='enable', use='enable', act='actual', mon='monitor',
                   hz='frequency', khz='frequency', mhz='frequency', ghz='frequency')
    words = re.findall(r'[A-Za-z]+[0-9]*|(?<![0-9])[0-9](?![0-9])', text.lower())
    result = set()
    for word in words:
        word = re.sub(r'(?:mhz|khz|hz)$', '', word) if len(word) > 3 else word
        match = re.fullmatch(r'([a-z]+)([0-9]+)', word)
        parts = [word]
        if match:
            parts += list(match.groups())
        for part in parts:
            result.add(aliases.get(part, part))
    return result


OBSERVATION = {'actual', 'monitor', 'threshold', 'status', 'debug'}
ROLE_QUALIFIERS = {'clock', 'frequency', 'reset'}


def name_matches(row, query):
    """Query terms matched by a row's own name and label (the field name for nested rows)."""
    names = terms((row.get('subkey') or row['key'][7:]) + ' ' + row.get('label', ''))
    return len({q for q in terms(query) if len(q) >= 2 and any(
        q.startswith(t) or t.startswith(q) for t in names if len(t) >= 2)})


def word_alias(word):
    return (sorted(terms(word)) or [word])[0] if len(terms(word)) == 1 else word


def index_score(name, pairs):
    """An index names one word ("gen4", "clock 0"); fields without that word are not penalized."""
    numbered, bare, numbers = [], set(), set()
    for token in name.lower().split('_'):
        match = re.fullmatch(r'([a-z]+)([0-9]+)', token)
        if match:
            numbered.append((word_alias(match[1]), match[2]))
            numbers.add(match[2])
        elif token.isalpha():
            bare.add(word_alias(token))
    related = lambda a, b: len(a) >= 2 and len(b) >= 2 and (a.startswith(b) or b.startswith(a))
    score = 0
    for words, wanted in pairs:
        same = {n for w, n in numbered if any(related(w, word) for word in words)}
        if same & wanted:
            score += 4
        elif same:
            score -= 12
        elif any(related(b, word) for b in bare for word in words):
            score -= 6 if numbers and not numbers & wanted else 0
        elif numbers & wanted and not set(words) & {'gen','generation'}:
            # Physical indices often use abbreviated source names. Keep that
            # weak fallback, but generation values are not physical indices.
            score += 2
    return score


STOPWORDS = {'on', 'at', 'of', 'in', 'to', 'the', 'for', 'and', 'with', 'as', 'by', 'is', 'be', 'an', 'or'}


def rank_fields(rows, query, limit=12):
    # Engineering notation describes purpose, not a parameter index. These
    # additions retrieve source controls; they never prescribe an encoding.
    if re.search(r'\bgen\s*\d+\b', query, re.I):
        query += ' link speed'
    if re.search(r'\bx\d+\b', query, re.I):
        query += ' link width lanes'
    if re.search(r'\b\d+\s*(?:KB|MB|GB)\b', query, re.I):
        query += ' size scale'
    wanted = terms(query) - STOPWORDS
    identifiers = re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', query.lower())
    # A hyphenated name ("can-fd 1") owns its index through each of its parts.
    pairs = {(tuple(word_alias(p) for p in w.split('-') if len(p) >= 2), n)
             for w, n in re.findall(r'([a-z]+(?:-[a-z]+)*) ?([0-9]{1,2})\b', query.lower())}
    grouped = {}
    for w, n in pairs:
        if w:
            grouped.setdefault(w, set()).add(n)
    pairs = [(w, ns) for w, ns in grouped.items()]
    # Field names often glue two words into one token ("controller type" -> CONTROLLERTYPE).
    words = [w for w in re.findall(r'[a-z]+', query.lower()) if w not in STOPWORDS]
    compounds = {(a + b, a, b) for a, b in zip(words, words[1:]) if len(a) >= 3 and len(b) >= 3}
    found, counts = [], {}
    for row in rows:
        choices = ' '.join(str(c.get('value', '')) + ' ' + str(c.get('label', '')) for c in row.get('choices', []))
        available = terms(row['subkey'] + ' ' + row.get('label', '') + ' ' + row.get('help', '') + ' ' + choices)
        # Numbered words ("gen4") match exactly; index_score handles their numbers.
        matches = {q for q in wanted if len(q) >= 2 and any(
            q == t if any(c.isdigit() for c in q + t) else
            q.startswith(t) or t.startswith(q) or (len(q) == 2 and t.endswith(q))
            for t in available if len(t) >= 2)}
        own = terms(row['subkey'] + ' ' + row.get('label', ''))
        glued = {c for c in compounds if c[0] in own}
        for _, a, b in glued:
            matches |= {a, b} & wanted
        if glued:
            matches.add('+compound')
        if matches:
            found.append((row, matches))
            for q in matches:
                counts[q] = counts.get(q, 0) + 1
    # Words that occur in few fields identify the setting better than words most fields share.
    rarity = lambda q: (counts[q] < 0.01 * len(rows)) + (counts[q] < 0.003 * len(rows)) if len(rows) >= 300 else 0
    ranked = []
    for row, matches in found:
        score = len(matches) + sum(rarity(q) for q in matches) + 3 * ('+compound' in matches)
        # Preserve explicit interface/parameter identifiers ahead of broad words.
        score += 8 * sum(bool(re.search(r'(?:^|_)' + re.escape(name) + r'(?:_|$)', row['subkey'].lower())) for name in identifiers)
        score += index_score(row['subkey'], pairs)
        # In "RESET on PIN_3", RESET is the assignment being configured;
        # the pin identifier is its value, not a substitute for that setting.
        subjects = re.split(r'\b(?:on|at|to|via)\b', query, maxsplit=1, flags=re.I)
        anchors = {w.lower() for w in re.findall(r'\b[A-Z]{3,}\b', subjects[0])} if len(subjects) > 1 else set()
        score += 5 * len(anchors & terms(row['subkey']))
        # Observed/derived values (actual, monitor, threshold, status) rank below the setting itself.
        if (terms(row['subkey'] + ' ' + row.get('label', '')) & OBSERVATION) and not (wanted & OBSERVATION):
            score -= 3
        # A sibling clock/reset control often shares the controller index,
        # enable flag, and MIO value with the requested peripheral setting.
        # Prefer it only when the request names that role explicitly.
        unmatched_roles = (terms(row['subkey'] + ' ' + row.get('label', '')) & ROLE_QUALIFIERS) - wanted
        score -= 4 * len(unmatched_roles)
        ranked.append((score, row['key'], row['subkey'], row))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [item[3] for item in ranked[:limit] if item[0] > 0]


def coverage_queries(query):
    """Small source-purpose queries for independent facts in one requirement."""
    result = []
    result += [m.group(0) + ' link speed rate' for m in re.finditer(r'\bgen\s*\d+\b', query, re.I)]
    result += [m.group(0) + ' link width lanes' for m in re.finditer(r'\bx\d+\b', query, re.I)]
    result += [m.group(0) + ' size scale depth' for m in re.finditer(r'\b\d+\s*(?:KB|MB|GB)\b', query, re.I)]
    role_patterns = [
        r'\b(?:endpoint|root[ _-]?port)\b.*?\b(?:port[ _-]?type|type|mode)\b',
        r'\b(?:input|output)(?:[ _-]only)?\b.*?\b(?:direction|mode|type)?\b',
        r'\bactive[ _-]?(?:low|high)\b.*?\b(?:polarity|reset)?\b',
    ]
    result += [m.group(0) for pattern in role_patterns for m in re.finditer(pattern, query, re.I)]
    return list(dict.fromkeys(' '.join(q.split()) for q in result if q.strip()))


def rank_fields_covering(rows, query, limit=6):
    """Keep the best overall rows and one winner for each explicit semantic fact.

    Lexical ranking alone can fill a short result with sibling or override
    controls.  Fact winners preserve independent roles such as generation,
    width and endpoint type without knowing an IP or parameter name.
    """
    base = rank_fields(rows, query, limit)
    winners = []
    for subquery in coverage_queries(query):
        found = rank_fields(rows, subquery, 1)
        if found:
            winners.append(found[0])
    result = []
    identity = lambda row: (row['key'], row['subkey'])
    # Retain the strongest overall answer, then guarantee each distinct fact.
    for row in base[:1] + winners + base[1:]:
        if identity(row) not in {identity(old) for old in result}:
            result.append(row)
        if len(result) == limit:
            break
    return result


def related_fields(rows, selected, query, limit=4):
    """Bounded sibling controls of an explicitly requested numbered field.

    An enable flag alone cannot implement a size/type request. Return source
    fields sharing the exact indexed stem, preserving parent provenance.
    """
    result, seen = [], {(r['key'], r['subkey']) for r in selected}
    for row in selected:
        name = row['subkey']
        tokens = name.split('_')
        positions = [i for i, token in enumerate(tokens) if re.search(r'\d$', token)]
        if not positions or positions[-1] == len(tokens) - 1:
            continue
        stem = '_'.join(tokens[:positions[-1] + 1]) + '_'
        # The numbered stem must occur in the request; broad context cannot
        # pull an arbitrary controller family into a requirement.
        if normalized_identifier(tokens[positions[-1]]) not in normalized_identifier(query):
            continue
        siblings = [r for r in rows if r['key'] == row['key'] and r['subkey'].startswith(stem)
                    and (r['key'], r['subkey']) not in seen]
        for sibling in rank_fields(siblings, query, limit) or siblings[:limit]:
            item = dict(sibling, related_to=name, relation_scope='same_parent_indexed_source_fields')
            result.append(item); seen.add((item['key'], item['subkey']))
            if len(result) >= limit:
                return result
    return result


def normalized_identifier(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())


def enrich(rows, hints):
    result = []
    for original in rows:
        row = dict(original)
        hint = hints.get(row['subkey'], {})
        if hint:
            row.update(hint_scope=hint['hint_scope'], source_vlnvs=hint['source_vlnvs'],
                       help=hint.get('help', ''), help_source='database_exact_field_name')
            if hint.get('labels'):
                row['label'] = ' / '.join(hint['labels'])[:160]
            for field in ['type', 'range', 'choices', 'choices_more']:
                if hint.get(field) is not None:
                    row[field] = hint[field]
            # A recorded prerequisite is the one hint that says what to do about a disabled
            # field rather than only describing it, so it travels with the row.
            for field in ['enablement_requires', 'enablement_source_scope', 'enablement_observed_value']:
                if hint.get(field) is not None and not row.get(field):
                    row[field] = hint[field]
            row['range_scope'] = 'standalone_definition_not_parent_live_range'
        result.append(row)
    return result
