#!/usr/bin/env python3
"""
Parse Vivado synthesis elaboration messages from log files.

Extracts ERROR, CRITICAL WARNING, WARNING, and INFO messages with
[Synth 8-XXX] pattern from Vivado synthesis logs and outputs a
7-column CSV with header row.

Usage:
    python3 parse_elab_messages.py <log_file> <output_csv>
    python3 parse_elab_messages.py --test

CSV columns (with header row):
    0: S.No         - Sequential serial number (1-based)
    1: severity     - ERROR, CRITICAL WARNING, WARNING, INFO
    2: msg_id       - Integer message ID from [Synth 8-XXX]
    3: text         - Full message text
    4: file         - Source file path (empty if not present)
    5: line         - Source line number (0 if not present)
    6: language     - VLOG or VHDL (detected from file extension)
"""

import re
import sys
import csv
import os

# Pattern to match Vivado elaboration messages
# Examples:
#   ERROR: [Synth 8-128] 'my_signal' is not declared [/path/to/file.v:42]
#   WARNING: [Synth 8-566] inferring latch for variable 'state_reg' [/path/file.v:100]
#   CRITICAL WARNING: [Synth 8-637] variable 'data' cannot be ... [/path/file.v:55]
#   INFO: [Synth 8-400] synthesizing module 'top' [/path/file.v:1]
#   ERROR: [Synth 8-128] 'sig' is not declared [C:\Users\proj\src\top.v:42]
MSG_PATTERN = re.compile(
    r'^(ERROR|CRITICAL WARNING|WARNING|INFO):\s*'
    r'\[Synth\s+8-(\d+)\]\s*'   # [Synth 8-XXX]
    r'(.*?)'                     # message text (non-greedy)
    r'(?:\s*\[([^\]]+?)(?::(\d+))?\])?\s*$'  # optional [file:line] or [file]
)

# Continuation pattern: message wrapped across lines
# Vivado may wrap long messages; continuation lines lack severity prefix
CONTINUATION_PATTERN = re.compile(
    r'^\s+(.+?)'                 # indented continuation
    r'(?:\s*\[([^\]]+?)(?::(\d+))?\])?\s*$'  # optional trailing [file:line]
)

# File extensions for language detection
VHDL_EXTENSIONS = {'.vhd', '.vhdl', '.vho'}
VLOG_EXTENSIONS = {'.v', '.sv', '.vh', '.svh', '.vo'}


def normalize_path(filepath):
    """Normalize file path separators to forward slashes for consistency."""
    if not filepath:
        return filepath
    # Normalize backslashes to forward slashes
    return filepath.replace('\\', '/')


def detect_language(filepath):
    """Detect HDL language from file extension."""
    if not filepath:
        return 'VLOG'  # default
    ext = os.path.splitext(filepath)[1].lower()
    if ext in VHDL_EXTENSIONS:
        return 'VHDL'
    return 'VLOG'


def parse_log(log_path):
    """Parse a Vivado synthesis log file and extract elaboration messages.
    
    Handles:
    - Standard single-line messages
    - Multi-line messages (continuation lines indented under severity line)
    - Windows backslash and Linux forward slash paths
    - Drive-letter paths (C:\\path\\to\\file.v)
    
    Returns list of tuples: (severity, msg_id, text, file, line, language)
    """
    messages = []
    
    # State for multi-line message accumulation
    pending = None  # (severity, msg_id, text_parts, filepath, lineno)
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.rstrip('\n\r')
            
            m = MSG_PATTERN.match(line)
            if m:
                # Flush any pending multi-line message
                if pending:
                    sev, mid, parts, fp, ln = pending
                    text = ' '.join(parts)
                    fp = normalize_path(fp)
                    lang = detect_language(fp)
                    messages.append((sev, mid, text, fp, ln, lang))
                
                severity = m.group(1)
                msg_id = int(m.group(2))
                text = m.group(3).strip()
                filepath = m.group(4) or ''
                lineno = int(m.group(5)) if m.group(5) else 0
                
                if filepath or lineno or text:
                    # Complete single-line message
                    filepath = normalize_path(filepath)
                    language = detect_language(filepath)
                    messages.append((severity, msg_id, text, filepath, lineno, language))
                    pending = None
                else:
                    # Might have continuation on next line
                    pending = (severity, msg_id, [text] if text else [], filepath, lineno)
            elif pending:
                # Check for continuation line
                cm = CONTINUATION_PATTERN.match(line)
                if cm:
                    cont_text = cm.group(1).strip()
                    cont_file = cm.group(2) or ''
                    cont_line = int(cm.group(3)) if cm.group(3) else 0
                    pending[2].append(cont_text)
                    if cont_file:
                        pending = (pending[0], pending[1], pending[2],
                                   cont_file, cont_line)
                else:
                    # Not a continuation — flush pending
                    sev, mid, parts, fp, ln = pending
                    text = ' '.join(parts)
                    fp = normalize_path(fp)
                    lang = detect_language(fp)
                    messages.append((sev, mid, text, fp, ln, lang))
                    pending = None
    
    # Flush any remaining pending message
    if pending:
        sev, mid, parts, fp, ln = pending
        text = ' '.join(parts)
        fp = normalize_path(fp)
        lang = detect_language(fp)
        messages.append((sev, mid, text, fp, ln, lang))
    
    return messages


