import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from app_runtime import ApprovalRequest
from conversation_store import ConversationStore
from tutor_window import TutorMainWindow
from ui_presenter import PresentedResult


class WebUiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_react_frontend_loads_and_connects_python_bridge(self):
        window = TutorMainWindow(None, conversation_store=ConversationStore())
        loaded = []
        load_loop = QEventLoop()

        def on_loaded(ok):
            loaded.append(bool(ok))
            QTimer.singleShot(1200, load_loop.quit)

        window.web_view.loadFinished.connect(on_loaded)
        QTimer.singleShot(10000, load_loop.quit)
        window.show()
        load_loop.exec()

        dom_result = []
        js_loop = QEventLoop()

        def on_javascript(value):
            dom_result.append(json.loads(value))
            js_loop.quit()

        window.web_view.page().runJavaScript(
            "JSON.stringify({"
            "textarea: !!document.querySelector('textarea'),"
            "bridge: !!window.pyBridge,"
            "appText: document.body.innerText.includes('向智能体提问')"
            "})",
            on_javascript,
        )
        QTimer.singleShot(5000, js_loop.quit)
        js_loop.exec()

        self.assertEqual(loaded, [True])
        self.assertTrue(dom_result)
        self.assertEqual(
            dom_result[0],
            {"textarea": True, "bridge": True, "appText": True},
        )

        class WaitingWorker:
            def __init__(self):
                self.decision = None

            def resolve_approval(self, approved):
                self.decision = approved

        worker = WaitingWorker()
        window._worker = worker
        window._on_approval_requested(
            ApprovalRequest(
                "approval_1",
                "允许发送题目图片？",
                "图片将发送给视觉模型进行识别。",
                "允许本次发送",
            )
        )
        approval_loop = QEventLoop()
        QTimer.singleShot(300, approval_loop.quit)
        approval_loop.exec()

        approval_dom = []
        approval_js_loop = QEventLoop()
        window.web_view.page().runJavaScript(
            "JSON.stringify({"
            "title: document.body.innerText.includes('允许发送题目图片？'),"
            "button: document.body.innerText.includes('允许本次发送')"
            "})",
            lambda value: (approval_dom.append(json.loads(value)), approval_js_loop.quit()),
        )
        QTimer.singleShot(5000, approval_js_loop.quit)
        approval_js_loop.exec()
        self.assertEqual(approval_dom[0], {"title": True, "button": True})

        click_loop = QEventLoop()
        window.web_view.page().runJavaScript(
            "Array.from(document.querySelectorAll('button'))"
            ".find((button) => button.innerText.includes('允许本次发送')).click()",
            lambda _: QTimer.singleShot(300, click_loop.quit),
        )
        QTimer.singleShot(5000, click_loop.quit)
        click_loop.exec()
        self.assertTrue(worker.decision)
        self.assertIsNone(window._pending_approval)

        video_message = PresentedResult(
            title="视频学习建议",
            body="找到一个视频。",
            tone="answer",
            status_text="视频学习资源已准备",
            resources=({
                "resource_id": "video_1",
                "resource_kind": "video_candidate",
                "title": "动作电位动画",
                "url": "https://www.bilibili.com/video/BV1234567890",
                "recommendation_reason": "演示复极化过程",
            },),
            actions=({"id": "reexplain", "label": "换一种讲法", "prompt": "换一种讲法"},),
        )
        serialized = window._format_ai_result_json(video_message)
        self.assertEqual(json.loads(serialized)["resources"][0]["title"], "动作电位动画")
        self.assertEqual(json.loads(serialized)["meta"]["evidence"], "学习资源：动作电位动画")
        window.web_bridge.answerReceived.emit(serialized)
        video_loop = QEventLoop()
        QTimer.singleShot(300, video_loop.quit)
        video_loop.exec()
        video_dom = []
        video_js_loop = QEventLoop()
        window.web_view.page().runJavaScript(
            "JSON.stringify({"
            "card: document.body.innerText.includes('动作电位动画'),"
            "reason: document.body.innerText.includes('演示复极化过程'),"
            "reexplain: document.body.innerText.includes('换一种讲法')"
            "})",
            lambda value: (video_dom.append(json.loads(value)), video_js_loop.quit()),
        )
        QTimer.singleShot(5000, video_js_loop.quit)
        video_js_loop.exec()
        self.assertEqual(video_dom[0], {"card": True, "reason": True, "reexplain": True})
        window._worker = None
        window.close()


if __name__ == "__main__":
    unittest.main()
