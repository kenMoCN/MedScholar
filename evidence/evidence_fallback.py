"""Policy-controlled fallback when local course evidence is insufficient."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any

from evidence.external_retrievers import PmcOpenAccessRetriever
from evidence.official_evidence import OfficialEvidenceBundleBuilder, OfficialEvidenceItem
from evidence.official_guidelines import OfficialGuidelinePacket, OfficialGuidelineRetriever
from retrieval.retrieval_agent import LayeredRetrievalAgent, RetrievalPacket
from evidence.research_evidence import HybridResearchRetriever, ResearchPacket
from evidence.external_evidence_policy import (
    assess_authority,
    evaluate_external_publication_gate,
)


class EvidencePolicy(str, Enum):
    LOCAL_ONLY = "local_only"
    LOCAL_FIRST = "local_first"
    RESEARCH_DEEP = "research_deep"


class FallbackQuestionType(str, Enum):
    TERMINOLOGY = "terminology"
    COURSE_FACT = "course_fact"
    CLINICAL = "clinical"
    RESEARCH = "research"


class FallbackStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    DISABLED = "disabled"
    CANDIDATES_FOUND = "candidates_found"
    NO_CANDIDATES = "no_candidates"
    FAILED = "failed"


@dataclass(frozen=True)
class FallbackResource:
    source: str
    resource_id: str
    title: str
    url: str
    year: int | None
    authors: tuple[str, ...]
    identifiers: dict[str, str]
    evidence_scope: str = "external_candidate"
    verification_status: str = "not_content_verified"
    may_determine_answer: bool = False
    cross_validated: bool = False
    evidence_level: str | None = None
    has_abstract: bool = False
    has_excerpt: bool = False
    publication_types: tuple[str, ...] = ()
    authority_level: str = "E"
    authority_reason: str = "其他外部候选来源"
    is_guideline_or_consensus: bool = False
    clinical_answer_eligible: bool = False
    has_full_text: bool = False
    full_text_url: str | None = None
    full_text_sha256: str | None = None
    full_text_char_count: int = 0
    description: str = ""


@dataclass
class FallbackOutcome:
    status: str
    policy: str
    question_type: str
    message: str
    resources: list[FallbackResource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    publication_decision: str = "blocked"
    publication_reason: str = ""
    official_evidence: list[OfficialEvidenceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_evidence_policy(value: str | EvidencePolicy) -> EvidencePolicy:
    if isinstance(value, EvidencePolicy):
        return value
    try:
        return EvidencePolicy(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"未知证据策略: {value!r}") from exc


def evidence_policy_options() -> tuple[dict[str, str], ...]:
    return (
        {
            "id": EvidencePolicy.LOCAL_ONLY.value,
            "display_name": "仅课程资料",
            "description": "教材或PPT不足时停止，不访问外部来源。",
        },
        {
            "id": EvidencePolicy.LOCAL_FIRST.value,
            "display_name": "课程优先，外部补证",
            "description": "本地证据不足时检索外部候选资料，但未经核验不发布答案。",
        },
        {
            "id": EvidencePolicy.RESEARCH_DEEP.value,
            "display_name": "深度外部检索",
            "description": "本地不足时优先检索PubMed与OpenAlex候选文献。",
        },
    )


class EvidenceFallbackController:
    """Find traceable external candidates without silently upgrading them to evidence."""

    def __init__(
        self,
        retriever: LayeredRetrievalAgent,
        *,
        research_retriever: HybridResearchRetriever | None = None,
        full_text_retriever: PmcOpenAccessRetriever | None = None,
        official_guideline_retriever: OfficialGuidelineRetriever | None = None,
        official_evidence_builder: OfficialEvidenceBundleBuilder | None = None,
    ) -> None:
        self.retriever = retriever
        self.research_retriever = research_retriever
        self.full_text_retriever = full_text_retriever
        self.official_guideline_retriever = official_guideline_retriever
        self.official_evidence_builder = (
            official_evidence_builder or OfficialEvidenceBundleBuilder()
        )

    def run(
        self,
        query: str,
        *,
        mode: str,
        policy: str | EvidencePolicy,
        environment: str = "cn",
    ) -> FallbackOutcome:
        selected_policy = normalize_evidence_policy(policy)
        question_type = classify_fallback_question(query, mode=mode)
        if selected_policy is EvidencePolicy.LOCAL_ONLY:
            return FallbackOutcome(
                status=FallbackStatus.DISABLED.value,
                policy=selected_policy.value,
                question_type=question_type.value,
                message="当前为仅课程资料模式；本地证据不足，未启动外部检索。",
            )

        retrieval_mode = mode
        official_evidence: list[OfficialEvidenceItem] = []
        if (
            selected_policy is EvidencePolicy.RESEARCH_DEEP
            or question_type in {FallbackQuestionType.CLINICAL, FallbackQuestionType.RESEARCH}
        ):
            retrieval_mode = "research"
        try:
            if (
                question_type in {
                    FallbackQuestionType.CLINICAL,
                    FallbackQuestionType.RESEARCH,
                }
                and self.research_retriever is not None
            ):
                research_packet = self.research_retriever.search(query, limit=10)
                resources, full_text_warnings = self._research_candidate_resources(
                    research_packet
                )
                warnings = [*research_packet.warnings, *full_text_warnings]
                if (
                    question_type is FallbackQuestionType.CLINICAL
                    and self.official_guideline_retriever is not None
                ):
                    official_packet = self.official_guideline_retriever.search(
                        research_packet.plan.english_query,
                        limit=5,
                    )
                    evidence_bundle = self.official_evidence_builder.build(
                        research_packet.plan.english_query,
                        official_packet.records,
                    )
                    official_evidence = evidence_bundle.evidence
                    resources = [
                        *self._official_candidate_resources(official_packet),
                        *resources,
                    ]
                    warnings.extend(official_packet.warnings)
                    warnings.extend(evidence_bundle.warnings)
            else:
                packet = self.retriever.retrieve(
                    query,
                    mode=retrieval_mode,
                    environment=environment,
                    include_local=False,
                    external_fallback=True,
                )
                resources = self._candidate_resources(packet, question_type)
                warnings = list(packet.warnings)
        except Exception as exc:
            return FallbackOutcome(
                status=FallbackStatus.FAILED.value,
                policy=selected_policy.value,
                question_type=question_type.value,
                message="本地证据不足，外部补证检索也未完成；答案继续保持拦截。",
                warnings=[f"外部检索失败: {type(exc).__name__}"],
            )

        if not resources:
            return FallbackOutcome(
                status=FallbackStatus.NO_CANDIDATES.value,
                policy=selected_policy.value,
                question_type=question_type.value,
                message="本地证据不足，且没有找到符合当前来源策略的外部候选资料。",
                warnings=warnings,
            )
        gate = evaluate_external_publication_gate(question_type.value, resources)
        if gate.may_publish_answer:
            qualifying = set(gate.qualifying_resource_ids)
            resources = [
                replace(
                    item,
                    may_determine_answer=item.resource_id in qualifying,
                )
                for item in resources
            ]
        source_names = "、".join(dict.fromkeys(item.source for item in resources))
        return FallbackOutcome(
            status=FallbackStatus.CANDIDATES_FOUND.value,
            policy=selected_policy.value,
            question_type=question_type.value,
            message=(
                f"本地课程证据不足；已找到 {len(resources)} 条外部候选资料"
                f"（{source_names}）。这些资料尚未完成逐条内容与引文核验，"
                f"{gate.reason} 当前仍不直接发布答案。"
            ),
            resources=resources,
            warnings=warnings,
            publication_decision=gate.decision,
            publication_reason=gate.reason,
            official_evidence=official_evidence,
        )

    @staticmethod
    def _candidate_resources(
        packet: RetrievalPacket,
        question_type: FallbackQuestionType,
    ) -> list[FallbackResource]:
        candidates = []
        for item in packet.external_resources:
            # Wikipedia is useful for terminology and basic orientation, but
            # must never become decisive treatment evidence.
            if question_type is FallbackQuestionType.CLINICAL and item.source.startswith("wikipedia"):
                continue
            authority = assess_authority(
                source=item.source,
                publication_types=item.publication_types,
            )
            candidates.append(FallbackResource(
                source=item.source,
                resource_id=item.resource_id,
                title=item.title,
                url=item.url,
                year=item.year,
                authors=tuple(item.authors),
                identifiers=dict(item.identifiers),
                has_excerpt=bool(item.snippet),
                description=item.snippet,
                publication_types=tuple(item.publication_types),
                authority_level=authority.level,
                authority_reason=authority.reason,
                is_guideline_or_consensus=authority.is_guideline_or_consensus,
            ))
        return candidates[:10]

    def _research_candidate_resources(
        self,
        packet: ResearchPacket,
    ) -> tuple[list[FallbackResource], list[str]]:
        resources = []
        warnings = []
        full_text_attempts = 0
        for paper in packet.papers[:10]:
            authority = assess_authority(
                source="+".join(paper.sources),
                publication_types=paper.publication_types,
            )
            identifiers = dict(paper.identifiers)
            if paper.doi:
                identifiers.setdefault("doi", paper.doi)
            full_text = None
            pmcid = identifiers.get("pmc") or identifiers.get("pmcid")
            if (
                authority.is_guideline_or_consensus
                and pmcid
                and self.full_text_retriever is not None
                and full_text_attempts < 2
            ):
                full_text_attempts += 1
                try:
                    full_text = self.full_text_retriever.fetch(pmcid)
                    if full_text is None:
                        warnings.append(
                            f"{paper.paper_id} 的 PMC 记录未提供可复用正文"
                        )
                except Exception as exc:
                    warnings.append(
                        f"{paper.paper_id} 开放全文获取失败: {type(exc).__name__}"
                    )
            resources.append(FallbackResource(
                source="+".join(paper.sources),
                resource_id=paper.paper_id,
                title=paper.title,
                url=paper.url,
                year=paper.year,
                authors=tuple(paper.authors),
                identifiers=identifiers,
                cross_validated=paper.cross_validated,
                evidence_level=paper.evidence_level,
                has_abstract=bool(paper.abstract),
                description=paper.abstract,
                publication_types=tuple(paper.publication_types),
                authority_level=authority.level,
                authority_reason=authority.reason,
                is_guideline_or_consensus=authority.is_guideline_or_consensus,
                has_full_text=full_text is not None,
                full_text_url=full_text.url if full_text else None,
                full_text_sha256=(
                    hashlib.sha256(full_text.full_text.encode("utf-8")).hexdigest()
                    if full_text else None
                ),
                full_text_char_count=len(full_text.full_text) if full_text else 0,
            ))
        return resources, warnings

    @staticmethod
    def _official_candidate_resources(
        packet: OfficialGuidelinePacket,
    ) -> list[FallbackResource]:
        resources = []
        for record in packet.records:
            identifiers = {}
            if record.reference_number:
                identifiers["official_reference"] = record.reference_number
            if record.download_url:
                identifiers["download_url"] = record.download_url
            resources.append(FallbackResource(
                source=record.source,
                resource_id=record.resource_id,
                title=record.title,
                url=record.url,
                year=record.year,
                authors=((record.publisher or record.source.upper()),),
                identifiers=identifiers,
                has_excerpt=bool(record.summary),
                description=record.summary,
                publication_types=(record.publication_type,),
                authority_level="A",
                authority_reason=record.authority_reason,
                is_guideline_or_consensus=record.is_guideline_or_consensus,
                clinical_answer_eligible=record.clinical_answer_eligible,
                has_full_text=bool(record.full_text),
                full_text_url=(
                    record.full_text_url or record.download_url
                    if record.full_text else None
                ),
                full_text_sha256=(
                    hashlib.sha256(record.full_text.encode("utf-8")).hexdigest()
                    if record.full_text else None
                ),
                full_text_char_count=len(record.full_text),
            ))
        return resources


def classify_fallback_question(query: str, *, mode: str) -> FallbackQuestionType:
    compact = "".join(str(query).lower().split())
    if any(
        marker in compact
        for marker in (
            "患者", "病人", "休克", "治疗", "用药", "处理原则", "诊断", "剂量", "手术"
        )
    ):
        return FallbackQuestionType.CLINICAL
    if mode == "research" or any(
        marker in compact for marker in ("论文", "研究进展", "循证", "pubmed", "文献")
    ):
        return FallbackQuestionType.RESEARCH
    if any(marker in compact for marker in ("是什么", "定义", "名词", "全称", "缩写")) or len(compact) <= 12:
        return FallbackQuestionType.TERMINOLOGY
    return FallbackQuestionType.COURSE_FACT
