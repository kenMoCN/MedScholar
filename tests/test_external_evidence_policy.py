import unittest
from types import SimpleNamespace

from external_evidence_policy import (
    assess_authority,
    evaluate_external_publication_gate,
)


def record(
    resource_id,
    *,
    level="E",
    abstract=False,
    excerpt=False,
    guideline=False,
    clinical_guidance=False,
    full_text=False,
):
    return SimpleNamespace(
        resource_id=resource_id,
        authority_level=level,
        has_abstract=abstract,
        has_excerpt=excerpt,
        is_guideline_or_consensus=guideline,
        clinical_answer_eligible=clinical_guidance,
        has_full_text=full_text,
    )


class ExternalEvidencePolicyTests(unittest.TestCase):
    def test_authority_grading_distinguishes_guidelines_reviews_and_wikipedia(self):
        guideline = assess_authority(
            source="pubmed", publication_types=("Practice Guideline",)
        )
        review = assess_authority(
            source="pubmed", publication_types=("Systematic Review",)
        )
        wiki = assess_authority(source="wikipedia_zh")
        self.assertEqual(guideline.level, "A")
        self.assertTrue(guideline.is_guideline_or_consensus)
        self.assertEqual(review.level, "A")
        self.assertFalse(review.is_guideline_or_consensus)
        self.assertEqual(wiki.level, "E")

    def test_terminology_can_enter_verification_from_traceable_excerpt(self):
        gate = evaluate_external_publication_gate(
            "terminology", [record("wiki", excerpt=True)]
        )
        self.assertTrue(gate.may_publish_answer)
        self.assertEqual(gate.qualifying_resource_ids, ("wiki",))

    def test_basic_fact_requires_level_a_or_two_independent_sources(self):
        one_review = evaluate_external_publication_gate(
            "course_fact", [record("r1", level="D", abstract=True)]
        )
        two_reviews = evaluate_external_publication_gate(
            "course_fact",
            [
                record("r1", level="D", abstract=True),
                record("r2", level="C", abstract=True),
            ],
        )
        self.assertFalse(one_review.may_publish_answer)
        self.assertTrue(two_reviews.may_publish_answer)

    def test_clinical_question_rejects_systematic_review_without_guideline(self):
        gate = evaluate_external_publication_gate(
            "clinical", [record("sr", level="A", abstract=True)]
        )
        self.assertFalse(gate.may_publish_answer)
        self.assertIn("指南", gate.reason)

    def test_clinical_question_accepts_guideline_content_for_next_verification(self):
        gate = evaluate_external_publication_gate(
            "clinical",
            [record(
                "g1", level="A", abstract=True, guideline=True, full_text=True
            )],
        )
        self.assertTrue(gate.may_publish_answer)
        self.assertEqual(gate.qualifying_resource_ids, ("g1",))

    def test_clinical_question_accepts_official_treatment_guidance_full_text(self):
        gate = evaluate_external_publication_gate(
            "clinical",
            [record(
                "cdc1", level="A", excerpt=True,
                clinical_guidance=True, full_text=True,
            )],
        )
        self.assertTrue(gate.may_publish_answer)
        self.assertEqual(gate.qualifying_resource_ids, ("cdc1",))
        self.assertIn("官方处置指引", gate.reason)

    def test_guideline_abstract_alone_is_not_enough_for_clinical_answer(self):
        gate = evaluate_external_publication_gate(
            "clinical",
            [record("g1", level="A", abstract=True, guideline=True)],
        )
        self.assertFalse(gate.may_publish_answer)
        self.assertIn("只有摘要", gate.reason)

    def test_research_evidence_never_replaces_core_answer(self):
        gate = evaluate_external_publication_gate(
            "research", [record("r1", level="A", abstract=True)]
        )
        self.assertFalse(gate.may_publish_answer)
        self.assertEqual(gate.decision, "candidates_only")


if __name__ == "__main__":
    unittest.main()
