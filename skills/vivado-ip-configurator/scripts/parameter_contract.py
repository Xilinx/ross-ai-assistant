# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Selected live ownership and value shape, independent of the IP family."""
import re

from structured_config import dict_tcl


def named_intent(task):
    match = re.search(r'\b(?:add|create|instantiate|configure)\s+(?:an?\s+|the\s+)?'
                      r'(.{1,160}?)\s+named\s+[`\"\']?([A-Za-z][A-Za-z0-9_]*)',
                      task, re.I)
    return dict(ip=match[1].strip(), cell=match[2]) if match else None


def parse_live(receipt):
    result = {}
    for key, raw in dict_tcl(receipt).items():
        fields = dict_tcl(raw)
        value, source = fields['value'], fields.get('value_src', '')
        row = dict(value_src=source or 'unavailable',
                   write_mode='assert' if source.upper() in ['PROPAGATED', 'CONSTANT'] else 'write',
                   live_scope='current_cell')
        row.update(range=fields.get('range', 'RANGE_NONE'),
                   range_scope=fields.get('range_scope', 'unavailable'),
                   range_complete=fields.get('range_complete') == '1')
        if fields.get('enabled') in ['0', '1']:
            row.update(enabled=fields['enabled'] == '1',
                       enablement_scope=fields.get('enablement_scope', 'unavailable'))
        if fields.get('truncated') == '1':
            row.update(current_truncated=True, current=value, shape='unresolved')
        elif ',' in value:
            values = value.split(',')
            row.update(shape='vector', length=len(values), current=value)
            if all(v.strip().lower() in ['true', 'false'] for v in values):
                row['element_type'] = 'boolean'
        else:
            row.update(shape='scalar', current=value)
        result[key] = row
    return result


ACT_TOKEN = re.compile(r'(?<=_)ACT_')


def achieved_source(key, keys):
    """The requested parameter an ACT_ readback reports, when the cell has one.

    Vivado publishes a PS clock twice: PCW_FPGA0_PERIPHERAL_FREQMHZ is the
    request and PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ is what the PLL achieved
    (PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ on MPSoC). Both come back as
    enabled writable floats over the same range, and the achieved one reads
    value_src USER because Vivado wrote the computed result into it, so it
    looks like the more authoritative of the two. Writing it is ignored, and
    the design keeps the old frequency while the run reports success.

    The sibling's existence is the evidence, so a parameter merely spelled
    with ACT and no requested twin is left writable.
    """
    for span in ACT_TOKEN.finditer(key):
        sibling = key[:span.start()] + key[span.end():]
        if sibling in keys:
            return sibling
    return None


def boolean_value(value):
    if str(value).lower() not in ['true', 'false', '0', '1']:
        raise ValueError('Boolean candidate accepts only true, false, 0 or 1; preserve the requested value and retrieve its parameter')
    return 'true' if str(value).lower() in ['true', '1'] else 'false'
