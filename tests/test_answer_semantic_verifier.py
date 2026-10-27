import copy
import unittest

from evidence.answer_contract import (
    AnswerContractError,
    build_answer_generation_request,
    parse_answer_draft,
)
from evidence.evidence_bundle import EvidenceBundle, EvidenceItem
from evidence.semantic_claim_verifier import (
    SemanticClaimVerifier,
    _quote_is_from_source,
    build_semantic_verification_request,
)


class FakeVerifierClient:
    def __init__(self, response):
        self.response = response

    def verify_json(self, request):
        response = copy.deepcopy(self.response)
        existing = {item["claim_id"] for item in response.get("claims", [])}
        evidence = {item["evidence_id"]: item for item in request["evidence"]}
        for claim in request["claims"]:
            if claim["claim_id"] in existing:
                continue
            if claim["claim_id"] == "__question_coverage__":
                response.setdefault("claims", []).append(
                    {
                        "claim_id": claim["claim_id"],
                        "verdict": "supported",
                        "reason": "答案覆盖题目要求。",
                        "supporting_quotes": [],
                        "suggested_revision": None,
                    }
                )
                continue
            evidence_id = claim["evidence_ids"][0]
            response.setdefault("claims", []).append(
                {
                    "claim_id": claim["claim_id"],
                    "verdict": "supported",
                    "reason": "测试中的答案表层核验通过。",
                    "supporting_quotes": [
                        {
                            "evidence_id": evidence_id,
                            "quote": evidence[evidence_id]["evidence_text"][:12],
                        }
                    ],
                    "suggested_revision": None,
                }
            )
        return response


class RepairingVerifierClient(FakeVerifierClient):
    def __init__(self, invalid_response, repaired_response):
        super().__init__(invalid_response)
        self.repaired_response = repaired_response
        self.calls = 0

    def verify_json(self, request):
        self.calls += 1
        if self.calls == 1:
            return super().verify_json(request)
        self.response = self.repaired_response
        return super().verify_json(request)


