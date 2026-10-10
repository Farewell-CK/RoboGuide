#!/usr/bin/env python3
"""Materialize and verify the three reviewed COHERENT dataset corrections.

The script edits only the selected benchmark JSON copies, refuses unknown input
states, and writes a hash-bound manifest suitable for experiment provenance.
It is idempotent: already-corrected entries are verified rather than changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SUITES = ("PEFA", "DRMS", "mcts", "CRMS", "PEFA_wo_history")
VARIANT = "coherent-official+dataset-fixes-v1"


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def node(task: dict[str, Any], node_id: int) -> dict[str, Any]:
    """Resolve one exact graph node or fail closed."""
    matches = [item for item in task["init_graph"]["nodes"] if item.get("id") == node_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one node {node_id}, found {len(matches)}")
    return matches[0]


def add_once(values: list[str], value: str) -> None:
    """Append a semantic marker once while preserving source ordering."""
    if value not in values:
        values.append(value)


def apply_env3(document: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Correct env3/task0 reachability and env3/task13 goal identity."""
    task0 = document[0]
    trash = node(task0, 25)
    if trash.get("class_name") != "trash can":
        raise ValueError("env3/task0 node 25 is not the reviewed trash can")
    properties = trash.get("properties")
    states = trash.get("states")
    if not isinstance(properties, list) or not isinstance(states, list):
        raise ValueError("env3/task0 trash can properties/states are malformed")
    if set(properties) - {"CONTAINERS"} or set(states) - {"OPEN_FOREVER"}:
        raise ValueError("env3/task0 trash can has an unreviewed source state")
    add_once(properties, "CONTAINERS")
    add_once(states, "OPEN_FOREVER")

    task13 = document[13]
    goals = task13.get("task_goal")
    if not isinstance(goals, dict):
        raise ValueError("env3/task13 task_goal is malformed")
    old = "on_<quadrotor>(21)_<dining table>(2)"
    new = "on_<quadrotor>(21)_<dining table>(3)"
    if old in goals and new in goals:
        raise ValueError("env3/task13 contains both old and corrected landing goals")
    if old in goals:
        value = goals.pop(old)
        goals[new] = value
    elif new not in goals:
        raise ValueError("env3/task13 landing goal is neither reviewed source nor correction")
    instruction = " ".join(task13.get("goal_instruction", []))
    if "<dining table>(3)" not in instruction:
        raise ValueError("env3/task13 instruction does not support the corrected table identity")
    return document


