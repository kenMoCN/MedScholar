"""Rebuild one managed PDF map and page index after indexer upgrades."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from retrieval.page_text_index import build_page_text_index, save_page_text_index
from retrieval.pdf_structure_indexer import PdfStructureIndexer, save_document_map


def rebuild(base_dir: Path, document_id: str, document_type: str) -> None:
    map_path = base_dir / "data" / "document_maps" / f"{document_id}.json"
    if not map_path.is_file():
        raise FileNotFoundError(map_path)
    previous = json.loads(map_path.read_text(encoding="utf-8"))
    document_map = PdfStructureIndexer(previous["source_path"]).build(document_type)
    document_map.subject_ids = list(previous.get("subject_ids", []))
    save_document_map(document_map, map_path)
    index_path = base_dir / "data" / "page_indexes" / f"{document_id}-pages.json"
    save_page_text_index(build_page_text_index(map_path), index_path)
    print(
        json.dumps(
            {
                "document_id": document_id,
                "document_type": document_map.document_type,
                "nodes": len(document_map.nodes),
                "segments": [asdict(segment) for segment in document_map.page_map_segments],
                "page_count": document_map.page_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--document-type", default="textbook")
    args = parser.parse_args()
    # ``absolute`` avoids a Windows Path.resolve edge case in non-UTF-8 shells.
    rebuild(Path(args.base_dir).absolute(), args.document_id, args.document_type)
