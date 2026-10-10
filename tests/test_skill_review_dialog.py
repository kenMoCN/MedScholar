import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from skill_memory import SkillStatus
from skill_review_dialog import SkillReviewDialog


class SkillReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_workflow_feedback_stays_pending_until_manual_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            dialog = SkillReviewDialog(Path(temp), question="动作电位怎么复习")
            try:
                dialog.kind_combo.setCurrentIndex(1)
                dialog.trigger_edit.setText("动作电位")
                dialog.guidance_edit.setPlainText("以后先给分期表，再解释离子机制。")
                dialog._submit_candidate()
                pending = dialog.store.list_records()
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0].status, SkillStatus.PENDING.value)
                dialog.table.selectRow(0)
                dialog._approve_selected()
                active = dialog.store.list_records()
                self.assertEqual(active[0].status, SkillStatus.ACTIVE.value)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
