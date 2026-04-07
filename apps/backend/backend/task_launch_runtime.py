from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PreparedTaskLaunch:
    task_id: str
    video_path: str
    work_dir: Path
    log_path: Path
    cmd: List[str]
    effective: Dict[str, Any]
    mode: str
    replace_existing: bool = False


@dataclass
class QualityQueueItem:
    task_id: str
    video_path: str
    work_dir: Path
    log_path: Path
    cmd: List[str]
    effective: Dict[str, Any]
    mode: str
    replace_existing: bool = False


@dataclass
class PreparedTaskBundle:
    launch: PreparedTaskLaunch
    preset: Optional[str]
    cleaned_params: Dict[str, Any]
    rules_disable_global: bool
    rules_template_id: Optional[str]
    rules_override: Optional[Dict[str, Any]]
    effective_rules: Dict[str, Any]
    derived: Dict[str, Path]
    resume_from: Optional[str] = None


def build_prepared_task_launch(
    *,
    task_id: str,
    video_path: str,
    work_dir: Path,
    log_path: Path,
    cmd: List[str],
    effective: Dict[str, Any],
    mode: str,
    replace_existing: bool = False,
) -> PreparedTaskLaunch:
    return PreparedTaskLaunch(
        task_id=task_id,
        video_path=video_path,
        work_dir=work_dir,
        log_path=log_path,
        cmd=cmd,
        effective=effective,
        mode=mode,
        replace_existing=replace_existing,
    )


def build_prepared_task_bundle(
    *,
    launch: PreparedTaskLaunch,
    preset: Optional[str],
    cleaned_params: Dict[str, Any],
    rules_disable_global: bool,
    rules_template_id: Optional[str],
    rules_override: Optional[Dict[str, Any]],
    effective_rules: Dict[str, Any],
    derived: Dict[str, Path],
    resume_from: Optional[str] = None,
) -> PreparedTaskBundle:
    return PreparedTaskBundle(
        launch=launch,
        preset=preset,
        cleaned_params=cleaned_params,
        rules_disable_global=rules_disable_global,
        rules_template_id=rules_template_id,
        rules_override=rules_override,
        effective_rules=effective_rules,
        derived=derived,
        resume_from=resume_from,
    )


def build_bundle_task_meta(
    bundle: PreparedTaskBundle,
    *,
    config_stack_meta: Dict[str, Any],
    effective_hash: str,
    created_at: Optional[float] = None,
    resumed_at: Optional[float] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "task_id": bundle.launch.task_id,
        "video": bundle.launch.video_path,
        "mode": bundle.launch.mode,
        "preset": bundle.preset,
        "params": bundle.cleaned_params,
        "ruleset_disable_global": bundle.rules_disable_global,
        "ruleset_template_id": bundle.rules_template_id,
        "ruleset_override": bundle.rules_override,
        "ruleset_effective": bundle.effective_rules,
        "ruleset_derived": {k: str(v) for k, v in bundle.derived.items()},
        "cmd": bundle.launch.cmd,
        "config_stack": config_stack_meta,
        "effective_config_hash": effective_hash or "",
        "effective_config_path": str(bundle.launch.work_dir / "effective_config.json"),
    }
    if bundle.resume_from:
        meta["resume_from"] = bundle.resume_from
    if created_at is not None:
        meta["created_at"] = created_at
    if resumed_at is not None:
        meta["resumed_at"] = resumed_at
    return meta
