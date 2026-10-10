"""Batch the explicitly approved PPTX visual-index operation."""

from __future__ import annotations

from pathlib import Path

from app_runtime import build_vision_client
from knowledge_base import KnowledgeBaseService


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    service = KnowledgeBaseService(base_dir)
    vision_client = build_vision_client(base_dir)
    documents = [
        item for item in service.list_documents()
        if item.original_name.lower().endswith(".pptx")
    ]
    total_candidates = sum(
        service.visual_index_plan(item.document_id).candidate_count
        for item in documents
    )
    print(f"待识别图片页总数: {total_candidates}", flush=True)
    total_indexed = total_review = total_failed = 0
    completed_before = 0
    for document in documents:
        plan = service.visual_index_plan(document.document_id)
        if not plan.candidate_count:
            continue
        print(
            f"开始: {document.original_name} ({plan.candidate_count} 张)",
            flush=True,
        )

        def progress(message: str, current: int, total: int) -> None:
            print(
                f"[{completed_before + current}/{total_candidates}] {message}",
                flush=True,
            )

        report = service.index_document_visuals(
            document.document_id,
            vision_client,
            progress=progress,
        )
        completed_before += report.attempted
        total_indexed += report.indexed
        total_review += report.review_required
        total_failed += report.failed
        print(
            f"完成: {document.original_name}，写入 {report.indexed}，"
            f"待复核 {report.review_required}，失败 {report.failed}",
            flush=True,
        )
    print(
        f"汇总: 写入 {total_indexed}，待复核 {total_review}，失败 {total_failed}",
        flush=True,
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
