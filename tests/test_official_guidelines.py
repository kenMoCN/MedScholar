import unittest

from official_guidelines import (
    CdcHypoglycemiaGuidanceRetriever,
    OfficialGuidelineRetriever,
    PdfDocumentTextRetriever,
    WhoOfficialGuidelineRetriever,
    official_guideline_source_options,
)


class FakeJsonTransport:
    def __init__(self, response=None, error=None):
        self.response = response or {"value": []}
        self.error = error
        self.calls = []

    def get_json(self, url, params, headers, timeout_seconds):
        self.calls.append((url, params, headers, timeout_seconds))
        if self.error:
            raise self.error
        return self.response


class FakeDocumentRetriever:
    def __init__(self, text="Official recommendation text.", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def fetch_text(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.text


class StubPdfDocumentRetriever(PdfDocumentTextRetriever):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.downloads = []

    def _download_and_extract(self, url):
        self.downloads.append(url)
        return f"text:{url}"


class WhoOfficialGuidelineTests(unittest.TestCase):
    def test_pdf_text_cache_is_bounded_and_can_be_cleared(self):
        retriever = StubPdfDocumentRetriever(max_cache_items=2)
        retriever.fetch_text("one")
        retriever.fetch_text("two")
        retriever.fetch_text("one")
        retriever.fetch_text("three")
        self.assertEqual(retriever.downloads, ["one", "two", "three"])
        retriever.fetch_text("two")
        self.assertEqual(retriever.downloads, ["one", "two", "three", "two"])
        retriever.clear_cache()
        retriever.fetch_text("one")
        self.assertEqual(retriever.downloads[-1], "one")

    def test_search_uses_topic_and_guideline_filters_and_reads_open_pdf(self):
        transport = FakeJsonTransport({"value": [{
            "Title": "WHO guideline for shock care",
            "Summary": "Evidence-based recommendations.",
            "PublicationDate": "2025-04-03T00:00:00Z",
            "ItemDefaultUrl": "/shock-guideline",
            "DownloadUrl": "https://iris.who.int/example.pdf",
            "WHOReferenceNumber": "WHO/TEST/1",
        }]})
        documents = FakeDocumentRetriever()
        packet = WhoOfficialGuidelineRetriever(transport, documents).search(
            "shock resuscitation management"
        )
        self.assertEqual(len(packet.records), 1)
        record = packet.records[0]
        self.assertEqual(record.resource_id, "WHO/TEST/1")
        self.assertEqual(record.year, 2025)
        self.assertEqual(record.full_text, "Official recommendation text.")
        self.assertEqual(documents.calls, ["https://iris.who.int/example.pdf"])
        query_filter = transport.calls[0][1]["$filter"]
        self.assertIn("shock", query_filter)
        self.assertIn("resuscitation", query_filter)
        self.assertIn("guideline", query_filter)
        self.assertIn(" or ", query_filter)

    def test_non_guideline_publication_is_not_upgraded(self):
        transport = FakeJsonTransport({"value": [{
            "Title": "Shock epidemiology report",
            "Summary": "A technical report.",
            "DownloadUrl": "https://iris.who.int/report.pdf",
        }]})
        documents = FakeDocumentRetriever()
        packet = WhoOfficialGuidelineRetriever(transport, documents).search("shock")
        self.assertEqual(packet.records, [])
        self.assertEqual(documents.calls, [])

    def test_pdf_failure_keeps_metadata_candidate_with_warning(self):
        transport = FakeJsonTransport({"value": [{
            "Title": "Guidance for shock care",
            "Summary": "Recommendations.",
            "ItemDefaultUrl": "/shock",
            "DownloadUrl": "https://iris.who.int/shock.pdf",
        }]})
        packet = WhoOfficialGuidelineRetriever(
            transport, FakeDocumentRetriever(error=TimeoutError())
        ).search("shock")
        self.assertEqual(len(packet.records), 1)
        self.assertEqual(packet.records[0].full_text, "")
        self.assertTrue(any("全文获取失败" in item for item in packet.warnings))

    def test_composite_retriever_is_failure_isolated(self):
        who = WhoOfficialGuidelineRetriever(
            FakeJsonTransport(error=TimeoutError()), FakeDocumentRetriever()
        )
        packet = OfficialGuidelineRetriever(
            who, CdcHypoglycemiaGuidanceRetriever(FakeDocumentRetriever())
        ).search("shock")
        self.assertEqual(packet.records, [])
        self.assertTrue(any("WHO 官方指南检索失败" in item for item in packet.warnings))

    def test_source_registry_exposes_legal_access_state(self):
        by_id = {item.source_id: item for item in official_guideline_source_options()}
        self.assertEqual(by_id["who"].automation_status, "automatic")
        self.assertEqual(by_id["cdc"].automation_status, "automatic")
        self.assertEqual(by_id["ada"].automation_status, "official_search_only")
        self.assertEqual(by_id["nice"].automation_status, "license_required")
        self.assertEqual(by_id["nhc_cn"].automation_status, "official_search_only")

    def test_cdc_hypoglycemia_query_reads_public_guidance(self):
        documents = FakeDocumentRetriever("6.15 Glucose is the preferred treatment.")
        packet = CdcHypoglycemiaGuidanceRetriever(documents).search(
            "type 2 diabetes hypoglycemia management"
        )
        self.assertEqual(len(packet.records), 1)
        record = packet.records[0]
        self.assertEqual(record.source, "cdc")
        self.assertEqual(record.publisher, "Centers for Disease Control and Prevention")
        self.assertIn("Glucose is the preferred", record.full_text)
        self.assertEqual(documents.calls, [CdcHypoglycemiaGuidanceRetriever.ARTICLE_URL])
        self.assertFalse(record.is_guideline_or_consensus)
        self.assertTrue(record.clinical_answer_eligible)

    def test_cdc_irrelevant_query_does_not_download_document(self):
        documents = FakeDocumentRetriever()
        packet = CdcHypoglycemiaGuidanceRetriever(documents).search(
            "septic shock fluid resuscitation"
        )
        self.assertEqual(packet.records, [])
        self.assertEqual(documents.calls, [])

    def test_cdc_keeps_article_body_and_removes_navigation_footer(self):
        page = (
            "Treatment of Low Blood Sugar (Hypoglycemia) | Diabetes | CDC\n"
            "navigation\nTreatment of Low Blood Sugar (Hypoglycemia)\n"
            "The 15-15 rule\nHave 15 grams of carbs.\n"
            "Content Source:\nfooter"
        )
        packet = CdcHypoglycemiaGuidanceRetriever(
            FakeDocumentRetriever(page)
        ).search("hypoglycemia")
        text = packet.records[0].full_text
        self.assertTrue(text.startswith("Treatment of Low Blood Sugar"))
        self.assertIn("15 grams", text)
        self.assertNotIn("navigation", text)
        self.assertNotIn("footer", text)

    def test_composite_keeps_cdc_when_who_fails(self):
        who = WhoOfficialGuidelineRetriever(
            FakeJsonTransport(error=TimeoutError()), FakeDocumentRetriever()
        )
        cdc = CdcHypoglycemiaGuidanceRetriever(FakeDocumentRetriever("hypoglycemia glucose"))
        packet = OfficialGuidelineRetriever(who, cdc).search("hypoglycemia")
        self.assertEqual([item.source for item in packet.records], ["cdc"])
        self.assertTrue(any("WHO 官方指南检索失败" in item for item in packet.warnings))


if __name__ == "__main__":
    unittest.main()
