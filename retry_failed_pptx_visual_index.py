"""Retry only PPTX visual pages that failed during the approved batch."""

from __future__ import annotations

import json
from pathlib import Path

from app_runtime import build_vision_client
from knowledge_base import KnowledgeBaseService


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    service = KnowledgeBaseService(base_dir)
    vision_client = build_vision_client(base_dir)
    failures: list[tuple[str, str, set[int]]] = []
    for map_path in service.map_dir.glob("*.json"):
        data = json.loads(map_path.read_text(encoding="utf-8"))
        pages = {int(value) for value in data.get("visual_failed_pages", [])}
        if pages:
            failures.append((
                str(data["document_id"]),
                str(data.get("original_name", map_path.stem)),
                pages,
            ))
    total = sum(len(pages) for _, _, pages in failures)
    print(f"待重试失败页: {total}", flush=True)
    completed = indexed = review = failed = 0
    for document_id, name, pages in failures:
        print(f"重试: {name} {sorted(pages)}", flush=True)

        def progress(message: str, current: int, _: int) -> None:
            print(f"[{completed + current}/{total}] {message}", flush=True)

        report = service.index_document_visuals(
            document_id,
            vision_client,
            progress=progress,
            slide_numbers=pages,
        )
        completed += report.attempted
        indexed += report.indexed
        review += report.review_required
        failed += report.failed
    print(f"重试汇总: 写入 {indexed}，待复核 {review}，仍失败 {failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
