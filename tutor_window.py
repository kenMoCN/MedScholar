"""PySide6 desktop shell for the medical learning companion."""

from __future__ import annotations

import html
import json
import mimetypes
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_runtime import ApprovalRequest, ProgressUpdate, TutorApplicationService
from conversation_store import (
    ConversationMessage,
    ConversationStore,
    conversation_context_for_session,
)
from evidence_labels import friendly_resource_intro
from execution_control import RunCancelled, RunTimedOut, ensure_run_active
from research_evidence import ResearchFilters
from dialog_theme import (
    DEEPSEEK_ACCENT_BLUE,
    DEEPSEEK_ACTIVE_TINT,
    DEEPSEEK_BORDER,
    DEEPSEEK_BORDER_FOCUS,
    DEEPSEEK_MAIN_BG,
    DEEPSEEK_SIDEBAR_BG,
    DEEPSEEK_TEXT_MUTED,
    DEEPSEEK_TEXT_PRIMARY,
    DEEPSEEK_TEXT_SECONDARY,
    DeepSeekPillButton,
    ScalePressButton,
)
from resource_dialog import ResourceDialog
from ui_presenter import PresentedResult, present_agent_result


def _logo_path_for_module(module_path: str | Path) -> Path:
    """Locate the logo beside this module in both source and frozen builds."""
    return Path(module_path).resolve().with_name("logo.png")


