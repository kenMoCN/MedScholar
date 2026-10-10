import os
import shutil
import sys
import winreg
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QPushButton, QFrame, QLineEdit, QFileDialog, QMessageBox, 
                               QGraphicsOpacityEffect, QStackedWidget, QTextEdit)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, Signal, QPoint

from config_loader import read_env_file

# ==============================================================================
# QSS Stylesheets for Light & Dark Mode
# ==============================================================================
LIGHT_QSS = """
QWidget {
    background-color: #F8FAFC;
    color: #0F172A;
    font-family: "Microsoft YaHei UI", -apple-system, sans-serif;
}
QFrame#ProgressBarTrack { background-color: #E2E8F0; border-radius: 4px; }
QFrame#ProgressBarFill { background-color: #4F46E5; border-radius: 4px; }
QFrame#CardInner {
    background-color: #FFFFFF;
    border: 2px solid #E2E8F0;
    border-radius: 12px;
}
QFrame#CardInner[state="hover"] { border: 2px solid #818CF8; }
QFrame#CardInner[state="selected"] {
    background-color: #EEF2FF;
    border: 2px solid #10B981;
}
QLabel#CardTitle { font-size: 18px; font-weight: bold; color: #0F172A; background: transparent; }
QLabel#CardDesc { font-size: 14px; color: #475569; background: transparent; margin-top: 5px; }
QLabel#CheckMark { font-size: 22px; color: #10B981; font-weight: bold; background: transparent; }
QLabel#Badge {
    background-color: #E0E7FF;
    color: #3730A3;
    border-radius: 6px;
    font-size: 12px;
    font-weight: bold;
    padding: 4px 8px;
}
QPushButton#NavBtnPrimary {
    background-color: #4F46E5;
    color: #FFFFFF;
    border-radius: 8px;
    font-size: 15px;
    font-weight: bold;
    padding: 10px 24px;
}
QPushButton#NavBtnPrimary:hover { background-color: #6366F1; }
QPushButton#NavBtnSecondary {
    background-color: #E2E8F0;
    color: #0F172A;
    border-radius: 8px;
    font-size: 15px;
    font-weight: bold;
    padding: 10px 24px;
}
QPushButton#NavBtnSecondary:hover { background-color: #CBD5E1; }
QPushButton#NavBtnSecondary:disabled { background-color: #F1F5F9; color: #94A3B8; }
QLineEdit {
    background-color: #FFFFFF;
    border: 2px solid #CBD5E1;
    border-radius: 8px;
    padding: 10px;
    font-size: 15px;
    font-family: Consolas, monospace;
}
QLineEdit:focus { border: 2px solid #4F46E5; }
QPushButton#EyeBtn {
    background-color: transparent;
    border: none;
    font-size: 18px;
}
QTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    padding: 10px;
    color: #475569;
    font-size: 14px;
}
QFrame#HintFrame {
    background-color: #F1F5F9;
    border-radius: 8px;
}
QLabel#HintText { color: #64748B; font-size: 13px; background: transparent; }
QLabel#Brand { color: #818CF8; font-size: 14px; font-weight: bold; }
QLabel#StepInfo { color: #64748B; font-size: 14px; font-weight: bold; }
QLabel#MainTitle { font-size: 28px; font-weight: bold; }
QLabel#SubTitle { font-size: 15px; color: #475569; margin-top: 5px; }
QLabel#LabelBold { font-size: 15px; font-weight: bold; }
"""

DARK_QSS = LIGHT_QSS.replace("#F8FAFC", "#0B0F19") \
                    .replace("#FFFFFF", "#151D2A") \
                    .replace("#E2E8F0", "#1E293B") \
                    .replace("#0F172A", "#F8FAFC") \
                    .replace("#475569", "#94A3B8") \
                    .replace("#CBD5E1", "#334155") \
                    .replace("#EEF2FF", "#1E1C4E") \
                    .replace("#E0E7FF", "#312E81") \
                    .replace("#3730A3", "#C7D2FE") \
                    .replace("#F1F5F9", "#1E293B") \
                    .replace("#64748B", "#94A3B8")

def get_system_theme():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val == 1 else "dark"
    except Exception:
        return "light"


# ==============================================================================
# Animated Widgets (PySide6)
# ==============================================================================
class AnimatedProgressBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.setObjectName("ProgressBarTrack")
        
        self.fill = QFrame(self)
        self.fill.setObjectName("ProgressBarFill")
        self.fill.setFixedHeight(8)
        self.fill.setFixedWidth(0)
        
        self.anim = QPropertyAnimation(self.fill, b"geometry")
        self.anim.setDuration(500)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self._ratio = 0
        
    def set_progress(self, ratio):
        self._ratio = ratio
        target_width = int(self.width() * ratio)
        self.anim.setStartValue(self.fill.geometry())
        self.anim.setEndValue(QRect(0, 0, target_width, 8))
        self.anim.start()
        
    def resizeEvent(self, event):
        target_width = int(event.size().width() * self._ratio)
        self.fill.setFixedWidth(target_width)
        super().resizeEvent(event)


