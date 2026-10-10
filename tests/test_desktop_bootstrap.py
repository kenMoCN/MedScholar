import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from agent_orchestrator import AgentRunResult
from app_runtime import ProgressUpdate, TutorApplicationService
from app_runtime import ApprovalRequest
from conversation_store import ConversationStore
from dynamic_config import load_dynamic_config, save_dynamic_config
from main import ApplicationController
from model_settings_dialog import ModelSettingsDialog
from tutor_window import AgentWorker, DeepSeekTextEdit, TutorMainWindow, WebBridge
from ui_presenter import PresentedResult


class FakeOrchestrator:
    def run(self, request, *, progress_callback=None):
        return AgentRunResult(
            status="completed",
            plan=None,
            answer=None,
            rendered_answer="测试答案",
            evidence_bundle=None,
            deterministic_report=None,
            semantic_report=None,
            clarification_questions=[],
            events=[],
        )


class DesktopBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_populates_all_modes(self):
        service = TutorApplicationService(FakeOrchestrator())
        window = TutorMainWindow(service, conversation_store=ConversationStore())
        try:
            self.assertEqual(window.mode_combo.count(), 5)
            self.assertGreaterEqual(window.subject_combo.count(), 10)
            self.assertEqual(window.subject_combo.currentData(), "auto")
            self.assertEqual(window.horizon_combo.count(), 3)
            self.assertEqual(
                window.horizon_combo.currentData(), "research_mode_only"
            )
            self.assertTrue(window.send_button.isEnabled())
            self.assertTrue(window.stop_button.isHidden())
            self.assertTrue(window.skills_button.isEnabled())
            self.assertTrue(window.evaluation_button.isEnabled())
            self.assertTrue(window.official_sources_button.isEnabled())
            self.assertTrue(window.research_panel.isHidden())
            self.assertEqual(window.history.objectName(), "Conversation")
            self.assertEqual(window.input_edit.objectName(), "QuestionInput")
            self.assertEqual(window.session_list.objectName(), "SessionList")
        finally:
            window.close()

    def test_model_settings_table_keeps_editors_and_api_key_visible(self):
        with tempfile.TemporaryDirectory() as base_dir:
            dialog = ModelSettingsDialog(base_dir)
            try:
                dialog.resize(1180, 620)
                dialog.show()
                self.app.processEvents()
                self.assertGreaterEqual(dialog.table.rowHeight(0), 50)
                self.assertGreaterEqual(dialog.table.columnWidth(7), 180)
                protocol = dialog.table.cellWidget(0, 5)
                self.assertEqual(protocol.currentData(), "openai_compatible")
                self.assertEqual(protocol.currentText(), "标准兼容")
                self.assertLessEqual(
                    protocol.geometry().right(),
                    dialog.table.visualRect(dialog.table.model().index(0, 5)).right(),
                )
                self.assertGreaterEqual(
                    dialog.table.rowHeight(0),
                    dialog.table.cellWidget(0, 1).sizeHint().height(),
                )
                self.assertLessEqual(
                    dialog.table.visualRect(dialog.table.model().index(0, 7)).right(),
                    dialog.table.viewport().width(),
                )
            finally:
                dialog.close()

    def test_model_settings_can_route_vision_to_deepseek_flash(self):
        with tempfile.TemporaryDirectory() as base_dir:
            save_dynamic_config(
                base_dir,
                models=[{
                    "model_id": "deepseek_default",
                    "provider": "deepseek",
                    "model_name": "deepseek-flash",
                    "api_protocol": "openai_compatible",
                    "capabilities": ["text", "reasoning", "structured_output"],
                    "enabled": True,
                    "api_key": "secret-ds",
                }],
                routes={"reasoning": "deepseek_default", "verification": "deepseek_default"},
            )
            dialog = ModelSettingsDialog(base_dir)
            try:
                vision = dialog.route_combos["vision"]
                index = vision.findData("deepseek_default")
                self.assertGreater(index, 0)
                self.assertIn("DeepSeek Flash", vision.itemText(index))
                vision.setCurrentIndex(index)
                dialog._save()
                loaded = load_dynamic_config(base_dir)
                self.assertEqual(loaded.issues, ())
                self.assertEqual(loaded.config["model_routes"]["vision"], "deepseek_default")
                self.assertIn(
                    "vision",
                    loaded.config["model_registry"]["deepseek_default"]["capabilities"],
                )
                self.assertEqual(loaded.secrets["MODEL_KEY_DEEPSEEK_DEFAULT"], "secret-ds")
            finally:
                dialog.close()

    def test_subject_selector_updates_service_subject(self):
        service = TutorApplicationService(FakeOrchestrator())
        window = TutorMainWindow(service, conversation_store=ConversationStore())
        try:
            window.subject_combo.setCurrentIndex(
                window.subject_combo.findData("pharmacology")
            )
            self.assertEqual(service.current_subject, "pharmacology")
            self.assertIn("药理学", window.subject_badge.text())
        finally:
            window.close()

    def test_medical_horizon_selector_updates_service_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            service = TutorApplicationService(FakeOrchestrator())
            window = TutorMainWindow(
                service,
                conversation_store=ConversationStore(Path(temp) / "history.json"),
            )
            try:
                window.horizon_combo.setCurrentIndex(
                    window.horizon_combo.findData("all_answers")
                )
                self.assertEqual(service.current_medical_horizon_policy, "all_answers")
                self.assertIn("每次答题", window.status_label.text())
            finally:
                window.close()

    def test_official_source_status_is_rendered_from_service_interface(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            text = window._official_source_status_text()
            self.assertIn("世界卫生组织 WHO：自动检索与全文核验", text)
            self.assertIn("英国 NICE：需要另行申请正式接口许可", text)
            self.assertIn("中国国家卫生健康委员会：官方入口人工复核", text)
            self.assertIn("https://www.nhc.gov.cn/", text)
        finally:
            window.close()

    def test_research_mode_shows_filters_and_builds_request_options(self):
        service = TutorApplicationService(FakeOrchestrator())
        window = TutorMainWindow(service, conversation_store=ConversationStore())
        try:
            window.mode_combo.setCurrentIndex(window.mode_combo.findData("research"))
            self.assertFalse(window.research_panel.isHidden())
            window.research_year_from.setCurrentIndex(
                window.research_year_from.findData(2020)
            )
            window.research_year_to.setCurrentIndex(
                window.research_year_to.findData(2025)
            )
            window.research_study_type.setCurrentIndex(2)
            filters = window._research_filters()
            self.assertEqual((filters.year_from, filters.year_to), (2020, 2025))
            self.assertEqual(filters.study_types, ("randomized controlled trial",))
        finally:
            window.close()

    def test_invalid_research_year_range_is_blocked_in_ui(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window.mode_combo.setCurrentIndex(window.mode_combo.findData("research"))
            window.research_year_from.setCurrentIndex(
                window.research_year_from.findData(2025)
            )
            window.research_year_to.setCurrentIndex(
                window.research_year_to.findData(2020)
            )
            with self.assertRaisesRegex(ValueError, "起始年份"):
                window._research_filters()
        finally:
            window.close()

    def test_progress_updates_replace_same_phase_in_timeline(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._set_busy(True)
            window._on_progress(ProgressUpdate("paper_search", "正在检索", "running"))
            window._on_progress(ProgressUpdate("paper_search", "检索完成", "ok"))
            self.assertEqual(window.progress_list.count(), 1)
            self.assertIn("检索完成", window.progress_list.item(0).text())
            self.assertFalse(window.progress_panel.isHidden())
            self.assertFalse(window.official_sources_button.isEnabled())
        finally:
            window._set_busy(False)
            self.assertTrue(window.official_sources_button.isEnabled())
            window.close()

    def test_learning_actions_render_as_clickable_buttons(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._set_learning_actions((
                {"id": "example", "label": "举个例子", "prompt": "请举例"},
                {"id": "video", "label": "找视频", "prompt": "请找视频"},
            ))

            buttons = [
                window.learning_actions_layout.itemAt(index).widget()
                for index in range(window.learning_actions_layout.count())
                if window.learning_actions_layout.itemAt(index).widget() is not None
            ]
            self.assertFalse(window.learning_actions_panel.isHidden())
            self.assertEqual([button.text() for button in buttons], ["举个例子", "找视频"])
        finally:
            window.close()

    def test_learning_action_button_passes_action_id_and_current_topic(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._set_learning_actions((
                {"id": "example", "label": "举个例子", "prompt": "请举例"},
            ), current_topic="动作电位为什么会复极化？")
            button = window.learning_actions_layout.itemAt(0).widget()
            with patch.object(window, "_send") as send:
                button.click()
            send.assert_called_once_with()
            self.assertEqual(window._pending_learning_action_id, "example")
            self.assertEqual(window._pending_learning_topic, "动作电位为什么会复极化？")
            self.assertEqual(window.input_edit.toPlainText(), "请举例")
        finally:
            window.close()

    def test_web_learning_action_keeps_backend_topic(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._set_learning_actions(
                ({"id": "video", "label": "找视频", "prompt": "请找视频"},),
                current_topic="脂肪酸β-氧化",
            )
            bridge = WebBridge(window)
            with patch.object(window, "_send") as send:
                bridge.sendLearningAction("video", "找视频", "请找视频")
            send.assert_called_once_with()
            self.assertEqual(window._pending_learning_action_id, "video")
            self.assertEqual(window._pending_learning_topic, "脂肪酸β-氧化")
        finally:
            window.close()

    def test_web_learning_action_uses_topic_from_clicked_answer(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._current_learning_topic = "新问题：糖酵解"
            bridge = WebBridge(window)
            with patch.object(window, "_send") as send:
                bridge.sendLearningAction(
                    "research", "找论文拓展", "请找论文", "旧回答：动作电位"
                )
            send.assert_called_once_with()
            self.assertEqual(window._pending_learning_action_id, "research")
            self.assertEqual(window._pending_learning_topic, "旧回答：动作电位")
        finally:
            window.close()

    def test_web_result_uses_only_real_evidence_and_actions(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            no_evidence = PresentedResult("回答", "正文", "answer", "完成")
            payload = json.loads(window._format_ai_result_json(no_evidence))
            self.assertEqual(payload["meta"]["evidence"], "")
            self.assertEqual(payload["actions"], [])

            with_actions = PresentedResult(
                "回答",
                "正文",
                "answer",
                "完成",
                resources=({"title": "生物化学教材第147页"},),
                actions=({"id": "video", "label": "找视频", "prompt": "请找视频"},),
            )
            payload = json.loads(window._format_ai_result_json(with_actions))
            self.assertIn("生物化学教材第147页", payload["meta"]["evidence"])
            self.assertEqual([item["id"] for item in payload["actions"]], ["video"])

            with_paper = PresentedResult(
                "论文拓展", "已找到论文", "answer", "完成",
                resources=({
                    "paper_id": "paper_1", "title": "Action potential review",
                    "url": "https://example.org/paper", "year": 2025,
                },),
                actions=({"id": "research", "label": "找论文拓展"},),
            )
            payload = json.loads(window._format_ai_result_json(
                with_paper, topic="动作电位"
            ))
            self.assertEqual(payload["resources"][0]["resource_kind"], "research_paper")
            self.assertEqual(payload["resources"][0]["url"], "https://example.org/paper")
            self.assertEqual(payload["actions"][0]["topic"], "动作电位")
            self.assertIn("论文资源", payload["meta"]["evidence"])
        finally:
            window.close()

    def test_send_shortcut_mode_is_configurable(self):
        editor = DeepSeekTextEdit()
        for mode in ("Enter", "Ctrl+Enter", "Shift+Enter"):
            editor.set_send_key_mode(mode)
            self.assertEqual(editor.send_key_mode, mode)
        editor.set_send_key_mode("unsupported")
        self.assertEqual(editor.send_key_mode, "Shift+Enter")

    def test_worker_does_not_emit_success_after_late_cancellation(self):
        holder = {}

        class LateCancellingService:
            def ask(self, *args, **kwargs):
                holder["worker"].request_cancel()
                return object()

        worker = AgentWorker(
            LateCancellingService(),
            query="陪我聊聊天",
            image=None,
            image_media_type=None,
            research_filters=None,
        )
        holder["worker"] = worker
        succeeded = []
        cancelled = []
        worker.succeeded.connect(succeeded.append)
        worker.cancelled.connect(cancelled.append)

        worker.run()

        self.assertEqual(succeeded, [])
        self.assertEqual(len(cancelled), 1)

    def test_inline_approval_card_resolves_waiting_worker(self):
        class WaitingWorker:
            def __init__(self):
                self.decision = None

            def resolve_approval(self, approved):
                self.decision = approved

        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            worker = WaitingWorker()
            window._worker = worker
            request = ApprovalRequest(
                "approval_1",
                "允许发送题目图片？",
                "图片将发送给视觉模型。",
                "允许本次发送",
            )
            window._on_approval_requested(request)
            self.assertFalse(window.approval_panel.isHidden())

            window._resolve_approval(True)

            self.assertTrue(worker.decision)
            self.assertTrue(window.approval_panel.isHidden())
        finally:
            window._worker = None
            window.close()

    def test_unconfigured_window_disables_input(self):
        window = TutorMainWindow(
            None,
            startup_issues=("缺少模型名称",),
            conversation_store=ConversationStore(),
        )
        try:
            self.assertEqual(window.windowTitle(), "MedScholar")
            self.assertFalse(window.send_button.isEnabled())
            self.assertIn("缺少模型名称", window.history.toPlainText())
        finally:
            window.close()

    def test_first_run_routes_to_setup_wizard(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = ApplicationController(self.app, Path(temp))
            controller.start()
            try:
                self.assertIsNotNone(controller.wizard)
                self.assertIsNone(controller.window)
                self.assertTrue(controller.wizard.isMaximized())
            finally:
                controller.wizard.close()

    def test_setup_flag_opens_wizard_even_with_existing_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = ApplicationController(self.app, Path(temp))
            controller.start(force_setup=True)
            try:
                self.assertIsNotNone(controller.wizard)
                self.assertIsNone(controller.window)
            finally:
                controller.wizard.close()

    def test_new_conversation_is_added_to_sidebar(self):
        store = ConversationStore()
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=store,
        )
        try:
            initial_count = window.session_list.count()
            window._new_conversation()
            self.assertEqual(window.session_list.count(), initial_count + 1)
        finally:
            window.close()

    def test_current_conversation_can_be_deleted(self):
        store = ConversationStore()
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=store,
        )
        try:
            deleted_id = window._session_id
            window._new_conversation()
            window._session_id = deleted_id
            window._refresh_session_list()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window._delete_current_conversation()

            self.assertNotIn(
                deleted_id,
                [session.session_id for session in store.list_sessions()],
            )
            self.assertEqual(window.session_list.count(), 1)
            self.assertEqual(window.status_label.text(), "对话已删除")
        finally:
            window.close()

    def test_focus_reading_expands_answer_surface(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            window._set_focus_mode(True)
            self.assertTrue(window.sidebar.isHidden())
            self.assertTrue(window.top_bar.isHidden())
            self.assertTrue(window.composer.isHidden())
            self.assertFalse(window.history.isHidden())

            window._set_focus_mode(False)
            self.assertFalse(window.sidebar.isHidden())
            self.assertFalse(window.composer.isHidden())
        finally:
            window.close()

    def test_full_resource_button_reports_all_candidates(self):
        window = TutorMainWindow(
            TutorApplicationService(FakeOrchestrator()),
            conversation_store=ConversationStore(),
        )
        try:
            resources = ({"title": "A"}, {"title": "B"})
            window._set_available_resources(resources)
            self.assertTrue(window.sources_button.isEnabled())
            self.assertIn("2", window.sources_button.text())
        finally:
            window.close()

    def test_dropped_image_attaches_and_pdf_routes_to_knowledge_base(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "question.png"
            pdf = Path(temp) / "course.pdf"
            image.write_bytes(b"png")
            pdf.write_bytes(b"pdf")
            routed_pdfs = []
            window = TutorMainWindow(
                TutorApplicationService(FakeOrchestrator()),
                conversation_store=ConversationStore(),
            )
            window.knowledge_files_dropped.connect(routed_pdfs.extend)
            try:
                window._handle_dropped_paths([str(image), str(pdf)])

                self.assertEqual(window._image_path, str(image))
                self.assertEqual(window.attachment_label.text(), "question.png")
                self.assertEqual(routed_pdfs, [str(pdf)])
                self.assertIn("1 份 PDF", window.status_label.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
