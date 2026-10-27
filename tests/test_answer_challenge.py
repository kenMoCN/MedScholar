import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.agent_orchestrator import AgentRunResult, ExecutionPlan
from evidence.answer_challenge import (
    disputed_option_terms,
    final_choice_set,
    is_answer_challenge,
    is_question_format_correction,
)
from core.app_runtime import TutorApplicationService
from ui.ui_presenter import present_agent_result


QUESTION = (
    "嘌呤环中的碳原子来自于（多选） "
    "A. CO2 B. 甘氨酸 C. 天冬氨酸 D. 谷氨酰胺 E. 一碳单位"
)


def result_for(answer_text, *, query=QUESTION):
    return AgentRunResult(
        status="completed",
        plan=ExecutionPlan(query, "quick", [], [query], topic_query=query,
                           subject_ids=["biochemistry"]),
        answer=SimpleNamespace(direct_answer=answer_text, answerability="answer"),
        rendered_answer=answer_text,
        evidence_bundle=None,
        deterministic_report=None,
        semantic_report=None,
        clarification_questions=[],
        events=[],
    )


class RecheckingOrchestrator:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        return self.response


class AnswerChallengeTests(unittest.TestCase):
    def test_challenge_detection_does_not_capture_wrong_option_question(self):
        self.assertTrue(is_answer_challenge("但是你这道题答错了，应该选 ABE"))
        self.assertTrue(is_answer_challenge("这道题做错了"))
        self.assertFalse(is_answer_challenge("下列叙述错误的是哪一项？"))
        self.assertFalse(is_answer_challenge("这道新题应该选什么？"))
        self.assertTrue(is_question_format_correction("第51题是多选题"))
        self.assertFalse(is_question_format_correction("这道题是如何形成的？"))

    def test_question_type_correction_rechecks_previous_image_question(self):
        question = "51题：[单选] 巨幼细胞贫血的原因是？ A. 胃酸不足 B. 胃大部切除 C. 回肠切除 D. 抗内因子抗体"
        old = result_for("原回答认为 C、D 均符合，因此单选题无唯一答案。", query=question)
        new = result_for("正确选项为 B、C、D。", query=question)
        orchestrator = RecheckingOrchestrator(new)
        service = TutorApplicationService(orchestrator)

        reviewed = service.ask(
            "第51题是多选题",
            prior_result=old,
            conversation_key="image-session",
        )

        self.assertEqual(len(orchestrator.requests), 1)
        self.assertIn(question, orchestrator.requests[0].query)
        self.assertIn("用户补充题型：第51题是多选题", orchestrator.requests[0].query)
        self.assertEqual(orchestrator.requests[0].challenge_feedback, "第51题是多选题")
        self.assertIn("按你补充的题型重新核查", reviewed.rendered_answer)
        self.assertEqual(reviewed.plan.topic_query, question)

    def test_restored_ocr_text_and_answer_support_format_correction(self):
        question = "51题：巨幼细胞贫血的原因？A 胃酸不足 B 胃大部切除 C 回肠切除 D 抗内因子抗体"
        orchestrator = RecheckingOrchestrator(result_for("正确答案 B、C、D", query=question))
        service = TutorApplicationService(orchestrator)
        result = service.ask(
            "第51题是多选题",
            conversation_context=[
                {"role": "user", "body": "[图片题目] 附件：question.jpg"},
                {"role": "assistant", "body": "原回答认为无唯一答案", "question_context": question},
            ],
        )
        self.assertEqual(result.status, "completed")
        self.assertIn(question, orchestrator.requests[0].query)
        self.assertEqual(orchestrator.requests[0].previous_answer, "原回答认为无唯一答案")

    def test_format_correction_can_recheck_retained_image_after_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "question.png"
            image.write_bytes(b"image")
            old = result_for("原回答：单选题无唯一答案。")
            orchestrator = RecheckingOrchestrator(result_for("正确选项为 B、C、D。"))
            service = TutorApplicationService(orchestrator)
            approvals = []
            result = service.ask(
                "第51题是多选题",
                prior_result=old,
                prior_image=str(image),
                approval_callback=lambda request: approvals.append(request.title) or True,
            )
            self.assertEqual(result.status, "completed")
            self.assertEqual(orchestrator.requests[0].image, str(image))
            self.assertIn("重新读取", approvals[0])

    def test_declined_image_recheck_uses_saved_question_text(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "question.png"
            image.write_bytes(b"image")
            old = result_for("原回答：单选题无唯一答案。")
            orchestrator = RecheckingOrchestrator(result_for("正确选项为 B、C、D。"))
            service = TutorApplicationService(orchestrator)
            result = service.ask(
                "第51题是多选题",
                prior_result=old,
                prior_image=str(image),
                approval_callback=lambda request: False,
            )
            self.assertEqual(result.status, "completed")
            self.assertIsNone(orchestrator.requests[0].image)

    def test_old_image_history_without_ocr_requests_reupload(self):
        orchestrator = RecheckingOrchestrator(result_for("正确答案 B、C、D"))
        service = TutorApplicationService(orchestrator)
        result = service.ask(
            "第51题是多选题",
            conversation_context=[
                {"role": "user", "body": "[图片题目] 附件：question.jpg"},
                {"role": "assistant", "body": "原回答认为无唯一答案"},
            ],
        )
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(orchestrator.requests)
        self.assertIn("重新上传原图", result.clarification_questions[0])

    def test_disputed_option_targets_option_content(self):
        self.assertEqual(final_choice_set("应该选ABE"), frozenset("ABE"))
        self.assertEqual(
            disputed_option_terms(QUESTION, "答案为 A、B、C、E", "应该选ABE"),
            ["天冬氨酸"],
        )
        self.assertIsNone(final_choice_set("C 不应该选"))
        self.assertEqual(
            disputed_option_terms(QUESTION, "答案为 A、B、C、E", "C 不应该选"),
            ["天冬氨酸"],
        )

    def test_challenge_reuses_original_question_and_reports_correction(self):
        old = result_for("答案为 A、B、C、E")
        new = result_for("正确选项为 A、B、E。天冬氨酸提供氮，不提供环碳。")
        orchestrator = RecheckingOrchestrator(new)
        service = TutorApplicationService(orchestrator)

        reviewed = service.ask(
            "你答错了，应该选ABE",
            prior_result=old,
            prior_query=QUESTION,
            conversation_key="session-1",
        )

        self.assertEqual(len(orchestrator.requests), 1)
        request = orchestrator.requests[0]
        self.assertEqual(request.query, QUESTION)
        self.assertEqual(request.challenge_feedback, "你答错了，应该选ABE")
        self.assertEqual(request.challenge_focus_terms, ["天冬氨酸"])
        self.assertIn("原回答有误", reviewed.rendered_answer)
        self.assertEqual(reviewed.answer_source, "answer_challenge")
        self.assertEqual(present_agent_result(reviewed).title, "答案复核")

    def test_unresolved_disagreement_is_not_presented_as_verified(self):
        old = result_for("答案为 A、B、C、E")
        same = result_for("正确选项为 A、B、C、E。")
        service = TutorApplicationService(RecheckingOrchestrator(same))

        reviewed = service.ask("应该选ABE", prior_result=old, prior_query=QUESTION)

        self.assertEqual(reviewed.status, "review_required")
        self.assertEqual(present_agent_result(reviewed).title, "答案争议待核实")
        self.assertIn("暂不把任一答案标为已解决", reviewed.rendered_answer)

    def test_missing_previous_question_asks_for_it_instead_of_searching_feedback(self):
        orchestrator = RecheckingOrchestrator(result_for("答案为 ABE"))
        service = TutorApplicationService(orchestrator)

        result = service.ask("你答错了")

        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(orchestrator.requests)

    def test_recent_text_history_can_recover_question_after_restart(self):
        orchestrator = RecheckingOrchestrator(result_for("答案为 A、B、E"))
        service = TutorApplicationService(orchestrator)

        result = service.ask(
            "你答错了，应该选ABE",
            conversation_context=[
                {"role": "user", "body": QUESTION},
                {"role": "assistant", "body": "答案为 A、B、C、E"},
            ],
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(orchestrator.requests[0].query, QUESTION)

    def test_history_does_not_mistake_video_action_for_original_question(self):
        orchestrator = RecheckingOrchestrator(result_for("答案为 A、B、E"))
        service = TutorApplicationService(orchestrator)

        result = service.ask(
            "你答错了",
            conversation_context=[
                {"role": "user", "body": "找视频"},
                {"role": "assistant", "body": "这是相关视频"},
            ],
        )

        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(orchestrator.requests)


if __name__ == "__main__":
    unittest.main()
