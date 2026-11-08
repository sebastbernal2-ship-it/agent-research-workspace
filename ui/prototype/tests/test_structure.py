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

    def test_demo_has_explicit_no_persistence_and_no_network_or_storage_code(self):
        self.assertIn("no models, service, or persistence connected", self.html)
        self.assertIn("nothing is saved", self.html)
        self.assertNotIn("fetch(", self.js)
        self.assertNotIn("localStorage", self.js)
        self.assertNotIn("sessionStorage", self.js)
        self.assertNotIn("XMLHttpRequest", self.js)
        self.assertNotRegex(self.css, r"url\(\s*['\"]?https?://")

    def test_inbox_decision_controls_have_confirmation_dialogs(self):
        for dialog_id in ("answer-dialog", "clarify-dialog", "reject-dialog"):
            self.assertIn(f'id="{dialog_id}"', self.html)
        self.assertIn("Demo only. These controls do not save decisions", self.html)
        self.assertIn('aria-live="polite"', self.html)


if __name__ == "__main__":
    unittest.main()
