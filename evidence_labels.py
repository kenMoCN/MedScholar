"""User-facing explanations for research designs and verification metadata."""

from __future__ import annotations

import re
from collections.abc import Iterable


def friendly_evidence_label(
    raw_level: object = "",
    publication_types: Iterable[object] = (),
) -> str:
    """Explain what a study design can support without exposing A-E jargon.

    ``raw_level`` remains accepted because saved conversations may contain the
    legacy labels.  Publication types take precedence when they are available.
    """

    types = " ".join(str(item) for item in publication_types).lower()
    raw = str(raw_level or "").strip()
    combined = f"{types} {raw.lower()}"

    if any(term in combined for term in ("practice guideline", "clinical guideline", "guideline", "consensus", "指南", "共识")):
        return "临床指南或专家共识｜适合了解规范建议，使用时要核对适用人群和版本"
    if any(term in combined for term in ("meta-analysis", "meta analysis", "systematic review", "系统综述", "meta分析", "层级a")):
        return "系统综述或 Meta 分析｜汇总多项研究，适合了解总体研究结果"
    if any(term in combined for term in ("randomized", "randomised", "randomized controlled trial", "随机对照", "层级b")):
        return "随机对照研究｜较适合比较干预措施的效果，但仍要看样本量和研究质量"
    if "clinical trial" in combined or "临床试验" in combined:
        return "临床试验｜用于评估干预措施的效果与安全性，结论需结合试验设计判断"
    if any(term in combined for term in ("cohort", "case-control", "case control", "observational", "队列", "病例对照", "观察性", "层级c")):
        return "观察性研究｜可以提示相关关系，但不能单独证明因果"
    if any(term in combined for term in ("case report", "case series", "病例报告", "病例系列")):
        return "病例报告或病例系列｜适合发现线索，不能代表多数患者"
    if "review" in combined or "叙述性综述" in combined or "层级d" in combined:
        return "叙述性综述｜适合快速了解背景，不等同于系统评价"

    # Match both the full legacy label and shortened forms such as “证据层级E”.
    if re.search(r"(?:证据)?层级\s*e\b", raw, re.IGNORECASE) or "其他研究设计" in raw:
        return "研究类型暂未识别｜可作拓展阅读，不能单独支撑结论"
    return "研究类型暂未识别｜可作拓展阅读，不能单独支撑结论"


def friendly_claim_certainty(value: object) -> str:
    """Translate internal certainty values used by verified synthesis claims."""

    labels = {
        "high": "摘要对这条说法支持较充分",
        "moderate": "摘要支持这条说法，但仍需结合全文",
        "low": "目前只能作为初步线索",
    }
    return labels.get(str(value or "").strip().lower(), "支持程度尚不明确")


def friendly_resource_intro(
    description: object = "",
    raw_level: object = "",
    publication_types: Iterable[object] = (),
    authority_reason: object = "",
    *,
    max_chars: int = 220,
) -> str:
    """Return a short, source-grounded introduction for a resource card."""

    text = re.sub(r"\s+", " ", str(description or "")).strip()
    # Keep raw English abstracts for retrieval verification, but do not present
    # them as though they were a Chinese-facing introduction.
    if text and re.search(r"[\u4e00-\u9fff]", text):
        if len(text) <= max_chars:
            return text
        shortened = text[:max_chars].rstrip(" ,，;；:：")
        return f"{shortened}…"

    authority = re.sub(r"\s+", " ", str(authority_reason or "")).strip()
    design = friendly_evidence_label(raw_level, publication_types)
    kind, _, use = design.partition("｜")
    if authority and authority not in kind:
        return f"这是一份{authority}资料。{use or kind}。"
    return f"这是一份{kind}资料。{use or '可用于进一步了解相关主题'}。"
