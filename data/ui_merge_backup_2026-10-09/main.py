"""Desktop entry point for the multi-subject medical learning companion."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PySide6.QtCore import QObject, Slot
    from PySide6.QtWidgets import QApplication, QMessageBox
except ModuleNotFoundError as exc:
    raise SystemExit(
        "缺少 PySide6，请先运行: py -m pip install -r requirements.txt"
    ) from exc

from app_runtime import (
    RuntimeConfigurationError,
    RuntimePaths,
    build_application_service,
    inspect_runtime,
)
from conversation_store import ConversationStore
from model_settings_dialog import ModelSettingsDialog
from knowledge_base_dialog import KnowledgeBaseDialog
from skill_review_dialog import SkillReviewDialog
from evaluation_dialog import EvaluationDialog
from setup import AgentSetupWizard
from tutor_window import TutorMainWindow


class ApplicationController(QObject):
    """Own top-level windows and route first-run setup into the tutor UI."""

    def __init__(self, app: QApplication, base_dir: str | Path) -> None:
        super().__init__()
        self.app = app
        self.base_dir = Path(base_dir).resolve()
        self.wizard: AgentSetupWizard | None = None
        self.window: TutorMainWindow | None = None
        self.model_settings: ModelSettingsDialog | None = None
        self.knowledge_dialog: KnowledgeBaseDialog | None = None
        self.skill_dialog: SkillReviewDialog | None = None
        self.evaluation_dialog: EvaluationDialog | None = None
        self._knowledge_dirty = False
        self.conversation_store = ConversationStore(
            self.base_dir / "data" / "conversations.json"
        )

    def start(self) -> None:
        inspection = inspect_runtime(RuntimePaths.from_base_dir(self.base_dir))
        if inspection.ready:
            self._show_tutor()
        else:
            self._show_setup()

    def _show_setup(self) -> None:
        if self.wizard is not None:
            self.wizard.show()
            self.wizard.raise_()
            self.wizard.activateWindow()
            return
        if self.window is not None:
            self.window.setEnabled(False)
        self.wizard = AgentSetupWizard(auto_close_on_finish=False)
        self.wizard.configuration_completed.connect(self._configuration_completed)
        self.wizard.wizard_closed.connect(self._setup_closed)
        self.wizard.show()

    @Slot()
    def _setup_closed(self) -> None:
        self.wizard = None
        if self.window is not None:
            self.window.setEnabled(True)
            self.window.raise_()

    @Slot(str)
    def _configuration_completed(self, _: str) -> None:
        try:
            service = build_application_service(self.base_dir)
        except RuntimeConfigurationError as exc:
            QMessageBox.warning(
                self.wizard,
                "配置尚未就绪",
                "\n".join(f"• {item}" for item in exc.issues),
            )
            return
        except Exception as exc:
            QMessageBox.critical(
                self.wizard,
                "启动失败",
                f"{type(exc).__name__}: {exc}",
            )
            return
        self._show_tutor(service)
        if self.wizard is not None:
            wizard = self.wizard
            wizard.close()

    def _show_tutor(self, service=None) -> None:
        try:
            active_service = service or build_application_service(self.base_dir)
            issues: tuple[str, ...] = ()
        except RuntimeConfigurationError as exc:
            active_service = None
            issues = exc.issues
        except Exception as exc:
            active_service = None
            issues = (f"启动失败: {type(exc).__name__}: {exc}",)
        old_window = self.window
        self.window = TutorMainWindow(
            active_service,
            startup_issues=issues,
            conversation_store=self.conversation_store,
        )
        self.window.settings_requested.connect(self._show_model_settings)
        self.window.knowledge_requested.connect(self._show_knowledge_base)
        self.window.knowledge_files_dropped.connect(
            self._import_dropped_knowledge_files
        )
        self.window.skills_requested.connect(self._show_skills)
        self.window.evaluation_requested.connect(self._show_evaluation)
        self.window.show()
        if old_window is not None:
            old_window.close()

    @Slot()
    def _show_model_settings(self) -> None:
        if self.model_settings is not None:
            self.model_settings.show()
            self.model_settings.raise_()
            return
        self.model_settings = ModelSettingsDialog(self.base_dir, self.window)
        self.model_settings.configuration_saved.connect(self._model_settings_saved)
        self.model_settings.finished.connect(self._model_settings_closed)
        self.model_settings.show()

    @Slot()
    def _model_settings_saved(self) -> None:
        try:
            service = build_application_service(self.base_dir)
        except Exception as exc:
            QMessageBox.warning(
                self.model_settings,
                "应用配置失败",
                f"{type(exc).__name__}: {exc}",
            )
            return
        self._show_tutor(service)

    @Slot()
    def _model_settings_closed(self) -> None:
        self.model_settings = None

    @Slot()
    def _show_knowledge_base(self) -> None:
        if self.knowledge_dialog is not None:
            self.knowledge_dialog.show()
            self.knowledge_dialog.raise_()
            return
        self._knowledge_dirty = False
        self.knowledge_dialog = KnowledgeBaseDialog(self.base_dir, self.window)
        self.knowledge_dialog.knowledge_changed.connect(self._mark_knowledge_dirty)
        self.knowledge_dialog.finished.connect(self._knowledge_base_closed)
        self.knowledge_dialog.show()

    @Slot(object)
    def _import_dropped_knowledge_files(self, paths) -> None:
        self._show_knowledge_base()
        if self.knowledge_dialog is not None:
            self.knowledge_dialog.import_files(list(paths))

    @Slot()
    def _mark_knowledge_dirty(self) -> None:
        self._knowledge_dirty = True

    @Slot()
    def _knowledge_base_closed(self) -> None:
        self.knowledge_dialog = None
        if not self._knowledge_dirty:
            return
        self._knowledge_dirty = False
        try:
            service = build_application_service(self.base_dir)
        except Exception as exc:
            QMessageBox.warning(
                self.window,
                "刷新知识库失败",
                f"{type(exc).__name__}: {exc}",
            )
            return
        self._show_tutor(service)

    @Slot(object, str)
    def _show_skills(self, result, question: str) -> None:
        if self.skill_dialog is not None:
            self.skill_dialog.show()
            self.skill_dialog.raise_()
            return
        self.skill_dialog = SkillReviewDialog(
            self.base_dir,
            result=result,
            question=question,
            parent=self.window,
        )
        self.skill_dialog.finished.connect(self._skills_closed)
        self.skill_dialog.show()

    @Slot()
    def _skills_closed(self) -> None:
        self.skill_dialog = None

    @Slot()
    def _show_evaluation(self) -> None:
        if self.window is None or self.window.service is None:
            return
        if self.evaluation_dialog is not None:
            self.evaluation_dialog.show()
            self.evaluation_dialog.raise_()
            return
        self.evaluation_dialog = EvaluationDialog(
            self.base_dir, self.window.service, parent=self.window
        )
        self.evaluation_dialog.setModal(True)
        self.evaluation_dialog.finished.connect(self._evaluation_closed)
        self.evaluation_dialog.show()

    @Slot()
    def _evaluation_closed(self) -> None:
        self.evaluation_dialog = None


def main() -> int:
    app = QApplication(sys.argv)
    controller = ApplicationController(app, Path(__file__).resolve().parent)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
