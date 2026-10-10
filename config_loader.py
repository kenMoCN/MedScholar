"""Compatibility loader for the setup wizard's legacy ``config.env`` output.

The public ``config`` object contains secret references only.  Secret values are
kept separately so callers do not accidentally serialize or log API keys with
ordinary application settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from model_router import supports_deepseek_flash_vision


class ConfigLoadError(ValueError):
    """Raised when a configuration file cannot be parsed safely."""


@dataclass(frozen=True)
class LoadedConfig:
    """Normalized settings plus separately held secrets and validation issues."""

    config: dict[str, Any]
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    issues: tuple[str, ...] = ()


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigLoadError(f"无法解析布尔值: {value!r}")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the simple KEY=VALUE format emitted by the setup wizard."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigLoadError(f"第 {line_number} 行缺少 '='")

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigLoadError(f"第 {line_number} 行的配置名称为空")
        values[key] = value.strip()
    return values


def read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigLoadError(f"无法读取配置文件: {env_path}") from exc
    return parse_env_text(text)


def _normalized_proficiency(value: str | None) -> str:
    return value if value in {"beginner", "proficient"} else "beginner"


def _normalized_theme(value: str | None) -> str:
    return value if value in {"light", "dark", "system"} else "system"


def normalize_legacy_env(values: Mapping[str, str]) -> LoadedConfig:
    """Convert legacy environment values to the v1 configuration contract."""

    vision_enabled = _parse_bool(
        values.get("ENABLE_IMAGE_RECOGNITION"), default=False
    )
    has_documents = _parse_bool(values.get("HAS_TEXTBOOKS"), default=False)
    cite_location = _parse_bool(
        values.get("CITE_TEXTBOOK_LOCATION"), default=True
    )
    require_approval = _parse_bool(
        values.get("REQUIRE_FILE_OPERATION_APPROVAL"), default=True
    )

    deepseek_model_name = values.get("DEEPSEEK_MODEL_NAME", "").strip()
    doubao_model_name = values.get("DOUBAO_VISION_MODEL_NAME", "").strip()

    deepseek_capabilities = ["text", "reasoning", "structured_output"]
    if supports_deepseek_flash_vision("deepseek", deepseek_model_name):
        deepseek_capabilities.append("vision")

    model_registry: dict[str, dict[str, Any]] = {
        "deepseek_default": {
            "provider": "deepseek",
            "model_name": deepseek_model_name or None,
            "api_base": None,
            "api_protocol": "openai_compatible",
            "api_key_ref": "env:DEEPSEEK_API_KEY",
            "capabilities": deepseek_capabilities,
            "enabled": True,
        },
        "doubao_vision_default": {
            "provider": "doubao",
            "model_name": doubao_model_name or None,
            "api_base": None,
            "api_protocol": "vendor_or_openai_compatible",
            "api_key_ref": "env:DOUBAO_API_KEY",
            "capabilities": ["vision", "ocr", "structured_output"],
            "enabled": vision_enabled,
        },
    }

    model_routes: dict[str, str] = {
        "reasoning": "deepseek_default",
        "planning": "deepseek_default",
        "verification": "deepseek_default",
    }
    if vision_enabled:
        model_routes["vision"] = "doubao_vision_default"

    config = {
        "schema_version": "1.0",
        "generated_by": "legacy_config_env_loader",
        "model_registry": model_registry,
        "model_routes": model_routes,
        "user_defaults": {
            "initial_proficiency_label": _normalized_proficiency(
                values.get("USER_PROFICIENCY")
            ),
            "interaction_style": "auto",
            "answer_depth": "auto",
            "socratic_mode": "auto",
        },
        "document_defaults": {
            "has_documents": has_documents,
            "legacy_textbook_dir": values.get("TEXTBOOK_DIR", "./textbook"),
            "citation_display": "detailed" if cite_location else "hidden",
            "evidence_capture": True,
        },
        "permission_defaults": {
            "require_file_operation_approval": require_approval,
        },
        "ui": {"theme": _normalized_theme(values.get("THEME_MODE"))},
    }

    secrets = {
        name: values[name]
        for name in ("DEEPSEEK_API_KEY", "DOUBAO_API_KEY")
        if values.get(name)
    }

    issues: list[str] = []
    if not deepseek_model_name:
        issues.append("缺少 DEEPSEEK_MODEL_NAME")
    if "DEEPSEEK_API_KEY" not in secrets:
        issues.append("缺少 DEEPSEEK_API_KEY")
    if vision_enabled and not doubao_model_name:
        issues.append("已启用图片识别，但缺少 DOUBAO_VISION_MODEL_NAME")
    if vision_enabled and "DOUBAO_API_KEY" not in secrets:
        issues.append("已启用图片识别，但缺少 DOUBAO_API_KEY")

    return LoadedConfig(config=config, secrets=secrets, issues=tuple(issues))


def load_legacy_config(path: str | Path) -> LoadedConfig:
    """Read and normalize a legacy ``config.env`` file."""

    return normalize_legacy_env(read_env_file(path))
