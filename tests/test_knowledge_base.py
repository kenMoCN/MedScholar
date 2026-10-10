import tempfile
import unittest
import json
import zipfile
from pathlib import Path

from pypdf import PdfWriter

from knowledge_base import KnowledgeBaseService


def make_pdf(path: Path, pages: int = 2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as stream:
        writer.write(stream)


def make_pptx(path: Path):
    slide = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody>
        <a:p><a:r><a:t>糖酵解</a:t></a:r></a:p>
        <a:p><a:r><a:t>磷酸果糖激酶-1是关键限速酶</a:t></a:r></a:p>
      </p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)


class KnowledgeBaseServiceTests(unittest.TestCase):
    def test_import_duplicate_and_recoverable_remove(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            source = Path(temp) / "course.pdf"
            make_pdf(source)
            service = KnowledgeBaseService(base)

            imported = service.import_pdfs([source])
            duplicate = service.import_pdfs([source])

            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].status, "needs_ocr")
            self.assertEqual(duplicate[0].document_id, imported[0].document_id)
            self.assertEqual(len(service.list_documents()), 1)

            trash = service.remove_document(imported[0].document_id)
            self.assertTrue(trash.is_dir())
            self.assertEqual(service.list_documents(), ())
            self.assertTrue(any(trash.iterdir()))

    def test_non_pdf_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "notes.txt"
            source.write_text("text", encoding="utf-8")
            service = KnowledgeBaseService(Path(temp) / "app")
            with self.assertRaisesRegex(ValueError, "仅支持 PDF"):
                service.import_pdfs([source])

    def test_user_can_correct_document_subject_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            map_dir = base / "data" / "document_maps"
            map_dir.mkdir(parents=True)
            map_path = map_dir / "map.json"
            map_path.write_text(json.dumps({
                "document_id": "doc_subject",
                "original_name": "课程资料.pdf",
                "document_type": "textbook",
                "page_count": 10,
                "text_layer": "full",
                "warnings": [],
            }, ensure_ascii=False), encoding="utf-8")
            service = KnowledgeBaseService(base)

            selected = service.set_document_subjects(
                "doc_subject", ["pharmacology"]
            )

            self.assertEqual(selected, ("pharmacology",))
            self.assertEqual(
                service.list_documents()[0].subject_names,
                ("药理学",),
            )

    def test_pptx_course_deck_is_indexed_with_slide_locator(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            source = Path(temp) / "07 糖代谢.pptx"
            make_pptx(source)
            service = KnowledgeBaseService(base)

            imported = service.import_documents([source])

            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0].document_type, "course_deck")
            self.assertEqual(imported[0].subject_names, ("生物化学与分子生物学",))
            index_path = next((base / "data" / "page_indexes").glob("*.json"))
            data = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(data["pages"][0]["title"], "糖酵解")
            self.assertIn("磷酸果糖激酶", data["pages"][0]["text"])


if __name__ == "__main__":
    unittest.main()
