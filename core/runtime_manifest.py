from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def get_quality_windows_manifest_path(root: Path) -> Path:
    return root / "assets" / "runtime" / "quality_windows_manifest.json"


def load_quality_windows_manifest(root: Path) -> Dict[str, Any]:
    path = get_quality_windows_manifest_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_quality_runtime_resource_check(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    manifest_path = get_quality_windows_manifest_path(root)
    manifest = load_quality_windows_manifest(root)
    quality = manifest.get("quality") if isinstance(manifest, dict) else {}
    runtime_files = quality.get("runtimeFiles") if isinstance(quality, dict) else []
    checks = []
    for item in runtime_files if isinstance(runtime_files, list) else []:
        rel = str((item or {}).get("relativePath") or "").strip()
        target = root / rel if rel else root
        checks.append(
            {
                "key": str((item or {}).get("key") or ""),
                "label": str((item or {}).get("label") or (item or {}).get("key") or rel or "unknown"),
                "path": str(target),
                "exists": bool(target.exists()),
            }
        )

    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    llm_model = str(defaults.get("llm_model") or "").strip()
    zh_phrase_llm_model = str(defaults.get("zh_phrase_llm_model") or "").strip() or llm_model
    required_ollama_models = []
    for model_id in [llm_model, zh_phrase_llm_model]:
        if model_id and model_id not in required_ollama_models:
            required_ollama_models.append(model_id)

    return {
        "manifest_path": str(manifest_path),
        "manifest_exists": bool(manifest_path.exists()),
        "runtime_checks": checks,
        "required_ollama_models": required_ollama_models,
        "offline": bool(defaults.get("offline")),
        "llm_endpoint": str(defaults.get("llm_endpoint") or ""),
        "zh_phrase_llm_endpoint": str(defaults.get("zh_phrase_llm_endpoint") or defaults.get("llm_endpoint") or ""),
        "offline_endpoint_adjustments": defaults.get("_offline_endpoint_adjustments") if isinstance(defaults.get("_offline_endpoint_adjustments"), list) else [],
    }

