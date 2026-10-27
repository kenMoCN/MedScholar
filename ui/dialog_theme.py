"""DeepSeek-styled visual language and animations for secondary application dialogs."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QStyle, QStyleOptionButton, QWidget


# ==============================================================================
# DeepSeek Design Tokens
# ==============================================================================
DEEPSEEK_SIDEBAR_BG = "#F8F9FA"
DEEPSEEK_MAIN_BG = "#FFFFFF"
DEEPSEEK_ACCENT_BLUE = "#4D6BFE"
DEEPSEEK_ACCENT_HOVER = "#3B5BFE"
DEEPSEEK_ACCENT_PRESSED = "#2A4AF0"
DEEPSEEK_ACTIVE_TINT = "#EEF2FF"
DEEPSEEK_BORDER = "#E5E7EB"
DEEPSEEK_BORDER_FOCUS = "#4D6BFE"
DEEPSEEK_TEXT_PRIMARY = "#111827"
DEEPSEEK_TEXT_SECONDARY = "#4B5563"
DEEPSEEK_TEXT_MUTED = "#9CA3AF"


DIALOG_QSS = """
QDialog {
    background: #FFFFFF;
    color: #111827;
    font-family: "Microsoft YaHei UI", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
}
QLabel { background: transparent; color: #111827; }
QLabel#DialogTitle { color: #111827; font-size: 18px; font-weight: 800; letter-spacing: -0.2px; }
QLabel#DialogHint { color: #4B5563; font-size: 13px; }
QLabel#SectionTitle { color: #111827; font-size: 14px; font-weight: 700; }
QLabel#StatusText { color: #4B5563; font-size: 12px; }
QLabel#InfoBanner {
    background: #EEF2FF; color: #4D6BFE; border: 1px solid #C7D2FE;
    border-radius: 8px; padding: 10px 14px; font-weight: 500;
}
QLabel#MetricCard {
    background: #F8F9FA; color: #111827; border: 1px solid #E5E7EB;
    border-radius: 8px; padding: 12px 14px; font-weight: 700;
}
QLineEdit, QTextEdit, QComboBox {
    background: #FFFFFF; color: #111827; border: 1px solid #E5E7EB;
    border-radius: 6px; padding: 7px 10px; selection-background-color: #EEF2FF; selection-color: #4D6BFE;
}
QLineEdit:hover, QTextEdit:hover, QComboBox:hover { border-color: #D1D5DB; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1.5px solid #4D6BFE; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    background: #F9FAFB; color: #9CA3AF; border: 1px solid #E5E7EB;
}
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 7px; color: #374151; background: transparent; }
QCheckBox::indicator {
    width: 15px; height: 15px; border: 1px solid #D1D5DB;
    border-radius: 3px; background: #FFFFFF;
}
QCheckBox::indicator:hover { border-color: #4D6BFE; }
QCheckBox::indicator:checked { background: #4D6BFE; border-color: #4D6BFE; }
QPushButton {
    min-height: 20px; background: #F3F4F6; color: #4B5563; border: 1px solid #E5E7EB;
    border-radius: 6px; padding: 7px 15px; font-weight: 600; font-size: 13px;
}
QPushButton:hover { background: #E5E7EB; color: #111827; }
QPushButton:pressed { background: #D1D5DB; }
QPushButton#PrimaryButton {
    background: #4D6BFE; color: #FFFFFF; font-weight: 700; border: none;
}
QPushButton#PrimaryButton:hover { background: #3B5BFE; }
QPushButton#PrimaryButton:pressed { background: #2A4AF0; }
QPushButton#DangerButton { background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; }
QPushButton#DangerButton:hover { background: #FEE2E2; color: #B91C1C; }
QPushButton#QuietButton { background: transparent; color: #4B5563; border: none; }
QPushButton#QuietButton:hover { background: #F3F4F6; color: #111827; }
QPushButton#PillButton {
    background: #F8F9FA; color: #4B5563; border: 1px solid #E5E7EB;
    border-radius: 9999px; padding: 5px 14px; font-size: 12px; font-weight: 600;
}
QPushButton#PillButton:hover { background: #EEF2FF; color: #4D6BFE; border-color: #C7D2FE; }
QPushButton#PillButton:checked { background: #EEF2FF; color: #4D6BFE; border: 1px solid #4D6BFE; font-weight: 700; }
QPushButton:disabled { background: #F3F4F6; color: #9CA3AF; border-color: #E5E7EB; }
QTableWidget {
    background: #FFFFFF; alternate-background-color: #F9FAFB; color: #111827;
    border: 1px solid #E5E7EB; border-radius: 6px; gridline-color: #F3F4F6;
    selection-background-color: #EEF2FF; selection-color: #4D6BFE;
}
QTableWidget::item { padding: 8px 10px; border: none; }
QTableWidget::item:selected { background: #EEF2FF; color: #4D6BFE; font-weight: 600; }
QHeaderView::section {
    background: #F9FAFB; color: #4B5563; border: none;
    border-right: 1px solid #E5E7EB; border-bottom: 1px solid #E5E7EB;
    padding: 8px 8px; font-weight: 700;
}
QTableCornerButton::section { background: #F9FAFB; border: none; }
QProgressBar {
    background: #E5E7EB; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px;
}
QProgressBar::chunk { background: #4D6BFE; border-radius: 2px; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
QScrollBar::handle:vertical { background: #E5E7EB; min-height: 28px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: #9CA3AF; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip {
    background: #111827; color: #FFFFFF; border: none; padding: 6px 9px; border-radius: 4px;
}
"""

DARK_DIALOG_QSS = """
QDialog { background: #171B22; color: #F4F4F5; }
QLabel, QLabel#DialogTitle, QLabel#SectionTitle { color: #F4F4F5; }
QLabel#DialogHint, QLabel#StatusText { color: #AEB6C5; }
QLabel#InfoBanner { background: #222B48; color: #9BA8FF; border-color: #3F4B78; }
QLabel#MetricCard { background: #252B36; color: #F4F4F5; border-color: #303644; }
QLineEdit, QTextEdit, QComboBox {
    background: #1D222C; color: #F4F4F5; border-color: #303644;
    selection-background-color: #3F4B78; selection-color: #FFFFFF;
}
QLineEdit:hover, QTextEdit:hover, QComboBox:hover { border-color: #566079; }
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    background: #171B22; color: #8993A4; border-color: #303644;
}
QCheckBox { color: #E4E4E7; }
QCheckBox::indicator { background: #1D222C; border-color: #566079; }
QPushButton { background: #252B36; color: #E4E4E7; border-color: #303644; }
QPushButton:hover, QPushButton#QuietButton:hover { background: #303848; color: #FFFFFF; }
QPushButton#QuietButton { color: #AEB6C5; }
QPushButton#PillButton { background: #252B36; color: #E4E4E7; border-color: #303644; }
QPushButton#PillButton:hover, QPushButton#PillButton:checked {
    background: #222B48; color: #9BA8FF; border-color: #5B6CFF;
}
QPushButton:disabled { background: #1D222C; color: #8993A4; border-color: #303644; }
QTableWidget { background: #1D222C; alternate-background-color: #252B36;
    color: #F4F4F5; border-color: #303644; gridline-color: #303644; }
QHeaderView::section { background: #252B36; color: #E4E4E7; border-color: #303644; }
QTableCornerButton::section { background: #252B36; }
QScrollBar::handle:vertical { background: #485164; }
QToolTip { background: #303848; color: #FFFFFF; }
"""


class ScalePressButton(QPushButton):
    """Button with DeepSeek 0.95 -> 1.0 tactile press micro-scale animation."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._scale = 1.0
        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.setDuration(120)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scale_anim.valueChanged.connect(self._on_scale_changed)

    def _on_scale_changed(self, val: float) -> None:
        self._scale = val
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if self.isEnabled() and event.button() == Qt.LeftButton:
            self._scale_anim.stop()
            self._scale_anim.setStartValue(self._scale)
            self._scale_anim.setEndValue(0.95)
            self._scale_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if self.isEnabled():
            self._scale_anim.stop()
            self._scale_anim.setStartValue(self._scale)
            self._scale_anim.setEndValue(1.0)
            self._scale_anim.start()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: Any) -> None:
        if abs(self._scale - 1.0) < 0.005:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        painter.translate(w / 2.0, h / 2.0)
        painter.scale(self._scale, self._scale)
        painter.translate(-w / 2.0, -h / 2.0)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        self.style().drawControl(QStyle.CE_PushButton, opt, painter, self)


class DeepSeekPillButton(ScalePressButton):
    """DeepSeek full capsule pill button (border-radius: 9999px) with active #EEF2FF state."""

    def __init__(self, text: str = "", parent: QWidget | None = None, *, checkable: bool = False) -> None:
        super().__init__(text, parent)
        self.setObjectName("PillButton")
        self.setCheckable(checkable)
        self.setFixedHeight(30)


def apply_dialog_theme(dialog: Any, *, animate: bool = True) -> None:
    parent = dialog.parent() if hasattr(dialog, "parent") else None
    while parent is not None and not hasattr(parent, "_effective_theme"):
        parent = parent.parent() if hasattr(parent, "parent") else None
    is_dark = parent is not None and parent._effective_theme == "dark"
    dialog.setStyleSheet(DIALOG_QSS + (DARK_DIALOG_QSS if is_dark else ""))
    if animate and hasattr(dialog, "setGraphicsEffect"):
        try:
            effect = QGraphicsOpacityEffect(dialog)
            dialog.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", dialog)
            anim.setDuration(200)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: dialog.setGraphicsEffect(None))
            dialog._fade_anim = anim
            dialog._fade_effect = effect
            anim.start()
        except Exception:
            pass


def mark_button(button: Any, role: str) -> None:
    names = {
        "primary": "PrimaryButton",
        "danger": "DangerButton",
        "quiet": "QuietButton",
        "pill": "PillButton",
    }
    button.setObjectName(names.get(role, "PrimaryButton"))
