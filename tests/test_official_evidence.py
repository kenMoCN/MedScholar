import hashlib
import unittest

from evidence.official_evidence import OfficialEvidenceBundleBuilder
from evidence.official_guidelines import OfficialGuidelineRecord


def record(text):
    return OfficialGuidelineRecord(
        "who", "WHO/1", "WHO shock guideline", "https://who.test/1",
        2025, "Summary", "WHO/1", "https://iris.test/1.pdf", text,
    )


class OfficialEvidenceBundleTests(unittest.TestCase):
    def test_recommendation_spans_rank_above_background(self):
        text = (
            "Shock is a circulatory disorder. Hemodynamic changes vary by cause.\n\n"
            "For septic shock, clinicians should reassess perfusion after initial "
            "resuscitation. The guideline recommends repeated clinical assessment.\n\n"
            "Unrelated appendix material about publication methods."
        )
        bundle = OfficialEvidenceBundleBuilder().build(
            "septic shock resuscitation", [record(text)], max_span_chars=130
        )
        self.assertTrue(bundle.evidence)
        best = bundle.evidence[0]
        self.assertIn("should reassess", best.evidence_text)
        self.assertIn("shock", best.matched_terms)
        self.assertIn("should", best.recommendation_markers)
        self.assertEqual(
            best.full_text_sha256,
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(text[best.span_start:best.span_end].strip(), best.evidence_text)

    def test_irrelevant_full_text_does_not_create_evidence(self):
        bundle = OfficialEvidenceBundleBuilder().build(
            "septic shock", [record("Recommendations about malaria prevention.")]
        )
        self.assertEqual(bundle.evidence, [])
        self.assertTrue(any("未定位" in item for item in bundle.warnings))

    def test_empty_full_text_is_metadata_only(self):
        empty = record("")
        bundle = OfficialEvidenceBundleBuilder().build("shock", [empty])
        self.assertEqual(bundle.evidence, [])

    def test_hypoglycemia_query_matches_plain_language_low_blood_sugar_steps(self):
        text = (
            "Background information about diabetes.\n\n"
            "If your blood sugar is low, have 15 grams of carbs and wait 15 minutes. "
            "Check again and repeat treatment if it is still below 70 mg/dL.\n\n"
            "For severely low blood sugar, use glucagon and seek emergency care."
        )
        bundle = OfficialEvidenceBundleBuilder().build(
            "type 2 diabetes hypoglycemia treatment",
            [record(text)],
            max_span_chars=180,
        )
        combined = " ".join(item.evidence_text for item in bundle.evidence)
        self.assertIn("15 grams", combined)
        self.assertIn("glucagon", combined)
        self.assertTrue(any(
            "low blood sugar" in item.matched_terms for item in bundle.evidence
        ))


if __name__ == "__main__":
    unittest.main()
