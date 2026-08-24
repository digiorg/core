"""Contract tests for the repository pull request template."""

from collections import Counter
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPOSITORY_ROOT / ".github" / "pull_request_template.md"
REQUIRED_HEADINGS = (
    "## Linked Work",
    "## Problem and Outcome",
    "## Scope and Non-Goals",
    "## Requirement-to-Test Matrix",
    "## RED Evidence",
    "## GREEN and Full Validation Evidence",
    "## Documentation Changes",
    "## Delivery Plan",
    "## System/Integration Validation Plan",
    "## Rollback",
    "## Limitations",
)


class PullRequestTemplateTest(unittest.TestCase):
    def test_required_headings_exist_once_in_approved_order(self) -> None:
        self.assertTrue(
            TEMPLATE_PATH.is_file(),
            f"Pull request template is missing: {TEMPLATE_PATH}",
        )

        headings = [
            line.strip()
            for line in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ")
        ]
        counts = Counter(headings)
        missing = [heading for heading in REQUIRED_HEADINGS if counts[heading] == 0]
        duplicates = [heading for heading in REQUIRED_HEADINGS if counts[heading] > 1]

        self.assertFalse(missing, f"Missing required headings: {missing}")
        self.assertFalse(duplicates, f"Duplicate required headings: {duplicates}")

        required_headings_in_template = [
            heading for heading in headings if heading in REQUIRED_HEADINGS
        ]
        self.assertEqual(
            list(REQUIRED_HEADINGS),
            required_headings_in_template,
            "Required headings are not in the approved order. "
            f"Expected {list(REQUIRED_HEADINGS)}, got {required_headings_in_template}",
        )


if __name__ == "__main__":
    unittest.main()
