"""Regression checks for the setup wizard's local API-key tutorial."""

from pathlib import Path
import unittest
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
HTML = APP_DIR / "ui" / "preview_wizard.html"
TUTORIAL_DIR = APP_DIR / "Process"


class SetupTutorialTests(unittest.TestCase):
    def test_tutorial_assets_are_complete_and_safe(self):
        for provider, count in (("ds", 7), ("db", 11)):
            for number in range(1, count + 1):
                self.assertTrue((TUTORIAL_DIR / provider / f"{number}.png").is_file())
            for suffix in ("", "2"):
                self.assertTrue((TUTORIAL_DIR / f"{provider}{suffix}.png").is_file())

    def test_card_carousel_and_original_submission_are_present(self):
        html = HTML.read_text(encoding="utf-8")
        for marker in (
            "还不会获取 API Key？",
            "点这里查看教程",
            "showTutorialModal",
            "ReactDOM.createPortal",
            "zIndex: 2147483647",
            "document.body.style.overflow = 'hidden'",
            "event.key === 'Escape'",
            "event.key === 'ArrowLeft'",
            "event.key === 'ArrowRight'",
            "TUTORIAL_IMAGES.map",
            "onClick={closeTutorial}",
            "返回配置界面",
            "finishWizard",
        ):
            self.assertIn(marker, html)
        self.assertIn("tutorialImages[tutorialProvider] || []", html)
        self.assertIn("openTutorial('deepseek')", html)
        self.assertIn("openTutorial('doubao')", html)
        self.assertIn("openProviderSite('deepseek')", html)
        self.assertIn("openProviderSite('doubao')", html)
        self.assertIn("一键跳转 https://www.deepseek.com/", html)
        self.assertIn("一键跳转 https://ark.volcengine.com/region:cn-beijing/model?view=DEFAULT_VIEW&amp;groupType=ModelGroups", html)
        self.assertNotIn('src="Process/ds2.png"', html)
        self.assertNotIn('src="Process/db2.png"', html)
        self.assertIn("tutorialPreloadsRef.current = TUTORIAL_IMAGES.map", html)
        self.assertNotIn("key={tutorialIndex}", html)
        overlay_opening = html.split("ref={tutorialOverlayRef}", 1)[1].split("ref={tutorialCloseButtonRef}", 1)[0]
        self.assertNotIn("onClick={closeTutorial}", overlay_opening)
        self.assertIn("if (event.target === event.currentTarget) closeTutorial();", overlay_opening)
        self.assertIn("w-16 h-24", html)
        self.assertNotIn("transition-opacity duration-200 ${index === tutorialIndex", html)

    def test_optional_doubao_requires_key_only_when_enabled(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn("if (currentStep === 1 && !validateApiKeys())", html)
        self.assertIn("if (step > 1 && !validateApiKeys()) return;", html)
        self.assertIn("onClick={() => goToStep(step)}", html)
        self.assertIn("hasValue(config.deepseekApiKey)", html)
        self.assertIn("DEEPSEEK 模型名称：（默认填写deepseek-flash）", html)
        label = html.split("DEEPSEEK 模型名称：（默认填写deepseek-flash）", 1)[0].rsplit("<label className=", 1)[1]
        self.assertNotIn("uppercase", label)
        self.assertIn("!config.enableImageRecognition || (hasValue(config.doubaoVisionModelName) && hasValue(config.doubaoApiKey))", html)
        self.assertIn("checked={config.enableImageRecognition}", html)
        self.assertIn("启用豆包图片识别", html)
        self.assertIn("豆包 模型名称:(默认填写doubao-seed-evolving)", html)
        doubao_label = html.split("豆包 模型名称:(默认填写doubao-seed-evolving)", 1)[0].rsplit("<label className=", 1)[1]
        self.assertNotIn("uppercase", doubao_label)
        self.assertNotIn("普通文字提问只使用回答模型；仅在上传题目图片或建立课件视觉索引时调用豆包。", html)
        self.assertIn("请先填写 DeepSeek API Key，才能进入下一页。", html)
        self.assertIn("请先填写豆包 API Key，才能进入下一页。", html)
        self.assertIn("transition-[max-height,opacity,margin-top] duration-200", html)
        self.assertEqual(html.count("flex h-full min-h-0 flex-col items-center justify-center gap-2 rounded-[18px]"), 2)
        self.assertEqual(html.count("md:grid-cols-[minmax(0,1fr)_minmax(0,0.35fr)]"), 2)
        self.assertNotIn('className="min-h-[64px] flex-[1]"', html)
        self.assertIn("max-w-full break-all text-left text-xs", html)

    def test_python_bridge_scans_both_tutorials_and_has_fixed_destinations(self):
        from ui.setup import AgentSetupWizard, WizardBridge

        self.assertEqual(len(AgentSetupWizard._tutorial_images("ds")), 7)
        self.assertEqual(len(AgentSetupWizard._tutorial_images("db")), 11)
        self.assertEqual(AgentSetupWizard._tutorial_images("db")[-1], "Process/db/11.png")
        source = (APP_DIR / "ui" / "setup.py").read_text(encoding="utf-8")
        self.assertIn("QDesktopServices.openUrl(QUrl(urls[provider]))", source)
        self.assertIn("https://www.deepseek.com/", source)
        self.assertIn("https://ark.volcengine.com/region:cn-beijing/model?view=DEFAULT_VIEW&groupType=ModelGroups", source)

        bridge = WizardBridge(None)
        with patch("ui.setup.QDesktopServices.openUrl") as open_url:
            bridge.openProviderSite("deepseek")
            bridge.openProviderSite("doubao")
            bridge.openProviderSite("unknown")
        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(open_url.call_args_list[0].args[0].toString(), "https://www.deepseek.com/")
        self.assertEqual(
            open_url.call_args_list[1].args[0].toString(),
            "https://ark.volcengine.com/region:cn-beijing/model?view=DEFAULT_VIEW&groupType=ModelGroups",
        )


if __name__ == "__main__":
    unittest.main()
