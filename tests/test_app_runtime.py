import tempfile
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.agent_orchestrator import AgentRunResult, ExecutionPlan
from core.app_runtime import (
    ApprovalRequest,
    ProgressUpdate,
    RuntimePaths,
    TutorApplicationService,
    inspect_runtime,
)
from evidence.evidence_fallback import FallbackOutcome, FallbackResource
from core.execution_control import RunCancelled, RunTimedOut
from evidence.official_evidence import OfficialEvidenceItem
from core.interaction_modes import InteractionMode, mode_options, normalize_mode
from evidence.research_evidence import (
    ResearchIntroduction,
    ResearchPaper,
    ResearchPacket,
    ResearchSearchPlan,
    ResearchSynthesis,
)
from core.skill_memory import SkillKind, SkillStore


class RecordingOrchestrator:
    def __init__(self):
        self.requests = []

    def run(self, request, *, progress_callback=None):
        self.requests.append(request)
        if progress_callback:
            from core.agent_orchestrator import ExecutionEvent
            progress_callback(ExecutionEvent("planned", "ok", "测试规划完成"))
        return AgentRunResult(
            status="completed",
            plan=None,
            answer=None,
            rendered_answer="ok",
            evidence_bundle=None,
            deterministic_report=None,
            semantic_report=None,
            clarification_questions=[],
            events=[],
        )


class ImageContextOrchestrator(RecordingOrchestrator):
    def run(self, request, *, progress_callback=None):
        result = super().run(request, progress_callback=progress_callback)
        topic = (
            "第51题：维生素B12吸收障碍的原因？ A 胃切除 B 回肠切除"
            if request.image is not None else request.resolved_topic_query
        )
        result.plan = ExecutionPlan(topic, "quick", [], [topic], topic_query=topic)
        return result


class PlannedOrchestrator(RecordingOrchestrator):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def run(self, request, *, progress_callback=None):
        result = super().run(request, progress_callback=progress_callback)
        result.plan = ExecutionPlan(request.query, self.mode, [], [request.query])
        return result


class ReviewOrchestrator(RecordingOrchestrator):
    def run(self, request, *, progress_callback=None):
        result = super().run(request, progress_callback=progress_callback)
        result.status = "review_required"
        result.error = "课程证据不足"
        result.rendered_answer = None
        result.plan = ExecutionPlan(request.query, "quick", [], [request.query])
        return result


class PartialOrchestrator(RecordingOrchestrator):
    def run(self, request, *, progress_callback=None):
        result = super().run(request, progress_callback=progress_callback)
        result.plan = ExecutionPlan(request.query, "guided", [], [request.query])
        result.answer = SimpleNamespace(answerability="partial")
        result.rendered_answer = "这是已经核验的课程部分。\n\n还需要进一步核实：\n- 临床处置"
        return result


