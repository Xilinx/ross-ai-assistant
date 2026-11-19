#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Draft a DesignSpec from a recorded reference design.

usage: draft_spec.py --reference REF.json --out DIR [--name NAME] [--request TEXT]
                     [--part PART] [--board BOARD_PART]

REF.json is ced_reference.json (ipiasm::ced_reference) or example_reference.json
(ipiasm::example_reference). Writes:

  DIR/design_spec.draft.json  a schema-valid DesignSpec with one block per functional cell of
                              the reference (glue, interconnect, resets and simulation models
                              left out), each with the reference's user-set CONFIG values as
                              parameters, and the reference's connections, control and memory
                              reachability, interrupts, fixed address windows and clocks as
                              functional contracts
  DIR/reference_digest.txt    the reference's IP counts, clocks, processor configuration,
                              interrupts and address map: the excerpt to cite in
                              spec_sources.json for the reference

Edit the draft to the request (counts, values the request or the board's reference document
sets differently), then validate and audit it as usual and save it as design_spec.json.
Prints DRAFT_SPEC:OK blocks=<n> contracts=<n> with the two paths.
"""
import argparse
import collections
import json
import os
import re
import sys

GLUE = {'xlconcat', 'xlconstant', 'xlslice', 'ilconcat', 'ilconstant', 'ilslice', 'ilvector_logic',
        'ilreduced_logic', 'util_vector_logic', 'util_reduced_logic'}
WIRES = {'smartconnect', 'axi_interconnect', 'axis_register_slice', 'axi_register_slice', 'axis_data_fifo'}
INFRA = {'proc_sys_reset', 'lmb_v10', 'lmb_bram_if_cntlr', 'blk_mem_gen', 'emb_mem_gen', 'util_ds_buf',
         'clk_wiz', 'clk_wizard', 'pmcbridge'}
SIM = {'clk_gen_sim', 'ddr_responder', 'sim_trig', 'axi_vip', 'sim_clk_gen', 'axi4stream_vip'}
PROCESSORS = {'microblaze_riscv', 'microblaze', 'zynq_ultra_ps_e', 'versal_cips', 'ps_wizard', 'processing_system7'}
MEMORY = {'lpddrmc', 'ddr4', 'mig_7series', 'axi_noc', 'noc_mc_ddr4', 'axi_bram_ctrl'}


def catalog(vlnv):
    if vlnv.startswith('inline:'):
        return vlnv.split(':', 1)[1]
    parts = vlnv.split(':')
    return parts[2] if len(parts) >= 3 else vlnv


def leaf(path):
    return path.rstrip('/').rsplit('/', 1)[-1]


def cell_of(pin):
    return pin if pin.startswith('port:') else pin.rsplit('/', 1)[0]


def workload(ip):
    if ip in PROCESSORS:
        return 'bare_metal_firmware'
    if ip in MEMORY:
        return 'memory_controller'
    if ip.startswith(('v_frmbuf', 'axi_dma', 'axi_vdma', 'axi_cdma', 'xdma', 'qdma')):
        return 'data_movement'
    if ip.startswith(('mipi', 'v_hdmi', 'v_dp')):
        return 'video_io'
    if ip.startswith(('v_', 'axis_')):
        return 'video_processing'
    return 'control_logic'


def protocol(vlnv):
    name = catalog(vlnv)
    return {'aximm_rtl': 'AXI4', 'axis_rtl': 'AXI4-Stream', 'iic_rtl': 'IIC', 'gpio_rtl': 'GPIO',
            'uart_rtl': 'UART', 'mipi_phy_rtl': 'MIPI D-PHY', 'diff_clock_rtl': 'differential clock',
            'spi_rtl': 'SPI'}.get(name, name.replace('_rtl', ''))


def load(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reference', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--name', default='')
    ap.add_argument('--request', default='')
    ap.add_argument('--part', default='')
    ap.add_argument('--board', default='')
    a = ap.parse_args()
    rows = load(a.reference)
    head = next((r for r in rows if r.get('kind') in ('ced', 'example')), {})
    ref_name = head.get('name') or head.get('ip') or os.path.basename(a.reference)
    cells = {r['path']: r for r in rows if r.get('kind') == 'cell'}
    rtl_top = ''
    if not cells:
        # An RTL example's units are its IP instances; its top module, not a block design,
        # wires them, so the draft has blocks and parameters but no connections.
        cells = {'/' + r['name']: {'path': '/' + r['name'], 'vlnv': r.get('vlnv', ''), 'config': r.get('config', {}),
                                   'user': r.get('user', {})}
                 for r in rows if r.get('kind') == 'ip' and r.get('name')}
        rtl_top = head.get('top', '') or 'its top module'
    if not cells:
        print(f'DRAFT_SPEC_FAIL:{a.reference} records neither block-design cells nor IP instances')
        return 1
    ip = {p: catalog(r['vlnv']) for p, r in cells.items()}
    # A CED's automation rebuilds its infrastructure and simulation models; in an RTL example
    # every IP instance is part of the example and nothing else would add it back.
    left_out = GLUE | WIRES if rtl_top else GLUE | WIRES | INFRA | SIM
    functional = [p for p in sorted(cells) if ip[p] not in left_out]
    # Block names: the cell's own name, unique and spec-safe.
    names, used = {}, set()
    for p in functional:
        base = re.sub(r'[^a-z0-9_]', '_', leaf(p).lower()) or ip[p]
        name, i = base, 1
        while name in used:
            i += 1
            name = f'{base}_{i}'
        used.add(name)
        names[p] = name

    links = [r for r in rows if r.get('kind') == 'link']
    adj = collections.defaultdict(set)
    for r in links:
        adj[r['a']].add((r['b'], r['vlnv'], r.get('mode', '')))
        adj[r['b']].add((r['a'], r['vlnv'], ''))

    def through(pin, seen):
        """Ends of an interface after passing through wire cells (interconnect, slices)."""
        out = set()
        for other, vlnv, _ in adj[pin]:
            if other in seen:
                continue
            seen.add(other)
            c = cell_of(other)
            if ip.get(c) in WIRES:
                for p2 in [p for p in adj if cell_of(p) == c and p != other]:
                    seen.add(p2)
                    out |= through(p2, seen)
            else:
                out.add(other)
        return out

    blocks = {p: {'name': names[p], 'description': '', 'workload_type': workload(ip[p]), 'interfaces': [],
                  'constraints': {}, 'parameters': {}, 'instance_name': leaf(p)} for p in functional}
    def add_intf(p, iname, proto, direction, scope='internal'):
        b = blocks.get(p)
        if b is None or any(i['name'] == iname for i in b['interfaces']):
            return
        b['interfaces'].append({'name': iname, 'protocol': proto, 'direction': direction, 'scope': scope})

    contracts = {k: [] for k in ('connections', 'control_reachability', 'interrupts', 'memory_reachability',
                                 'fixed_address_windows', 'visibility_policy', 'clock_reset', 'software')}
    # Address map: masters reach slaves; memory slaves are memory reachability.
    maps = [r for r in rows if r.get('kind') == 'map']
    seen_pairs = set()
    for r in maps:
        m, s = r['master'], r['slave']
        if m not in names or s not in names:
            continue
        mem = ip[s] in MEMORY and ip[s] != 'axi_bram_ctrl'
        add_intf(m, f"m_axi_{r['space'].lower()}", 'AXI4', 'out')
        add_intf(s, (r.get('slave_intf') or 's_axi').lower(), 'AXI4' if mem else 'AXI4-Lite', 'in')
        pair = (names[m], names[s])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        contracts['connections'].append({'source': f"{names[m]}.m_axi_{r['space'].lower()}",
                                         'destination': f"{names[s]}.{(r.get('slave_intf') or 's_axi').lower()}",
                                         'protocol': 'AXI4' if mem else 'AXI4-Lite',
                                         'purpose': f'{ref_name} address map', 'required': True})
        if mem:
            contracts['memory_reachability'].append({'master': names[m], 'memory': names[s], 'access': 'read_write',
                                                     'required': True})
        else:
            contracts['control_reachability'].append({'master': names[m], 'target': names[s], 'access': 'read_write',
                                                      'required': True})
            contracts['fixed_address_windows'].append({
                'name': f'{names[s]}_window', 'block': names[s], 'target': names[s], 'masters': [names[m]],
                'offset': r['offset'].lower(), 'range': r['range'].lower(),
                'reason': f'{ref_name} address map'})
    # Datapath and board interfaces (not memory-mapped).
    for p in functional:
        for pin in [x for x in adj if cell_of(x) == p]:
            vl = next((v for _, v, _ in adj[pin]), '')
            if 'aximm' in vl:
                continue
            mode = next((md for _, _, md in adj[pin] if md), '')
            direction = 'out' if mode.lower() in ('master', 'mirroredslave') else 'in'
            for end in through(pin, {pin}):
                if end.startswith('port:'):
                    add_intf(p, leaf(pin).lower(), protocol(vl), 'bidirectional' if 'gpio' in vl or 'iic' in vl else direction,
                             'external')
                elif cell_of(end) in names and direction == 'out':
                    add_intf(p, leaf(pin).lower(), protocol(vl), 'out')
                    add_intf(cell_of(end), leaf(end).lower(), protocol(vl), 'in')
                    contracts['connections'].append({'source': f'{names[p]}.{leaf(pin).lower()}',
                                                     'destination': f'{names[cell_of(end)]}.{leaf(end).lower()}',
                                                     'protocol': protocol(vl), 'purpose': f'{ref_name} datapath',
                                                     'required': True})
    # Interrupts: source -> controller, through concat cells.
    irqs = [r for r in rows if r.get('kind') == 'irq']
    by_src = collections.defaultdict(list)
    for r in irqs:
        by_src[r['src']].append(r['dst'])
    def final(dst, depth=0):
        c = cell_of(dst)
        if ip.get(c) in GLUE and depth < 4:
            outs = [d for s, ds in by_src.items() if cell_of(s) == c for d in ds]
            return [x for o in outs for x in final(o, depth + 1)] or [dst]
        return [dst]
    irq_lines = []
    for src, dsts in sorted(by_src.items()):
        c = cell_of(src)
        if c not in names:
            continue
        for d in [x for dst in dsts for x in final(dst)]:
            if cell_of(d) in names:
                add_intf(c, leaf(src).lower(), 'interrupt', 'out')
                contracts['interrupts'].append({'source': names[c], 'outputs': [leaf(src)],
                                                'destination': names[cell_of(d)], 'allow_aggregation': True,
                                                'required': True})
                irq_lines.append(f'{leaf(c)}.{leaf(src)} -> {leaf(cell_of(d))}')
    # Clocks: each block at its reference frequency; the domains they share.
    clks = [r for r in rows if r.get('kind') == 'clk']
    domains = collections.OrderedDict()
    block_clock = {}
    for r in clks:
        c = cell_of(r['pin'])
        try:
            mhz = round(float(r['freq']) / 1e6, 3)
        except (TypeError, ValueError):
            continue
        src = r.get('src', '')
        domains.setdefault((src, mhz), set()).add(names.get(c, leaf(c)))
        if c in names and names[c] not in block_clock:
            block_clock[names[c]] = mhz
    for name, mhz in block_clock.items():
        contracts['clock_reset'].append({'target': name, 'clock_mhz': mhz, 'reset_polarity': 'interface_defined',
                                         'synchronized': True, 'required': True})
    clock_domains = [{'name': f"clk_{str(mhz).rstrip('0').rstrip('.').replace('.', 'p')}mhz_{i}",
                      'frequency_mhz': mhz, 'source': src or 'external', 'members': sorted(members)}
                     for i, ((src, mhz), members) in enumerate(domains.items())]
    # Parameters and descriptions: the values the reference sets on purpose.
    for p in functional:
        user = {k: v for k, v in cells[p].get('user', {}).items() if k != 'CONFIG.Component_Name'}
        blocks[p]['parameters'] = {'catalog_ip': cells[p]['vlnv'], 'reference_config': user}
        shown = ', '.join(f'{k[7:]}={v}' for k, v in list(user.items())[:6])
        blocks[p]['description'] = f"{ip[p]} from {ref_name}" + (f'; set: {shown}' if shown else '') + \
            (f'; clock {block_clock[names[p]]} MHz' if names[p] in block_clock else '')
    spec = {
        'name': a.name or re.sub(r'[^a-z0-9_]', '_', (a.request or ref_name).lower())[:48].strip('_') or 'design',
        'description': (a.request + '; ' if a.request else '') + f'drafted from {ref_name}' +
                       (f'; an RTL example: take the connections, clocks and ports from {rtl_top}' if rtl_top else ''),
        'target_device': a.part or head.get('part', '') or 'TODO_part',
        'blocks': list(blocks.values()),
        'functional_contracts': contracts,
        'clock_domains': clock_domains,
        'global_constraints': {'unresolved_requirements': []},
        'power_budget_watts': None,
    }
    board = a.board or head.get('board', '')
    if board:
        spec['board_part'] = board
    os.makedirs(a.out, exist_ok=True)
    draft = os.path.join(a.out, 'design_spec.draft.json')
    with open(draft, 'w') as handle:
        json.dump(spec, handle, indent=1)
    counts = collections.Counter(ip[p] for p in cells if ip[p] not in (GLUE | WIRES if rtl_top else GLUE | WIRES | SIM))
    proc = [p for p in functional if ip[p] in PROCESSORS]
    digest = [f'Reference {ref_name} (recorded from Vivado {os.path.basename(a.reference)}).',
              'IP: ' + ', '.join(f'{k} x{v}' for k, v in sorted(counts.items())),
              'Clocks: ' + '; '.join(f"{d['frequency_mhz']} MHz from {d['source']} ({len(d['members'])} inputs)"
                                    for d in clock_domains)]
    for p in proc:
        digest.append(f'Processor {leaf(p)} ({ip[p]}): ' +
                      ', '.join(f'{k[7:]}={v}' for k, v in cells[p].get('user', {}).items()))
    if rtl_top:
        digest.append(f'RTL example: {rtl_top} instantiates these IPs and wires them; read it for the '
                      'connections, clocks, resets and top-level ports')
    digest.append('Interrupts: ' + ('; '.join(irq_lines) or 'none'))
    digest.append('Address map: ' + '; '.join(f"{leaf(r['master'])}->{leaf(r['slave'])} {r['offset']}/{r['range']}"
                                              for r in maps if r['master'] in names and r['slave'] in names))
    digest_path = os.path.join(a.out, 'reference_digest.txt')
    with open(digest_path, 'w') as handle:
        handle.write('\n'.join(digest) + '\n')
    n = sum(len(v) for v in contracts.values())
    print(f'DRAFT_SPEC:OK blocks={len(blocks)} contracts={n} clock_domains={len(clock_domains)} '
          f'draft={draft} digest={digest_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
