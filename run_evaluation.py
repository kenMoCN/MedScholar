"""Run an end-to-end question-bank evaluation with incremental checkpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app_runtime import build_application_service
from evaluation import EvaluationRunner, load_question_bank, save_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the tutor against a JSON question bank and checkpoint every case."
    )
    parser.add_argument("question_bank", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-dir", type=Path, default=Path.cwd())
    parser.add_argument("--use-skills", action="store_true")
    args = parser.parse_args()

    base_dir = args.base_dir.resolve()
    question_bank = args.question_bank.resolve()
    output = args.output.resolve()
    cases = load_question_bank(question_bank)
    service = build_application_service(base_dir)

    def checkpoint(report) -> None:
        save_report(report, output)
        latest = report.case_results[-1]
        print(
            json.dumps(
                {
                    "completed": len(report.case_results),
                    "total": len(cases),
                    "case_id": latest.case_id,
                    "status": latest.actual_status,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    report = EvaluationRunner(service).run(
        cases,
        use_skills=args.use_skills,
        checkpoint=checkpoint,
    )
    print(json.dumps({"done": True, "metrics": report.metrics}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
