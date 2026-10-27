import unittest

from evidence.general_knowledge import (
    GeneralKnowledgeAnswerEngine,
    is_low_risk_foundational_question,
)


class FakeGenerator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        return self.responses.pop(0)


class FakeVerifier:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def verify_json(self, request):
        self.calls.append(request)
        return self.responses.pop(0)


def draft(answer="C"):
    return {
        "answerable": True,
        "final_answer": f"答案是 {answer}。",
        "explanation": "酶蛋白主要决定底物专一性。",
        "option_analysis": [
            {"option": answer, "verdict": "错误", "reason": "该表述颠倒了决定因素。"}
        ],
        "question_quality": "valid",
        "confidence": "high",
        "uncertainties": [],
    }


class GeneralKnowledgeTests(unittest.TestCase):
    def test_foundational_route_excludes_clinical_decisions(self):
        self.assertTrue(is_low_risk_foundational_question(
            "[单选题] 关于辅助因子，下列错误的是", "course_fact"
        ))
        self.assertTrue(is_low_risk_foundational_question(
            "糖酵解的限速酶是什么", "terminology"
        ))
        self.assertFalse(is_low_risk_foundational_question(
            "患者低血糖如何处理", "clinical"
        ))
        self.assertFalse(is_low_risk_foundational_question(
            "某患者应该如何用药", "course_fact"
        ))

    def test_verified_general_answer_is_transparently_labeled(self):
        engine = GeneralKnowledgeAnswerEngine(
            FakeGenerator([draft()]),
            FakeVerifier([{"verdict": "pass", "issues": []}]),
        )
        result = engine.answer("辅助因子选择题", mode="quick")
        self.assertEqual(result.status, "pass")
        self.assertIn("未与课程资料核对", result.rendered_answer)
        self.assertIn("答案是 C", result.rendered_answer)
        self.assertIn("逐项判断", result.rendered_answer)

    def test_example_followup_gives_one_example_without_reanswering_options(self):
        generator = FakeGenerator([{
            "answerable": True,
            "example": "把清蛋白想成海绵：酸性环境中它更容易结合氢离子，整体带正电。",
            "takeaway": "pH低于等电点时，蛋白质净带正电。",
            "confidence": "high",
            "uncertainties": [],
        }])
        verifier = FakeVerifier([{"verdict": "pass", "issues": []}])
        result = GeneralKnowledgeAnswerEngine(generator, verifier).answer(
            "血清清蛋白的等电点为4.7，哪个pH带正电？A.4.0 B.4.7 C.5.0",
            mode="guided", learning_intent="example",
        )
        self.assertEqual(result.status, "pass")
        self.assertIn("海绵", result.rendered_answer)
        self.assertIn("对应原理", result.rendered_answer)
        self.assertNotIn("逐项判断", result.rendered_answer)
        self.assertNotIn("A.4.0", result.rendered_answer)
        self.assertNotIn("option_analysis", generator.calls[0][1]["output_schema"])

    def test_practice_followup_shows_new_question_without_answer(self):
        generator = FakeGenerator([{
            "question": "若某蛋白质处于低于其等电点的溶液中，它的净电荷是什么？",
            "expected_answer": "正电荷",
            "rationale": "低于等电点时蛋白质更容易结合质子。",
            "confidence": "high",
        }])
        verifier = FakeVerifier([{"verdict": "pass", "issues": []}])
        result = GeneralKnowledgeAnswerEngine(generator, verifier).answer(
            "清蛋白pI为4.7，A. pH4.0 B. pH4.7 C. pH5.0",
            mode="guided", learning_intent="practice",
        )
        self.assertEqual(result.status, "pass")
        self.assertIn("若某蛋白质", result.rendered_answer)
        self.assertNotIn("正电荷", result.rendered_answer)
        self.assertNotIn("逐项判断", result.rendered_answer)
        self.assertNotIn("option_analysis", generator.calls[0][1]["output_schema"])

    def test_review_triggers_one_revision_and_second_check(self):
        generator = FakeGenerator([draft("B"), draft("C")])
        verifier = FakeVerifier([
            {"verdict": "review", "issues": ["题干要求错误项，应选C"]},
            {"verdict": "pass", "issues": []},
        ])
        result = GeneralKnowledgeAnswerEngine(generator, verifier).answer(
            "辅助因子选择题", mode="quick"
        )
        self.assertEqual(result.status, "pass")
        self.assertIn("答案是 C", result.rendered_answer)
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(verifier.calls), 2)

    def test_invalid_first_draft_gets_one_schema_repair(self):
        generator = FakeGenerator([
            {"answerable": True, "final_answer": "答案是C"},
            draft("C"),
        ])
        verifier = FakeVerifier([{"verdict": "pass", "issues": []}])
        result = GeneralKnowledgeAnswerEngine(generator, verifier).answer(
            "辅助因子选择题", mode="quick"
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual(len(generator.calls), 2)

    def test_failed_check_with_issues_gets_one_revision(self):
        generator = FakeGenerator([draft("B"), draft("C")])
        verifier = FakeVerifier([
            {"verdict": "fail", "issues": ["结论与逐项分析不一致"]},
            {"verdict": "pass", "issues": []},
        ])
        result = GeneralKnowledgeAnswerEngine(generator, verifier).answer(
            "辅助因子选择题", mode="quick"
        )
        self.assertEqual(result.status, "pass")
        self.assertIn("答案是 C", result.rendered_answer)

    def test_failed_consistency_check_keeps_answer_hidden(self):
        result = GeneralKnowledgeAnswerEngine(
            FakeGenerator([draft()]),
            FakeVerifier([{"verdict": "fail", "issues": ["事实错误"]}]),
        ).answer("辅助因子选择题", mode="quick")
        self.assertEqual(result.status, "review")
        self.assertIsNone(result.rendered_answer)

    def test_ambiguous_single_choice_can_publish_when_reviewer_confirms(self):
        ambiguous = draft("B、C")
        ambiguous["question_quality"] = "ambiguous"
        result = GeneralKnowledgeAnswerEngine(
            FakeGenerator([ambiguous]),
            FakeVerifier([{"verdict": "pass", "issues": []}]),
        ).answer("[单选题] 下列错误的是", mode="quick")
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.question_quality, "ambiguous")
        self.assertIn("题目存在矛盾", result.rendered_answer)
        self.assertIn("不是因为没有足够证据", result.rendered_answer)


if __name__ == "__main__":
    unittest.main()
