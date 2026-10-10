import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from evaluation import (
    CaseEvaluation,
    EvaluationReport,
    RetrievalCaseEvaluation,
    RetrievalDiagnosticReport,
)
from evaluation_dialog import EvaluationDialog


class FakeService:
    def ask(self, *args, **kwargs):
        raise AssertionError("this UI test must not call a model")


class EvaluationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_report_metrics_and_error_rows_render_without_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = EvaluationDialog(Path(temp), FakeService())
            report = EvaluationReport(
                schema_version="1.0",
                run_id="eval_test",
                created_at="2026-10-02T00:00:00+00:00",
                use_skills=False,
                case_results=[CaseEvaluation(
                    case_id="q1", question="静息电位", expected_outcome="answer",
                    actual_status="review_required", passed=False,
                    outcome_correct=False, retrieval_hit=False,
                    term_coverage=0.0, citation_verified=None,
                    hallucination_blocked=None, recalled_skill_count=0,
                    duration_seconds=1.25,
                    error_categories=["outcome_mismatch", "retrieval_miss"],
                )],
                metrics={
                    "pass_rate": 0.0,
                    "outcome_accuracy": 0.0,
                    "retrieval_hit_rate": 0.0,
                    "citation_verification_rate": None,
                    "hallucination_block_rate": None,
                    "mean_term_coverage": 0.0,
                },
            )
            try:
                dialog._render_report(report)
                self.assertEqual(dialog.table.rowCount(), 1)
                self.assertIn("retrieval_miss", dialog.table.item(0, 8).text())
                self.assertIn("0.0%", dialog.metric_labels["pass_rate"][0].text())
            finally:
                dialog.close()

    def test_run_without_question_bank_is_blocked_before_worker_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = EvaluationDialog(Path(temp), FakeService())
            try:
                dialog._start()
                self.assertIsNone(dialog._thread)
                self.assertIn("先导入", dialog.status_label.text())
            finally:
                dialog.close()

    def test_retrieval_only_report_renders_without_model_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = EvaluationDialog(Path(temp), FakeService())
            report = RetrievalDiagnosticReport(
                "1.0", "retrieval_test", "2026-10-02T00:00:00+00:00", 3,
                [RetrievalCaseEvaluation(
                    "q1", "静息电位", True, True, 1, 3, 1, 0.02, []
                )],
                {"case_count": 1, "evaluable_case_count": 1, "hit_at_k_rate": 1.0, "mean_duration_seconds": 0.02},
            )
            try:
                dialog._render_retrieval_report(report)
                self.assertIn("100.0%", dialog.metric_labels["retrieval_hit_rate"][0].text())
                self.assertEqual(dialog.table.item(0, 3).text(), "Top-3命中")
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