APP_QSS = """
QWidget {
    background: #FAFAFA;
    color: #18181B;
    font-family: "Microsoft YaHei UI", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
}
QLabel { background: transparent; color: #18181B; }

/* Sidebar (Collapsible #F8F9FA) */
QFrame#Sidebar {
    background: #F8F9FA;
    border: none;
    border-right: 1px solid #EEEEEE;
    border-radius: 0px;
}
QFrame#Sidebar QLabel { background: transparent; color: #18181B; }
QLabel#BrandMark {
    background: #5B6CFF;
    color: #FFFFFF;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 800;
    qproperty-alignment: AlignCenter;
}
QLabel#BrandTitle { color: #18181B; font-size: 15px; font-weight: 800; letter-spacing: -0.2px; }
QLabel#BrandSubtitle { color: #71717A; font-size: 11px; font-weight: 500; }
QLabel#SidebarSection {
    color: #A1A1AA;
    font-size: 11px;
    font-weight: 700;
    padding: 8px 4px 2px 4px;
    letter-spacing: 0.5px;
}
QPushButton#SidebarToggle {
    background: transparent;
    color: #52525B;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
    padding: 4px;
}
QPushButton#SidebarToggle:hover {
    background: #F4F5FF;
    color: #5B6CFF;
}
QPushButton#SidebarExpandBtn {
    background: #FFFFFF;
    color: #5B6CFF;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 800;
}
QPushButton#SidebarExpandBtn:hover {
    background: #F4F5FF;
    border-color: #D5DAFF;
    color: #4858EC;
}
QPushButton#NewChat {
    background: #FFFFFF;
    color: #18181B;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    text-align: left;
}
QPushButton#NewChat:hover { background: #FAFAFA; border-color: #D4D4D8; }
QPushButton#NewChat:pressed { background: #E4E4E7; }
QPushButton#NewChat[collapsed="true"] {
    text-align: center;
    padding: 0px;
    font-size: 16px;
}
QPushButton#DeleteChat {
    background: transparent;
    color: #A1A1AA;
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    text-align: left;
    font-size: 11px;
}
QPushButton#DeleteChat:hover { background: #FEE2E2; color: #DC2626; }
QPushButton#DeleteChat[collapsed="true"] {
    text-align: center;
    padding: 0px;
    font-size: 14px;
}
QPushButton#SideAction {
    background: transparent;
    color: #3F3F46;
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#SideAction:hover { background: #EAEAEA; color: #18181B; }
QPushButton#SideAction:pressed { background: #E4E4E7; color: #18181B; }
QPushButton#SideAction[collapsed="true"] {
    text-align: center;
    padding: 0px;
    font-size: 16px;
}

QListWidget#SessionList { background: transparent; border: none; outline: none; color: #3F3F46; }
QListWidget#SessionList::item {
    padding: 7px 9px;
    margin: 2px 0;
    border-radius: 8px;
}
QListWidget#SessionList::item:hover { background: #EAEAEA; color: #18181B; }
QListWidget#SessionList::item:selected {
    background: #FFFFFF;
    color: #5B6CFF;
    font-weight: 700;
    border: 1px solid #EAEAEA;
}

/* Minimalist Line Sidebar Toggle Button */
QPushButton#SidebarToggleBtn {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0px;
}

/* Merged Sleek TopBar & Expandable Drawer */
QFrame#TopBar {
    background: #FFFFFF;
    border: 1px solid #EEEEEE;
    border-radius: 12px;
}
QFrame#TopHeader {
    background: transparent;
    border: none;
}
QFrame#TopDrawer {
    background: transparent;
    border: none;
}
QLabel#ModeBadge {
    background: #F4F5FF;
    color: #5B6CFF;
    border: 1px solid #D5DAFF;
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#HorizonBadge {
    background: #F4F4F5;
    color: #52525B;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 500;
}
QPushButton#DrawerToggle {
    background: #F4F4F5;
    color: #52525B;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#DrawerToggle:hover {
    background: #EAEAEA;
    color: #18181B;
    border-color: #D4D4D8;
}
QPushButton#DrawerToggle:pressed {
    background: #E4E4E7;
}

QFrame#StatusBar {
    background: #FAFAFA;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
}
QFrame#ReadingToolbar {
    background: #FAFAFA;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
}
QPushButton#ToolbarButton {
    background: #FFFFFF;
    color: #3F3F46;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 500;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
}
QPushButton#ToolbarButton:hover { background: #F4F4F5; color: #18181B; border-color: #D4D4D8; }
QPushButton#FocusButton {
    background: #18181B;
    color: #FFFFFF;
    border: 1px solid #18181B;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 10px;
}
QPushButton#FocusButton:hover { background: #27272A; }
QLabel#Title { font-size: 15px; font-weight: 800; color: #18181B; letter-spacing: -0.2px; }
QLabel#Subtle { color: #71717A; font-size: 12px; }
QLabel#ControlLabel { color: #18181B; font-size: 12px; font-weight: 600; }
QLabel#EvidenceChip {
    background: #F4F5FF;
    color: #5B6CFF;
    border: 1px solid #D5DAFF;
    border-radius: 10px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#ShortcutChip {
    background: #F4F4F5;
    color: #71717A;
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 11px;
    font-weight: 500;
}
QComboBox {
    min-width: 140px;
    padding: 6px 10px;
    background: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 10px;
    color: #18181B;
}
QComboBox:hover { border-color: #D4D4D8; background: #FFFFFF; }
QComboBox:focus { border: 1.5px solid #5B6CFF; background: #FFFFFF; }
QComboBox::drop-down { border: none; width: 22px; }
QFrame#ResearchPanel {
    background: #F8F9FA;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
}
QFrame#ProgressPanel {
    background: #F8F9FA;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
}
QFrame#ProgressPanel QLabel { color: #52525B; }
QLabel#RunTitle { color: #18181B; font-weight: 700; font-size: 13px; }
QLabel#Elapsed { color: #71717A; font-size: 12px; }
QListWidget#ProgressList { color: #52525B; background: transparent; border: none; font-size: 12px; }
QListWidget#ProgressList::item { padding: 4px 6px; }
QListWidget#ProgressList::item:selected { background: #F4F5FF; color: #5B6CFF; }
QFrame#ApprovalCard {
    background: #FFFBEB;
    border: 1px solid #FDE68A;
    border-radius: 10px;
}
QFrame#ApprovalCard QLabel { color: #92400E; }
QPushButton#Approve {
    background: #10B981;
    color: white;
    font-weight: 700;
    border-radius: 8px;
    padding: 6px 14px;
}
QPushButton#Approve:hover { background: #059669; }
QPushButton#Reject {
    background: transparent;
    color: #B45309;
    border: 1px solid #FCD34D;
    border-radius: 8px;
    padding: 6px 14px;
}
QPushButton#Reject:hover { background: #FEF3C7; }
QTextBrowser#Conversation {
    background: #FAFAFA;
    border: 1px solid #EEEEEE;
    border-radius: 16px;
    padding: 16px;
    selection-background-color: #F4F5FF;
    selection-color: #5B6CFF;
}

/* DeepSeek Composer Frame (18px rounded) */
QFrame#Composer {
    background: #FFFFFF;
    border: 1.5px solid #E4E4E7;
    border-radius: 18px;
}
QFrame#Composer[dragActive="true"] {
    background: #F4F5FF;
    border: 2px dashed #5B6CFF;
}

QTextEdit#QuestionInput {
    background: transparent;
    border: none;
    padding: 4px 6px;
    selection-background-color: #F4F5FF;
    selection-color: #5B6CFF;
    color: #18181B;
    font-size: 14px;
}
QPushButton {
    padding: 7px 15px;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    background: #FFFFFF;
    color: #3F3F46;
    font-weight: 500;
    font-size: 13px;
}
QPushButton:hover { background: #F4F4F5; color: #18181B; border-color: #D4D4D8; }
QPushButton:pressed { background: #E4E4E7; }

QPushButton#Attach {
    background: #F4F4F5;
    color: #52525B;
    border: 1px solid #EEEEEE;
    border-radius: 8px;
    padding: 5px 12px;
    font-weight: 500;
}
QPushButton#Attach:hover { background: #EAEAEA; color: #18181B; }

/* Function Pill Buttons (学习动作) */
QPushButton#PillButton, QPushButton#LearningAction {
    background: #F4F4F5;
    color: #3F3F46;
    border: 1px solid #EEEEEE;
    border-radius: 10px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton#PillButton:hover, QPushButton#LearningAction:hover {
    background: #F4F5FF;
    color: #5B6CFF;
    border-color: #D5DAFF;
}
QPushButton#PillButton:checked, QPushButton#LearningAction:checked {
    background: #F4F5FF;
    color: #5B6CFF;
    border: 1px solid #5B6CFF;
    font-weight: 700;
}

QPushButton:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #EEEEEE; }
QProgressBar { border: none; border-radius: 2px; background: #E4E4E7; height: 4px; min-height: 4px; max-height: 4px; }
QProgressBar::chunk { border-radius: 2px; background: #5B6CFF; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 6px; border-radius: 8px; }
QListWidget::item:selected { background: #F4F5FF; color: #5B6CFF; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
QScrollBar::handle:vertical { background: #E4E4E7; min-height: 28px; border-radius: 3px; }
QScrollBar::handle:vertical:hover { background: #A1A1AA; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

DARK_APP_QSS = """
QWidget { background: #111318; color: #F4F4F5; }
QLabel, QFrame#Sidebar QLabel, QLabel#BrandTitle, QLabel#Title,
QLabel#ControlLabel, QLabel#RunTitle { color: #F4F4F5; }
QFrame#Sidebar, QFrame#TopBar, QFrame#TopHeader, QFrame#TopDrawer,
QFrame#StatusBar, QFrame#ReadingToolbar { background: #171B22; border-color: #303644; }
QFrame#ResearchPanel, QFrame#ProgressPanel, QFrame#Composer,
QTextBrowser#Conversation, QComboBox, QTextEdit#QuestionInput {
    background: #1D222C; color: #F4F4F5; border-color: #303644;
}
QPushButton, QPushButton#NewChat, QPushButton#SideAction,
QPushButton#ToolbarButton, QPushButton#PillButton, QPushButton#LearningAction {
    background: #252B36; color: #E4E4E7; border-color: #303644;
}
QPushButton:hover, QPushButton#SideAction:hover, QPushButton#ToolbarButton:hover {
    background: #303848; color: #FFFFFF;
}
QComboBox:hover, QComboBox:focus { background: #252B36; color: #F4F4F5; }
QListWidget#SessionList, QListWidget#ProgressList, QLabel#Subtle,
QLabel#BrandSubtitle, QLabel#Elapsed { color: #A1A1AA; }
QListWidget#SessionList::item:hover, QListWidget#SessionList::item:selected {
    background: #252B36; color: #9BA8FF;
}
QScrollBar::handle:vertical { background: #485164; }
"""


class PulsingStatusIndicator(QObject):
    """Dynamic pulsing dot indicator for AI thinking/processing state."""
    text_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(320)
        self._timer.timeout.connect(self._tick)
        self._base_text = "正在工作"
        self._step = 0
        self._symbols = ["● ◌ ◌", "◌ ● ◌", "◌ ◌ ●", "● ● ●"]

    def start(self, base_text: str = "正在深度思考与检索……") -> None:
        self._base_text = base_text
        self._step = 0
        self._timer.start()
        self._tick()

    def stop(self) -> None:
        self._timer.stop()

    def update_base_text(self, text: str) -> None:
        self._base_text = text
        self._tick()

    def _tick(self) -> None:
        symbol = self._symbols[self._step % len(self._symbols)]
        self._step += 1
        self.text_changed.emit(f"{symbol}  {self._base_text}")


class SidebarCollapseToggleButton(QPushButton):
    """DeepSeek Harness style minimalist line panel-collapse button with 120ms hover fade."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarToggleBtn")
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self._collapsed = False
        self._hover_progress = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(120)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_step)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed != collapsed:
            self._collapsed = collapsed
            self.update()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _on_hover_step(self, val: Any) -> None:
        self._hover_progress = float(val)
        self.update()

    def enterEvent(self, event: Any) -> None:
        super().enterEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def leaveEvent(self, event: Any) -> None:
        super().leaveEvent(event)
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        # Background: Smooth light gray fade-in (120ms)
        if self.isDown():
            bg_color = QColor(228, 228, 231)  # #E4E4E7
            painter.setBrush(bg_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(0, 0, w, h, 8, 8)
        elif self._hover_progress > 0.001:
            alpha = int(255 * self._hover_progress)
            bg_color = QColor(244, 244, 245, alpha)  # #F4F4F5
            painter.setBrush(bg_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(0, 0, w, h, 8, 8)

        # Icon pen
        pen_color = QColor(24, 24, 27) if self._hover_progress > 0.5 else QColor(82, 82, 91)
        pen = QPen(pen_color, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # Panel outline: 16x15 rect, centered at (16, 16) -> x=8.0, y=8.5, w=16.0, h=15.0
        rect = QRectF(8.0, 8.5, 16.0, 15.0)
        painter.drawRoundedRect(rect, 2.5, 2.5)

        # Left panel vertical divider: at x = 13.5
        painter.drawLine(13.5, 8.5, 13.5, 23.5)

        # Chevron / Arrow indicator
        if not self._collapsed:
            # Expanded state: arrow/chevron points left (collapse action)
            painter.drawLine(19.5, 13.5, 17.0, 16.0)
            painter.drawLine(17.0, 16.0, 19.5, 18.5)
        else:
            # Collapsed state: arrow/chevron points right (expand action)
            painter.drawLine(17.0, 13.5, 19.5, 16.0)
            painter.drawLine(19.5, 16.0, 17.0, 18.5)


class DeepSeekComposerFrame(QFrame):
    """DeepSeek composer container with 18px rounded border and 200ms focus transition."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Composer")
        self._current_border = QColor(DEEPSEEK_BORDER)
        self._is_focused = False
        self._border_anim = QVariantAnimation(self)
        self._border_anim.setDuration(200)
        self._border_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._border_anim.valueChanged.connect(self._on_border_color_changed)
        self._apply_border_color(self._current_border)

    def set_focused(self, focused: bool) -> None:
        if self._is_focused == focused:
            return
        self._is_focused = focused
        self._border_anim.stop()
        self._border_anim.setStartValue(self._current_border)
        target = QColor(DEEPSEEK_BORDER_FOCUS) if focused else QColor(DEEPSEEK_BORDER)
        self._border_anim.setEndValue(target)
        self._border_anim.start()

    def _on_border_color_changed(self, color: QColor) -> None:
        self._current_border = color
        self._apply_border_color(color)

    def _apply_border_color(self, color: QColor) -> None:
        if self.property("dragActive"):
            self.setStyleSheet("QFrame#Composer { background: #EEF2FF; border: 2px dashed #4D6BFE; border-radius: 18px; }")
        else:
            self.setStyleSheet(f"QFrame#Composer {{ background: #FFFFFF; border: 1.5px solid {color.name()}; border-radius: 18px; }}")


class DeepSeekTextEdit(QTextEdit):
    """Text editor with focus notification for 200ms smooth border transition."""
    focus_changed = Signal(bool)
    return_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QuestionInput")
        self.send_key_mode = "Enter"

    def set_send_key_mode(self, mode: str) -> None:
        if mode in ("Enter", "Ctrl+Enter", "Shift+Enter"):
            self.send_key_mode = mode

    def focusInEvent(self, event: Any) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event: Any) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            mods = event.modifiers()
            if self.send_key_mode == "Enter":
                if not (mods & (Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier)):
                    self.return_pressed.emit()
                    event.accept()
                    return
            elif self.send_key_mode == "Ctrl+Enter":
                if mods & (Qt.ControlModifier | Qt.MetaModifier):
                    self.return_pressed.emit()
                    event.accept()
                    return
            elif self.send_key_mode == "Shift+Enter":
                if mods & Qt.ShiftModifier:
                    self.return_pressed.emit()
                    event.accept()
                    return
        super().keyPressEvent(event)


class DeepSeekSendButton(QPushButton):
    """DeepSeek circular send button (34x34) with dynamic input state and tactile press feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("↑", parent)
        self.setObjectName("Send")
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self._has_input = False
        self._scale = 1.0
        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.setDuration(120)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scale_anim.valueChanged.connect(self._on_scale_changed)
        self._apply_theme()

    def set_has_input(self, has_input: bool) -> None:
        if self._has_input == bool(has_input):
            return
        self._has_input = bool(has_input)
        self._apply_theme()

    def _apply_theme(self) -> None:
        if self._has_input:
            self.setStyleSheet(
                "QPushButton#Send {"
                "    background-color: #5B6CFF;"
                "    color: #FFFFFF;"
                "    border: none;"
                "    border-radius: 17px;"
                "    font-size: 16px;"
                "    font-weight: 800;"
                "}"
                "QPushButton#Send:hover {"
                "    background-color: #4858EC;"
                "}"
                "QPushButton#Send:pressed {"
                "    background-color: #3846D4;"
                "}"
                "QPushButton#Send:disabled {"
                "    background-color: #F3F4F6;"
                "    color: #9CA3AF;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QPushButton#Send {"
                "    background-color: #E4E4E7;"
                "    color: #9CA3AF;"
                "    border: none;"
                "    border-radius: 17px;"
                "    font-size: 16px;"
                "    font-weight: 800;"
                "}"
                "QPushButton#Send:hover {"
                "    background-color: #D1D5DB;"
                "    color: #4B5563;"
                "}"
                "QPushButton#Send:disabled {"
                "    background-color: #F3F4F6;"
                "    color: #9CA3AF;"
                "}"
            )

    def _on_scale_changed(self, val: float) -> None:
        self._scale = val
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if self.isEnabled() and event.button() == Qt.LeftButton:
            self._scale_anim.stop()
            self._scale_anim.setStartValue(self._scale)
            self._scale_anim.setEndValue(0.92)
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


class AnimatedLearningButton(ScalePressButton):
    """DeepSeek capsule learning action button with tactile press feedback."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("LearningAction")
        self.setFixedHeight(30)


class AgentWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)
    progressed = Signal(object)
    approval_requested = Signal(object)
    finished = Signal()

    def __init__(
        self,
        service: TutorApplicationService,
        *,
        query: str,
        image: str | None,
        image_media_type: str | None,
        research_filters: ResearchFilters | None,
        conversation_context: list[dict[str, str]] | None = None,
        conversation_key: str | None = None,
        prior_result: Any = None,
        prior_query: str = "",
        prior_image: str | None = None,
        learning_action_id: str | None = None,
        learning_topic: str | None = None,
        max_duration_seconds: float = 180.0,
    ) -> None:
        super().__init__()
        self._service = service
        self._query = query
        self._image = image
        self._image_media_type = image_media_type
        self._research_filters = research_filters
        self._conversation_context = list(conversation_context or [])
        self._conversation_key = conversation_key
        self._prior_result = prior_result
        self._prior_query = prior_query
        self._prior_image = prior_image
        self._learning_action_id = learning_action_id
        self._learning_topic = learning_topic
        self._max_duration_seconds = max(10.0, float(max_duration_seconds))
        self._approval_event = Event()
        self._approval_decision = False
        self._cancel_event = Event()
        self._timeout_requested = False

    def request_cancel(self, *, timed_out: bool = False) -> None:
        self._timeout_requested = self._timeout_requested or timed_out
        self._cancel_event.set()
        self._approval_decision = False
        self._approval_event.set()

    def resolve_approval(self, approved: bool) -> None:
        self._approval_decision = bool(approved)
        self._approval_event.set()

    def _await_approval(self, request: ApprovalRequest) -> bool:
        if self._cancel_event.is_set():
            raise RunCancelled("用户已停止本轮回答")
        self._approval_decision = False
        self._approval_event.clear()
        self.approval_requested.emit(request)
        self._approval_event.wait()
        if self._cancel_event.is_set():
            raise RunCancelled("用户已停止本轮回答")
        return self._approval_decision

    @Slot()
    def run(self) -> None:
        try:
            deadline = time.monotonic() + self._max_duration_seconds
            result = self._service.ask(
                self._query,
                image=self._image,
                image_media_type=self._image_media_type,
                research_filters=self._research_filters,
                progress_callback=self.progressed.emit,
                approval_callback=self._await_approval,
                conversation_context=self._conversation_context,
                conversation_key=self._conversation_key,
                prior_result=self._prior_result,
                prior_query=self._prior_query,
                prior_image=self._prior_image,
                learning_action_id=self._learning_action_id,
                learning_topic=self._learning_topic,
                cancel_check=self._cancel_event.is_set,
                deadline_monotonic=deadline,
            )
            ensure_run_active(self._cancel_event.is_set, deadline)
            self.succeeded.emit(result)
        except RunTimedOut as exc:
            self.cancelled.emit(str(exc))
        except RunCancelled as exc:
            self.cancelled.emit(
                "本轮回答超过总时限，已安全停止"
                if self._timeout_requested else str(exc)
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


class WebBridge(QObject):
    sessionsUpdated = Signal(str)
    sessionSwitched = Signal(str, str)
    progressUpdated = Signal(str, str, str)
    answerReceived = Signal(str)
    generatingStateChanged = Signal(bool)
    statusChanged = Signal(str)
    attachmentChanged = Signal(str)
    subjectChanged = Signal(str)
    modeChanged = Signal(str)
    horizonChanged = Signal(str)
    framelessModeChanged = Signal(bool)
    sidebarCollapsedChanged = Signal(bool)
    themeChanged = Signal(str)
    approvalRequested = Signal(str)
    approvalCleared = Signal()

    def __init__(self, window: Any) -> None:
        super().__init__()
        self.window = window

    @Slot()
    def toggleSidebar(self) -> None:
        self.window._toggle_sidebar()

    @Slot()
    def onFrontendReady(self) -> None:
        self.window._push_state_to_frontend()

    @Slot()
    def minimizeWindow(self) -> None:
        self.window.showMinimized()

    @Slot()
    def maximizeWindow(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    @Slot()
    def closeWindow(self) -> None:
        self.window.close()

    @Slot()
    def startWindowMove(self) -> None:
        handle = self.window.windowHandle()
        if handle is not None and hasattr(handle, "startSystemMove"):
            handle.startSystemMove()

    @Slot(str)
    def setSubject(self, code: str) -> None:
        idx = self.window.subject_combo.findData(code)
        if idx >= 0:
            self.window.subject_combo.setCurrentIndex(idx)

    @Slot(str)
    def setMode(self, code: str) -> None:
        idx = self.window.mode_combo.findData(code)
        if idx >= 0:
            self.window.mode_combo.setCurrentIndex(idx)

    @Slot(str)
    def setHorizon(self, code: str) -> None:
        idx = self.window.horizon_combo.findData(code)
        if idx >= 0:
            self.window.horizon_combo.setCurrentIndex(idx)

    @Slot()
    def newConversation(self) -> None:
        self.window._new_conversation()

    @Slot(str)
    def selectSession(self, session_id: str) -> None:
        if session_id and session_id != self.window._session_id:
            self.window._session_id = session_id
            self.window._render_current_session()
            self.window._refresh_session_list()

    @Slot(str)
    def deleteSession(self, session_id: str) -> None:
        if session_id:
            self.window._session_id = session_id
            self.window._delete_current_conversation()

    @Slot()
    def openKnowledgeDialog(self) -> None:
        self.window.knowledge_requested.emit()

    @Slot()
    def openSkillsDialog(self) -> None:
        self.window.skills_requested.emit(self.window._last_result, self.window._last_query)

    @Slot()
    def openEvaluationDialog(self) -> None:
        self.window.evaluation_requested.emit()

    @Slot()
    def openOfficialSources(self) -> None:
        self.window._show_official_sources()

    @Slot()
    def openSettingsDialog(self) -> None:
        self.window.settings_requested.emit()

    @Slot()
    def openAllResources(self) -> None:
        self.window._show_all_resources()

    @Slot(str)
    def openVideoResource(self, raw_url: str) -> None:
        url = QUrl(str(raw_url or ""))
        host = url.host().lower()
        path = url.path()
        if url.scheme() != "https":
            return
        if host == "www.bilibili.com" and path.startswith("/video/BV"):
            QDesktopServices.openUrl(url)
        elif host == "search.bilibili.com" and path == "/all":
            QDesktopServices.openUrl(url)

    @Slot()
    def chooseImage(self) -> None:
        self.window._choose_image()

    @Slot()
    def clearAttachment(self) -> None:
        self.window._clear_attachment()

    @Slot()
    def stopAnswer(self) -> None:
        self.window._stop_current_answer()

    @Slot(bool)
    def resolveApproval(self, approved: bool) -> None:
        self.window._resolve_approval(bool(approved))

    @Slot(str)
    def setSendKeyMode(self, mode: str) -> None:
        if hasattr(self.window, "question_input") and hasattr(self.window.question_input, "set_send_key_mode"):
            self.window.question_input.set_send_key_mode(mode)

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        self.window.input_edit.setPlainText(text)
        self.window._send()

    @Slot(str, str, str)
    @Slot(str, str, str, str)
    def sendLearningAction(
        self, action_id: str, label: str, prompt: str, topic: str = ""
    ) -> None:
        self.window._pending_learning_action_id = action_id
        self.window._pending_learning_topic = topic or self.window._current_learning_topic
        self.window.input_edit.setPlainText(prompt or label)
        self.window._send()

    @Slot(str)
    def openResearchResource(self, raw_url: str) -> None:
        url = QUrl(str(raw_url or ""))
        host = url.host().lower()
        if (
            url.scheme() != "https"
            or not host
            or url.userInfo()
            or host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith(".local")
        ):
            return
        QDesktopServices.openUrl(url)


class TutorMainWindow(QMainWindow):
    settings_requested = Signal()
    knowledge_requested = Signal()
    skills_requested = Signal(object, str)
    evaluation_requested = Signal()
    knowledge_files_dropped = Signal(object)

    def __init__(
        self,
        service: TutorApplicationService | None,
        *,
        startup_issues: tuple[str, ...] = (),
        conversation_store: ConversationStore | None = None,
        theme_mode: str = "system",
    ) -> None:
        super().__init__()
        self.service = service
        self.startup_issues = startup_issues
        self.theme_mode = theme_mode if theme_mode in {"light", "dark", "system"} else "system"
        self._effective_theme = self._resolve_effective_theme()
        self._image_path: str | None = None
        self._thread: QThread | None = None
        self._worker: AgentWorker | None = None
        self._progress_items: dict[str, QListWidgetItem] = {}
        self._run_started_at: float | None = None
        self._pending_approval: ApprovalRequest | None = None
        self._last_result: Any = None
        self._answer_context_by_session: dict[str, tuple[Any, str]] = {}
        self._last_resources: tuple[dict[str, Any], ...] = ()
        self._focus_mode = False
        self._active_query = ""
        self._last_query = ""
        self._pending_learning_action_id: str | None = None
        self._pending_learning_topic = ""
        self._current_learning_topic = ""
        self.conversation_store = conversation_store or ConversationStore()
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._run_timeout_timer = QTimer(self)
        self._run_timeout_timer.setSingleShot(True)
        self._run_timeout_timer.timeout.connect(self._timeout_current_answer)
        self._close_after_cancel = False
        self._status_animator = PulsingStatusIndicator(self)
        
        # Sidebar collapse state & animation (QPropertyAnimation on maximumWidth, 220ms, ease-out)
        self._sidebar_collapsed = False
        self._sidebar_anim = QPropertyAnimation(self, b"maximumWidth")
        self._sidebar_anim.setDuration(220)
        self._sidebar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._sidebar_anim.finished.connect(self._on_sidebar_anim_finished)

        # Top Drawer (Red box 1 & 2 merged accordion) state & animation
        self._top_drawer_expanded = False
        self._top_drawer_anim = QVariantAnimation(self)
        self._top_drawer_anim.setDuration(240)
        self._top_drawer_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._top_drawer_anim.valueChanged.connect(self._on_top_drawer_anim_step)

        sessions = self.conversation_store.list_sessions()
        self._session_id = (
            sessions[0].session_id
            if sessions
            else self.conversation_store.create_session().session_id
        )

        self.setWindowTitle("MedScholar")
        self.setAcceptDrops(True)
        self.resize(1280, 840)
        self.setMinimumSize(900, 640)
        self.setStyleSheet(APP_QSS + (DARK_APP_QSS if self._effective_theme == "dark" else ""))
        self._build_ui()
        if self.theme_mode == "system":
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._apply_ui_theme)
        self._status_animator.text_changed.connect(self.status_label.setText)
        self._populate_modes()
        self._sync_research_controls()
        self._refresh_session_list()
        self._render_current_session()
        if not self.conversation_store.get_session(self._session_id).messages:
            self._show_startup_state()

    def _resolve_effective_theme(self) -> str:
        if self.theme_mode != "system":
            return self.theme_mode
        return "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"

    def _apply_ui_theme(self, *_: Any) -> None:
        theme = self._resolve_effective_theme()
        if theme == self._effective_theme:
            return
        self._effective_theme = theme
        self.setStyleSheet(APP_QSS + (DARK_APP_QSS if theme == "dark" else ""))
        if hasattr(self, "web_view"):
            self.web_view.page().setBackgroundColor(QColor("#111318" if theme == "dark" else "#FAFAFA"))
        if hasattr(self, "web_bridge"):
            self.web_bridge.themeChanged.emit(theme)

    def _build_ui(self) -> None:
        root_stack = QStackedWidget(self)
        self.setCentralWidget(root_stack)
        self.stack = root_stack

        # 0. Modern Web View matching preview.html with 100% animations and styling
        self.web_bridge = WebBridge(self)
        self.web_channel = QWebChannel(self)
        self.web_channel.registerObject("bridge", self.web_bridge)

        self.web_view = QWebEngineView(self)
        # Match the HTML canvas while Chromium briefly re-composites on
        # Windows activation changes, instead of exposing the default white.
        self.web_view.page().setBackgroundColor(QColor("#111318" if self._effective_theme == "dark" else "#FAFAFA"))
        # Let the top-level window keep handling file drops so the existing
        # image/PDF drag-and-drop workflow is not swallowed by Chromium.
        self.web_view.setAcceptDrops(False)
        self.web_view.page().setWebChannel(self.web_channel)
        # Load the packaged frontend next to this module, independent of the
        # process working directory or which shortcut launched the app.
        preview_path = Path(__file__).resolve().with_name("preview.html")
        preview_url = QUrl.fromLocalFile(str(preview_path))
        preview_url.setQuery(f"theme={self._effective_theme}")
        self.web_view.load(preview_url)
        root_stack.addWidget(self.web_view)

        # 1. Native container with all backing widgets for programmatic & test compatibility
        self.native_container = QWidget()
        outer_layout = QHBoxLayout(self.native_container)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        outer_layout.setSpacing(12)
        root_stack.addWidget(self.native_container)
        root_stack.setCurrentIndex(0)

        # ==============================================================================
        # 1. Collapsible Sidebar (#F8F9FA) with Perfect Alignment
        # ==============================================================================
        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setMaximumWidth(240)
        sidebar.setMinimumWidth(0)
        self._sidebar_anim.setTargetObject(self.sidebar)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 14, 12, 14)
        sidebar_layout.setSpacing(8)

        # Brand header: Logo (36x36) + Title widget
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(8)
        self.brand_mark = QLabel()
        self.brand_mark.setObjectName("BrandMark")
        self.brand_mark.setFixedSize(36, 36)
        logo_path = _logo_path_for_module(__file__)
        if not logo_path.exists():
            logo_path = Path("logo.png").resolve()
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.brand_mark.setPixmap(pix)
            self.brand_mark.setStyleSheet("background: transparent; border-radius: 8px;")
        else:
            self.brand_mark.setText("M")
        brand_row.addWidget(self.brand_mark)

        self.brand_text_widget = QWidget()
        brand_text = QVBoxLayout(self.brand_text_widget)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        brand_title = QLabel("MedScholar")
        brand_title.setObjectName("BrandTitle")
        brand_text.addWidget(brand_title)
        brand_row.addWidget(self.brand_text_widget, 1)

        self.sidebar_collapse_button = None
        sidebar_layout.addLayout(brand_row)

        self.conversation_section_label = QLabel("对话")
        self.conversation_section_label.setObjectName("SidebarSection")
        sidebar_layout.addWidget(self.conversation_section_label)

        # New chat button (Always directly beneath brand header / section)
        self.new_chat_button = ScalePressButton("＋ 新建对话")
        self.new_chat_button.setObjectName("NewChat")
        self.new_chat_button.setToolTip("新建对话")
        self.new_chat_button.setFixedHeight(36)
        self.new_chat_button.clicked.connect(self._new_conversation)
        sidebar_layout.addWidget(self.new_chat_button)

        # Session list (hidden when sidebar is collapsed)
        self.session_list = QListWidget()
        self.session_list.setObjectName("SessionList")
        self.session_list.currentItemChanged.connect(self._session_changed)
        sidebar_layout.addWidget(self.session_list, 1)

        # Delete chat button (hidden when sidebar is collapsed)
        self.delete_chat_button = ScalePressButton("删除当前对话")
        self.delete_chat_button.setObjectName("DeleteChat")
        self.delete_chat_button.setToolTip("删除当前对话")
        self.delete_chat_button.setFixedHeight(28)
        self.delete_chat_button.clicked.connect(self._delete_current_conversation)
        sidebar_layout.addWidget(self.delete_chat_button)

        self.sidebar_expand_button = None

        # Middle spacer
        sidebar_layout.addStretch(1)

        self.workspace_section_label = QLabel("学习工作台")
        self.workspace_section_label.setObjectName("SidebarSection")
        sidebar_layout.addWidget(self.workspace_section_label)

        self.knowledge_button = ScalePressButton("📚  课程知识库")
        self.knowledge_button.setToolTip("课程知识库")
        self.knowledge_button.setFixedHeight(34)
        self.knowledge_button.clicked.connect(self.knowledge_requested.emit)

        self.skills_button = ScalePressButton("💡  纠错反馈与 Skill")
        self.skills_button.setToolTip("纠错反馈与 Skill")
        self.skills_button.setFixedHeight(34)
        self.skills_button.clicked.connect(
            lambda: self.skills_requested.emit(self._last_result, self._last_query)
        )

        self.evaluation_button = ScalePressButton("📝  题库评测")
        self.evaluation_button.setToolTip("题库评测")
        self.evaluation_button.setFixedHeight(34)
        self.evaluation_button.clicked.connect(self.evaluation_requested.emit)

        self.official_sources_button = ScalePressButton("🏛  官方证据源")
        self.official_sources_button.setToolTip("官方证据源")
        self.official_sources_button.setFixedHeight(34)
        self.official_sources_button.clicked.connect(self._show_official_sources)

        self.settings_button = ScalePressButton("⚙️  设置与模型配置")
        self.settings_button.setToolTip("设置与模型配置")
        self.settings_button.setFixedHeight(34)
        self.settings_button.clicked.connect(self.settings_requested.emit)

        for button in (
            self.knowledge_button,
            self.skills_button,
            self.evaluation_button,
            self.official_sources_button,
            self.settings_button,
        ):
            button.setObjectName("SideAction")
            sidebar_layout.addWidget(button)

        outer_layout.addWidget(sidebar)

        # ==============================================================================
        # 2. Main Content Area (#FFFFFF)
        # ==============================================================================
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        outer_layout.addWidget(content, 1)

        # Merged Sleek Top Bar (Single compact bar + smooth accordion drawer)
        top = QFrame()
        self.top_bar = top
        top.setObjectName("TopBar")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(14, 9, 14, 9)
        top_layout.setSpacing(4)

        # Compact Header Line
        header_line = QHBoxLayout()
        header_line.setSpacing(8)

        # 1. Sidebar collapse toggle button at the far left
        self.sidebar_toggle_button = SidebarCollapseToggleButton()
        self.sidebar_toggle_button.setObjectName("SidebarToggleBtn")
        self.sidebar_toggle_button.setToolTip("折叠/展开侧边栏")
        self.sidebar_toggle_button.clicked.connect(self._toggle_sidebar)
        header_line.addWidget(self.sidebar_toggle_button)

        self.subject_badge = QLabel("自动识别学科")
        self.subject_badge.setObjectName("ModeBadge")
        header_line.addWidget(self.subject_badge)

        self.mode_badge = QLabel("智能选择")
        self.mode_badge.setObjectName("ModeBadge")
        header_line.addWidget(self.mode_badge)

        self.horizon_badge = QLabel("仅科研模式拓展")
        self.horizon_badge.setObjectName("HorizonBadge")
        header_line.addWidget(self.horizon_badge)

        header_line.addStretch()

        evidence_chip = QLabel("回到课程 · 因材施教")
        evidence_chip.setObjectName("EvidenceChip")
        header_line.addWidget(evidence_chip)

        self.top_drawer_toggle_btn = ScalePressButton("⚙️ 模式与参数 ▾")
        self.top_drawer_toggle_btn.setObjectName("DrawerToggle")
        self.top_drawer_toggle_btn.clicked.connect(self._toggle_top_drawer)
        header_line.addWidget(self.top_drawer_toggle_btn)
        top_layout.addLayout(header_line)

        # Expandable Drawer Container
        self.top_drawer = QFrame()
        self.top_drawer.setObjectName("TopDrawer")
        self.top_drawer.setMaximumHeight(0)
        drawer_layout = QVBoxLayout(self.top_drawer)
        drawer_layout.setContentsMargins(0, 6, 0, 2)
        drawer_layout.setSpacing(8)

        subtitle = QLabel("陪你理解、复习与拓展；证据核验在后台安静守护")
        subtitle.setObjectName("Subtle")
        drawer_layout.addWidget(subtitle)

        control_row = QHBoxLayout()
        control_row.setSpacing(9)
        subject_label = QLabel("课程学科")
        subject_label.setObjectName("ControlLabel")
        control_row.addWidget(subject_label)
        self.subject_combo = QComboBox()
        self.subject_combo.currentIndexChanged.connect(self._on_subject_changed)
        control_row.addWidget(self.subject_combo)

        mode_label = QLabel("回答模式")
        mode_label.setObjectName("ControlLabel")
        control_row.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        control_row.addWidget(self.mode_combo)

        horizon_label = QLabel("医学拓展")
        horizon_label.setObjectName("ControlLabel")
        control_row.addWidget(horizon_label)
        self.horizon_combo = QComboBox()
        self.horizon_combo.currentIndexChanged.connect(self._on_horizon_changed)
        control_row.addWidget(self.horizon_combo)
        control_row.addStretch()

        mode_hint = QLabel("可随时切换，下一次提问生效")
        mode_hint.setObjectName("Subtle")
        control_row.addWidget(mode_hint)
        drawer_layout.addLayout(control_row)

        # Research Filters embedded inside drawer
        self.research_panel = QFrame()
        self.research_panel.setObjectName("ResearchPanel")
        research_layout = QGridLayout(self.research_panel)
        research_layout.setContentsMargins(12, 8, 12, 8)
        research_layout.addWidget(QLabel("科研检索筛选"), 0, 0)
        research_layout.addWidget(QLabel("研究类型"), 0, 1)
        self.research_study_type = QComboBox()
        self.research_study_type.addItem("自动判断", ())
        self.research_study_type.addItem(
            "系统综述 / Meta分析", ("systematic review", "meta-analysis")
        )
        self.research_study_type.addItem(
            "随机对照试验", ("randomized controlled trial",)
        )
        self.research_study_type.addItem("临床试验", ("clinical trial",))
        self.research_study_type.addItem("综述", ("review",))
        research_layout.addWidget(self.research_study_type, 0, 2, 1, 3)
        research_layout.addWidget(QLabel("起始年份"), 1, 0)
        self.research_year_from = QComboBox()
        self.research_year_from.addItem("自动", None)
        current_year = datetime.now().year
        for year in range(current_year, 1989, -1):
            self.research_year_from.addItem(str(year), year)
        research_layout.addWidget(self.research_year_from, 1, 1)
        research_layout.addWidget(QLabel("结束年份"), 1, 2)
        self.research_year_to = QComboBox()
        self.research_year_to.addItem("自动", None)
        for year in range(current_year, 1989, -1):
            self.research_year_to.addItem(str(year), year)
        research_layout.addWidget(self.research_year_to, 1, 3)
        research_layout.setColumnStretch(4, 1)
        drawer_layout.addWidget(self.research_panel)
        top_layout.addWidget(self.top_drawer)
        layout.addWidget(top)

        # Progress Panel
        self.progress_panel = QFrame()
        self.progress_panel.setObjectName("ProgressPanel")
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(12, 10, 12, 10)
        progress_layout.setSpacing(6)
        progress_heading = QHBoxLayout()
        progress_title = QLabel("正在工作")
        progress_title.setObjectName("RunTitle")
        progress_heading.addWidget(progress_title)
        progress_heading.addStretch()
        public_process = QLabel("公开任务过程 · 不展示隐藏思维链")
        public_process.setObjectName("Elapsed")
        progress_heading.addWidget(public_process)
        self.elapsed_label = QLabel("0 秒")
        self.elapsed_label.setObjectName("Elapsed")
        progress_heading.addWidget(self.elapsed_label)
        progress_layout.addLayout(progress_heading)
        self.progress_list = QListWidget()
        self.progress_list.setObjectName("ProgressList")
        self.progress_list.setMaximumHeight(126)
        progress_layout.addWidget(self.progress_list)

        self.approval_panel = QFrame()
        self.approval_panel.setObjectName("ApprovalCard")
        approval_layout = QVBoxLayout(self.approval_panel)
        approval_layout.setContentsMargins(11, 9, 11, 9)
        approval_layout.setSpacing(5)
        self.approval_title = QLabel("需要你的允许")
        self.approval_title.setStyleSheet("font-weight: 700;")
        self.approval_description = QLabel()
        self.approval_description.setWordWrap(True)
        approval_actions = QHBoxLayout()
        approval_actions.addStretch()
        self.reject_approval_button = ScalePressButton("拒绝")
        self.reject_approval_button.setObjectName("Reject")
        self.reject_approval_button.clicked.connect(
            lambda: self._resolve_approval(False)
        )
        self.approve_button = ScalePressButton("允许本次操作")
        self.approve_button.setObjectName("Approve")
        self.approve_button.clicked.connect(lambda: self._resolve_approval(True))
        approval_actions.addWidget(self.reject_approval_button)
        approval_actions.addWidget(self.approve_button)
        approval_layout.addWidget(self.approval_title)
        approval_layout.addWidget(self.approval_description)
        approval_layout.addLayout(approval_actions)
        self.approval_panel.hide()
        progress_layout.addWidget(self.approval_panel)
        self.progress_panel.hide()
        layout.addWidget(self.progress_panel)

        # Reading Toolbar
        self.reading_toolbar = QFrame()
        self.reading_toolbar.setObjectName("ReadingToolbar")
        reading_actions = QHBoxLayout(self.reading_toolbar)
        reading_actions.setContentsMargins(10, 5, 8, 5)
        reading_actions.setSpacing(4)
        answer_label = QLabel("回答")
        answer_label.setStyleSheet("font-weight:700;color:#111827;")
        reading_actions.addWidget(answer_label)
        reading_actions.addStretch()
        self.process_button = ScalePressButton("查看过程")
        self.process_button.setObjectName("ToolbarButton")
        self.process_button.clicked.connect(self._toggle_progress_panel)
        reading_actions.addWidget(self.process_button)
        self.sources_button = ScalePressButton("全部来源")
        self.sources_button.setObjectName("ToolbarButton")
        self.sources_button.setEnabled(False)
        self.sources_button.clicked.connect(self._show_all_resources)
        reading_actions.addWidget(self.sources_button)
        self.focus_button = None
        layout.addWidget(self.reading_toolbar)

        self.history = QTextBrowser()
        self.history.setObjectName("Conversation")
        self.history.setOpenExternalLinks(True)
        layout.addWidget(self.history, 1)

        # DeepSeek 18px rounded composer frame
        composer = DeepSeekComposerFrame()
        self.composer = composer
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 11, 14, 12)
        composer_layout.setSpacing(8)

        composer_heading = QHBoxLayout()
        composer_title = QLabel("向智能体提问")
        composer_title.setStyleSheet("font-weight: 700; color: #111827;")
        composer_heading.addWidget(composer_title)
        composer_heading.addStretch()
        shortcut_chip = QLabel("Enter 发送")
        shortcut_chip.setObjectName("ShortcutChip")
        composer_heading.addWidget(shortcut_chip)
        composer_layout.addLayout(composer_heading)

        self.learning_actions_panel = QFrame()
        self.learning_actions_panel.setObjectName("LearningActions")
        self.learning_actions_layout = QHBoxLayout(self.learning_actions_panel)
        self.learning_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.learning_actions_layout.setSpacing(6)
        self.learning_actions_panel.hide()
        composer_layout.addWidget(self.learning_actions_panel)

        self.input_edit = DeepSeekTextEdit()
        self.input_edit.setPlaceholderText(
            "输入题目、知识点或不确定的想法；也可以上传题目截图……"
        )
        self.input_edit.setFixedHeight(72)
        self.input_edit.focus_changed.connect(self.composer.set_focused)
        self.input_edit.textChanged.connect(self._update_send_button_state)
        self.input_edit.return_pressed.connect(self._send)
        composer_layout.addWidget(self.input_edit)

        action_row = QHBoxLayout()
        self.attach_button = ScalePressButton("添加图片")
        self.attach_button.setObjectName("Attach")
        self.attach_button.clicked.connect(self._choose_image)
        action_row.addWidget(self.attach_button)
        self.attachment_label = QLabel("可拖入图片或 PDF")
        self.attachment_label.setObjectName("Subtle")
        action_row.addWidget(self.attachment_label)
        action_row.addStretch()

        self.stop_button = ScalePressButton("停止回答")
        self.stop_button.setObjectName("DeleteChat")
        self.stop_button.setToolTip("安全停止本轮回答；当前网络请求返回后结束")
        self.stop_button.clicked.connect(self._stop_current_answer)
        self.stop_button.hide()
        action_row.addWidget(self.stop_button)

        # DeepSeek circular 34x34 send button
        self.send_button = DeepSeekSendButton()
        self.send_button.clicked.connect(self._send)
        action_row.addWidget(self.send_button)
        composer_layout.addLayout(action_row)
        layout.addWidget(composer)

        status_bar = QFrame()
        self.status_bar = status_bar
        status_bar.setObjectName("StatusBar")
        status_row = QHBoxLayout(status_bar)
        status_row.setContentsMargins(12, 6, 12, 6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(120)
        status_row.addWidget(self.progress)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("Subtle")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_note = QLabel("回答用于学习参考，临床决策请遵循专业规范")
        status_note.setObjectName("Subtle")
        status_row.addWidget(status_note)
        layout.addWidget(status_bar)

        # The web composer owns its selected send shortcut. A window-level
        # Ctrl+Return shortcut intercepts the key before QWebEngine sees it.
        # The native fallback editor handles the selected send mode itself.
        # Keep native focus-mode Esc local to the hidden native fallback; the
        # web composer needs Esc for its own modal dialogs.
        self.focus_shortcut = QShortcut(QKeySequence("Esc"), self.native_container)
        self.focus_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.focus_shortcut.activated.connect(lambda: self._set_focus_mode(False))

    def _push_state_to_frontend(self) -> None:
        if not hasattr(self, "web_bridge"):
            return
        is_frameless = bool(self.windowFlags() & Qt.FramelessWindowHint)
        self.web_bridge.framelessModeChanged.emit(is_frameless)
        self.web_bridge.themeChanged.emit(self._effective_theme)
        sessions = [
            {
                "id": s.session_id,
                "title": s.title,
                "time": s.updated_at[11:16] if len(s.updated_at) >= 16 else "",
                "subject": "医学课程",
            }
            for s in self.conversation_store.list_sessions()
        ]
        self.web_bridge.sessionsUpdated.emit(json.dumps(sessions, ensure_ascii=False))

        session = self.conversation_store.get_session(self._session_id)
        msgs_json = self._format_session_messages_json(session)
        self.web_bridge.sessionSwitched.emit(self._session_id, msgs_json)

        self.web_bridge.subjectChanged.emit(self.subject_combo.currentData() or "auto")
        self.web_bridge.modeChanged.emit(self.mode_combo.currentData() or "auto")
        self.web_bridge.horizonChanged.emit(self.horizon_combo.currentData() or "research_mode_only")
        self.web_bridge.statusChanged.emit(self.status_label.text())
        self.web_bridge.sidebarCollapsedChanged.emit(self._sidebar_collapsed)
        if self._pending_approval is not None:
            self._emit_web_approval(self._pending_approval)
        if self._image_path:
            self.web_bridge.attachmentChanged.emit(Path(self._image_path).name)

    def _emit_web_approval(self, request: ApprovalRequest) -> None:
        self.web_bridge.approvalRequested.emit(
            json.dumps(
                {
                    "requestId": request.request_id,
                    "title": request.title,
                    "description": request.description,
                    "actionLabel": request.action_label,
                    "riskLevel": request.risk_level,
                },
                ensure_ascii=False,
            )
        )

    def _format_ai_result_json(
        self, presented: PresentedResult, *, topic: str = ""
    ) -> str:
        evidence = ""
        if presented.resources:
            res_titles = [r.get("title", "") for r in presented.resources if r.get("title")]
            if res_titles:
                label = (
                    "学习资源"
                    if any(r.get("resource_kind") in {"video_candidate", "platform_search"}
                           for r in presented.resources)
                    else "论文资源"
                    if any(r.get("resource_kind") == "research_paper" or r.get("paper_id")
                           for r in presented.resources)
                    else "核验依据"
                )
                evidence = f"{label}：{res_titles[0]}"

        actions = []
        if presented.actions:
            actions = [
                {
                    "id": a.get("id", f"act_{idx}"),
                    "label": a.get("label", ""),
                    "icon": "sparkles",
                    "prompt": a.get("prompt", a.get("label", "")),
                    "topic": topic,
                }
                for idx, a in enumerate(presented.actions)
            ]
        msg = {
            "id": f"ai_{int(time.time() * 1000)}",
            "sender": "ai",
            "timestamp": datetime.now().strftime("%H:%M"),
            "meta": {
                "engine": "智能学习伙伴",
                "evidence": evidence,
                "status": presented.status_text or "回答完成",
            },
            "coreAnswer": presented.body,
            "generalKnowledge": {
                "title": "机制深度拓展",
                "content": presented.research_body,
            } if presented.research_body else None,
            "optionAnalysis": [],
            "resources": [
                {
                    **dict(item),
                    "resource_kind": (
                        item.get("resource_kind")
                        or ("research_paper" if item.get("paper_id") else "")
                    ),
                }
                for item in presented.resources
                if item.get("resource_kind") in {
                    "video_candidate", "platform_search", "research_paper"
                } or item.get("paper_id")
            ],
            "actions": actions,
        }
        return json.dumps(msg, ensure_ascii=False)

    def _format_session_messages_json(self, session) -> str:
        msgs = []
        for idx, m in enumerate(session.messages):
            if m.role == "user":
                msgs.append({
                    "id": f"msg_u_{idx}",
                    "sender": "user",
                    "text": m.body,
                    "timestamp": m.created_at[11:16] if len(m.created_at) >= 16 else "",
                })
            elif m.role == "assistant":
                evidence = ""
                if m.resources:
                    res_titles = [r.get("title", "") for r in m.resources if r.get("title")]
                    if res_titles:
                        label = (
                            "学习资源"
                            if any(r.get("resource_kind") in {"video_candidate", "platform_search"}
                                   for r in m.resources)
                            else "论文资源"
                            if any(r.get("resource_kind") == "research_paper" or r.get("paper_id")
                                   for r in m.resources)
                            else "核验依据"
                        )
                        evidence = f"{label}：{res_titles[0]}"
                msgs.append({
                    "id": f"msg_a_{idx}",
                    "sender": "ai",
                    "timestamp": m.created_at[11:16] if len(m.created_at) >= 16 else "",
                    "meta": {
                        "engine": "智能学习伙伴",
                        "evidence": evidence,
                        "status": "历史回答" if m.tone == "answer" else "提示",
                    },
                    "coreAnswer": m.body,
                    "generalKnowledge": None,
                    "optionAnalysis": [],
                    "resources": [
                        {
                            **dict(item),
                            "resource_kind": (
                                item.get("resource_kind")
                                or ("research_paper" if item.get("paper_id") else "")
                            ),
                        }
                        for item in m.resources
                        if item.get("resource_kind") in {
                            "video_candidate", "platform_search", "research_paper"
                        } or item.get("paper_id")
                    ],
                    "actions": [],
                })
        return json.dumps(msgs, ensure_ascii=False)

    # ==============================================================================
    # Sidebar Collapse / Expand Controller (QPropertyAnimation 220ms ease-out)
    # ==============================================================================
    @Slot()
    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        if hasattr(self, "sidebar_toggle_button") and self.sidebar_toggle_button is not None:
            self.sidebar_toggle_button.set_collapsed(self._sidebar_collapsed)

        start_w = self.sidebar.width()
        end_w = 0 if self._sidebar_collapsed else 240

        self._sidebar_anim.stop()
        if not self._sidebar_collapsed:
            self.sidebar.show()
            self.sidebar.setMinimumWidth(0)
            self._apply_sidebar_collapsed_ui(False)

        self._sidebar_anim.setStartValue(start_w)
        self._sidebar_anim.setEndValue(end_w)
        self._sidebar_anim.start()

        if hasattr(self, "web_bridge") and self.web_bridge is not None:
            self.web_bridge.sidebarCollapsedChanged.emit(self._sidebar_collapsed)

    def _on_sidebar_anim_finished(self) -> None:
        if self._sidebar_collapsed:
            self.sidebar.hide()
            self.sidebar.setMaximumWidth(0)
            self.sidebar.setMinimumWidth(0)
            self._apply_sidebar_collapsed_ui(True)
        else:
            self.sidebar.setMaximumWidth(240)
            self.sidebar.setMinimumWidth(240)

    def _apply_sidebar_collapsed_ui(self, collapsed: bool) -> None:
        if hasattr(self, "brand_mark") and self.brand_mark:
            self.brand_mark.setVisible(not collapsed)
        if hasattr(self, "brand_text_widget") and self.brand_text_widget:
            self.brand_text_widget.setVisible(not collapsed)
        if hasattr(self, "conversation_section_label") and self.conversation_section_label:
            self.conversation_section_label.setVisible(not collapsed)
        if hasattr(self, "workspace_section_label") and self.workspace_section_label:
            self.workspace_section_label.setVisible(not collapsed)
        if hasattr(self, "session_list") and self.session_list:
            self.session_list.setVisible(not collapsed)
        if hasattr(self, "delete_chat_button") and self.delete_chat_button:
            self.delete_chat_button.setVisible(not collapsed)

        btn_list = (
            getattr(self, "new_chat_button", None),
            getattr(self, "knowledge_button", None),
            getattr(self, "skills_button", None),
            getattr(self, "evaluation_button", None),
            getattr(self, "official_sources_button", None),
            getattr(self, "settings_button", None),
        )
        for btn in btn_list:
            if btn is not None:
                btn.setVisible(not collapsed)

    # ==============================================================================
    # Top Drawer Accordion Animation Controller
    # ==============================================================================
    @Slot()
    def _toggle_top_drawer(self) -> None:
        self._top_drawer_expanded = not self._top_drawer_expanded
        start_h = self.top_drawer.maximumHeight()
        is_research = (self.mode_combo.currentData() == "research")
        end_h = (180 if is_research else 96) if self._top_drawer_expanded else 0

        self.top_drawer_toggle_btn.setText("收起配置 ▴" if self._top_drawer_expanded else "⚙️ 模式与参数 ▾")
        self._top_drawer_anim.stop()
        self._top_drawer_anim.setStartValue(start_h)
        self._top_drawer_anim.setEndValue(end_h)
        self._top_drawer_anim.start()

    def _on_top_drawer_anim_step(self, val: Any) -> None:
        self.top_drawer.setMaximumHeight(int(val))

    def _update_send_button_state(self) -> None:
        has_text = bool(self.input_edit.toPlainText().strip())
        has_attachment = bool(self._image_path)
        self.send_button.set_has_input(has_text or has_attachment)

    def _refresh_session_list(self) -> None:
        self.session_list.blockSignals(True)
        self.session_list.clear()
        selected_row = 0
        sessions = self.conversation_store.list_sessions()
        for row, session in enumerate(sessions):
            item = QListWidgetItem(session.title)
            item.setData(Qt.UserRole, session.session_id)
            self.session_list.addItem(item)
            if session.session_id == self._session_id:
                selected_row = row
        self.session_list.setCurrentRow(selected_row)
        self.session_list.blockSignals(False)
        if hasattr(self, "web_bridge"):
            sessions_data = [
                {
                    "id": s.session_id,
                    "title": s.title,
                    "time": s.updated_at[11:16] if len(s.updated_at) >= 16 else "",
                    "subject": "医学课程",
                }
                for s in sessions
            ]
            self.web_bridge.sessionsUpdated.emit(json.dumps(sessions_data, ensure_ascii=False))

    def _render_current_session(self) -> None:
        self.history.clear()
        session = self.conversation_store.get_session(self._session_id)
        latest_resources: tuple[dict[str, Any], ...] = ()
        for message in session.messages:
            self._append_stored_message(message)
            if message.role == "assistant" and message.resources:
                latest_resources = tuple(message.resources)
        self._set_available_resources(latest_resources)
        if hasattr(self, "web_bridge"):
            msgs_json = self._format_session_messages_json(session)
            self.web_bridge.sessionSwitched.emit(self._session_id, msgs_json)

    @Slot()
    def _new_conversation(self) -> None:
        if self._thread is not None:
            return
        self._set_focus_mode(False)
        self._session_id = self.conversation_store.create_session().session_id
        self._refresh_session_list()
        self._render_current_session()
        self._show_startup_state()
        self.progress_panel.hide()
        self._last_result = None
        self._last_query = ""
        self._set_available_resources(())
        self.input_edit.setFocus()

    @Slot()
    def _delete_current_conversation(self) -> None:
        if self._thread is not None:
            return
        session = self.conversation_store.get_session(self._session_id)
        choice = QMessageBox.question(
            self,
            "删除对话",
            f"确定删除“{session.title}”吗？\n该对话及本地保存的题目图片会一并删除，无法恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self._answer_context_by_session.pop(self._session_id, None)
        self.conversation_store.delete_session(self._session_id)
        sessions = self.conversation_store.list_sessions()
        if not sessions:
            sessions = (self.conversation_store.create_session(),)
        self._session_id = sessions[0].session_id
        self._last_result = None
        self._last_query = ""
        self._set_available_resources(())
        self._progress_items.clear()
        self.progress_list.clear()
        self.progress_panel.hide()
        self._refresh_session_list()
        self._render_current_session()
        if not self.conversation_store.get_session(self._session_id).messages:
            self._show_startup_state()
        self.status_label.setText("对话已删除")

    @Slot(object, object)
    def _session_changed(self, current: Any, _: Any) -> None:
        if current is None or self._thread is not None:
            return
        session_id = current.data(Qt.UserRole)
        if session_id and session_id != self._session_id:
            self._set_focus_mode(False)
            self._session_id = str(session_id)
            self._last_result = None
            self._last_query = ""
            self._set_available_resources(())
            self.progress_panel.hide()
            self._render_current_session()

    def _populate_modes(self) -> None:
        self.subject_combo.blockSignals(True)
        self.subject_combo.clear()
        subject_options = (
            self.service.available_subjects()
            if self.service is not None
            else _fallback_subject_options()
        )
        for option in subject_options:
            self.subject_combo.addItem(option["display_name"], option["id"])
            index = self.subject_combo.count() - 1
            self.subject_combo.setItemData(index, option["description"], Qt.ToolTipRole)
        if self.service is not None:
            current_index = self.subject_combo.findData(self.service.current_subject)
            if current_index >= 0:
                self.subject_combo.setCurrentIndex(current_index)
        self.subject_combo.blockSignals(False)
        self.subject_badge.setText(self.subject_combo.currentText())

        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        options = (
            self.service.available_modes()
            if self.service is not None
            else _fallback_mode_options()
        )
        for option in options:
            self.mode_combo.addItem(option["display_name"], option["id"])
            index = self.mode_combo.count() - 1
            self.mode_combo.setItemData(index, option["description"], Qt.ToolTipRole)
        if self.service is not None:
            current_index = self.mode_combo.findData(self.service.current_mode)
            if current_index >= 0:
                self.mode_combo.setCurrentIndex(current_index)
        self.mode_combo.blockSignals(False)
        self.mode_badge.setText(self.mode_combo.currentText())

        self.horizon_combo.blockSignals(True)
        self.horizon_combo.clear()
        horizon_options = (
            self.service.available_medical_horizon_policies()
            if self.service is not None
            else (
                {"id": "off", "display_name": "关闭论文拓展", "description": "不访问论文数据库。"},
                {"id": "research_mode_only", "display_name": "仅科研模式拓展", "description": "只在科研模式检索论文。"},
                {"id": "all_answers", "display_name": "每次答题均拓展", "description": "每次课程答案通过后追加论文拓展。"},
            )
        )
        for option in horizon_options:
            self.horizon_combo.addItem(option["display_name"], option["id"])
            index = self.horizon_combo.count() - 1
            self.horizon_combo.setItemData(index, option["description"], Qt.ToolTipRole)
        if self.service is not None:
            current_index = self.horizon_combo.findData(
                self.service.current_medical_horizon_policy
            )
            if current_index >= 0:
                self.horizon_combo.setCurrentIndex(current_index)
        self.horizon_combo.blockSignals(False)
        self.horizon_badge.setText(self.horizon_combo.currentText())

    def _show_startup_state(self) -> None:
        if self.service is not None:
            self._append_system(
                "你可以输入医学课程题目，或添加题目截图。系统会先检索课程材料，"
                "通过引用与语义核验后再展示答案。"
            )
            return
        self._set_input_enabled(False)
        issues = "\n".join(f"• {item}" for item in self.startup_issues)
        self._append_presented(
            PresentedResult(
                title="需要完成配置",
                body=issues or "主程序尚未完成模型配置。",
                tone="warning",
                status_text="等待完成配置",
            )
        )

    def _show_official_sources(self) -> None:
        QMessageBox.information(
            self,
            "官方证据源状态",
            self._official_source_status_text(),
        )

    def _official_source_status_text(self) -> str:
        if self.service is None:
            return "应用尚未完成配置，暂时无法读取官方证据源状态。"
        labels = {
            "automatic": "自动检索与全文核验",
            "license_required": "需要另行申请正式接口许可",
            "official_search_only": "官方入口人工复核",
        }
        lines = []
        for source in self.service.available_official_guideline_sources():
            state = labels.get(
                source["automation_status"],
                source["automation_status"],
            )
            lines.append(
                f"{source['display_name']}：{state}\n"
                f"  {source['requirement']}\n"
                f"  {source['search_url']}"
            )
        return "\n\n".join(lines)

    @Slot(int)
    def _on_subject_changed(self, _: int) -> None:
        if self.service is None:
            return
        subject_id = self.subject_combo.currentData()
        try:
            selected = self.service.set_subject(subject_id)
        except ValueError as exc:
            QMessageBox.warning(self, "学科切换失败", str(exc))
            return
        self.status_label.setText(f"已切换学科：{self.subject_combo.currentText()}")
        self.subject_badge.setText(self.subject_combo.currentText())
        if hasattr(self, "web_bridge"):
            self.web_bridge.subjectChanged.emit(self.subject_combo.currentData() or "auto")
            self.web_bridge.statusChanged.emit(self.status_label.text())

    @Slot(int)
    def _on_mode_changed(self, _: int) -> None:
        if self.service is None:
            return
        mode_id = self.mode_combo.currentData()
        try:
            selected = self.service.set_mode(mode_id)
        except ValueError as exc:
            QMessageBox.warning(self, "模式切换失败", str(exc))
            return
        self.status_label.setText(f"已切换模式：{selected}")
        self.mode_badge.setText(self.mode_combo.currentText())
        self._sync_research_controls()
        if hasattr(self, "web_bridge"):
            self.web_bridge.modeChanged.emit(self.mode_combo.currentData() or "auto")
            self.web_bridge.statusChanged.emit(self.status_label.text())

    @Slot(int)
    def _on_horizon_changed(self, _: int) -> None:
        if self.service is None:
            return
        policy_id = self.horizon_combo.currentData()
        try:
            selected = self.service.set_medical_horizon_policy(policy_id)
        except ValueError as exc:
            QMessageBox.warning(self, "医学拓展切换失败", str(exc))
            return
        labels = {
            "off": "已关闭论文拓展",
            "research_mode_only": "仅科研模式追加论文拓展",
            "all_answers": "每次答题均追加论文拓展",
        }
        self.status_label.setText(labels.get(selected, f"医学拓展：{selected}"))
        self.horizon_badge.setText(self.horizon_combo.currentText())
        if hasattr(self, "web_bridge"):
            self.web_bridge.horizonChanged.emit(self.horizon_combo.currentData() or "research_mode_only")
            self.web_bridge.statusChanged.emit(self.status_label.text())

    def _sync_research_controls(self) -> None:
        mode_id = self.mode_combo.currentData()
        is_research = (mode_id == "research")
        self.research_panel.setVisible(is_research)
        if self._top_drawer_expanded:
            target = 180 if is_research else 96
            self._top_drawer_anim.stop()
            self._top_drawer_anim.setStartValue(self.top_drawer.maximumHeight())
            self._top_drawer_anim.setEndValue(target)
            self._top_drawer_anim.start()

    def _research_filters(self) -> ResearchFilters | None:
        if self.mode_combo.currentData() != "research":
            return None
        year_from = self.research_year_from.currentData()
        year_to = self.research_year_to.currentData()
        if year_from and year_to and year_from > year_to:
            raise ValueError("科研检索起始年份不能晚于结束年份")
        return ResearchFilters(
            year_from=year_from,
            year_to=year_to,
            study_types=tuple(self.research_study_type.currentData() or ()),
        )

    @Slot()
    def _choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择题目图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not path:
            return
        self._image_path = path
        self.attachment_label.setText(Path(path).name)
        self.attach_button.setText("更换图片")
        self._update_send_button_state()
        if hasattr(self, "web_bridge"):
            self.web_bridge.attachmentChanged.emit(Path(path).name)

    @Slot()
    def _send(self) -> None:
        if self.service is None or self._thread is not None:
            return
        query = self.input_edit.toPlainText().strip()
        learning_action_id = self._pending_learning_action_id
        learning_topic = self._pending_learning_topic
        self._pending_learning_action_id = None
        self._pending_learning_topic = ""
        if not query and not self._image_path:
            self.status_label.setText("请输入问题或添加图片")
            self.input_edit.setFocus()
            return

        try:
            research_filters = self._research_filters()
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return

        image_path = self._image_path
        original_image_name = Path(image_path).name if image_path else ""
        attachment: dict[str, str] | None = None
        if image_path and self.conversation_store.path is not None:
            try:
                attachment = self.conversation_store.save_image(
                    self._session_id, image_path
                )
                retained = self.conversation_store.resolve_image(
                    self._session_id, attachment
                )
                if retained is None:
                    raise OSError("本地图片保存后无法读取")
                image_path = str(retained)
            except (OSError, ValueError) as exc:
                self.status_label.setText(f"保存题目图片失败：{exc}")
                return
        image_media_type = (
            mimetypes.guess_type(image_path)[0] if image_path else None
        )
        summary = query or "[图片题目]"
        if image_path:
            summary += f"\n附件：{original_image_name}"
        session = self.conversation_store.get_session(self._session_id)
        conversation_context = conversation_context_for_session(session)
        prior_result, prior_query = self._answer_context_by_session.get(
            self._session_id, (None, "")
        )
        prior_image = self.conversation_store.latest_image_for_topic(
            self._session_id
        )
        self._append_user(summary)
        self.conversation_store.append_message(
            self._session_id,
            role="user",
            title="你",
            body=summary,
            tone="user",
            attachments=[attachment] if attachment else None,
        )
        self._refresh_session_list()
        self.input_edit.clear()
        self._clear_attachment()
        self._set_available_resources(())
        self._set_learning_actions(())
        self._set_busy(True)
        self._active_query = query

        thread = QThread(self)
        worker = AgentWorker(
            self.service,
            query=query,
            image=image_path,
            image_media_type=image_media_type,
            research_filters=research_filters,
            conversation_context=conversation_context,
            conversation_key=self._session_id,
            prior_result=prior_result,
            prior_query=prior_query,
            prior_image=str(prior_image) if prior_image is not None else None,
            learning_action_id=learning_action_id,
            learning_topic=learning_topic,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_result)
        worker.failed.connect(self._on_worker_error)
        worker.cancelled.connect(self._on_worker_cancelled)
        worker.progressed.connect(self._on_progress)
        worker.approval_requested.connect(self._on_approval_requested)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _on_result(self, result: Any) -> None:
        self._last_result = result
        self._last_query = (
            result.plan.topic_query
            if result.plan is not None and (
                result.answer_source == "answer_challenge" or not self._active_query
            )
            else self._active_query
        )
        if (
            result.status == "completed"
            and result.rendered_answer
            and result.answer_source not in {
                "video_learning", "learning_feedback", "casual_chat"
            }
        ):
            self._answer_context_by_session[self._session_id] = (
                result,
                self._last_query,
            )
        self._set_available_resources(tuple(result.external_resources or ()))
        presented = present_agent_result(result)
        current_topic = result.plan.topic_query if result.plan is not None else ""
        self._set_learning_actions(presented.actions, current_topic=current_topic)
        self._append_presented(presented)
        self._save_presented(
            presented,
            question_context=(
                result.plan.topic_query
                if result.status in {"completed", "review_required"}
                and result.plan is not None
                and result.answer_source not in {
                    "video_learning", "learning_feedback", "casual_chat"
                }
                else ""
            ),
        )
        self.status_label.setText(presented.status_text)
        if hasattr(self, "web_bridge"):
            self.web_bridge.answerReceived.emit(
                self._format_ai_result_json(presented, topic=current_topic)
            )
            self.web_bridge.statusChanged.emit(presented.status_text)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        presented = PresentedResult("运行失败", message, "error", "运行失败")
        self._append_presented(presented)
        self._save_presented(presented)
        self.status_label.setText("运行失败")
        if hasattr(self, "web_bridge"):
            self.web_bridge.answerReceived.emit(self._format_ai_result_json(presented))
            self.web_bridge.statusChanged.emit("运行失败")

    @Slot(str)
    def _on_worker_cancelled(self, message: str) -> None:
        timed_out = "时限" in message or "超时" in message
        title = "回答超时" if timed_out else "已停止回答"
        body = (
            "本轮任务超过总时限，已在安全检查点停止。你可以缩小问题范围后重试。"
            if timed_out else
            "本轮回答已停止；已经显示的过程仅用于说明进度，没有发布未完成答案。"
        )
        presented = PresentedResult(title, body, "warning", title)
        self._append_presented(presented)
        self._save_presented(presented)
        self.status_label.setText(title)
        if hasattr(self, "web_bridge"):
            self.web_bridge.answerReceived.emit(self._format_ai_result_json(presented))
            self.web_bridge.statusChanged.emit(title)

    @Slot(object)
    def _on_progress(self, update: Any) -> None:
        if not isinstance(update, ProgressUpdate):
            return
        if hasattr(self, "web_bridge"):
            self.web_bridge.progressUpdated.emit(update.phase_id, update.message, update.state)
        starting = self._progress_items.pop("starting", None)
        if starting is not None:
            row = self.progress_list.row(starting)
            if row >= 0:
                self.progress_list.takeItem(row)
        icons = {
            "ok": "✓",
            "pass": "✓",
            "running": "●",
            "warning": "!",
            "empty": "○",
            "failed": "×",
            "fail": "×",
            "needs_review": "!",
            "needs_input": "?",
            "cancelled": "×",
        }
        item = self._progress_items.get(update.phase_id)
        if item is None:
            item = QListWidgetItem()
            self._progress_items[update.phase_id] = item
            self.progress_list.addItem(item)
        item.setText(f"{icons.get(update.state, '•')} {update.message}")
        self.progress_list.scrollToBottom()
        self._status_animator.update_base_text(update.message)

    @Slot(object)
    def _on_approval_requested(self, request: Any) -> None:
        if not isinstance(request, ApprovalRequest):
            return
        self._pending_approval = request
        self.approval_title.setText(request.title)
        self.approval_description.setText(request.description)
        self.approve_button.setText(request.action_label)
        self.approval_panel.show()
        self.status_label.setText("任务已暂停，等待你的允许")
        if hasattr(self, "web_bridge"):
            self._emit_web_approval(request)
            self.web_bridge.statusChanged.emit(self.status_label.text())

    @Slot()
    def _update_elapsed_time(self) -> None:
        if self._run_started_at is None:
            self.elapsed_label.setText("0 秒")
            return
        seconds = max(0, int(time.monotonic() - self._run_started_at))
        self.elapsed_label.setText(f"{seconds} 秒")

    def _resolve_approval(self, approved: bool) -> None:
        if self._pending_approval is None or self._worker is None:
            return
        self._worker.resolve_approval(approved)
        self._pending_approval = None
        self.approval_panel.hide()
        if hasattr(self, "web_bridge"):
            self.web_bridge.approvalCleared.emit()
        decision = "已允许，继续执行" if approved else "已拒绝，正在安全停止"
        self.status_label.setText(decision)
        if hasattr(self, "web_bridge"):
            self.web_bridge.statusChanged.emit(decision)

    @Slot()
    def _stop_current_answer(self) -> None:
        self._request_stop(timed_out=False)

    @Slot()
    def _timeout_current_answer(self) -> None:
        self._request_stop(timed_out=True)

    def _request_stop(self, *, timed_out: bool) -> None:
        if self._worker is None:
            return
        self._worker.request_cancel(timed_out=timed_out)
        self.stop_button.setEnabled(False)
        self.status_label.setText(
            "本轮已达到总时限，正在安全停止……"
            if timed_out else "正在安全停止本轮回答……"
        )

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_busy(False)
        self.progress_panel.hide()
        if hasattr(self, "web_bridge"):
            self.web_bridge.approvalCleared.emit()
        self.process_button.setText("查看过程")
        if self._close_after_cancel:
            self._close_after_cancel = False
            QTimer.singleShot(0, self.close)

    @Slot()
    def _toggle_progress_panel(self) -> None:
        if self._pending_approval is not None:
            self.progress_panel.show()
            self.process_button.setText("隐藏过程")
            self.status_label.setText("请先处理等待中的授权请求")
            return
        visible = not self.progress_panel.isHidden()
        if visible:
            self.progress_panel.hide()
            self.process_button.setText("查看过程")
        else:
            self.progress_panel.show()
            try:
                effect = QGraphicsOpacityEffect(self.progress_panel)
                self.progress_panel.setGraphicsEffect(effect)
                anim = QPropertyAnimation(effect, b"opacity", self.progress_panel)
                anim.setDuration(220)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.finished.connect(lambda: self.progress_panel.setGraphicsEffect(None))
                self.progress_panel._fade_anim = anim
                anim.start()
            except Exception:
                pass
            self.process_button.setText("隐藏过程")

    @Slot()
    def _toggle_focus_mode(self) -> None:
        self._set_focus_mode(not self._focus_mode)

    def _set_focus_mode(self, enabled: bool) -> None:
        if enabled and self._thread is not None:
            self.status_label.setText("任务完成后可进入专注阅读")
            return
        self._focus_mode = bool(enabled)
        for widget in (
            self.sidebar,
            self.top_bar,
            self.composer,
            self.status_bar,
            self.research_panel,
            self.progress_panel,
        ):
            widget.setVisible(not self._focus_mode)
        if hasattr(self, "focus_button") and self.focus_button is not None:
            self.focus_button.setText("退出专注（Esc）" if enabled else "专注阅读")
        if not enabled and self._thread is None:
            self.progress_panel.hide()

    def _set_available_resources(
        self,
        resources: tuple[dict[str, Any], ...],
    ) -> None:
        self._last_resources = resources
        self.sources_button.setEnabled(bool(resources))
        self.sources_button.setText(
            f"全部来源 ({len(resources)})" if resources else "全部来源"
        )

    @Slot()
    def _show_all_resources(self) -> None:
        if not self._last_resources:
            return
        ResourceDialog(self._last_resources, self).exec()

    def _set_busy(self, busy: bool) -> None:
        enabled = not busy and self.service is not None
        self.input_edit.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.attach_button.setEnabled(enabled)
        self.subject_combo.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.horizon_combo.setEnabled(enabled)
        self.research_panel.setEnabled(enabled)
        self.session_list.setEnabled(enabled)
        self.new_chat_button.setEnabled(enabled)
        self.delete_chat_button.setEnabled(enabled)
        self.skills_button.setEnabled(enabled)
        self.evaluation_button.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)
        self.knowledge_button.setEnabled(enabled)
        self.official_sources_button.setEnabled(enabled)
        if getattr(self, "focus_button", None) is not None:
            self.focus_button.setEnabled(enabled)
        if busy:
            self._progress_items.clear()
            self.progress_list.clear()
            initial = QListWidgetItem("● 正在分析请求并规划下一步")
            self._progress_items["starting"] = initial
            self.progress_list.addItem(initial)
            self._run_started_at = time.monotonic()
            self.elapsed_label.setText("0 秒")
            self._elapsed_timer.start()
            self._run_timeout_timer.start(180_000)
            self.stop_button.setEnabled(True)
            self.stop_button.show()
            self._pending_approval = None
            self.approval_panel.hide()
            self.progress_panel.show()
            self.process_button.setText("隐藏过程")
            self.progress.setRange(0, 0)
            self._status_animator.start("正在深度思考与检索……")
        else:
            self._elapsed_timer.stop()
            self._run_timeout_timer.stop()
            self.stop_button.hide()
            self._status_animator.stop()
            self._run_started_at = None
            self._pending_approval = None
            self.approval_panel.hide()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        self._update_send_button_state()
        if hasattr(self, "web_bridge"):
            self.web_bridge.generatingStateChanged.emit(busy)

    def _set_input_enabled(self, enabled: bool) -> None:
        self.input_edit.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.attach_button.setEnabled(enabled)
        self.subject_combo.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.horizon_combo.setEnabled(enabled)
        self.research_panel.setEnabled(enabled)
        self.session_list.setEnabled(enabled)
        self.new_chat_button.setEnabled(enabled)
        self.delete_chat_button.setEnabled(enabled)
        self.skills_button.setEnabled(enabled)
        self.evaluation_button.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)
        self.knowledge_button.setEnabled(enabled)
        self.official_sources_button.setEnabled(enabled)
        if getattr(self, "focus_button", None) is not None:
            self.focus_button.setEnabled(enabled)
        if hasattr(self, "web_bridge"):
            self.web_bridge.generatingStateChanged.emit(not enabled)

    def _clear_attachment(self) -> None:
        self._image_path = None
        self.attachment_label.setText("可拖入图片或 PDF")
        self.attach_button.setText("添加图片")
        self._update_send_button_state()
        if hasattr(self, "web_bridge"):
            self.web_bridge.attachmentChanged.emit("")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._supported_drop_paths(event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_drag_active(True)
            self.status_label.setText("松开即可添加图片或导入 PDF")
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag_active(False)
        paths = self._supported_drop_paths(event.mimeData().urls())
        if not paths:
            event.ignore()
            return
        self._handle_dropped_paths(paths)
        event.acceptProposedAction()

    def _set_drag_active(self, active: bool) -> None:
        self.composer.setProperty("dragActive", active)
        self.composer.style().unpolish(self.composer)
        self.composer.style().polish(self.composer)
        self.composer._apply_border_color(self.composer._current_border)

    @staticmethod
    def _supported_drop_paths(urls: Any) -> list[str]:
        supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"}
        paths = []
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in supported:
                paths.append(str(path))
        return paths

    def _handle_dropped_paths(self, paths: list[str]) -> None:
        if self._thread is not None:
            self.status_label.setText("请等待当前回答完成后再添加文件")
            return
        self._set_focus_mode(False)
        image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        images = [path for path in paths if Path(path).suffix.lower() in image_suffixes]
        pdfs = [path for path in paths if Path(path).suffix.lower() == ".pdf"]
        messages = []
        if images:
            self._image_path = images[0]
            self.attachment_label.setText(Path(images[0]).name)
            self.attach_button.setText("更换图片")
            self._update_send_button_state()
            if hasattr(self, "web_bridge"):
                self.web_bridge.attachmentChanged.emit(Path(images[0]).name)
            messages.append(f"已添加图片：{Path(images[0]).name}")
            if len(images) > 1:
                messages.append("当前每次提问只支持一张图片，其余图片未添加")
        if pdfs:
            self.knowledge_files_dropped.emit(pdfs)
            messages.append(f"已将 {len(pdfs)} 份 PDF 送往课程知识库")
        self.status_label.setText("；".join(messages) or "未发现支持的文件")

    def _append_user(self, text: str) -> None:
        self._append_card("你", text, "#F4F4F5", "#18181B", align="right", border="#EAEAEA")

    def _append_system(self, text: str) -> None:
        self._append_card("学习助手", text, "#F8F9FA", "#52525B", border="#EEEEEE", left_accent="#5B6CFF")

    def _append_presented(self, result: PresentedResult) -> None:
        palette = {
            "answer": ("#FFFFFF", "#18181B", "#EEEEEE", "#5B6CFF"),
            "clarification": ("#F4F5FF", "#18181B", "#D5DAFF", "#5B6CFF"),
            "warning": ("#FFFBEB", "#111827", "#FDE68A", "#F59E0B"),
            "error": ("#FEF2F2", "#111827", "#FECACA", "#EF4444"),
        }
        background, foreground, border, left_accent = palette.get(
            result.tone, ("#FFFFFF", "#18181B", "#EEEEEE", "#5B6CFF")
        )
        self._append_card(
            result.title, result.body, background, foreground, border=border, left_accent=left_accent
        )
        if result.research_body:
            research_palette = {
                "pass": ("#ECFDF5", "#065F46", "#A7F3D0", "#10B981"),
                "review": ("#FFFBEB", "#92400E", "#FDE68A", "#F59E0B"),
                "insufficient": ("#FFFBEB", "#92400E", "#FDE68A", "#F59E0B"),
                "fail": ("#FEF2F2", "#991B1B", "#FECACA", "#EF4444"),
                "error": ("#FEF2F2", "#991B1B", "#FECACA", "#EF4444"),
            }
            res_bg, res_fg, res_border, res_left = research_palette.get(
                result.research_status, ("#EEF2FF", "#111827", "#C7D2FE", "#4D6BFE")
            )
            self._append_card(
                "科研证据核验", result.research_body,
                res_bg, res_fg, border=res_border, left_accent=res_left
            )
        for resource in result.resources:
            self._append_research_resource(resource)

    def _set_learning_actions(
        self,
        actions: tuple[dict[str, str], ...] | list[dict[str, str]],
        *,
        current_topic: str = "",
    ) -> None:
        if current_topic:
            self._current_learning_topic = current_topic
        while self.learning_actions_layout.count():
            item = self.learning_actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for action in list(actions)[:6]:
            prompt = str(action.get("prompt", "")).strip()
            label = str(action.get("label", "")).strip()
            action_id = str(action.get("id", "")).strip()
            if not prompt or not label or not action_id:
                continue
            button = AnimatedLearningButton(label)
            button.clicked.connect(
                lambda checked=False, aid=action_id, topic=current_topic, value=prompt:
                    self._trigger_learning_action(aid, topic, value)
            )
            self.learning_actions_layout.addWidget(button)
        self.learning_actions_layout.addStretch()
        has_actions = self.learning_actions_layout.count() > 1
        if has_actions:
            self.learning_actions_panel.show()
            try:
                effect = QGraphicsOpacityEffect(self.learning_actions_panel)
                self.learning_actions_panel.setGraphicsEffect(effect)
                anim = QPropertyAnimation(effect, b"opacity", self.learning_actions_panel)
                anim.setDuration(220)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.finished.connect(lambda: self.learning_actions_panel.setGraphicsEffect(None))
                self.learning_actions_panel._fade_anim = anim
                anim.start()
            except Exception:
                pass
        else:
            self.learning_actions_panel.hide()

    def _trigger_learning_action(
        self,
        action_id: str,
        topic: str,
        prompt: str,
    ) -> None:
        if self._thread is not None:
            return
        self._pending_learning_action_id = action_id
        self._pending_learning_topic = topic
        self.input_edit.setPlainText(prompt)
        self._send()

    def _save_presented(
        self,
        result: PresentedResult,
        *,
        question_context: str = "",
    ) -> None:
        body_parts = [result.body]
        if result.research_body:
            body_parts.append(result.research_body)
        self.conversation_store.append_message(
            self._session_id,
            role="assistant",
            title=result.title,
            body="\n\n".join(part for part in body_parts if part),
            tone=result.tone,
            resources=list(self._last_resources),
            question_context=question_context,
        )
        self._refresh_session_list()

    def _append_stored_message(self, message: ConversationMessage) -> None:
        if message.role == "user":
            self._append_user(message.body)
            return
        palette = {
            "answer": ("#FFFFFF", "#18181B", "#EEEEEE", "#5B6CFF"),
            "clarification": ("#F4F5FF", "#18181B", "#D5DAFF", "#5B6CFF"),
            "warning": ("#FFFBEB", "#111827", "#FDE68A", "#F59E0B"),
            "error": ("#FEF2F2", "#111827", "#FECACA", "#EF4444"),
            "system": ("#F8F9FA", "#4B5563", "#E5E7EB", "#4D6BFE"),
        }
        background, foreground, border, left_accent = palette.get(
            message.tone, ("#FFFFFF", "#111827", "#E5E7EB", "#4D6BFE")
        )
        self._append_card(message.title, message.body, background, foreground, border=border, left_accent=left_accent)

    def _smooth_scroll_to_bottom(self) -> None:
        scrollbar = self.history.verticalScrollBar()
        target = scrollbar.maximum()
        if abs(scrollbar.value() - target) < 10:
            scrollbar.setValue(target)
            return
        try:
            anim = QPropertyAnimation(scrollbar, b"value", self)
            anim.setDuration(240)
            anim.setStartValue(scrollbar.value())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._scroll_anim = anim
            anim.start()
        except Exception:
            scrollbar.setValue(target)

    def _append_card(
        self,
        title: str,
        body: str,
        background: str,
        foreground: str,
        *,
        align: str = "left",
        border: str = "#E5E7EB",
        left_accent: str = "",
    ) -> None:
        safe_title = html.escape(title)
        safe_body = html.escape(body).replace("\n", "<br>")
        empty_width = "18%" if align == "right" else "0%"
        trailing_width = "0%" if align == "right" else "12%"
        border_style = f"border: 1px solid {border};"
        if left_accent:
            border_style += f" border-left: 3.5px solid {left_accent};"
        title_color = "#5B6CFF" if align == "right" else "#18181B"
        border_radius = "16px"
        self.history.append(
            '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:6px;margin-bottom:6px;"><tr>'
            f'<td width="{empty_width}"></td>'
            f'<td style="background-color:{background};color:{foreground};'
            f'padding:14px 18px;border-radius:{border_radius};{border_style}">'
            f'<span style="font-size:12px;font-weight:700;color:{title_color};">{safe_title}</span>'
            f'<br><span style="line-height:1.6;font-size:13px;">{safe_body}</span></td>'
            f'<td width="{trailing_width}"></td></tr></table>'
        )
        self._smooth_scroll_to_bottom()

    def _append_research_resource(self, resource: dict[str, Any]) -> None:
        original_title = str(resource.get("title") or "未命名论文")
        chinese_title = str(resource.get("title_zh") or "").strip()
        title = html.escape(chinese_title or original_title)
        raw_url = str(resource.get("url") or "")
        safe_url = html.escape(raw_url, quote=True)
        link = (
            f'<a href="{safe_url}" style="color:#4D6BFE;text-decoration:none;font-weight:600;">{title}</a>'
            if raw_url.startswith(("https://", "http://"))
            else f'<span style="color:#111827;font-weight:600;">{title}</span>'
        )
        resource_kind = str(resource.get("resource_kind") or "")
        platform = str(resource.get("platform") or "").strip()
        sources = " + ".join(str(item) for item in resource.get("sources", []))
        badges = [sources] if sources else []
        if platform:
            badges.append(platform)
        if resource.get("cross_validated"):
            badges.append("多个数据库记录一致")
        if resource.get("year"):
            badges.append(str(resource["year"]))
        intro = html.escape(
            str(resource.get("description") or "")
            if resource_kind in {"video_candidate", "platform_search"}
            else friendly_resource_intro(
                resource.get("description"),
                resource.get("evidence_level"),
                resource.get("publication_types", ()),
                resource.get("authority_reason"),
            )
        )
        badge_text = " · ".join(html.escape(item) for item in badges)
        original_line = (
            f'<br><span style="font-size:11px;color:#9CA3AF;">{html.escape(original_title)}</span>'
            if chinese_title and chinese_title != original_title
            else ""
        )
        kind_label = (
            "视频候选"
            if resource_kind == "video_candidate"
            else "平台搜索入口"
            if resource_kind == "platform_search"
            else "延伸阅读"
        )
        intro_label = (
            "推荐说明"
            if resource_kind == "video_candidate"
            else "搜索说明"
            if resource_kind == "platform_search"
            else "资料简介"
        )
        self.history.append(
            '<table width="88%" cellspacing="0" cellpadding="0" style="margin:6px 0;">'
            '<tr><td style="background-color:#FFFFFF;color:#111827;padding:12px 14px;'
            'border:1px solid #E5E7EB;border-left:3px solid #4D6BFE;border-radius:10px;">'
            f'<span style="font-size:11px;font-weight:700;color:#4D6BFE;">{kind_label}</span><br>'
            f'{link}{original_line}<br><span style="color:#4D6BFE;font-size:12px;font-weight:600;">{badge_text}</span>'
            f'<br><span style="line-height:1.5;color:#4B5563;"><b>{intro_label}：</b>{intro}</span></td></tr></table>'
        )
        self._smooth_scroll_to_bottom()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            if self._close_after_cancel:
                event.ignore()
                return
            answer = QMessageBox.question(
                self,
                "任务仍在运行",
                "当前回答仍在运行。是否停止本轮回答，并在安全退出后关闭窗口？",
            )
            if answer == QMessageBox.Yes:
                self._close_after_cancel = True
                self._request_stop(timed_out=False)
            event.ignore()
            return
        event.accept()


def _fallback_mode_options() -> tuple[dict[str, str], ...]:
    from interaction_modes import mode_options

    return mode_options()


def _fallback_subject_options() -> tuple[dict[str, str], ...]:
    from subject_profiles import subject_options

    return subject_options()
