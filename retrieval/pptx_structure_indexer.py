"""Extract searchable slide text and structure from PPTX course decks."""

from __future__ import annotations

import json
import os
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from retrieval.document_importer import document_id_from_hash, sha256_file
from retrieval.page_text_index import PageTextIndex, PageTextRecord
from core.subject_profiles import infer_document_subjects


PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def build_pptx_indexes(path: str | Path) -> tuple[dict, PageTextIndex]:
    source = Path(path).expanduser().absolute()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".pptx":
        raise ValueError(f"不是 PPTX 文件: {source.name}")

    digest = sha256_file(source)
    document_id = document_id_from_hash(digest)
    with zipfile.ZipFile(source) as archive:
        slide_paths = _ordered_slide_paths(archive)
        pages = []
        nodes = []
        for slide_number, slide_path in enumerate(slide_paths, start=1):
            try:
                paragraphs = _slide_paragraphs(archive.read(slide_path))
                title = _slide_title(paragraphs, slide_number)
                text = "\n".join(paragraphs).strip()
                status = "ok" if text else "empty"
            except Exception as exc:
                title = f"幻灯片 {slide_number}"
                text = ""
                status = f"error:{type(exc).__name__}"
            pages.append(PageTextRecord(
                pdf_page=slide_number,
                title=title,
                text=text,
                char_count=len(text),
                extraction_status=status,
            ))
            nodes.append({
                "level": 2,
                "title": title,
                "pdf_start": slide_number,
                "pdf_end": slide_number,
                "printed_start": None,
                "source": "slide_title",
                "title_verified": bool(text),
            })

    first_text = " ".join(page.text for page in pages[:5])
    subject_ids = list(infer_document_subjects(f"{source.name} {first_text}"))
    nonempty = sum(bool(page.text.strip()) for page in pages)
    warnings = []
    if pages and nonempty / len(pages) < 0.5:
        warnings.append("超过一半的幻灯片没有可提取文本，图片内容可能需要OCR")
    if not pages:
        warnings.append("PPTX中没有识别到幻灯片")
    document_map = {
        "schema_version": "1.0",
        "document_id": document_id,
        "source_path": str(source),
        "original_name": source.name,
        "document_type": "course_deck",
        "page_count": len(pages),
        "metadata": {"title": pages[0].title if pages else source.stem},
        "outline_available": False,
        "toc_pdf_pages": [],
        "toc_entries": [],
        "text_layer": "full" if nonempty == len(pages) and pages else "partial",
        "sampled_text_page_ratio": nonempty / len(pages) if pages else 0.0,
        "nodes": nodes,
        "page_map_segments": [],
        "warnings": warnings,
        "subject_ids": subject_ids,
        "source_format": "pptx",
    }
    page_index = PageTextIndex(
        schema_version="1.0",
        document_id=document_id,
        source_path=str(source),
        page_count=len(pages),
        pages=pages,
    )
    return document_map, page_index


def save_pptx_document_map(document_map: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(document_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _ordered_slide_paths(archive: zipfile.ZipFile) -> list[str]:
    names = set(archive.namelist())
    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            item.attrib.get("Id", ""): item.attrib.get("Target", "")
            for item in rels.findall(f"{{{PKG_REL}}}Relationship")
        }
        ordered = []
        for slide_id in presentation.findall(f".//{{{PML}}}sldId"):
            rel_id = slide_id.attrib.get(f"{{{REL}}}id", "")
            target = targets.get(rel_id, "")
            normalized = str(PurePosixPath("ppt") / target)
            normalized = str(PurePosixPath(normalized))
            while "/../" in normalized:
                normalized = re.sub(r"[^/]+/\.\./", "", normalized, count=1)
            if normalized in names:
                ordered.append(normalized)
        if ordered:
            return ordered
    except (KeyError, ET.ParseError):
        pass
    slide_pattern = re.compile(r"ppt/slides/slide(\d+)\.xml$")
    numbered = []
    for name in names:
        match = slide_pattern.fullmatch(name)
        if match:
            numbered.append((int(match.group(1)), name))
    return [name for _, name in sorted(numbered)]


def _slide_paragraphs(xml_data: bytes) -> list[str]:
    root = ET.fromstring(xml_data)
    paragraphs = []
    for paragraph in root.findall(f".//{{{DML}}}p"):
        text = "".join(
            node.text or "" for node in paragraph.findall(f".//{{{DML}}}t")
        )
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paragraphs.append(text)
    if paragraphs:
        return paragraphs
    return [
        re.sub(r"\s+", " ", node.text or "").strip()
        for node in root.findall(f".//{{{DML}}}t")
        if (node.text or "").strip()
    ]


def _slide_title(paragraphs: list[str], slide_number: int) -> str:
    for paragraph in paragraphs:
        if 2 <= len(paragraph) <= 100:
            return paragraph
    return f"幻灯片 {slide_number}"
