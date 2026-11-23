# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Rank current-run native API evidence; no database or prior-run corpus."""
import re
from pathlib import Path

from ip_database import Database


def parameters(path):
    rows, index = [], {}
    for block in re.split(r'(?m)^\*{5,}.*$', Path(path).read_text()):
        def field(name):
            match = re.search(r'(?m)^' + re.escape(name) + r':[ \t]*(.*)$', block)
            return match[1].strip() if match else ''
        name = field('Parameter Name')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            continue
        row = dict(key='CONFIG.'+name, label=field('Display Name') or name,
                   type=field('Type'), default=field('Default Value')[:240],
                   help=field('Description')[:240], help_source='native_description',
                   range='RANGE_NONE', range_scope='requires_current_configuration')
        state = re.search(r'(?m)^(Enabled|Disabled):', block)
        if state:
            row.update(enabled=state[1] == 'Enabled', enablement_scope='native_default_instance')
        # An HDL model parameter repeats the user parameter of the same name;
        # only the user parameter is configurable, so it supplies the row.
        model = field('Parameter ID').startswith('MODELPARAM_VALUE.')
        if name in index:
            if not model:
                rows[index[name]] = row
            continue
        index[name] = len(rows)
        rows.append(row)
    return rows


def family(key):
    """Keys differing only in their digits (IN0_WIDTH, IN1_WIDTH, ...) share a pattern."""
    return re.sub(r'\d+', '#', key) if re.search(r'\d', key) else None


def collapse_families(rows, query='', per_family=2, context=()):
    """At most per_family rows per numbered family, so a long indexed family
    cannot fill every slot. Members whose index the query names come first,
    then the lowest indices; the family keeps the position of its best row.
    """
    numbers = set(re.findall(r'\d+', query))
    # "tile 2" / "isp0" name an index of a specific word; prefer members matching more such pairs.
    wanted = {(w, n) for w, n in re.findall(r'([a-z]+)\s*(\d+)', query.lower())}
    def pairs(r):
        found = re.findall(r'([a-z]+)(\d+)', r['key'][7:].lower().replace('_', ' '))
        return sum(any(k.startswith(w) or w.startswith(k) for k, m in found if m == n and len(k) >= 2) for w, n in wanted if len(w) >= 2)
    # "3 tiles" asks for three members of the tile family; a plural query ("core clocks")
    # inherits a count that another requirement of the same task names.
    count_pattern = r'\b(\d)\s+([a-z]{3,})s\b'
    counted = re.findall(count_pattern, query.lower())
    counts = {w: min(int(n), 8) for n, w in counted}
    for text in context:
        for n, w in re.findall(count_pattern, text.lower()):
            counts.setdefault(w, min(int(n), 8))
    numbers -= {n for n, _ in counted}
    related = lambda p, w: len(p) >= 3 and (p.startswith(w) or w.startswith(p))
    index = lambda r: [-pairs(r)] + [int(n) for n in re.findall(r'\d+', r['key'])]
    def counted_index(r):
        # Members differ first in the counted word's index (tile0, tile1, tile2 before isp1).
        indexed = re.findall(r'([a-z]+)(\d+)', r['key'][7:].lower().replace('_', ' '))
        is_counted = lambda k: any(related(k, w) for w in counts)
        return [-pairs(r)] + [int(n) for k, n in indexed if not is_counted(k)] + [int(n) for k, n in indexed if is_counted(k)]
    def limit(pattern):
        words = re.findall(r'[a-z]+', pattern[7:].lower())
        return max([per_family] + [n for w, n in counts.items() if any(related(p, w) for p in words)])
    members = {}
    for row in rows:
        pattern = family(row['key'])
        if pattern:
            members.setdefault(pattern, []).append(row)
    result, emitted = [], set()
    for row in rows:
        pattern = family(row['key'])
        group = members.get(pattern, [])
        keep = limit(pattern) if pattern else per_family
        # A counted family ("3 tiles") stays together where its best member ranks.
        if len(group) <= keep and keep == per_family:
            result.append(row)
            continue
        if pattern in emitted:
            continue
        emitted.add(pattern)
        order = index if keep == per_family else counted_index
        if len(group) <= keep:
            result.extend(sorted(group, key=order))
            continue
        named = sorted((r for r in group if numbers & set(re.findall(r'\d+', r['key']))), key=order)
        rest = sorted((r for r in group if r not in named), key=order)
        for kept in (named + rest)[:keep]:
            kept = dict(kept)
            kept.update(family_pattern=pattern, family_size=len(group),
                        family_scope='other_members_share_name_pattern_describe_by_index')
            result.append(kept)
    return result


