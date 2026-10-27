"""Build small, exact evidence spans from transient official guideline text."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from evidence.official_guidelines import OfficialGuidelineRecord


@dataclass(frozen=True)
class OfficialEvidenceItem:
    evidence_id: str
    source: str
    resource_id: str
    title: str
    url: str
    locator: str
    evidence_text: str
    span_start: int
    span_end: int
    full_text_sha256: str
    matched_terms: tuple[str, ...]
    recommendation_markers: tuple[str, ...]
    retrieval_score: float
    access_level: str = "official_full_text_span"


@dataclass
class OfficialEvidenceBundle:
    query: str
    evidence: list[OfficialEvidenceItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OfficialEvidenceBundleBuilder:
    RECOMMENDATION_MARKERS = (
        "recommend", "should", "must", "is advised", "is indicated",
        "follow", "repeat", "seek", "call emergency", "right away",
        "建议", "推荐", "应当", "必须", "不应",
    )

    def build(
        self,
        query: str,
        records: Iterable[OfficialGuidelineRecord],
        *,
        max_items: int = 6,
        max_span_chars: int = 2200,
    ) -> OfficialEvidenceBundle:
        bundle = OfficialEvidenceBundle(query=query)
        terms = _query_terms(query)
        if not terms:
            bundle.warnings.append("官方指南证据定位缺少有效主题词")
            return bundle

        candidates = []
        for record in records:
            if not record.full_text:
                continue
            full_hash = hashlib.sha256(record.full_text.encode("utf-8")).hexdigest()
            for start, end, text in _chunks(record.full_text, max_span_chars):
                lowered = text.lower()
                matched = tuple(term for term in terms if term in lowered)
                markers = tuple(
                    marker for marker in self.RECOMMENDATION_MARKERS
                    if marker in lowered
                )
                if not matched:
                    continue
                # Topic coverage matters more than generic recommendation verbs;
                # otherwise a broad "should" paragraph can outrank the passage
                # that actually contains two or three query concepts.
                score = len(matched) * 10.0 + min(len(markers), 3) * 1.5
                if markers:
                    score += 1.0
                digest = hashlib.sha256(
                    f"{record.source}|{record.resource_id}|{start}|{end}".encode("utf-8")
                ).hexdigest()[:20]
                candidates.append((score, OfficialEvidenceItem(
                    evidence_id=f"ext_ev_{digest}",
                    source=record.source,
                    resource_id=record.resource_id,
                    title=record.title,
                    url=record.url,
                    locator=f"全文字符 {start}-{end}",
                    evidence_text=text,
                    span_start=start,
                    span_end=end,
                    full_text_sha256=full_hash,
                    matched_terms=matched,
                    recommendation_markers=markers,
                    retrieval_score=score,
                )))
        candidates.sort(key=lambda item: item[0], reverse=True)
        bundle.evidence = [item for _, item in candidates[:max_items]]
        if not bundle.evidence:
            bundle.warnings.append("官方指南全文中未定位到与问题直接相关的证据段")
        return bundle


def _query_terms(value: str) -> tuple[str, ...]:
    stop = {
        "and", "or", "the", "of", "for", "in", "with", "how", "what",
        "patient", "patients", "type", "management", "guideline",
        "before", "after", "recommend", "recommended", "recommendation",
        "should", "must",
    }
    english = [
        token for token in re.findall(r"[a-z][a-z0-9-]{2,}", value.lower())
        if token not in stop
    ]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,8}", value)
    expanded = [*english, *chinese]
    lowered = value.lower()
    if "hypoglycemia" in lowered or "hypoglycaemia" in lowered:
        expanded.extend(("low blood sugar", "low blood glucose"))
    if "low blood sugar" in lowered or "low blood glucose" in lowered:
        expanded.extend(("hypoglycemia", "hypoglycaemia"))
    return tuple(dict.fromkeys(expanded))[:12]


def _chunks(text: str, max_chars: int) -> Iterable[tuple[int, int, str]]:
    paragraphs = [match for match in re.finditer(r"\S(?:[\s\S]*?)(?=\n\s*\n|\Z)", text)]
    if not paragraphs:
        paragraphs = [re.match(r"[\s\S]+", text)] if text else []
    current_start = None
    current_end = None
    parts: list[str] = []
    for match in paragraphs:
        if match is None:
            continue
        paragraph = match.group(0).strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if parts and current_start is not None and current_end is not None:
                yield current_start, current_end, "\n\n".join(parts)
                parts = []
            for offset in range(0, len(paragraph), max_chars):
                piece = paragraph[offset:offset + max_chars]
                yield match.start() + offset, match.start() + offset + len(piece), piece
            current_start = current_end = None
            continue
        projected = len("\n\n".join([*parts, paragraph]))
        if parts and projected > max_chars:
            yield current_start, current_end, "\n\n".join(parts)
            parts = []
            current_start = None
        if current_start is None:
            current_start = match.start()
        current_end = match.end()
        parts.append(paragraph)
    if parts and current_start is not None and current_end is not None:
        yield current_start, current_end, "\n\n".join(parts)
