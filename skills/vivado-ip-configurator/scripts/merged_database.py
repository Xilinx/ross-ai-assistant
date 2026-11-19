# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Read-only reader for the ipdb.sqlite a Vivado install ships at data/ip/ipdb/.

That file is a different artifact from the snapshot `ip_database.Database` opens.  It carries
schema 1.7-merged -- tables ip/param/nested_key/probe_fact with FTS5 indexes -- and no embedding
vectors, so retrieval here is lexical only and no external model or SQLite extension is mounted.
Every reply keeps the shapes ip_database produces, so the call sites in structured_config are
identical for either backend and nothing downstream has to know which one answered.

The database still never supplies legality: ranges, enum legality and enablement come from the
live session.  What it adds over the live path is searchable names, help text, enum labels, and
the recorded prerequisite of a value that Vivado will not let you write yet.
"""
import json
import re
import sqlite3
from pathlib import Path

from ip_database import Database as Snapshot

SCHEMA_PREFIX = '1.7'
IP_RANK = 'bm25(ip_search, 4.0, 3.0, 1.0)'               # name, display_name, description
PARAM_RANK = 'bm25(param_search, 4.0, 3.0, 2.0, 1.0)'    # name, display_name, labels, help
RELATIVE = ('data', 'ip', 'ipdb', 'ipdb.sqlite')

# Query expansion mirrors the builder's indexer (DBCreation/search_index.py): a query word and an
# indexed word must be split, expanded and stemmed alike or recall collapses on identifier names.
ABBREV = {'clk': 'clock', 'freq': 'frequency', 'num': 'number', 'no': 'number', 'nr': 'number',
          'en': 'enable', 'ena': 'enable', 'intf': 'interface', 'rx': 'receive', 'tx': 'transmit',
          'dir': 'direction', 'addr': 'address', 'rst': 'reset', 'ctrl': 'control', 'sel': 'select',
          'src': 'source', 'cfg': 'configuration', 'config': 'configuration', 'prot': 'protocol',
          'diff': 'differential', 'mem': 'memory', 'buf': 'buffer', 'coef': 'coefficient',
          'coeff': 'coefficient', 'mult': 'multiply', 'div': 'divide', 'sync': 'synchronous',
          'async': 'asynchronous', 'ext': 'external', 'int': 'internal', 'pkt': 'packet',
          'bw': 'bandwidth', 'ref': 'reference', 'reg': 'register', 'cdc': 'clock domain crossing',
          'out': 'output'}
# A quantity's unit names the measure a parameter holds (16-bit -> width).
UNITS = {'bit': 'width', 'bits': 'width', 'wide': 'width', 'mhz': 'frequency', 'khz': 'frequency',
         'ghz': 'frequency', 'hz': 'frequency', 'kb': 'size', 'mb': 'size', 'gb': 'size',
         'kbyte': 'size', 'deep': 'depth', 'gbps': 'rate', 'mbps': 'speed', 'db': 'range'}
# Function words only: up, down, and, or, not, out, all and none are enum values or name parts.
STOP = set('''a an the of to at for by with from into onto as is are be been being it its this that
these those but so than then there their them they we you your our use used using uses set sets
setting configure configured make made each every which who whom while when where how what such can
may must should would will shall do does done one two three four five six seven eight nine ten'''.split())
NUMBER_WORDS = {'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
                'single', 'dual', 'twelve', 'sixteen'}


def shipped_database(install_root):
    """Path of the database inside a Vivado install, or None when that install ships none."""
    if not install_root:
        return None
    path = Path(install_root).joinpath(*RELATIVE)
    return path if path.is_file() else None


def release_of(version_text):
    """The release (2026.2) from a Vivado banner; the gate below is the release, never the build."""
    match = re.search(r'\bv?(\d{4}\.\d+)', version_text or '')
    return match[1] if match else None


def split_words(text):
    """Lowercase words of identifiers and prose: whole tokens, plus their pieces at '_', case
    changes and letter/digit boundaries (C_S_AXI_DATA_WIDTH, PortBWidth, FREQMHZ)."""
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(text or ''))
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', text)
    text = re.sub(r'(?i)(?<![A-Za-z])([A-Za-z]{2,}?)(MHZ|KHZ|GHZ|HZ)(?![A-Za-z])', r'\1 \2', text)
    words = []
    for token in re.findall(r'[A-Za-z0-9]+', text):
        words.append(token.lower())
        parts = re.findall(r'[A-Za-z]+|\d+', token)
        if len(parts) > 1:
            words += [p.lower() for p in parts]
    return words


def agentive(word, vocabulary):
    """The verb behind an agent noun the stemmer leaves alone (subtractor -> subtract)."""
    if len(word) >= 6 and word.endswith(('or', 'er')):
        return [v for v in (word[:-2], word[:-1]) if v in vocabulary][:1]
    return []


def param_query(text, vocabulary):
    """FTS5 OR-query for a parameter search: words, abbreviations spelled out, and the measure a
    unit or a count asks for ('two lanes' -> number, '16-bit' -> width)."""
    text = re.sub(r'(?i)\b([kmg])b/s\b|\b([kmg])bps\b', ' speed rate ', text)
    raw, out = split_words(text), []
    # A count asks for a number of things even with a qualifier between ('4 active lanes').
    for index, word in enumerate(raw):
        if word.isdigit() or word in NUMBER_WORDS:
            if any(f.endswith('s') and len(f) > 2 and f not in UNITS and f not in STOP
                   for f in raw[index + 1:index + 3]):
                out.append('number')
                break
    for word in raw:
        if word in STOP or word.isdigit() or len(word) < 2:
            continue
        out += [word, *agentive(word, vocabulary), *UNITS.get(word, '').split(), *ABBREV.get(word, '').split()]
    return ' OR '.join('"%s"' % w for w in dict.fromkeys(filter(None, out)))


def ip_query(text):
    """FTS5 OR-query for IP discovery: the words, plus adjacent words joined (UART Lite -> uartlite)."""
    words = [w.lower() for w in re.findall(r'[A-Za-z0-9]+', text)]
    out = [w for w in words if w not in STOP and not w.isdigit() and len(w) > 1]
    out += [a + b for a, b in zip(words, words[1:])
            if a not in STOP and b not in STOP and not (a + b).isdigit()]
    return ' OR '.join('"%s"' % w for w in dict.fromkeys(out))


class MergedDatabase:
    """The release database shipped in a Vivado install, opened read-only."""

    def __init__(self, path, release=None):
        self.path = Path(path).resolve()
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.meta = {r['key']: r['value'] for r in self.db.execute('SELECT key, value FROM meta')}
        schema = str(self.meta.get('schema_version', ''))
        if not schema.startswith(SCHEMA_PREFIX):
            raise ValueError('Unsupported database schema ' + (schema or 'unknown'))
        self.release = self.meta.get('vivado_version')
        if release and self.release != release:
            raise ValueError('SNAPSHOT_MISMATCH: database release ' + str(self.release))
        match = re.search(r'SW Build (\d+)', self.meta.get('parts_vivado', '') or '')
        self.build = match[1] if match else None
        self.vocabulary = None

    def close(self):
        self.db.close()

    def stamp(self, build):
        """Both builds, never a rejection.

        The shipped file is extracted from an earlier build of its release than the install that
        carries it, so the SW-build equality the snapshot backend enforces would reject this
        database on the very install that ships it.  The release is the gate; the drift is
        reported so a caller can see metadata may trail the running tools.
        """
        data = dict(build=self.build, release=self.release, backend='release_database')
        if build and self.build and str(build) != self.build:
            data['build_scope'] = 'extracted_from_build_%s_of_release_%s' % (self.build, self.release)
            data['live_build'] = str(build)
        return data

    def vocab(self):
        if self.vocabulary is None:
            try:
                self.vocabulary = {w for (w,) in self.db.execute('SELECT word FROM search_vocab')}
            except sqlite3.Error:
                self.vocabulary = set()
        return self.vocabulary

    def ip_row(self, vlnv):
        row = self.db.execute('SELECT * FROM ip WHERE vlnv=?', (vlnv,)).fetchone()
        if not row:
            raise ValueError('VLNV absent from snapshot')
        return row

    def enablement(self, ip_id, param):
        """The recorded prerequisite of a value, keyed exactly as the probe recorded it.

        `detail` is the assignment that made the value stick, so it is actionable advice rather
        than a bare "disabled"; it is still confirmed by live readback like everything else.
        """
        rows = self.db.execute(
            "SELECT value, context, part, detail FROM probe_fact WHERE ip_id=? AND param=? "
            "AND fact='enabled_by' AND detail IS NOT NULL ORDER BY context='xgui' DESC LIMIT 1",
            (ip_id, param)).fetchall()
        if not rows:
            return {}
        row = rows[0]
        try:
            requires = json.loads(row['detail'])
        except ValueError:
            return {}
        if not isinstance(requires, dict) or not requires:
            return {}
        scope = ('xgui_rule_recorded_in_release_database' if row['context'] == 'xgui'
                 else 'probe_observed_on_' + str(row['part']))
        data = dict(enablement_requires={('CONFIG.' + k if not k.startswith('CONFIG.') else k): v
                                         for k, v in list(requires.items())[:6]},
                    enablement_source_scope=scope)
        if row['value']:
            data['enablement_observed_value'] = str(row['value'])[:80]
        return data

    def parameter_record(self, row, part=None, ip_id=None):
        """One candidate in the shape ip_database.parameter returns."""
        name = row['name']
        result = dict(key='CONFIG.' + name, label=str(row['display_name'] or name)[:160], vlnv=row['vlnv'])
        for field, column in (('type', 'format'), ('default', 'default_value')):
            full = '' if row[column] is None else str(row[column])
            result[field] = full[:240]
            if len(full) > 240:
                result[field + '_truncated'] = True
        result.update(range='RANGE_NONE', range_scope='requires_current_configuration',
                      choices_scope='packaged_labels_not_current_legal_values')
        try:
            choices = json.loads(row['enum_json']) if row['enum_json'] else []
        except ValueError:
            choices = []
        # An enum entry without a display label shows its value, never the string 'None'.
        pair = lambda c: ([c[0], c[-1] if c[-1] is not None else c[0]] if isinstance(c, (list, tuple)) and c
                          else [c, c])
        result['choices'] = [{'value': str(pair(c)[0])[:160],
                              'label': str(pair(c)[-1])[:160],
                              'truncated': len(str(pair(c)[0])) > 160 or len(str(pair(c)[-1])) > 160}
                             for c in choices[:6]]
        result['choices_count'] = len(choices)
        result['choices_more'] = len(choices) > 6
        text = str(row['tooltip'] or row['description'] or '')
        result.update(help=text[:240], help_truncated=len(text) > 240,
                      help_source=row['tooltip_source'] or ('packaged' if row['description'] else 'unavailable'),
                      help_scope='packaged_definition_not_current_live_state', help_part=None,
                      bd_write_mapping_verified=False, requires_live_validation=True)
        if row['gui_path']:
            result['gui_context'] = [s.strip() for s in str(row['gui_path']).split('/') if s.strip()][:8]
            result['gui_context_scope'] = 'packaged_gui_literal_not_live'
        result.update(self.enablement(ip_id if ip_id is not None else row['ip_id'], name))
        return result

    def lookup(self, vlnv, keys, build, part=None):
        if not 1 <= len(keys) <= 4:
            raise ValueError('Select 1..4 exact CONFIG keys')
        ip = self.ip_row(vlnv)
        found, missing = [], []
        for key in keys:
            if not re.fullmatch(r'CONFIG\.[A-Za-z0-9_]+', key):
                raise ValueError('Exact CONFIG key required')
            row = self.db.execute('SELECT * FROM param_v WHERE ip_id=? AND name=?',
                                  (ip['id'], key[7:])).fetchone()
            if row:
                found.append(self.parameter_record(row, part, ip['id']))
            else:
                missing.append(key)
        return dict(status='DB:LOOKUP', parameters=found, missing=missing, **self.stamp(build))

    def catalog(self, vlnv, build, part=None):
        """Complete small catalogs bypass ranked retrieval, within a fixed budget."""
        ip = self.ip_row(vlnv)
        rows = self.db.execute('SELECT * FROM param_v WHERE ip_id=? ORDER BY name', (ip['id'],)).fetchall()
        if not rows:
            raise ValueError('VLNV absent from snapshot')
        if len(rows) > 24:
            return dict(status='DB:CATALOG_TOO_LARGE', count=len(rows), candidates=[])
        return dict(status='DB:CATALOG', count=len(rows),
                    candidates=[self.parameter_record(r, part, ip['id']) for r in rows])

    def field_hints(self, names, build, part=None, vlnv=None):
        """Exact-name help across source definitions, never a parent write map.

        A dictionary field usually has no definition of its own: nested_key records which subcore
        parameter it feeds, and that parameter carries the text and enum Vivado renders.  Resolving
        it is the only way this backend can describe a nested field at all.
        """
        if not isinstance(names, list) or not 1 <= len(names) <= 16 or any(
                not isinstance(n, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', n) for n in names):
            raise ValueError('Select 1..16 exact native field names')
        owner = None
        if vlnv:
            row = self.db.execute('SELECT id FROM ip WHERE vlnv=?', (vlnv,)).fetchone()
            owner = row['id'] if row else None
        result = {}
        for name in dict.fromkeys(names):
            records = [self.parameter_record(r, part)
                       for r in self.db.execute('SELECT * FROM param_v WHERE name=? ORDER BY id LIMIT 6', (name,))]
            nested = self.db.execute(
                'SELECT * FROM nested_key WHERE key=?' + (' AND ip_id=?' if owner else '') + ' LIMIT 6',
                (name, owner) if owner else (name,)).fetchall()
            for row in nested:
                if not (row['subcore_vlnv'] and row['subcore_param']):
                    continue
                sub = self.db.execute('SELECT * FROM param_v WHERE vlnv=? AND name=?',
                                      (row['subcore_vlnv'], row['subcore_param'])).fetchone()
                if sub is not None:
                    records.append(self.parameter_record(sub, part))
            if not records:
                continue
            hint = dict(source_vlnvs=list(dict.fromkeys(r['vlnv'] for r in records))[:6],
                        hint_scope='exact_name_in_other_definitions_not_parent_binding',
                        help='; '.join(dict.fromkeys(r.get('help', '') for r in records if r.get('help')))[:240],
                        labels=list(dict.fromkeys(r.get('label', '') for r in records if r.get('label')))[:3])
            for key in ['type', 'choices', 'choices_more']:
                values = [r.get(key) for r in records]
                if all(v == values[0] for v in values):
                    hint[key] = values[0]
            # The prerequisite is recorded against the container-qualified name, which is where a
            # dictionary field's enablement actually lives.
            for row in nested:
                enablement = self.enablement(row['ip_id'], row['container'] + '.' + name)
                if enablement:
                    hint.update(enablement)
                    break
            result[name] = hint
        return dict(status='DB:FIELD_HINTS', fields=result, **self.stamp(build))

    def search(self, query, build, vlnv=None, part=None, mode='hybrid', limit=None):
        """Lexical retrieval. `semantic` is refused rather than silently answered another way."""
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError('Nonempty query of at most 600 characters required')
        if mode not in ['hybrid', 'semantic', 'lexical']:
            raise ValueError('Unknown search mode')
        if mode == 'semantic':
            raise ValueError('Release database has no embeddings; use lexical or hybrid')
        maximum = 4 if vlnv else 3
        limit = maximum if limit is None else limit
        if not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise ValueError('Result limit outside bounded budget')
        candidates = (self.parameter_candidates(query, vlnv, part, limit) if vlnv
                      else self.ip_candidates(query, part, limit))
        return dict(status='DB:PARAMETERS' if vlnv else 'DB:IPS', mode='lexical', query=query,
                    candidates=candidates, candidate_only=True, requires_live_validation=True,
                    **self.stamp(build))

    def part_support(self, part):
        """{vlnv: status} for the IPs the release lists as supported on `part`, or None when the
        part is unknown to this database (then nothing is reordered or claimed)."""
        if not part:
            return None
        cache = getattr(self, '_support', {})
        if part not in cache:
            try:
                rows = self.db.execute('SELECT vlnv, status FROM ip_part_v WHERE part=?', (part,)).fetchall()
            except sqlite3.Error:
                rows = []
            cache[part] = {r['vlnv']: r['status'] for r in rows} or None
            self._support = cache
        return cache[part]

    def part_support_rows(self, vlnvs, part):
        """Release-recorded part support for 1..6 VLNVs, in the shape lite::candidate_availability
        returns, so the live check is needed only for what this release does not record.

        A WRONG_PART row also carries `alternatives`: visible IPs the part does support with the
        same function -- the same name family (axi_noc -> axi_noc2) or display name (clk_wizard ->
        clkx5_wiz), else the single closest catalog text match (versal_cips -> ps_wizard).
        Nothing is keyed on IP names; the agent still chooses and live creation decides.
        """
        if not isinstance(vlnvs, list) or not 1 <= len(vlnvs) <= 6 or any(
                not isinstance(v, str) or not re.fullmatch(r'[\w.-]+:[\w.-]+:[\w.-]+:[\w.-]+', v) for v in vlnvs):
            raise ValueError('Select 1..6 exact VLNVs')
        if not isinstance(part, str) or not re.fullmatch(r'[\w.-]+', part):
            raise ValueError('Exact part required')
        support = self.part_support(part)
        rows = []
        for vlnv in vlnvs:
            known = self.db.execute('SELECT 1 FROM ip WHERE vlnv=?', (vlnv,)).fetchone()
            if support is None or not known:
                rows.append(dict(vlnv=vlnv, status='UNKNOWN'))
                continue
            if vlnv in support:
                rows.append(dict(vlnv=vlnv, status='AVAILABLE'))
                continue
            parts = [r['part'] for r in self.db.execute(
                'SELECT part FROM ip_part_v WHERE vlnv=? ORDER BY part', (vlnv,))]
            examples, families = [], set()
            for name in parts:
                family = re.match(r'[a-z]+', name)
                family = family[0] if family else name
                if family not in families:
                    families.add(family)
                    examples.append(name)
                if len(examples) >= 4:
                    break
            alternatives, pool = self.alternatives(vlnv, support), self.same_category(vlnv, support)
            # A word-match stand-in that is just one member of a several-IP category is a guess;
            # list the category instead of naming that guess as the replacement.
            if len(pool) > 1:
                members = {c['name'] for c in pool}
                alternatives = [a for a in alternatives if a['match'] != 'catalog_text'
                                or a['vlnv'].split(':')[2] not in members]
            rows.append(dict(vlnv=vlnv, status='WRONG_PART', supported_count=len(parts),
                             example_parts=examples, alternatives=alternatives, same_category=pool))
        return rows

    def support_table(self, part):
        """{vlnv: [status, supported_count, example_parts]} for every IP, for seeding lite.tcl.

        Reading SUPPORTED_PARTS live costs up to 100 s for one definition (vcu:1.2 on 2026.2),
        and lite::catalog read it twice for an IP the part cannot host, which took a case past
        its time budget. The release records the same lists; ~1 s builds the whole table once
        per part. Example parts follow lite::supported_examples: sorted, one per family, four.
        """
        if not isinstance(part, str) or not re.fullmatch(r'[\w.-]+', part):
            raise ValueError('Exact part required')
        cache = getattr(self, '_support_tables', {})
        if part in cache:
            return cache[part]
        support = self.part_support(part)
        if support is None:
            cache[part] = None
            self._support_tables = cache
            return None
        table = {v: ['AVAILABLE', 0, []] for v in support}
        counts = {r[0]: r[1] for r in self.db.execute('SELECT vlnv, count(*) FROM ip_part_v GROUP BY vlnv')}
        for vlnv, name in self.db.execute('SELECT vlnv, part FROM ip_part_v ORDER BY vlnv, part'):
            if vlnv in support:
                continue
            row = table.setdefault(vlnv, ['WRONG_PART', counts.get(vlnv, 0), []])
            family = (re.match(r'[a-z]+', name) or [name])[0]
            if len(row[2]) < 4 and family not in [(re.match(r'[a-z]+', p) or [p])[0] for p in row[2]]:
                row[2].append(name)
        cache[part] = table
        self._support_tables = cache
        return table

    def influences(self, vlnv, keys, per_key=4):
        """Parameters recorded as affecting each of `keys`, with values worth trying (issue #548).

        A write can be accepted yet not stick because another setting governs it (axi_noc2 NUM_MC
        reads back unchanged until the memory-controller mode is set). The release graph records
        such links: `affects`/`enables`/`sets_value` edges, measured by probing Vivado
        (vivado_probe) or read from package and GUI files. Probe-measured links come first.
        *_BOARD_INTERFACE parameters are left out: changing them rewires board connections, which
        the skill never does unasked. Candidates only -- lite::stick_effect proves them live.
        """
        if not isinstance(keys, list) or not 1 <= len(keys) <= 4 or any(
                not isinstance(k, str) or not re.fullmatch(r'(CONFIG\.)?[A-Za-z0-9_]+', k) for k in keys):
            raise ValueError('Select 1..4 exact parameter names')
        ip = self.ip_row(vlnv)
        rank = {'vivado_probe': 0, 'component.xml': 1}
        result = {}
        for key in keys:
            name = key[7:] if key.startswith('CONFIG.') else key
            rows = self.db.execute(
                "SELECT src, relation, source FROM edge_v WHERE dst=? AND relation IN ('affects','enables','sets_value')",
                ('param:%s/%s' % (ip['name'], name),)).fetchall()
            found = {}
            for row in sorted(rows, key=lambda r: rank.get(r['source'], 2)):
                param = row['src'].split('/', 1)[-1]
                if not row['src'].startswith('param:') or param == name or param.upper().endswith('_BOARD_INTERFACE'):
                    continue
                if param in found:
                    continue
                prow = self.db.execute('SELECT default_value, enum_json, format FROM param_v WHERE ip_id=? AND name=?',
                                       (ip['id'], param)).fetchone()
                if prow is None:
                    continue
                values = []
                try:
                    choices = json.loads(prow['enum_json']) if prow['enum_json'] else []
                except ValueError:
                    choices = []
                for choice in choices:
                    value = str(choice[0] if isinstance(choice, (list, tuple)) else choice)
                    if value != str(prow['default_value']) and value not in values:
                        values.append(value)
                if not values and str(prow['default_value']).lower() in ('true', 'false'):
                    values = ['false' if str(prow['default_value']).lower() == 'true' else 'true']
                if not values:
                    continue
                found[param] = dict(param='CONFIG.' + param, values=values[:3], source=row['source'],
                                    relation=row['relation'])
                if len(found) >= per_key:
                    break
            result['CONFIG.' + name] = list(found.values())
        return result

    def same_category(self, vlnv, support, limit=16):
        """Supported, visible IPs in the unsupported IP's catalog category with the same interfaces.

        A function can be served by an IP with a different name and wording: on Versal the AXI
        Protocol Converter is not supported and SmartConnect does the conversion (issue #674),
        yet the two share no name or description words -- only the taxonomy
        (/Interfaces_and_Interconnect/AXI_Infrastructure) and interface set (AXI4). Ranking that
        pool by words picked the wrong IP, so the pool is returned whole, for the agent to choose
        from by function; live creation still decides.
        """
        own = self.db.execute('SELECT taxonomy, interfaces FROM ip WHERE vlnv=?', (vlnv,)).fetchone()
        if not own or not own['taxonomy']:
            return []
        split = lambda text: {x for x in re.split(r'[\s,]+', (text or '').strip()) if x}
        wanted = split(own['interfaces'])
        pool = []
        for row in self.db.execute('SELECT vlnv, name, display_name, description, interfaces FROM ip '
                                   'WHERE visible=1 AND taxonomy=? ORDER BY name', (own['taxonomy'],)):
            if row['vlnv'] == vlnv or support.get(row['vlnv']) in (None, 'Hidden'):
                continue
            if split(row['interfaces']) != wanted:
                continue
            pool.append(dict(vlnv=row['vlnv'], name=row['name'], display_name=row['display_name'],
                             description=str(row['description'] or '')[:200]))
        return pool if len(pool) <= limit else []

    def alternatives(self, vlnv, support):
        own = self.db.execute('SELECT name, display_name, interfaces FROM ip WHERE vlnv=?', (vlnv,)).fetchone()
        name, display = own['name'], own['display_name'] or ''
        split = lambda text: {x for x in re.split(r'[\s,]+', (text or '').strip()) if x}
        own_ifaces = split(own['interfaces'])
        usable = lambda v: support.get(v) not in (None, 'Hidden') and v != vlnv
        matches = []
        for row in self.db.execute('SELECT vlnv, name, display_name FROM ip WHERE visible=1 ORDER BY vlnv'):
            if not usable(row['vlnv']):
                continue
            shorter = min(row['name'], name, key=len)
            if row['name'] != name and len(shorter) >= 5 and (row['name'].startswith(name) or name.startswith(row['name'])):
                matches.append(dict(vlnv=row['vlnv'], display_name=row['display_name'], match='name_family'))
            elif display and row['display_name'] == display:
                matches.append(dict(vlnv=row['vlnv'], display_name=row['display_name'], match='display_name'))
        if not matches and ip_query(display):
            for row in self.db.execute(
                    'SELECT i.vlnv, i.display_name, i.visible, i.interfaces FROM (SELECT rowid AS id, ' + IP_RANK + ' AS score '
                    'FROM ip_search WHERE ip_search MATCH ?) s JOIN ip i ON i.id = s.id ORDER BY s.score LIMIT 40',
                    (ip_query(display),)):
                # A word match alone is weak: never offer an IP whose interfaces are all of other
                # kinds (an AXI4-Stream clock converter for an AXI4 protocol converter, #674).
                theirs = split(row['interfaces'])
                if own_ifaces and theirs and not own_ifaces & theirs:
                    continue
                if row['visible'] and usable(row['vlnv']):
                    matches.append(dict(vlnv=row['vlnv'], display_name=row['display_name'], match='catalog_text'))
                    break
        return matches[:3]

    def ip_candidates(self, query, part, limit):
        results, seen = [], set()
        # An exact identifier is resolved deterministically before any ranking.
        exact = self.db.execute('SELECT * FROM ip WHERE lower(vlnv)=lower(?) OR lower(name)=lower(?) '
                                'OR lower(display_name)=lower(?)', (query, query, query)).fetchall()
        rows = list(exact)
        expression = ip_query(query)
        support = self.part_support(part)
        if expression:
            ranked = self.db.execute(
                'SELECT i.*, s.score FROM (SELECT rowid AS id, ' + IP_RANK + ' AS score FROM ip_search '
                'WHERE ip_search MATCH ?) s JOIN ip i ON i.id = s.id ORDER BY i.visible = 0, s.score LIMIT 24',
                (expression,)).fetchall()
            # Part support is reported on each candidate but never reorders them: a request may
            # name an IP for another family on purpose and swap the part (an UltraScale+ CMAC,
            # a clocking wizard on a VC part), and the live availability check decides.
            rows += ranked
        for row in rows:
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            families = str(row['families'] or '').split()
            interfaces = str(row['interfaces'] or '').split()
            description = str(row['description'] or '')
            result = dict(vlnv=row['vlnv'], label=str(row['display_name'] or row['name']),
                          description=description[:240], description_truncated=len(description) > 240,
                          compatibility='not_validated_for_requested_part', requested_part=part,
                          catalog_families=families[:8], interface_names=interfaces[:8],
                          hidden_subcore=not row['visible'], requires_live_validation=True)
            if support is not None:
                status = support.get(row['vlnv'])
                result['catalog_part_supported'] = bool(status) and status != 'Hidden'
                result['catalog_part_status'] = status or 'not_supported_on_requested_part'
                result['compatibility'] = 'release_support_list_not_live_validated'
            elif row['part_supported'] is not None:
                result['catalog_part_supported'] = bool(row['part_supported'])
            results.append(result)
            if len(results) >= limit:
                break
        return results

    def parameter_candidates(self, query, vlnv, part, limit):
        ip = self.ip_row(vlnv)
        expression = param_query(query, self.vocab())
        if not expression:
            return []
        rows = self.db.execute(
            'SELECT p.*, s.score FROM (SELECT rowid AS doc, ' + PARAM_RANK + ' AS score FROM param_search '
            'WHERE param_search MATCH ?) s JOIN param_v p ON p.id = s.doc WHERE p.ip_id = ? '
            'ORDER BY s.score LIMIT 32', (expression, ip['id'])).fetchall()
        # Multi-word matches in the identifier itself separate opposite controls (OUT vs output,
        # FREQ vs frequency) that bm25 alone scores alike.
        terms = Snapshot.identifier_terms(query)
        scored = []
        for rank, row in enumerate(rows):
            own = Snapshot.identifier_terms(row['name'] + ' ' + str(row['display_name'] or ''))
            matched = sorted(t for t in terms if any(t.startswith(o) or o.startswith(t) for o in own))
            scored.append((-len(matched) if len(matched) >= 2 else 0, rank, row, matched))
        results = []
        for _, _, row, matched in sorted(scored, key=lambda x: (x[0], x[1])):
            record = self.parameter_record(row, part, ip['id'])
            if len(matched) >= 2:
                record['matched_identifier_terms'] = matched
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def request(self, request):
        if not isinstance(request, dict):
            raise ValueError('Database request must be an object')
        allowed = {'op', 'query', 'build', 'vlnv', 'part', 'mode', 'limit', 'keys', 'vlnvs'}
        if set(request) - allowed:
            raise ValueError('Unknown database request fields')
        op = request.get('op')
        if op == 'info':
            return dict(status='DB:INFO', schema_version=self.meta.get('schema_version'),
                        path=str(self.path), ip_count=self.db.execute('SELECT count(*) FROM ip').fetchone()[0],
                        requires_live_validation=True, **self.stamp(request.get('build')))
        if op == 'lookup':
            return self.lookup(request['vlnv'], request['keys'], request.get('build'), request.get('part'))
        if op == 'catalog':
            return self.catalog(request['vlnv'], request.get('build'), request.get('part'))
        if op == 'support_table':
            table = self.support_table(request.get('part'))
            if table is None:
                raise ValueError('Part not recorded in the release database')
            return dict(status='DB:SUPPORT_TABLE', part=request.get('part'), table=table,
                        scope='release_support_list_not_live_validated', **self.stamp(request.get('build')))
        if op == 'influences':
            return dict(status='DB:INFLUENCES', influences=self.influences(request.get('vlnv'), request.get('keys')),
                        scope='recorded_links_not_live_proof', **self.stamp(request.get('build')))
        if op == 'part_support':
            return dict(status='DB:PART_SUPPORT', rows=self.part_support_rows(request.get('vlnvs'), request.get('part')),
                        scope='release_support_list_not_live_validated', **self.stamp(request.get('build')))
        if op == 'field_hints':
            return self.field_hints(request['keys'], request.get('build'), request.get('part'),
                                    request.get('vlnv'))
        if op not in ['ips', 'parameters'] or (op == 'parameters' and not request.get('vlnv')):
            raise ValueError('Use ips, parameters or lookup')
        if op == 'ips' and request.get('vlnv'):
            raise ValueError('IP discovery must not require a VLNV filter')
        return self.search(request['query'], request.get('build'), request.get('vlnv'),
                           request.get('part'), request.get('mode', 'hybrid'), request.get('limit'))
