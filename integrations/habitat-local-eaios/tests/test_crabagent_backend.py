"""Deterministic tests for the deployment-selected CrabAgent Local How backend."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.crabagent_backend import (  # noqa: E402
    START_ACTION_PROMPT,
    STEP_ACTION_PROMPT,
    CrabAgentBackendConfig,
    CrabAgentMobilityBackend,
)
from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402


def _config(tmp_path: Path, subtask_mode: str = "natural-objective") -> CrabAgentBackendConfig:
    """Build one deterministic backend configuration for tests."""
    return CrabAgentBackendConfig(
        config_path=Path("habitat.yaml"),
        episode_id="51",
        agent_id=0,
        max_steps=100,
        step_period_ms=0,
        subtask_mode=subtask_mode,
        robot_type="SpotRobot",
        evidence_dir=tmp_path / "evidence",
    )


def _invocation(
    objective: str = "Navigate the Habitat robot to the target.",
) -> CanonicalMobilityInvocation:
    """Build one canonical navigation invocation."""
    return CanonicalMobilityInvocation(
        mission_id="mission-c1-s0b",
        task_id="navigate-target",
        group_id="group-c1-s0b",
        role_id="navigator",
        operation="mobility.navigate@v1",
        objective=objective,
        parameters={"destination": "any_targets|0"},
        resource_ids=("habitat-navigation-slot",),
    )


class ScriptedCrabAgentBackend(CrabAgentMobilityBackend):
    """Backend fixture that scripts LLM decisions and fakes the Habitat stepping."""

    def __init__(self, config: CrabAgentBackendConfig, decisions: list[object]) -> None:
        """Preload the scripted local-agent decisions for the loop."""
        super().__init__(config)
        self._decisions = list(decisions)
        self.dispatched: list[object] = []
        self.waits: list[int] = []
        self.finished: list[str] = []
        self._steps = 0

    def _build_crab_agent(self):  # noqa: ANN202 - test fixture avoids EMOS imports
        """Return a dummy agent handle; decisions come from the script."""
        return object()

    def _initialize_agent(self, crab_agent, invocation):  # noqa: ANN001, ANN202
        """Record initialization instead of calling the real EMOS entry."""
        self.initialized_with = invocation

    def _message_pipe_snapshot(self) -> list[str]:
        """Fake the class-level pipe without importing EMOS."""
        return []

    def _require_initialized(self):  # noqa: ANN202 - test fixture signature
        """Return a fake env triplet without Habitat imports."""
        fake_env = types.SimpleNamespace(
            episodes=None,
            reset=lambda: None,
            current_episode=types.SimpleNamespace(scene_id="scene.glb"),
            episode_over=False,
        )
        return fake_env, object(), object()

    def _chat_once(self, crab_agent, prompt):  # noqa: ANN001, ANN202
        """Pop one scripted decision with deterministic accounting."""
        assert isinstance(prompt, str) and prompt
        if not self._decisions:
            return None, 10, 1.0
        decision = self._decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return decision, 10, 1.0

    def _step_wait(self, numpy, count: int) -> int:  # noqa: ANN001
        """Consume scripted wait steps without a simulator."""
        count = max(1, min(count, self._config.max_steps - self._steps))
        self.waits.append(count)
        self._steps += count
        return count

    def _dispatch_nav(  # noqa: ANN001, ANN202
        self,
        numpy,
        arguments,
        steps_used,
        finished_key,
        cancellation_requested,
    ):
        """Record the nav selection and return the scripted step/finish/cancel tuple."""
        self.dispatched.append(arguments)
        self._steps += 30
        return 30, True, False

    def _scene_description(self) -> str:
        """Return a deterministic EMOS-shaped scene description."""
        return "scene"

    def _agent_position(self):  # noqa: ANN202
        """Return a deterministic position pair."""
        return (1.0, 2.0, 3.0)

    def _finish(self, state, detail, destination, scene_id, steps, initial):  # noqa: ANN001, ANN202
        """Record terminal states without touching the parent evidence writer."""
        self.finished.append(state)
        return LocalExecutionOutcome(
            state=state,
            detail=detail,
            episode_id=self._config.episode_id,
            scene_id=scene_id,
            destination=destination,
            simulator_steps=steps,
            initial_position=initial,
            final_position=initial,
        )


def _backend(tmp_path: Path, decisions: list[object], mode: str = "natural-objective"):
    """Build one scripted backend fixture."""
    return ScriptedCrabAgentBackend(_config(tmp_path, mode), decisions)


def test_subtask_mode_natural_objective_uses_objective_text(tmp_path: Path) -> None:
    """The Protocol B mode must feed the exact objective text to the local agent."""
    backend = CrabAgentMobilityBackend(_config(tmp_path))
    objective = "使用 pointcloud 之外的语义：移动到园区大门。"
    assert backend._subtask(_invocation(objective)) == objective


def test_subtask_mode_entity_grounded_uses_destination(tmp_path: Path) -> None:
    """The ablation mode must ground the subtask on the semantic destination."""
    backend = CrabAgentMobilityBackend(_config(tmp_path, "entity-grounded"))
    assert backend._subtask(_invocation()) == "Navigate to any_targets|0."


def test_invalid_subtask_mode_is_rejected(tmp_path: Path) -> None:
    """Deployment configuration must fail closed on unknown subtask modes."""
    with pytest.raises(IntegrationError, match="subtask mode"):
        _config(tmp_path, "whatever")


def test_valid_nav_selection_dispatches_and_completes(tmp_path: Path) -> None:
    """A valid nav_to_obj function call must dispatch and produce local COMPLETED."""
    backend = _backend(
        tmp_path,
        [{"name": "nav_to_obj", "arguments": {"target_obj": "any_targets|0", "robot": "agent_0"}}],
    )
    outcome = backend.execute(_invocation(), lambda: False, lambda detail: None)
    assert outcome.state == "COMPLETED"
    assert backend.dispatched == [{"target_obj": "any_targets|0", "robot": "agent_0"}]


def test_unsupported_skill_fails_closed(tmp_path: Path) -> None:
    """Pick/place-style selections must fail closed without silent Oracle fallback."""
    backend = _backend(tmp_path, [{"name": "pick", "arguments": {"target_obj": "x"}}])
    outcome = backend.execute(_invocation(), lambda: False, lambda detail: None)
    assert outcome.state == "FAILED"
    assert "unsupported skill" in outcome.detail
    assert backend.dispatched == []


def test_consecutive_invalid_outputs_exhaust_and_fail(tmp_path: Path) -> None:
    """More than the bounded invalid budget must fail with an explicit reason."""
    backend = _backend(tmp_path, [None] * 7)
    outcome = backend.execute(_invocation(), lambda: False, lambda detail: None)
    assert outcome.state == "FAILED"
    assert "invalid local agent outputs" in outcome.detail
    assert len(backend.waits) == 5


def test_wait_selection_consumes_bounded_steps(tmp_path: Path) -> None:
    """A wait selection must consume its parsed step count before the next decision."""
    backend = _backend(
        tmp_path,
        [
            {"name": "wait", "arguments": ["50"]},
            {"name": "nav_to_obj", "arguments": {"target_obj": "any_targets|0"}},
        ],
    )
    outcome = backend.execute(_invocation(), lambda: False, lambda detail: None)
    assert outcome.state == "COMPLETED"
    assert backend.waits == [50]
    assert sum(backend.waits) + 30 < backend._config.max_steps


def test_action_trace_records_stage_and_accounting(tmp_path: Path) -> None:
    """Every local LLM decision must land in the trace with stage and token deltas."""
    backend = _backend(
        tmp_path,
        [{"name": "nav_to_obj", "arguments": {"target_obj": "any_targets|0"}}],
    )
    backend.execute(_invocation(), lambda: False, lambda detail: None)
    records = [
        json.loads(line)
        for line in (tmp_path / "evidence" / "action_trace.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[0]["stage"] == "local-agent"
    assert records[0]["token_delta"] == 10
    assert records[0]["latency_ms"] == 1.0
    assert records[0]["call_index"] == 1


def test_prompt_templates_match_emos_shapes(tmp_path: Path) -> None:
    """The start prompt must carry the EMOS scene slot; the step prompt must not."""
    assert "{scene_description}" in START_ACTION_PROMPT
    assert "{scene_description}" not in STEP_ACTION_PROMPT


def test_build_and_initialize_are_overridable(tmp_path: Path) -> None:
    """Scripted fixtures must bypass real EMOS imports via the two hooks."""
    backend = _backend(tmp_path, [])
    assert backend.dispatched == []
