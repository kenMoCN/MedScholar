"""Shared visual language for secondary application dialogs."""

from __future__ import annotations

from typing import Any


DIALOG_QSS = """
QDialog {
    background: #F4F7FA;
    color: #172B3A;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QLabel { background: transparent; }
QLabel#DialogTitle { color: #122A38; font-size: 21px; font-weight: 800; }
QLabel#DialogHint { color: #647985; font-size: 12px; }
QLabel#SectionTitle { color: #244553; font-size: 15px; font-weight: 700; }
QLabel#StatusText { color: #667B86; font-size: 12px; }
QLabel#InfoBanner {
    background: #EAF7F3; color: #176859; border: 1px solid #C5E9E0;
    border-radius: 9px; padding: 9px 11px;
}
QLabel#MetricCard {
    background: white; color: #294754; border: 1px solid #DEE7EC;
    border-radius: 9px; padding: 12px 14px; font-weight: 700;
}
QLineEdit, QTextEdit, QComboBox {
    background: white; color: #193440; border: 1px solid #D2DEE4;
    border-radius: 8px; padding: 7px 9px; selection-background-color: #BCE9DF;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #159780; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    background: #EDF2F4; color: #96A5AC;
}
QComboBox::drop-down { border: none; width: 24px; }
QCheckBox { spacing: 7px; color: #3C5662; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #AFC1C9;
    border-radius: 4px; background: white;
}
QCheckBox::indicator:checked { background: #159780; border-color: #159780; }
QPushButton {
    min-height: 20px; background: #E8EFF2; color: #284653; border: none;
    border-radius: 8px; padding: 8px 14px; font-weight: 600;
}
QPushButton:hover { background: #D9E5E9; }
QPushButton#PrimaryButton { background: #138A77; color: white; font-weight: 700; }
QPushButton#PrimaryButton:hover { background: #087563; }
QPushButton#DangerButton { background: #FFF0F1; color: #A13C48; }
QPushButton#DangerButton:hover { background: #FADDE0; color: #842C36; }
QPushButton#QuietButton { background: transparent; color: #54707D; }
QPushButton#QuietButton:hover { background: #E9F0F3; }
QPushButton:disabled { background: #E7ECEF; color: #A1AEB4; }
QTableWidget {
    background: white; alternate-background-color: #F7FAFB; color: #213C49;
    border: 1px solid #DCE6EB; border-radius: 10px; gridline-color: #E7EEF1;
    selection-background-color: #DDF3EE; selection-color: #155E52;
}
QTableWidget::item { padding: 6px; border: none; }
QTableWidget::item:selected { background: #DDF3EE; color: #155E52; }
QHeaderView::section {
    background: #EDF3F5; color: #506873; border: none;
    border-right: 1px solid #DDE6EA; border-bottom: 1px solid #D7E2E7;
    padding: 8px 6px; font-weight: 700;
}
QTableCornerButton::section { background: #EDF3F5; border: none; }
QProgressBar {
    background: #DFE8EC; border: none; border-radius: 3px;
    min-height: 6px; max-height: 6px;
}
QProgressBar::chunk { background: #19A58C; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #C5D3D9; min-height: 28px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip {
    background: #173744; color: white; border: none; padding: 5px;
}
"""


def apply_dialog_theme(dialog: Any) -> None:
    dialog.setStyleSheet(DIALOG_QSS)


def mark_button(button: Any, role: str) -> None:
    names = {
        "primary": "PrimaryButton",
        "danger": "DangerButton",
        "quiet": "QuietButton",
    }
    button.setObjectName(names[role])
