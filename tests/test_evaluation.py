import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation import (
    EvaluationCase,
    EvaluationRunner,
    EvidenceExpectation,
    evaluate_result,
    load_question_bank,
    save_question_bank_template,
    run_retrieval_diagnostic,
)


class FakeService:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def ask(self, question, **kwargs):
        self.calls.append((question, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeRetriever:
    def search(self, question):
        return SimpleNamespace(
            hits=[SimpleNamespace(
                document_id="doc_1", original_name="生理学.pdf",
                pdf_page=10, printed_page=3,
            )],
            routed_documents=["doc_1"],
        )


def completed_result(*, text="钾离子通透性形成静息电位", page=10):
    evidence = SimpleNamespace(
        document_id="doc_1", original_name="生理学.pdf",
        pdf_page=page, printed_page=3,
    )
    return SimpleNamespace(
        status="completed",
        rendered_answer=text,
        evidence_bundle=SimpleNamespace(evidence=[evidence]),
        deterministic_report=SimpleNamespace(status="pass"),
        semantic_report=SimpleNamespace(status="pass"),
        recalled_skills=[],
        error=None,
    )


class EvaluationTests(unittest.TestCase):
    def test_completed_answer_requires_terms_evidence_and_verified_citations(self):
        case = EvaluationCase(
            case_id="q1",
            question="静息电位如何形成",
            expected_terms=("钾离子", "静息电位"),
            expected_evidence=(EvidenceExpectation(original_name="生理学.pdf", pdf_page=10),),
        )
        evaluated = evaluate_result(case, completed_result(), duration_seconds=0.5)
        self.assertTrue(evaluated.passed)
        self.assertTrue(evaluated.retrieval_hit)
        self.assertEqual(evaluated.term_coverage, 1.0)
        self.assertTrue(evaluated.citation_verified)

    def test_partial_concept_coverage_is_not_a_strict_pass(self):
        case = EvaluationCase(
            "q1", "静息电位", expected_terms=("钾离子", "钠离子")
        )
        evaluated = evaluate_result(case, completed_result(), duration_seconds=0.1)
        self.assertFalse(evaluated.passed)
        self.assertIn("answer_concept_partial", evaluated.error_categories)

    def test_term_coverage_normalizes_ion_and_subscript_typography(self):
        case = EvaluationCase(
            "q1",
            "氧解离曲线",
            expected_terms=("K+外流", "PCO2升高"),
        )
        result = completed_result(text="K⁺ 外流；PCO₂ 升高。")
        evaluated = evaluate_result(case, result, duration_seconds=0.1)
        self.assertEqual(evaluated.term_coverage, 1.0)
        self.assertEqual(evaluated.answer_text, "K⁺ 外流；PCO₂ 升高。")

    def test_term_coverage_accepts_declared_alternatives(self):
        case = EvaluationCase(
            "q1", "氧释放", expected_terms=("氧释放|释放O2",)
        )
        evaluated = evaluate_result(
            case, completed_result(text="促进血红蛋白释放 O₂"), duration_seconds=0.1
        )
        self.assertEqual(evaluated.term_coverage, 1.0)

    def test_multiple_choice_requires_exact_final_option_set(self):
        case = EvaluationCase(
            "beta_oxidation",
            "参与脂肪酸β-氧化的有",
            expected_options=("A", "B", "D"),
        )
        correct = evaluate_result(
            case,
            completed_result(text="正确答案是 A、B、D。C 不参与。"),
            duration_seconds=0.1,
        )
        partial = evaluate_result(
            case,
            completed_result(text="正确答案是 D。"),
            duration_seconds=0.1,
        )

        self.assertTrue(correct.option_set_correct)
        self.assertFalse(partial.option_set_correct)
        self.assertIn("option_set_mismatch", partial.error_categories)

    def test_unexpected_answer_on_withhold_case_is_safety_failure(self):
        case = EvaluationCase("bad", "不存在证据的问题", expected_outcome="withhold")
        evaluated = evaluate_result(case, completed_result(), duration_seconds=0.1)
        self.assertFalse(evaluated.hallucination_blocked)
        self.assertIn("unsafe_unexpected_answer", evaluated.error_categories)

    def test_runner_records_runtime_failure_without_counting_it_as_safe_block(self):
        case = EvaluationCase("bad", "问题", expected_outcome="withhold")
        service = FakeService([TimeoutError("offline")])
        report = EvaluationRunner(service).run([case], use_skills=False)
        self.assertEqual(report.case_results[0].actual_status, "runtime_error")
        self.assertFalse(report.case_results[0].hallucination_blocked)
        self.assertEqual(service.calls[0][1]["use_skills"], False)

    def test_runner_emits_incremental_checkpoint_after_each_case(self):
        cases = [
            EvaluationCase("one", "问题一"),
            EvaluationCase("two", "问题二"),
        ]
        snapshots = []
        service = FakeService([completed_result(), completed_result()])

        report = EvaluationRunner(service).run(
            cases,
            use_skills=False,
            checkpoint=lambda snapshot: snapshots.append(snapshot),
        )

        self.assertEqual([len(item.case_results) for item in snapshots], [1, 2])
        self.assertEqual(snapshots[-1].run_id, report.run_id)
        self.assertEqual(snapshots[-1].metrics, report.metrics)

    def test_template_round_trip_and_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bank.json"
            save_question_bank_template(path)
            cases = load_question_bank(path)
            self.assertEqual(cases[0].case_id, "example_001")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cases"].append(dict(data["cases"][0]))
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不能重复"):
                load_question_bank(path)

    def test_official_guideline_template_loads_source_and_gate_expectations(self):
        path = Path(__file__).resolve().parents[1] / "official_guideline_evaluation_template.json"
        cases = load_question_bank(path)
        self.assertEqual(len(cases), 3)
        self.assertEqual(cases[0].expected_answer_source, "official_external")
        self.assertEqual(
            cases[0].expected_publication_decision,
            "eligible_for_verified_external_answer",
        )

    def test_answer_source_mismatch_is_not_a_strict_pass(self):
        case = EvaluationCase(
            "source", "问题", expected_answer_source="official_external"
        )
        evaluated = evaluate_result(case, completed_result(), duration_seconds=0.1)
        self.assertFalse(evaluated.passed)
        self.assertIn("answer_source_mismatch", evaluated.error_categories)

    def test_retrieval_diagnostic_runs_without_answer_model(self):
        cases = [EvaluationCase(
            "q1", "静息电位",
            expected_evidence=(EvidenceExpectation(document_id="doc_1"),),
        )]
        report = run_retrieval_diagnostic(FakeRetriever(), cases, top_k=1)
        self.assertEqual(report.metrics["hit_at_k_rate"], 1.0)
        self.assertEqual(report.case_results[0].first_matching_rank, 1)


if __name__ == "__main__":
    unittest.main()
