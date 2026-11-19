# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/tcl_data.py must read and write Tcl lists exactly as Tcl does.

It replaced subprocess calls to a host tclsh, which Vivado's bundled Python cannot
count on. Values it writes are set on Vivado parameters, so "parses back the same" is
not enough: the text must be byte-identical to Tcl's own `list`. These tests compare
against the real tclsh on a fixed corpus and on random strings built from every
character that matters to Tcl's list syntax, and are skipped when no tclsh exists.
"""
import base64
import random
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from tcl_data import TclListError, join_list, split_list  # noqa: E402

TCLSH = shutil.which('tclsh')
ALPHABET = list('ab #{}[]$;"\\\t\n\r\x0b\x0cxu0') + ['µ', 'é', '–']
CORPUS = ['', 'plain', 'two words', '{', '}', '{}', '{a}', 'a{', 'a}b', '#hash', 'x#', '\\', 'a\\', 'a\\\nb',
          '"quoted"', 'q"x', '[cmd]', '$var', 'semi;colon', 'tab\there', 'line\nbreak', '\\{', '\\}',
          '{unbalanced', 'unbalanced}', '}{', '{{nested} {braces}}', 'CONFIG.PSU__X {a b}', ' lead', 'trail ',
          'µs 9600', '\\u0041', '\\x41', '\\101']


def b64(text):
    return base64.b64encode(text.encode()).decode()


def tcl(script):
    done = subprocess.run([TCLSH], input=script, text=True, capture_output=True, timeout=30)
    if done.returncode or done.stderr:
        raise AssertionError(done.stderr)
    return done.stdout


def tcl_join(lists):
    """What Tcl's `list` writes for each list of values."""
    script = ''.join('puts [binary encode base64 -maxlen 0 [encoding convertto utf-8 [list %s]]]\n' %
                     ' '.join('[encoding convertfrom utf-8 [binary decode base64 {%s}]]' % b64(v) for v in values)
                     for values in lists)
    return [base64.b64decode(line).decode() for line in tcl(script).splitlines()]


def tcl_split(texts):
    """What Tcl reads from each text: its elements, or None where it is not a list."""
    script = ''.join(
        'if {[catch {set l [encoding convertfrom utf-8 [binary decode base64 {%s}]]; llength $l}]} {puts ERR} '
        'else {puts [llength $l]:[join [lmap e $l {binary encode base64 -maxlen 0 [encoding convertto utf-8 $e]}] ,]}\n' % b64(t)
        for t in texts)
    out = []
    for line in tcl(script).splitlines():
        if line == 'ERR':
            out.append(None)
            continue
        count, _, body = line.partition(':')
        out.append([base64.b64decode(p).decode() for p in body.split(',')] if int(count) else [])
    return out


def ours(text):
    try:
        return split_list(text)
    except TclListError:
        return None


@unittest.skipIf(TCLSH is None, 'tclsh is not installed')
class TclParityTests(unittest.TestCase):
    def random_strings(self, count, seed):
        rng = random.Random(seed)
        return [''.join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 12))) for _ in range(count)]

    def test_writing_matches_tcl_list_byte_for_byte(self):
        lists = [[v] for v in CORPUS] + [CORPUS] + [['#a', '#b'], ['a', '#b']]
        rng = random.Random(7)
        strings = self.random_strings(3000, 1)
        lists += [rng.sample(strings, rng.randint(1, 4)) for _ in range(1500)]
        for values, expected in zip(lists, tcl_join(lists)):
            self.assertEqual(join_list(values), expected, values)

    def test_what_we_write_reads_back_to_the_same_values(self):
        for values in [[v] for v in CORPUS + self.random_strings(2000, 2)]:
            self.assertEqual(split_list(join_list(values)), values)

    def test_reading_matches_tcl_including_what_is_not_a_list(self):
        texts = CORPUS + self.random_strings(4000, 3) + ['{a}b', '"a"b', '{a} "b c" d\\ e', '  {x}  ', 'a\\\n   b']
        for text, expected in zip(texts, tcl_split(texts)):
            self.assertEqual(ours(text), expected, repr(text))


class TclDataTests(unittest.TestCase):
    def test_nothing_is_evaluated(self):
        self.assertEqual(split_list('[exit 1] $env(HOME)'), ['[exit', '1]', '$env(HOME)'])

    def test_errors_name_the_problem(self):
        with self.assertRaisesRegex(TclListError, 'unmatched open brace'):
            split_list('{a')
        with self.assertRaisesRegex(TclListError, 'instead of space'):
            split_list('{a}b')


if __name__ == '__main__':
    unittest.main()
