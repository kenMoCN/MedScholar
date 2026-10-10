import unittest

from external_retrievers import ExternalResource
from research_evidence import (
    HybridResearchRetriever,
    ResearchPaper,
    ResearchPacket,
    ResearchFilters,
    ResearchQueryPlanner,
    ResearchSearchPlan,
    ResearchSynthesisEngine,
    render_research,
)


class FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, system_instruction, payload):
        self.calls.append((system_instruction, payload))
        return self.response


class FakeVerifier:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def verify_json(self, request):
        self.calls.append(request)
        return self.response


class FakeRetriever:
    def __init__(self, results=(), error=None):
        self.results = results
        self.error = error
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.results


def resource(source, title, abstract, *, doi=None, year=2024, types=(), cited=0):
    return ExternalResource(
        source=source,
        resource_id=f"{source}-1",
        title=title,
        url=f"https://example.test/{source}",
        snippet=abstract,
        year=year,
        authors=("A Author",),
        identifiers={"doi": doi} if doi else {},
        publication_types=types,
        cited_by_count=cited,
    )


class QueryPlanningTests(unittest.TestCase):
    def test_planning_model_failure_keeps_search_available(self):
        class BrokenPlanner:
            def complete_json(self, *args):
                raise TimeoutError("planning timeout")

        plan = ResearchQueryPlanner(BrokenPlanner()).plan("action potential review")
        self.assertEqual(plan.english_query, "action potential review")
        self.assertIn("action", plan.keywords)

    def test_model_plan_keeps_bilingual_query_and_filters(self):
        client = FakeJsonClient({
            "chinese_query": "动作电位 钾通道",
            "english_query": "action potential potassium channel",
            "keywords": ["action potential", "potassium"],
            "year_from": 2020,
            "year_to": 2025,
            "study_types": ["review"],
        })
        plan = ResearchQueryPlanner(client).plan("动作电位研究进展")
        self.assertEqual(plan.english_query, "action potential potassium channel")
        self.assertEqual(plan.keywords, ("action potential", "potassium"))
        self.assertEqual((plan.year_from, plan.year_to), (2020, 2025))

    def test_model_plan_discards_duplicate_non_english_relevance_terms(self):
        client = FakeJsonClient({
            "chinese_query": "休克 中心静脉压",
            "english_query": "shock central venous pressure",
            "keywords": ["休克", "中心静脉压", "shock", "central venous pressure"],
            "year_from": None,
            "year_to": 2026,
            "study_types": [],
        })
        plan = ResearchQueryPlanner(client).plan("休克处理")
        self.assertEqual(plan.keywords, ("shock", "central venous pressure"))


