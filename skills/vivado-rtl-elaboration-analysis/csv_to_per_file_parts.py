#!/usr/bin/env python3
"""
CSV to Per-File Elaboration Message Parts
==========================================
Reads the 7-column, headered messages.csv from parse_elab_messages.py and generates
actionable per-file message reports sorted by ascending message count (easiest
files first), chunked for subagent token budget.

CSV Column Schema (headered, comma-separated):
    field[0] = S.No       (1-based serial number)
    field[1] = severity   (ERROR, CRITICAL WARNING, WARNING, INFO)
    field[2] = msg_id     (integer, e.g. 128, 566, 637)
    field[3] = text       (full message text)
    field[4] = file       (source file path, may be empty)
    field[5] = line       (source line number, 0 if not present)
    field[6] = language   (VLOG or VHDL)

Usage:
    python3 csv_to_per_file_parts.py <elab_messages.csv> <output_dir>

Output Files:
    - messages_by_file.txt:       Summary with error counts + file rankings
    - messages_by_file_part1.txt: Easiest files (fewest messages), max 20 files
    - messages_by_file_partN.txt: Progressive difficulty

Author: GitHub Copilot
Version: 1.0
"""
import sys
import os
import csv
from collections import defaultdict
from datetime import datetime


def parse_csv(csv_file):
    """
    Parse elab_messages.csv and group messages by source file.

    Args:
        csv_file: Path to elab_messages.csv

    Returns:
        tuple: (messages_by_file dict, total_messages count)
    """
    messages_by_file = defaultdict(list)
    total_messages = 0

    with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 6:
                continue

            severity = row[0].strip()
            msg_id = row[1].strip()
            text = row[2].strip()
            filepath = row[3].strip()
            line_num = row[4].strip()
            language = row[5].strip()

            # Skip INFO messages (not actionable)
            if severity == 'INFO':
                continue

            # Extract filename from path for grouping
            if filepath:
                filename = os.path.basename(filepath)
            else:
                filename = '<no-file>'

            messages_by_file[filename].append({
                'severity': severity,
                'msg_id': msg_id,
                'text': text,
                'file': filepath,
                'line': line_num,
                'language': language,
            })
            total_messages += 1

    return messages_by_file, total_messages


def severity_rank(severity):
    """Return sort rank for severity (lower = more severe)."""
    return {'ERROR': 0, 'CRITICAL WARNING': 1, 'WARNING': 2}.get(severity, 3)


