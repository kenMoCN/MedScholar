"""Run retrieval, build an EvidenceBundle, and verify source integrity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_bundle import EvidenceBundleBuilder, save_evidence_bundle
from evidence_verify import EvidenceVerifier
from structure_retriever import StructureAwareRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify a local evidence bundle")
    parser.add_argument("query")
    parser.add_argument("--map-dir", default="data/document_maps")
    parser.add_argument("--index-dir", default="data/page_indexes")
    parser.add_argument("--output", required=True)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    retriever = StructureAwareRetriever.from_directories(args.map_dir, args.index_dir)
    result = retriever.search(args.query, top_k=args.top_k)
    builder = EvidenceBundleBuilder.from_directories(args.map_dir, args.index_dir)
    bundle = builder.build(result)
    save_evidence_bundle(bundle, args.output)

    verifier = EvidenceVerifier.from_directories(args.map_dir, args.index_dir)
    report = verifier.verify(bundle)
    summary = {
        "bundle": str(Path(args.output).absolute()),
        "evidence_count": len(bundle.evidence),
        "verification_status": report.status,
        "checks": [check.__dict__ for check in report.checks],
        "semantic_verification_required": report.semantic_verification_required,
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if report.status != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())

