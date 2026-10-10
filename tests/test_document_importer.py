import tempfile
import unittest
from pathlib import Path

from document_importer import (
    DocumentImportManifest,
    DocumentImporter,
    ImportStatus,
    sha256_file,
)


class DocumentImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.manifest_path = self.root / "config" / "document_imports.json"
        self.storage = self.root / "documents"
        self.manifest = DocumentImportManifest(self.manifest_path)
        self.importer = DocumentImporter(self.storage, self.manifest)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_same_name_different_content_do_not_overwrite_each_other(self):
        first_dir = self.sources / "one"
        second_dir = self.sources / "two"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "生理学.pdf"
        second = second_dir / "生理学.pdf"
        first.write_bytes(b"first edition")
        second.write_bytes(b"second edition")

        items = self.importer.enqueue([first, second])
        self.importer.process_pending()

        self.assertEqual(items[0].status, ImportStatus.COPIED.value)
        self.assertEqual(items[1].status, ImportStatus.COPIED.value)
        self.assertNotEqual(items[0].document_id, items[1].document_id)
        self.assertNotEqual(items[0].stored_path, items[1].stored_path)
        self.assertEqual(Path(items[0].stored_path).read_bytes(), b"first edition")
        self.assertEqual(Path(items[1].stored_path).read_bytes(), b"second edition")

    def test_same_content_is_deduplicated_even_with_different_names(self):
        first = self.sources / "教材.pdf"
        second = self.sources / "教材副本.pdf"
        first.write_bytes(b"identical content")
        second.write_bytes(b"identical content")

        items = self.importer.enqueue([first, second])
        self.importer.process_pending()

        self.assertEqual(items[0].status, ImportStatus.COPIED.value)
        self.assertEqual(items[1].status, ImportStatus.DUPLICATE.value)
        self.assertEqual(items[0].document_id, items[1].document_id)
        self.assertEqual(items[0].stored_path, items[1].stored_path)
        self.assertEqual(items[1].duplicate_of, items[0].import_id)
        self.assertEqual(len(list(self.storage.rglob("*.pdf"))), 1)

    def test_missing_source_is_recorded_as_failed(self):
        missing = self.sources / "missing.pdf"
        item = self.importer.enqueue([missing])[0]

        self.importer.process_pending()

        self.assertEqual(item.status, ImportStatus.FAILED.value)
        self.assertIn("源文件不存在", item.error)

    def test_manifest_round_trip_preserves_records(self):
        source = self.sources / "chapter.pdf"
        source.write_bytes(b"chapter")
        item = self.importer.enqueue([source])[0]
        self.importer.process_pending()

        loaded = DocumentImportManifest.load(self.manifest_path)
        restored = loaded.get(item.import_id)
        self.assertEqual(restored.sha256, sha256_file(source))
        self.assertEqual(restored.status, ImportStatus.COPIED.value)
        self.assertTrue(Path(restored.stored_path).is_file())

    def test_source_file_is_not_modified(self):
        source = self.sources / "source.pdf"
        source.write_bytes(b"original bytes")
        before_hash = sha256_file(source)

        self.importer.enqueue([source])
        self.importer.process_pending()

        self.assertEqual(sha256_file(source), before_hash)


if __name__ == "__main__":
    unittest.main()
