import unittest

from core.medical_horizon import (
    MedicalHorizonPolicy,
    medical_horizon_policy_options,
    normalize_medical_horizon_policy,
    should_expand_medical_horizon,
)


class MedicalHorizonPolicyTests(unittest.TestCase):
    def test_policy_options_have_stable_frontend_ids(self):
        self.assertEqual(
            [item["id"] for item in medical_horizon_policy_options()],
            ["off", "research_mode_only", "all_answers"],
        )

    def test_research_mode_only_does_not_slow_normal_answers(self):
        self.assertFalse(should_expand_medical_horizon(
            MedicalHorizonPolicy.RESEARCH_MODE_ONLY, mode="quick"
        ))
        self.assertTrue(should_expand_medical_horizon(
            MedicalHorizonPolicy.RESEARCH_MODE_ONLY, mode="research"
        ))

    def test_all_answers_enables_extension_for_guided_mode(self):
        self.assertTrue(should_expand_medical_horizon(
            "all_answers", mode="guided"
        ))

    def test_unknown_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知医学视野拓展策略"):
            normalize_medical_horizon_policy("hallucinate")


if __name__ == "__main__":
    unittest.main()

