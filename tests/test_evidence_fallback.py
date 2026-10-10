import unittest

from evidence_fallback import (
    EvidenceFallbackController,
    EvidencePolicy,
    FallbackQuestionType,
    classify_fallback_question,
    normalize_evidence_policy,
)
from external_retrievers import ExternalResource, OpenFullTextRecord
from official_guidelines import OfficialGuidelinePacket, OfficialGuidelineRecord
from retrieval_agent import RetrievalPacket, RetrievalPlan
from research_evidence import ResearchPaper, ResearchPacket, ResearchSearchPlan


class FakeLayeredRetriever:
    def __init__(self, resources=(), error=None):
        self.resources = list(resources)
        self.error = error
        self.calls = []

    def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return RetrievalPacket(
            plan=RetrievalPlan("hybrid", query, False, (), True, True),
            external_resources=list(self.resources),
        )


class FakeResearchRetriever:
    def __init__(self, packet):
        self.packet = packet
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.packet


class FakeFullTextRetriever:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch(self, pmcid):
        self.calls.append(pmcid)
        if self.error:
            raise self.error
        return self.result


class FakeOfficialGuidelineRetriever:
    def __init__(self, packet):
        self.packet = packet
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.packet


def resource(source, resource_id):
    return ExternalResource(
        source=source,
        resource_id=resource_id,
        title=f"Title {resource_id}",
        url=f"https://example.test/{resource_id}",
    )


