# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generic versioned IP metadata database and bounded offline hybrid retrieval."""
import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

SCHEMA = 1


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local(tag):
    return tag.rsplit('}', 1)[-1]


def clean(text):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', text or '')).split())


def child(node, name):
    return next((x for x in node if local(x.tag) == name), None)


def value(node, name):
    c = child(node, name)
    return c.text or '' if c is not None else ''


def attribute(node, name):
    return next((v for k, v in node.attrib.items() if local(k) == name), '') if node is not None else ''


def catalog_rows(path):
    rows, native, missing, header = [], {}, [], None
    for line in Path(path).read_text().splitlines():
        fields = line.split('\t')
        kind = fields.pop(0)
        if kind == 'IP':
            vlnv, xml = [base64.b64decode(x).decode() for x in fields[:2]]
            rows.append({'vlnv': vlnv, 'xml': xml, 'native_status': int(fields[2]),
                         'native_record': base64.b64decode(fields[3]).decode()})
        else:
            fields = [base64.b64decode(x).decode() for x in fields]
            if kind == 'HEADER':
                header = {'version_full': fields[0], 'part': fields[1]}
            elif kind == 'MISSING':
                missing.extend(fields)
            elif kind == 'PARAM':
                vlnv, name, label, typ, default, help_text, bounds, block = fields
                native.setdefault(vlnv, {})[name] = dict(label=label, type=typ, default=default,
                    help=clean(help_text), range=bounds, block=block)
            else:
                raise ValueError('Unknown catalog record')
    if not header:
        raise ValueError('Catalog snapshot has no version header')
    header['build'] = re.search(r'SW Build (\d+)', header['version_full']).group(1)
    return header, rows, native, missing


def xml_metadata(path):
    root = ET.parse(path).getroot()
    choices = {}
    container = child(root, 'choices')
    for item in container if container is not None else []:
        choices[value(item, 'name')] = [{'value': x.text or '', 'label': attribute(x, 'text')}
                                      for x in item if local(x.tag) == 'enumeration']
    parameters = []
    container = child(root, 'parameters')
    for item in container if container is not None else []:
        v = child(item, 'value')
        lo, hi = attribute(v, 'minimum'), attribute(v, 'maximum')
        enums = choices.get(attribute(v, 'choiceRef'), [])
        parameters.append(dict(name=value(item, 'name'), label=value(item, 'displayName'),
            help=clean(value(item, 'description')), default=v.text or '' if v is not None else '',
            type=attribute(v, 'format'), choices=enums,
            range='RANGE_NUM:' + lo + ':' + hi if lo and hi else 'RANGE_NONE'))
    extensions = child(root, 'vendorExtensions')
    display = next((x.text or '' for x in extensions.iter() if local(x.tag) == 'displayName'), '') if extensions is not None else ''
    families = [{'family': x.text or '', 'lifecycle': attribute(x, 'lifeCycle')}
                for x in extensions.iter() if local(x.tag) == 'family'] if extensions is not None else []
    buses = child(root, 'busInterfaces')
    interfaces = [{'name': value(x, 'name'), 'bus_type': dict(child(x, 'busType').attrib)
                   if child(x, 'busType') is not None else {}} for x in buses] if buses is not None else []
    return dict(name=value(root, 'name'), label=display or value(root, 'name'),
                description=clean(value(root, 'description')), families=families,
                interfaces=interfaces, parameters=parameters)


class Embedder:
    def __init__(self, path):
        # Load an explicitly local snapshot; search never downloads models.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(4)
        self.model = SentenceTransformer(str(path), device='cpu', local_files_only=True)

    def encode(self, texts):
        return self.model.encode(texts, batch_size=32, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False).astype('float32')


def load_vector(db, dependencies):
    sys.path.insert(0, str(dependencies))
    import sqlite_vec
    db.enable_load_extension(True)
    try:
        sqlite_vec.load(db)
    finally:
        db.enable_load_extension(False)
    return db.execute('select vec_version()').fetchone()[0]


