"""Managed PDF/PPTX knowledge-base ingestion and recoverable removal."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from retrieval.document_importer import (
    DocumentImportManifest,
    DocumentImporter,
    ImportStatus,
)
from retrieval.page_text_index import build_page_text_index, save_page_text_index
from retrieval.pdf_structure_indexer import PdfStructureIndexer, save_document_map
from retrieval.pptx_structure_indexer import build_pptx_indexes, save_pptx_document_map
from retrieval.pptx_visual_indexer import (
    VisualIndexPlan,
    VisualIndexReport,
    VisionClient,
    augment_pptx_visual_index,
    build_visual_index_plan,
    extract_slide_preview,
)
from retrieval.page_text_index import load_page_text_index
from core.subject_profiles import infer_document_subjects, subject_display_names, valid_subject_ids


ProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    original_name: str
    document_type: str
    page_count: int
    text_layer: str
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    managed_copy: bool = False
    subject_ids: tuple[str, ...] = field(default_factory=tuple)
    visual_indexed_pages: int = 0
    visual_review_pages: int = 0
    visual_failed_pages: int = 0

    @property
    def subject_names(self) -> tuple[str, ...]:
        return subject_display_names(self.subject_ids)


@dataclass(frozen=True)
class VisualReviewItem:
    document_id: str
    original_name: str
    slide_number: int
    proposed_text: str
    confidence: float | None
    uncertain_spans: tuple[str, ...]
    model: str | None


class KnowledgeBaseService:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).absolute()
        self.data_dir = self.base_dir / "data"
        self.storage_dir = self.data_dir / "library"
        self.map_dir = self.data_dir / "document_maps"
        self.index_dir = self.data_dir / "page_indexes"
        self.trash_dir = self.data_dir / "knowledge_trash"
        self.manifest_path = self.data_dir / "document_imports.json"
        self._migrate_legacy_visual_provenance()

    def _migrate_legacy_visual_provenance(self) -> None:
        """Mark OCR text created before provenance fields were introduced."""

        if not self.map_dir.exists() or not self.index_dir.exists():
            return
        for map_path in self.map_dir.glob("*.json"):
            try:
                data = json.loads(map_path.read_text(encoding="utf-8"))
                if data.get("source_format") != "pptx":
                    continue
                document_id = str(data["document_id"])
                index_path = self.index_dir / f"{document_id}-pages.json"
                if not index_path.is_file():
                    continue
                visual_pages = {
                    int(value) for value in data.get("visual_indexed_pages", [])
                }
                review_pages = {
                    int(value) for value in data.get("visual_review_pages", [])
                }
                if not visual_pages and not review_pages:
                    continue
                index = load_page_text_index(index_path)
                changed = False
                for page in index.pages:
                    if page.pdf_page not in visual_pages | review_pages:
                        continue
                    if page.content_origin != "native_text":
                        continue
                    page.content_origin = "visual_ocr"
                    page.visual_source_ref = f"legacy_slide:{page.pdf_page}"
                    page.visual_model = "legacy_visual_index"
                    page.visual_verified_by_user = False
                    page.visual_requires_recheck = page.pdf_page in review_pages
                    changed = True
                if changed:
                    save_page_text_index(index, index_path)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def list_documents(self) -> tuple[KnowledgeDocument, ...]:
        managed_ids = {
            item.document_id
            for item in self._manifest().items
            if item.document_id and item.status != ImportStatus.REMOVED.value
        }
        documents: list[KnowledgeDocument] = []
        for path in sorted(self.map_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                document_id = str(data["document_id"])
                warnings = tuple(str(item) for item in data.get("warnings", []))
                text_layer = str(data.get("text_layer", "unknown"))
                status = "needs_ocr" if text_layer == "none" else "indexed"
                subject_ids = valid_subject_ids(data.get("subject_ids", []))
                if not subject_ids:
                    subject_ids = infer_document_subjects(
                        " ".join((
                            str(data.get("original_name", path.stem)),
                            str((data.get("metadata") or {}).get("title", "")),
                        ))
                    )
                documents.append(
                    KnowledgeDocument(
                        document_id=document_id,
                        original_name=str(data.get("original_name", path.stem)),
                        document_type=str(data.get("document_type", "unknown")),
                        page_count=int(data.get("page_count", 0)),
                        text_layer=text_layer,
                        status=status,
                        warnings=warnings,
                        managed_copy=document_id in managed_ids,
                        subject_ids=subject_ids,
                        visual_indexed_pages=len(data.get("visual_indexed_pages", [])),
                        visual_review_pages=len(data.get("visual_review_pages", [])),
                        visual_failed_pages=len(data.get("visual_failed_pages", [])),
                    )
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return tuple(sorted(documents, key=lambda item: item.original_name.lower()))

    def import_pdfs(
        self,
        source_paths: Iterable[str | Path],
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[KnowledgeDocument, ...]:
        """Backward-compatible alias for course document import."""
        return self.import_documents(source_paths, progress=progress)

    def import_documents(
        self,
        source_paths: Iterable[str | Path],
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[KnowledgeDocument, ...]:
        sources = [Path(path).expanduser().resolve() for path in source_paths]
        if not sources:
            return ()
        for source in sources:
            if source.suffix.lower() not in {".pdf", ".pptx"}:
                raise ValueError(f"当前知识库索引仅支持 PDF 或 PPTX: {source.name}")

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.map_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        manifest = self._manifest()
        importer = DocumentImporter(self.storage_dir, manifest)
        queued = importer.enqueue(sources)
        importer.process_pending()

        total = len(queued)
        imported_ids: set[str] = set()
        for position, item in enumerate(queued, start=1):
            if progress:
                progress(f"正在处理 {item.original_name}", position, total)
            if item.status == ImportStatus.DUPLICATE.value:
                if item.document_id:
                    imported_ids.add(item.document_id)
                continue
            if item.status != ImportStatus.COPIED.value or not item.stored_path:
                continue
            try:
                item.status = ImportStatus.STRUCTURE_INDEXING.value
                manifest.save()
                stored_path = Path(item.stored_path)
                if stored_path.suffix.lower() == ".pptx":
                    document_map_data, page_index = build_pptx_indexes(stored_path)
                    document_id = str(document_map_data["document_id"])
                    map_path = self.map_dir / f"{document_id}.json"
                    save_pptx_document_map(document_map_data, map_path)
                else:
                    document_map = PdfStructureIndexer(stored_path).build("auto")
                    document_id = document_map.document_id
                    map_path = self.map_dir / f"{document_id}.json"
                    save_document_map(document_map, map_path)
                    page_index = build_page_text_index(map_path)
                index_path = self.index_dir / f"{document_id}-pages.json"
                save_page_text_index(page_index, index_path)
                item.status = ImportStatus.INDEXED.value
                item.error = None
                imported_ids.add(document_id)
            except Exception as exc:
                item.status = ImportStatus.FAILED.value
                item.error = f"{type(exc).__name__}: {exc}"
            finally:
                item.updated_at = datetime.now(timezone.utc).isoformat()
                manifest.save()
        inventory = self.list_documents()
        return tuple(item for item in inventory if item.document_id in imported_ids)

    def remove_document(self, document_id: str) -> Path:
        """Remove indexes and managed copy into a recoverable local trash folder."""

        if not document_id.startswith("doc_"):
            raise ValueError("无效的文档ID")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.trash_dir / f"{timestamp}-{document_id}"
        destination.mkdir(parents=True, exist_ok=False)

        moved = 0
        for path in [*self.map_dir.glob("*.json"), *self.index_dir.glob("*.json")]:
            if self._json_document_id(path) != document_id:
                continue
            shutil.move(str(path), str(destination / path.name))
            moved += 1

        managed_dir = self.storage_dir / document_id
        if managed_dir.is_dir():
            shutil.move(str(managed_dir), str(destination / managed_dir.name))
            moved += 1
        if not moved:
            destination.rmdir()
            raise KeyError(f"知识库中不存在文档: {document_id}")

        manifest = self._manifest()
        for item in manifest.items:
            if item.document_id == document_id:
                item.status = ImportStatus.REMOVED.value
                item.updated_at = datetime.now(timezone.utc).isoformat()
        manifest.save()
        return destination

    def set_document_subjects(
        self,
        document_id: str,
        subject_ids: Iterable[str],
    ) -> tuple[str, ...]:
        """Persist user-confirmed subject tags in the document map."""
        selected = valid_subject_ids(subject_ids)
        if not selected:
            raise ValueError("请至少选择一个有效医学学科")
        for path in self.map_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if str(data.get("document_id")) != document_id:
                continue
            data["subject_ids"] = list(selected)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return selected
        raise KeyError(f"知识库中不存在文档: {document_id}")

    def visual_index_plan(self, document_id: str) -> VisualIndexPlan:
        map_path, index_path = self._document_index_paths(document_id)
        return build_visual_index_plan(map_path, index_path)

    def index_document_visuals(
        self,
        document_id: str,
        vision_client: VisionClient,
        *,
        progress: ProgressCallback | None = None,
        slide_numbers: set[int] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> VisualIndexReport:
        """Index visually substantive PPTX slides after explicit UI approval."""
        map_path, index_path = self._document_index_paths(document_id)
        return augment_pptx_visual_index(
            map_path,
            index_path,
            vision_client,
            progress=progress,
            slide_numbers=slide_numbers,
            cancel_check=cancel_check,
        )

    def list_visual_reviews(
        self, document_id: str | None = None
    ) -> tuple[VisualReviewItem, ...]:
        items: list[VisualReviewItem] = []
        for map_path in self.map_dir.glob("*.json"):
            data = json.loads(map_path.read_text(encoding="utf-8"))
            if document_id and str(data.get("document_id")) != document_id:
                continue
            candidates = data.get("visual_review_candidates") or {}
            index_path = self.index_dir / f"{data['document_id']}-pages.json"
            index = load_page_text_index(index_path) if index_path.is_file() else None
            page_records = {
                page.pdf_page: page for page in (index.pages if index else [])
            }
            review_pages = {
                int(value) for value in data.get("visual_review_pages", [])
            }
            review_pages.update(
                page.pdf_page for page in page_records.values()
                if page.content_origin == "visual_ocr"
                and not page.visual_verified_by_user
                and page.extraction_status != "visual_dismissed"
            )
            for page in review_pages:
                candidate = candidates.get(str(page), {})
                record = page_records.get(page)
                proposed = str(candidate.get("text", ""))
                if not proposed and record is not None:
                    proposed = _visual_text_only(record.text)
                items.append(VisualReviewItem(
                    document_id=str(data["document_id"]),
                    original_name=str(data.get("original_name", map_path.stem)),
                    slide_number=page,
                    proposed_text=proposed,
                    confidence=(
                        float(candidate["confidence"])
                        if candidate.get("confidence") is not None
                        else (record.visual_confidence if record is not None else None)
                    ),
                    uncertain_spans=tuple(
                        str(value) for value in (
                            candidate.get("uncertain_spans", [])
                            or (record.visual_uncertain_spans if record is not None else [])
                        )
                    ),
                    model=(
                        str(candidate.get("model"))
                        if candidate.get("model")
                        else (record.visual_model if record is not None else None)
                    ),
                ))
        return tuple(sorted(items, key=lambda item: (item.original_name, item.slide_number)))

    def visual_review_image(
        self, document_id: str, slide_number: int
    ) -> tuple[bytes, str]:
        map_path, _ = self._document_index_paths(document_id)
        data = json.loads(map_path.read_text(encoding="utf-8"))
        return extract_slide_preview(data["source_path"], slide_number)

    def confirm_visual_review(
        self,
        document_id: str,
        slide_number: int,
        corrected_text: str,
    ) -> None:
        text = corrected_text.strip()
        if not text:
            raise ValueError("确认后的图片文字不能为空")
        map_path, index_path = self._document_index_paths(document_id)
        data = json.loads(map_path.read_text(encoding="utf-8"))
        index = load_page_text_index(index_path)
        page = next(
            (item for item in index.pages if item.pdf_page == slide_number), None
        )
        if page is None:
            raise KeyError(f"幻灯片不存在: {slide_number}")
        native_text = _native_text_only(page.text)
        page.text = (
            f"{native_text}\n【人工确认的图片文字】\n{text}"
            if native_text else f"【人工确认的图片文字】\n{text}"
        )
        page.char_count = len(page.text)
        page.extraction_status = "visual_ocr_verified"
        page.content_origin = "visual_ocr_verified"
        page.visual_requires_recheck = False
        page.visual_verified_by_user = True
        reviews = {int(value) for value in data.get("visual_review_pages", [])}
        reviews.discard(slide_number)
        indexed = {int(value) for value in data.get("visual_indexed_pages", [])}
        indexed.add(slide_number)
        candidates = dict(data.get("visual_review_candidates") or {})
        candidates.pop(str(slide_number), None)
        data["visual_review_pages"] = sorted(reviews)
        data["visual_indexed_pages"] = sorted(indexed)
        data["visual_review_candidates"] = candidates
        self._refresh_visual_warnings(data)
        save_page_text_index(index, index_path)
        save_pptx_document_map(data, map_path)

    def dismiss_visual_review(self, document_id: str, slide_number: int) -> None:
        map_path, index_path = self._document_index_paths(document_id)
        data = json.loads(map_path.read_text(encoding="utf-8"))
        index = load_page_text_index(index_path)
        page = next(
            (item for item in index.pages if item.pdf_page == slide_number), None
        )
        if page is not None:
            page.text = _native_text_only(page.text)
            page.char_count = len(page.text)
            page.extraction_status = "visual_dismissed"
            page.content_origin = "native_text"
            page.visual_requires_recheck = False
            page.visual_verified_by_user = False
        reviews = {int(value) for value in data.get("visual_review_pages", [])}
        reviews.discard(slide_number)
        dismissed = {int(value) for value in data.get("visual_dismissed_pages", [])}
        dismissed.add(slide_number)
        indexed = {int(value) for value in data.get("visual_indexed_pages", [])}
        indexed.discard(slide_number)
        candidates = dict(data.get("visual_review_candidates") or {})
        candidates.pop(str(slide_number), None)
        data["visual_review_pages"] = sorted(reviews)
        data["visual_dismissed_pages"] = sorted(dismissed)
        data["visual_indexed_pages"] = sorted(indexed)
        data["visual_review_candidates"] = candidates
        self._refresh_visual_warnings(data)
        save_page_text_index(index, index_path)
        save_pptx_document_map(data, map_path)

    @staticmethod
    def _refresh_visual_warnings(data: dict) -> None:
        warnings = [
            str(value) for value in data.get("warnings", [])
            if "图片页需要人工复核" not in str(value)
        ]
        count = len(data.get("visual_review_pages", []))
        if count:
            warnings.append(f"{count} 张图片页需要人工复核，未进入回答证据")
        data["warnings"] = warnings
    def _document_index_paths(self, document_id: str) -> tuple[Path, Path]:
        if not document_id.startswith("doc_"):
            raise ValueError("无效的文档ID")
        map_path = next(
            (
                path for path in self.map_dir.glob("*.json")
                if self._json_document_id(path) == document_id
            ),
            None,
        )
        index_path = next(
            (
                path for path in self.index_dir.glob("*.json")
                if self._json_document_id(path) == document_id
            ),
            None,
        )
        if map_path is None or index_path is None:
            raise KeyError(f"知识库中不存在完整索引: {document_id}")
        return map_path, index_path

    def _manifest(self) -> DocumentImportManifest:
        return DocumentImportManifest.load(self.manifest_path)

    @staticmethod
    def _json_document_id(path: Path) -> str | None:
        try:
            return str(json.loads(path.read_text(encoding="utf-8")).get("document_id"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None


def _native_text_only(text: str) -> str:
    for marker in ("【图片文字识别】", "【人工确认的图片文字】"):
        if marker in text:
            return text.split(marker, 1)[0].strip()
    return text.strip()


def _visual_text_only(text: str) -> str:
    for marker in ("【图片文字识别】", "【人工确认的图片文字】"):
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return text.strip()
