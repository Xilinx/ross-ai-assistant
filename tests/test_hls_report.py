from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "skills" / "hls-scripts"))
from hls_synth_report.report_info_getter import _get_synthesis_report_content

class ReportParserTests(unittest.TestCase):
    def test_hls_2024_summary_keeps_resources_loops_and_pragmas(self):
        summary, pragmas = _get_synthesis_report_content(str(FIXTURES / "hls_2024_1"))
        for value in ["top", "2 (1%)", "4 (2%)", "100", "200", "VITIS_LOOP_1", "50.000"]:
            self.assertIn(value, summary)
        self.assertNotIn("== HW Interfaces", summary)
        self.assertIn("== Pragma Report", pragmas)
        self.assertIn("II=1", pragmas)
        self.assertIn("variable=accum complete dim=0", pragmas)

    def test_hls_existing_summary_marker_keeps_previous_boundary(self):
        marker = "Name Prefix: '+' for module, 'o' for loop, '*' for dataflow"
        summary = "Synthesis report\n| top | 256 |\n" + marker
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "syn" / "report" / "csynth.rpt"
            path.parent.mkdir(parents=True)
            path.write_text(summary + "\n== HW Interfaces\ninterface data\n== Pragma Report\npipeline II=1\n")
            actual_summary, pragmas = _get_synthesis_report_content(directory)
            self.assertEqual(actual_summary, summary)
            self.assertEqual(pragmas, "== Pragma Report\npipeline II=1\n")

if __name__ == "__main__":
    unittest.main()
