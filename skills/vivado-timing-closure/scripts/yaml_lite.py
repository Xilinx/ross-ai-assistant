#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Simple YAML parser for agent config files.
Handles the subset of YAML used in agent_config.yaml (no external dependencies).
Supports: scalars, lists, nested dicts, comments.
"""

import re


def load_yaml(filepath: str) -> dict:
    """Parse a simple YAML file into a Python dict."""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    return _parse_lines(lines)


def _parse_lines(lines: list) -> dict:
    """Parse lines into nested dict structure."""
    result = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip empty lines and comments
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            i += 1
            continue

        # Get indentation level
        indent = len(line) - len(line.lstrip())

        # Parse key: value
        match = re.match(r'^(\s*)([\w.-]+)\s*:\s*(.*)', line)
        if not match:
            i += 1
            continue

        key = match.group(2)
        value_str = match.group(3).strip()

        # Remove inline comments
        if value_str and '#' in value_str:
            # Be careful not to remove # inside quotes
            if not (value_str.startswith('"') or value_str.startswith("'")):
                value_str = re.split(r'\s+#', value_str, 1)[0].strip()

        if value_str:
            # Inline value
            result[key] = _parse_value(value_str)
            i += 1
        else:
            # Check if next lines are a list or nested dict
            i += 1
            child_lines = []
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.strip()
                if not next_stripped or next_stripped.startswith('#'):
                    child_lines.append(next_line)
                    i += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > indent:
                    child_lines.append(next_line)
                    i += 1
                else:
                    break

            # Determine if it's a list or dict
            first_content = None
            for cl in child_lines:
                cs = cl.strip()
                if cs and not cs.startswith('#'):
                    first_content = cs
                    break

            if first_content and first_content.startswith('- '):
                result[key] = _parse_list(child_lines, indent + 2)
            else:
                # Dedent child lines
                result[key] = _parse_lines(child_lines)

    return result


def _parse_list(lines: list, base_indent: int) -> list:
    """Parse a YAML list."""
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('- '):
            value_str = stripped[2:].strip()
            result.append(_parse_value(value_str))
    return result


def _parse_value(value_str: str):
    """Parse a scalar value."""
    # Handle quoted strings (strip trailing comments after closing quote)
    if value_str.startswith('"'):
        end_quote = value_str.find('"', 1)
        if end_quote > 0:
            return value_str[1:end_quote]
    if value_str.startswith("'"):
        end_quote = value_str.find("'", 1)
        if end_quote > 0:
            return value_str[1:end_quote]

    # Remove inline comments for unquoted values
    if '#' in value_str:
        value_str = re.split(r'\s+#', value_str, 1)[0].strip()

    # Boolean
    if value_str.lower() == 'true':
        return True
    if value_str.lower() == 'false':
        return False

    # None
    if value_str.lower() in ('null', '~', ''):
        return None

    # Integer
    try:
        return int(value_str)
    except ValueError:
        pass

    # Float
    try:
        return float(value_str)
    except ValueError:
        pass

    # List inline [1, 2, 3]
    if value_str.startswith('[') and value_str.endswith(']'):
        inner = value_str[1:-1]
        if not inner.strip():
            return []
        items = [_parse_value(x.strip()) for x in inner.split(',')]
        return items

    # Plain string
    return value_str
