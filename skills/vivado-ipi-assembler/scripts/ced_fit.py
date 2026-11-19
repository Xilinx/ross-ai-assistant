#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Does a recorded CED already meet the request?

usage: ced_fit.py --reference ced_reference.json --checklist ced_checklist.json [--out ced_fit.json]

ced_reference.json comes from ipiasm::ced_reference, or from the delivered design (see
ipiasm::deliver_ced). The checklist states only what the request and the board's reference
document require:

  {"ip_counts":  {"<catalog ip>": n, ...},        exact counts of the IP the request names
   "exclusive":  false,                            true: no other functional IP is allowed
   "clocks_mhz": {"<catalog ip>": mhz, ...},       clock of each cell of that IP
   "config":     {"<catalog ip>": {"CONFIG.K": "v"}}}

Verdict:
  EXACT         every item holds: deliver the CED as it is (ipiasm::deliver_ced)
  CONFIG_ONLY   only clock or CONFIG values differ: changes lists the cell settings that make them
                hold, which deliver_ced applies
  STRUCTURAL    an IP count differs, a required IP is missing, or an exclusive design has extra
                IP: build through the plan, seeded with ipiasm::instantiate_ced
Prints CED_FIT:<verdict> with one line per difference; writes the same to --out.
"""
import argparse
import collections
import json
import re
import sys

NOT_FUNCTIONAL = {'xlconcat', 'xlconstant', 'xlslice', 'ilconcat', 'ilconstant', 'ilslice', 'smartconnect',
                  'axi_interconnect', 'proc_sys_reset', 'lmb_v10', 'lmb_bram_if_cntlr', 'blk_mem_gen',
                  'util_ds_buf', 'util_vector_logic', 'clk_wiz', 'clk_wizard', 'pmcbridge', 'mdm_riscv', 'mdm',
                  'axi_intc'}


def catalog(vlnv):
    parts = vlnv.split(':')
    return parts[2] if len(parts) >= 3 else vlnv.split(':', 1)[-1]


def same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reference', required=True)
    ap.add_argument('--checklist', required=True)
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.reference, encoding='utf-8', errors='replace') if l.strip()]
    check = json.load(open(a.checklist))
    cells = {r['path']: r for r in rows if r.get('kind') == 'cell'}
    ip = {p: catalog(r['vlnv']) for p, r in cells.items()}
    counts = collections.Counter(ip.values())
    structural, config, changes = [], [], collections.defaultdict(dict)

    for name, n in (check.get('ip_counts') or {}).items():
        if counts.get(name, 0) != int(n):
            structural.append(f'{name}: the CED has {counts.get(name, 0)}, the request needs {n}')
    if check.get('exclusive'):
        allowed = set(check.get('ip_counts') or {}) | NOT_FUNCTIONAL
        for name in sorted(set(counts) - allowed):
            structural.append(f'{name}: the CED has {counts[name]} the request excludes')

    clocks = [r for r in rows if r.get('kind') == 'clk']
    for name, mhz in (check.get('clocks_mhz') or {}).items():
        targets = [p for p in cells if ip[p] == name]
        if not targets:
            structural.append(f'{name}: no such cell for its {mhz} MHz clock')
            continue
        for p in targets:
            pins = [r for r in clocks if r['pin'].rsplit('/', 1)[0] == p]
            if not pins:
                continue
            pin = pins[0]
            have = float(pin['freq'] or 0) / 1e6
            if abs(have - float(mhz)) < 1e-3:
                continue
            config.append(f'{p}: clock {have:g} MHz, the request needs {mhz} MHz (from {pin["src"] or "?"})')
            m = re.match(r'^(/.+)/clk_out(\d+)$', pin.get('src', '') or '')
            if m and ip.get(m.group(1), '') in ('clk_wiz', 'clk_wizard'):
                key = 'CLKOUT%s_REQUESTED_OUT_FREQ' % m.group(2)
                if ip[m.group(1)] == 'clk_wizard':
                    key = 'CLKOUT_REQUESTED_OUT_FREQUENCY'
                changes[m.group(1)]['CONFIG.' + key] = str(mhz)
            else:
                structural.append(f'{p}: its clock comes from {pin.get("src") or "an external port"}, '
                                  'not a clocking wizard output this check can retune')

    for name, wanted in (check.get('config') or {}).items():
        targets = [p for p in cells if ip[p] == name]
        if not targets:
            structural.append(f'{name}: no such cell for its settings')
            continue
        for p in targets:
            for key, value in wanted.items():
                have = cells[p]['config'].get(key)
                if not same(have, value):
                    config.append(f'{p}: {key} is {have}, the request needs {value}')
                    changes[p][key] = str(value)

    verdict = 'STRUCTURAL' if structural else ('CONFIG_ONLY' if config else 'EXACT')
    record = {'verdict': verdict, 'structural': structural, 'config': config, 'changes': dict(changes),
              'reference': a.reference}
    if a.out:
        with open(a.out, 'w') as handle:
            json.dump(record, handle, indent=1)
    print(f'CED_FIT:{verdict} structural={len(structural)} config={len(config)}'
          + (f' file={a.out}' if a.out else ''))
    for line in structural + config:
        print('  ' + line)
    if changes:
        print('  changes=' + json.dumps(changes, separators=(',', ':')))
    brace = lambda text: '{' + str(text).replace('\\', '').replace('{', '').replace('}', '') + '}'
    print('CHANGES_TCL ' + ' '.join(f'{brace(cell)} {{' + ' '.join(f'{brace(k)} {brace(v)}' for k, v in kv.items()) + '}'
                                   for cell, kv in changes.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
