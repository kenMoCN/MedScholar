import tempfile
import unittest
from pathlib import Path

from core.config_loader import ConfigLoadError, load_legacy_config, parse_env_text


class ConfigLoaderTests(unittest.TestCase):
    def _load(self, content: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.env"
            path.write_text(content, encoding="utf-8")
            return load_legacy_config(path)

    def test_dual_model_config_is_normalized_without_keys_in_public_config(self):
        loaded = self._load(
            """
            ENABLE_IMAGE_RECOGNITION=true
            DEEPSEEK_MODEL_NAME=deepseek-example
            DEEPSEEK_API_KEY=secret-ds
            DOUBAO_VISION_MODEL_NAME=doubao-vision-example
            DOUBAO_API_KEY=secret-db
            USER_PROFICIENCY=proficient
            HAS_TEXTBOOKS=true
            CITE_TEXTBOOK_LOCATION=false
            REQUIRE_FILE_OPERATION_APPROVAL=false
            THEME_MODE=dark
            """
        )

        self.assertEqual(loaded.issues, ())
        self.assertEqual(
            loaded.config["model_routes"]["vision"], "doubao_vision_default"
        )
        self.assertEqual(
            loaded.config["model_routes"]["reasoning"], "deepseek_default"
        )
        self.assertNotIn("secret-ds", repr(loaded))
        self.assertNotIn("secret-ds", str(loaded.config))
        self.assertEqual(
            loaded.config["model_registry"]["deepseek_default"]["api_key_ref"],
            "env:DEEPSEEK_API_KEY",
        )
        self.assertEqual(loaded.secrets["DEEPSEEK_API_KEY"], "secret-ds")
        self.assertEqual(
            loaded.config["document_defaults"]["citation_display"], "hidden"
        )
        self.assertTrue(
            loaded.config["document_defaults"]["evidence_capture"]
        )

    def test_vision_disabled_does_not_require_doubao(self):
        loaded = self._load(
            """
            ENABLE_IMAGE_RECOGNITION=false
            DEEPSEEK_MODEL_NAME=deepseek-example
            DEEPSEEK_API_KEY=secret-ds
            HAS_TEXTBOOKS=false
            """
        )

        self.assertEqual(loaded.issues, ())
        self.assertNotIn("vision", loaded.config["model_routes"])
        self.assertFalse(
            loaded.config["model_registry"]["doubao_vision_default"]["enabled"]
        )

    def test_deepseek_flash_is_marked_vision_capable_without_changing_default_route(self):
        loaded = self._load(
            "DEEPSEEK_MODEL_NAME=deepseek-flash\n"
            "DEEPSEEK_API_KEY=secret-ds\n"
            "ENABLE_IMAGE_RECOGNITION=false\n"
        )
        self.assertIn(
            "vision", loaded.config["model_registry"]["deepseek_default"]["capabilities"]
        )
        self.assertNotIn("vision", loaded.config["model_routes"])

    def test_old_config_loads_but_reports_missing_model_names(self):
        loaded = self._load(
            """
            ENABLE_IMAGE_RECOGNITION=true
            DEEPSEEK_API_KEY=secret-ds
            DOUBAO_API_KEY=secret-db
            """
        )

        self.assertIn("缺少 DEEPSEEK_MODEL_NAME", loaded.issues)
        self.assertIn(
            "已启用图片识别，但缺少 DOUBAO_VISION_MODEL_NAME", loaded.issues
        )

    def test_invalid_boolean_is_rejected(self):
        with self.assertRaises(ConfigLoadError):
            self._load("ENABLE_IMAGE_RECOGNITION=maybe")

    def test_invalid_line_is_rejected(self):
        with self.assertRaises(ConfigLoadError):
            parse_env_text("NOT_AN_ASSIGNMENT")


if __name__ == "__main__":
    unittest.main()
