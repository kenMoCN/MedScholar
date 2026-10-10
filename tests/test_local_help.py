"""Local identity and greeting copy stays warm without hijacking study questions."""

import unittest

from local_help import local_help_response


class LocalHelpTests(unittest.TestCase):
    def test_greeting_invites_a_concrete_learning_start(self):
        response = local_help_response("你好")
        self.assertEqual(response.intent, "greeting")
        self.assertIn("MedScholar", response.text)
        self.assertIn("课件或截图", response.text)
        self.assertIn("今天想从哪里开始", response.text)

    def test_identity_explains_companionship_and_evidence_boundary(self):
        response = local_help_response("你是谁？")
        self.assertEqual(response.intent, "identity")
        self.assertIn("智能伙伴", response.text)
        self.assertIn("不确定之处", response.text)

    def test_study_question_still_uses_normal_route(self):
        self.assertIsNone(local_help_response("你好，为什么动作电位会复极化？"))


if __name__ == "__main__":
    unittest.main()
