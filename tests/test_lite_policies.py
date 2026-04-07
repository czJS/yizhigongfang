from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.lite_policies import apply_lite_fixed_policies


class LitePoliciesTest(unittest.TestCase):
    def test_apply_lite_fixed_policies_removes_retired_keys(self) -> None:
        params = {
            "sentence_unit_enable": True,
            "display_srt_enable": True,
            "tts_plan_enable": True,
            "mt_topic": "movie",
            "keep_me": 123,
        }

        actual = apply_lite_fixed_policies(params)

        self.assertNotIn("sentence_unit_enable", actual)
        self.assertNotIn("display_srt_enable", actual)
        self.assertNotIn("tts_plan_enable", actual)
        self.assertNotIn("mt_topic", actual)
        self.assertEqual(actual["keep_me"], 123)

    def test_apply_lite_fixed_policies_forces_mainline_defaults(self) -> None:
        actual = apply_lite_fixed_policies({"mt_batch_enable": False})

        self.assertTrue(actual["asr_normalize_enable"])
        self.assertTrue(actual["asr_glossary_fix_enable"])
        self.assertTrue(actual["asr_low_cost_clean_enable"])
        self.assertTrue(actual["asr_badline_detect_enable"])
        self.assertTrue(actual["mt_batch_enable"])


if __name__ == "__main__":
    unittest.main()
