import subprocess
import tempfile
import unittest
from pathlib import Path

from ui.prototype.repository_inspection import RepositoryInspectionError, alignment_segments, inspect_repository


class RepositoryInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "existing-project"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test User"], check=True)
        (self.repo / "README.md").write_text("# Project Atlas\n\nA local research notebook that turns speech transcripts into reviewed forecasts.\n", encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "architecture.md").write_text("# Architecture\n\nThe pipeline has collection, extraction, and evaluation stages.\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "main.py").write_text("DO NOT OPEN SOURCE CONTENT\n", encoding="utf-8")
        (self.repo / ".env").write_text("API_KEY=secret-sentinel\n", encoding="utf-8")
        (self.repo / "data").mkdir()
        (self.repo / "data" / "bulk.csv").write_text("private,data\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "README.md", "docs", "src", ".env"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-m", "fixture"], check=True, capture_output=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_only_bounded_tracked_docs_and_builds_evidence_grounded_questions(self):
        report = inspect_repository(str(self.repo), allowed_root=self.root)
        self.assertEqual(report["repositoryName"], "existing-project")
        self.assertEqual(report["trackedFileCount"], 3)
        self.assertEqual({doc["path"] for doc in report["documents"]}, {"README.md", "docs/architecture.md"})
        rendered = str(report)
        self.assertNotIn("secret-sentinel", rendered)
        self.assertNotIn("DO NOT OPEN SOURCE CONTENT", rendered)
        self.assertNotIn("private,data", rendered)
        self.assertIn("local research notebook", report["goalHint"])
        segments = alignment_segments(report)
        self.assertEqual(len(segments), 5)
        self.assertIn("README.md", segments[0]["evidence"])
        self.assertIn("unresolved", segments[3]["prompt"])

    def test_rejects_non_git_and_out_of_scope_paths(self):
        outside = self.root.parent / f"outside-{self.root.name}"
        outside.mkdir()
        try:
            with self.assertRaises(RepositoryInspectionError):
                inspect_repository(str(outside), allowed_root=self.root)
            with self.assertRaises(RepositoryInspectionError):
                inspect_repository(str(self.root), allowed_root=self.root)
        finally:
            outside.rmdir()

    def test_repository_configured_fsmonitor_helper_is_never_executed(self):
        marker = self.root / "fsmonitor-ran"
        helper = self.root / "fsmonitor-helper.sh"
        helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
        helper.chmod(0o700)
        subprocess.run(["git", "-C", str(self.repo), "config", "core.fsmonitor", str(helper)], check=True)
        inspect_repository(str(self.repo), allowed_root=self.root)
        self.assertFalse(marker.exists())

    def test_refuses_root_symlink(self):
        alias = self.root / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(RepositoryInspectionError):
            inspect_repository(str(alias), allowed_root=self.root)


if __name__ == "__main__":
    unittest.main()
