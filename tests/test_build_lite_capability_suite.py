from __future__ import annotations

import unittest

from pipelines.tools.build_lite_capability_suite_impl import build_suite


class BuildLiteCapabilitySuiteTest(unittest.TestCase):
    def test_build_suite_has_balanced_capabilities_and_valid_labels(self) -> None:
        rows = build_suite(seed=42)
        self.assertEqual(len(rows), 60)

        ids = set()
        counts = {}
        label_counts = {"A": 0, "B": 0, "C": 0}
        for row in rows:
            rid = row["id"]
            self.assertNotIn(rid, ids)
            ids.add(rid)

            cap = row["capability"]
            counts[cap] = counts.get(cap, 0) + 1

            options = row["options"]
            self.assertEqual(len(options), 3)
            opt_ids = {opt["id"] for opt in options}
            self.assertEqual(opt_ids, {"A", "B", "C"})

            label = row["label"]
            self.assertIn(label, opt_ids)
            label_counts[label] += 1

        self.assertEqual(counts["terminology_consistency"], 20)
        self.assertEqual(counts["subtitle_readability"], 20)
        self.assertEqual(counts["tts_stability"], 20)
        self.assertGreater(label_counts["A"], 10)
        self.assertGreater(label_counts["B"], 10)
        self.assertGreater(label_counts["C"], 10)


if __name__ == "__main__":
    unittest.main()
