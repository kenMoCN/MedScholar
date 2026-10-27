import unittest

from core.learning_companion import (
    LearningIntent,
    analyze_learning_turn,
    learning_action_options,
    learning_turn_from_action,
)


class LearningCompanionTests(unittest.TestCase):
    def test_reexplanation_inherits_previous_topic_and_changes_strategy(self):
        turn = analyze_learning_turn(
            "我还是没听懂",
            [
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "首先解释钾离子外流。"},
            ],
        )

        self.assertEqual(turn.intent, LearningIntent.REEXPLAIN)
        self.assertEqual(turn.topic_query, "动作电位为什么会复极化？")
        self.assertTrue(turn.inherited_context)
        self.assertIn("耐心、亲切的学习伙伴", turn.effective_query)
        self.assertIn("上一版解释摘要", turn.effective_query)

    def test_standalone_question_does_not_inherit_old_topic(self):
        turn = analyze_learning_turn(
            "为什么低钾血症会导致乏力？",
            [{"role": "user", "body": "动作电位如何形成？"}],
        )

        self.assertEqual(turn.intent, LearningIntent.QUESTION)
        self.assertFalse(turn.inherited_context)
        self.assertEqual(turn.topic_query, "为什么低钾血症会导致乏力？")

    def test_short_referential_question_uses_previous_ocr_text(self):
        original = "第51题：维生素B12吸收障碍的原因？ A 胃大部切除 B 回肠切除"
        turn = analyze_learning_turn(
            "那第51题为什么选B？",
            [
                {"role": "user", "body": "[图片题目] 附件：question.jpg"},
                {"role": "assistant", "body": "上一轮认为选A。", "question_context": original},
            ],
        )
        self.assertTrue(turn.inherited_context)
        self.assertEqual(turn.topic_query, original)
        self.assertIn("本轮追问：那第51题为什么选B？", turn.effective_query)
        self.assertIn("上一轮回答摘要：上一轮认为选A。", turn.effective_query)

    def test_followup_preserves_recent_correction_and_last_answer(self):
        turn = analyze_learning_turn(
            "那第52题呢？",
            [
                {"role": "assistant", "body": "第51题原回答", "question_context": "第51题和第52题的原题文本"},
                {"role": "user", "body": "第51题是多选题"},
                {"role": "assistant", "body": "已按多选重新核对第51题", "question_context": "第51题和第52题的原题文本"},
            ],
        )
        self.assertTrue(turn.inherited_context)
        self.assertIn("用户：第51题是多选题", turn.effective_query)
        self.assertIn("学习伙伴：已按多选重新核对第51题", turn.effective_query)

    def test_action_reexplanation_receives_previous_explanation(self):
        turn = learning_turn_from_action(
            "reexplain",
            "动作电位为什么会复极化？",
            display_prompt="换一种讲法",
            conversation_context=[
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "复极化主要与钾离子外流有关。"},
            ],
        )
        self.assertIn("复极化主要与钾离子外流有关", turn.effective_query)
        self.assertIn("不是医学证据", turn.effective_query)

    def test_short_new_question_without_reference_stays_independent(self):
        turn = analyze_learning_turn(
            "什么是心输出量？",
            [{"role": "assistant", "body": "旧回答", "question_context": "动作电位如何形成？"}],
        )
        self.assertFalse(turn.inherited_context)
        self.assertEqual(turn.topic_query, "什么是心输出量？")

    def test_video_request_inherits_topic(self):
        turn = analyze_learning_turn(
            "有没有适合看的B站视频？",
            [{"role": "user", "body": "讲讲心动周期"}],
        )

        self.assertEqual(turn.intent, LearningIntent.VIDEO)
        self.assertEqual(turn.topic_query, "讲讲心动周期")

    def test_action_only_followups_keep_original_knowledge_topic(self):
        turn = analyze_learning_turn(
            "找视频",
            [
                {"role": "user", "body": "动作电位为什么会复极化？"},
                {"role": "assistant", "body": "与钾离子外流有关。"},
                {"role": "user", "body": "举个例子"},
                {"role": "assistant", "body": "可以把它类比为恢复电位。"},
            ],
        )
        self.assertEqual(turn.intent, LearningIntent.VIDEO)
        self.assertEqual(turn.topic_query, "动作电位为什么会复极化？")

    def test_explicit_topic_in_followup_switches_topic(self):
        turn = analyze_learning_turn(
            "找糖酵解的视频",
            [{"role": "user", "body": "动作电位如何形成？"}],
            current_topic="动作电位如何形成？",
        )
        self.assertEqual(turn.intent, LearningIntent.VIDEO)
        self.assertEqual(turn.topic_query, "糖酵解")
        self.assertFalse(turn.inherited_context)

    def test_negative_feedback_beats_embedded_understood_phrase(self):
        turn = analyze_learning_turn(
            "我以为懂了，但还是不懂",
            [{"role": "user", "body": "动作电位如何形成？"}],
        )
        self.assertEqual(turn.intent, LearningIntent.REEXPLAIN)

    def test_practice_request_beats_understood_phrase(self):
        turn = analyze_learning_turn(
            "我明白了，请再出一道练习题",
            [{"role": "user", "body": "动作电位如何形成？"}],
        )
        self.assertEqual(turn.intent, LearningIntent.PRACTICE)

    def test_course_location_request_inherits_topic(self):
        turn = analyze_learning_turn(
            "带我回到课件看看",
            [{"role": "user", "body": "讲讲心动周期"}],
        )

        self.assertEqual(turn.intent, LearningIntent.COURSE)
        self.assertEqual(turn.topic_query, "讲讲心动周期")

    def test_completed_turn_exposes_learning_actions(self):
        actions = learning_action_options("question", completed=True)
        self.assertIn("找视频", [item["label"] for item in actions])
        self.assertIn("找论文拓展", [item["label"] for item in actions])

    def test_unfinished_turn_does_not_push_extra_actions(self):
        self.assertEqual(
            learning_action_options("question", completed=False),
            [],
        )

    def test_action_followup_only_offers_reexplanation(self):
        actions = learning_action_options("video", completed=True, followup=True)
        self.assertEqual([item["id"] for item in actions], ["reexplain"])

    def test_structured_actions_keep_topic_without_parsing_button_copy(self):
        topic = "动作电位为什么会复极化？"
        for action in learning_action_options("question", completed=True):
            with self.subTest(action=action["id"]):
                turn = learning_turn_from_action(
                    action["id"],
                    topic,
                    display_prompt=action["prompt"],
                )
                self.assertEqual(turn.intent.value, action["id"])
                self.assertEqual(turn.topic_query, topic)
                self.assertTrue(turn.inherited_context)

    def test_practice_action_requests_new_question_without_early_answer(self):
        turn = learning_turn_from_action(
            "practice", "清蛋白在什么pH下带正电？A.4.0 B.4.7 C.5.0",
            display_prompt="出题检验",
        )
        self.assertIn("一道新的", turn.effective_query)
        self.assertIn("不要重答原题", turn.effective_query)
        self.assertIn("等用户作答后再核对", turn.effective_query)
        self.assertEqual(
            learning_action_options("practice", completed=True, followup=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
