import unittest

from evidence.evidence_labels import (
    friendly_claim_certainty,
    friendly_evidence_label,
    friendly_resource_intro,
)


class EvidenceLabelsTests(unittest.TestCase):
    def test_legacy_e_label_is_explained_without_jargon(self):
        label = friendly_evidence_label("证据层级E（其他研究设计）")
        self.assertEqual(label, "研究类型暂未识别｜可作拓展阅读，不能单独支撑结论")
        self.assertNotIn("层级", label)

    def test_publication_type_overrides_legacy_unknown_label(self):
        label = friendly_evidence_label(
            "证据层级E（其他研究设计）", ("Practice Guideline",)
        )
        self.assertIn("临床指南", label)
        self.assertIn("适用人群", label)
        self.assertNotIn("层级", label)

    def test_observational_study_explains_limit(self):
        label = friendly_evidence_label(publication_types=("Cohort Study",))
        self.assertIn("不能单独证明因果", label)

    def test_internal_certainty_is_translated(self):
        self.assertEqual(
            friendly_claim_certainty("moderate"),
            "摘要支持这条说法，但仍需结合全文",
        )

    def test_resource_intro_prefers_source_description(self):
        self.assertEqual(
            friendly_resource_intro("  本研究探讨动作电位的离子机制。  "),
            "本研究探讨动作电位的离子机制。",
        )

    def test_resource_intro_does_not_show_raw_english_abstract(self):
        intro = friendly_resource_intro(
            "This paper studies membrane potential.",
            publication_types=("Cohort Study",),
        )
        self.assertNotIn("This paper", intro)
        self.assertIn("这是一份观察性研究资料", intro)

    def test_resource_intro_has_readable_fallback(self):
        intro = friendly_resource_intro(
            raw_level="证据层级E（其他研究设计）",
            publication_types=("Practice Guideline",),
        )
        self.assertIn("这是一份临床指南或专家共识资料", intro)
        self.assertNotIn("层级", intro)


if __name__ == "__main__":
    unittest.main()
