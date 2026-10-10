import json
import tempfile
import unittest
from pathlib import Path

from answer_contract import AnswerClaim, AnswerDraft

from agent_orchestrator import (
    AgentOrchestrator,
    AgentRequest,
    ExecutionPlan,
    QuestionPlanner,
    RunStatus,
    VisualObservation,
    _decompose_search_queries,
    _discard_non_surface_failed_claims,
    _accept_explicit_partial_coverage,
    _extract_answer_requirements,
    _substantially_repeats,
    _trim_repeated_explanation,
)
from evidence_bundle import EvidenceBundleBuilder
from evidence_verify import EvidenceVerifier
from page_text_index import PageTextIndex, PageTextRecord, save_page_text_index
from structure_retriever import RetrievalHit, RetrievalResult, StructureAwareRetriever
from semantic_claim_verifier import (
    ClaimSemanticVerdict,
    SemanticVerificationReport,
)


class FakeReasoningClient:
    def __init__(self):
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        if "current_answer" in payload:
            data = dict(payload["current_answer"])
            data["claims"] = [dict(data["claims"][0])]
            data["claims"][0]["text"] = "静息电位与膜通透性有关。"
            return data
        task = payload["task"]
        evidence_id = task["evidence"][0]["evidence_id"]
        return {
            "schema_version": "1.0",
            "query": task["query"],
            "mode": task["mode"],
            "direct_answer": "静息电位与离子平衡电位及膜通透性有关。",
            "claims": [
                {
                    "claim_id": "claim_1",
                    "text": "静息电位与钾离子平衡电位和膜通透性有关。",
                    "category": "mechanism",
                    "strength": "causal",
                    "evidence_ids": [evidence_id],
                    "uncertainty": None,
                }
            ],
            "explanation": "根据课程材料进行解释。",
            "teaching_questions": ["静息时哪种离子的通透性最高？"],
            "research_extension": [],
            "limitations": [],
        }


class FakeSearchPlanningClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        if self.fail:
            raise RuntimeError("planning service unavailable")
        return {
            "search_queries": [
                "右心衰竭 静脉回流 中心静脉压",
                "右心衰竭 毛细血管静水压 组织液滤出",
                "右心衰竭 肾素 血管紧张素 醛固酮 水钠潴留",
            ]
        }


class ResolvedLearningContextTests(unittest.TestCase):
    def test_followup_uses_original_for_search_but_current_turn_for_coverage(self):
        original = "第51题：A. 胃切除 B. 回肠切除 C. 胃酸不足"
        plan = QuestionPlanner().plan(AgentRequest(
            query="为什么选B？",
            resolved_learning_intent="question",
            resolved_topic_query=original,
            resolved_effective_query=(
                f"原题或学习主题：{original}\n本轮追问：为什么选B？"
            ),
        ))
        self.assertEqual(plan.topic_query, original)
        self.assertEqual(plan.answer_requirements, [])

    def test_image_ocr_becomes_topic_even_with_generic_user_prompt(self):
        recognized = "第51题：巨幼细胞贫血的原因？ A 胃切除 B 回肠切除"
        observation = VisualObservation(recognized, recognized, [], 0.9, False)
        plan = QuestionPlanner().plan(AgentRequest(
            query="帮我做这道题",
            resolved_learning_intent="question",
            resolved_topic_query="帮我做这道题",
            resolved_effective_query="帮我做这道题",
        ), observation)
        self.assertEqual(plan.topic_query, recognized)
        self.assertIn(recognized, plan.effective_query)

    def test_planner_uses_service_resolved_topic_instead_of_recent_messages(self):
        plan = QuestionPlanner().plan(AgentRequest(
            query="请用一个贴合这个知识点的例子解释。",
            conversation_context=[
                {"role": "user", "body": f"最近但无关的消息 {index}"}
                for index in range(8)
            ],
            resolved_learning_intent="example",
            resolved_topic_query="动作电位为什么会复极化？",
            resolved_effective_query=(
                "原学习主题：动作电位为什么会复极化？\n"
                "教学要求：请给出一个贴合原问题的例子。"
            ),
        ))

        self.assertEqual(plan.learning_intent, "example")
        self.assertEqual(plan.topic_query, "动作电位为什么会复极化？")
        self.assertIn("原学习主题：动作电位", plan.effective_query)

    def test_example_followup_does_not_inherit_choice_coverage_requirements(self):
        original = "血清清蛋白何时带正电？A. pH4.0 B. pH4.7 C. pH5.0"
        plan = QuestionPlanner().plan(AgentRequest(
            query="请举个例子",
            resolved_learning_intent="example",
            resolved_topic_query=original,
            resolved_effective_query=f"原学习主题：{original}\n教学要求：只举一个新例子。",
        ))
        self.assertEqual(plan.answer_requirements, [])
        self.assertEqual(plan.learning_intent, "example")

    def test_practice_followup_does_not_inherit_choice_coverage_requirements(self):
        original = "清蛋白何时带正电？A. pH4.0 B. pH4.7 C. pH5.0"
        plan = QuestionPlanner().plan(AgentRequest(
            query="请出题检验",
            resolved_learning_intent="practice",
            resolved_topic_query=original,
            resolved_effective_query=f"原学习主题：{original}\n教学要求：只出一道新题。",
        ))
        self.assertEqual(plan.answer_requirements, [])
        self.assertEqual(plan.learning_intent, "practice")


