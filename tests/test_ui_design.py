import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "dietnerd-website" / "index.html").read_text()
CSS = (ROOT / "dietnerd-website" / "index.css").read_text()
LOGIN_CSS = (ROOT / "dietnerd-website" / "login.css").read_text()


class UiDesignContractTest(unittest.TestCase):
    def test_page_has_semantic_hero_and_workspace(self):
        self.assertIn('<section class="hero">', HTML)
        self.assertIn('<main class="chat-shell">', HTML)
        self.assertIn('role="log"', HTML)
        self.assertIn('class="composer"', HTML)
        self.assertIn('class="evidence-visual"', HTML)

    def test_controls_keep_the_ids_used_by_javascript(self):
        for element_id in ("conversation-select", "new-conversation", "delete-conversation", "question", "submit"):
            self.assertIn(f'id="{element_id}"', HTML)

    def test_design_has_mobile_and_reduced_motion_rules(self):
        self.assertIn('@media(max-width:800px)', CSS)
        self.assertIn('prefers-reduced-motion:reduce', CSS)
        self.assertIn('@media(max-width:520px)', LOGIN_CSS)

    def test_old_open_sans_visual_system_is_gone(self):
        self.assertNotIn("Open Sans", CSS)
        self.assertNotIn("#007bff", CSS.lower())


if __name__ == "__main__":
    unittest.main()
