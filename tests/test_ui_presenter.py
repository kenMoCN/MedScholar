import unittest

from core.agent_orchestrator import AgentRunResult, RunStatus
from ui.ui_presenter import present_agent_result


def make_result(status, **changes):
    values = {
        "status": status,
        "plan": None,
        "answer": None,
        "rendered_answer": None,
        "evidence_bundle": None,
        "deterministic_report": None,
        "semantic_report": None,
        "clarification_questions": [],
        "events": [],
        "error": None,
    }
    values.update(changes)
    return AgentRunResult(**values)


class UiPresenterTests(unittest.TestCase):
    def test_verified_official_answer_is_not_labeled_as_course_evidence(self):
        result = AgentRunResult(
            status="completed", plan=None, answer=None,
            rendered_answer="【官方指南答复｜非课程教材证据】\n答复",
            evidence_bundle=None, deterministic_report=None, semantic_report=None,
            clarification_questions=[], events=[], answer_source="official_external",
            external_resources=[{"title": "WHO guideline"}],
        )
        presented = present_agent_result(result)
        self.assertEqual(presented.title, "官方指南答复")
        self.assertIn("官方指南", presented.status_text)
        self.assertEqual(len(presented.resources), 1)

    def test_official_answer_only_inlines_verified_answer_sources(self):
        result = make_result(
            RunStatus.COMPLETED.value,
            rendered_answer="已核验答复",
            answer_source="official_external",
            external_resources=[
                {"title": "直接依据", "may_determine_answer": True},
                {"title": "普通候选一", "may_determine_answer": False},
                {"title": "普通候选二", "may_determine_answer": False},
            ],
        )

        presented = present_agent_result(result)

        self.assertEqual(len(presented.resources), 1)
        self.assertEqual(presented.resources[0]["title"], "直接依据")

    def test_completed_answer_is_presented(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="已核验答案",
                learning_actions=[
                    {"id": "video", "label": "找视频", "prompt": "找视频"}
                ],
            )
        )
        self.assertEqual(presented.body, "已核验答案")
        self.assertEqual(presented.tone, "answer")
        self.assertEqual(presented.actions[0]["id"], "video")

    def test_standalone_paper_search_is_not_shown_as_course_answer(self):
        extension = (
            "科研证据拓展：\n自动综合未通过完整核验，以下仅列出候选论文。\n"
            "- [paper_1] Action potential review（2025）— https://example.test/paper"
        )
        presented = present_agent_result(make_result(
            RunStatus.COMPLETED.value,
            rendered_answer=extension,
            research_extension=extension,
            research_synthesis_status="review",
            answer_source="research_extension",
            external_resources=[{"paper_id": "paper_1", "title": "Action potential review"}],
        ))
        self.assertEqual(presented.title, "论文拓展")
        self.assertIn("1 篇候选论文", presented.body)
        self.assertNotIn("课程答案已核验", presented.status_text)
        self.assertEqual(len(presented.resources), 1)

    def test_local_help_is_not_labeled_as_verified_course_answer(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="我是 MedScholar。",
                answer_source="local_help",
            )
        )
        self.assertEqual(presented.title, "学习助手")
        self.assertEqual(presented.status_text, "已提供助手介绍与使用帮助")
        self.assertNotIn("课程证据", presented.status_text)

    def test_general_knowledge_answer_has_transparent_non_course_label(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="【通用知识讲解｜未与课程资料核对】\n答案是B、C。",
                answer_source="general_knowledge",
            )
        )
        self.assertEqual(presented.title, "通用知识讲解")
        self.assertIn("未与课程资料核对", presented.status_text)
        self.assertNotIn("课程答案已核验", presented.status_text)

    def test_question_diagnostic_explains_conflict_instead_of_evidence_refusal(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer=(
                    "【题目存在矛盾｜无法确定唯一选项】\n"
                    "本题无唯一答案：B、C均为错误表述。"
                ),
                answer_source="question_diagnostic",
            )
        )
        self.assertEqual(presented.title, "题目矛盾分析")
        self.assertIn("不强行给出唯一选项", presented.status_text)
        self.assertNotIn("证据不足", presented.body)

    def test_casual_chat_has_its_own_non_evidence_label(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="我们可以轻松聊一会儿。",
                answer_source="casual_chat",
            )
        )
        self.assertEqual(presented.title, "轻松聊天")
        self.assertEqual(presented.status_text, "已直接回复日常聊天")
        self.assertNotIn("核验", presented.status_text)

    def test_video_learning_uses_resource_cards_without_course_label(self):
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="找到一个视频候选。",
                answer_source="video_learning",
                external_resources=[{
                    "title": "动作电位动画",
                    "resource_kind": "video_candidate",
                }],
            )
        )

        self.assertEqual(presented.title, "视频学习建议")
        self.assertEqual(len(presented.resources), 1)
        self.assertNotIn("课程证据", presented.status_text)

    def test_research_extension_is_structured_for_evidence_cards(self):
        extension = (
            "科研证据拓展：\n摘要结论已核验。\n"
            "- [paper_1] Paper（2025；证据层级D）— https://example.test"
        )
        presented = present_agent_result(make_result(
            RunStatus.COMPLETED.value,
            rendered_answer=f"课程答案\n\n{extension}",
            research_extension=extension,
            research_synthesis_status="pass",
            external_resources=[{
                "paper_id": "paper_1", "title": "Paper",
                "url": "https://example.test", "sources": ["pubmed"],
            }],
        ))
        self.assertEqual(presented.body, "课程答案")
        self.assertIn("摘要结论已核验", presented.research_body)
        self.assertNotIn("[paper_1]", presented.research_body)
        self.assertEqual(presented.resources[0]["paper_id"], "paper_1")
        self.assertEqual(presented.research_status, "pass")

    def test_research_answer_inlines_at_most_three_resource_cards(self):
        resources = [{"paper_id": str(index)} for index in range(8)]
        presented = present_agent_result(
            make_result(
                RunStatus.COMPLETED.value,
                rendered_answer="课程答案",
                research_synthesis_status="pass",
                external_resources=resources,
            )
        )
        self.assertEqual(len(presented.resources), 3)

    def test_clarification_questions_are_preserved(self):
        presented = present_agent_result(
            make_result(
                RunStatus.CLARIFICATION_REQUIRED.value,
                clarification_questions=["具体是哪一章？", "是否有题目截图？"],
            )
        )
        self.assertIn("具体是哪一章？", presented.body)
        self.assertIn("是否有题目截图？", presented.body)
        self.assertEqual(presented.tone, "clarification")

    def test_unverified_answer_is_not_displayed(self):
        presented = present_agent_result(
            make_result(
                RunStatus.REVIEW_REQUIRED.value,
                rendered_answer="不应展示的草稿",
                error="证据不足",
            )
        )
        self.assertIn("为什么暂不回答", presented.body)
        self.assertIn("证据不足", presented.body)
        self.assertIn("当前卡在哪里", presented.body)
        self.assertIn("怎样可以继续", presented.body)
        self.assertNotIn("不应展示", presented.body)
        self.assertEqual(presented.tone, "warning")

    def test_review_can_show_unverified_external_candidates_separately(self):
        presented = present_agent_result(make_result(
            RunStatus.REVIEW_REQUIRED.value,
            rendered_answer="不应展示的草稿",
            error="课程证据不足",
            fallback_status="candidates_found",
            fallback_message="找到外部候选资料，但尚未核验。",
            external_resources=[{"title": "候选文献", "may_determine_answer": False}],
        ))
        self.assertNotIn("不应展示", presented.body)
        self.assertIn("为什么暂不回答", presented.body)
        self.assertIn("还没有完成正文内容", presented.body)
        self.assertIn("继续核对候选资料正文", presented.body)
        self.assertEqual(presented.resources[0]["title"], "候选文献")

    def test_review_shows_only_three_candidate_cards_after_explaining_reason(self):
        presented = present_agent_result(make_result(
            RunStatus.REVIEW_REQUIRED.value,
            error="课程资料没有覆盖这个问题",
            fallback_status="candidates_found",
            external_resources=[{"title": str(index)} for index in range(10)],
        ))
        self.assertEqual(len(presented.resources), 3)

    def test_local_only_review_explains_that_external_sources_are_disabled(self):
        presented = present_agent_result(make_result(
            RunStatus.REVIEW_REQUIRED.value,
            fallback_status="disabled",
        ))
        self.assertIn("仅课程资料", presented.body)
        self.assertIn("允许外部补证", presented.body)

    def test_failed_result_uses_safe_fallback(self):
        presented = present_agent_result(make_result(RunStatus.FAILED.value))
        self.assertEqual(presented.tone, "error")
        self.assertTrue(presented.body)

    def test_cancelled_approval_is_presented_without_failure_label(self):
        presented = present_agent_result(
            make_result(
                RunStatus.CANCELLED.value,
                error="你已拒绝发送图片，本次任务未继续执行。",
            )
        )
        self.assertEqual(presented.title, "操作已取消")
        self.assertEqual(presented.status_text, "操作已取消")


if __name__ == "__main__":
    unittest.main()