class InsufficientReasoningClient(FakeReasoningClient):
    def complete_json(self, system_instruction, payload):
        data = super().complete_json(system_instruction, payload)
        data["answerability"] = "insufficient"
        return data


class PartialReasoningClient(FakeReasoningClient):
    def complete_json(self, system_instruction, payload):
        data = super().complete_json(system_instruction, payload)
        data["answerability"] = "partial"
        data["unanswered_aspects"] = ["临床处置缺少当前指南证据。"]
        return data


class EmptyClaimsThenRepairClient(FakeReasoningClient):
    def complete_json(self, system_instruction, payload):
        if not self.calls:
            self.calls.append((system_instruction, payload))
            task = payload["task"]
            return {
                "schema_version": "1.0",
                "query": task["query"],
                "mode": task["mode"],
                "answerability": "answer",
                "direct_answer": "格式不完整。",
                "claims": [],
                "explanation": "格式不完整。",
                "teaching_questions": [],
                "research_extension": [],
                "limitations": [],
            }
        return super().complete_json(system_instruction, payload)


class FakeSemanticClient:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def verify_json(self, request):
        self.calls.append(request)
        verdict = self.verdicts.pop(0)
        results = []
        for claim in request["claims"]:
            if claim["claim_id"] == "__question_coverage__":
                results.append(
                    {
                        "claim_id": claim["claim_id"],
                        "verdict": verdict,
                        "reason": "测试题意覆盖结果。",
                        "supporting_quotes": [],
                        "suggested_revision": (
                            "补充题目要求。" if verdict != "supported" else None
                        ),
                    }
                )
                continue
            evidence_id = claim["evidence_ids"][0]
            quotes = (
                [{"evidence_id": evidence_id, "quote": "静息电位主要与钾离子平衡电位"}]
                if verdict == "supported"
                else []
            )
            results.append(
                {
                    "claim_id": claim["claim_id"],
                    "verdict": verdict,
                    "reason": "测试核验结果。",
                    "supporting_quotes": quotes,
                    "suggested_revision": (
                        "降低结论强度。" if verdict != "supported" else None
                    ),
                }
            )
        return {"claims": results}


