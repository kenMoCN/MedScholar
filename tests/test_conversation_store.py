import json
import tempfile
import unittest
from pathlib import Path

from conversation_store import ConversationStore, conversation_context_for_session


class ConversationStoreTests(unittest.TestCase):
    def test_text_messages_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.json"
            store = ConversationStore(path)
            session = store.create_session()
            store.append_message(
                session.session_id,
                role="user",
                title="你",
                body="静息电位如何形成？",
                tone="user",
            )
            store.append_message(
                session.session_id,
                role="assistant",
                title="课程助手",
                body="测试答案",
                tone="answer",
                resources=[{"title": "课程资料", "url": "https://example.test"}],
                question_context="识别出的图片题干：A、B、C、D",
            )

            loaded = ConversationStore(path)
            restored = loaded.get_session(session.session_id)
            self.assertEqual(restored.title, "静息电位如何形成？")
            self.assertEqual(len(restored.messages), 2)
            self.assertEqual(restored.messages[1].body, "测试答案")
            self.assertEqual(restored.messages[1].resources[0]["title"], "课程资料")
            self.assertEqual(restored.messages[1].question_context, "识别出的图片题干：A、B、C、D")

    def test_history_contract_excludes_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.json"
            store = ConversationStore(path)
            store.create_session()
            data = json.loads(path.read_text(encoding="utf-8"))

            serialized = json.dumps(data, ensure_ascii=False)
            self.assertIn("local_session_images_no_secrets", serialized)
            self.assertNotIn("api_key", serialized.lower())
            self.assertNotIn("image_path", serialized.lower())

    def test_context_keeps_question_anchor_outside_recent_window(self):
        store = ConversationStore()
        session = store.create_session()
        store.append_message(
            session.session_id,
            role="assistant", title="课程回答", body="原题回答", tone="answer",
            question_context="第51题：维生素B12吸收",
        )
        for index in range(12):
            store.append_message(
                session.session_id,
                role="user" if index % 2 == 0 else "assistant",
                title="追问", body=f"后续消息 {index}", tone="answer",
            )
        context = conversation_context_for_session(session)
        self.assertEqual(len(context), 11)
        self.assertEqual(context[0]["question_context"], "第51题：维生素B12吸收")
        self.assertEqual(context[-1]["body"], "后续消息 11")

    def test_invalid_role_is_rejected(self):
        store = ConversationStore()
        session = store.create_session()
        with self.assertRaises(ValueError):
            store.append_message(
                session.session_id,
                role="tool",
                title="tool",
                body="hidden",
                tone="neutral",
            )

    def test_session_can_be_deleted_and_remains_deleted_after_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history.json"
            store = ConversationStore(path)
            kept = store.create_session("保留")
            deleted = store.create_session("删除")

            removed = store.delete_session(deleted.session_id)

            self.assertEqual(removed.session_id, deleted.session_id)
            self.assertEqual(
                [item.session_id for item in store.list_sessions()],
                [kept.session_id],
            )
            loaded = ConversationStore(path)
            self.assertEqual(
                [item.session_id for item in loaded.list_sessions()],
                [kept.session_id],
            )

    def test_image_is_retained_locally_and_deleted_with_its_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "question.png"
            source.write_bytes(b"image-bytes")
            store = ConversationStore(root / "history.json")
            session = store.create_session()
            attachment = store.save_image(session.session_id, source)
            retained = store.resolve_image(session.session_id, attachment)
            self.assertIsNotNone(retained)
            self.assertEqual(retained.read_bytes(), b"image-bytes")
            self.assertNotEqual(retained, source)
            store.append_message(
                session.session_id, role="user", title="你", body="[图片题目]",
                tone="user", attachments=[attachment],
            )
            store.append_message(
                session.session_id, role="assistant", title="课程回答",
                body="第51题答案", tone="answer", question_context="第51题：题干",
            )
            reloaded = ConversationStore(root / "history.json")
            self.assertEqual(
                reloaded.latest_image_for_topic(session.session_id), retained
            )
            reloaded.delete_session(session.session_id)
            self.assertFalse(retained.exists())
            self.assertFalse(retained.parent.exists())
            self.assertTrue(source.exists())

    def test_image_lookup_does_not_cross_to_previous_topic_or_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "question.jpg"
            source.write_bytes(b"image")
            store = ConversationStore(root / "history.json")
            first = store.create_session()
            second = store.create_session()
            attachment = store.save_image(first.session_id, source)
            store.append_message(first.session_id, role="user", title="你",
                                 body="图片题", tone="user", attachments=[attachment])
            store.append_message(first.session_id, role="assistant", title="回答",
                                 body="旧题回答", tone="answer", question_context="旧题")
            store.append_message(first.session_id, role="user", title="你",
                                 body="新题", tone="user")
            store.append_message(first.session_id, role="assistant", title="回答",
                                 body="新题回答", tone="answer", question_context="新题")
            self.assertIsNone(store.latest_image_for_topic(first.session_id))
            self.assertIsNone(store.latest_image_for_topic(second.session_id))
            self.assertIsNone(store.resolve_image(second.session_id, attachment))
            self.assertIsNone(store.resolve_image(first.session_id, {
                "stored_name": "../question.jpg",
            }))


if __name__ == "__main__":
    unittest.main()