class EvidenceFallbackTests(unittest.TestCase):
    def test_clinical_query_combines_who_full_text_with_research_candidates(self):
        plan = ResearchSearchPlan(
            "休克处理", "休克处理", "shock management", ("shock",),
            None, 2026, (),
        )
        official = FakeOfficialGuidelineRetriever(OfficialGuidelinePacket([
            OfficialGuidelineRecord(
                "who", "WHO/1", "WHO guideline for shock", "https://who.test/1",
                2025, "Summary", "WHO/1", "https://iris.test/1.pdf",
                "Shock recommendation: clinicians should reassess management.",
            )
        ]))
        outcome = EvidenceFallbackController(
            FakeLayeredRetriever(),
            research_retriever=FakeResearchRetriever(ResearchPacket(plan, [])),
            official_guideline_retriever=official,
        ).run("休克病人处理原则", mode="quick", policy="local_first")
        self.assertEqual(official.calls[0][0], "shock management")
        self.assertEqual(outcome.resources[0].source, "who")
        self.assertTrue(outcome.resources[0].has_full_text)
        self.assertEqual(outcome.resources[0].authority_level, "A")
        self.assertTrue(outcome.official_evidence)
        self.assertEqual(outcome.official_evidence[0].resource_id, "WHO/1")
        self.assertEqual(
            outcome.publication_decision,
            "eligible_for_verified_external_answer",
        )

    def test_clinical_guideline_open_full_text_enters_verification_gate(self):
        plan = ResearchSearchPlan(
            "休克处理", "休克处理", "shock management", ("shock", "management"),
            None, 2026, (),
        )
        paper = ResearchPaper(
            "paper_1", "Shock practice guideline", 2025, ("A",), "Abstract",
            "https://example.test/paper", "10/test", ("pubmed",),
            False, ("Practice Guideline",), "证据层级E（其他研究设计）", 1.0,
            identifiers={"pmc": "PMC12345"},
        )
        full_text = FakeFullTextRetriever(OpenFullTextRecord(
            "PMC12345", "Shock practice guideline",
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/",
            "CC BY 4.0", "Recommendation text that can be independently verified.",
        ))
        outcome = EvidenceFallbackController(
            FakeLayeredRetriever(),
            research_retriever=FakeResearchRetriever(ResearchPacket(plan, [paper])),
            full_text_retriever=full_text,
        ).run("休克病人处理原则", mode="quick", policy="local_first")
        self.assertEqual(outcome.publication_decision, "eligible_for_verified_external_answer")
        self.assertTrue(outcome.resources[0].may_determine_answer)
        self.assertTrue(outcome.resources[0].has_full_text)
        self.assertEqual(outcome.resources[0].full_text_char_count, 55)
        self.assertEqual(len(outcome.resources[0].full_text_sha256), 64)
        self.assertEqual(full_text.calls, ["PMC12345"])

    def test_clinical_guideline_without_reusable_full_text_remains_candidate(self):
        plan = ResearchSearchPlan(
            "休克处理", "休克处理", "shock management", ("shock", "management"),
            None, 2026, (),
        )
        paper = ResearchPaper(
            "paper_1", "Shock practice guideline", 2025, ("A",), "Abstract",
            "https://example.test/paper", None, ("pubmed",),
            False, ("Practice Guideline",), "证据层级E（其他研究设计）", 1.0,
            identifiers={"pmc": "PMC12345"},
        )
        outcome = EvidenceFallbackController(
            FakeLayeredRetriever(),
            research_retriever=FakeResearchRetriever(ResearchPacket(plan, [paper])),
            full_text_retriever=FakeFullTextRetriever(None),
        ).run("休克病人处理原则", mode="quick", policy="local_first")
        self.assertEqual(outcome.publication_decision, "candidates_only")
        self.assertFalse(outcome.resources[0].has_full_text)
        self.assertTrue(any("未提供可复用正文" in item for item in outcome.warnings))

    def test_local_only_never_calls_external_retriever(self):
        retriever = FakeLayeredRetriever([resource("pubmed", "1")])
        outcome = EvidenceFallbackController(retriever).run(
            "休克如何处理", mode="quick", policy="local_only"
        )
        self.assertEqual(outcome.status, "disabled")
        self.assertEqual(retriever.calls, [])

    def test_clinical_fallback_uses_research_route_and_rejects_wikipedia(self):
        retriever = FakeLayeredRetriever([
            resource("wikipedia_zh", "wiki"),
            resource("pubmed", "pmid"),
        ])
        outcome = EvidenceFallbackController(retriever).run(
            "休克病人处理原则", mode="quick", policy="local_first"
        )
        self.assertEqual(outcome.question_type, "clinical")
        self.assertEqual(retriever.calls[0][1]["mode"], "research")
        self.assertEqual([item.source for item in outcome.resources], ["pubmed"])
        self.assertFalse(outcome.resources[0].may_determine_answer)

    def test_external_candidates_are_never_marked_verified(self):
        outcome = EvidenceFallbackController(
            FakeLayeredRetriever([resource("wikipedia_zh", "wiki")])
        ).run("动作电位机制", mode="quick", policy=EvidencePolicy.LOCAL_FIRST)
        self.assertEqual(outcome.status, "candidates_found")
        self.assertEqual(outcome.resources[0].evidence_scope, "external_candidate")
        self.assertEqual(outcome.resources[0].verification_status, "not_content_verified")

    def test_course_fact_uses_basic_reference_route_not_paper_search(self):
        plan = ResearchSearchPlan(
            "辅助因子", "辅助因子", "enzyme cofactors", ("cofactor",),
            None, 2026, (),
        )
        research = FakeResearchRetriever(ResearchPacket(plan, []))
        layered = FakeLayeredRetriever([resource("wikipedia_zh", "wiki")])
        outcome = EvidenceFallbackController(
            layered, research_retriever=research
        ).run(
            "[单选题] 关于辅助因子，下列叙述错误的是：A包括金属离子；"
            "B结合牢固称辅酶；C决定底物专一性；D组成全酶",
            mode="quick",
            policy="local_first",
        )
        self.assertEqual(outcome.question_type, "course_fact")
        self.assertEqual(len(layered.calls), 1)
        self.assertEqual(research.calls, [])
        self.assertEqual(outcome.resources[0].source, "wikipedia_zh")

    def test_question_classifier_separates_term_clinical_and_research(self):
        self.assertEqual(
            classify_fallback_question("动作电位是什么", mode="quick"),
            FallbackQuestionType.TERMINOLOGY,
        )
        self.assertEqual(
            classify_fallback_question("患者应该如何治疗", mode="quick"),
            FallbackQuestionType.CLINICAL,
        )
        self.assertEqual(
            classify_fallback_question("休克病人处理原则", mode="research"),
            FallbackQuestionType.CLINICAL,
        )
        self.assertEqual(
            classify_fallback_question("动作电位研究进展", mode="research"),
            FallbackQuestionType.RESEARCH,
        )

    def test_unknown_policy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知证据策略"):
            normalize_evidence_policy("unsafe_model_memory")

    def test_clinical_question_prefers_bilingual_research_pipeline(self):
        plan = ResearchSearchPlan(
            "休克处理", "休克处理", "shock management", ("shock", "management"),
            None, 2026, (),
        )
        paper = ResearchPaper(
            "paper_1", "Shock management", 2025, ("A",), "Abstract", 
            "https://example.test/paper", "10/test", ("pubmed", "openalex"),
            True, ("review",), "证据层级D（叙述性综述）", 1.0,
        )
        research = FakeResearchRetriever(ResearchPacket(plan, [paper]))
        layered = FakeLayeredRetriever(error=AssertionError("不应使用原始查询管线"))
        outcome = EvidenceFallbackController(
            layered, research_retriever=research
        ).run("休克病人处理原则", mode="quick", policy="local_first")
        self.assertEqual(outcome.status, "candidates_found")
        self.assertEqual(len(research.calls), 1)
        self.assertEqual(layered.calls, [])
        self.assertTrue(outcome.resources[0].cross_validated)
        self.assertTrue(outcome.resources[0].has_abstract)
        self.assertEqual(outcome.resources[0].authority_level, "D")
        self.assertEqual(outcome.publication_decision, "candidates_only")


if __name__ == "__main__":
    unittest.main()
