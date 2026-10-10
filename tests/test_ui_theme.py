"""The setup wizard's theme must reach the main desktop UI."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from main import configured_ui_theme


APP_DIR = Path(__file__).resolve().parents[1]


class UiThemeTests(unittest.TestCase):
    def test_wizard_theme_takes_priority_over_older_dynamic_config(self):
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            (base / "config.env").write_text("THEME_MODE=dark\n", encoding="utf-8")
            (base / "agent_config.json").write_text(
                '{"model_registry": {}, "model_routes": {}, "ui": {"theme": "light"}}',
                encoding="utf-8",
            )
            self.assertEqual(configured_ui_theme(base), "dark")
            (base / "config.env").write_text("THEME_MODE=system\n", encoding="utf-8")
            self.assertEqual(configured_ui_theme(base), "system")

    def test_frontend_receives_theme_and_has_dark_palette(self):
        html = (APP_DIR / "preview.html").read_text(encoding="utf-8")
        source = (APP_DIR / "tutor_window.py").read_text(encoding="utf-8")
        self.assertIn("document.documentElement.dataset.theme", html)
        self.assertIn('html[data-theme="dark"]', html)
        self.assertIn("window.pyBridge.themeChanged.connect", html)
        self.assertIn("self.web_bridge.themeChanged.emit(self._effective_theme)", source)
        self.assertIn("preview_url.setQuery(f\"theme={self._effective_theme}\")", source)

    def test_secondary_dialog_follows_main_window_theme(self):
        from PySide6.QtWidgets import QApplication, QDialog, QWidget
        from dialog_theme import apply_dialog_theme

        app = QApplication.instance() or QApplication([])
        parent = QWidget()
        parent._effective_theme = "dark"
        dialog = QDialog(parent)
        apply_dialog_theme(dialog, animate=False)
        self.assertIn("QDialog { background: #171B22", dialog.styleSheet())
        dialog.close()
        parent.close()


if __name__ == "__main__":
    unittest.main()
