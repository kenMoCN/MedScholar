"""Conversation-aware learning intent and follow-up action contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Sequence


class LearningIntent(str, Enum):
    QUESTION = "question"
    REEXPLAIN = "reexplain"
    EXAMPLE = "example"
    VISUAL = "visual"
    COURSE = "course"
    VIDEO = "video"
    RESEARCH = "research"
    PRACTICE = "practice"
    UNDERSTOOD = "understood"


@dataclass(frozen=True)
class LearningTurn:
    intent: LearningIntent
    topic_query: str
    effective_query: str
    inherited_context: bool = False


_INTENT_MARKERS: tuple[tuple[LearningIntent, tuple[str, ...]], ...] = (
    # Negative feedback and concrete learning requests must win over phrases
    # such as “我以为懂了” or “明白了，请再出一道题”.
    (
        LearningIntent.REEXPLAIN,
        ("不懂", "没懂", "没听懂", "没看懂", "还是不明白", "换种讲法", "重新解释"),
    ),
    (LearningIntent.VIDEO, ("视频", "b站", "哔哩哔哩", "公开课")),
    (LearningIntent.RESEARCH, ("论文", "文献", "研究进展", "科研拓展")),
    (LearningIntent.VISUAL, ("画图", "流程图", "图示", "示意图")),
    (LearningIntent.COURSE, ("回到课件", "课件位置", "教材位置", "哪一页")),
    (LearningIntent.EXAMPLE, ("举例", "例子", "病例解释", "打个比方")),
    (LearningIntent.PRACTICE, ("出题", "练习", "测测我", "小测")),
    (LearningIntent.UNDERSTOOD, ("懂了", "明白了", "会了", "理解了")),
)

_FOLLOWUP_INSTRUCTIONS = {
    LearningIntent.REEXPLAIN: (
        "请像一位耐心、亲切的学习伙伴一样重新讲解，使用简单易懂的词和短句，"
        "分步骤说明，并配一个直观类比；不要照抄上一版。"
    ),
    LearningIntent.EXAMPLE: (
        "用户已经看过上一轮题目解析，本轮只需要一个新的、具体的例子。"
        "用两三句话讲清例子里发生了什么，再用一句话点明它对应的原理；"
        "不要重新报告选项答案，不要逐项解析原题，也不要复述整段上一轮解释。"
    ),
    LearningIntent.VISUAL: "用户希望改用可视化的步骤、流程或结构关系来理解。",
    LearningIntent.COURSE: "用户希望回到原课程材料定位并复习这个知识点。",
    LearningIntent.VIDEO: "用户希望围绕原问题寻找视频学习资源。",
    LearningIntent.RESEARCH: "用户希望围绕原问题继续了解论文或研究进展。",
    LearningIntent.PRACTICE: (
        "用户已经看过原题解析，本轮只需围绕相同知识点出一道新的、低负担的练习题。"
        "不要重答原题、逐项解析原选项或提前公布新题答案；请等用户作答后再核对。"
    ),
    LearningIntent.UNDERSTOOD: "用户表示已经理解，不需要重复讲解。",
}


def analyze_learning_turn(
    query: str,
    conversation_context: Sequence[Mapping[str, Any]] | None = None,
    *,
    current_topic: str | None = None,
) -> LearningTurn:
    """Resolve short learning feedback against the previous conversation topic."""

    clean_query = str(query or "").strip()
    compact = "".join(clean_query.lower().split())
    intent = LearningIntent.QUESTION
    for candidate, markers in _INTENT_MARKERS:
        if any(marker in compact for marker in markers):
            intent = candidate
            break
    if intent is LearningIntent.UNDERSTOOD and not _is_pure_understood(compact):
        intent = LearningIntent.QUESTION

    explicit_topic = _explicit_followup_topic(clean_query, intent)
    prior_user = (
        str(current_topic or "").strip()
        or _latest_topic_message(conversation_context)
    )
    prior_assistant = _latest_message(conversation_context, "assistant")
    dialogue = _recent_dialogue_context(conversation_context)
    if (
        intent is LearningIntent.QUESTION
        and prior_user
        and _is_referential_question(compact)
    ):
        effective = (
            f"原题或学习主题：{prior_user[:3000]}\n"
            f"近期对话（只帮助理解指代，不是医学证据）：\n{dialogue}\n"
            f"上一轮回答摘要：{prior_assistant[:1000]}\n"
            f"本轮追问：{clean_query}\n"
            "请针对本轮追问回答；上一轮回答仅作上下文，不作为事实依据。"
        )
        return LearningTurn(intent, prior_user[:3000], effective, True)
    contextual_intents = {
        LearningIntent.REEXPLAIN,
        LearningIntent.EXAMPLE,
        LearningIntent.VISUAL,
        LearningIntent.COURSE,
        LearningIntent.VIDEO,
        LearningIntent.RESEARCH,
        LearningIntent.PRACTICE,
        LearningIntent.UNDERSTOOD,
    }
    short_reference = len(compact) <= 18 or any(
        token in compact for token in ("这个", "刚才", "上面", "还是")
    )
    inherit = bool(
        prior_user
        and intent in contextual_intents
        and short_reference
        and not explicit_topic
    )
    if not inherit:
        topic = explicit_topic or clean_query
        return LearningTurn(intent, topic, clean_query, False)

    instruction = _FOLLOWUP_INSTRUCTIONS[intent]
    previous = (
        f"\n上一版解释摘要：{prior_assistant[:600]}"
        if prior_assistant and intent is LearningIntent.REEXPLAIN
        else ""
    )
    effective = (
        f"原学习主题：{prior_user[:3000]}\n"
        f"近期对话（只帮助理解指代，不是医学证据）：\n{dialogue}\n"
        f"本轮反馈：{clean_query}\n教学要求：{instruction}{previous}"
    )
    return LearningTurn(intent, prior_user[:3000], effective, True)


def learning_turn_from_action(
    action_id: str,
    topic_query: str,
    *,
    display_prompt: str = "",
    conversation_context: Sequence[Mapping[str, Any]] | None = None,
) -> LearningTurn:
    """Build a follow-up from structured UI data, without parsing button text."""

    try:
        intent = LearningIntent(str(action_id).strip())
    except ValueError as exc:
        raise ValueError(f"未知学习动作: {action_id!r}") from exc
    if intent not in _FOLLOWUP_INSTRUCTIONS or intent is LearningIntent.UNDERSTOOD:
        raise ValueError(f"不支持的学习动作: {action_id!r}")
    topic = str(topic_query).strip()
    if not topic:
        raise ValueError("学习动作缺少当前知识点")
    feedback = str(display_prompt).strip() or intent.value
    effective = (
        f"原学习主题：{topic[:3000]}\n"
        f"近期对话（只帮助理解指代，不是医学证据）：\n"
        f"{_recent_dialogue_context(conversation_context)}\n"
        f"本轮反馈：{feedback}\n教学要求：{_FOLLOWUP_INSTRUCTIONS[intent]}"
    )
    return LearningTurn(intent, topic[:3000], effective, True)


def learning_action_options(
    intent: str | LearningIntent,
    *,
    completed: bool,
    followup: bool = False,
) -> list[dict[str, str]]:
    """Return stable action records for desktop and future frontends."""

    normalized = LearningIntent(intent)
    if not completed or normalized in {LearningIntent.UNDERSTOOD, LearningIntent.PRACTICE}:
        return []
    actions = [
        ("reexplain", "换一种讲法", "这个我还是不太懂，请换一种更直观的方式解释。"),
        ("example", "举个例子", "请用一个贴合这个知识点的例子解释。"),
        ("course", "回到课件", "请带我回到课件位置，定位并复习这个知识点。"),
        ("practice", "出题检验", "请围绕这个知识点出一道小题检验我是否理解。"),
        ("video", "找视频", "请帮我找适合学习这个知识点的视频。"),
        ("research", "找论文拓展", "请帮我找与这个知识点相关、适合入门拓展的论文。"),
    ]
    if normalized is LearningIntent.REEXPLAIN:
        actions.insert(2, ("visual", "用图或流程", "请把这个知识点整理成图示或流程。"))
    if followup:
        # A blue-chip follow-up is already an action on the original answer;
        # do not repeat the full six-action toolbar under every auxiliary turn.
        actions = [action for action in actions if action[0] == "reexplain"]
    return [
        {"id": action_id, "label": label, "prompt": prompt}
        for action_id, label, prompt in actions
    ]


def _latest_message(
    context: Sequence[Mapping[str, Any]] | None,
    role: str,
) -> str:
    for item in reversed(tuple(context or ())):
        if str(item.get("role", "")).strip() != role:
            continue
        body = str(item.get("body", "")).strip()
        if body:
            return body
    return ""


def _recent_dialogue_context(
    context: Sequence[Mapping[str, Any]] | None,
) -> str:
    """A small, role-marked transcript for resolving references, not evidence."""
    entries = []
    budget = 1800
    for item in reversed(tuple(context or ())[-8:]):
        role = str(item.get("role", "")).strip()
        body = str(item.get("body", "")).strip()
        if role not in {"user", "assistant"} or not body:
            continue
        if role == "user" and body.startswith("[图片题目]"):
            continue
        label = "用户" if role == "user" else "学习伙伴"
        excerpt = body[:350 if role == "user" else 550]
        line = f"{label}：{excerpt}"
        if len(line) > budget:
            line = line[:budget]
        entries.append(line)
        budget -= len(line)
        if budget <= 0 or len(entries) >= 6:
            break
    return "\n".join(reversed(entries)) or "（无可用的近期文字对话）"


def _latest_topic_message(
    context: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Return the last substantive user topic, skipping action-only follow-ups."""

    for item in reversed(tuple(context or ())):
        if str(item.get("role", "")).strip() == "assistant":
            saved_topic = str(item.get("question_context", "")).strip()
            if saved_topic:
                return saved_topic
        if str(item.get("role", "")).strip() != "user":
            continue
        body = str(item.get("body", "")).strip()
        if not body:
            continue
        compact = "".join(body.lower().split())
        intent = LearningIntent.QUESTION
        for candidate, markers in _INTENT_MARKERS:
            if any(marker in compact for marker in markers):
                intent = candidate
                break
        explicit = _explicit_followup_topic(body, intent)
        if explicit:
            return explicit
        if intent is LearningIntent.QUESTION:
            if body.startswith("[图片题目]"):
                continue
            return body
    return ""


