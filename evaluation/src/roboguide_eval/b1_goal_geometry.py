"""Optional terminal geometry comparison for B1 any-at goals.

The official Habitat metric remains the only benchmark outcome. This module
reconstructs the deployed three-dimensional any-at geometry and a clearly
labeled world-X/Z counterfactual from the same terminal diagnostic snapshot.
Missing or inconsistent evidence yields ``unavailable`` and never changes
admission, Mission state, or the benchmark result.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from roboguide_eval.b1_provenance import load_document, plan_digest

GEOMETRY_SCHEMA = "roboguide.e1.b1-goal-geometry-diagnostic/v0.1"
TERMINAL_SCHEMA = "roboguide.e1.physical-diagnostics/v0.7"
MOTION_TERMINAL_SCHEMA = "roboguide.e1.physical-diagnostics/v0.6"
ORACLE_TERMINAL_SCHEMA = "roboguide.e1.physical-diagnostics/v0.5"
PREVIOUS_TERMINAL_SCHEMA = "roboguide.e1.physical-diagnostics/v0.4"
SEMANTIC_SCHEMA = "roboguide.authoritative-semantic-evidence/v0.2"
OUTPUT_NAME = "evidence/goal-geometry-diagnostic.json"


class GoalGeometryUnavailable(ValueError):
    """Explain why optional geometry cannot be calculated from frozen evidence."""


def _object(value: object, name: str) -> dict[str, Any]:
    """Require an object instead of silently substituting an empty record."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise GoalGeometryUnavailable(f"{name}_invalid")
    return value


def _position(value: object, name: str) -> tuple[float, float, float]:
    """Accept only a finite world-coordinate triple from the terminal snapshot."""
    if not isinstance(value, list) or len(value) != 3:
        raise GoalGeometryUnavailable(f"{name}_unavailable")
    if any(isinstance(item, bool) or not isinstance(item, int | float) for item in value):
        raise GoalGeometryUnavailable(f"{name}_invalid")
    point = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in point):
        raise GoalGeometryUnavailable(f"{name}_nonfinite")
    return point[0], point[1], point[2]


def _goal_value(goal: object, truths: dict[str, bool]) -> bool:
    """Evaluate only exact any-at atoms and AND/OR composition from frozen semantics."""
    node = _object(goal, "semantic_goal")
    if node.get("kind") == "predicate":
        arguments = node.get("arguments")
        if (
            set(node) != {"kind", "name", "arguments"}
            or node.get("name") != "any_at"
            or not isinstance(arguments, list)
            or len(arguments) != 1
            or not isinstance(arguments[0], str)
            or not arguments[0]
        ):
            raise GoalGeometryUnavailable("goal_not_any_at_only")
        try:
            return truths[arguments[0]]
        except KeyError as error:
            raise GoalGeometryUnavailable("goal_entity_position_missing") from error
    if (
        node.get("kind") != "logical"
        or set(node) != {"kind", "operator", "operands", "quantifier", "variables"}
        or node.get("operator") not in {"and", "or"}
        or node.get("quantifier") is not None
        or node.get("variables") != []
        or not isinstance(node.get("operands"), list)
        or len(node["operands"]) < 2
    ):
        raise GoalGeometryUnavailable("goal_not_any_at_only")
    values = [_goal_value(child, truths) for child in node["operands"]]
    return all(values) if node["operator"] == "and" else any(values)


