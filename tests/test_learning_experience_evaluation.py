import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation import EvaluationCase
from learning_experience_evaluation import run_blind_comparison


class FakeService:
    def __init__(self):
        self.tokens = 0

    def ask(self, question, **kwargs):
        self.tokens += 30
        return SimpleNamespace(
            rendered_answer=f"智能体：{question}", error=None, status="completed"
        )

    def baseline_answer(self, question):
        self.tokens += 10
        return f"直接模型：{question}"

    def usage_snapshot(self):
        return {
            "prompt_tokens": self.tokens,
            "completion_tokens": 0,
            "total_tokens": self.tokens,
            "requests": self.tokens // 10,
        }


class LearningExperienceEvaluationTests(unittest.TestCase):
    def test_outputs_blind_review_and_separate_answer_key(self):
        with tempfile.TemporaryDirectory() as temp:
            output = run_blind_comparison(
                FakeService(),
                [EvaluationCase(case_id="q1", question="什么是辅酶？")],
                temp,
            )
            review = json.loads(output.review_path.read_text(encoding="utf-8"))
            key = json.loads(output.answer_key_path.read_text(encoding="utf-8"))
            self.assertNotIn("agent", json.dumps(review, ensure_ascii=False))
            self.assertEqual(key["answer_key"][0]["case_id"], "q1")
            self.assertIn("learning_helpfulness", review["criteria"])
            self.assertEqual(key["answer_key"][0]["agent_usage"]["total_tokens"], 30)
            self.assertEqual(key["answer_key"][0]["direct_model_usage"]["total_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
