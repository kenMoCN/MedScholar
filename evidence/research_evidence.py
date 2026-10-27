"""Bilingual research planning, cross-source paper fusion, and claim verification."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping, Protocol

from evidence.evidence_labels import friendly_claim_certainty, friendly_evidence_label
from evidence.external_retrievers import ExternalResource, OpenAlexRetriever, PubMedRetriever


class JsonGenerator(Protocol):
    def complete_json(self, system_instruction: str, payload: Mapping[str, Any]): ...


class JsonVerifier(Protocol):
    def verify_json(self, request: Mapping[str, Any]): ...


@dataclass(frozen=True)
class ResearchSearchPlan:
    original_query: str
    chinese_query: str
    english_query: str
    keywords: tuple[str, ...]
    year_from: int | None
    year_to: int | None
    study_types: tuple[str, ...]


@dataclass(frozen=True)
class ResearchFilters:
    """Optional UI/API overrides applied after automatic query planning."""

    year_from: int | None = None
    year_to: int | None = None
    study_types: tuple[str, ...] = ()


@dataclass
class ResearchPaper:
    paper_id: str
    title: str
    year: int | None
    authors: tuple[str, ...]
    abstract: str
    url: str
    doi: str | None
    sources: tuple[str, ...]
    cross_validated: bool
    publication_types: tuple[str, ...]
    evidence_level: str
    relevance_score: float
    cited_by_count: int = 0
    identifiers: dict[str, str] = field(default_factory=dict)


@dataclass
class ResearchPacket:
    plan: ResearchSearchPlan
    papers: list[ResearchPaper] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ResearchClaim:
    claim_id: str
    text: str
    paper_ids: list[str]
    quotes: list[dict[str, str]]
    certainty: str


@dataclass
class ResearchIntroduction:
    intro_id: str
    paper_id: str
    text: str
    quote: str


@dataclass
class ResearchSynthesis:
    status: str
    overview: str
    claims: list[ResearchClaim]
    limitations: list[str]
    errors: list[str] = field(default_factory=list)
    introductions: list[ResearchIntroduction] = field(default_factory=list)


class ResearchQueryPlanner:
    def __init__(self, client: JsonGenerator | None = None) -> None:
        self.client = client

    def plan(self, query: str) -> ResearchSearchPlan:
        current_year = datetime.now().year
        if self.client is None:
            recent = any(marker in query for marker in ("最新", "近年", "研究进展"))
            return ResearchSearchPlan(
                query, query, query, tuple(_terms(query)),
                current_year - 5 if recent else None, current_year, (),
            )
        try:
            raw = self.client.complete_json(
                (
                    "你是医学文献检索式规划器。只做查询改写，不回答医学问题。"
                    "english_query必须是适合PubMed/OpenAlex的简洁英文检索式，只保留3到6个核心医学概念，"
                    "不得复述问题、答案选项或加入推荐答案。keywords只返回4到10个英文概念词。输出JSON。"
                ),
                {
                    "query": query,
                    "current_year": current_year,
                    "required_fields": [
                        "chinese_query", "english_query", "keywords",
                        "year_from", "year_to", "study_types",
                    ],
                },
            )
            if not isinstance(raw, Mapping):
                raise ValueError("文献检索规划响应不是对象")
        except Exception:
            # Planning is useful but cannot be a single point of failure.
            return ResearchQueryPlanner().plan(query)
        raw_keywords = [
            str(item).strip().lower()
            for item in raw.get("keywords", [])
            if str(item).strip()
        ]
        english_keywords = [
            item for item in raw_keywords if re.search(r"[a-z]", item)
        ]
        keywords = english_keywords or raw_keywords
        return ResearchSearchPlan(
            original_query=query,
            chinese_query=_text(raw.get("chinese_query"), query),
            english_query=_text(raw.get("english_query"), query),
            keywords=tuple(dict.fromkeys(keywords[:10])),
            year_from=_year(raw.get("year_from")),
            year_to=_year(raw.get("year_to")) or current_year,
            study_types=tuple(str(item).strip() for item in raw.get("study_types", []) if str(item).strip()),
        )


class HybridResearchRetriever:
    def __init__(
        self,
        *,
        pubmed: PubMedRetriever | None = None,
        openalex: OpenAlexRetriever | None = None,
        planner: ResearchQueryPlanner | None = None,
    ) -> None:
        self.pubmed = pubmed or PubMedRetriever()
        self.openalex = openalex or OpenAlexRetriever()
        self.planner = planner or ResearchQueryPlanner()

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        filters: ResearchFilters | None = None,
        progress_callback: Callable[[str, str, str], None] | None = None,
    ) -> ResearchPacket:
        _notify(progress_callback, "research_planning", "正在生成中英文检索式", "running")
        plan = self.planner.plan(query)
        if filters is not None:
            plan = replace(
                plan,
                year_from=(filters.year_from if filters.year_from is not None else plan.year_from),
                year_to=(filters.year_to if filters.year_to is not None else plan.year_to),
                study_types=(filters.study_types or plan.study_types),
            )
            if plan.year_from and plan.year_to and plan.year_from > plan.year_to:
                raise ValueError("科研检索起始年份不能晚于结束年份")
        _notify(progress_callback, "research_planning", "中英文检索式与筛选条件已确定", "ok")
        packet = ResearchPacket(plan)
        database_query = _database_query(plan)
        pubmed_query = _pubmed_query(database_query, plan.study_types)
        calls = {
            "pubmed": lambda: self.pubmed.search(
                pubmed_query, limit=limit, year_from=plan.year_from,
                year_to=plan.year_to, include_abstracts=True,
            ),
            "openalex": lambda: self.openalex.search(
                database_query, limit=limit, year_from=plan.year_from,
                year_to=plan.year_to,
            ),
        }
        records: list[ExternalResource] = []
        successful_sources = 0
        available_sources: set[str] = set()
        _notify(progress_callback, "paper_search", "正在并行检索 PubMed 与 OpenAlex", "running")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(call): name for name, call in calls.items()}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    source_records = future.result()
                    records.extend(source_records)
                    successful_sources += 1
                    available_sources.add(source)
                    _notify(
                        progress_callback,
                        f"{source}_search",
                        f"{source} 返回 {len(source_records)} 条候选记录",
                        "ok",
                    )
                except Exception as exc:
                    packet.warnings.append(f"{source}检索失败: {type(exc).__name__}")
                    _notify(
                        progress_callback,
                        f"{source}_search",
                        f"{source} 检索失败，继续使用另一数据源",
                        "warning",
                    )
        _notify(
            progress_callback,
            "paper_search",
            "双数据库检索阶段已结束",
            "ok" if successful_sources else "warning",
        )
        _notify(progress_callback, "cross_source_merge", "正在按 DOI、标题和年份交叉匹配", "running")
        packet.papers = _merge_and_rank(records, plan, limit)
        if not packet.papers and plan.keywords and available_sources:
            broad_query = _broader_query(plan, database_query)
            if broad_query:
                retry_year_from = (
                    filters.year_from if filters is not None else None
                )
                retry_types = (
                    filters.study_types if filters is not None else ()
                )
                _notify(
                    progress_callback, "paper_search_retry",
                    "首次检索未得到相关论文，正用核心概念放宽检索一次", "running",
                )
                broad_calls = {
                    "pubmed": lambda: self.pubmed.search(
                        _pubmed_query(broad_query, retry_types),
                        limit=limit, year_from=retry_year_from,
                        year_to=plan.year_to, include_abstracts=True,
                    ),
                    "openalex": lambda: self.openalex.search(
                        broad_query, limit=limit, year_from=retry_year_from,
                        year_to=plan.year_to,
                    ),
                }
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {
                        executor.submit(call): name
                        for name, call in broad_calls.items()
                        if name in available_sources
                    }
                    for future in as_completed(futures):
                        try:
                            records.extend(future.result())
                        except Exception as exc:
                            packet.warnings.append(
                                f"{futures[future]}宽检索失败: {type(exc).__name__}"
                            )
                packet.papers = _merge_and_rank(records, plan, limit, relaxed=True)
                if packet.papers:
                    packet.warnings.append("首次精确检索无结果，已用核心概念放宽检索；请核对论文与问题的关联。")
                _notify(
                    progress_callback, "paper_search_retry",
                    f"放宽检索后找到 {len(packet.papers)} 篇候选论文",
                    "ok" if packet.papers else "warning",
                )
        matched = sum(paper.cross_validated for paper in packet.papers)
        _notify(
            progress_callback,
            "cross_source_merge",
            f"完成去重与相关性排序：{len(packet.papers)} 篇，其中 {matched} 篇在多个数据库中记录一致",
            "ok",
        )
        if not packet.papers:
            packet.warnings.append("没有找到满足相关性阈值的论文")
        return packet


class ResearchSynthesisEngine:
    def __init__(self, generator: JsonGenerator, verifier: JsonVerifier) -> None:
        self.generator = generator
        self.verifier = verifier

    def synthesize(
        self,
        packet: ResearchPacket,
        *,
        progress_callback: Callable[[str, str, str], None] | None = None,
    ) -> ResearchSynthesis:
        usable = [paper for paper in packet.papers if paper.abstract]
        if not usable:
            _notify(progress_callback, "abstract_synthesis", "候选论文缺少摘要，跳过自动综合", "warning")
            return ResearchSynthesis("insufficient", "", [], [], ["候选论文缺少摘要"])
        papers = [_paper_payload(paper) for paper in usable[:8]]
        _notify(progress_callback, "abstract_synthesis", "正在依据摘要生成原子化科研主张", "running")
        raw = self.generator.complete_json(
            (
                "你是面向医学生的医学视野拓展助手。只能依据给定摘要，逐条绑定paper_id并逐字引用摘要，"
                "禁止根据标题补造结论。拓展应紧扣原问题，并优先选择机制新进展、临床转化、研究方法或仍存争议中"
                "最有学习价值的内容；同时为每篇有摘要的论文写一条不超过80个汉字的中文简介，简介也必须"
                "绑定paper_id和一段摘要逐字引文。不要重复课程答案，也不要把研究发现写成确定的临床建议。只输出JSON。"
            ),
            {
                "query": packet.plan.original_query,
                "papers": papers,
                "output": {
                    "overview": "string",
                    "paper_introductions": [{
                        "intro_id": "intro_paper_1",
                        "paper_id": "paper_1",
                        "text": "只依据该摘要写成的简短中文简介，不超过80个汉字",
                        "quote": "supporting exact abstract text",
                    }],
                    "claims": [{
                        "claim_id": "rc_1", "text": "atomic claim",
                        "paper_ids": ["paper_id"],
                        "quotes": [{"paper_id": "paper_id", "quote": "exact abstract text"}],
                        "certainty": "high|moderate|low",
                    }],
                    "limitations": ["string"],
                },
            },
        )
        try:
            synthesis = _parse_synthesis(raw, usable)
        except ValueError as exc:
            _notify(progress_callback, "abstract_synthesis", "摘要逐字引文检查失败", "warning")
            return ResearchSynthesis("fail", "", [], [], [str(exc)])
        _notify(progress_callback, "abstract_synthesis", f"生成 {len(synthesis.claims)} 条可定位主张", "ok")
        _notify(progress_callback, "research_verification", "正在用独立模型复核每条科研主张", "running")
        verdict = self.verifier.verify_json(
            {
                "system_instruction": "独立判断每条科研主张是否被所给摘要完整支持，不得使用模型记忆。只输出JSON。",
                "papers": papers,
                "claims": [
                    *[asdict(claim) for claim in synthesis.claims],
                    *[
                        {
                            "claim_id": intro.intro_id,
                            "text": intro.text,
                            "paper_ids": [intro.paper_id],
                            "quotes": [{
                                "paper_id": intro.paper_id,
                                "quote": intro.quote,
                            }],
                            "certainty": "summary",
                        }
                        for intro in synthesis.introductions
                    ],
                ],
                "output_schema": {"claims": [{"claim_id": "rc_1", "verdict": "supported|partial|contradicted|insufficient", "reason": "string"}]},
            }
        )
        expected = {
            *(claim.claim_id for claim in synthesis.claims),
            *(intro.intro_id for intro in synthesis.introductions),
        }
        returned = verdict.get("claims", [])
        if not isinstance(returned, list):
            _notify(progress_callback, "research_verification", "独立核验响应格式错误", "warning")
            return ResearchSynthesis(
                "review", synthesis.overview, synthesis.claims,
                synthesis.limitations, ["独立核验响应格式错误"],
                synthesis.introductions,
            )
        returned_ids = [str(item.get("claim_id", "")) for item in returned if isinstance(item, Mapping)]
        valid = {
            str(item.get("claim_id"))
            for item in returned
            if isinstance(item, Mapping) and item.get("verdict") == "supported"
        }
        if set(returned_ids) != expected or len(returned_ids) != len(expected) or valid != expected:
            _notify(progress_callback, "research_verification", "部分科研主张未获完整支持", "warning")
            return ResearchSynthesis(
                "review", synthesis.overview, synthesis.claims,
                synthesis.limitations, ["独立核验未支持全部科研主张"],
                synthesis.introductions,
            )
        synthesis.status = "pass"
        _notify(progress_callback, "research_verification", "全部科研主张通过独立复核", "ok")
        return synthesis

    def localize_resources(self, resources: list[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
        """Translate source titles and explain their purpose in concise Chinese."""

        candidates = [
            {
                "resource_id": str(item.get("resource_id") or item.get("paper_id") or ""),
                "original_title": str(item.get("title") or ""),
                "source_excerpt": str(item.get("description") or "")[:1200],
            }
            for item in resources[:10]
            if str(item.get("resource_id") or item.get("paper_id") or "")
            and str(item.get("title") or "")
        ]
        if not candidates:
            return {}
        raw = self.generator.complete_json(
            (
                "你是医学资料中文编辑。逐条准确翻译英文标题，并用一句中文说明这份资料具体讨论什么、"
                "可帮助读者了解什么。只能依据原标题和给定摘录，不得补充其中没有的信息；"
                "不要使用‘这是一份指南资料’等同质化开头。中文标题不超过60字，用途简介不超过80字。只输出JSON。"
            ),
            {
                "resources": candidates,
                "output": {"resources": [{
                    "resource_id": "original id",
                    "chinese_title": "准确的中文标题",
                    "purpose": "具体说明主题和用途的中文简介",
                }]},
            },
        )
        items = raw.get("resources", []) if isinstance(raw, Mapping) else []
        by_id = {item["resource_id"]: item for item in candidates}
        parsed = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            resource_id = str(item.get("resource_id") or "")
            chinese_title = str(item.get("chinese_title") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
            if (
                resource_id in by_id
                and re.search(r"[\u4e00-\u9fff]", chinese_title)
                and re.search(r"[\u4e00-\u9fff]", purpose)
                and len(chinese_title) <= 80
                and len(purpose) <= 120
            ):
                parsed.append({
                    "resource_id": resource_id,
                    "chinese_title": chinese_title,
                    "purpose": purpose,
                })
        if not parsed:
            return {}
        verdict = self.verifier.verify_json({
            "system_instruction": (
                "逐条核对中文标题是否忠实翻译原标题、用途简介是否仅来自原标题和摘录。"
                "若增加原资料未提供的人群、结论或建议，判为unsupported。只输出JSON。"
            ),
            "resources": candidates,
            "localized": parsed,
            "output_schema": {"resources": [{
                "resource_id": "id", "verdict": "supported|unsupported", "reason": "string",
            }]},
        })
        decisions = verdict.get("resources", []) if isinstance(verdict, Mapping) else []
        supported = {
            str(item.get("resource_id"))
            for item in decisions if isinstance(item, Mapping)
            and item.get("verdict") == "supported"
        }
        return {
            item["resource_id"]: {
                "chinese_title": item["chinese_title"],
                "purpose": item["purpose"],
            }
            for item in parsed if item["resource_id"] in supported
        }


def render_research(packet: ResearchPacket, synthesis: ResearchSynthesis | None) -> str:
    lines = ["科研证据拓展："]
    if not packet.papers:
        lines.append("没有找到满足当前检索条件和相关性要求的论文。")
        lines.extend(f"- 检索提示：{warning}" for warning in packet.warnings)
        return "\n".join(lines)
    if synthesis and synthesis.status == "pass":
        # The free-form overview is useful during drafting but is not itself
        # bound to exact abstract quotes. Only render the atomic claims that
        # passed quote validation and independent semantic verification.
        lines.append("以下内容均来自论文摘要中可定位的原文，并已通过独立复核：")
        for claim in synthesis.claims:
            support = friendly_claim_certainty(claim.certainty)
            lines.append(f"- {claim.text}（来源：{', '.join(claim.paper_ids)}；{support}）")
        for limitation in synthesis.limitations:
            lines.append(f"- 局限：{limitation}")
    else:
        lines.append("自动综合未通过完整核验，以下仅列出候选论文，不生成研究结论。")
    for paper in packet.papers[:8]:
        cross = "；多个数据库记录一致" if paper.cross_validated else ""
        year = paper.year or "年份未知"
        design = friendly_evidence_label(paper.evidence_level, paper.publication_types)
        lines.append(f"- [{paper.paper_id}] {paper.title}（{year}；{design}{cross}）— {paper.url}")
    lines.extend(f"- 检索提示：{warning}" for warning in packet.warnings)
    return "\n".join(lines)


def _merge_and_rank(records, plan, limit, *, relaxed=False):
    papers: list[ResearchPaper] = []
    for record in records:
        doi = _doi(record.identifiers.get("doi", ""))
        matched = next((paper for paper in papers if _same_paper(paper, record, doi)), None)
        if matched:
            matched.sources = tuple(sorted(set((*matched.sources, record.source))))
            matched.cross_validated = len(matched.sources) > 1
            matched.publication_types = tuple(
                dict.fromkeys((*matched.publication_types, *record.publication_types))
            )
            matched.evidence_level = _evidence_level(matched.publication_types)
            if len(record.snippet) > len(matched.abstract):
                matched.abstract = record.snippet
            matched.cited_by_count = max(matched.cited_by_count, record.cited_by_count)
            matched.identifiers.update(
                {
                    str(key): str(value)
                    for key, value in record.identifiers.items()
                    if value
                }
            )
            continue
        score = _relevance(record, plan.keywords)
        papers.append(ResearchPaper(
            paper_id=f"paper_{len(papers)+1}", title=record.title, year=record.year,
            authors=record.authors, abstract=record.snippet, url=record.url,
            doi=doi or None, sources=(record.source,), cross_validated=False,
            publication_types=record.publication_types,
            evidence_level=_evidence_level(record.publication_types),
            relevance_score=score, cited_by_count=record.cited_by_count,
            identifiers={
                str(key): str(value)
                for key, value in record.identifiers.items()
                if value
            },
        ))
    term_count = len(plan.keywords)
    if term_count >= 2:
        required = (1 if relaxed else min(2, term_count)) / term_count
        papers = [paper for paper in papers if paper.relevance_score >= required]
    papers.sort(key=lambda paper: (paper.relevance_score, paper.cross_validated, bool(paper.abstract), paper.year or 0), reverse=True)
    return papers[:limit]


def _same_paper(paper, record, doi):
    if doi and paper.doi == doi:
        return True
    compatible_year = not paper.year or not record.year or abs(paper.year - record.year) <= 1
    return compatible_year and SequenceMatcher(None, _normalize(paper.title), _normalize(record.title)).ratio() >= 0.94


def _relevance(record, keywords):
    if not keywords:
        return 1.0
    haystack = _normalize(f"{record.title} {record.snippet}")
    return sum(_normalize(term) in haystack for term in keywords) / len(keywords)


def _evidence_level(types):
    return friendly_evidence_label(publication_types=types)


def _pubmed_query(query, study_types):
    publication_type_terms = {
        "systematic review": "Systematic Review",
        "meta-analysis": "Meta-Analysis",
        "meta analysis": "Meta-Analysis",
        "randomized controlled trial": "Randomized Controlled Trial",
        "randomised controlled trial": "Randomized Controlled Trial",
        "clinical trial": "Clinical Trial",
        "review": "Review",
    }
    selected = []
    for value in study_types:
        mapped = publication_type_terms.get(str(value).strip().lower())
        if mapped and mapped not in selected:
            selected.append(mapped)
    if not selected:
        return query
    filters = " OR ".join(f'"{value}"[Publication Type]' for value in selected)
    return f"({query}) AND ({filters})"


def _database_query(plan):
    """Keep answer options and natural-language prose out of database queries."""

    query = re.split(r"(?i)\boptions?\s*:", plan.english_query, maxsplit=1)[0].strip()
    if len(query) <= 160 and re.search(r"(?i)\b(?:and|or)\b", query):
        return query
    concepts = [
        term for term in plan.keywords
        if re.search(r"[a-z]", term) and len(term) >= 3
    ][:3]
    if not concepts:
        return query.rstrip("?")[:160]
    quoted = [f'"{term}"' if " " in term else term for term in concepts]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return " AND ".join(quoted)
    return f"{quoted[0]} AND ({quoted[1]} OR {quoted[2]})"


def _broader_query(plan, first_query):
    concepts = [
        term.strip() for term in plan.keywords
        if re.search(r"[a-z]", term) and len(term.strip()) >= 4
    ]
    if not concepts:
        return ""
    primary = concepts[0]
    broad = f'"{primary}"' if " " in primary else primary
    return broad if _normalize(broad) != _normalize(first_query) else ""


def _parse_synthesis(raw, papers):
    if not isinstance(raw, Mapping):
        raise ValueError("科研综合响应必须是JSON对象")
    by_id = {paper.paper_id: paper for paper in papers}
    claims = []
    seen_claim_ids = set()
    raw_claims = raw.get("claims", [])
    if not isinstance(raw_claims, list):
        raise ValueError("科研综合claims必须是列表")
    for item in raw_claims:
        if not isinstance(item, Mapping):
            raise ValueError("科研主张格式错误")
        claim_id = str(item.get("claim_id", "")).strip()
        claim_text = str(item.get("text", "")).strip()
        certainty = str(item.get("certainty", "low")).strip().lower()
        if not claim_id or claim_id in seen_claim_ids or not claim_text:
            raise ValueError("科研主张缺少唯一编号或正文")
        if certainty not in {"high", "moderate", "low"}:
            raise ValueError("科研主张确定性取值无效")
        seen_claim_ids.add(claim_id)
        paper_ids = [str(value) for value in item.get("paper_ids", [])]
        if not paper_ids or any(value not in by_id for value in paper_ids):
            raise ValueError("科研主张引用未知论文")
        quotes = []
        for quote in item.get("quotes", []):
            paper_id = str(quote.get("paper_id", ""))
            text = str(quote.get("quote", ""))
            if paper_id not in paper_ids or not text or text not in by_id[paper_id].abstract:
                raise ValueError("科研主张的引文不是摘要逐字原文")
            quotes.append({"paper_id": paper_id, "quote": text})
        if not quotes:
            raise ValueError("科研主张缺少摘要逐字引文")
        claims.append(ResearchClaim(
            claim_id, claim_text, paper_ids, quotes, certainty,
        ))
    if not claims:
        raise ValueError("科研综合没有可核验主张")
    introductions = []
    seen_intro_ids = set()
    raw_introductions = raw.get("paper_introductions", [])
    if not isinstance(raw_introductions, list):
        raise ValueError("论文简介必须是列表")
    for item in raw_introductions:
        if not isinstance(item, Mapping):
            raise ValueError("论文简介格式错误")
        intro_id = str(item.get("intro_id", "")).strip()
        paper_id = str(item.get("paper_id", "")).strip()
        text = str(item.get("text", "")).strip()
        quote = str(item.get("quote", "")).strip()
        if not intro_id or intro_id in seen_intro_ids or intro_id in seen_claim_ids:
            raise ValueError("论文简介缺少唯一编号")
        if paper_id not in by_id or not text or not re.search(r"[\u4e00-\u9fff]", text):
            raise ValueError("论文简介必须绑定已知论文并使用中文")
        if len(text) > 120:
            raise ValueError("论文简介超过120字")
        if not quote or quote not in by_id[paper_id].abstract:
            raise ValueError("论文简介的引文不是摘要逐字原文")
        seen_intro_ids.add(intro_id)
        introductions.append(ResearchIntroduction(intro_id, paper_id, text, quote))
    return ResearchSynthesis(
        "pending", str(raw.get("overview", "")).strip(), claims,
        [str(x) for x in raw.get("limitations", [])],
        introductions=introductions,
    )


def _paper_payload(paper):
    return {"paper_id": paper.paper_id, "title": paper.title, "year": paper.year, "abstract": paper.abstract, "sources": paper.sources, "cross_validated": paper.cross_validated, "evidence_level": paper.evidence_level}


def _terms(value):
    return [item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,}", value)]


def _normalize(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).lower())


def _doi(value):
    return str(value).lower().removeprefix("https://doi.org/").removeprefix("doi:").strip()


def _text(value, fallback):
    result = str(value or "").strip()
    return result or fallback


def _year(value):
    try:
        number = int(value)
        return number if 1800 <= number <= datetime.now().year + 1 else None
    except (TypeError, ValueError):
        return None


def _notify(callback, phase_id, message, state):
    if callback is None:
        return
    try:
        callback(phase_id, message, state)
    except Exception:
        # Progress reporting is observational and must not affect retrieval.
        pass
