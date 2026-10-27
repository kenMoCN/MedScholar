import json
import tempfile
import unittest
from pathlib import Path

from core.config_loader import ConfigLoadError
from core.dynamic_config import (
    CONFIG_FILENAME,
    SECRETS_FILENAME,
    editable_models,
    load_dynamic_config,
    save_dynamic_config,
)


class DynamicConfigTests(unittest.TestCase):
    def test_arbitrary_models_and_routes_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            saved = save_dynamic_config(
                temp,
                models=[
                    {
                        "model_id": "reasoner",
                        "provider": "custom_provider",
                        "model_name": "medical-model-v1",
                        "api_base": "https://example.test/v1",
                        "api_protocol": "openai_compatible",
                        "api_key": "secret-value",
                        "capabilities": ["text", "structured_output"],
                        "enabled": True,
                    }
                ],
                routes={
                    "reasoning": "reasoner",
                    "planning": "reasoner",
                    "verification": "reasoner",
                },
            )
            loaded = load_dynamic_config(temp)

            self.assertEqual(loaded.issues, ())
            self.assertEqual(loaded.config["schema_version"], "2.0")
            self.assertEqual(saved.secrets["MODEL_KEY_REASONER"], "secret-value")
            self.assertNotIn(
                "secret-value",
                (Path(temp) / CONFIG_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "secret-value",
                (Path(temp) / SECRETS_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertEqual(editable_models(loaded)[0]["api_key"], "secret-value")

    def test_custom_provider_requires_api_base(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ConfigLoadError, "必须填写 API 地址"):
                save_dynamic_config(
                    temp,
                    models=[
                        {
                            "model_id": "custom_model",
                            "provider": "custom",
                            "model_name": "m",
                            "api_key": "k",
                            "capabilities": ["text"],
                        }
                    ],
                    routes={"reasoning": "custom_model"},
                )

    def test_public_json_never_contains_api_key_field(self):
        with tempfile.TemporaryDirectory() as temp:
            save_dynamic_config(
                temp,
                models=[
                    {
                        "model_id": "deepseek_main",
                        "provider": "deepseek",
                        "model_name": "deepseek-chat",
                        "api_key": "top-secret",
                        "capabilities": ["text", "structured_output"],
                    }
                ],
                routes={
                    "reasoning": "deepseek_main",
                    "verification": "deepseek_main",
                },
            )
            public = json.loads(
                (Path(temp) / CONFIG_FILENAME).read_text(encoding="utf-8")
            )
            spec = public["model_registry"]["deepseek_main"]
            self.assertNotIn("api_key", spec)
            self.assertNotIn("top-secret", json.dumps(public))


if __name__ == "__main__":
    unittest.main()
