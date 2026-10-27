"""Structure-first lexical retrieval over DocumentMap and per-page indexes."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from retrieval.page_text_index import PageTextIndex, PageTextRecord, load_page_text_index
from retrieval.page_mapping import trusted_printed_page
from core.subject_profiles import infer_document_subjects, valid_subject_ids


QUESTION_NOISE = (
    "请问",
    "请解释",
    "解释一下",
    "什么是",
    "是什么",
    "为什么",
    "如何",
    "怎么",
    "哪些",
    "一下",
)
QUESTION_PARTICLES = "的是吗呢呀啊请"


@dataclass
class RetrievalHit:
    document_id: str
    original_name: str
    document_type: str
    pdf_page: int
    printed_page: int | None
    locator: str
    section_title: str | None
    score: float
    matched_terms: list[str]
    snippet: str
    context_pdf_pages: list[int]
    context_text: str


@dataclass
class RetrievalResult:
    query: str
    route_mode: str
    routed_documents: list[str]
    hits: list[RetrievalHit] = field(default_factory=list)


@dataclass
class _IndexedDocument:
    document_map: dict[str, Any]
    page_index: PageTextIndex


class StructureAwareRetriever:
    def __init__(self, documents: Iterable[_IndexedDocument]) -> None:
        self.documents = list(documents)

    @classmethod
    def from_directories(
        cls,
        document_map_dir: str | Path,
        page_index_dir: str | Path,
    ) -> "StructureAwareRetriever":
        maps: dict[str, dict[str, Any]] = {}
        for path in Path(document_map_dir).glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            maps[data["document_id"]] = data

        indexes: dict[str, PageTextIndex] = {}
        for path in Path(page_index_dir).glob("*.json"):
            index = load_page_text_index(path)
            indexes[index.document_id] = index

        documents = [
            _IndexedDocument(document_map=data, page_index=indexes[document_id])
            for document_id, data in maps.items()
            if document_id in indexes
        ]
        return cls(documents)

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        context_radius: int = 1,
        max_per_document: int = 2,
        subject_ids: Iterable[str] = (),
    ) -> RetrievalResult:
        normalized_query = _normalize_query(query)
        if len(normalized_query) < 2:
            return RetrievalResult(query=query, route_mode="invalid_query", routed_documents=[])
        stem_query = _normalize_query(_question_stem(query)) or normalized_query
        option_queries = [
            _normalize_query(f"{_question_stem(query)} {content}")
            for _, content in _question_options(query)
            if content.strip()
        ]

        requested_subjects = set(valid_subject_ids(subject_ids))
        eligible_documents = self.documents
        if requested_subjects:
            eligible_documents = [
                document
                for document in self.documents
                if requested_subjects.intersection(
                    _document_subject_ids(document.document_map)
                )
            ]
            if not eligible_documents:
                return RetrievalResult(
                    query=query,
                    route_mode="subject_no_documents",
                    routed_documents=[],
                )

        document_routes = [
            (document, self._document_route_score(stem_query, document.document_map))
            for document in eligible_documents
        ]
        positive_routes = [item for item in document_routes if item[1] >= 0.2]
        if positive_routes:
            positive_routes.sort(key=lambda item: item[1], reverse=True)
            selected_documents = positive_routes[: max(4, top_k)]
            # A full textbook is the fallback evidence backbone.  Its title is
            # usually generic, so a chapter-specific PPT can otherwise push it
            # out of the structural shortlist even when the exact term occurs
            # in the textbook body (for example, 窦房结 or 横管膜).  Keep the
            # structure-first shortlist, but always add reference textbooks for
            # page-level lexical scoring.
            selected_ids = {
                item[0].document_map["document_id"] for item in selected_documents
            }
            selected_documents.extend(
                (document, score)
                for document, score in document_routes
                if document.document_map.get("document_type") == "textbook"
                and document.document_map["document_id"] not in selected_ids
            )
            route_mode = "structure_first"
        else:
            # Keep a global lexical fallback inside the requested subject.
            # Falling back to every document would re-introduce physiology or
            # other-course evidence after subject routing had already filtered
            # the knowledge base.
            selected_documents = [(document, 0.0) for document in eligible_documents]
            route_mode = "global_fallback"

        page_candidates: list[tuple[float, _IndexedDocument, PageTextRecord]] = []
        for document, route_score in selected_documents:
            preferred_pages = self._preferred_pages(stem_query, document.document_map)
            pages = document.page_index.pages

            for page in pages:
                page_text = f"{page.title or ''}\n{page.text}"
                stem_score = _lexical_score(stem_query, page_text)
                if option_queries:
                    option_score = max(
                        (_lexical_score(item, page_text) for item in option_queries),
                        default=0.0,
                    )
                    # The stem determines the topic. Options only supplement
                    # recall inside that topic, so a generic vitamin/CoA page
                    # cannot outrank the actual beta-oxidation section merely
                    # because it mentions several distractors.
                    page_score = 1.35 * stem_score + 0.25 * option_score
                else:
                    page_score = stem_score
                if page_score <= 0:
                    continue
                if page.pdf_page in set(document.document_map.get("toc_pdf_pages", [])):
                    page_score *= 0.35
                source_bonus = 0.35 if document.document_map["document_type"] == "course_deck" else 0.25
                # The table of contents narrows the search, but must not become
                # a hard filter.  Exam stems often connect concepts that live
                # in different chapters (for example feedback control and a
                # urinary reflex), and a hard section boundary can discard the
                # only page that states the answer explicitly.
                structure_bonus = 0.4 if page.pdf_page in preferred_pages else 0.0
                total = page_score + route_score * 2.0 + source_bonus + structure_bonus
                page_candidates.append((total, document, page))

        page_candidates.sort(key=lambda item: item[0], reverse=True)
        if page_candidates:
            minimum_score = max(0.75, page_candidates[0][0] * 0.22)
            page_candidates = [
                candidate for candidate in page_candidates if candidate[0] >= minimum_score
            ]
        selected: list[tuple[float, _IndexedDocument, PageTextRecord]] = []
        per_document: dict[str, int] = {}
        selected_pages: set[tuple[str, int]] = set()

        # First retain the best page from each source *type* so course PPTs and
        # the textbook can corroborate one another. Reserving one slot for
        # every individual deck over-diversifies the result and can evict a
        # textbook continuation page that contains the decisive sentence.
        selected_source_types: set[str] = set()
        for candidate in page_candidates:
            document_id = candidate[1].document_map["document_id"]
            source_type = candidate[1].document_map.get("document_type", "unknown")
            if source_type in selected_source_types:
                continue
            selected.append(candidate)
            per_document[document_id] = 1
            selected_source_types.add(source_type)
            selected_pages.add((document_id, candidate[2].pdf_page))
            if len(selected) >= top_k:
                break

        for candidate in page_candidates:
            document_id = candidate[1].document_map["document_id"]
            page_key = (document_id, candidate[2].pdf_page)
            if page_key in selected_pages:
                continue
            document_limit = max_per_document
            if candidate[1].document_map.get("document_type") == "textbook":
                # Textbook explanations often continue across a page break.
                # Keeping four pages within the fixed top-k budget captures
                # that continuation without increasing prompt size.
                document_limit = max(document_limit, 4)
            if per_document.get(document_id, 0) >= document_limit:
                continue
            selected.append(candidate)
            per_document[document_id] = per_document.get(document_id, 0) + 1
            selected_pages.add(page_key)
            if len(selected) >= top_k:
                break

        selected.sort(key=lambda item: item[0], reverse=True)

        hits = [
            self._make_hit(
                normalized_query,
                score,
                document,
                page,
                context_radius,
            )
            for score, document, page in selected
        ]
        routed_names = [
            document.document_map["original_name"] for document, _ in selected_documents
        ]
        return RetrievalResult(
            query=query,
            route_mode=route_mode,
            routed_documents=routed_names,
            hits=hits,
        )
    def _document_route_score(self, query: str, document_map: dict[str, Any]) -> float:
        fields = [document_map.get("original_name", "")]
        fields.extend(entry.get("title", "") for entry in document_map.get("toc_entries", []))
        fields.extend(node.get("title", "") for node in document_map.get("nodes", []))
        return max((_structural_similarity(query, field) for field in fields), default=0.0)

    def _preferred_pages(self, query: str, document_map: dict[str, Any]) -> set[int]:
        scored_nodes = []
        for node in document_map.get("nodes", []):
            score = _structural_similarity(query, node.get("title", ""))
            if score >= 0.18:
                scored_nodes.append((score, node))
        scored_nodes.sort(key=lambda item: item[0], reverse=True)

        pages: set[int] = set()
        for _, node in scored_nodes[:5]:
            start = int(node["pdf_start"])
            end = int(node.get("pdf_end") or start)
            if end - start > 80:
                end = start + 80
            pages.update(range(start, end + 1))
        return pages

    def _make_hit(
        self,
        query: str,
        score: float,
        document: _IndexedDocument,
        page: PageTextRecord,
        context_radius: int,
    ) -> RetrievalHit:
        document_map = document.document_map
        printed_page = _printed_page_for(document_map, page.pdf_page)
        if document_map["document_type"] == "course_deck":
            locator = f"幻灯片 {page.pdf_page}"
        elif printed_page is not None:
            locator = f"教材印刷页 {printed_page}（PDF第 {page.pdf_page} 页）"
        else:
            locator = f"PDF第 {page.pdf_page} 页"

        section = _section_for_page(document_map, page.pdf_page)
        terms = _matched_terms(query, f"{page.title or ''}\n{page.text}")
        snippet = _snippet(page.text, terms or [query])
        context_start = max(1, page.pdf_page - context_radius)
        context_end = min(document.page_index.page_count, page.pdf_page + context_radius)
        context_records = [
            record
            for record in document.page_index.pages
            if context_start <= record.pdf_page <= context_end
        ]
        context_text = "\n\n".join(
            f"[PDF {record.pdf_page}]\n{record.text}" for record in context_records
        )
        if len(context_text) > 9000:
            context_text = context_text[:9000] + "\n[上下文已截断]"

        return RetrievalHit(
            document_id=document_map["document_id"],
            original_name=document_map["original_name"],
            document_type=document_map["document_type"],
            pdf_page=page.pdf_page,
            printed_page=printed_page,
            locator=locator,
            section_title=section,
            score=round(score, 4),
            matched_terms=terms,
            snippet=snippet,
            context_pdf_pages=[record.pdf_page for record in context_records],
            context_text=context_text,
        )


def _document_subject_ids(document_map: dict[str, Any]) -> tuple[str, ...]:
    explicit = valid_subject_ids(document_map.get("subject_ids", []))
    if explicit:
        return explicit
    return infer_document_subjects(
        " ".join((
            str(document_map.get("original_name", "")),
            str((document_map.get("metadata") or {}).get("title", "")),
        ))
    )


def _normalize_query(value: str) -> str:
    # PDF extraction commonly yields full-width Latin letters and digits
    # (for example ＦＥＶ１). NFKC lets a normal user query such as FEV1 match
    # those pages before punctuation and spaces are removed.
    cleaned = unicodedata.normalize("NFKC", value).lower()
    cleaned = cleaned.replace("脂酸", "脂肪酸")
    for noise in QUESTION_NOISE:
        cleaned = cleaned.replace(noise, "")
    cleaned = cleaned.translate(str.maketrans("", "", QUESTION_PARTICLES))
    return re.sub(r"[^0-9a-z\u0370-\u03ff\u4e00-\u9fff]+", "", cleaned)


def _question_stem(value: str) -> str:
    """Drop answer options so distractor vocabulary cannot dominate retrieval."""

    match = re.search(r"(?i)(?:^|[\s；;，,：:?？])a\s*[\.．、:]", value)
    if match and match.start() >= 4:
        return value[: match.start()].strip()
    return value


def _question_options(value: str) -> list[tuple[str, str]]:
    """Extract labelled options for supplementary, not primary, retrieval."""

    matches = list(re.finditer(
        r"(?i)(?:^|[\s；;，,：:])([A-H])\s*[\.．、:]\s*",
        str(value or ""),
    ))
    options: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        content = str(value)[match.end():end].strip(" \t\r\n；;，,。")
        if content:
            options.append((match.group(1).upper(), content))
    return options


def _ngrams(value: str, size: int) -> set[str]:
    if len(value) < size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _structural_similarity(query: str, value: str) -> float:
    target = _normalize_query(value)
    if not target:
        return 0.0
    if query in target:
        return 1.0
    if target in query:
        # A short generic heading such as “细胞” appearing inside a long exam
        # stem is weak routing evidence, not an exact structural match.
        return min(1.0, len(target) / max(len(query), 1))
    query_bigrams = _ngrams(query, 2)
    if not query_bigrams:
        return 0.0
    overlap = query_bigrams & _ngrams(target, 2)
    return len(overlap) / len(query_bigrams)


def _lexical_score(query: str, value: str) -> float:
    target = _normalize_query(value)
    if not target:
        return 0.0
    score = 0.0
    if query in target:
        score += 8.0 + math.log1p(target.count(query))
    for size, weight in ((4, 4.0), (3, 2.5), (2, 1.0)):
        query_grams = _ngrams(query, size)
        if not query_grams:
            continue
        overlap = query_grams & _ngrams(target, size)
        coverage = len(overlap) / len(query_grams)
        # Long clinical stems contain symptoms, context and an explicit task.
        # Dividing only by every n-gram in that full sentence can reduce an
        # exact concept such as “胃排空” or “门静脉” almost to zero. Preserve
        # global coverage, while also rewarding several exact local phrases.
        local_density = min(1.0, len(overlap) / {4: 4, 3: 6, 2: 12}[size])
        score += weight * max(coverage, 0.45 * local_density)
    return score


def _matched_terms(query: str, value: str) -> list[str]:
    target = _normalize_query(value)
    matches: list[str] = []
    if query in target:
        matches.append(query)
    for size in (4, 3, 2):
        for term in sorted(_ngrams(query, size)):
            if term in target and term not in matches:
                matches.append(term)
        if len(matches) >= 8:
            break
    return matches[:8]


def _snippet(text: str, terms: Iterable[str], max_chars: int = 1800) -> str:
    if len(text) <= max_chars:
        return text.strip()
    normalized = _normalize_query(text)
    position = -1
    for term in terms:
        position = normalized.find(_normalize_query(term))
        if position >= 0:
            break
    if position < 0:
        return text[:max_chars].strip() + "…"

    approximate_ratio = position / max(len(normalized), 1)
    center = round(len(text) * approximate_ratio)
    start = max(0, center - max_chars // 2)
    end = min(len(text), start + max_chars)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _printed_page_for(document_map: dict[str, Any], pdf_page: int) -> int | None:
    return trusted_printed_page(document_map, pdf_page)


def _section_for_page(document_map: dict[str, Any], pdf_page: int) -> str | None:
    candidates = [
        node
        for node in document_map.get("nodes", [])
        if int(node["pdf_start"]) <= pdf_page <= int(node.get("pdf_end") or node["pdf_start"])
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda node: (int(node.get("level", 0)), int(node["pdf_start"])), reverse=True)
    return str(candidates[0].get("title") or "") or None