class HybridRetrievalTests(unittest.TestCase):
    def test_cross_source_merge_preserves_pubmed_pmc_identifier(self):
        pubmed_record = ExternalResource(
            source="pubmed",
            resource_id="1",
            title="Open guideline for shock",
            url="https://pubmed.ncbi.nlm.nih.gov/1/",
            snippet="Shock guideline abstract.",
            year=2025,
            identifiers={"doi": "10.1000/open", "pmc": "PMC12345"},
            publication_types=("Practice Guideline",),
        )
        openalex_record = resource(
            "openalex",
            "Open guideline for shock",
            "Shock guideline abstract with additional details.",
            doi="10.1000/open",
            year=2025,
        )
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "休克指南",
            "english_query": "shock guideline",
            "keywords": ["shock", "guideline"],
            "year_from": None,
            "year_to": 2026,
            "study_types": [],
        }))
        packet = HybridResearchRetriever(
            pubmed=FakeRetriever((pubmed_record,)),
            openalex=FakeRetriever((openalex_record,)),
            planner=planner,
        ).search("休克指南")
        self.assertEqual(packet.papers[0].identifiers["pmc"], "PMC12345")

    def test_same_doi_is_cross_validated_and_relevance_beats_citations(self):
        pubmed = FakeRetriever((resource(
            "pubmed", "Potassium channels in action potentials",
            "Potassium channels regulate the action potential.",
            doi="10.1000/test", types=("Systematic Review",), cited=3,
        ),))
        openalex = FakeRetriever((
            resource(
                "openalex", "Potassium channels in action potentials",
                "Potassium channels regulate the action potential in excitable cells.",
                doi="https://doi.org/10.1000/TEST", types=("review",), cited=20,
            ),
            resource(
                "openalex", "Unrelated cancer atlas", "Tumour sequencing cohort.",
                cited=5000,
            ),
        ))
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "动作电位 钾通道",
            "english_query": "action potential potassium channels",
            "keywords": ["action potential", "potassium"],
            "year_from": 2020,
            "year_to": 2025,
            "study_types": [],
        }))
        packet = HybridResearchRetriever(
            pubmed=pubmed, openalex=openalex, planner=planner
        ).search("动作电位研究", limit=8)
        self.assertEqual(len(packet.papers), 1)
        paper = packet.papers[0]
        self.assertEqual(paper.sources, ("openalex", "pubmed"))
        self.assertTrue(paper.cross_validated)
        self.assertIn("系统综述", paper.evidence_level)
        self.assertTrue(pubmed.calls[0][1]["include_abstracts"])

    def test_study_type_is_translated_to_pubmed_publication_filter(self):
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "膜电位综述",
            "english_query": "membrane potential",
            "keywords": ["membrane potential"],
            "year_from": None,
            "year_to": 2026,
            "study_types": ["systematic review"],
        }))
        pubmed = FakeRetriever()
        HybridResearchRetriever(
            pubmed=pubmed, openalex=FakeRetriever(), planner=planner
        ).search("膜电位综述")
        self.assertIn('"Systematic Review"[Publication Type]', pubmed.calls[0][0])

    def test_long_question_with_options_is_compacted_for_databases(self):
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "休克 中心静脉压",
            "english_query": (
                "Shock patient with elevated central venous pressure and normal blood "
                "pressure: which treatment? Options: A fluids B vasodilators"
            ),
            "keywords": ["shock", "central venous pressure", "blood pressure", "vasodilators"],
            "year_from": None,
            "year_to": 2026,
            "study_types": [],
        }))
        pubmed = FakeRetriever()
        openalex = FakeRetriever()
        HybridResearchRetriever(
            pubmed=pubmed, openalex=openalex, planner=planner
        ).search("休克题")
        self.assertNotIn("Options", pubmed.calls[0][0])
        self.assertNotIn("vasodilators", openalex.calls[0][0])

    def test_ui_filters_override_automatic_plan(self):
        pubmed = FakeRetriever()
        packet = HybridResearchRetriever(
            pubmed=pubmed,
            openalex=FakeRetriever(),
            planner=ResearchQueryPlanner(),
        ).search(
            "membrane potential",
            filters=ResearchFilters(
                year_from=2021,
                year_to=2024,
                study_types=("randomized controlled trial",),
            ),
        )
        self.assertEqual((packet.plan.year_from, packet.plan.year_to), (2021, 2024))
        self.assertIn('"Randomized Controlled Trial"[Publication Type]', pubmed.calls[0][0])
        self.assertEqual(pubmed.calls[0][1]["year_from"], 2021)

    def test_empty_ui_filters_preserve_planner_constraints(self):
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "动作电位钾通道综述",
            "english_query": "action potential potassium channel",
            "keywords": ["action potential", "potassium"],
            "year_from": 2020,
            "year_to": 2025,
            "study_types": ["review"],
        }))
        pubmed = FakeRetriever()
        packet = HybridResearchRetriever(
            pubmed=pubmed, openalex=FakeRetriever(), planner=planner,
        ).search("动作电位综述", filters=ResearchFilters())
        self.assertEqual((packet.plan.year_from, packet.plan.year_to), (2020, 2025))
        self.assertEqual(packet.plan.study_types, ("review",))
        self.assertIn('"Review"[Publication Type]', pubmed.calls[0][0])

    def test_empty_strict_search_retries_core_concept_once(self):
        class BroadOnlyRetriever(FakeRetriever):
            def search(self, query, **kwargs):
                self.calls.append((query, kwargs))
                if query == '"action potential"':
                    return (resource(
                        "openalex", "Action potential in excitable cells",
                        "A review of excitable cell physiology.",
                    ),)
                return ()

        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "动作电位 钾通道",
            "english_query": "action potential potassium channel",
            "keywords": ["action potential", "potassium", "ion channels"],
            "year_from": None,
            "year_to": 2026,
            "study_types": [],
        }))
        openalex = BroadOnlyRetriever()
        packet = HybridResearchRetriever(
            pubmed=FakeRetriever(), openalex=openalex, planner=planner,
        ).search("动作电位钾通道", limit=5)
        self.assertEqual(len(packet.papers), 1)
        self.assertEqual(openalex.calls[-1][0], '"action potential"')
        self.assertTrue(any("放宽检索" in warning for warning in packet.warnings))

    def test_invalid_ui_year_range_is_rejected(self):
        agent = HybridResearchRetriever(
            pubmed=FakeRetriever(), openalex=FakeRetriever(),
            planner=ResearchQueryPlanner(),
        )
        with self.assertRaisesRegex(ValueError, "起始年份"):
            agent.search(
                "query",
                filters=ResearchFilters(year_from=2025, year_to=2020),
            )

    def test_one_source_failure_keeps_other_source_results(self):
        packet = HybridResearchRetriever(
            pubmed=FakeRetriever(error=TimeoutError()),
            openalex=FakeRetriever((resource("openalex", "Paper", "Abstract"),)),
            planner=ResearchQueryPlanner(),
        ).search("paper")
        self.assertEqual(len(packet.papers), 1)
        self.assertTrue(any("pubmed" in warning for warning in packet.warnings))

    def test_broad_retry_does_not_repeat_failed_provider(self):
        pubmed = FakeRetriever(error=TimeoutError())
        openalex = FakeRetriever()
        planner = ResearchQueryPlanner(FakeJsonClient({
            "chinese_query": "动作电位",
            "english_query": "action potential potassium channels",
            "keywords": ["action potential", "potassium"],
            "year_from": None, "year_to": 2026, "study_types": [],
        }))
        HybridResearchRetriever(
            pubmed=pubmed, openalex=openalex, planner=planner,
        ).search("动作电位")
        self.assertEqual(len(pubmed.calls), 1)
        self.assertEqual(len(openalex.calls), 2)

    def test_retrieval_reports_real_phase_transitions(self):
        updates = []
        HybridResearchRetriever(
            pubmed=FakeRetriever(), openalex=FakeRetriever(),
            planner=ResearchQueryPlanner(),
        ).search("membrane potential", progress_callback=lambda *args: updates.append(args))
        phase_states = {(phase, state) for phase, _, state in updates}
        self.assertIn(("research_planning", "running"), phase_states)
        self.assertIn(("research_planning", "ok"), phase_states)
        self.assertIn(("cross_source_merge", "ok"), phase_states)


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        plan = ResearchSearchPlan("query", "查询", "query", ("query",), None, 2026, ())
        self.packet = ResearchPacket(plan, [ResearchPaper(
            paper_id="paper_1",
            title="A paper",
            year=2025,
            authors=("A",),
            abstract="Potassium channels shorten repolarization in this experiment.",
            url="https://example.test/paper",
            doi="10/x",
            sources=("pubmed",),
            cross_validated=False,
            publication_types=("Randomized Controlled Trial",),
            evidence_level="证据层级B（随机对照研究）",
            relevance_score=1.0,
        )])

    def test_render_never_publishes_unverified_free_form_overview(self):
        from research_evidence import ResearchClaim, ResearchSynthesis

        synthesis = ResearchSynthesis(
            "pass",
            "这一概述没有逐字引文，不能展示。",
            [ResearchClaim(
                "rc_1", "可核验主张", ["paper_1"],
                [{"paper_id": "paper_1", "quote": "Potassium channels"}],
                "moderate",
            )],
            [],
        )
        rendered = render_research(self.packet, synthesis)
        self.assertNotIn("这一概述没有逐字引文", rendered)
        self.assertIn("可核验主张", rendered)

    def generator(self, quote=None):
        return FakeJsonClient({
            "overview": "摘要证据综合。",
            "claims": [{
                "claim_id": "rc_1",
                "text": "该实验中钾通道缩短复极。",
                "paper_ids": ["paper_1"],
                "quotes": [{
                    "paper_id": "paper_1",
                    "quote": quote or "Potassium channels shorten repolarization",
                }],
                "certainty": "moderate",
            }],
            "limitations": ["仅一项研究"],
        })

    def test_exact_quote_and_independent_support_are_required(self):
        updates = []
        engine = ResearchSynthesisEngine(
            self.generator(),
            FakeVerifier({"claims": [{"claim_id": "rc_1", "verdict": "supported", "reason": "direct"}]}),
        )
        synthesis = engine.synthesize(
            self.packet, progress_callback=lambda *args: updates.append(args)
        )
        self.assertEqual(synthesis.status, "pass")
        self.assertIn("该实验中", render_research(self.packet, synthesis))
        self.assertIn(
            ("research_verification", "全部科研主张通过独立复核", "ok"),
            updates,
        )

    def test_fabricated_quote_fails_before_semantic_verifier(self):
        verifier = FakeVerifier({"claims": []})
        synthesis = ResearchSynthesisEngine(
            self.generator("This sentence is not in the abstract."), verifier
        ).synthesize(self.packet)
        self.assertEqual(synthesis.status, "fail")
        self.assertEqual(verifier.calls, [])

    def test_partial_verdict_hides_claim_and_only_lists_candidates(self):
        synthesis = ResearchSynthesisEngine(
            self.generator(),
            FakeVerifier({"claims": [{"claim_id": "rc_1", "verdict": "partial", "reason": "overstated"}]}),
        ).synthesize(self.packet)
        rendered = render_research(self.packet, synthesis)
        self.assertEqual(synthesis.status, "review")
        self.assertIn("仅列出候选论文", rendered)
        self.assertNotIn("该实验中钾通道缩短复极", rendered)

    def test_chinese_paper_intro_requires_quote_and_independent_support(self):
        raw = dict(self.generator().response)
        raw["paper_introductions"] = [{
            "intro_id": "intro_paper_1",
            "paper_id": "paper_1",
            "text": "本研究探讨钾通道对复极过程的影响。",
            "quote": "Potassium channels shorten repolarization",
        }]
        engine = ResearchSynthesisEngine(
            FakeJsonClient(raw),
            FakeVerifier({"claims": [
                {"claim_id": "rc_1", "verdict": "supported", "reason": "direct"},
                {"claim_id": "intro_paper_1", "verdict": "supported", "reason": "faithful"},
            ]}),
        )
        synthesis = engine.synthesize(self.packet)
        self.assertEqual(synthesis.status, "pass")
        self.assertEqual(
            synthesis.introductions[0].text,
            "本研究探讨钾通道对复极过程的影响。",
        )

    def test_resource_title_translation_and_purpose_require_verification(self):
        generator = FakeJsonClient({"resources": [{
            "resource_id": "who_1",
            "chinese_title": "妊娠期糖尿病女性照护建议",
            "purpose": "说明妊娠期糖尿病女性的照护重点与适用建议。",
        }]})
        verifier = FakeVerifier({"resources": [{
            "resource_id": "who_1", "verdict": "supported", "reason": "faithful",
        }]})
        localized = ResearchSynthesisEngine(generator, verifier).localize_resources([{
            "resource_id": "who_1",
            "title": "WHO recommendations on care for women with diabetes during pregnancy",
            "description": "",
        }])
        self.assertEqual(
            localized["who_1"]["chinese_title"],
            "妊娠期糖尿病女性照护建议",
        )
        self.assertIn("照护重点", localized["who_1"]["purpose"])


if __name__ == "__main__":
    unittest.main()
