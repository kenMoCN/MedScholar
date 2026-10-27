"""PySide knowledge-base manager with background PDF/PPTX indexing."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from retrieval.knowledge_base import KnowledgeBaseService
from core.app_runtime import RuntimeConfigurationError, build_vision_client
from ui.dialog_theme import apply_dialog_theme, mark_button
from core.subject_profiles import subject_options


class KnowledgeWorker(QObject):
    progress = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: KnowledgeBaseService, action: str, payload: Any) -> None:
        super().__init__()
        self.service = service
        self.action = action
        self.payload = payload
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @Slot()
    def run(self) -> None:
        try:
            if self.action == "import":
                result = self.service.import_documents(
                    self.payload,
                    progress=lambda message, current, total: self.progress.emit(
                        message, current, total
                    ),
                )
            elif self.action == "remove":
                result = self.service.remove_document(str(self.payload))
            elif self.action == "visual_index":
                document_id, vision_client = self.payload
                result = self.service.index_document_visuals(
                    str(document_id),
                    vision_client,
                    cancel_check=self._cancel_requested.is_set,
                    progress=lambda message, current, total: self.progress.emit(
                        message, current, total
                    ),
                )
            else:
                raise ValueError(f"未知知识库操作: {self.action}")
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


class VisualReviewDialog(QDialog):
    """Review low-confidence slide OCR against the complete slide image."""

    def __init__(
        self, service: KnowledgeBaseService, document_id: str, parent=None
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.document_id = document_id
        self.items = list(service.list_visual_reviews(document_id))
        self.position = 0
        self.changed = False
        self.setWindowTitle("复核图片文字")
        self.resize(980, 760)
        self.setMinimumSize(760, 600)
        apply_dialog_theme(self)
        self._build_ui()
        self._show_current()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("对照整张幻灯片复核识别文字")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.meta_label.setObjectName("DialogHint")
        layout.addWidget(self.meta_label)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumHeight(300)
        self.image_label.setStyleSheet(
            "QLabel { background: #f7f8fa; border: 1px solid #dfe3e8; "
            "border-radius: 8px; padding: 8px; }"
        )
        layout.addWidget(self.image_label, 3)
        text_hint = QLabel("识别文字（可直接修改；确认后才会作为课程证据使用）")
        layout.addWidget(text_hint)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("请根据上方原图补充或修正文字……")
        layout.addWidget(self.editor, 2)
        actions = QHBoxLayout()
        self.dismiss_button = QPushButton("忽略此页")
        mark_button(self.dismiss_button, "quiet")
        self.dismiss_button.clicked.connect(self._dismiss_current)
        self.confirm_button = QPushButton("确认并采用")
        mark_button(self.confirm_button, "primary")
        self.confirm_button.clicked.connect(self._confirm_current)
        close_button = QPushButton("稍后继续")
        mark_button(close_button, "quiet")
        close_button.clicked.connect(self.accept)
        actions.addWidget(self.dismiss_button)
        actions.addStretch()
        actions.addWidget(close_button)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

    def _show_current(self) -> None:
        if self.position >= len(self.items):
            self.meta_label.setText("这份课件没有待复核的图片页。")
            self.image_label.clear()
            self.editor.clear()
            self.editor.setEnabled(False)
            self.confirm_button.setEnabled(False)
            self.dismiss_button.setEnabled(False)
            return
        item = self.items[self.position]
        confidence = (
            f"{item.confidence:.0%}" if item.confidence is not None else "未提供"
        )
        uncertain = "、".join(item.uncertain_spans) or "无具体标记"
        self.meta_label.setText(
            f"{item.original_name} · 第 {item.slide_number} 页 · "
            f"识别置信度 {confidence}\n视觉模型：{item.model or '未记录'}；"
            f"不确定内容：{uncertain}"
        )
        try:
            image_data, _ = self.service.visual_review_image(
                item.document_id, item.slide_number
            )
            pixmap = QPixmap()
            if pixmap.loadFromData(image_data):
                self.image_label.setPixmap(
                    pixmap.scaled(
                        900, 430, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
            else:
                self.image_label.setText("无法显示该页预览")
        except Exception as exc:
            self.image_label.setText(f"无法生成该页预览：{exc}")
        self.editor.setPlainText(item.proposed_text)
        self.editor.setEnabled(True)
        self.confirm_button.setEnabled(True)
        self.dismiss_button.setEnabled(True)

    @Slot()
    def _confirm_current(self) -> None:
        if self.position >= len(self.items):
            return
        item = self.items[self.position]
        try:
            self.service.confirm_visual_review(
                item.document_id, item.slide_number, self.editor.toPlainText()
            )
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "无法确认", str(exc))
            return
        self.changed = True
        self.position += 1
        self._show_current()

    @Slot()
    def _dismiss_current(self) -> None:
        if self.position >= len(self.items):
            return
        item = self.items[self.position]
        try:
            self.service.dismiss_visual_review(item.document_id, item.slide_number)
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "无法忽略", str(exc))
            return
        self.changed = True
        self.position += 1
        self._show_current()


class KnowledgeBaseDialog(QDialog):
    knowledge_changed = Signal()

    def __init__(self, base_dir: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.service = KnowledgeBaseService(base_dir)
        self._thread: QThread | None = None
        self._worker: KnowledgeWorker | None = None
        self._operation_succeeded = False
        self._completion_message = ""
        self.setWindowTitle("课程知识库管理")
        self.setAcceptDrops(True)
        self.resize(960, 580)
        self.setMinimumSize(760, 460)
        apply_dialog_theme(self)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("课程教材与课件")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        hint = QLabel(
            "导入时先进行哈希去重，再识别目录、页码关系并建立逐页索引。"
            "从知识库移除的文件会进入本地回收目录，不会删除原始教材。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 7)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(
            ["文件名", "学科", "类型", "页数", "文本层", "状态", "提示"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 280)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 100)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton("＋ 导入教材/课件")
        mark_button(self.add_button, "primary")
        self.add_button.clicked.connect(self._choose_files)
        self.remove_button = QPushButton("从知识库移除")
        mark_button(self.remove_button, "danger")
        self.remove_button.clicked.connect(self._remove_selected)
        self.subject_button = QPushButton("设置学科")
        mark_button(self.subject_button, "quiet")
        self.subject_button.clicked.connect(self._set_selected_subject)
        self.visual_button = QPushButton("识别图片页")
        mark_button(self.visual_button, "quiet")
        self.visual_button.clicked.connect(self._index_selected_visuals)
        self.review_button = QPushButton("复核图片文字")
        mark_button(self.review_button, "quiet")
        self.review_button.clicked.connect(self._review_selected_visuals)
        self.cancel_button = QPushButton("取消当前识别")
        mark_button(self.cancel_button, "danger")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_current)
        self.refresh_button = QPushButton("刷新")
        mark_button(self.refresh_button, "quiet")
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.subject_button)
        actions.addWidget(self.visual_button)
        actions.addWidget(self.review_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        self.close_button = QPushButton("完成")
        mark_button(self.close_button, "primary")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

        status_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedWidth(180)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("StatusText")
        status_row.addWidget(self.progress)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

    @Slot()
    def refresh(self) -> None:
        self.table.setRowCount(0)
        for document in self.service.list_documents():
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(document.original_name)
            name_item.setData(Qt.UserRole, document.document_id)
            values = [
                name_item,
                QTableWidgetItem("、".join(document.subject_names) or "待识别"),
                QTableWidgetItem(document.document_type),
                QTableWidgetItem(str(document.page_count)),
                QTableWidgetItem(document.text_layer),
                QTableWidgetItem(document.status),
                QTableWidgetItem("；".join([
                    *document.warnings,
                    *(
                        [f"已识别 {document.visual_indexed_pages} 张图片页"]
                        if document.visual_indexed_pages else []
                    ),
                ])),
            ]
            for column, item in enumerate(values):
                self.table.setItem(row, column, item)
        self.status_label.setText(f"共 {self.table.rowCount()} 份课程资料")

    @Slot()
    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "导入课程教材或课件",
            "",
            "课程资料 (*.pdf *.pptx);;PDF 文件 (*.pdf);;PowerPoint 课件 (*.pptx)",
        )
        if paths:
            self.import_files(paths)

    def import_files(self, paths: list[str] | tuple[str, ...]) -> None:
        pdfs = [
            str(Path(path))
            for path in paths
            if Path(path).is_file()
            and Path(path).suffix.lower() in {".pdf", ".pptx"}
        ]
        if not pdfs:
            self.status_label.setText("只支持导入 PDF 或 PPTX 课程资料")
            return
        if self._thread is not None:
            self.status_label.setText("当前正在建立索引，请完成后再导入")
            return
        self._start_worker("import", pdfs)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if any(
            path.is_file() and path.suffix.lower() in {".pdf", ".pptx"}
            for path in paths
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        self.import_files(paths)
        event.acceptProposedAction()

    @Slot()
    def _set_selected_subject(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("请先选择一份资料")
            return
        item = self.table.item(row, 0)
        document_id = str(item.data(Qt.UserRole))
        options = [
            option for option in subject_options() if option["id"] != "auto"
        ]
        labels = [option["display_name"] for option in options]
        current_label = self.table.item(row, 1).text()
        current_index = labels.index(current_label) if current_label in labels else 0
        selected_label, accepted = QInputDialog.getItem(
            self,
            "设置资料学科",
            f"为“{item.text()}”选择主要学科：",
            labels,
            current_index,
            False,
        )
        if not accepted:
            return
        subject_id = next(
            option["id"] for option in options
            if option["display_name"] == selected_label
        )
        try:
            self.service.set_document_subjects(document_id, [subject_id])
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "设置学科失败", str(exc))
            return
        self.knowledge_changed.emit()
        self.refresh()
        self.status_label.setText(f"已将资料归入：{selected_label}")

    @Slot()
    def _index_selected_visuals(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("请先选择一份 PPTX 课件")
            return
        item = self.table.item(row, 0)
        document_id = str(item.data(Qt.UserRole))
        try:
            plan = self.service.visual_index_plan(document_id)
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "无法识别图片页", str(exc))
            return
        if not plan.candidate_count:
            QMessageBox.information(
                self,
                "无需识别",
                "这份资料没有待识别的图片型幻灯片，或它不是 PPTX 课件。",
            )
            return
        answer = QMessageBox.question(
            self,
            "发送图片页进行识别",
            (
                f"将在“{plan.original_name}”中选取 {plan.candidate_count} 张"
                "以图片或图表为主、文字层不足的幻灯片，逐张把整页画面发送给你已配置的"
                "视觉模型。\n\n不会发送其他课程资料；低置信度结果不会进入回答证据。"
                "是否继续？"
            ),
        )
        if answer != QMessageBox.Yes:
            return
        try:
            vision_client = build_vision_client(self.service.base_dir)
        except (RuntimeConfigurationError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "视觉模型不可用", str(exc))
            return
        self._start_worker("visual_index", (document_id, vision_client))

    @Slot()
    def _review_selected_visuals(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("请先选择一份 PPTX 课件")
            return
        item = self.table.item(row, 0)
        document_id = str(item.data(Qt.UserRole))
        reviews = self.service.list_visual_reviews(document_id)
        if not reviews:
            QMessageBox.information(
                self, "无需复核", "这份课件当前没有待复核的图片文字。"
            )
            return
        dialog = VisualReviewDialog(self.service, document_id, self)
        dialog.exec()
        if dialog.changed:
            self.knowledge_changed.emit()
            self.refresh()
            self.status_label.setText("图片文字复核结果已保存")

    @Slot()
    def _cancel_current(self) -> None:
        if self._worker is None or self._worker.action != "visual_index":
            return
        self._worker.request_cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在安全停止；已完成的页面会保留，下次可继续……")

    @Slot()
    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("请先选择一份资料")
            return
        item = self.table.item(row, 0)
        document_id = item.data(Qt.UserRole)
        answer = QMessageBox.question(
            self,
            "移除课程资料",
            f"确定将“{item.text()}”从知识库移除吗？\n原始文件不会被删除，索引副本会移入本地回收目录。",
        )
        if answer == QMessageBox.Yes:
            self._start_worker("remove", document_id)

    def _start_worker(self, action: str, payload: Any) -> None:
        if self._thread is not None:
            return
        self._operation_succeeded = False
        self._completion_message = ""
        thread = QThread(self)
        worker = KnowledgeWorker(self.service, action, payload)
        self._worker = worker
        self._set_busy(True)
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
        thread.start()

    @Slot(str, int, int)
    def _on_progress(self, message: str, current: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(message)

    @Slot(object)
    def _on_success(self, _: Any) -> None:
        self._operation_succeeded = True
        if hasattr(_, "indexed") and hasattr(_, "review_required"):
            prefix = "图片页识别已取消" if getattr(_, "cancelled", False) else "图片页识别完成"
            self._completion_message = (
                f"{prefix}：写入 {_.indexed} 张，待复核 {_.review_required} 张，"
                f"失败 {_.failed} 张"
            )
        else:
            self._completion_message = "知识库操作完成"
        self.status_label.setText(self._completion_message)

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        self.status_label.setText("知识库操作失败")
        QMessageBox.warning(self, "知识库操作失败", message)

    @Slot()
    def _on_finished(self) -> None:
        changed = self._operation_succeeded
        self._thread = None
        self._worker = None
        self._set_busy(False)
        self.refresh()
        if self._completion_message:
            self.status_label.setText(self._completion_message)
        if changed:
            self.knowledge_changed.emit()

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.add_button,
            self.remove_button,
            self.subject_button,
            self.visual_button,
            self.review_button,
            self.refresh_button,
            self.close_button,
            self.table,
        ):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(
            busy and self._worker is not None and self._worker.action == "visual_index"
        )
        if busy:
            self.progress.setRange(0, 0)
            self.status_label.setText("正在建立课程资料索引……")
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "索引仍在运行", "请等待当前知识库操作完成。")
            event.ignore()
            return
        event.accept()
