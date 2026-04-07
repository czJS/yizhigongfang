from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.text.translate_post import conservative_shorten_en


class TranslatePostTest(unittest.TestCase):
    def test_conservative_shorten_en_trims_edge_over_cps_line(self) -> None:
        text = "Well, I am there, actually, and then waiting for you."
        actual = conservative_shorten_en(text, duration_s=2.6, max_cps=20.0)
        self.assertNotEqual(actual, text)
        self.assertIn("I'm", actual)
        self.assertLess(len(actual), len(text))

    def test_conservative_shorten_en_keeps_safe_line_unchanged(self) -> None:
        text = "I am here to help."
        actual = conservative_shorten_en(text, duration_s=2.0, max_cps=20.0)
        self.assertEqual(actual, "I am here to help.")

    def test_conservative_shorten_en_skips_heavily_over_limit_line(self) -> None:
        text = "I am actually there and then waiting for you because the situation is becoming extremely complicated right now."
        actual = conservative_shorten_en(text, duration_s=2.5, max_cps=20.0)
        self.assertEqual(actual, text)


if __name__ == "__main__":
    unittest.main()
