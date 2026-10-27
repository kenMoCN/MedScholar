"""Build page text indexes for all DocumentMap JSON files in a directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retrieval.page_text_index import build_page_text_index, save_page_text_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build persistent PDF page indexes")
    parser.add_argument("--map-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    summaries = []
    for map_path in sorted(Path(args.map_dir).glob("*.json")):
        index = build_page_text_index(map_path)
        short_id = index.document_id.removeprefix("doc_")[:12]
        output_path = output_dir / f"{short_id}-pages.json"
        save_page_text_index(index, output_path)
        summaries.append(
            {
                "document_id": index.document_id,
                "pages": index.page_count,
                "text_pages": sum(page.extraction_status == "ok" for page in index.pages),
                "output": str(output_path.absolute()),
            }
        )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

