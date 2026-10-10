"""Command-line entry point for building PDF DocumentMap files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pdf_structure_indexer import PdfStructureIndexer, save_document_map


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return cleaned or "document"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build structure-first PDF maps")
    parser.add_argument("pdfs", nargs="+", help="PDF files to index")
    parser.add_argument("--output-dir", required=True, help="Directory for JSON maps")
    parser.add_argument(
        "--document-type",
        choices=("auto", "textbook", "course_deck"),
        default="auto",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    summaries = []
    for raw_path in args.pdfs:
        document_map = PdfStructureIndexer(raw_path).build(args.document_type)
        short_id = document_map.document_id.removeprefix("doc_")[:12]
        output_path = output_dir / f"{_safe_stem(Path(raw_path).stem)}-{short_id}.json"
        save_document_map(document_map, output_path)
        summaries.append(
            {
                "source": str(Path(raw_path)),
                "output": str(output_path.absolute()),
                "type": document_map.document_type,
                "pages": document_map.page_count,
                "toc_pages": document_map.toc_pdf_pages,
                "nodes": len(document_map.nodes),
                "segments": [
                    {
                        "offset": segment.offset,
                        "anchors": len(segment.anchors),
                        "confidence": segment.confidence,
                    }
                    for segment in document_map.page_map_segments
                ],
                "text_layer": document_map.text_layer,
                "warnings": document_map.warnings,
            }
        )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
