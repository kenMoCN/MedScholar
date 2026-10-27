import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from retrieval.pdf_structure_indexer import (
    PageAnchor,
    PdfStructureIndexer,
    _has_toc_heading,
    _json_safe,
    _parse_toc_page,
    build_page_map_segments,
    save_document_map,
)


class PdfStructureIndexerTests(unittest.TestCase):
    def test_same_offset_anchors_form_one_segment(self):
        anchors = [
            PageAnchor("第一章", 1, 8, 7, True),
            PageAnchor("第二章", 5, 12, 7, True),
            PageAnchor("第三章", 35, 42, 7, True),
        ]

        segments = build_page_map_segments(anchors, page_count=298)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].offset, 7)
        self.assertEqual(segments[0].pdf_start, 8)
        self.assertGreaterEqual(segments[0].confidence, 0.99)

    def test_offset_change_creates_a_new_segment(self):
        anchors = [
            PageAnchor("第一章", 1, 8, 7, True),
            PageAnchor("第二章", 5, 12, 7, True),
            PageAnchor("附录", 1, 250, 249, True),
        ]

        segments = build_page_map_segments(anchors, page_count=260)

        self.assertEqual([segment.offset for segment in segments], [7, 249])
        self.assertEqual(segments[0].pdf_end, 249)

    def test_single_offset_outliers_are_ignored_when_dominant_mapping_is_strong(self):
        anchors = [
            PageAnchor("第一章", 1, 8, 7, True),
            PageAnchor("第二章", 5, 12, 7, True),
            PageAnchor("第三章", 35, 42, 7, True),
            PageAnchor("重复标题", 120, 178, 58, True),
        ]

        segments = build_page_map_segments(anchors, page_count=298)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].offset, 7)

    def test_course_deck_toc_lines_are_parsed_individually(self):
        entries = _parse_toc_page(
            "目录/Contents\n第一节 细胞膜的物质转运功能\n第二节 细胞的信号转导",
            4,
        )

        self.assertEqual([entry.title for entry in entries], [
            "第一节 细胞膜的物质转运功能",
            "第二节 细胞的信号转导",
        ])

    def test_toc_detection_does_not_match_body_use_of_contents_word(self):
        self.assertTrue(_has_toc_heading("目录/Contents\n第一节 绪论"))
        self.assertFalse(_has_toc_heading("本节目录内容用于说明课程安排"))

    def test_invalid_pdf_surrogates_are_made_json_safe(self):
        cleaned = _json_safe({"title": "标题\ud800"})
        self.assertEqual(cleaned["title"], "标题?")

    def test_outline_is_extracted_from_a_minimal_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "outline.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.add_blank_page(width=595, height=842)
            writer.add_outline_item("第一章 绪论", 0)
            writer.add_outline_item("第二章 细胞", 1)
            with pdf_path.open("wb") as stream:
                writer.write(stream)

            document_map = PdfStructureIndexer(pdf_path).build("textbook")

            self.assertTrue(document_map.outline_available)
            self.assertEqual(len(document_map.nodes), 2)
            self.assertEqual(document_map.nodes[0].pdf_start, 1)
            self.assertEqual(document_map.nodes[0].pdf_end, 1)
            self.assertEqual(document_map.nodes[1].pdf_end, 2)

            output = Path(temp_dir) / "map.json"
            save_document_map(document_map, output)
            self.assertTrue(output.is_file())

    def test_textbook_filename_overrides_landscape_first_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "生物化学与分子生物学.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=842, height=595)
            with pdf_path.open("wb") as stream:
                writer.write(stream)

            document_map = PdfStructureIndexer(pdf_path).build("auto")

            self.assertEqual(document_map.document_type, "textbook")


if __name__ == "__main__":
    unittest.main()