class AnimatedCard(QWidget):
    clicked = Signal(object)
    
    def __init__(self, title, description, value, icon, badge=""):
        super().__init__()
        self.value = value
        self.is_selected = False
        
        self.setFixedSize(410, 240)
        self.setCursor(Qt.PointingHandCursor)
        
        self.inner = QFrame(self)
        self.inner.setFixedSize(390, 220)
        self.inner.move(10, 15)
        self.inner.setObjectName("CardInner")
        self.inner.setProperty("state", "normal")
        
        # Smooth lift effect on hover & select (No elastic bounce)
        self.anim = QPropertyAnimation(self.inner, b"pos")
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

        layout = QVBoxLayout(self.inner)
        layout.setContentsMargins(22, 22, 22, 22)
        
        if badge:
            self.badge_lbl = QLabel(f" {badge} ")
            self.badge_lbl.setObjectName("Badge")
            layout.addWidget(self.badge_lbl, 0, Qt.AlignLeft)
        
        top_row = QHBoxLayout()
        self.title_lbl = QLabel(f"{icon} {title}")
        self.title_lbl.setObjectName("CardTitle")
        top_row.addWidget(self.title_lbl)
        
        top_row.addStretch()
        
        self.check_lbl = QLabel("")
        self.check_lbl.setObjectName("CheckMark")
        top_row.addWidget(self.check_lbl)
        
        layout.addLayout(top_row)
        
        self.desc_lbl = QLabel(description)
        self.desc_lbl.setObjectName("CardDesc")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.desc_lbl, 1)

    def set_selected(self, selected):
        self.is_selected = selected
        self.inner.setProperty("state", "selected" if selected else "normal")
        self.check_lbl.setText("✔" if selected else "")
        self.inner.style().polish(self.inner)
        
        self.anim.setEndValue(QPoint(10, 5) if selected else QPoint(10, 15))
        self.anim.start()
        
    def enterEvent(self, event):
        if not self.is_selected:
            self.inner.setProperty("state", "hover")
            self.inner.style().polish(self.inner)
            self.anim.setEndValue(QPoint(10, 5))
            self.anim.start()
            
    def leaveEvent(self, event):
        if not self.is_selected:
            self.inner.setProperty("state", "normal")
            self.inner.style().polish(self.inner)
            self.anim.setEndValue(QPoint(10, 15))
            self.anim.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.value)


