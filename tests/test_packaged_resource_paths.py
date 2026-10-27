"""Frozen builds can place Python modules directly beside their assets."""

import unittest
from pathlib import Path

from ui.tutor_window import _logo_path_for_module


class PackagedResourcePathTests(unittest.TestCase):
    def test_logo_resolution_does_not_require_deep_parent_directory(self):
        module = Path("C:/tutor_window.py")
        self.assertEqual(_logo_path_for_module(module), module.with_name("logo.png"))


if __name__ == "__main__":
    unittest.main()