def build(config, catalog, captures=None):
    destination = Path(config['database']).resolve()
    if destination.is_relative_to('/home'):
        raise ValueError('Database artifacts must be outside /home')
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing snapshot')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.building.sqlite')
    if temporary.exists():
        raise ValueError('Unfinished build exists; use a fresh output path')
    header, rows, native, missing = catalog_rows(catalog)
    db = sqlite3.connect(temporary)
    vector_version = load_vector(db, config['dependencies'])
    db.executescript("""
      PRAGMA foreign_keys=ON;
      CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE sources(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL);
      CREATE TABLE ips(id INTEGER PRIMARY KEY, vlnv TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        label TEXT NOT NULL, description TEXT NOT NULL, families TEXT NOT NULL,
        interfaces TEXT NOT NULL, source_id INTEGER REFERENCES sources(id));
      CREATE TABLE parameters(id INTEGER PRIMARY KEY, ip_id INTEGER REFERENCES ips(id),
        name TEXT NOT NULL, label TEXT NOT NULL, UNIQUE(ip_id,name));
      CREATE TABLE evidence(id INTEGER PRIMARY KEY, parameter_id INTEGER REFERENCES parameters(id),
        source_id INTEGER REFERENCES sources(id), kind TEXT NOT NULL, scope TEXT NOT NULL,
        part TEXT, payload TEXT NOT NULL);
      CREATE TABLE documents(id INTEGER PRIMARY KEY, kind INTEGER NOT NULL, ip_id INTEGER REFERENCES ips(id),
        parameter_id INTEGER REFERENCES parameters(id), text TEXT NOT NULL);
      CREATE VIRTUAL TABLE search_fts USING fts5(text, content='documents', content_rowid='id', tokenize='unicode61');
      CREATE VIRTUAL TABLE search_vectors USING vec0(embedding float[384] distance_metric=cosine, ip_id integer, kind integer);
    """)
    def source(kind, path):
        return db.execute('INSERT INTO sources(kind,path,sha256) VALUES (?,?,?)',
                          (kind, str(path), digest(path))).lastrowid
    def evidence(pid, sid, kind, scope, part, data):
        # Snapshot ranges and raw native blocks are configuration-dependent.
        data = {k: v for k, v in data.items() if k not in ['range', 'block']}
        db.execute('INSERT INTO evidence(parameter_id,source_id,kind,scope,part,payload) VALUES (?,?,?,?,?,?)',
                   (pid, sid, kind, scope, part, json.dumps(data, ensure_ascii=False)))
    catalog_source = source('live_catalog_and_default_metadata', catalog)
    ids = {}
    for row in rows:
        metadata = xml_metadata(row['xml'])
        sid = source('packaged_ipxact', row['xml'])
        from static_gui import extract
        static = {}
        for gui_path in sorted((Path(row['xml']).parent / 'xgui').glob('*.tcl')):
            extracted = extract(gui_path)
            if extracted:
                gui_sid = source('packaged_static_gui_definition', gui_path)
                for name, data in extracted.items():
                    static.setdefault(name, []).append((gui_sid, data))
        ipid = db.execute('INSERT INTO ips(vlnv,name,label,description,families,interfaces,source_id) VALUES (?,?,?,?,?,?,?)',
            (row['vlnv'], metadata['name'], metadata['label'], metadata['description'],
             json.dumps(metadata['families']), json.dumps(metadata['interfaces']), sid)).lastrowid
        ids[row['vlnv']] = ipid
        packaged = {p['name']: p for p in metadata['parameters']}
        sampled = native.get(row['vlnv'], {})
        for name in sorted(set(packaged) | set(sampled)):
            label = packaged.get(name, {}).get('label') or sampled.get(name, {}).get('label') or name
            pid = db.execute('INSERT INTO parameters(ip_id,name,label) VALUES (?,?,?)', (ipid, name, label)).lastrowid
            if name in packaged:
                evidence(pid, sid, 'packaged', 'packaged_not_live', None, packaged[name])
            if name in sampled:
                evidence(pid, catalog_source, 'native', 'native_default_instance', header['part'], sampled[name])
            for gui_sid, data in static.get(name, []):
                evidence(pid, gui_sid, 'static_gui', 'packaged_gui_literal_not_live', None, data)
    if captures:
        sid = source('validated_gui_tooltip_capture', captures)
        for capture in json.loads(Path(captures).read_text()):
            ipid = ids[capture['vlnv']]
            for name, data in capture['parameters'].items():
                p = db.execute('SELECT id FROM parameters WHERE ip_id=? AND name=?', (ipid, name)).fetchone()
                if not p:
                    raise ValueError('GUI key not found in this catalog: ' + name)
                evidence(p[0], sid, 'gui', 'captured_gui_instance', capture['part'],
                         {'help': clean(data['tooltip']), 'getter': data['source'],
                          'capture_configuration': capture.get('configuration', 'unspecified')})
    # Index IP descriptions and interfaces separately from parameter documents.
    # Large IPs cannot crowd IP discovery with thousands of parameter hits.
    for ip in db.execute('SELECT * FROM ips').fetchall():
        ipid, vlnv, name, label, description, families, interfaces, sid = ip
        labels = [r[0] for r in db.execute('SELECT DISTINCT label FROM parameters WHERE ip_id=? ORDER BY name LIMIT 24', (ipid,))]
        text = ' '.join([name.replace('_', ' '), label, description[:700],
                        'Parameter labels:', ' '.join(labels)[:550],
                        'Interfaces:', ' '.join(x['name'] for x in json.loads(interfaces))[:160]])
        db.execute('INSERT INTO documents(kind,ip_id,text) VALUES (0,?,?)', (ipid, text))
    for pid, ipid, name, label in db.execute('SELECT * FROM parameters').fetchall():
        help_texts, labels = [], []
        for (raw,) in db.execute('SELECT payload FROM evidence WHERE parameter_id=?', (pid,)):
            data = json.loads(raw)
            if data.get('help') and data['help'] not in help_texts:
                help_texts.append(data['help'])
            labels.extend(data.get('gui_context', []))
            labels.extend(x.get('label', '') for x in data.get('choices', []))
        text = ' '.join([name.replace('_', ' '), label, *help_texts, *labels])
        # MiniLM truncates long text; keep it bounded and put purpose first.
        db.execute('INSERT INTO documents(kind,ip_id,parameter_id,text) VALUES (1,?,?,?)', (ipid, pid, text[:1800]))
    model = Path(config['model']).resolve()
    weights = model / 'model.safetensors'
    embedding = {'model_path': str(model), 'snapshot': model.name, 'weights_sha256': digest(weights),
                 'dimensions': 384, 'normalized': True, 'device': 'cpu', 'max_sequence_tokens': 256,
                 'document_text_limit': 1800, 'preprocessing': 'metadata-purpose-gui-v3'}
    manifest = dict(header, schema_version=SCHEMA, embedding=embedding, vector_extension=vector_version,
                    missing_requested_names=missing, created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    db.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in manifest.items()])
    db.execute("INSERT INTO search_fts(search_fts) VALUES ('rebuild')")
    db.commit()
    encoder = Embedder(model)
    records = db.execute('SELECT id,kind,ip_id,text FROM documents ORDER BY id').fetchall()
    for start in range(0, len(records), 128):
        batch = records[start:start+128]
        vectors = encoder.encode([x[3] for x in batch])
        db.executemany('INSERT INTO search_vectors(rowid,embedding,ip_id,kind) VALUES (?,?,?,?)',
                       [(record[0], vector.tobytes(), record[2], record[1]) for record, vector in zip(batch, vectors)])
        db.commit()
        print('Embedded', min(start+128, len(records)), '/', len(records), file=sys.stderr, flush=True)
    assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert db.execute('SELECT count(*) FROM search_vectors').fetchone()[0] == len(records)
    counts = {t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0]
              for t in ['ips', 'parameters', 'evidence', 'sources', 'documents']}
    db.close()
    temporary.replace(destination)
    return dict(database=str(destination), counts=counts, snapshot=manifest)


