import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from literal_coverage import check


def test_frozen_literal_fixtures_match_all_rows():
    fixtures = json.loads((ROOT / 'tests/fixtures/literal-coverage.json').read_text())
    assert len(fixtures) == 50
    for fixture in fixtures:
        assert check(fixture['task'], fixture['written'], fixture['identity']) == fixture['expected_rows']
