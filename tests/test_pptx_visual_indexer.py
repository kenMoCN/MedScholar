import base64
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from knowledge_base import KnowledgeBaseService


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def make_image_only_pptx(path: Path) -> None:
    slide = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree/></p:cSld>
    </p:sld>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        Target="../media/image1.png"/>
    </Relationships>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        archive.writestr("ppt/media/image1.png", PNG_1X1)


class FakeVisionClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.model_identity = "fake:vision"

    def analyze_image(self, image, prompt, *, media_type=None, max_image_bytes=0):
        self.calls.append((image, prompt, media_type))
        return self.response


class PptxVisualIndexerTests(unittest.TestCase):
    def test_approved_high_confidence_visual_text_enters_index(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            source = Path(temp) / "糖代谢图片课件.pptx"
            make_image_only_pptx(source)
            service = KnowledgeBaseService(base)
            document = service.import_documents([source])[0]
            client = FakeVisionClient({
                "plain_text": "磷酸果糖激酶-1是糖酵解的关键限速酶",
                "question_text": None,
                "uncertain_spans": [],
                "overall_confidence": 0.96,
                "requires_recheck": False,
            })

            plan = service.visual_index_plan(document.document_id)
            report = service.index_document_visuals(document.document_id, client)

            self.assertEqual(plan.candidate_count, 1)
            self.assertEqual(report.indexed, 1)
            self.assertEqual(client.calls[0][2], "image/png")
            index_path = next((base / "data" / "page_indexes").glob("*.json"))
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertIn("磷酸果糖激酶", index_data["pages"][0]["text"])
            self.assertEqual(index_data["pages"][0]["extraction_status"], "visual_ocr")
            self.assertEqual(index_data["pages"][0]["content_origin"], "visual_ocr")
            self.assertEqual(index_data["pages"][0]["visual_model"], "fake:vision")
            self.assertTrue(index_data["pages"][0]["visual_image_sha256"])
            self.assertFalse(index_data["pages"][0]["visual_verified_by_user"])

    def test_low_confidence_visual_text_is_not_searchable(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            source = Path(temp) / "图片课件.pptx"
            make_image_only_pptx(source)
            service = KnowledgeBaseService(base)
            document = service.import_documents([source])[0]
            client = FakeVisionClient({
                "plain_text": "模型猜测的医学结论",
                "uncertain_spans": ["结论"],
                "overall_confidence": 0.55,
                "requires_recheck": True,
            })

            report = service.index_document_visuals(document.document_id, client)

            self.assertEqual(report.review_required, 1)
            index_path = next((base / "data" / "page_indexes").glob("*.json"))
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertNotIn("模型猜测", index_data["pages"][0]["text"])
            self.assertEqual(
                index_data["pages"][0]["extraction_status"],
                "visual_review_required",
            )
            review = service.list_visual_reviews(document.document_id)[0]
            self.assertEqual(review.proposed_text, "模型猜测的医学结论")
            service.confirm_visual_review(
                document.document_id, 1, "人工核对后的医学结论"
            )
            confirmed = json.loads(index_path.read_text(encoding="utf-8"))["pages"][0]
            self.assertTrue(confirmed["visual_verified_by_user"])
            self.assertEqual(confirmed["content_origin"], "visual_ocr_verified")

    def test_cancel_keeps_remaining_slide_available_for_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "app"
            source = Path(temp) / "图片课件.pptx"
            make_image_only_pptx(source)
            service = KnowledgeBaseService(base)
            document = service.import_documents([source])[0]
            client = FakeVisionClient({"plain_text": "不会调用"})
            report = service.index_document_visuals(
                document.document_id, client, cancel_check=lambda: True
            )
            self.assertTrue(report.cancelled)
            self.assertEqual(client.calls, [])
            self.assertEqual(service.visual_index_plan(document.document_id).candidate_count, 1)


if __name__ == "__main__":
    unittest.main()
