"""PySide dialog for submitting, reviewing, and activating local skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from agent_orchestrator import AgentRunResult
from skill_memory import (
    SkillKind,
    SkillStatus,
    SkillStore,
    evidence_anchors_from_result,
)
from dialog_theme import apply_dialog_theme, mark_button


STATUS_LABELS = {
    SkillStatus.PENDING.value: "待审核",
    SkillStatus.ACTIVE.value: "已启用",
    SkillStatus.REJECTED.value: "已驳回",
}


class SkillReviewDialog(QDialog):
    skills_changed = Signal()

    def __init__(
        self,
        base_dir: str | Path,
        *,
        result: AgentRunResult | None = None,
        question: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = SkillStore(Path(base_dir) / "data" / "skills.json")
        self.result = result
        self.anchors = evidence_anchors_from_result(result)
        self.setWindowTitle("纠错反馈与 Skill 审核")
        self.resize(980, 680)
        self.setMinimumSize(800, 560)
        apply_dialog_theme(self)
        self._build_ui(question)
        self.refresh()

    def _build_ui(self, question: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)
        title = QLabel("把有价值的纠错沉淀为可审核 Skill")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        safety = QLabel(
            "提交后仅进入待审核区，不会立即影响回答。医学知识必须绑定可追溯证据，"
            "审核通过后也只能作为提示，不能替代当前问题的证据核验。"
        )
        safety.setWordWrap(True)
        safety.setObjectName("DialogHint")
        layout.addWidget(safety)

        form = QFormLayout()
        self.question_edit = QLineEdit(question)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("医学知识纠错", SkillKind.MEDICAL.value)
        self.kind_combo.addItem("答题流程或表达偏好", SkillKind.WORKFLOW.value)
        self.trigger_edit = QLineEdit()
        self.trigger_edit.setPlaceholderText("可选，用逗号分隔；留空时从原问题自动提取")
        self.guidance_edit = QTextEdit()
        self.guidance_edit.setPlaceholderText(
            "写明正确内容或以后应采用的答题策略。请勿填写 API Key、隐私信息或隐藏推理。"
        )
        self.guidance_edit.setFixedHeight(100)
        form.addRow("原问题", self.question_edit)
        form.addRow("Skill 类型", self.kind_combo)
        form.addRow("触发词", self.trigger_edit)
        form.addRow("纠错/指导", self.guidance_edit)
        layout.addLayout(form)

        evidence_text = (
            f"本次已带入 {len(self.anchors)} 条通过课程来源检查的证据锚点。"
            if self.anchors
            else "本次没有可绑定证据；医学类候选可以保存，但不能通过审核。"
        )
        self.evidence_label = QLabel(evidence_text)
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setObjectName("InfoBanner")
        layout.addWidget(self.evidence_label)
        submit_row = QHBoxLayout()
        self.submit_button = QPushButton("提交为待审核 Skill")
        mark_button(self.submit_button, "primary")
        self.submit_button.clicked.connect(self._submit_candidate)
        submit_row.addWidget(self.submit_button)
        submit_row.addStretch()
        layout.addLayout(submit_row)

        self.table = QTableWidget(0, 6)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(
            ["状态", "类型", "触发词", "纠错/指导", "证据", "创建时间"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 300)
        self.table.setColumnWidth(4, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        review_row = QHBoxLayout()
        self.review_note = QLineEdit()
        self.review_note.setPlaceholderText("可选：填写审核备注")
        self.approve_button = QPushButton("审核通过并启用")
        self.reject_button = QPushButton("驳回")
        self.refresh_button = QPushButton("刷新")
        self.close_button = QPushButton("完成")
        mark_button(self.approve_button, "primary")
        mark_button(self.reject_button, "danger")
        mark_button(self.refresh_button, "quiet")
        mark_button(self.close_button, "quiet")
        self.approve_button.clicked.connect(self._approve_selected)
        self.reject_button.clicked.connect(self._reject_selected)
        self.refresh_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.accept)
        review_row.addWidget(self.review_note, 1)
        review_row.addWidget(self.approve_button)
        review_row.addWidget(self.reject_button)
        review_row.addWidget(self.refresh_button)
        review_row.addWidget(self.close_button)
        layout.addLayout(review_row)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("StatusText")
        layout.addWidget(self.status_label)

    @Slot()
    def _submit_candidate(self) -> None:
        raw_terms = self.trigger_edit.text().strip()
        terms = tuple(
            item.strip() for item in re.split(r"[,，;；\n]+", raw_terms)
            if item.strip()
        )
        try:
            record = self.store.create_candidate(
                question=self.question_edit.text(),
                guidance=self.guidance_edit.toPlainText(),
                kind=self.kind_combo.currentData(),
                trigger_terms=terms,
                evidence=self.anchors,
            )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "无法提交 Skill", str(exc))
            return
        self.guidance_edit.clear()
        self.refresh()
        self.status_label.setText(f"已提交 {record.skill_id}，等待人工审核")
        self.skills_changed.emit()

    @Slot()
    def _approve_selected(self) -> None:
        skill_id = self._selected_skill_id()
        if not skill_id:
            self.status_label.setText("请先选择一条待审核 Skill")
            return
        try:
            record = self.store.approve(
                skill_id, review_note=self.review_note.text()
            )
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "无法通过审核", str(exc))
            return
        self.refresh()
        self.status_label.setText(f"{record.skill_id} 已启用")
        self.skills_changed.emit()

    @Slot()
    def _reject_selected(self) -> None:
        skill_id = self._selected_skill_id()
        if not skill_id:
            self.status_label.setText("请先选择一条待审核 Skill")
            return
        try:
            record = self.store.reject(
                skill_id, review_note=self.review_note.text()
            )
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(self, "无法驳回", str(exc))
            return
        self.refresh()
        self.status_label.setText(f"{record.skill_id} 已驳回")
        self.skills_changed.emit()

    def _selected_skill_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        value = self.table.item(row, 0).data(Qt.UserRole)
        return str(value) if value else None

    @Slot()
    def refresh(self) -> None:
        self.table.setRowCount(0)
        for record in self.store.list_records():
            row = self.table.rowCount()
            self.table.insertRow(row)
            status_item = QTableWidgetItem(STATUS_LABELS.get(record.status, record.status))
            status_item.setData(Qt.UserRole, record.skill_id)
            values = [
                status_item,
                QTableWidgetItem("医学知识" if record.kind == SkillKind.MEDICAL.value else "流程偏好"),
                QTableWidgetItem("、".join(record.trigger_terms)),
                QTableWidgetItem(record.guidance[:160]),
                QTableWidgetItem(str(len(record.evidence))),
                QTableWidgetItem(record.created_at[:19].replace("T", " ")),
            ]
            for column, item in enumerate(values):
                self.table.setItem(row, column, item)
        self.status_label.setText(f"共 {self.table.rowCount()} 条 Skill 记录")
