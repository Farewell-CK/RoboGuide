"""Deterministic tests for the original-EMOS Stage2 backend boundary."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.crabagent_backend import (  # noqa: E402
    CrabAgentBackendConfig,
    CrabAgentMobilityBackend,
)
from habitat_local_eaios.emos_stage2 import EmosStage2Runtime  # noqa: E402
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalMobilityInvocation,
    IntegrationError,
)


def _config(tmp_path: Path, subtask_mode: str = "natural-objective") -> CrabAgentBackendConfig:
    """Build one deterministic deployment config for boundary tests."""
    return CrabAgentBackendConfig(
        config_path=Path("habitat.yaml"),
        episode_id="51",
        agent_id=0,
        max_steps=100,
        step_period_ms=0,
        subtask_mode=subtask_mode,
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


class FakeStage2Runtime:
    """Records wrapper calls without importing EMOS or Habitat."""

    instances: list[FakeStage2Runtime] = []

    def __init__(self, config: CrabAgentBackendConfig) -> None:
        """Retain config and expose deterministic lifecycle counters."""
        self.config = config
        self.initialized = False
        self.closed = False
        self.executions: list[CanonicalMobilityInvocation] = []
        type(self).instances.append(self)

    def initialize(self) -> None:
        """Record persistent policy initialization."""
        self.initialized = True

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Any,
        running: Any,
    ) -> LocalExecutionOutcome:
        """Record one invocation and return separated local/benchmark evidence."""
        del cancellation_requested
        self.executions.append(invocation)
        running("official Stage2 running")
        return LocalExecutionOutcome(
            state="COMPLETED",
            detail="official skill completed",
            episode_id="51",
            scene_id="scene.glb",
            destination=invocation.destination,
            simulator_steps=7,
            initial_position=(0.0, 0.0, 0.0),
            final_position=(1.0, 0.0, 1.0),
            local_skill_completed=True,
            benchmark_task_achieved=False,
            episode_terminated=False,
            skill_sequence=("nav_to_obj|wait",),
            local_llm_calls=2,
            local_tokens=42,
            terminal_basis="oracle-nav-skill",
        )

    def readiness_detail(self) -> str:
        """Return deterministic readiness evidence."""
        return "official Stage2 ready"

    def close(self) -> None:
        """Record policy-environment cleanup."""
        self.closed = True


class FakeAgentArguments:
    """Captures the fields supplied at EMOS' Stage1-to-Stage2 boundary."""

    def __init__(self, **values: Any) -> None:
        """Retain named arguments exactly as supplied by the adapter."""
        self.values = values


def test_wrapper_delegates_to_persistent_original_stage2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend must not implement its own decision or skill loop."""
    FakeStage2Runtime.instances.clear()
    monkeypatch.setattr(CrabAgentMobilityBackend, "_runtime_class", FakeStage2Runtime)
    backend = CrabAgentMobilityBackend(_config(tmp_path))
    backend.initialize()
    observed: list[str] = []
    outcome = backend.execute(_invocation(), lambda: False, observed.append)
    runtime = FakeStage2Runtime.instances[-1]
    assert runtime.initialized
    assert runtime.executions == [_invocation()]
    assert observed == ["official Stage2 running"]
    assert outcome.local_skill_completed
    assert not outcome.benchmark_task_achieved
    assert backend.readiness_detail() == "official Stage2 ready"
    backend.close()
    assert runtime.closed


def test_subtask_modes_only_change_assignment_text(tmp_path: Path) -> None:
    """Natural and entity-grounded modes must not select a different skill path."""
    objective = "使用语义目标移动到园区大门。"
    assert CrabAgentMobilityBackend(_config(tmp_path))._subtask(_invocation(objective)) == objective
    assert (
        CrabAgentMobilityBackend(_config(tmp_path, "entity-grounded"))._subtask(_invocation())
        == "Navigate to any_targets|0."
    )


def test_invalid_subtask_mode_is_rejected(tmp_path: Path) -> None:
    """Unknown assignment wording modes fail before simulator initialization."""
    with pytest.raises(IntegrationError, match="subtask mode"):
        _config(tmp_path, "whatever")


def test_committed_assignment_replaces_only_stage1_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control assigns one agent while the original Stage2 receives all agent slots."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)
    runtime = EmosStage2Runtime(_config(tmp_path))
    context = {
        "robot_resume": json.dumps(
            {
                "agent_0": {"robot_type": "SpotRobot"},
                "agent_1": {"robot_type": "FetchRobot"},
            }
        )
    }
    assigned = runtime._assigned_arguments(context, _invocation())
    assert assigned["agent_0"].values["subtask_description"] == _invocation().objective
    assert assigned["agent_1"].values["subtask_description"] == "Nothing to do"
    assert assigned["agent_0"].values["task_description"] == _invocation().objective


def test_malformed_robot_resume_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assignment injection must not guess missing EMOS robot identities."""
    utils = types.ModuleType("habitat_mas.utils")
    utils.AgentArguments = FakeAgentArguments  # type: ignore[attr-defined]
    package = types.ModuleType("habitat_mas")
    package.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas", package)
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", utils)
    runtime = EmosStage2Runtime(_config(tmp_path))
    with pytest.raises(IntegrationError, match="valid JSON"):
        runtime._assigned_arguments({"robot_resume": "{"}, _invocation())


def test_gym_step_result_preserves_episode_terminal_semantics(tmp_path: Path) -> None:
    """Gym API normalization must keep info and terminal evidence independent."""
    runtime = EmosStage2Runtime(_config(tmp_path))
    observation, done, info = runtime._gym_step_result(({"x": 1}, 0.0, True, {"pddl_success": 0}))
    assert observation == {"x": 1}
    assert done
    assert info == {"pddl_success": 0}
    _, done, _ = runtime._gym_step_result(({}, 0.0, False, True, {}))
    assert done


def test_oracle_finish_reads_local_action_authority(tmp_path: Path) -> None:
    """The adapter reads the action state that backs Habitat's finish sensor."""
    runtime = EmosStage2Runtime(_config(tmp_path))
    action = types.SimpleNamespace(skill_done=True)
    runtime._habitat_env = types.SimpleNamespace(
        task=types.SimpleNamespace(actions={"agent_0_oracle_nav_action": action})
    )
    assert runtime._oracle_nav_finished()
    action.skill_done = False
    assert not runtime._oracle_nav_finished()


def test_local_outcome_keeps_success_layers_distinct() -> None:
    """Local completion must not fabricate benchmark or episode success."""
    outcome = FakeStage2Runtime(_config(Path("."))).execute(
        _invocation(), lambda: False, lambda detail: None
    )
    encoded = outcome.as_dict()
    assert encoded["state"] == "COMPLETED"
    assert encoded["local_skill_completed"] is True
    assert encoded["benchmark_task_achieved"] is False
    assert encoded["episode_terminated"] is False
    assert encoded["local_llm_calls"] == 2
    assert encoded["local_tokens"] == 42
    assert encoded["terminal_basis"] == "oracle-nav-skill"


def test_wrapper_source_has_no_reimplemented_policy_loop() -> None:
    """The deployment wrapper must not regain copied prompts or retry policy."""
    source = (INTEGRATION_ROOT / "habitat_local_eaios" / "crabagent_backend.py").read_text(
        encoding="utf-8"
    )
    assert "START_ACTION_PROMPT" not in source
    assert "MAX_CONSECUTIVE_INVALID_OUTPUTS" not in source
    assert "_dispatch_nav" not in source
    assert "_step_wait" not in source
