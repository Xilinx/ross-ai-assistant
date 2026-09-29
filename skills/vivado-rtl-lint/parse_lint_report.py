# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import os
import sys


def extract_synthesis_insights(file_path):
    """
    Reads a lint report file (JSON or ASCII table) and returns a
    headerless CSV string with columns:
      rule_id, description, rtl_name, info, module, file_name, line_number
    """
    condensed_data = []

    with open(file_path, 'r') as f:
        content = f.read().strip()

    # Strategy 1: Attempt to parse as JSON
    try:
        if content.startswith('"cols"'):
            content = f"{{{content}}}"

        data = json.loads(content)

        # JSON cols: ["id", "description", "name", "info", "module", "file", "line"]
        for row in data.get("rows", []):
            rule_id = str(row[0]).strip()
            description = str(row[1]).strip()
            rtl_name = str(row[2]).strip()
            info = str(row[3]).strip()
            module = str(row[4]).strip()
            file_name = str(row[5]).strip()
            line_num = str(row[6]).strip()

            condensed_data.append(
                f"{rule_id},{description},{rtl_name},{info},{module},{file_name},{line_num}")

        return "\n".join(condensed_data)

    # Strategy 2: Fallback to ASCII Table parsing
    except json.JSONDecodeError:
        lines = content.split('\n')

        # Extract from "2. Expanded" table ONLY (skip "1. Summary").
        # Expanded table has 7 pipe-delimited columns:
        #   Rule ID | description | rtl_name | info | rtl_hierarchy | rtl_file | Line number
        # Rule ID preserves T1/T2 suffix, e.g. "ASSIGN-5 (T1)", "ASSIGN-6 (T2)".
        #
        # IMPORTANT: Parse using fixed column positions from the +---+---+ border
        # line, NOT split('|'). Some fields contain literal '|' characters
        # (e.g. ASSIGN-2 rtl_name = "|" for bitwise OR), which would break
        # a naive pipe-split.
        in_expanded = False
        col_positions = []  # character positions of '+' in border line
        found_header = False  # tracks whether header row has been seen
        found_data = False    # tracks whether any data row has been parsed

        for line in lines:
            stripped = line.strip()

            if stripped.startswith("2. Expanded"):
                in_expanded = True
                col_positions = []  # reset — "2. Expanded" appears in ToC too
                found_header = False
                found_data = False
                continue

            if not in_expanded:
                continue

            # Detect column boundaries from the +---+---+ border line
            # Use the raw line (not stripped) so positions match data rows
            # Table has 3 borders: top (sets positions), after-header (skip),
            # and closing (break after data rows seen).
            if stripped.startswith('+') and '-' in stripped:
                if not col_positions:
                    col_positions = [i for i, c in enumerate(line.rstrip())
                                     if c == '+']
                elif found_data:
                    # Closing border after data rows = end of table
                    break
                # else: header-separator border, just skip
                continue

            # Skip empty lines
            if not stripped:
                continue

            # Need at least 8 boundary positions for 7 data columns
            if len(col_positions) < 8:
                continue

            # Extract fields by slicing the raw line at fixed column positions
            raw = line.rstrip()
            fields = []
            for j in range(len(col_positions) - 1):
                start = col_positions[j] + 1   # skip the '|' or '+'
                end = col_positions[j + 1]
                if end <= len(raw):
                    fields.append(raw[start:end].strip())
                else:
                    fields.append('')

            # Skip header row
            if len(fields) >= 7 and fields[0].lower() == "rule id":
                found_header = True
                continue

            if len(fields) >= 7:
                found_data = True
                rule_id = fields[0]        # e.g. "ASSIGN-5 (T1)", "INFER-2"
                description = fields[1]    # e.g. "Bit(s) not assigned"
                rtl_name = fields[2]       # signal/instance name (may be empty or '|')
                info = fields[3]           # bit index (T1), count (T2), or N/A
                module = fields[4]         # rtl_hierarchy / parent module
                file_name = fields[5]      # rtl_file
                line_num = fields[6]       # Line number

                condensed_data.append(
                    f"{rule_id},{description},{rtl_name},{info},{module},{file_name},{line_num}")

        return "\n".join(condensed_data)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_lint_report.py <report_file> [<output_csv>]",
              file=sys.stderr)
        sys.exit(1)

    report_file = sys.argv[1]
    result = extract_synthesis_insights(report_file)

    if len(sys.argv) >= 3:
        with open(sys.argv[2], 'w') as f:
            f.write(result + "\n")
    else:
        print(result)
# Output: ASSIGN-6,Bit(s) not used,input_a_reg,0,mult_signed,mult_signed.vhd,56