class FakeFallbackController:
    def __init__(self):
        self.calls = []

    def run(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return FallbackOutcome(
            status="candidates_found",
            policy=str(kwargs["policy"].value),
            question_type="course_fact",
            message="找到外部候选资料，但尚未核验。",
            resources=[FallbackResource(
                source="wikipedia_zh",
                resource_id="1",
                title="动作电位",
                url="https://example.test/1",
                year=None,
                authors=(),
                identifiers={},
            )],
        )


class VerifiedOfficialFallbackController:
    def run(self, query, **kwargs):
        evidence = OfficialEvidenceItem(
            "ext_ev_1", "who", "WHO/1", "WHO shock guideline",
            "https://who.test/1", "全文字符 10-80",
            "Clinicians should reassess shock after resuscitation.",
            10, 62, "a" * 64, ("shock",), ("should",), 5.0,
        )
        return FallbackOutcome(
            status="candidates_found",
            policy=str(kwargs["policy"].value),
            question_type="clinical",
            message="找到 WHO 官方指南全文。",
            resources=[FallbackResource(
                source="who", resource_id="WHO/1", title="WHO shock guideline",
                url="https://who.test/1", year=2025,
                authors=("World Health Organization",), identifiers={},
                may_determine_answer=True, authority_level="A",
                is_guideline_or_consensus=True, has_full_text=True,
            )],
            publication_decision="eligible_for_verified_external_answer",
            publication_reason="官方指南全文可进入核验。",
            official_evidence=[evidence],
        )


class FakeExternalAnswerEngine:
    def __init__(self, status="pass"):
        self.status = status
        self.calls = []

    def answer(self, query, **kwargs):
        from types import SimpleNamespace
        self.calls.append((query, kwargs))
        return SimpleNamespace(
            status=self.status,
            answer=None,
            rendered_answer="【官方指南答复｜非课程教材证据】\n已核验答复" if self.status == "pass" else None,
            semantic_report=None,
            evidence_bundle=None,
            error=None if self.status == "pass" else "未通过核验",
        )


class FakeGeneralKnowledgeEngine:
    def __init__(self, status="pass"):
        self.status = status
        self.calls = []

    def answer(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(
            status=self.status,
            rendered_answer=(
                "【通用知识讲解｜未与课程资料核对】\n答案是 C。"
                if self.status == "pass" else None
            ),
            error=None if self.status == "pass" else "未通过一致性复核",
        )


class FakeResearchRetriever:
    def __init__(self, packet=None, error=None):
        self.packet = packet
        self.error = error
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.packet


class FakeResearchSynthesis:
    def __init__(self, synthesis, localized=None):
        self.synthesis = synthesis
        self.localized = localized or {}
        self.calls = []

    def synthesize(self, packet, **kwargs):
        self.calls.append(packet)
        return self.synthesis

    def localize_resources(self, resources):
        return self.localized


class FailingLayeredRetriever:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("research mode must not use the basic layered retriever")


class FakeCasualConversationEngine:
    def __init__(self, answer="听起来你今天有点累，我们可以慢慢聊一会儿。"):
        self.answer_text = answer
        self.calls = []

    def answer(self, query):
        self.calls.append(query)
        return self.answer_text


class InteractionModeTests(unittest.TestCase):
    def test_mode_options_are_stable_and_ui_serializable(self):
        options = mode_options()
        self.assertEqual(
            [item["id"] for item in options],
            ["auto", "quick", "guided", "socratic", "research"],
        )
        self.assertEqual(options[0]["display_name"], "智能选择")
        self.assertNotIn("mode", options[0])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知交互模式"):
            normalize_mode("magic")


class TutorApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = RecordingOrchestrator()
        self.service = TutorApplicationService(
            self.orchestrator,
            default_mode=InteractionMode.GUIDED,
            initial_proficiency_label="beginner",
        )

    def test_set_mode_is_used_by_following_requests(self):
        self.assertEqual(self.service.set_mode("socratic"), "socratic")
        result = self.service.ask("动作电位如何形成")

        self.assertEqual(result.rendered_answer, "ok")
        self.assertEqual(
            self.orchestrator.requests[-1].mode, InteractionMode.SOCRATIC
        )

    def test_usage_snapshot_deduplicates_shared_provider_client(self):
        class Meter:
            usage_totals = {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "requests": 2,
            }

        meter = Meter()
        orchestrator = RecordingOrchestrator()
        orchestrator.reasoning_client = meter
        orchestrator.semantic_verifier = SimpleNamespace(client=meter)
        service = TutorApplicationService(
            orchestrator,
            baseline_client=meter,
        )

        self.assertEqual(service.usage_snapshot()["total_tokens"], 25)

    def test_identity_question_uses_local_help_without_medical_retrieval(self):
        result = self.service.ask("你是谁？")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "local_help")
        self.assertEqual(result.plan.mode, "local_help")
        self.assertIn("MedScholar", result.rendered_answer)
        self.assertEqual(self.orchestrator.requests, [])

    def test_medical_question_still_uses_evidence_orchestrator(self):
        self.service.ask("动作电位为什么会发生复极化？")

        self.assertEqual(len(self.orchestrator.requests), 1)

    def test_conversation_context_reaches_learning_planner(self):
        self.service.ask(
            "我还是没听懂",
            conversation_context=[
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "上一版解释"},
            ],
        )

        request = self.orchestrator.requests[-1]
        self.assertEqual(
            request.conversation_context[0]["body"],
            "动作电位为什么会复极化？",
        )

    def test_video_followup_uses_previous_topic_without_medical_answer_pipeline(self):
        class FakeVideoRetriever:
            def __init__(self):
                self.queries = []

            def search(self, query):
                self.queries.append(query)
                return [SimpleNamespace(
                    resource_id="video_1",
                    title="动作电位动画",
                    url="https://www.bilibili.com/video/BV1/",
                    platform="Bilibili",
                    description="动画解释",
                    resource_kind="video_candidate",
                )]

        videos = FakeVideoRetriever()
        service = TutorApplicationService(
            self.orchestrator,
            video_retriever=videos,
        )

        result = service.ask(
            "有没有B站视频？",
            conversation_context=[
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "上一版解释"},
            ],
        )

        self.assertEqual(videos.queries, ["动作电位为什么会复极化？"])
        self.assertEqual(result.answer_source, "video_learning")
        self.assertEqual(result.external_resources[0]["resource_kind"], "video_candidate")
        self.assertEqual(
            [item["id"] for item in result.learning_actions],
            ["reexplain"],
        )
        self.assertEqual(self.orchestrator.requests, [])

    def test_conversation_key_keeps_topic_and_explicit_video_topic_replaces_it(self):
        class FakeVideoRetriever:
            def __init__(self):
                self.queries = []

            def search(self, query):
                self.queries.append(query)
                return []

        videos = FakeVideoRetriever()
        service = TutorApplicationService(
            self.orchestrator,
            video_retriever=videos,
        )
        service.ask("动作电位为什么会复极化？", conversation_key="session-1")
        service.ask(
            "举个例子",
            conversation_key="session-1",
            conversation_context=[{"role": "user", "body": "动作电位为什么会复极化？"}],
        )
        service.ask("找视频", conversation_key="session-1")
        service.ask("找糖酵解的视频", conversation_key="session-1")
        self.assertEqual(videos.queries, ["动作电位为什么会复极化？", "糖酵解"])

    def test_image_question_topic_is_available_for_followup(self):
        orchestrator = ImageContextOrchestrator()
        service = TutorApplicationService(orchestrator)
        service.ask(image=b"image", conversation_key="session-image")
        service.ask("那第51题为什么选B？", conversation_key="session-image")

        followup = orchestrator.requests[-1]
        self.assertIn("第51题：维生素B12", followup.resolved_topic_query)
        self.assertIn("本轮追问：那第51题为什么选B？", followup.resolved_effective_query)

    def test_explicit_image_revisit_uses_retained_file_with_new_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "question.png"
            image.write_bytes(b"image")
            service = TutorApplicationService(self.orchestrator)
            approvals = []
            service.ask(
                "请重新看原图解释第51题",
                prior_image=str(image),
                approval_callback=lambda request: approvals.append(request.title) or True,
            )
            self.assertEqual(self.orchestrator.requests[-1].image, str(image))
            self.assertEqual(len(approvals), 1)

    def test_structured_learning_action_reaches_planner_with_saved_topic(self):
        service = TutorApplicationService(self.orchestrator)
        service.ask("动作电位为什么会复极化？", conversation_key="session-action")
        service.ask(
            "请用一个贴合这个知识点的例子解释。",
            conversation_key="session-action",
            conversation_context=[
                {"role": "user", "body": f"无关历史消息 {index}"}
                for index in range(10)
            ],
            learning_action_id="example",
            learning_topic="动作电位为什么会复极化？",
        )

        request = self.orchestrator.requests[-1]
        self.assertEqual(request.resolved_learning_intent, "example")
        self.assertEqual(request.resolved_topic_query, "动作电位为什么会复极化？")
        self.assertIn("原学习主题：动作电位为什么会复极化？", request.resolved_effective_query)

    def test_structured_reexplain_passes_prior_answer_as_context(self):
        service = TutorApplicationService(self.orchestrator)
        service.ask(
            "请换一种说法",
            conversation_key="session-reexplain",
            learning_action_id="reexplain",
            learning_topic="动作电位复极化",
            conversation_context=[
                {"role": "user", "body": "动作电位复极化怎么发生？"},
                {"role": "assistant", "body": "上一版说钾离子外流。"},
            ],
        )
        request = self.orchestrator.requests[-1]
        self.assertIn("上一版说钾离子外流", request.resolved_effective_query)
        self.assertEqual(request.resolved_topic_query, "动作电位复极化")

    def test_understood_feedback_ends_turn_without_repeating_answer(self):
        result = self.service.ask(
            "我懂了",
            conversation_context=[
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "上一版解释"},
            ],
        )

        self.assertEqual(result.answer_source, "learning_feedback")
        self.assertEqual(result.learning_actions, [])
        self.assertEqual(self.orchestrator.requests, [])

    def test_research_followup_automatically_enters_research_mode(self):
        self.service.ask(
            "我想找论文拓展",
            conversation_context=[
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "上一版解释"},
            ],
        )

        self.assertEqual(
            self.orchestrator.requests[-1].mode,
            InteractionMode.RESEARCH,
        )

    def test_help_query_with_image_does_not_bypass_vision_pipeline(self):
        self.service.ask("你好", image=b"image", image_media_type="image/png")

        self.assertEqual(len(self.orchestrator.requests), 1)

    def test_image_request_can_pause_and_be_rejected_before_model_call(self):
        approvals = []

        result = self.service.ask(
            "识别这道题",
            image=b"image",
            image_media_type="image/png",
            approval_callback=lambda request: approvals.append(request) or False,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.orchestrator.requests, [])
        self.assertEqual(len(approvals), 1)
        self.assertIsInstance(approvals[0], ApprovalRequest)
        self.assertIn("视觉模型", approvals[0].description)

    def test_approved_image_request_resumes_same_service_call(self):
        result = self.service.ask(
            "识别这道题",
            image=b"image",
            image_media_type="image/png",
            approval_callback=lambda request: True,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.orchestrator.requests), 1)

    def test_clear_casual_chat_can_reply_without_evidence_pipeline(self):
        casual = FakeCasualConversationEngine()
        service = TutorApplicationService(
            self.orchestrator,
            casual_conversation_engine=casual,
        )

        result = service.ask("今天有点累，陪我聊聊天吧")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "casual_chat")
        self.assertIn("有点累", result.rendered_answer)
        self.assertEqual(casual.calls, ["今天有点累，陪我聊聊天吧"])
        self.assertEqual(self.orchestrator.requests, [])

    def test_cancelled_casual_model_result_is_not_published(self):
        cancelled = {"value": False}

        class CancellingCasualEngine:
            def answer(self, query):
                cancelled["value"] = True
                return "这条回复不应发布"

        service = TutorApplicationService(
            self.orchestrator,
            casual_conversation_engine=CancellingCasualEngine(),
        )

        with self.assertRaises(RunCancelled):
            service.ask(
                "今天有点累，陪我聊聊天吧",
                cancel_check=lambda: cancelled["value"],
            )

    def test_medical_word_overrides_casual_cue_and_keeps_strict_route(self):
        casual = FakeCasualConversationEngine()
        service = TutorApplicationService(
            self.orchestrator,
            casual_conversation_engine=casual,
        )

        service.ask("我对动作电位机制很难过，陪我聊聊怎么学")

        self.assertEqual(casual.calls, [])
        self.assertEqual(len(self.orchestrator.requests), 1)

    def test_one_request_override_does_not_change_session_mode(self):
        self.service.ask("最新研究", mode="research")

        self.assertEqual(
            self.orchestrator.requests[-1].mode, InteractionMode.RESEARCH
        )
        self.assertEqual(self.service.current_mode, "guided")

    def test_settings_snapshot_can_feed_a_settings_page(self):
        self.service.set_initial_proficiency_label("proficient")
        self.assertEqual(
            self.service.settings_snapshot(),
            {
                "mode": "guided",
                "initial_proficiency_label": "proficient",
                "evidence_policy": "local_first",
                "medical_horizon_policy": "research_mode_only",
                "subject": "auto",
            },
        )

    def test_subject_has_stable_ui_interface_and_reaches_request(self):
        self.assertEqual(self.service.current_subject, "auto")
        self.assertEqual(self.service.set_subject("biochemistry"), "biochemistry")
        self.service.ask("糖酵解的关键酶是什么")
        self.assertEqual(self.orchestrator.requests[-1].subject, "biochemistry")
        self.assertIn(
            "physiology",
            [item["id"] for item in self.service.available_subjects()],
        )

    def test_evidence_policy_has_stable_ui_interface(self):
        self.assertEqual(self.service.set_evidence_policy("local_only"), "local_only")
        self.assertEqual(self.service.current_evidence_policy, "local_only")
        self.assertEqual(
            [item["id"] for item in self.service.available_evidence_policies()],
            ["local_only", "local_first", "research_deep"],
        )

    def test_medical_horizon_policy_has_stable_ui_interface(self):
        self.assertEqual(
            self.service.set_medical_horizon_policy("all_answers"),
            "all_answers",
        )
        self.assertEqual(self.service.current_medical_horizon_policy, "all_answers")
        self.assertEqual(
            [item["id"] for item in self.service.available_medical_horizon_policies()],
            ["off", "research_mode_only", "all_answers"],
        )

    def test_official_source_status_has_stable_ui_interface(self):
        sources = {
            item["id"]: item
            for item in self.service.available_official_guideline_sources()
        }
        self.assertEqual(sources["who"]["automation_status"], "automatic")
        self.assertEqual(sources["nice"]["automation_status"], "license_required")
        self.assertEqual(
            sources["nhc_cn"]["automation_status"],
            "official_search_only",
        )
        self.assertTrue(sources["nhc_cn"]["search_url"].startswith("https://"))

    def test_review_required_result_triggers_safe_external_fallback(self):
        fallback = FakeFallbackController()
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=fallback,
        )
        result = service.ask("动作电位机制")
        self.assertEqual(result.status, "review_required")
        self.assertEqual(result.fallback_status, "candidates_found")
        self.assertFalse(result.external_resources[0]["may_determine_answer"])
        self.assertEqual(len(fallback.calls), 1)

    def test_low_risk_course_gap_can_use_verified_general_knowledge(self):
        fallback = FakeFallbackController()
        general = FakeGeneralKnowledgeEngine()
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=fallback,
            general_knowledge_engine=general,
        )
        result = service.ask("[单选题] 关于辅助因子，下列错误的是")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "general_knowledge")
        self.assertIn("未与课程资料核对", result.rendered_answer)
        self.assertEqual(len(general.calls), 1)
        self.assertEqual(fallback.calls, [])

    def test_low_risk_general_failure_can_still_continue_to_external_fallback(self):
        fallback = FakeFallbackController()
        general = FakeGeneralKnowledgeEngine("fail")
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=fallback,
            general_knowledge_engine=general,
        )
        result = service.ask("[单选题] 关于辅助因子，下列错误的是")
        self.assertEqual(result.status, "review_required")
        self.assertEqual(result.fallback_status, "candidates_found")
        self.assertEqual(len(fallback.calls), 1)
        self.assertEqual(len(general.calls), 1)

    def test_local_only_never_publishes_general_model_answer(self):
        fallback = FakeFallbackController()
        general = FakeGeneralKnowledgeEngine()
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=fallback,
            general_knowledge_engine=general,
        )
        result = service.ask(
            "[单选题] 关于辅助因子，下列错误的是",
            evidence_policy="local_only",
        )
        self.assertEqual(result.status, "review_required")
        self.assertEqual(general.calls, [])
        self.assertEqual(len(fallback.calls), 1)

    def test_general_knowledge_fallback_is_not_used_for_clinical_question(self):
        class ClinicalFallback(FakeFallbackController):
            def run(self, query, **kwargs):
                outcome = super().run(query, **kwargs)
                outcome.question_type = "clinical"
                return outcome

        general = FakeGeneralKnowledgeEngine()
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=ClinicalFallback(),
            general_knowledge_engine=general,
        )
        result = service.ask("患者应该如何用药")
        self.assertEqual(result.status, "review_required")
        self.assertEqual(general.calls, [])

    def test_partial_answer_is_kept_while_external_fallback_continues(self):
        fallback = FakeFallbackController()
        service = TutorApplicationService(
            PartialOrchestrator(),
            fallback_controller=fallback,
        )
        result = service.ask("动作电位机制与临床处置")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer.answerability, "partial")
        self.assertIn("已经核验的课程部分", result.rendered_answer)
        self.assertEqual(result.fallback_status, "candidates_found")
        self.assertEqual(len(fallback.calls), 1)

    def test_low_risk_partial_course_answer_can_be_completed_by_general_fallback(self):
        general = FakeGeneralKnowledgeEngine()
        service = TutorApplicationService(
            PartialOrchestrator(),
            general_knowledge_engine=general,
        )

        result = service.ask(
            "[多选题] 参与脂肪酸β-氧化的有： A. FAD B. NAD+ C. NADP+ D. CoA-SH"
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "general_knowledge")
        self.assertIsNone(result.answer)
        self.assertEqual(len(general.calls), 1)

    def test_external_resource_uses_verified_chinese_title_and_purpose(self):
        localizer = FakeResearchSynthesis(
            ResearchSynthesis("pass", "", [], []),
            {"1": {
                "chinese_title": "动作电位基础介绍",
                "purpose": "介绍动作电位的基本概念及其生理学意义。",
            }},
        )
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=FakeFallbackController(),
            research_synthesis=localizer,
        )
        result = service.ask("动作电位机制")
        self.assertEqual(result.external_resources[0]["title_zh"], "动作电位基础介绍")
        self.assertEqual(
            result.external_resources[0]["description"],
            "介绍动作电位的基本概念及其生理学意义。",
        )

    def test_verified_official_fallback_can_upgrade_only_after_external_engine_passes(self):
        engine = FakeExternalAnswerEngine()
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=VerifiedOfficialFallbackController(),
            external_answer_engine=engine,
        )
        result = service.ask("休克病人处理原则")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "official_external")
        self.assertIn("非课程教材证据", result.rendered_answer)
        self.assertEqual(engine.calls[0][0], "休克病人处理原则")
        self.assertEqual(len(result.external_evidence), 1)

    def test_failed_external_verification_preserves_review_required_status(self):
        service = TutorApplicationService(
            ReviewOrchestrator(),
            fallback_controller=VerifiedOfficialFallbackController(),
            external_answer_engine=FakeExternalAnswerEngine("fail"),
        )
        result = service.ask("休克病人处理原则")
        self.assertEqual(result.status, "review_required")
        self.assertEqual(result.answer_source, "course")
        self.assertEqual(result.error, "未通过核验")

    def test_failed_external_upgrade_does_not_mark_valid_partial_answer_as_error(self):
        service = TutorApplicationService(
            PartialOrchestrator(),
            fallback_controller=VerifiedOfficialFallbackController(),
            external_answer_engine=FakeExternalAnswerEngine("fail"),
        )

        result = service.ask("课程机制与临床表现")

        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error)
        self.assertEqual(result.answer.answerability, "partial")

    def test_local_only_review_skips_network_inside_controller(self):
        fallback = FakeFallbackController()
        service = TutorApplicationService(
            ReviewOrchestrator(), fallback_controller=fallback
        )
        result = service.ask("动作电位机制", evidence_policy="local_only")
        self.assertEqual(fallback.calls[0][1]["policy"].value, "local_only")
        self.assertEqual(result.status, "review_required")

    def test_invalid_mode_never_reaches_orchestrator(self):
        with self.assertRaises(ValueError):
            self.service.ask("测试", mode="invalid")
        self.assertEqual(self.orchestrator.requests, [])

    def test_cancelled_run_stops_before_orchestrator(self):
        with self.assertRaises(RunCancelled):
            self.service.ask("动作电位", cancel_check=lambda: True)
        self.assertEqual(self.orchestrator.requests, [])

    def test_expired_whole_turn_deadline_stops_before_orchestrator(self):
        with self.assertRaises(RunTimedOut):
            self.service.ask("动作电位", deadline_monotonic=0.0)
        self.assertEqual(self.orchestrator.requests, [])

    def test_service_forwards_core_progress_as_stable_updates(self):
        updates = []
        self.service.ask("动作电位", progress_callback=updates.append)
        self.assertEqual(updates, [ProgressUpdate("planned", "测试规划完成", "ok")])

    def test_only_approved_skill_is_recalled_into_agent_request(self):
        store = SkillStore()
        pending = store.create_candidate(
            question="动作电位", guidance="按阶段解释", kind=SkillKind.WORKFLOW,
            trigger_terms=("动作电位",),
        )
        store.approve(pending.skill_id)
        service = TutorApplicationService(self.orchestrator, skill_store=store)
        result = service.ask("动作电位的形成")
        self.assertEqual(len(self.orchestrator.requests[-1].approved_skills), 1)
        self.assertEqual(result.recalled_skills[0]["skill_id"], pending.skill_id)

    def test_research_mode_uses_verified_research_branch_without_duplicate_search(self):
        plan = ResearchSearchPlan("query", "查询", "query", ("query",), None, 2026, ())
        paper = ResearchPaper(
            "paper_1", "Paper", 2025, ("A",), "Abstract", "https://example.test",
            "10/x", ("pubmed", "openalex"), True, ("review",),
            "证据层级D（叙述性综述）", 1.0,
        )
        packet = ResearchPacket(plan, [paper])
        retriever = FakeResearchRetriever(packet)
        synthesis = FakeResearchSynthesis(
            ResearchSynthesis("review", "", [], [], ["not fully supported"])
        )
        service = TutorApplicationService(
            PlannedOrchestrator("research"),
            retrieval_agent=FailingLayeredRetriever(),
            research_retriever=retriever,
            research_synthesis=synthesis,
        )
        result = service.ask("query")
        self.assertEqual(retriever.calls[0][0], "query")
        self.assertIsNone(retriever.calls[0][1]["filters"])
        self.assertEqual(result.research_synthesis_status, "review")
        self.assertTrue(result.external_resources[0]["cross_validated"])
        self.assertIn("仅列出候选论文", result.rendered_answer)

    def test_explicit_paper_action_searches_topic_even_when_course_answer_is_withheld(self):
        class ResearchReviewOrchestrator(RecordingOrchestrator):
            def run(self, request, *, progress_callback=None):
                result = super().run(request, progress_callback=progress_callback)
                result.status = "review_required"
                result.error = "课程证据不足"
                result.rendered_answer = None
                result.plan = ExecutionPlan(
                    request.resolved_effective_query, "research", [],
                    [request.resolved_effective_query],
                    learning_intent="research",
                    topic_query=request.resolved_topic_query,
                )
                return result

        plan = ResearchSearchPlan("动作电位", "动作电位", "action potential", ("action potential",), None, 2026, ())
        paper = ResearchPaper(
            "paper_1", "Action potential review", 2025, ("A",),
            "Action potential review abstract.", "https://example.test/paper",
            None, ("pubmed",), False, ("review",), "综述", 1.0,
        )
        retriever = FakeResearchRetriever(ResearchPacket(plan, [paper]))
        orchestrator = ResearchReviewOrchestrator()
        service = TutorApplicationService(
            orchestrator,
            research_retriever=retriever,
        )
        result = service.ask(
            "找论文拓展", learning_action_id="research",
            learning_topic="动作电位为什么会复极化？",
        )
        self.assertEqual(retriever.calls[0][0], "动作电位为什么会复极化？")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer_source, "research_extension")
        self.assertIn("Action potential review", result.rendered_answer)
        self.assertEqual(result.external_resources[0]["resource_kind"], "research_paper")
        self.assertEqual(result.external_resources[0]["url"], "https://example.test/paper")
        self.assertNotIn("课程证据不足", result.rendered_answer)
        self.assertEqual(orchestrator.requests, [])

    def test_explicit_paper_action_with_no_hits_does_not_repeat_course_answer(self):
        plan = ResearchSearchPlan("动作电位", "动作电位", "action potential", ("action potential",), None, 2026, ())
        service = TutorApplicationService(
            PlannedOrchestrator("research"),
            research_retriever=FakeResearchRetriever(ResearchPacket(plan)),
        )
        result = service.ask(
            "找论文拓展", learning_action_id="research",
            learning_topic="动作电位",
        )
        self.assertEqual(result.answer_source, "research_extension")
        self.assertEqual(result.status, "completed")
        self.assertNotIn("ok", result.rendered_answer)
        self.assertIn("没有找到", result.rendered_answer)

    def test_research_extension_failure_preserves_core_answer(self):
        service = TutorApplicationService(
            PlannedOrchestrator("research"),
            research_retriever=FakeResearchRetriever(error=TimeoutError()),
        )
        result = service.ask("query")
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.rendered_answer.startswith("ok"))
        self.assertEqual(result.research_synthesis_status, "error")
        self.assertIn("课程证据答案不受影响", result.rendered_answer)

    def test_all_answers_policy_adds_verified_horizon_to_quick_answer(self):
        plan = ResearchSearchPlan("query", "查询", "query", ("query",), None, 2026, ())
        paper = ResearchPaper(
            "paper_1", "Paper", 2025, ("A",), "Abstract", "https://example.test",
            "10/x", ("pubmed", "openalex"), True, ("review",),
            "证据层级D（叙述性综述）", 1.0,
        )
        retriever = FakeResearchRetriever(ResearchPacket(plan, [paper]))
        synthesis = FakeResearchSynthesis(
            ResearchSynthesis(
                "pass", "一个经核验的医学拓展。", [], [],
                introductions=[ResearchIntroduction(
                    "intro_paper_1", "paper_1",
                    "这篇论文介绍相关生理学研究。", "Abstract",
                )],
            )
        )
        service = TutorApplicationService(
            PlannedOrchestrator("quick"),
            research_retriever=retriever,
            research_synthesis=synthesis,
            default_medical_horizon_policy="all_answers",
        )
        result = service.ask("query")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.research_synthesis_status, "pass")
        self.assertIn("论文摘要中可定位的原文", result.rendered_answer)
        self.assertNotIn("一个经核验的医学拓展", result.rendered_answer)
        self.assertEqual(
            result.external_resources[0]["description"],
            "这篇论文介绍相关生理学研究。",
        )

    def test_off_policy_skips_horizon_even_in_research_mode(self):
        retriever = FakeResearchRetriever(error=AssertionError("不应检索"))
        service = TutorApplicationService(
            PlannedOrchestrator("research"),
            research_retriever=retriever,
            default_medical_horizon_policy="off",
        )
        result = service.ask("query")
        self.assertEqual(result.rendered_answer, "ok")
        self.assertEqual(retriever.calls, [])


class RuntimeInspectionTests(unittest.TestCase):
    def test_missing_files_are_reported_for_configuration_page(self):
        with tempfile.TemporaryDirectory() as temp:
            inspection = inspect_runtime(RuntimePaths.from_base_dir(temp))

        self.assertFalse(inspection.ready)
        self.assertTrue(any("缺少配置文件" in item for item in inspection.issues))
        self.assertEqual(inspection.modes[0]["id"], "auto")


if __name__ == "__main__":
    unittest.main()