def apply_env4(document: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Correct env4/task15 apple manipulation properties."""
    task15 = document[15]
    apple = node(task15, 36)
    if apple.get("class_name") != "apple":
        raise ValueError("env4/task15 node 36 is not the reviewed apple")
    properties = apple.get("properties")
    if not isinstance(properties, list):
        raise ValueError("env4/task15 apple properties are malformed")
    allowed = {"SURFACES", "ON_HIGH_SURFACE", "GRABABLE", "MOVABLE"}
    if set(properties) - allowed or not {"SURFACES", "ON_HIGH_SURFACE"}.issubset(properties):
        raise ValueError("env4/task15 apple has an unreviewed source state")
    add_once(properties, "GRABABLE")
    add_once(properties, "MOVABLE")
    return document


def task_segment(text: str, task_index: int) -> tuple[int, int, str]:
    """Return the source slice owned by one indexed task without reformatting JSON."""
    marker = re.compile(rf'^\s*"task_id":\s*{task_index},\s*$', re.MULTILINE)
    match = marker.search(text)
    if match is None:
        raise ValueError(f"task {task_index} source marker is unavailable")
    next_match = re.compile(r'^\s*"task_id":\s*\d+,\s*$', re.MULTILINE).search(
        text, match.end()
    )
    end = next_match.start() if next_match is not None else len(text)
    return match.start(), end, text[match.start() : end]


def replace_once(segment: str, old: str, new: str, label: str) -> str:
    """Replace one reviewed source form or accept one already-corrected form."""
    old_count = segment.count(old)
    new_count = segment.count(new)
    if old_count == 1 and new_count == 0:
        return segment.replace(old, new, 1)
    if old_count == 0 and new_count == 1:
        return segment
    raise ValueError(f"{label} is not in a unique reviewed source/corrected state")


def patch_text(text: str, env_name: str) -> str:
    """Apply exact corrections while retaining all unrelated source formatting."""
    if env_name == "env3":
        start, end, segment = task_segment(text, 0)
        old = '''                    "prefab_name": "trash_can_1",
                    "properties": [
                    ],
                    "states": []'''
        new = '''                    "prefab_name": "trash_can_1",
                    "properties": [
                        "CONTAINERS"
                    ],
                    "states": [
                        "OPEN_FOREVER"
                    ]'''
        segment = replace_once(segment, old, new, "env3/task0 trash can")
        text = text[:start] + segment + text[end:]

        start, end, segment = task_segment(text, 13)
        old = '"on_<quadrotor>(21)_<dining table>(2)"'
        new = '"on_<quadrotor>(21)_<dining table>(3)"'
        segment = replace_once(segment, old, new, "env3/task13 landing goal")
        return text[:start] + segment + text[end:]
    if env_name == "env4":
        start, end, segment = task_segment(text, 15)
        old = '''                    "prefab_name": "apple_0",
                    "properties": [
                        "SURFACES",
                        "ON_HIGH_SURFACE"
                    ],'''
        new = '''                    "prefab_name": "apple_0",
                    "properties": [
                        "SURFACES",
                        "ON_HIGH_SURFACE",
                        "GRABABLE",
                        "MOVABLE"
                    ],'''
        segment = replace_once(segment, old, new, "env4/task15 apple")
        return text[:start] + segment + text[end:]
    raise ValueError(f"unsupported environment {env_name}")


def patch_file(path: Path, env_name: str, source_text: str | None = None) -> dict[str, str]:
    """Patch one environment file and return before/after identity evidence."""
    text = path.read_text(encoding="utf-8") if source_text is None else source_text
    before = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document = json.loads(text)
    if not isinstance(document, list) or len(document) <= 15:
        raise ValueError(f"{path} is not the reviewed COHERENT task array")
    if env_name == "env3":
        apply_env3(document)
    elif env_name == "env4":
        apply_env4(document)
    else:
        raise ValueError(f"unsupported environment {env_name}")
    rendered = patch_text(text, env_name)
    corrected = json.loads(rendered)
    if env_name == "env3":
        apply_env3(corrected)
    else:
        apply_env4(corrected)
    if corrected != json.loads(rendered):
        raise AssertionError("text correction did not exactly match semantic correction")
    path.write_text(rendered, encoding="utf-8")
    return {"path": str(path), "sha256_before": before, "sha256_after": sha256(path)}


def main() -> int:
    """Apply all reviewed corrections and emit a reproducibility manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--coherent-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--source-revision",
        help="Materialize from an exact Git revision before applying fixes (migration only).",
    )
    args = parser.parse_args()
    root = args.coherent_root.resolve()
    records: list[dict[str, str]] = []
    for suite in SUITES:
        env_root = root / "src" / "experiment" / suite / "env"
        for env_name in ("env3", "env4"):
            path = env_root / f"{env_name}.json"
            source_text = None
            if args.source_revision:
                relative = path.relative_to(root).as_posix()
                source_text = subprocess.check_output(
                    ["git", "-C", str(root), "show", f"{args.source_revision}:{relative}"],
                    text=True,
                    encoding="utf-8",
                )
            records.append(patch_file(path, env_name, source_text))
    manifest = {
        "schema": "roboguide.coherent-dataset-fixes/v1",
        "dataset_variant": VARIANT,
        "source_root": str(root),
        "corrections": [
            {
                "task": "env3/task0",
                "change": "node 25 trash can: add CONTAINERS and OPEN_FOREVER",
            },
            {
                "task": "env3/task13",
                "change": "task_goal landing target: dining table 2 -> dining table 3",
            },
            {
                "task": "env4/task15",
                "change": "node 36 apple: add GRABABLE and MOVABLE",
            },
        ],
        "files": records,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
