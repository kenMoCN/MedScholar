"""Create exact, traceable evidence spans from retrieval results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval.page_text_index import PageTextIndex, load_page_text_index
from retrieval.page_mapping import trusted_printed_page
from retrieval.structure_retriever import RetrievalResult


@dataclass
class EvidenceItem:
    evidence_id: str
    document_id: str
    original_name: str
    document_type: str
    pdf_page: int
    printed_page: int | None
    locator: str
    section_title: str | None
    evidence_text: str
    span_start: int
    span_end: int
    page_text_sha256: str
    matched_terms: list[str]
    retrieval_score: float
    context_pdf_pages: list[int]
    access_level: str = "local_full_text"
    evidence_origin: str = "native_text"
    visual_source_ref: str | None = None
    visual_image_sha256: str | None = None
    visual_model: str | None = None
    visual_confidence: float | None = None
    visual_uncertain_spans: list[str] = field(default_factory=list)
    visual_requires_recheck: bool = False
    visual_verified_by_user: bool = False


@dataclass
class EvidenceBundle:
    schema_version: str
    bundle_id: str
    query: str
    route_mode: str
    created_at: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceBundleBuilder:
    def __init__(
        self,
        document_maps: dict[str, dict[str, Any]],
        page_indexes: dict[str, PageTextIndex],
    ) -> None:
        self.document_maps = document_maps
        self.page_indexes = page_indexes

    @classmethod
    def from_directories(
        cls,
        document_map_dir: str | Path,
        page_index_dir: str | Path,
    ) -> "EvidenceBundleBuilder":
        maps: dict[str, dict[str, Any]] = {}
        for path in Path(document_map_dir).glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            maps[data["document_id"]] = data

        indexes: dict[str, PageTextIndex] = {}
        for path in Path(page_index_dir).glob("*.json"):
            index = load_page_text_index(path)
            indexes[index.document_id] = index
        return cls(maps, indexes)

    def build(
        self,
        result: RetrievalResult,
        max_span_chars: int = 2400,
        *,
        max_items: int = 12,
        max_context_pages_per_hit: int = 2,
    ) -> EvidenceBundle:
        items: list[EvidenceItem] = []
        warnings: list[str] = []
        seen_pages: set[tuple[str, int]] = set()

        for hit in result.hits:
            index = self.page_indexes.get(hit.document_id)
            document_map = self.document_maps.get(hit.document_id)
            if index is None or document_map is None:
                warnings.append(f"缺少文档或页索引: {hit.document_id}")
                continue
            page_numbers = [hit.pdf_page]
            if hit.document_type == "textbook" and max_context_pages_per_hit:
                neighbors = sorted(
                    (
                        page_number
                        for page_number in hit.context_pdf_pages
                        if page_number != hit.pdf_page
                    ),
                    key=lambda page_number: (abs(page_number - hit.pdf_page), page_number),
                )
                page_numbers.extend(neighbors[:max_context_pages_per_hit])

            pages_by_number = {record.pdf_page: record for record in index.pages}
            for page_number in page_numbers:
                if len(items) >= max_items:
                    break
                page_key = (hit.document_id, page_number)
                if page_key in seen_pages:
                    continue
                page = pages_by_number.get(page_number)
                if page is None:
                    warnings.append(f"页索引不存在: {hit.document_id} PDF {page_number}")
                    continue
                if not page.text.strip():
                    continue
                seen_pages.add(page_key)
                matched_terms = _terms_present_on_page(
                    page.text,
                    [*hit.matched_terms, *_query_terms(result.query)],
                )
                start, end = _select_exact_span(
                    page.text,
                    matched_terms,
                    max_span_chars=max_span_chars,
                )
                evidence_text = page.text[start:end]
                evidence_id = f"ev_{uuid.uuid4().hex}"
                is_visual = page.content_origin in {"visual_ocr", "visual_ocr_verified"}
                has_transcribed_figure = page.content_origin == "native_text_plus_figure_transcription"
                printed_page = trusted_printed_page(document_map, page_number)
                locator = _locator_for_page(
                    hit.document_type,
                    page_number,
                    printed_page,
                )
                items.append(
                    EvidenceItem(
                        evidence_id=evidence_id,
                        document_id=hit.document_id,
                        original_name=hit.original_name,
                        document_type=hit.document_type,
                        pdf_page=page_number,
                        printed_page=printed_page,
                        locator=locator,
                        section_title=_section_for_page(document_map, page_number),
                        evidence_text=evidence_text,
                        span_start=start,
                        span_end=end,
                        page_text_sha256=_sha256_text(page.text),
                        matched_terms=matched_terms,
                        retrieval_score=(
                            hit.score if page_number == hit.pdf_page else round(hit.score * 0.92, 4)
                        ),
                        context_pdf_pages=list(hit.context_pdf_pages),
                        access_level=(
                            "local_figure_transcription_agent_checked"
                            if has_transcribed_figure
                            else (
                                "local_visual_ocr_verified"
                                if page.visual_verified_by_user
                                else ("local_visual_ocr_unverified" if is_visual else "local_full_text")
                            )
                        ),
                        evidence_origin=page.content_origin,
                        visual_source_ref=page.visual_source_ref,
                        visual_image_sha256=page.visual_image_sha256,
                        visual_model=page.visual_model,
                        visual_confidence=page.visual_confidence,
                        visual_uncertain_spans=list(page.visual_uncertain_spans),
                        visual_requires_recheck=page.visual_requires_recheck,
                        visual_verified_by_user=page.visual_verified_by_user,
                    )
                )
                if is_visual and not page.visual_verified_by_user:
                    warnings.append(
                        f"{hit.original_name} {locator} 来自图片文字识别，关键符号需回看原图"
                    )
                if has_transcribed_figure:
                    warnings.append(
                        f"{hit.original_name} {locator} 含图示转录，关键原子位置可回看原 PDF 图示"
                    )
            if len(items) >= max_items:
                break

        if not items:
            warnings.append("检索未产生可验证证据")

        return EvidenceBundle(
            schema_version="1.0",
            bundle_id=f"bundle_{uuid.uuid4().hex}",
            query=result.query,
            route_mode=result.route_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
            evidence=items,
            warnings=warnings,
        )


def save_evidence_bundle(bundle: EvidenceBundle, path: str | Path) -> None:
    _atomic_json_write(bundle.to_dict(), path)


def load_evidence_bundle(path: str | Path) -> EvidenceBundle:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0":
        raise ValueError(f"不支持的证据包版本: {data.get('schema_version')!r}")
    return EvidenceBundle(
        schema_version=data["schema_version"],
        bundle_id=data["bundle_id"],
        query=data["query"],
        route_mode=data["route_mode"],
        created_at=data["created_at"],
        evidence=[EvidenceItem(**item) for item in data.get("evidence", [])],
        warnings=list(data.get("warnings", [])),
    )


def _select_exact_span(
    page_text: str,
    matched_terms: list[str],
    *,
    max_span_chars: int,
) -> tuple[int, int]:
    if len(page_text) <= max_span_chars:
        return 0, len(page_text)

    normalized_page, raw_offsets = _normalized_with_offsets(page_text)
    occurrences: list[tuple[int, int, str]] = []
    for term in matched_terms:
        normalized_term, _ = _normalized_with_offsets(term)
        if not normalized_term:
            continue
        search_from = 0
        while len(occurrences) < 80:
            position = normalized_page.find(normalized_term, search_from)
            if position < 0:
                break
            raw_start = raw_offsets[position]
            raw_end = raw_offsets[position + len(normalized_term) - 1] + 1
            occurrences.append((raw_start, raw_end, normalized_term))
            search_from = position + 1

    if occurrences:
        candidates: list[tuple[int, int, int]] = []
        for raw_start, _, _ in occurrences:
            window_start = max(0, raw_start - max_span_chars // 3)
            window_end = min(len(page_text), window_start + max_span_chars)
            covered_terms = {
                term
                for occurrence_start, occurrence_end, term in occurrences
                if occurrence_start < window_end and occurrence_end > window_start
            }
            candidates.append(
                (sum(len(term) for term in covered_terms), len(covered_terms), raw_start)
            )
        _, _, position = max(candidates)
    else:
        position = 0

    start = max(0, position - max_span_chars // 3)
    end = min(len(page_text), start + max_span_chars)
    start = _nearest_boundary(page_text, start, backwards=True)
    end = _nearest_boundary(page_text, end, backwards=False)
    if end - start > max_span_chars * 1.25:
        end = start + max_span_chars
    return start, end


def _normalized_with_offsets(value: str) -> tuple[str, list[int]]:
    """Normalize OCR spacing/full-width glyphs while retaining raw offsets."""

    normalized_chars: list[str] = []
    raw_offsets: list[int] = []
    for raw_index, raw_char in enumerate(value):
        for char in unicodedata.normalize("NFKC", raw_char).lower():
            if re.fullmatch(r"[0-9a-z\u0370-\u03ff\u4e00-\u9fff]", char):
                normalized_chars.append(char)
                raw_offsets.append(raw_index)
    return "".join(normalized_chars), raw_offsets


def _query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(query or ""))
    parts = re.findall(r"[A-Za-z0-9+\-α-ωΑ-Ω]{2,}|[\u4e00-\u9fff]{2,12}", normalized)
    return list(dict.fromkeys(parts))[:16]


def _terms_present_on_page(page_text: str, terms: list[str]) -> list[str]:
    normalized_page, _ = _normalized_with_offsets(page_text)
    present = []
    for term in terms:
        normalized_term, _ = _normalized_with_offsets(term)
        if normalized_term and normalized_term in normalized_page and term not in present:
            present.append(term)
    return present[:12]


def _locator_for_page(
    document_type: str,
    pdf_page: int,
    printed_page: int | None,
) -> str:
    if document_type == "course_deck":
        return f"幻灯片 {pdf_page}"
    if printed_page is not None:
        return f"教材印刷页 {printed_page}（PDF第 {pdf_page} 页）"
    return f"PDF第 {pdf_page} 页"


def _section_for_page(document_map: dict[str, Any], pdf_page: int) -> str | None:
    candidates = [
        node
        for node in document_map.get("nodes", [])
        if int(node["pdf_start"]) <= pdf_page <= int(node.get("pdf_end") or node["pdf_start"])
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda node: (int(node.get("level", 0)), int(node["pdf_start"])),
        reverse=True,
    )
    return str(candidates[0].get("title") or "") or None


def _nearest_boundary(text: str, position: int, *, backwards: bool) -> int:
    if position <= 0:
        return 0
    if position >= len(text):
        return len(text)
    boundary_chars = "\n。！？；"
    search_range = range(position, max(-1, position - 160), -1) if backwards else range(position, min(len(text), position + 160))
    for index in search_range:
        if text[index] in boundary_chars:
            return index + (0 if backwards else 1)
    return position


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_json_write(data: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
