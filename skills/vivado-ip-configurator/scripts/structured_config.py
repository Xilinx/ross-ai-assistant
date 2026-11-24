# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generic stateful JSON adapter. Candidate IDs bind source metadata to writes."""
import base64
import json
import math
import re
from pathlib import Path

from tcl_data import TclListError, join_list, split_list


# Per-row provenance fields that repeat one value on most rows of a reply.
ROW_DEFAULT_FIELDS = frozenset({'help_source', 'range_complete', 'choices_more', 'help_truncated',
                                'write_mode', 'shape', 'enabled', 'value_src'})


def compact_reply(reply):
    """Copy of `reply` with repeated per-row provenance values stated once, for sending.

    A describe reply lists every candidate parameter as a row, and about a third of its bytes
    were labels such as range_scope or choices_scope repeating the same value on nearly every
    row: one MIPI CSI-2 RX describe reply was 41 KB, and OpenCode cut off a 52 KB one, so Gemma
    never saw its candidates (ip-configurator-test-kit_21, 2026-11-15). For each such field
    (any `*_scope` field, or one in ROW_DEFAULT_FIELDS) that EVERY row carries, the most common
    value moves into `row_defaults` and is dropped from the rows that have it; a row that omits
    the field therefore has exactly that value, so nothing is lost. A row is a dict holding both
    `id` and `key`. Only the reply sent to the agent is compacted: the session's own candidate
    rows and the recorded evidence keep every field. Returns `reply` itself when nothing repeats.
    """
    rows = []

    def walk(node):
        if isinstance(node, dict):
            if 'id' in node and 'key' in node:
                rows.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(reply)
    if len(rows) < 2 or not isinstance(reply, dict):
        return reply
    defaults = {}
    for field in sorted({f for row in rows for f in row}):
        if not (field.endswith('_scope') or field in ROW_DEFAULT_FIELDS):
            continue
        values = [row.get(field, rows) for row in rows]  # `rows` marks a row missing the field
        if any(v is rows or isinstance(v, (dict, list)) for v in values):
            continue
        common = max(set(map(json.dumps, values)), key=[json.dumps(v) for v in values].count)
        if [json.dumps(v) for v in values].count(common) >= 2:
            defaults[field] = json.loads(common)
    if not defaults:
        return reply

    def strip(node):
        if isinstance(node, dict):
            row = 'id' in node and 'key' in node
            return {k: strip(v) for k, v in node.items()
                    if not (row and k in defaults and v == defaults[k])}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    out = strip(reply)
    out['row_defaults'] = dict(defaults, note='A candidate row that omits one of these fields has this value.')
    return out


def run_root(log_file):
    """The run root holding proj/, evidence/ and cache/, from Vivado's session log.

    Mirrors lite::run_root in lib/lite.tcl and ipcfg_op::root in lib/ipcfg_op.tcl.
    Vivado's working directory is meant to be the project directory <root>/proj, so
    the root is its parent. Take the parent only when the working directory is that
    `proj` folder: started anywhere else, its parent is not ours to write in -- on a
    test host it is the directory holding every case's workspace, so each run would
    build its project on top of the last one's. A working directory that is not
    `proj` is its own root, which keeps every run inside it.
    """
    workdir = Path(log_file).resolve().parent.parent
    return workdir.parent if workdir.name == 'proj' else workdir


def atom(value):
    if isinstance(value, bool):
        value = int(value)
    encoded = base64.b64encode(str(value).encode()).decode()
    return '[encoding convertfrom utf-8 [binary decode base64 {' + encoded + '}]]'


def tlist(values):
    return '[list ' + ' '.join(atom(v) for v in values) + ']'


def tdict(values):
    return '[dict create ' + ' '.join(atom(k) + ' ' + atom(v) for k, v in values.items()) + ']'


def split_tcl(value):
    # Tcl list parsing only: decoded model/source data is never evaluated (tcl_data.py).
    try:
        return split_list(str(value))
    except TclListError as exc:
        raise ValueError('Invalid native Tcl data: ' + str(exc)[:160]) from None


def tcl_list_string(values):
    """Canonical Tcl list text for data values, byte-identical to Tcl's own `list`."""
    return join_list(int(v) if isinstance(v, bool) else v for v in values)


def dict_tcl(value):
    values = split_tcl(value)
    if len(values) % 2:
        raise ValueError('Invalid native dict')
    return dict(zip(values[::2], values[1::2]))


class RequestSchemaError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


