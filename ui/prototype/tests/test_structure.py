import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PrototypeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.landmarks = set()
        self.asset_paths = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag in {"main", "nav", "aside", "header"}:
            self.landmarks.add(tag)
        for name in ("src", "href"):
            value = attributes.get(name, "")
            if value and tag in {"script", "link"}:
                self.asset_paths.append(value)


class PrototypeStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "app.css").read_text(encoding="utf-8")
        cls.parser = PrototypeParser()
        cls.parser.feed(cls.html)

    def test_document_ids_are_unique_and_local_assets_exist(self):
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))
        for path in self.parser.asset_paths:
            self.assertFalse(path.startswith(("http://", "https://", "//")), path)
            self.assertTrue((ROOT / path).is_file(), path)

    def test_contains_distinct_project_chat_and_inbox_views(self):
        self.assertIn('id="view-conversation"', self.html)
        self.assertIn('id="view-inbox"', self.html)
        self.assertIn("SEPARATE FROM VP CHAT", self.html)
        self.assertTrue({"main", "nav", "aside", "header"}.issubset(self.parser.landmarks))

    def test_browser_uses_only_same_origin_api_and_no_browser_storage(self):
        self.assertIn('id="service-status"', self.html)
        self.assertIn("fetch(path, options)", self.js)
        self.assertNotRegex(self.js, r"https?://")
        self.assertNotIn("localStorage", self.js)
        self.assertNotIn("sessionStorage", self.js)
        self.assertNotIn("XMLHttpRequest", self.js)
        self.assertNotRegex(self.css, r"url\(\s*['\"]?https?://")

    def test_project_history_views_use_scoped_api_and_safe_text_rendering(self):
        for collection in ("runs", "evidence", "artifacts"):
            self.assertIn(f"${{PROJECT_API}}/{collection}", self.js)
        self.assertIn('id="run-record-list"', self.html)
        self.assertIn('id="evidence-record-list"', self.html)
        self.assertIn('id="artifact-record-list"', self.html)
        self.assertNotIn("innerHTML", self.js)
        self.assertIn("item.textContent", self.js)

    def test_project_and_work_creation_are_connected_to_local_api(self):
        self.assertIn('id="project-form"', self.html)
        self.assertIn('id="work-form"', self.html)
        self.assertIn('apiRequest("/api/v1/projects"', self.js)
        self.assertIn('`${PROJECT_API}/work`', self.js)
        self.assertIn("No agent is connected yet", self.js)
        self.assertIn("It has not been executed.", self.js)

    def test_inbox_tabs_support_roving_focus_and_keyboard_navigation(self):
        self.assertIn('role="tablist"', self.html)
        self.assertIn('role="tab"', self.html)
        self.assertIn('button.tabIndex = selected ? 0 : -1', self.js)
        self.assertIn('event.key === "ArrowRight"', self.js)
        self.assertIn('event.key === "ArrowLeft"', self.js)
        self.assertIn('event.key === "Home"', self.js)
        self.assertIn('event.key === "End"', self.js)
        self.assertIn('tabs[nextIndex].focus()', self.js)

    def test_inbox_decision_controls_are_explicit_and_service_backed(self):
        for dialog_id in ("answer-dialog", "reject-dialog"):
            self.assertIn(f'id="{dialog_id}"', self.html)
        self.assertIn("Clarification commands are not implemented yet", self.html)
        self.assertIn("local canonical project history", self.html)
        self.assertIn("aria-live=\"polite\"", self.html)


if __name__ == "__main__":
    unittest.main()
