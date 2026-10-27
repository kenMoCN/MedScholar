"""Reproducible question-bank evaluation and failure attribution."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from core.interaction_modes import normalize_mode


EXPECTED_OUTCOMES = {"answer", "withhold", "clarify"}
EXPECTED_ANSWER_SOURCES = {"any", "course", "official_external"}
EXPECTED_PUBLICATION_DECISIONS = {
    "any", "eligible_for_verified_external_answer", "candidates_only", "blocked",
}
OUTCOME_STATUSES = {
    "answer": "completed",
    "withhold": "review_required",
    "clarify": "clarification_required",
}


class TutorService(Protocol):
    def ask(self, query: str, **kwargs): ...


@dataclass(frozen=True)
class EvidenceExpectation:
    document_id: str | None = None
    original_name: str | None = None
    pdf_page: int | None = None
    printed_page: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceExpectation":
        expectation = cls(
            document_id=_optional_text(value.get("document_id")),
            original_name=_optional_text(value.get("original_name")),
            pdf_page=_optional_positive_int(value.get("pdf_page")),
            printed_page=_optional_positive_int(value.get("printed_page")),
        )
        if not any(asdict(expectation).values()):
            raise ValueError("expected_evidence 至少要指定一个定位字段")
        return expectation


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    mode: str = "quick"
    expected_outcome: str = "answer"
    expected_terms: tuple[str, ...] = ()
    expected_options: tuple[str, ...] = ()
    expected_evidence: tuple[EvidenceExpectation, ...] = ()
    tags: tuple[str, ...] = ()
    notes: str = ""
    expected_answer_source: str = "any"
    expected_publication_decision: str = "any"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationCase":
        case_id = str(value.get("case_id", "")).strip()
        question = str(value.get("question", "")).strip()
        if not case_id or not question:
            raise ValueError("评测题必须包含 case_id 和 question")
        expected_outcome = str(value.get("expected_outcome", "answer")).strip()
        if expected_outcome not in EXPECTED_OUTCOMES:
            raise ValueError(f"不支持的 expected_outcome: {expected_outcome}")
        expected_answer_source = str(
            value.get("expected_answer_source", "any")
        ).strip()
        if expected_answer_source not in EXPECTED_ANSWER_SOURCES:
            raise ValueError(
                f"不支持的 expected_answer_source: {expected_answer_source}"
            )
        expected_publication_decision = str(
            value.get("expected_publication_decision", "any")
        ).strip()
        if expected_publication_decision not in EXPECTED_PUBLICATION_DECISIONS:
            raise ValueError(
                "不支持的 expected_publication_decision: "
                f"{expected_publication_decision}"
            )
        raw_evidence = value.get("expected_evidence", [])
        if not isinstance(raw_evidence, list) or any(
            not isinstance(item, Mapping) for item in raw_evidence
        ):
            raise ValueError("expected_evidence 必须是对象数组")
        return cls(
            case_id=case_id,
            question=question,
            mode=normalize_mode(str(value.get("mode", "quick")).strip() or "quick").value,
            expected_outcome=expected_outcome,
            expected_terms=_string_tuple(value.get("expected_terms", [])),
            expected_options=_option_tuple(value.get("expected_options", [])),
            expected_evidence=tuple(
                EvidenceExpectation.from_mapping(item)
                for item in raw_evidence
            ),
            tags=_string_tuple(value.get("tags", [])),
            notes=str(value.get("notes", "")).strip(),
            expected_answer_source=expected_answer_source,
            expected_publication_decision=expected_publication_decision,
        )


@dataclass
class CaseEvaluation:
    case_id: str
    question: str
    expected_outcome: str
    actual_status: str
    passed: bool
    outcome_correct: bool
    retrieval_hit: bool | None
    term_coverage: float | None
    citation_verified: bool | None
    hallucination_blocked: bool | None
    recalled_skill_count: int
    duration_seconds: float
    option_set_correct: bool | None = None
    error_categories: list[str] = field(default_factory=list)
    error: str | None = None
    answer_text: str | None = None
    semantic_errors: list[str] = field(default_factory=list)
    actual_answer_source: str = "course"
    actual_publication_decision: str | None = None
    answer_source_correct: bool = True
    publication_decision_correct: bool = True


@dataclass
class EvaluationReport:
    schema_version: str
    run_id: str
    created_at: str
    use_skills: bool
    case_results: list[CaseEvaluation]
    metrics: dict[str, float | int | None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalCaseEvaluation:
    case_id: str
    question: str
    evaluable: bool
    hit_at_k: bool | None
    first_matching_rank: int | None
    returned_hit_count: int
    routed_document_count: int
    duration_seconds: float
    error_categories: list[str] = field(default_factory=list)


@dataclass
class RetrievalDiagnosticReport:
    schema_version: str
    run_id: str
    created_at: str
    top_k: int
    case_results: list[RetrievalCaseEvaluation]
    metrics: dict[str, float | int | None]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvaluationRunner:
    def __init__(self, service: TutorService) -> None:
        self.service = service

    def run(
        self,
        cases: tuple[EvaluationCase, ...] | list[EvaluationCase],
        *,
        use_skills: bool = True,
        progress: Callable[[str, int, int], None] | None = None,
        checkpoint: Callable[[EvaluationReport], None] | None = None,
    ) -> EvaluationReport:
        results = []
        total = len(cases)
        run_id = f"eval_{uuid4().hex}"
        created_at = datetime.now(timezone.utc).isoformat()
        for index, case in enumerate(cases, start=1):
            _notify(progress, f"正在评测 {case.case_id}", index - 1, total)
            started = time.perf_counter()
            try:
                result = self.service.ask(
                    case.question,
                    mode=case.mode,
                    use_skills=use_skills,
                )
                evaluation = evaluate_result(
                    case, result, duration_seconds=time.perf_counter() - started
                )
            except Exception as exc:
                evaluation = CaseEvaluation(
                    case_id=case.case_id,
                    question=case.question,
                    expected_outcome=case.expected_outcome,
                    actual_status="runtime_error",
                    actual_answer_source="unknown",
                    actual_publication_decision=None,
                    passed=False,
                    outcome_correct=False,
                    answer_source_correct=(case.expected_answer_source == "any"),
                    publication_decision_correct=(
                        case.expected_publication_decision == "any"
                    ),
                    retrieval_hit=None,
                    term_coverage=None,
                    option_set_correct=None,
                    citation_verified=None,
                    hallucination_blocked=(
                        False if case.expected_outcome == "withhold" else None
                    ),
                    recalled_skill_count=0,
                    duration_seconds=time.perf_counter() - started,
                    error_categories=["runtime_failure"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(evaluation)
            if checkpoint is not None:
                checkpoint(EvaluationReport(
                    schema_version="1.0",
                    run_id=run_id,
                    created_at=created_at,
                    use_skills=use_skills,
                    case_results=list(results),
                    metrics=_aggregate(results),
                ))
            _notify(progress, f"完成 {case.case_id}", index, total)
        return EvaluationReport(
            schema_version="1.0",
            run_id=run_id,
            created_at=created_at,
            use_skills=use_skills,
            case_results=results,
            metrics=_aggregate(results),
        )


def run_retrieval_diagnostic(
    retriever: Any,
    cases: tuple[EvaluationCase, ...] | list[EvaluationCase],
    *,
    top_k: int = 3,
    progress: Callable[[str, int, int], None] | None = None,
) -> RetrievalDiagnosticReport:
    if top_k < 1:
        raise ValueError("top_k 必须大于零")
    results = []
    total = len(cases)
    for index, case in enumerate(cases, start=1):
        _notify(progress, f"正在诊断检索 {case.case_id}", index - 1, total)
        started = time.perf_counter()
        retrieval = retriever.search(case.question)
        hits = list(getattr(retrieval, "hits", []) or [])[:top_k]
        evaluable = bool(case.expected_evidence)
        matched_ranks = []
        if evaluable:
            for expected in case.expected_evidence:
                rank = next(
                    (
                        rank
                        for rank, hit in enumerate(hits, start=1)
                        if _matches_evidence(expected, hit)
                    ),
                    None,
                )
                matched_ranks.append(rank)
            hit_at_k = all(rank is not None for rank in matched_ranks)
            first_rank = min(
                (rank for rank in matched_ranks if rank is not None),
                default=None,
            )
        else:
            hit_at_k = None
            first_rank = None
        errors = []
        if not evaluable:
            errors.append("missing_retrieval_ground_truth")
        elif not hit_at_k:
            errors.append("retrieval_miss")
        results.append(RetrievalCaseEvaluation(
            case_id=case.case_id,
            question=case.question,
            evaluable=evaluable,
            hit_at_k=hit_at_k,
            first_matching_rank=first_rank,
            returned_hit_count=len(hits),
            routed_document_count=len(getattr(retrieval, "routed_documents", []) or []),
            duration_seconds=round(time.perf_counter() - started, 4),
            error_categories=errors,
        ))
        _notify(progress, f"完成检索诊断 {case.case_id}", index, total)
    evaluable_results = [item for item in results if item.evaluable]
    return RetrievalDiagnosticReport(
        schema_version="1.0",
        run_id=f"retrieval_eval_{uuid4().hex}",
        created_at=datetime.now(timezone.utc).isoformat(),
        top_k=top_k,
        case_results=results,
        metrics={
            "case_count": len(results),
            "evaluable_case_count": len(evaluable_results),
            "hit_at_k_rate": (
                sum(bool(item.hit_at_k) for item in evaluable_results)
                / len(evaluable_results)
                if evaluable_results else None
            ),
            "mean_duration_seconds": (
                sum(item.duration_seconds for item in results) / len(results)
                if results else None
            ),
        },
    )


def evaluate_result(case: EvaluationCase, result: Any, *, duration_seconds: float) -> CaseEvaluation:
    actual_status = str(getattr(result, "status", "unknown"))
    actual_answer_source = str(getattr(result, "answer_source", "course"))
    actual_publication_decision = getattr(
        result, "fallback_publication_decision", None
    )
    expected_status = OUTCOME_STATUSES[case.expected_outcome]
    outcome_correct = actual_status == expected_status
    answer_source_correct = (
        case.expected_answer_source == "any"
        or actual_answer_source == case.expected_answer_source
    )
    publication_decision_correct = (
        case.expected_publication_decision == "any"
        or actual_publication_decision == case.expected_publication_decision
    )
    bundle = getattr(result, "evidence_bundle", None)
    evidence_items = list(getattr(bundle, "evidence", []) or [])
    retrieval_hit = (
        all(any(_matches_evidence(expected, item) for item in evidence_items) for expected in case.expected_evidence)
        if case.expected_evidence else None
    )
    answer_text = str(getattr(result, "rendered_answer", "") or "")
    if not answer_text:
        draft = getattr(result, "answer", None)
        if draft is not None:
            answer_text = "\n".join(
                part for part in (
                    str(getattr(draft, "direct_answer", "") or "").strip(),
                    str(getattr(draft, "explanation", "") or "").strip(),
                ) if part
            )
    if case.expected_terms:
        normalized_answer = _normalize_concept_text(answer_text)
        hits = sum(
            any(
                _normalize_concept_text(alternative) in normalized_answer
                for alternative in str(term).split("|")
                if _normalize_concept_text(alternative)
            )
            for term in case.expected_terms
        )
        term_coverage = hits / len(case.expected_terms)
    else:
        term_coverage = None
    draft = getattr(result, "answer", None)
    option_surface = (
        str(getattr(draft, "direct_answer", "") or "")
        if draft is not None else answer_text[:400]
    )
    actual_options = _extract_answer_options(option_surface)
    option_set_correct = (
        set(actual_options) == set(case.expected_options)
        if case.expected_options else None
    )
    deterministic = getattr(result, "deterministic_report", None)
    semantic = getattr(result, "semantic_report", None)
    citation_verified = (
        getattr(deterministic, "status", None) != "fail"
        and getattr(semantic, "status", None) == "pass"
        if actual_status == "completed" else None
    )
    hallucination_blocked = (
        actual_status in {"review_required", "clarification_required"}
        if case.expected_outcome == "withhold" else None
    )
    errors = _attribute_errors(
        case,
        actual_status=actual_status,
        retrieval_hit=retrieval_hit,
        term_coverage=term_coverage,
        option_set_correct=option_set_correct,
        citation_verified=citation_verified,
        answer_source_correct=answer_source_correct,
        publication_decision_correct=publication_decision_correct,
    )
    passed = (
        outcome_correct
        and retrieval_hit is not False
        and (term_coverage is None or term_coverage == 1.0)
        and option_set_correct is not False
        and citation_verified is not False
        and answer_source_correct
        and publication_decision_correct
    )
    return CaseEvaluation(
        case_id=case.case_id,
        question=case.question,
        expected_outcome=case.expected_outcome,
        actual_status=actual_status,
        actual_answer_source=actual_answer_source,
        actual_publication_decision=actual_publication_decision,
        passed=passed,
        outcome_correct=outcome_correct,
        answer_source_correct=answer_source_correct,
        publication_decision_correct=publication_decision_correct,
        retrieval_hit=retrieval_hit,
        term_coverage=term_coverage,
        option_set_correct=option_set_correct,
        citation_verified=citation_verified,
        hallucination_blocked=hallucination_blocked,
        recalled_skill_count=len(getattr(result, "recalled_skills", []) or []),
        duration_seconds=round(duration_seconds, 4),
        error_categories=errors,
        error=getattr(result, "error", None),
        answer_text=answer_text or None,
        semantic_errors=list(getattr(semantic, "errors", []) or []),
    )


def load_question_bank(path: str | Path) -> tuple[EvaluationCase, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0":
        raise ValueError("不支持的题库版本")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("题库 cases 必须是数组")
    if any(not isinstance(item, Mapping) for item in raw_cases):
        raise ValueError("题库 cases 只能包含对象")
    cases = tuple(EvaluationCase.from_mapping(item) for item in raw_cases)
    ids = [item.case_id for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("题库 case_id 不能重复")
    return cases


def save_report(report: Any, path: str | Path) -> None:
    _atomic_json_write(report.to_dict(), path)


def save_question_bank_template(path: str | Path) -> None:
    _atomic_json_write(
        {
            "schema_version": "1.0",
            "instructions": {
                "expected_outcome": "answer | withhold | clarify",
                "expected_terms": "答案应覆盖的关键术语；不要写成长答案",
                "expected_options": "选择题完整答案集合，例如 [\"A\", \"B\", \"D\"]",
                "expected_evidence": "可按文档、PDF页或教材印刷页定位，可留空",
                "expected_answer_source": "any | course | official_external",
                "expected_publication_decision": (
                    "any | eligible_for_verified_external_answer | candidates_only | blocked"
                ),
            },
            "cases": [
                {
                    "case_id": "example_001",
                    "question": "请替换为经过教师确认的生理学题目",
                    "mode": "quick",
                    "expected_outcome": "answer",
                    "expected_answer_source": "course",
                    "expected_publication_decision": "any",
                    "expected_terms": ["关键术语1", "关键术语2"],
                    "expected_options": [],
                    "expected_evidence": [
                        {"original_name": "请填写教材或课件名", "pdf_page": 1}
                    ],
                    "tags": ["章节", "题型"],
                    "notes": "示例行，正式评测前请删除或改写",
                }
            ],
        },
        path,
    )


def _attribute_errors(
    case, *, actual_status, retrieval_hit, term_coverage, citation_verified,
    answer_source_correct, publication_decision_correct, option_set_correct,
):
    errors = []
    if actual_status != OUTCOME_STATUSES[case.expected_outcome]:
        errors.append("outcome_mismatch")
    if actual_status == "runtime_error" or actual_status == "failed":
        errors.append("runtime_failure")
    if case.expected_outcome == "answer" and actual_status == "clarification_required":
        errors.append("over_clarification")
    if case.expected_outcome == "answer" and actual_status == "review_required":
        errors.append("answer_withheld")
    if case.expected_outcome == "withhold" and actual_status == "completed":
        errors.append("unsafe_unexpected_answer")
    if retrieval_hit is False:
        errors.append("retrieval_miss")
    if term_coverage == 0.0:
        errors.append("answer_concept_miss")
    elif term_coverage is not None and term_coverage < 1.0:
        errors.append("answer_concept_partial")
    if option_set_correct is False:
        errors.append("option_set_mismatch")
    if citation_verified is False:
        errors.append("verification_failure")
    if not answer_source_correct:
        errors.append("answer_source_mismatch")
    if not publication_decision_correct:
        errors.append("publication_decision_mismatch")
    return errors


def _normalize_concept_text(value: Any) -> str:
    """Normalize typography without changing the underlying medical term."""

    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = text.translate(
        str.maketrans(
            {
                "⁺": "+",
                "⁻": "-",
                "₀": "0",
                "₁": "1",
                "₂": "2",
                "₃": "3",
                "₄": "4",
                "₅": "5",
                "₆": "6",
                "₇": "7",
                "₈": "8",
                "₉": "9",
            }
        )
    )
    return re.sub(r"[^0-9a-z+\-一-鿿]+", "", text)


def _extract_answer_options(value: str) -> tuple[str, ...]:
    """Read the declared final option set, not letters mentioned in analysis."""

    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    match = re.search(
        r"(?:答案|正确选项|应选|选择)\s*(?:是|为|：|:)?\s*([A-H](?:\s*[、,，和与及/]\s*[A-H])*)",
        text,
    )
    if not match:
        compact = text.strip()
        if re.fullmatch(r"[A-H](?:\s*[、,，/]\s*[A-H])*[。.]?", compact):
            match_text = compact
        else:
            return ()
    else:
        match_text = match.group(1)
    return tuple(dict.fromkeys(re.findall(r"[A-H]", match_text)))


def _matches_evidence(expected, item):
    checks = []
    for key in ("document_id", "original_name", "pdf_page", "printed_page"):
        value = getattr(expected, key)
        if value is not None:
            checks.append(getattr(item, key, None) == value)
    return bool(checks) and all(checks)


def _aggregate(results):
    total = len(results)
    passed = sum(item.passed for item in results)
    retrieval = [item.retrieval_hit for item in results if item.retrieval_hit is not None]
    citations = [item.citation_verified for item in results if item.citation_verified is not None]
    blocked = [item.hallucination_blocked for item in results if item.hallucination_blocked is not None]
    coverage = [item.term_coverage for item in results if item.term_coverage is not None]
    option_sets = [
        item.option_set_correct
        for item in results
        if item.option_set_correct is not None
    ]
    source_cases = [
        item for item in results
        if item.actual_answer_source != "unknown"
    ]
    decision_cases = [
        item for item in results
        if item.actual_publication_decision is not None
    ]
    return {
        "case_count": total,
        "pass_rate": passed / total if total else None,
        "outcome_accuracy": sum(item.outcome_correct for item in results) / total if total else None,
        "retrieval_hit_rate": sum(retrieval) / len(retrieval) if retrieval else None,
        "citation_verification_rate": sum(citations) / len(citations) if citations else None,
        "hallucination_block_rate": sum(blocked) / len(blocked) if blocked else None,
        "mean_term_coverage": sum(coverage) / len(coverage) if coverage else None,
        "option_set_accuracy": (
            sum(option_sets) / len(option_sets) if option_sets else None
        ),
        "answer_source_accuracy": (
            sum(item.answer_source_correct for item in source_cases) / len(source_cases)
            if source_cases else None
        ),
        "publication_decision_accuracy": (
            sum(item.publication_decision_correct for item in decision_cases)
            / len(decision_cases)
            if decision_cases else None
        ),
        "mean_duration_seconds": (
            sum(item.duration_seconds for item in results) / total if total else None
        ),
        "skill_recall_case_count": sum(item.recalled_skill_count > 0 for item in results),
    }


def _atomic_json_write(data, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _string_tuple(value):
    if not isinstance(value, list):
        raise ValueError("列表字段格式错误")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _option_tuple(value):
    options = tuple(item.upper() for item in _string_tuple(value))
    if any(not re.fullmatch(r"[A-H]", item) for item in options):
        raise ValueError("expected_options 只能填写 A-H 的选项字母")
    return tuple(dict.fromkeys(options))


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


def _optional_positive_int(value):
    if value in (None, ""):
        return None
    number = int(value)
    if number <= 0:
        raise ValueError("页码必须是正整数")
    return number


def _notify(callback, message, current, total):
    if callback:
        callback(message, current, total)
