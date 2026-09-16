"""RoboGuide runner for the real Controller-to-Local-EAIOS path.

The configured command must submit through the Controller HTTP API and leave
the C1-S0B controlled verdict in the run directory. This runner only reduces
that persisted evidence into the shared evaluation contract; it never calls a
Habitat skill, mutates Control, or infers benchmark success from Mission state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from roboguide_eval.metrics import MetricsPayload, RawEvidenceRef
from roboguide_eval.models import JSONObject, JSONValue
from roboguide_eval.process import ProcessOutcome
from roboguide_eval.runner import PreparedSystem, ProcessSystemRunner

CONTROLLED_VERDICT = "verdict.json"
CONTROLLED_VERDICT_SCHEMA = "roboguide.c1-s0b-controlled-verdict/v0.2"
CONTROLLED_EVIDENCE = (
    "verdict.json",
    "mission.json",
    "events.json",
    "execution-attempts.json",
    "evidence/controlled-outcome.json",
    "evidence/action_trace.jsonl",
    "evidence/scene_description.txt",
    "evidence/subtask.txt",
)


def _object(value: object) -> dict[str, object] | None:
    """Narrow a decoded value to a string-keyed object when possible."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def _integer(value: object) -> int | None:
    """Return a JSON integer while rejecting booleans."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def read_controlled_verdict(path: Path) -> dict[str, object] | None:
    """Read one strict controlled verdict without weakening run validation.

    Args:
        path: Expected ``verdict.json`` path inside the run directory.

    Returns:
        The decoded verdict, or ``None`` when it is absent, malformed, or from
        an unsupported schema.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    verdict = _object(document)
    if verdict is None or verdict.get("schema") != CONTROLLED_VERDICT_SCHEMA:
        return None
    return verdict


