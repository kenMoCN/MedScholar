"""Stable interaction-mode contract shared by the agent core and future UIs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class InteractionMode(str, Enum):
    AUTO = "auto"
    QUICK = "quick"
    GUIDED = "guided"
    SOCRATIC = "socratic"
    RESEARCH = "research"


@dataclass(frozen=True)
class ModeDefinition:
    mode: InteractionMode
    display_name: str
    description: str
    supports_direct_answer: bool
    asks_guiding_questions: bool
    enables_research_extension: bool

    def to_ui_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["id"] = self.mode.value
        del data["mode"]
        return data


MODE_DEFINITIONS: tuple[ModeDefinition, ...] = (
    ModeDefinition(
        InteractionMode.AUTO,
        "智能选择",
        "根据问题内容和用户基础自动选择答疑方式。",
        True,
        True,
        True,
    ),
    ModeDefinition(
        InteractionMode.QUICK,
        "快速答疑",
        "先给结论，再给精炼的课程证据与解释。",
        True,
        False,
        False,
    ),
    ModeDefinition(
        InteractionMode.GUIDED,
        "引导学习",
        "在完整解释后给出帮助理解和复习的提示。",
        True,
        True,
        False,
    ),
    ModeDefinition(
        InteractionMode.SOCRATIC,
        "苏格拉底式",
        "优先用递进问题帮助学生自行定位思路。",
        False,
        True,
        False,
    ),
    ModeDefinition(
        InteractionMode.RESEARCH,
        "科研拓展",
        "完成课程答疑后，保留论文检索和研究延伸接口。",
        True,
        True,
        True,
    ),
)


def normalize_mode(
    value: str | InteractionMode,
    *,
    allow_auto: bool = True,
) -> InteractionMode:
    if isinstance(value, InteractionMode):
        mode = value
    else:
        try:
            mode = InteractionMode(str(value).strip().lower())
        except ValueError as exc:
            supported = ", ".join(item.value for item in InteractionMode)
            raise ValueError(f"未知交互模式 {value!r}；可选值: {supported}") from exc
    if not allow_auto and mode is InteractionMode.AUTO:
        raise ValueError("此处不能使用 auto 模式")
    return mode


def mode_options(*, include_auto: bool = True) -> tuple[dict[str, Any], ...]:
    """Return serialization-safe records suitable for a PySide model."""

    return tuple(
        definition.to_ui_dict()
        for definition in MODE_DEFINITIONS
        if include_auto or definition.mode is not InteractionMode.AUTO
    )
