# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Example MCP relay for the CED workflow, for local models only (examples/README.md).

The skill itself needs none of this: on the stock Vivado MCP the model sources
lib/ced_op.tcl and sends `ced_op {<json>}` through plain vivado_execute. This
relay exists for a host serving a local model that cannot be trusted with raw
Tcl: it re-exposes vivado_execute as a JSON op and adds ced_apply_phase2, so
Phase 2 writes can be gated on the user's approval (OpenCode permission "ask").
Launch it from the harness, never as a model tool.

Reuses the lite guard (evidence records, retry stops, cleanup) with CED tool
schemas.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
# Bundled engine (engine/README.md); CED_LITE_SKILL points at another engine root.
LITE = Path(os.environ.get('CED_LITE_SKILL', SKILL / 'engine')).resolve()
sys.path[:0] = [str(SKILL / 'scripts'), str(LITE / 'scripts')]

from mcp import types  # noqa: E402
from guard_mcp import error_result, serve  # noqa: E402
from ced_session import CEDSession  # noqa: E402

SPECS = {
    'vivado_start': ('Start an owned headless Vivado IPI session in the existing scratch directory.', {
        'working_dir': {'type': 'string'}}, ['working_dir']),
    'vivado_execute': ('Run one JSON CED workflow operation on the returned session_id: begin, choose_target, choose_ced, '
                       'set_options, defer, waive, instantiate, cells, assign, describe, select, propose, finish or status. '
                       'The skill documents command fields. The server constructs all Tcl.', {
        'session_id': {'type': 'string'}, 'command': {'type': 'string', 'description': 'JSON object encoded as a string.'}},
        ['session_id', 'command']),
    'vivado_stop': ('Close the owned Vivado session cleanly after saving.', {
        'session_id': {'type': 'string'}, 'close_vivado': {'type': 'boolean', 'default': True}}, ['session_id']),
    'ced_apply_phase2': ('Apply an approved Phase 2 proposal to the design. Call only after the user has approved this exact '
                         'proposal_id in the conversation.', {
        'session_id': {'type': 'string'}, 'proposal_id': {'type': 'string'}}, ['session_id', 'proposal_id']),
}


VIVADO_INIT = """\
catch {{source -notrace {{{user_init}}}}}
for {{set _i 0}} {{$_i < 20}} {{incr _i}} {{
    if {{![catch {{webserver -start -key none -port [expr {{20000 + int(rand() * 30000)}}]}}]}} break
}}
unset _i
"""


def isolated_environments(state, vivado_path):
    """Keep other Vivado MCP servers on this host away from our sessions.

    Those servers adopt sessions from the shared $HOME/.vivado-ai store and probe
    webservers on localhost:8088-8095; their requests run Tcl on Vivado's webserver
    thread and crash long commands (example-design instantiation is one). A private
    MCP home hides our sessions; a private Vivado home starts the webserver on a high
    port first and still sources the user's own Vivado_init.tcl.
    """
    real_home = os.environ['HOME']
    match = re.search(r'/(\d{4}\.\d)(?:\.\d)?/Vivado/', vivado_path or '')
    version = match.group(1) if match else ''
    init_dir = state / 'vivado-home' / '.Xilinx' / 'Vivado' / version
    init_dir.mkdir(parents=True, exist_ok=True)
    (state / 'mcp-home').mkdir(parents=True, exist_ok=True)
    user_init = Path(real_home, '.Xilinx', 'Vivado', version, 'Vivado_init.tcl')
    (init_dir / 'Vivado_init.tcl').write_text(VIVADO_INIT.format(user_init=user_init))
    return dict(os.environ, HOME=str(state / 'mcp-home')), {'HOME': str(state / 'vivado-home')}


async def apply_phase2(guard, upstream, arguments):
    session = guard.structured_sessions.get(arguments.get('session_id'))
    try:
        if session is None:
            raise ValueError('Unknown session_id')
        # Unattended runs cannot ask anyone: the eval auto-allows this tool on
        # every backend, so "ask" was only ever prompt text, and ced-gen_H5w's
        # Haiku run wrote 230400 baud the user never approved. With no human to
        # answer, an "ask" session's proposal stays unapplied. Interactive use
        # leaves CED_UNATTENDED unset and keeps the host's own "ask" gate.
        if os.environ.get('CED_UNATTENDED') == '1' and not session.pre_approved:
            raise ValueError('This session is unattended, so no user can approve proposal ' + str(arguments.get('proposal_id')) +
                             ', and begin recorded phase2_approval=ask. Nothing was written. Finish to keep the Phase 1 '
                             'design and report the proposal as awaiting approval.')
        reply = await session.approve(arguments.get('proposal_id'))
    except Exception as exc:
        path = guard.record({'tool': 'ced_apply_phase2', 'arguments': arguments, 'error': str(exc), 'failed': True})
        return error_result('APPROVAL_ERROR: ' + str(exc)[:400], path)
    path = guard.record({'tool': 'ced_apply_phase2', 'arguments': arguments, 'reply': reply, 'failed': False})
    data = dict(exit_code=0, output=json.dumps(reply, separators=(',', ':')), evidence_path=str(path), used_mode='structured')
    return types.CallToolResult(structuredContent=data, content=[types.TextContent(type='text', text=json.dumps(data))])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--ced-cache', required=True, help='Absolute mirror cache directory outside /home')
    parser.add_argument('--database-config', help='Optional lite IP metadata snapshot runtime.json for Phase 2 cell discovery')
    parser.add_argument('--state-dir', help='Private MCP and Vivado homes (default: beside --evidence)')
    args = parser.parse_args()
    args.state_dir = args.state_dir or str(Path(args.evidence).parent / 'ced-guard-state')
    for name in ['evidence', 'ced_cache', 'state_dir']:
        value = Path(getattr(args, name))
        if not value.is_absolute() or value == Path('/home') or Path('/home') in value.resolve().parents:
            parser.error('--' + name.replace('_', '-') + ' must be absolute and outside /home')
    Path(args.evidence).mkdir(parents=True, exist_ok=True)
    CEDSession.cache = args.ced_cache
    upstream_env, start_env = isolated_environments(Path(args.state_dir), os.environ.get('VIVADO_PATH'))
    asyncio.run(serve(args.upstream, args.evidence, args.database_config, profile='structured', session_factory=CEDSession,
                      tool_specs=SPECS, extra_handlers={'ced_apply_phase2': apply_phase2}, server_name='vivado-ced-guard',
                      upstream_env=upstream_env, start_env=start_env))
