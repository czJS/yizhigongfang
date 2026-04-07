from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.ruleset_runtime import extract_rules_inputs, materialize_effective_rules


class RulesetRuntimeTest(unittest.TestCase):
    def test_extract_rules_inputs_strips_frontend_only_keys(self) -> None:
        cleaned, disable_global, template_id, override = extract_rules_inputs(
            {
                "foo": 1,
                "ruleset_disable_global": True,
                "rules_template_id": "tpl_1",
                "rules_override": '{"asr_fixes": [{"src": "錯字", "tgt": "错字"}]}',
            }
        )

        self.assertEqual(cleaned, {"foo": 1})
        self.assertIsNone(disable_global)
        self.assertEqual(template_id, "tpl_1")
        self.assertIsInstance(override, dict)
        self.assertIn("asr_fixes", override or {})

    def test_materialize_effective_rules_writes_derived_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_dir = root / "job"
            work_dir.mkdir(parents=True, exist_ok=True)
            seed_path = root / "seed.json"
            global_path = root / "global.json"
            templates_dir = root / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)

            seed_path.write_text(
                json.dumps({"version": 1, "asr_fixes": [{"src": "甲", "tgt": "乙"}], "en_fixes": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            global_path.write_text(
                json.dumps({"version": 1, "asr_fixes": [{"src": "丙", "tgt": "丁"}], "en_fixes": [{"src": "teh", "tgt": "the"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (templates_dir / "tpl_a.json").write_text(
                json.dumps(
                    {
                        "id": "tpl_a",
                        "name": "tpl",
                        "doc": {"version": 1, "asr_fixes": [{"src": "戊", "tgt": "己"}], "en_fixes": []},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            effective, derived = materialize_effective_rules(
                work_dir,
                rules_override={"version": 1, "asr_fixes": [{"src": "庚", "tgt": "辛"}], "en_fixes": []},
                ruleset_seed_path=seed_path,
                ruleset_global_path=global_path,
                ruleset_templates_dir=templates_dir,
                template_id="tpl_a",
            )

            self.assertTrue((work_dir / ".ygf_rules" / "ruleset_effective.json").exists())
            self.assertTrue((work_dir / ".ygf_rules" / "glossary.json").exists())
            self.assertTrue((work_dir / ".ygf_rules" / "asr_dict.json").exists())
            self.assertTrue((work_dir / ".ygf_rules" / "en_dict.json").exists())
            self.assertIn("ruleset_path", derived)
            self.assertIn("glossary_path", derived)
            self.assertIn("asr_dict_path", derived)
            self.assertIn("en_dict_path", derived)
            self.assertTrue(any(str(item.get("src")) == "庚" for item in effective.get("asr_fixes") or []))


if __name__ == "__main__":
    unittest.main()
