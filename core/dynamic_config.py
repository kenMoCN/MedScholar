"""Version-2 dynamic model configuration with separately stored secrets."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.config_loader import ConfigLoadError, LoadedConfig, parse_env_text
from core.model_router import ModelRegistry


CONFIG_FILENAME = "agent_config.json"
SECRETS_FILENAME = "model_secrets.env"
SUPPORTED_PROTOCOLS = {"openai_compatible", "vendor_or_openai_compatible"}
MODEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def dynamic_config_exists(base_dir: str | Path) -> bool:
    return (Path(base_dir) / CONFIG_FILENAME).is_file()


def load_dynamic_config(base_dir: str | Path) -> LoadedConfig:
    base = Path(base_dir)
    config_path = base / CONFIG_FILENAME
    secrets_path = base / SECRETS_FILENAME
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"无法读取动态配置: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigLoadError(f"动态配置不是有效JSON: {config_path}") from exc
    if not isinstance(config, dict):
        raise ConfigLoadError("动态配置根节点必须是对象")

    secrets: dict[str, str] = {}
    if secrets_path.is_file():
        secrets = parse_env_text(secrets_path.read_text(encoding="utf-8-sig"))

    issues = list(ModelRegistry.from_config(config, secrets).validate())
    for model_id, raw in config.get("model_registry", {}).items():
        if not isinstance(raw, Mapping) or not raw.get("enabled", True):
            continue
        reference = str(raw.get("api_key_ref", ""))
        secret_name = reference.split(":", 1)[1] if reference.startswith("env:") else ""
        if secret_name and not (secrets.get(secret_name) or os.environ.get(secret_name)):
            issues.append(f"模型 {model_id} 缺少 API Key")
    return LoadedConfig(
        config=config,
        secrets=secrets,
        issues=tuple(dict.fromkeys(issues)),
    )


def save_dynamic_config(
    base_dir: str | Path,
    *,
    models: Iterable[Mapping[str, Any]],
    routes: Mapping[str, str],
    base_config: Mapping[str, Any] | None = None,
) -> LoadedConfig:
    """Validate and atomically save arbitrary OpenAI-compatible model entries."""

    base = Path(base_dir)
    public_config = deepcopy(dict(base_config or {}))
    registry: dict[str, dict[str, Any]] = {}
    secrets: dict[str, str] = {}

    for raw in models:
        model_id = str(raw.get("model_id", "")).strip().lower()
        if not MODEL_ID_PATTERN.fullmatch(model_id):
            raise ConfigLoadError(
                f"模型ID {model_id!r} 无效；请使用小写字母开头及字母、数字、_、-"
            )
        if model_id in registry:
            raise ConfigLoadError(f"模型ID重复: {model_id}")
        provider = str(raw.get("provider", "")).strip().lower()
        model_name = str(raw.get("model_name", "")).strip()
        api_base = str(raw.get("api_base", "")).strip() or None
        protocol = str(raw.get("api_protocol", "openai_compatible")).strip()
        capabilities = _normalize_capabilities(raw.get("capabilities", ()))
        enabled = bool(raw.get("enabled", True))
        api_key = str(raw.get("api_key", "")).strip()
        if not provider:
            raise ConfigLoadError(f"模型 {model_id} 缺少供应商")
        if enabled and not model_name:
            raise ConfigLoadError(f"模型 {model_id} 缺少模型名称")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ConfigLoadError(f"模型 {model_id} 使用了不支持的协议 {protocol}")
        if provider not in {"deepseek", "doubao"} and enabled and not api_base:
            raise ConfigLoadError(f"自定义供应商模型 {model_id} 必须填写 API 地址")
        if api_key and ("\n" in api_key or "\r" in api_key):
            raise ConfigLoadError(f"模型 {model_id} 的 API Key 包含非法换行")
        secret_name = f"MODEL_KEY_{_secret_suffix(model_id)}"
        registry[model_id] = {
            "provider": provider,
            "model_name": model_name or None,
            "api_base": api_base,
            "api_protocol": protocol,
            "api_key_ref": f"env:{secret_name}",
            "capabilities": sorted(capabilities),
            "enabled": enabled,
        }
        if api_key:
            secrets[secret_name] = api_key

    public_config.update(
        {
            "schema_version": "2.0",
            "generated_by": "dynamic_model_settings",
            "model_registry": registry,
            "model_routes": {str(role): str(model_id) for role, model_id in routes.items() if model_id},
        }
    )
    for required_role in ("reasoning", "verification"):
        routed_model_id = public_config["model_routes"].get(required_role)
        if not routed_model_id:
            raise ConfigLoadError(f"必须为角色 {required_role} 指定模型")
        routed_spec = registry.get(routed_model_id)
        if routed_spec is None:
            raise ConfigLoadError(f"角色 {required_role} 指向不存在的模型 {routed_model_id}")
        if "structured_output" not in routed_spec["capabilities"]:
            raise ConfigLoadError(
                f"角色 {required_role} 的模型必须声明 structured_output 能力"
            )
    candidate = LoadedConfig(public_config, secrets)
    validation = ModelRegistry.from_config(public_config, secrets).validate()
    missing_keys = [
        f"模型 {model_id} 缺少 API Key"
        for model_id, spec in registry.items()
        if spec["enabled"] and spec["api_key_ref"].split(":", 1)[1] not in secrets
    ]
    issues = tuple(dict.fromkeys((*validation, *missing_keys)))
    if issues:
        raise ConfigLoadError("；".join(issues))

    base.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(base / CONFIG_FILENAME, public_config)
    _atomic_write_text(
        base / SECRETS_FILENAME,
        "".join(f"{name}={value}\n" for name, value in sorted(secrets.items())),
    )
    return candidate


def editable_models(loaded: LoadedConfig) -> list[dict[str, Any]]:
    """Convert a loaded registry into UI records while keeping secrets separate."""

    records = []
    for model_id, spec in loaded.config.get("model_registry", {}).items():
        reference = str(spec.get("api_key_ref", ""))
        secret_name = reference.split(":", 1)[1] if reference.startswith("env:") else ""
        records.append(
            {
                "model_id": model_id,
                "provider": spec.get("provider", ""),
                "model_name": spec.get("model_name") or "",
                "api_base": spec.get("api_base") or "",
                "api_protocol": spec.get("api_protocol", "openai_compatible"),
                "capabilities": list(spec.get("capabilities", [])),
                "enabled": bool(spec.get("enabled", True)),
                "api_key": loaded.secrets.get(secret_name, ""),
            }
        )
    return records


def _normalize_capabilities(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = value
    capabilities = {str(item).strip() for item in items if str(item).strip()}
    if not capabilities:
        raise ConfigLoadError("模型至少需要声明一种能力")
    return capabilities


def _secret_suffix(model_id: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", model_id.upper()).strip("_")


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
