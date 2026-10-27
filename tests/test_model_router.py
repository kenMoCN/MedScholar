import unittest

from core.config_loader import normalize_legacy_env
from core.model_router import (
    ModelRegistry,
    ModelRole,
    RouteUnavailableError,
    SecretResolutionError,
)


class ModelRouterTests(unittest.TestCase):
    def _loaded_config(self, vision: bool = True):
        values = {
            "ENABLE_IMAGE_RECOGNITION": "true" if vision else "false",
            "DEEPSEEK_MODEL_NAME": "deepseek-example",
            "DEEPSEEK_API_KEY": "secret-ds",
            "USER_PROFICIENCY": "beginner",
        }
        if vision:
            values.update(
                {
                    "DOUBAO_VISION_MODEL_NAME": "doubao-vision-example",
                    "DOUBAO_API_KEY": "secret-db",
                }
            )
        return normalize_legacy_env(values)

    def test_default_roles_are_resolved_by_capability(self):
        loaded = self._loaded_config()
        registry = ModelRegistry.from_config(loaded.config, loaded.secrets)

        vision = registry.resolve(ModelRole.VISION)
        reasoning = registry.resolve(ModelRole.REASONING)

        self.assertEqual(vision.spec.provider, "doubao")
        self.assertIn("vision", vision.spec.capabilities)
        self.assertEqual(reasoning.spec.provider, "deepseek")
        self.assertIn("text", reasoning.spec.capabilities)
        self.assertNotIn("secret-ds", repr(reasoning))

    def test_vision_disabled_has_no_vision_route(self):
        loaded = self._loaded_config(vision=False)
        registry = ModelRegistry.from_config(loaded.config, loaded.secrets)

        with self.assertRaises(RouteUnavailableError):
            registry.resolve(ModelRole.VISION)

    def test_capability_mismatch_is_rejected(self):
        loaded = self._loaded_config()
        registry = ModelRegistry.from_config(loaded.config, loaded.secrets)

        with self.assertRaises(RouteUnavailableError):
            registry.set_session_route(ModelRole.VISION, "deepseek_default")

    def test_session_override_can_be_set_and_cleared(self):
        loaded = self._loaded_config()
        config = loaded.config
        config["model_registry"]["backup_reasoner"] = {
            "provider": "custom",
            "model_name": "backup-model",
            "api_base": "https://example.invalid/v1",
            "api_protocol": "openai_compatible",
            "api_key_ref": "env:BACKUP_API_KEY",
            "capabilities": ["text", "reasoning"],
            "enabled": True,
        }
        secrets = {**loaded.secrets, "BACKUP_API_KEY": "secret-backup"}
        registry = ModelRegistry.from_config(config, secrets)

        registry.set_session_route(ModelRole.REASONING, "backup_reasoner")
        self.assertEqual(registry.resolve(ModelRole.REASONING).spec.model_id, "backup_reasoner")

        registry.clear_session_route(ModelRole.REASONING)
        self.assertEqual(registry.resolve(ModelRole.REASONING).spec.model_id, "deepseek_default")

    def test_missing_secret_is_reported_without_exposing_values(self):
        loaded = self._loaded_config()
        registry = ModelRegistry.from_config(loaded.config, secrets={})

        with self.assertRaises(SecretResolutionError) as context:
            registry.resolve(ModelRole.REASONING)
        self.assertNotIn("secret-ds", str(context.exception))

    def test_registry_validation_detects_invalid_route(self):
        loaded = self._loaded_config()
        loaded.config["model_routes"]["reasoning"] = "missing-model"
        registry = ModelRegistry.from_config(loaded.config, loaded.secrets)

        self.assertIn(
            "角色 reasoning 指向不存在的模型 missing-model",
            registry.validate(),
        )


if __name__ == "__main__":
    unittest.main()
