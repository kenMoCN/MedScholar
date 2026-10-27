"""Guard the WebEngine toolbar against focus-change compositor flicker."""

import unittest
from pathlib import Path


PREVIEW = Path(__file__).resolve().parents[1] / "ui" / "preview.html"


class TopToolbarStyleTests(unittest.TestCase):
    def test_top_bars_use_opaque_backgrounds_without_blur_or_entry_animation(self):
        html = PREVIEW.read_text(encoding="utf-8")
        toolbar = html.split("2. 顶部工具条", 1)[1].split("Expandable Parameters Drawer", 1)[0]
        subheader = html.split("Top Subheader", 1)[1].split("Message History Area", 1)[0]

        self.assertIn('h-[38px] bg-white border-b', toolbar)
        self.assertNotIn("backdrop-blur", toolbar)
        self.assertNotIn("hover:scale", toolbar)
        self.assertNotIn("active:scale", toolbar)
        self.assertNotIn("backdrop-blur", subheader)
        self.assertNotIn("animate-stagger", subheader)

    def test_subject_mode_and_horizon_controls_are_still_wired(self):
        html = PREVIEW.read_text(encoding="utf-8")
        for bridge_call in ("setSubject(sub.code)", "setMode(m.code)", "setHorizon(r.code)"):
            self.assertIn(bridge_call, html)

    def test_mode_help_covers_every_mode_and_keeps_dropdown_independent(self):
        html = PREVIEW.read_text(encoding="utf-8")
        shell = (PREVIEW.parent / "tutor_window.py").read_text(encoding="utf-8")
        for code in ("auto", "quick", "guided", "socratic", "research"):
            self.assertIn(f"{code}: {{", html)
        self.assertIn("const MODE_HELP_QUESTION = '动作电位为什么会复极化？'", html)
        self.assertIn('name="helpCircle"', html)
        self.assertIn("event.stopPropagation();", html)
        self.assertIn("setActiveDropdown(null);", html)
        self.assertIn("modeHelpOpen && ReactDOM.createPortal(", html)
        self.assertIn("style={{ zIndex: 2147483647 }}", html)
        mode_help_overlay = html.split("modeHelpOpen && ReactDOM.createPortal(", 1)[1].split(
            'ref={modeHelpDialogRef}', 1
        )[0]
        self.assertNotIn("backdrop-blur", mode_help_overlay)
        self.assertNotIn("mode-help-enter", mode_help_overlay)
        self.assertNotIn("mode-help-exit", mode_help_overlay)
        self.assertIn("overflow-y-auto px-7 py-5", html)
        self.assertIn("event.key === 'Escape'", html)
        self.assertIn("self.focus_shortcut.setContext(Qt.WidgetWithChildrenShortcut)", shell)

    def test_upload_approval_is_static_top_layer_without_blur_animation(self):
        html = PREVIEW.read_text(encoding="utf-8")
        approval = html.split("pendingApproval && ReactDOM.createPortal(", 1)[1].split(
            "modeHelpOpen && ReactDOM.createPortal(", 1
        )[0]
        self.assertIn("style={{ zIndex: 2147483647 }}", approval)
        self.assertIn('aria-modal="true"', approval)
        self.assertIn("handleApprovalDecision(false)", approval)
        self.assertIn("handleApprovalDecision(true)", approval)
        self.assertNotIn("backdrop-blur", approval)
        self.assertNotIn("animate-fade-in-up", approval)

    def test_desktop_loads_this_frontend_independently_of_working_directory(self):
        shell = (PREVIEW.parent / "tutor_window.py").read_text(encoding="utf-8")
        self.assertIn('Path(__file__).resolve().with_name("preview.html")', shell)

    def test_persistent_answer_area_has_no_opacity_or_transform_animation(self):
        html = PREVIEW.read_text(encoding="utf-8")
        workspace = html.split("Message History Area", 1)[1].split(
            "Attachment Badge", 1
        )[0]
        self.assertNotIn("animate-stagger", workspace)
        self.assertNotIn('className="flex justify-start animate-fade-in-up"', workspace)
        self.assertNotIn('className="flex justify-end animate-fade-in-up"', workspace)
        self.assertNotIn("bg-gradient-to-t", workspace)
        self.assertNotIn("focus-within:shadow-glow-blue", workspace)
        shell = (PREVIEW.parent / "tutor_window.py").read_text(encoding="utf-8")
        self.assertIn('self.web_view.page().setBackgroundColor(QColor("#111318" if theme == "dark" else "#FAFAFA"))', shell)
        self.assertIn('self.web_view.page().setBackgroundColor(QColor("#111318" if self._effective_theme == "dark" else "#FAFAFA"))', shell)

    def test_paper_actions_and_links_are_wired_in_web_ui(self):
        html = PREVIEW.read_text(encoding="utf-8")
        shell = (PREVIEW.parent / "tutor_window.py").read_text(encoding="utf-8")
        self.assertIn("act.topic || ''", html)
        self.assertIn("resource.resource_kind === 'research_paper'", html)
        self.assertIn("handleOpenResearchResource(resource.url)", html)
        self.assertIn("def openResearchResource(self, raw_url: str)", shell)

    def test_send_mode_selection_restores_composer_focus(self):
        html = PREVIEW.read_text(encoding="utf-8")
        selector = html.split("const handleSelectSendMode =", 1)[1].split(
            "const messagesEndRef", 1
        )[0]
        self.assertIn("requestAnimationFrame(() => inputRef.current?.focus())", selector)

    def test_ctrl_enter_is_web_owned_and_enter_is_the_new_default(self):
        html = PREVIEW.read_text(encoding="utf-8")
        shell = (PREVIEW.parent / "tutor_window.py").read_text(encoding="utf-8")
        self.assertIn("localStorage.getItem('medscholar_send_key_mode_v3') || 'Enter'", html)
        self.assertIn("localStorage.setItem('medscholar_send_key_mode_v3', modeKey)", html)
        self.assertIn('self.send_key_mode = "Enter"', shell)
        self.assertNotIn('QShortcut(QKeySequence("Ctrl+Return"), self)', shell)
        self.assertIn("(e.ctrlKey || e.metaKey) && !e.altKey", html)

    def test_modified_enter_is_not_discarded_as_ime_keycode(self):
        html = PREVIEW.read_text(encoding="utf-8")
        handler = html.split("const handleKeyDown =", 1)[1].split(
            "const handleNewConversation", 1
        )[0]
        self.assertIn("e.code === 'NumpadEnter'", handler)
        self.assertIn("e.keyCode === 229 && !e.ctrlKey && !e.metaKey && !e.shiftKey", handler)
        self.assertIn("(e.ctrlKey || e.metaKey) && !e.altKey", handler)
        self.assertIn("e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey", handler)

    def test_new_conversation_button_has_only_one_plus_icon(self):
        html = PREVIEW.read_text(encoding="utf-8")
        button = html.split("{/* + 新建对话 Button */}", 1)[1].split("</button>", 1)[0]
        self.assertIn('<Icon name="plus"', button)
        self.assertIn("<span>新建对话</span>", button)
        self.assertNotIn("<span>+ 新建对话</span>", button)

    def test_about_dialog_shows_purpose_and_two_author_portraits(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("aboutOpen && ReactDOM.createPortal(", html)
        about_overlay = html.split("aboutOpen && ReactDOM.createPortal(", 1)[1].split(
            '<div role="dialog"', 1
        )[0]
        self.assertNotIn("backdrop-blur", about_overlay)
        self.assertIn("软件作者", html)
        self.assertIn("我们想做的不是替你记住答案", html)
        self.assertNotIn("项目署名", html)
        for filename, name in (
            ("author_shanyiji.png", "山已几"),
            ("author_eternity_fox.png", "Eternity_fox"),
        ):
            self.assertIn(f'src="web/assets/{filename}"', html)
            self.assertIn(name, html)
            self.assertTrue((PREVIEW.parent / "web" / "assets" / filename).is_file())


if __name__ == "__main__":
    unittest.main()
