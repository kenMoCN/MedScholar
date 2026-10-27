"""Build a lightweight structure-first index for textbooks and course decks."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

from pypdf import PdfReader

from retrieval.document_importer import document_id_from_hash, sha256_file
from core.subject_profiles import infer_document_subjects


CHINESE_NUMBER = "一二三四五六七八九十百零〇两0-9"
HEADING_START_PATTERN = re.compile(
    rf"第\s*[{CHINESE_NUMBER}]+\s*[章节篇部]"
)
TRAILING_PAGE_PATTERN = re.compile(r"\s+(\d{1,4})\s*$")
GENERIC_SLIDE_TITLE_PATTERN = re.compile(r"^(?:幻灯片|slide)\s*\d+$", re.IGNORECASE)


@dataclass
class TocEntry:
    title: str
    printed_page: int | None
    toc_pdf_page: int


@dataclass
class StructureNode:
    level: int
    title: str
    pdf_start: int
    pdf_end: int | None = None
    printed_start: int | None = None
    source: str = "outline"
    title_verified: bool = False


@dataclass
class PageAnchor:
    title: str
    printed_page: int
    pdf_page: int
    offset: int
    title_verified: bool


@dataclass
class PageMapSegment:
    printed_start: int
    printed_end: int
    pdf_start: int
    pdf_end: int
    offset: int
    confidence: float
    anchors: list[PageAnchor] = field(default_factory=list)


@dataclass
class DocumentMap:
    schema_version: str
    document_id: str
    source_path: str
    original_name: str
    document_type: str
    page_count: int
    metadata: dict[str, str | None]
    outline_available: bool
    toc_pdf_pages: list[int]
    toc_entries: list[TocEntry]
    text_layer: str
    sampled_text_page_ratio: float
    nodes: list[StructureNode]
    page_map_segments: list[PageMapSegment]
    warnings: list[str]
    subject_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PdfStructureIndexer:
    def __init__(self, pdf_path: str | Path) -> None:
        self.pdf_path = Path(pdf_path).expanduser().absolute()
        if not self.pdf_path.is_file():
            raise FileNotFoundError(self.pdf_path)
        self.reader = PdfReader(str(self.pdf_path))
        self._text_cache: dict[int, str] = {}

    def build(self, document_type: str = "auto") -> DocumentMap:
        page_count = len(self.reader.pages)
        if document_type == "auto":
            document_type = self._infer_document_type()

        outline_nodes = self._outline_nodes()
        if document_type == "course_deck" and outline_nodes:
            generic_count = sum(
                bool(GENERIC_SLIDE_TITLE_PATTERN.fullmatch(node.title))
                for node in outline_nodes
            )
            if generic_count / len(outline_nodes) >= 0.8:
                outline_nodes = []
        toc_pages = self._detect_toc_pages()
        toc_entries = self._extract_toc_entries(toc_pages)

        if outline_nodes:
            nodes = outline_nodes
        else:
            nodes = self._nodes_from_toc_entries(toc_entries)
        if document_type == "course_deck":
            nodes = _deduplicate_nodes([*nodes, *self._slide_title_nodes(toc_pages)])
            nodes.sort(key=lambda node: (node.pdf_start, node.level, node.title))

        self._assign_node_ends(nodes, page_count)
        for node in nodes:
            if _normalize_title(node.title) in {"目录", "contents", "目录contents"} and toc_pages:
                node.pdf_end = max(toc_pages)
        self._match_printed_pages(nodes, toc_entries)
        for node in nodes:
            node.title_verified = self._page_contains_title(node.pdf_start, node.title)

        anchors = [
            PageAnchor(
                title=node.title,
                printed_page=node.printed_start,
                pdf_page=node.pdf_start,
                offset=node.pdf_start - node.printed_start,
                title_verified=node.title_verified,
            )
            for node in nodes
            if node.printed_start is not None
        ]
        segments = build_page_map_segments(anchors, page_count)
        self._backfill_printed_pages(nodes, segments)
        text_layer, sampled_ratio = self._text_layer_quality()

        warnings: list[str] = []
        if not nodes:
            warnings.append("未识别到可用书签或目录节点")
        if not segments and document_type == "textbook":
            warnings.append("尚未建立印刷页与 PDF 物理页映射")
        if any(len(segment.anchors) < 2 for segment in segments):
            warnings.append("部分页码映射区段少于两个锚点，需要人工或后续扫描复核")
        if text_layer == "none":
            warnings.append("PDF 缺少可用文本层，需要 OCR")

        metadata = self.reader.metadata
        metadata_title = _metadata_value(metadata, "title")
        digest = sha256_file(self.pdf_path)
        return DocumentMap(
            schema_version="1.0",
            document_id=document_id_from_hash(digest),
            source_path=str(self.pdf_path),
            original_name=self.pdf_path.name,
            document_type=document_type,
            page_count=page_count,
            metadata={
                "title": metadata_title,
                "author": _metadata_value(metadata, "author"),
                "subject": _metadata_value(metadata, "subject"),
                "creator": _metadata_value(metadata, "creator"),
            },
            outline_available=bool(outline_nodes),
            toc_pdf_pages=toc_pages,
            toc_entries=toc_entries,
            text_layer=text_layer,
            sampled_text_page_ratio=sampled_ratio,
            nodes=nodes,
            page_map_segments=segments,
            warnings=warnings,
            subject_ids=list(infer_document_subjects(
                f"{self.pdf_path.name} {metadata_title or ''}"
            )),
        )

    def _infer_document_type(self) -> str:
        if not self.reader.pages:
            return "unknown"
        # A few scanned textbooks keep every page in landscape orientation, so
        # page geometry alone is not a reliable signal.  Very long documents
        # and filenames that explicitly identify a textbook should remain
        # textbook sources even when their first page is wider than tall.
        name = self.pdf_path.stem.lower()
        textbook_hints = (
            "教材",
            "教科书",
            "textbook",
            "生理学",
            "生物化学与分子生物学",
        )
        if len(self.reader.pages) >= 300 or any(hint in name for hint in textbook_hints):
            return "textbook"
        page = self.reader.pages[0]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        return "course_deck" if width > height else "textbook"

    def _text(self, pdf_page: int) -> str:
        if pdf_page < 1 or pdf_page > len(self.reader.pages):
            return ""
        if pdf_page not in self._text_cache:
            try:
                self._text_cache[pdf_page] = (
                    self.reader.pages[pdf_page - 1].extract_text() or ""
                )
            except Exception:
                self._text_cache[pdf_page] = ""
        return self._text_cache[pdf_page]

    def _outline_nodes(self) -> list[StructureNode]:
        try:
            outline = self.reader.outline
        except Exception:
            return []

        nodes: list[StructureNode] = []

        def visit(items: Iterable[Any], level: int) -> None:
            for item in items:
                if isinstance(item, list):
                    visit(item, level + 1)
                    continue
                title = str(getattr(item, "title", "")).strip()
                if not title:
                    continue
                try:
                    pdf_start = self.reader.get_destination_page_number(item) + 1
                except Exception:
                    continue
                nodes.append(
                    StructureNode(
                        level=level,
                        title=_clean_title(title),
                        pdf_start=pdf_start,
                        source="outline",
                    )
                )

        if isinstance(outline, list):
            visit(outline, 1)
        return nodes

    def _detect_toc_pages(self) -> list[int]:
        scan_limit = min(len(self.reader.pages), 30)
        direct: list[int] = []
        for pdf_page in range(1, scan_limit + 1):
            text = self._text(pdf_page)
            if _has_toc_heading(text) and len(_parse_toc_page(text, pdf_page)) >= 2:
                direct.append(pdf_page)

        toc_pages = set(direct)
        for pdf_page in direct:
            next_page = pdf_page + 1
            if next_page <= scan_limit:
                entries = _parse_toc_page(self._text(next_page), next_page)
                numbered = sum(entry.printed_page is not None for entry in entries)
                if numbered >= 3:
                    toc_pages.add(next_page)
        return sorted(toc_pages)

    def _extract_toc_entries(self, toc_pages: Iterable[int]) -> list[TocEntry]:
        entries: list[TocEntry] = []
        for pdf_page in toc_pages:
            entries.extend(_parse_toc_page(self._text(pdf_page), pdf_page))
        return _deduplicate_toc_entries(entries)

    def _nodes_from_toc_entries(self, entries: Iterable[TocEntry]) -> list[StructureNode]:
        nodes: list[StructureNode] = []
        toc_pages = {entry.toc_pdf_page for entry in entries}
        for entry in entries:
            title_key = _normalize_title(entry.title)
            if not title_key:
                continue
            for pdf_page in range(1, len(self.reader.pages) + 1):
                if pdf_page in toc_pages:
                    continue
                page_text = self._text(pdf_page)
                if len(_parse_toc_page(page_text, pdf_page)) >= 2:
                    continue
                prominent_text = "\n".join(page_text.splitlines()[:6])
                page_key = _normalize_title(prominent_text)
                if title_key in page_key:
                    nodes.append(
                        StructureNode(
                            level=_heading_level(entry.title),
                            title=entry.title,
                            pdf_start=pdf_page,
                            printed_start=entry.printed_page,
                            source="toc_scan",
                            title_verified=True,
                        )
                    )
                    break
        nodes.sort(key=lambda node: (node.pdf_start, node.level, node.title))
        return _deduplicate_nodes(nodes)

    def _backfill_printed_pages(
        self,
        nodes: Iterable[StructureNode],
        segments: Iterable[PageMapSegment],
    ) -> None:
        segment_list = list(segments)
        for node in nodes:
            if node.printed_start is not None:
                continue
            for segment in segment_list:
                if segment.pdf_start <= node.pdf_start <= segment.pdf_end:
                    printed_page = node.pdf_start - segment.offset
                    if printed_page >= 1:
                        node.printed_start = printed_page
                    break

    def _slide_title_nodes(self, toc_pages: Iterable[int]) -> list[StructureNode]:
        toc_page_set = set(toc_pages)
        candidates_by_page: dict[int, list[str]] = {}
        frequencies: dict[str, int] = {}
        for pdf_page in range(1, len(self.reader.pages) + 1):
            candidates: list[str] = []
            for raw_line in self._text(pdf_page).splitlines()[:10]:
                title = _clean_title(raw_line)
                key = _normalize_title(title)
                if (
                    len(key) < 2
                    or len(title) > 100
                    or key in {"目录", "contents", "目录contents", "contents目录"}
                    or GENERIC_SLIDE_TITLE_PATTERN.fullmatch(title)
                    or title.isdigit()
                ):
                    continue
                candidates.append(title)
                frequencies[key] = frequencies.get(key, 0) + 1
            candidates_by_page[pdf_page] = candidates

        repeated_limit = max(3, round(len(self.reader.pages) * 0.2))
        nodes: list[StructureNode] = []
        for pdf_page, candidates in candidates_by_page.items():
            if pdf_page in toc_page_set:
                continue
            title = next(
                (
                    candidate
                    for candidate in candidates
                    if frequencies.get(_normalize_title(candidate), 0) <= repeated_limit
                ),
                candidates[0] if candidates else None,
            )
            if title:
                nodes.append(
                    StructureNode(
                        level=3,
                        title=title,
                        pdf_start=pdf_page,
                        source="slide_title",
                        title_verified=True,
                    )
                )
        return nodes

    def _assign_node_ends(self, nodes: list[StructureNode], page_count: int) -> None:
        for index, node in enumerate(nodes):
            node.pdf_end = page_count
            for later in nodes[index + 1 :]:
                if later.pdf_start > node.pdf_start and later.level <= node.level:
                    node.pdf_end = later.pdf_start - 1
                    break

    def _match_printed_pages(
        self, nodes: list[StructureNode], entries: list[TocEntry]
    ) -> None:
        for node in nodes:
            best: TocEntry | None = None
            best_score = 0.0
            node_key = _normalize_title(node.title)
            for entry in entries:
                if entry.printed_page is None:
                    continue
                entry_key = _normalize_title(entry.title)
                if not entry_key or not node_key:
                    continue
                if entry_key in node_key or node_key in entry_key:
                    score = 1.0
                else:
                    score = SequenceMatcher(None, node_key, entry_key).ratio()
                if score > best_score:
                    best_score = score
                    best = entry
            if best and best_score >= 0.72:
                node.printed_start = best.printed_page

    def _page_contains_title(self, pdf_page: int, title: str) -> bool:
        title_key = _normalize_title(title)
        if not title_key:
            return False
        page_key = _normalize_title(self._text(pdf_page)[:1600])
        return title_key in page_key or SequenceMatcher(
            None, title_key, page_key[: max(len(title_key) * 3, 1)]
        ).ratio() >= 0.72

    def _text_layer_quality(self) -> tuple[str, float]:
        page_count = len(self.reader.pages)
        if page_count == 0:
            return "none", 0.0
        sample_count = min(page_count, 16)
        sample_pages = sorted(
            {
                1 + round(index * (page_count - 1) / max(sample_count - 1, 1))
                for index in range(sample_count)
            }
        )
        usable = sum(len(self._text(page).strip()) >= 20 for page in sample_pages)
        ratio = usable / len(sample_pages)
        if ratio >= 0.7:
            return "usable", round(ratio, 3)
        if ratio >= 0.2:
            return "partial", round(ratio, 3)
        return "none", round(ratio, 3)


def build_page_map_segments(
    anchors: Iterable[PageAnchor], page_count: int
) -> list[PageMapSegment]:
    ordered = sorted(anchors, key=lambda item: (item.pdf_page, item.printed_page))
    if not ordered:
        return []

    offset_counts: dict[int, int] = {}
    for anchor in ordered:
        offset_counts[anchor.offset] = offset_counts.get(anchor.offset, 0) + 1
    dominant_offset, dominant_count = max(offset_counts.items(), key=lambda item: item[1])
    if dominant_count >= 3 and dominant_count / len(ordered) >= 0.6:
        ordered = [
            anchor
            for anchor in ordered
            if anchor.offset == dominant_offset or offset_counts[anchor.offset] >= 2
        ]

    groups: list[list[PageAnchor]] = []
    for anchor in ordered:
        if not groups or groups[-1][-1].offset != anchor.offset:
            groups.append([anchor])
        else:
            groups[-1].append(anchor)

    segments: list[PageMapSegment] = []
    for index, group in enumerate(groups):
        offset = group[0].offset
        printed_start = min(anchor.printed_page for anchor in group)
        if index + 1 < len(groups):
            pdf_end = groups[index + 1][0].pdf_page - 1
        else:
            pdf_end = page_count
        pdf_start = printed_start + offset
        if pdf_start > pdf_end:
            continue
        printed_end = max(printed_start, pdf_end - offset)
        verified_ratio = sum(anchor.title_verified for anchor in group) / len(group)
        anchor_factor = min(1.0, len(group) / 2)
        confidence = round(0.55 + 0.25 * anchor_factor + 0.2 * verified_ratio, 3)
        segments.append(
            PageMapSegment(
                printed_start=printed_start,
                printed_end=printed_end,
                pdf_start=pdf_start,
                pdf_end=pdf_end,
                offset=offset,
                confidence=confidence,
                anchors=group,
            )
        )
    return segments


def save_document_map(document_map: DocumentMap, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(_json_safe(document_map.to_dict()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_toc_page(text: str, pdf_page: int) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for raw_line in text.replace("\u3000", " ").splitlines():
        line = raw_line.strip()
        matches = list(HEADING_START_PATTERN.finditer(line))
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            segment = line[start:end].strip()
            page_match = TRAILING_PAGE_PATTERN.search(segment)
            printed_page = int(page_match.group(1)) if page_match else None
            if page_match:
                segment = segment[: page_match.start()].strip()
            title = _clean_title(segment)
            if title:
                entries.append(TocEntry(title, printed_page, pdf_page))
    return entries


def _has_toc_heading(text: str) -> bool:
    for raw_line in text.splitlines()[:10]:
        line = re.sub(r"[\s/\\|·:：-]+", "", raw_line).lower()
        if line in {"目录", "contents", "目录contents", "contents目录"}:
            return True
    return False


def _deduplicate_toc_entries(entries: Iterable[TocEntry]) -> list[TocEntry]:
    result: list[TocEntry] = []
    seen: set[tuple[str, int | None]] = set()
    for entry in entries:
        key = (_normalize_title(entry.title), entry.printed_page)
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return result


def _deduplicate_nodes(nodes: Iterable[StructureNode]) -> list[StructureNode]:
    result: list[StructureNode] = []
    seen: set[tuple[str, int]] = set()
    for node in nodes:
        key = (_normalize_title(node.title), node.pdf_start)
        if key not in seen:
            seen.add(key)
            result.append(node)
    return result


def _heading_level(title: str) -> int:
    compact = re.sub(r"\s+", "", title)
    if "章" in compact or "篇" in compact or "部" in compact:
        return 1
    return 2


def _clean_title(title: str) -> str:
    utf8_safe = title.encode("utf-8", errors="replace").decode("utf-8")
    return re.sub(r"\s+", " ", utf8_safe).strip(" \t.-·…")


def _normalize_title(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", title).lower()


def _metadata_value(metadata: Any, name: str) -> str | None:
    if metadata is None:
        return None
    value = getattr(metadata, name, None)
    return str(value) if value is not None else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value
