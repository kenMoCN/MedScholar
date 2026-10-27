import json
import tempfile
import unittest
from pathlib import Path

from evidence.evidence_bundle import EvidenceBundleBuilder
from evidence.evidence_verify import ClaimDraft, EvidenceVerifier
from retrieval.page_text_index import PageTextIndex, PageTextRecord, save_page_text_index
from retrieval.structure_retriever import StructureAwareRetriever


class EvidenceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.map_dir = self.root / "maps"
        self.index_dir = self.root / "indexes"
        self.map_dir.mkdir()
        self.index_dir.mkdir()

        document_map = {
            "schema_version": "1.0",
            "document_id": "doc_test",
            "source_path": "test.pdf",
            "original_name": "生理学教材.pdf",
            "document_type": "textbook",
            "page_count": 10,
            "toc_entries": [{"title": "静息电位"}],
            "nodes": [
                {
                    "level": 3,
                    "title": "静息电位",
                    "pdf_start": 10,
                    "pdf_end": 10,
                    "source": "outline",
                }
            ],
            "page_map_segments": [
                {"pdf_start": 8, "pdf_end": 10, "offset": 7}
            ],
            "toc_pdf_pages": [],
        }
        (self.map_dir / "map.json").write_text(
            json.dumps(document_map, ensure_ascii=False), encoding="utf-8"
        )
        pages = [
            PageTextRecord(page, None, "无关内容", 4, "ok")
            for page in range(1, 11)
        ]
        pages[9] = PageTextRecord(
            10,
            "静息电位",
            "静息电位主要与钾离子平衡电位和静息状态下的膜通透性有关。",
            30,
            "ok",
        )
        save_page_text_index(
            PageTextIndex("1.0", "doc_test", "test.pdf", 10, pages),
            self.index_dir / "pages.json",
        )

        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )
        result = retriever.search("静息电位形成", top_k=1, context_radius=0)
        builder = EvidenceBundleBuilder.from_directories(
            self.map_dir, self.index_dir
        )
        self.bundle = builder.build(result)
        self.verifier = EvidenceVerifier.from_directories(
            self.map_dir, self.index_dir
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_exact_source_span_and_locator_pass(self):
        report = self.verifier.verify(self.bundle)
        self.assertEqual(report.status, "pass")
        item = self.bundle.evidence[0]
        self.assertEqual(item.printed_page, 3)
        self.assertEqual(item.locator, "教材印刷页 3（PDF第 10 页）")

    def test_tampered_evidence_text_fails(self):
        self.bundle.evidence[0].evidence_text += "篡改"
        report = self.verifier.verify(self.bundle)
        self.assertEqual(report.status, "fail")
        self.assertIn("evidence_text_mismatch", [check.code for check in report.checks])

    def test_tampered_page_hash_fails(self):
        self.bundle.evidence[0].page_text_sha256 = "0" * 64
        report = self.verifier.verify(self.bundle)
        self.assertIn("page_hash_mismatch", [check.code for check in report.checks])

    def test_uncited_external_claim_fails(self):
        claim = ClaimDraft("claim_1", "静息电位与膜通透性有关", [])
        report = self.verifier.verify(self.bundle, [claim])
        self.assertIn("uncited_claim", [check.code for check in report.checks])

    def test_unknown_evidence_id_fails(self):
        claim = ClaimDraft("claim_1", "静息电位与膜通透性有关", ["missing"])
        report = self.verifier.verify(self.bundle, [claim])
        self.assertIn("unknown_evidence_id", [check.code for check in report.checks])

    def test_low_overlap_claim_requires_semantic_review(self):
        evidence_id = self.bundle.evidence[0].evidence_id
        claim = ClaimDraft("claim_1", "肾小球滤过率显著升高", [evidence_id])
        report = self.verifier.verify(self.bundle, [claim])
        self.assertEqual(report.status, "warning")
        self.assertIn("low_lexical_overlap", [check.code for check in report.checks])
        self.assertTrue(report.semantic_verification_required)

    def test_neighboring_textbook_pages_become_separate_verifiable_evidence(self):
        index_path = self.index_dir / "pages.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["pages"][8]["text"] = "前一页说明FAD和NAD+参与连续两次脱氢。"
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )
        result = retriever.search("静息电位形成", top_k=1, context_radius=1)
        bundle = EvidenceBundleBuilder.from_directories(
            self.map_dir, self.index_dir
        ).build(result)

        self.assertEqual(
            {item.pdf_page for item in bundle.evidence},
            {9, 10},
        )
        self.assertEqual(len({item.evidence_id for item in bundle.evidence}), 2)
        self.assertEqual(EvidenceVerifier.from_directories(
            self.map_dir, self.index_dir
        ).verify(bundle).status, "pass")


if __name__ == "__main__":
    unittest.main()
