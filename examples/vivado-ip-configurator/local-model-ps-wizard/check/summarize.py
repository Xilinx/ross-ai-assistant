#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Summarize finished trials of this example as one table row each.

Usage: python3 check/summarize.py <trial_dir> [<trial_dir> ...]

Reads each trial's transcript.jsonl (OpenCode's --format json event stream) and check.txt
(written by run_trial.sh) and prints, per trial: the check result, the model's wall time from
its first to its last event, tool calls, tokens, the largest single tool reply, and where the
skill looked IP settings up ("database" on a Vivado release that ships the IP database, "live"
otherwise; "-" when the skill was not used). Standard library only; reads nothing else.
"""
import json
import re
import sys
from pathlib import Path


def summarize(trial):
    """Return one dict of metrics for a trial directory."""
    trial = Path(trial)
    times, calls, biggest, discovery = [], 0, 0, '-'
    tokens = dict(input=0, output=0, cache_read=0)
    for line in (trial / 'transcript.jsonl').read_text(errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event.get('timestamp'), (int, float)):
            times.append(event['timestamp'])
        part = event.get('part') or {}
        if part.get('type') == 'tool':
            calls += 1
            output = str((part.get('state') or {}).get('output') or '')
            biggest = max(biggest, len(output))
            # The skill's begin reply says which source it used.
            match = re.search(r'\\?"discovery\\?"\s*:\s*\\?"(\w+)', output)
            if match:
                discovery = match[1]
        elif part.get('type') == 'step-finish':
            used = part.get('tokens') or {}
            tokens['input'] += used.get('input') or 0
            tokens['output'] += (used.get('output') or 0) + (used.get('reasoning') or 0)
            tokens['cache_read'] += (used.get('cache') or {}).get('read') or 0
    check = (trial / 'check.txt').read_text() if (trial / 'check.txt').exists() else ''
    result = next((l[len('RESULT: '):] for l in check.splitlines() if l.startswith('RESULT: ')), 'not checked')
    misplaced = 'not where requested' in check
    seconds = round((max(times) - min(times)) / 1000) if len(times) > 1 else None
    return dict(trial=trial.name, result=result + (' (project misplaced)' if misplaced else ''),
                seconds=seconds, tool_calls=calls, largest_reply_kb=round(biggest / 1024, 1),
                discovery=discovery, **{'tokens_' + k: v for k, v in tokens.items()})


if __name__ == '__main__':
    rows = [summarize(path) for path in sys.argv[1:]]
    if rows:
        keys = list(rows[0])
        print('| ' + ' | '.join(keys) + ' |')
        print('|' + '---|' * len(keys))
        for row in rows:
            print('| ' + ' | '.join(str(row[k]) for k in keys) + ' |')
