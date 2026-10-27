"""Detect a challenge to the previous answer without treating it as evidence."""

from __future__ import annotations

import re
from typing import Any


_CHALLENGE_MARKERS = (
    "答错了", "回答错了", "你错了", "你说错了", "选错了", "选项错了",
    "答案不对", "回答不对", "这题不对", "这道题不对", "这题做错了",
    "这道题做错了", "你这题错了", "不应该选", "不应选", "不该选",
    "参考答案是", "正确答案是", "重新核对", "重新复核", "再核对一下",
)


def is_answer_challenge(message: str) -> bool:
    """Require an explicit correction cue; ordinary wrong-answer questions are not challenges."""
    compact = re.sub(r"\s+", "", str(message or "").lower())
    if not compact or len(compact) > 300:
        return False
    if any(marker in compact for marker in _CHALLENGE_MARKERS):
        return True
    return bool(
        ("应该选" in compact or "应选" in compact)
        and final_choice_set(compact)
    )


def is_question_format_correction(message: str) -> bool:
    """Recognize a short correction to the previous question's answer format."""
    compact = re.sub(r"\s+", "", str(message or ""))
    if not compact or len(compact) > 100:
        return False
    return bool(re.search(
        r"(?:第[\d一二三四五六七八九十百零两]+题|这道题|这题|刚才那题)"
        r"(?:其实|应该|应当|本来|是|属于|按|要按|改为|改成|不是|并非)*"
        r"(?:单选题|多选题|单项选择题|多项选择题|不定项选择题|选多项|选一项)",
        compact,
    ))


def previous_answer_text(result: Any) -> str:
    answer = getattr(result, "answer", None)
    direct = str(getattr(answer, "direct_answer", "") or "").strip()
    rendered = str(getattr(result, "rendered_answer", "") or "").strip()
    return (direct or rendered)[:1800]


def previous_evidence_locations(result: Any) -> list[str]:
    bundle = getattr(result, "evidence_bundle", None)
    locations = []
    for item in getattr(bundle, "evidence", []) or []:
        name = str(getattr(item, "original_name", "") or "").strip()
        locator = str(getattr(item, "locator", "") or "").strip()
        if name and locator:
            locations.append(f"{name}：{locator}")
    return list(dict.fromkeys(locations))[:8]


def final_choice_set(text: str) -> frozenset[str] | None:
    """Read a clearly declared final choice set, not letters in the explanation."""
    source = str(text or "")
    match = re.search(
        r"(?<!不)(?:正确选项|参考答案|最终选项|正确答案|答案|应该选|应选|本题选|选项为)"
        r"\s*(?:是|为|：|:)?\s*"
        r"([A-H](?:[\s、，,和及与]*[A-H]){0,7})",
        source,
        re.IGNORECASE,
    )
    if not match:
        return None
    letters = re.findall(r"[A-H]", match.group(1).upper())
    return frozenset(letters) if letters else None


def disputed_option_terms(question: str, old_answer: str, feedback: str) -> list[str]:
    old = final_choice_set(old_answer)
    proposed = final_choice_set(feedback)
    negated = {
        label.upper() for label in re.findall(
            r"(?i)([A-H])\s*(?:项)?\s*(?:不应该选|不应选|不该选|不能选|选错了|不对)",
            feedback,
        )
    }
    if not old and not negated:
        return []
    labels = (old.symmetric_difference(proposed) if old and proposed else negated)
    matches = list(re.finditer(
        r"(?i)(?:^|[\s；;，,：:])([A-H])\s*[\.．、:]\s*",
        question,
    ))
    terms = []
    for index, match in enumerate(matches):
        if match.group(1).upper() not in labels:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question)
        content = question[match.end():end].strip(" \t\r\n；;，,。")
        if content:
            terms.append(content[:50])
    return terms[:3]
