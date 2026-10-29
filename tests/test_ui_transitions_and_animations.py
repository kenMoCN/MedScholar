"""Tests for global transitions, smooth inertial scrolling, and Python window animations."""

import unittest
from pathlib import Path
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QApplication

PREVIEW = Path(__file__).resolve().parents[1] / "ui" / "preview.html"
TUTOR_WINDOW = Path(__file__).resolve().parents[1] / "ui" / "tutor_window.py"


class UiTransitionsAndAnimationsTests(unittest.TestCase):
    def test_css_smooth_scroll_and_button_transitions(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("scroll-behavior: smooth;", html)
        self.assertIn("transition: background-color 0.2s ease, opacity 0.2s ease, color 0.2s ease, border-color 0.2s ease;", html)

    def test_window_control_buttons_have_hover_transitions(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn(".window-control-btn {", html)
        self.assertIn("transition: all 0.2s ease-in-out !important;", html)
        self.assertIn(".window-close-btn {", html)
        self.assertIn("background-color: #E81123 !important;", html)
        self.assertIn("color: #FFFFFF !important;", html)
        self.assertIn('className="w-[46px] h-full flex items-center justify-center text-[#3F3F46] window-control-btn cursor-default"', html)
        self.assertIn('className="w-[46px] h-full flex items-center justify-center text-[#3F3F46] window-close-btn cursor-default"', html)

    def test_dropdown_panels_have_pop_in_and_exit_transitions(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn(".dropdown-panel {", html)
        self.assertIn("opacity: 0;", html)
        self.assertIn("transform: translateY(-10px);", html)
        self.assertIn("transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s ease;", html)
        self.assertIn(".dropdown-panel.show {", html)
        self.assertIn("transform: translateY(0);", html)
        self.assertIn("opacity: 1;", html)
        # Check all 3 dropdowns use the class
        self.assertIn("openMenu === 'workbench' ? 'show' : ''", html)
        self.assertIn("openMenu === 'custom' ? 'show' : ''", html)
        self.assertIn("openMenu === 'more' ? 'show' : ''", html)

    def test_inertial_scroll_intercepts_wheel_with_scrollable_child_guard(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("container.addEventListener('wheel', onWheel, { passive: false });", html)
        self.assertIn("isScrollableChild(e.target, e.deltaY, e.deltaX)", html)
        self.assertIn("requestAnimationFrame(smoothStep)", html)
        self.assertIn("targetScrollTop - current", html)

    def test_python_frameless_window_animations(self):
        shell = TUTOR_WINDOW.read_text(encoding="utf-8")
        self.assertIn("def maximizeOrRestore(self)", shell)
        self.assertIn("def toggle_maximize(self)", shell)
        self.assertIn("self.showMaximized()", shell)
        self.assertIn("self.showNormal()", shell)
        self.assertIn("def minimize_window(self)", shell)
        self.assertIn("self.showMinimized()", shell)
        self.assertIn("windowTransitionStarted", shell)
        self.assertIn("windowTransitionFinished", shell)
        self.assertIn("def close_with_animation(self)", shell)
        self.assertIn("def _setup_windows_frameless(self)", shell)
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("window.windowControl =", html)

    def test_frontend_visual_deception_transition_styles(self):
        html = PREVIEW.read_text(encoding="utf-8")
        self.assertIn("#transition-mask", html)
        self.assertIn("is-transitioning", html)
        self.assertIn("#app {", html)
        self.assertIn(".app-fading {", html)
        self.assertIn("triggerWithFading", html)



    def test_animated_frameless_window_property_animation_and_event_loop(self):
        from ui.tutor_window import AnimatedFramelessWindow
        from PySide6.QtCore import QRect, QSize

        app = QApplication.instance() or QApplication([])
        win = AnimatedFramelessWindow()
        win.setGeometry(QRect(100, 100, 400, 300))

        # Test property getters & setters as per CSDN article
        win.setAnimSize(QSize(500, 400))
        self.assertEqual(win.animSize, QSize(500, 400))
        self.assertEqual(win.getAnimSize(), QSize(500, 400))

        win.setAnimGeometry(QRect(120, 120, 480, 360))
        self.assertEqual(win.animGeometry, QRect(120, 120, 480, 360))
        self.assertEqual(win.getAnimGeometry(), QRect(120, 120, 480, 360))

        # Test signals and run method with QEventLoop
        started = []
        finished = []
        win.animStarted.connect(lambda: started.append(True))
        win.animFinished.connect(lambda: finished.append(True))

        win.run(QRect(120, 120, 480, 360), QRect(150, 150, 600, 450), duration=40)
        self.assertTrue(len(started) > 0)
        self.assertTrue(len(finished) > 0)
        self.assertEqual(win.geometry(), QRect(150, 150, 600, 450))
        win.close()

if __name__ == "__main__":
    unittest.main()
