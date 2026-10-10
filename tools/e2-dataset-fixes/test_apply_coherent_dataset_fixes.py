"""Tests for the reviewed COHERENT dataset correction tool."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).with_name("apply_coherent_dataset_fixes.py")
SPEC = importlib.util.spec_from_file_location("dataset_fixes", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def graph_task(nodes: list[dict[str, object]], goal: dict[str, object]) -> dict[str, object]:
    """Build the minimum task shape accepted by the correction functions."""
    return {"init_graph": {"nodes": nodes}, "task_goal": goal, "goal_instruction": []}


def test_reviewed_corrections_are_idempotent() -> None:
    """Each reviewed source state becomes the intended corrected state exactly once."""
    env3 = [graph_task([], {}) for _ in range(16)]
    env3[0] = graph_task(
        [{"id": 25, "class_name": "trash can", "properties": [], "states": []}], {}
    )
    env3[13] = graph_task(
        [], {"on_<quadrotor>(21)_<dining table>(2)": [1, []]}
    )
    env3[13]["goal_instruction"] = ["land on <dining table>(3)"]
    MODULE.apply_env3(env3)
    MODULE.apply_env3(env3)
    trash = env3[0]["init_graph"]["nodes"][0]
    assert trash["properties"] == ["CONTAINERS"]
    assert trash["states"] == ["OPEN_FOREVER"]
    assert env3[13]["task_goal"] == {
        "on_<quadrotor>(21)_<dining table>(3)": [1, []]
    }

    env4 = [graph_task([], {}) for _ in range(16)]
    env4[15] = graph_task(
        [
            {
                "id": 36,
                "class_name": "apple",
                "properties": ["SURFACES", "ON_HIGH_SURFACE"],
                "states": [],
            }
        ],
        {},
    )
    MODULE.apply_env4(env4)
    MODULE.apply_env4(env4)
    assert env4[15]["init_graph"]["nodes"][0]["properties"] == [
        "SURFACES",
        "ON_HIGH_SURFACE",
        "GRABABLE",
        "MOVABLE",
    ]


def test_text_patch_preserves_unrelated_formatting() -> None:
    """Text materialization changes only reviewed fields, not the whole JSON style."""
    env3 = '''[
    {
        "task_id": 0,
        "init_graph": {"nodes": [{
                    "prefab_name": "trash_can_1",
                    "properties": [
                    ],
                    "states": []
        }]}
    },
    {"task_id": 1},
    {
        "task_id": 13,
        "task_goal": {"on_<quadrotor>(21)_<dining table>(2)": [1, []]}
    }
]'''
    corrected = MODULE.patch_text(env3, "env3")
    assert '"states": [\n                        "OPEN_FOREVER"\n                    ]' in corrected
    assert '"on_<quadrotor>(21)_<dining table>(3)"' in corrected
    assert corrected.startswith('[\n    {')