def assess_goal_geometry(
    semantic: object,
    terminal: object,
    summary: object,
    verdict: object,
) -> dict[str, Any]:
    """Compare terminal 3D truth and horizontal counterfactual without new simulation.

    Cross-check run-local source links and terminal episode/scene/step identity.
    The 3D reconstruction must agree with Habitat's final official metric
    before the horizontal comparison is exposed as an available diagnostic.
    """
    result: dict[str, Any] = {
        "schema_version": GEOMETRY_SCHEMA,
        "status": "unavailable",
        "authority": "diagnostic-only; never benchmark success",
        "comparison": "terminal world-X/Z projection versus deployed 3D any_at",
    }
    try:
        admission = _object(_object(verdict, "b1_verdict").get("admission"), "b1_admission")
        if admission.get("provenance_valid") is not True:
            raise GoalGeometryUnavailable("b1_provenance_not_valid")
        source = _object(semantic, "semantic_evidence")
        if source.get("schema_version") != SEMANTIC_SCHEMA:
            raise GoalGeometryUnavailable("semantic_schema_unsupported")
        source_digest = source.get("digest")
        if not isinstance(source_digest, str) or source_digest != plan_digest(
            {key: value for key, value in source.items() if key != "digest"}
        ):
            raise GoalGeometryUnavailable("semantic_digest_invalid")
        world = _object(summary, "shared_world_summary")
        if world.get("authoritative_semantic_evidence_digest") != source_digest:
            raise GoalGeometryUnavailable("semantic_summary_mismatch")
        official = world.get("official_pddl_success")
        if type(official) is not bool:
            raise GoalGeometryUnavailable("official_pddl_success_unavailable")
        snapshot = _object(terminal, "terminal_diagnostics")
        if (
            snapshot.get("schema_version")
            not in {
                TERMINAL_SCHEMA,
                MOTION_TERMINAL_SCHEMA,
                ORACLE_TERMINAL_SCHEMA,
                PREVIOUS_TERMINAL_SCHEMA,
            }
            or snapshot.get("phase") != "terminal_world_state"
        ):
            raise GoalGeometryUnavailable("terminal_diagnostics_schema_unsupported")
        world_identity = _object(world.get("identity"), "world_identity")
        if (
            not isinstance(snapshot.get("episode_id"), str)
            or not snapshot["episode_id"]
            or snapshot.get("episode_id") != world_identity.get("episode_id")
            or not isinstance(snapshot.get("scene_id"), str)
            or not snapshot["scene_id"]
            or snapshot.get("scene_id") != world_identity.get("scene_id")
        ):
            raise GoalGeometryUnavailable("terminal_episode_scene_mismatch")
        terminal_steps = snapshot.get("simulator_steps")
        if (
            type(terminal_steps) is not int
            or terminal_steps < 0
            or terminal_steps != world_identity.get("simulator_steps")
        ):
            raise GoalGeometryUnavailable("terminal_step_identity_mismatch")
        metrics = _object(snapshot.get("official_metrics"), "terminal_official_metrics")
        if type(metrics.get("pddl_success")) is not bool or metrics["pddl_success"] != official:
            raise GoalGeometryUnavailable("terminal_official_metric_mismatch")
        threshold_value = snapshot.get("robot_at_threshold_m")
        if (
            isinstance(threshold_value, bool)
            or not isinstance(threshold_value, int | float)
            or not math.isfinite(threshold_value)
            or threshold_value <= 0
        ):
            raise GoalGeometryUnavailable("robot_at_threshold_unavailable")
        threshold = float(threshold_value)
        agents = _object(snapshot.get("agents"), "terminal_agents")
        if not agents:
            raise GoalGeometryUnavailable("terminal_agents_empty")
        expected_agents = _object(world_identity.get("initial_agent_positions"), "world_agents")
        if set(agents) != set(expected_agents):
            raise GoalGeometryUnavailable("terminal_agent_set_mismatch")
        agent_positions = {
            agent: _position(
                _object(row, "terminal_agent").get("pddl_reference_position"),
                "pddl_reference_position",
            )
            for agent, row in agents.items()
        }
        targets = _object(snapshot.get("goal_entity_positions"), "goal_entity_positions")
        predicate_rows: list[dict[str, Any]] = []
        three_d_truths: dict[str, bool] = {}
        horizontal_truths: dict[str, bool] = {}
        for entity_id, raw_target in sorted(targets.items()):
            target = _position(raw_target, "goal_entity_position")
            distances: dict[str, dict[str, float]] = {}
            for agent, point in sorted(agent_positions.items()):
                horizontal = math.hypot(point[0] - target[0], point[2] - target[2])
                vertical = abs(point[1] - target[1])
                distances[agent] = {
                    "horizontal_m": horizontal,
                    "vertical_m": vertical,
                    "three_d_m": math.hypot(horizontal, vertical),
                }
            three_d_truths[entity_id] = any(
                item["three_d_m"] <= threshold for item in distances.values()
            )
            horizontal_truths[entity_id] = any(
                item["horizontal_m"] <= threshold for item in distances.values()
            )
            predicate_rows.append(
                {
                    "entity_id": entity_id,
                    "agent_distances": distances,
                    "three_d_any_at": three_d_truths[entity_id],
                    "horizontal_any_at_counterfactual": horizontal_truths[entity_id],
                }
            )
        goal = source.get("goal")
        three_d_goal = _goal_value(goal, three_d_truths)
        horizontal_goal = _goal_value(goal, horizontal_truths)
        if three_d_goal != official:
            raise GoalGeometryUnavailable("three_d_reconstruction_disagrees_with_official")
        result.update(
            status="available",
            semantic_evidence_digest=source_digest,
            terminal_diagnostics_schema=snapshot["schema_version"],
            robot_at_threshold_m=threshold,
            official_pddl_success=official,
            reconstructed_three_d_goal=three_d_goal,
            horizontal_goal_counterfactual=horizontal_goal,
            predicate_distances=predicate_rows,
        )
    except (GoalGeometryUnavailable, ValueError, TypeError, OverflowError) as error:
        result["reason"] = (
            str(error)
            if isinstance(error, GoalGeometryUnavailable)
            else "malformed_geometry_evidence"
        )
    return result


def write_goal_geometry_diagnostic(run: Path, output: Path | None = None) -> dict[str, Any]:
    """Write one optional, atomic sidecar from archived B1 evidence only."""
    document = assess_goal_geometry(
        load_document(run / "evidence/authoritative-semantic-evidence.json"),
        load_document(run / "evidence/diagnostics-terminal.json"),
        load_document(run / "evidence/shared-world-summary.json"),
        load_document(run / "b1-verdict.json"),
    )
    target = output or run / OUTPUT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return document


def main() -> None:
    """Recompute a diagnostic sidecar offline without invoking a Provider or Habitat."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    document = write_goal_geometry_diagnostic(arguments.run, arguments.output)
    print(json.dumps({"status": document["status"], "reason": document.get("reason")}))


if __name__ == "__main__":
    main()
