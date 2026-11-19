# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Schema-preserving Vivado MCP relay; launch from the scratch harness, not as a model tool."""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import threading
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.server.stdio import stdio_server

import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # its sibling modules, however it is loaded
from request_repair import parse_request, remove_redundant_session, repair_response_echoes  # noqa: E402,F401

TOOLS = {'vivado_start', 'vivado_execute', 'vivado_stop'}


def payload(result):
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        if block.type == 'text':
            try:
                value = json.loads(block.text)
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    return {}


def error_result(category, evidence, workflow=None, request_schema=None):
    data = {'exit_code': 1, 'error_type': category, 'output': category,
            'evidence_path': str(evidence)}
    if workflow:
        data['workflow_state'] = workflow
    if request_schema:
        data['request_schema'] = request_schema
    return types.CallToolResult(isError=True, structuredContent=data,
                                content=[types.TextContent(type='text', text=json.dumps(data))])


async def tcl_complete(command):
    # The command is decoded as DATA. Tcl never evaluates the supplied script here.
    encoded = base64.b64encode(command.encode()).decode('ascii')
    script = 'set script [encoding convertfrom utf-8 [binary decode base64 {' + encoded + '}]]\nputs [info complete $script]\n'
    process = await asyncio.create_subprocess_exec('tclsh', stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(script.encode()), 5)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode or out.strip() not in (b'0', b'1'):
        raise RuntimeError('Tcl completeness checker failed: ' + err.decode(errors='replace')[:200])
    return out.strip() == b'1'


