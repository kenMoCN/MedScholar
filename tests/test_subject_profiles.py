import unittest

from core.subject_profiles import (
    infer_document_subjects,
    infer_query_subjects,
    normalize_subject,
    subject_options,
)


class SubjectProfileTests(unittest.TestCase):
    def test_document_name_gets_conservative_subject_tag(self):
        self.assertEqual(
            infer_document_subjects("生物化学与分子生物学第九版.pdf"),
            ("biochemistry",),
        )

    def test_query_can_route_to_multiple_related_subjects(self):
        subjects = infer_query_subjects("从休克机制和药理学解释肾上腺素的作用")
        self.assertIn("pathophysiology", subjects)
        self.assertIn("pharmacology", subjects)

    def test_auto_and_catalog_are_stable(self):
        self.assertEqual(normalize_subject("AUTO"), "auto")
        self.assertEqual(subject_options()[0]["id"], "auto")
        with self.assertRaises(ValueError):
            normalize_subject("unknown-course")


if __name__ == "__main__":
    unittest.main()
