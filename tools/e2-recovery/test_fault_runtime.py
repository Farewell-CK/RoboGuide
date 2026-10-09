"""Unit tests for the recovery-pilot scientific boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


def load(name: str) -> Any:
    """Load one sibling script without installing the tools directory."""
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_has_three_clean_and_three_fault_runs() -> None:
    """The pilot denominator and replicate identities are frozen."""
    module = load("run_fault_batch")
    manifest = module.load_manifest(Path(__file__).with_name("fault-pilot6.json"))
    profiles = [run["fault_profile"] for run in manifest["runs"]]
    assert profiles == [
        "f1-node-loss",
        "f0-clean",
        "f0-clean",
        "f1-node-loss",
        "f1-node-loss",
        "f0-clean",
    ]


def test_fault_verdict_requires_real_standby_registration(tmp_path: Path) -> None:
    """A task success alone cannot be reported as a successful F1 recovery."""
    module = load("run_fault")
    (tmp_path / "verdict.json").write_text(json.dumps({"success": True}))
    (tmp_path / "fault-timeline.json").write_text(
        json.dumps(
            [
                {"event": "fault_triggered"},
                {"event": "primary_exited"},
            ]
        )
    )
    verdict = module.fault_verdict(tmp_path, "f1-node-loss", 6)
    assert verdict["task_success"] is True
    assert verdict["injection_valid"] is False
    assert verdict["full_fault_run_success"] is False


def test_clean_verdict_rejects_accidental_injection(tmp_path: Path) -> None:
    """F0 remains a true no-fault control."""
    module = load("run_fault")
    (tmp_path / "verdict.json").write_text(json.dumps({"success": True}))
    (tmp_path / "fault-timeline.json").write_text(json.dumps([{"event": "fault_triggered"}]))
    verdict = module.fault_verdict(tmp_path, "f0-clean", None)
    assert verdict["injection_valid"] is False