class ExecutionGuard:
    def __init__(self, evidence, output_limit=1800, database_config=None, session_factory=None):
        # session_factory replaces StructuredSession for relays that subclass it
        # (the CED guard adopts cells a example design built); None keeps lite's own.
        self.session_factory = session_factory
        self.evidence = Path(evidence).resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.output_limit = output_limit
        self.failures = {}
        self.counter = 0
        self.database_config = database_config
        self.database = None
        self.database_lock = threading.Lock()
        # The release database Vivado ships at data/ip/ipdb/.  A session reports its own install
        # once it is up, so this stays unset until then and an explicit snapshot always wins.
        self.release_database_path = None
        self.release_database_release = None
        self.started = {}
        self.closed = set()
        self.structured_sessions = {}
        self.structured_failures = {}
        self.native_sequences = {}

    def next_native_serial(self, session):
        """Allocate a session-wide nonce across consecutive cell ledgers."""
        self.native_sequences[session] = self.native_sequences.get(session, 0) + 1
        return self.native_sequences[session]

    def record(self, data):
        self.counter += 1
        path = self.evidence / f'execute-{self.counter:04d}.json'
        path.write_text(json.dumps(data, indent=2))
        return path

    def adopt_release_database(self, install_root, version_text):
        """Offer the install's own database; an explicitly mounted snapshot still wins."""
        if self.database_config or self.database is not None:
            return None
        from merged_database import release_of, shipped_database
        path = shipped_database(install_root)
        if path is None:
            return None
        self.release_database_path = str(path)
        self.release_database_release = release_of(version_text)
        return self.release_database_path

    def has_database(self):
        return bool(self.database_config or self.release_database_path)

    def database_request(self, command):
        import time
        # Reserved session is a read-only JSON protocol, never Tcl or SQL.
        if not self.has_database():
            raise ValueError('DATABASE_NOT_CONFIGURED: use live Vivado discovery')
        if not isinstance(command, str) or len(command) > 2400:
            raise ValueError('Database command must be a bounded JSON object')
        request = json.loads(command)
        started = time.monotonic()
        with self.database_lock:
            if self.database is None:
                if self.database_config:
                    from ip_database import Database
                    config = json.loads(Path(self.database_config).read_text())
                    self.database = Database(config)
                else:
                    from merged_database import MergedDatabase
                    self.database = MergedDatabase(self.release_database_path,
                                                   release=self.release_database_release)
            result = self.database.request(request)
            self.record(dict(database_query_timing=True, op=request.get('op'),
                             elapsed_seconds=time.monotonic()-started))
            return result

    def close_database(self):
        with self.database_lock:
            if self.database is not None:
                self.database.close()
                self.database = None

    async def execute(self, upstream, arguments):
        raw_command = arguments.get('command', '')
        command = (json.dumps(raw_command, separators=(',', ':'))
                   if isinstance(raw_command, dict) else raw_command)
        session = arguments.get('session_id', '')
        structured_shape = isinstance(raw_command, dict) or (
            isinstance(command, str) and command.lstrip().startswith('{'))
        if session != 'ipdb' and structured_shape:
            request = None
            try:
                request, repaired = parse_request(command)
                if not isinstance(request, dict) or len(command) > 16000:
                    raise ValueError('Bounded JSON object required')
                nested_session = request.get('session_id', '')
                if session and nested_session and nested_session != session:
                    raise ValueError('Structured request session_id does not match the outer MCP session')
                if not session:
                    if nested_session:
                        session = nested_session
                    else:
                        active = [sid for sid in self.started if sid not in self.closed]
                        if len(active) != 1:
                            raise ValueError('Omitted session_id requires exactly one owned Vivado session')
                        session = active[0]
                redundant_session = remove_redundant_session(request, session)
                request_repairs = repair_response_echoes(request)
                if session not in self.started:
                    raise ValueError('Start Vivado with this MCP first; use the returned session_id')
                factory = self.session_factory
                if factory is None:
                    from structured_config import StructuredSession as factory
                previous = self.structured_sessions.get(session)
                # finish saves a cell and keeps Vivado open, so the next prompt's begin starts a
                # fresh cell ledger on the same session (project, part and Tcl state carry over).
                # A terminal stop only blocks re-running that failed cell's own request.
                if (request.get('op') == 'begin' and previous is not None and previous.stage == 'finished'
                        and session not in self.closed
                        and not (getattr(previous, 'terminal_stop', False)
                                 and request.get('cell') == getattr(previous, 'cell', None))):
                    self.structured_sessions[session] = factory(self, upstream, session, self.started[session])
                if session not in self.structured_sessions:
                    self.structured_sessions[session] = factory(self, upstream, session, self.started[session])
                reply = await self.structured_sessions[session].request(request)
                repairs = []
                if repaired:
                    repairs.append('Removed ' + str(repaired) + ' unmatched closing bracket(s)')
                if redundant_session:
                    repairs.append('Removed matching duplicate session_id from the structured command')
                repairs.extend(request_repairs)
                if repairs and isinstance(reply, dict):
                    reply = dict(reply, request_repaired='; '.join(repairs) + '; check the parsed request fields in this reply')
                self.structured_failures = {k:v for k,v in self.structured_failures.items() if k[0] != session}
                path = self.record({'arguments': arguments, 'structured_request': True, 'reply': reply, 'failed': False})
                from structured_config import compact_reply  # the reply sent; the record above keeps every field
                data = dict(exit_code=0, output=json.dumps(compact_reply(reply), separators=(',', ':')), evidence_path=str(path), used_mode='structured')
                return types.CallToolResult(structuredContent=data, content=[types.TextContent(type='text', text=json.dumps(data))])
            except Exception as exc:
                path = self.record({'arguments': arguments, 'structured_request': True, 'error': str(exc), 'failed': True})
                state = self.structured_sessions.get(session)
                workflow = None
                if state:
                    snapshot = state.state()
                    workflow = {k: snapshot[k] for k in ['stage', 'next_op']}
                    if hasattr(state, 'vlnv'):
                        workflow['vlnv'] = state.vlnv
                    identity = (session, json.dumps(request, sort_keys=True), str(exc))
                    self.structured_failures[identity] = self.structured_failures.get(identity, 0) + 1
                    repeat_count = self.structured_failures[identity]
                    if repeat_count >= 3:
                        # Before begin succeeds there is no IP workflow to lock.
                        # Preserve the prepared project and allow one valid begin
                        # after the model loads the skill instructions. Once a
                        # real workflow exists, the same breaker is terminal.
                        state.terminal_stop = bool(state.initialized)
                        reply = await state.finish_partial('Repeated unchanged workflow failure; ' + str(exc)[:160])
                        if state.terminal_stop:
                            # Ends this cell only; Vivado and the project stay open for other cells.
                            reply.update(next_op='begin', cell_stopped=True,
                                         cell_stopped_note='This cell stopped after repeated identical failures; '
                                                           'begin a different cell, or stop Vivado when the design is done.')
                            state.final_reply = reply
                        path = self.record({'arguments': arguments, 'structured_request': True, 'reply': reply, 'failed': False, 'failure_stop': True})
                        data = dict(exit_code=0, output=json.dumps(reply, separators=(',', ':')), evidence_path=str(path), used_mode='structured')
                        return types.CallToolResult(structuredContent=data, content=[types.TextContent(type='text', text=json.dumps(data))])
                    if repeat_count >= 2:
                        workflow['repeat_count'] = repeat_count
                        workflow['guidance'] = 'Change the request using next_op; one more identical failure finishes partial'
                return error_result('STRUCTURED_ERROR: ' + str(exc), path, workflow,
                                    getattr(exc, 'details', None))
        if session == 'ipdb':
            try:
                result = await asyncio.to_thread(self.database_request, command)
                path = self.record({'arguments': arguments, 'forwarded': False,
                                    'database_result': result, 'failed': False})
                data = {'exit_code': 0, 'output': json.dumps(result, separators=(',', ':')),
                        'evidence_path': str(path), 'used_mode': 'sqlite_readonly'}
                return types.CallToolResult(structuredContent=data,
                    content=[types.TextContent(type='text', text=json.dumps(data))])
            except Exception as exc:
                path = self.record({'arguments': arguments, 'forwarded': False,
                                    'database_error': str(exc), 'failed': True})
                return error_result('DATABASE_ERROR: ' + str(exc), path)
        identity = (session, command)
        if self.failures.get(identity, (None, 0))[1] >= 2:
            # Preserve the current design before refusing another unchanged failure.
            saved = await upstream.call_tool('vivado_execute', {
                'session_id': session,
                'command': 'if {[current_project -quiet] ne "" && [current_bd_design -quiet] ne ""} {save_bd_design}',
            })
            path = self.record({'arguments': arguments, 'forwarded': False,
                                'rejection': 'REPEATED_FAILURE', 'partial_save': saved.model_dump(mode='json')})
            return error_result('REPEATED_FAILURE: two identical failures; inspect evidence or finish partial', path)
        if not await tcl_complete(command):
            previous, count = self.failures.get(identity, (None, 0))
            self.failures[identity] = ('INCOMPLETE_TCL', count + 1 if previous == 'INCOMPLETE_TCL' else 1)
            path = self.record({'arguments': arguments, 'forwarded': False,
                                'rejection': 'INCOMPLETE_TCL'})
            return error_result('INCOMPLETE_TCL: close the unmatched brace, bracket, or quote; nothing ran', path)
        try:
            result = await upstream.call_tool('vivado_execute', arguments)
        except Exception as exc:
            result = types.CallToolResult(isError=True, content=[types.TextContent(type='text', text=str(exc))])
        data = payload(result)
        failed = result.isError or data.get('exit_code', 0) != 0 or bool(data.get('error_type'))
        if failed:
            signature = json.dumps([data.get('error_type'), data.get('exit_code'), data.get('output'),
                                    [b.text for b in result.content if b.type == 'text'] if not data else None], sort_keys=True)
            previous, count = self.failures.get(identity, (None, 0))
            self.failures[identity] = (signature, count + 1 if signature == previous else 1)
        else:
            self.failures.pop(identity, None)
        path = self.record({'arguments': arguments, 'forwarded': True,
                            'result': result.model_dump(mode='json'), 'failed': bool(failed)})
        output = data.get('output')
        if isinstance(output, str) and len(output) > self.output_limit:
            compact = dict(data, output=output[:300] + '\n[TRUNCATED; full output in evidence]\n' + output[-self.output_limit + 400:],
                           evidence_path=str(path), output_truncated=True)
            return types.CallToolResult(isError=result.isError, structuredContent=compact,
                                        content=[types.TextContent(type='text', text=json.dumps(compact))])
        return result