def generate_summary(messages_by_file, total_messages, output_file):
    """
    Generate main summary file with error counts and file rankings.
    """
    sorted_files = sorted(
        messages_by_file.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 120 + "\n")
        f.write("Per-File Elaboration Messages Summary (FROM CSV)\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 120 + "\n\n")

        f.write("STATISTICS:\n")
        f.write("-" * 120 + "\n")
        f.write(f"Total Actionable Messages: {total_messages}\n")
        f.write(f"Total Files with Messages: {len(messages_by_file)}\n\n")

        # ERRORS (highest priority)
        f.write("=" * 120 + "\n")
        f.write("ERRORS — DETAILED TABLE:\n")
        f.write("=" * 120 + "\n\n")

        error_entries = []
        for filename, messages in messages_by_file.items():
            for m in messages:
                if m['severity'] == 'ERROR':
                    error_entries.append((filename, m))

        if error_entries:
            f.write(f"{'File':<35} {'Line':<6} {'Synth 8-':<10} "
                    f"{'Message':<50} {'Lang':<5}\n")
            f.write("-" * 120 + "\n")
            for filename, m in sorted(
                error_entries,
                key=lambda x: (x[0], int(x[1]['line']) if x[1]['line'].isdigit() else 0),
            ):
                msg_short = m['text'][:48] + '..' if len(m['text']) > 50 else m['text']
                f.write(f"{filename:<35} {m['line']:<6} {m['msg_id']:<10} "
                        f"{msg_short:<50} {m['language']:<5}\n")
            f.write(f"\nTotal Errors: {len(error_entries)}\n")
        else:
            f.write("No ERRORS found.\n")

        # FILES RANKED BY MESSAGE COUNT
        f.write("\n" + "=" * 120 + "\n")
        f.write("ALL FILES RANKED BY MESSAGE COUNT:\n")
        f.write("=" * 120 + "\n")
        f.write(f"{'Rank':<6} {'File Name':<50} {'Total':>8} "
                f"{'Errors':>8} {'CritWarn':>10} {'Warnings':>10}\n")
        f.write("-" * 120 + "\n")

        for rank, (filename, messages) in enumerate(sorted_files, 1):
            errors = sum(1 for m in messages if m['severity'] == 'ERROR')
            crit = sum(1 for m in messages if m['severity'] == 'CRITICAL WARNING')
            warns = sum(1 for m in messages if m['severity'] == 'WARNING')
            f.write(f"{rank:<6} {filename:<50} {len(messages):>8} "
                    f"{errors:>8} {crit:>10} {warns:>10}\n")

        # PART FILE REFERENCE
        f.write("\n" + "=" * 120 + "\n")
        f.write("DETAILED MESSAGES — SEE PART FILES:\n")
        f.write("=" * 120 + "\n")
        f.write("  - Sorted by message count (ASCENDING — easiest first)\n")
        f.write("  - Split by file count (max 20 files per part)\n")
        f.write("  - Each part file is processable by a single subagent\n\n")

        # SUMMARY BY MESSAGE ID
        f.write("=" * 120 + "\n")
        f.write("MESSAGES BY SYNTH 8-XXX ID:\n")
        f.write("=" * 120 + "\n")

        id_summary = defaultdict(lambda: {'count': 0, 'severity': ''})
        for messages in messages_by_file.values():
            for m in messages:
                id_summary[m['msg_id']]['count'] += 1
                id_summary[m['msg_id']]['severity'] = m['severity']

        f.write(f"{'Synth 8-':<12} {'Severity':<20} {'Count':>10} "
                f"{'Percentage':>12}\n")
        f.write("-" * 120 + "\n")
        for mid in sorted(id_summary, key=lambda x: id_summary[x]['count'], reverse=True):
            pct = (id_summary[mid]['count'] / total_messages) * 100
            f.write(f"{mid:<12} {id_summary[mid]['severity']:<20} "
                    f"{id_summary[mid]['count']:>10} {pct:>11.2f}%\n")

    print(f"  Generated {output_file}")


def generate_part_files(messages_by_file, output_dir):
    """
    Generate detailed per-file messages split into parts (max 20 files each).
    Files sorted ascending by message count (easiest first).
    """
    sorted_files = sorted(
        messages_by_file.items(),
        key=lambda x: len(x[1]),
        reverse=False,
    )

    # Split into parts of max 20 files each
    max_files_per_part = 20
    parts = []
    current_part = []
    current_count = 0

    for filename, messages in sorted_files:
        current_part.append((filename, messages))
        current_count += len(messages)
        if len(current_part) >= max_files_per_part:
            parts.append((current_part, current_count))
            current_part = []
            current_count = 0

    if current_part:
        parts.append((current_part, current_count))

    num_parts = len(parts)
    overall_rank = 0

    for part_num, (part_files, total_part_messages) in enumerate(parts, 1):
        part_output = os.path.join(
            output_dir, f"messages_by_file_part{part_num}.txt")
        start_rank = overall_rank + 1
        end_rank = overall_rank + len(part_files)

        with open(part_output, 'w', encoding='utf-8') as f:
            f.write("=" * 120 + "\n")
            f.write(f"DETAILED MESSAGES — PART {part_num} of {num_parts}\n")
            f.write(f"Files Ranked {start_rank}-{end_rank} "
                    f"(by message count — ASCENDING)\n")
            f.write(f"Total files in part: {len(part_files)}, "
                    f"Total messages: {total_part_messages}\n")
            mrange = f"{len(part_files[0][1])}-{len(part_files[-1][1])}"
            f.write(f"Message range per file: {mrange}\n")
            f.write(f"Generated: "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 120 + "\n\n")

            for file_idx, (filename, messages) in enumerate(
                part_files, start=start_rank
            ):
                f.write(f"\n{'=' * 120}\n")
                f.write(f"FILE #{file_idx}: {filename} "
                        f"({len(messages)} messages)\n")
                f.write(f"{'=' * 120}\n")
                f.write(f"{'#':<4} {'Sev':<18} {'Line':<6} {'Synth 8-':<10} "
                        f"{'Lang':<6} {'Message':<60}\n")
                f.write("-" * 120 + "\n")

                # Sort by severity (errors first) then line number
                sorted_msgs = sorted(
                    messages,
                    key=lambda x: (
                        severity_rank(x['severity']),
                        int(x['line']) if x['line'].isdigit() else 0,
                    ),
                )

                for m_idx, m in enumerate(sorted_msgs, start=1):
                    msg_short = (m['text'][:58] + '..'
                                 if len(m['text']) > 60
                                 else m['text'])
                    f.write(f"{m_idx:<4} {m['severity']:<18} {m['line']:<6} "
                            f"{m['msg_id']:<10} {m['language']:<6} "
                            f"{msg_short:<60}\n")

            f.write(f"\n{'=' * 120}\n")
            f.write(f"END OF PART {part_num} — {len(part_files)} files, "
                    f"{total_part_messages} messages\n")
            f.write(f"{'=' * 120}\n")

        overall_rank += len(part_files)
        print(f"  Generated {part_output} "
              f"({len(part_files)} files, {total_part_messages} messages)")


def main():
    if len(sys.argv) < 3:
        print("Usage: csv_to_per_file_parts.py <elab_messages.csv> <output_dir>",
              file=sys.stderr)
        sys.exit(1)

    csv_file = sys.argv[1]
    output_dir = sys.argv[2]

    if not os.path.exists(csv_file):
        print(f"ERROR: CSV file not found: {csv_file}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    summary_file = os.path.join(output_dir, "messages_by_file.txt")

    print(f"\nCSV to Per-File Parts (Elaboration)")
    print(f"Input:  {csv_file}")
    print(f"Output: {output_dir}/\n")

    messages_by_file, total_messages = parse_csv(csv_file)

    if total_messages == 0:
        print("No actionable messages found in CSV.")
        sys.exit(0)

    generate_summary(messages_by_file, total_messages, summary_file)
    generate_part_files(messages_by_file, output_dir)

    print(f"\nComplete: {total_messages} messages in "
          f"{len(messages_by_file)} files")


if __name__ == '__main__':
    main()