class RoboGuideRunner(ProcessSystemRunner):
    """Run and observe RoboGuide through its production Controller path."""

    system = "roboguide"

    def collect_result(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> MetricsPayload:
        """Reduce controlled production evidence without collapsing outcomes.

        Args:
            prepared: Prepared runner configuration.
            run_directory: Run directory populated by the production scenario.
            outcome: External process outcome used only by the base evidence
                collector and later wall-time backfill.

        Returns:
            Canonical values plus independent Mission, Local EAIOS, episode,
            and benchmark evidence. Missing evidence remains unavailable.
        """
        base = super().collect_result(prepared, run_directory, outcome)
        values = dict(base.values)
        details = dict(base.details)
        evidence = list(base.raw_evidence)
        known_paths = {item.path for item in evidence}
        for relative_path in CONTROLLED_EVIDENCE:
            if relative_path not in known_paths and (run_directory / relative_path).is_file():
                evidence.append(
                    RawEvidenceRef(
                        path=relative_path,
                        description="RoboGuide controlled production evidence",
                        media_type=(
                            "application/x-ndjson"
                            if relative_path.endswith(".jsonl")
                            else "application/json"
                            if relative_path.endswith(".json")
                            else "text/plain"
                        ),
                    )
                )

        verdict = read_controlled_verdict(run_directory / CONTROLLED_VERDICT)
        if verdict is None:
            details["unavailable_metrics"] = {
                "controlled_outcomes": "no supported controlled verdict was produced"
            }
            return MetricsPayload(values=values, details=details, raw_evidence=tuple(evidence))

        checks = _object(verdict.get("checks"))
        local_execution = _object(verdict.get("local_execution"))
        local_outcome = (
            _object(local_execution.get("outcome")) if local_execution is not None else None
        )
        if checks is None:
            details["unavailable_metrics"] = {
                "controlled_outcomes": "controlled verdict lacks checks"
            }
            return MetricsPayload(values=values, details=details, raw_evidence=tuple(evidence))

        self._copy_boolean(values, "success", checks.get("benchmark_task_achieved"))
        self._copy_boolean(values, "local_skill_completed", checks.get("local_skill_completed"))
        self._copy_boolean(values, "episode_terminated", checks.get("episode_terminated"))
        mission_status_value = checks.get("mission_status")
        mission_status = mission_status_value if isinstance(mission_status_value, str) else None
        if isinstance(mission_status, str):
            values["mission_completed"] = mission_status == "Completed"
            values["system_failure"] = mission_status == "Failed"
        local_state_value = checks.get("local_outcome_state")
        local_state = local_state_value if isinstance(local_state_value, str) else None
        outcome_present = local_outcome is not None
        if isinstance(local_state, str) and outcome_present:
            values["local_agent_failure"] = local_state == "FAILED"
        local_skill_value = checks.get("local_skill_completed")
        local_skill_completed = local_skill_value if isinstance(local_skill_value, bool) else None
        terminal_basis_value = checks.get("local_terminal_basis")
        terminal_basis = terminal_basis_value if isinstance(terminal_basis_value, str) else None
        verdict_value = verdict.get("verdict")
        verdict_name = verdict_value if isinstance(verdict_value, str) else None

        if local_outcome is not None:
            simulator_steps = _integer(local_outcome.get("simulator_steps"))
            if simulator_steps is not None:
                values["simulation_steps"] = simulator_steps
        local_calls = _integer(checks.get("llm_calls"))
        local_tokens = _integer(checks.get("local_model_tokens"))
        if local_calls is not None:
            values["local_llm_calls"] = local_calls
        if local_tokens is not None:
            values["local_token_usage"] = local_tokens
            values["token_usage"] = local_tokens
        values["global_llm_calls"] = 0
        values["global_token_usage"] = 0

        for metric, check in (
            ("local_replan_count", "local_replans"),
            ("invalid_output_count", "invalid_outputs"),
            ("send_request_count", "send_request_count"),
            ("message_pipe_activity_count", "message_pipe_activity_count"),
            ("physical_dispatch_count", "physical_dispatch_count"),
        ):
            metric_value = _integer(checks.get(check))
            if metric_value is not None:
                values[metric] = metric_value

        infrastructure_checks = [
            checks.get("controller_alive"),
            checks.get("node_connected"),
            checks.get("bridge_responsive"),
        ]
        if all(isinstance(value, bool) for value in infrastructure_checks):
            values["infrastructure_failure"] = not all(infrastructure_checks)

        unavailable_metrics: JSONObject = {
            "model_failure": "the local stack does not emit a distinct model-failure fact"
        }
        if local_outcome is None:
            unavailable_metrics["local_execution_outcome"] = (
                "the bridge did not persist a semantic local terminal outcome"
            )
        details.update(
            {
                "benchmark_success_source": "Habitat pddl_success",
                "episode_outcome": (
                    "terminated" if checks.get("episode_terminated") is True else "not-terminated"
                ),
                "local_execution_outcome": local_state,
                "local_terminal_basis": terminal_basis,
                "local_skill_completed": local_skill_completed,
                "mission_outcome": mission_status,
                "skill_action_sequence": cast(JSONValue, checks.get("skill_sequence")),
                "verdict": verdict_name,
                "unavailable_metrics": unavailable_metrics,
            }
        )
        return MetricsPayload(values=values, details=details, raw_evidence=tuple(evidence))

    def resolve_episode_identity(
        self,
        prepared: PreparedSystem,
        run_directory: Path,
        outcome: ProcessOutcome,
    ) -> JSONObject:
        """Resolve Habitat episode and scene from persisted local outcome.

        Args:
            prepared: Prepared runner configuration, unused beyond interface
                conformance.
            run_directory: Finished RoboGuide run directory.
            outcome: Process outcome, unused because identity comes from local
                execution evidence.

        Returns:
            A resolved identity only when both episode and scene are present.
        """
        del prepared, outcome
        verdict = read_controlled_verdict(run_directory / CONTROLLED_VERDICT)
        local_execution = _object(verdict.get("local_execution")) if verdict else None
        local_outcome = (
            _object(local_execution.get("outcome")) if local_execution is not None else None
        )
        if local_outcome is None:
            return {"status": "unresolved", "reason": "controlled local outcome is unavailable"}
        episode_id = local_outcome.get("episode_id")
        scene_id = local_outcome.get("scene_id")
        if not isinstance(episode_id, str) or not isinstance(scene_id, str):
            return {"status": "unresolved", "reason": "local outcome lacks episode or scene"}
        return {
            "status": "resolved",
            "resolved_episode_id": episode_id,
            "resolved_scene_id": scene_id,
            "dataset_index": None,
            "evidence_source": ["RoboGuide Local EAIOS controlled outcome"],
        }

    @staticmethod
    def _copy_boolean(values: dict[str, bool | int | float], name: str, value: object) -> None:
        """Copy one evidenced boolean into canonical values when present."""
        if isinstance(value, bool):
            values[name] = value