def structured_definitions(definitions, specs=None):
    """Minimal schemas for the JSON workflow; legacy upstream profile stays available."""
    specs = specs or {
        'vivado_start': ('Start one headless Vivado IPI session in the existing scratch directory; reuse it for every prompt of the design.', {
            'working_dir': {'type': 'string'}}, ['working_dir']),
        'vivado_execute': ('Run one JSON workflow operation on the returned session_id: begin, choose_ip, describe, select, waive, apply, finish or status. The skill documents command fields. The server constructs Tcl and verifies native readback.', {
            'session_id': {'type': 'string'}, 'command': {}}, ['command']),
        'vivado_stop': ('Stop Vivado only when the user is done with the design; finish already saved each cell.', {
            'session_id': {'type': 'string'}, 'close_vivado': {'type': 'boolean', 'default': True}}, ['session_id'])}
    # start/execute tolerate extra upstream fields (session_type, vivado_path, capture_log, verbosity,
    # ...) that a harness preparing or reading a shared session sends; the relay forces headless IPI
    # and builds its own commands, so they change nothing, and the agent-facing schema stays small.
    open_ended = {'vivado_start', 'vivado_execute'}
    return [types.Tool(name=name, description=description,
            inputSchema={'type': 'object', 'properties': properties, 'required': required,
                         'additionalProperties': name in open_ended})
            for name, (description, properties, required) in specs.items()]


