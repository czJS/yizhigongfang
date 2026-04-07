from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.ruleset_store import (
    default_doc as ruleset_default_doc,
    load_ruleset,
    merge_rulesets,
    ruleset_to_asr_dict,
    ruleset_to_en_dict,
    ruleset_to_glossary_doc,
)
from backend.ruleset_template_store import load_template_doc as load_ruleset_template_doc


def extract_rules_inputs(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[bool], Optional[str], Optional[Dict[str, Any]]]:
    """
    Allow frontend to attach rules-related inputs without leaking unknown keys into pipeline CLI.

    Supported keys (frontend payload):
    - ruleset_template_id: str
    - ruleset_override: dict (ruleset doc)
    """
    src = dict(params or {})

    # IMPORTANT (product decision):
    # Global rules are always enabled in v2 to keep behavior simple and predictable.
    # We still pop legacy keys to avoid leaking unknown flags into pipeline CLI,
    # but we ignore their values.
    src.pop("ruleset_disable_global", None)
    src.pop("disable_global_rules", None)  # compatibility
    disable_global = None

    template_id = src.pop("ruleset_template_id", None)
    if template_id is None:
        template_id = src.pop("rules_template_id", None)  # compatibility
    if template_id is not None and not isinstance(template_id, str):
        template_id = str(template_id)
    template_id = (template_id or "").strip() or None

    override = src.pop("ruleset_override", None)
    if override is None:
        override = src.pop("rules_override", None)  # backward/alternate key
    if isinstance(override, str):
        try:
            override = json.loads(override or "{}")
        except Exception:
            override = None
    if override is not None and not isinstance(override, dict):
        override = None

    return src, disable_global, template_id, override


def materialize_effective_rules(
    work_dir: Path,
    *,
    rules_override: Optional[Dict[str, Any]],
    ruleset_seed_path: Path,
    ruleset_global_path: Path,
    ruleset_templates_dir: Path,
    disable_global: bool = False,
    template_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Path]]:
    """
    Compute and persist the effective rules for this task, then emit derived files:
    - .ygf_rules/ruleset_effective.json
    - .ygf_rules/glossary.json (pipeline-compatible)
    - .ygf_rules/asr_dict.json (pipeline-compatible)
    """
    # Global rules are always enabled; ignore disable_global.
    try:
        seed: Dict[str, Any] = load_ruleset(ruleset_seed_path)
    except Exception:
        seed = ruleset_default_doc()
    try:
        global_rules: Dict[str, Any] = load_ruleset(ruleset_global_path)
    except Exception:
        global_rules = ruleset_default_doc()
    try:
        base = merge_rulesets(seed, global_rules)
    except Exception:
        base = global_rules or seed or ruleset_default_doc()

    if template_id:
        try:
            tpl_doc = load_ruleset_template_doc(ruleset_templates_dir, template_id)
            base = merge_rulesets(base, tpl_doc)
        except Exception:
            pass
    try:
        effective = merge_rulesets(base, rules_override)
    except Exception:
        effective = base

    rules_dir = work_dir / ".ygf_rules"
    derived: Dict[str, Path] = {}
    try:
        rules_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return effective, derived

    p_rules = rules_dir / "ruleset_effective.json"
    try:
        p_rules.write_text(json.dumps(effective, ensure_ascii=False, indent=2), encoding="utf-8")
        derived["ruleset_path"] = p_rules
    except Exception:
        pass

    p_glossary = rules_dir / "glossary.json"
    try:
        gdoc = ruleset_to_glossary_doc(effective)
        p_glossary.write_text(json.dumps(gdoc, ensure_ascii=False, indent=2), encoding="utf-8")
        derived["glossary_path"] = p_glossary
    except Exception:
        pass

    p_asr = rules_dir / "asr_dict.json"
    try:
        asr_map = ruleset_to_asr_dict(effective)
        p_asr.write_text(json.dumps(asr_map, ensure_ascii=False, indent=2), encoding="utf-8")
        derived["asr_dict_path"] = p_asr
    except Exception:
        pass

    p_en = rules_dir / "en_dict.json"
    try:
        en_map = ruleset_to_en_dict(effective)
        p_en.write_text(json.dumps(en_map, ensure_ascii=False, indent=2), encoding="utf-8")
        derived["en_dict_path"] = p_en
    except Exception:
        pass

    return effective, derived
