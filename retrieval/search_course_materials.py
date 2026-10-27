"""Small CLI for inspecting structure-aware retrieval results."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from retrieval.structure_retriever import StructureAwareRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Search indexed course materials")
    parser.add_argument("query")
    parser.add_argument("--map-dir", default="data/document_maps")
    parser.add_argument("--index-dir", default="data/page_indexes")
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    retriever = StructureAwareRetriever.from_directories(args.map_dir, args.index_dir)
    result = retriever.search(args.query, top_k=args.top_k)
    compact = {
        "query": result.query,
        "route_mode": result.route_mode,
        "routed_documents": result.routed_documents,
        "hits": [
            {
                key: value
                for key, value in asdict(hit).items()
                if key != "context_text"
            }
            for hit in result.hits
        ],
    }
    # ASCII-escaped JSON remains portable on Windows terminals using GBK while
    # JSON consumers still recover the original Unicode text.
    print(json.dumps(compact, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