class FadingStackedWidget(QStackedWidget):
    """QStackedWidget with smooth opacity crossfade animation between pages."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fade_anim = QPropertyAnimation()
        self.fade_anim.setDuration(350)
        self.fade_anim.setEasingCurve(QEasingCurve.InOutQuad)
        
    def fade_to_index(self, index):
        if index == self.currentIndex():
            return
            
        self.setCurrentIndex(index)
        new_widget = self.currentWidget()
        
        self.effect = QGraphicsOpacityEffect(new_widget)
        new_widget.setGraphicsEffect(self.effect)
        
        self.fade_anim.setTargetObject(self.effect)
        self.fade_anim.setPropertyName(b"opacity")
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        
        try:
            self.fade_anim.finished.disconnect()
        except Exception:
            pass
            
        self.fade_anim.finished.connect(lambda: new_widget.setGraphicsEffect(None))
        self.fade_anim.start()


# ==============================================================================
# Main Setup Wizard (PySide6)
# ==============================================================================
class AgentSetupWizard(QMainWindow):
    configuration_completed = Signal(str)
    wizard_closed = Signal()

    def __init__(self, *, auto_close_on_finish=True):
        super().__init__()
        self.auto_close_on_finish = auto_close_on_finish
        self.setWindowTitle("AI Agent 高度客制化配置向导 (PySide6 动画版)")
        self.resize(1000, 750)
        self.setMinimumSize(950, 680)
        
        # Config Data
        self.config_data = {
            "need_image_recognition": None,
            "deepseek_model_name": "",
            "deepseek_api_key": "",
            "doubao_vision_model_name": "",
            "doubao_api_key": "",
            "proficiency": None,
            "need_textbook": None,
            "selected_files": [],
            "cite_location": None,
            "require_file_approval": None,
            "theme_mode": "system"
        }
        
        self.current_step = 1
        self.total_steps = 7
        
        self.setup_ui()
        self.load_existing_configuration()
        self.apply_theme(self.config_data["theme_mode"])
        self.show_step(1)

    def load_existing_configuration(self):
        """Prefill an existing local config without logging or exposing keys."""

        env_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "config.env")
        if not os.path.isfile(env_path):
            return
        try:
            values = read_env_file(env_path)
        except ValueError:
            return

        def as_bool(name, default=False):
            return values.get(name, str(default)).strip().lower() in {
                "1", "true", "yes", "on"
            }

        vision = as_bool("ENABLE_IMAGE_RECOGNITION")
        self.on_step1_select(vision)
        self.entry_ds_model.setText(values.get("DEEPSEEK_MODEL_NAME", ""))
        self.entry_ds.setText(values.get("DEEPSEEK_API_KEY", ""))
        self.entry_db_model.setText(values.get("DOUBAO_VISION_MODEL_NAME", ""))
        self.entry_db.setText(values.get("DOUBAO_API_KEY", ""))
        self.save_step_2()

        proficiency = values.get("USER_PROFICIENCY", "beginner")
        self.on_step3_select(
            proficiency if proficiency in {"beginner", "proficient"} else "beginner"
        )

        textbook_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "textbook")
        existing_files = []
        if os.path.isdir(textbook_dir):
            existing_files = [
                os.path.join(textbook_dir, name)
                for name in os.listdir(textbook_dir)
                if os.path.isfile(os.path.join(textbook_dir, name))
            ]
        has_textbooks = as_bool("HAS_TEXTBOOKS")
        self.config_data["selected_files"] = existing_files if has_textbooks else []
        self.on_step4_select(has_textbooks)

        cite_location = as_bool("CITE_TEXTBOOK_LOCATION", True)
        self.on_step5_select(cite_location)
        self.on_step6_select(as_bool("REQUIRE_FILE_OPERATION_APPROVAL", True))

        theme = values.get("THEME_MODE", "system")
        if theme not in {"light", "dark", "system"}:
            theme = "system"
        self.on_step7_select(theme)
        
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(45, 30, 45, 25)
        main_layout.setSpacing(15)
        
        # --- Header ---
        header_layout = QVBoxLayout()
        header_layout.setSpacing(12)
        
        top_row = QHBoxLayout()
        self.lbl_brand = QLabel("🤖 AI AGENT BUILDER")
        self.lbl_brand.setObjectName("Brand")
        top_row.addWidget(self.lbl_brand)
        top_row.addStretch()
        
        self.lbl_step_info = QLabel("步骤 1 / 7 (14%)")
        self.lbl_step_info.setObjectName("StepInfo")
        top_row.addWidget(self.lbl_step_info)
        header_layout.addLayout(top_row)
        
        self.progress = AnimatedProgressBar()
        header_layout.addWidget(self.progress)
        
        self.lbl_main_title = QLabel("")
        self.lbl_main_title.setObjectName("MainTitle")
        header_layout.addWidget(self.lbl_main_title)
        
        self.lbl_subtitle = QLabel("")
        self.lbl_subtitle.setObjectName("SubTitle")
        self.lbl_subtitle.setWordWrap(True)
        header_layout.addWidget(self.lbl_subtitle)
        
        main_layout.addLayout(header_layout)
        
        # --- Stacked Pages ---
        self.stack = FadingStackedWidget()
        main_layout.addWidget(self.stack, 1)
        
        # --- Footer ---
        footer_layout = QHBoxLayout()
        self.btn_prev = QPushButton("← 上一页")
        self.btn_prev.setObjectName("NavBtnSecondary")
        self.btn_prev.setCursor(Qt.PointingHandCursor)
        self.btn_prev.clicked.connect(self.prev_step)
        footer_layout.addWidget(self.btn_prev)
        
        footer_layout.addStretch()
        
        self.btn_next = QPushButton("下一页 →")
        self.btn_next.setObjectName("NavBtnPrimary")
        self.btn_next.setCursor(Qt.PointingHandCursor)
        self.btn_next.clicked.connect(self.next_step)
        footer_layout.addWidget(self.btn_next)
        
        main_layout.addLayout(footer_layout)
        
        # Create all steps
        self.step_widgets = []
        for i in range(7):
            w = QWidget()
            w_layout = QVBoxLayout(w)
            w_layout.setContentsMargins(0, 10, 0, 10)
            self.step_widgets.append((w, w_layout))
            self.stack.addWidget(w)
            
        self.build_step_1()
        self.build_step_2()
        self.build_step_3()
        self.build_step_4()
        self.build_step_5()
        self.build_step_6()
        self.build_step_7()

    # --------------------------------------------------------------------------
    # Step View Routing
    # --------------------------------------------------------------------------
    def show_step(self, step):
        self.current_step = step
        
        titles = [
            ("图片识别与多模态设置", "请选择 AI Agent 是否需要具备图像理解能力。开启后可以分析图表、照片与文档扫描件："),
            ("配置模型 API Key", "请输入您的模型 API Key（安全承诺：仅明文保存在本地 config.env，绝不会上传至第三方）："),
            ("选择 AI 工具熟练程度", "请选择您使用 AI 的习惯，我们将调整 Agent 的回答策略（直接高效输出 vs 主动引导提问）："),
            ("教材及专业资料库", "是否需要为 AI Agent 注入专属教材或本地知识库（自动复制至 ./textbook 目录）："),
            ("标注教材引用位置", "在解答问题时，是否要求 Agent 在回答末尾附带具体的教材来源及页码段落？"),
            ("文件操作权限与安全", "当 AI Agent 需要对本地目录文件执行创建、修改或删除等高风险操作时："),
            ("外观与界面主题设置", "请选择应用主题外观（点击选项可立即实时预览整体模式变化）：")
        ]
        
        self.lbl_main_title.setText(titles[step-1][0])
        self.lbl_subtitle.setText(titles[step-1][1])
        
        ratio = step / self.total_steps
        self.lbl_step_info.setText(f"步骤 {step} / {self.total_steps} ({int(ratio*100)}%)")
        self.progress.set_progress(ratio)
        
        self.btn_prev.setDisabled(step == 1)
        self.btn_next.setText("🚀 完成配置并生成" if step == self.total_steps else "下一页 →")
        
        self.stack.fade_to_index(step - 1)

    # --------------------------------------------------------------------------
    # Step 1: Image Recognition
    # --------------------------------------------------------------------------
    def build_step_1(self):
        layout = self.step_widgets[0][1]
        card_row = QHBoxLayout()
        
        self.card_img_yes = AnimatedCard("需要图片识别", "开启多模态能力。支持上传解析表格图表、截图及视觉资料（需配置豆包 API Key）。", True, "🖼️", "VISION MULTIMODAL")
        self.card_img_no = AnimatedCard("不需要图片识别", "仅保留纯文本对话能力。架构更加简洁清爽，仅需提供 DeepSeek API Key 即可快速使用。", False, "💬", "TEXT ONLY")
        
        self.card_img_yes.clicked.connect(self.on_step1_select)
        self.card_img_no.clicked.connect(self.on_step1_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_img_yes)
        card_row.addSpacing(25)
        card_row.addWidget(self.card_img_no)
        card_row.addStretch()
        
        layout.addLayout(card_row)
        layout.addStretch()
        
    def on_step1_select(self, val):
        self.config_data["need_image_recognition"] = val
        self.card_img_yes.set_selected(val is True)
        self.card_img_no.set_selected(val is False)
        
        # Dynamically toggle Doubao visibility for Step 2
        self.db_group.setVisible(val is True)

    # --------------------------------------------------------------------------
    # Step 2: API Keys Input
    # --------------------------------------------------------------------------
    def build_step_2(self):
        layout = self.step_widgets[1][1]
        
        # DeepSeek
        lbl_ds_model = QLabel("DeepSeek 模型名称:")
        lbl_ds_model.setObjectName("LabelBold")
        layout.addWidget(lbl_ds_model)

        self.entry_ds_model = QLineEdit()
        self.entry_ds_model.setPlaceholderText("请输入 API 实际调用使用的模型 ID")
        layout.addWidget(self.entry_ds_model)
        layout.addSpacing(10)

        lbl_ds = QLabel("DeepSeek API Key:")
        lbl_ds.setObjectName("LabelBold")
        layout.addWidget(lbl_ds)
        
        ds_row = QHBoxLayout()
        self.entry_ds = QLineEdit()
        self.entry_ds.setEchoMode(QLineEdit.Password)
        ds_row.addWidget(self.entry_ds)
        
        self.btn_eye_ds = QPushButton("👁️")
        self.btn_eye_ds.setObjectName("EyeBtn")
        self.btn_eye_ds.setCursor(Qt.PointingHandCursor)
        self.btn_eye_ds.clicked.connect(lambda: self.toggle_key('ds'))
        ds_row.addWidget(self.btn_eye_ds)
        
        layout.addLayout(ds_row)
        layout.addSpacing(15)
        
        # Doubao (Wrapped in QWidget for easy hide/show)
        self.db_group = QWidget()
        db_layout = QVBoxLayout(self.db_group)
        db_layout.setContentsMargins(0,0,0,0)

        lbl_db_model = QLabel("豆包视觉模型名称:")
        lbl_db_model.setObjectName("LabelBold")
        db_layout.addWidget(lbl_db_model)

        self.entry_db_model = QLineEdit()
        self.entry_db_model.setPlaceholderText("请输入具备图片识别能力的模型 ID")
        db_layout.addWidget(self.entry_db_model)
        db_layout.addSpacing(10)
        
        lbl_db = QLabel("豆包 API Key (图片识别服务):")
        lbl_db.setObjectName("LabelBold")
        db_layout.addWidget(lbl_db)
        
        db_row = QHBoxLayout()
        self.entry_db = QLineEdit()
        self.entry_db.setEchoMode(QLineEdit.Password)
        db_row.addWidget(self.entry_db)
        
        self.btn_eye_db = QPushButton("👁️")
        self.btn_eye_db.setObjectName("EyeBtn")
        self.btn_eye_db.setCursor(Qt.PointingHandCursor)
        self.btn_eye_db.clicked.connect(lambda: self.toggle_key('db'))
        db_row.addWidget(self.btn_eye_db)
        
        db_layout.addLayout(db_row)
        layout.addWidget(self.db_group)
        
        layout.addSpacing(25)
        
        # Security Hint Frame
        hint_frame = QFrame()
        hint_frame.setObjectName("HintFrame")
        hint_layout = QVBoxLayout(hint_frame)
        hint_layout.setContentsMargins(15, 15, 15, 15)
        hint_lbl = QLabel("🔒 安全承诺：您的密钥仅明文保存在本地 config.env 文件中，绝不会向任何第三方或云端上传。")
        hint_lbl.setObjectName("HintText")
        hint_layout.addWidget(hint_lbl)
        
        layout.addWidget(hint_frame)
        self.btn_advanced_models = QPushButton("⚙ 使用任意模型 / 高级模型注册")
        self.btn_advanced_models.setObjectName("NavBtnSecondary")
        self.btn_advanced_models.clicked.connect(self.open_advanced_model_settings)
        layout.addWidget(self.btn_advanced_models)
        layout.addStretch()

    def open_advanced_model_settings(self):
        from model_settings_dialog import ModelSettingsDialog

        dialog = ModelSettingsDialog(os.path.abspath(os.path.dirname(__file__)), self)
        dialog.exec()
        
    def toggle_key(self, target):
        if target == 'ds':
            mode = QLineEdit.Normal if self.entry_ds.echoMode() == QLineEdit.Password else QLineEdit.Password
            self.entry_ds.setEchoMode(mode)
            self.btn_eye_ds.setText("🙈" if mode == QLineEdit.Normal else "👁️")
        else:
            mode = QLineEdit.Normal if self.entry_db.echoMode() == QLineEdit.Password else QLineEdit.Password
            self.entry_db.setEchoMode(mode)
            self.btn_eye_db.setText("🙈" if mode == QLineEdit.Normal else "👁️")
            
    def save_step_2(self):
        self.config_data["deepseek_model_name"] = self.entry_ds_model.text().strip()
        self.config_data["deepseek_api_key"] = self.entry_ds.text().strip()
        self.config_data["doubao_vision_model_name"] = self.entry_db_model.text().strip()
        self.config_data["doubao_api_key"] = self.entry_db.text().strip()

    # --------------------------------------------------------------------------
    # Step 3: Proficiency
    # --------------------------------------------------------------------------
    def build_step_3(self):
        layout = self.step_widgets[2][1]
        card_row = QHBoxLayout()
        
        self.card_prof = AnimatedCard("我能熟练使用", "能够正确且详尽地描述问题，目的明确。\n\n[策略]：直接输出精准解答，减少确认提示。", "proficient", "⚡", "EXPERT DIRECT")
        self.card_beg = AnimatedCard("我还不太熟练", "暂时无法全面表达需求，需要 AI 引导。\n\n[策略]：启用双向追问模式，帮助梳理补全需求。", "beginner", "🌱", "GUIDED INTERACTIVE")
        
        self.card_prof.clicked.connect(self.on_step3_select)
        self.card_beg.clicked.connect(self.on_step3_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_prof)
        card_row.addSpacing(25)
        card_row.addWidget(self.card_beg)
        card_row.addStretch()
        
        layout.addLayout(card_row)
        layout.addStretch()
        
    def on_step3_select(self, val):
        self.config_data["proficiency"] = val
        self.card_prof.set_selected(val == "proficient")
        self.card_beg.set_selected(val == "beginner")

    # --------------------------------------------------------------------------
    # Step 4: Textbook Upload
    # --------------------------------------------------------------------------
    def build_step_4(self):
        layout = self.step_widgets[3][1]
        
        card_row = QHBoxLayout()
        self.card_tb_yes = AnimatedCard("上传专属教材资料", "支持 PDF、Word 等格式，开启本地检索。", True, "📚", "RAG KNOWLEDGE")
        self.card_tb_yes.setFixedSize(410, 180)
        self.card_tb_yes.inner.setFixedSize(390, 160)
        
        self.card_tb_no = AnimatedCard("无需通用教材", "不依赖外部参考资料，使用通用模型知识库。", False, "🚀", "GENERAL MODEL")
        self.card_tb_no.setFixedSize(410, 180)
        self.card_tb_no.inner.setFixedSize(390, 160)
        
        self.card_tb_yes.clicked.connect(self.on_step4_select)
        self.card_tb_no.clicked.connect(self.on_step4_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_tb_yes)
        card_row.addSpacing(25)
        card_row.addWidget(self.card_tb_no)
        card_row.addStretch()
        layout.addLayout(card_row)
        
        # File selector frame
        self.file_group = QWidget()
        fg_layout = QVBoxLayout(self.file_group)
        fg_layout.setContentsMargins(0, 15, 0, 0)
        
        top_row = QHBoxLayout()
        self.lbl_file_count = QLabel("尚未选择文件：")
        self.lbl_file_count.setObjectName("LabelBold")
        top_row.addWidget(self.lbl_file_count)
        top_row.addStretch()
        
        self.btn_select_files = QPushButton("📁 选择教材文件...")
        self.btn_select_files.setObjectName("NavBtnSecondary")
        self.btn_select_files.setCursor(Qt.PointingHandCursor)
        self.btn_select_files.clicked.connect(self.browse_files)
        top_row.addWidget(self.btn_select_files)
        fg_layout.addLayout(top_row)
        
        self.file_list = QTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setText("（暂未添加文件）")
        fg_layout.addWidget(self.file_list)
        
        layout.addWidget(self.file_group, 1) # Give remaining space to list
        
    def on_step4_select(self, val):
        self.config_data["need_textbook"] = val
        self.card_tb_yes.set_selected(val is True)
        self.card_tb_no.set_selected(val is False)
        
        if val is True:
            if not self.config_data["selected_files"]:
                self.browse_files()
            self.btn_select_files.show()
            self.update_file_list_display()
        else:
            self.config_data["selected_files"] = []
            self.btn_select_files.hide()
            self.lbl_file_count.setText("当前选择不使用专属教材资料。")
            self.file_list.setText("AI Agent 将仅依赖语言模型本身具备的全量知识进行对话答疑。\n(选择不需要教材时，会自动跳过第 5 步「引文位置标注」设置。)")
            
    def browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择教材及相关资料文件", "", "支持文件 (*.pdf *.txt *.docx *.doc *.md *.epub *.pptx);;所有文件 (*.*)"
        )
        if files:
            self.config_data["selected_files"] = list(files)
            self.config_data["need_textbook"] = True
            self.card_tb_yes.set_selected(True)
            self.card_tb_no.set_selected(False)
            self.btn_select_files.show()
            self.update_file_list_display()
            
    def update_file_list_display(self):
        files = self.config_data["selected_files"]
        if files:
            self.lbl_file_count.setText(f"已选择 {len(files)} 个文件：")
            text = ""
            for f in files:
                text += f"📄 {os.path.basename(f)}\n"
            self.file_list.setText(text)
        else:
            self.lbl_file_count.setText("尚未选择文件：")
            self.file_list.setText("（暂未添加文件）")

    # --------------------------------------------------------------------------
    # Step 5: Citation
    # --------------------------------------------------------------------------
    def build_step_5(self):
        layout = self.step_widgets[4][1]
        card_row = QHBoxLayout()
        
        self.card_cite_yes = AnimatedCard("需要标注位置", "严谨模式。明确标明参考文件名、页码，方便快速查阅原始出处。", True, "📌", "STRICT CITATION")
        self.card_cite_no = AnimatedCard("不需要标注位置", "流畅模式。Agent 融合教材知识直接解答，文本更加简洁干净。", False, "📝", "CLEAN TEXT")
        
        self.card_cite_yes.clicked.connect(self.on_step5_select)
        self.card_cite_no.clicked.connect(self.on_step5_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_cite_yes)
        card_row.addSpacing(25)
        card_row.addWidget(self.card_cite_no)
        card_row.addStretch()
        
        layout.addLayout(card_row)
        layout.addStretch()

    def on_step5_select(self, val):
        self.config_data["cite_location"] = val
        self.card_cite_yes.set_selected(val is True)
        self.card_cite_no.set_selected(val is False)

    # --------------------------------------------------------------------------
    # Step 6: Approval
    # --------------------------------------------------------------------------
    def build_step_6(self):
        layout = self.step_widgets[5][1]
        card_row = QHBoxLayout()
        
        self.card_appr_yes = AnimatedCard("每个步骤都需要批准", "高安全性模式。每次修改本地磁盘前均会弹出确认框，经人工同意后才执行。", True, "🛡️", "HIGH SECURITY")
        self.card_appr_no = AnimatedCard("不需要批准 (全自动)", "高效率模式。Agent 自动完成文件的读写编辑与清理工作，无需频繁人工打扰。", False, "⚡", "AUTONOMOUS EXEC")
        
        self.card_appr_yes.clicked.connect(self.on_step6_select)
        self.card_appr_no.clicked.connect(self.on_step6_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_appr_yes)
        card_row.addSpacing(25)
        card_row.addWidget(self.card_appr_no)
        card_row.addStretch()
        
        layout.addLayout(card_row)
        layout.addStretch()

    def on_step6_select(self, val):
        self.config_data["require_file_approval"] = val
        self.card_appr_yes.set_selected(val is True)
        self.card_appr_no.set_selected(val is False)

    # --------------------------------------------------------------------------
    # Step 7: Theme
    # --------------------------------------------------------------------------
    def build_step_7(self):
        layout = self.step_widgets[6][1]
        card_row = QHBoxLayout()
        
        self.card_th_light = AnimatedCard("白天模式", "明亮清爽界面，适合高亮环境。", "light", "☀️", "LIGHT")
        self.card_th_dark = AnimatedCard("黑夜模式", "沉浸护眼深色，夜晚使用更佳。", "dark", "🌙", "DARK")
        self.card_th_sys = AnimatedCard("跟随系统", "自动同步系统当前外观配置。", "system", "💻", "AUTO")
        
        self.card_th_light.setFixedSize(290, 240)
        self.card_th_light.inner.setFixedSize(270, 220)
        
        self.card_th_dark.setFixedSize(290, 240)
        self.card_th_dark.inner.setFixedSize(270, 220)
        
        self.card_th_sys.setFixedSize(290, 240)
        self.card_th_sys.inner.setFixedSize(270, 220)
        
        self.card_th_light.clicked.connect(self.on_step7_select)
        self.card_th_dark.clicked.connect(self.on_step7_select)
        self.card_th_sys.clicked.connect(self.on_step7_select)
        
        card_row.addStretch()
        card_row.addWidget(self.card_th_light)
        card_row.addWidget(self.card_th_dark)
        card_row.addWidget(self.card_th_sys)
        card_row.addStretch()
        
        layout.addLayout(card_row)
        layout.addStretch()
        
    def on_step7_select(self, val):
        self.config_data["theme_mode"] = val
        self.card_th_light.set_selected(val == "light")
        self.card_th_dark.set_selected(val == "dark")
        self.card_th_sys.set_selected(val == "system")
        self.apply_theme(val)
        
    def apply_theme(self, mode):
        actual_mode = mode
        if mode == "system":
            actual_mode = get_system_theme()
            
        app = QApplication.instance()
        new_qss = DARK_QSS if actual_mode == "dark" else LIGHT_QSS
        
        if app.styleSheet() == new_qss:
            return
            
        # If the window is already shown, do a crossfade animation
        if self.isVisible():
            pixmap = self.grab()
            self.overlay = QLabel(self)
            self.overlay.setPixmap(pixmap)
            self.overlay.setGeometry(self.rect())
            self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.overlay.show()
            
            app.setStyleSheet(new_qss)
            
            self.theme_effect = QGraphicsOpacityEffect(self.overlay)
            self.overlay.setGraphicsEffect(self.theme_effect)
            
            self.theme_anim = QPropertyAnimation(self.theme_effect, b"opacity")
            self.theme_anim.setDuration(350)
            self.theme_anim.setStartValue(1.0)
            self.theme_anim.setEndValue(0.0)
            self.theme_anim.setEasingCurve(QEasingCurve.InOutQuad)
            
            self.theme_anim.finished.connect(self.overlay.deleteLater)
            self.theme_anim.start()
        else:
            app.setStyleSheet(new_qss)

    # --------------------------------------------------------------------------
    # Navigation Logic
    # --------------------------------------------------------------------------
    def prev_step(self):
        if self.current_step == 2:
            self.save_step_2()

        target = self.current_step - 1
        if target == 5 and self.config_data["need_textbook"] is False:
            target = 4

        if target >= 1:
            self.show_step(target)

    def next_step(self):
        # Validation checks
        if self.current_step == 1:
            if self.config_data["need_image_recognition"] is None:
                QMessageBox.warning(self, "提示", "请选择是否需要图片识别功能。")
                return
        elif self.current_step == 2:
            self.save_step_2()
            from dynamic_config import dynamic_config_exists

            has_dynamic_models = dynamic_config_exists(
                os.path.abspath(os.path.dirname(__file__))
            )
            if not has_dynamic_models and not self.config_data["deepseek_model_name"]:
                QMessageBox.warning(self, "提示", "请填写 DeepSeek 模型名称。")
                return
            if not has_dynamic_models and not self.config_data["deepseek_api_key"]:
                QMessageBox.warning(self, "提示", "请填写 DeepSeek API Key。")
                return
            if not has_dynamic_models and self.config_data["need_image_recognition"] is True and not self.config_data["doubao_vision_model_name"]:
                QMessageBox.warning(self, "提示", "您开启了图片识别功能，请填写豆包视觉模型名称。")
                return
            if not has_dynamic_models and self.config_data["need_image_recognition"] is True and not self.config_data["doubao_api_key"]:
                QMessageBox.warning(self, "提示", "您开启了图片识别功能，请填写豆包 API Key。")
                return
        elif self.current_step == 3:
            if self.config_data["proficiency"] is None:
                QMessageBox.warning(self, "提示", "请选择您的 AI 工具熟练程度。")
                return
        elif self.current_step == 4:
            if self.config_data["need_textbook"] is None:
                QMessageBox.warning(self, "提示", "请选择是否需要上传教材及相关资料。")
                return
            if self.config_data["need_textbook"] is True and not self.config_data["selected_files"]:
                QMessageBox.warning(self, "提示", "您选择了需要上传资料，请至少选择一个文件。")
                return
        elif self.current_step == 5:
            if self.config_data["cite_location"] is None:
                QMessageBox.warning(self, "提示", "请选择是否需要在回答时标注教材位置。")
                return
        elif self.current_step == 6:
            if self.config_data["require_file_approval"] is None:
                QMessageBox.warning(self, "提示", "请选择对文件操作时是否需要批准。")
                return

        if self.current_step == 4 and self.config_data["need_textbook"] is False:
            self.config_data["cite_location"] = False
            target = 6
        else:
            target = self.current_step + 1

        if target > self.total_steps:
            self.finish_wizard()
        else:
            self.show_step(target)

    # --------------------------------------------------------------------------
    # Finalization
    # --------------------------------------------------------------------------
    def finish_wizard(self):
        root_dir = os.path.abspath(os.path.dirname(__file__))
        textbook_dir = os.path.join(root_dir, "textbook")

        copied_files = 0
        if self.config_data["need_textbook"] is True and self.config_data["selected_files"]:
            os.makedirs(textbook_dir, exist_ok=True)
            for f in self.config_data["selected_files"]:
                if os.path.isfile(f):
                    try:
                        destination = os.path.join(textbook_dir, os.path.basename(f))
                        if os.path.abspath(f) != os.path.abspath(destination):
                            shutil.copy2(f, destination)
                        copied_files += 1
                    except Exception as e:
                        print(f"Error copying {f}: {e}")

        env_content = f"""# ==============================================================================