def write_csv(messages, output_path):
    """Write parsed messages to a 7-column CSV with header and serial numbers.
    
    File paths are stored relative to the output CSV directory so that
    the report (in the same directory) can use them directly in markdown links.
    """
    output_dir = os.path.dirname(os.path.abspath(output_path))
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['S.No', 'Severity', 'Msg_ID', 'Message', 'File', 'Line', 'Language'])
        for i, msg in enumerate(messages, start=1):
            sev, mid, text, filepath, lineno, lang = msg
            if filepath:
                filepath = os.path.relpath(filepath, output_dir)
            writer.writerow([i, sev, mid, text, filepath, lineno, lang])


def run_self_test():
    """Run self-test with sample log lines."""
    test_lines = [
        'ERROR: [Synth 8-128] \'my_signal\' is not declared [/home/user/design.v:42]',
        'WARNING: [Synth 8-566] inferring latch for variable \'state_reg\' [/home/user/fsm.sv:100]',
        'CRITICAL WARNING: [Synth 8-637] variable \'data\' cannot be written by both continuous and procedural assignments [/home/user/top.v:55]',
        'INFO: [Synth 8-400] synthesizing module \'top\' [/home/user/top.v:1]',
        'ERROR: [Synth 8-759] width mismatch in assignment; target has 8 bits, source has 16 bits [/home/user/alu.vhd:45]',
        'WARNING: [Synth 8-758] signal \'sel\' is read in the process but is not in the sensitivity list [/home/user/mux.vhd:80]',
        'ERROR: [Synth 8-402] failed synthesizing module \'bad_module\'',
        'CRITICAL WARNING: [Synth 8-769] null range (7 downto 8) not supported [/home/user/pkg.vhd:20]',
        'WARNING: [Synth 8-564] referenced signal \'enable\' should be on the sensitivity list [/home/user/ctrl.v:200]',
        'ERROR: [Synth 8-731] no port \'clk_in\' on instance [/home/user/wrapper.vhd:30]',
        # Edge cases: Windows backslash paths
        'ERROR: [Synth 8-128] \'win_sig\' is not declared [C:\\Users\\proj\\src\\top.v:10]',
        # Edge cases: Path with spaces
        'WARNING: [Synth 8-566] inferring latch for variable \'q\' [/home/user/my project/fsm.sv:25]',
    ]
    
    expected = [
        ('ERROR',             128, "'my_signal' is not declared",          '/home/user/design.v',  42, 'VLOG'),
        ('WARNING',           566, "inferring latch for variable 'state_reg'", '/home/user/fsm.sv', 100, 'VLOG'),
        ('CRITICAL WARNING',  637, "variable 'data' cannot be written by both continuous and procedural assignments", '/home/user/top.v', 55, 'VLOG'),
        ('INFO',              400, "synthesizing module 'top'",            '/home/user/top.v',      1, 'VLOG'),
        ('ERROR',             759, 'width mismatch in assignment; target has 8 bits, source has 16 bits', '/home/user/alu.vhd', 45, 'VHDL'),
        ('WARNING',           758, "signal 'sel' is read in the process but is not in the sensitivity list", '/home/user/mux.vhd', 80, 'VHDL'),
        ('ERROR',             402, "failed synthesizing module 'bad_module'", '', 0, 'VLOG'),
        ('CRITICAL WARNING',  769, 'null range (7 downto 8) not supported', '/home/user/pkg.vhd', 20, 'VHDL'),
        ('WARNING',           564, "referenced signal 'enable' should be on the sensitivity list", '/home/user/ctrl.v', 200, 'VLOG'),
        ('ERROR',             731, "no port 'clk_in' on instance",         '/home/user/wrapper.vhd', 30, 'VHDL'),
        # Windows path normalized to forward slashes
        ('ERROR',             128, "'win_sig' is not declared",            'C:/Users/proj/src/top.v', 10, 'VLOG'),
        # Path with spaces preserved
        ('WARNING',           566, "inferring latch for variable 'q'",     '/home/user/my project/fsm.sv', 25, 'VLOG'),
    ]
    
    # Write test lines to a temp file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as tmp:
        # Add some non-matching lines to test filtering
        tmp.write('Vivado v2026.1 (64-bit)\n')
        tmp.write('Copyright 1986-2026 Xilinx, Inc.\n')
        tmp.write('\n')
        for line in test_lines:
            tmp.write(line + '\n')
        tmp.write('# Some trailing content\n')
        tmp_path = tmp.name
    
    try:
        messages = parse_log(tmp_path)
        
        passed = 0
        failed = 0
        
        if len(messages) != len(expected):
            print(f'FAIL: Expected {len(expected)} messages, got {len(messages)}')
            failed += 1
        else:
            passed += 1
        
        for i, (got, exp) in enumerate(zip(messages, expected)):
            if got[0] != exp[0]:
                print(f'FAIL [{i}]: severity: got "{got[0]}", expected "{exp[0]}"')
                failed += 1
            elif got[1] != exp[1]:
                print(f'FAIL [{i}]: msg_id: got {got[1]}, expected {exp[1]}')
                failed += 1
            elif got[2] != exp[2]:
                print(f'FAIL [{i}]: text: got "{got[2]}", expected "{exp[2]}"')
                failed += 1
            elif got[3] != exp[3]:
                print(f'FAIL [{i}]: file: got "{got[3]}", expected "{exp[3]}"')
                failed += 1
            elif got[4] != exp[4]:
                print(f'FAIL [{i}]: line: got {got[4]}, expected {exp[4]}')
                failed += 1
            elif got[5] != exp[5]:
                print(f'FAIL [{i}]: language: got "{got[5]}", expected "{exp[5]}"')
                failed += 1
            else:
                passed += 1
        
        total = passed + failed
        print(f'\nSelf-test: {passed}/{total} checks passed')
        if failed > 0:
            print('SOME TESTS FAILED')
            return 1
        else:
            print('ALL TESTS PASSED')
            return 0
    finally:
        os.unlink(tmp_path)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--test':
        sys.exit(run_self_test())
    
    if len(sys.argv) != 3:
        print(f'Usage: {sys.argv[0]} <log_file> <output_csv>')
        print(f'       {sys.argv[0]} --test')
        sys.exit(1)
    
    log_path = sys.argv[1]
    output_path = sys.argv[2]
    
    if not os.path.isfile(log_path):
        print(f'ERROR: Log file not found: {log_path}')
        sys.exit(1)
    
    messages = parse_log(log_path)
    write_csv(messages, output_path)
    
    # Print summary
    severity_counts = {}
    for msg in messages:
        severity_counts[msg[0]] = severity_counts.get(msg[0], 0) + 1
    
    print(f'Parsed {len(messages)} elaboration messages from {log_path}')
    for sev in ['ERROR', 'CRITICAL WARNING', 'WARNING', 'INFO']:
        count = severity_counts.get(sev, 0)
        if count > 0:
            print(f'  {sev}: {count}')
    print(f'Output: {output_path}')


if __name__ == '__main__':
    main()
