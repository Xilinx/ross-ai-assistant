#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rank the running release's Configurable Example Designs (CEDs) against a design request.

usage: reference_search.py --index CED_INDEX.json --out REFERENCE_SEARCH.json
                           [--board BOARD_PART] [--part PART] [--text REQUEST] [--spec design_spec.json]
                           [--roles binding_snapshot.json]

CED_INDEX.json is written by ipiasm::reference_search from Vivado's own example-design objects
(get_example_designs): name, display name, description, SUPPORTED_BOARDS, SUPPORTED_PARTS, the
CONFIG options and the repository directory. This script adds what the CED's Tcl creates (the
catalog IPs it names) and its option value lists, then scores each CED:

  supported   the board (or, without a board, the part) is in the CED's support lists
  coverage    share of the requested functions (catalog IPs inferred from the request and the
              DesignSpec, or the bound roles when given) that the CED creates
  text        word overlap between the request and the CED's name, description and keywords

and writes the ranking with a verdict per CED: STRONG (supported, coverage >= 0.8), PARTIAL
(supported, coverage >= 0.4), WEAK. Requests that name an IP's example design are reported as
IP_EXAMPLE with the catalog IP, for ipiasm::example_reference. Prints one line per result.
"""
import argparse
import importlib.util
import json
import os
import re
import sys

# Request words -> catalog IP names a design with that function contains.
FUNCTIONS = [
    (r'\bi2c\b|\biic\b', ['axi_iic']),
    (r'\bgpio\b', ['axi_gpio']),
    (r'\buart\b|\bserial console\b', ['axi_uartlite', 'axi_uart16550']),
    (r'\bq?spi\b', ['axi_quad_spi']),
    (r'\btimers?\b', ['axi_timer']),
    (r'\binterrupt controller\b|\bintc\b', ['axi_intc']),
    (r'microblaze[ -]?v\b|risc-?v', ['microblaze_riscv']),
    (r'microblaze(?![ -]?v\b)', ['microblaze']),
    # Versal reaches DDR through the NoC's integrated controller, 7-series through MIG.
    (r'\bddr4?\b', ['ddr4', 'axi_noc', 'noc_mc_ddr4', 'mig_7series']),
    (r'\blpddr\d?\b', ['lpddrmc']),
    (r'\bethernet\b', ['axi_ethernet', 'axi_ethernetlite']),
    (r'\bxdma\b', ['xdma']),
    (r'\bqdma\b', ['qdma']),
    (r'\bpcie\b|\bpci express\b', ['pcie4_uscale_plus', 'xdma', 'qdma', 'versal_cips']),
    (r'\bcpm\d?\b', ['versal_cips']),
    (r'\baxi dma\b|\bdma\b', ['axi_dma']),
    (r'\bcsi-?2?\b|\bmipi camera\b|\bcamera\b', ['mipi_csi2_rx_subsystem']),
    (r'\bdsi\b', ['mipi_dsi_tx_subsystem']),
    (r'\bhdmi\b', ['v_hdmi_txss1', 'v_hdmi_rxss1', 'v_hdmi_phy1']),
    (r'\bframe ?buffer\b', ['v_frmbuf_wr', 'v_frmbuf_rd']),
    (r'\bbram\b|\bblock ram\b', ['axi_bram_ctrl', 'blk_mem_gen', 'emb_mem_gen']),
    (r'\bvio\b', ['axis_vio']),
    (r'\bila\b', ['axis_ila']),
    (r'\bzynq ?ultrascale\+?|\bmpsoc\b|\bkria\b|\bk26\b|\bk24\b', ['zynq_ultra_ps_e']),
    (r'\bzynq[- ]?7000\b|\bzynq-?7\b', ['processing_system7']),
    (r'\bversal\b|\bcips\b', ['versal_cips']),
    (r'\bnoc\b', ['axi_noc']),
    (r'\bai ?engine\b|\baie\b', ['ai_engine']),
    (r'\bemc\b|\bflash\b', ['axi_emc']),
    (r'\bibert\b', ['ibert']),
    (r'\btmr\b|\btriple modular\b', ['tmr_manager']),
    (r'\bclock(ing)? wizard\b|\bmmcm\b', ['clk_wiz', 'clk_wizard']),
]
# Present in almost every processor design; neither evidence of a match nor an extra.
INFRA = {'proc_sys_reset', 'smartconnect', 'axi_interconnect', 'lmb_v10', 'lmb_bram_if_cntlr', 'blk_mem_gen',
         'mdm', 'mdm_riscv', 'xlconcat', 'xlconstant', 'xlslice', 'util_vector_logic', 'util_ds_buf',
         'clk_wiz', 'clk_wizard', 'axi_intc', 'ilconcat', 'ilconstant', 'ilslice', 'axi_noc', 'pmcbridge'}
# Example-design requests: "IP example", "example design of <ip>".
EXAMPLE_REQUEST = re.compile(r'\b(ip )?example design\b|\bopen (ip )?example\b|\bexample project\b', re.I)
STOP = set('a an and the with for to of in on by from as is are be use using its it this that board design '
           'create build make vivado block ipi generate xsa evaluation'.split())


def words(text):
    return {w for w in re.findall(r'[a-z0-9][a-z0-9+\-]*', (text or '').lower()) if w not in STOP and len(w) > 1}


def wanted_ips(text):
    """Catalog IPs the request's words call for, each function as one group of alternatives."""
    groups = []
    low = (text or '').lower()
    for pattern, ips in FUNCTIONS:
        if re.search(pattern, low):
            groups.append(ips)
    return groups


