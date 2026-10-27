"""Stable policy interface for optional evidence-verified medical expansion."""

from __future__ import annotations

from enum import Enum


class MedicalHorizonPolicy(str, Enum):
    OFF = "off"
    RESEARCH_MODE_ONLY = "research_mode_only"
    ALL_ANSWERS = "all_answers"


def normalize_medical_horizon_policy(
    value: str | MedicalHorizonPolicy,
) -> MedicalHorizonPolicy:
    if isinstance(value, MedicalHorizonPolicy):
        return value
    try:
        return MedicalHorizonPolicy(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"未知医学视野拓展策略: {value!r}") from exc


def medical_horizon_policy_options() -> tuple[dict[str, str], ...]:
    return (
        {
            "id": MedicalHorizonPolicy.OFF.value,
            "display_name": "关闭论文拓展",
            "description": "只显示课程答案，不访问论文数据库。",
        },
        {
            "id": MedicalHorizonPolicy.RESEARCH_MODE_ONLY.value,
            "display_name": "仅科研模式拓展",
            "description": "只有切换到科研模式时才检索并核验相关论文。",
        },
        {
            "id": MedicalHorizonPolicy.ALL_ANSWERS.value,
            "display_name": "每次答题均拓展",
            "description": "课程答案通过后，自动追加相关论文与医学视野拓展。",
        },
    )


def should_expand_medical_horizon(
    policy: str | MedicalHorizonPolicy,
    *,
    mode: str,
) -> bool:
    normalized = normalize_medical_horizon_policy(policy)
    if normalized is MedicalHorizonPolicy.OFF:
        return False
    if normalized is MedicalHorizonPolicy.ALL_ANSWERS:
        return True
    return mode == "research"

