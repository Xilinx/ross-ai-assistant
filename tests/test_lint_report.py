import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
spec = importlib.util.spec_from_file_location("parse_lint_report", ROOT / "skills" / "vivado-rtl-lint" / "parse_lint_report.py")
lint_parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint_parser)

class ReportParserTests(unittest.TestCase):
    def test_empty_lint_table_does_not_parse_waiver_footer(self):
        self.assertEqual(
            lint_parser.extract_synthesis_insights(FIXTURES / "linter_2024_1_empty.rpt"),
            "",
        )

    def test_lint_violation_preserves_literal_pipe(self):
        headers = ["Rule ID", "description", "rtl_name", "info", "rtl_hierarchy", "rtl_file", "Line number"]
        violation = ["ASSIGN-2", "Bit width mismatch", "a | b", "N/A", "top", "top.v", "42"]
        widths = [20] * 7
        border = "+" + "+".join("-" * width for width in widths) + "+"

        def row(values):
            return "|" + "|".join(value.ljust(width) for value, width in zip(values, widths)) + "|"

        report = "\n".join(["2. Expanded", border, row(headers), border, row(violation), border,
                            "* create_waiver can be used to apply waiver to linter violations."])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "linter.rpt"
            path.write_text(report)
            self.assertEqual(lint_parser.extract_synthesis_insights(path), ",".join(violation))

if __name__ == "__main__":
    unittest.main()