def ced_ips(repo):
    """Catalog IP names created by the CED's Tcl (all board branches, so an upper bound)."""
    ips, rules = set(), set()
    for root, _, files in os.walk(repo or ''):
        for name in files:
            if not name.endswith('.tcl'):
                continue
            try:
                text = open(os.path.join(root, name), encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            ips |= set(re.findall(r'xilinx\.com:ip:([A-Za-z0-9_]+)', text))
            rules |= set(re.findall(r'xilinx\.com:bd_rule:([A-Za-z0-9_]+)', text))
    return sorted(ips), sorted(rules)


def ced_options(repo):
    """Option name -> list of values, from the CED's addOptions value_list entries."""
    out = {}
    path = os.path.join(repo or '', 'init.tcl')
    try:
        text = open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return out
    text = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
    for m in re.finditer(r'name\s+"?([\w.]+)"?\s+type\s+"?\w+"?\s+value\s+"?([^"\s]*)"?\s+value_list\s+\{(.*?)\}\s+enabled', text, re.S):
        values = [v.split()[0] for v in re.findall(r'"([^"]+)"', m.group(3))] or m.group(3).split()
        out.setdefault(m.group(1), [])
        for v in values:
            if v not in out[m.group(1)]:
                out[m.group(1)].append(v)
    return out


def keywords(repo):
    try:
        item = json.load(open(os.path.join(repo or '', 'xitem.json')))['config']['items'][0]['infra']
        return ' '.join(item.get('search-keywords', [])) + ' ' + item.get('category', '')
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return ''


def board_name(board_part):
    # xilinx.com:scu35:part0:2.1 -> scu35 ; xilinx.com:scu35:2.1 -> scu35
    parts = (board_part or '').split(':')
    return parts[1].lower() if len(parts) >= 2 else (board_part or '').lower()


def supported(ced, board, part):
    boards = {board_name(b) for b in ced.get('boards', '').split()}
    parts = {p.lower() for p in ced.get('parts', '').split()}
    if board:
        if board_name(board) in boards:
            return True, f'board {board_name(board)} listed'
        if not boards and part and part.lower() in parts:
            return True, f'part {part} listed (part-based CED)'
        return False, f'board {board_name(board)} not in {len(boards)} supported boards'
    if part and part.lower() in parts:
        return True, f'part {part} listed'
    if not part and not board:
        return True, 'no board or part given'
    if boards and not parts:
        # Board-based CED and no board chosen yet: support depends on the board.
        return None, f'board-based CED ({len(boards)} boards); no board chosen'
    return False, 'part not listed'


def spec_text(path):
    try:
        spec = json.load(open(path))
    except (OSError, ValueError):
        return ''
    out = [spec.get('name', ''), spec.get('description', '')]
    for b in spec.get('blocks', []):
        out += [b.get('name', ''), b.get('description', ''), b.get('workload_type', '')]
        out += [i.get('protocol', '') for i in b.get('interfaces', [])]
    return ' '.join(str(x) for x in out)


def role_ips(path):
    """Catalog IPs bound to the DesignSpec's roles by scratch_bind (binding_snapshot.json)."""
    try:
        snap = json.load(open(path))
    except (OSError, ValueError):
        return []
    found = set(re.findall(r'xilinx\.com:ip:([A-Za-z0-9_]+)', json.dumps(snap)))
    return [[ip] for ip in sorted(found - INFRA)]


def ced_skill_index(index):
    """The vivado-ced-configurator skill's Index over the release's own CED store, or None.

    That skill owns CED search: BM25 over names, descriptions, keywords and option text, with
    MicroBlaze V and classic MicroBlaze kept apart, and static addOptions parsing (choices,
    defaults, dynamic options). The store is the one Vivado reports for each CED (REPO_DIRECTORY
    .../XilinxCEDStore/ced/...), so search and Vivado's own support lists describe the same
    release. Absent the sibling skill, the local scoring below is used.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.normpath(os.path.join(here, '..', '..', 'vivado-ced-configurator', 'scripts', 'ced_index.py'))
    roots = {r.split('/ced/', 1)[0] for r in (c.get('repo', '') for c in index.get('ceds', [])) if '/ced/' in r}
    branch = (index.get('build', '') or '').split('/', 1)[0]
    if not os.path.isfile(script) or len(roots) != 1 or not branch:
        return None
    try:
        spec = importlib.util.spec_from_file_location('ced_index', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Index(roots.pop(), branch)
    except Exception:  # the sibling skill is optional; any failure falls back to local scoring
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--index', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--board', default='')
    ap.add_argument('--part', default='')
    ap.add_argument('--text', default='')
    ap.add_argument('--spec', default='')
    ap.add_argument('--roles', default='')
    a = ap.parse_args()
    index = json.load(open(a.index))
    request = a.text + ' ' + (spec_text(a.spec) if a.spec else '')
    groups = role_ips(a.roles) if a.roles else []
    groups = groups or wanted_ips(request)
    req_words = words(request)
    skill = ced_skill_index(index)
    bm25 = {}
    if skill is not None:
        hits = skill.search(request, limit=len(skill.entries), exclude=[a.board, a.part])
        top = max((score for score, _ in hits), default=0) or 1
        bm25 = {entry['name']: score / top for score, entry in hits}
    results = []
    for ced in index.get('ceds', []):
        ips, rules = ced_ips(ced.get('repo'))
        ok, why = supported(ced, a.board, a.part)
        ipset = set(ips) | {r for r in rules}
        hit = [g for g in groups if ipset & set(g)]
        coverage = len(hit) / len(groups) if groups else 0.0
        ced_words = words(' '.join([ced.get('name', ''), ced.get('display', ''), ced.get('description', ''),
                                    keywords(ced.get('repo'))]))
        text = len(req_words & ced_words) / max(1, min(len(req_words), 12))
        entry = None
        if skill is not None:
            short = (ced.get('name') or '').split(':')
            entry = skill.by_vlnv(ced.get('name', '')) or (skill.by_name(short[2]) if len(short) > 2 else None)
            text = bm25.get(entry['name'], 0.0) if entry else 0.0
        wanted = {ip for g in groups for ip in g}
        extras = sorted(set(ips) - wanted - INFRA)
        factor = 1.0 if ok else (0.6 if ok is None else 0.3)
        score = round((0.7 * coverage + 0.3 * min(1.0, text)) * factor, 3)
        verdict = 'STRONG' if ok and coverage >= 0.8 else ('PARTIAL' if ok is not False and coverage >= 0.4 else 'WEAK')
        results.append({
            'ced': ced.get('name'), 'display': ced.get('display'), 'verdict': verdict, 'score': score,
            'supported': ok, 'support_reason': why, 'coverage': round(coverage, 2),
            'covered': [g[0] if len(g) == 1 else '|'.join(g) for g in hit],
            'missing': [g[0] if len(g) == 1 else '|'.join(g) for g in groups if g not in hit],
            'possible_extras': extras,
            'options': ({o['name']: [c['value'] for c in o['choices']] or [o['default']] for o in entry['options']}
                        if entry else (ced_options(ced.get('repo')) or ced.get('config', {}))),
            'option_defaults': {o['name']: o['default'] for o in entry['options']} if entry else {},
            'search': 'ced-skill' if entry else 'local',
            'automation_rules': rules, 'repo': ced.get('repo'),
        })
    results.sort(key=lambda r: (-r['score'], r['ced'] or ''))
    example = None
    if EXAMPLE_REQUEST.search(a.text or ''):
        named = [g[0] for g in wanted_ips(a.text) if g]
        example = {'request': 'IP_EXAMPLE', 'ips': named,
                   'next': 'ipiasm::example_reference <catalog vlnv> <spec dir> for the IP whose example is asked for'}
    record = {'schema': 'reference_search/1', 'board': a.board, 'part': a.part, 'request': a.text,
              'functions': ['|'.join(g) for g in groups], 'release': index.get('build', ''),
              'ced_count': len(results), 'ip_example': example, 'ranking': results[:10]}
    with open(a.out, 'w') as handle:
        json.dump(record, handle, indent=1)
    best = results[0] if results else None
    if example:
        print(f'IP_EXAMPLE ips={",".join(example["ips"]) or "?"} -> {example["next"]}')
    for r in results[:3]:
        print(f'CED {r["verdict"]} {r["ced"]} score={r["score"]} supported={"?" if r["supported"] is None else int(r["supported"])} '
              f'covered={",".join(r["covered"]) or "-"} missing={",".join(r["missing"]) or "-"} '
              f'options={json.dumps(r["options"], separators=(",", ":"))[:160]}')
    if best and best['verdict'] == 'STRONG':
        print(f"NEXT: puts [ipiasm::ced_reference {best['ced']} {os.path.dirname(os.path.abspath(a.out))} "
              "{<option> <value>}], write ced_checklist.json from the request and the board's reference "
              "document, then puts [ipiasm::ced_fit <out>]: EXACT or CONFIG_ONLY is delivered with "
              "ipiasm::deliver_ced, without a DesignSpec or plan")
    status = best['verdict'] if best else 'NONE'
    print(f'REFERENCE_SEARCH:{status} ceds={len(results)} functions={len(groups)} '
          f'best={best["ced"] if best else "-"} file={a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
