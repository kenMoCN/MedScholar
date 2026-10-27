import tempfile
import unittest
from pathlib import Path

from core.skill_memory import (
    SkillEvidenceAnchor,
    SkillKind,
    SkillStatus,
    SkillStore,
    skill_prompt_records,
)


def anchor():
    return SkillEvidenceAnchor(
        document_id="doc_1",
        original_name="生理学.pdf",
        pdf_page=10,
        locator="教材印刷页3（PDF第10页）",
        section_title="静息电位",
        page_text_sha256="a" * 64,
        excerpt="静息电位主要由钾离子外流形成。",
    )


class SkillStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "skills.json"
        self.store = SkillStore(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pending_skill_is_never_recalled(self):
        record = self.store.create_candidate(
            question="静息电位如何形成",
            guidance="回答时先解释钾离子通透性。",
            kind=SkillKind.WORKFLOW,
            trigger_terms=("静息电位",),
        )
        self.assertEqual(record.status, SkillStatus.PENDING.value)
        self.assertEqual(self.store.match("解释静息电位"), ())

    def test_auto_trigger_terms_remove_question_scaffolding(self):
        record = self.store.create_candidate(
            question="请解释静息电位如何形成",
            guidance="先核对离子通透性。",
            kind=SkillKind.WORKFLOW,
        )
        self.store.approve(record.skill_id)
        self.assertEqual(record.trigger_terms, ("静息电位",))
        self.assertEqual(self.store.match("静息电位的影响因素")[0].skill_id, record.skill_id)

    def test_medical_skill_requires_evidence_before_approval(self):
        record = self.store.create_candidate(
            question="静息电位",
            guidance="静息电位与钾通透性有关。",
            kind=SkillKind.MEDICAL,
            trigger_terms=("静息电位",),
        )
        with self.assertRaisesRegex(ValueError, "绑定可追溯证据"):
            self.store.approve(record.skill_id)

    def test_approved_evidence_bound_skill_persists_and_matches(self):
        record = self.store.create_candidate(
            question="静息电位如何形成",
            guidance="优先核对钾平衡电位和静息钾通透性。",
            kind=SkillKind.MEDICAL,
            trigger_terms=("静息电位", "钾通透性"),
            evidence=(anchor(),),
        )
        self.store.approve(record.skill_id, review_note="已核对教材原文")
        reloaded = SkillStore(self.path)
        matches = reloaded.match("请解释静息电位和钾通透性")
        self.assertEqual(matches[0].skill_id, record.skill_id)
        self.assertEqual(matches[0].status, SkillStatus.ACTIVE.value)
        prompt = skill_prompt_records(matches)
        self.assertEqual(prompt[0]["evidence_anchors"][0]["document_id"], "doc_1")
        self.assertNotIn("excerpt", prompt[0]["evidence_anchors"][0])

    def test_rejected_skill_is_not_recalled(self):
        record = self.store.create_candidate(
            question="动作电位",
            guidance="按分期解释。",
            kind=SkillKind.WORKFLOW,
            trigger_terms=("动作电位",),
        )
        self.store.reject(record.skill_id, review_note="不够具体")
        self.assertEqual(self.store.match("动作电位"), ())


if __name__ == "__main__":
    unittest.main()
