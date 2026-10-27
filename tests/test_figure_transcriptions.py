import json
import tempfile
import unittest
from pathlib import Path

from retrieval.page_text_index import (
    PageTextIndex,
    PageTextRecord,
    load_page_text_index,
    save_page_text_index,
)
from retrieval.structure_retriever import StructureAwareRetriever


class FigureTranscriptionTests(unittest.TestCase):
    def test_purine_carbon_source_figure_survives_index_reload_and_retrieval(self):
        base = Path(__file__).resolve().parents[1]
        index_path = (
            base / "data" / "page_indexes"
            / "doc_2eb4432b6d8876c539d55875679d0f40998ee3bc92393ac9bede20a273874fe6-pages.json"
        )
        if not index_path.is_file():
            self.skipTest("本地生化教材索引尚未安装")
        page = load_page_text_index(index_path).pages[247]
        self.assertEqual(page.pdf_page, 248)
        self.assertIn("C6 来自 CO₂", page.text)
        self.assertIn("N1 来自天冬氨酸", page.text)
        self.assertIn("图示对照转录", page.text)

        retriever = StructureAwareRetriever.from_directories(
            base / "data" / "document_maps", base / "data" / "page_indexes"
        )
        result = retriever.search(
            "嘌呤环中的碳原子来自于（多选） "
            "A. CO2 B. 甘氨酸 C. 天冬氨酸 D. 谷氨酰胺 E. 一碳单位",
            top_k=6,
            subject_ids=["biochemistry"],
        )
        self.assertIn(248, [hit.pdf_page for hit in result.hits])

    def test_matching_pdf_figure_is_added_with_separate_provenance(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            index_dir = root / "page_indexes"
            index_dir.mkdir()
            index_path = index_dir / "doc_test-pages.json"
            save_page_text_index(
                PageTextIndex(
                    "1.0", "doc_test", "book.pdf", 1,
                    [PageTextRecord(1, None, "图 1 元素来源", 8, "ok")],
                ),
                index_path,
            )
            (root / "figure_transcriptions.json").write_text(
                json.dumps([{
                    "document_id": "doc_test",
                    "pdf_page": 1,
                    "caption": "图 1 元素来源",
                    "transcription": "碳来自甲；氮来自乙。",
                }], ensure_ascii=False),
                encoding="utf-8",
            )

            page = load_page_text_index(index_path).pages[0]

            self.assertIn("碳来自甲；氮来自乙", page.text)
            self.assertEqual(page.content_origin, "native_text_plus_figure_transcription")
            self.assertEqual(page.visual_source_ref, "book.pdf#page=1")
            self.assertFalse(page.visual_verified_by_user)
            self.assertTrue(page.visual_requires_recheck)
            # Loading an index does not rewrite the original PDF extraction.
            self.assertNotIn("碳来自甲", index_path.read_text(encoding="utf-8"))

    def test_wrong_edition_or_missing_caption_is_not_silently_used(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            index_dir = root / "page_indexes"
            index_dir.mkdir()
            index_path = index_dir / "doc_test-pages.json"
            save_page_text_index(
                PageTextIndex(
                    "1.0", "doc_test", "book.pdf", 1,
                    [PageTextRecord(1, None, "另一张图", 4, "ok")],
                ),
                index_path,
            )
            overlay = root / "figure_transcriptions.json"
            overlay.write_text(json.dumps([{
                "document_id": "another_document", "pdf_page": 1,
                "caption": "图 1 元素来源", "transcription": "碳来自甲。",
            }], ensure_ascii=False), encoding="utf-8")
            self.assertNotIn("碳来自甲", load_page_text_index(index_path).pages[0].text)
            overlay.write_text(json.dumps([{
                "document_id": "doc_test", "pdf_page": 1,
                "caption": "图 1 元素来源", "transcription": "碳来自甲。",
            }], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "无法匹配原页图题"):
                load_page_text_index(index_path)


if __name__ == "__main__":
    unittest.main()
