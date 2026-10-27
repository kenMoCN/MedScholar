"""Blind A/B packages for judging real medical-student learning experience."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from evaluation.evaluation import EvaluationCase, _extract_answer_options


@dataclass(frozen=True)
class BlindComparisonOutput:
    run_id: str
    review_path: Path
    answer_key_path: Path
    case_count: int


def run_blind_comparison(
    service,
    cases: Iterable[EvaluationCase],
    output_dir: str | Path,
    *,
    use_skills: bool = True,
    progress: Callable[[str, int, int], None] | None = None,
) -> BlindComparisonOutput:
    """Run agent and direct-model answers, then conceal their identities."""

    selected = tuple(cases)
    if not selected:
        raise ValueError("匿名对比至少需要一道题")
    run_id = uuid4().hex
    review_cases: list[dict] = []
    answer_key: list[dict] = []
    total = len(selected)
    for index, case in enumerate(selected, start=1):
        if progress:
            progress(f"正在生成匿名对比：{case.case_id}", index - 1, total)
        usage_before_agent = _usage_snapshot(service)
        agent_started = time.perf_counter()
        agent_result = service.ask(
            case.question,
            mode=case.mode,
            use_skills=use_skills,
        )
        agent_seconds = time.perf_counter() - agent_started
        usage_after_agent = _usage_snapshot(service)
        agent_usage = _usage_delta(usage_before_agent, usage_after_agent)
        agent_answer = (
            str(agent_result.rendered_answer or "").strip()
            or str(agent_result.error or "未生成回答").strip()
        )
        usage_before_baseline = usage_after_agent
        baseline_started = time.perf_counter()
        baseline_answer = service.baseline_answer(case.question)
        baseline_seconds = time.perf_counter() - baseline_started
        baseline_usage = _usage_delta(
            usage_before_baseline,
            _usage_snapshot(service),
        )

        agent_first = _agent_is_a(run_id, case.case_id)
        answer_a = agent_answer if agent_first else baseline_answer
        answer_b = baseline_answer if agent_first else agent_answer
        review_cases.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "tags": list(case.tags),
                "answer_a": answer_a,
                "answer_b": answer_b,
                "review": {
                    "preferred": "A / B / 相当",
                    "directness": {"A": None, "B": None},
                    "clarity": {"A": None, "B": None},
                    "problem_diagnosis": {"A": None, "B": None},
                    "evidence_relevance": {"A": None, "B": None},
                    "learning_helpfulness": {"A": None, "B": None},
                    "comment": "",
                },
            }
        )
        answer_key.append(
            {
                "case_id": case.case_id,
                "A": "agent" if agent_first else "direct_model",
                "B": "direct_model" if agent_first else "agent",
                "agent_status": agent_result.status,
                "agent_seconds": round(agent_seconds, 3),
                "direct_model_seconds": round(baseline_seconds, 3),
                "agent_option_set_correct": _option_set_correct(
                    case, agent_answer
                ),
                "direct_model_option_set_correct": _option_set_correct(
                    case, baseline_answer
                ),
                "agent_usage": agent_usage,
                "direct_model_usage": baseline_usage,
                "cost_note": (
                    "token usage is recorded as the cost basis; monetary cost requires "
                    "the configured provider model price"
                ),
            }
        )
        if progress:
            progress(f"已完成匿名对比：{case.case_id}", index, total)

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    review_path = destination / f"{timestamp}_blind_review_{run_id[-8:]}.json"
    key_path = destination / f"{timestamp}_blind_key_{run_id[-8:]}.json"
    common = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instructions": (
            "请让不了解系统实现的医学生独立评分。各项用1至5分；先完成review，"
            "再由项目负责人查看单独的answer key，避免评分受到来源影响。"
        ),
        "criteria": {
            "directness": "是否直接回应问题，而非被流程或免责声明淹没",
            "clarity": "是否容易理解，解释层次是否适合医学生",
            "problem_diagnosis": "题目有矛盾或缺条件时，是否准确指出具体原因",
            "evidence_relevance": "引用或依据是否真正支持当前回答",
            "learning_helpfulness": "是否帮助理解、记忆或形成下一步学习行动",
        },
    }
    review_path.write_text(
        json.dumps({**common, "cases": review_cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    key_path.write_text(
        json.dumps(
            {"run_id": run_id, "answer_key": answer_key},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return BlindComparisonOutput(run_id, review_path, key_path, total)


def _agent_is_a(run_id: str, case_id: str) -> bool:
    digest = hashlib.sha256(f"{run_id}:{case_id}".encode("utf-8")).digest()
    return digest[0] % 2 == 0


def _usage_snapshot(service) -> dict[str, int] | None:
    snapshot = getattr(service, "usage_snapshot", None)
    if not callable(snapshot):
        return None
    value = snapshot()
    if not isinstance(value, dict):
        return None
    return {
        key: max(0, int(value.get(key, 0)))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "requests")
    }


def _usage_delta(
    before: dict[str, int] | None,
    after: dict[str, int] | None,
) -> dict[str, int] | None:
    if before is None or after is None:
        return None
    return {key: max(0, after[key] - before[key]) for key in before}


def _option_set_correct(case: EvaluationCase, answer: str) -> bool | None:
    if not case.expected_options:
        return None
    return set(_extract_answer_options(answer[:400])) == set(case.expected_options)
