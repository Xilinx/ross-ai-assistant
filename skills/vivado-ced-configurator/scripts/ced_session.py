# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Two-phase JSON workflow for configurable example designs (CEDs).

Phase 1 retrieves a design from a commit-keyed live mirror of the store, sets
its top-level options (checked by the design's own updater code inside Vivado),
instantiates it and validates the block design. Phase 2 maps the remaining
requests onto generated cells through the lite IP engine, dry-runs them and
writes nothing until the separate approval tool restores the exact dry-run
result.
"""
import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
# Bundled engine (engine/README.md); CED_LITE_SKILL points at another engine root.
LITE = Path(os.environ.get('CED_LITE_SKILL', SKILL / 'engine')).resolve()
for path in [HERE, LITE / 'scripts']:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ced_index import Index, tcl_words, tokens  # noqa: E402
from ced_store import Mirror, release_branch  # noqa: E402
from structured_config import (StructuredSession, atom, dict_tcl, run_root, split_tcl, tdict,  # noqa: E402
                               tlist)  # noqa: E402

LIMITS = dict(begin=3, choose=3, set_options=8, proposals=3)
APPROVAL = re.compile(r'\b(without asking|ask(ing)? me|approv\w*|confirm\w*|permission)\b', re.I)
CELL_ROWS = 40




def parse_debug_info(text):
    """Vivado's evaluated parameter view from ced::debug_design_info."""
    params = []
    for block in text.split('* * * * * ')[1:]:
        if not block.startswith('Param'):
            continue
        row = {}
        for line in block.splitlines()[1:]:
            match = re.match(r'(Name|Format Type|Range Type|Range|Value|Enabled)\s*:\s?(.*)$', line) or \
                re.match(r'(display Name|Visible|Tooltip):\s?(.*)$', line)
            if match and match.group(1) not in row:
                row[match.group(1)] = match.group(2).rstrip()
        if row.get('Name'):
            params.append(row)
    return params


def norm(value):
    text = str(value).strip().lower()
    return {'1': 'true', 'yes': 'true', 'on': 'true', '0': 'false', 'no': 'false', 'off': 'false'}.get(text, text)


def squash(text):
    return re.sub(r'[\s_]+', ' ', str(text).strip().lower())


def overlap(a, b):
    return len(set(tokens(a)) & set(tokens(b)))


# Words every clock request and every frequency parameter share, so they cannot say
# WHICH clock is meant. Prefixes, so "clocking" and "frequencies" are caught too:
# "Clock the clocking wizard input" once named a PCIe reference clock through
# "clocking" alone.
GENERIC_CLOCK_WORDS = ('clock', 'frequenc', 'mhz', 'khz', 'ghz', 'hz')


def frequency_setter(pin, matches, requirement):
    """The one parameter of a driving cell that sets the frequency of its output pin, or None.

    The move to a driving cell names the pin it drives through, and a model then
    searches for that pin's name: ced-gen_H3 (Gemma-31B) was told ps_e drives
    clk_wiz_0/clk_in1 "through pl_clk0", described ps_e four times as
    "pl_clk0 frequency", "PL_CLK0_FREQ" and so on, and never reached
    PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ, whose name never says "clock".

    matches are the cell's frequency parameters whose current value equals the pin's
    frequency (cedcfg::frequency_source). On a PS that is dozens -- 43 on ps_e at
    100 MHz -- so the value only narrows the field. A parameter is named only when it
    carries a word of the REQUEST that says which clock ("PL" reaches PL0 on an
    UltraScale+ PS and, through the ranker's PL/FPGA alias, FPGA0 on a Zynq-7000),
    beyond the clock and frequency words every candidate shares. That is what keeps
    out PCW_CLK0_FREQ, which a Zynq-7000 matches on value, VALUE_SRC and name alike
    and which silently ignores a write. ACT_ readbacks are dropped, and a numbered
    family (PL0..PL3) is settled by the pin's own number. Anything short of one clear
    winner returns None: a wrong name here would send the model to write it.
    """
    import native_metadata as nm
    from ip_database import Database
    from parameter_contract import achieved_source

    def words(text):
        asked = nm.expand_compound_identifiers(text)
        return Database.identifier_terms(asked), nm.short_tokens(asked) - nm.GLUE, asked

    keys = [m['key'] for m in matches]
    wanted, short_wanted, asked = words(requirement)
    initials = nm.acronyms(asked) | short_wanted
    index = (re.findall(r'(\d+)$', pin) or [None])[0]
    scored = []
    for key in keys:
        if achieved_source(key, keys) is not None:
            continue
        names, short, _ = words(key)
        exact = {w for w in wanted if any(w.startswith(t) or t.startswith(w) for t in names)} | (initials & short)
        if not any(not w.startswith(GENERIC_CLOCK_WORDS) for w in exact):
            continue
        scored.append((len(exact), index is not None and index in re.findall(r'\d+', key), key))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    if not scored or (len(scored) > 1 and scored[1][:2] == scored[0][:2]):
        return None
    return scored[0][2]


class CellSession(StructuredSession):
    """Lite engine on one generated cell; native requests go through the parent CED session."""
    parent = None

    async def native(self, expression, prefix=''):
        return await self.parent.native(expression, prefix)

    async def close(self):
        raise ValueError('Cell sessions never close Vivado; finish the CED workflow')


class CEDSession:
    cache = None
    close = StructuredSession.close
    DETACH_POLL, DETACH_LIMIT, CALL_LIMIT = 30, 3600, 900
    # Longest a long op holds a reply. Claude Code abandons a tool call after
    # 60 s while the work keeps running; see await_running.
    REPLY_BUDGET = 40
    LONG_OPS = ('begin', 'choose_target', 'choose_ced', 'instantiate', 'port', 'assign', 'propose')

    async def native(self, expression, prefix=''):
        """One helper call with a base64 reply. Long commands that the upstream detaches are awaited, then read back."""
        import time
        from guard_mcp import payload
        self.native_serial += 1
        serial = self.native_serial
        # cedcfg::plain keeps Vivado objects out of the stored reply (see lib/ced.tcl).
        command = ('set __ipcfg_request_seq ' + str(serial) + '\nunset -nocomplain __ipcfg_reply\n' + prefix +
                   '\nset __ipcfg_reply [cedcfg::plain ' + expression + ']' +
                   '\nputs "__IPCFG__[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__ipcfg_reply]]"')
        started = time.monotonic()
        try:
            # The upstream detaches long commands after about two minutes; a call that never returns means Vivado died.
            result = await asyncio.wait_for(self.upstream.call_tool('vivado_execute', {'session_id': self.sid, 'command': command}),
                                            self.CALL_LIMIT)
        except asyncio.TimeoutError:
            path = self.guard.record({'structured_native': True, 'arguments': {'session_id': self.sid, 'command': command},
                                      'timeout_seconds': self.CALL_LIMIT})
            raise ValueError('NATIVE_ERROR: no answer from Vivado within ' + str(self.CALL_LIMIT) + ' s; it may have exited; evidence=' + str(path))
        data = payload(result)
        # The 2026.10 upstream hands a command off after 300 s with status
        # running and no error_type. Read as finished, ced-gen_H4's 5-minute
        # Versal build had no reply marker and became a failed build, and the
        # save that followed hit a busy Vivado, which then terminated.
        if data.get('error_type') == 'STILL_RUNNING_DETACHED' or data.get('status') == 'running':
            result, data = await self.await_detached(serial)
        path = self.guard.record({'structured_native': True, 'arguments': {'session_id': self.sid, 'command': command},
                                  'forwarded': True, 'elapsed_seconds': time.monotonic() - started,
                                  'result': result.model_dump(mode='json')})
        output = data.get('output', '') or ''
        # An empty payload is a real answer: an unmatched target or an empty
        # list encodes to nothing. Only a missing marker means the command died.
        matches = re.findall(r'__IPCFG__([A-Za-z0-9+/=]*)', output)
        if result.isError or data.get('exit_code', 0) != 0 or not matches:
            raise ValueError('NATIVE_ERROR: ' + (output or data.get('error', ''))[-700:] + '; evidence=' + str(path))
        return base64.b64decode(matches[-1]).decode()

    async def await_detached(self, serial):
        """Wait for a detached command, then read the reply it stored for this request."""
        from guard_mcp import payload
        waited, status = 0, {}
        while waited < self.DETACH_LIMIT:
            await asyncio.sleep(self.DETACH_POLL)
            waited += self.DETACH_POLL
            status = payload(await self.upstream.call_tool('vivado_status', {'session_id': self.sid}))
            if status.get('is_command_running') is False:
                break
        else:
            raise ValueError('NATIVE_ERROR: detached command still running after ' + str(waited) + ' s')
        read = ('if {[info exists __ipcfg_reply] && $__ipcfg_request_seq == ' + str(serial) + '} '
                '{puts "__IPCFG__[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__ipcfg_reply]]"} '
                'else {puts "__IPCFG_MISSING__ last_completed_code=[expr {[info exists errorInfo] ? [string range $errorInfo 0 400] : {}}]"}')
        result = await self.upstream.call_tool('vivado_execute', {'session_id': self.sid, 'command': read})
        data = payload(result)
        self.record(dict(op='detached_read', serial=serial, waited_seconds=waited, status=status, output=str(data.get('output', ''))[:800]))
        return result, data

    def __init__(self, guard, upstream, sid, log_file):
        self.guard, self.upstream, self.sid, self.log_file = guard, upstream, sid, log_file
        self.native_serial = 0
        self.stage = 'new'
        self.initialized = False
        self.counts = dict.fromkeys(LIMITS, 0)
        self.requirements, self.added_requirements = {}, []
        self.task = ''
        self.mirror = self.index = self.target = self.entry = None
        self.candidates, self.target_candidates, self.support = {}, {}, {}
        self.requested_target = ''
        self.preloaded = set()
        self.options, self.option_selections = {}, {}
        self.context_waived = []
        self.left_bankless = set()
        self.deferred, self.waived = [], {}
        # (requirement, option) -> the value the design forced instead. A later
        # selection equal to that value is the design's answer, not the one the
        # requirement asked for; see vetoed().
        self.design_vetoes = {}
        # (requirement, option) -> the value the requirement asked for when vetoed.
        self.veto_requests = {}
        self.evaluation = None
        self.instantiate_warned = False
        self.running, self.build_sent = None, False
        self.lock = asyncio.Lock()
        self.phase1 = None
        self.cells, self.assignments, self.subs = {}, {}, {}
        self.helper_warned, self.clock_warned = set(), set()
        self.proposal = None
        self.dry_run_written = False
        self.phase2 = dict(status='not_needed')
        self.final_reply = None
        self.pre_approved = False
        self.finish_refused = False
        self.unbuildable = None
        # Port (references/port.md): allowed only when the user asked for it at begin.
        self.port_allowed, self.port_plan, self.port = False, None, None

    def unbuilt_report(self, entry):
        """What to report when the requested design cannot be built; finish keeps these values."""
        self.unbuildable = entry
        return dict(ced=entry['name'], phase1_options={}, phase2_status='not_needed', full=False)

    def report(self, full=False):
        """The values a final answer reports, so they never need to be inferred."""
        named = self.entry or self.unbuildable
        options = {}
        if self.phase1:
            options = self.phase1.get('options_effective') or self.phase1['options_passed']
        report = dict(ced=named['name'] if named else None, phase1_options=options,
                      phase2_status=self.phase2['status'], full=full)
        if self.port:
            report['port'] = self.port
        return report

    # ----- shared helpers -------------------------------------------------
    def requirement_ids(self):
        ids = []
        for rid in self.requirements:
            ids.extend(self.assignments.get(rid, {}).get('ids', [rid]))
        return ids

    def resolved_phase1(self):
        # A requirement the design vetoed is already answered (unresolved), and
        # refuse_vetoed blocks defer and waive for it. Pausing instantiate on it
        # asked for a move that did not exist: ced-gen_H4's Haiku run finished
        # without building rather than instantiate again.
        return ({rid for rid, _ in self.option_selections} | set(self.deferred) | set(self.waived)
                | {rid for rid, _ in self.design_vetoes})

    def pending_phase1(self):
        return [dict(id=k, text=v) for k, v in self.requirements.items() if k not in self.resolved_phase1()]

    def base_requirement(self, rid):
        """The deferred ID assign accepts, given a per-cell derived ID."""
        return next((key for key, row in self.assignments.items() if rid in row['ids']), rid)

    def sub_for(self, rid):
        for row in self.assignments.values():
            for derived, cell in zip(row['ids'], row['cells']):
                if derived == rid:
                    return self.subs.get(cell)
        return None

    def unassigned(self):
        return [rid for rid in self.deferred if rid not in self.assignments and rid not in self.waived]

    def selected_cells(self, rid):
        """The cells where this requirement already picked a parameter."""
        row = self.assignments.get(rid) or dict(ids=[], cells=[])
        return [path for derived, path in zip(row['ids'], row['cells'])
                if path in self.subs and any(r == derived for r, _ in self.subs[path].selections)]

    def release(self, rid):
        """Forget a cell choice so the requirement can be assigned elsewhere."""
        row = self.assignments.pop(rid)
        for derived, path in zip(row['ids'], row['cells']):
            sub = self.subs.get(path)
            if sub is None:
                continue
            for store in (sub.requirements, sub.describe_counts, sub.exhausted, sub.waived):
                store.pop(derived, None)
            if not sub.requirements:
                self.subs.pop(path, None)

    def narrow(self, rid, drop_paths):
        """Drop some cells from a split assignment, keeping its selections on the rest.

        ced-gen_H3: r5 split onto ps_e and clk_wiz_0. clk_wiz_0 has no
        parameter bank, so it can hold a value only by having it flow in from
        the cell that drives it -- exactly what the proposal's side_effects
        showed happening automatically once ps_e was set. A bank-blocked half
        with no selection of its own has nothing left to wait for once its
        sibling is settled; this clears it instead of leaving it stuck pending
        forever, which blocked propose and read as unresolved at finish.
        """
        row = self.assignments[rid]
        for derived, path in list(zip(row['ids'], row['cells'])):
            if path not in drop_paths:
                continue
            sub = self.subs.get(path)
            if sub is not None:
                for store in (sub.requirements, sub.describe_counts, sub.exhausted, sub.waived):
                    store.pop(derived, None)
                if not sub.requirements:
                    self.subs.pop(path, None)
            i = row['ids'].index(derived)
            row['ids'].pop(i)
            row['cells'].pop(i)

    def phase2_next(self):
        if self.unassigned():
            return 'assign'
        # A cell that published no parameter bank AND holds only values it
        # receives refuses every write, so a requirement still pending on one is
        # not waiting on describe or on propose: it is waiting to be moved to the
        # cell that drives this one. ced-gen_H3's 26B run spent its describe
        # budget on clk_wiz_0, read next_op propose, and proposed nothing.
        # Keyed on bank_blocks_writes, not bank_missing: a bank-less cell that
        # still holds a writable row -- a clk_wiz output frequency on 2026.2,
        # where the customizer fails for every clk_wiz -- is the right cell, and
        # answering 'assign' there sends the model away from the only cell that
        # can satisfy the requirement.
        if any(sub.pending() and sub.bank_blocks_writes() for sub in self.subs.values()):
            return 'assign'
        for sub in self.subs.values():
            if any(p['id'] not in sub.exhausted and sub.describe_counts.get(p['id'], 0) < sub.describe_limit(p['id'])
                   for p in sub.pending()):
                return sub.state()['next_op'] if sub.state()['next_op'] in ('describe', 'select') else 'select'
        return 'propose'

    def state(self, **extra):
        next_op = {'new': 'begin', 'choosing_target': 'choose_target', 'choosing_ced': 'choose_ced',
                   'options': 'set_options' if self.pending_phase1() else 'instantiate',
                   'instantiating': 'status',
                   'phase2': self.phase2_next() if self.stage == 'phase2' else None,
                   'port_ready': 'port', 'phase1_done': 'finish', 'phase2_applied': 'finish',
                   'awaiting_approval': 'ced_apply_phase2' if self.pre_approved else 'ask_user',
                   'failed': 'finish', 'finished': 'stop', 'close_failed': 'vivado_stop'}.get(self.stage)
        result = dict(stage=self.stage, next_op=next_op)
        if self.stage in ('options',) and self.pending_phase1():
            result['unresolved'] = self.pending_phase1()
        result.update(extra)
        return result

    async def run(self, func, *args):
        return await asyncio.to_thread(func, *args)

    def record(self, data):
        return self.guard.record(dict(ced_workflow=True, **data))

    @property
    def root(self):
        return run_root(self.log_file)

    # ----- request dispatch -----------------------------------------------
    SCHEMAS = {'begin': {'task', 'target', 'requirements', 'phase2_approval', 'port'}, 'choose_target': {'target_id'},
               'choose_ced': {'candidate_id', 'name'}, 'set_options': {'selections'},
               'defer': {'requirement_id', 'requirement_ids'}, 'waive': {'requirement_id', 'reason'},
               'instantiate': set(), 'port': set(), 'cells': {'offset', 'query'}, 'assign': {'assignments'},
               'describe': {'requirement_id', 'query'}, 'select': {'selections'}, 'propose': set(),
               'finish': set(), 'status': set()}

    async def request(self, request):
        # "seq" exists for the stock MCP's duplicate skip, which ced_bridge.py enforces;
        # the relay in examples/ never hits that skip, so a model following the skill
        # may still send one there.
        request = {k: v for k, v in request.items() if k != 'seq'}
        op = request.get('op')
        if op not in self.SCHEMAS or set(request) - self.SCHEMAS[op] - {'op'}:
            raise ValueError('Use ' + ', '.join(self.SCHEMAS) + ' with documented fields')
        if op == 'status':
            if self.running and not self.running['seen']:
                return await self.await_running()
            return self.state(requirements=self.requirements)
        # A client that gives up on a long call can resend it while the first
        # is still running; ops run one at a time so neither sees half a change.
        async with self.lock:
            return await self.dispatch(op, request)

    async def dispatch(self, op, request):
        if self.stage in ('finished', 'close_failed'):
            return self.final_reply or self.state()
        if self.running and not self.running['seen']:
            if op == self.running['op']:
                return await self.await_running()
            if not self.running['task'].done():
                raise ValueError('Vivado is still working on ' + self.running['op'] + '; call {"op":"status"} until it finishes')
        # ced-gen_H5 (Gemma-26B): with a proposal approved in advance waiting, the model
        # kept calling select and was told "use set_options for CED options";
        # three refusals ended the session and nothing was applied.
        if self.stage == 'awaiting_approval' and op in ('select', 'describe', 'assign', 'waive', 'defer', 'set_options',
                                                         'instantiate', 'propose'):
            step = ('call the ced_apply_phase2 tool with this session_id and proposal_id ' + self.proposal['id'] +
                    ', then finish') if self.pre_approved else \
                   ('stop and ask the user whether to apply it; if they decline, finish')
            raise ValueError('Proposal ' + self.proposal['id'] + ' is awaiting approval, so ' + op + ' is closed: the '
                             'selections are fixed in it. Next: ' + step + '.')
        # ced-gen_M5 (Haiku): every Phase 2 write was rejected by the board
        # interface, which is the graded answer. The model read the refusal of a
        # further describe as a fault in the session rather than the end of
        # Phase 2, finished, and then rebuilt the whole task in a new project.
        if self.stage == 'phase2_applied' and op in ('set_options', 'defer', 'waive', 'instantiate',
                                                     'assign', 'describe', 'select', 'propose'):
            raise ValueError('Phase 2 is over with phase2_status ' + self.phase2['status'] + ', so ' + op + ' cannot '
                             'change the design any further. This is the outcome, not a fault: a write Vivado rejected '
                             'here is rejected in any session. Next: finish, then report phase2_status ' +
                             self.phase2['status'] + '.')
        if op == 'finish':
            self.refuse_premature_finish()
            # Saving and closing a large Versal design outlasted 60 s (ced-gen_H4),
            # and a model without the report invents one.
            return await self.start(op, self.finish())
        handler = getattr(self, 'op_' + op)
        if op in self.LONG_OPS:
            return await self.start(op, handler(request))
        return await handler(request)

    async def start(self, op, work):
        self.running = dict(op=op, task=asyncio.create_task(work), started=time.monotonic(), seen=False)
        return await self.await_running()

    async def await_running(self):
        """The running op's reply if it finishes within REPLY_BUDGET, else a progress reply.

        ced-gen_M6's Haiku run: Claude Code abandoned the first instantiate at
        exactly 60.0 s while Vivado kept building, status still read stage
        options because the stage moved only when the build returned, and the
        retried instantiate passed the stage check and queued a second build
        behind the first. That one failed with [Vivado_Tcl 4-481] Design is not
        empty, and the model reported a correctly built design as failed (M3
        and H4 the same way). M3's next run lost begin to the same limit, and
        M1's first assign. A long op now runs as its own task: a reply never
        waits past the budget, and status or a repeat of the same op waits on
        that task instead of starting another. asyncio.wait does not cancel
        the task, so an abandoned call cannot stop the work either.
        """
        run = self.running
        done, _ = await asyncio.wait({run['task']}, timeout=self.REPLY_BUDGET)
        if not done:
            return self.state(next_op='status', running=run['op'], running_seconds=round(time.monotonic() - run['started']),
                              note='Vivado is still working on ' + run['op'] + '. Call {"op":"status"} to keep waiting; '
                                   'do not repeat the call.')
        run['seen'] = True
        return run['task'].result()

    # ----- phase 1 ----------------------------------------------------------
    async def op_begin(self, request):
        if self.stage not in ('new', 'choosing_target'):
            raise ValueError('Already begun; follow next_op')
        texts, task, target = request.get('requirements'), request.get('task', ''), request.get('target', '')
        if not isinstance(texts, list) or not 1 <= len(texts) <= 24 or any(not isinstance(t, str) or not t.strip() or len(t) > 600 for t in texts):
            raise ValueError('requirements must hold 1..24 nonempty feature strings from the task')
        if not isinstance(task, str) or not task.strip() or len(task) > 6000:
            raise ValueError('task must be the original request text')
        if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.:+ -]{2,64}', target.strip()):
            raise ValueError('target must be a board name (e.g. zcu102) or an exact part name')
        approval = request.get('phase2_approval', 'ask')
        if approval not in ('ask', 'pre_approved'):
            raise ValueError('phase2_approval is "pre_approved" only when the user explicitly said to apply changes without asking; otherwise "ask"')
        # Guidance only: the approval tool itself stays host-gated in interactive use.
        self.pre_approved = approval == 'pre_approved'
        port = request.get('port', 'never')
        if port not in ('never', 'allowed'):
            raise ValueError('port is "allowed" only when the user explicitly asked to port, retarget or adapt a design '
                             'to a board it does not support; otherwise leave it out')
        self.port_allowed = port == 'allowed'
        prior = self.earlier_report(task)
        if prior:
            self.stage = 'finished'
            self.final_reply = dict(stage='finished', next_op='stop', report=prior['report'],
                                    requirements=prior['requirements'],
                                    note='This task already ran to completion in this workspace and the design is on '
                                         'disk; beginning again would only build the same design a second time. These '
                                         'are its results. Report them and stop.')
            return self.final_reply
        self.counts['begin'] += 1
        self.requested_target = target or ''
        if self.counts['begin'] > LIMITS['begin']:
            return await self.finish('Begin retry limit reached')
        if not self.requirements:
            from intent_contract import missing_clauses
            # Keep only omitted clauses that add words beyond the given requirements and target.
            # Sentences about approval or asking are instructions to the agent, not design features.
            design_text = ' '.join(s for s in re.split(r'(?<=[.!?])\s+', task) if not APPROVAL.search(s))
            known = set(tokens(' '.join(texts) + ' ' + target))
            additions = [a for a in missing_clauses(design_text, texts) if a not in texts and set(tokens(a)) - known][:6]
            self.requirements = {f'r{i + 1}': t for i, t in enumerate(list(texts) + additions)}
            self.added_requirements, self.task = additions, task
        if not self.initialized:
            lib = SKILL / 'lib/ced.tcl'
            prefix = 'source -notrace ' + atom(lib) + '\nset __begin [cedcfg::begin ' + atom(self.log_file) + ']\n'
            info = dict_tcl(await self.native('[dict create version [version] '
                                              'bundled [file join $::env(XILINX_VIVADO) data xhub ced XilinxCEDStore]]', prefix))
            build = re.search(r'SW Build (\d+)', info['version'])
            if not build:
                raise ValueError('Cannot establish installed Vivado SW build')
            self.build, self.bundled = build[1], info['bundled']
            self.branch = release_branch(info['version'])
            self.initialized = True
        rows = [dict_tcl(r) for r in split_tcl(await self.native('[cedcfg::targets ' + atom(target.strip()) + ']'))]
        if not rows:
            return await self.target_not_found(target)
        if len(rows) > 1:
            self.stage = 'choosing_target'
            self.target_candidates = {f't{i + 1}': r for i, r in enumerate(rows)}
            return self.state(target_candidates=[dict(id=k, board=r.get('board', ''), name=r['name'], display=r.get('display', ''), part=r['part'])
                                                 for k, r in self.target_candidates.items()])
        return await self.open_target(rows[0])

    async def target_not_found(self, target):
        """Name the design the request describes even when its part is not installed.

        The store index needs no project, so a target this Vivado cannot
        resolve still yields an identification. Nothing is created, and finish
        keeps these values rather than reporting no design at all.
        """
        extra = {}
        try:
            self.mirror = Mirror(self.cache, self.branch, bundled=self.bundled)
            info = await self.run(self.mirror.resolve)
            self.index = await self.run(Index, info['path'], self.branch)
            ranked = self.index.search(self.task + ' ' + ' '.join(self.requirements.values()), limit=8,
                                       exclude=[self.requested_target])
            ranked = self.fewest_unasked(ranked)[:3]
            if ranked:
                extra = dict(report=self.unbuilt_report(ranked[0][1]),
                             store_matches=[dict(name=e['name'], display=e['display']) for _, e in ranked])
        except Exception as exc:
            extra = dict(store_lookup_failed=str(exc)[:200])
        self.record(dict(op='target_not_found', target=target, **extra))
        note = 'No installed board or part matches, so nothing can be built here. '
        if extra.get('store_matches'):
            note += ('The store names the closest designs above; finish and report the first one as unavailable on this '
                     'Vivado install, without substituting a design or a target. ')
        note += 'To build instead, retry begin with an installed board short name (for example zcu102, vck190, kv260).'
        return self.state(target_not_found=target, note=note,
                          **dict(extra, next_op='finish' if extra.get('store_matches') else 'begin'))

    def fewest_unasked(self, ranked):
        """Search hits reordered by how many of a design's distinctive name words the
        request does not contain; ties keep the search order.

        Nothing is built for a part this Vivado lacks, so the first hit is the design
        the report names. ced-gen_H1 asks for the "CPM6 DMA example"; on Vivado
        2026.2's bundled store the text search put "Versal CPM6 CXL EP Bridge
        Design" first (four words the request never says) over "Versal CPM6 DMA
        Design" (one).
        """
        stem = lambda words: {w[:-1] if w.endswith('s') and len(w) > 3 else w for w in words}
        asked = stem(tokens(self.task + ' ' + ' '.join(self.requirements.values())))
        unasked = lambda entry: len(stem(tokens(entry['display'])) - self.GENERIC - asked)
        return [row for _, row in sorted(enumerate(ranked), key=lambda item: (unasked(item[1][1]), item[0]))]

    async def op_choose_target(self, request):
        row = self.target_candidates.get(request.get('target_id'))
        if self.stage != 'choosing_target' or row is None:
            raise ValueError('Choose a returned target ID while choosing_target')
        return await self.open_target(row)

    async def open_target(self, row):
        self.target = row
        board_part = row['name'] if row['kind'] == 'board' else ''
        await self.native('[cedcfg::project ' + atom(row['part']) + ' ' + atom(board_part) + ']')
        self.mirror = Mirror(self.cache, self.branch, bundled=self.bundled)
        info = await self.run(self.mirror.resolve)
        self.index = await self.run(Index, info['path'], self.branch)
        query = self.task + ' ' + ' '.join(self.requirements.values())
        ranked = self.index.search(query, limit=8,
                                   exclude=[self.requested_target, row['part'], row.get('display', ''), board_part])
        if info['source'] != 'bundled_offline':
            for _, entry in ranked:
                await self.run(self.mirror.add_design, entry['root'])
                self.preloaded.add(entry['vlnv'])
        await self.native('[cedcfg::load_repo ' + atom(Path(info['path']) / 'ced') + ']')
        rows = [dict_tcl(r) for r in split_tcl(await self.native('[cedcfg::support ' + tlist([e['vlnv'] for e in self.index.entries]) + ']'))]
        self.support = {r['vlnv']: r for r in rows}
        live = lambda e: self.support.get(e['vlnv'], {}).get('present') == '1'
        ok = lambda e: live(e) and self.support[e['vlnv']].get('supported') != '0'
        ordered = [e for _, e in ranked if ok(e)][:5]
        unsupported = [e for _, e in ranked[:4] if live(e) and not ok(e)][:2]
        hidden = [e for _, e in ranked[:4] if not live(e)][:2]
        self.candidates = {}
        for entry in ordered + unsupported + hidden:
            self.candidates['c' + str(len(self.candidates) + 1)] = entry
        self.stage = 'choosing_ced'
        shown = {e['vlnv'] for e in self.candidates.values()}
        others = sorted(e['display'] for e in self.index.entries if ok(e) and e['vlnv'] not in shown)
        self.record(dict(op='open_target', mirror=info, ranked=[(s, e['vlnv']) for s, e in ranked], support=rows))
        # ced-gen_H6 (Zynq-7000 preset on the ZCU102): Haiku read the card
        # marked supported=false and answered without choose_ced or finish, so
        # no report existed and it invented phase2_status. When every design the
        # request names is unbuildable here, choose_ced would only refuse, so
        # the answer and the report belong in this reply.
        named = self.named_designs()
        port_named, plan = None, {}
        if named and not any(ok(e) for e in named) and self.port_allowed:
            for candidate in [e for e in named if live(e)]:
                plan = await self.port_candidate(candidate)
                if plan.get('found') == '1':
                    port_named = candidate
                    break
        if port_named:
            return self.state(target=self.target_summary(), store=self.store_summary(),
                              ced_candidates=[self.card(cid, e) for cid, e in self.candidates.items()],
                              requested_design=port_named['display'], next_op='choose_ced',
                              port_possible=dict(reference_board=plan['board'], why=plan['why']),
                              note='The request names ' + port_named['display'] + ', which does not list ' + self.target['name'] +
                                   '. The user asked for a port, so choose it: {"op":"choose_ced","name":"' + port_named['display'] +
                                   '"}. It will be built on ' + plan['board'] + ' and carried to ' + self.target['name'] + '.')
        if named and not any(ok(e) for e in named):
            row = self.support.get(named[0]['vlnv'], {})
            if self.port_allowed and plan.get('reason'):
                self.port = dict(refused=plan['reason'])
            return self.state(target=self.target_summary(), store=self.store_summary(),
                              ced_candidates=[self.card(cid, e) for cid, e in self.candidates.items()],
                              **({'port_refused': plan['reason']} if self.port_allowed and plan.get('reason') else {}),
                              requested_design=named[0]['display'], report=self.unbuilt_report(named[0]),
                              example_boards=split_tcl(row.get('boards', ''))[:6], next_op='finish',
                              note='The request names ' + named[0]['display'] + ', which cannot be built on ' + self.target['name'] +
                                   '. Nothing was created. Finish and copy report; never substitute a different design or target.')
        return self.state(target=self.target_summary(), store=self.store_summary(),
                          ced_candidates=[self.card(cid, e) for cid, e in self.candidates.items()],
                          others_supported_on_target=others[:40],
                          note='Cards are ranked by text match, not suitability. supported=false or available=false cannot be '
                               'built on this target; choose one only to report that. Another supported design may be chosen by "name".')

    def target_summary(self):
        return {k: self.target[k] for k in ('kind', 'name', 'part', 'display') if self.target.get(k)}

    def store_summary(self):
        info = self.mirror.info()
        return dict(branch=info['branch'], commit=(info['sha'] or '')[:12], source=info['source'],
                    **({'notes': info['notes']} if info.get('notes') else {}))

    def part_only(self, entry):
        """True when this design declares parts but no boards, on a board target.

        Two CEDs often share a display name and all their options, one built
        for boards and one for parts (Versal Extensible Embedded Platform and
        its "(Part based)" twin). Only the kind mismatch separates them, so say
        which it is rather than reporting a bare unsupported target.
        """
        return (self.target.get('kind') == 'board'
                and self.support.get(entry['vlnv'], {}).get('part_only') == '1')

    def board_twin(self, entry):
        """The twin of a part-based design that does take this board, if there is one.

        Saying "its board twin usually carries the same display name without
        '(Part based)'" describes the twin without naming it. ced-gen_M3's 26B
        run read that, reported the design could not be built, and finished
        with ced ext_platform_part and full false -- the board twin was in the
        same candidate list the whole time. Match on the display name with the
        parenthesised qualifier stripped, and only return one the live support
        table says is buildable here, so this never invents a substitute.
        """
        # Only a "(Part based)" qualifier marks a twin. Three store pairs share
        # a stem without being twins -- CPU (HDL) and CPU (Synthesized) are
        # different implementations, and the "(Spartan UltraScale+)" presets are
        # a different device family. Offering one of those as the same design on
        # another target would be exactly the silent substitution choose_ced
        # refuses everywhere else.
        head, _, qualifier = entry['display'].partition('(')
        if 'part' not in qualifier.lower():
            return None
        stem = head.strip().lower()
        for other in self.index.entries:
            if other['vlnv'] == entry['vlnv'] or other['display'].split('(')[0].strip().lower() != stem:
                continue
            support = self.support.get(other['vlnv'], {})
            if support.get('present') == '1' and support.get('supported') != '0':
                return other
        return None

    def card(self, cid, entry):
        support = self.support.get(entry['vlnv'], {})
        options = '; '.join(o['name'] + '=' + '|'.join(c['value'] for c in o['choices'][:5]) if o['choices']
                            else o['name'] + ' (text)' for o in entry['options'])[:220]
        card = dict(id=cid, display=entry['display'], name=entry['name'],
                    summary=entry['description'][:220], options=options or 'none')
        if support.get('present') != '1':
            card.update(available=False, reason='Not loadable in this Vivado: its supported parts are not installed')
        else:
            card['supported'] = {'1': True, '0': False}.get(support.get('supported'), 'unknown')
            if card['supported'] is False:
                card['example_boards'] = split_tcl(support.get('boards', ''))[:4]
                if self.part_only(entry):
                    card['reason'] = 'Part-based variant: it takes a part, not the board ' + self.target['name']
        return card

    async def op_choose_ced(self, request):
        if self.stage != 'choosing_ced':
            raise ValueError('Choose a design while choosing_ced; follow next_op')
        entry = self.candidates.get(request.get('candidate_id')) if request.get('candidate_id') else \
            self.index.by_name(str(request.get('name', '')))
        if entry is None:
            raise ValueError('Use a returned candidate_id, or "name" with a display name from others_supported_on_target')
        self.counts['choose'] += 1
        if self.counts['choose'] > LIMITS['choose']:
            return await self.finish('Design choice retry limit reached')
        support = self.support.get(entry['vlnv'], {})
        if support.get('present') != '1':
            return self.state(refused=entry['display'], report=self.unbuilt_report(entry), reason='This design is not loadable in the installed Vivado (no supported part installed). '
                              'Choose another design, or finish and report it as unavailable.')
        port_refused = None
        if support.get('supported') == '0' and self.port_allowed:
            plan = await self.port_candidate(entry)
            if plan.get('found') == '1':
                # Example-design repositories belong to the Vivado session, not the
                # project, so they stay loaded across the swap (adding the same one
                # again fails with Vivado_Tcl 4-505 Invalid repo; ced-port-haiku3).
                await self.native('[cedcfg::port_reference ' + atom(plan['board']) + ' ' + atom(plan['part']) + ']')
                self.port_plan = plan
                support = dict(support, supported='1')
            else:
                port_refused = plan.get('reason', 'no reference board')
        if support.get('supported') == '0':
            twin = self.board_twin(entry) if self.part_only(entry) else None
            why = ('This design is the part-based variant: it takes a part, not the board ' + self.target['name'] + '.') \
                if self.part_only(entry) else 'This design does not support target ' + self.target['name'] + '.'
            if twin:
                # Name the twin and the call that takes it. The same design on
                # the same board is not a substitution, and a description the
                # model has to resolve itself is one it finishes without.
                why += (' Its board twin is ' + twin['display'] + ', which does build on ' + self.target['name'] + ': '
                        '{"op":"choose_ced","name":"' + twin['display'] + '"}.')
            elif self.part_only(entry):
                why += ' Its board twin usually carries the same display name without "(Part based)".'
            if port_refused:
                self.port = dict(refused=port_refused)
            return self.state(refused=entry['display'], report=self.unbuilt_report(entry), example_boards=split_tcl(support.get('boards', ''))[:6],
                              **({'port_refused': port_refused} if port_refused else {}),
                              reason=why + ' Nothing was created. '
                              'Choose a supported design that the task describes, or finish and report the mismatch; never change the user\'s target yourself.')
        named = self.named_designs()
        blocked = [e for e in named if self.support.get(e['vlnv'], {}).get('present') != '1'
                   or self.support[e['vlnv']].get('supported') == '0']
        if blocked and entry not in named:
            row = self.support.get(blocked[0]['vlnv'], {})
            return self.state(refused=entry['display'], requested_design=blocked[0]['display'], report=self.unbuilt_report(blocked[0]),
                              example_boards=split_tcl(row.get('boards', ''))[:6],
                              reason='The request names ' + blocked[0]['display'] + ', which cannot be built on ' + self.target['name'] +
                                     '. Choosing a different design would silently change the request. Nothing was created; '
                                     'finish and report that the requested design does not support this target.')
        if self.mirror.source != 'bundled_offline' and entry['vlnv'] not in self.preloaded:
            # Vivado loads complete folders only; a repo path must contain design folders.
            folder = await self.run(self.mirror.add_design, entry['root'])
            repo = self.root / 'ced-picks' / entry['name']
            await self.run(lambda: shutil.copytree(folder, repo / folder.name, dirs_exist_ok=True))
            await self.native('[cedcfg::load_repo ' + atom(repo) + ']')
        support = dict_tcl(split_tcl(await self.native('[cedcfg::support ' + tlist([entry['vlnv']]) + ']'))[0])
        self.entry = entry
        self.repo_directory = support.get('repo', '')
        native = dict_tcl(await self.native('[cedcfg::options ' + atom(entry['vlnv']) + ']'))
        text = Path(native['file']).read_text(errors='replace')
        self.board_part, self.part = native.get('board_part', ''), native.get('part', '')
        self.options = self.option_table(parse_debug_info(text), entry)
        self.stage = 'options'
        self.record(dict(op='choose_ced', vlnv=entry['vlnv'], options=self.options, debug_file=native['file']))
        hints = {}
        for rid, req in self.requirements.items():
            best = max(((overlap(req, c['label'] + ' ' + c['value'].replace('_', ' ')) + overlap(req, row['name'].replace('_', ' ')), oid, c['value'])
                        for oid, row in self.options.items() for c in row['choices']), default=(0, None, None))
            if best[0] >= 2:
                hints[rid] = best[1] + '=' + best[2]
        return self.state(ced=dict(name=entry['name'], vlnv=entry['vlnv'], display=entry['display'],
                                   source='live_mirror' if self.mirror.source != 'bundled_offline' and str(self.mirror.path) in self.repo_directory else 'bundled'),
                          options=[self.option_view(oid, row) for oid, row in self.options.items()],
                          requirements=[dict(id=k, text=v) for k, v in self.requirements.items()],
                          **({'option_hints': hints} if hints else {}),
                          **({'port': dict(reference_board=self.port_plan['board'], target=self.target['name'], why=self.port_plan['why'])}
                             if self.port_plan else {}),
                          note=('This design does not list ' + self.target['name'] + ': instantiate builds it on ' + self.port_plan['board'] +
                                ', then the port step carries it across. ' if self.port_plan else '') +
                               'Map each requirement: set_options for a CED option, defer for a setting inside the generated design '
                               '(Phase 2), or waive context the design or target already covers. An empty options list means the '
                               'design has no top-level options.')

    GENERIC = {'design', 'designs', 'preset', 'presets', 'example', 'part', 'based'}
    # Acronyms in store design names and what a request may say instead. The
    # description rule in names_only_context finds the ones the store spells
    # out itself; this list covers the known ones it does not.
    NAME_SYNONYMS = {'tmr': 'triple modular redundancy', 'bmd': 'bus mastering dma'}

    def names_only_context(self, rid):
        """True when a requirement only names the chosen design and the target.

        Gemma-26B, stage1 (tree 9454863c): "Versal processor performance example"
        (E6) and the task clause begin added, "VCK190 extensible embedded
        platform with DDR but no LPDDR" (M3), were left unmapped, instantiate
        paused, the model instantiated again, and both ended unresolved with
        full false. In E4 it deferred the added "Kria KV260 starter kit design
        using the BRAM" into Phase 2, which then had nothing to do and failed.
        Every word of each named the design, the board or another requirement.
        A clause begin added may lean on the other requirements' words; one the
        model wrote must be covered by the design and target alone, so a real
        request is never waived for sharing words with another. Only consulted
        for a requirement still unmapped at instantiate or offered to defer: one
        the model maps to an option ("processor preset" -> Preset) is its call.
        """
        target = ' '.join(str(self.target.get(k, '')) for k in ('name', 'board', 'display', 'part')) + ' ' + self.requested_target
        known = set(tokens(self.entry['display'] + ' ' + self.entry['name'].replace('_', ' ') + ' ' + target)) | self.GENERIC
        # ced-gen_H5/H5w (Gemma-26B) deferred "Triple-modular-redundant classic
        # MicroBlaze" for the "TMR Microblaze Example Design" and wedged Phase 2.
        # An acronym the name uses and the description spells out ("TMR (Triple
        # Modular Redundancy)") is the name; "classic" only says not MicroBlaze V.
        for acronym, expansion in re.findall(r'\b([A-Z]{2,})\s*\(([^)]{3,80})\)', self.entry.get('description', '')):
            if acronym.lower() in known:
                known |= set(tokens(expansion))
        for acronym, expansion in self.NAME_SYNONYMS.items():
            if acronym in known:
                known |= set(tokens(expansion))
        if 'microblaze' in known and not re.search(r'\bV\b', self.entry['display']):
            known.add('classic')
        stem = lambda words: {w[:6] for w in words}
        text = self.requirements[rid]
        if text in self.added_requirements:
            known |= set(tokens(' '.join(t for r, t in self.requirements.items() if r != rid)))
        words = set(tokens(text))
        return bool(words) and stem(words) <= stem(known)

    def named_designs(self):
        """Designs whose distinctive display-name words all appear in the original request."""
        stem = lambda words: {w[:-1] if w.endswith('s') and len(w) > 3 else w for w in words}
        task = stem(tokens(self.task))
        named = []
        for entry in self.index.entries:
            words = stem(tokens(entry['display'])) - self.GENERIC
            if words and words <= task:
                named.append((len(words), entry))
        # Most specific first: a request naming "Versal MicroBlaze V Design Presets"
        # also contains every word of "MicroBlaze Design Presets", and reporting the
        # shorter name as the one requested misreads it (ced-port-haiku2). Words alone
        # can tie: the tokenizer drops the one-letter "V", so "Versal MicroBlaze
        # Design Presets" matches as well; a display name the request spells out
        # verbatim wins, then the longer name.
        text = ' '.join(self.task.lower().split())
        verbatim = lambda e: ' '.join(e['display'].lower().split()) in text
        return [entry for _, entry in sorted(named, key=lambda row: (-row[0], not verbatim(row[1]), -len(row[1]['display'])))]

    def option_table(self, params, entry):
        static = {o['name']: o for o in entry['options']}
        table = {}
        for row in params:
            name = row['Name']
            choices = []
            if row.get('Range Type', '').lower() == 'discrete':
                for element in tcl_words(row.get('Range', ''))[0]:
                    parts = element.split(None, 1)
                    if parts:
                        choices.append(dict(value=parts[0], label=parts[1] if len(parts) > 1 else parts[0]))
            source = static.get(name, {})
            if not choices and source.get('choices'):
                choices = source['choices']
            kind = source.get('type', 'string')
            if kind in ('bool', 'boolean') and not choices:
                choices = [dict(value='true', label='true'), dict(value='false', label='false')]
            table['o' + str(len(table) + 1)] = dict(
                name=name, display=row.get('display Name', name), type=kind, choices=choices,
                default=row.get('Value', source.get('default', '')), enabled=row.get('Enabled', '1') == '1',
                visible=row.get('Visible', '1') == '1')
        for name, source in static.items():
            if name not in {r['name'] for r in table.values()}:
                table['o' + str(len(table) + 1)] = dict(name=name, display=name, type=source['type'], choices=source['choices'],
                                                        default=source['default'], enabled=True, visible=True, static_only=True)
        return table

    @staticmethod
    def option_view(oid, row):
        view = dict(id=oid, name=row['name'], default=row['default'])
        if row['display'] != row['name']:
            view['display'] = row['display']
        if row['choices']:
            view['choices'] = [c['value'] if squash(c['label']) == squash(c['value']) else c['value'] + ' = ' + c['label'] for c in row['choices']]
        else:
            view['format_example'] = row['default']
        if not row['enabled']:
            view['enabled'] = False
            view['note'] = 'Fixed by the design for this board'
        if not row['visible']:
            view['visible'] = False
        return view

    def seeds_a_value(self, proc):
        """True when this updater only computes a starting value for the board.

        MPSoC_Ext_Platform's gui_updater reads PROJECT_PARAM.BOARD_PART alone
        and sets Include_DDR.VALUE false for every board, while enabling it on
        DDR4 ones: it seeds the widget the user then ticks, and in the GUI the
        tick stands. An updater that reads any option VALUE is instead reacting
        to the configuration, so what it writes is a rule and overrides a
        selection. A board that truly forces the value says so through
        ENABLEMENT, which is checked separately.
        """
        row = next((u for u in self.entry['updaters'] if u['proc'] == proc), None)
        return row is not None and all(name.startswith('PROJECT_PARAM.') for name in row['inputs'])

    def coerce(self, row, value):
        if not isinstance(value, (str, int, float, bool)) or len(str(value)) > 400:
            raise ValueError('Option values are short strings')
        if not row['choices']:
            return str(value)
        wanted = norm(value) if row['type'] in ('bool', 'boolean') else squash(value)
        for c in row['choices']:
            if wanted in (squash(c['value']), squash(c['label'])) or (row['type'] in ('bool', 'boolean') and wanted == norm(c['value'])):
                return c['value']
        return None

    async def evaluate(self, selections):
        state = {'PROJECT_PARAM.BOARD_PART': self.board_part, 'PROJECT_PARAM.PART': self.part}
        for row in self.options.values():
            state[row['name'] + '.VALUE'] = row['default']
            state[row['name'] + '.ENABLEMENT'] = 'true' if row['enabled'] else 'false'
            state[row['name'] + '.VISIBLE'] = 'true' if row['visible'] else 'false'
        for name, value in selections.items():
            state[name + '.VALUE'] = value
        updaters = '[list ' + ' '.join('[list ' + atom(u['proc']) + ' ' + tlist(u['inputs']) + ' ' + tlist(u['outputs']) + ']'
                                       for u in self.entry['updaters']) + ']'
        result = dict_tcl(await self.native('[cedcfg::evaluate ' + atom(self.entry['vlnv']) + ' ' + tdict(state) + ' ' + updaters + ']'))
        return dict(state=dict_tcl(result['state']), writes=[split_tcl(w) for w in split_tcl(result['writes'])],
                    errors=[split_tcl(e) for e in split_tcl(result['errors'])], messages=dict_tcl(result['messages']),
                    converged=result.get('converged') == '1')

    async def op_set_options(self, request):
        if self.stage != 'options':
            raise ValueError('Options are set after choose_ced and before instantiate')
        items = request.get('selections')
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ValueError('selections must list 1..16 {requirement_id, option_id, value} objects')
        self.counts['set_options'] += 1
        if self.counts['set_options'] > LIMITS['set_options']:
            raise ValueError('Option selection limit reached; instantiate or finish')
        rejected, batch = [], dict(self.option_selections)
        for item in items:
            if not isinstance(item, dict) or set(item) - {'requirement_id', 'option_id', 'value'}:
                raise ValueError('Each selection has requirement_id, option_id and value')
            rid, oid = item.get('requirement_id'), item.get('option_id')
            if rid not in self.requirements or oid not in self.options:
                raise ValueError('Use returned requirement and option IDs')
            row = self.options[oid]
            value = self.coerce(row, item.get('value', ''))
            if value is None:
                rejected.append(dict(requirement_id=rid, option_id=oid, value=item.get('value'), reason='not one of the choices',
                                     choices=[c['value'] for c in row['choices']]))
                continue
            for key in [k for k in batch if k[1] == oid and k[0] != rid]:
                if norm(batch[key]) != norm(value):
                    rejected.append(dict(requirement_id=rid, option_id=oid, value=value,
                                         reason='conflicts with ' + key[0] + ' which selected ' + batch[key]))
                    break
            else:
                batch = {k: v for k, v in batch.items() if k[0] != rid or k[1] != oid}
                batch[(rid, oid)] = value
        names = {self.options[oid]['name']: value for (rid, oid), value in batch.items()}
        owner = {self.options[oid]['name']: rid for (rid, oid) in batch}
        evaluation = await self.evaluate(names)
        conflicts, seeded = [], []
        for key, old, new, proc in evaluation['writes']:
            name = key[:-6]
            if name in names and norm(new) != norm(names[name]):
                if self.seeds_a_value(proc):
                    seeded.append(dict(option=name, selected=names[name], design_default=new, updater=proc))
                    continue
                conflicts.append(dict(option=name, selected=names[name], design_sets=new, updater=proc))
                self.design_vetoes[(owner[name], name)] = new
                self.veto_requests[(owner[name], name)] = names[name]
        for name in names:
            default = next(r['default'] for r in self.options.values() if r['name'] == name)
            if evaluation['state'].get(name + '.ENABLEMENT', 'true').lower() == 'false' and \
                    norm(names[name]) != norm(default):
                conflicts.append(dict(option=name, selected=names[name], reason='disabled by the other selections'))
                self.design_vetoes[(owner[name], name)] = default
                self.veto_requests[(owner[name], name)] = names[name]
        messages = [m for m in evaluation['messages'].values() if m]
        if conflicts or messages:
            return self.state(accepted=[], rejected=rejected, conflicts=conflicts, design_messages=messages,
                              note='Nothing was kept from this batch. The design\'s own updater logic rejects this combination; '
                                   'select values it allows. A requirement the design will not allow stays unmapped and is '
                                   'reported unresolved: Phase 2 cannot change design options, so do not defer it.')
        self.option_selections, self.evaluation = batch, evaluation
        if seeded:
            self.record(dict(op='set_options', kept_over_board_defaults=seeded))
        effective = {}
        for row in self.options.values():
            # A selection that survived the conflict check is what the design
            # will be given, whatever a board initializer wrote over it.
            value = names.get(row['name'], evaluation['state'].get(row['name'] + '.VALUE', row['default']))
            if norm(value) != norm(row['default']) or row['name'] in names:
                effective[row['name']] = value
        return self.state(accepted=[dict(requirement_id=r, option=self.options[o]['name'], value=v) for (r, o), v in batch.items()],
                          rejected=rejected, effective_options=effective,
                          **({'updater_errors': evaluation['errors']} if evaluation['errors'] else {}))

    def refuse_vetoed(self, ids, why):
        """Keep a requirement the design vetoed as an option in Phase 1, unresolved.

        ced-gen_H4: the design's updaters vetoed Video_Streams and
        Stream_Source, the model deferred both, and Phase 2, which configures
        cells and never design options, opened, proposed nothing and reported
        not_proposed. The design already answered these requirements, and the
        answer is no; deferring hides that, and waiving would count a requested
        value as satisfied.
        """
        vetoed = {}
        for rid, name in self.design_vetoes:
            if rid in ids:
                vetoed.setdefault(rid, []).append(name)
        if vetoed:
            raise ValueError('; '.join(rid + ' asked for design option ' + ', '.join(names) for rid, names in vetoed.items()) +
                             ', which the design\'s own logic rejected. ' + why + 'Leave ' + ', '.join(vetoed) +
                             ' unmapped: instantiate reports it unresolved, which is the true answer')

    async def op_defer(self, request):
        if self.stage != 'options':
            raise ValueError('Defer requirements before instantiate')
        ids = request.get('requirement_ids') or [request.get('requirement_id')]
        if not isinstance(ids, list) or any(r not in self.requirements for r in ids):
            raise ValueError('Use returned requirement IDs')
        if self.entry['kind'] != 'IPI':
            return self.state(refused=ids, reason='This design has no block design; Phase 2 is unavailable. Waive or report these.')
        self.refuse_vetoed(ids, 'Phase 2 configures blocks inside the built design and cannot change design options. ')
        # ced-gen_E2 (Gemma-26B) deferred "block RAM" while Preset=PS7_PL ("PS+PL with
        # GPIO and Block RAM") was selected; Phase 2 had nothing to set and the
        # requirement ended unresolved. Only a requirement made entirely of the
        # chosen label's words is covered: "GPIO 4 bits wide" still defers.
        for rid in ids:
            words = set(tokens(self.requirements[rid]))
            for (owner, oid), value in self.option_selections.items():
                row = self.options[oid]
                label = next((c['label'] for c in row['choices'] if c['value'] == value), '')
                if owner != rid and words and words <= set(tokens(label + ' ' + str(value).replace('_', ' '))):
                    raise ValueError(rid + ' is already provided by the selected ' + row['name'] + '=' + value +
                                     ' (' + label.replace('_', ' ')[:90] + '). Map it to that option instead of deferring: '
                                     '{"op":"set_options","selections":[{"requirement_id":"' + rid + '","option_id":"' + oid +
                                     '","value":"' + value + '"}]}. Defer only a setting inside a generated block.')
        context = [rid for rid in ids if self.names_only_context(rid)]
        if context:
            raise ValueError(', '.join(context) + ' only names the chosen design and the target, which the build already '
                             'satisfies; Phase 2 has nothing to set for it. Waive it: {"op":"waive","requirement_id":"' +
                             context[0] + '","reason":"names the chosen design and target"}')
        for rid in ids:
            self.option_selections = {k: v for k, v in self.option_selections.items() if k[0] != rid}
            self.waived.pop(rid, None)
            if rid not in self.deferred:
                self.deferred.append(rid)
        return self.state(deferred=self.deferred)

    async def op_waive(self, request):
        rid, reason = request.get('requirement_id'), request.get('reason')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            raise ValueError('A short reason is required')
        if self.stage == 'phase2':
            sub = self.sub_for(rid)
            # ced-gen_M7 (Gemma-26B): "PL clock at 100 MHz" sat on the BRAM
            # controller, the propose refusal said "describe and select (or
            # waive)", the model waived, and the clock stayed at 50 while the
            # report counted it met. A waiver is for context, not a stated value.
            text = self.requirements.get(self.base_requirement(rid), '')
            value = re.search(r'\b\d+(?:\.\d+)?\s*-?\s*(?:[kmg]?hz|baud|bits?|[kmg]b|bytes?)\b', text, re.I)
            if sub is not None and value:
                # Let propose go ahead and report it unresolved, as the refusal says.
                sub.exhausted[rid] = sub.exhausted.get(rid, 0) + 1
                # Not for a cell that generates the clock it hands out: there is
                # nowhere upstream to move a frequency it produces itself.
                move = '' if await self.clock_load_rows(sub.cell) else await self.drivers_move(rid, sub.cell)
                raise ValueError(rid + ' asks for ' + value.group(0) + '; a waiver would report that value as met. ' +
                                 (move.strip() + ' ' if move else 'If ' + sub.cell + ' does not hold it, move the requirement '
                                  'to the cell that sets it with assign. ') +
                                 'If nothing can set it, propose: it is then reported unresolved, which is the true answer.')
            if sub is not None:
                return self.phase2_reply(rid, await sub.request(dict(op='waive', requirement_id=rid, reason=reason)))
            if rid not in self.deferred:
                raise ValueError('Use a deferred requirement ID')
        elif self.stage != 'options' or rid not in self.requirements:
            raise ValueError('Waive a returned requirement ID while setting options or in Phase 2')
        else:
            self.refuse_vetoed([rid], 'A waiver is for context the design already covers, not for a requested value it refuses. ')
        self.waived[rid] = reason.strip()
        self.option_selections = {k: v for k, v in self.option_selections.items() if k[0] != rid}
        return self.state(waived=list(self.waived)) if self.stage == 'options' else self.phase2_state()

    async def op_instantiate(self, request):
        if self.build_sent:
            raise ValueError('Already instantiated (stage ' + self.stage + '); a second build would find the block design '
                             'not empty. Follow next_op')
        if self.stage != 'options':
            raise ValueError('Instantiate after choose_ced; follow next_op')
        for row in self.pending_phase1():
            if self.names_only_context(row['id']):
                self.waived[row['id']] = 'names only the chosen design and the target'
                self.context_waived.append(row['id'])
        if self.pending_phase1() and not self.instantiate_warned:
            self.instantiate_warned = True
            return self.state(instantiate_paused=True, note='Nothing was created. Each listed requirement still needs set_options, defer '
                              '(Phase 2) or waive. Instantiate again to continue and report them unresolved.')
        self.stage, self.build_sent = 'instantiating', False
        try:
            return await self.build_body()
        except Exception as exc:
            if not self.build_sent:
                # Nothing reached Vivado's build, so instantiate may run again.
                self.stage = 'options'
                raise
            # The design may exist now; another build would only meet 4-481.
            self.stage = 'failed'
            self.record(dict(op='instantiate_interrupted', error=str(exc)[:800]))
            return self.state(phase1=self.phase1, reason='The build reached Vivado but did not finish cleanly: ' + str(exc)[-400:])

    async def build_body(self):
        if self.evaluation is None:
            self.evaluation = await self.evaluate({})
        # Selected values pass as chosen (set_options already rejected updater conflicts);
        # other options pass only when the design's updaters moved them off their default.
        selected = {self.options[o]['name']: v for (_, o), v in self.option_selections.items()}
        passed = {}
        for row in self.options.values():
            value = selected.get(row['name'], self.evaluation['state'].get(row['name'] + '.VALUE', row['default']))
            if row['name'] in selected or norm(value) != norm(row['default']):
                passed[row['name'] + '.VALUE'] = value
        self.build_sent = True
        result = dict_tcl(await self.native('[cedcfg::instantiate ' + atom(self.entry['vlnv']) + ' ' + tdict(passed) + ' ' + atom(self.entry['kind']) + ']'))
        messages = dict_tcl(result['messages'])
        counts = dict_tcl(messages['counts'])
        # An option the board forces (CPM_Config on a VPK120 reads CPM5, Enabled 0)
        # is part of how the design was configured even though nothing passed it,
        # and the answer cannot report a value it was never told.
        # A static-only option has no evaluated value, only the literal from
        # init.tcl, which may be an unevaluated Tcl expression; report it only
        # when it was chosen.
        effective = {row['name']: selected.get(row['name'],
                                               self.evaluation['state'].get(row['name'] + '.VALUE', row['default']))
                     for row in self.options.values()
                     if row['name'] in selected or not row.get('static_only')}
        self.phase1 = dict(options_passed={k[:-6]: v for k, v in passed.items()},
                           options_effective=effective, status=result['status'],
                           validation=result['validation'], errors=int(counts['errors']),
                           critical_warnings=int(counts['critical_warnings']), first_messages=split_tcl(messages['first']),
                           option_check='design_updaters_before_instantiation')
        self.record(dict(op='instantiate', phase1=self.phase1, result=result))
        if result['status'] != 'OK':
            self.stage = 'failed'
            return self.state(phase1=self.phase1, reason=result['result'])
        if self.port_plan and self.port is None:
            self.stage = 'port_ready'
            return self.state(phase1=self.phase1,
                              note='The design is built and validated on the reference board ' + self.port_plan['board'] +
                                   '. Send {"op":"port"} to carry it to ' + self.target['name'] + '.')
        return await self.after_build()

    async def after_build(self, **extra):
        if self.entry['kind'] == 'IPI':
            await self.native('[cedcfg::checkpoint phase1]')
            rows = [dict_tcl(r) for r in split_tcl(await self.native('[cedcfg::cells]'))]
            self.cells = {'k' + str(i + 1): dict(path=r['path'], vlnv=r['vlnv'], display=r['display'], ports=split_tcl(r['ports']))
                          for i, r in enumerate(rows)}
        open_deferred = [r for r in self.deferred if r not in self.waived]
        self.stage = 'phase2' if open_deferred and self.cells else 'phase1_done'
        if self.stage == 'phase2':
            self.phase2 = dict(status='not_proposed')
            return self.state(phase1=self.phase1, **extra, deferred=[dict(id=r, text=self.requirements[r]) for r in open_deferred],
                              cells=self.cell_rows(0), cell_count=len(self.cells), more=len(self.cells) > CELL_ROWS,
                              note='Phase 1 is saved. Assign each deferred requirement to the generated cell(s) that implement it. '
                                   'A requirement covering several cells gets one ID per cell. When more is true, find other cells '
                                   'with {"op":"cells","query":"<ip or port words>"}.')
        # ced-gen_M3 (Haiku) built cleanly with nothing deferred, then stopped to
        # ask whether to "proceed with Phase 2" and never finished, so its answer
        # had no report. There is no Phase 2 here and nothing awaits approval.
        return self.state(phase1=self.phase1, **extra,
                          note='Phase 1 is complete and no requirement was deferred, so there is no Phase 2 and nothing '
                               'for the user to approve. Call finish now and report from its reply.')

    async def port_candidate(self, entry):
        """The reference board for a port, decided by Vivado's data (cedcfg::port_plan)."""
        if self.target.get('kind') != 'board':
            return dict(found='0', reason='a port needs a target board; ' + self.target['name'] + ' is a part')
        return dict_tcl(await self.native('[cedcfg::port_plan ' + atom(entry['vlnv']) + ']'))

    async def op_port(self, request):
        if self.stage != 'port_ready':
            raise ValueError('port runs once, after instantiate, when next_op is port; follow next_op')
        result = dict_tcl(await self.native('[cedcfg::port_apply ' + atom(self.target['name']) + ']'))
        messages = dict_tcl(result.get('messages', ''))
        if result.get('ok') != '1':
            self.port = dict(refused=result.get('reason', 'port failed'), reference_board=self.port_plan['board'])
            self.stage = 'failed'
            self.record(dict(op='port', refused=self.port, result=result))
            return self.state(phase1=self.phase1, port_refused=self.port['refused'],
                              note='The design was built on ' + self.port_plan['board'] + ' but could not be carried to ' +
                                   self.target['name'] + '. Finish and report port_refused.')
        rows = lambda text: [dict_tcl(r) for r in split_tcl(text)]
        kept = {cell: dict_tcl(values) for cell, values in dict_tcl(result.get('kept_settings', '')).items()}
        dropped = [dict(cell=r['cell'], ip=r['ip'], board_interfaces=split_tcl(r['board_interfaces']), reason=r['reason'])
                   for r in rows(result.get('dropped', ''))]
        rebound = [dict(cell=r['cell'], board_interfaces=split_tcl(r['board_interfaces'])) for r in rows(result.get('rebound', ''))]
        tied = rows(result.get('tied_off', ''))
        counts = dict_tcl(messages.get('counts', '')) if messages else {}
        self.port = dict(**{'from': result['from']}, to=result['to'], kept_settings=kept, dropped=dropped,
                         rebound=rebound, tied_off=tied, validation=result['validation'],
                         errors=int(counts.get('errors', 0) or 0), critical_warnings=int(counts.get('critical_warnings', 0) or 0))
        self.phase1['validation'] = 'OK' if result['validation'] == 'OK' else 'FAIL'
        self.record(dict(op='port', port=self.port, result=result))
        if result['validation'] != 'OK':
            self.stage = 'failed'
            return self.state(phase1=self.phase1, port=self.port, reason='The ported design does not validate: ' + result.get('result', ''))
        return await self.after_build(port=self.port)

    def matching_cells(self, query=''):
        items = list(self.cells.items())
        if not query:
            return items
        wanted = set(tokens(query))
        text = lambda row: ' '.join([row['path'].replace('/', ' '), row['display'], row['vlnv'].split(':')[2].replace('_', ' ')] + row['ports'])
        return [(cid, row) for cid, row in items if wanted & set(tokens(text(row)))]

    def generated_for(self, path):
        """The cell this one was generated to serve, when the name says so.

        Block automation names a helper after the cell it belongs to:
        axi_bram_ctrl_0_bram is the blk_mem_gen behind axi_bram_ctrl_0, and both
        sit at the top level. On ced-gen_M7 (Haiku) "the block RAM controller in
        single-port mode" was configured on the memory, which Vivado accepted
        and validated, leaving the controller itself dual-port.
        """
        owner = ''
        for other in self.cells.values():
            name = other['path']
            if path.startswith(name + '_') and len(name) > len(owner):
                owner = name
        return owner

    def cell_id(self, path):
        return next((cid for cid, row in self.cells.items() if row['path'] == path), '')

    def cell_rows(self, offset, query=''):
        rows = []
        for cid, row in self.matching_cells(query)[offset:offset + CELL_ROWS]:
            view = dict(id=cid, path=row['path'], ip=row['display'] or row['vlnv'].split(':')[2])
            owner = self.generated_for(row['path'])
            if owner:
                view['generated_for'] = owner
            if row['ports']:
                view['ports'] = row['ports']
            rows.append(view)
        return rows

    async def op_cells(self, request):
        if not self.cells:
            raise ValueError('Cells are listed after instantiate')
        offset, query = request.get('offset', 0), request.get('query', '')
        if not isinstance(offset, int) or offset < 0 or not isinstance(query, str) or len(query) > 120:
            raise ValueError('offset must be a nonnegative integer and query a short string')
        matched = len(self.matching_cells(query))
        if query and not matched:
            # An empty list reads as "this design has no such cell", which is
            # false: the words are simply absent from every cell name, IP and
            # port. Cells are named after the IP that implements a function,
            # never after the function, so a search for "clock" in a design
            # whose clock comes from processing_system7_0 matched nothing and
            # the model gave up on the PS (ced-gen_M7, 26B). Show them all.
            return self.state(cells=self.cell_rows(offset), matched=0, cell_count=len(self.cells),
                              more=offset + CELL_ROWS < len(self.cells), query=query,
                              note='No cell path, IP name or port contains those words, so every cell is listed instead. '
                                   'A cell is named for the IP that implements the function, not the function: a PL clock '
                                   'comes from the processing system, a reset from a reset block.')
        return self.state(cells=self.cell_rows(offset, query), matched=matched, cell_count=len(self.cells),
                          more=offset + CELL_ROWS < matched, **({'query': query} if query else {}))

    # ----- phase 2 ----------------------------------------------------------
    async def op_assign(self, request):
        if self.stage != 'phase2':
            raise ValueError('Assign deferred requirements after instantiate')
        items = request.get('assignments')
        if not isinstance(items, list) or not 1 <= len(items) <= 12:
            raise ValueError('assignments must list 1..12 {requirement_id, cell_id} objects')
        requested = {}
        for item in items:
            if not isinstance(item, dict) or set(item) != {'requirement_id', 'cell_id'}:
                raise ValueError('Each assignment has requirement_id and cell_id')
            rid, kid = item['requirement_id'], item['cell_id']
            if rid not in self.deferred or kid not in self.cells:
                raise ValueError('Use a deferred requirement ID and a returned cell ID')
            requested.setdefault(rid, [])
            if kid not in requested[rid]:
                requested[rid].append(kid)
        grouped, settled, moved, narrowed = {}, [], [], []
        for rid, kids in requested.items():
            # ced-gen_H3 (Haiku, tree f9785987): r4 moved off clk_wiz_0, which has
            # no parameter bank, onto ps_e -- then straight back, where nothing
            # can be written, and the run ended unresolved. A cell already shown
            # unable to take the write is not a second chance.
            back = [self.cells[k]['path'] for k in kids if (rid, self.cells[k]['path']) in self.left_bankless]
            if back:
                where = ', '.join(self.assignments.get(rid, {}).get('cells', [])) or 'the cell that produces the value'
                raise ValueError(rid + ' was moved off ' + back[0] + ', which has no parameter bank, so nothing can be set '
                                 'there. Keep it on ' + where + ' and find the parameter there: {"op":"describe",'
                                 '"requirement_id":"' + rid + '","query":"<parameter purpose with units>"}')
            self.warn_generated(rid, kids)
            await self.warn_clock_source(rid, kids)
            if rid in self.assignments:
                # A resubmitted batch must still place the requirements that are
                # new to it, and names the same cells for the settled ones.
                current_cells = set(self.assignments[rid]['cells'])
                want_cells = set(self.cells[k]['path'] for k in kids)
                if want_cells <= current_cells:
                    dropped = current_cells - want_cells
                    drop_derived = [(derived, path) for derived, path in
                                    zip(self.assignments[rid]['ids'], self.assignments[rid]['cells'])
                                    if path in dropped]
                    # A cell can be dropped from a split assignment only when it
                    # never took a selection of its own: that is exactly what no
                    # parameter bank means, and the sibling cell already holds
                    # the value. Anything else is a no-op resubmission, not a
                    # release, so it still reads back as settled below.
                    if dropped and all(path in self.subs and self.subs[path].bank_blocks_writes() and
                                       not any(r == derived for r, _ in self.subs[path].selections)
                                       for derived, path in drop_derived):
                        self.narrow(rid, dropped)
                        narrowed.append(dict(requirement_id=rid, dropped=sorted(dropped)))
                    settled.append(rid)
                    continue
                # A first guess at the cell can be wrong: a name that reads like
                # the request (rst_ps7_0_50M for a clock) holds none of its
                # parameters, and describe can only report what the cell has.
                # Moving the requirement is safe until it has picked something.
                if self.selected_cells(rid):
                    raise ValueError(rid + ' is already assigned to ' + ', '.join(self.assignments[rid]['cells']) +
                                     ' and has selections there; describe/select its returned IDs')
                moved.append(dict(requirement_id=rid, was=list(self.assignments[rid]['cells']),
                                  now=[self.cells[k]['path'] for k in kids]))
                self.left_bankless |= {(rid, path) for path in self.assignments[rid]['cells']
                                       if path in self.subs and self.subs[path].bank_blocks_writes()}
                self.release(rid)
            grouped[rid] = kids
        if not grouped:
            if narrowed:
                return self.phase2_state(narrowed=narrowed)
            raise ValueError(', '.join(dict.fromkeys(settled)) + ' is already assigned; describe/select its returned IDs')
        replies = []
        for rid, kids in grouped.items():
            ids = [rid] if len(kids) == 1 else [rid + '_' + kid for kid in kids]
            paths = [self.cells[k]['path'] for k in kids]
            # Record the assignment only once every cell opened. A half-done
            # assignment would leave the requirement unreachable: describe asks
            # for an assignment and assign reports it already has one.
            opened, added = [], []
            try:
                for derived, kid, path in zip(ids, kids, paths):
                    text = self.requirements[rid]
                    if path in self.subs:
                        sub = self.subs[path]
                        sub.requirements[derived] = text
                        added.append((sub, derived))
                        reply = sub.state()
                    else:
                        # One bridge serves every parallel case. A class-wide parent
                        # was reset by another session's assign while ced-gen_H3's
                        # 132 s ps_e open was still running ('NoneType' object has no
                        # attribute 'native'), so bind the parent per session.
                        bound = type('CellSession', (CellSession,), {'parent': self})
                        sub, reply = await bound.adopted(self.guard, self.upstream, self.sid, self.log_file,
                                                         path, self.part, self.task, self.build, {derived: text})
                        sub.parent = self
                        self.subs[path] = sub
                        opened.append(path)
                    replies.append(self.compact_sub(derived, path, reply))
            except Exception as exc:
                for path in opened:
                    self.subs.pop(path, None)
                for sub, derived in added:
                    sub.requirements.pop(derived, None)
                raise ValueError(str(exc)[:400] + ' -- ' + rid + ' stays unassigned; assign it to another cell, '
                                 'or waive it and continue with the rest.')
            self.assignments[rid] = dict(ids=ids, cells=paths)
        return self.phase2_state(assigned=replies, **({'reassigned': moved} if moved else {}),
                                 **({'narrowed': narrowed} if narrowed else {}))

    def warn_generated(self, rid, kids):
        """Say once that this cell is a generated helper, and name the cell it serves.

        Both cells answer to the same words -- ced-gen_M7 asks for "the block
        RAM controller in single-port mode" and axi_bram_ctrl_0_bram is the
        memory, not the controller -- so the choice is a coin toss the design
        itself can settle. It fires once per requirement: a requirement that
        really is about the memory gets there by assigning again.
        """
        for kid in kids:
            owner = self.generated_for(self.cells[kid]['path'])
            if not owner or (rid, kid) in self.helper_warned:
                continue
            self.helper_warned.add((rid, kid))
            ip = lambda k: self.cells[k]['display'] or self.cells[k]['vlnv'].split(':')[2]
            oid = self.cell_id(owner)
            raise ValueError(kid + ' ' + self.cells[kid]['path'] + ' is the ' + ip(kid) + ' Vivado generated to serve ' +
                             owner + ' (' + oid + ', ' + ip(oid) + '), not ' + owner + ' itself. A requirement about ' +
                             owner + ' is set on ' + oid + '. Assign ' + rid + ' to ' + oid + ', or send this same '
                             'assignment again if ' + self.cells[kid]['path'] + ' really is what it is about.')

    async def warn_clock_source(self, rid, kids):
        """Say once that this cell takes its clock from another, and name that one.

        ced-gen_M7 (Gemma-26B, tree ffe17dd6): "PL clock at 100 MHz" was assigned
        to axi_bram_ctrl_0 alongside the single-port requirement, and the
        controller is clocked by processing_system7_0. Three describes returned
        read latency and ECC, the budget ran out, and the run reported the clock
        unresolved while the PS stayed at 50 MHz. The waive refusal already names
        the driver, but only once the model has given up; the block design can
        say it at the assignment instead.

        Only a requirement that states a frequency, and only where Vivado shows
        another cell driving this one's clock input. A cell that sets its own
        clock has no such driver, so the requirement lands there unremarked.

        Two cells that drive each other are both in the clock path, and the
        block design does not settle which one holds the frequency. ced-gen_H3's
        MPSoC_Ext_Platform is exactly that: ps_e/pl_clk0 feeds clk_wiz_0/clk_in1
        and clk_wiz_0/clk_out2 feeds ps_e/maxihpm0_fpd_aclk. An earlier form of
        this warning pointed each at the other until the warn-once budget ran
        out and left the requirement on clk_wiz_0, failing a case that passed on
        both Gemma models before. Say nothing rather than guess.

        A cell that hands a clock to other cells out of its own output clock
        pins generates that clock, so "does not set its own clock" is false of
        it however its reference input is fed: mbv-scu35-200 assigned 200 MHz to
        clk_wiz_1, which drives microblaze_riscv_0/Clk, and the only cell that
        can hold the number is the wizard itself.
        """
        text = self.requirements.get(self.base_requirement(rid), '')
        if not re.search(r'\b\d+(?:\.\d+)?\s*-?\s*[kmg]?hz\b', text, re.I):
            return
        for kid in kids:
            path = self.cells[kid]['path']
            if (rid, kid) in self.clock_warned:
                continue
            if await self.clock_load_rows(path):
                continue
            named = await self.driver_rows(path)
            if not named or any(r['cell'] == path for _, r in await self.driver_rows(named[0][1]['cell'])):
                continue
            move = await self.drivers_move(rid, path)
            if not move:
                continue
            self.clock_warned.add((rid, kid))
            raise ValueError(rid + ' states a frequency and ' + path + ' does not set its own clock.' + move +
                             ' Or send this same assignment again if ' + path + ' really is where the frequency is set.')

    @staticmethod
    def compact_sub(rid, path, reply):
        keep = {k: v for k, v in reply.items() if k in ('catalog', 'candidates', 'port_dependencies', 'nested_containers', 'vlnv',
                                                         'coverage_gaps', 'selected', 'unchanged_selections', 'value_mismatches',
                                                         'describe_exhausted', 'repeated_query', 'covered_by_source',
                                                         'covered_by_vector', 'reason', 'note') and v}
        # Say once, for the cell, that nothing will check these values. Per
        # candidate it shows only as range PB_MISS:* and a range_scope reading
        # "unavailable", which is indistinguishable from an ordinary writable
        # parameter: ced-gen_H3 read clk_wiz_0's CONFIG.REF_CLK_FREQ as a
        # settable float and wrote 125 to the wizard's own input instead of to
        # the PS driving it, on both models.
        #
        # The packaged fallback is one way to get here and not the common one.
        # clk_wiz_0 answers live, with a current and a value_src per row, and
        # only its parameter bank is missing, so the backend still reads live.
        # Judge the rows, not the route that produced them.
        #
        # The notice is keyed on the same PB_MISS rows that select refuses, so
        # the prose and the gate never disagree: both models wrote 125 into
        # clk_wiz_0 in the run where only the prose existed.
        # A selection the engine discarded must not vanish: ced-gen_H7 sent
        # c_include_sg and c_sg_length_width together, saw only the width under
        # selected, and later reported scatter-gather as applied.
        dropped = [dict(candidate_id=r.get('candidate_id'), repair=r['repair'], **({'note': r['note']} if r.get('note') else {}))
                   for r in reply.get('selection_repairs', []) if str(r.get('repair', '')).startswith(('dropped_', 'not_added_'))]
        if dropped:
            keep['not_kept'] = dropped[:8]
        candidates = [c for c in keep.get('candidates', []) if not c.get('subkey')]
        degraded = candidates and all(c.get('range', '').startswith('PB_MISS:') for c in candidates)
        if reply.get('backend') == 'packaged_component_xml' or degraded:
            keep['metadata'] = ('degraded: the customizer of ' + path + ' does not answer here, so these rows carry no '
                                'ranges, enablement or help. They are unavailable rather than unlimited.')
            # Only say "go to the producing cell" when every row really is one this
            # cell receives. clk_wiz on 2026.2 reaches this state for its own output
            # frequency, and sending the model away from the wizard left the 200 MHz
            # requirement unresolved with the design still at 100.
            if candidates and all(StructuredSession.received_quantity(c) for c in candidates):
                keep['metadata'] += (' Nothing would check a value before it was written, so select refuses a write to '
                                     'them and only an assert reads one back. A frequency or width this cell receives '
                                     'is set on the cell that produces it; use {"op":"cells","query":"<ip or port '
                                     'words>"} to find that one.')
            else:
                keep['metadata'] += (' A value this cell drives on its own output is still set here: select accepts it, '
                                     'and propose dry-runs the write, validates the design and reads the value back, so '
                                     'it is checked after the write rather than before it. Values this cell receives '
                                     'from another cell are still refused.')
        return dict(requirement_id=rid, cell=path, **keep)

    def phase2_state(self, **extra):
        pending = []
        for path, sub in self.subs.items():
            pending.extend(dict(id=p['id'], cell=path, text=p['text']) for p in sub.pending())
        result = self.state(**extra)
        if self.unassigned():
            result['unassigned'] = [dict(id=r, text=self.requirements[r]) for r in self.unassigned()]
        if pending:
            result['unresolved'] = pending
        return result

    def phase2_reply(self, rid, reply):
        return self.phase2_state(result=self.compact_sub(rid, self.sub_for(rid).cell, reply))

    async def op_describe(self, request):
        if self.stage != 'phase2':
            raise ValueError('Describe applies to Phase 2 cell requirements')
        rid = request.get('requirement_id')
        sub = self.sub_for(rid)
        if sub is None:
            raise ValueError('Assign the requirement to a cell first, then use its returned ID')
        result = self.compact_sub(rid, sub.cell, await sub.request(dict(request)))
        # A cell that holds nothing for the request is usually the wrong cell:
        # describe can only offer what this IP has. Say so, because the way out
        # is another cell, not another query.
        if not any(k in result for k in ('candidates', 'nested_containers', 'catalog', 'selected', 'note')):
            result['note'] = ('No parameter of ' + sub.cell + ' matches this requirement. If another cell implements it, '
                              'assign ' + self.base_requirement(rid) + ' to that cell (use {"op":"cells","query":"<ip or port words>"} to find '
                              'it); the assignment moves while nothing is selected here.')
        return self.phase2_state(result=result)

    async def op_select(self, request):
        if self.stage != 'phase2':
            raise ValueError('Select applies to Phase 2 cell requirements; use set_options for CED options')
        items = request.get('selections')
        if not isinstance(items, list) or not items:
            raise ValueError('selections must be a nonempty list')
        grouped = {}
        for item in items:
            sub = self.sub_for(item.get('requirement_id') if isinstance(item, dict) else None)
            if sub is None:
                raise ValueError('Each selection needs an assigned requirement ID')
            grouped.setdefault(sub.cell, []).append(item)
        results = []
        for path, group in grouped.items():
            try:
                reply = await self.subs[path].request(dict(op='select', selections=group))
            except ValueError as exc:
                raise await self.name_the_move(path, group, exc)
            results.append(self.compact_sub(','.join(i['requirement_id'] for i in group), path, reply))
        return self.phase2_state(results=results)

    async def driver_rows(self, path):
        """(cell id, row) for every cell of this design driving an input of path."""
        try:
            rows = [dict_tcl(r) for r in split_tcl(await self.native('[cedcfg::drivers ' + atom(path) + ']'))]
        except ValueError:
            return []
        by_path = {cell['path']: kid for kid, cell in self.cells.items()}
        return [(by_path[r['cell']], r) for r in rows if r.get('cell') in by_path and r['cell'] != path]

    async def clock_load_rows(self, path):
        """The rows for every cell this one clocks out of its own output clock pins.

        Non-empty means the cell generates a clock, which is what makes it the
        right home for a stated frequency even when it has no parameter bank and
        nothing else about it can be read.
        """
        try:
            return [dict_tcl(r) for r in split_tcl(await self.native('[cedcfg::clock_loads ' + atom(path) + ']'))]
        except ValueError:
            return []

    async def drivers_move(self, rid, path):
        """The assign call that moves rid onto the cell driving path's input clock, if Vivado shows one.

        ced-gen_H3 (Haiku, after the bounce gate): told to find the producing
        cell, the model searched "PL clock input si570", landed on the DDR4
        controller, wrote 125 MHz and Custom there and reported applied, while
        ps_e -- which drives clk_wiz_0/clk_in1 through pl_clk0 -- stayed at 100.
        The block design already names the producer.

        Naming the pin also named the wrong search term: ced-gen_H3 (Gemma-31B)
        read "through pl_clk0", moved to ps_e, then described it four times by
        the pin's name and never reached the parameter. So the move also says
        which parameter of the driver sets that pin's frequency, when Vivado and
        the request together single one out (frequency_setter), with the describe
        call that reaches it; and otherwise says outright that the pin name is
        not a parameter name.
        """
        named = await self.driver_rows(path)
        if not named:
            return ''
        kid, row = named[0]
        move = (' In this design ' + row['cell'] + ' (' + kid + ') drives ' + path + '/' + row['pin'] + ' through ' +
                row['source_pin'] + ': move the requirement there with {"op":"assign","assignments":[{"requirement_id":"' +
                rid + '","cell_id":"' + kid + '"}]}.')
        return move + await self.setter_hint(rid, row['cell'], row['source_pin'])

    async def setter_hint(self, rid, cell, pin):
        """Where on the driving cell the frequency of pin is set, for the move message."""
        generic = (' ' + pin + ' is a pin name, not a parameter name: on ' + cell + ', describe the frequency in the '
                   "request's own words.")
        try:
            reply = dict_tcl(await self.native('[cedcfg::frequency_source ' + atom(cell) + ' ' + atom(pin) + ']'))
            matches = [dict_tcl(m) for m in split_tcl(reply.get('matches', ''))]
        except ValueError:
            return generic
        key = frequency_setter(pin, matches, self.requirements.get(self.base_requirement(rid), ''))
        if key is None:
            return generic
        value = next((m.get('value') for m in matches if m.get('key') == key), '')
        return (' On ' + cell + ', ' + pin + ' runs at ' + reply.get('pin_mhz', '?') + ' MHz, set by CONFIG.' + key +
                ' (now ' + str(value) + '); after the move, reach it with {"op":"describe","requirement_id":"' + rid +
                '","query":"' + key + '"}.')

    async def name_the_move(self, path, group, exc):
        """Spell out the call that moves the requirement, not just the cell that holds it.

        ced-gen_H3's 26B run read the bank-missing refusal, ran
        {"op":"cells","query":"ps_e"} and got k11 back -- the right cell, found
        from the refusal's own words -- then described clk_wiz_0 again and
        proposed nothing. The refusal ended "configure that one instead" and
        named no op. describe already writes the move out in full when a cell
        holds nothing for the request; select has to as well, because a cell
        with no bank holds rows that read as perfectly settable.

        Only the bank-missing refusal is rewritten. Every other select error is
        about the value, and the cell is the right one.
        """
        if 'no parameter bank' not in str(exc):
            return exc
        rid = self.base_requirement(group[0]['requirement_id'])
        named = await self.drivers_move(rid, path)
        if named:
            return ValueError(str(exc) + named + ' The assignment moves while nothing is selected on ' + path + '.')
        return ValueError(str(exc) + ' Find that cell with {"op":"cells","query":"<ip or port words>"}, then move the '
                          'requirement with {"op":"assign","assignments":[{"requirement_id":"' + rid + '","cell_id":'
                          '"<its id>"}]}; the assignment moves while nothing is selected on ' + path + '.')

    def reset_subs(self):
        for sub in self.subs.values():
            if sub.stage in ('configured', 'apply_failed'):
                sub.stage = 'discovering'
                sub.apply_attempts = 0

    async def op_propose(self, request):
        if self.stage != 'phase2':
            raise ValueError('Propose after Phase 2 selections; follow next_op')
        if self.unassigned():
            return self.phase2_state(note='Assign or waive every deferred requirement before propose.')
        # ced-gen_H3 (Haiku): r4 sat on clk_wiz_0, which has no parameter bank;
        # select was refused with the assign call spelled out, next_op said
        # assign, the model's own cells query returned ps_e -- and it proposed
        # anyway, got changes [] and reported failed. Its describe budget was
        # spent, so the open-cell check below no longer counted it.
        stuck = [(self.base_requirement(p['id']), path) for path, sub in self.subs.items() if sub.bank_blocks_writes()
                 for p in sub.pending()]
        if stuck:
            rid, path = stuck[0]
            move = await self.drivers_move(rid, path) or (
                ' Find that cell with {"op":"cells","query":"<ip or port words>"} and move the requirement with '
                '{"op":"assign","assignments":[{"requirement_id":"' + rid + '","cell_id":"<its id>"}]}.')
            return self.phase2_state(note='Nothing was proposed. ' + rid + ' is on ' + path + ', which has no parameter bank, so '
                                          'nothing can be written there: a value this cell receives is set on the cell that '
                                          'produces it.' + move + ' Or waive ' + rid + ' with a reason to report it unresolved.')
        for sub in self.subs.values():
            # A requirement whose describe budget is spent stays unresolved; it must not block the others.
            for p in sub.open_pending():
                if sub.describe_counts.get(p['id'], 0) >= sub.describe_limit(p['id']):
                    sub.exhausted[p['id']] = sub.exhausted.get(p['id'], 0) + 1
        open_cells = [path for path, sub in self.subs.items() if sub.open_pending()]
        if open_cells:
            return self.phase2_state(note='Nothing was written. Describe and select the unresolved requirements (waive only context that needs no setting) on '
                                          + ', '.join(open_cells) + ' before propose.')
        self.counts['proposals'] += 1
        if self.counts['proposals'] > LIMITS['proposals']:
            raise ValueError('Proposal limit reached; finish to keep the Phase 1 design')
        if self.dry_run_written:
            await self.native('[cedcfg::restore phase1]')
            self.dry_run_written = False
            self.reset_subs()
        number = self.counts['proposals']
        before = self.root / 'evidence' / f'proposal-{number}-before.tsv'
        after = self.root / 'evidence' / f'proposal-{number}-after.tsv'
        await self.native('[cedcfg::snapshot ' + atom(before) + ']')
        applied, failures = {}, []
        for path, sub in self.subs.items():
            if not sub.selections:
                if sub.pending():
                    failures.append(dict(cell=path, unresolved=[p['id'] for p in sub.pending()]))
                continue
            reply = await sub.request(dict(op='apply'))
            if reply.get('apply_paused'):
                if self.dry_run_written:
                    await self.native('[cedcfg::restore phase1]')
                    self.dry_run_written = False
                self.reset_subs()
                self.counts['proposals'] -= 1
                return self.phase2_state(proposal_paused=self.compact_sub('', path, reply) | {
                    k: reply[k] for k in ('coverage_gaps', 'unchanged_selections', 'value_mismatches', 'note') if reply.get(k)})
            self.dry_run_written = True
            applied[path] = reply
            if reply.get('stage') != 'configured':
                failures.append(dict(cell=path, receipt=reply.get('native_receipt', '')[:400],
                                     **{k: reply[k] for k in ('rejected_fields', 'disabled_writes', 'withheld_keys') if reply.get(k)}))
        validation = dict_tcl(await self.native('[cedcfg::validate]'))
        messages = dict_tcl(validation['messages'])
        counts = dict_tcl(messages['counts'])
        await self.native('[cedcfg::snapshot ' + atom(after) + ']')
        changes = self.diff(before, after)
        tag = f'proposal-{number}'
        await self.native('[cedcfg::checkpoint ' + atom(tag) + ']')
        await self.native('[cedcfg::restore phase1]')
        self.dry_run_written = False
        requested = [c for c in changes if c['kind'] == 'requested']
        side = [c for c in changes if c['kind'] == 'side_effect']
        self.proposal = dict(id='p' + str(number), tag=tag, after=str(after), changes=changes,
                             validation=dict(status=validation['status'], errors=int(counts['errors']),
                                             critical_warnings=int(counts['critical_warnings']),
                                             first_messages=split_tcl(messages['first'])),
                             failures=failures, waived=self.phase2_waivers(),
                             unresolved=[p for sub in self.subs.values() for p in sub.pending()])
        files = self.write_proposal()
        if not changes:
            # Nothing would change, so there is nothing to approve.
            self.stage = 'phase2_applied'
            status = self.empty_proposal_status(failures)
            self.phase2 = dict(status=status, proposal_id=self.proposal['id'])
            note = ('Vivado rejected every Phase 2 write; the Phase 1 design is unchanged and correct as built. That is '
                    'the answer: the same write is rejected in any session, so do not retry it, restart the session or '
                    'build the design again elsewhere. Finish, report the failures, and copy report.' if failures else
                    'The Phase 1 design already satisfies every Phase 2 selection; nothing needs approval. Finish.' if status == 'already_satisfied' else
                    'Nothing was selected for Phase 2, so there is nothing to approve. Finish.')
            return self.state(proposal_id=self.proposal['id'], changes=[], validation=self.proposal['validation'],
                              **({'failures': failures} if failures else {}), note=note, proposal_file=files['md'],
                              report=self.report())
        self.stage = 'awaiting_approval'
        self.phase2 = dict(status='proposed', proposal_id=self.proposal['id'])
        return self.state(proposal_id=self.proposal['id'], requested_changes=requested[:24], side_effects=side[:12],
                          side_effects_more=max(0, len(side) - 12), validation=self.proposal['validation'],
                          **({'failures': failures} if failures else {}),
                          **({'unresolved': self.proposal['unresolved']} if self.proposal['unresolved'] else {}),
                          proposal_file=files['md'], report_while_waiting=self.report(),
                          note=('Nothing has been written yet. begin recorded phase2_approval=pre_approved, so call the '
                                'ced_apply_phase2 tool with this session_id and proposal_id now, then finish.') if self.pre_approved else
                               # ced-gen_E3w/M1w (Haiku) showed the changes and asked, and their
                               # answers carried no report at all: "holds the values to report"
                               # read as background, not as part of the answer.
                               ('Nothing has been written to the design yet (the Phase 1 design is on disk). Stop here. Show '
                                'the changes, side effects and validation, ask whether to apply them, and then end the answer '
                                'with the final report copied from report_while_waiting (phase2_status proposed: nothing is '
                                'applied) -- the report comes after the question. After the user approves, call '
                                'ced_apply_phase2; if the user declines, execute finish.'))

    def empty_proposal_status(self, failures):
        """What a proposal that would change nothing is reporting.

        "Already satisfied" is a claim about selections that were made and turned
        out to need no write: ced-gen_E2's GPIO is bound to the ZC702 LED
        interface and is already 4 bits wide and all outputs. With nothing
        selected at all there is no such claim to make. Every deferred
        requirement was waived instead, and Phase 2 was simply not needed.
        """
        if failures:
            return 'failed'
        return 'already_satisfied' if any(sub.selections for sub in self.subs.values()) else 'not_needed'

    def phase2_waivers(self):
        rows = [dict(id=r, text=self.requirements[r], reason=self.waived[r]) for r in self.deferred if r in self.waived]
        for sub in self.subs.values():
            rows.extend(dict(id=r, text=sub.requirements[r], reason=reason) for r, reason in sub.waived.items())
        return rows

    def requested_keys(self):
        keys = {}
        for path, sub in self.subs.items():
            for (rid, cid), item in sub.selections.items():
                keys.setdefault((path, sub.candidates[cid]['key']), []).append(rid)
            for row in sub.derived_enables:
                keys.setdefault((path, row['key']), []).append(row['requirement_id'] + ' (enable)')
        return keys

    @staticmethod
    def read_snapshot(path):
        values = {}
        for line in Path(path).read_text().splitlines():
            parts = line.split('\t')
            if len(parts) == 4:
                values[(parts[0], parts[1])] = (base64.b64decode(parts[3]).decode(errors='replace'), parts[2])
        return values

    def diff(self, before, after):
        old, new = self.read_snapshot(before), self.read_snapshot(after)
        requested = self.requested_keys()
        changes = []
        for key in sorted(set(old) | set(new)):
            a, b = old.get(key, ('<absent>', ''))[0], new.get(key, ('<absent>', ''))[0]
            if a == b or key[1] == 'CONFIG.Component_Name':
                continue
            kind = 'requested' if key in requested else 'side_effect'
            fields = self.dictionary_diff(a, b) if len(a) > 120 or len(b) > 120 else None
            rows = [(key[1][7:] + '/' + k, x, y) for k, x, y in fields] if fields else [(key[1][7:], a, b)]
            for name, before_value, after_value in rows:
                row = dict(cell=key[0], parameter=name, before=before_value[:120], after=after_value[:120], kind=kind)
                if key in requested:
                    row['requirements'] = requested[key]
                if len(before_value) > 120 or len(after_value) > 120:
                    row['truncated'] = True
                changes.append(row)
        return changes

    @staticmethod
    def dictionary_diff(a, b):
        """Changed subkeys of a dictionary-valued parameter, or None when the values are not dictionaries."""
        try:
            old, new = dict_tcl(a), dict_tcl(b)
        except ValueError:
            return None
        if not old or not new:
            return None
        changed = [k for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
        return [(k, old.get(k, '<absent>'), new.get(k, '<absent>')) for k in changed[:30]]

    def write_proposal(self):
        base = self.root / 'evidence' / ('phase2-' + self.proposal['id'])
        Path(str(base) + '.json').write_text(json.dumps(self.proposal, indent=2))
        lines = ['# Phase 2 proposal ' + self.proposal['id'], '', 'Design: ' + self.entry['display'] + ' (' + self.entry['vlnv'] + ')',
                 'Target: ' + self.target['name'], '', '| Cell | Parameter | Before | After | Kind | Requirements |', '|---|---|---|---|---|---|']
        for c in self.proposal['changes']:
            lines.append('| ' + ' | '.join([c['cell'], c['parameter'], c['before'].replace('|', '/'), c['after'].replace('|', '/'),
                                            c['kind'], ', '.join(c.get('requirements', []))]) + ' |')
        v = self.proposal['validation']
        lines += ['', f"Validation: {v['status']} ({v['errors']} errors, {v['critical_warnings']} critical warnings)"]
        lines += ['- ' + m for m in v['first_messages']]
        for title, rows in [('Failures', self.proposal['failures']), ('Unresolved', self.proposal['unresolved']), ('Waived', self.proposal['waived'])]:
            if rows:
                lines += ['', title + ':'] + ['- ' + json.dumps(r) for r in rows]
        Path(str(base) + '.md').write_text('\n'.join(lines) + '\n')
        return dict(json=str(base) + '.json', md=str(base) + '.md')

    async def approve(self, proposal_id):
        """Called by the approval tool, which the host gates on the user's consent."""
        if self.stage != 'awaiting_approval' or not self.proposal or proposal_id != self.proposal['id']:
            raise ValueError('No proposal ' + str(proposal_id) + ' is awaiting approval')
        restore = split_tcl(await self.native('[cedcfg::restore ' + atom(self.proposal['tag']) + ']'))
        locked = dict(zip(restore[1::2], restore[2::2])).get('locked_ips', '')
        validation = dict_tcl(await self.native('[cedcfg::validate]'))
        check = self.root / 'evidence' / (self.proposal['tag'] + '-approved.tsv')
        await self.native('[cedcfg::snapshot ' + atom(check) + ']')
        expected, actual = self.read_snapshot(self.proposal['after']), self.read_snapshot(check)
        # Component names are regenerated when the project reopens; they are not configuration.
        divergent = [dict(cell=k[0], parameter=k[1][7:], expected=expected.get(k, ('<absent>',))[0][:80], actual=actual.get(k, ('<absent>',))[0][:80])
                     for k in sorted(set(expected) | set(actual))
                     if k[1] != 'CONFIG.Component_Name' and expected.get(k, (None,))[0] != actual.get(k, (None,))[0]]
        readback = {}
        for path, sub in self.subs.items():
            if sub.selections:
                await sub.native('[lite_ced::finish ' + atom(path) + ']')
                readback[path] = await sub.verified_readback()
        saved = await self.native('[cedcfg::save]')
        passed = all(f['passed'] for r in readback.values() if r.get('available') for f in r['fields']) and \
            all(r.get('available') for r in readback.values())
        self.stage = 'phase2_applied'
        self.phase2 = dict(status='applied', proposal_id=proposal_id, divergent_from_proposal=len(divergent),
                           validation=validation['status'], requirement_readback_passed=passed, readback=readback,
                           locked_ips=locked)
        self.record(dict(op='approve', phase2=self.phase2, divergent=divergent))
        return self.state(applied=proposal_id, matches_proposal=not divergent, divergent=divergent[:8], validation=validation['status'],
                          requirement_readback_passed=passed, saved=saved, **({'locked_ips': locked} if locked else {}))

    # ----- finish -------------------------------------------------------------
    def vetoed(self, rid, oid, value):
        """Whether this selection is the design's answer rather than the requirement's.

        ced-gen_H4 asked for multi-stream from DDR. set_options refused
        Stream_Source=AVTPG_and_DDR_(MST) with design_sets AVTPG, the model then
        selected AVTPG and the run reported full: true. The skill cannot know
        that AVTPG does not mean "from DDR", but it does know the design forced
        exactly this value over the one the requirement asked for, and that is
        enough to keep the requirement out of full.

        A value rejected as "not one of the choices" is not a veto: retrying
        "4 = 4 Streams" as "4" really does satisfy the requirement.
        """
        forced = self.design_vetoes.get((rid, self.options[oid]['name']))
        return forced is not None and norm(forced) == norm(value)

    def coverage(self):
        rows = []
        phase1 = {rid: self.options[oid]['name'] + '=' + v for (rid, oid), v in self.option_selections.items()}
        overridden = {rid for (rid, oid), v in self.option_selections.items() if self.vetoed(rid, oid, v)}
        # ced-gen_H4 (Gemma-26B): one requirement asked for DDR source, multi-stream
        # and 4 streams; the design vetoed the first two, the model kept only
        # Video_Streams=4, and the requirement counted as met (full: true). A
        # vetoed option value the requirement no longer holds is part of it unmet.
        for (rid, name), wanted in self.veto_requests.items():
            if not any(r == rid and self.options[o]['name'] == name and norm(v) == norm(wanted)
                       for (r, o), v in self.option_selections.items()):
                overridden.add(rid)
        verified = {}
        for path, result in self.phase2.get('readback', {}).items():
            for field in result.get('fields', []):
                rid = field['requirement_id'].split('.')[0]
                verified.setdefault(rid, []).append(field['passed'])
        for rid, text in self.requirements.items():
            row = dict(id=rid, text=text)
            if rid in self.waived:
                row.update(status='waived', reason=self.waived[rid])
            elif rid in phase1:
                row.update(status='phase1_option' if self.phase1 and self.phase1['status'] == 'OK' else 'not_built', value=phase1[rid])
                if rid in overridden and row['status'] == 'phase1_option':
                    row.update(status='overridden_by_design',
                               note='The design rejected or forced part of what this requirement asked for. '
                                    'Report it as not satisfied rather than as the requested setting.')
            elif rid in self.deferred:
                ids = self.assignments.get(rid, {}).get('ids', [])
                selected = [i for i in ids if self.sub_for(i) and any(r == i for r, _ in self.sub_for(i).selections)]
                results = [ok for i in ids for ok in verified.get(i, [])]
                sub_waived = [i for i in ids if self.sub_for(i) and i in self.sub_for(i).waived]
                status = self.phase2['status']
                if ids and len(sub_waived) == len(ids):
                    row['status'] = 'waived'
                elif len(selected) + len(sub_waived) < len(ids) or not selected:
                    row['status'] = 'unresolved'
                elif status == 'applied':
                    row['status'] = 'phase2_applied' if results and all(results) else 'phase2_failed'
                elif status == 'already_satisfied':
                    row['status'] = 'phase2_already_satisfied'
                elif status == 'failed':
                    row['status'] = 'phase2_failed'
                elif status == 'proposed':
                    row['status'] = 'phase2_declined'
                else:
                    row['status'] = 'unresolved'
            else:
                row['status'] = 'unresolved'
            rows.append(row)
        return rows

    def refuse_premature_finish(self):
        """Say no once to a finish that would throw away an approval already given.

        ced-gen_M7's 26B run proposed both writes, got stage awaiting_approval
        and next_op ced_apply_phase2, and called finish instead. finish took it
        as a decline: it recorded both requirements phase2_declined, saved the
        untouched Phase 1 design and closed Vivado. The model then called
        ced_apply_phase2 against a dead session, got null, and restarted with
        begin until the retry limit, so its final answer reported ced null.
        next_op had named the right tool and the model read past it; a field the
        model can ignore is not a gate.

        Only the pre_approved case is refused. Where begin recorded "ask",
        finish IS the decline path and SKILL.md says so. The refusal also fires
        once: a model that means it gets to decline on the next call rather than
        being trapped, and declining after approval is honest, only unusual.
        """
        if self.stage != 'awaiting_approval' or not self.pre_approved or self.finish_refused:
            return
        self.finish_refused = True
        raise ValueError('The user approved these changes in advance, so finishing now would record them as declined and '
                         'close Vivado with the Phase 1 design unchanged. Call the ced_apply_phase2 tool with this '
                         'session_id and proposal_id ' + self.proposal['id'] + ' first, then finish. Repeat finish only if '
                         'you really mean to decline.')

    async def finish(self, reason=None):
        saved = 'SAVE:UNAVAILABLE'
        if self.stage == 'awaiting_approval':
            self.phase2['status'] = 'proposed'
        if self.initialized:
            try:
                if self.dry_run_written:
                    await self.native('[cedcfg::restore phase1]')
                    self.dry_run_written = False
                saved = await self.native('[cedcfg::save]')
            except Exception as exc:
                saved = str(exc)[:300]
        closed = await self.close()
        coverage = self.coverage()
        # A port that did not reach the user's board leaves only the reference build:
        # the design the user asked for on their board does not exist.
        port_failed = bool(self.port_plan) and (self.port is None or 'refused' in self.port)
        full = (closed and self.phase1 is not None and self.phase1['status'] == 'OK' and self.phase1['validation'] in ('OK', 'none')
                and not port_failed
                and all(r['status'] in ('phase1_option', 'phase2_applied', 'phase2_already_satisfied', 'waived') for r in coverage))
        self.final_reply = dict(stage='finished' if closed else 'close_failed', full=full, session_closed=closed,
                                next_op='stop' if closed else 'vivado_stop', saved=saved,
                                ced=dict(name=self.entry['name'], vlnv=self.entry['vlnv'], display=self.entry['display']) if self.entry else None,
                                target=self.target_summary() if self.target else None,
                                store=self.store_summary() if self.mirror and self.mirror.source else None,
                                phase1=self.phase1, phase2={k: v for k, v in self.phase2.items() if k != 'readback'},
                                requirements=coverage, project=str(self.root / 'proj') if self.phase1 and not port_failed else None,
                                **({'reference_project': str(self.root / 'port-ref')} if self.port_plan else {}),
                                report=self.report(full),
                                **({'reason': reason} if reason else {}),
                                **({'added_task_requirements': self.added_requirements} if self.added_requirements else {}))
        self.stage = self.final_reply['stage']
        self.record(dict(op='finish', reply=self.final_reply))
        self.keep_report()
        return self.final_reply

    REPORT_FILE = 'ced_final_report.json'

    def keep_report(self):
        """Leave the finished report in the workspace, for a session that starts over.

        A model that dislikes its own answer restarts: ced-gen_M5 (Haiku)
        finished with the correct phase2_status failed, then closed Vivado and
        ran the entire task again in a fresh project, and reported that second,
        unfinished session instead.
        """
        if self.phase1 is None:
            # Nothing was built, so there is no design to return to: a run
            # that stopped at an unknown target must be able to begin again.
            return
        try:
            out = self.root / 'outputs'
            out.mkdir(parents=True, exist_ok=True)
            (out / self.REPORT_FILE).write_text(json.dumps(
                dict(task=self.task, report=self.final_reply['report'],
                     requirements=self.final_reply['requirements']), indent=1))
        except OSError:
            pass

    def earlier_report(self, task):
        """The report of an earlier session in this workspace, if it answered this task.

        Only the same task: a workspace reused for a genuinely different design
        must still be able to begin.
        """
        try:
            prior = json.loads((self.root / 'outputs' / self.REPORT_FILE).read_text())
        except (OSError, ValueError):
            return None
        words = set(tokens(prior.get('task', '')))
        if not words or len(words & set(tokens(task))) < 0.6 * len(words):
            return None
        return prior

    async def finish_partial(self, reason):
        if self.stage in ('finished', 'close_failed'):
            return self.final_reply
        return await self.finish(reason)
