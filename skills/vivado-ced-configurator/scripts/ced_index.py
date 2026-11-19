# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Per-run CED index built from one exact store commit.

Store files are untrusted data: JSON and XML are parsed, Tcl is tokenized and
never evaluated. The index ranks designs for retrieval only; Vivado evaluates
options, support and legality for the chosen design.
"""
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

MAX_FILE = 400_000
STOPWORDS = {'the', 'and', 'for', 'with', 'that', 'this', 'into', 'from', 'using', 'use', 'design', 'designs',
             'example', 'examples', 'based', 'create', 'want', 'need', 'make', 'build', 'set', 'xilinx', 'amd',
             'com', 'its', 'are', 'can', 'will', 'which', 'both', 'all', 'only', 'one', 'two', 'has', 'have',
             'any', 'also', 'such', 'other', 'more', 'than', 'out', 'per', 'via', 'run', 'not', 'get', 'give',
             'on', 'of', 'to', 'in', 'at', 'by', 'as', 'is', 'or', 'an', 'be', 'it', 'we', 'my', 'so', 'up'}
WEIGHTS = {'display': 3, 'name': 3, 'keywords': 2, 'options': 2, 'category': 1, 'description': 1,
           'readme': 1, 'supports': 1}


def read_text(path):
    data = Path(path).read_bytes()[:MAX_FILE]
    return data.decode('utf-8', errors='replace').lstrip('\ufeff')


def tcl_words(text, start=0, limit=None):
    """Tokenize Tcl word syntax as data. Returns (words, end). Brackets stay raw text."""
    words, i, n = [], start, len(text)
    while i < n and (limit is None or len(words) < limit):
        while i < n and (text[i] in ' \t\r\n;' or text.startswith('\\\n', i)):
            i += 2 if text.startswith('\\\n', i) else 1
        if i >= n:
            break
        c = text[i]
        if c in '{[':
            close = '}' if c == '{' else ']'
            depth, j = 0, i
            while j < n:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == c:
                    depth += 1
                elif text[j] == close:
                    depth -= 1
                    if not depth:
                        break
                j += 1
            words.append(text[i + 1:j] if c == '{' else text[i:j + 1])
            i = j + 1
        elif c == '"':
            j, out = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == '\\' and j + 1 < n:
                    out.append(' ' if text[j + 1] == '\n' else text[j + 1])
                    j += 2
                    continue
                out.append(text[j])
                j += 1
            words.append(''.join(out))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n;':
                if text[j] == '[':
                    depth = 0
                    while j < n:
                        depth += text[j] == '['
                        depth -= text[j] == ']'
                        j += 1
                        if not depth:
                            break
                    continue
                j += 1
            words.append(text[i:j])
            i = j
    return words, i


def strip_comments(text):
    return '\n'.join('' if line.lstrip().startswith('#') else line for line in text.splitlines())


def proc_body(text, name):
    match = re.search(r'(?m)^\s*proc\s+' + re.escape(name) + r'\s', text)
    if not match:
        return None
    words, _ = tcl_words(text, match.end(), 2)
    return words[1] if len(words) == 2 else None


def bracket_spans(text, opener):
    """Raw text inside each top-level `[<opener> ...]` command."""
    spans = []
    for match in re.finditer(r'\[\s*' + opener + r'\b', text):
        depth, j = 0, match.start()
        while j < len(text):
            if text[j] == '\\':
                j += 2
                continue
            depth += text[j] == '['
            depth -= text[j] == ']'
            if not depth:
                break
            j += 1
        spans.append(text[match.end():j])
    return spans


def choice(element):
    parts = element.split(None, 1)
    if not parts:
        return None
    value = parts[0]
    label = parts[1] if len(parts) > 1 else value
    return dict(value=value, label=' '.join(label.replace('_', ' ').split()))


def parse_options(init_text):
    """Static addOptions view: names, types, defaults, choices. Board-conditional variants are merged."""
    body = proc_body(strip_comments(init_text), 'addOptions')
    if body is None:
        return []
    options = {}
    for span in bracket_spans(body, 'dict create'):
        words, _ = tcl_words(span)
        if len(words) % 2:
            words = words[:-1]
        fields = dict(zip(words[::2], words[1::2]))
        name = fields.get('name', '')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            continue
        row = options.setdefault(name, dict(name=name, type=fields.get('type', 'string'), default=fields.get('value', ''),
                                            choices=[], variants=0, dynamic=False))
        row['variants'] += 1
        # Commands and variable references are only known when Vivado evaluates addOptions.
        dynamic = lambda word: word.startswith(('[', '$'))
        if any(dynamic(w) for w in fields.values()):
            row['dynamic'] = True
        if 'value_list' in fields and not dynamic(fields['value_list']):
            seen = {c['value'] for c in row['choices']}
            for element in tcl_words(fields['value_list'])[0]:
                item = choice(element)
                if item and item['value'] not in seen:
                    row['choices'].append(item)
                    seen.add(item['value'])
    for row in options.values():
        if row['type'] in ('bool', 'boolean') and not row['choices']:
            row['choices'] = [dict(value='true', label='true'), dict(value='false', label='false')]
    return list(options.values())


def parse_updaters(init_text):
    """Updater headers in definition order, mapped to the proc names Vivado creates for them."""
    text = strip_comments(init_text)
    counters, rows = Counter(), []
    prefix = {'updater': 'update_', 'gui_updater': 'update_gui_for_', 'validater': 'validate_'}
    for match in re.finditer(r'(?m)^\s*(gui_updater|updater|validater)\s', text):
        kind = match.group(1)
        words, _ = tcl_words(text, match.end(), 2)
        if len(words) < 2:
            continue
        rows.append(dict(kind=kind, proc=prefix[kind] + str(counters[kind]),
                         inputs=tcl_words(words[0])[0], outputs=tcl_words(words[1])[0]))
        counters[kind] += 1
    return rows


def support_text(init_text):
    text = strip_comments(init_text)
    out = []
    for name in ['getSupportedBoards', 'getSupportedParts']:
        body = proc_body(text, name)
        if body and body.strip() and body.strip() not in ('return ""', 'return ""; ', 'return "";'):
            out.append(name + ': ' + ' '.join(body.split())[:300])
    return ' | '.join(out)


def parse_design_xml(path):
    root = ET.fromstring(read_text(path))
    field = lambda tag: ' '.join((root.findtext(tag) or '').split())
    return dict(vendor=field('Vendor'), library=field('Library'), name=field('Name'), version=field('Version'),
                display=field('DisplayName'), description=field('Description'), entry=field('Design'))


def tokens(text):
    out = []
    for word in re.findall(r'[a-z0-9]+', text.lower()):
        if len(word) < 2 or word in STOPWORDS:
            continue
        out.append(word)
        alpha = re.match(r'[a-z]+', word)
        if alpha and alpha.group() != word and len(alpha.group()) >= 2:
            out.append(alpha.group())
    return out


def variant_marks(text):
    """The standalone single letters in a name.

    tokens() drops every word shorter than two characters, so "MicroBlaze V
    Design Presets" and "MicroBlaze Design Presets" -- two different processors
    -- scored identically. ced-gen_E3 and ced-gen_E3w sent the same "Classic
    MicroBlaze microcontroller preset" request and were handed different
    designs, because nothing but the lost "V" separated them.
    """
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if re.fullmatch(r'[a-z]', w)}


def version_key(text):
    """Sortable form of a dotted version, e.g. '3.0' -> (3, 0). Blank sorts first."""
    return tuple(int(n) for n in re.findall(r'\d+', str(text or '')))


class Index:
    def __init__(self, mirror_path, branch):
        self.root = Path(mirror_path)
        self.branch = branch
        self.entries = []
        self.variants = []
        self.catalog_only = []
        self.load()
        self.prepare()

    def load(self):
        catalog_items = []
        path = self.root / 'catalog' / ('vivado_' + self.branch + '.json')
        if path.is_file():
            catalog_items = json.loads(read_text(path)).get('catalog', {}).get('items', [])
        by_root, by_name = {}, {}
        for item in catalog_items:
            root = ((item.get('config') or {}).get('root') or '').strip('/')
            if root:
                by_root.setdefault(root, []).append(item)
            by_name[item.get('name')] = item
        consumed = set()
        for design_xml in sorted((self.root / 'ced').rglob('design.xml')):
            folder = design_xml.parent
            try:
                design = parse_design_xml(design_xml)
            except ET.ParseError:
                continue
            if not design['name']:
                continue
            infra = {}
            if (folder / 'xitem.json').is_file():
                try:
                    items = json.loads(read_text(folder / 'xitem.json'))['config']['items']
                    infra = next((i['infra'] for i in items if i.get('infra', {}).get('name') == design['name']),
                                 items[0].get('infra', {}) if items else {})
                except (ValueError, KeyError, TypeError):
                    infra = {}
            init_name = design['entry'] if re.fullmatch(r'[\w.+-]+\.tcl', design['entry'] or '') else 'init.tcl'
            init_text = read_text(folder / init_name) if (folder / init_name).is_file() else ''
            readme = next((read_text(folder / n) for n in ['readme.txt', 'README.md'] if (folder / n).is_file()), '')
            rel = folder.relative_to(self.root).as_posix()
            entry = dict(
                vlnv=':'.join([design['vendor'] or 'xilinx.com', design['library'] or 'design', design['name'],
                               design['version'] or '1.0']),
                name=design['name'], display=design['display'] or infra.get('display', design['name']),
                description=' '.join((infra.get('description', '') + ' ' + design['description']).split()),
                category=infra.get('category', ''), keywords=infra.get('search-keywords', []),
                active=infra.get('is_active', True) is not False, root=rel,
                kind='nonIPI' if '/nonIPI/' in '/' + rel + '/' else 'IPI',
                options=parse_options(init_text), updaters=parse_updaters(init_text),
                supports=support_text(init_text), readme=' '.join(readme.split())[:1500])
            item = self.catalog_item(by_root.get(rel, []), by_name, design['name'])
            if item is not None:
                consumed.add(id(item))
            item = item or {}
            entry['revision'] = item.get('latest_revision', infra.get('revision', ''))
            entry['commit_id'] = item.get('commit_id', '')
            self.entries.append(entry)
        self.catalog_only = sorted({i.get('name') or '' for i in catalog_items if id(i) not in consumed} - {''})
        self.collapse()

    @staticmethod
    def catalog_item(candidates, by_name, name):
        """The catalog item describing one design folder, matched on root not name.

        Design names are not unique in the store catalog. 2026.2 lists 55 items
        under 48 names: edf_base appears four times, once per versioned folder
        plus the extensible-platform folder whose design.xml calls itself
        base_ext_platform. Keying the catalog by name dropped seven items and
        handed all three edf_base folders the revision of whichever one parsed
        last, while base_ext_platform missed the join and came out blank.

        One root can still carry several items -- the fixture's beta_stale
        shadows beta_pcie -- so a name match inside the root wins, and anything
        left unclaimed is reported through catalog_only.
        """
        if candidates:
            named = [i for i in candidates if i.get('name') == name]
            if named:
                return named[-1]
            if len(candidates) == 1:
                return candidates[0]
            return max(candidates, key=lambda i: version_key(i.get('latest_revision')))
        return by_name.get(name)

    def collapse(self):
        """One entry per design, the highest VLNV version; older folders stay in variants.

        2026.2 ships three edf_base folders and three edf_base_pl folders whose
        display name, option set and support filter are all identical -- only
        the folder version differs. Ranked side by side they are indistinguish-
        able rows, and choose_ced by display name cannot say which was meant.
        Collapsing loses nothing the model could have acted on; the superseded
        revisions stay reachable through by_vlnv.
        """
        groups = {}
        for entry in self.entries:
            groups.setdefault((entry['name'], entry['display']), []).append(entry)
        newest = {id(max(family, key=lambda e: version_key(e['vlnv'].rsplit(':', 1)[-1])))
                  for family in groups.values()}
        self.variants = [e for e in self.entries if id(e) not in newest]
        self.entries = [e for e in self.entries if id(e) in newest]

    def fields(self, entry):
        option_text = ' '.join(o['name'].replace('_', ' ') + ' ' + ' '.join(c['value'].replace('_', ' ') + ' ' + c['label']
                                                                            for c in o['choices']) for o in entry['options'])
        return dict(display=entry['display'], name=entry['name'].replace('_', ' '),
                    keywords=' '.join(entry['keywords']), options=option_text, category=entry['category'],
                    description=entry['description'], readme=entry['readme'], supports=entry['supports'])

    def prepare(self):
        self.docs = []
        for entry in self.entries:
            counts = Counter()
            for field, text in self.fields(entry).items():
                for token in tokens(text):
                    counts[token] += WEIGHTS[field]
            self.docs.append(counts)
        self.avg = sum(sum(d.values()) for d in self.docs) / max(1, len(self.docs))
        self.df = Counter(t for d in self.docs for t in d)
        self.marks = self.variant_families()

    def variant_families(self):
        """Per design, (its own variant letters, every variant letter in its family).

        A lone letter marks a variant only where another design carries the same
        name without it. The store has exactly one such family, the three
        MicroBlaze V presets against their classic twins. "VM-P-M1369 Embedded
        Common Platform" has no twin, so its stray "P" stays part of a part
        number and the design keeps its full score.
        """
        stem = lambda entry: ' '.join(w for w in re.findall(r'[a-z0-9]+', entry['display'].lower())
                                      if not re.fullmatch(r'[a-z]', w))
        families = {}
        for entry in self.entries:
            families.setdefault(stem(entry), []).append(entry)
        marks = {}
        for family in families.values():
            shared = set()
            if len(family) > 1:
                shared = set().union(*(variant_marks(e['display']) for e in family))
            for entry in family:
                marks[entry['vlnv']] = (variant_marks(entry['display']) & shared, shared)
        return marks

    def search(self, query, limit=8, exclude=()):
        """BM25 over weighted fields. Similarity is retrieval order, not suitability.

        exclude drops terms that describe the target rather than the design. A
        package and speed grade are nearly unique, so they carry the highest idf
        in the index while saying nothing about what a design does: on ced-gen_H2
        the supports text of cpm6_dma matched vsvc3340 and 3hp and outscored
        cpm6_bmd's own "controllers" and "bus", and the bus-mastering demo was
        reported as the DMA one. Which designs fit the target is a separate live
        question that cedcfg::support answers.
        """
        drop = set(tokens(' '.join(exclude)))
        terms = [t for t in dict.fromkeys(tokens(query)) if t not in drop]
        asked = variant_marks(query)
        n, scored = len(self.docs), []
        for entry, doc in zip(self.entries, self.docs):
            length = sum(doc.values())
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self.avg))
            if not entry['active']:
                score *= 0.5
            # A request that does not name the variant means the plain design,
            # and one that names it means that one. Both directions, so "Classic
            # MicroBlaze" and "MicroBlaze V" each reach their own preset.
            own, family = self.marks.get(entry['vlnv'], (set(), set()))
            if (own - asked) or (asked & family and not own & asked):
                score *= 0.5
            scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1]['display']))
        return [(round(s, 3), e) for s, e in scored[:limit] if s > 0]

    def by_vlnv(self, vlnv):
        """Current designs first, then the revisions collapse() set aside."""
        return next((e for e in self.entries if e['vlnv'] == vlnv),
                    next((e for e in self.variants if e['vlnv'] == vlnv), None))

    def by_name(self, text):
        key = ' '.join(text.lower().replace('_', ' ').split())
        for entry in self.entries:
            if key in {entry['name'].lower().replace('_', ' '), entry['display'].lower(), entry['vlnv'].lower()}:
                return entry
        return None
