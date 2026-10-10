import unittest

from page_mapping import trusted_printed_page


class PageMappingTests(unittest.TestCase):
    def test_clean_mapping_returns_printed_page(self):
        document = {
            "page_count": 577,
            "page_map_segments": [
                {"pdf_start": 30, "pdf_end": 577, "offset": 29, "confidence": 1.0}
            ],
        }
        self.assertEqual(trusted_printed_page(document, 491), 462)

    def test_fragmented_or_invalid_mapping_is_hidden(self):
        fragmented = {
            "page_count": 100,
            "page_map_segments": [
                {"pdf_start": i, "pdf_end": i, "offset": 0, "confidence": 1.0}
                for i in range(1, 15)
            ],
        }
        invalid = {
            "page_count": 20,
            "page_map_segments": [
                {"pdf_start": 10, "pdf_end": 8, "offset": 2, "confidence": 1.0}
            ],
        }
        self.assertIsNone(trusted_printed_page(fragmented, 10))
        self.assertIsNone(trusted_printed_page(invalid, 10))


if __name__ == "__main__":
    unittest.main()