class HiddenClaimConflictSemanticClient:
    def verify_json(self, request):
        results = []
        for claim in request["claims"]:
            claim_id = claim["claim_id"]
            if claim_id == "claim_1":
                verdict = "contradicted"
                quotes = []
            elif claim_id == "__question_coverage__":
                verdict = "supported"
                quotes = []
            else:
                verdict = "supported"
                evidence_id = claim["evidence_ids"][0]
                quotes = [{
                    "evidence_id": evidence_id,
                    "quote": "静息电位主要与钾离子平衡电位",
                }]
            results.append({
                "claim_id": claim_id,
                "verdict": verdict,
                "reason": "测试隐藏主张清理。",
                "supporting_quotes": quotes,
                "suggested_revision": None,
            })
        return {"claims": results}


class FakeVisionClient:
    def analyze_image(self, image, prompt, *, media_type=None):
        return {
            "question_text": "静息电位如何形成",
            "plain_text": "静息电位如何形成",
            "uncertain_spans": [],
            "overall_confidence": 0.99,
            "requires_recheck": False,
        }


class FakeVisionStemClient:
    def analyze_image(self, image, prompt, *, media_type=None):
        return {
            "question_number": "22*",
            "stem": "关于细胞静息电位的论述，不正确的是：",
            "options": [
                {"label": "A", "content": "细胞膜处于极化状态"},
                {"label": "B", "content": "静息电位主要由K+内流形成"},
            ],
            "uncertain_spans": [],
        }


