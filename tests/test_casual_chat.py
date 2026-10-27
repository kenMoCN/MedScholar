import unittest

from core.casual_chat import CasualConversationEngine, is_casual_chat_candidate


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete_json(self, instruction, payload):
        self.calls.append((instruction, payload))
        return self.payload


class CasualChatTests(unittest.TestCase):
    def test_clear_social_cue_is_candidate(self):
        self.assertTrue(is_casual_chat_candidate("我今天有点累，陪我聊聊天吧"))

    def test_medical_or_academic_content_is_never_casual_candidate(self):
        self.assertFalse(is_casual_chat_candidate("我对动作电位机制很难过"))
        self.assertFalse(is_casual_chat_candidate("这道考试题让我不开心"))
        self.assertFalse(is_casual_chat_candidate("陪我聊聊这篇论文"))

    def test_image_never_enters_casual_route(self):
        self.assertFalse(is_casual_chat_candidate("讲个笑话", has_image=True))

    def test_engine_returns_casual_response(self):
        client = FakeClient({"route": "casual", "response": "当然，可以聊聊。"})
        self.assertEqual(
            CasualConversationEngine(client).answer("陪我聊聊"),
            "当然，可以聊聊。",
        )

    def test_casual_reply_receives_bounded_recent_dialogue(self):
        client = FakeClient({"route": "casual", "response": "听起来今天挺累的。"})
        CasualConversationEngine(client).answer(
            "陪我聊聊",
            conversation_context=[
                {"role": "user", "body": "我今天上完课很累。"},
                {"role": "assistant", "body": "辛苦了，先休息一下。"},
            ],
        )
        self.assertEqual(
            client.calls[0][1]["recent_conversation"][0]["body"],
            "我今天上完课很累。",
        )

    def test_engine_can_escalate_ambiguous_query_to_strict_route(self):
        client = FakeClient({"route": "strict", "response": "不应展示"})
        self.assertIsNone(CasualConversationEngine(client).answer("陪我聊聊"))


if __name__ == "__main__":
    unittest.main()
