#!/usr/bin/env python3
"""Fail closed unless the physical backend satisfies the declared autonomy boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def evaluate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Derive readiness only from explicit requirements and macro status."""
    requirements = manifest.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise ValueError("readiness manifest must contain requirements")
    invalid = [
        item.get("id", "<missing-id>")
        for item in requirements
        if not isinstance(item, dict)
        or type(item.get("ready")) is not bool
        or not isinstance(item.get("id"), str)
    ]
    if invalid:
        raise ValueError(f"invalid readiness requirements: {invalid}")
    blocked = [item for item in requirements if not item["ready"]]
    ready = not blocked and manifest.get("fixed_plan_macro") is False
    declared = manifest.get("status")
    expected_declared = "READY" if ready else "BLOCKED"
    if declared != expected_declared:
        raise ValueError(
            f"declared status {declared!r} disagrees with derived {expected_declared!r}"
        )
    return {
        "slice": manifest.get("slice"),
        "ready": ready,
        "status": expected_declared,
        "fixed_plan_macro": manifest.get("fixed_plan_macro"),
        "blocked_requirements": [item["id"] for item in blocked],
        "claim_policy": manifest.get("claim_policy"),
    }


def main() -> int:
    """Check either the current blocked declaration or future autonomous readiness."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("autonomous-readiness.json"),
    )
    parser.add_argument("--expect", choices=("blocked", "ready"), default="ready")
    args = parser.parse_args()
    result = evaluate(json.loads(args.manifest.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] == (args.expect == "ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