# AI Agent 客制化配置文件 (config.env)
# ==============================================================================

# 1. 图片识别与多模态支持
# Options: true | false
ENABLE_IMAGE_RECOGNITION={'true' if self.config_data['need_image_recognition'] else 'false'}

# 2. 核心大模型 API Keys
DEEPSEEK_MODEL_NAME={self.config_data['deepseek_model_name']}
DEEPSEEK_API_KEY={self.config_data['deepseek_api_key']}
DOUBAO_VISION_MODEL_NAME={self.config_data['doubao_vision_model_name']}
DOUBAO_API_KEY={self.config_data['doubao_api_key']}

# 3. 用户 AI 熟练度及交互模式
# Options: proficient | beginner
USER_PROFICIENCY={self.config_data['proficiency']}

# 4. 专属教材与本地知识库
# HAS_TEXTBOOKS Options: true | false
HAS_TEXTBOOKS={'true' if self.config_data['need_textbook'] else 'false'}
TEXTBOOK_DIR=./textbook
COPIED_TEXTBOOK_COUNT={copied_files}

# 5. 引文位置溯源及标注
# Options: true | false
CITE_TEXTBOOK_LOCATION={'true' if self.config_data['cite_location'] else 'false'}

# 6. 本地系统操作与文件权限
# Options: true | false
REQUIRE_FILE_OPERATION_APPROVAL={'true' if self.config_data['require_file_approval'] else 'false'}

# 7. UI 外观与界面主题
# Options: light | dark | system
THEME_MODE={self.config_data['theme_mode']}
"""

        env_path = os.path.join(root_dir, "config.env")
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_content)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"写入 config.env 时发生错误：{e}")
            return

        msg = f"🎉 AI Agent 配置已成功完成！\n\n📄 配置文件: config.env\n📁 路径: {env_path}\n"
        if copied_files > 0:
            msg += f"📚 已成功将 {copied_files} 个文件复制至 ./textbook\n"

        QMessageBox.information(self, "配置完成", msg)
        self.configuration_completed.emit(env_path)
        if self.auto_close_on_finish:
            self.close()

    def closeEvent(self, event):
        self.wizard_closed.emit()
        super().closeEvent(event)


# ==============================================================================
# Entry Point
# ==============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Enable High DPI scaling
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        
    wizard = AgentSetupWizard()
    wizard.show()
    sys.exit(app.exec())