async def serve(upstream_binary, evidence, database_config=None, profile='legacy',
                session_factory=None, tool_specs=None, extra_handlers=None, server_name='vivado-lite-guard',
                upstream_env=None, start_env=None):
    """tool_specs replaces the structured schemas; extra_handlers serve guard-owned tools named in them.
    upstream_env replaces the upstream server's environment; start_env is added to every vivado_start."""
    server = Server(server_name)
    guard = ExecutionGuard(evidence, database_config=database_config, session_factory=session_factory)
    extra_handlers = extra_handlers or {}
    sessions = set()
    # stderr stays outside the protocol stream; upstream inherits the harness's private env.
    with (Path(evidence) / 'upstream-stderr.log').open('a') as err:
        async with stdio_client(StdioServerParameters(command=upstream_binary, args=['--stdio'],
                                                      env=upstream_env or dict(os.environ)), errlog=err) as (read, write):
            async with ClientSession(read, write) as upstream:
                await upstream.initialize()
                definitions = [t for t in (await upstream.list_tools()).tools if t.name in TOOLS]
                if {t.name for t in definitions} != TOOLS:
                    raise RuntimeError('Required Vivado tool missing')
                if profile == 'structured':
                    definitions = structured_definitions(definitions, tool_specs)
                allowed = {t.name for t in definitions}

                @server.list_tools()
                async def list_tools():
                    return definitions

                @server.call_tool()
                async def call_tool(name, arguments):
                    if name not in allowed:
                        return error_result('TOOL_NOT_ALLOWED', guard.evidence)
                    if name in extra_handlers:
                        return await extra_handlers[name](guard, upstream, arguments)
                    if (profile == 'structured' and name == 'vivado_execute'
                            and not isinstance(arguments.get('command'), dict)
                            and not str(arguments.get('command', '')).lstrip().startswith('{')):
                        return error_result('STRUCTURED_PROFILE_REQUIRES_JSON', guard.evidence)
                    if profile == 'structured' and name == 'vivado_start':
                        arguments = dict(arguments, gui_mode=False, session_type='ipi')
                    if start_env and name == 'vivado_start':
                        arguments = dict(arguments, env={**(arguments.get('env') or {}), **start_env})
                    if name == 'vivado_stop' and arguments.get('session_id') in guard.closed:
                        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(
                            dict(status='success', session_id=arguments['session_id'], message='Vivado was already closed by finish')))])
                    result = await guard.execute(upstream, arguments) if name == 'vivado_execute' else await upstream.call_tool(name, arguments)
                    sid = payload(result).get('session_id')
                    if name == 'vivado_start' and sid:
                        sessions.add(sid)
                        guard.started[sid] = payload(result)['log_file']
                    if name == 'vivado_stop' and not result.isError:
                        sessions.discard(arguments.get('session_id'))
                        guard.structured_sessions.pop(arguments.get('session_id'), None)
                        guard.started.pop(arguments.get('session_id'), None)
                    return result

                try:
                    async with stdio_server() as (r, w):
                        await server.run(r, w, server.create_initialization_options())
                finally:
                    for sid in sessions - guard.closed:
                        try:
                            state = guard.structured_sessions.get(sid)
                            # Save a cell left mid-workflow; finish keeps Vivado open, so the relay
                            # going away is what stops it.
                            if state is not None and state.initialized and state.stage != 'finished':
                                state.literal_review_bypass = True
                                await state.request({'op': 'finish'})
                            result = await upstream.call_tool('vivado_stop', {'session_id': sid, 'close_vivado': True})
                            guard.record({'cleanup_stop': True, 'arguments': {'session_id': sid, 'close_vivado': True},
                                          'result': result.model_dump(mode='json')})
                        except Exception as exc:
                            guard.record({'cleanup_stop': True, 'session_id': sid, 'error': str(exc)})
                    guard.close_database()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--database-config', help='Optional trusted SQLite runtime config; enables read-only ipdb session')
    parser.add_argument('--profile', dest='tool_set', choices=['legacy', 'structured'], default='legacy')
    args = parser.parse_args()
    if not Path(args.evidence).is_absolute():
        parser.error('--evidence must be absolute and point into the scratch run')
    Path(args.evidence).mkdir(parents=True, exist_ok=True)
    asyncio.run(serve(args.upstream, args.evidence, args.database_config, args.tool_set))
