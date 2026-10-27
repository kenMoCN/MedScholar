"""Run one metadata-only smoke test of the official-guideline fallback path."""

from __future__ import annotations

import argparse
import json

from core.app_runtime import build_application_service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query",
        nargs="?",
        default="登革热休克患者的一般处理原则是什么？",
    )
    args = parser.parse_args()
    service = build_application_service(".")
    result = service.ask(
        args.query,
        mode="quick",
        evidence_policy="local_first",
        medical_horizon_policy="off",
    )
    print(json.dumps({
        "status": result.status,
        "answer_source": result.answer_source,
        "fallback_status": result.fallback_status,
        "publication_decision": result.fallback_publication_decision,
        "resources": len(result.external_resources),
        "external_evidence": len(result.external_evidence),
        "evidence_matches": [
            {
                "terms": item.get("matched_terms", []),
                "markers": item.get("recommendation_markers", []),
                "chars": len(item.get("evidence_text", "")),
            }
            for item in result.external_evidence
        ],
        "semantic_status": (
            result.semantic_report.status if result.semantic_report else None
        ),
        "rendered": bool(result.rendered_answer),
        "error": result.error,
    }, ensure_ascii=False))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
