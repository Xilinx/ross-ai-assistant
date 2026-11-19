# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A stand-in for scripts/ced_bridge.py, so lib/ced_op.tcl can be tested without Vivado.

It speaks the helper side of the pipe protocol exactly as the bridge does, one
scenario per run, and writes everything it received to a transcript file so the
test can check what Tcl answered. See tests/unit/test_ced_op_tcl.py.
"""
import base64
import json
import sys


def encode(text):
    return base64.b64encode(text.encode()).decode('ascii')


def decode(text):
    return base64.b64decode(text).decode()


def send(kind, payload):
    sys.stdout.write(kind + ' ' + encode(payload) + '\n')
    sys.stdout.flush()


def receive(transcript):
    line = sys.stdin.readline()
    if not line:
        transcript.append({'closed': True})
        raise SystemExit(0)
    kind, _, body = line.strip().partition(' ')
    transcript.append({'kind': kind, 'body': decode(body) if body else ''})
    return kind, transcript[-1]['body']


def main():
    scenario, record = sys.argv[1], sys.argv[2]
    transcript = []
    try:
        _, op = receive(transcript)
        if scenario == 'done':
            send('DONE', json.dumps({'stage': 'new'}))
        elif scenario == 'echo':
            send('DONE', op)
        elif scenario == 'tcl':
            send('TCL', 'expr {6 * 7}')
            receive(transcript)
            send('DONE', json.dumps({'stage': 'new'}))
        elif scenario == 'tcl_error':
            send('TCL', 'error {boom}')
            receive(transcript)
            send('DONE', json.dumps({'stage': 'new'}))
        elif scenario == 'fail':
            send('FAIL', 'STRUCTURED_ERROR: no good')
        elif scenario == 'bogus':
            send('WAT', 'not a protocol line')
        elif scenario == 'exit':
            pass  # the helper dies without answering
        else:
            raise SystemExit('unknown scenario ' + scenario)
    finally:
        with open(record, 'w') as handle:
            json.dump(transcript, handle)


if __name__ == '__main__':
    main()
