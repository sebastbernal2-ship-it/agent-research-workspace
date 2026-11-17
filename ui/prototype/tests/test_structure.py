import json
import re
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
        cls.board_css = (WEB_ROOT / "src" / "kanban-board.css").read_text(encoding="utf-8")
        cls.readme = (WEB_ROOT / "README.md").read_text(encoding="utf-8")

    def test_shadcn_base_is_pinned_and_licensed_and_the_ui_is_small(self):
        for dependency, version in self.package["dependencies"].items():
            self.assertFalse(version.startswith(("^", "~")), dependency)
        self.assertIn("dashboard-01", self.readme)
        self.assertIn("dd41d19e2f80ff6891951f7ced74c2c9a650b065", self.readme)
        self.assertIn("Amp's public", self.readme)
        self.assertIn('Record answer', self.tsx)
        for color in ('#f9fdf6', '#f2f6ef', '#edf1ea', '#2f8052', '#bb8e00'):
            self.assertIn(color, self.css)
        self.assertTrue((WEB_ROOT / "SHADCN-LICENSE.md").is_file())
        self.assertTrue((WEB_ROOT / "package-lock.json").is_file())

    def test_navigation_is_three_places_and_the_slop_is_gone(self):
        nav = self.tsx[self.tsx.index('aria-label="Project navigation"'):self.tsx.index('<div className="sidebar-spacer"/>')]
        self.assertEqual(len(re.findall(r"<button onClick=\{\(\) => setView\(", nav)), 3)  # Home, Phases, Memory
        for label in ("Home", "Phases", "Memory"):
            self.assertIn(label, nav)
        for home_block in ("Needs you", "Now", "Latest"):
            self.assertIn(home_block, self.tsx)
        # the things the owner never used or looked at
        for gone in ("PROJECT BREAKDOWN", "Goal → outcomes → workstreams → steps", "Process and work-item details", "Project history",
                     "Recorded acceptance criteria", "PROJECT STATE", "WorkTree", "projectPulse", "Sidebar collapse unavailable",
                     "Search unavailable", "Prototype · not authenticated", "goal-footnote", "Event-by-event detail"):
            self.assertNotIn(gone, self.tsx)
        # chat is a drawer available from any page, not a section
        self.assertIn("ChatDrawer", self.tsx)
        self.assertIn("Ask the VP", self.tsx)
        # the question you are on must not be reset by background polling
        self.assertIn("selectedIdRef", self.tsx)
        self.assertIn("const keep = result.questions.some((q) => q.questionId === selectedIdRef.current)", self.tsx)

    def test_frontend_uses_same_origin_api_and_no_remote_assets_or_storage(self):
        self.assertIn("fetch(path", self.tsx)
        self.assertIn("URLSearchParams(window.location.search)", self.tsx)
        self.assertNotRegex(self.tsx, r"https?://")
        self.assertNotRegex(self.css, r"https?://")
        for forbidden in ("localStorage", "sessionStorage", "dangerouslySetInnerHTML"):
            self.assertNotIn(forbidden, self.tsx)

    def test_core_project_work_inbox_and_limitations_are_visible(self):
        for text in (
            "New project", "Existing project", "Inspect repository", "Add an outcome or nested step", "OPEN DECISIONS",
            "Alignment interview", "Auto-run", "KanbanBoard", "PhasesView", "PhasesPage", "MemoryPage", "QuestionChat",
            "RECORD THIS AS YOUR ANSWER?", "Approve task", "Plan phases with the VP", "Move on to the next phase",
            "LOCAL DATA AGENTS CAN READ", "Project settings", "loopback only",
        ):
            self.assertIn(text, self.tsx)
        self.assertIn("/api/v1/projects", self.tsx)
        self.assertIn("/answers", self.tsx)
        self.assertIn("/closure", self.tsx)
        self.assertIn("/api/v1/repositories/inspect", self.tsx)
        self.assertIn("/alignment/resume", self.tsx)
        self.assertIn("/approval", self.tsx)
        self.assertIn('aria-label={`Open ${item.title} details`}', self.tsx)
        self.assertIn('className="work-detail-modal"', self.tsx)
        self.assertIn('grid-template-columns:repeat(5', self.board_css)
        self.assertNotIn('board-status-banner', self.tsx)


if __name__ == "__main__":
    unittest.main()
