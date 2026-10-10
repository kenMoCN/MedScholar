"""Dynamic model registry editor for the PySide desktop application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from config_loader import LoadedConfig, load_legacy_config
from dynamic_config import (
    dynamic_config_exists,
    editable_models,
    load_dynamic_config,
    save_dynamic_config,
)
from dialog_theme import apply_dialog_theme, mark_button
from model_router import supports_deepseek_flash_vision


ROLE_LABELS = {
    "reasoning": "答案推理",
    "planning": "任务规划",
    "verification": "答案核验",
    "vision": "图片识别",
}


class ModelSettingsDialog(QDialog):
    configuration_saved = Signal()

    def __init__(self, base_dir: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir).resolve()
        self.loaded = self._load_source()
        self._route_values = dict(self.loaded.config.get("model_routes", {}))
        self.setWindowTitle("模型注册与角色路由")
        self.resize(1180, 620)
        self.setMinimumSize(900, 520)
        apply_dialog_theme(self)
        self._build_ui()
        self._load_rows()
        self._refresh_route_choices()

    def _load_source(self) -> LoadedConfig:
        if dynamic_config_exists(self.base_dir):
            return load_dynamic_config(self.base_dir)
        legacy_path = self.base_dir / "config.env"
        if legacy_path.is_file():
            return load_legacy_config(legacy_path)
        return LoadedConfig(
            config={
                "schema_version": "2.0",
                "model_registry": {},
                "model_routes": {},
                "user_defaults": {
                    "initial_proficiency_label": "beginner",
                    "interaction_style": "auto",
                },
                "document_defaults": {},
                "permission_defaults": {},
                "ui": {"theme": "system"},
            }
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("模型注册表")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        hint = QLabel(
            "可添加任意 OpenAI Chat Completions 兼容服务。API Key 单独保存，"
            "不会写入公开模型配置。能力用英文逗号分隔。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("DialogHint")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 8)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(
            ["启用", "模型ID", "供应商", "模型名称", "API地址", "协议", "能力", "API Key"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        for column, width in enumerate((55, 150, 100, 155, 160, 110, 170, 200)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        model_buttons = QHBoxLayout()
        add_button = QPushButton("＋ 添加模型")
        mark_button(add_button, "primary")
        add_button.clicked.connect(lambda: self._add_row({}))
        remove_button = QPushButton("删除选中模型")
        mark_button(remove_button, "danger")
        remove_button.clicked.connect(self._remove_selected)
        refresh_button = QPushButton("刷新角色候选")
        mark_button(refresh_button, "quiet")
        refresh_button.clicked.connect(self._refresh_route_choices)
        model_buttons.addWidget(add_button)
        model_buttons.addWidget(remove_button)
        model_buttons.addWidget(refresh_button)
        model_buttons.addStretch()
        layout.addLayout(model_buttons)

        routes_title = QLabel("角色路由")
        routes_title.setObjectName("SectionTitle")
        layout.addWidget(routes_title)
        route_form = QFormLayout()
        self.route_combos: dict[str, QComboBox] = {}
        for role, label in ROLE_LABELS.items():
            combo = QComboBox()
            combo.setMinimumWidth(260)
            if role == "vision":
                combo.setToolTip("可选豆包视觉模型，或使用已配置的 DeepSeek Flash 模型与其 API Key")
            self.route_combos[role] = combo
            route_form.addRow(f"{label} ({role})", combo)
        layout.addLayout(route_form)
        vision_hint = QLabel(
            "图片识别可选 DeepSeek Flash（使用该模型已配置的 API Key）或豆包视觉模型；"
            "选择“未配置”则关闭图片识别。"
        )
        vision_hint.setWordWrap(True)
        vision_hint.setObjectName("DialogHint")
        layout.addWidget(vision_hint)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_button = QPushButton("取消")
        mark_button(cancel_button, "quiet")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("保存并应用")
        mark_button(save_button, "primary")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save)
        footer.addWidget(cancel_button)
        footer.addWidget(save_button)
        layout.addLayout(footer)

    def _load_rows(self) -> None:
        records = editable_models(self.loaded)
        for record in records:
            self._add_row(record)
        if not records:
            self._add_row(
                {
                    "model_id": "reasoning_main",
                    "api_protocol": "openai_compatible",
                    "capabilities": ["text", "structured_output"],
                    "enabled": True,
                }
            )

    def _add_row(self, record: dict[str, Any]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setRowHeight(row, 54)
        enabled = QCheckBox()
        enabled.setChecked(bool(record.get("enabled", True)))
        enabled.toggled.connect(self._refresh_route_choices)
        self.table.setCellWidget(row, 0, enabled)
        for column, key in ((1, "model_id"), (2, "provider"), (3, "model_name"), (4, "api_base")):
            editor = QLineEdit(str(record.get(key, "")))
            editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            editor.setCursorPosition(0)
            editor.setToolTip(editor.text())
            editor.textChanged.connect(editor.setToolTip)
            if key in {"model_id", "provider", "model_name"}:
                editor.editingFinished.connect(self._refresh_route_choices)
            self.table.setCellWidget(row, column, editor)
        protocol = QComboBox()
        protocol.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        protocol.addItem("标准兼容", "openai_compatible")
        protocol.addItem("厂商兼容", "vendor_or_openai_compatible")
        protocol.setToolTip("标准兼容：OpenAI Chat Completions；厂商兼容：兼容该接口的厂商服务")
        selected_protocol = str(record.get("api_protocol", "openai_compatible"))
        selected_index = protocol.findData(selected_protocol)
        protocol.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.table.setCellWidget(row, 5, protocol)
        capabilities = record.get("capabilities", ["text", "structured_output"])
        capability_editor = QLineEdit(",".join(capabilities))
        capability_editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        capability_editor.setCursorPosition(0)
        capability_editor.setToolTip(capability_editor.text())
        capability_editor.textChanged.connect(capability_editor.setToolTip)
        capability_editor.editingFinished.connect(self._refresh_route_choices)
        self.table.setCellWidget(row, 6, capability_editor)
        api_key = QLineEdit(str(record.get("api_key", "")))
        api_key.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        api_key.setEchoMode(QLineEdit.Password)
        api_key.setPlaceholderText("仅保存在本机密钥文件")
        self.table.setCellWidget(row, 7, api_key)

    @Slot()
    def _remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        self._refresh_route_choices()

    @Slot()
    def _refresh_route_choices(self) -> None:
        model_ids = []
        vision_models = []
        for row in range(self.table.rowCount()):
            value = self._line_text(row, 1).strip().lower()
            if value and value not in model_ids:
                model_ids.append(value)
            if not value or not self.table.cellWidget(row, 0).isChecked():
                continue
            provider = self._line_text(row, 2)
            model_name = self._line_text(row, 3)
            capabilities = {part.strip() for part in self._line_text(row, 6).split(",")}
            if "vision" in capabilities or supports_deepseek_flash_vision(provider, model_name):
                label = (
                    f"DeepSeek Flash · {value}"
                    if supports_deepseek_flash_vision(provider, model_name)
                    else f"{provider or '视觉模型'} · {value}"
                )
                vision_models.append((label, value))
        for role, combo in self.route_combos.items():
            current = combo.currentData() if combo.count() else self._route_values.get(role, "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("未配置", "")
            if role == "vision":
                for label, model_id in vision_models:
                    combo.addItem(label, model_id)
            else:
                for model_id in model_ids:
                    combo.addItem(model_id, model_id)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    @Slot()
    def _save(self) -> None:
        self._refresh_route_choices()
        models = []
        for row in range(self.table.rowCount()):
            capabilities = [
                item.strip() for item in self._line_text(row, 6).split(",") if item.strip()
            ]
            models.append(
                {
                    "enabled": self.table.cellWidget(row, 0).isChecked(),
                    "model_id": self._line_text(row, 1),
                    "provider": self._line_text(row, 2),
                    "model_name": self._line_text(row, 3),
                    "api_base": self._line_text(row, 4),
                    "api_protocol": self.table.cellWidget(row, 5).currentData(),
                    "capabilities": capabilities,
                    "api_key": self._line_text(row, 7),
                }
            )
        routes = {role: combo.currentData() for role, combo in self.route_combos.items()}
        vision_model_id = routes.get("vision")
        if vision_model_id:
            for model in models:
                if model["model_id"].strip().lower() != vision_model_id:
                    continue
                if supports_deepseek_flash_vision(model["provider"], model["model_name"]):
                    model["capabilities"] = sorted(set(model["capabilities"]) | {"vision"})
                break
        try:
            save_dynamic_config(
                self.base_dir,
                models=models,
                routes=routes,
                base_config=self.loaded.config,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "配置无法保存", str(exc))
            return
        self.configuration_saved.emit()
        self.accept()

    def _line_text(self, row: int, column: int) -> str:
        widget = self.table.cellWidget(row, column)
        return widget.text() if isinstance(widget, QLineEdit) else ""
