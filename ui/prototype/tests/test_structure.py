import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "web"


class PrototypeStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))
        cls.tsx = (WEB_ROOT / "src" / "main.tsx").read_text(encoding="utf-8")
        cls.css = (WEB_ROOT / "src" / "style.css").read_text(encoding="utf-8")
        cls.readme = (WEB_ROOT / "README.md").read_text(encoding="utf-8")

    def test_shadcn_base_is_pinned_and_licensed_with_amp_inspired_thread_layout(self):
        for dependency, version in self.package["dependencies"].items():
            self.assertFalse(version.startswith(("^", "~")), dependency)
        self.assertIn("dashboard-01", self.readme)
        self.assertIn("dd41d19e2f80ff6891951f7ced74c2c9a650b065", self.readme)
        self.assertIn("Amp's public", self.readme)
        for surface in ('thread-list', 'conversation-pane', 'review-pane', 'No agent response yet', 'No file changes'):
            self.assertIn(surface, self.tsx)
        self.assertTrue((WEB_ROOT / "SHADCN-LICENSE.md").is_file())
        self.assertTrue((WEB_ROOT / "package-lock.json").is_file())

    def test_frontend_uses_same_origin_api_and_no_remote_assets_or_storage(self):
        self.assertIn("fetch(path", self.tsx)
        self.assertNotRegex(self.tsx, r"https?://")
        self.assertNotRegex(self.css, r"https?://")
        for forbidden in ("localStorage", "sessionStorage", "dangerouslySetInnerHTML"):
            self.assertNotIn(forbidden, self.tsx)

    def test_core_project_work_inbox_and_limitations_are_visible(self):
        for text in (
            "Create a project", "Add a work item", "OPEN QUESTIONS",
            "no model or worker is connected", "not authenticated",
        ):
            self.assertIn(text, self.tsx)
        self.assertIn("/api/v1/projects", self.tsx)
        self.assertIn("/answers", self.tsx)
        self.assertIn("/closure", self.tsx)


if __name__ == "__main__":
    unittest.main()
