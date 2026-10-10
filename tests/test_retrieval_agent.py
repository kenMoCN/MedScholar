import unittest

from external_retrievers import (
    OpenAlexRetriever,
    PmcOpenAccessRetriever,
    PubMedRetriever,
    WikipediaRetriever,
)
from retrieval_agent import LayeredRetrievalAgent, RetrievalPlanner
from structure_retriever import RetrievalResult


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, url, params, headers, timeout_seconds):
        self.calls.append((url, params, headers, timeout_seconds))
        return self.responses.pop(0)


class FakeTextTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_text(self, url, params, headers, timeout_seconds):
        self.calls.append((url, params, headers, timeout_seconds))
        return self.response


class FakeLocalRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        return RetrievalResult(query, "test", [], [])


class FakeExternal:
    def __init__(self, results=(), error=None):
        self.results = results
        self.error = error
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.results


class ExternalRetrieverTests(unittest.TestCase):
    def test_pmc_oai_retrieves_reusable_jats_full_text(self):
        transport = FakeTextTransport(
            '<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            '<GetRecord><record><metadata><article xmlns="http://jats.nlm.nih.gov">'
            '<front><article-meta><title-group><article-title>Shock guideline</article-title>'
            '</title-group><permissions><license><license-p>CC BY 4.0</license-p>'
            '</license></permissions></article-meta></front>'
            '<body><sec><title>Recommendations</title>'
            '<p>Use a structured assessment.</p><p>Reassess after treatment.</p>'
            '</sec></body></article></metadata></record></GetRecord></OAI-PMH>'
        )
        result = PmcOpenAccessRetriever(transport).fetch("PMC12345")
        self.assertIsNotNone(result)
        self.assertEqual(result.pmcid, "PMC12345")
        self.assertEqual(result.title, "Shock guideline")
        self.assertIn("Recommendations", result.full_text)
        self.assertIn("CC BY 4.0", result.license_text)
        self.assertEqual(
            transport.calls[0][1]["identifier"],
            "oai:pubmedcentral.nih.gov:12345",
        )

    def test_pmc_oai_error_is_not_treated_as_full_text(self):
        transport = FakeTextTransport(
            '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            '<error code="idDoesNotExist">missing</error></OAI-PMH>'
        )
        self.assertIsNone(PmcOpenAccessRetriever(transport).fetch("12345"))

    def test_pmc_rejects_invalid_identifier_before_network_call(self):
        transport = FakeTextTransport("")
        with self.assertRaisesRegex(ValueError, "PMCID"):
            PmcOpenAccessRetriever(transport).fetch("not-an-id")
        self.assertEqual(transport.calls, [])

    def test_wikipedia_uses_language_specific_action_api(self):
        transport = FakeTransport(
            [{"query": {"pages": [{"pageid": 1, "title": "动作电位", "fullurl": "https://zh.wikipedia.org/wiki/x", "extract": "简介"}]}}]
        )
        results = WikipediaRetriever(transport).search("动作电位", language="zh")
        self.assertEqual(results[0].source, "wikipedia_zh")
        self.assertIn("zh.wikipedia.org/w/api.php", transport.calls[0][0])

    def test_pubmed_uses_esearch_then_esummary(self):
        transport = FakeTransport(
            [
                {"esearchresult": {"idlist": ["123"]}},
                {"result": {"123": {"title": "A study", "pubdate": "2025", "authors": [{"name": "Li A"}], "articleids": [{"idtype": "doi", "value": "10/x"}]}}},
            ]
        )
        results = PubMedRetriever(transport).search("membrane potential")
        self.assertEqual(results[0].resource_id, "123")
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(transport.calls[0][0].endswith("esearch.fcgi"))

    def test_pubmed_fetches_xml_abstract_when_requested(self):
        transport = FakeTransport([
            {"esearchresult": {"idlist": ["123"]}},
            {"result": {"123": {"title": "A study", "pubdate": "2025"}}},
        ])
        text_transport = FakeTextTransport(
            "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            "<PMID>123</PMID><Article><Abstract>"
            '<AbstractText Label="RESULTS">Membrane voltage changed.</AbstractText>'
            "</Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
        )
        results = PubMedRetriever(transport, text_transport).search(
            "membrane potential", include_abstracts=True, year_from=2020, year_to=2025
        )
        self.assertEqual(results[0].snippet, "RESULTS: Membrane voltage changed.")
        self.assertTrue(text_transport.calls[0][0].endswith("efetch.fcgi"))
        self.assertIn("2020[pdat]", transport.calls[0][1]["term"])

    def test_openalex_reconstructs_abstract(self):
        transport = FakeTransport(
            [{"results": [{"id": "https://openalex.org/W1", "display_name": "Paper", "publication_year": 2024, "doi": "https://doi.org/10/x", "authorships": [], "primary_location": {}, "abstract_inverted_index": {"Cell": [0], "signal": [1]}}]}]
        )
        result = OpenAlexRetriever(transport).search("cell signal")[0]
        self.assertEqual(result.snippet, "Cell signal")
        self.assertEqual(result.year, 2024)


class LayeredRetrievalAgentTests(unittest.TestCase):
    def test_term_prefers_chinese_wikipedia_without_paper_search(self):
        local = FakeLocalRetriever()
        wiki = FakeExternal()
        pubmed = FakeExternal()
        openalex = FakeExternal()
        agent = LayeredRetrievalAgent(
            local_retriever=local,
            wikipedia=wiki,
            pubmed=pubmed,
            openalex=openalex,
        )
        packet = agent.retrieve("动作电位是什么", mode="quick")
        self.assertEqual(packet.plan.intent, "terminology")
        self.assertEqual(wiki.calls[0][1]["language"], "zh")
        self.assertEqual(pubmed.calls, [])
        self.assertEqual(openalex.calls, [])

    def test_research_mode_uses_both_paper_sources_and_survives_one_failure(self):
        agent = LayeredRetrievalAgent(
            local_retriever=FakeLocalRetriever(),
            wikipedia=FakeExternal(),
            pubmed=FakeExternal(error=TimeoutError()),
            openalex=FakeExternal(),
        )
        packet = agent.retrieve("静息电位研究进展", mode="research")
        self.assertEqual(packet.plan.intent, "research")
        self.assertTrue(any("PubMed" in item for item in packet.warnings))
        self.assertEqual(len(agent.openalex.calls), 1)

    def test_course_question_stays_local_only(self):
        plan = RetrievalPlanner().plan("解释动作电位的形成机制和各阶段", mode="guided")
        self.assertEqual(plan.intent, "course")
        self.assertFalse(plan.wikipedia_languages)
        self.assertFalse(plan.use_pubmed)


if __name__ == "__main__":
    unittest.main()