class AgentOrchestratorTests(unittest.TestCase):
    def test_planner_decomposes_explicit_mechanism_perspectives(self):
        queries = _decompose_search_queries(
            "某患者右心衰竭出现下肢水肿。从静脉回流、Starling力和肾脏体液调节角度解释。"
        )
        self.assertEqual(queries[0], "某患者右心衰竭出现下肢水肿。从静脉回流、Starling力和肾脏体液调节角度解释。")
        self.assertTrue(any("Starling力" in item for item in queries[1:]))
        self.assertTrue(any("肾脏体液调节" in item for item in queries[1:]))

    def test_answer_requirements_only_use_explicit_user_aspects(self):
        requirements = _extract_answer_requirements(
            "某患者左心衰竭。从Starling力和淋巴回流角度解释肺水肿。"
        )

        self.assertEqual(requirements, ["Starling力", "淋巴回流"])

    def test_multiple_choice_requires_every_option_to_be_judged(self):
        requirements = _extract_answer_requirements(
            "参与脂肪酸β-氧化的有： A. FAD B. NAD+ C. NADP+ D. CoA-SH"
        )
        self.assertEqual(len(requirements), 4)
        self.assertEqual(requirements[0], "逐项判断 A. FAD")
        self.assertEqual(requirements[-1], "逐项判断 D. CoA-SH")

    def test_partial_gap_triggers_one_targeted_course_retrieval(self):
        def hit(page):
            return RetrievalHit(
                document_id="doc_biochem",
                original_name="生化教材.pdf",
                document_type="textbook",
                pdf_page=page,
                printed_page=page - 29,
                locator=f"PDF第 {page} 页",
                section_title="脂肪酸β-氧化",
                score=5.0,
                matched_terms=["脂肪酸β氧化"],
                snippet="",
                context_pdf_pages=[page],
                context_text="",
            )

        class GapRetriever:
            def __init__(self):
                self.queries = []

            def search(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return RetrievalResult(query, "gap", ["生化教材.pdf"], [hit(176)])

        retriever = GapRetriever()
        orchestrator = AgentOrchestrator(
            retriever=retriever,
            evidence_builder=object(),
            evidence_verifier=object(),
            reasoning_client=object(),
            semantic_client=object(),
        )
        initial = RetrievalResult(
            "参与脂肪酸β-氧化的有",
            "initial",
            ["生化教材.pdf"],
            [hit(177)],
        )
        plan = ExecutionPlan(
            effective_query=initial.query,
            mode="quick",
            clarification_questions=[],
            search_queries=[initial.query],
            topic_query=initial.query,
            subject_ids=["biochemistry"],
        )

        supplemented = orchestrator._supplement_retrieval(
            plan,
            initial,
            ["FAD 与 NAD+ 是否参与"],
        )

        self.assertEqual([item.pdf_page for item in supplemented.hits], [176, 177])
        self.assertIn("FAD 与 NAD+", retriever.queries[0][0])
        self.assertEqual(retriever.queries[0][1]["subject_ids"], ["biochemistry"])

    def test_combined_aspects_are_decomposed_and_kept_ahead_of_synonyms(self):
        client = FakeSearchPlanningClient()
        plan = QuestionPlanner(client).plan(AgentRequest(
            query="结合门静脉系统和Starling力解释肝硬化腹水"
        ))

        self.assertEqual(plan.answer_requirements, ["门静脉系统", "Starling力"])
        self.assertIn("门静脉系统", plan.search_queries[1])
        self.assertIn("Starling力", plan.search_queries[2])

    def test_retrieval_synonyms_do_not_become_answer_requirements(self):
        client = FakeSearchPlanningClient()
        plan = QuestionPlanner(client).plan(AgentRequest(
            query="从静脉回流和Starling力角度解释右心衰竭水肿"
        ))

        self.assertEqual(plan.answer_requirements, ["静脉回流", "Starling力"])
        self.assertFalse(any("肾素" in item for item in plan.answer_requirements))
        self.assertEqual(len(client.calls), 1)

    def test_repeated_answer_paragraphs_are_detected_for_compact_rendering(self):
        direct = "右心衰竭使右心房压升高，静脉回流减慢并形成静脉淤血。" * 4
        explanation = direct + "现有证据尚未覆盖肾脏体液调节。"
        self.assertTrue(_substantially_repeats(direct, explanation))

    def test_repeated_explanation_sentences_are_trimmed_but_new_detail_remains(self):
        direct = "左心衰竭使肺毛细血管压升高，液体滤出增加并形成肺水肿。"
        explanation = (
            "左心衰竭使肺毛细血管压升高，液体滤出增加并形成肺水肿。"
            "正常情况下，少量滤出液可由肺淋巴管回流。"
        )

        trimmed = _trim_repeated_explanation(direct, explanation)

        self.assertNotIn("左心衰竭使肺毛细血管压升高", trimmed)
        self.assertIn("肺淋巴管回流", trimmed)

    def test_failed_hidden_claim_is_dropped_when_all_published_surfaces_pass(self):
        answer = AnswerDraft(
            schema_version="1.0",
            query="测试问题",
            mode="guided",
            direct_answer="已核验正文",
            claims=[
                AnswerClaim("claim_1", "未进入正文的冲突候选", "course_fact", "descriptive", ["ev_1"]),
                AnswerClaim("claim_2", "正文使用的支持主张", "mechanism", "causal", ["ev_2"]),
            ],
            explanation="已核验解释",
        )
        report = SemanticVerificationReport(
            status="fail",
            claims=[
                ClaimSemanticVerdict("claim_1", "contradicted", "冲突", []),
                ClaimSemanticVerdict("claim_2", "supported", "支持", []),
                ClaimSemanticVerdict("__direct_answer__", "supported", "支持", []),
                ClaimSemanticVerdict("__explanation__", "supported", "支持", []),
                ClaimSemanticVerdict("__question_coverage__", "supported", "覆盖", []),
            ],
        )

        changed = _discard_non_surface_failed_claims(answer, report)

        self.assertTrue(changed)
        self.assertEqual(report.status, "pass")
        self.assertEqual([claim.claim_id for claim in answer.claims], ["claim_2"])

    def test_explicit_partial_coverage_can_correct_coverage_only_false_negative(self):
        answer = AnswerDraft(
            schema_version="1.0",
            query="区分急性疲劳和延迟性酸痛",
            mode="guided",
            direct_answer="从代谢角度可解释急性疲劳；延迟性酸痛证据不足。",
            claims=[
                AnswerClaim("claim_1", "急性疲劳涉及代谢变化", "mechanism", "causal", ["ev_1"]),
            ],
            explanation="兴奋-收缩耦联参与肌肉收缩。",
            answerability="partial",
            unanswered_aspects=["延迟性酸痛的机制尚缺证据。"],
        )
        report = SemanticVerificationReport(
            status="review",
            claims=[
                ClaimSemanticVerdict("claim_1", "supported", "支持", []),
                ClaimSemanticVerdict("__direct_answer__", "supported", "支持", []),
                ClaimSemanticVerdict("__explanation__", "supported", "支持", []),
                ClaimSemanticVerdict(
                    "__question_coverage__",
                    "partially_supported",
                    "误判遗漏",
                    [],
                ),
            ],
        )

        changed = _accept_explicit_partial_coverage(
            answer,
            report,
            ["代谢", "兴奋-收缩耦联", "延迟性酸痛"],
        )

        self.assertTrue(changed)
        self.assertEqual(report.status, "pass")

    def test_explicit_partial_coverage_normalizes_coupling_term_variant(self):
        answer = AnswerDraft(
            schema_version="1.0",
            query="从兴奋-收缩耦联角度解释",
            mode="guided",
            direct_answer="急性疲劳可从代谢改变解释。",
            claims=[
                AnswerClaim("claim_1", "代谢改变", "mechanism", "causal", ["ev_1"]),
            ],
            explanation="兴奋-收缩偶联参与肌肉收缩。",
            answerability="partial",
            unanswered_aspects=["延迟性肌肉酸痛机制尚缺证据。"],
        )
        report = SemanticVerificationReport(
            status="review",
            claims=[
                ClaimSemanticVerdict("claim_1", "supported", "支持", []),
                ClaimSemanticVerdict("__direct_answer__", "supported", "支持", []),
                ClaimSemanticVerdict("__explanation__", "supported", "支持", []),
                ClaimSemanticVerdict("__question_coverage__", "partially_supported", "误判", []),
            ],
        )

        self.assertTrue(_accept_explicit_partial_coverage(
            answer,
            report,
            ["兴奋-收缩耦联", "延迟性肌肉酸痛"],
        ))
        self.assertEqual(report.status, "pass")

    def test_distinct_mechanism_explanation_is_kept(self):
        self.assertFalse(
            _substantially_repeats(
                "低钾血症使骨骼肌兴奋性下降。",
                "细胞外钾降低使钾平衡电位变得更负，膜电位远离阈电位。",
            )
        )

    def test_quick_mode_hides_generated_teaching_questions(self):
        orchestrator, _, _ = self._orchestrator(["supported"])

        result = orchestrator.run(
            AgentRequest(query="静息电位如何形成", mode="quick")
        )

        self.assertEqual(result.status, "completed")
        self.assertNotIn("思考提示", result.rendered_answer)

    def test_socratic_mode_places_questions_before_reference_answer(self):
        orchestrator, _, _ = self._orchestrator(["supported"])

        result = orchestrator.run(
            AgentRequest(query="静息电位如何形成", mode="socratic")
        )

        self.assertEqual(result.status, "completed")
        self.assertLess(
            result.rendered_answer.index("先想一想"),
            result.rendered_answer.index("参考思路"),
        )

    def test_generation_receives_decomposed_answer_requirements(self):
        orchestrator, reasoning, _ = self._orchestrator(["supported"])

        orchestrator.run(AgentRequest(
            query="从膜通透性和离子浓度梯度角度解释静息电位形成",
            mode="guided",
        ))

        requirements = reasoning.calls[0][1]["task"]["answer_requirements"]
        self.assertTrue(any("膜通透性" in item for item in requirements))
        self.assertTrue(any("离子浓度梯度" in item for item in requirements))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.map_dir = root / "maps"
        self.index_dir = root / "indexes"
        self.map_dir.mkdir()
        self.index_dir.mkdir()
        document_map = {
            "schema_version": "1.0",
            "document_id": "doc_test",
            "source_path": "test.pdf",
            "original_name": "生理学教材.pdf",
            "document_type": "textbook",
            "page_count": 10,
            "toc_entries": [{"title": "静息电位"}],
            "toc_pdf_pages": [],
            "nodes": [
                {
                    "level": 3,
                    "title": "静息电位",
                    "pdf_start": 10,
                    "pdf_end": 10,
                    "source": "outline",
                }
            ],
            "page_map_segments": [
                {"pdf_start": 8, "pdf_end": 10, "offset": 7}
            ],
        }
        (self.map_dir / "map.json").write_text(
            json.dumps(document_map, ensure_ascii=False), encoding="utf-8"
        )
        pages = [
            PageTextRecord(page, None, "无关内容", 4, "ok")
            for page in range(1, 11)
        ]
        text = "静息电位主要与钾离子平衡电位和静息状态下的膜通透性有关。"
        pages[9] = PageTextRecord(10, "静息电位", text, len(text), "ok")
        save_page_text_index(
            PageTextIndex("1.0", "doc_test", "test.pdf", 10, pages),
            self.index_dir / "pages.json",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _orchestrator(self, semantic_verdicts, *, with_vision=False, max_revisions=1):
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )
        builder = EvidenceBundleBuilder.from_directories(
            self.map_dir, self.index_dir
        )
        verifier = EvidenceVerifier.from_directories(
            self.map_dir, self.index_dir
        )
        reasoning = FakeReasoningClient()
        semantic = FakeSemanticClient(semantic_verdicts)
        orchestrator = AgentOrchestrator(
            retriever=retriever,
            evidence_builder=builder,
            evidence_verifier=verifier,
            reasoning_client=reasoning,
            semantic_client=semantic,
            vision_client=FakeVisionClient() if with_vision else None,
            max_revisions=max_revisions,
        )
        return orchestrator, reasoning, semantic

    def test_complete_text_question_passes_all_gates(self):
        orchestrator, reasoning, semantic = self._orchestrator(["supported"])
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))

        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertIn("教材印刷页 3（PDF第 10 页）", result.rendered_answer)
        self.assertEqual(len(reasoning.calls), 1)
        self.assertEqual(len(semantic.calls), 1)

    def test_progress_callback_receives_recorded_execution_events(self):
        orchestrator, _, _ = self._orchestrator(["supported"])
        updates = []
        result = orchestrator.run(
            AgentRequest(query="静息电位如何形成"),
            progress_callback=updates.append,
        )
        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertEqual(
            [event.step for event in updates],
            [event.step for event in result.events],
        )
        self.assertIn("semantic_verified", [event.step for event in updates])

    def test_vague_question_stops_for_clarification(self):
        orchestrator, reasoning, semantic = self._orchestrator(["supported"])
        result = orchestrator.run(AgentRequest(query="生理学"))

        self.assertEqual(result.status, RunStatus.CLARIFICATION_REQUIRED.value)
        self.assertTrue(result.clarification_questions)
        self.assertEqual(reasoning.calls, [])
        self.assertEqual(semantic.calls, [])

    def test_broad_topic_request_stops_for_scope_clarification(self):
        orchestrator, reasoning, semantic = self._orchestrator(["supported"])

        result = orchestrator.run(AgentRequest(query="有关心脏的知识"))

        self.assertEqual(result.status, "clarification_required")
        self.assertIn("范围较大", result.clarification_questions[0])
        self.assertEqual(reasoning.calls, [])
        self.assertEqual(semantic.calls, [])

    def test_specific_topic_question_does_not_trigger_broad_clarification(self):
        plan = QuestionPlanner().plan(
            AgentRequest(query="心脏泵血过程如何受神经调节")
        )

        self.assertEqual(plan.clarification_questions, [])

    def test_model_backed_planner_adds_textbook_synonym_queries(self):
        client = FakeSearchPlanningClient()
        plan = QuestionPlanner(client).plan(
            AgentRequest(
                query="右心衰竭水肿的肾脏体液调节机制是什么"
            )
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(plan.search_queries[0], plan.effective_query)
        self.assertTrue(any("肾素" in query for query in plan.search_queries))
        self.assertLessEqual(len(plan.search_queries), 4)

    def test_model_backed_planner_falls_back_when_provider_fails(self):
        plan = QuestionPlanner(FakeSearchPlanningClient(fail=True)).plan(
            AgentRequest(
                query="请从静脉回流、Starling力和肾脏体液调节解释右心衰竭水肿"
            )
        )

        self.assertGreaterEqual(len(plan.search_queries), 2)
        self.assertTrue(any("静脉回流" in query for query in plan.search_queries))

    def test_image_question_uses_visual_observation(self):
        orchestrator, _, _ = self._orchestrator(["supported"], with_vision=True)
        result = orchestrator.run(
            AgentRequest(image=b"fake-image", image_media_type="image/png")
        )
        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertEqual(result.plan.effective_query, "静息电位如何形成")

    def test_image_question_normalizes_stem_and_options_shape(self):
        orchestrator, _, _ = self._orchestrator(["supported"])
        orchestrator.vision_client = FakeVisionStemClient()
        result = orchestrator.run(
            AgentRequest(image=b"fake-image", image_media_type="image/png")
        )
        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertIn("静息电位", result.plan.effective_query)
        self.assertIn("B. 静息电位主要由K+内流形成", result.plan.effective_query)

    def test_partial_support_triggers_one_revision_then_passes(self):
        orchestrator, reasoning, semantic = self._orchestrator(
            ["partially_supported", "supported"],
            max_revisions=1,
        )
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))

        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertEqual(len(reasoning.calls), 2)
        self.assertEqual(len(semantic.calls), 2)
        self.assertIn("revised", [event.step for event in result.events])

    def test_failed_semantic_verification_stops_after_budget(self):
        orchestrator, reasoning, semantic = self._orchestrator(
            ["insufficient", "insufficient"],
            max_revisions=1,
        )
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))

        self.assertEqual(result.status, RunStatus.REVIEW_REQUIRED.value)
        self.assertEqual(len(reasoning.calls), 2)
        self.assertEqual(len(semantic.calls), 2)
        self.assertIsNone(result.rendered_answer)

    def test_insufficient_answerability_is_withheld_before_semantic_verification(self):
        orchestrator, _, semantic = self._orchestrator(["supported"])
        orchestrator.reasoning_client = InsufficientReasoningClient()
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))
        self.assertEqual(result.status, RunStatus.REVIEW_REQUIRED.value)
        self.assertEqual(semantic.calls, [])
        self.assertIn("证据", result.error)

    def test_partial_answer_is_published_with_explicit_gap(self):
        orchestrator, _, semantic = self._orchestrator(["supported"])
        orchestrator.reasoning_client = PartialReasoningClient()
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))
        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertEqual(result.answer.answerability, "partial")
        self.assertIn("还需要进一步核实", result.rendered_answer)
        self.assertIn("临床处置", result.rendered_answer)
        self.assertEqual(len(semantic.calls), 1)

    def test_invalid_empty_claims_get_one_schema_repair_attempt(self):
        orchestrator, _, semantic = self._orchestrator(["supported"])
        reasoning = EmptyClaimsThenRepairClient()
        orchestrator.reasoning_client = reasoning
        result = orchestrator.run(AgentRequest(query="静息电位如何形成"))
        self.assertEqual(result.status, RunStatus.COMPLETED.value)
        self.assertEqual(len(reasoning.calls), 2)
        self.assertIn("repair_instruction", reasoning.calls[1][1])
        self.assertEqual(len(semantic.calls), 1)


if __name__ == "__main__":
    unittest.main()