class Database:
    def __init__(self, config, encoder=None):
        self.config = config
        path = Path(config['database']).resolve()
        self.db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.vector_version = load_vector(self.db, config['dependencies'])
        self.meta = {r['key']: json.loads(r['value']) for r in self.db.execute('SELECT * FROM metadata')}
        if self.meta['schema_version'] != SCHEMA:
            raise ValueError('Unsupported database schema')
        if Path(config['model']).resolve() != Path(self.meta['embedding']['model_path']):
            raise ValueError('Embedding model identity mismatch')
        if self.vector_version != self.meta['vector_extension']:
            raise ValueError('Vector extension version mismatch')
        if digest(Path(config['model']) / 'model.safetensors') != self.meta['embedding']['weights_sha256']:
            raise ValueError('Embedding weights fingerprint mismatch')
        self.encoder = encoder

    def close(self):
        self.db.close()

    def check(self, build):
        if str(build) != self.meta['build']:
            raise ValueError('SNAPSHOT_MISMATCH: database build ' + self.meta['build'])

    def encode(self, query):
        if self.encoder is None:
            self.encoder = Embedder(self.config['model'])
        return self.encoder.encode([query])[0].tobytes()

    def parameter(self, pid, part=None):
        row = self.db.execute('SELECT p.*,i.vlnv FROM parameters p JOIN ips i ON i.id=p.ip_id WHERE p.id=?', (pid,)).fetchone()
        result = dict(key='CONFIG.' + row['name'], label=row['label'][:160], vlnv=row['vlnv'])
        records = []
        for e in self.db.execute('SELECT * FROM evidence WHERE parameter_id=?', (pid,)):
            record = dict(e);record['payload'] = json.loads(e['payload']);records.append(record)
        applicable = [e for e in records if e['kind'] != 'native' or e['part'] == part]
        native = next((e for e in applicable if e['kind'] == 'native'), None)
        packaged = next((e for e in records if e['kind'] == 'packaged'), None)
        help_record = next((e for kind in ['native', 'packaged', 'gui', 'static_gui'] for e in applicable
                            if e['kind'] == kind and e['payload'].get('help')), None)
        # GUI help can aid discovery across devices, but is tagged as a sample.
        if help_record is None:
            help_record = next((e for e in records if e['payload'].get('help')), None)
        primary = native or packaged
        if primary:
            data = primary['payload']
            for k in ['type', 'default']:
                full = str(data.get(k, ''))
                result[k] = full[:240]
                if len(full) > 240:
                    result[k + '_truncated'] = True
        result.update(range='RANGE_NONE', range_scope='requires_current_configuration',
                      choices_scope='packaged_labels_not_current_legal_values')
        choices = packaged['payload'].get('choices', []) if packaged else []
        result['choices'] = [{'value': str(x.get('value', ''))[:160], 'label': str(x.get('label', ''))[:160],
                              'truncated': len(str(x.get('value', ''))) > 160 or len(str(x.get('label', ''))) > 160}
                             for x in choices[:6]]
        result['choices_count'] = len(choices)
        result['choices_more'] = len(choices) > 6
        result['choices_scope'] = 'packaged_labels_not_current_legal_values'
        text = help_record['payload']['help'] if help_record else ''
        result.update(help=text[:240], help_truncated=len(text) > 240,
                      help_source=help_record['kind'] if help_record else 'unavailable',
                      help_scope=help_record['scope'] if help_record else 'unavailable',
                      help_part=help_record['part'] if help_record else None,
                      evidence_ids=[e['id'] for e in records],
                      bd_write_mapping_verified=False, requires_live_validation=True)
        contexts = [e['payload']['gui_context'] for e in records if e['payload'].get('gui_context')]
        if contexts:
            result['gui_context'] = contexts[0][:8]
            result['gui_context_scope'] = 'packaged_gui_literal_not_live'
        return result

    def lookup(self, vlnv, keys, build, part=None):
        self.check(build)
        if not 1 <= len(keys) <= 4:
            raise ValueError('Select 1..4 exact CONFIG keys')
        ip = self.db.execute('SELECT id FROM ips WHERE vlnv=?', (vlnv,)).fetchone()
        if not ip:
            raise ValueError('VLNV absent from snapshot')
        found, missing = [], []
        for key in keys:
            if not re.fullmatch(r'CONFIG\.[A-Za-z0-9_]+', key):
                raise ValueError('Exact CONFIG key required')
            p = self.db.execute('SELECT id FROM parameters WHERE ip_id=? AND name=?', (ip['id'], key[7:])).fetchone()
            if p:
                found.append(self.parameter(p['id'], part))
            else:
                missing.append(key)
        return dict(status='DB:LOOKUP', build=self.meta['build'], parameters=found, missing=missing)

    def catalog(self, vlnv, build, part=None):
        """Complete small catalogs bypass ranked retrieval, within a fixed budget."""
        self.check(build)
        rows = self.db.execute('SELECT p.id FROM parameters p JOIN ips i ON i.id=p.ip_id WHERE i.vlnv=? ORDER BY p.name', (vlnv,)).fetchall()
        if not rows:
            raise ValueError('VLNV absent from snapshot')
        if len(rows) > 24:
            return dict(status='DB:CATALOG_TOO_LARGE', count=len(rows), candidates=[])
        candidates = [self.parameter(r['id'], part) for r in rows]
        return dict(status='DB:CATALOG', count=len(rows), candidates=candidates)

    def field_hints(self, names, build, part=None):
        """Exact-name help across source definitions, never a parent write map."""
        self.check(build)
        if not isinstance(names, list) or not 1 <= len(names) <= 16 or any(
                not isinstance(n, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', n) for n in names):
            raise ValueError('Select 1..16 exact native field names')
        result = {}
        for name in dict.fromkeys(names):
            ids = self.db.execute('SELECT id FROM parameters WHERE name=? ORDER BY id', (name,)).fetchall()
            records = [self.parameter(row['id'], part) for row in ids[:6]]
            if not records:
                continue
            hint = dict(source_vlnvs=[r['vlnv'] for r in records],
                        hint_scope='exact_name_in_other_definitions_not_parent_binding',
                        help='; '.join(dict.fromkeys(r.get('help', '') for r in records if r.get('help')))[:240],
                        labels=list(dict.fromkeys(r.get('label', '') for r in records if r.get('label')))[:3])
            for key in ['type', 'choices', 'choices_more']:
                values = [r.get(key) for r in records]
                if all(v == values[0] for v in values):
                    hint[key] = values[0]
            result[name] = hint
        return dict(status='DB:FIELD_HINTS', fields=result)

    @staticmethod
    def identifier_terms(text):
        # Units express purpose even when the prompt omits "frequency".
        # Split common port abbreviations before comparing identifier words.
        text = re.sub(r'(?<![A-Za-z0-9])CLK(OUT|IN)(\d*)(?![A-Za-z0-9])',
                      lambda m: 'clock ' + ('output' if m[1].lower() == 'out' else 'input'),
                      text, flags=re.I)
        # "MHz" reads as a camelCase compound to the splits below, which tear
        # the unit into "M"+"Hz" and lose it; a property spells it MHZ. Settle
        # on the property's spelling before anything is split.
        text = re.sub(r'(?<![A-Z])([KMG]?[Hh][Zz])(?![A-Za-z0-9])',
                      lambda m: m[1].upper(), text)
        # A unit abbreviation glued onto its quantity (…_FREQMHZ) has no case
        # transition for the split below to find, so "mhz" below never fires
        # and a decoy property with a separately-spelled "FREQ" wins instead.
        # PCW_FPGA0_PERIPHERAL_FREQMHZ and PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ
        # (the real Zynq-7000/UltraScale+ PL clock properties) are exactly this.
        text = re.sub(r'(?<![A-Za-z0-9])([A-Za-z]{2,}?)(MHZ|KHZ|GHZ|HZ)(?![A-Za-z0-9])',
                      lambda m: m[1] + ' ' + m[2], text, flags=re.I)
        text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
        text = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', text)
        aliases = {'clk': 'clock', 'freq': 'frequency', 'frequencies': 'frequency',
                   'out': 'output', 'in': 'input'}
        # Every unit of frequency stands for the measure, so a request saying
        # "150 MHz" reaches a property named ..._FREQ as readily as one named
        # ..._FREQMHZ. The unit itself is dropped rather than kept beside the
        # measure: these identifier terms feed recall, and the ranker in
        # native_metadata is what tells two frequency properties apart.
        units = {'hz': 'frequency', 'khz': 'frequency', 'mhz': 'frequency', 'ghz': 'frequency'}
        words = []
        for token in re.findall(r'[A-Za-z]+', text):
            token = token.lower()
            if token in units:
                words.append(units[token])
            else:
                words.append(aliases.get(token, token))
        return {t for t in words if len(t) >= 3}

    def search(self, query, build, vlnv=None, part=None, mode='hybrid', limit=None):
        self.check(build)
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError('Nonempty query of at most 600 characters required')
        if mode not in ['hybrid', 'semantic', 'lexical']:
            raise ValueError('Unknown search mode')
        maximum = 4 if vlnv else 3
        limit = maximum if limit is None else limit
        if not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise ValueError('Result limit outside bounded budget')
        ipid = None
        if vlnv:
            ip = self.db.execute('SELECT id FROM ips WHERE vlnv=?', (vlnv,)).fetchone()
            if not ip:
                raise ValueError('VLNV absent from snapshot')
            ipid = ip['id']
        ranks, similarity, identifier_matches = {}, {}, {}
        if mode != 'semantic':
            terms = re.findall(r'[A-Za-z0-9_]+', query)[:32]
            expression = ' OR '.join('"' + x + '"' for x in terms)
            if expression:
                sql = 'SELECT d.id FROM search_fts JOIN documents d ON d.id=search_fts.rowid WHERE search_fts MATCH ?'
                args = [expression]
                if ipid:
                    sql += ' AND d.ip_id=? AND d.kind=1';args.append(ipid)
                else:
                    sql += ' AND d.kind=0'
                rows = self.db.execute(sql + ' ORDER BY bm25(search_fts) LIMIT 64', args).fetchall()
                for rank, row in enumerate(rows, 1):
                    ranks[row['id']] = ranks.get(row['id'], 0) + 1 / (60 + rank)
        if mode != 'lexical':
            sql = 'SELECT rowid,distance FROM search_vectors WHERE embedding MATCH ? AND k=?'
            args = [self.encode(query), 64]
            if ipid:
                sql += ' AND ip_id=? AND kind=1';args.append(ipid)
            else:
                sql += ' AND kind=0'
            for rank, row in enumerate(self.db.execute(sql, args), 1):
                ranks[row['rowid']] = ranks.get(row['rowid'], 0) + 1 / (60 + rank)
                similarity[row['rowid']] = 1 - row['distance']
        # Semantic similarity can conflate opposite interfaces. Preserve
        # multi-word matches in native identifiers/labels (OUT vs output, FREQ
        # vs frequency) without an IP-specific alias table or a new index.
        if ipid and mode != 'semantic':
            query_terms = self.identifier_terms(query)
            sql = 'SELECT d.id,p.name,p.label FROM documents d JOIN parameters p ON p.id=d.parameter_id WHERE d.ip_id=? AND d.kind=1'
            for row in self.db.execute(sql, (ipid,)):
                terms = self.identifier_terms(row['name'] + ' ' + row['label'])
                matched = sorted(q for q in query_terms if any(q.startswith(t) or t.startswith(q) for t in terms))
                if len(matched) >= 2:
                    identifier_matches[row['id']] = matched
                    ranks.setdefault(row['id'], 0)
        # Exact identifiers are resolved deterministically before similarity.
        exact = self.db.execute('SELECT id FROM ips WHERE lower(vlnv)=lower(?) OR lower(name)=lower(?) OR lower(label)=lower(?)', (query, query, query)).fetchall() if not vlnv else []
        ranked = sorted(ranks, key=lambda x: (-len(identifier_matches.get(x, [])), -ranks[x], x))
        results, seen = [], set()
        if not vlnv:
            for row in exact:
                ranked.insert(0, self.db.execute('SELECT id FROM documents WHERE ip_id=? AND kind=0', (row['id'],)).fetchone()['id'])
        for docid in ranked:
            doc = self.db.execute('SELECT * FROM documents WHERE id=?', (docid,)).fetchone()
            identity = doc['parameter_id'] if vlnv else doc['ip_id']
            if identity in seen:
                continue
            seen.add(identity)
            if vlnv:
                result = self.parameter(identity, part)
            else:
                ip = self.db.execute('SELECT * FROM ips WHERE id=?', (identity,)).fetchone()
                result = dict(vlnv=ip['vlnv'], label=ip['label'], description=ip['description'][:240],
                    description_truncated=len(ip['description']) > 240,
                    compatibility='not_validated_for_requested_part', requested_part=part,
                    catalog_families=json.loads(ip['families'])[:8],
                    interface_names=[x['name'] for x in json.loads(ip['interfaces'])][:8],
                    source_id=ip['source_id'], matched_parameter_id=doc['parameter_id'])
            result['rank_score'] = round(ranks.get(docid, 0), 6)
            if docid in identifier_matches:
                result['matched_identifier_terms'] = identifier_matches[docid]
            if docid in similarity:
                result['semantic_similarity'] = round(similarity[docid], 4)
            results.append(result)
            if len(results) >= limit:
                break
        # A vector's values do not establish its enablement. Expose a bounded
        # Boolean-vector neighbour from the same source identifier family.
        # This is context for the model, not dependency or write-legality proof.
        related = []
        if ipid:
            known = {r['key'] for r in results}
            all_rows = self.db.execute('SELECT id,name FROM parameters WHERE ip_id=?', (ipid,)).fetchall()
            for parent in results[:2]:
                if ',' not in parent.get('default', '') or parent.get('default_truncated'):
                    continue
                family = next((t for t in parent['key'][7:].split('_') if len(t) > 1), '')
                for row in all_rows:
                    child_family = next((t for t in row['name'].split('_') if len(t) > 1), '')
                    if not family or child_family != family or 'CONFIG.'+row['name'] in known:
                        continue
                    candidate = self.parameter(row['id'], part)
                    purpose = self.identifier_terms(candidate['key'] + ' ' + candidate.get('label', ''))
                    if not any(t.startswith(('enable', 'used', 'active', 'present')) for t in purpose):
                        continue
                    defaults = candidate.get('default', '').lower().split(',')
                    if len(defaults) > 1 and not candidate.get('default_truncated') and all(v in ['true', 'false', '0', '1'] for v in defaults):
                        candidate.update(related_to=parent['key'], relation_scope='shared_identifier_family_not_dependency_proof')
                        related.append(candidate);known.add(candidate['key'])
                        break
                if related:
                    break
            if related:
                results = (results[:1] + related + results[1:])[:limit]
        return dict(status='DB:PARAMETERS' if vlnv else 'DB:IPS', build=self.meta['build'], mode=mode,
                    query=query, candidates=results, candidate_only=True, requires_live_validation=True)

    def request(self, request):
        if not isinstance(request, dict):
            raise ValueError('Database request must be an object')
        allowed = {'op', 'query', 'build', 'vlnv', 'part', 'mode', 'limit', 'keys'}
        if set(request) - allowed:
            raise ValueError('Unknown database request fields')
        op = request.get('op')
        if op == 'info':
            return dict(status='DB:INFO', build=self.meta['build'], version_full=self.meta['version_full'],
                        schema_version=SCHEMA, vector_extension=self.vector_version,
                        ip_count=self.db.execute('SELECT count(*) FROM ips').fetchone()[0],
                        requires_live_validation=True)
        if op == 'lookup':
            return self.lookup(request['vlnv'], request['keys'], request['build'], request.get('part'))
        if op == 'catalog':
            return self.catalog(request['vlnv'], request['build'], request.get('part'))
        if op == 'field_hints':
            return self.field_hints(request['keys'], request['build'], request.get('part'))
        if op not in ['ips', 'parameters'] or (op == 'parameters' and not request.get('vlnv')):
            raise ValueError('Use ips, parameters or lookup')
        if op == 'ips' and request.get('vlnv'):
            raise ValueError('IP discovery must not require a VLNV filter')
        return self.search(request['query'], request['build'], request.get('vlnv'), request.get('part'),
                           request.get('mode', 'hybrid'), request.get('limit'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--catalog', required=True)
    builder.add_argument('--captures')
    request = commands.add_parser('query')
    request.add_argument('request_json')
    commands.add_parser('check')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if args.command == 'build':
        result = build(config, args.catalog, args.captures)
    else:
        database = Database(config)
        try:
            result = database.request(json.loads(args.request_json)) if args.command == 'query' else {
                'integrity': database.db.execute('PRAGMA integrity_check').fetchone()[0],
                'metadata': database.meta,
                'counts': {t: database.db.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                           for t in ['ips', 'parameters', 'evidence', 'documents', 'search_vectors']}}
        finally:
            database.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
