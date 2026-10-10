"""Capability-aware model registry and role router.

Business code asks for a role (for example ``vision`` or ``reasoning``) and
never depends on a provider brand.  API clients can later be created from the
returned :class:`RoutedModel`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class ModelRegistryError(ValueError):
    """Base error for invalid model registry data."""


class RouteUnavailableError(ModelRegistryError):
    """Raised when no usable model can satisfy a requested role."""


class SecretResolutionError(ModelRegistryError):
    """Raised when a model's credential reference cannot be resolved."""


class ModelRole(str, Enum):
    VISION = "vision"
    REASONING = "reasoning"
    PLANNING = "planning"
    VERIFICATION = "verification"


ROLE_REQUIRED_CAPABILITIES: dict[str, frozenset[str]] = {
    ModelRole.VISION.value: frozenset({"vision"}),
    ModelRole.REASONING.value: frozenset({"text"}),
    ModelRole.PLANNING.value: frozenset({"text"}),
    ModelRole.VERIFICATION.value: frozenset({"text"}),
}


def supports_deepseek_flash_vision(provider: str, model_name: str | None) -> bool:
    """Only the documented Flash model is eligible for automatic vision routing."""

    return (
        provider.strip().lower() == "deepseek"
        and (model_name or "").strip().lower() == "deepseek-flash"
    )


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    provider: str
    model_name: str | None
    api_base: str | None
    api_protocol: str
    api_key_ref: str
    capabilities: frozenset[str]
    enabled: bool = True

    @classmethod
    def from_mapping(cls, model_id: str, data: Mapping[str, Any]) -> "ModelSpec":
        capabilities = data.get("capabilities", ())
        if isinstance(capabilities, str):
            capabilities = (capabilities,)

        return cls(
            model_id=model_id,
            provider=str(data.get("provider", "")).strip(),
            model_name=(str(data["model_name"]).strip() if data.get("model_name") else None),
            api_base=(str(data["api_base"]).strip() if data.get("api_base") else None),
            api_protocol=str(data.get("api_protocol", "")).strip(),
            api_key_ref=str(data.get("api_key_ref", "")).strip(),
            capabilities=frozenset(str(item).strip() for item in capabilities if str(item).strip()),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(frozen=True)
class RoutedModel:
    role: str
    spec: ModelSpec
    api_key: str = field(repr=False)


class ModelRegistry:
    """Resolve models by role with optional session-level route overrides."""

    def __init__(
        self,
        models: Mapping[str, ModelSpec],
        routes: Mapping[str, str],
        secrets: Mapping[str, str] | None = None,
    ) -> None:
        self._models = dict(models)
        self._default_routes = dict(routes)
        self._route_overrides: dict[str, str] = {}
        self._secrets = dict(secrets or {})

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        secrets: Mapping[str, str] | None = None,
    ) -> "ModelRegistry":
        raw_models = config.get("model_registry", {})
        raw_routes = config.get("model_routes", {})
        if not isinstance(raw_models, Mapping):
            raise ModelRegistryError("model_registry 必须是对象")
        if not isinstance(raw_routes, Mapping):
            raise ModelRegistryError("model_routes 必须是对象")

        models = {
            str(model_id): ModelSpec.from_mapping(str(model_id), data)
            for model_id, data in raw_models.items()
            if isinstance(data, Mapping)
        }
        routes = {str(role): str(model_id) for role, model_id in raw_routes.items()}
        return cls(models=models, routes=routes, secrets=secrets)

    @property
    def models(self) -> Mapping[str, ModelSpec]:
        return dict(self._models)

    @property
    def effective_routes(self) -> Mapping[str, str]:
        routes = dict(self._default_routes)
        routes.update(self._route_overrides)
        return routes

    def validate(self) -> tuple[str, ...]:
        issues: list[str] = []
        for model_id, spec in self._models.items():
            if not spec.provider:
                issues.append(f"模型 {model_id} 缺少 provider")
            if spec.enabled and not spec.model_name:
                issues.append(f"模型 {model_id} 已启用但缺少 model_name")
            if spec.enabled and not spec.api_protocol:
                issues.append(f"模型 {model_id} 已启用但缺少 api_protocol")
            if spec.enabled and not spec.api_key_ref:
                issues.append(f"模型 {model_id} 已启用但缺少 api_key_ref")

        for role, model_id in self.effective_routes.items():
            if model_id not in self._models:
                issues.append(f"角色 {role} 指向不存在的模型 {model_id}")
                continue
            spec = self._models[model_id]
            required = ROLE_REQUIRED_CAPABILITIES.get(role, frozenset())
            missing = required - spec.capabilities
            if missing:
                issues.append(
                    f"角色 {role} 的模型 {model_id} 缺少能力: {', '.join(sorted(missing))}"
                )
        return tuple(issues)

    def set_session_route(self, role: str | ModelRole, model_id: str) -> None:
        role_name = _role_name(role)
        self._assert_route_compatible(role_name, model_id)
        self._route_overrides[role_name] = model_id

    def clear_session_route(self, role: str | ModelRole) -> None:
        self._route_overrides.pop(_role_name(role), None)

    def clear_all_session_routes(self) -> None:
        self._route_overrides.clear()

    def available_models(
        self, required_capabilities: Iterable[str] = ()
    ) -> tuple[ModelSpec, ...]:
        required = frozenset(required_capabilities)
        return tuple(
            spec
            for spec in self._models.values()
            if spec.enabled and required.issubset(spec.capabilities)
        )

    def resolve(
        self,
        role: str | ModelRole,
        required_capabilities: Iterable[str] = (),
    ) -> RoutedModel:
        role_name = _role_name(role)
        model_id = self.effective_routes.get(role_name)
        if not model_id:
            raise RouteUnavailableError(f"角色 {role_name} 尚未配置模型")

        requested = set(ROLE_REQUIRED_CAPABILITIES.get(role_name, frozenset()))
        requested.update(required_capabilities)
        spec = self._assert_route_compatible(role_name, model_id, requested)
        api_key = self._resolve_secret(spec.api_key_ref)
        return RoutedModel(role=role_name, spec=spec, api_key=api_key)

    def _assert_route_compatible(
        self,
        role: str,
        model_id: str,
        required_capabilities: Iterable[str] | None = None,
    ) -> ModelSpec:
        spec = self._models.get(model_id)
        if spec is None:
            raise RouteUnavailableError(f"角色 {role} 指向不存在的模型 {model_id}")
        if not spec.enabled:
            raise RouteUnavailableError(f"模型 {model_id} 当前未启用")
        if not spec.model_name:
            raise RouteUnavailableError(f"模型 {model_id} 缺少 model_name")

        required = frozenset(
            required_capabilities
            if required_capabilities is not None
            else ROLE_REQUIRED_CAPABILITIES.get(role, frozenset())
        )
        missing = required - spec.capabilities
        if missing:
            raise RouteUnavailableError(
                f"模型 {model_id} 无法承担角色 {role}，缺少能力: "
                f"{', '.join(sorted(missing))}"
            )
        return spec

    def _resolve_secret(self, reference: str) -> str:
        if not reference:
            raise SecretResolutionError("模型未配置 api_key_ref")
        if ":" not in reference:
            raise SecretResolutionError(f"无法识别密钥引用: {reference}")

        scheme, name = reference.split(":", 1)
        if scheme != "env":
            raise SecretResolutionError(f"暂不支持密钥引用类型: {scheme}")

        value = self._secrets.get(name) or os.environ.get(name)
        if not value:
            raise SecretResolutionError(f"无法解析密钥引用: {reference}")
        return value


def _role_name(role: str | ModelRole) -> str:
    return role.value if isinstance(role, ModelRole) else str(role).strip()
