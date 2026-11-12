import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.scoped_workspace import ScopedWorkspaceError, create_scoped_git_snapshot


class ScopedGitSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scoped-git-snapshot-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.output_parent = self.root / "snapshots"
        self.output_parent.mkdir(mode=0o700)
        self.git("init", "--quiet")
        self.git("config", "user.email", "snapshot-test@example.invalid")
        self.git("config", "user.name", "Snapshot Test")
        (self.repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "main.py").write_text("committed safe source\n", encoding="utf-8")
        (self.repo / ".env").write_text("SECRET_SENTINEL=not-for-snapshot\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "baseline")
        self.commit = self.git("rev-parse", "HEAD").strip()
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "data.bin").write_bytes(b"ignored local data")
        (self.repo / "untracked.txt").write_text("untracked local data\n", encoding="utf-8")
        (self.repo / "src" / "main.py").write_text("uncommitted user edit\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True,
        ).stdout

    def test_materializes_only_allowlisted_committed_files_without_git_or_local_data(self):
        before = self.git("status", "--porcelain")
        snapshot = create_scoped_git_snapshot(
            self.repo, commit=self.commit, allowed_paths=["src"], output_parent=self.output_parent,
        )
        try:
            self.assertEqual(snapshot.source_commit, self.commit)
            self.assertEqual(snapshot.allowed_paths, ("src",))
            self.assertEqual(len(snapshot.files), 1)
            self.assertEqual(snapshot.total_bytes, len(b"committed safe source\n"))
            copied = snapshot.root / "src" / "main.py"
            self.assertEqual(copied.read_text(encoding="utf-8"), "committed safe source\n")
            self.assertFalse((snapshot.root / ".git").exists())
            self.assertFalse((snapshot.root / ".env").exists())
            self.assertFalse((snapshot.root / "ignored").exists())
            self.assertFalse((snapshot.root / "untracked.txt").exists())
            self.assertEqual(snapshot.manifest()["files"][0]["sha256"], hashlib.sha256(b"committed safe source\n").hexdigest())
            self.assertEqual(snapshot.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.git("status", "--porcelain"), before, "materialization must not touch source state")
        finally:
            snapshot.cleanup()
        self.assertFalse(snapshot.root.exists())

    def test_rejects_traversal_git_metadata_and_credential_paths(self):
        for path in ("../outside", "/absolute/path", "src/../.env", "src//main.py", "./src", ".git", ".env", "config/.env.production"):
            with self.subTest(path=path), self.assertRaises(ScopedWorkspaceError):
                create_scoped_git_snapshot(
                    self.repo, commit=self.commit, allowed_paths=[path], output_parent=self.output_parent,
                )

    def test_rejects_non_full_or_nonexistent_commit_and_missing_path(self):
        with self.assertRaisesRegex(ScopedWorkspaceError, "full lowercase"):
            create_scoped_git_snapshot(
                self.repo, commit="HEAD", allowed_paths=["src"], output_parent=self.output_parent,
            )
        with self.assertRaises(ScopedWorkspaceError):
            create_scoped_git_snapshot(
                self.repo, commit="f" * 40, allowed_paths=["src"], output_parent=self.output_parent,
            )
        with self.assertRaisesRegex(ScopedWorkspaceError, "absent"):
            create_scoped_git_snapshot(
                self.repo, commit=self.commit, allowed_paths=["missing"], output_parent=self.output_parent,
            )
        self.assertEqual(list(self.output_parent.iterdir()), [])

    def test_rejects_output_inside_source_repository(self):
        inside = self.repo / "snapshot-output"
        inside.mkdir()
        with self.assertRaisesRegex(ScopedWorkspaceError, "outside the source repository"):
            create_scoped_git_snapshot(
                self.repo, commit=self.commit, allowed_paths=["src"], output_parent=inside,
            )
        self.assertEqual(list(inside.iterdir()), [])

    def test_archive_symlinks_are_rejected_and_partial_snapshot_removed(self):
        link = self.repo / "src" / "linked-file"
        link.symlink_to("main.py")
        self.git("add", "src/linked-file")
        self.git("commit", "--quiet", "-m", "add symlink")
        symlink_commit = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(ScopedWorkspaceError, "symlink"):
            create_scoped_git_snapshot(
                self.repo, commit=symlink_commit, allowed_paths=["src"], output_parent=self.output_parent,
            )
        self.assertEqual(list(self.output_parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
