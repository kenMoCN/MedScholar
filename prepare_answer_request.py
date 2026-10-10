"""Prepare a strict answer-generation request without calling an external API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from answer_contract import build_answer_generation_request
from evidence_bundle import load_evidence_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an evidence-grounded answer request")
    parser.add_argument("bundle")
    parser.add_argument("--mode", choices=("quick", "guided", "socratic", "research"), default="guided")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    bundle = load_evidence_bundle(args.bundle)
    request = build_answer_generation_request(bundle, mode=args.mode)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output.absolute()), "claims_source_count": len(bundle.evidence)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