def acronyms(query):
    """Initials of adjacent query words ("virtual channels" -> "vc")."""
    words = re.findall(r'[a-z]{3,}', query.lower())
    return {''.join(w[0] for w in words[i:i + n]) for n in (2, 3) for i in range(len(words) - n + 1)}


def short_tokens(text):
    return {t.lower() for t in re.split(r'[^A-Za-z]+', re.sub(r'([a-z])([A-Z])', r'\1 \2', text)) if 2 <= len(t) <= 3}


OBSERVATION = {'actual', 'monitor', 'status', 'threshold'}
# ACT_ prefixes a readback of the frequency a PS actually reached; it is the
# same observation as "actual", too short to survive identifier_terms.
OBSERVED_SHORT = {'act'}
# Grammar words a request spells in full; they name no identifier.
GLUE = {'at', 'in', 'on', 'of', 'to', 'the', 'and', 'for', 'it', 'is', 'be',
        'by', 'or', 'an', 'as', 'its', 'via', 'per', 'but', 'not', 'any'}


def expand_compound_identifiers(text):
    """Expand common direction-prefixed compounds without IP-specific aliases."""
    text = re.sub(r'(?i)(?<![A-Za-z0-9])icache(?![A-Za-z0-9])', 'instruction cache', text)
    text = re.sub(r'(?i)(?<![A-Za-z0-9])dcache(?![A-Za-z0-9])', 'data cache', text)
    # The unit is a word of the identifier too: "150 MHz" and ..._FREQMHZ name
    # the same measure in the same spelling, which ..._CLK does not. Settle on
    # one spelling and free it from the quantity so it compares as a token.
    text = re.sub(r'(?<![A-Z])([KMG]?[Hh][Zz])(?![A-Za-z0-9])', lambda m: m[1].upper(), text)
    text = re.sub(r'(?i)(?<![A-Za-z0-9])([A-Za-z]{2,}?)(MHZ|KHZ|GHZ)(?![A-Za-z0-9])',
                  lambda m: m[1] + ' ' + m[2].upper(), text)
    # One fabric, two spellings: a Zynq-7000 PS names its PL clocks FPGA<n>,
    # an UltraScale+ PS names the same clocks PL<n>, and a request says either.
    text = re.sub(r'(?i)(?<![A-Za-z0-9])PL(\d*)(?![A-Za-z0-9])', r'PL\1 FPGA\1', text)
    text = re.sub(r'(?i)(?<![A-Za-z0-9])FPGA(\d*)(?![A-Za-z0-9])', r'FPGA\1 PL\1', text)
    return text


def rank(rows, query, limit=4, context=()):
    asked = expand_compound_identifiers(query)
    wanted = Database.identifier_terms(asked)
    # An acronym the request spells out itself (PL, GP, HP) is the word that
    # tells two otherwise identical clock properties apart, and it is shorter
    # than identifier_terms keeps, so match it as an abbreviation.
    initials = acronyms(asked) | (short_tokens(asked) - GLUE)
    ranked = []
    for row in rows:
        text = expand_compound_identifiers(row['key'][7:] + ' ' + row['label'])
        names = Database.identifier_terms(text)
        short = short_tokens(text)
        all_terms = names | Database.identifier_terms(row.get('help', ''))
        matches = lambda terms: {q for q in wanted if any(q.startswith(t) or t.startswith(q) for t in terms)}
        exact, broad = matches(names), matches(all_terms)
        abbreviated = initials & short
        exact, broad = exact | abbreviated, broad | abbreviated
        if broad:
            # Observed/derived values (actual, monitor, status) rank below the setting itself.
            observed = (bool(names & OBSERVATION or short & OBSERVED_SHORT)
                        and not (wanted & OBSERVATION or initials & OBSERVED_SHORT))
            ranked.append((len(exact) - observed, len(broad), row['key'], row))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return collapse_families([item[3] for item in ranked], query, context=context)[:limit]
