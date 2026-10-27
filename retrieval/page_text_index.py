"""Persistent per-page text index built once during PDF ingestion."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from retrieval.document_importer import document_id_from_hash, sha256_file


@dataclass
class PageTextRecord:
    pdf_page: int
    title: str | None
    text: str
    char_count: int
    extraction_status: str
    content_origin: str = "native_text"
    visual_source_ref: str | None = None
    visual_image_sha256: str | None = None
    visual_model: str | None = None
    visual_confidence: float | None = None
    visual_uncertain_spans: list[str] = field(default_factory=list)
    visual_requires_recheck: bool = False
    visual_verified_by_user: bool = False


@dataclass
class PageTextIndex:
    schema_version: str
    document_id: str
    source_path: str
    page_count: int
    pages: list[PageTextRecord]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_page_text_index(document_map_path: str | Path) -> PageTextIndex:
    map_path = Path(document_map_path)
    data = json.loads(map_path.read_text(encoding="utf-8"))
    source_path = Path(data["source_path"])
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    current_document_id = document_id_from_hash(sha256_file(source_path))
    if current_document_id != data["document_id"]:
        raise ValueError("PDF 内容已变化，请先重新生成 DocumentMap")

    titles_by_page: dict[int, str] = {}
    for node in data.get("nodes", []):
        if node.get("source") == "slide_title" and node.get("title"):
            titles_by_page.setdefault(int(node["pdf_start"]), str(node["title"]))

    reader = PdfReader(str(source_path))
    pages: list[PageTextRecord] = []
    for pdf_page, page in enumerate(reader.pages, start=1):
        try:
            text = _safe_text(page.extract_text() or "")
            status = "ok" if text.strip() else "empty"
        except Exception as exc:
            text = ""
            status = f"error:{type(exc).__name__}"
        pages.append(
            PageTextRecord(
                pdf_page=pdf_page,
                title=titles_by_page.get(pdf_page),
                text=text,
                char_count=len(text.strip()),
                extraction_status=status,
            )
        )

    return PageTextIndex(
        schema_version="1.0",
        document_id=data["document_id"],
        source_path=str(source_path),
        page_count=len(pages),
        pages=pages,
    )


def save_page_text_index(index: PageTextIndex, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_page_text_index(path: str | Path) -> PageTextIndex:
    index_path = Path(path)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0":
        raise ValueError(f"不支持的页索引版本: {data.get('schema_version')!r}")
    index = PageTextIndex(
        schema_version=data["schema_version"],
        document_id=data["document_id"],
        source_path=data["source_path"],
        page_count=int(data["page_count"]),
        pages=[PageTextRecord(**page) for page in data.get("pages", [])],
    )
    _apply_figure_transcriptions(index, index_path.parent.parent / "figure_transcriptions.json")
    return index


def _apply_figure_transcriptions(index: PageTextIndex, path: Path) -> None:
    """Overlay checked diagram labels without altering the extracted PDF index.

    The document ID is a hash of the source PDF, so a transcription cannot
    silently attach to a different edition after the library is rebuilt.
    """
    if not path.is_file():
        return
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("图示转录文件必须是列表")
    pages = {page.pdf_page: page for page in index.pages}
    for entry in entries:
        if entry.get("document_id") != index.document_id:
            continue
        page = pages.get(int(entry["pdf_page"]))
        if page is None:
            raise ValueError(f"图示转录页不存在: PDF {entry['pdf_page']}")
        caption = str(entry["caption"]).strip()
        transcription = str(entry["transcription"]).strip()
        if not caption or not transcription or caption not in page.text:
            raise ValueError(f"图示转录无法匹配原页图题: PDF {page.pdf_page}")
        marker = f"[图示对照转录：{caption}]"
        if marker in page.text:
            continue
        page.text = f"{page.text.rstrip()}\n{marker}\n{transcription}"
        page.char_count = len(page.text.strip())
        page.content_origin = "native_text_plus_figure_transcription"
        page.visual_source_ref = f"{index.source_path}#page={page.pdf_page}"
        page.visual_model = "agent_visual_inspection"
        page.visual_requires_recheck = True


def _safe_text(value: str) -> str:
    return value.encode("utf-8", errors="replace").decode("utf-8")