def _is_referential_question(compact: str) -> bool:
    if not compact or len(compact) > 55:
        return False
    return bool(re.search(
        r"(?:这道题|这题|刚才|上面|前面|上一题|第[\d一二三四五六七八九十百零两]+题|"
        r"为什么选|为什么不选|哪个选项|那.+呢|这个选项|原图|这张图|图片)",
        compact,
    ))


def _is_pure_understood(compact: str) -> bool:
    remainder = compact
    for marker in ("懂了", "明白了", "会了", "理解了"):
        remainder = remainder.replace(marker, "")
    for token in ("我", "现在", "已经", "终于", "谢谢", "感谢", "好的", "好", "嗯"):
        remainder = remainder.replace(token, "")
    remainder = re.sub(r"[，。！？,.!?~～\s]", "", remainder)
    return not remainder


def _explicit_followup_topic(query: str, intent: LearningIntent) -> str:
    if intent in {LearningIntent.QUESTION, LearningIntent.UNDERSTOOD}:
        return ""
    remainder = str(query).strip().lower()
    markers = next(
        (values for candidate, values in _INTENT_MARKERS if candidate is intent),
        (),
    )
    for marker in markers:
        remainder = remainder.replace(marker, "")
    generic = (
        "请帮我", "帮我", "能不能", "可不可以", "可以", "有没有", "请", "再",
        "找", "看看", "看", "适合", "相关", "关于", "围绕", "学习", "资源",
        "一道", "一题", "小题", "练习题", "题目", "这个知识点", "这个", "刚才",
        "上面", "一下", "一个", "一些", "我想", "我要", "给我", "带我", "举个",
        "懂了", "明白了", "会了", "理解了", "还是", "以为", "我", "但", "却",
        "了", "举", "个", "出", "题", "的",
    )
    for token in generic:
        remainder = remainder.replace(token, "")
    remainder = re.sub(r"[，。！？、,.!?：:；;（）()\s]", "", remainder)
    return remainder if len(remainder) >= 2 else ""
