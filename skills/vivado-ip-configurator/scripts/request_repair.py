# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Repairs to a model's JSON op before the session sees it, shared by both hosts.

scripts/ipcfg_bridge.py (the stock-MCP path, under Vivado's bundled Python) and the
optional guard_mcp.py relay apply the same bounded repairs. Standard library only.
"""
import json


def parse_request(command):
    """Parse a structured request, dropping at most three unmatched closing brackets."""
    text, removed = command, 0
    while True:
        try:
            return json.loads(text), removed
        except json.JSONDecodeError as exc:
            stray = exc.pos < len(text) and text[exc.pos] in '}]' and exc.msg in (
                "Expecting ',' delimiter", 'Extra data')
            if not stray or removed >= 3:
                if removed:
                    raise ValueError(str(exc).split(':')[0] + ' after removing ' + str(removed) + ' stray bracket(s)') from None
                raise
            text, removed = text[:exc.pos] + text[exc.pos + 1:], removed + 1


def remove_redundant_session(request, session):
    """Drop a duplicated outer MCP session ID only when it matches exactly."""
    if 'session_id' not in request:
        return False
    if request['session_id'] != session:
        raise ValueError('Structured request session_id does not match the outer MCP session')
    del request['session_id']
    return True


def repair_response_echoes(request):
    """Remove bounded reply metadata that small models sometimes echo into a request."""
    repairs = []
    if 'op' not in request and request.get('operation') in {
            'begin', 'choose_ip', 'describe', 'select', 'waive', 'apply', 'finish', 'status'}:
        request['op'] = request.pop('operation')
        repairs.append('Renamed operation to op')
    if request.get('op') == 'begin':
        for old, new in (('ip_name', 'ip'), ('cell_name', 'cell')):
            if new not in request and old in request:
                request[new] = request.pop(old)
                repairs.append('Renamed ' + old + ' to ' + new)
        for key in ('block_design', 'design', 'bd'):
            if key in request:
                del request[key]
                repairs.append('Removed open-design locator ' + key)
    for key in ('stage', 'next_op', 'session_preserved', 'session_closed',
                'full', 'operation_rejected', 'repeat_count'):
        if key in request:
            del request[key]
            repairs.append('Removed response-only ' + key)
    return repairs
