"""Every relative Markdown link in the skill resolves (GitHub issue #518)."""
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_skill_links import broken_links  # noqa: E402

SKILL = HERE.parents[1]


class SkillLinkTests(unittest.TestCase):
    def test_no_broken_or_unpublished_links(self):
        found = broken_links(SKILL, SKILL.parent)
        self.assertEqual([], ['%s:%d: %s -- %s' % (md.relative_to(SKILL), n, t, why) for md, n, t, why in found])


if __name__ == '__main__':
    unittest.main()
