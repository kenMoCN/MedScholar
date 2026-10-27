import unittest

from evidence.external_answer_engine import ExternalAnswerEngine
from evidence.official_evidence import OfficialEvidenceItem


class FakeGenerator:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


class FakeVerifier:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def verify_json(self, request):
        self.calls.append(request)
        return self.response


def evidence():
    return OfficialEvidenceItem(
        evidence_id="ext_ev_1",
        source="who",
        resource_id="WHO/1",
        title="WHO shock guideline",
        url="https://who.test/1",
        locator="全文字符 100-180",
        evidence_text="Clinicians should reassess perfusion after initial resuscitation.",
        span_start=100,
        span_end=165,
        full_text_sha256="a" * 64,
        matched_terms=("shock", "resuscitation"),
        recommendation_markers=("should",),
        retrieval_score=6.0,
    )


def answer_payload(answerability="answer"):
    return {
        "schema_version": "1.0",
        "query": "休克处理原则",
        "mode": "quick",
        "answerability": answerability,
        "direct_answer": "指南建议初始复苏后重新评估灌注。",
        "claims": [{
            "claim_id": "claim_1",
            "text": "初始复苏后应重新评估灌注。",
            "category": "clinical_guidance",
            "strength": "recommendation",
            "evidence_ids": ["ext_ev_1"],
            "uncertainty": None,
        }],
        "explanation": "该表述仅概括指南中的一般原则。",
        "teaching_questions": [],
        "research_extension": [],
        "limitations": ["未覆盖具体患者的用药与剂量。"],
    }


def verification_payload(quote=None):
    exact = quote or "should reassess perfusion"
    return {"claims": [
        {
            "claim_id": "claim_1", "verdict": "supported", "reason": "direct",
            "supporting_quotes": [{"evidence_id": "ext_ev_1", "quote": exact}],
            "suggested_revision": None,
        },
        {
            "claim_id": "__direct_answer__", "verdict": "supported", "reason": "direct",
            "supporting_quotes": [{"evidence_id": "ext_ev_1", "quote": exact}],
            "suggested_revision": None,
        },
        {
            "claim_id": "__explanation__", "verdict": "supported", "reason": "direct",
            "supporting_quotes": [{"evidence_id": "ext_ev_1", "quote": exact}],
            "suggested_revision": None,
        },
        {
            "claim_id": "__question_coverage__", "verdict": "supported",
            "reason": "answers the question", "supporting_quotes": [],
            "suggested_revision": None,
        },
    ]}


class ExternalAnswerEngineTests(unittest.TestCase):
    def test_invalid_answer_json_gets_one_constrained_repair(self):
        invalid = answer_payload()
        invalid["claims"] = []
        generator = FakeGenerator([invalid, answer_payload()])
        result = ExternalAnswerEngine(
            generator, FakeVerifier(verification_payload())
        ).answer("休克处理原则", mode="quick", evidence=[evidence()])
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(generator.calls), 2)
        self.assertIn("repair_instruction", generator.calls[1][1])

    def test_answer_is_published_only_after_independent_exact_quote_verification(self):
        generator = FakeGenerator(answer_payload())
        verifier = FakeVerifier(verification_payload())
        result = ExternalAnswerEngine(generator, verifier).answer(
            "休克处理原则", mode="quick", evidence=[evidence()]
        )
        self.assertEqual(result.status, "pass")
        self.assertIn("官方指南答复", result.rendered_answer)
        self.assertIn("非课程教材证据", result.rendered_answer)
        self.assertIn("WHO shock guideline", result.rendered_answer)
        self.assertIn("不得给出证据中没有的药名", generator.calls[0][0])
        self.assertIn("clinical_guidance/recommendation", generator.calls[0][0])

    def test_fabricated_verifier_quote_keeps_answer_blocked(self):
        verifier = FakeVerifier(verification_payload("fabricated dose recommendation"))
        result = ExternalAnswerEngine(
            FakeGenerator(answer_payload()), verifier
        ).answer("休克处理原则", mode="quick", evidence=[evidence()])
        self.assertEqual(result.status, "fail")
        self.assertIsNone(result.rendered_answer)
        self.assertEqual(len(verifier.calls), 2)

    def test_generator_can_explicitly_decline_when_evidence_is_insufficient(self):
        result = ExternalAnswerEngine(
            FakeGenerator(answer_payload("insufficient")),
            FakeVerifier(verification_payload()),
        ).answer("休克处理原则", mode="quick", evidence=[evidence()])
        self.assertEqual(result.status, "insufficient")
        self.assertIsNone(result.rendered_answer)

    def test_verified_partial_external_answer_is_published_with_gap(self):
        payload = answer_payload("partial")
        payload["unanswered_aspects"] = ["未覆盖个体化药物调整。"]
        result = ExternalAnswerEngine(
            FakeGenerator(payload), FakeVerifier(verification_payload())
        ).answer("休克处理原则", mode="quick", evidence=[evidence()])
        self.assertEqual(result.status, "pass")
        self.assertIn("这份资料尚未覆盖", result.rendered_answer)
        self.assertIn("个体化药物调整", result.rendered_answer)


if __name__ == "__main__":
    unittest.main()
