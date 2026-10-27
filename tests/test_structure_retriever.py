import json
import tempfile
import unittest
from pathlib import Path

from retrieval.page_text_index import PageTextIndex, PageTextRecord, save_page_text_index
from retrieval.structure_retriever import (
    StructureAwareRetriever,
    _normalize_query,
    _question_options,
    _question_stem,
)


class StructureRetrieverTests(unittest.TestCase):
    def test_multiple_choice_options_are_removed_from_retrieval_stem(self):
        question = "心率过快时心输出量为什么下降？A.心房收缩期 B.心室充盈期"
        self.assertEqual(_question_stem(question), "心率过快时心输出量为什么下降")

    def test_greek_letters_are_preserved_and_fatty_acid_synonym_is_unified(self):
        self.assertEqual(_normalize_query("脂酸β-氧化"), _normalize_query("脂肪酸β氧化"))
        self.assertNotEqual(_normalize_query("β-氧化"), _normalize_query("ω-氧化"))

    def test_multiple_choice_options_are_extracted_separately(self):
        self.assertEqual(
            _question_options("参与脂肪酸β-氧化的有： A. FAD B. NAD+ C. NADP+ D. CoA-SH"),
            [("A", "FAD"), ("B", "NAD+"), ("C", "NADP+"), ("D", "CoA-SH")],
        )

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
            "page_count": 12,
            "toc_entries": [{"title": "第三节 生物电现象和兴奋性"}],
            "nodes": [
                {
                    "level": 2,
                    "title": "第二章 细胞的基本功能",
                    "pdf_start": 8,
                    "pdf_end": 12,
                    "source": "outline",
                },
                {
                    "level": 3,
                    "title": "第三节 生物电现象和兴奋性",
                    "pdf_start": 10,
                    "pdf_end": 12,
                    "source": "outline",
                },
            ],
            "page_map_segments": [
                {"pdf_start": 8, "pdf_end": 12, "offset": 7}
            ],
        }
        (self.map_dir / "map.json").write_text(
            json.dumps(document_map, ensure_ascii=False), encoding="utf-8"
        )
        pages = [
            PageTextRecord(page, None, "无关内容", 4, "ok")
            for page in range(1, 13)
        ]
        pages[9] = PageTextRecord(
            10,
            "静息电位",
            "静息电位的形成与钾离子平衡电位和膜通透性有关。",
            24,
            "ok",
        )
        index = PageTextIndex("1.0", "doc_test", "test.pdf", 12, pages)
        save_page_text_index(index, self.index_dir / "pages.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_query_returns_target_page_and_dual_page_locator(self):
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search("静息电位如何形成", top_k=1)

        self.assertEqual(result.hits[0].pdf_page, 10)
        self.assertEqual(result.hits[0].printed_page, 3)
        self.assertEqual(
            result.hits[0].locator, "教材印刷页 3（PDF第 10 页）"
        )
        self.assertEqual(result.hits[0].context_pdf_pages, [9, 10, 11])

    def test_structure_route_is_used_for_matching_section(self):
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search("生物电现象", top_k=2)

        self.assertEqual(result.route_mode, "structure_first")
        self.assertEqual(result.routed_documents, ["生理学教材.pdf"])

    def test_short_query_is_rejected_without_searching(self):
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )
        result = retriever.search("电")
        self.assertEqual(result.route_mode, "invalid_query")
        self.assertEqual(result.hits, [])

    def test_subject_filter_prevents_cross_course_evidence(self):
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        physiology = retriever.search(
            "静息电位如何形成", subject_ids=["physiology"]
        )
        biochemistry = retriever.search(
            "静息电位如何形成", subject_ids=["biochemistry"]
        )

        self.assertTrue(physiology.hits)
        self.assertEqual(biochemistry.route_mode, "subject_no_documents")
        self.assertEqual(biochemistry.hits, [])

    def test_global_fallback_stays_inside_requested_subject(self):
        biochemistry_map = {
            "schema_version": "1.0",
            "document_id": "doc_biochemistry",
            "source_path": "biochemistry.pdf",
            "original_name": "课程资料.pdf",
            "document_type": "textbook",
            "page_count": 1,
            "subject_ids": ["biochemistry"],
            "toc_entries": [],
            "nodes": [],
            "page_map_segments": [],
        }
        (self.map_dir / "biochemistry.json").write_text(
            json.dumps(biochemistry_map, ensure_ascii=False), encoding="utf-8"
        )
        index = PageTextIndex(
            "1.0",
            "doc_biochemistry",
            "biochemistry.pdf",
            1,
            [PageTextRecord(1, None, "共同检索词属于生化内容。", 12, "ok")],
        )
        save_page_text_index(index, self.index_dir / "biochemistry-pages.json")
        physiology_index = self.index_dir / "pages.json"
        data = json.loads(physiology_index.read_text(encoding="utf-8"))
        data["pages"][0]["text"] = "共同检索词属于生理内容。"
        physiology_index.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search(
            "共同检索词", subject_ids=["biochemistry"], top_k=5
        )

        self.assertEqual(result.route_mode, "global_fallback")
        self.assertEqual(
            {hit.document_id for hit in result.hits}, {"doc_biochemistry"}
        )

    def test_structural_route_boost_does_not_hide_exact_page_elsewhere(self):
        # The heading routes to pages 10-12, while the option-specific evidence
        # is deliberately placed outside that chapter. It must remain searchable.
        index_path = self.index_dir / "pages.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["pages"][2]["text"] = "排尿反射属于正反馈调节。"
        data["pages"][2]["char_count"] = len(data["pages"][2]["text"])
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search(
            "生物电现象中存在正反馈的神经反射是：A.排尿反射；B.牵张反射",
            top_k=1,
        )

        self.assertEqual(result.hits[0].pdf_page, 3)
        self.assertIn("排尿反射", result.hits[0].matched_terms)

    def test_long_clinical_stem_keeps_exact_concept_recall(self):
        index_path = self.index_dir / "pages.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["pages"][4]["text"] = (
            "胃排空过快时，高渗食物进入小肠可因渗透压变化引起液体转移，"
            "并伴随胃肠激素释放。"
        )
        data["pages"][4]["char_count"] = len(data["pages"][4]["text"])
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search(
            "某患者胃大部切除术后出现心悸出汗腹痛腹泻，之后又出现乏力。"
            "请从胃排空、渗透压和胃肠激素角度解释早期与晚期倾倒综合征。",
            top_k=1,
        )

        self.assertEqual(result.hits[0].pdf_page, 5)

    def test_full_width_latin_text_matches_ascii_query(self):
        index_path = self.index_dir / "pages.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["pages"][5]["text"] = "肺功能检查可测量ＦＥＶ１和ＦＶＣ。"
        data["pages"][5]["char_count"] = len(data["pages"][5]["text"])
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search("FEV1/FVC有什么用途", top_k=1)

        self.assertEqual(result.hits[0].pdf_page, 6)

    def test_stem_outweighs_distractor_only_page(self):
        index_path = self.index_dir / "pages.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["pages"][5]["text"] = "维生素辅酶包括FAD、NAD+、NADP+和CoA-SH。"
        data["pages"][6]["text"] = (
            "脂肪酸β-氧化包括脱氢、加水、再脱氢和硫解，"
            "分别使用FAD、NAD+和CoA-SH。"
        )
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retriever = StructureAwareRetriever.from_directories(
            self.map_dir, self.index_dir
        )

        result = retriever.search(
            "参与脂肪酸β-氧化的有： A. FAD B. NAD+ C. NADP+ D. CoA-SH",
            top_k=1,
        )

        self.assertEqual(result.hits[0].pdf_page, 7)


if __name__ == "__main__":
    unittest.main()
