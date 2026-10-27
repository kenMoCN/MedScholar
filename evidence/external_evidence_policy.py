"""Authority grading and publication gates for non-course medical evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class AuthorityLevel(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class PublicationDecision(str, Enum):
    ELIGIBLE = "eligible_for_verified_external_answer"
    CANDIDATES_ONLY = "candidates_only"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AuthorityAssessment:
    level: str
    reason: str
    is_guideline_or_consensus: bool = False


@dataclass(frozen=True)
class ExternalPublicationGate:
    decision: str
    may_publish_answer: bool
    reason: str
    qualifying_resource_ids: tuple[str, ...] = ()


def assess_authority(
    *,
    source: str,
    publication_types: Iterable[str] = (),
) -> AuthorityAssessment:
    source_value = str(source).lower()
    type_value = " ".join(str(item).lower() for item in publication_types)
    combined = f"{source_value} {type_value}"
    if "guideline" in combined or "consensus" in combined:
        return AuthorityAssessment("A", "指南或专家共识", True)
    if "systematic review" in combined or "meta-analysis" in combined or "meta analysis" in combined:
        return AuthorityAssessment("A", "系统综述或Meta分析")
    if "randomized" in combined or "randomised" in combined:
        return AuthorityAssessment("B", "随机对照研究")
    if any(term in combined for term in ("cohort", "case-control", "observational")):
        return AuthorityAssessment("C", "观察性研究")
    if "review" in combined:
        return AuthorityAssessment("D", "叙述性综述")
    if source_value.startswith("wikipedia"):
        return AuthorityAssessment("E", "百科术语参考")
    return AuthorityAssessment("E", "其他外部候选来源")


def evaluate_external_publication_gate(
    question_type: str,
    resources: Iterable[Any],
) -> ExternalPublicationGate:
    records = list(resources)
    if not records:
        return ExternalPublicationGate(
            PublicationDecision.BLOCKED.value,
            False,
            "没有可评估的外部资料。",
        )

    usable = [item for item in records if _has_content(item)]
    if question_type == "terminology":
        qualifying = [
            item for item in usable
            if _level(item) in {"A", "B", "C", "D", "E"}
        ]
        if qualifying:
            return ExternalPublicationGate(
                PublicationDecision.ELIGIBLE.value,
                True,
                "术语题已有可回溯的百科或医学来源正文，可进入外部答案核验。",
                tuple(_resource_id(item) for item in qualifying),
            )

    if question_type == "course_fact":
        level_a = [item for item in usable if _level(item) == "A"]
        independent = [
            item for item in usable if _level(item) in {"B", "C", "D"}
        ]
        if level_a:
            return ExternalPublicationGate(
                PublicationDecision.ELIGIBLE.value,
                True,
                "基础医学题已有A级外部证据，可进入外部答案核验。",
                tuple(_resource_id(item) for item in level_a),
            )
        if len({_resource_id(item) for item in independent}) >= 2:
            return ExternalPublicationGate(
                PublicationDecision.ELIGIBLE.value,
                True,
                "基础医学题已有两条独立的B-D级正文证据，可进入外部答案核验。",
                tuple(_resource_id(item) for item in independent),
            )

    if question_type == "clinical":
        clinical_full_texts = [
            item for item in usable
            if (
                bool(getattr(item, "is_guideline_or_consensus", False))
                or bool(getattr(item, "clinical_answer_eligible", False))
            )
            and bool(getattr(item, "has_full_text", False))
        ]
        if clinical_full_texts:
            return ExternalPublicationGate(
                PublicationDecision.ELIGIBLE.value,
                True,
                "临床题已有指南、专家共识或官方处置指引正文，可进入外部答案核验。",
                tuple(_resource_id(item) for item in clinical_full_texts),
            )
        guideline_abstracts = [
            item for item in usable
            if bool(getattr(item, "is_guideline_or_consensus", False))
        ]
        if guideline_abstracts:
            return ExternalPublicationGate(
                PublicationDecision.CANDIDATES_ONLY.value,
                False,
                "已检索到指南或专家共识记录，但目前只有摘要或元数据；临床题必须取得可逐字核验的正式推荐原文。",
            )
        return ExternalPublicationGate(
            PublicationDecision.CANDIDATES_ONLY.value,
            False,
            "临床诊疗题未检索到指南或专家共识正文；普通论文与摘要只能作为候选资料。",
        )

    if question_type == "research":
        return ExternalPublicationGate(
            PublicationDecision.CANDIDATES_ONLY.value,
            False,
            "科研论文用于视野拓展，不替代课程或临床核心答案。",
        )

    return ExternalPublicationGate(
        PublicationDecision.CANDIDATES_ONLY.value,
        False,
        "外部资料尚未达到当前题型的答案发布门槛。",
    )


def _has_content(item: Any) -> bool:
    return bool(
        getattr(item, "has_abstract", False)
        or getattr(item, "has_excerpt", False)
        or getattr(item, "has_full_text", False)
    )


def _level(item: Any) -> str:
    return str(getattr(item, "authority_level", "E"))


def _resource_id(item: Any) -> str:
    return str(getattr(item, "resource_id", ""))