class StructuredSession:
    def __init__(self, guard, upstream, sid, log_file):
        self.guard, self.upstream, self.sid, self.log_file = guard, upstream, sid, log_file
        self.stage = 'new'
        self.requirements = {}
        self.candidates = {}
        self.bundles = {}
        self.ids = {}
        self.selections = {}
        self.describe_counts = {}
        self.apply_attempts = 0
        self.begin_signature = None
        self.begin_attempts = 0
        self.initialized = False
        self.ip_candidates = {}
        self.native_serial = 0
        self.vector_bases = {}
        self.nested_rows = []
        self.port_cache = {}
        self.final_reply = None
        self.soft_seen = {}            # T6: (rule, rid, cid, value) -> rejections issued
        self.advisory_overrides = []   # T6: soft rejections accepted on an unchanged resubmission
        self.borrowed_session = False
        self.multi_cell_scope = None
        self.api_parameters = None
        self.metadata_fallback = None
        self.waived = {}
        self.exhausted = {}
        self.repeated_selects = 0
        self.asked_queries, self.repeated_queries = {}, {}
        self.unchanged_reviewed = False
        self.coverage_checked = False
        self.unavailable_ips = {}
        self.derived_enables = []
        self.api_help = {}
        self.gui_parameters = None
        self.gui_dependencies = {}
        self.added_requirements = []
        self.contract_reviewed = False
        self.unretained_repairs = {}
        self.chosen_candidate_id = None
        self.candidate_validation = None
        self.rejected_candidates = []
        self.reselection_used = False
        self.successful_apply = False
        self.created_by_session = False
        self.invalid_request = None
        self.invalid_repeat_count = 0
        self.terminal_stop = False
        self.same_category = {}
        self.native_enum_retry_used = False
        self.allow_partial_apply = False
        self.native_assertions = {}
        self.literal_reviewed = False
        self.literal_review_bypass = False
        self.literal_review_initial = []
        self.literal_review_written = {}

    @classmethod
    async def adopted(cls, guard, upstream, sid, log_file, cell, part, task, build, requirements):
        """Discover an existing cell of a design another workflow built.

        A relay (the CED configurator) prepares the project and the populated
        block design, then borrows this engine for one cell of it.  Nothing is
        created here: lite::begin finds the open project outside its own
        scratch directory and reports session_mode borrowed, so finish
        preserves the session instead of closing Vivado.
        """
        session = cls(guard, upstream, sid, log_file)
        session.cell, session.part, session.task, session.build = cell, part, task, build
        session.original_part = part
        session.requirements = dict(requirements)
        session.identify_native_assertions()
        session.coverage_checked = True
        session.hint_check_task = None
        lib = Path(__file__).resolve().parents[1] / 'lib/lite.tcl'
        prefix = ('source -notrace ' + atom(lib) + '\nset __init [lite::begin ' + atom(part) + ' '
                  + atom(log_file) + ']\nif {[lindex $__init end] ne "borrowed"} {error "INIT_FAIL:$__init"}\n')
        vlnv = dict_tcl(await session.native(
            '[dict create vlnv [get_property VLNV [get_bd_cells ' + atom(cell) + ']]]', prefix))['vlnv']
        session.borrowed_session = True
        session.initialized = True
        session.begin_signature = ('adopted', cell)
        return session, await session.open_cell(vlnv)

    async def native(self, expression, prefix=''):
        import time
        from guard_mcp import payload
        if hasattr(self.guard, 'next_native_serial'):
            self.native_serial = self.guard.next_native_serial(self.sid)
        else:
            self.native_serial += 1
        # Each logical request needs its full receipt, including fresh readback.
        # The upstream duplicate cache returns a truncated summary otherwise.
        # __ipcfg_reply_seq is set only after the expression completes, so a reply fetched later
        # can be proven to belong to this request rather than an earlier one.
        command = ('set __ipcfg_request_seq ' + str(self.native_serial) + '\n' + prefix + '\nset __ipcfg_reply '
                   + expression + '\nset __ipcfg_reply_seq ' + str(self.native_serial)
                   + '\nputs "__IPCFG__[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__ipcfg_reply]]"')
        started = time.monotonic()
        result = await self.upstream.call_tool('vivado_execute', {'session_id': self.sid, 'command': command})
        data = payload(result)
        path = self.guard.record({'structured_native': True, 'arguments': {'session_id': self.sid, 'command': command},
                                  'forwarded': True, 'elapsed_seconds': time.monotonic()-started,
                                  'result': result.model_dump(mode='json')})
        if data.get('error_type') == 'STILL_RUNNING_DETACHED':
            # The MCP server's proxy gave up waiting, but Vivado is still running the command.
            # Wait for it and collect the stored reply instead of surfacing an empty error that
            # invites a restart on top of a session that is still busy.
            return await self.recover_detached(self.native_serial, path)
        output = data.get('output', '')
        matches = re.findall(r'__IPCFG__([A-Za-z0-9+/=]+)', output)
        if result.isError or data.get('exit_code', 0) != 0 or not matches:
            raise ValueError('NATIVE_ERROR: ' + output[-700:] + '; evidence=' + str(path))
        return base64.b64decode(matches[-1]).decode()

    DETACHED_WAIT_SECONDS = 900
    DETACHED_POLL_SECONDS = 5

    async def recover_detached(self, serial, evidence):
        """Wait out a detached native command, then read the reply it stored in Vivado."""
        import asyncio
        import time
        from guard_mcp import payload
        deadline = time.monotonic() + self.DETACHED_WAIT_SECONDS
        polls = 0
        while True:
            status = payload(await self.upstream.call_tool('vivado_status', {'session_id': self.sid}))
            polls += 1
            if not status.get('is_command_running'):
                break
            if time.monotonic() > deadline:
                raise ValueError('NATIVE_ERROR: command still running in Vivado after '
                                 + str(self.DETACHED_WAIT_SECONDS) + ' s; evidence=' + str(evidence))
            await asyncio.sleep(self.DETACHED_POLL_SECONDS)
        fetch = ('if {[info exists __ipcfg_reply_seq] && $__ipcfg_reply_seq == ' + str(serial) + '} '
                 '{puts "__IPCFG__[binary encode base64 -maxlen 0 [encoding convertto utf-8 $__ipcfg_reply]]"} '
                 'else {puts "__IPCFG_FAILED__[string range $::errorInfo 0 600]"}')
        result = await self.upstream.call_tool('vivado_execute', {'session_id': self.sid, 'command': fetch})
        data = payload(result)
        path = self.guard.record({'structured_native_recovery': True, 'serial': serial, 'status_polls': polls,
                                  'detached_evidence': str(evidence), 'result': result.model_dump(mode='json')})
        output = data.get('output', '')
        matches = re.findall(r'__IPCFG__([A-Za-z0-9+/=]+)', output)
        if result.isError or not matches:
            raise ValueError('NATIVE_ERROR: detached command did not complete: '
                             + output.split('__IPCFG_FAILED__')[-1][-600:] + '; evidence=' + str(path))
        return base64.b64decode(matches[-1]).decode()

    async def close(self):
        from guard_mcp import payload
        result = await self.upstream.call_tool('vivado_stop', {'session_id': self.sid, 'close_vivado': True})
        self.guard.record({'structured_stop': True, 'arguments': {'session_id': self.sid, 'close_vivado': True},
                           'result': result.model_dump(mode='json')})
        data = payload(result)
        if result.isError or data.get('status') != 'success':
            return False
        self.guard.closed.add(self.sid)
        return True

    async def open_project_part(self):
        """Return Vivado's authoritative part when the supplied session owns a project."""
        expression = ('[apply {{} {set p [current_project -quiet]; '
                      'if {$p eq ""} {return [dict create]}; '
                      'return [dict create part [get_property PART $p]]}}]')
        try:
            return dict_tcl(await self.native(expression)).get('part')
        except Exception:
            return None

    async def existing_cell_vlnv(self, cell):
        """Resolve a named cell from the open design so prose cannot misidentify its IP."""
        expression = ('[apply {{name} {set rows [get_bd_cells -quiet $name]; '
                      'if {[llength $rows] != 1} {return [dict create]}; '
                      'return [dict create vlnv [get_property VLNV $rows]]}} '
                      + atom(cell) + ']')
        try:
            return dict_tcl(await self.native(expression)).get('vlnv')
        except Exception:
            return None

    SOFT_RETRY = (' If you still intend this value, resubmit it unchanged; it will be accepted as advisory '
                  'and verified by readback.')

    def soft_reject(self, rule, rid, cid, candidate, value, message, repairs):
        """T6: a wording heuristic rejects once; an unchanged resubmission is accepted as advisory."""
        key = (rule, rid, cid, str(value).strip().lower())
        if not self.soft_seen.get(key):
            self.soft_seen[key] = 1
            raise ValueError(message + self.SOFT_RETRY)
        self.soft_seen[key] += 1
        self.advisory_overrides.append(dict(requirement_id=rid, key=candidate.get('key'),
                                            subkey=candidate.get('subkey'), value=value, rule=rule))
        repairs.append(dict(candidate_id=cid, repair='advisory_override', rule=rule, message=message[:200]))

    def vivado_closed(self):
        """Whether this session's Vivado was actually stopped (by vivado_stop or a relay's close())."""
        return getattr(self, 'sid', None) in getattr(getattr(self, 'guard', None), 'closed', set())

    SESSION_OPEN_NOTE = ('Saved. Vivado and the project stay open: begin the next cell in this session, '
                         'or call vivado_stop only when the user says the design is done.')

    async def finish_partial(self, reason):
        if self.stage == 'finished':
            return self.state(full=False, session_closed=False, session_preserved=True, reason=reason,
                              next_op='begin', session_note=self.SESSION_OPEN_NOTE,
                              coverage_scope='unresolved_original_requirements')
        receipt = 'SAVE:UNAVAILABLE'
        # A malformed request can trip the repetition breaker before
        # lite::begin establishes ownership.  Preserve a session that already
        # has an open project; its project was prepared by the caller.
        if not self.initialized and not self.borrowed_session:
            self.borrowed_session = bool(await self.open_project_part())
        verified = dict(available=False, reason='no saved configured fields')
        if self.initialized and self.apply_attempts and hasattr(self, 'cell') and hasattr(self, 'vlnv'):
            try:
                receipt = await self.native('[lite::finish ' + atom(self.cell) + ']')
                verified = await self.verified_readback()
            except Exception as exc:
                try:
                    receipt = await self.native('[lite::partial_save]')
                except Exception:
                    receipt = str(exc)[:500]
        elif self.initialized:
            try:
                receipt = await self.native('[lite::partial_save]')
            except Exception as exc:
                receipt = str(exc)[:500]
        saved = self.saved_configuration(verified)
        literal_rows = self.literal_rows(saved)
        literal_gaps = ['literal not realized: ' + text for text in self.unaccounted_literal_texts(literal_rows)]
        # A design is built over many prompts: saving ends this cell, never the Vivado session.
        closed, preserved = False, True
        self.stage = 'finished'
        self.final_reply = self.state(full=False, session_closed=closed,
                          session_preserved=preserved, reason=reason,
                          native_receipt=receipt, coverage_scope='unresolved_original_requirements',
                          requires_original_task_check=True, session_note=self.SESSION_OPEN_NOTE,
                          next_op='begin')
        self.final_reply['saved_result'] = saved
        if literal_gaps:
            self.final_reply['coverage_gaps'] = literal_gaps
        return self.final_reply

    async def bounded_finish(self, reason, **extra):
        """Finish without opening the model review used only by explicit finish."""
        previous = self.literal_review_bypass
        self.literal_review_bypass = True
        try:
            result = await self.request({'op': 'finish'})
        finally:
            self.literal_review_bypass = previous
        result.update(full=False, reason=reason, **extra)
        return result

    def pending(self):
        selected = ({rid for rid, cid in self.selections} | set(self.waived) |
                    set(self.vector_covered()) | set(self.scalar_covered()) |
                    set(self.native_assertions))
        return [dict(id=k, text=v) for k, v in self.requirements.items() if k not in selected]

    def identify_native_assertions(self):
        """Ground cell naming requests in the BD object identity.

        Component_Name is generated metadata and Vivado may refuse writes to
        it.  A request that explicitly names this session's cell is instead
        verified through the native cell NAME at apply/finish time.
        """
        self.native_assertions = {}
        wanted = re.escape(self.cell)
        pattern = re.compile(r'\b(?:(?:cell\s+)?name(?:d)?|called)\s*(?::|=)?\s+(?:the\s+)?(?:cell\s+)?' +
                             wanted + r'\b', re.I)
        for rid, text in self.requirements.items():
            if pattern.search(text):
                self.native_assertions[rid] = dict(
                    requirement_id=rid, key='__CELL_NAME__', wanted=self.cell,
                    scope='native_bd_cell_identity')

    @staticmethod
    def inner_dictionary_value(candidate, value):
        """Requested keys of a small inner dictionary, as Tcl dict text; the server merges them into the live value."""
        fields = candidate.get('fields') or list(dict_tcl(candidate.get('default', '')))
        usage = (candidate['subkey'] + ' is a dictionary with keys ' + ' '.join(fields) +
                 '; select only the keys to change, e.g. {"' + fields[0] + '": <value>}')
        if isinstance(value, str):
            try:
                value = dict_tcl(value)
            except ValueError:
                raise ValueError(usage) from None
        if not isinstance(value, dict) or not value or 'indices' in value:
            raise ValueError(usage)
        by_name = {f.lower(): f for f in fields}
        pairs = []
        for k, v in value.items():
            if str(k).lower() not in by_name:
                raise ValueError('Unknown key ' + str(k) + '; ' + usage)
            if not isinstance(v, (str, int, float, bool)):
                raise ValueError(usage)
            pairs += [by_name[str(k).lower()], int(v) if isinstance(v, bool) else v]
        return tcl_list_string(pairs)

    @staticmethod
    def same_value(row, value):
        from parameter_contract import boolean_value
        current = row.get('current')
        if current is None or row.get('current_truncated') or isinstance(value, dict):
            return False
        if row.get('type', '').lower() in ['bool', 'boolean']:
            try:
                return boolean_value(value) == boolean_value(current)
            except Exception:
                return False
        try:
            return float(value) == float(current)
        except (TypeError, ValueError):
            return str(value).strip().lower() == str(current).strip().lower()

    #: Name words that do not say which interface or instance a setting belongs to.
    GENERIC_NAME_WORDS = {'c', 'cmn', 'common', 'num', 'no', 'of', 'cnt', 'count', 'total', 'cfg', 'user'}

    def same_quantity_unselected(self, selections, batch):
        """Numeric settings offered for a requirement, left unselected, that name the quantity just set.

        "4 active lanes" on a MIPI CSI-2 subsystem is two parameters, C_DPHY_LANES and CMN_NUM_LANES;
        an agent that selects one leaves the other at its default and the cell is half configured.
        One is reported when all of these hold: both are numeric scalars offered for the same
        requirement; their names end in the same word; that word, singular or plural, is in the
        requirement text; the unselected one is writable, enabled and still holds a different value;
        and every other word of its name is either shared with the selected name, generic (NUM, CMN,
        ...) or in the requirement text. The last rule keeps another interface's copy of the
        quantity out (S_AXI_DATA_WIDTH selected, M_AXIS_DATA_WIDTH not offered). Advisory only: the
        agent decides, and nothing is selected for it.
        """
        numeric = lambda row: str(row.get('type', '')).lower() in ['long', 'integer', 'int', 'float', 'double']
        name_words = lambda row: [w for w in re.split(r'[^a-z0-9]+', str(row.get('subkey') or row['key'].split('.', 1)[-1]).lower()) if w]
        taken = {(self.candidates[cid]['key'], self.candidates[cid].get('subkey')) for _, cid in selections}
        result = []
        for rid in sorted(r for r in batch if r in self.requirements):
            text_words = set(re.findall(r'[a-z0-9]+', self.requirements[rid].lower()))
            for (r, cid), item in selections.items():
                row = self.candidates[cid]
                if r != rid or item.get('indices') or not numeric(row) or row.get('shape', 'scalar') != 'scalar':
                    continue
                words = name_words(row)
                if not words or len(words[-1]) < 3:
                    continue
                last = words[-1]
                if not ({last, last.rstrip('s'), last + 's'} & text_words):
                    continue
                also = []
                for other_id, other in self.candidates.items():
                    if (other.get('requirement_id') != rid or other_id == cid or not numeric(other)
                            or (other['key'], other.get('subkey')) in taken or other.get('enabled') is False
                            or other.get('write_mode', 'write') != 'write' or other.get('current') is None
                            or other.get('shape', 'scalar') != 'scalar' or self.same_value(other, item['value'])):
                        continue
                    other_words = name_words(other)
                    if not other_words or other_words[-1] != last:
                        continue
                    if all(w in words or w in self.GENERIC_NAME_WORDS or w in text_words for w in other_words[:-1]):
                        also.append(dict(candidate_id=other_id, key=other['key'],
                                         **({'subkey': other['subkey']} if other.get('subkey') else {}),
                                         label=str(other.get('label', ''))[:80], current=other.get('current')))
                if also:
                    result.append(dict(requirement_id=rid, selected_key=row['key'], value=item['value'], also=also[:3]))
        return result

    def unchanged_selections(self, selections, batch):
        """Batch requirements whose every selection already equals the live value, with differing alternatives."""
        result = []
        for rid in sorted(r for r in batch if r in self.requirements):
            picked = [(cid, item) for (r, cid), item in selections.items() if r == rid]
            if not picked or not all(self.same_value(self.candidates[cid], item['value']) for cid, item in picked):
                continue
            controls=self.port_control_requirements(rid,selections)
            if controls and all(c['covered'] for c in controls):
                continue
            if all(self.value_named_in_text(self.candidates[cid], item['value'], self.requirements[rid]) for cid, item in picked):
                continue
            # A requirement that is the selected setting's own label ("all outputs"
            # -> All Outputs = 1) is already met, not a missed alternative.
            # ced-gen_E2's Gemma-31B run read the pause as a prompt to change
            # something and unbound the board LEDs (GPIO_BOARD_INTERFACE ->
            # Custom) where the verified answer is already_satisfied.
            words = lambda value: {w for w in re.findall(r'[a-z0-9]+', str(value).lower()) if len(w) > 1}
            if all(words(self.candidates[cid].get('label', '')) and words(self.candidates[cid].get('label', ''))
                   <= words(self.requirements[rid]) for cid, _ in picked):
                continue
            chosen = {cid for cid, _ in picked}
            # A board-interface binding is wiring, not a value: switching it to
            # Custom detaches the board part, so it is never offered as the
            # setting a request "really" meant.
            alternatives = [dict(candidate_id=cid, key=row['key'], **({'subkey': row['subkey']} if row.get('subkey') else {}),
                                 label=row.get('label', '')[:80], current=row.get('current'))
                            for cid, row in self.candidates.items()
                            if row.get('requirement_id') == rid and cid not in chosen and row.get('current') is not None
                            and not str(row.get('key', '')).upper().endswith('_BOARD_INTERFACE')
                            and not any(self.same_value(row, item['value']) for _, item in picked)][:3]
            result.append(dict(requirement_id=rid, keys=[self.candidates[cid]['key'] for cid in chosen], alternatives=alternatives))
        return result

    @staticmethod
    def value_named_in_text(row, value, text):
        """The requirement text writes this value, as a number or as the value's choice label."""
        from intent_contract import facts, unit_value, number
        if number(value) is not None and any(unit_value(row,f) is not None and
                math.isclose(number(value),unit_value(row,f),rel_tol=1e-12,abs_tol=1e-9) for f in facts(text)):
            return True
        if number(value)==1 and re.search(r'\bsingle[ -](?:clock|channel|port|output|input|lane|tile|bank|core)s?\b',text,re.I):
            return True
        value = str(value)
        numbers = re.findall(r'(?<![A-Za-z0-9.])(?<![A-Za-z]-)\d+(?:\.\d+)?(?![A-Za-z0-9.])', text)
        try:
            if any(math.isclose(float(value), float(n)) for n in numbers):
                return True
        except ValueError:
            pass
        flat = re.sub(r'[^a-z0-9]', '', text.lower())
        labels = [re.sub(r'[^a-z0-9]', '', str(c.get('label', '')).lower()) for c in row.get('choices') or []
                  if isinstance(c, dict) and str(c.get('value')) == value]
        return any(len(l) >= 3 and l in flat for l in labels)

    def open_pending(self):
        """Unresolved requirements that still have describe budget."""
        return [p for p in self.pending() if p['id'] not in self.exhausted]

    def describe_limit(self, rid):
        from intent_contract import facts
        return 5 if len(facts(self.requirements[rid])) > 1 else 3

    @staticmethod
    def semantic_query(query, requirement):
        """Add a few direction/polarity terms that source metadata actually uses.

        These terms describe generic signal intent. They do not name an IP or a
        property, and the returned candidates still require native validation.
        """
        text = (query + ' ' + requirement).lower()
        additions = []
        if re.search(r'\b(?:drive|drives|driver|drivers|driving|driven|transmit|source)\b', text):
            additions += ['output direction all outputs']
        if re.search(r'\b(?:read|reads|reading|receive|receiving|capture|capturing|sense|sample)\b', text):
            additions += ['input direction all inputs']
        if re.search(r'\bactive[ _-]?low\b', text):
            additions += ['polarity low active']
        if re.search(r'\b(?:expose|exposed|include|included)\b', text):
            additions += ['use enable port']
        if re.search(r'\bsingle[ -]bit\b', text):
            additions += ['width 1 data width']
        if re.search(r'\bnot\s+two\b', text):
            additions += ['disable second instance 2 enable']
        suffix = ' '.join(dict.fromkeys(additions))
        # The ledger is authoritative.  A small model may shorten a follow-up
        # query and drop the token that distinguishes two neighboring fields
        # (NMI versus MI, for example).  Always retain the original words while
        # still putting the caller's refinement first.
        base = query.strip()
        if re.sub(r'[^a-z0-9]', '', base.lower()) != re.sub(r'[^a-z0-9]', '', requirement.lower()):
            base += ' ' + requirement.strip()
        return (base + (' ' + suffix if suffix else ''))[:600]

    @staticmethod
    def actionable_requirement(text):
        """True when prose requests observable IP behavior rather than context."""
        return bool(re.search(
            r'\b(?:drive|drives|driving|read|reads|reading|receive|receiving|capture|capturing|transmit|enable[ds]?|disable[ds]?|'
            r'expose[ds]?|include[ds]?|active[ _-]?(?:low|high)|input|output)s?\b', text, re.I))

    def saved_configuration(self, verified):
        """Compact native observations for the final user-visible report."""
        fields = {}
        for row in verified.get('fields', []) if verified.get('available') else []:
            key = row.get('key')
            if not key or 'actual' not in row:
                continue
            name = key + ('.' + row['subkey'] if row.get('subkey') else '')
            fields[name] = str(row['actual'])
        return dict(identified_ip=self.vlnv.split(':')[-2] if getattr(self, 'vlnv', None) else None,
                    vlnv=getattr(self, 'vlnv', None), as_configured=fields,
                    source='saved_native_readback')

    def literal_rows(self, saved):
        from literal_coverage import check
        identity = (getattr(self, 'ip_hint', '') + ' ' + str(saved.get('vlnv') or '')).strip()
        return check(getattr(self, 'task', ''), saved.get('as_configured') or {}, identity)

    @staticmethod
    def unaccounted_literal_texts(rows):
        return list(dict.fromkeys(row['text'] for row in rows if row.get('accounted') is None))

    def vector_covered(self):
        """Requirements whose own described vector key was written, by another selection, with a number they name."""
        covered, selected = {}, {rid for rid, _ in self.selections}
        for rid, text in self.requirements.items():
            if rid in selected or rid in self.waived:
                continue
            numbers = {float(n) for n in re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\w.])', text)}
            own_keys = {c['key'] for c in self.candidates.values() if c.get('requirement_id') == rid}
            for (other, cid), item in self.selections.items():
                key = self.candidates.get(cid, {}).get('key')
                if key not in own_keys or not item.get('indices'):
                    continue
                hits = []
                for index, value in item['indices'].items():
                    try:
                        if not isinstance(value, bool) and float(value) in numbers:
                            hits.append(index)
                    except (TypeError, ValueError):
                        pass
                if hits:
                    covered[rid] = dict(requirement_id=rid, covered_by=other, key=key, indices=sorted(hits, key=int))
                    break
        return covered

    @staticmethod
    def requested_direction(text):
        """'outputs' or 'inputs' when a requirement asks for signal direction, else None.

        A drive/receive verb or an explicit "output"/"input" role names direction;
        "output value/data/default" names payload content instead.
        """
        wants_output = bool(re.search(
            r'\b(?:drive|drives|driver|drivers|driving|transmit|write|writes|writing)\b|'
            r'\ball[ _-]outputs\b|\boutput[ _-]direction\b|'
            r'\b(?:gpio|channel|signal|line|port)\s+(?:is\s+)?output\b|'
            r'\b(?:is|as)\s+(?:an?\s+)?output\b', text, re.I))
        wants_input = bool(re.search(
            r'\b(?:read|reads|reading|receive|receiving|capture|capturing)\b|'
            r'\ball[ _-]inputs\b|\binput[ _-]direction\b|'
            r'\b(?:gpio|channel|signal|line|port)\s+(?:is\s+)?input\b|'
            r'\b(?:is|as)\s+(?:an?\s+)?input\b', text, re.I))
        if re.search(r'\bdefault(?:s|ed)?\b|\b(?:input|output)\s+(?:value|data)\b', text, re.I):
            return None
        if wants_output == wants_input:
            return None
        return 'outputs' if wants_output else 'inputs'

    @staticmethod
    def is_direction_field(row, direction_word):
        """True when a candidate is a direction control such as All Outputs or OUTPUT_DIRECTION."""
        field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
        return bool(re.search(r'(?:all[_ ]' + direction_word + r'|' + direction_word.rstrip('s') + r'[_ ]direction)',
                              field, re.I))

    def scalar_covered(self):
        """Share a discovered source setting, never a coincidentally equal value."""
        from intent_contract import facts, normalized, uncovered_values
        selected={rid for rid,_ in self.selections}
        covered={}
        for rid,text in self.requirements.items():
            if rid in selected or rid in self.waived:
                continue
            own={self.row_identity(row) for row in self.candidates.values() if row.get('requirement_id')==rid}
            matches=[(other,self.candidates[cid],item) for (other,cid),item in self.selections.items()
                     if not item.get('indices') and self.row_identity(self.candidates[cid]) in own
                     and not self.qualified_source_alternatives(rid,self.candidates[cid])]
            # Discovery can attach one live enum to a neighboring requirement
            # even though that enum also implements a short task value retained
            # as its own requirement (for example C-PHY versus CPHY).  Accept
            # only exact punctuation-insensitive literal/identifier values that
            # are declared by the selected candidate's live choices.  This does
            # not share numeric coincidences, substrings, or inferred encodings.
            wanted=facts(text)
            if (not matches and text in self.added_requirements and wanted
                    and all(f['unit'] in ['literal','identifier'] for f in wanted)):
                literal_matches=[]
                for (other,cid),item in self.selections.items():
                    if item.get('indices'):
                        continue
                    row=self.candidates[cid]
                    selected_choice=next((c for c in row.get('choices',[])
                                          if str(c.get('value'))==str(item.get('value'))),None)
                    if not selected_choice:
                        continue
                    encodings={normalized(item.get('value')),
                               normalized(selected_choice.get('label',''))}
                    if all(normalized(f['text']) in encodings for f in wanted):
                        literal_matches.append((other,row,item))
                if literal_matches:
                    matches=literal_matches
            if not matches:
                continue
            # "Single-bit ... to drive X" carries a direction as well as a width; a
            # shared width alone does not cover it.
            direction=self.requested_direction(text)
            if direction and not any(self.is_direction_field(self.candidates[cid],direction)
                                     for (_,cid) in self.selections if cid in self.candidates):
                continue
            controls=self.port_control_requirements(rid)
            if any(not c['covered'] for c in controls):
                continue
            if facts(text):
                if uncovered_values(text,[(row,item) for _,row,item in matches]):
                    continue
            else:
                # A bare exposure request needs every named feature to be a
                # source-proven Boolean port control. Unknown prose is not
                # silently discharged by an unrelated matching field.
                descriptors={'the','a','an','with','and','signal','signals','port','ports','expose','exposed',
                             'enable','enabled','disable','disabled','include','included','use','using',
                             'on','off','no','without','input','inputs','output','outputs'}
                clean=re.sub(r'\br\d+\s*:', '', text.lower())
                requested=set(re.findall(r'[a-z]+',clean))-descriptors
                known={w for c in controls for w in re.findall(r'[a-z]+',c['feature'].lower())}
                if not controls or not requested or not requested <= known:
                    continue
            covered[rid]=dict(requirement_id=rid,covered_by=sorted({other for other,_,_ in matches}),
                fields=[dict(key=row['key'],**({'subkey':row['subkey']} if row.get('subkey') else {})) for _,row,_ in matches],
                scope='selected_source_settings_require_saved_readback')
        return covered

    @staticmethod
    def compact(row, cid):
        fields = ['units', 'units_source', 'source_transform', 'derived_from', 'derived_scope', 'subkey', 'mapping_scope', 'label', 'gui_context', 'gui_context_scope', 'enabled', 'enablement_scope', 'default', 'default_truncated', 'type', 'range', 'range_scope', 'range_complete', 'help', 'help_source', 'hint_scope', 'choices_more', 'choices_scope', 'help_truncated', 'range_truncated', 'matched_identifier_terms', 'related_to', 'relation_scope', 'dependency_ports', 'dependency_scope', 'value_src', 'write_mode', 'live_scope', 'shape', 'fields', 'length', 'element_type', 'current', 'current_truncated', 'family_pattern', 'family_size', 'family_scope']
        result = dict(id=cid, key=row['key'], **{k: row[k] for k in fields if k in row})
        if row.get('choices'):
            result['choices'] = row['choices']
        for field in ['enablement_controls', 'enablement_requires', 'enablement_comparisons', 'enablement_source_scope', 'enablement_observed_part']:
            if row.get(field):
                result[field] = row[field]
        return result

    def has_database(self):
        """A mounted snapshot or an adopted release database; relays that predate the release
        database expose only database_config, so fall back to that."""
        probe = getattr(self.guard, 'has_database', None)
        return bool(probe() if callable(probe) else getattr(self.guard, 'database_config', None))

    def state(self, **extra):
        result = dict(stage=self.stage, unresolved=self.pending(), next_op='select' if self.pending() and any(c['requirement_id'] is None for c in self.candidates.values()) else 'describe' if self.pending() else 'apply')
        if self.pending() and self.selections and not self.open_pending():
            result['next_op'] = 'apply'
        if self.exhausted:
            result['exhausted_requirements'] = [p['id'] for p in self.pending() if p['id'] in self.exhausted]
        if getattr(self, 'part_change', None) and self.stage == 'discovering' and not self.candidates:
            result['part_change'] = self.part_change
        result['next_op'] = {'new': 'begin', 'configured': 'finish',
                             'apply_failed': 'select_or_finish' if self.apply_attempts < 2 else 'finish',
                             'literal_review': 'select|finish',
                             'choosing_ip': 'choose_ip', 'initialization_failed': 'vivado_stop',
                             'finished': 'stop' if self.vivado_closed() else 'begin',
                             'close_failed': 'vivado_stop'}.get(self.stage, result['next_op'])
        covered = self.vector_covered()
        if covered:
            result['covered_by_vector'] = list(covered.values())
        scalar=self.scalar_covered()
        if scalar:
            result['covered_by_source']=list(scalar.values())
        if self.added_requirements:
            result['added_task_requirements'] = self.added_requirements
        if self.multi_cell_scope:
            result['multi_cell_scope'] = self.multi_cell_scope
        result.update(extra)
        return result

    @staticmethod
    def ip_name(hint):
        for separator in [':', '|']:
            parts = hint.split(separator)
            if len(parts) in [3, 4] and re.fullmatch(r'\d+(?:\.\d+)*', parts[-1]):
                return parts[-2].strip()
        return hint.strip()

    STOPWORDS = {'the', 'and', 'for', 'with', 'that', 'this', 'into', 'from', 'one', 'two', 'three',
                 'four', 'block', 'core', 'amd', 'xilinx', 'using', 'use', 'set', 'enable', 'enabled',
                 'mode', 'configure', 'create', 'add', 'instantiate', 'design', 'default', 'configuration'}

    @classmethod
    def significant_terms(cls, text):
        words = [w.lower() for w in re.findall(r'[A-Za-z][A-Za-z0-9]*', text)]
        return list(dict.fromkeys(w for w in words if len(w) >= 3 and w not in cls.STOPWORDS))

    @staticmethod
    def candidate_terms(text):
        return [w.lower() for w in re.findall(r'[A-Za-z][A-Za-z0-9]*', text or '')]

    @staticmethod
    def term_matches(task_term, candidate_term):
        return task_term == candidate_term or (min(len(task_term), len(candidate_term)) >= 3 and
                                                (task_term in candidate_term or candidate_term in task_term))

    @classmethod
    def rerank_ip_candidates(cls, task, rows):
        """Stable task-first ranking over a bounded live-catalog page."""
        task_terms = cls.significant_terms(task)
        ranked = []
        for position, source in enumerate(rows, 1):
            row = dict(source)
            catalog_position = int(row.get('original_catalog_position', position))
            basename = row['vlnv'].split(':')[2] if len(row['vlnv'].split(':')) > 2 else row['vlnv']
            base_terms = cls.candidate_terms(basename)
            display_terms = cls.candidate_terms(row.get('display_name') or row.get('label', ''))
            sequences = [base_terms, display_terms]
            best = []
            for start in range(len(task_terms)):
                for sequence in sequences:
                    for candidate_start in range(len(sequence)):
                        phrase = []
                        while (start + len(phrase) < len(task_terms) and
                               candidate_start + len(phrase) < len(sequence) and
                               cls.term_matches(task_terms[start + len(phrase)], sequence[candidate_start + len(phrase)])):
                            phrase.append(task_terms[start + len(phrase)])
                        if len(phrase) > len(best):
                            best = phrase
            base_hits = [term for term in task_terms if any(cls.term_matches(term, item) for item in base_terms)]
            display_hits = [term for term in task_terms if any(cls.term_matches(term, item) for item in display_terms)]
            first = int(bool(task_terms and (task_terms[0] in base_hits or task_terms[0] in display_hits)))
            row.update(matched_phrase=' '.join(best), basename_terms=base_hits,
                       display_terms=display_hits, original_catalog_position=catalog_position)
            ranked.append(((-len(best), -first, -len(base_hits), -len(set(base_hits + display_hits)), catalog_position), row))
        return [row for _, row in sorted(ranked, key=lambda item: item[0])]

    def explicit_identity_candidate(self):
        """Return a uniquely stronger supported multiword identity, if one exists.

        The begin hint contributes only when all of its significant words occur
        in the task.  A single generic word such as ``interrupt`` or ``versal``
        is insufficient, and a tied multiword phrase remains ambiguous.
        """
        if self.hint_terms_missing_from_task(getattr(self, 'ip_hint', ''), getattr(self, 'task', '')):
            return None
        # When the named IP itself is not on the part, a partial word match (AXI Protocol Checker
        # for "AXI protocol converter") is no stronger identity than a same-function stand-in.
        flat = lambda text: re.sub(r'[^a-z0-9]', '', (text or '').lower())
        hint = flat(self.ip_name(getattr(self, 'ip_hint', '') or ''))
        if hint and any(cid.startswith('u') and hint in (flat(row['vlnv'].split(':')[2]), flat(row.get('display_name')))
                        or (cid.startswith('u') and flat(row['vlnv'].split(':')[2]).startswith(hint) and len(hint) >= 8)
                        for cid, row in self.ip_candidates.items()):
            return None
        supported = [(cid, row) for cid, row in self.ip_candidates.items()
                     if row.get('supported_on_current_part') is not False and not row.get('rejected')]
        if len(supported) < 2:
            return None
        preferred, first = supported[0]
        if first.get('source') != 'begin_ip_hint':
            return None
        strength = len(self.significant_terms(first.get('matched_phrase', '')))
        others = [len(self.significant_terms(row.get('matched_phrase', ''))) for _, row in supported[1:]]
        return preferred if strength >= 2 and strength > max(others, default=0) else None

    def hint_terms_missing_from_task(self, hint, task):
        """IP hint words that the original task never uses (prefix-tolerant)."""
        task_terms = self.significant_terms(task)
        if not task_terms or re.fullmatch(r'[\w.-]+:[\w.-]+:[\w.-]+(:[\w.-]+)?', hint.strip()):
            return []
        return [h for h in self.significant_terms(self.ip_name(hint))
                if not any(t.startswith(h) or h.startswith(t) for t in task_terms)]

    async def task_ip_candidates(self, task):
        terms = self.significant_terms(task)[:16]
        if not terms:
            return []
        native = split_tcl(await self.native('[lite::catalog ' + atom(' '.join(terms)) + ' 6 0 0]'))
        self.note_unavailable(native)
        fields = dict(zip(native[1::2], native[2::2]))
        if native[0] == 'CATALOG:EXACT':
            return [dict(vlnv=fields['vlnv'], source='task_text_catalog')]
        rows = [dict(dict_tcl(r), source='task_text_catalog') for r in split_tcl(fields.get('shown', ''))]
        return self.rerank_ip_candidates(task, rows)

    async def available_ip_candidates(self, rows):
        """Check part support once, after candidates from all queries are ranked."""
        import asyncio
        rows = rows[:6]
        if not rows:
            return []
        # The release database records each IP's supported parts. Reading SUPPORTED_PARTS live
        # (and probing get_parts for example parts) took 100+ s for six candidates, past an
        # agent's per-call MCP limit; the live check now runs only for what it does not record.
        status, alternatives = {}, {}
        if self.has_database():
            try:
                reply = await asyncio.to_thread(self.guard.database_request, json.dumps(dict(
                    op='part_support', vlnvs=[row['vlnv'] for row in rows], part=self.part, build=self.build)))
                for item in reply.get('rows', []):
                    if item.get('status') in ('AVAILABLE', 'WRONG_PART'):
                        status[item['vlnv']] = dict(item, example_parts=' '.join(item.get('example_parts', [])))
                        alternatives[item['vlnv']] = item.get('alternatives') or []
                        if item.get('same_category'):
                            self.same_category[item['vlnv']] = item['same_category']
            except Exception as exc:
                self.guard.record({'part_support_database': False, 'error': str(exc)[:240]})
        live = [row['vlnv'] for row in rows if row['vlnv'] not in status]
        if live:
            expression = '[lite::candidate_availability [list ' + ' '.join(atom(v) for v in live) + ']]'
            native = split_tcl(await self.native(expression))
            if not native or native[0] != 'AVAILABILITY:OK':
                raise ValueError('Candidate availability check failed')
            fields = dict(zip(native[1::2], native[2::2]))
            for item in (dict_tcl(value) for value in split_tcl(fields.get('rows', ''))):
                if item.get('same_category'):
                    # No release database: the live same-category pool (lite::same_category_live).
                    pool = [dict_tcl(entry) for entry in split_tcl(item.pop('same_category'))]
                    if pool:
                        self.same_category[item['vlnv']] = pool
                if item.get('alternatives'):
                    # No release database: the same stand-in rule, read live (lite::alternatives_live).
                    alternatives[item['vlnv']] = [dict_tcl(entry) for entry in split_tcl(item.pop('alternatives'))]
                status[item['vlnv']] = item
        self.guard.record({'structured_part_support': True, 'from_database': len(rows) - len(live), 'live': len(live)})
        supported = []
        for row in rows:
            check = status.get(row['vlnv'], {})
            if check.get('status') != 'WRONG_PART':
                supported.append(row)
                continue
            unavailable = dict(row, supported_on_current_part=False,
                               supported_part_count=int(check.get('supported_count') or 0),
                               example_parts=split_tcl(check.get('example_parts', ''))[:4],
                               source='catalog_other_parts')
            self.unavailable_ips[row['vlnv']] = unavailable
            # A same-function IP this part does support, offered beside the one it cannot host.
            for alt in alternatives.get(row['vlnv'], []):
                if alt['vlnv'] not in {r['vlnv'] for r in rows + supported}:
                    supported.append(dict(vlnv=alt['vlnv'], display_name=alt.get('display_name') or '',
                                          source='release_database_alternative', alternative_to=row['vlnv'],
                                          alternative_match=alt.get('match'), supported_on_current_part=True,
                                          requires_live_validation=True))
        return supported

    def note_unavailable(self, native):
        """Catalog matches that the current part does not support, with installed parts that do."""
        if not native:
            return
        fields = dict(zip(native[1::2], native[2::2]))
        rows = [fields] if native[0] == 'CATALOG:UNAVAILABLE' else [dict_tcl(r) for r in split_tcl(fields.get('unavailable', ''))]
        for row in rows:
            if row.get('vlnv') and row.get('example_parts'):
                self.unavailable_ips.setdefault(row['vlnv'], dict(
                    vlnv=row['vlnv'], **({'display_name': row['display_name']} if row.get('display_name') else {}),
                    supported_on_current_part=False, supported_part_count=int(row.get('supported_count') or 0),
                    example_parts=split_tcl(row['example_parts'])[:4], source='catalog_other_parts'))

    async def seed_part_support(self):
        """Give lite.tcl the release's part-support table for the project part.

        lite::catalog read SUPPORTED_PARTS twice for an IP the part cannot host (about 100 s
        each for vcu:1.2 on 2026.2), which took case 28 past its time budget. Best effort: a
        missing database or unknown part leaves the live reads in place.
        """
        import asyncio
        if not self.has_database():
            return
        try:
            reply = await asyncio.to_thread(self.guard.database_request, json.dumps(dict(
                op='support_table', part=self.part, build=self.build)))
            table = reply.get('table') or {}
            if not table:
                return
            body = ' '.join(atom(v) + ' [list ' + atom(row[0]) + ' ' + atom(str(row[1])) + ' ' + tlist(row[2]) + ']'
                            for v, row in table.items())
            await self.native('[lite::seed_support ' + atom(self.part) + ' [dict create ' + body + ']]')
            self.guard.record({'seed_part_support': True, 'part': self.part, 'ips': len(table)})
        except Exception as exc:
            self.guard.record({'seed_part_support': False, 'error': str(exc)[:240]})

    async def add_database_alternatives(self):
        """Offer a same-function IP this part supports for each catalog match it cannot host.

        Without it an agent asked for a Gen 1 IP on a Gen 2 part (axi_noc, versal_cips) saw only
        the unsupported IP and unrelated text matches, and stopped to ask for a part change.
        """
        import asyncio
        offered = {row['vlnv'] for row in self.ip_candidates.values()}
        pending = [v for v in self.unavailable_ips if v not in offered][:6]
        if not pending:
            return
        try:
            if self.has_database():
                reply = await asyncio.to_thread(self.guard.database_request, json.dumps(dict(
                    op='part_support', vlnvs=pending, part=self.part, build=self.build)))
                rows = reply.get('rows', [])
            else:
                rows = await self.live_part_support(pending)
        except Exception as exc:
            self.guard.record({'part_support_alternatives': False, 'error': str(exc)[:240]})
            return
        for item in rows:
            if item.get('same_category'):
                self.same_category = getattr(self, 'same_category', {})
                self.same_category[item['vlnv']] = item['same_category']
            for alt in item.get('alternatives') or []:
                if alt['vlnv'] in offered:
                    # Already offered from the task text: mark it as the stand-in rather than
                    # leaving the agent to connect it to the unsupported IP on its own.
                    for row in self.ip_candidates.values():
                        if row['vlnv'] == alt['vlnv'] and not row.get('alternative_to'):
                            row.update(alternative_to=item['vlnv'], alternative_match=alt.get('match'),
                                       supported_on_current_part=True)
                    continue
                offered.add(alt['vlnv'])
                self.ip_candidates['i' + str(len(self.ip_candidates) + 1)] = dict(
                    vlnv=alt['vlnv'], display_name=alt.get('display_name') or '',
                    source='release_database_alternative', alternative_to=item['vlnv'],
                    alternative_match=alt.get('match'), supported_on_current_part=True,
                    requires_live_validation=True)

    async def live_part_support(self, vlnvs):
        """The release database's part_support rows, read live, for a release that ships none.

        2026.1 has no IP database, so a NoC request that reached axi_noc only as a catalog
        match the part cannot host got no axi_noc2 stand-in, and the agent stopped
        (ip-configurator-test-kit_26). lite::candidate_availability applies the same rule.
        """
        native = split_tcl(await self.native('[lite::candidate_availability [list ' + ' '.join(atom(v) for v in vlnvs) + ']]'))
        if not native or native[0] != 'AVAILABILITY:OK':
            raise ValueError('Candidate availability check failed')
        rows = []
        for item in (dict_tcl(value) for value in split_tcl(dict(zip(native[1::2], native[2::2])).get('rows', ''))):
            row = dict(vlnv=item['vlnv'], status=item.get('status'))
            if item.get('alternatives'):
                row['alternatives'] = [dict_tcl(entry) for entry in split_tcl(item['alternatives'])]
            if item.get('same_category'):
                row['same_category'] = [dict_tcl(entry) for entry in split_tcl(item['same_category'])]
            rows.append(row)
        self.guard.record({'live_part_support': True, 'vlnvs': vlnvs, 'rows': rows})
        return rows

    def add_same_category_choices(self, standins, limit=10):
        """Offer supported same-category IPs of an unsupported IP that has no stand-in, as c* candidates."""
        task_terms = set(self.significant_terms(getattr(self, 'task', '') or ''))
        offered = {r['vlnv'] for r in self.ip_candidates.values()}
        pool = []
        for cid, row in list(self.ip_candidates.items()):
            if not cid.startswith('u') or row['vlnv'] in standins:
                continue
            for entry in getattr(self, 'same_category', {}).get(row['vlnv'], []):
                if entry.get('vlnv') and entry['vlnv'] not in offered:
                    words = set(self.significant_terms(' '.join([entry.get('display_name') or '', entry.get('description') or ''])))
                    pool.append((-len(task_terms & words), entry.get('name', ''), entry, row['vlnv']))
                    offered.add(entry['vlnv'])
        added = []
        for _, _, entry, unsupported in sorted(pool, key=lambda item: (item[0], item[1]))[:limit]:
            cid = 'c' + str(len(added) + 1)
            self.ip_candidates[cid] = dict(vlnv=entry['vlnv'], display_name=entry.get('display_name') or '',
                                           description=entry.get('description') or '', source='same_category_supported',
                                           alternative_to=unsupported, supported_on_current_part=True,
                                           requires_live_validation=True)
            added.append(cid)
        return added

    def add_unavailable_choices(self):
        for row in list(self.unavailable_ips.values())[:3]:
            if row['vlnv'] not in {r['vlnv'] for r in self.ip_candidates.values()}:
                self.ip_candidates['u' + str(sum(c.startswith('u') for c in self.ip_candidates) + 1)] = row
        if any(c.startswith('u') for c in self.ip_candidates):
            note = ('u* candidates match the catalog but do not support the current part ' + self.part + '. '
                    'Choose one only if the task names that IP or approves changing the part or device: '
                    '{"op":"choose_ip","candidate_id":"u1","part":"<one of its example_parts>"} changes the project part before the cell is created.')
            # Point at the same-function IP this part supports, so an unsupported name is not
            # read as a reason to stop and ask for another device (case 31: versal_cips was
            # flagged, ps_wizard was offered as i2, and the agent asked for a part change).
            standins = {}
            for cid, row in self.ip_candidates.items():
                if not cid.startswith('u') and row.get('alternative_to'):
                    standins.setdefault(row['alternative_to'], []).append(cid)
            pointers = ['; '.join(standins[r['vlnv']]) + ' is the same-function IP this part supports in place of '
                        + cid + ' (' + r['vlnv'] + ')'
                        for cid, r in self.ip_candidates.items() if cid.startswith('u') and r['vlnv'] in standins]
            # No strong stand-in, but the release lists supported IPs of the same catalog category
            # and interfaces (#674: the AXI Protocol Converter is not on Versal; SmartConnect does
            # the conversion). They become choosable c* candidates, ranked by the task's wording,
            # each with its catalog description, so the agent picks one by function.
            added = self.add_same_category_choices(standins)
            if added:
                note += (' The IP the task names is not available on this part, so the requested function is the '
                         'requirement: unless the task explicitly asks for a part change, choose the c* candidate that '
                         'performs it (' + ', '.join(added) + ') with '
                         '{"op":"choose_ip","candidate_id":"c1"}, configure it, and report the substitution. Do not stop '
                         'to ask unless the user forbade substitutes.')
            if pointers:
                note += (' ' + '. '.join(pointers) + '. Prefer it over changing the part: choose it with '
                         '{"op":"choose_ip","candidate_id":"' + standins[next(iter(
                             r['vlnv'] for c, r in self.ip_candidates.items() if c.startswith('u') and r['vlnv'] in standins))][0]
                         + '"} unless the task explicitly requires the other IP or a different device.')
            return dict(part_note=note)
        return {}

    async def open_exact(self, hint, vlnv):
        """Open the cell for an exact catalog match, unless the request names a different catalog IP.

        An agent can put one IP in `ip` and name another in its own task or requirement text: ip
        `axi_fifo_mm_s` with the requirement "IP is AXI4-Stream Data FIFO (axis_data_fifo)". The
        word check in discover_ip compares terms of three letters or more, so two names that differ
        only in short tokens (mm, s) pass it, the wrong cell is created, and no later begin can
        replace it. Only identifier-shaped tokens (lower case, containing an underscore) are looked
        up, at most four, and only one that is itself an exact catalog name of ANOTHER IP stops the
        open; prose and parameter names never do. The agent then chooses between the two.
        """
        text = ' '.join([getattr(self, 'task', '') or ''] + list(getattr(self, 'requirements', {}).values()))
        own, cell = vlnv.split(':')[2].lower(), str(getattr(self, 'cell', '')).lower()
        names = [n for n in dict.fromkeys(re.findall(r'(?<![\w.])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w.])', text))
                 if n not in (own, cell)][:4]
        named = []
        for name in names:
            try:
                native = split_tcl(await self.native('[lite::catalog ' + atom(name) + ' 6 0 0]'))
            except Exception:
                continue
            if native and native[0] == 'CATALOG:EXACT':
                other = dict(zip(native[1::2], native[2::2])).get('vlnv', '')
                if other and other != vlnv and other not in [r['vlnv'] for r in named]:
                    named.append(dict(vlnv=other, source='named_in_requirements', named_as=name))
        if not named:
            return await self.open_cell(vlnv)
        self.ip_candidates = {}
        for row in await self.available_ip_candidates(named + [dict(vlnv=vlnv, source='begin_ip_hint')]):
            self.ip_candidates['i' + str(len(self.ip_candidates) + 1)] = dict(row, requires_live_validation=True)
        self.stage = 'choosing_ip'
        part_note = self.add_unavailable_choices()
        return self.state(ip_candidates=[dict(id=cid, **row) for cid, row in self.ip_candidates.items()], **part_note,
                          ip_hint_check=dict(hint=hint, named_in_requirements=[r['named_as'] for r in named],
                                             note='The begin ip and the task or requirement text name different catalog IPs. '
                                                  'No cell was created. Choose the IP the task describes with choose_ip.'))

    async def discover_ip(self, hint):
        """Exact identities are automatic; fuzzy source candidates need a choice.

        A hint whose words the original task never uses is checked against the
        catalog matches for the task text before any cell is created.
        """
        task = getattr(self, 'hint_check_task', None)
        missing = self.hint_terms_missing_from_task(hint, task) if task else []
        native = split_tcl(await self.native('[lite::catalog ' + atom(hint) + ' 6 0 0]')) if task else ['']
        self.note_unavailable(native)
        if missing or (task and native[0] != 'CATALOG:EXACT'):
            from_task = await self.task_ip_candidates(task)
            fields = dict(zip(native[1::2], native[2::2]))
            from_hint = ([dict(vlnv=fields['vlnv'])] if native[0] == 'CATALOG:EXACT' else
                         [dict_tcl(r) for r in split_tcl(fields.get('shown', ''))])
            from_hint = [dict(r, source='begin_ip_hint') for r in from_hint[:3]]
            if from_task and from_hint and from_task[0]['vlnv'] == from_hint[0]['vlnv'] and native[0] == 'CATALOG:EXACT':
                return await self.open_exact(hint, from_hint[0]['vlnv'])
            if from_task:
                self.ip_candidates, seen = {}, set()
                combined = []
                for row in from_task + from_hint:
                    if row['vlnv'] in seen:
                        continue
                    seen.add(row['vlnv'])
                    combined.append(row)
                ranked = await self.available_ip_candidates(self.rerank_ip_candidates(task, combined))
                for row in ranked:
                    self.ip_candidates['i'+str(len(self.ip_candidates)+1)] = dict(row, requires_live_validation=True)
                await self.add_database_alternatives()
                self.stage = 'choosing_ip'
                part_note = self.add_unavailable_choices()
                return self.state(ip_candidates=[dict(id=cid, **row) for cid, row in self.ip_candidates.items()], **part_note,
                                  ip_hint_check=dict(hint=hint, terms_not_in_task=missing[:6],
                                                     note=('The begin ip names words the original task does not use. ' if missing else
                                                           'The begin ip has no exact catalog match. ') +
                                                     'task_text_catalog rows match the task wording; choose the IP the task describes.'))
        if native[0] == 'CATALOG:EXACT':
            return await self.open_exact(hint, dict(zip(native[1::2], native[2::2]))['vlnv'])
        import asyncio
        normalized = lambda value: re.sub(r'[^a-z0-9]', '', value.lower())
        name = self.ip_name(hint)
        query = re.sub(r'[^A-Za-z0-9]+', ' ', name).strip()[:600]
        database_rows = []
        if self.has_database() and query:
            try:
                reply = await asyncio.to_thread(self.guard.database_request, json.dumps(
                    dict(op='ips', query=query, build=self.build, mode='lexical', part=self.part)))
                exact = [c['vlnv'] for c in reply.get('candidates', []) if normalized(name) in
                         [normalized(c.get('label', '')), normalized(c['vlnv'].split(':')[2])]]
                if len(set(exact)) == 1:
                    hint = exact[0]
            except Exception:
                pass  # A missing/stale snapshot cannot establish live identity.
        native = split_tcl(await self.native('[lite::catalog ' + atom(hint) + ']'))
        self.note_unavailable(native)
        fields = dict(zip(native[1::2], native[2::2]))
        if native[0] == 'CATALOG:EXACT':
            return await self.open_exact(hint, fields['vlnv'])
        if self.has_database() and query:
            try:
                reply = await asyncio.to_thread(self.guard.database_request, json.dumps(
                    dict(op='ips', query=query, build=self.build, mode='hybrid', part=self.part)))
                database_rows = reply.get('candidates', [])[:3]
                self.guard.record({'structured_ip_discovery': True, 'backend': 'database',
                                   'query': query, 'candidates': database_rows})
            except Exception:
                pass
        rows = [dict(vlnv=r['vlnv'], label=r.get('label', ''), description=r.get('description', ''),
                     source='database', requires_live_validation=True) for r in database_rows]
        rows += [dict(dict_tcl(r), source='live_catalog') for r in split_tcl(fields.get('shown', ''))]
        self.ip_candidates = {}
        seen = set()
        for row in self.rerank_ip_candidates(getattr(self, 'task', hint), rows):
            if row['vlnv'] in seen:
                continue
            seen.add(row['vlnv'])
            self.ip_candidates['i'+str(len(self.ip_candidates)+1)] = row
            if len(self.ip_candidates) == 6:
                break
        await self.add_database_alternatives()
        part_note = self.add_unavailable_choices()
        if not self.ip_candidates:
            raise ValueError('No catalog candidates for this IP name; use a different IP name or stop')
        self.stage = 'choosing_ip'
        return self.state(ip_candidates=[dict(id=cid, **row) for cid, row in self.ip_candidates.items()], **part_note)

    @staticmethod
    def uncovered_task_terms(task, texts, ip):
        """Task terms carrying a number that no requirement or the IP name contains."""
        covered = ' '.join(list(texts) + [ip]).lower()
        present = lambda part: re.search((r'(?<![0-9.])' + re.escape(part) + r'(?![0-9])') if part[0].isdigit()
                                         else (r'\b' + re.escape(part)), covered)
        found = []
        for token in re.findall(r'[A-Za-z0-9][A-Za-z0-9_.+-]*[A-Za-z0-9]|[0-9]', task):
            if not re.search(r'[0-9]', token) or '_' in token or token in found or token.lower() in covered:
                continue
            if not all(present(p) for p in re.split(r'[-+]', token.lower()) if p):
                found.append(token)
        return found[:12]

    def run_root(self):
        """The run root holding evidence/: the one lite::begin reported, else derived.

        Before begin has reported it, lite::begin's own rule applies: a borrowed session is
        rooted at the working directory itself, because the relay owns the directory above it;
        an owned one at the run root derived from the log path.
        """
        reported = getattr(self, 'evidence_root', None)
        if reported is not None:
            return reported
        return Path(self.log_file).resolve().parents[1] if self.borrowed_session else run_root(self.log_file)

    def evidence_text(self, reply):
        match = re.search(r'evidence (\S+\.tcldict)', reply)
        if match:
            path = Path(match[1]).resolve()
            try:
                if path.is_relative_to(self.run_root() / 'evidence') and path.stat().st_size <= 3_000_000:
                    return path.read_text(errors='replace')
            except (OSError, IndexError):
                pass
        return reply

    def rejected_keys(self, reply, config):
        """Top-level parameters whose validation failure aborted the whole native write."""
        text = self.evidence_text(reply)
        if 'CONFIGURE_FAIL' not in text and 'VALUE_FAIL' not in text:
            return []
        names = re.findall(r"Validation failed for parameter '([A-Za-z0-9_]+)'", text)
        names += re.findall(r"Validation failed for parameter '[^'(]*\(([A-Za-z0-9_]+)\)'", text)
        names += re.findall(r"out of the range for parameter '[^'(]*\(([A-Za-z0-9_]+)\)'", text)
        # The helper's context-aware preflight may reject a scalar before
        # set_property reaches Vivado's own validation message.
        names += re.findall(r'VALUE_FAIL:VAL_(?:OUT_OF_RANGE|NOT_IN_LIST):([A-Za-z0-9_]+)=', text)
        names += [name for name in re.findall(r'VALUE_FAIL:CONFIG\.([A-Za-z0-9_]+)\b', text)]
        return [k for k in config if k[7:] in set(names)]

    def validation_findings(self, reply):
        """Disabled or rejected fields named by native validation in this apply's evidence."""
        text = self.evidence_text(reply)
        owners = {}
        for (rid, cid), _ in self.selections.items():
            row = self.candidates[cid]
            owners.setdefault(row.get('subkey') or row['key'][7:], []).append(rid)
        disabled = list(dict.fromkeys(re.findall(r"modify the value of disabled parameter '([A-Za-z0-9_]+)'", text)))
        rejected = {}
        for name, detail in re.findall(r"Validation failed for parameter '([A-Za-z0-9_]+)\([A-Za-z0-9_]+\)'([^\n]{0,600})", text):
            detail = re.sub(r"with current value '[^']*'( for BD Cell '[^']*')?\.?", '', detail)
            rejected.setdefault(name, re.split(r'\}+ \{', detail)[0].strip(" :'.")[:240])
        for name, detail in re.findall(r"out of the range for parameter '[^'(]*\(([A-Za-z0-9_]+)\)'([^\n]{0,400})", text):
            detail = re.sub(r"for BD Cell '[^']*' *\.?", '', detail)
            rejected.setdefault(name, re.split(r'\}+ \{', detail)[0].strip(" :'.")[:240])
        returned = {}
        for cid, row in self.candidates.items():
            returned.setdefault(row.get('subkey') or row['key'][7:], []).append(cid)
        live_valid = {}
        for name, detail in rejected.items():
            match = re.search(r'Valid values are\s*-\s*([^\n.;}]+)', detail, re.I)
            values = [value.strip(" '\"") for value in match.group(1).split(',')] if match else []
            values = [value for value in values if value and len(value) <= 160][:16]
            if not values:
                continue
            live_valid[name] = values
            for cid in returned.get(name, []):
                row = self.candidates[cid]
                existing = {str(choice.get('value')) for choice in row.get('choices', [])}
                row['choices'] = list(row.get('choices', [])) + [
                    dict(value=value, label=value) for value in values if value not in existing]
                row['choices_scope'] = 'current_configuration_validation_error'
        found = {}
        missing = set(re.findall(r'missing nested key ([A-Za-z0-9_]+)', text))
        repairs = []
        for (rid, cid), item in self.selections.items():
            row, wanted = self.candidates[cid], str(item['value'])
            if row.get('subkey') not in missing or not re.fullmatch(r'[A-Za-z]+\d+', wanted):
                continue
            if row.get('mapping_scope') not in ['native_same_name_candidate','sibling_definition_candidate']:
                continue
            # A provisional direct field was not retained, but another
            # selected source enum explicitly spells its requested value.
            token = r'(?:^|[^A-Za-z0-9])' + re.escape(wanted) + r'(?:$|[^A-Za-z0-9])'
            source_owners = [(c, v) for (r, c), v in self.selections.items() if r == rid and c != cid
                      and self.candidates[c]['key'] == row['key']
                      and re.search(token, str(v['value']), re.I)]
            if len(source_owners) != 1:
                continue
            owner, control = source_owners[0]
            from intent_contract import facts, normalized
            fact = next((f['text'] for f in facts(self.requirements[rid]) if normalized(f['text']) == normalized(wanted)), None)
            if fact is None:
                continue
            retry = []
            for (r, c), value in self.selections.items():
                if r != rid or c == cid:
                    continue
                selection = dict(requirement_id=r,candidate_id=c,value=value['value'])
                for k in ['covers','grounding','mode']:
                    if k in value:selection[k]=value[k]
                if c == owner:
                    selection.update(covers=list(dict.fromkeys(value.get('covers',[]) + [fact])),
                                     grounding='The published selected enum '+str(control['value'])+' explicitly names '+wanted+
                                               '; verify that owning setting instead of the unretained provisional field.')
                retry.append(selection)
            repair = dict(field=row['subkey'],requirement_id=rid,replacement_selections=retry)
            self.unretained_repairs[(rid, row['key'], row['subkey'])] = dict(
                value=item['value'], owner=owner, owner_value=control['value'], repair=repair)
            repairs.append(repair)
        if repairs:
            found.update(unretained_field_repairs=repairs,
                         repair_note='A provisional direct field was not retained. Review the listed source enum that explicitly names the requested value; '
                                     'if it is the owning setting, select the entire replacement_selections batch once, then apply. Other settings are preserved.')
        if disabled:
            found['disabled_writes'] = [dict(field=n, requirement_ids=owners.get(n, [])) for n in disabled[:12]]
            found['disabled_note'] = ('Vivado ignored these writes because the fields are disabled until a controlling setting enables them '
                                      '(for example the interface, controller or peripheral enable, often an unapplied requirement). '
                                      'Select that enabling setting together with them and apply again.')
        if rejected:
            found['rejected_fields'] = [dict(field=n, requirement_ids=owners.get(n, []), detail=d,
                                             **({'valid_values': live_valid[n]} if n in live_valid else {}),
                                             **({'returned_candidates': returned[n][:4]} if n in returned and n not in owners else {}))
                                        for n, d in list(rejected.items())[:8]]
            if any(not owners.get(n) for n in rejected):
                found['rejected_note'] = ('Vivado rejected a field you did not select (its current value is invalid with your other choices). '
                                          'Select a valid value for it, using a returned candidate when listed or describing it by name, then apply again.')
        return found

    def repair_unique_native_enums(self, reply):
        """Adopt one uniquely matching legal enum returned by Vivado.

        This is a bounded spelling completion (for example LPDDR5 to
        LPDDR5_SDRAM), not a semantic guess: the submitted token must occur in
        the requirement and as a whole token sequence in exactly one native
        valid value for the rejected source field.
        """
        findings = self.validation_findings(reply)
        repairs = []
        for rejected in findings.get('rejected_fields', []):
            legal = rejected.get('valid_values', [])
            if not legal:
                continue
            field = rejected['field'].lower()
            owners = [(identity, item, self.candidates[identity[1]])
                      for identity, item in self.selections.items()
                      if (self.candidates[identity[1]].get('subkey') or
                          self.candidates[identity[1]]['key'][7:]).lower() == field]
            if len(owners) != 1:
                continue
            identity, item, row = owners[0]
            before = str(item['value'])
            if before.lower() not in self.requirements[identity[0]].lower():
                continue
            token = r'(?:^|[^a-z0-9])' + re.escape(before.lower()) + r'(?:$|[^a-z0-9])'
            matches = [value for value in legal if re.search(token, str(value).lower())]
            if len(matches) != 1 or str(matches[0]) == before:
                continue
            item['value'] = matches[0]
            repairs.append(dict(requirement_id=identity[0], candidate_id=identity[1],
                                key=row['key'], **({'subkey': row['subkey']} if row.get('subkey') else {}),
                                before=before, value=matches[0],
                                scope='unique_native_valid_value_token_completion'))
        return repairs

    def propagated_equivalences(self):
        """Find strict source-enum encodings for leaves Vivado may omit."""
        ignored = {'link', 'width', 'speed', 'mode', 'type', 'size', 'value',
                   'enable', 'enabled', 'en', 'input', 'output', 'override', 'max'}
        selected = [(rid, cid, self.candidates[cid], item)
                    for (rid, cid), item in self.selections.items()]
        result = {}
        for rid, cid, row, item in selected:
            wanted = str(item['value'])
            if (not row.get('subkey') or
                    row.get('mapping_scope') not in ['native_same_name_candidate',
                                                      'sibling_definition_candidate'] or
                    not re.fullmatch(r'[A-Za-z]+\d+', wanted)):
                continue
            wanted_re = r'(?:^|[^A-Za-z0-9])' + re.escape(wanted) + r'(?:$|[^A-Za-z0-9])'
            identity = {token for token in re.findall(r'[a-z]+\d+', row['subkey'].lower())
                        if token not in ignored}
            if not identity:
                continue
            owners = []
            for other_rid, other_cid, other, control in selected:
                encoded = str(control['value'])
                if (other_cid == cid or other['key'] != row['key'] or not other.get('subkey') or
                        not re.search(wanted_re, encoded, re.I)):
                    continue
                if identity & set(re.findall(r'[a-z]+\d+', encoded.lower())):
                    owners.append((other_rid, other_cid, other, control))
            if len(owners) == 1:
                other_rid, other_cid, other, control = owners[0]
                result[(rid, cid)] = dict(
                    key=other['key'], subkey=other['subkey'], wanted=control['value'],
                    owner_requirement_id=other_rid, owner_candidate_id=other_cid,
                    scope='same_container_exact_value_and_identifier_tokens')
        return result

    async def sibling_rows(self, rows):
        """Fields of a same-named catalog definition that a container does not list yet."""
        from parameter_help import user_rows
        added = []
        for key in sorted({r['key'] for r in rows}):
            found = split_tcl(await self.native('[lite::sibling_definition ' + atom(key) + ']'))
            if not found or found[0] != 'SIBLING:OK':
                continue
            info = dict(zip(found[1::2], found[2::2]))
            if info['vlnv'] == self.vlnv:
                continue
            known = {r['subkey'] for r in rows if r['key'] == key}
            params = user_rows(info['xml'], max_choices=16)
            names = {p['key'][7:] for p in params}
            if len(names & known) < max(5, 0.3 * len(names)):
                continue
            packaged = {p['key'][7:]: p for p in params}
            for row in rows:
                hint = packaged.get(row['subkey']) if row['key'] == key else None
                if hint and row.get('range', 'RANGE_NONE') == 'RANGE_NONE':
                    row.update({k: hint[k] for k in ['range', 'choices', 'choices_more'] if k in hint},
                               range_scope='sibling_definition_not_parent_live_range',
                               choices_scope='sibling_definition_not_current_legal_values', source_vlnv=info['vlnv'])
            for p in params:
                subkey = p['key'][7:]
                if subkey in known:
                    continue
                default = p['default']
                parts = split_tcl(default) if default.startswith('{') else []
                if len(parts) == 1:
                    default = parts[0]
                row = dict(p, key=key, subkey=subkey, default=default, mapping_scope='sibling_definition_candidate',
                           source_vlnv=info['vlnv'], range_scope='sibling_definition_not_parent_live_range',
                           choices_scope='sibling_definition_not_current_legal_values')
                try:
                    inner = dict_tcl(default) if ' ' in default else {}
                except Exception:
                    inner = {}
                if len(inner) > 1 and len(default) <= 240:
                    row.update(shape='dictionary', type='dictionary', fields=list(inner))
                added.append(row)
        return added

    async def open_cell(self, vlnv):
        prefix = 'set __cell [lite::open_cell ' + atom(vlnv) + ' ' + atom(self.cell) + ']\nif {[lindex $__cell 0] ne "CELL:OK"} {error $__cell}\n'
        expression = '[dict create vlnv [get_property VLNV [get_bd_cells ' + atom(self.cell) + ']] properties [list_property [get_bd_cells ' + atom(self.cell) + ']] version [version]]'
        data = dict_tcl(await self.native(expression, prefix))
        self.vlnv = data['vlnv']
        self.properties = set(split_tcl(data['properties']))
        self.gui_parameters = None
        self.port_cache = {}
        self.nested_rows = []
        self.stage = 'discovering'
        nested = split_tcl(await self.native('[lite::nested_schema ' + atom(self.vlnv) + ' ' + atom(self.cell) + ']'))
        if nested and nested[0] == 'NESTED:OK':
            schema = dict(zip(nested[1::2], nested[2::2]))
            if schema.get('file'):
                from nested_config import load_schema
                self.nested_rows = load_schema(schema['file'], self.run_root())
                try:
                    self.nested_rows += await self.sibling_rows(self.nested_rows)
                except Exception:
                    pass
        catalog, port_context = [], None
        if not self.has_database() and not self.nested_rows:
            try:
                rows = await self.api_rows()
                if len(rows) <= 24:
                    rows, port_context = await self.port_controls(' '.join(self.requirements.values()), rows)
                    await self.enrich_live(rows)
                    rows = self.enabled_first(rows)
                    if len(json.dumps([self.compact(r, 'p1') for r in rows])) <= 10000:
                        evidence = self.guard.record({'structured_metadata': True, 'backend': 'live', 'complete_small_catalog': True, 'parameters': rows})
                        catalog = self.register_candidates(rows, None, evidence)
                    else:
                        catalog = self.scalar_begin_catalog(rows, 'live')
            except Exception:
                catalog = []
        if self.has_database():
            try:
                reply = await __import__('asyncio').to_thread(self.guard.database_request, json.dumps(
                    dict(op='catalog', vlnv=self.vlnv, build=self.build, part=self.part)))
                rows = [r for r in reply.get('candidates', []) if r['key'] in self.properties] if reply.get('status') == 'DB:CATALOG' else []
                if rows and not self.nested_rows:
                    rows, port_context = await self.port_controls(' '.join(self.requirements.values()), rows)
                    await self.enrich_live(rows)
                    rows = self.enabled_first(rows)
                catalog = [self.compact(row, 'p'+str(i+1)) for i, row in enumerate(rows)]
                if self.nested_rows or len(catalog) > 24 or len(json.dumps(catalog)) > 10000:
                    catalog = [] if self.nested_rows else self.scalar_begin_catalog(rows, 'database')
                if catalog:
                    if not any(row.get('id', '').startswith('r1.') for row in catalog):
                        evidence = self.guard.record({'structured_metadata': True, 'backend': 'database', 'complete_small_catalog': True, 'parameters': rows, 'port_dependencies': port_context})
                        catalog = self.register_candidates(rows, None, evidence)
            except Exception:
                catalog = []
        # `discovery` tells the agent which search backs describe. With a shipped IP database
        # (Vivado 2026.2+) describe matches the task's words against parameter names, labels and
        # help text; without one (2026.1) it matches against Vivado's raw parameter names only, so
        # task-language queries match weakly and need Vivado's own vocabulary. Without this field
        # the two modes looked identical to the agent and it could not adapt its queries.
        return self.state(vlnv=self.vlnv, build=self.build, catalog=catalog,
                          discovery='database' if self.has_database() else 'live',
                          port_dependencies=self.port_summary(port_context, catalog) if catalog else None,
                          nested_containers=[dict(key=key, fields=sum(r['key']==key for r in self.nested_rows)) for key in sorted({r['key'] for r in self.nested_rows})],
                          next_op='select' if catalog else 'describe', metadata_scope='source_candidates_require_live_validation')

    def scalar_begin_catalog(self, rows, backend):
        """Return one source-grounded scalar candidate when its mapping is unique.

        This is a latency optimization for small models.  It uses the same
        live-enriched rows and range checks as ordinary describe; ambiguous,
        disabled, asserted, nested, or compound requirements keep the normal
        discovery path.
        """
        if len(self.requirements) != 1 or self.nested_rows:
            return []
        rid, text = next(iter(self.requirements.items()))
        wanted = self.scalar_integer(text)
        if wanted is None:
            return []
        # Use the same generic count vocabulary as provisional-candidate
        # validation.  User prose says sources, lanes, or inputs more often
        # than a catalog field's conventional NUM_PORTS spelling.
        validation_query = 'number count ports inputs sources ' + text
        eligible = []
        for row in rows:
            if (row.get('enabled') is False or row.get('write_mode') == 'assert'
                    or row.get('shape', 'scalar') != 'scalar'
                    or not self.range_accepts(row, wanted)):
                continue
            score = self.parameter_relevance(row, validation_query)
            if score[0]:
                eligible.append((score, row))
        eligible.sort(key=lambda item: (-item[0][0], -item[0][1], item[1]['key']))
        if not eligible or (len(eligible) > 1 and eligible[0][0] == eligible[1][0]):
            return []
        row = eligible[0][1]
        evidence = self.guard.record({'structured_metadata': True, 'backend': backend,
                                      'scalar_begin_candidate': True, 'requirement_id': rid,
                                      'wanted': wanted, 'parameter': row})
        return self.register_candidates([row], rid, evidence)

    @staticmethod
    def scalar_integer(text):
        values = re.findall(r'(?<![A-Za-z0-9.])(?<![A-Za-z]-)-?\d+(?![A-Za-z0-9.])', text)
        return int(values[0]) if len(values) == 1 else None

    @staticmethod
    def range_accepts(row, value):
        if not row.get('range_complete'):
            return False
        numeric = re.fullmatch(r'RANGE_NUM:([^:]+):([^:]+)', row.get('range', ''))
        if numeric:
            try:
                return float(numeric[1]) <= value <= float(numeric[2])
            except ValueError:
                return False
        listed = re.fullmatch(r'RANGE_LIST:(.*)', row.get('range', ''))
        return bool(listed and str(value) in re.findall(r'[^, {}]+', listed[1]))

    @classmethod
    def parameter_relevance(cls, row, text):
        wanted = cls.significant_terms(text)
        names = cls.candidate_terms(row.get('key', '')[7:] + ' ' + row.get('label', ''))
        help_terms = cls.candidate_terms(row.get('help', ''))
        exact = sum(any(cls.term_matches(term, item) for item in names) for term in wanted)
        broad = sum(any(cls.term_matches(term, item) for item in names + help_terms) for term in wanted)
        return exact, broad

    async def discard_provisional(self):
        if not self.created_by_session or not getattr(self, 'vlnv', None):
            raise ValueError('Cannot replace a cell that this structured session did not create')
        receipt = await self.native('[lite::discard_cell ' + atom(self.cell) + ' ' + atom(self.vlnv) + ']')
        if not receipt.startswith('DISCARD:OK'):
            raise ValueError('Provisional cell deletion failed: ' + receipt[:240])
        if getattr(self, 'part_change', None):
            restored = await self.native('[lite::restore_part ' + atom(self.part_change['previous']) + ']')
            if not restored.startswith('PART:RESTORED'):
                raise ValueError('Original part restoration failed: ' + restored[:240])
            self.part = self.part_change['previous']
            self.part_change = None
        self.created_by_session = False
        return receipt

    def clear_ip_state(self):
        self.candidates = {}; self.bundles = {}; self.ids = {}; self.selections = {}; self.describe_counts = {}
        self.apply_attempts = 0; self.vector_bases = {}; self.nested_rows = []; self.port_cache = {}
        self.api_parameters = None; self.metadata_fallback = None; self.waived = {}; self.exhausted = {}
        self.repeated_selects = 0; self.asked_queries = {}; self.repeated_queries = {}
        self.unchanged_reviewed = False; self.derived_enables = []; self.api_help = {}
        self.gui_parameters = None; self.gui_dependencies = {}; self.contract_reviewed = False
        self.unretained_repairs = {}; self.candidate_validation = None
        self.chosen_candidate_id = None
        for name in ['vlnv', 'properties']:
            if hasattr(self, name):
                delattr(self, name)

    async def validate_candidate(self, cid):
        """Use current live metadata; reject only a definitive reversible probe failure."""
        result = dict(status='inconclusive', matched_requirements=[], writable_keys=[], trial_write=None)
        if len(self.requirements) != 1 or self.nested_rows:
            return result
        rid, text = next(iter(self.requirements.items()))
        wanted = self.scalar_integer(text)
        if wanted is None:
            return result
        try:
            from native_metadata import rank
            validation_query = 'number count ports inputs sources ' + text
            rows = rank(await self.api_rows(), validation_query, limit=6,
                        context=list(self.requirements.values()))
            await self.enrich_live(rows)
        except Exception:
            return result
        writable = [row for row in rows if row.get('write_mode') != 'assert' and row.get('enabled') is not False
                    and row.get('shape') == 'scalar']
        result['writable_keys'] = [row['key'] for row in writable[:6]]
        eligible = [row for row in writable if self.range_accepts(row, wanted)]
        if not eligible:
            return result
        scored = [(self.parameter_relevance(row, validation_query), row) for row in eligible]
        scored.sort(key=lambda item: (-item[0][0], -item[0][1], item[1]['key']))
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return result
        row = scored[0][1]
        result['matched_requirements'] = [rid]
        probe = split_tcl(await self.native('[lite::probe_scalar ' + atom(self.cell) + ' ' + atom(row['key']) + ' ' + atom(wanted) + ']'))
        fields = dict(zip(probe[1::2], probe[2::2]))
        result['trial_write'] = dict(key=row['key'], wanted=wanted, result=probe[0],
                                     **{k: fields[k] for k in ['reason', 'before', 'actual', 'restored'] if k in fields})
        result['status'] = 'passed' if probe[0] == 'PROBE:OK' else 'rejected'
        if result['status'] == 'rejected':
            result['writable_keys'] = [key for key in result['writable_keys'] if key != row['key']]
        else:
            evidence = self.guard.record({'structured_metadata': True,
                                          'candidate_validation_selection': True,
                                          'requirement_id': rid, 'wanted': wanted,
                                          'parameter': row, 'probe': result['trial_write']})
            shown = self.register_candidates([row], rid, evidence)[0]
            candidate_id = shown['id']
            self.selections[(rid, candidate_id)] = dict(candidate_id=candidate_id, value=wanted)
            self.stage = 'ready'
            result.update(validated_candidate=shown,
                          auto_selection=dict(requirement_id=rid,
                                              candidate_id=candidate_id,
                                              key=row['key'], value=wanted,
                                              scope='unique_reversible_live_probe'))
        return result

    async def choose_candidate(self, cid):
        row = self.ip_candidates[cid]
        reply = await self.open_cell(row['vlnv'])
        self.created_by_session = True
        self.chosen_candidate_id = cid
        self.candidate_validation = await self.validate_candidate(cid)
        if self.candidate_validation['status'] == 'rejected':
            discard = await self.discard_provisional()
            self.ip_candidates[cid]['rejected'] = True
            self.rejected_candidates.append(dict(candidate_id=cid, vlnv=row['vlnv'],
                                                  validation=self.candidate_validation, native_receipt=discard[:300]))
            self.clear_ip_state()
            self.stage = 'choosing_ip'
            return self.state(candidate_validation=self.rejected_candidates[-1]['validation'],
                              rejected_candidates=self.rejected_candidates, ip_candidates=[dict(id=k, **v) for k, v in self.ip_candidates.items()])
        if self.candidate_validation.get('auto_selection'):
            reply = self.state(vlnv=self.vlnv, build=self.build,
                               catalog=[self.candidate_validation['validated_candidate']])
        reply.update(candidate_validation=self.candidate_validation,
                     rejected_candidates=self.rejected_candidates, reselection_used=self.reselection_used)
        return reply

    def allowed_operations(self):
        return {'new': ['begin'], 'choosing_ip': ['choose_ip', 'status'],
                'discovering': ['describe', 'select', 'finish', 'status'],
                'ready': ['describe', 'select', 'apply', 'finish', 'status'],
                'apply_failed': ['select', 'apply', 'finish', 'status'],
                'literal_review': ['select', 'apply', 'finish', 'status'],
                'configured': ['finish', 'status'], 'finished': ['status']}.get(self.stage, ['status'])

    async def reject_operation(self, request, reason):
        canonical = (self.stage, json.dumps(request, sort_keys=True, separators=(',', ':')))
        if canonical == self.invalid_request:
            self.invalid_repeat_count += 1
        else:
            self.invalid_request, self.invalid_repeat_count = canonical, 1
        if self.invalid_repeat_count >= 2:
            self.terminal_stop = True
            result = await self.finish_partial('Repeated out-of-stage operation: ' + reason)
            result.update(operation_rejected=True, repeat_count=self.invalid_repeat_count, next_op='begin',
                          cell_stopped=True,
                          cell_stopped_note='This cell stopped after a repeated out-of-stage request; begin a '
                                            'different cell, or stop Vivado when the design is done.')
            return result
        return self.state(operation_rejected=True, repeat_count=1, reason=reason,
                          allowed_operations=self.allowed_operations())

    def valid_operation(self):
        self.invalid_request = None
        self.invalid_repeat_count = 0

    @staticmethod
    def row_identity(row):
        return row['key'], row.get('subkey')

    @staticmethod
    def explicit_setting_choice(row, text):
        """Return a scalar assigned to an exact source identifier in text."""
        source=' '.join(str(row.get(k,'')) for k in ['subkey','key','label'])
        matches=[]
        for found in re.finditer(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b\s*[:=]\s*'
                                 r'([+-]?(?:0x[0-9A-F]+|\d+(?:\.\d+)?))\b',text,re.I):
            name,value=found.groups()
            if re.search(r'(?:^|[_ .])'+re.escape(name)+r'(?:$|[_ .])',source,re.I):
                matches.append(value)
        return matches[0] if len(set(matches))==1 else None

    def explicit_setting_target(self, row, value):
        """Find one ledger row explicitly assigning this live source field."""
        targets=[]
        for rid,text in self.requirements.items():
            wanted=self.explicit_setting_choice(row,text)
            if wanted is not None and str(wanted)==str(value):
                targets.append(rid)
        return targets[0] if len(targets)==1 else None

    async def api_rows(self):
        if self.api_parameters is None:
            from native_metadata import parameters
            try:
                metadata = dict_tcl(await self.native('[lite::metadata ' + atom(self.vlnv) + ']'))
                rows = parameters(metadata['file'])
            except Exception as exc:
                if 'METADATA_UNAVAILABLE' not in str(exc):
                    raise
                # Some customizers cannot run in batch mode; the packaged
                # component.xml still lists the user parameters.
                from parameter_help import user_rows
                xml = await self.native('[get_property XML_FILE_NAME [lindex [get_ipdefs -all -quiet ' + atom(self.vlnv) + '] 0]]')
                rows = user_rows(xml)
                self.metadata_fallback = str(exc)[:240]
            self.api_parameters = [r for r in rows if r['key'] in self.properties]
        return [dict(r) for r in self.api_parameters]

    async def enrich_live(self, rows):
        from parameter_contract import parse_live, achieved_source
        if not self.has_database():
            keys = list(dict.fromkeys(r['key'] for r in rows if not r.get('subkey') and r['key'] not in self.api_help))
            for start in range(0, len(keys), 4):
                raw = split_tcl(await self.native('[lite::describe ' + atom(self.vlnv) + ' ' + tlist(keys[start:start+4]) + ']'))
                details = dict_tcl(dict(zip(raw[1::2], raw[2::2])).get('parameters', ''))
                for key, value in details.items():
                    fields = dict_tcl(value)
                    self.api_help[key] = dict(help=fields.get('help', ''), help_source=fields.get('help_source', 'unavailable'))
                    enums = dict_tcl(fields.get('enums', ''))
                    choices = []
                    for encoded in split_tcl(enums.get('shown', '')):
                        pair = split_tcl(encoded)
                        if len(pair) == 2:
                            choices.append(dict(value=pair[0], label=pair[1]))
                    self.api_help[key].update(choices=choices, choices_more=enums.get('more') == '1',
                                              choices_scope='default_instance_labels_not_current_legal_values')
            for row in rows:
                row.update(self.api_help.get(row['key'], {}))
        keys = list(dict.fromkeys(r['key'] for r in rows if not r.get('subkey')))
        for start in range(0, len(keys), 24):
            live = parse_live(await self.native('[lite::live_parameters ' + atom(self.cell) + ' ' + tlist(keys[start:start+24]) + ']'))
            for row in rows:
                if not row.get('subkey') and row['key'] in live:
                    row.update(live[row['key']])
        for row in rows:
            source = achieved_source(row['key'], self.properties) if not row.get('subkey') else None
            if source:
                row.update(derived_from=source, derived_scope='achieved_readback', write_mode='assert')
        gui = await self.gui_metadata()
        for row in rows:
            data = gui.get(row['key'][7:]) if not row.get('subkey') else None
            if not data:
                continue
            if data.get('gui_context') and not row.get('gui_context'):
                row.update(gui_context=data['gui_context'][:8], gui_context_scope='packaged_gui_literal_not_live')
            for field in ['units','units_source','source_transform','derived_from','write_mode']:
                if field in data:row[field]=data[field]
            if data.get('help') and not row.get('help'):
                row.update(help=data['help'][:240], help_source='packaged_gui_tooltip')
            # Database search can return the right parameter without its
            # IP-XACT display labels.  Merge those source labels after live
            # range discovery, and retain only values legal in the current
            # configuration.  The database still never supplies legality.
            if data.get('choices'):
                listed = re.fullmatch(r'RANGE_LIST:(.*)', row.get('range', ''))
                legal = set(re.findall(r'[^, {}]+', listed[1])) if listed and row.get('range_complete') else set()
                choices = []
                for source in data['choices']:
                    choice = dict(source)
                    # Some packaged enum tables pair a display label with an
                    # ordinal even when that same label is itself a current
                    # native value.  Current live legality wins: rebind the
                    # label to itself and keep the ordinal's own later row.
                    label = str(choice.get('label', ''))
                    if label in legal and str(choice.get('value')) != label:
                        choice['value'] = label
                    if not row.get('range_complete') or self.range_accepts(row, choice['value']):
                        choices.append(choice)
                choices = list({str(choice['value']): choice for choice in choices}.values())
                if choices:
                    existing = {str(choice.get('value')): choice for choice in row.get('choices', [])}
                    for choice in choices:
                        previous = existing.get(str(choice['value']), {})
                        if previous.get('semantic_labels'):
                            choice['semantic_labels'] = previous['semantic_labels']
                    row.update(choices=choices,
                               choices_more=data.get('choices_more', False),
                               choices_scope='packaged_labels_filtered_by_current_live_range'
                                              if row.get('range_complete')
                                              else 'packaged_choices_not_current_legal_values')
        from value_semantics import annotate
        for row in rows:
            row.update(annotate(row))

    async def gui_metadata(self):
        """Literal XGUI page/group context and tooltips for the opened IP."""
        if self.gui_parameters is None:
            self.gui_parameters = {}
            try:
                from static_gui import extract, update_dependencies, numeric_transforms
                from parameter_help import user_rows
                xml = await self.native('[get_property XML_FILE_NAME [lindex [get_ipdefs -all -quiet ' + atom(self.vlnv) + '] 0]]')
                try:
                    for row in user_rows(xml, max_choices=16):
                        if row.get('choices'):
                            self.gui_parameters.setdefault(row['key'][7:], {}).update(
                                choices=row['choices'], choices_more=row.get('choices_more', False))
                except Exception:
                    pass  # XGUI evidence remains independently usable without IP-XACT choices.
                for path in sorted((Path(xml).parent / 'xgui').glob('*.tcl')):
                    for name, data in extract(path).items():
                        self.gui_parameters.setdefault(name, data)
                    self.gui_dependencies.update(update_dependencies(path))
                    for name, transform in numeric_transforms(path).items():
                        self.gui_parameters.setdefault(name,{})['source_transform']=transform
                        target=transform['target'][7:]
                        self.gui_parameters.setdefault(target,{}).update(derived_from='CONFIG.'+name,write_mode='assert')
                        # A literal SI suffix on the target and an exact source
                        # multiplier establish the input unit without guessing
                        # from its often misleading parameter name.
                        suffix=re.search(r'_(GHZ|MHZ|KHZ|HZ)$',target,re.I)
                        if suffix:
                            from intent_contract import FREQUENCY_UNITS
                            target_unit=suffix[1].lower()
                            unit=next((u for u,scale in FREQUENCY_UNITS.items()
                                       if scale==FREQUENCY_UNITS[target_unit]*transform['factor']),None)
                            if unit:
                                for field, declared in [(name,unit),(target,target_unit)]:
                                    self.gui_parameters[field].update(units=declared,units_source=transform)

            except Exception:
                pass  # GUI context is a hint; candidates remain usable without it.
        return self.gui_parameters

    VERIFIED_PREREQUISITES = None

    @classmethod
    def verified_prerequisite_table(cls):
        """Exact enabling assignments read from an IP's own (encrypted) XGUI rules.

        static_gui cannot see these: an encrypted xgui file is ciphertext on disk, so its
        regex finds no update procedures, and the live inference below only accepts a single
        Boolean switch turned on.  Most PS-wizard fields need two or three switches, an enum
        or integer value, or a switch turned *off* (PCIE0_COPY_PF0_EN defaults to 1 and
        disables every per-PF field).  Each entry here was verified by re-executing the IP's
        rules with the assignments pinned; none of them reads a part property.
        """
        if cls.VERIFIED_PREREQUISITES is None:
            # vlnv -> name -> (assignments, scope, part or None).  Rule-derived entries win over
            # probed ones: a probe stops at the first single switch that made one value stick on
            # one part, so it is a hint to confirm by readback, not a proven condition.
            table = {}
            try:
                path = Path(__file__).resolve().parent.parent / 'references' / 'verified_prerequisites.json'
                doc = json.loads(path.read_text())
                for vlnv, entries in doc.get('probed', {}).items():
                    for name, e in entries.items():
                        table.setdefault(vlnv, {})[name] = (e['requires'], 'probed_single_switch_on_part', e.get('part'))
                for vlnv, entries in doc.get('ips', {}).items():
                    for name, need in entries.items():
                        table.setdefault(vlnv, {})[name] = (need, 'xgui_rule_verified_by_reexecution', None)
            except Exception:
                table = {}
            cls.VERIFIED_PREREQUISITES = table
        return cls.VERIFIED_PREREQUISITES

    async def verified_prerequisites(self, rows):
        """Attach exact verified prerequisites and surface the fields they name as candidates."""
        table = self.verified_prerequisite_table().get(self.vlnv or '', {})
        if not table:
            return rows
        known = {self.row_identity(r) for r in rows}
        extra = []
        pool = None
        for row in list(rows):
            if row.get('enabled') is True:
                continue
            name = row['key'][7:] + ('.' + row['subkey'] if row.get('subkey') else '')
            entry = table.get(name)
            if not entry:
                continue
            need, scope, part = entry
            row.update(enablement_requires={'CONFIG.' + k: v for k, v in need.items()},
                       enablement_source_scope=scope)
            if part:
                row['enablement_observed_part'] = part
            if pool is None:
                pool = {}
                for r in (self.nested_rows or []):
                    pool.setdefault(r['key'][7:] + '.' + r['subkey'], r)
                try:
                    for r in await self.api_rows():
                        pool.setdefault(r['key'][7:], r)
                except Exception:
                    pass
            for dep in need:
                source = pool.get(dep)
                if source is None:
                    continue
                candidate = dict(source, related_to=row['key'] + ('.' + row['subkey'] if row.get('subkey') else ''),
                                 relation_scope='verified_prerequisite' if scope.startswith('xgui') else 'probed_prerequisite')
                identity = self.row_identity(candidate)
                if identity not in known and len(extra) < 6:
                    known.add(identity); extra.append(candidate)
        if extra:
            try:
                await self.enrich_live(extra)
            except Exception:
                pass
        return rows + extra

    async def prerequisite_candidates(self, rows):
        """Expose source-declared enum/Boolean controllers of disabled fields."""
        await self.gui_metadata()
        if not any(r.get('enabled') is False and r['key'][7:] in self.gui_dependencies for r in rows):
            return rows
        pool = {r['key'][7:]: r for r in await self.api_rows()}
        extra, known = [], {self.row_identity(r) for r in rows}
        for row in rows:
            if row.get('subkey') or row.get('enabled') is not False or row.get('enablement_requires'):
                continue
            rule = self.gui_dependencies.get(row['key'][7:], {})
            names = [n for n in rule.get('controls', []) if n in pool]
            if not names:
                continue
            row.update(enablement_controls=['CONFIG.' + n for n in names],
                       enablement_comparisons=rule['comparisons'], enablement_source_scope=rule['scope'])
            for name in names:
                candidate = dict(pool[name], related_to=row['key'], relation_scope=rule['scope'])
                identity = self.row_identity(candidate)
                if identity not in known and len(extra) < 4:
                    known.add(identity); extra.append(candidate)
        await self.enrich_live(extra)
        return rows + extra

    async def infer_live_prerequisites(self, rows):
        """Infer unique scalar enable controls from live context transitions."""
        targets = [row for row in rows if row.get('enabled') is False and not row.get('subkey')
                   and not row.get('enablement_requires')][:4]
        if not targets:
            return rows
        from parameter_contract import boolean_value
        controls = []
        for row in rows:
            if row.get('subkey') or row.get('key') in {target['key'] for target in targets}:
                continue
            choices = row.get('choices') or []
            raw = {str(choice.get('value')).lower() for choice in choices}
            if len(choices) != 2 or not raw <= {'0', '1', 'true', 'false'}:
                continue
            try:
                alternate = next(choice['value'] for choice in choices
                                 if boolean_value(choice['value']) == 'true')
            except (StopIteration, ValueError, TypeError):
                continue
            controls.append((row, alternate))
        enabled_by = {target['key']: [] for target in targets}
        target_keys = list(enabled_by)
        for control, value in controls[:4]:
            proposal = '[dict create ' + atom(control['key']) + ' ' + atom(value) + ']'
            try:
                raw = dict_tcl(await self.native('[lite::context_ranges ' + atom(self.cell) +
                                                  ' ' + tlist(target_keys) + ' ' + proposal + ']'))
            except Exception:
                continue
            for key in target_keys:
                detail = dict_tcl(raw.get(key, ''))
                if str(detail.get('enabled', '')).lower() in ['1', 'true']:
                    enabled_by[key].append(control['key'])
        for target in targets:
            unique = list(dict.fromkeys(enabled_by[target['key']]))
            if len(unique) == 1:
                target.update(enablement_controls=unique,
                              enablement_source_scope='live_current_configuration_transition')
        return rows + await self.outside_enable_controls(rows, [t for t in targets if not t.get('enablement_controls')])

    # Words that name the kind of field rather than what it is about.
    GENERIC_TERMS = frozenset({'port', 'use', 'enable', 'enabled', 'en', 'name', 'config', 'configuration',
                               'mode', 'type', 'select', 'value', 'param', 'parameter', 'c'})

    @staticmethod
    def boolean_row(row):
        """The value that switches a two-state parameter on, or None when it is not two-state."""
        values = {str(c.get('value')).lower() for c in row.get('choices') or []}
        state = str(row.get('current', row.get('default', ''))).lower()
        if str(row.get('type', '')).lower() in ('bool', 'boolean') or (len(values) == 2 and values <= {'0', '1', 'true', 'false'}) \
                or state in ('true', 'false'):
            return '1' if state == '0' or values == {'0', '1'} else 'true'
        return None

    async def outside_enable_controls(self, rows, targets):
        """Find, and prove live, the switch of a disabled field that retrieval did not return.

        A requirement such as "locked output exposed" retrieves the port-name field
        (LOCKED_PORT), which is disabled until a separate two-state parameter (USE_LOCKED)
        is on. The controls tried above come only from retrieved rows, so that switch was
        never offered and the field was asserted instead of enabled (case 16). Here the
        cell's own two-state parameters that share a specific word with the disabled field
        are tried; one is kept only if switching it on enables the field in a live
        context check, so the shared word selects what to test and never decides.
        """
        if not targets:
            return []
        from ip_database import Database as Snapshot
        try:
            pool = await self.api_rows()
        except Exception:
            return []
        terms = lambda r: Snapshot.identifier_terms(r['key'][7:] + ' ' + str(r.get('label') or '')) - self.GENERIC_TERMS
        present = {r['key'] for r in rows}
        picks = []
        for target in targets:
            wanted = terms(target)
            for row in pool:
                if (row['key'] in present or row.get('subkey') or row['key'] in {p['key'] for p in picks}
                        or not wanted & terms(row) or self.boolean_row(row) is None):
                    continue
                picks.append(dict(row))
        picks = picks[:4]
        if not picks:
            return []
        try:
            await self.enrich_live(picks)
        except Exception:
            pass
        target_keys = [t['key'] for t in targets]
        proven = []
        for control in picks:
            value = self.boolean_row(control)
            if value is None or str(control.get('current', '')).lower() in ('1', 'true'):
                continue   # already on, so it cannot be what keeps the field disabled
            proposal = '[dict create ' + atom(control['key']) + ' ' + atom(value) + ']'
            try:
                raw = dict_tcl(await self.native('[lite::context_ranges ' + atom(self.cell) + ' '
                                                 + tlist(target_keys) + ' ' + proposal + ']'))
            except Exception:
                continue
            enables = [key for key in target_keys
                       if str(dict_tcl(raw.get(key, '')).get('enabled', '')).lower() in ['1', 'true']]
            if not enables:
                continue
            for target in targets:
                if target['key'] in enables and not target.get('enablement_controls'):
                    target.update(enablement_controls=[control['key']],
                                  enablement_source_scope='live_current_configuration_transition')
            proven.append(dict(control, related_to=enables[0], relation_scope='live_enablement_control',
                               enables=enables))
        self.guard.record({'outside_enable_controls': True, 'targets': target_keys,
                           'tried': [p['key'] for p in picks], 'proven': [p['key'] for p in proven]})
        return proven

    async def port_name_controls(self, rows):
        """Link a field that names a missing pin to the switch that creates that pin.

        A port-name field (clkx5_wiz LOCKED_PORT = 'locked') is not reliably reported enabled or
        disabled, and some IPs decide port presence in encrypted Tcl and publish no port gating.
        The observable fact is the pin: the field names one this cell lacks. Two-state
        parameters sharing a specific word with the field are switched on, one at a time, on a
        throwaway cell of the same IP (lite::port_effect); the one that adds the named pin is
        linked and offered. Words choose what to test; only the pin decides.
        """
        if not getattr(self, 'vlnv', None) or not getattr(self, 'cell', None):
            return rows
        try:
            pins = set(split_tcl(await self.native('[get_property NAME [get_bd_pins -quiet -of_objects [get_bd_cells -quiet '
                                                   + atom(self.cell) + ']]]')))
        except Exception:
            return rows
        ident = lambda v: bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(v or '')))
        targets = [r for r in rows if not r.get('subkey') and not r.get('enablement_controls')
                   and ident(r.get('current')) and str(r.get('current')) not in pins
                   and str(r.get('current')).lower() not in ('true', 'false', 'none', 'auto')
                   and self.boolean_row(r) is None]
        # Port-like fields first; the probe still decides.
        targets.sort(key=lambda r: not re.search(r'port|pin|signal', r['key'] + ' ' + str(r.get('label') or ''), re.I))
        targets = targets[:2]
        if not targets:
            return rows
        from ip_database import Database as Snapshot
        terms = lambda r: Snapshot.identifier_terms(r['key'][7:] + ' ' + str(r.get('label') or '')) - self.GENERIC_TERMS
        try:
            pool = {r['key']: dict(r) for r in await self.api_rows()}
        except Exception:
            pool = {}
        for row in rows:
            if not row.get('subkey'):
                pool[row['key']] = row
        picks = []
        for target in targets:
            for row in pool.values():
                if (row['key'] == target['key'] or row in picks or not terms(target) & terms(row)
                        or self.boolean_row(row) is None):
                    continue
                picks.append(row)
        picks = [p for p in picks if str(p.get('current', p.get('default', ''))).lower() not in ('1', 'true')][:4]
        if not picks:
            return rows
        missing = [p for p in picks if 'current' not in p]
        if missing:
            try:
                await self.enrich_live(missing)
            except Exception:
                pass
        proposal = '[dict create ' + ' '.join(atom(p['key']) + ' ' + atom(self.boolean_row(p)) for p in picks) + ']'
        try:
            raw = split_tcl(await self.native('[lite::port_effect ' + atom(self.vlnv) + ' ' + proposal + ']'))
            effects = dict_tcl(dict(zip(raw[1::2], raw[2::2])).get('effects', '')) if raw and raw[0] == 'PORT_EFFECT:OK' else {}
        except Exception as exc:
            self.guard.record({'port_name_controls': False, 'error': str(exc)[:240]})
            return rows
        present, extra, linked = {r['key'] for r in rows}, [], []
        for target in targets:
            pin = str(target['current'])
            for control in picks:
                if pin not in split_tcl(effects.get(control['key'], '')):
                    continue
                target.update(enablement_controls=[control['key']], enablement_source_scope='live_port_presence',
                              enablement_on_value=self.boolean_row(control), missing_pin=pin)
                linked.append((target['key'], control['key'], pin))
                if control['key'] not in present:
                    present.add(control['key'])
                    extra.append(dict(control, related_to=target['key'], relation_scope='live_port_control',
                                      dependency_ports=[pin], dependency_scope='live_port_presence'))
                break
        self.guard.record({'port_name_controls': True, 'targets': [t['key'] for t in targets],
                           'tested': [p['key'] for p in picks], 'linked': linked})
        if extra:
            try:
                await self.enrich_live(extra)
            except Exception:
                pass
        return rows + extra

    async def prove_stick_prerequisites(self, reply):
        """After a failed apply, find what makes a value that did not stick stick (issue #548).

        CFG:FIX lists {rN.rN.pM actual A wanted W} for values Vivado accepted but did not keep.
        For up to two such scalar keys, the release graph proposes the parameters that affect
        them (DB op `influences`, probe-measured links first); lite::stick_effect then tries each
        on a throwaway cell of the same IP. Only a setting that made the value stick is kept.
        """
        import asyncio
        if not self.has_database():
            return []
        failures = re.findall(r'\{(r[\w]+\.(r[\w]+\.p\d+)) actual (\S*) wanted (\S+)\}', reply)
        targets = []
        for _, cid, actual, wanted in failures:
            row = self.candidates.get(cid)
            if row and not row.get('subkey') and actual != wanted and row['key'] not in [t[0] for t in targets]:
                targets.append((row['key'], wanted))
        targets = targets[:2]
        if not targets:
            return []
        try:
            links = (await asyncio.to_thread(self.guard.database_request, json.dumps(dict(
                op='influences', vlnv=self.vlnv, keys=[k for k, _ in targets], build=self.build)))).get('influences', {})
        except Exception as exc:
            self.guard.record({'stick_prerequisites': False, 'error': str(exc)[:240]})
            return []
        proven = []
        self.stick_found = getattr(self, 'stick_found', {})
        for key, wanted in targets:
            trials = [(link['param'], value) for link in links.get(key, []) for value in link['values']][:8]
            if not trials:
                continue
            encoded = '[list ' + ' '.join(atom(k) + ' ' + atom(v) for k, v in trials) + ']'
            try:
                raw = split_tcl(await self.native('[lite::stick_effect ' + atom(self.vlnv) + ' ' + atom(key) + ' '
                                                  + atom(wanted) + ' ' + encoded + ']'))
                data = dict(zip(raw[1::2], raw[2::2])) if raw and raw[0] == 'STICK:OK' else {}
            except Exception as exc:
                self.guard.record({'stick_prerequisites': False, 'key': key, 'error': str(exc)[:240]})
                continue
            for trial in split_tcl(data.get('trials', '')):
                control, value, stuck = (split_tcl(trial) + ['', '', ''])[:3]
                if stuck == '1':
                    self.stick_found[key] = (control, value)
                    proven.append(dict(key=key, wanted=wanted, requires={control: value},
                                       evidence_scope='live_throwaway_cell'))
                    break
        self.guard.record({'stick_prerequisites': True, 'targets': targets, 'proven': proven})
        return proven

    def stick_repairs(self, config):
        """Add a proven prerequisite to the write when its value is selected again."""
        repairs = []
        for key, (control, value) in getattr(self, 'stick_found', {}).items():
            if key in config and str(config.get(control, '')) != value:
                before = config.get(control)
                # The prerequisite must be written before the value it governs.
                rest = [(k, v) for k, v in config.items() if k != control]
                config.clear()
                config[control] = value
                config.update(rest)
                repairs.append(dict(key=control, before=before, value=value, enables=key, derived=True,
                                    evidence_scope='live_stick_prerequisite'))
        return repairs

    def port_presence_repairs(self, config):
        """Switch on the control that creates a pin a selected port-name field names.

        Selecting LOCKED_PORT = 'locked' only renames a pin; the pin exists once USE_LOCKED is on
        (proven by lite::port_effect). Without this the selection verified trivially and the
        port was never exposed (case 16).
        """
        repairs = []
        for (rid, cid), item in list(self.selections.items()):
            row = self.candidates[cid]
            if row.get('enablement_source_scope') != 'live_port_presence' or not row.get('enablement_controls'):
                continue
            control_key, on = row['enablement_controls'][0], row.get('enablement_on_value') or 'true'
            if str(config.get(control_key, '')).lower() in ('1', 'true'):
                continue
            control = next((c for c in self.candidates.values() if c['key'] == control_key and not c.get('subkey')), {})
            before = config.get(control_key, control.get('current'))
            if str(before).lower() in ('1', 'true'):
                continue
            config[control_key] = on
            repairs.append(dict(requirement_id=rid, candidate_id=cid, key=control_key, before=before, value=on,
                                enables=row['key'], pin=row.get('missing_pin'), derived=True,
                                evidence_scope='live_port_presence'))
        return repairs

    def bank_missing(self):
        """Whether this cell published no parameter bank at all, so nothing checks a write.

        param_range returns PB_MISS:<param> when the bank file holds no block for
        a parameter, and the row then carries range_scope unavailable. One such
        row is ordinary. Every row being unavailable means the bank itself is
        absent, and the cell is answering live with a current and a value_src
        per row, so the backend still reads live and nothing in the row marks it
        as unchecked.

        Both cells in the corpus that reach this state are the cell on the
        receiving end of a clock: ced-gen_H3's clk_wiz_0 and ced-gen_M7's
        rst_ps7_0_50M. Both models wrote 125 MHz into clk_wiz_0's own
        REF_CLK_FREQ instead of into the PS driving it, on every iteration,
        including the one where describe said in prose that these rows are
        unchecked. Prose the model can skip is not a gate.

        Keyed on PB_MISS rather than on range_scope: unavailable is the wider
        word, covering a RANGE_NONE and a customizer that errored, and those
        rows are still ordinary writable parameters.
        """
        rows = [row for row in self.candidates.values() if not row.get('subkey')]
        return bool(rows) and all(row.get('range', '').startswith('PB_MISS:') for row in rows)

    # A quantity the cell drives out of one of its OWN output ports, so this cell is
    # where it is set and there is no "cell that produces it" to move to.  Xilinx names
    # the output side consistently -- clk_wiz publishes CLKOUT<n>_REQUESTED_OUT_FREQ and
    # CLK_OUT<n>_PORT for clk_out<n>, against PRIM_IN_FREQ / CLKIN<n>_* / REF_CLK_FREQ on
    # the input side -- so the direction is readable from the key without a customizer,
    # which matters precisely because these rows only appear when the bank is missing.
    # Matched against the parameter name with every separator turned into _ and the
    # ends padded, so CONFIG.CLKOUT1_REQUESTED_OUT_FREQ and CLK_OUT1_PORT both read the
    # same way and the trailing _FREQ is a boundary rather than the end of the string.
    PRODUCED_KEY = re.compile(r'_CLK_?OUT\d*_|_OUT_(?:FREQ|PERIOD|MHZ)_', re.I)

    @staticmethod
    def received_quantity(candidate):
        """Whether a row on a bank-less cell holds a value this cell receives from another cell.

        That is what the bank-missing refusal guards against (both corpus cases wrote a clock
        frequency into the receiving cell).  A bank can also be missing because the IP's own
        customizer fails in batch mode -- proc_sys_reset on Vivado 2026.2 errors in create_gui
        with "Invalid Parameter 'Component_Name'" -- and then an ordinary local setting such as
        a reset polarity (value_src DEFAULT) is still writable and checked by saved readback.

        An OUTPUT the cell produces is not a received quantity, and refusing it strands the
        requirement: on 2026.2 clk_wiz:6.0 fails create_gui the same way, so every clk_wiz row
        is PB_MISS, and "MicroBlaze V clock at 200 MHz" was refused on CLKOUT1_REQUESTED_OUT_FREQ
        -- the one parameter that sets it -- while the refusal told the model to go to the cell
        that produces the clock, which is the clk_wiz it was already on.  Written directly that
        value validates with 0 errors and propagates 200 MHz to microblaze_riscv_0/Clk, so the
        bank is missing but the write is both legal and checkable by propose's readback.
        """
        if str(candidate.get('value_src', '')).upper() in ('PROPAGATED', 'CONSTANT'):
            return True
        key = '_' + re.sub(r'[^A-Za-z0-9]+', '_', str(candidate.get('key') or '')).strip('_') + '_'
        if StructuredSession.PRODUCED_KEY.search(key):
            return False
        text = ' '.join(str(candidate.get(k) or '') for k in ('key', 'label'))
        return bool(re.search(r'FREQ|CLK|CLOCK|MHZ|PERIOD', text, re.I))

    def bank_blocks_writes(self):
        """Whether a missing bank actually leaves this cell with nothing to write.

        bank_missing() asks whether the metadata is absent; this asks whether that costs
        the requirement anything.  select refuses a bank-less row only when it is a
        received quantity, so a cell still holding a writable row -- a clk_wiz output
        frequency, a reset polarity -- is not waiting to be reassigned, and the flow must
        not route the model away from it.  Before the first describe there are no rows and
        nothing is blocked.
        """
        rows = [row for row in self.candidates.values() if not row.get('subkey')]
        return self.bank_missing() and all(self.received_quantity(row) for row in rows)

    def qualified_source_alternatives(self, rid, candidate):
        """Disambiguate a shared label only with exact source interface help."""
        from intent_contract import facts, normalized
        text=self.requirements[rid]
        qualifiers=[f['text'] for f in facts(text) if f['unit']=='literal' and re.search(
            re.escape(f['text'])+r'\s+(?:line\s+rate|clock|frequency|interface|port)\b',text,re.I)]
        if not qualifiers or not candidate.get('label') or candidate.get('subkey'):
            return []
        ambiguous=[q for q in qualifiers if normalized(q) not in normalized(candidate.get('help',''))]
        if not ambiguous:
            return []
        return [dict(candidate_id=cid,key=row['key'],help=row['help'],help_source=row['help_source'])
                for cid,row in self.candidates.items()
                if row.get('requirement_id') in [None,rid] and row['key']!=candidate['key']
                and row.get('label')==candidate['label'] and row.get('gui_context')
                and row.get('help_source') in ['packaged_gui_tooltip','native_description']
                and all(normalized(q) in normalized(row.get('help','')) for q in ambiguous)]

    def port_control_requirements(self, rid, selections=None):
        """Explicit source use/enable controls for a requested native port.

        A polarity setting alone cannot prove the port exists. Only Boolean
        controls already tied to a native port and naming this feature qualify;
        unrelated predecessors do not establish an enabling value.
        """
        from intent_contract import normalized
        from parameter_contract import boolean_value
        text=self.requirements[rid]
        selected={self.row_identity(self.candidates[cid]):item['value'] for (_,cid),item in (self.selections if selections is None else selections).items()}
        result,seen=[],set()
        for cid,row in self.candidates.items():
            if row.get('requirement_id') not in [None,rid] or row.get('type','').lower() not in ['bool','boolean']:
                continue
            if not row.get('dependency_ports') or row.get('dependency_scope')!='direct_native_predecessor':
                continue
            name=row.get('subkey',row['key'][7:])
            feature=re.fullmatch(r'(?:USE|ENABLE|INCLUDE)_(.+)',name)
            if not feature or normalized(feature[1]) not in normalized(text):
                continue
            identity=self.row_identity(row)
            if identity in seen:continue
            seen.add(identity)
            words=re.escape(feature[1]).replace('_',r'[ _-]*')
            negative=bool(re.search(r'\b(?:no|without|disable[ds]?|exclude[ds]?)\s+(?:the\s+)?'+words+r'\b',text,re.I)
                          or re.search(words+r'\s+(?:port\s+)?(?:disabled|off)\b',text,re.I))
            wanted='false' if negative else 'true'
            try:actual=boolean_value(selected.get(identity))
            except (ValueError,TypeError):actual=None
            result.append(dict(candidate_id=cid,key=row['key'],value=(wanted=='true'),feature=feature[1],covered=actual==wanted,
                               dependency_ports=row['dependency_ports'],scope=row['dependency_scope']))
        return result

    def requested_port_controls(self, rid):
        return [{k:v for k,v in c.items() if k!='covered'} for c in self.port_control_requirements(rid) if not c['covered']]

    def coverage_findings(self):
        from intent_contract import uncovered_values
        findings = []
        for rid, text in self.requirements.items():
            if rid in self.waived:
                continue
            picked = [(self.candidates[cid], item) for (r, cid), item in self.selections.items() if r == rid]
            if not picked:
                continue
            controls=self.requested_port_controls(rid)
            if controls:
                findings.append(dict(requirement_id=rid,text=text,reason='requested_port_enablement',
                    missing_values=[c['key']+'='+str(c['value']).lower() for c in controls],
                    required_port_controls=controls))
            missing = uncovered_values(text, picked)
            if missing:
                encodings = [dict(candidate_id=cid, key=self.candidates[cid]['key'], value=item['value'])
                             for (r, cid), item in self.selections.items() if r == rid][:6]
                findings.append(dict(requirement_id=rid, text=text, missing_values=[f['text'] for f in missing],
                                     selected_encodings=encodings))
            # A count named elsewhere in the task also applies to repeated
            # fields (e.g. clocks on three enabled tiles). One member cannot
            # discharge the whole family requirement.
            if self.api_parameters:
                from native_metadata import family, collapse_families
                chosen = {row['key'] for row, _ in picked if not row.get('subkey')}
                for pattern in {family(key) for key in chosen} - {None}:
                    axes = re.findall(r'\b\d+\s+([a-z]{3,})s\b', ' '.join(self.requirements.values()).lower())
                    if any(re.search(r'\b' + re.escape(axis) + r'\s*\d', text, re.I) for axis in axes):
                        continue
                    siblings = [r for r in self.api_parameters if family(r['key']) == pattern]
                    required = collapse_families(siblings, text, context=list(self.requirements.values()))
                    # Only enforce a larger explicitly counted family; the
                    # ordinary two-member retrieval cap is not a requirement.
                    if len(required) > 2:
                        absent = [r['key'] for r in required if r['key'] not in chosen]
                        if absent:
                            findings.append(dict(requirement_id=rid, text=text,
                                                 missing_values=absent, reason='requested_counted_family_members'))
        return findings

    async def verified_readback(self):
        """Return actual values from the finish receipt, never selected guesses."""
        try:
            receipt = dict_tcl(await self.native('[lite::verified_readback ' + atom(self.cell) + ']'))
            fields = []
            for encoded in split_tcl(receipt.get('fields', '')):
                row = dict_tcl(encoded)
                row['passed'] = row.pop('pass', '0') == '1'
                fields.append(row)
            return dict(available=True, scope='saved_design_selected_fields', fields=fields)
        except Exception as exc:
            return dict(available=False, reason=str(exc)[:240])

    @staticmethod
    def numbered_stem(key):
        """Key with channel/instance numbers removed: C_ALL_OUTPUTS_2 and C_GPIO2_WIDTH
        become C_ALL_OUTPUTS and C_GPIO_WIDTH."""
        return re.sub(r'_?\d+(?=_|$)', '', key.upper())

    async def same_label_siblings(self, rows, limit=4):
        """Numbered copies of a returned top-level parameter that share its label.

        Copies such as All Outputs (GPIO) and All Outputs (GPIO 2) have the same
        label and help, so retrieval scores them almost equally and may return
        only the copy that is disabled now.  Showing both lets the live enabled
        state and the GUI group decide instead of a score tie.
        """
        try:
            source = await self.api_rows()
        except Exception:
            return []
        shown = {r['key'] for r in rows}
        added = []
        for row in rows:
            label = str(row.get('label') or '').strip().lower()
            if row.get('subkey') or not label:
                continue
            stem = self.numbered_stem(row['key'])
            for other in source:
                if (other['key'] in shown or str(other.get('label') or '').strip().lower() != label
                        or self.numbered_stem(other['key']) != stem):
                    continue
                added.append(dict(other, related_to=row['key'], relation_scope='same_label_numbered_copy'))
                shown.add(other['key'])
                if len(added) >= limit:
                    return added
        return added

    @staticmethod
    def enabled_first(rows):
        """Parameters disabled in the current configuration rank last."""
        return sorted(rows, key=lambda row: row.get('enabled') is False)

    @staticmethod
    def requirement_role_first(rows, text):
        """Put the requested direction setting ahead of topology controls.

        A dual-channel enable may be necessary for channel 2, but it does not
        itself implement input/output direction.  Preserve the source ranking
        within each semantic role and leave other requirement kinds alone.
        """
        wants_output = bool(re.search(r'\b(?:output|outputs|drive|drives|driver|drivers|driving|transmit)\b', text, re.I))
        wants_input = bool(re.search(r'\b(?:input|inputs|read|reads|reading|receive|receiving|capture|capturing)\b', text, re.I))
        if wants_output == wants_input:
            return rows
        wanted = 'outputs' if wants_output else 'inputs'
        opposite = 'inputs' if wants_output else 'outputs'
        def role(row):
            field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key']).lower().replace('-', '_')
            if re.search(r'(?:^|[_ .])all[_ ]'+wanted+r'(?:$|[_ .])', field):
                return 2
            if re.search(r'(?:^|[_ .])all[_ ]'+opposite+r'(?:$|[_ .])', field):
                return -1
            return 0
        return sorted(rows, key=lambda row: -role(row))

    def register_candidates(self, rows, rid, evidence):
        from value_semantics import annotate
        result, seen = [], set()
        for row in rows:
            if row.get('key', '').upper() == 'CONFIG.COMPONENT_NAME':
                continue
            row = annotate(row)
            identity = (rid, *self.row_identity(row))
            if identity in seen:
                continue
            seen.add(identity)
            number = 1 + sum(k[0] == rid for k in self.ids)
            cid = self.ids.setdefault(identity, (rid + '.p' if rid else 'p') + str(number))
            self.candidates[cid] = dict(row, requirement_id=rid, evidence=str(evidence))
            shown = self.compact(row, cid)
            choice = self.text_choice(row, self.requirements.get(rid, '')) if rid else None
            if choice is not None:
                shown['text_choice'] = choice
            result.append(shown)
        return result

    def family_bundles(self, rid, shown):
        """Group a complete, explicitly counted source family behind one ID.

        The bundle carries no value or IP knowledge. Selecting it expands one
        shared value to the returned member candidate IDs, which are then
        checked and written through the ordinary path.
        """
        from native_metadata import family
        text = ' '.join(self.requirements.values())
        counts = [(int(n), w.rstrip('s')) for n, w in re.findall(r'\b([2-8])\s+([a-z]{3,})s\b', text.lower())]
        groups = {}
        for row in shown:
            pattern = family(row['key']) if not row.get('subkey') else None
            if pattern:
                groups.setdefault(pattern, []).append(row)
        result = []
        related = lambda a, b: len(a) >= 3 and (a.startswith(b) or b.startswith(a))
        for pattern, members in groups.items():
            words = re.findall(r'[a-z]+', pattern[7:].lower())
            requested = [n for n, noun in counts if any(related(word, noun) for word in words)]
            if not requested:
                continue
            count = max(requested)
            if len(members) != count or len({m['id'] for m in members}) != count:
                continue
            member_ids = [m['id'] for m in members]
            existing = next((bid for bid, b in self.bundles.items()
                             if b['requirement_id'] == rid and b['candidate_ids'] == member_ids), None)
            bid = existing or rid+'.b'+str(1+sum(b.startswith(rid+'.b') for b in self.bundles))
            self.bundles[bid] = dict(requirement_id=rid,candidate_ids=member_ids,
                                     family_pattern=pattern,requested_count=count)
            result.append(dict(id=bid,candidate_ids=member_ids,keys=[m['key'] for m in members],
                               value_mode='shared',requested_count=count,
                               note='Select this bundle once with the value shared by every listed family member.'))
        return result

    @staticmethod
    def role_numeric_choice(row, text):
        """Resolve a numeric enum only when its field role and unit agree."""
        from intent_contract import facts, number
        name = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key']).lower()
        if not re.search(r'(?:^|[_ ]|\.)size(?:$|[_ ])', name):
            return None
        storage_units = {'byte', 'bytes', 'kb', 'mb', 'gb', 'tb', 'kib', 'mib', 'gib', 'tib'}
        requested = [number(f['value']) for f in facts(text) if f.get('unit', '').lower() in storage_units]
        requested = [value for value in requested if value is not None]
        matches = []
        for choice in row.get('choices', []):
            value = number(choice.get('value'))
            if value is not None and any(math.isclose(value, wanted, rel_tol=1e-12, abs_tol=1e-9)
                                         for wanted in requested):
                matches.append(choice.get('value'))
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def explicit_count_choice(cls, row, text):
        """Return the one source-derived count requested by this ledger row."""
        if row.get('type', '').lower() not in ['long', 'integer', 'int']:
            return None
        name = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
        if not re.search(r'(?:^|[_ .])(?:num|number|count)(?:$|[_ .])', name, re.I):
            return None
        # Only bind a negative count when the field and requirement share a
        # meaningful role; this prevents an unrelated NUM_* candidate from
        # acquiring a zero merely because the prose contains "no".
        if re.search(r'\b(?:no|zero)\b', text, re.I) and cls.parameter_relevance(row, text)[0]:
            return 0
        from intent_contract import facts, number
        values = [number(f['value']) for f in facts(text) if number(f['value']) is not None]
        values = list(dict.fromkeys(values))
        return int(values[0]) if len(values) == 1 and float(values[0]).is_integer() else None

    @staticmethod
    def unit_scale_choice(row, text):
        """Map an explicit storage unit to one uniquely published scale enum."""
        from intent_contract import facts
        name = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key']).lower()
        if not re.search(r'(?:^|[_ ]|\.)scale(?:$|[_ ])', name):
            return None
        labels = {
            'byte': {'byte', 'bytes'}, 'bytes': {'byte', 'bytes'},
            'kb': {'kilobyte', 'kilobytes'}, 'kib': {'kilobyte', 'kilobytes'},
            'mb': {'megabyte', 'megabytes'}, 'mib': {'megabyte', 'megabytes'},
            'gb': {'gigabyte', 'gigabytes'}, 'gib': {'gigabyte', 'gigabytes'},
            'tb': {'terabyte', 'terabytes'}, 'tib': {'terabyte', 'terabytes'}}
        wanted = set()
        for fact in facts(text):
            wanted |= labels.get(fact.get('unit', '').lower(), set())
        matches = [choice.get('value') for choice in row.get('choices', [])
                   if re.sub(r'[^a-z]', '', str(choice.get('value', '')).lower()) in wanted or
                   re.sub(r'[^a-z]', '', str(choice.get('label', '')).lower()) in wanted]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def text_choice(row, text):
        """The one listed choice sharing the most words with the requirement text, if any."""
        from value_semantics import requested_labels
        exact = requested_labels(row,text)
        if len(exact)==1:
            return exact[0]['value']
        numeric_role = StructuredSession.role_numeric_choice(row, text)
        if numeric_role is not None:
            return numeric_role
        unit_scale = StructuredSession.unit_scale_choice(row, text)
        if unit_scale is not None:
            return unit_scale
        choices = row.get('choices', [])
        # For a Boolean source enum, action words carry the value.  A digit in
        # "clock 0" or "channel 1" identifies the field and must not be
        # mistaken for the enum value itself.
        from parameter_contract import boolean_value
        raw_values = {str(choice.get('value')).lower() for choice in choices}
        encoded = ([(choice, boolean_value(choice.get('value'))) for choice in choices]
                   if len(choices) == 2 and raw_values <= {'0', '1', 'true', 'false'} else [])
        if encoded and {value for _, value in encoded} == {'true', 'false'}:
            wants_false = bool(re.search(r'\b(?:disable|disabled|off|exclude|excluded|unused)\b', text, re.I))
            wants_true = bool(re.search(r'\b(?:enable|enabled|on|include|included|use|used|generate|expose)\b', text, re.I))
            field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key']).lower()
            if re.search(r'\bnot\s+two\b', text, re.I) and re.search(r'(?:^|[_ ])(?:2|second)(?:$|[_ ])|[a-z]2(?:$|[_ ])', field):
                wants_false = True
            output_field = bool(re.search(r'(?:all[_ ]outputs|output[_ ]direction)', field))
            input_field = bool(re.search(r'(?:all[_ ]inputs|input[_ ]direction)', field))
            wants_true = wants_true or (output_field and bool(re.search(r'\b(?:drive|drives|driving|transmit|outputs?)\b', text, re.I)))
            wants_true = wants_true or (input_field and bool(re.search(r'\b(?:read|reads|reading|receive|receiving|capture|inputs?)\b', text, re.I)))
            if wants_false != wants_true:
                wanted = 'false' if wants_false else 'true'
                return next(choice['value'] for choice, value in encoded if value == wanted)
        tokens = lambda value: set(re.findall(r'[a-z0-9]+', str(value).lower().replace('_', ' ')))
        without_identifiers = re.sub(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b', ' ', text)
        wanted = tokens(without_identifiers)
        def choice_tokens(value):
            base = tokens(value)
            # Source enums often concatenate task words (AXI4LITE) while the
            # user writes punctuation (AXI4-Lite). Preserve the full token and
            # expose only task tokens literally contained in it.
            return base | {word for token in base for word in wanted
                           if len(word) >= 3 and word != token and word in token}
        scored = [(len(choice_tokens(str(c['value'])+' '+' '.join(c.get('semantic_labels',[]))) & wanted), c['value']) for c in choices]
        if len(scored) < 2:
            return None
        scored.sort(key=lambda item: -item[0])
        return scored[0][1] if scored[0][0] and scored[0][0] > scored[1][0] else None

    @staticmethod
    def part_change_approved(task):
        """The task text explicitly allows changing the project part or device."""
        text = (task or '').lower()
        return bool(re.search(r'\b(part|device)s?\b', text) and
                    re.search(r'\b(swap|chang|switch|retarget|migrat|replac|approv|select a supported|move)', text))

    async def current_part_fit(self):
        """Score whether current live metadata offers a ready setting per requirement."""
        try:
            from native_metadata import rank
            source = await self.api_rows()
        except Exception as exc:
            return dict(matched=0, ready=0, blocked=0, requirements=len(self.requirements),
                        status='inconclusive', reason=str(exc)[:160])
        details = []
        for rid, text in self.requirements.items():
            try:
                rows = rank(source, self.semantic_query(text, text), limit=4,
                            context=list(self.requirements.values()))
                await self.enrich_live(rows)
            except Exception:
                continue
            scored = [(self.parameter_relevance(row, text), row) for row in rows]
            strength = max((score[0] for score, _ in scored), default=0)
            relevant = [row for score, row in scored if strength and score[0] == strength]
            if not relevant:
                continue
            usable = [row for row in relevant if row.get('enabled') is not False
                      and row.get('write_mode') != 'assert']
            details.append(dict(requirement_id=rid, ready=bool(usable),
                                keys=[row['key'] for row in relevant[:3]]))
        ready = sum(item['ready'] for item in details)
        return dict(matched=len(details), ready=ready, blocked=len(details)-ready,
                    requirements=len(self.requirements), status='complete' if ready == len(self.requirements) else 'partial',
                    details=details)

    def named_value_mismatches(self, selections):
        """Requirements whose text names numbers but whose numeric selections use none of them."""
        from intent_contract import facts, number
        result = []
        for rid in sorted({rid for rid, _ in selections}):
            text = self.requirements.get(rid, '')
            # facts() excludes instance indices such as ``clock 3`` and
            # ``channel 1`` while retaining requested counts, widths, rates,
            # and other scalar values.  Treating an index as a desired value
            # can turn a correct disable value (0) into an illegal value (3).
            named = {str(f['value']) for f in facts(text) if number(f.get('value')) is not None}
            numeric = [(cid, str(item['value'])) for (r, cid), item in selections.items() if r == rid
                       and not item.get('indices') and self.candidates[cid].get('type', '').lower() in ['long', 'integer', 'int', 'float', 'double']]
            if named and numeric and not any(self.value_named_in_text(self.candidates[cid], v, text) for cid, v in numeric):
                result.append(dict(requirement_id=rid, text=self.requirements[rid], named_numbers=sorted(named),
                                   selected=[dict(key=self.candidates[cid]['key'], value=v) for cid, v in numeric]))
        return result

    def control_binding(self, name):
        key = 'CONFIG.' + name
        matches = [p for p in self.properties if p.lower() == key.lower()]
        if len(matches) == 1:
            key = matches[0]
        if key in self.properties and not any(r['key'] == key for r in self.nested_rows):
            return dict(key=key)
        matches = [r for r in self.nested_rows if r['subkey'] == name and r['key'] in self.properties]
        if len(matches) == 1:
            return dict(matches[0])
        return None

    async def port_controls(self, query, rows):
        """Consult direct native gating and add at most four new writable controls."""
        from port_dependencies import keywords, parse, detail_fields
        import copy
        all_words = keywords(query, None)
        words = all_words[:8]
        if not words:
            return rows, dict(status='unavailable', reason='No port keywords in this feature', ports=[])
        cache_key = tuple(words)
        if cache_key not in self.port_cache:
            try:
                self.port_cache[cache_key] = parse(await self.native(
                    '[lite::gates ' + atom(self.vlnv) + ' ' + tlist(words) + ' 4]'))
            except Exception as exc:
                self.port_cache[cache_key] = dict(status='unavailable', reason=str(exc)[:180], ports=[])
        context = copy.deepcopy(self.port_cache[cache_key])
        if len(all_words) > 8:
            context['keywords_more'] = True
        result = [dict(row) for row in rows]
        known = {self.row_identity(row): row for row in result}
        controls, added = {}, 0
        for port in context['ports']:
            for name in port['parameters']:
                binding = self.control_binding(name)
                if not binding:
                    continue
                identity = self.row_identity(binding)
                if identity not in known:
                    if added >= 4:
                        context['controls_more'] = True
                        continue
                    row = dict(label=name, type='', default='', help='', help_source='unavailable',
                               range='RANGE_NONE', range_scope='unavailable')
                    row.update(binding)
                    result.append(row);known[identity] = row;added += 1
                row = known[identity]
                row['dependency_ports'] = sorted(set(row.get('dependency_ports', []) + [port['name']]))
                row['dependency_scope'] = 'direct_native_predecessor'
                controls[identity] = row
        flat = [r for r in controls.values() if not r.get('subkey')]
        for start in range(0, len(flat), 4):
            batch = flat[start:start+4]
            keys = [r['key'] for r in batch]
            if self.has_database():
                try:
                    reply = await __import__('asyncio').to_thread(self.guard.database_request, json.dumps(
                        dict(op='lookup', keys=keys, vlnv=self.vlnv, build=self.build, part=self.part)))
                    lookup = {r['key']: r for r in reply.get('parameters', [])}
                    for row in batch:
                        row.update(lookup.get(row['key'], {}))
                except Exception:
                    pass  # Fresh native selected metadata is also available below.
            try:
                raw = split_tcl(await self.native('[lite::describe ' + atom(self.vlnv) + ' ' + tlist(keys) + ']'))
                data = dict(zip(raw[1::2], raw[2::2]))
                params = dict_tcl(data.get('parameters', ''))
                for row in batch:
                    if row['key'] in params:
                        fields = detail_fields(params[row['key']])
                        if fields.get('help_source') == 'unavailable' and row.get('help'):
                            fields.pop('help_source')
                        row.update(fields)
            except Exception as exc:
                context['metadata_unavailable'] = str(exc)[:180]
        nested = [r for r in controls.values() if r.get('subkey')]
        if nested and self.has_database():
            try:
                from nested_config import enrich
                names = list(dict.fromkeys(r['subkey'] for r in nested))
                reply = await __import__('asyncio').to_thread(self.guard.database_request, json.dumps(
                    dict(op='field_hints', keys=names, build=self.build, part=self.part, vlnv=self.vlnv)))
                for row, enriched in zip(nested, enrich(nested, reply.get('fields', {}))):
                    row.update(enriched)
            except Exception as exc:
                context['metadata_unavailable'] = str(exc)[:180]
        return result, context

    @staticmethod
    def port_summary(context, candidates):
        if context is None:
            return None
        summary = {k: context[k] for k in ['status', 'reason', 'matched', 'more', 'controls_more', 'keywords_more', 'scope', 'evidence_path', 'metadata_unavailable'] if k in context}
        summary['ports'] = []
        for port in context.get('ports', []):
            entry = dict(name=port['name'], status=port['status'],
                         candidate_ids=[c['id'] for c in candidates if port['name'] in c.get('dependency_ports', [])])
            mapped_names = {c.get('subkey', c['key'][7:]).lower() for c in candidates if c['id'] in entry['candidate_ids']}
            missing = [p for p in port['parameters'] if p.lower() not in mapped_names]
            if missing:
                entry.update(unresolved_parameters=missing[:4], unresolved_more=len(missing)>4)
            for field in ['derived_parameters', 'derived_parameters_more', 'unmapped_nodes', 'unmapped_nodes_more', 'parameters_more']:
                if port.get(field):
                    entry[field] = port[field][:4] if isinstance(port[field], list) else port[field]
            summary['ports'].append(entry)
        return summary

    def begin_failed(self):
        if self.begin_attempts >= 3:
            self.stage = 'initialization_failed'

    async def vector_base(self, key):
        if key not in self.vector_bases:
            current = await self.native('[get_property ' + atom(key) + ' [get_bd_cells ' + atom(self.cell) + ']]')
            if ',' not in current:
                raise ValueError('Indexed updates require a live comma-separated vector')
            self.vector_bases[key] = current
        return self.vector_bases[key]

    async def merge_selections(self, selections):
        groups = {}
        for (rid, cid), item in selections.items():
            if item.get('mode') == 'assert' or self.candidates[cid].get('write_mode') == 'assert':
                if 'indices' in item:
                    base = await self.vector_base(self.candidates[cid]['key'])
                    encoding = 'boolean' if all(v.strip().lower() in ['true', 'false'] for v in base.split(',')) else 'scalar'
                    item['value'] = await self.native('[lite::vector ' + atom(base) + ' ' + tdict(item['indices']) + ' ' + atom(encoding) + ']')
                continue
            groups.setdefault(self.candidates[cid]['key'], []).append(item)
        config = {}
        for key, items in groups.items():
            nested_items = [item for item in items if 'subkey' in self.candidates[item['candidate_id']]]
            if nested_items:
                if len(nested_items) != len(items):
                    raise ValueError('Cannot mix a dictionary overwrite with selected fields')
                patch = {}
                for item in nested_items:
                    field = self.candidates[item['candidate_id']]['subkey']
                    if field in patch and patch[field] != item['value']:
                        raise ValueError('Conflicting values for the same dictionary field')
                    patch[field] = item['value']
                config[key] = patch
                continue
            patches = [item for item in items if 'indices' in item]
            if patches:
                if len(patches) != len(items):
                    raise ValueError('Use indexed updates consistently for this vector')
                base = await self.vector_base(key)
                encoding = 'boolean' if all(v.strip().lower() in ['true', 'false'] for v in base.split(',')) else 'scalar'
                updates = {}
                for item in patches:
                    for index, value in item['indices'].items():
                        if index in updates and updates[index] != value:
                            raise ValueError('Conflicting values for the same vector index')
                        updates[index] = value
                value = await self.native('[lite::vector ' + atom(base) + ' ' + tdict(updates) + ' ' + atom(encoding) + ']')
                for item in items:
                    item['value'] = value
                config[key] = value
            else:
                if any(item['value'] != items[0]['value'] for item in items):
                    raise ValueError('Conflicting values for the same discovered key')
                config[key] = items[0]['value']
        return config

    ENABLE_TOKENS = {'USED', 'USE', 'ENABLE', 'ENABLED', 'EN'}

    async def enable_companions(self, config):
        """Enable switches implied by the selected writes; returned, never silent.

        A per-output vector write (indices set to non-zero values) turns on the
        same positions of a boolean vector of equal length that shares the
        key's leading name segment and has an enable word (CLKOUT_USED for
        CLKOUT_REQUESTED_OUT_FREQUENCY). A write to a currently disabled scalar
        turns on a false boolean whose remaining name words all belong to that
        key (USE_RESET for RESET_TYPE).
        """
        words = lambda key: set(key[7:].upper().split('_'))
        targets = []
        for (rid, cid), item in self.selections.items():
            row = self.candidates[cid]
            if item.get('mode') == 'assert' or row.get('write_mode') == 'assert' or row.get('subkey'):
                continue
            if 'indices' in item:
                active = [i for i, v in item['indices'].items()
                          if str(v).strip().lower() not in ['', 'false', 'none'] and not re.fullmatch(r'[+-]?0*\.?0*', str(v).strip())]
                if active:
                    targets.append((rid, row['key'], active))
            elif row.get('enabled') is False:
                targets.append((rid, row['key'], None))
        pool = {}
        for rid, key, active in targets:
            for prop in self.properties:
                if prop == key or prop in config or not prop.startswith('CONFIG.') or not words(prop) & self.ENABLE_TOKENS:
                    continue
                rest = words(prop) - self.ENABLE_TOKENS
                if active is not None:
                    related = prop[7:].split('_')[0].upper() == key[7:].split('_')[0].upper()
                else:
                    related = bool(rest) and rest <= words(key)
                if related:
                    pool.setdefault(prop, []).append((rid, key, active))
        if not pool:
            return []
        keys = sorted(pool)[:12]
        values = split_tcl(await self.native('[lmap __k ' + tlist(keys) + ' {get_property $__k [get_bd_cells ' + atom(self.cell) + ']}]'))
        derived = []
        for prop, current in zip(keys, values):
            parts = [p.strip().lower() for p in current.split(',')]
            if not parts or not all(p in ['true', 'false'] for p in parts):
                continue
            changed_by = []
            for rid, key, active in pool[prop]:
                if active is None:
                    if len(parts) == 1 and parts[0] == 'false':
                        parts[0] = 'true';changed_by.append((rid, key))
                    continue
                if len(parts) != len((await self.vector_base(key)).split(',')):
                    continue
                flipped = [int(i) for i in active if int(i) < len(parts) and parts[int(i)] == 'false']
                for i in flipped:
                    parts[i] = 'true'
                if flipped:
                    changed_by.append((rid, key))
            if changed_by:
                config[prop] = ','.join(parts)
                for rid, key in changed_by:
                    derived.append(dict(requirement_id=rid, key=prop, value=config[prop], because=key))
        return derived

    async def resolve_disabled_dependencies(self, config):
        """Choose a unique source controller value that makes a selected value legal.

        The search is bounded to source-listed controller values and probes
        configuration mirrors through ``context_ranges``.  It is used only
        for controllers returned as prerequisites of a currently disabled
        selected field; explicit, unrelated user selections are untouched.
        """
        repairs = []
        for (rid, dependent_id), dependent_item in list(self.selections.items()):
            dependent = self.candidates[dependent_id]
            controls = dependent.get('enablement_controls', [])
            if dependent.get('enabled') is not False or not controls or dependent.get('subkey'):
                continue
            dependent_key, wanted = dependent['key'], dependent_item['value']
            for control_key in controls[:4]:
                selected = [((owner, cid), item, self.candidates[cid])
                            for (owner, cid), item in self.selections.items()
                            if self.candidates[cid]['key'] == control_key
                            and self.candidates[cid].get('related_to') == dependent_key]
                derived = False
                if len(selected) == 1:
                    identity, control_item, control = selected[0]
                elif not selected:
                    physical = [(cid, row) for cid, row in self.candidates.items()
                                if row['key'] == control_key and not row.get('subkey')]
                    identities = {self.row_identity(row) for _, row in physical}
                    if len(identities) != 1 or not physical:
                        continue
                    cid, control = physical[0]
                    identity = (rid, cid)
                    control_item = dict(value=control.get('current'))
                    derived = True
                else:
                    continue
                values = [choice['value'] for choice in control.get('choices', [])]
                if not values:
                    listed = re.fullmatch(r'RANGE_LIST:(.*)', control.get('range', ''))
                    values = split_tcl(listed.group(1)) if listed else []
                values = list(dict.fromkeys(str(value) for value in values))[:8]
                if len(values) < 2:
                    continue
                viable = []
                for value in values:
                    proposed = {key: item for key, item in config.items() if key != dependent_key}
                    proposed[control_key] = value
                    encoded = '[dict create ' + ' '.join(
                        atom(key) + ' ' + (tdict(item) if isinstance(item, dict) else atom(item))
                        for key, item in proposed.items()) + ']'
                    try:
                        raw = dict_tcl(await self.native('[lite::context_ranges ' + atom(self.cell) +
                                                         ' [list ' + atom(dependent_key) + '] ' + encoded + ']'))
                        detail = dict_tcl(raw.get(dependent_key, ''))
                    except Exception:
                        continue
                    probe = dict(detail,
                                 range_complete=str(detail.get('range_complete', '')).lower() in ['1', 'true'])
                    enabled = str(detail.get('enabled', '1')).lower() not in ['0', 'false']
                    if enabled and self.range_accepts(probe, wanted):
                        viable.append(value)
                if len(viable) == 1 and str(control_item['value']) != viable[0]:
                    before = control_item['value']
                    if not derived:
                        control_item['value'] = viable[0]
                    config[control_key] = viable[0]
                    repairs.append(dict(requirement_id=identity[0], candidate_id=identity[1],
                                        key=control_key, before=before, value=viable[0],
                                        enables=dependent_key, wanted=wanted,
                                        derived=derived,
                                        evidence_scope='current_configuration_range_trials'))
        return repairs

    def coalesce_nested_containers(self):
        """Keep exact duplicate fields with the selected related-field majority.

        Some IPs expose aliases of the same nested field through overlapping
        top-level dictionaries.  Sending both containers in one native write
        can make the later container reset the earlier one.  Remap only an
        exact same-requirement, same-subkey source candidate, and only when a
        unique selected container already has more related fields.
        """
        counts = {}
        for (_, cid), item in self.selections.items():
            row = self.candidates[cid]
            if row.get('subkey') and item.get('mode') != 'assert':
                counts[row['key']] = counts.get(row['key'], 0) + 1
        repaired = []
        updated = dict(self.selections)
        for identity, item in list(self.selections.items()):
            rid, cid = identity
            row = self.candidates[cid]
            if not row.get('subkey') or item.get('mode') == 'assert':
                continue
            alternatives = [(other_id, other) for other_id, other in self.candidates.items()
                            if other_id != cid and other.get('requirement_id') == rid
                            and other.get('subkey') == row['subkey'] and other['key'] != row['key']
                            and counts.get(other['key'], 0) > counts.get(row['key'], 0)
                            and (not other.get('range_complete') or self.range_accepts(other, item['value']))]
            if not alternatives:
                continue
            best_count = max(counts.get(other['key'], 0) for _, other in alternatives)
            best = [(other_id, other) for other_id, other in alternatives
                    if counts.get(other['key'], 0) == best_count]
            if len(best) != 1:
                continue
            other_id, other = best[0]
            target = (rid, other_id)
            if target in updated:
                continue
            updated.pop(identity)
            replacement = dict(item, candidate_id=other_id)
            updated[target] = replacement
            repaired.append(dict(requirement_id=rid, subkey=row['subkey'],
                                 from_candidate=cid, from_key=row['key'],
                                 candidate_id=other_id, key=other['key'], value=item['value'],
                                 evidence_scope='exact_duplicate_field_selected_container_majority'))
            counts[row['key']] -= 1
            counts[other['key']] = counts.get(other['key'], 0) + 1
        self.selections = updated
        return repaired

    async def request(self, request):
        # "seq" exists for the stock MCP's duplicate skip, which ipcfg_bridge.py enforces;
        # the relay never hits that skip, so a model following SKILL.md may still send one.
        request = {k: v for k, v in request.items() if k != 'seq'}
        schemas = {'begin':{'op', 'ip', 'cell', 'part', 'task', 'requirements'},
                   'choose_ip': {'op', 'candidate_id', 'part'},
                   'describe': {'op', 'requirement_id', 'query', 'queries'},
                   'select': {'op', 'selections'}, 'waive': {'op', 'requirement_id', 'reason'},
                   'apply': {'op', 'selections'}, 'finish': {'op', 'context'}, 'status': {'op'}}
        op = request.get('op')
        if op not in schemas:
            raise RequestSchemaError('Missing or unknown op; use one of: ' + ', '.join(schemas),
                                     dict(operation=op, allowed_operations=list(schemas)))
        unexpected = sorted(set(request) - schemas[op])
        if unexpected:
            details = dict(operation=op, unexpected_fields=unexpected,
                           allowed_fields=sorted(schemas[op]))
            message = ('Unexpected fields for ' + op + ': ' + ', '.join(unexpected) +
                       '. Allowed fields: ' + ', '.join(sorted(schemas[op])) + '.')
            if 'requirements' in unexpected:
                message += ' Requirements are supplied only in begin; the stored ledger is immutable.'
                same_ledger = request['requirements'] == list(self.requirements.values())
                details['requirements_match_stored_ledger'] = same_ledger
                if unexpected == ['requirements'] and same_ledger:
                    details['corrected_request'] = {key:value for key,value in request.items() if key != 'requirements'}
                    details['guidance'] = 'Resubmit corrected_request once. Only an exact duplicate ledger was omitted; no selections or values changed.'
                else:
                    details['guidance'] = 'Do not discard changed or unverified requirements. Use status to inspect the workflow; preserve the original task.'
            raise RequestSchemaError(message, details)
        if op == 'finish' and 'context' in request:
            context = request['context']
            if (not isinstance(context, dict) or len(context) > 32
                    or any(not isinstance(k, str) or not k or len(k) > 160
                           or not isinstance(v, str) or not v.strip() or len(v) > 600
                           for k, v in context.items())):
                raise ValueError('finish context must map bounded literal strings to nonempty reasons')
        if op == 'status':
            self.valid_operation()
            return self.state(next_allowed=self.stage)
        if self.stage == 'finished':
            closed = self.vivado_closed()
            return self.final_reply or self.state(session_closed=closed, next_op='stop' if closed else 'begin')
        if self.stage == 'initialization_failed':
            return await self.reject_operation(request, 'IP initialization retry limit reached; stop the Vivado session')
        if op == 'apply' and 'selections' in request:
            selections = request.get('selections')
            await self.request({'op': 'select', 'selections': selections})
            result = await self.request({'op': 'apply'})
            result.update(request_repaired=True,
                          request_repairs=[dict(repair='validated_embedded_apply_selections',
                                                count=len(selections) if isinstance(selections, list) else 0)])
            return result
        if op == 'begin':
            if self.stage not in ['new', 'choosing_ip']:
                return await self.reject_operation(request, 'Already initialized; preserve the requirement ledger')
            self.valid_operation()
            texts = request.get('requirements')
            if not isinstance(texts, list) or not 1 <= len(texts) <= 32 or any(not isinstance(t, str) or not t.strip() or len(t) > 600 for t in texts):
                raise ValueError('requirements must contain 1..32 nonempty original feature strings')
            if len(set(texts)) != len(texts):
                raise ValueError('Duplicate requirement text')
            task = request.get('task', '')
            if not isinstance(task, str) or len(task) > 8000:
                raise ValueError('Original task must be a bounded string')
            from intent_contract import missing_clauses, atomic_requirements
            cell = request.get('cell', '')
            named_cells = list(dict.fromkeys(re.findall(r'\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*\d+\b', task)))
            self.multi_cell_scope = (dict(cell=cell, named_cells=named_cells,
                note='This ledger is scoped to the named cell; finish it before beginning the next cell.')
                if isinstance(cell, str) and cell in named_cells and len(named_cells) > 1 else None)
            additions = [] if self.multi_cell_scope else missing_clauses(task, texts, request.get('ip', ''))
            if len(texts) + len(additions) > 32:
                raise ValueError('Split task into at most 32 complete requirements; uncovered clauses=' + str(additions))
            texts = atomic_requirements(list(texts) + additions)
            if len(texts) > 32:
                raise ValueError('Split task into at most 32 atomic requirements')
            self.added_requirements = additions
            self.coverage_checked = True
            from parameter_contract import named_intent
            intent = named_intent(task)
            if intent:
                request = dict(request, **intent)
            live_part = await self.open_project_part() if self.begin_signature is None else None
            if live_part:
                request = dict(request, part=live_part)
            for field in ['ip', 'cell', 'part']:
                if not isinstance(request.get(field), str) or not request[field].strip() or len(request[field]) > 160:
                    raise ValueError('Bounded ip, cell and part strings required')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', request['cell']):
                raise ValueError('Simple cell name required')
            signature = (request['cell'], request['part'], task, tuple(texts))
            if self.begin_signature is not None and self.begin_signature != signature:
                raise ValueError('Retry must preserve the original task, requirements, cell and part')
            if self.begin_attempts >= 3:
                self.stage = 'initialization_failed'
                raise ValueError('IP initialization retry limit reached; stop the Vivado session')
            self.begin_signature = signature
            self.cell, self.part, self.task = request['cell'], request['part'], task
            self.ip_hint = request['ip'].strip()
            if not hasattr(self, 'original_part'):
                self.original_part = self.part
            self.requirements = {f'r{i+1}': text for i, text in enumerate(texts)}
            self.identify_native_assertions()
            if not self.initialized:
                self.begin_attempts += 1
            try:
                if not self.initialized:
                    lib = Path(__file__).resolve().parents[1] / 'lib/lite.tcl'
                    prefix = 'source -notrace ' + atom(lib) + '\nset __init [lite::begin ' + atom(self.part) + ' ' + atom(self.log_file) + ']\n'
                    initialized = dict_tcl(await self.native(
                        '[dict create version [version] install [expr {[info exists ::env(XILINX_VIVADO)] '
                        '? $::env(XILINX_VIVADO) : {}}] init $__init]', prefix))
                    version = initialized['version']
                    init_fields = split_tcl(initialized.get('init', ''))
                    init_data = dict(zip(init_fields[1::2], init_fields[2::2])) if init_fields else {}
                    self.borrowed_session = init_data.get('session_mode') == 'borrowed'
                    # The run root lite::begin actually chose, from the evidence directory it
                    # reports. Deriving it again from the log path disagreed when the agent had
                    # already created the project in <workdir>/proj itself: begin then roots
                    # the run at the working directory, the derivation took its parent, and the
                    # IP's own nested-parameter schema was rejected as foreign evidence, so
                    # describe found none of ps_wizard's PL clock settings (Gemma 4 26B,
                    # Vivado 2026.1, 2026-11-15).
                    if init_data.get('evidence'):
                        self.evidence_root = Path(init_data['evidence']).resolve().parent
                    match = re.search(r'SW Build (\d+)', version)
                    if not match:
                        raise ValueError('Cannot establish installed Vivado SW build')
                    self.build = match[1]
                    # Vivado ships its own release database; use it when no snapshot was mounted,
                    # so the skill carries no external metadata dependency on a current install.
                    adopt = getattr(self.guard, 'adopt_release_database', None)
                    if callable(adopt):
                        try:
                            adopted = adopt(initialized.get('install'), version)
                            if adopted:
                                self.guard.record({'release_database': True, 'path': adopted, 'build': self.build})
                        except Exception as exc:
                            self.guard.record({'release_database': False, 'error': str(exc)[:240]})
                    self.initialized = True
                    if init_fields and init_fields[0] == 'INIT:REUSED':
                        # This Vivado session already served earlier prompts. A new begin for a cell
                        # configured before is a new request about it, not a retry of the old one.
                        try:
                            reset = await self.native('[lite::reset_cell ' + atom(self.cell) + ']')
                            self.guard.record({'reset_cell': self.cell, 'reply': reset})
                        except Exception as exc:
                            self.guard.record({'reset_cell': self.cell, 'error': str(exc)[:240]})
                    await self.seed_part_support()
                existing_vlnv = await self.existing_cell_vlnv(self.cell)
                if existing_vlnv:
                    self.hint_check_task = None
                    return await self.open_cell(existing_vlnv)
                self.hint_check_task = None if intent else task
                return await self.discover_ip(request['ip'].strip())
            except Exception:
                self.begin_failed()
                if self.stage == 'initialization_failed':
                    return await self.finish_partial('IP initialization attempts exhausted')
                raise
        if op == 'choose_ip':
            cid = request.get('candidate_id')
            if self.stage != 'choosing_ip' and not self.ip_candidates:
                self.valid_operation()
                return self.state(vlnv=getattr(self, 'vlnv', None), idempotent=True)
            if cid not in self.ip_candidates:
                raise ValueError('Choose a returned IP candidate ID while choosing_ip')
            if self.ip_candidates[cid].get('rejected'):
                return await self.reject_operation(request, 'This candidate was definitively rejected by live validation')
            if self.stage not in ['choosing_ip', 'discovering', 'ready', 'apply_failed']:
                return await self.reject_operation(request, 'IP selection is unavailable at this stage')
            if self.stage != 'choosing_ip':
                if cid == self.chosen_candidate_id:
                    self.valid_operation()
                    return self.state(vlnv=self.vlnv, candidate_validation=self.candidate_validation,
                                      rejected_candidates=self.rejected_candidates,
                                      reselection_used=self.reselection_used, idempotent=True)
                # Definitive rejection already deletes the provisional cell
                # and returns to choosing_ip.  Do not discard a candidate
                # that live validation accepted or could not disprove.
                if self.candidate_validation and self.candidate_validation.get('status') in ['passed', 'inconclusive']:
                    self.valid_operation()
                    return self.state(vlnv=self.vlnv, candidate_validation=self.candidate_validation,
                                      rejected_candidates=self.rejected_candidates,
                                      reselection_used=self.reselection_used,
                                      selection_locked=True, attempted_candidate_id=cid,
                                      note='The selected candidate passed or was inconclusive under live validation. '
                                           'Continue parameter discovery; a replacement is accepted only after definitive rejection.')
                if self.successful_apply or self.reselection_used:
                    return await self.reject_operation(request, 'A different IP may be selected only once before a successful apply')
                previous = self.vlnv
                deletion = await self.discard_provisional()
                self.clear_ip_state()
                self.reselection_used = True
                self.stage = 'choosing_ip'
            else:
                previous = None
            if self.begin_attempts >= 3:
                self.stage = 'initialization_failed'
                raise ValueError('IP initialization retry limit reached; stop the Vivado session')
            row = self.ip_candidates[cid]
            preferred = self.explicit_identity_candidate()
            if preferred and cid != preferred and row.get('supported_on_current_part') is not False:
                validation = dict(status='rejected', matched_requirements=[], writable_keys=[], trial_write=None,
                                  reason='weaker_explicit_ip_identity', recommended_candidate=preferred)
                row['rejected'] = True
                self.rejected_candidates.append(dict(candidate_id=cid, vlnv=row['vlnv'], validation=validation,
                                                     native_receipt='CELL:NOT_CREATED'))
                self.stage = 'choosing_ip'
                self.valid_operation()
                return self.state(candidate_validation=validation, rejected_candidates=self.rejected_candidates,
                                  recommended_candidate=preferred,
                                  ip_candidates=[dict(id=k, **v) for k, v in self.ip_candidates.items()])
            if row.get('supported_on_current_part') is False:
                part = request.get('part')
                if not isinstance(part, str) or not re.fullmatch(r'[A-Za-z0-9-]{4,64}', part):
                    standins = [k for k, r in self.ip_candidates.items()
                                if k.startswith('c') and r.get('alternative_to') == row['vlnv']]
                    if standins:
                        raise ValueError(cid + ' does not support part ' + self.part + '. Unless the task explicitly asks to '
                                         'change the part or device, choose the supported candidate among '
                                         + ', '.join(standins) + ' whose description performs the requested function; '
                                         'to change the part instead, add "part" with one of ' + str(row['example_parts']))
                    raise ValueError(cid + ' does not support part ' + self.part + '; add "part" with one of its example_parts ' +
                                     str(row['example_parts']) + ', only if the task approves changing the part')
                supported = [k for k, r in self.ip_candidates.items() if r.get('supported_on_current_part') is not False]
                flat = lambda s: ' '.join(re.findall(r'[a-z0-9+]+', (s or '').lower()))
                named = flat(row.get('display_name')) and flat(row.get('display_name')) in flat(self.task)
                if supported and not named and not self.part_change_approved(self.task):
                    raise ValueError(cid + ' would change the project part, and the task neither names this IP nor approves changing the part or device. '
                                     'Nothing changed. Choose the supported candidate (' + ', '.join(supported) + ') that the task describes.')
                reply = split_tcl(await self.native('[lite::set_part ' + atom(row['vlnv']) + ' ' + atom(part) + ']'))
                if not reply or reply[0] != 'PART:OK':
                    raise ValueError('Part change failed: ' + ' '.join(reply)[:300])
                self.part_change = dict(previous=self.part, part=part, vlnv=row['vlnv'])
                self.part = part
            self.begin_attempts += 1
            try:
                try:
                    result = await self.choose_candidate(cid)
                except Exception as first_error:
                    recoverable = (row.get('supported_on_current_part') is False
                                   and self.part_change_approved(self.task)
                                   and request.get('part', '').lower() not in self.task.lower())
                    if not recoverable:
                        raise
                    attempts = [dict(part=self.part, status='creation_failed',
                                     reason=str(first_error).split('; evidence=')[0][-180:])]
                    recovered = False
                    for trial_part in [p for p in row.get('example_parts', []) if p != self.part][:3]:
                        try:
                            await self.native('[lite::discard_cell ' + atom(self.cell) + ' ' + atom(row['vlnv']) + ']')
                        except Exception:
                            pass
                        self.created_by_session = False
                        self.clear_ip_state()
                        self.stage = 'choosing_ip'
                        changed = split_tcl(await self.native('[lite::set_part ' + atom(row['vlnv']) + ' ' + atom(trial_part) + ']'))
                        if not changed or changed[0] != 'PART:OK':
                            attempts.append(dict(part=trial_part, status='unavailable'))
                            continue
                        self.part_change = dict(previous=getattr(self, 'original_part', self.part),
                                                part=trial_part, vlnv=row['vlnv'])
                        self.part = trial_part
                        try:
                            result = await self.choose_candidate(cid)
                            attempts.append(dict(part=trial_part, status='created'))
                            recovered = True
                            break
                        except Exception as exc:
                            attempts.append(dict(part=trial_part, status='creation_failed',
                                                 reason=str(exc).split('; evidence=')[0][-180:]))
                    if not recovered:
                        raise first_error
                    result['creation_recovery'] = dict(selected=self.part, attempts=attempts,
                                                       scope='returned_example_parts')
                # A task-approved generic retarget may have many supported
                # example parts with different live customizer availability.
                # If the model's first arbitrary part leaves multiple matched
                # requirements disabled, try only the other returned examples
                # and keep the first part whose relevant settings are all ready.
                if (row.get('supported_on_current_part') is False and self.part_change_approved(self.task)
                        and request.get('part', '').lower() not in self.task.lower()
                        and self.created_by_session):
                    assessments = [dict(part=self.part, **(await self.current_part_fit()))]
                    best = (assessments[0]['ready'], assessments[0]['matched'], -assessments[0]['blocked'], self.part)
                    choices = [p for p in row.get('example_parts', []) if p != self.part][:3]
                    while (assessments[-1]['status'] != 'complete' and choices):
                        trial_part = choices.pop(0)
                        if self.created_by_session:
                            await self.discard_provisional()
                            self.clear_ip_state()
                        changed = split_tcl(await self.native('[lite::set_part ' + atom(row['vlnv']) + ' ' + atom(trial_part) + ']'))
                        if not changed or changed[0] != 'PART:OK':
                            assessments.append(dict(part=trial_part, matched=0, ready=0, blocked=0,
                                                    requirements=len(self.requirements), status='unavailable'))
                            continue
                        self.part_change = dict(previous=self.part, part=trial_part, vlnv=row['vlnv'])
                        self.part = trial_part
                        try:
                            result = await self.choose_candidate(cid)
                        except Exception as exc:
                            # Catalog support does not guarantee that a
                            # customizer can instantiate on every listed part.
                            # Delete only our exact provisional name, restore
                            # the original project part, and keep scanning.
                            try:
                                await self.native('[lite::discard_cell ' + atom(self.cell) + ' ' + atom(row['vlnv']) + ']')
                            except Exception:
                                pass
                            restored = await self.native('[lite::restore_part ' + atom(self.part_change['previous']) + ']')
                            if not restored.startswith('PART:RESTORED'):
                                raise ValueError('Part restoration failed after provisional customizer failure') from exc
                            self.part = self.part_change['previous']
                            self.part_change = None
                            self.created_by_session = False
                            self.clear_ip_state()
                            self.stage = 'choosing_ip'
                            assessments.append(dict(part=trial_part, matched=0, ready=0, blocked=0,
                                                    requirements=len(self.requirements), status='unavailable',
                                                    reason=str(exc).split('; evidence=')[0][-180:]))
                            continue
                        assessment = dict(part=self.part, **(await self.current_part_fit()))
                        assessments.append(assessment)
                        score = (assessment['ready'], assessment['matched'], -assessment['blocked'], self.part)
                        if score[:3] > best[:3]:
                            best = score
                    if self.part != best[3]:
                        await self.discard_provisional()
                        self.clear_ip_state()
                        changed = split_tcl(await self.native('[lite::set_part ' + atom(row['vlnv']) + ' ' + atom(best[3]) + ']'))
                        if not changed or changed[0] != 'PART:OK':
                            raise ValueError('Best live-compatible part could not be reopened')
                        self.part_change = dict(previous=self.part, part=best[3], vlnv=row['vlnv'])
                        self.part = best[3]
                        result = await self.choose_candidate(cid)
                    result['part_fit'] = dict(selected=self.part, assessments=assessments,
                                              scope='returned_example_parts_live_parameter_readiness')
                self.valid_operation()
                if previous:
                    result.update(reselection_used=True, previous_vlnv=previous,
                                  vlnv=getattr(self, 'vlnv', row['vlnv']), deletion_receipt=deletion[:300])
                return result
            except Exception:
                self.begin_failed()
                if self.stage == 'initialization_failed':
                    return await self.finish_partial('IP initialization attempts exhausted')
                raise
        if (op == 'finish' and self.stage == 'choosing_ip' and not getattr(self, 'premature_finish_warned', False)
                and any(k.startswith('c') for k in self.ip_candidates)):
            # The named IP is not on this part but supported same-category IPs were offered; a finish
            # here saves nothing (#674: the agent stopped instead of choosing SmartConnect). Once.
            self.premature_finish_warned = True
            return self.state(premature_finish=True, next_op='choose_ip',
                              ip_candidates=[dict(id=k, **v) for k, v in self.ip_candidates.items()],
                              reason='Nothing has been configured yet. The named IP is not available on this part, '
                                     'but supported IPs of the same category are offered as c* candidates: choose the '
                                     'one whose description performs the requested function with choose_ip. Send '
                                     'finish again only if none of them fits.')
        if op == 'finish' and not self.apply_attempts:
            if self.selections:
                unresolved = self.pending()
                applied = None
                self.allow_partial_apply = True
                try:
                    # Apply can pause once for coverage and once for a no-op
                    # review. Finish is an explicit request to preserve the
                    # grounded subset, so complete those bounded reviews.
                    for _ in range(3):
                        applied = await self.request({'op': 'apply'})
                        if self.apply_attempts or self.stage in ['finished', 'close_failed']:
                            break
                finally:
                    self.allow_partial_apply = False
                if self.stage in ['finished', 'close_failed']:
                    return applied
                final = await self.request({'op': 'finish'})
                final.update(partial_apply_trigger='finish_with_grounded_selections',
                             unresolved_requirements=unresolved,
                             partial_note='Grounded selections were applied before partial finish; unresolved requirements remain unclaimed.')
                return final
            return await self.finish_partial('Unresolved requirements; no configuration applied')
        if self.stage == 'new':
            return await self.reject_operation(request, 'Call begin first')
        if self.stage in ['choosing_ip', 'initialization_failed']:
            return await self.reject_operation(request, 'Select an IP candidate before parameter operations, or stop after the retry limit')
        if self.stage == 'finished' or (self.stage != 'literal_review' and self.apply_attempts and op in ['describe', 'select', 'waive'] and
                                        (self.stage == 'configured' or self.apply_attempts >= 2)):
            return await self.reject_operation(request, 'Ledger locked after apply; use status, apply retry, or finish')
        self.valid_operation()
        if op == 'describe':
            batch = request.get('queries')
            if batch is not None:
                if 'requirement_id' in request or 'query' in request:
                    raise ValueError('Batch describe uses queries only')
                if not isinstance(batch, list) or not 1 <= len(batch) <= 12:
                    raise ValueError('queries must contain 1..12 requirement IDs or query objects')
                duplicate_ids = []
                normalized_batch = []
                seen = set()
                for item in batch:
                    if isinstance(item, str):
                        rid, query = item, None
                        if rid not in self.requirements:
                            prefix = re.fullmatch(r'(r\d+)\.p\d+', rid)
                            if prefix and prefix[1] in self.requirements:
                                rid = prefix[1]
                            else:
                                query_terms = set(self.significant_terms(item))
                                scores = sorted((len(query_terms & set(self.significant_terms(text))), candidate)
                                                for candidate, text in self.requirements.items()
                                                if candidate not in seen)
                                if scores and scores[-1][0] and (len(scores) == 1 or scores[-1][0] > scores[-2][0]):
                                    rid, query = scores[-1][1], item
                    elif isinstance(item, dict) and set(item) <= {'requirement_id', 'query'}:
                        rid, query = item.get('requirement_id'), item.get('query')
                    else:
                        raise ValueError('Each batch query is a requirement ID or {requirement_id, query}')
                    if rid not in self.requirements:
                        raise ValueError('Use a returned requirement ID')
                    if rid in seen:
                        duplicate_ids.append(rid)
                        continue
                    if query is not None and (not isinstance(query, str) or not query.strip() or len(query) > 600):
                        raise ValueError('Bounded feature query required')
                    seen.add(rid)
                    normalized_batch.append((rid, query))
                descriptions = []
                keep = {'requirement_id', 'backend', 'candidates', 'selection_bundles',
                        'port_dependencies', 'requires_live_validation', 'no_candidates_reason',
                        'repeated_query', 'describe_exhausted', 'awaiting_selection', 'note',
                        'satisfied_by_native'}
                for rid, query in normalized_batch:
                    subrequest = {'op': 'describe', 'requirement_id': rid}
                    if query is not None:
                        subrequest['query'] = query
                    reply = await self.request(subrequest)
                    descriptions.append({key: reply[key] for key in keep if key in reply})
                    if self.stage == 'finished':
                        return reply
                if duplicate_ids:
                    descriptions.append(dict(request_repaired='Dropped duplicate requirement IDs: ' + ','.join(duplicate_ids)))
                return self.state(descriptions=descriptions,
                                  batch_describe=True,
                                  next_op='select',
                                  note='Choose candidates from these descriptions, then send all selections in one select call.')
            rid = request.get('requirement_id')
            if rid not in self.requirements:
                raise ValueError('Use a returned requirement ID')
            if rid in self.native_assertions:
                assertion = self.native_assertions[rid]
                return self.state(requirement_id=rid, candidates=[],
                                  satisfied_by_native=assertion,
                                  note='The requested cell name is already established by the native BD cell identity; no configurable parameter is needed.')
            query = request.get('query', self.requirements[rid])
            if not isinstance(query, str) or not query.strip() or len(query) > 600:
                raise ValueError('Bounded feature query required')
            query = self.semantic_query(query, self.requirements[rid])
            asked = self.asked_queries.setdefault(rid, {})
            normalized = ' '.join(query.lower().split())
            if normalized in asked and self.repeated_queries.get(rid, 0) < 2:
                self.repeated_queries[rid] = self.repeated_queries.get(rid, 0) + 1
                earlier = [self.compact(row, cid) for cid, row in self.candidates.items() if row.get('requirement_id') == rid][:8]
                return self.state(requirement_id=rid, repeated_query=True, candidates=earlier,
                                  note='This exact query was already answered; repeating it returns the same candidates and does not use describe budget. '
                                       'Select one of them, or describe again with different words (a setting name, label words or units).')
            asked[normalized] = True
            count = self.describe_counts.get(rid, 0)
            if count >= self.describe_limit(rid):
                self.exhausted[rid] = self.exhausted.get(rid, 0) + 1
                earlier = [self.compact(row, cid) for cid, row in self.candidates.items() if row.get('requirement_id') == rid][:8]
                described = {row.get('requirement_id') for row in self.candidates.values()}
                waiting = [p for p in self.pending() if p['id'] != rid and p['id'] in described]
                if self.exhausted[rid] >= (4 if waiting else 2) or not (earlier or self.selections or waiting or len(self.exhausted) < len(self.requirements)):
                    if self.selections:
                        result = await self.bounded_finish('Parameter discovery exhausted for ' + rid,
                                                           exhaustion_finish_trigger=rid)
                        result.setdefault('reason', 'Parameter discovery exhausted for ' + rid)
                        return result
                    return await self.finish_partial('Parameter discovery exhausted for ' + rid)
                note = 'Describe budget for ' + rid + ' is used; do not describe it again. Select from these earlier candidates if one fits; otherwise continue with the other requirements. apply may then run without ' + rid + ' and the result is reported as partial.'
                if self.exhausted[rid] >= 2 and waiting:
                    note += ' Select now for ' + ', '.join(p['id'] for p in waiting) + ' from the candidates already returned for them.'
                return self.state(requirement_id=rid, describe_exhausted=True, candidates=earlier,
                                  awaiting_selection=[p['id'] for p in waiting], note=note)
            self.describe_counts[rid] = count + 1
            rows, fallback = [], None
            if self.nested_rows:
                from nested_config import rank_fields, rank_fields_covering, enrich
                compound = len(__import__('intent_contract').facts(self.requirements[rid])) > 1
                shortlist = rank_fields(self.nested_rows, query, 24 if compound else 12)
                hints = {}
                if shortlist and self.has_database():
                    try:
                        names = list(dict.fromkeys(r['subkey'] for r in shortlist))
                        reply = await __import__('asyncio').to_thread(self.guard.database_request, json.dumps(
                            dict(op='field_hints', keys=names, build=self.build, part=self.part, vlnv=self.vlnv)))
                        hints = reply.get('fields', {})
                    except Exception as exc:
                        fallback = str(exc)[:240]
                enriched = enrich(shortlist, hints)
                rows = rank_fields_covering(enriched, query, 6) if compound else rank_fields(enriched, query, 4)
            mixed = False
            if rows:
                # A loose dictionary-field match must not hide a better-named top-level parameter.
                try:
                    from native_metadata import rank
                    from nested_config import name_matches
                    containers = {r['key'] for r in self.nested_rows}
                    top = rank([r for r in await self.api_rows() if r['key'] not in containers], query, context=list(self.requirements.values()))
                    best = lambda items: max((name_matches(r, query) for r in items), default=0)
                    if top and best(top) >= best(rows):
                        rows, mixed = (top[:2] + rows[:2]) if best(top) > best(rows) else (rows[:2] + top[:2]), True
                except Exception as exc:
                    fallback = str(exc)[:240]
            if not rows and self.has_database():
                try:
                    reply = await __import__('asyncio').to_thread(self.guard.database_request, json.dumps(
                        dict(op='parameters', query=query, vlnv=self.vlnv, build=self.build, part=self.part)))
                    rows = [r for r in reply['candidates'] if r['key'] in self.properties]
                except Exception as exc:
                    fallback = str(exc)[:240]
            backend = ('native_nested_and_live' if mixed else 'native_nested_with_database_hints') if rows and self.nested_rows else 'database'
            if not rows:
                backend = 'live'
                try:
                    from native_metadata import rank
                    rows = rank(await self.api_rows(), query, context=list(self.requirements.values()))
                    if self.metadata_fallback:
                        backend, fallback = 'packaged_component_xml', self.metadata_fallback
                except Exception as exc:
                    fallback = str(exc)[:240]
            if not rows:
                words = list(dict.fromkeys(re.findall(r'[A-Za-z_]{3,48}', query)))[:8] or [query[:48]]
                raw = await self.native('[lite::inspect_for ' + atom(self.vlnv) + ' ' + atom(self.cell) + ' ' + tlist(words) + ' 4]')
                parts = split_tcl(raw)
                data = dict(zip(parts[1::2], parts[2::2]))
                rows = [dict_tcl(row) for row in split_tcl(data.get('candidates', ''))]
                rows = [r for r in rows if r['key'] in self.properties]
                # Selected live purpose/help comes from the existing helper.
                if rows:
                    described = split_tcl(await self.native('[lite::describe ' + atom(self.vlnv) + ' ' + tlist([r['key'] for r in rows]) + ']'))
                    detail = dict(zip(described[1::2], described[2::2]))
                    params = dict_tcl(detail.get('parameters', ''))
                    for row in rows:
                        fields = dict_tcl(params.get(row['key'], ''))
                        row.update(help=fields.get('help', ''), help_source=fields.get('help_source', 'unavailable'),
                                   range_scope='native_default_instance', requires_live_validation=True)
            if self.nested_rows and any(r.get('subkey') for r in rows):
                from nested_config import related_fields, enable_counterparts
                rows += related_fields(self.nested_rows, [r for r in rows if r.get('subkey')], query)
                rows += enable_counterparts(self.nested_rows, [r for r in rows if r.get('subkey')], query)
            primary_limit = 6 if len(__import__('intent_contract').facts(self.requirements[rid])) > 1 else 4
            primary, related = rows[:primary_limit], rows[primary_limit:]
            rows, port_context = await self.port_controls(query, primary)
            rows += related
            await self.enrich_live(rows)
            rows = await self.verified_prerequisites(rows)
            rows = await self.prerequisite_candidates(rows)
            rows = await self.infer_live_prerequisites(rows)
            rows = await self.port_name_controls(rows)
            # After every source has contributed rows, so a copy reached through a
            # prerequisite or related-field path also brings its siblings.
            siblings = await self.same_label_siblings(rows)
            if siblings:
                await self.enrich_live(siblings)
                rows += siblings
            rows = self.enabled_first(rows)
            rows = self.requirement_role_first(rows, self.requirements[rid])
            evidence = self.guard.record({'structured_metadata': True, 'requirement_id': rid,
                                          'backend': backend, 'fallback': fallback, 'parameters': rows,
                                          'port_dependencies': port_context})
            candidates = self.register_candidates(rows, rid, evidence)
            bundles = self.family_bundles(rid, candidates)
            extra = {} if candidates else dict(no_candidates_reason=(fallback or 'no parameter matched this query') +
                                               '; rephrase with the setting name or purpose, or waive this requirement if it is context that needs no IP setting')
            if self.stage == 'discovering' and not self.pending():
                self.stage = 'ready'
            return self.state(requirement_id=rid, backend=backend, candidates=candidates,
                              selection_bundles=bundles,
                              port_dependencies=self.port_summary(port_context, candidates),
                              requires_live_validation=True, evidence_path=str(evidence), **extra)
        if op == 'waive':
            rid, reason = request.get('requirement_id'), request.get('reason')
            if rid not in self.requirements:
                raise ValueError('Use a returned requirement ID')
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
                raise ValueError('Waive needs a short reason (1..240 characters) why no IP setting is required')
            if not (set(self.requirements) - set(self.waived) - {rid}):
                raise ValueError('At least one requirement must map to an IP setting; finish partial instead')
            if not self.describe_counts.get(rid):
                raise ValueError('Describe ' + rid + ' at least once before waiving it')
            from intent_contract import facts, normalized
            explicit = {normalized(f['text']) for f in facts(self.requirements[rid]) if f['unit'] == 'literal'}
            matches = [dict(candidate_id=cid, key=row['key'], value=c['value'], label=c.get('label', ''))
                       for cid, row in self.candidates.items() for c in row.get('choices', [])
                       if explicit & {normalized(c['value']), normalized(c.get('label', ''))}]
            if matches:
                raise ValueError('Requested value has a source enum encoding and cannot be waived. '
                                 'Describe it on ' + rid + ' and select its matching encoding: ' + json.dumps(matches[:4]))
            if any(r == rid for r, _ in self.selections):
                raise ValueError(rid + ' already has a selection; it cannot also be waived')
            relevant = [dict(candidate_id=cid, key=row['key'], label=row.get('label', ''))
                        for cid, row in self.candidates.items() if row.get('requirement_id') == rid]
            negative_setting = re.search(
                r'\b(?:no|without)\s+(?:the\s+)?(?:[A-Za-z0-9_-]+\s+){0,4}'
                r'(?:ports?|clocks?|channels?|lanes?|inputs?|outputs?|interfaces?|masters?|slaves?)\b',
                self.requirements[rid], re.I)
            if negative_setting and relevant:
                raise ValueError('This requirement explicitly requests zero/disabled hardware and cannot be waived. '
                                 'Select the returned writable setting(s): ' + json.dumps(relevant[:6]))
            if self.actionable_requirement(self.requirements[rid]):
                detail = (' Returned candidates=' + json.dumps(relevant[:4])) if relevant else ''
                raise ValueError('This requirement requests observable IP behavior and cannot be waived as context.' +
                                 detail + ' Select a grounded setting or finish partial.')
            self.waived[rid] = reason.strip()
            self.stage = 'ready' if not self.pending() else 'discovering'
            return self.state(waived=[dict(requirement_id=k, text=self.requirements[k], reason=v) for k, v in self.waived.items()],
                              original_task_review=self.task if not self.pending() else None)
        if op == 'select':
            selections = request.get('selections')
            if not isinstance(selections, list) or not 1 <= len(selections) <= 32:
                raise ValueError('Supply 1..32 selections')
            selection_repairs = []
            expanded = []
            for raw in selections:
                if isinstance(raw, dict) and raw.get('candidate_id') in self.bundles:
                    bundle = self.bundles[raw['candidate_id']]
                    if raw.get('requirement_id') != bundle['requirement_id']:
                        raise ValueError('Bundle ID must be used with its returned requirement_id')
                    for cid in bundle['candidate_ids']:
                        expanded.append(dict(raw,candidate_id=cid))
                    selection_repairs.append(dict(candidate_id=raw['candidate_id'],repair='expanded_family_bundle',
                                                  members=bundle['candidate_ids']))
                else:
                    expanded.append(raw)
            selections = expanded
            if len(selections) > 32:
                raise ValueError('Expanded selections exceed the bounded 32-member limit')
            normalized_selections = []
            for raw in selections:
                item = dict(raw) if isinstance(raw, dict) else raw
                if isinstance(item, dict):
                    identity = (item.get('requirement_id'), item.get('candidate_id'))
                    if 'value' not in item and identity in self.selections:
                        item['value'] = self.selections[identity]['value']
                        selection_repairs.append(dict(candidate_id=identity[1], repair='reused_selected_value'))
                    if isinstance(item.get('covers'), str):
                        item['covers'] = [item['covers']]
                        selection_repairs.append(dict(candidate_id=identity[1], repair='wrapped_scalar_covers'))
                normalized_selections.append(item)
            selections = normalized_selections
            updated = {key: dict(item) for key, item in self.selections.items()}
            if self.apply_attempts:
                # Re-selection after a failed apply replaces that requirement's mapping.
                batch = {item.get('requirement_id') for item in selections if isinstance(item, dict)}
                updated = {key: item for key, item in updated.items() if key[0] not in batch}
            seen = set()
            for item in selections:
                if not isinstance(item, dict) or not {'requirement_id', 'candidate_id', 'value'} <= set(item) or set(item) - {'requirement_id', 'candidate_id', 'value', 'mode', 'covers', 'grounding', 'subkey'}:
                    raise ValueError('Each selection needs requirement_id, candidate_id and value')
                if item.get('mode', 'write') not in ['write', 'assert']:
                    raise ValueError('Selection mode is write or assert')
                rid, cid, value = item['requirement_id'], item['candidate_id'], item['value']
                candidate = self.candidates.get(cid)
                if rid not in self.requirements or not candidate:
                    valid = [c for c, r in self.candidates.items() if r['requirement_id'] in [None, rid]]
                    raise ValueError('Candidate ID must have been returned for this requirement; ' + str(rid) + ' valid IDs=' + ','.join(valid))
                explicit_target=self.explicit_setting_target(candidate,value)
                if explicit_target is not None and explicit_target!=rid:
                    previous_rid=rid
                    rid=explicit_target
                    item['requirement_id']=rid
                    selection_repairs.append(dict(candidate_id=cid,
                        source_requirement_id=previous_rid,requirement_id=rid,
                        repair='routed_explicit_setting_candidate'))
                if candidate['requirement_id'] not in [None, rid]:
                    twins=[(other_cid,other) for other_cid,other in self.candidates.items()
                           if other.get('requirement_id')==rid
                           and self.row_identity(other)==self.row_identity(candidate)]
                    if len(twins)==1:
                        previous_cid=cid
                        cid,candidate=twins[0]
                        item['candidate_id']=cid
                        selection_repairs.append(dict(candidate_id=previous_cid,
                            replacement_candidate_id=cid,
                            repair='canonicalized_requirement_candidate_id'))
                if candidate['requirement_id'] not in [None, rid]:
                    mapped = self.text_choice(candidate, self.requirements[rid])
                    explicit = self.explicit_setting_choice(candidate,self.requirements[rid])
                    if ((mapped is None or str(mapped) != str(value))
                            and (explicit is None or str(explicit) != str(value))):
                        valid = [c for c, r in self.candidates.items() if r['requirement_id'] in [None, rid]]
                        raise ValueError('Candidate ID was returned for another requirement and its published value mapping does not match '
                                         + str(rid) + '; valid IDs=' + ','.join(valid))
                    selection_repairs.append(dict(candidate_id=cid,
                                                  source_requirement_id=candidate['requirement_id'],
                                                  requirement_id=rid,
                                                  repair='reused_source_mapped_candidate'))
                # A returned field candidate already binds its dictionary subkey. Small
                # models sometimes echo that visible subkey in the selection. Accept an
                # exact echo, but never let the request redirect the candidate to a
                # different field.
                if 'subkey' in item and item['subkey'] != candidate.get('subkey'):
                    raise ValueError('Selection subkey must match the returned candidate field; use candidate_id only')
                # A readback of what a clock achieved cannot be written, and marking
                # it write_mode assert only turned ced-gen_M7's silent success into a
                # failed apply: the 26B model selected PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ
                # for "PL clock at 100 MHz" over the request parameter beside it,
                # because the readback carries value_src USER and a live-looking value.
                # Send the write to the parameter that sets the clock instead.
                if candidate.get('derived_scope') == 'achieved_readback' and item.get('mode', 'write') == 'write':
                    setter = [c for c, row in self.candidates.items()
                              if row['key'] == candidate['derived_from'] and not row.get('subkey')
                              and row['requirement_id'] in [None, rid]]
                    if not setter:
                        raise ValueError('CONFIG.' + candidate['key'][7:] + ' reports what the hardware achieved and cannot be '
                                         'written. Describe ' + candidate['derived_from'] + ' and select that instead.')
                    previous_cid = cid
                    cid, candidate = setter[0], self.candidates[setter[0]]
                    item['candidate_id'] = cid
                    selection_repairs.append(dict(candidate_id=previous_cid, replacement_candidate_id=cid,
                                                  repair='redirected_achieved_readback_to_its_setter'))
                if (self.bank_missing() and item.get('mode', 'write') == 'write'
                        and self.received_quantity(candidate)):
                    raise ValueError('CONFIG.' + candidate['key'][7:] + ' has no published range, enablement or help, because this '
                                     'cell has no parameter bank in this session. Nothing would check the value before it was '
                                     'written, so it can be read back but not set here. A frequency, width or mode this cell '
                                     'receives is set on the cell that produces it; configure that one instead.')
                # A *_BOARD_INTERFACE parameter binds the cell to a board part
                # interface; writing it (usually Custom) detaches the board wiring.
                # ced-gen_H3 (Haiku) switched the DDR4 controller to Custom while
                # chasing a clock, and E2 (Gemma-31B) unbound the ZC702 LEDs.
                if (str(candidate.get('key', '')).upper().endswith('_BOARD_INTERFACE') and item.get('mode', 'write') == 'write'
                        and not re.search(r'\b(?:board|interface|custom)\b', self.requirements[rid], re.I)):
                    raise ValueError('CONFIG.' + candidate['key'][7:] + ' binds this cell to a board interface; changing it '
                                     'rewires the board connection, which this requirement does not ask for. Select the '
                                     'parameter that holds the requested value instead.')
                qualified=self.qualified_source_alternatives(rid,candidate)
                if qualified:
                    chosen_ids = {entry.get('candidate_id') for entry in selections
                                  if isinstance(entry, dict) and entry.get('requirement_id') == rid}
                    replacements = [entry for entry in qualified if entry['candidate_id'] in chosen_ids]
                    if len(replacements) == 1:
                        updated.pop((rid, cid), None)
                        selection_repairs.append(dict(candidate_id=cid,
                                                      repair='dropped_redundant_unqualified_candidate',
                                                      replacement_candidate_id=replacements[0]['candidate_id']))
                        continue
                    raise ValueError('This shared source label lacks the requested interface context. '
                                     'The source GUI help specifically maps the interface to '+json.dumps(qualified)+
                                     '. Select the source-qualified candidate ID instead.')
                if candidate.get('enabled') is False:
                    current_exact = self.parameter_relevance(candidate, self.requirements[rid])[0]
                    better = []
                    for entry in selections:
                        if not isinstance(entry, dict) or entry.get('requirement_id') != rid or entry.get('candidate_id') == cid:
                            continue
                        other = self.candidates.get(entry.get('candidate_id'))
                        if other and other.get('enabled') is not False and self.parameter_relevance(other, self.requirements[rid])[0] >= current_exact + 2:
                            better.append(entry['candidate_id'])
                    if better:
                        updated.pop((rid, cid), None)
                        selection_repairs.append(dict(candidate_id=cid,
                                                      repair='dropped_redundant_disabled_candidate',
                                                      better_enabled_candidates=better[:4]))
                        continue
                if not candidate.get('subkey') and any(r['key'] == candidate['key'] for r in self.nested_rows):
                    raise ValueError('Preserve unrelated dictionary fields; describe and select individual field IDs')
                if candidate.get('shape') == 'dictionary':
                    value = self.inner_dictionary_value(candidate, value)
                if (isinstance(value, list) and value and not candidate.get('subkey')
                        and ',' in str(candidate.get('default', '')) and not candidate.get('default_truncated')):
                    base = await self.vector_base(candidate['key'])
                    if len(value) <= len(base.split(',')):
                        value = {'indices': {str(i): v for i, v in enumerate(value)}}
                        item = dict(item, value=value)
                        selection_repairs.append(dict(candidate_id=cid, repair='array_to_indexed_patch'))
                patch = isinstance(value, dict)
                if candidate.get('subkey') and patch:
                    raise ValueError('Select a scalar value for the returned dictionary field')
                if patch and candidate.get('shape') == 'scalar':
                    raise ValueError(candidate['key'] + ' is a scalar; indexed values need a returned vector candidate')
                if patch:
                    indices = value.get('indices')
                    if set(value) != {'indices'} or not isinstance(indices, dict) or not 1 <= len(indices) <= 64:
                        raise ValueError('Vector values accept only {"indices":{"0":value}}; select nested field IDs with scalar values')
                    if any(not isinstance(i, str) or not re.fullmatch(r'\d{1,3}', i) for i in indices):
                        raise ValueError('Vector indices must be nonnegative decimal strings')
                    if len({int(i) for i in indices}) != len(indices):
                        raise ValueError('Duplicate normalized vector index')
                    values = list(indices.values())
                else:
                    count_choice = self.explicit_count_choice(candidate, self.requirements[rid])
                    if count_choice is not None and str(value) != str(count_choice):
                        selection_repairs.append(dict(candidate_id=cid,
                            repair='used_explicit_ledger_count', requested=value,
                            value=count_choice))
                        value = count_choice
                        item = dict(item, value=value)
                    role_choice = self.role_numeric_choice(candidate, self.requirements[rid])
                    if role_choice is not None and str(value) != str(role_choice):
                        selection_repairs.append(dict(candidate_id=cid, repair='used_unique_ledger_size',
                                                      requested=value, value=role_choice))
                        value = role_choice
                        item = dict(item, value=value)
                    choices = candidate.get('choices', [])
                    label_matches = [choice for choice in choices
                                     if str(choice.get('label', '')).strip()
                                     and str(choice.get('label')).strip().casefold() == str(value).strip().casefold()]
                    if len(label_matches) == 1 and str(value) != str(label_matches[0].get('value')):
                        encoded = label_matches[0].get('value')
                        selection_repairs.append(dict(candidate_id=cid,
                            repair='normalized_unique_source_enum_label',
                            requested=value, value=encoded))
                        value = encoded
                        item = dict(item, value=value)
                    values = [value]
                choice_spellings = [str(c.get('value')) for c in candidate.get('choices', [])]
                if (not patch and isinstance(value, (str, bool, int)) and choice_spellings
                        and {v.lower() for v in choice_spellings} <= {'0', '1', 'true', 'false'}):
                    spelled = [v for v in choice_spellings if v.lower() == str(value).strip().lower()]
                    if spelled and spelled[0] != str(value):
                        selection_repairs.append(dict(candidate_id=cid, repair='normalized_boolean_spelling',
                                                      requested=value, value=spelled[0]))
                        value = spelled[0]
                        item = dict(item, value=value)
                        values = [value]
                from parameter_contract import boolean_value
                if candidate.get('type', '').lower() in ['bool', 'boolean'] or candidate.get('element_type') == 'boolean':
                    normalized = [boolean_value(v) for v in values]
                    if patch:
                        indices = dict(zip(indices, normalized))
                    else:
                        value = normalized[0]
                if any(not isinstance(v, (str, int, float, bool)) or (isinstance(v, float) and not math.isfinite(v)) or len(str(v)) > 1024 for v in values):
                    raise ValueError('Bounded finite scalar or indexed vector values required')
                if not patch and candidate.get('type', '').lower() in ['long', 'integer', 'int'] and not re.fullmatch(r'[+-]?\d+', str(int(value) if isinstance(value, bool) else value)):
                    raise ValueError('This candidate expects an integer')
                from intent_contract import facts, unit_value, number
                expected=[unit_value(candidate,f) for f in facts(self.requirements[rid]) if unit_value(candidate,f) is not None]
                if not patch and expected and number(value) is not None and not any(
                        math.isclose(number(value),v,rel_tol=1e-12,abs_tol=1e-9) for v in expected):
                    self.soft_reject('S5', rid, cid, candidate, value, 'Source unit for '+candidate['key']+' is '+candidate['units']+
                                     '; the requirement converts to '+str(expected)+'. Preserve the exact requested frequency.',
                                     selection_repairs)
                identity = (rid, cid)
                if identity in seen:
                    raise ValueError('Duplicate requirement/candidate pair in one batch: ' + rid + '/' + cid)
                seen.add(identity)
                choices = candidate.get('choices', [])
                from value_semantics import requested_labels
                exact_labels = requested_labels(candidate,self.requirements[rid])
                if len(exact_labels)==1 and str(value)!=str(exact_labels[0]['value']):
                    self.soft_reject('S1', rid, cid, candidate, value, 'Requested source label '+str(exact_labels[0]['label'])+' is encoded as '+
                                     str(exact_labels[0]['value'])+', not '+str(value), selection_repairs)
                lexical_choice = self.text_choice(candidate,self.requirements[rid])
                if not patch and lexical_choice is not None and str(value) != str(lexical_choice):
                    raw_choice_values = {str(c.get('value')).lower() for c in choices}
                    boolean_choices = (len(choices) == 2 and
                                       raw_choice_values <= {'0', '1', 'true', 'false'})
                    boolean_action = re.search(
                        r'\b(?:enable|enabled|disable|disabled|on|off|include|included|exclude|excluded|'
                        r'use|used|unused|generate|expose)\b', self.requirements[rid], re.I)
                    boolean_field = re.search(
                        r'(?:^|[_ .])(?:en|enable|enabled|use|include|present|all[_ ](?:inputs|outputs))(?:$|[_ .])',
                        ' '.join(str(candidate.get(k, '')) for k in ['subkey', 'label', 'key']).lower())
                    s2 = boolean_choices and boolean_action and boolean_field
                    if s2:
                        self.soft_reject('S2', rid, cid, candidate, value, 'The requested Boolean action is encoded as '+str(lexical_choice)+
                                         ', not '+str(value)+'. Preserve the enabled/disabled intent.', selection_repairs)
                    raw_tokens = lambda v: set(re.findall(r'[a-z0-9]+',str(v).lower().replace('_',' ')))
                    without_identifiers = re.sub(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b',
                                                 ' ', self.requirements[rid])
                    wanted = raw_tokens(without_identifiers)
                    tokens = lambda v: raw_tokens(v) | {word for token in raw_tokens(v) for word in wanted
                                                        if len(word) >= 3 and word != token and word in token}
                    distinguishing = (tokens(lexical_choice) - tokens(value)) & wanted
                    if distinguishing and not s2:
                        self.soft_reject('S3', rid, cid, candidate, value, 'The source enum '+str(lexical_choice)+' uniquely includes the distinguishing task terms '+
                                         ','.join(sorted(distinguishing))+'; '+str(value)+' omits them. Select the complete matching encoding.',
                                         selection_repairs)
                from intent_contract import facts, fact_identity
                requested_facts = {fact_identity(f) for f in facts(self.requirements[rid])}
                semantic = [(c['value'], c.get('semantics_source')) for c in choices
                            if any(fact_identity(f) in requested_facts for label in c.get('semantic_labels',[])
                                   for f in facts(label))]
                if len(semantic) == 1 and str(value) != str(semantic[0][0]):
                    self.soft_reject('S4', rid, cid, candidate, value, 'Documented source meaning for '+self.requirements[rid]+' uses '+str(semantic[0][0])+
                                     ', not '+str(value)+'; source='+str(semantic[0][1]), selection_repairs)
                if (not patch and choices and not candidate.get('choices_more') and not any(c.get('truncated') for c in choices)
                        and not candidate.get('choices_scope', '').startswith('sibling_definition')):
                    accepted = [c['value'] for c in choices]
                    checked_value = str(int(value) if isinstance(value, bool) else value)
                    if candidate.get('type', '').lower() in ['bool', 'boolean']:
                        accepted = [boolean_value(v) for v in accepted]
                    if checked_value not in accepted:
                        raise ValueError(rid + ' (' + self.requirements[rid] + '): ' + candidate['key'] + ' does not accept ' + str(value) + '; choices=' + str([c['value'] for c in choices]) + '. Preserve requested values; choose the matching parameter.')
                if (not patch and candidate.get('choices_scope') == 'current_configuration_validation_error'
                        and any(str(choice.get('value')) == str(value) for choice in choices)):
                    source_tokens = set(re.findall(r'[a-z0-9]+', str(value).lower()))
                    encoded = [fact['text'] for fact in facts(self.requirements[rid])
                               if fact['unit'] in ['literal', 'identifier']
                               and set(re.findall(r'[a-z0-9]+', fact['text'].lower())) <= source_tokens]
                    if encoded:
                        item = dict(item, covers=list(dict.fromkeys(encoded)),
                                    grounding='Vivado current-configuration validation listed '+str(value)+
                                              ' as a legal source enum encoding.')
                if not candidate.get('subkey') and not patch and ',' in candidate.get('default', '') and not candidate.get('default_truncated'):
                    base = await self.vector_base(candidate['key'])
                    raise ValueError('Preserve live vector length ' + str(len(base.split(','))) + ' and unrelated positions; use value={"indices":{"0":value}} for requested positions instead of a complete string')
                for (repair_rid, key, subkey), failure in self.unretained_repairs.items():
                    if repair_rid != rid:
                        continue
                    # The same source enum already implements this literal.
                    # Carry its established coverage into a recovery even if
                    # the model omits the optional annotation when copying it.
                    if cid == failure['owner'] and str(value) == str(failure['owner_value']):
                        replacement = next(x for x in failure['repair']['replacement_selections']
                                           if x['candidate_id'] == cid)
                        item = dict(item, covers=replacement['covers'], grounding=replacement['grounding'])
                    if candidate['key'] == key and candidate.get('subkey') == subkey and str(value) == str(failure['value']):
                        owner_value = next((x.get('value') for x in selections
                                            if isinstance(x, dict) and x.get('requirement_id') == rid
                                            and x.get('candidate_id') == failure['owner']),
                                           updated.get((rid, failure['owner']), {}).get('value'))
                        if str(owner_value) == str(failure['owner_value']):
                            raise ValueError('Native readback did not retain '+subkey+' with this owning source enum. '
                                             'Do not reselect the unretained provisional field. Select the complete replacement_selections, then apply: '+
                                             json.dumps(failure['repair']['replacement_selections']))
                updated[identity] = dict(candidate_id=cid, value=value)
                if item.get('mode') == 'assert' or candidate.get('write_mode') == 'assert':
                    updated[identity]['mode'] = 'assert'
                if patch:
                    updated[identity]['indices'] = {str(int(i)): v for i, v in indices.items()}
                if 'covers' in item:
                    from intent_contract import facts, normalized, fact_identity
                    allowed = {fact_identity(f) for f in facts(self.requirements[rid])}
                    covers, grounding = item['covers'], item.get('grounding', '')
                    if not isinstance(covers, list) or not 1 <= len(covers) <= 8 or any(not isinstance(v, str) or len(facts(v)) != 1 or fact_identity(facts(v)[0]) not in allowed for v in covers):
                        raise ValueError('covers must list exact explicit values from this requirement')
                    if not isinstance(grounding, str) or not 1 <= len(grounding.strip()) <= 1024:
                        raise ValueError('A semantic encoding needs source/document grounding explaining covers')
                    for covered in covers:
                        direct = [c['value'] for c in candidate.get('choices', [])
                                  if normalized(covered) in {normalized(c['value']), normalized(c.get('label', ''))}]
                        if direct and str(value) not in [str(v) for v in direct]:
                            raise ValueError('Source choices directly encode ' + covered + ' as ' + str(direct) + '; select that encoding')
                    updated[identity].update(covers=covers, grounding=grounding)
            # A drive/receive verb names signal direction.  If source metadata
            # offers exactly one enabled Boolean direction field, retain that
            # grounded control even when the model picked a nearby reset/default
            # value instead.  A disabled field is never added this way.  Default-value fields are unrelated unless the
            # requirement explicitly asks for a default.
            for rid in {item.get('requirement_id') for item in selections if isinstance(item, dict)}:
                if rid not in self.requirements:
                    continue
                text = self.requirements[rid]
                direction_word = self.requested_direction(text)
                if direction_word is None:
                    continue
                matches = []
                for cid, row in self.candidates.items():
                    if row.get('requirement_id') != rid:
                        continue
                    choice = self.text_choice(row, text)
                    direction_field = self.is_direction_field(row, direction_word)
                    if direction_field and choice is None:
                        truthy = [c.get('value') for c in row.get('choices', [])
                                  if str(c.get('value', '')).lower() in ['1', 'true'] or
                                  str(c.get('label', '')).lower() in ['1', 'true', 'yes', 'enabled']]
                        if len(truthy) == 1:
                            choice = truthy[0]
                    if choice is not None and direction_field:
                        matches.append((cid, choice))
                channel = re.search(r'\bchannel\s+(\d+)\b', text, re.I)
                if channel:
                    wanted_index = int(channel[1])
                    def direction_index(match):
                        row = self.candidates[match[0]]
                        field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
                        suffix = re.search(r'(?:ALL[_ ](?:INPUTS|OUTPUTS)|(?:INPUT|OUTPUT)[_ ]DIRECTION)_(\d+)(?:\b|_)', field, re.I)
                        return int(suffix[1]) if suffix else 1
                    indexed = [match for match in matches if direction_index(match) == wanted_index]
                    if indexed:
                        matches = indexed
                else:
                    enabled_matches = [match for match in matches
                                       if self.candidates[match[0]].get('enabled') is not False]
                    # A disabled field is usable when the selections already switch on
                    # one of its source enablement controls (e.g. dual channel = 1).
                    switched_on = {self.candidates[i[1]]['key'] for i, s in updated.items()
                                   if i[1] in self.candidates and
                                   str(s.get('value')).strip().lower() in ['1', 'true']}
                    if not enabled_matches:
                        enabled_matches = [match for match in matches
                                           if switched_on & set(self.candidates[match[0]].get('enablement_controls', []))]
                    if not enabled_matches and matches:
                        # Every matching direction field is disabled now, so writing it
                        # would also switch on a setting the requirement does not name
                        # (for example a second channel).  Report it; the model decides.
                        for cid, choice in matches:
                            selection_repairs.append(dict(
                                candidate_id=cid, repair='not_added_disabled_direction_control', value=choice,
                                note='Only a disabled direction field matched. It was not added because enabling it '
                                     'needs another setting the requirement does not ask for. Prefer an enabled '
                                     'field with the same label (describe again with its label words); select this '
                                     'one only if the task asks for that channel or group.'))
                        continue
                    matches = enabled_matches
                if len(matches) != 1:
                    continue
                cid, choice = matches[0]
                if not re.search(r'\bdefault\b', text, re.I):
                    for identity in list(updated):
                        if identity[0] != rid or identity[1] == cid:
                            continue
                        row = self.candidates[identity[1]]
                        field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
                        if re.search(r'\bdefault\b|(?:^|_)DOUT_DEFAULT(?:_|$)', field, re.I):
                            removed = updated.pop(identity)
                            selection_repairs.append(dict(candidate_id=identity[1],
                                                          repair='dropped_unrequested_default_for_direction',
                                                          value=removed['value']))
                if (rid, cid) not in updated:
                    updated[(rid, cid)] = dict(candidate_id=cid, value=choice)
                    selection_repairs.append(dict(candidate_id=cid,
                                                  repair='added_unique_direction_control',
                                                  value=choice))
                target = self.candidates[cid]
                controls = set(target.get('enablement_controls', []))
                for identity in list(updated):
                    if identity[0] != rid or identity[1] == cid:
                        continue
                    other = self.candidates[identity[1]]
                    choices = {str(c.get('value')).lower() for c in other.get('choices', [])}
                    boolean = len(choices) == 2 and choices <= {'0', '1', 'true', 'false'}
                    if boolean and other['key'] not in controls:
                        removed = updated.pop(identity)
                        selection_repairs.append(dict(candidate_id=identity[1],
                            repair='dropped_unrelated_boolean_for_unique_direction',
                            value=removed['value']))
            # A requirement such as ``channel 2 width 4`` has two numeric
            # facts: the channel ordinal and the requested width. If the model
            # selects only a nearby Boolean control, recover the unique returned
            # width field whose source suffix matches that ordinal. Unnumbered
            # members are the first member of a source-defined numbered family.
            for rid, text in self.requirements.items():
                if not re.search(r'\b(?:width|\d+\s*-?\s*bits?)\b', text, re.I):
                    continue
                width_value = None
                match = re.search(r'\bwidth\s*(?:of|=|:)?\s*(\d+)\b', text, re.I)
                if not match:
                    match = re.search(r'\b(\d+)\s*-?\s*bits?\b', text, re.I)
                if match:
                    width_value = int(match[1])
                if width_value is None:
                    continue
                ordinal_match = re.search(r'\b(?:channel|port|lane|interface)\s*(\d+)\b', text, re.I)
                ordinal = int(ordinal_match[1]) if ordinal_match else None
                matches = []
                for cid, row in self.candidates.items():
                    if row.get('requirement_id') != rid or row.get('enabled') is False:
                        continue
                    field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
                    if not re.search(r'\bwidth\b|(?:^|_)WIDTH(?:_|$)', field, re.I):
                        continue
                    key = str(row.get('subkey') or row.get('key', '')).upper()
                    before = re.search(r'(\d+)(?=_WIDTH(?:_|$))', key)
                    after = re.search(r'(?:^|_)WIDTH_(\d+)(?:_|$)', key)
                    field_ordinal = int((before or after)[1]) if before or after else 1
                    if ordinal is None or field_ordinal == ordinal:
                        matches.append((cid, row))
                if len(matches) != 1:
                    continue
                cid, row = matches[0]
                identity = (rid, cid)
                if identity not in updated:
                    updated[identity] = dict(candidate_id=cid, value=width_value)
                    selection_repairs.append(dict(candidate_id=cid,
                        repair='added_unique_ordinal_width', value=width_value,
                        **({'ordinal': ordinal} if ordinal is not None else {})))

            # If one selected width field carries the requirement's explicit
            # number, unrelated Boolean siblings cannot be alternate width
            # encodings. This prevents an instance index from being mistaken
            # for a requested scalar width.
            for rid, text in self.requirements.items():
                if not re.search(r'\b(?:width|\d+\s*-?\s*bits?)\b', text, re.I):
                    continue
                width = []
                for identity, item in updated.items():
                    if identity[0] != rid:
                        continue
                    row = self.candidates[identity[1]]
                    field = ' '.join(str(row.get(k, '')) for k in ['subkey', 'label', 'key'])
                    if re.search(r'\bwidth\b|(?:^|_)WIDTH(?:_|$)', field, re.I) and self.value_named_in_text(row,item['value'],text):
                        width.append(identity)
                if len(width) != 1:
                    continue
                controls = set(self.candidates[width[0][1]].get('enablement_controls', []))
                # A Boolean the requirement names is a second request, not an
                # alternate width encoding. ced-gen_H7 asked for "scatter-gather
                # enabled with 26-bit buffer length" and lost c_include_sg
                # ("Enable Scatter Gather Engine") to this rule, silently. Numbers
                # and ordinal nouns do not count as naming, so "channel 1 width 8"
                # still drops "Enable Dual Channel".
                plain = {'a', 'an', 'the', 'on', 'off', 'enable', 'enabled', 'disable', 'disabled', 'include', 'included',
                         'use', 'using', 'with', 'and', 'for', 'to', 'of', 'is', 'c', 'config', 'width', 'bit', 'bits',
                         'channel', 'port', 'lane', 'interface'}
                named = lambda value: {w for w in re.findall(r'[a-z]+', str(value).lower().replace('_', ' ')) if w not in plain}
                asked = named(text)
                for identity in list(updated):
                    if identity[0] != rid or identity == width[0]:
                        continue
                    row = self.candidates[identity[1]]
                    choices = {str(c.get('value')).lower() for c in row.get('choices', [])}
                    if len(named(' '.join(str(row.get(k, '')) for k in ['label', 'key', 'subkey'])) & asked) >= 2:
                        continue
                    if len(choices) == 2 and choices <= {'0', '1', 'true', 'false'} and row['key'] not in controls:
                        removed = updated.pop(identity)
                        selection_repairs.append(dict(candidate_id=identity[1],
                            repair='dropped_unrelated_boolean_for_exact_width', value=removed['value']))
            # A broad "feature on" request may map to more than one native
            # Boolean control.  If the model selects one that is already on,
            # complete a sibling only when both source identities contain the
            # same full multiword feature phrase from the requirement.  This
            # keeps qualifiers such as controller/PHY discriminative and does
            # not infer relationships from a single generic word.
            stop={'a','an','the','on','off','enable','enabled','disable','disabled',
                  'include','included','use','using','with','and','for','to'}
            tokens=lambda value: {w[:-1] if len(w)>4 and w.endswith('s') else w
                                  for w in re.findall(r'[a-z0-9]+',str(value).lower())
                                  if w not in stop}
            for rid in {item.get('requirement_id') for item in selections if isinstance(item, dict)}:
                if rid not in self.requirements or not re.search(r'\b(?:on|enable[ds]?|include[ds]?)\b',self.requirements[rid],re.I):
                    continue
                wanted=tokens(self.requirements[rid])
                additions=[]
                for (selected_rid,selected_cid),selected in list(updated.items()):
                    if selected_rid!=rid:
                        continue
                    row=self.candidates[selected_cid]
                    if row.get('type','').lower() not in ['bool','boolean'] or str(selected['value']).lower() not in ['1','true']:
                        continue
                    if row.get('current') is None or not self.same_value(row,selected['value']):
                        continue
                    source=' '.join(str(row.get(k,'')) for k in ['subkey','key','label','help'])
                    phrase=wanted & tokens(source)
                    if len(phrase)<2:
                        continue
                    matches=[]
                    for cid,other in self.candidates.items():
                        if other.get('requirement_id')!=rid or cid==selected_cid or (rid,cid) in updated:
                            continue
                        if other.get('enabled') is False or other.get('type','').lower() not in ['bool','boolean']:
                            continue
                        other_source=' '.join(str(other.get(k,'')) for k in ['subkey','key','label','help'])
                        if phrase <= tokens(other_source) and other.get('current') is not None and not self.same_value(other,True):
                            matches.append(cid)
                    if len(matches)==1:
                        additions.append((matches[0],selected_cid,sorted(phrase)))
                for cid,because,phrase in additions:
                    if (rid,cid) in updated:
                        continue
                    updated[(rid,cid)]=dict(candidate_id=cid,value=True)
                    selection_repairs.append(dict(candidate_id=cid,
                        repair='added_shared_boolean_feature',value=True,because=because,
                        matched_feature_terms=phrase))
            # A size with an explicit storage unit is incomplete without its
            # same-field-family scale. Add it only when both the companion
            # field and its published unit enum are unique.
            companions = []
            for (rid, cid), selected in list(updated.items()):
                row = self.candidates[cid]
                subkey = row.get('subkey', '')
                if not re.search(r'_SIZE$', subkey, re.I):
                    continue
                stem = re.sub(r'_SIZE$', '', subkey, flags=re.I)
                candidates = [(other_cid, other) for other_cid, other in self.candidates.items()
                              if other.get('requirement_id') == rid and other.get('key') == row.get('key')
                              and other.get('subkey', '').upper() == (stem + '_SCALE').upper()]
                if len(candidates) != 1:
                    continue
                other_cid, other = candidates[0]
                scale = self.unit_scale_choice(other, self.requirements[rid])
                if scale is None or (rid, other_cid) in updated:
                    continue
                updated[(rid, other_cid)] = dict(candidate_id=other_cid, value=scale)
                companions.append(dict(candidate_id=other_cid, repair='added_unique_unit_scale', value=scale,
                                       because=cid))
            selection_repairs.extend(companions)
            # Exact NAME=value ledger assignments already identify both the
            # source field and scalar. Retain the one enabled returned match
            # even if a small model omits it from a later batch.
            for rid,text in self.requirements.items():
                if rid in self.waived or any(selected_rid==rid for selected_rid,_ in updated):
                    continue
                exact=[]
                for cid,row in self.candidates.items():
                    if row.get('requirement_id')!=rid or row.get('enabled') is False:
                        continue
                    choice=self.explicit_setting_choice(row,text)
                    if choice is not None:
                        exact.append((cid,row,choice))
                if len(exact)!=1:
                    continue
                cid,row,choice=exact[0]
                selected=dict(candidate_id=cid,value=choice)
                if row.get('current') is not None and self.same_value(row,choice):
                    selected['mode']='assert'
                updated[(rid,cid)]=selected
                selection_repairs.append(dict(candidate_id=cid,
                    repair='added_unique_explicit_setting',value=choice))
            await self.merge_selections(updated)
            repeated = updated == self.selections
            self.repeated_selects = self.repeated_selects + 1 if repeated else 0
            if self.repeated_selects >= 3:
                upcoming = self.open_pending()
                raise ValueError('Repeated identical select; these selections are already recorded. Next: ' +
                                 ('describe ' + upcoming[0]['id'] if upcoming else 'apply'))
            dropped = [dict(requirement_id=rid, candidate_id=cid, key=self.candidates[cid]['key'],
                            **({'subkey': self.candidates[cid]['subkey']} if self.candidates[cid].get('subkey') else {}), value=item['value'])
                       for (rid, cid), item in self.selections.items() if (rid, cid) not in updated]
            self.selections = updated
            for rid, _ in updated:
                self.waived.pop(rid, None)
            self.stage = 'ready' if not self.pending() else 'discovering'
            if repeated and self.exhausted and self.selections:
                unresolved = self.pending()
                self.allow_partial_apply = True
                try:
                    result = await self.request({'op': 'apply'})
                finally:
                    self.allow_partial_apply = False
                result.update(partial_apply_trigger='repeated_selection_after_discovery_exhaustion',
                              unresolved_requirements=unresolved,
                              partial_note='Grounded selections were applied before partial finish; unresolved requirements remain unclaimed.')
                return result
            disabled = [dict(requirement_id=rid, candidate_id=cid, key=self.candidates[cid]['key'])
                        for rid, cid in updated if self.candidates[cid].get('enabled') is False]
            extra = dict(disabled_selections=disabled, disabled_note='Disabled in the current configuration; Vivado rejects the write unless a selected controlling setting enables it. Prefer an enabled candidate in the requested group.') if disabled else {}
            if repeated:
                upcoming = self.open_pending()
                extra.update(repeated_select=True, repeated_note='Already recorded; nothing changed. Do not repeat this select. Next: ' +
                             ('describe ' + upcoming[0]['id'] + ' (' + upcoming[0]['text'] + ')' if upcoming else 'apply'))
            if dropped:
                unretained = [d for d in dropped if (d['requirement_id'], d['key'], d.get('subkey')) in self.unretained_repairs]
                extra.update(replaced_selections=dropped, replaced_note='After a failed apply, selecting a requirement replaces its whole earlier mapping, so these were removed. '
                             'If they should stay (for example you only added an enabling setting), select them again together with the new ones.')
                if unretained:
                    extra['replaced_note'] = ('The unretained provisional fields were removed in favor of their owning source encodings. '
                                              'Keep this replacement and apply; do not reselect the removed fields.')
            batch_rids = {item.get('requirement_id') for item in selections}
            mismatched = [m for m in self.named_value_mismatches(updated) if m['requirement_id'] in batch_rids]
            if mismatched:
                extra.update(value_mismatches=mismatched, value_note='The requirement text names a number that none of its numeric selections uses. '
                             'If the text asks for that number, select it (select the requirement again to replace the value).')
            siblings = self.same_quantity_unselected(updated, batch_rids)
            if siblings:
                extra.update(same_quantity_unselected=siblings, same_quantity_note='Another setting offered for this requirement names the same quantity and still holds a different value. '
                             'If the task\'s value applies to it as well, select it too (in the same select as the first); if it is a different thing, leave it.')
            unchanged = self.unchanged_selections(updated, batch_rids)
            if unchanged:
                extra.update(unchanged_selections=unchanged, unchanged_note='These selections equal the current value, so they change nothing. If the requirement asks to turn on or change something, check the listed alternatives returned for it; select them too if they match.')
            if selection_repairs:
                extra.update(request_repaired=True, selection_repairs=selection_repairs)
            return self.state(selected=[dict(requirement_id=rid, key=self.candidates[cid]['key'], **({'subkey':self.candidates[cid]['subkey']} if self.candidates[cid].get('subkey') else {}), value=item['value']) for (rid, cid), item in updated.items()],
                              original_task_review=self.task if not self.pending() else None, **extra)
        if op == 'apply':
            auto_completed = []
            for pending in self.open_pending():
                rid = pending['id']
                matches = []
                for cid, row in self.candidates.items():
                    if row.get('requirement_id') != rid or row.get('enabled') is False:
                        continue
                    explicit_choice=self.explicit_setting_choice(row,self.requirements[rid])
                    choice = explicit_choice or self.text_choice(row, self.requirements[rid])
                    if choice is None or (explicit_choice is None and row.get('current') is not None
                                          and self.same_value(row, choice)):
                        continue
                    matches.append((cid, choice, explicit_choice is not None))
                if len(matches) == 1:
                    cid, choice, explicit_match = matches[0]
                    item=dict(candidate_id=cid, value=choice)
                    if (explicit_match and self.candidates[cid].get('current') is not None
                            and self.same_value(self.candidates[cid],choice)):
                        item['mode']='assert'
                    self.selections[(rid, cid)] = item
                    auto_completed.append(dict(requirement_id=rid, candidate_id=cid, value=choice,
                                               scope='unique_enabled_returned_text_choice'))
            if (self.open_pending() and not self.allow_partial_apply) or not (self.selections or self.native_assertions):
                raise ValueError('Unresolved requirements; describe and select before apply')
            gaps = self.coverage_findings()
            if gaps and not self.contract_reviewed:
                self.contract_reviewed = True
                return self.state(apply_paused=True, coverage_gaps=gaps, next_op='describe',
                                  note='Nothing was written. These explicit values lack a selected source encoding. '
                                       'Describe each missing setting separately, keeping existing selections. '
                                       'For an already selected semantic encoding, select its candidate again with covers listing the exact missing value and grounding explaining the source/document mapping. '
                                       'A size needs both its numeric value and scale. '
                                       'Apply again only to save a partial result if they remain unresolved.')
            if not self.unchanged_reviewed:
                self.unchanged_reviewed = True
                asserted = {rid for (rid, _), item in self.selections.items() if item.get('mode') == 'assert'}
                unchanged = [u for u in self.unchanged_selections(self.selections, {rid for rid, _ in self.selections})
                             if u['alternatives'] and u['requirement_id'] not in asserted]
                mismatched = self.named_value_mismatches(self.selections)
                if mismatched and not unchanged:
                    reply = self.state(apply_paused=True, value_mismatches=mismatched,
                                       note='Nothing was written. Each listed requirement names a number that none of its selected values uses. '
                                            'Select the requirement again with the number its text names, or execute apply again unchanged if the listed values are right.')
                    reply['next_op'] = 'select'
                    return reply
                if unchanged:
                    reply = self.state(apply_paused=True, unchanged_selections=unchanged, **({'value_mismatches': mismatched} if mismatched else {}),
                                       note='Nothing was written. Each listed requirement is selected only on values it already has, so it changes nothing. '
                                            'A request to turn on, enable or change something usually names a setting that is not yet in that state: '
                                            'compare the listed alternatives (currently off or different) with the requirement words and select the one it names, keeping the existing selection. '
                                            'Execute apply again unchanged only if no alternative fits.')
                    reply['next_op'] = 'select'
                    return reply
            if self.apply_attempts >= 2:
                return await self.bounded_finish(
                    'Two native apply attempts were used; saved the realized configuration',
                    apply_retry_exhausted=True)
            container_repairs = self.coalesce_nested_containers()
            await self.enrich_live([self.candidates[cid] for rid, cid in self.selections])
            config, ledger, ledger_keys = await self.merge_selections(self.selections), [], []
            dependency_repairs = await self.resolve_disabled_dependencies(config)
            dependency_repairs += self.port_presence_repairs(config)
            dependency_repairs += self.stick_repairs(config)
            propagated_equivalences = self.propagated_equivalences()
            for (rid, cid), item in self.selections.items():
                text = self.requirements[rid]; row = self.candidates[cid]
                entry = dict(id=rid+'.'+cid, text=text, key=row['key'], wanted=item['value'], doc=row['evidence'])
                if item.get('mode') == 'assert' or row.get('write_mode') == 'assert':
                    entry['mode'] = 'assert'
                for field in ['subkey', 'native_key', 'mapping_scope', 'source_vlnv']:
                    if row.get(field):
                        entry[field] = row[field]
                if row.get('enablement_controls'):
                    entry['prerequisites'] = tcl_list_string(row['enablement_controls'])
                if (rid, cid) in propagated_equivalences:
                    alternate = propagated_equivalences[(rid, cid)]
                    entry.update(equivalent_key=alternate['key'],
                                 equivalent_subkey=alternate['subkey'],
                                 equivalent_wanted=alternate['wanted'],
                                 equivalence_scope=alternate['scope'])
                if item.get('covers'):
                    # Only explicit source-enum tokens can discharge a
                    # previously pinned literal value during a remap.
                    literal = [v for v in item['covers'] if re.search(r'(?:^|[^A-Za-z0-9])'+re.escape(v)+r'(?:$|[^A-Za-z0-9])', str(item['value']), re.I)]
                    if literal:entry['covered_values']=tcl_list_string(literal)
                    entry['grounding']=item['grounding']
                ledger.append(tdict(entry)); ledger_keys.append(row['key'])
            for rid, assertion in self.native_assertions.items():
                ledger.append(tdict(dict(id=rid + '.native_cell_name',
                                         text=self.requirements[rid],
                                         key=assertion['key'],
                                         wanted=assertion['wanted'],
                                         doc=assertion['scope'], mode='assert')))
                ledger_keys.append(assertion['key'])
            self.derived_enables = await self.enable_companions(config)
            for d in {d['key']: d for d in self.derived_enables}.values():
                ledger.append(tdict(dict(id=d['requirement_id'] + '.enable.' + d['key'][7:], text=self.requirements[d['requirement_id']],
                                         key=d['key'], wanted=config[d['key']], doc='derived_enable_for_' + d['because'])))
                ledger_keys.append(d['key'])
            self.apply_attempts += 1
            encoded_config = '[dict create ' + ' '.join(atom(k) + ' ' + (tdict(v) if isinstance(v, dict) else atom(v)) for k,v in config.items()) + ']'
            full_ledger = '[list ' + ' '.join(ledger) + ']'
            expression = '[lite::configure ' + atom(self.vlnv) + ' ' + atom(self.cell) + ' ' + encoded_config + ' ' + full_ledger + ']'
            try:
                reply = await self.native(expression)
            except Exception as exc:
                if 'REQUIREMENTS_FAIL:immutable intent' not in str(exc):
                    raise
                self.apply_attempts -= 1
                raise ValueError(str(exc).split('; evidence=')[0] + '. Nothing was written. Select again so every requirement keeps the values its text names, then apply.')
            enum_repairs = []
            if not reply.startswith('CFG:OK') and not self.native_enum_retry_used:
                enum_repairs = self.repair_unique_native_enums(reply)
                if enum_repairs:
                    self.native_enum_retry_used = True
                    self.apply_attempts -= 1
                    retried = await self.request({'op': 'apply'})
                    retried['native_enum_repairs'] = enum_repairs
                    return retried
            rejected = self.rejected_keys(reply, config) if not reply.startswith('CFG:OK') else []
            kept = {k: v for k, v in config.items() if k not in rejected}
            withheld = None
            if rejected and kept:
                # One invalid parameter aborts the whole write. Apply the legal
                # topology first. A batch can expose another independently
                # invalid container only after the first rejected scalar is
                # removed, so peel at most three native-confirmed layers.
                first = reply
                recovery_rounds = []
                for _ in range(3):
                    encoded_config = '[dict create ' + ' '.join(atom(k) + ' ' + (tdict(v) if isinstance(v, dict) else atom(v)) for k,v in kept.items()) + ']'
                    kept_ledger = [entry for key, entry in zip(ledger_keys, ledger) if key in kept]
                    reply = await self.native('[lite::configure_subset ' + atom(self.vlnv) + ' ' + atom(self.cell) + ' ' +
                                              encoded_config + ' ' + full_ledger + ' [list ' + ' '.join(kept_ledger) + ']]')
                    recovery_rounds.append(dict(keys=list(kept), status='passed' if reply.startswith('CFG:OK') else 'rejected'))
                    if reply.startswith('CFG:OK'):
                        break
                    newly_rejected = [key for key in self.rejected_keys(reply, kept) if key not in rejected]
                    if not newly_rejected:
                        break
                    rejected += newly_rejected
                    kept = {k: v for k, v in config.items() if k not in rejected}
                    if not kept:
                        break
                dynamic_retry = None
                partial_checkpoint = None
                if reply.startswith('CFG:OK'):
                    partial_checkpoint = (await self.native('[lite::partial_save]'))[:300]
                    deferred_keys = list(rejected)
                    deferred = {k: config[k] for k in rejected}
                    await self.enrich_live([self.candidates[cid] for rid, cid in self.selections
                                            if self.candidates[cid]['key'] in deferred])
                    deferred_config = '[dict create ' + ' '.join(
                        atom(k) + ' ' + (tdict(v) if isinstance(v, dict) else atom(v))
                        for k, v in deferred.items()) + ']'
                    deferred_ledger = [entry for key, entry in zip(ledger_keys, ledger) if key in deferred]
                    retried = await self.native('[lite::configure_subset ' + atom(self.vlnv) + ' ' + atom(self.cell) + ' ' +
                                                deferred_config + ' ' + full_ledger + ' [list ' + ' '.join(deferred_ledger) + ']]')
                    if retried.startswith('CFG:OK'):
                        reply = await self.native('[lite::verify_pinned ' + atom(self.cell) + ' ' + full_ledger + ']')
                        if reply.startswith('CFG:OK'):
                            rejected = []
                    else:
                        reply = retried
                    dynamic_retry = dict(keys=deferred_keys, live_ranges_refreshed=True,
                                         status='passed' if not rejected else 'rejected')
            if rejected and kept and reply.startswith('CFG:STOP'):
                reply = first
            elif rejected and kept:
                withheld = dict(withheld_keys=rejected, withheld_note='Vivado rejected these parameters, which aborted the whole write, so they were left out and the other selections were written. '
                                'They remained illegal after the legal subset was applied and live ranges were refreshed; see rejected_fields.')
                findings = self.validation_findings(reply)
            self.stage = 'configured' if reply.startswith('CFG:OK') else 'apply_failed'
            if self.stage == 'configured':
                self.successful_apply = True
            result = dict(stage=self.stage, native_receipt=reply[:1100], next_op='finish', pinned_requirements=len(ledger))
            if auto_completed:
                result['auto_completed_selections'] = auto_completed
            if self.pending():
                result['unapplied_requirements'] = self.pending()
            if self.derived_enables:
                result['derived_enables'] = self.derived_enables
            if dependency_repairs:
                result['dependency_repairs'] = dependency_repairs
            if propagated_equivalences:
                result['propagated_equivalences'] = [dict(requirement_id=rid,
                                                          candidate_id=cid, **alternate)
                                                     for (rid, cid), alternate in propagated_equivalences.items()]
            if container_repairs:
                result['container_repairs'] = container_repairs
            if rejected is not None and 'dynamic_retry' in locals() and dynamic_retry:
                result['dynamic_range_retry'] = dynamic_retry
            if rejected is not None and 'recovery_rounds' in locals() and recovery_rounds:
                result['legal_subset_recovery'] = recovery_rounds
            if 'partial_checkpoint' in locals() and partial_checkpoint:
                result['partial_checkpoint'] = partial_checkpoint
            if self.stage == 'apply_failed' and reply.startswith('CFG:FIX') and self.apply_attempts < 2:
                result.update(next_op='select_or_finish', recovery='Select a different returned candidate for each failed requirement (describe again if needed), then apply once more; or finish partial.')
            if self.stage == 'apply_failed':
                result.update(self.validation_findings(reply))
                if reply.startswith('CFG:FIX') and self.apply_attempts < 2:
                    proven = await self.prove_stick_prerequisites(reply)
                    if proven:
                        result['stick_prerequisites'] = proven
                        result['recovery'] = ('Some selected values were accepted but did not stick; a setting that makes '
                                              'each one stick was proven on a throwaway cell (stick_prerequisites) and is '
                                              'included in the next apply. Apply again, or select a different candidate.')
            if withheld:
                result.update(withheld, **findings)
            if self.stage == 'apply_failed' and self.apply_attempts >= 2:
                return await self.bounded_finish(
                    'Second native apply failed; saved the realized configuration',
                    apply_retry_exhausted=True, last_apply=result)
            return result
        if op == 'finish':
            reply = await self.native('[lite::finish ' + atom(self.cell) + ']')
            verified = await self.verified_readback()
            saved = self.saved_configuration(verified)
            literal_rows = self.literal_rows(saved)
            unaccounted = self.unaccounted_literal_texts(literal_rows)
            if unaccounted and not self.literal_reviewed and not self.literal_review_bypass:
                self.literal_reviewed = True
                self.literal_review_initial = list(unaccounted)
                self.literal_review_written = dict(saved.get('as_configured') or {})
                self.stage = 'literal_review'
                return dict(
                    stage='literal_review', unaccounted_literals=unaccounted,
                    next_op='select|finish',
                    instruction='These values from the original task do not appear in the saved configuration. For each one, either select and apply a setting that realizes it, or call finish with "context": {"<literal>": "<why it is not a setting>"}. One review round only.')
            context = request.get('context') if self.literal_reviewed else {}
            context = context if isinstance(context, dict) else {}
            declared = {text: context[text].strip() for text in unaccounted
                        if text in context and isinstance(context[text], str) and context[text].strip()}
            uncovered = [text for text in unaccounted if text not in declared]
            # Saving ends this cell, not the session: the next prompt begins the next cell in the
            # same Vivado. Vivado is stopped only when the user is done (vivado_stop).
            closed, preserved = False, True
            self.stage = 'finished'
            gaps = self.coverage_findings()
            literal_gaps = ['literal not realized: ' + text for text in uncovered]
            gaps = list(gaps) + literal_gaps
            self.final_reply = dict(stage=self.stage, full=(closed or preserved) and reply.startswith('DONE:COVERAGE:FULL') and not self.pending() and not gaps and verified['available'] and all(f['passed'] for f in verified.get('fields', [])), native_receipt=reply[:1100], session_closed=closed,
                        session_preserved=preserved,
                        coverage_scope='pinned_selected_ledger_only', requires_original_task_check=True,
                        next_op='begin', session_note=self.SESSION_OPEN_NOTE)
            self.final_reply['verified_configuration'] = verified
            self.final_reply['saved_result'] = saved
            self.final_reply['declared_context'] = declared
            if self.literal_reviewed:
                now_accounted = {row['text'] for row in literal_rows if row.get('accounted') is not None}
                changed = [key for key, value in self.literal_review_written.items()
                           if key in saved['as_configured'] and str(saved['as_configured'][key]) != str(value)]
                self.final_reply['literal_review_result'] = dict(
                    fixed_by_selection=[text for text in self.literal_review_initial if text in now_accounted],
                    declared_as_context=list(declared), still_uncovered=uncovered,
                    changed_verified_fields=changed)
            self.final_reply['report_rule'] = ('Report only saved_result.as_configured. Set self_fidelity full only when full=true; '
                                               'otherwise report partial or negative.')
            if gaps:
                self.final_reply['coverage_gaps'] = gaps
            if self.pending():
                self.final_reply['unapplied_requirements'] = self.pending()
            if getattr(self, 'part_change', None):
                self.final_reply['part_change'] = self.part_change
            if self.waived:
                self.final_reply['waived_requirements'] = [dict(requirement_id=k, text=self.requirements[k], reason=v) for k, v in self.waived.items()]
            if self.derived_enables:
                self.final_reply['derived_enables'] = self.derived_enables
            if self.advisory_overrides:
                self.final_reply['advisory_overrides'] = self.advisory_overrides
            if self.vector_covered():
                self.final_reply['covered_by_vector'] = list(self.vector_covered().values())
            return self.final_reply
