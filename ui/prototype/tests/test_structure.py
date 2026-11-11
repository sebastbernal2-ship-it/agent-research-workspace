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
        cls.css = (WEB_ROOT / "src" / "style.css").read_text(encoding="utf-8") + (WEB_ROOT / "src" / "goal.css").read_text(encoding="utf-8")
        cls.readme = (WEB_ROOT / "README.md").read_text(encoding="utf-8")

    def test_shadcn_base_is_pinned_and_licensed_with_goal_centered_workspace(self):
        for dependency, version in self.package["dependencies"].items():
            self.assertFalse(version.startswith(("^", "~")), dependency)
        self.assertIn("dashboard-01", self.readme)
        self.assertIn("dd41d19e2f80ff6891951f7ced74c2c9a650b065", self.readme)
        self.assertIn("Amp's public", self.readme)
        for surface in ('PROJECT UPDATE', 'THE FINISH LINE', 'Work plan details', 'Project history', 'Your input'):
            self.assertIn(surface, self.tsx)
        self.assertNotIn('No agent response yet', self.tsx)
        self.assertNotIn('PROJECT CHANGES', self.tsx)
        for color in ('#f9fdf6', '#f2f6ef', '#edf1ea', '#2f8052', '#bb8e00'):
            self.assertIn(color, self.css)
        self.assertTrue((WEB_ROOT / "SHADCN-LICENSE.md").is_file())
        self.assertTrue((WEB_ROOT / "package-lock.json").is_file())

    def test_frontend_uses_same_origin_api_and_no_remote_assets_or_storage(self):
        self.assertIn("fetch(path", self.tsx)
        self.assertIn("URLSearchParams(window.location.search)", self.tsx)
        self.assertNotRegex(self.tsx, r"https?://")
        self.assertNotRegex(self.css, r"https?://")
        for forbidden in ("localStorage", "sessionStorage", "dangerouslySetInnerHTML"):
            self.assertNotIn(forbidden, self.tsx)

    def test_core_project_work_inbox_and_limitations_are_visible(self):
        for text in (
            "Create a project", "Add to the project plan", "OPEN DECISIONS",
            "No model or worker is connected", "not authenticated",
        ):
            self.assertIn(text, self.tsx)
        self.assertIn("/api/v1/projects", self.tsx)
        self.assertIn("/answers", self.tsx)
        self.assertIn("/closure", self.tsx)


if __name__ == "__main__":
    unittest.main()
