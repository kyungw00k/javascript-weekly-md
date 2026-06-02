import re
import unittest
from pathlib import Path


class WorkflowContractTests(unittest.TestCase):
    def test_workflow_uses_external_github_runner_with_public_source_checks(self):
        workflow = Path(".github/workflows/javascript-weekly.yml").read_text(encoding="utf-8")

        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("Verify JavaScript Weekly sources", workflow)
        self.assertIn("https://cprss.s3.amazonaws.com/javascriptweekly.com.xml", workflow)
        self.assertIn("https://javascriptweekly.com/issues", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("web" + "hook", workflow.lower())
        self.assertIsNone(re.search(r"[\uac00-\ud7a3]", workflow))
        self.assertNotIn("_ko" + ".md", workflow)

    def test_workflow_generates_and_commits_markdown_archive_only(self):
        workflow = Path(".github/workflows/javascript-weekly.yml").read_text(encoding="utf-8")

        self.assertIn("push:", workflow)
        self.assertIn("archive_year:", workflow)
        self.assertIn('default: ""', workflow)
        self.assertIn("python3 -m javascript_weekly_md.archive", workflow)
        self.assertIn("--all", workflow)
        self.assertIn('--year "$archive_year"', workflow)
        self.assertIn("python3 -m javascript_weekly_md.archive --all", workflow)
        self.assertNotIn("eval \"$(", workflow)
        self.assertEqual(workflow.count("python3 - <<"), 1)
        self.assertIn("newsletters/javascript-weekly/weekly", workflow)
        self.assertIn("git add newsletters/javascript-weekly/weekly", workflow)
        self.assertIn("git push", workflow)
        self.assertIsNone(
            re.search(r"(?m)^Archive JavaScript Weekly as Markdown$", workflow)
        )


if __name__ == "__main__":
    unittest.main()
