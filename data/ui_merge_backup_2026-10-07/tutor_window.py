"""PySide6 desktop shell for the physiology tutor application service."""

from __future__ import annotations

import html
import mimetypes
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_runtime import ApprovalRequest, ProgressUpdate, TutorApplicationService
from conversation_store import ConversationMessage, ConversationStore
from evidence_labels import friendly_resource_intro
from research_evidence import ResearchFilters
from resource_dialog import ResourceDialog
from ui_presenter import PresentedResult, present_agent_result


APP_QSS = """
QWidget {
    background: #F4F7FA;
    color: #172B3A;
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 13px;
}
QLabel { background: transparent; }
QFrame#Sidebar { background: #102C3C; border: none; border-radius: 16px; }
QFrame#Sidebar QLabel { background: transparent; color: #D6E4EA; }
QLabel#BrandMark {
    background: #19A58C; color: white; border-radius: 18px;
    font-size: 15px; font-weight: 800; qproperty-alignment: AlignCenter;
}
QLabel#BrandTitle { color: white; font-size: 18px; font-weight: 800; }
QLabel#BrandSubtitle { color: #8FB2BF; font-size: 11px; }
QLabel#SidebarSection {
    color: #7FA4B2; font-size: 11px; font-weight: 700;
    padding: 9px 3px 4px 3px;
}
QPushButton#NewChat {
    background: #19A58C; color: white; border: none; border-radius: 9px;
    padding: 10px 12px; font-weight: 700; text-align: left;
}
QPushButton#NewChat:hover { background: #22B89E; }
QPushButton#DeleteChat {
    background: transparent; color: #91AFBA; border: none; border-radius: 7px;
    padding: 6px 9px; text-align: left; font-size: 11px;
}
QPushButton#DeleteChat:hover { background: #4A2D35; color: #FFD8DC; }
QPushButton#SideAction {
    background: transparent; color: #D9E7EC; border: none; border-radius: 8px;
    padding: 9px 10px; text-align: left;
}
QPushButton#SideAction:hover { background: #1B4052; color: white; }
QFrame#SecurityCard { background: #183B4B; border: 1px solid #285365; border-radius: 10px; }
QFrame#SecurityCard QLabel { color: #AFC8D1; font-size: 11px; }
QListWidget#SessionList { background: transparent; border: none; outline: none; color: #CFE0E6; }
QListWidget#SessionList::item { padding: 9px 10px; margin: 1px 0; border-radius: 7px; }
QListWidget#SessionList::item:hover { background: #17394A; color: white; }
QListWidget#SessionList::item:selected { background: #214D5E; color: white; }
QFrame#TopBar, QFrame#Composer, QFrame#StatusBar {
    background: white; border: 1px solid #DFE7EC; border-radius: 14px;
}
QFrame#Composer[dragActive="true"] {
    background: #F0FAF7; border: 2px dashed #19A58C;
}
QFrame#ReadingToolbar {
    background: white; border: 1px solid #DFE7EC; border-radius: 10px;
}
QPushButton#ToolbarButton {
    background: transparent; color: #4C6672; padding: 6px 10px; font-size: 11px;
}
QPushButton#ToolbarButton:hover { background: #EAF2F4; color: #173E49; }
QPushButton#FocusButton {
    background: #E7F7F3; color: #087563; padding: 6px 11px; font-size: 11px;
}
QPushButton#FocusButton:hover { background: #D4EFE8; }
QLabel#Title { font-size: 20px; font-weight: 800; color: #122A38; }
QLabel#Subtle { color: #70828C; }
QLabel#ControlLabel { color: #536B78; font-size: 11px; font-weight: 700; }
QLabel#EvidenceChip {
    background: #E7F7F3; color: #087865; border: 1px solid #B9E9DE;
    border-radius: 11px; padding: 4px 9px; font-size: 11px; font-weight: 700;
}
QLabel#ShortcutChip {
    background: #F0F4F7; color: #6B7F89; border-radius: 9px;
    padding: 3px 8px; font-size: 11px;
}
QComboBox {
    min-width: 145px; padding: 8px 10px; background: #F8FAFB;
    border: 1px solid #D7E1E7; border-radius: 8px;
}
QComboBox:hover { border-color: #8BB9B0; background: white; }
QComboBox:focus { border: 1px solid #159780; background: white; }
QComboBox::drop-down { border: none; width: 25px; }
QFrame#ResearchPanel { background: #F0FAF7; border: 1px solid #C5E9E0; border-radius: 11px; }
QFrame#ProgressPanel { background: #132B36; border: 1px solid #284653; border-radius: 11px; }
QFrame#ProgressPanel QLabel { color: #C9DCE3; }
QLabel#RunTitle { color: white; font-weight: 700; }
QLabel#Elapsed { color: #86A5B1; font-size: 11px; }
QListWidget#ProgressList { color: #C9DCE3; background: transparent; border: none; }
QListWidget#ProgressList::item { padding: 4px 6px; }
QListWidget#ProgressList::item:selected { background: transparent; color: #C9DCE3; }
QFrame#ApprovalCard {
    background: #FFF8E8; border: 1px solid #E7C978; border-radius: 9px;
}
QFrame#ApprovalCard QLabel { color: #5F4B1E; }
QPushButton#Approve { background: #138A77; color: white; font-weight: 700; }
QPushButton#Approve:hover { background: #087563; }
QPushButton#Reject { background: transparent; color: #805F22; border: 1px solid #D7B865; }
QPushButton#Reject:hover { background: #F7E9C4; }
QTextBrowser#Conversation {
    background: #FBFCFD; border: 1px solid #DEE7EC; border-radius: 14px;
    padding: 16px; selection-background-color: #BCE9DF;
}
QTextEdit#QuestionInput {
    background: #FBFCFD; border: 1px solid #D5E0E6; border-radius: 10px;
    padding: 11px; selection-background-color: #BCE9DF;
}
QTextEdit#QuestionInput:focus { border: 1px solid #159780; background: white; }
QPushButton {
    padding: 9px 15px; border: none; border-radius: 8px;
    background: #EAF0F3; color: #294553; font-weight: 600;
}
QPushButton:hover { background: #DCE7EC; }
QPushButton#Attach { background: #EDF4F5; color: #245A63; }
QPushButton#Send {
    min-width: 92px; background: #138A77; color: white; font-weight: 700;
}
QPushButton#Send:hover { background: #087563; }
QPushButton#LearningAction {
    background: #F0FAF7; color: #0F766E; border: 1px solid #BFE5DC;
    padding: 6px 10px; border-radius: 10px; font-size: 11px;
}
QPushButton#LearningAction:hover { background: #DDF3EE; border-color: #78C8B7; }
QPushButton:disabled { background: #E8EDF0; color: #A3B0B7; }
QProgressBar { border: none; border-radius: 2px; background: #E1E9ED; height: 4px; }
QProgressBar::chunk { border-radius: 2px; background: #19A58C; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 7px; border-radius: 6px; }
QListWidget::item:selected { background: #DDF3EE; color: #0B6F5F; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #C8D5DB; min-height: 30px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #9FB2BC; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class AgentWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
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
    ) -> None:
        super().__init__()
        self._service = service
        self._query = query
        self._image = image
        self._image_media_type = image_media_type
        self._research_filters = research_filters
        self._conversation_context = list(conversation_context or [])
        self._approval_event = Event()
        self._approval_decision = False

    def resolve_approval(self, approved: bool) -> None:
        self._approval_decision = bool(approved)
        self._approval_event.set()

    def _await_approval(self, request: ApprovalRequest) -> bool:
        self._approval_decision = False
        self._approval_event.clear()
        self.approval_requested.emit(request)
        self._approval_event.wait()
        return self._approval_decision

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.ask(
                self._query,
                image=self._image,
                image_media_type=self._image_media_type,
                research_filters=self._research_filters,
                progress_callback=self.progressed.emit,
                approval_callback=self._await_approval,
                conversation_context=self._conversation_context,
            )
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


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
    ) -> None:
        super().__init__()
        self.service = service
        self.startup_issues = startup_issues
        self._image_path: str | None = None
        self._thread: QThread | None = None
        self._worker: AgentWorker | None = None
        self._progress_items: dict[str, QListWidgetItem] = {}
        self._run_started_at: float | None = None
        self._pending_approval: ApprovalRequest | None = None
        self._last_result: Any = None
        self._last_resources: tuple[dict[str, Any], ...] = ()
        self._focus_mode = False
        self._active_query = ""
        self._last_query = ""
        self.conversation_store = conversation_store or ConversationStore()
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        sessions = self.conversation_store.list_sessions()
        self._session_id = (
            sessions[0].session_id
            if sessions
            else self.conversation_store.create_session().session_id
        )

        self.setWindowTitle("生理学智能答疑助手")
        self.setAcceptDrops(True)
        self.resize(1280, 840)
        self.setMinimumSize(900, 640)
        self.setStyleSheet(APP_QSS)
        self._build_ui()
        self._populate_modes()
        self._sync_research_controls()
        self._refresh_session_list()
        self._render_current_session()
        if not self.conversation_store.get_session(self._session_id).messages:
            self._show_startup_state()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer_layout = QHBoxLayout(root)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        outer_layout.setSpacing(12)

        sidebar = QFrame()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(238)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 16, 14, 14)
        sidebar_layout.setSpacing(5)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_mark = QLabel("医+")
        brand_mark.setObjectName("BrandMark")
        brand_mark.setFixedSize(36, 36)
        brand_row.addWidget(brand_mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_title = QLabel("MedScholar")
        brand_title.setObjectName("BrandTitle")
        brand_subtitle = QLabel("生理学智能学习台")
        brand_subtitle.setObjectName("BrandSubtitle")
        brand_text.addWidget(brand_title)
        brand_text.addWidget(brand_subtitle)
        brand_row.addLayout(brand_text, 1)
        sidebar_layout.addLayout(brand_row)

        conversation_label = QLabel("对话")
        conversation_label.setObjectName("SidebarSection")
        sidebar_layout.addWidget(conversation_label)
        self.new_chat_button = QPushButton("＋ 新建对话")
        self.new_chat_button.setObjectName("NewChat")
        self.new_chat_button.clicked.connect(self._new_conversation)
        sidebar_layout.addWidget(self.new_chat_button)
        self.session_list = QListWidget()
        self.session_list.setObjectName("SessionList")
        self.session_list.currentItemChanged.connect(self._session_changed)
        sidebar_layout.addWidget(self.session_list, 1)
        self.delete_chat_button = QPushButton("删除当前对话")
        self.delete_chat_button.setObjectName("DeleteChat")
        self.delete_chat_button.clicked.connect(self._delete_current_conversation)
        sidebar_layout.addWidget(self.delete_chat_button)

        workspace_label = QLabel("学习工作台")
        workspace_label.setObjectName("SidebarSection")
        sidebar_layout.addWidget(workspace_label)
        self.settings_button = QPushButton("设置与模型配置")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.knowledge_button = QPushButton("课程知识库")
        self.knowledge_button.clicked.connect(self.knowledge_requested.emit)
        self.skills_button = QPushButton("纠错反馈与 Skill")
        self.skills_button.clicked.connect(
            lambda: self.skills_requested.emit(self._last_result, self._last_query)
        )
        self.evaluation_button = QPushButton("题库评测")
        self.evaluation_button.clicked.connect(self.evaluation_requested.emit)
        self.official_sources_button = QPushButton("官方证据源")
        self.official_sources_button.clicked.connect(self._show_official_sources)
        for button in (
            self.knowledge_button,
            self.skills_button,
            self.evaluation_button,
            self.official_sources_button,
            self.settings_button,
        ):
            button.setObjectName("SideAction")
            sidebar_layout.addWidget(button)

        security_card = QFrame()
        security_card.setObjectName("SecurityCard")
        security_layout = QVBoxLayout(security_card)
        security_layout.setContentsMargins(10, 8, 10, 8)
        security_layout.setSpacing(2)
        security_title = QLabel("本地隐私保护")
        security_title.setStyleSheet("font-weight: 700; color: #DCECF0;")
        privacy = QLabel("仅保存文字问答，不保存密钥、\n图片或模型隐藏推理")
        privacy.setWordWrap(True)
        security_layout.addWidget(security_title)
        security_layout.addWidget(privacy)
        sidebar_layout.addWidget(security_card)
        outer_layout.addWidget(sidebar)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        outer_layout.addWidget(content, 1)

        top = QFrame()
        self.top_bar = top
        top.setObjectName("TopBar")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(18, 14, 18, 13)
        top_layout.setSpacing(10)
        heading_row = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("医学生学习伙伴")
        title.setObjectName("Title")
        subtitle = QLabel("陪你理解、复习与拓展；证据核验在后台安静守护")
        subtitle.setObjectName("Subtle")
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        heading_row.addLayout(title_column)
        heading_row.addStretch()
        evidence_chip = QLabel("回到课程 · 因材施教")
        evidence_chip.setObjectName("EvidenceChip")
        heading_row.addWidget(evidence_chip, 0, Qt.AlignTop)
        top_layout.addLayout(heading_row)

        control_row = QHBoxLayout()
        control_row.setSpacing(9)
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
        top_layout.addLayout(control_row)
        layout.addWidget(top)

        self.research_panel = QFrame()
        self.research_panel.setObjectName("ResearchPanel")
        research_layout = QGridLayout(self.research_panel)
        research_layout.setContentsMargins(14, 10, 14, 10)
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
        layout.addWidget(self.research_panel)

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
        self.reject_approval_button = QPushButton("拒绝")
        self.reject_approval_button.setObjectName("Reject")
        self.reject_approval_button.clicked.connect(
            lambda: self._resolve_approval(False)
        )
        self.approve_button = QPushButton("允许本次操作")
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

        self.reading_toolbar = QFrame()
        self.reading_toolbar.setObjectName("ReadingToolbar")
        reading_actions = QHBoxLayout(self.reading_toolbar)
        reading_actions.setContentsMargins(10, 5, 8, 5)
        reading_actions.setSpacing(4)
        answer_label = QLabel("回答")
        answer_label.setStyleSheet("font-weight:700;color:#294754;")
        reading_actions.addWidget(answer_label)
        reading_actions.addStretch()
        self.process_button = QPushButton("查看过程")
        self.process_button.setObjectName("ToolbarButton")
        self.process_button.clicked.connect(self._toggle_progress_panel)
        reading_actions.addWidget(self.process_button)
        self.sources_button = QPushButton("全部来源")
        self.sources_button.setObjectName("ToolbarButton")
        self.sources_button.setEnabled(False)
        self.sources_button.clicked.connect(self._show_all_resources)
        reading_actions.addWidget(self.sources_button)
        self.focus_button = QPushButton("专注阅读")
        self.focus_button.setObjectName("FocusButton")
        self.focus_button.clicked.connect(self._toggle_focus_mode)
        reading_actions.addWidget(self.focus_button)
        layout.addWidget(self.reading_toolbar)

        self.history = QTextBrowser()
        self.history.setObjectName("Conversation")
        self.history.setOpenExternalLinks(True)
        layout.addWidget(self.history, 1)

        composer = QFrame()
        self.composer = composer
        composer.setObjectName("Composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(14, 11, 14, 12)
        composer_layout.setSpacing(8)
        composer_heading = QHBoxLayout()
        composer_title = QLabel("向智能体提问")
        composer_title.setStyleSheet("font-weight: 700; color: #24404E;")
        composer_heading.addWidget(composer_title)
        composer_heading.addStretch()
        shortcut_chip = QLabel("Ctrl + Enter 发送")
        shortcut_chip.setObjectName("ShortcutChip")
        composer_heading.addWidget(shortcut_chip)
        composer_layout.addLayout(composer_heading)
        self.learning_actions_panel = QFrame()
        self.learning_actions_panel.setObjectName("LearningActions")
        self.learning_actions_layout = QHBoxLayout(self.learning_actions_panel)
        self.learning_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.learning_actions_layout.setSpacing(5)
        self.learning_actions_panel.hide()
        composer_layout.addWidget(self.learning_actions_panel)
        self.input_edit = QTextEdit()
        self.input_edit.setObjectName("QuestionInput")
        self.input_edit.setPlaceholderText(
            "输入题目、知识点或不确定的想法；也可以上传题目截图……"
        )
        self.input_edit.setFixedHeight(72)
        composer_layout.addWidget(self.input_edit)

        action_row = QHBoxLayout()
        self.attach_button = QPushButton("添加图片")
        self.attach_button.setObjectName("Attach")
        self.attach_button.clicked.connect(self._choose_image)
        action_row.addWidget(self.attach_button)
        self.attachment_label = QLabel("可拖入图片或 PDF")
        self.attachment_label.setObjectName("Subtle")
        action_row.addWidget(self.attachment_label)
        action_row.addStretch()
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("Send")
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

        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.send_shortcut.activated.connect(self._send)
        self.focus_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.focus_shortcut.activated.connect(lambda: self._set_focus_mode(False))

    def _refresh_session_list(self) -> None:
        self.session_list.blockSignals(True)
        self.session_list.clear()
        selected_row = 0
        for row, session in enumerate(self.conversation_store.list_sessions()):
            item = QListWidgetItem(session.title)
            item.setData(Qt.UserRole, session.session_id)
            self.session_list.addItem(item)
            if session.session_id == self._session_id:
                selected_row = row
        self.session_list.setCurrentRow(selected_row)
        self.session_list.blockSignals(False)

    def _render_current_session(self) -> None:
        self.history.clear()
        session = self.conversation_store.get_session(self._session_id)
        latest_resources: tuple[dict[str, Any], ...] = ()
        for message in session.messages:
            self._append_stored_message(message)
            if message.role == "assistant" and message.resources:
                latest_resources = tuple(message.resources)
        self._set_available_resources(latest_resources)

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
            f"确定删除“{session.title}”吗？\n删除后无法恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

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

    def _show_startup_state(self) -> None:
        if self.service is not None:
            self._append_system(
                "你可以输入生理学题目，或添加题目截图。系统会先检索课程材料，"
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
        self._sync_research_controls()

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

    def _sync_research_controls(self) -> None:
        mode_id = self.mode_combo.currentData()
        self.research_panel.setVisible(mode_id == "research")

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

    @Slot()
    def _send(self) -> None:
        if self.service is None or self._thread is not None:
            return
        query = self.input_edit.toPlainText().strip()
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
        image_media_type = (
            mimetypes.guess_type(image_path)[0] if image_path else None
        )
        summary = query or "[图片题目]"
        if image_path:
            summary += f"\n附件：{Path(image_path).name}"
        session = self.conversation_store.get_session(self._session_id)
        conversation_context = [
            {"role": message.role, "body": message.body}
            for message in session.messages[-6:]
            if message.role in {"user", "assistant"}
        ]
        self._append_user(summary)
        self.conversation_store.append_message(
            self._session_id,
            role="user",
            title="你",
            body=summary,
            tone="user",
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
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_result)
        worker.failed.connect(self._on_worker_error)
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
        self._last_query = self._active_query
        self._set_available_resources(tuple(result.external_resources or ()))
        presented = present_agent_result(result)
        self._set_learning_actions(presented.actions)
        self._append_presented(presented)
        self._save_presented(presented)
        self.status_label.setText(presented.status_text)

    @Slot(str)
    def _on_worker_error(self, message: str) -> None:
        presented = PresentedResult("运行失败", message, "error", "运行失败")
        self._append_presented(presented)
        self._save_presented(presented)
        self.status_label.setText("运行失败")

    @Slot(object)
    def _on_progress(self, update: Any) -> None:
        if not isinstance(update, ProgressUpdate):
            return
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
        self.status_label.setText(update.message)

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
        decision = "已允许，继续执行" if approved else "已拒绝，正在安全停止"
        self.status_label.setText(decision)

    @Slot()
    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_busy(False)
        self.progress_panel.hide()
        self.process_button.setText("查看过程")

    @Slot()
    def _toggle_progress_panel(self) -> None:
        if self._pending_approval is not None:
            self.progress_panel.show()
            self.process_button.setText("隐藏过程")
            self.status_label.setText("请先处理等待中的授权请求")
            return
        visible = not self.progress_panel.isHidden()
        self.progress_panel.setVisible(not visible)
        self.process_button.setText("查看过程" if visible else "隐藏过程")

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
            self._pending_approval = None
            self.approval_panel.hide()
            self.progress_panel.show()
            self.process_button.setText("隐藏过程")
            self.progress.setRange(0, 0)
            self.status_label.setText("正在工作……")
        else:
            self._elapsed_timer.stop()
            self._run_started_at = None
            self._pending_approval = None
            self.approval_panel.hide()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def _set_input_enabled(self, enabled: bool) -> None:
        self.input_edit.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.attach_button.setEnabled(enabled)
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
        self.focus_button.setEnabled(enabled)

    def _clear_attachment(self) -> None:
        self._image_path = None
        self.attachment_label.setText("可拖入图片或 PDF")
        self.attach_button.setText("添加图片")

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
            messages.append(f"已添加图片：{Path(images[0]).name}")
            if len(images) > 1:
                messages.append("当前每次提问只支持一张图片，其余图片未添加")
        if pdfs:
            self.knowledge_files_dropped.emit(pdfs)
            messages.append(f"已将 {len(pdfs)} 份 PDF 送往课程知识库")
        self.status_label.setText("；".join(messages) or "未发现支持的文件")

    def _append_user(self, text: str) -> None:
        self._append_card("你", text, "#DFF3EE", "#174C43", align="right")

    def _append_system(self, text: str) -> None:
        self._append_card("学习助手", text, "#F1F5F7", "#405662")

    def _append_presented(self, result: PresentedResult) -> None:
        palette = {
            "answer": ("#EAF7F3", "#125F51"),
            "clarification": ("#EAF3F8", "#245873"),
            "warning": ("#FFFBEB", "#92400E"),
            "error": ("#FEF2F2", "#991B1B"),
        }
        background, foreground = palette.get(
            result.tone, ("#F8FAFC", "#334155")
        )
        self._append_card(result.title, result.body, background, foreground)
        if result.research_body:
            research_palette = {
                "pass": ("#ECFDF5", "#065F46"),
                "review": ("#FFFBEB", "#92400E"),
                "insufficient": ("#FFFBEB", "#92400E"),
                "fail": ("#FEF2F2", "#991B1B"),
                "error": ("#FEF2F2", "#991B1B"),
            }
            research_background, research_foreground = research_palette.get(
                result.research_status, ("#F0FDFA", "#115E59")
            )
            self._append_card(
                "科研证据核验", result.research_body,
                research_background, research_foreground,
            )
        for resource in result.resources:
            self._append_research_resource(resource)

    def _set_learning_actions(
        self,
        actions: tuple[dict[str, str], ...] | list[dict[str, str]],
    ) -> None:
        while self.learning_actions_layout.count():
            item = self.learning_actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for action in list(actions)[:6]:
            prompt = str(action.get("prompt", "")).strip()
            label = str(action.get("label", "")).strip()
            if not prompt or not label:
                continue
            button = QPushButton(label)
            button.setObjectName("LearningAction")
            button.clicked.connect(
                lambda checked=False, value=prompt: self._trigger_learning_action(value)
            )
            self.learning_actions_layout.addWidget(button)
        self.learning_actions_layout.addStretch()
        self.learning_actions_panel.setVisible(
            self.learning_actions_layout.count() > 1
        )

    def _trigger_learning_action(self, prompt: str) -> None:
        if self._thread is not None:
            return
        self.input_edit.setPlainText(prompt)
        self._send()

    def _save_presented(self, result: PresentedResult) -> None:
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
        )
        self._refresh_session_list()

    def _append_stored_message(self, message: ConversationMessage) -> None:
        if message.role == "user":
            self._append_user(message.body)
            return
        palette = {
            "answer": ("#EAF7F3", "#125F51"),
            "clarification": ("#EAF3F8", "#245873"),
            "warning": ("#FFFBEB", "#92400E"),
            "error": ("#FEF2F2", "#991B1B"),
            "system": ("#F1F5F9", "#334155"),
        }
        background, foreground = palette.get(
            message.tone, ("#F8FAFC", "#334155")
        )
        self._append_card(message.title, message.body, background, foreground)

    def _append_card(
        self,
        title: str,
        body: str,
        background: str,
        foreground: str,
        *,
        align: str = "left",
    ) -> None:
        safe_title = html.escape(title)
        safe_body = html.escape(body).replace("\n", "<br>")
        empty_width = "18%" if align == "right" else "0%"
        trailing_width = "0%" if align == "right" else "12%"
        self.history.append(
            '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:8px;margin-bottom:8px;"><tr>'
            f'<td width="{empty_width}"></td>'
            f'<td style="background-color:{background};color:{foreground};'
            'padding:13px 15px;border-radius:10px;">'
            f'<span style="font-size:11px;font-weight:700;">{safe_title}</span>'
            f'<br><span style="line-height:1.55;">{safe_body}</span></td>'
            f'<td width="{trailing_width}"></td></tr></table>'
        )
        scrollbar = self.history.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_research_resource(self, resource: dict[str, Any]) -> None:
        original_title = str(resource.get("title") or "未命名论文")
        chinese_title = str(resource.get("title_zh") or "").strip()
        title = html.escape(chinese_title or original_title)
        raw_url = str(resource.get("url") or "")
        safe_url = html.escape(raw_url, quote=True)
        link = (
            f'<a href="{safe_url}" style="color:#0F766E;text-decoration:none;">{title}</a>'
            if raw_url.startswith(("https://", "http://"))
            else title
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
            f'<br><span style="font-size:10px;color:#65808A;">{html.escape(original_title)}</span>'
            if chinese_title and chinese_title != original_title
            else ""
        )
        self.history.append(
            '<table width="88%" cellspacing="0" cellpadding="0" style="margin:7px 0;">'
            '<tr><td style="background-color:#F1F7F6;color:#334155;padding:11px 13px;'
            'border-left:4px solid #19A58C;">'
            '<span style="font-size:10px;color:#65808A;">'
            + (
                "视频候选"
                if resource_kind == "video_candidate"
                else "平台搜索入口"
                if resource_kind == "platform_search"
                else "延伸阅读"
            )
            + "</span><br>"
            f'<b>{link}</b>{original_line}<br><span style="color:#0F766E;">{badge_text}</span>'
            f'<br><span><b>资料简介：</b>{intro}</span></td></tr></table>'
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self,
                "任务仍在运行",
                "请等待当前回答完成后再关闭窗口。",
            )
            event.ignore()
            return
        event.accept()


def _fallback_mode_options() -> tuple[dict[str, str], ...]:
    from interaction_modes import mode_options

    return mode_options()
