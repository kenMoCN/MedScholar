"""PySide question-bank evaluation dashboard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.app_runtime import TutorApplicationService
from evaluation.evaluation import (
    EvaluationReport,
    RetrievalDiagnosticReport,
    EvaluationRunner,
    load_question_bank,
    save_question_bank_template,
    save_report,
    run_retrieval_diagnostic,
)
from retrieval.structure_retriever import StructureAwareRetriever
from ui.dialog_theme import apply_dialog_theme, mark_button
from evaluation.learning_experience_evaluation import (
    BlindComparisonOutput,
    run_blind_comparison,
)


class EvaluationWorker(QObject):
    progress = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, service, cases, use_skills, *, retriever=None, comparison_dir=None
    ) -> None:
        super().__init__()
        self.service = service
        self.cases = cases
        self.use_skills = use_skills
        self.retriever = retriever
        self.comparison_dir = comparison_dir

    @Slot()
    def run(self) -> None:
        try:
            callback = lambda message, current, total: self.progress.emit(
                message, current, total
            )
            if self.comparison_dir is not None:
                report = run_blind_comparison(
                    self.service,
                    self.cases,
                    self.comparison_dir,
                    use_skills=self.use_skills,
                    progress=callback,
                )
            elif self.retriever is not None:
                report = run_retrieval_diagnostic(
                    self.retriever, self.cases, top_k=3, progress=callback
                )
            else:
                report = EvaluationRunner(self.service).run(
                    self.cases,
                    use_skills=self.use_skills,
                    progress=callback,
                )
            self.succeeded.emit(report)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


class EvaluationDialog(QDialog):
    def __init__(
        self,
        base_dir: str | Path,
        service: TutorApplicationService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir).resolve()
        self.service = service
        self.cases = ()
        self.report: EvaluationReport | None = None
        self._thread: QThread | None = None
        self._worker: EvaluationWorker | None = None
        self.setWindowTitle("医学题库评测与错误归因")
        self.resize(1120, 700)
        self.setMinimumSize(900, 580)
        apply_dialog_theme(self)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("医学题库评测与错误归因")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        hint = QLabel(
            "严格统计课程检索命中、关键概念覆盖、引用核验、幻觉拦截和耗时。"
            "正式结果必须使用教师确认的题库；运行报错不会计作安全拒答。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        layout.addWidget(hint)

        actions = QHBoxLayout()
        self.load_button = QPushButton("导入题库 JSON")
        self.template_button = QPushButton("生成题库模板")
        self.use_skills = QCheckBox("启用已审核 Skill（可关闭做消融实验）")
        self.run_button = QPushButton("开始评测")
        self.retrieval_button = QPushButton("仅检索诊断（不调用模型）")
        self.comparison_button = QPushButton("生成匿名 A/B 学习体验评测")
        self.export_button = QPushButton("另存报告")
        mark_button(self.load_button, "primary")
        mark_button(self.template_button, "quiet")
        mark_button(self.run_button, "primary")
        mark_button(self.retrieval_button, "quiet")
        mark_button(self.comparison_button, "quiet")
        mark_button(self.export_button, "quiet")
        self.export_button.setEnabled(False)
        self.load_button.clicked.connect(self._load_bank)
        self.template_button.clicked.connect(self._save_template)
        self.run_button.clicked.connect(self._start)
        self.retrieval_button.clicked.connect(self._start_retrieval)
        self.comparison_button.clicked.connect(self._start_comparison)
        self.export_button.clicked.connect(self._export)
        actions.addWidget(self.load_button)
        actions.addWidget(self.template_button)
        actions.addWidget(self.use_skills)
        actions.addStretch()
        actions.addWidget(self.retrieval_button)
        actions.addWidget(self.comparison_button)
        actions.addWidget(self.run_button)
        actions.addWidget(self.export_button)
        layout.addLayout(actions)
        self.bank_label = QLabel("尚未导入正式题库")
        self.bank_label.setObjectName("InfoBanner")
        layout.addWidget(self.bank_label)

        metrics = QGridLayout()
        self.metric_labels = {}
        metric_names = [
            ("pass_rate", "严格通过率"),
            ("outcome_accuracy", "结果类型准确率"),
            ("retrieval_hit_rate", "检索命中率"),
            ("citation_verification_rate", "引用核验通过率"),
            ("hallucination_block_rate", "幻觉拦截率"),
            ("mean_term_coverage", "关键概念覆盖率"),
        ]
        for index, (key, name) in enumerate(metric_names):
            label = QLabel(f"{name}：—")
            label.setObjectName("MetricCard")
            metrics.addWidget(label, index // 3, index % 3)
            self.metric_labels[key] = (label, name)
        layout.addLayout(metrics)

        self.table = QTableWidget(0, 10)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels([
            "题号", "问题", "预期", "实际", "检索", "概念覆盖",
            "引用", "Skill", "错误归因", "耗时/s",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnWidth(0, 95)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 85)
        self.table.setColumnWidth(3, 120)
        self.table.setColumnWidth(8, 190)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedWidth(220)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("StatusText")
        close_button = QPushButton("关闭")
        mark_button(close_button, "quiet")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.status_label)
        bottom.addStretch()
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

    @Slot()
    def _load_bank(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择评测题库", "", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            self.cases = load_question_bank(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "题库格式错误", str(exc))
            return
        self.bank_label.setText(f"已载入 {len(self.cases)} 题：{path}")
        self.status_label.setText("题库已就绪")

    @Slot()
    def _save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存题库模板",
            str(self.base_dir / "evaluation" / "evaluation_question_bank_template.json"),
            "JSON 文件 (*.json)",
        )
        if not path:
            return
        try:
            save_question_bank_template(path)
        except OSError as exc:
            QMessageBox.warning(self, "无法保存模板", str(exc))
            return
        self.status_label.setText(f"题库模板已保存：{path}")

    @Slot()
    def _start(self) -> None:
        self._start_worker(retrieval_only=False)

    @Slot()
    def _start_retrieval(self) -> None:
        self._start_worker(retrieval_only=True)

    @Slot()
    def _start_comparison(self) -> None:
        self._start_worker(retrieval_only=False, comparison=True)

    def _start_worker(
        self, *, retrieval_only: bool, comparison: bool = False
    ) -> None:
        if self._thread is not None:
            return
        if not self.cases:
            self.status_label.setText("请先导入至少一道正式评测题")
            return
        self.report = None
        self.table.setRowCount(0)
        self._set_busy(True)
        thread = QThread(self)
        retriever = None
        if retrieval_only:
            retriever = StructureAwareRetriever.from_directories(
                self.base_dir / "data" / "document_maps",
                self.base_dir / "data" / "page_indexes",
            )
        worker = EvaluationWorker(
            self.service,
            self.cases,
            self.use_skills.isChecked(),
            retriever=retriever,
            comparison_dir=(
                self.base_dir / "data" / "evaluations" if comparison else None
            ),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(str, int, int)
    def _on_progress(self, message: str, current: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(message)

    @Slot(object)
    def _on_success(self, report: Any) -> None:
        if isinstance(report, BlindComparisonOutput):
            self.report = None
            self.status_label.setText(
                f"匿名对比已生成（{report.case_count}题）：{report.review_path}；"
                f"答案来源密钥单独保存于 {report.answer_key_path}"
            )
            self.export_button.setEnabled(False)
            return
        self.report = report
        if isinstance(report, RetrievalDiagnosticReport):
            self._render_retrieval_report(report)
        else:
            self._render_report(report)
        output_dir = self.base_dir / "data" / "evaluations"
        suffix = (
            "retrieval_only"
            if isinstance(report, RetrievalDiagnosticReport)
            else ("skills_on" if report.use_skills else "skills_off")
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = output_dir / f"{timestamp}_{suffix}_{report.run_id[-8:]}.json"
        try:
            save_report(report, output)
            self.status_label.setText(f"评测完成，报告已保存：{output}")
        except OSError as exc:
            self.status_label.setText(f"评测完成，但自动保存失败：{exc}")
        self.export_button.setEnabled(True)

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        self.status_label.setText("评测流程失败")
        QMessageBox.warning(self, "评测失败", message)

    @Slot()
    def _on_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_busy(False)

    def _render_report(self, report: EvaluationReport) -> None:
        for key, (label, name) in self.metric_labels.items():
            value = report.metrics.get(key)
            rendered = "—" if value is None else f"{float(value) * 100:.1f}%"
            label.setText(f"{name}：{rendered}")
        self.table.setRowCount(0)
        for item in report.case_results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                item.case_id,
                item.question,
                item.expected_outcome,
                item.actual_status,
                _flag(item.retrieval_hit),
                "—" if item.term_coverage is None else f"{item.term_coverage * 100:.0f}%",
                _flag(item.citation_verified),
                str(item.recalled_skill_count),
                "、".join(item.error_categories) or "通过",
                f"{item.duration_seconds:.2f}",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    def _render_retrieval_report(self, report: RetrievalDiagnosticReport) -> None:
        for key, (label, name) in self.metric_labels.items():
            value = report.metrics.get("hit_at_k_rate") if key == "retrieval_hit_rate" else None
            rendered = "—" if value is None else f"{float(value) * 100:.1f}%"
            label.setText(f"{name}：{rendered}")
        self.table.setRowCount(0)
        for item in report.case_results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                item.case_id,
                item.question,
                "检索命中",
                "Top-3命中" if item.hit_at_k else "未命中",
                _flag(item.hit_at_k),
                "—",
                "—",
                "—",
                "、".join(item.error_categories) or "通过",
                f"{item.duration_seconds:.2f}",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

    @Slot()
    def _export(self) -> None:
        if self.report is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "另存评测报告", "evaluation_report.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            save_report(self.report, path)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status_label.setText(f"报告已导出：{path}")

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.load_button, self.template_button, self.use_skills,
            self.retrieval_button, self.comparison_button,
            self.run_button, self.export_button,
        ):
            widget.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)
            self.status_label.setText("正在运行正式评测，请勿关闭窗口……")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.export_button.setEnabled(self.report is not None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "评测仍在运行", "请等待当前题库评测完成。")
            event.ignore()
            return
        event.accept()


def _flag(value: bool | None) -> str:
    if value is None:
        return "—"
    return "通过" if value else "失败"
