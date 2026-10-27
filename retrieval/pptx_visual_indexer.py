"""Opt-in visual indexing for PPTX slides whose diagrams need richer text.

The normal PPTX importer remains entirely local.  This module is only called
after an explicit UI approval and sends a rendered complete slide (falling
back to its dominant embedded image) to the configured vision provider.
Low-confidence output is
recorded as needing review and is not added to the searchable evidence text.
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol
from xml.etree import ElementTree as ET

from retrieval.page_text_index import (
    PageTextIndex,
    PageTextRecord,
    load_page_text_index,
    save_page_text_index,
)
from retrieval.pptx_structure_indexer import (
    DML,
    PKG_REL,
    PML,
    _ordered_slide_paths,
    save_pptx_document_map,
)
from retrieval.pptx_slide_renderer import SlideRenderUnavailable, render_pptx_slides


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# A title plus a diagram commonly has some local text but still loses arrows,
# labels and spatial relations when only the PPTX text layer is indexed.
# Longer, prose-heavy slides remain local-text only to control API cost.
DEFAULT_MAX_LOCAL_TEXT_CHARS = 240
DEFAULT_MIN_CONFIDENCE = 0.75


class VisionClient(Protocol):
    def analyze_image(
        self,
        image: str | Path | bytes,
        prompt: str,
        *,
        media_type: str | None = None,
        max_image_bytes: int = ...,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class VisualSlideCandidate:
    slide_number: int
    title: str
    image_path: str
    image_count: int
    media_type: str


@dataclass(frozen=True)
class VisualIndexPlan:
    document_id: str
    original_name: str
    candidate_count: int
    candidates: tuple[VisualSlideCandidate, ...]


@dataclass(frozen=True)
class VisualIndexReport:
    document_id: str
    attempted: int
    indexed: int
    review_required: int
    failed: int
    cancelled: bool = False


def build_visual_index_plan(
    document_map_path: str | Path,
    page_index_path: str | Path,
    *,
    max_local_text_chars: int = DEFAULT_MAX_LOCAL_TEXT_CHARS,
) -> VisualIndexPlan:
    map_data = json.loads(Path(document_map_path).read_text(encoding="utf-8"))
    if map_data.get("source_format") != "pptx":
        return VisualIndexPlan(
            str(map_data.get("document_id", "")),
            str(map_data.get("original_name", "")),
            0,
            (),
        )
    index = load_page_text_index(page_index_path)
    source = Path(map_data["source_path"])
    page_by_number = {page.pdf_page: page for page in index.pages}
    completed = {
        int(value) for value in map_data.get("visual_indexed_pages", [])
        if str(value).isdigit()
    }
    completed.update(
        int(value) for value in map_data.get("visual_dismissed_pages", [])
        if str(value).isdigit()
    )
    candidates: list[VisualSlideCandidate] = []
    with zipfile.ZipFile(source) as archive:
        slide_width, slide_height = _presentation_size(archive)
        for slide_number, slide_path in enumerate(
            _ordered_slide_paths(archive), start=1
        ):
            page = page_by_number.get(slide_number)
            if page is None or slide_number in completed:
                continue
            images = _slide_image_paths(archive, slide_path)
            picture_ratio, graphic_frames, connectors = _slide_visual_metrics(
                archive, slide_path, slide_width, slide_height
            )
            text_chars = len(page.text.strip())
            graphically_substantive = (
                picture_ratio >= 0.12
                or graphic_frames >= 1
                or connectors >= 2
                or (len(images) >= 2 and picture_ratio >= 0.05)
            )
            if text_chars and (
                text_chars > max_local_text_chars or not graphically_substantive
            ):
                continue
            if not images and not graphically_substantive:
                continue
            primary = (
                max(images, key=lambda name: archive.getinfo(name).file_size)
                if images else ""
            )
            media_type = (
                mimetypes.guess_type(primary)[0] or "application/octet-stream"
                if primary else "image/png"
            )
            candidates.append(VisualSlideCandidate(
                slide_number=slide_number,
                title=page.title or f"幻灯片 {slide_number}",
                image_path=primary,
                image_count=len(images),
                media_type=media_type,
            ))
    return VisualIndexPlan(
        document_id=str(map_data["document_id"]),
        original_name=str(map_data.get("original_name", source.name)),
        candidate_count=len(candidates),
        candidates=tuple(candidates),
    )


def augment_pptx_visual_index(
    document_map_path: str | Path,
    page_index_path: str | Path,
    vision_client: VisionClient,
    *,
    progress: Callable[[str, int, int], None] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    slide_numbers: set[int] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> VisualIndexReport:
    map_path = Path(document_map_path)
    index_path = Path(page_index_path)
    map_data = json.loads(map_path.read_text(encoding="utf-8"))
    index = load_page_text_index(index_path)
    plan = build_visual_index_plan(map_path, index_path)
    candidates = tuple(
        candidate for candidate in plan.candidates
        if slide_numbers is None or candidate.slide_number in slide_numbers
    )
    page_by_number = {page.pdf_page: page for page in index.pages}
    indexed_pages = {
        int(value) for value in map_data.get("visual_indexed_pages", [])
        if str(value).isdigit()
    }
    review_pages = {
        int(value) for value in map_data.get("visual_review_pages", [])
        if str(value).isdigit()
    }
    failed_pages = {
        int(value) for value in map_data.get("visual_failed_pages", [])
        if str(value).isdigit()
    }
    failure_reasons = {
        str(key): str(value)
        for key, value in (map_data.get("visual_failure_reasons") or {}).items()
    }
    review_candidates = dict(map_data.get("visual_review_candidates") or {})
    indexed = review_required = failed = 0
    source = Path(map_data["source_path"])
    with tempfile.TemporaryDirectory(prefix="pptx-slide-render-") as render_dir:
        try:
            rendered_slides = render_pptx_slides(
                source,
                (candidate.slide_number for candidate in candidates),
                render_dir,
            )
        except SlideRenderUnavailable:
            rendered_slides = {}
        with zipfile.ZipFile(source) as archive:
            cancelled = _augment_candidates(
                archive,
                candidates,
                rendered_slides,
                page_by_number,
                plan,
                vision_client,
                progress,
                min_confidence,
                indexed_pages,
                review_pages,
                failed_pages,
                failure_reasons,
                review_candidates,
                cancel_check,
            )
    indexed = sum(
        1 for candidate in candidates if candidate.slide_number in indexed_pages
    )
    review_required = sum(
        1 for candidate in candidates if candidate.slide_number in review_pages
    )
    failed = sum(
        1 for candidate in candidates if candidate.slide_number in failed_pages
    )

    map_data["visual_indexed_pages"] = sorted(indexed_pages)
    map_data["visual_review_pages"] = sorted(review_pages)
    map_data["visual_failed_pages"] = sorted(failed_pages)
    map_data["visual_failure_reasons"] = failure_reasons
    map_data["visual_review_candidates"] = review_candidates
    map_data["visual_indexed_at"] = datetime.now(timezone.utc).isoformat()
    nonempty = sum(bool(page.text.strip()) for page in index.pages)
    map_data["sampled_text_page_ratio"] = nonempty / len(index.pages) if index.pages else 0.0
    map_data["text_layer"] = (
        "full" if index.pages and nonempty == len(index.pages) else "partial"
    )
    warnings = [
        str(item) for item in map_data.get("warnings", [])
        if "图片内容可能需要OCR" not in str(item)
        and "图片页需要人工复核" not in str(item)
        and "图片页识别失败" not in str(item)
    ]
    if review_pages:
        warnings.append(f"{len(review_pages)} 张图片页需要人工复核，未进入回答证据")
    if failed_pages:
        warnings.append(f"{len(failed_pages)} 张图片页识别失败，可稍后重试")
    map_data["warnings"] = warnings
    save_page_text_index(index, index_path)
    save_pptx_document_map(map_data, map_path)
    return VisualIndexReport(
        document_id=plan.document_id,
        attempted=len(candidates),
        indexed=indexed,
        review_required=review_required,
        failed=failed,
        cancelled=cancelled,
    )


def _augment_candidates(
    archive: zipfile.ZipFile,
    candidates: tuple[VisualSlideCandidate, ...],
    rendered_slides: dict[int, Path],
    page_by_number: dict[int, PageTextRecord],
    plan: VisualIndexPlan,
    vision_client: VisionClient,
    progress: Callable[[str, int, int], None] | None,
    min_confidence: float,
    indexed_pages: set[int],
    review_pages: set[int],
    failed_pages: set[int],
    failure_reasons: dict[str, str],
    review_candidates: dict,
    cancel_check: Callable[[], bool] | None,
) -> bool:
    for position, candidate in enumerate(candidates, start=1):
        if cancel_check is not None and cancel_check():
            return True
        if progress:
            progress(
                f"正在识别 {plan.original_name} 第 {candidate.slide_number} 张",
                position,
                len(candidates),
            )
        page = page_by_number[candidate.slide_number]
        try:
            prompt = (
                "请逐字转录这张课程幻灯片图片中的标题、正文、表格、图注、"
                "坐标轴和代谢通路标签。仅描述图片中明确可见的箭头或关系，"
                "不要补充课外知识，不要推断被遮挡内容。"
            )
            rendered = rendered_slides.get(candidate.slide_number)
            if rendered is not None:
                image_bytes = rendered.read_bytes()
                image_media_type = "image/png"
                source_ref = f"rendered_slide:{candidate.slide_number}"
            else:
                if not candidate.image_path:
                    raise SlideRenderUnavailable(
                        "该页包含PPT原生图表，必须先完成整页渲染"
                    )
                image_bytes = archive.read(candidate.image_path)
                image_media_type = candidate.media_type
                source_ref = candidate.image_path
            course_method = getattr(vision_client, "analyze_course_slide", None)
            if callable(course_method):
                raw = course_method(image_bytes, prompt, media_type=image_media_type)
            else:
                raw = vision_client.analyze_image(
                    image_bytes, prompt, media_type=image_media_type
                )
            text = _visual_text(raw)
            confidence = _confidence(raw.get("overall_confidence"))
            uncertain = raw.get("uncertain_spans", [])
            uncertain_spans = (
                [str(item) for item in uncertain if str(item).strip()]
                if isinstance(uncertain, list) else []
            )
            needs_review = (
                bool(raw.get("requires_recheck", False)) or bool(uncertain_spans)
            )
            image_sha256 = hashlib.sha256(image_bytes).hexdigest()
            model_identity = str(
                getattr(vision_client, "model_identity", "configured_vision_model")
            )
            page.content_origin = "visual_ocr"
            page.visual_source_ref = source_ref
            page.visual_image_sha256 = image_sha256
            page.visual_model = model_identity
            page.visual_confidence = confidence
            page.visual_uncertain_spans = uncertain_spans
            page.visual_verified_by_user = False
            if not text or confidence < min_confidence or needs_review:
                page.extraction_status = "visual_review_required"
                page.visual_requires_recheck = True
                review_pages.add(candidate.slide_number)
                review_candidates[str(candidate.slide_number)] = {
                    "text": text,
                    "confidence": confidence,
                    "uncertain_spans": uncertain_spans,
                    "source_ref": source_ref,
                    "image_sha256": image_sha256,
                    "model": model_identity,
                }
                failure_reasons.pop(str(candidate.slide_number), None)
                continue
            original = page.text.strip()
            page.text = (
                f"{original}\n【图片文字识别】\n{text}" if original
                else f"【图片文字识别】\n{text}"
            )
            page.char_count = len(page.text)
            page.extraction_status = "visual_ocr"
            page.visual_requires_recheck = False
            if not page.title or page.title == f"幻灯片 {candidate.slide_number}":
                page.title = _first_line_title(text, candidate.slide_number)
            indexed_pages.add(candidate.slide_number)
            review_pages.discard(candidate.slide_number)
            review_candidates.pop(str(candidate.slide_number), None)
            failed_pages.discard(candidate.slide_number)
            failure_reasons.pop(str(candidate.slide_number), None)
        except Exception as exc:
            page.extraction_status = "visual_ocr_failed"
            failed_pages.add(candidate.slide_number)
            failure_reasons[str(candidate.slide_number)] = (
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )
    return False


def extract_slide_preview(
    source: str | Path,
    slide_number: int,
) -> tuple[bytes, str]:
    """Return a complete rendered slide when possible, else its largest image."""

    source_path = Path(source)
    with tempfile.TemporaryDirectory(prefix="pptx-slide-preview-") as render_dir:
        try:
            rendered = render_pptx_slides(source_path, [slide_number], render_dir)
            return rendered[slide_number].read_bytes(), "image/png"
        except SlideRenderUnavailable:
            pass
    with zipfile.ZipFile(source_path) as archive:
        slide_paths = _ordered_slide_paths(archive)
        if slide_number < 1 or slide_number > len(slide_paths):
            raise IndexError(f"幻灯片页码超出范围: {slide_number}")
        images = _slide_image_paths(archive, slide_paths[slide_number - 1])
        if not images:
            raise ValueError(f"幻灯片 {slide_number} 没有可预览图片")
        primary = max(images, key=lambda name: archive.getinfo(name).file_size)
        return archive.read(primary), mimetypes.guess_type(primary)[0] or "image/png"


def _slide_image_paths(archive: zipfile.ZipFile, slide_path: str) -> list[str]:
    slide = PurePosixPath(slide_path)
    rel_path = str(slide.parent / "_rels" / f"{slide.name}.rels")
    try:
        root = ET.fromstring(archive.read(rel_path))
    except (KeyError, ET.ParseError):
        return []
    names = set(archive.namelist())
    images: list[str] = []
    for relation in root.findall(f"{{{PKG_REL}}}Relationship"):
        relation_type = str(relation.attrib.get("Type", ""))
        target = str(relation.attrib.get("Target", ""))
        if not relation_type.endswith("/image") or not target:
            continue
        normalized = _resolve_zip_target(slide.parent, target)
        if normalized in names and PurePosixPath(normalized).suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            images.append(normalized)
    return list(dict.fromkeys(images))


def _presentation_size(archive: zipfile.ZipFile) -> tuple[int, int]:
    try:
        root = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = root.find(f"{{{PML}}}sldSz")
        if size is not None:
            return int(size.attrib.get("cx", 0)), int(size.attrib.get("cy", 0))
    except (KeyError, ET.ParseError, TypeError, ValueError):
        pass
    return 0, 0


def _slide_visual_metrics(
    archive: zipfile.ZipFile,
    slide_path: str,
    slide_width: int,
    slide_height: int,
) -> tuple[float, int, int]:
    """Estimate whether visuals are instructional rather than a tiny logo."""

    try:
        root = ET.fromstring(archive.read(slide_path))
    except (KeyError, ET.ParseError):
        return 0.0, 0, 0
    total_area = max(slide_width * slide_height, 1)
    picture_area = 0
    for picture in root.findall(f".//{{{PML}}}pic"):
        extent = picture.find(f".//{{{DML}}}xfrm/{{{DML}}}ext")
        if extent is None:
            continue
        try:
            picture_area += int(extent.attrib.get("cx", 0)) * int(
                extent.attrib.get("cy", 0)
            )
        except (TypeError, ValueError):
            continue
    return (
        min(picture_area / total_area, 1.0),
        len(root.findall(f".//{{{PML}}}graphicFrame")),
        len(root.findall(f".//{{{PML}}}cxnSp")),
    )


def _resolve_zip_target(base: PurePosixPath, target: str) -> str:
    parts: list[str] = []
    for part in (base / target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _visual_text(raw: Mapping[str, object]) -> str:
    for key in ("plain_text", "question_text"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"[ \t]+", " ", value.strip())
    return ""


def _confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _first_line_title(text: str, slide_number: int) -> str:
    for line in text.splitlines():
        line = line.strip()
        if 2 <= len(line) <= 100:
            return line
    return f"幻灯片 {slide_number}"
