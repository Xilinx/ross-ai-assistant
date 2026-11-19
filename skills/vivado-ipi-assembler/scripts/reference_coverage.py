#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Does the built design keep every functional IP of its reference design?

usage: reference_coverage.py --built CELLS.txt --spec-dir DIR

CELLS.txt holds one VLNV per line (the built design's cells, hierarchy flattened). DIR is the
DesignSpec's directory; the references are the ced_reference.json and example_reference.json
found there or one level below (ipiasm::ced_reference, ipiasm::example_reference). Each
reference's functional IPs (glue, interconnect and simulation-only models excluded) are counted
and compared with the build. Every IP the build has fewer of must be listed in
DIR/reference_deviations.json as {"ip": "<catalog name>", "count": <units dropped>,
"reason": "<why>"} entries; a missing or short entry fails.

Prints REFERENCE_COVERAGE:OK|NA|FAIL with one line per shortfall.
"""
import argparse
import collections
import json
import os
import sys

# Not functional units of a design: glue, wiring, resets, clocking buffers, sim-only models.
EXEMPT = {
    'xlconcat', 'xlconstant', 'xlslice', 'ilconcat', 'ilconstant', 'ilslice', 'ilvector_logic',
    'ilreduced_logic', 'util_vector_logic', 'util_reduced_logic', 'util_ds_buf', 'smartconnect',
    'axi_interconnect', 'axis_register_slice', 'axi_register_slice', 'axis_data_fifo',
    'proc_sys_reset', 'lmb_v10', 'lmb_bram_if_cntlr', 'blk_mem_gen', 'emb_mem_gen', 'pmcbridge',
    'clk_gen_sim', 'ddr_responder', 'sim_trig', 'axi_vip', 'sim_clk_gen', 'axi4stream_vip',
}


def name(vlnv):
    parts = vlnv.split(':')
    return parts[2] if len(parts) >= 3 else vlnv


def reference_files(spec_dir):
    found = []
    for root in (spec_dir, *[os.path.join(spec_dir, d) for d in sorted(os.listdir(spec_dir))
                             if os.path.isdir(os.path.join(spec_dir, d)) and not d.startswith('.')]):
        for f in ('ced_reference.json', 'example_reference.json'):
            path = os.path.join(root, f)
            if os.path.isfile(path):
                found.append(path)
    return found


def reference_ips(path):
    rows = []
    for line in open(path, encoding='utf-8', errors='replace'):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    cells = [r for r in rows if r.get('kind') == 'cell']
    # An RTL example has no block-design cells: its IP instances are the units.
    units = cells or [r for r in rows if r.get('kind') == 'ip']
    return collections.Counter(n for n in (name(r.get('vlnv', '')) for r in units) if n and n not in EXEMPT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--built', required=True)
    ap.add_argument('--spec-dir', required=True)
    a = ap.parse_args()
    refs = reference_files(a.spec_dir)
    if not refs:
        print('REFERENCE_COVERAGE:NA no ced_reference.json or example_reference.json beside the DesignSpec')
        return 0
    built = collections.Counter(name(l.strip()) for l in open(a.built) if l.strip())
    deviations = collections.Counter()
    reasons = {}
    path = os.path.join(a.spec_dir, 'reference_deviations.json')
    if os.path.isfile(path):
        try:
            for entry in json.load(open(path)):
                if str(entry.get('reason', '')).strip():
                    # Agents often write the VLNV they read from the reference; count it by name.
                    ip = name(str(entry['ip']))
                    deviations[ip] += int(entry.get('count', 1))
                    reasons[ip] = entry['reason']
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            print(f'REFERENCE_COVERAGE:FAIL reference_deviations.json is not a list of {{ip, count, reason}}: {error}')
            return 0
    lines, short = [], 0
    for ref in refs:
        want = reference_ips(ref)
        for ip, n in sorted(want.items()):
            missing = n - built.get(ip, 0)
            if missing <= 0:
                continue
            if deviations.get(ip, 0) >= missing:
                lines.append(f'  explained {ip}: {missing} of {n} not built ({reasons[ip][:80]})')
            else:
                short += 1
                lines.append(f'  UNEXPLAINED {ip}: reference {os.path.basename(ref)} has {n}, build has '
                             f'{built.get(ip, 0)}; list it in reference_deviations.json with a reason or build it: '
                             f'{{"ip": "{ip}", "count": {missing}, "reason": "<why the request drops it>"}}')
    if short:
        print(f'REFERENCE_COVERAGE:FAIL {short} functional IP(s) of the reference dropped without a reason')
    else:
        print(f'REFERENCE_COVERAGE:OK references={len(refs)} explained={len([l for l in lines if "explained" in l])}')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