class AnswerSemanticVerifierTests(unittest.TestCase):
    def setUp(self):
        self.evidence_text = "静息电位主要与钾离子平衡电位和静息状态下的膜通透性有关。"
        self.bundle = EvidenceBundle(
            schema_version="1.0",
            bundle_id="bundle_test",
            query="静息电位如何形成",
            route_mode="structure_first",
            created_at="2026-01-01T00:00:00+00:00",
            evidence=[
                EvidenceItem(
                    evidence_id="ev_1",
                    document_id="doc_1",
                    original_name="生理学教材.pdf",
                    document_type="textbook",
                    pdf_page=10,
                    printed_page=3,
                    locator="教材印刷页 3（PDF第 10 页）",
                    section_title="静息电位",
                    evidence_text=self.evidence_text,
                    span_start=0,
                    span_end=len(self.evidence_text),
                    page_text_sha256="0" * 64,
                    matched_terms=["静息电位"],
                    retrieval_score=10.0,
                    context_pdf_pages=[10],
                )
            ],
        )
        self.answer_data = {
            "schema_version": "1.0",
            "query": self.bundle.query,
            "mode": "guided",
            "direct_answer": "静息电位与离子平衡电位及膜通透性有关。",
            "claims": [
                {
                    "claim_id": "claim_1",
                    "text": "静息电位与钾离子平衡电位和膜通透性有关。",
                    "category": "mechanism",
                    "strength": "causal",
                    "evidence_ids": ["ev_1"],
                    "uncertainty": None,
                }
            ],
            "explanation": "根据教材证据作答。",
            "teaching_questions": [],
            "research_extension": [],
            "limitations": [],
        }

    def test_answer_contract_rejects_uncited_claim(self):
        self.answer_data["claims"][0]["evidence_ids"] = []
        with self.assertRaises(AnswerContractError):
            parse_answer_draft(self.answer_data, self.bundle)

    def test_answer_contract_rejects_unknown_evidence(self):
        self.answer_data["claims"][0]["evidence_ids"] = ["ev_missing"]
        with self.assertRaises(AnswerContractError):
            parse_answer_draft(self.answer_data, self.bundle)

    def test_insufficient_answer_may_have_no_medical_claims(self):
        self.answer_data["answerability"] = "insufficient"
        self.answer_data["claims"] = []
        answer = parse_answer_draft(self.answer_data, self.bundle)
        self.assertEqual(answer.answerability, "insufficient")
        self.assertEqual(answer.claims, [])

    def test_publishable_answer_still_requires_at_least_one_claim(self):
        self.answer_data["answerability"] = "answer"
        self.answer_data["claims"] = []
        with self.assertRaisesRegex(AnswerContractError, "可发布答案"):
            parse_answer_draft(self.answer_data, self.bundle)

    def test_partial_answer_requires_supported_claims_and_unanswered_aspects(self):
        self.answer_data["answerability"] = "partial"
        self.answer_data["unanswered_aspects"] = ["临床处置原则缺少指南证据。"]
        answer = parse_answer_draft(self.answer_data, self.bundle)
        self.assertEqual(answer.answerability, "partial")
        self.assertEqual(len(answer.claims), 1)
        self.assertIn("临床处置", answer.unanswered_aspects[0])

    def test_partial_answer_without_gap_list_is_rejected(self):
        self.answer_data["answerability"] = "partial"
        with self.assertRaisesRegex(AnswerContractError, "尚缺证据"):
            parse_answer_draft(self.answer_data, self.bundle)

    def test_complete_answer_cannot_claim_unanswered_aspects(self):
        self.answer_data["answerability"] = "answer"
        self.answer_data["unanswered_aspects"] = ["仍有核心问题未回答。"]
        with self.assertRaisesRegex(AnswerContractError, "不能同时声明未回答"):
            parse_answer_draft(self.answer_data, self.bundle)

    def test_generation_request_carries_coverage_requirements_and_mode_policy(self):
        request = build_answer_generation_request(
            self.bundle,
            mode="quick",
            answer_requirements=["解释K+平衡电位", "说明兴奋性变化"],
        )

        self.assertEqual(
            request["task"]["answer_requirements"],
            ["解释K+平衡电位", "说明兴奋性变化"],
        )
        self.assertIn("teaching_questions", request["task"]["response_policy"])
        self.assertIn("逐项覆盖", request["system_instruction"])

    def test_reexplain_request_adds_friendly_plain_language_style(self):
        request = build_answer_generation_request(
            self.bundle,
            mode="guided",
            learning_intent="reexplain",
        )

        style = request["task"]["teaching_style"]
        self.assertIn("学习伙伴", style)
        self.assertIn("常用词和短句", style)
        self.assertIn("直观类比", style)
        self.assertIn("不能作为新增医学事实", request["system_instruction"])

    def test_example_followup_does_not_require_original_choice_answer(self):
        self.bundle.query = "血清清蛋白在何种pH下带正电？A.4.0 B.4.7 C.5.0"
        request = build_answer_generation_request(
            self.bundle, mode="guided", learning_intent="example",
            answer_requirements=["逐项判断 A", "逐项判断 B", "逐项判断 C"],
        )
        self.assertEqual(request["task"]["answer_requirements"], [])
        self.assertEqual(request["task"]["choice_options"], [])
        self.assertIn("只给一个新而具体的例子", request["task"]["response_policy"])
        self.assertIn("direct_answer只写一个具体的新情境", request["system_instruction"])

        self.answer_data["query"] = self.bundle.query
        answer = parse_answer_draft(self.answer_data, self.bundle)
        verification = build_semantic_verification_request(
            answer, self.bundle, learning_intent="example",
        )
        self.assertEqual(verification["learning_intent"], "example")
        self.assertIn("原题选项只是背景", verification["system_instruction"])

    def test_practice_followup_asks_new_question_without_original_choice_coverage(self):
        self.bundle.query = "清蛋白何时带正电？A. pH4.0 B. pH4.7 C. pH5.0"
        request = build_answer_generation_request(
            self.bundle, mode="guided", learning_intent="practice",
            answer_requirements=["逐项判断 A", "逐项判断 B"],
        )
        self.assertEqual(request["task"]["answer_requirements"], [])
        self.assertEqual(request["task"]["choice_options"], [])
        self.assertIn("direct_answer只写题目", request["task"]["response_policy"])
        self.assertIn("答案不得出现在direct_answer", request["system_instruction"])

        self.answer_data["query"] = self.bundle.query
        answer = parse_answer_draft(self.answer_data, self.bundle)
        verification = build_semantic_verification_request(
            answer, self.bundle, learning_intent="practice",
        )
        self.assertIn("答案不应提前透露", verification["system_instruction"])

    def test_multiple_choice_request_exposes_every_option(self):
        self.bundle.query = (
            "参与脂肪酸β-氧化的有： A. FAD B. NAD+ C. NADP+ D. CoA-SH"
        )
        request = build_answer_generation_request(
            self.bundle,
            mode="quick",
            answer_requirements=[
                "逐项判断 A. FAD",
                "逐项判断 B. NAD+",
                "逐项判断 C. NADP+",
                "逐项判断 D. CoA-SH",
            ],
        )

        self.assertEqual(
            [item["label"] for item in request["task"]["choice_options"]],
            ["A", "B", "C", "D"],
        )
        self.assertIn("不得只发布其中一个", request["system_instruction"])

    def test_atom_origin_question_requires_atom_fate_not_reagent_participation(self):
        self.bundle.query = (
            "嘌呤环中的碳原子来自于（ ） "
            "A. CO2 B. 甘氨酸 C. 天冬氨酸 D. 谷氨酰胺 E. 一碳单位"
        )
        generation = build_answer_generation_request(self.bundle, mode="quick")
        self.answer_data["query"] = self.bundle.query
        answer = parse_answer_draft(self.answer_data, self.bundle)
        verification = build_semantic_verification_request(answer, self.bundle)

        self.assertEqual(len(generation["task"]["choice_options"]), 5)
        self.assertIn("反应物参与合成与其原子最终留在目标结构", generation["system_instruction"])
        self.assertIn("不能支持该物质提供目标结构的碳或氮", verification["system_instruction"])

    def test_challenge_context_is_not_treated_as_evidence(self):
        request = build_answer_generation_request(
            self.bundle,
            mode="quick",
            challenge_review={
                "previous_answer": "旧答案是 A、C",
                "user_feedback": "应该选 A、B",
                "previous_evidence_locations": ["教材 PDF 第 5 页"],
            },
        )

        self.assertEqual(request["task"]["challenge_review"]["user_feedback"], "应该选 A、B")
        self.assertIn("旧答案、用户反馈和旧引用", request["system_instruction"])
        self.assertEqual(len(request["task"]["evidence"]), len(self.bundle.evidence))

    def test_semantic_coverage_gate_rejects_single_option_partial_answer(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        request = build_semantic_verification_request(answer, self.bundle)

        self.assertIn(
            "只确认其中一项必须判partially_supported",
            request["system_instruction"],
        )

    def test_supported_verdict_requires_exact_source_quote(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "supported",
                        "reason": "教材原文直接支持该主张。",
                        "supporting_quotes": [
                            {
                                "evidence_id": "ev_1",
                                "quote": "与钾离子平衡电位和静息状态下的膜通透性有关",
                            }
                        ],
                        "suggested_revision": None,
                    }
                ]
            }
        )
        report = SemanticClaimVerifier(client).verify(answer, self.bundle)
        self.assertEqual(report.status, "pass")

    def test_fabricated_supporting_quote_fails(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "supported",
                        "reason": "声称支持。",
                        "supporting_quotes": [
                            {"evidence_id": "ev_1", "quote": "原文中不存在的句子"}
                        ],
                    }
                ]
            }
        )
        report = SemanticClaimVerifier(client).verify(answer, self.bundle)
        self.assertEqual(report.status, "fail")
        self.assertIn("不是证据原文", report.errors[0])

    def test_partial_support_requires_review(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "partially_supported",
                        "reason": "证据只支持部分表述。",
                        "supporting_quotes": [],
                        "suggested_revision": "降低因果表述强度。",
                    }
                ]
            }
        )
        report = SemanticClaimVerifier(client).verify(answer, self.bundle)
        self.assertEqual(report.status, "review")

    def test_contradicted_claim_fails(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "contradicted",
                        "reason": "证据与主张冲突。",
                        "supporting_quotes": [],
                    }
                ]
            }
        )
        report = SemanticClaimVerifier(client).verify(answer, self.bundle)
        self.assertEqual(report.status, "fail")

    def test_question_coverage_failure_requires_review(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient({
            "claims": [{
                "claim_id": "__question_coverage__",
                "verdict": "partially_supported",
                "reason": "答案只讨论膜通透性，遗漏离子扩散方向。",
                "supporting_quotes": [],
                "suggested_revision": "补充K+外流机制。",
            }]
        })

        report = SemanticClaimVerifier(client).verify(answer, self.bundle)

        self.assertEqual(report.status, "review")

    def test_quote_matching_ignores_ocr_whitespace_and_private_use_bullets(self):
        source = "静息电位主要是：\uf0aa K+ 外流形成"
        self.assertTrue(_quote_is_from_source("静息电位主要是：K+外流形成", source))

    def test_verifier_span_id_uses_immutable_source_text(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "supported",
                        "reason": "编号片段直接支持主张。",
                        "supporting_quotes": [
                            {
                                "evidence_id": "ev_1",
                                "span_id": "ev_1_s1",
                                "quote": "这段由模型改写，不能作为逐字引文",
                            }
                        ],
                    }
                ]
            }
        )

        report = SemanticClaimVerifier(client).verify(answer, self.bundle)

        self.assertEqual(report.status, "pass")
        self.assertEqual(
            report.claims[0].supporting_quotes[0].quote,
            self.evidence_text,
        )

    def test_verifier_can_realign_claim_to_other_evidence_in_same_bundle(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        second = copy.deepcopy(self.bundle.evidence[0])
        second.evidence_id = "ev_2"
        second.evidence_text = "另一页教材明确说明静息电位与膜通透性有关。"
        self.bundle.evidence.append(second)
        client = FakeVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "supported",
                        "reason": "证据包内另一页直接支持。",
                        "supporting_quotes": [
                            {
                                "evidence_id": "ev_2",
                                "quote": "静息电位与膜通透性有关",
                            }
                        ],
                    }
                ]
            }
        )

        report = SemanticClaimVerifier(client).verify(answer, self.bundle)

        self.assertEqual(report.status, "pass")

    def test_invalid_quote_is_retried_once_with_machine_error(self):
        answer = parse_answer_draft(self.answer_data, self.bundle)
        repaired = {
            "claims": [
                {
                    "claim_id": "claim_1",
                    "verdict": "supported",
                    "reason": "教材原文直接支持。",
                    "supporting_quotes": [
                        {
                            "evidence_id": "ev_1",
                            "quote": "钾离子平衡电位和静息状态下的膜通透性有关",
                        }
                    ],
                }
            ]
        }
        client = RepairingVerifierClient(
            {
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "verdict": "supported",
                        "reason": "错误引文。",
                        "supporting_quotes": [
                            {"evidence_id": "ev_1", "quote": "不存在的原文"}
                        ],
                    }
                ]
            },
            repaired,
        )

        report = SemanticClaimVerifier(client).verify(answer, self.bundle)

        self.assertEqual(report.status, "pass")
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
