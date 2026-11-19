#!/usr/bin/env python3
"""Fail when a skill's Markdown links outside its own folder to something not published.

GitHub issue #518 (agentic-ai-suite): skills/vivado-ip-configurator/SKILL.md on main linked to
../ipi-assembler/REFERENCE.md and ../ipi-assembler/SKILL.md, but ipi-assembler lived only in
staging/, so the links resolved to skills/ipi-assembler/, which does not exist in the
published package.

A link is checked when it is a relative Markdown link target, e.g. [x](../other/FILE.md#a).
It passes when it resolves inside the skill folder, or into a sibling folder that exists
under the published skills root. Everything else is reported with file and line.

Usage:
  check_skill_links.py SKILL_DIR [--published-root DIR]
    SKILL_DIR         the skill folder (contains SKILL.md)
    --published-root  folder holding the published skills (default: SKILL_DIR's parent)
Exit status: 0 when every link is valid, 1 otherwise.
"""
import argparse
import re
import sys
from pathlib import Path

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def broken_links(skill_dir, published_root):
    skill_dir, published_root = skill_dir.resolve(), published_root.resolve()
    found = []
    for md in sorted(skill_dir.rglob('*.md')):
        for number, line in enumerate(md.read_text(errors='replace').splitlines(), 1):
            for target in LINK.findall(line):
                if re.match(r'^[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('#'):
                    continue          # URL, mailto: or in-page anchor
                path = (md.parent / target.split('#', 1)[0]).resolve()
                if path == skill_dir or skill_dir in path.parents:
                    if not path.exists():
                        found.append((md, number, target, 'target missing inside the skill'))
                    continue
                if published_root not in path.parents:
                    found.append((md, number, target, 'points outside the published skills root'))
                    continue
                sibling = published_root / path.relative_to(published_root).parts[0]
                if not sibling.is_dir():
                    found.append((md, number, target, 'skill %r is not published' % sibling.name))
                elif not path.exists():
                    found.append((md, number, target, 'target missing in skill %r' % sibling.name))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('skill_dir', type=Path)
    parser.add_argument('--published-root', type=Path)
    args = parser.parse_args()
    if not (args.skill_dir / 'SKILL.md').is_file():
        parser.error('%s has no SKILL.md' % args.skill_dir)
    root = args.published_root or args.skill_dir.resolve().parent
    found = broken_links(args.skill_dir, root)
    for md, number, target, why in found:
        print('%s:%d: %s -- %s' % (md.relative_to(args.skill_dir.resolve()), number, target, why))
    print('%d broken link(s) in %s' % (len(found), args.skill_dir))
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
