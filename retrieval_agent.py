"""Layered retrieval routing for course, terminology, and research requests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Any

from external_retrievers import (
    ExternalResource,
    OpenAlexRetriever,
    PubMedRetriever,
    WikipediaRetriever,
)
from structure_retriever import RetrievalResult, StructureAwareRetriever


class RetrievalIntent(str, Enum):
    COURSE = "course"
    TERMINOLOGY = "terminology"
    RESEARCH = "research"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class RetrievalPlan:
    intent: str
    query: str
    use_local: bool
    wikipedia_languages: tuple[str, ...]
    use_pubmed: bool
    use_openalex: bool


@dataclass
class RetrievalPacket:
    plan: RetrievalPlan
    local_result: RetrievalResult | None = None
    external_resources: list[ExternalResource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetrievalPlanner:
    def plan(self, query: str, *, mode: str, environment: str = "cn") -> RetrievalPlan:
        compact = "".join(query.split())
        research_markers = ("论文", "研究进展", "最新研究", "循证", "pubmed", "文献")
        term_markers = ("是什么", "定义", "名词", "英文", "全称", "缩写")
        wants_research = mode == "research" or any(item in compact.lower() for item in research_markers)
        explicit_term = any(item in compact.lower() for item in term_markers)
        wants_term = explicit_term or (len(compact) <= 12 and not wants_research)
        if wants_research and wants_term:
            intent = RetrievalIntent.HYBRID
        elif wants_research:
            intent = RetrievalIntent.RESEARCH
        elif wants_term:
            intent = RetrievalIntent.TERMINOLOGY
        else:
            intent = RetrievalIntent.COURSE
        languages = ("zh", "en") if environment.lower() in {"cn", "zh", "china"} else ("en",)
        return RetrievalPlan(
            intent=intent.value,
            query=query,
            use_local=True,
            wikipedia_languages=languages if wants_term else (),
            use_pubmed=wants_research,
            use_openalex=wants_research,
        )


class LayeredRetrievalAgent:
    def __init__(
        self,
        *,
        local_retriever: StructureAwareRetriever,
        wikipedia: WikipediaRetriever | None = None,
        pubmed: PubMedRetriever | None = None,
        openalex: OpenAlexRetriever | None = None,
        planner: RetrievalPlanner | None = None,
    ) -> None:
        self.local_retriever = local_retriever
        self.wikipedia = wikipedia or WikipediaRetriever()
        self.pubmed = pubmed or PubMedRetriever()
        self.openalex = openalex or OpenAlexRetriever()
        self.planner = planner or RetrievalPlanner()

    def retrieve(
        self,
        query: str,
        *,
        mode: str = "auto",
        environment: str = "cn",
        include_local: bool = True,
        external_fallback: bool = False,
    ) -> RetrievalPacket:
        plan = self.planner.plan(query, mode=mode, environment=environment)
        if external_fallback and not (
            plan.wikipedia_languages or plan.use_pubmed or plan.use_openalex
        ):
            languages = (
                ("zh", "en")
                if environment.lower() in {"cn", "zh", "china"}
                else ("en",)
            )
            plan = RetrievalPlan(
                intent=RetrievalIntent.HYBRID.value,
                query=plan.query,
                use_local=plan.use_local,
                wikipedia_languages=languages,
                use_pubmed=False,
                use_openalex=False,
            )
        packet = RetrievalPacket(plan=plan)
        if include_local and plan.use_local:
            packet.local_result = self.local_retriever.search(query)

        for language in plan.wikipedia_languages:
            try:
                results = self.wikipedia.search(query, language=language, limit=2)
                packet.external_resources.extend(results)
                if results:
                    break
            except Exception as exc:
                packet.warnings.append(f"Wikipedia({language})检索失败: {type(exc).__name__}")
        paper_tasks = []
        if plan.use_pubmed:
            paper_tasks.append(("PubMed", self.pubmed.search))
        if plan.use_openalex:
            paper_tasks.append(("OpenAlex", self.openalex.search))
        if paper_tasks:
            with ThreadPoolExecutor(max_workers=len(paper_tasks)) as executor:
                futures = {
                    executor.submit(search, query, limit=5): label
                    for label, search in paper_tasks
                }
                for future in as_completed(futures):
                    label = futures[future]
                    try:
                        packet.external_resources.extend(future.result())
                    except Exception as exc:
                        packet.warnings.append(
                            f"{label}检索失败: {type(exc).__name__}"
                        )
        packet.external_resources = _deduplicate(packet.external_resources)
        return packet


def render_external_resources(packet: RetrievalPacket) -> str:
    if not packet.external_resources and not packet.warnings:
        return ""
    lines = ["延伸检索（外部资料，未作为课程答案证据）："]
    for resource in packet.external_resources[:8]:
        year = f" ({resource.year})" if resource.year else ""
        lines.append(f"- [{resource.source}] {resource.title}{year} — {resource.url}")
    for warning in packet.warnings:
        lines.append(f"- 检索提示：{warning}")
    return "\n".join(lines)


def _deduplicate(resources: list[ExternalResource]) -> list[ExternalResource]:
    result = []
    seen = set()
    for resource in resources:
        key = resource.identifiers.get("doi") or resource.url or resource.title.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(resource)
    return result
