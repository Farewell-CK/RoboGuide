"""Deterministic tests for the shared-world coordinator and node endpoints."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.backend import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.diagnostics import BufferedJsonlWriter  # noqa: E402
from habitat_local_eaios.emos_stage2 import EmosStage2Runtime  # noqa: E402
from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.shared_world import (  # noqa: E402
    InProcessWorldService,
    NodeEndpoint,
    SharedEmosStage2Runtime,
    SharedWorldCoordinator,
)
from habitat_local_eaios.stage2_contract import Stage2ExecutionContract  # noqa: E402
from habitat_local_eaios.store import ExecutionStore  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class FakeTensor:
    """Minimal tensor-shaped value for policy-loop failure tests."""

    def detach(self) -> FakeTensor:
        """Return the same fake detached value."""
        return self

    def cpu(self) -> FakeTensor:
        """Return the same fake CPU value."""
        return self

    def __getitem__(self, index: object) -> FakeTensor:
        """Ignore indexing while retaining the tensor-shaped facade."""
        del index
        return self

    def numpy(self) -> list[float]:
        """Expose one deterministic environment action."""
        return [0.0, 0.0]

    def copy_(self, value: object) -> FakeTensor:
        """Accept recurrent action updates without retaining state."""
        del value
        return self

    def repeat(self, *shape: int) -> FakeTensor:
        """Accept mask repetition without retaining its shape."""
        del shape
        return self


class FakeTorch:
    """Minimal torch facade used before the injected execution failure."""

    long = object()
    float = object()
    bool = object()

    @staticmethod
    def zeros(*shape: object, **options: object) -> FakeTensor:
        """Return one fake zero tensor."""
        del shape, options
        return FakeTensor()

    @staticmethod
    def tensor(value: object, **options: object) -> FakeTensor:
        """Return one fake tensor for a supplied mask."""
        del value, options
        return FakeTensor()


class RecordingDiagnostics:
    """Record the evidence boundary and emulate terminal buffer persistence."""

    def __init__(self, *, fail_terminal: bool = False) -> None:
        """Create an empty recorder with an optional terminal-write failure."""
        self.fail_terminal = fail_terminal
        self.pending_steps: list[int] = []
        self.persisted_steps: list[int] = []
        self.terminals: list[tuple[int, str]] = []
        self.reset_calls = 0

    def record_reset(self, habitat_env: object, config: object) -> None:
        """Count one reset observation without reading the fake environment."""
        del habitat_env, config
        self.reset_calls += 1

    def record_step(self, step: int, *args: object, **kwargs: object) -> None:
        """Buffer one successful pre-failure simulator step."""
        del args, kwargs
        self.pending_steps.append(step)

    def record_terminal(self, habitat_env: object, steps: int, reason: str) -> None:
        """Persist pending steps, then optionally emulate a storage regression."""
        del habitat_env
        self.persisted_steps.extend(self.pending_steps)
        self.pending_steps.clear()
        self.terminals.append((steps, reason))
        if self.fail_terminal:
            raise OSError("diagnostic storage unavailable")

    def flush_boundary(self) -> None:
        """Persist pending segment records before the next Task starts."""
        self.persisted_steps.extend(self.pending_steps)
        self.pending_steps.clear()


class PolicyActor:
    """Return a stable action or raise at the requested call."""

    policy_action_space = object()
    hidden_state_shape = (1,)
    hidden_state_shape_lens = (1,)
    policy_action_space_shape_lens = (1,)

    def __init__(self, fail_at: int | None = None) -> None:
        """Configure the one-indexed actor call that raises."""
        self.fail_at = fail_at
        self.calls = 0

    def act(self, *args: object, **kwargs: object) -> object:
        """Return minimal action data unless this call is the injected failure."""
        del args, kwargs
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("actor failure sentinel")
        return SimpleNamespace(
            actions=FakeTensor(),
            env_actions=FakeTensor(),
            rnn_hidden_states=FakeTensor(),
            should_inserts=None,
        )


class StepEnvironment:
    """Return nonterminal observations until the configured step raises."""

    def __init__(self, fail_at: int) -> None:
        """Configure the one-indexed Gym step that raises."""
        self.fail_at = fail_at
        self.calls = 0

    def step(self, action: object) -> tuple[dict[str, list[int]], float, bool, dict[str, bool]]:
        """Return one valid Gym tuple or the injected original exception."""
        del action
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("gym step failure sentinel")
        return (
            {
                "agent_0_has_finished_oracle_nav": [0],
                "agent_1_has_finished_oracle_nav": [0],
            },
            0.0,
            False,
            {"pddl_success": False},
        )


class LoopHarness(SharedEmosStage2Runtime):
    """Exercise the real shared policy loop without importing Habitat or Torch."""

    def __init__(self, tmp_path: Path, diagnostics: RecordingDiagnostics) -> None:
        """Install deterministic facades around the production loop."""
        self._agent_ids = (0, 1)
        self._config = SimpleNamespace(max_steps=3, step_period_ms=0)
        self._runtime = {
            "device": "cpu",
            "get_action_space_info": lambda unused: ((1,), False),
            "torch": FakeTorch,
        }
        self._diagnostics = cast(Any, diagnostics)
        self._test_evidence_dir = tmp_path / "evidence"
        self._action_trace_writer = BufferedJsonlWriter(
            self._test_evidence_dir / "action_trace.jsonl"
        )
        self.action_trace_flushes = 0
        self.trace_steps: list[int] = []

    def _batch(self, observations: Any) -> Any:
        """Keep observations unchanged in the failure harness."""
        return observations

    def _agent_position_for(self, agent_id: int) -> tuple[float, float, float]:
        """Return one deterministic initial agent position."""
        return (float(agent_id), 0.0, 0.0)

    def _evidence_dir(self) -> Path:
        """Return the isolated test evidence directory."""
        return self._test_evidence_dir

    def _install_assignment(self, assignment: dict[str, Any]) -> tuple[Any, Any]:
        """Install a mutable module facade for restoration checks."""
        del assignment
        return SimpleNamespace(group_discussion="installed"), "original"

    def _install_execution_contract(
        self, contracts: dict[str, Any], completed_steps: Callable[[], int]
    ) -> Callable[[], None]:
        """Skip the vendor import while exercising the loop with fake actors."""
        del contracts, completed_steps
        return lambda: None

    def _current_skills(self, actor: Any) -> list[str]:
        """Expose two active navigation skills without reading actor internals."""
        del actor
        return ["nav_to_obj", "nav_to_obj"]

    def _oracle_nav_finished_for(self, agent_id: int) -> bool:
        """Keep both fake navigation skills nonterminal."""
        del agent_id
        return False

    def _append_action_trace(self, steps: int, skills: list[str], info: dict[str, Any]) -> None:
        """Retain the simulator step identity without file I/O."""
        del skills, info
        self.trace_steps.append(steps)

    def _flush_action_trace(self) -> None:
        """Count the action-trace flush performed at every exit."""
        self.action_trace_flushes += 1


class SerialGym:
    """Count actual reset and step calls across two local Task segments."""

    def __init__(self) -> None:
        """Start an unreset deterministic world."""
        self.resets = 0
        self.steps = 0

    def reset(self) -> dict[str, int]:
        """Return the first observation from the only episode reset."""
        self.resets += 1
        return {"step": 0}

    def step(self, action: object) -> tuple[dict[str, int], float, bool, dict[str, bool]]:
        """Advance exactly one shared-world step for either segment."""
        del action
        self.steps += 1
        return {"step": self.steps}, 0.0, False, {"pddl_success": self.steps == 2}


class SerialRuntimeHarness(SharedEmosStage2Runtime):
    """Drive production serial session setup with a deterministic policy boundary."""

    def __init__(self, tmp_path: Path) -> None:
        """Install a fake world and retain real serial session lifecycle methods."""
        super().__init__(
            CrabAgentBackendConfig(
                config_path=tmp_path / "unused.yaml",
                episode_id="3",
                agent_id=0,
                max_steps=10,
                step_period_ms=0,
                evidence_dir=tmp_path,
            ),
            (0, 1),
        )
        self._gym_env = SerialGym()
        agent_data = lambda unused: SimpleNamespace(  # noqa: E731 - compact fake simulator accessor
            articulated_agent=SimpleNamespace(base_pos=(0.0, 0.0, 0.0))
        )
        self._habitat_env = SimpleNamespace(
            episodes=[],
            current_episode=SimpleNamespace(episode_id="3", scene_id="scene"),
            episode_over=False,
            task=SimpleNamespace(get_task_text_context=lambda: {"scene_description": "scene"}),
            sim=SimpleNamespace(get_agent_data=agent_data),
            get_metrics=lambda: {"pddl_success": self._gym_env.steps == 2},
        )
        self._episode = object()
        self._actor = object()
        self._agent_access = object()
        self._diagnostics = cast(Any, RecordingDiagnostics())
        self.observation_inputs: list[int] = []

    def _assigned_arguments(
        self, text_context: dict[str, Any], invocation: CanonicalMobilityInvocation
    ) -> dict[str, Any]:
        """Avoid importing the vendor AgentArguments class in this offline test."""
        del text_context, invocation
        return {"agent_0": object(), "agent_1": object()}

    def _policy_loop(
        self,
        observations: Any,
        text_context: dict[str, Any],
        assignment: dict[str, Any],
        invocation: CanonicalMobilityInvocation,
        initial: tuple[float, float, float],
        scene_id: str,
        actor: Any,
        access: Any,
        gym_env: Any,
        habitat_env: Any,
        cancellation_requested: Callable[[], bool],
    ) -> LocalExecutionOutcome:
        """Observe the retained input, step once, and emit a local Task result."""
        del text_context, assignment, actor, access, habitat_env, cancellation_requested
        self.observation_inputs.append(int(observations["step"]))
        next_observations, done, info = self._gym_step_result(gym_env.step(None))
        self._last_policy_observations = next_observations
        self._observe_policy_step(
            1,
            ["nav_to_obj", "wait"],
            None,
            self._habitat_env,
            None,
            done,
            info,
            next_observations,
            observations,
        )
        return LocalExecutionOutcome(
            state="COMPLETED",
            detail="fake local navigation completed",
            episode_id="3",
            scene_id=scene_id,
            destination=invocation.destination,
            simulator_steps=1,
            initial_position=initial,
            final_position=initial,
            local_skill_completed=True,
        )


def _run_failing_loop(runtime: LoopHarness, actor: PolicyActor, gym_env: StepEnvironment) -> None:
    """Invoke the production pair loop with deterministic nonterminal inputs."""
    habitat_env = SimpleNamespace(
        current_episode=SimpleNamespace(scene_id="scene"),
        episode_over=False,
    )
    runtime._pair_loop(
        {
            "agent_0_has_finished_oracle_nav": [0],
            "agent_1_has_finished_oracle_nav": [0],
        },
        {"episode_id": "51"},
        {},
        {},
        actor,
        SimpleNamespace(masks_shape=(1,)),
        gym_env,
        habitat_env,
        lambda: False,
        lambda agent_id, detail: None,
    )


def test_actor_exception_flushes_terminal_diagnostics_without_masking_error(
    tmp_path: Path,
) -> None:
    """An actor failure preserves its identity after best-effort terminal capture."""
    diagnostics = RecordingDiagnostics(fail_terminal=True)
    runtime = LoopHarness(tmp_path, diagnostics)
    with pytest.raises(RuntimeError, match="actor failure sentinel"):
        _run_failing_loop(runtime, PolicyActor(fail_at=1), StepEnvironment(fail_at=3))
    assert diagnostics.terminals == [(0, "execution_exception:actor_act:RuntimeError")]
    assert runtime.action_trace_flushes == 1


def test_gym_exception_flushes_prior_steps_and_reads_terminal_state(tmp_path: Path) -> None:
    """A Gym failure retains all successful pre-failure rows and its exact phase."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)
    with pytest.raises(RuntimeError, match="gym step failure sentinel"):
        _run_failing_loop(runtime, PolicyActor(), StepEnvironment(fail_at=2))
    assert diagnostics.persisted_steps == [1]
    assert diagnostics.terminals == [(1, "execution_exception:gym_env_step:RuntimeError")]
    assert runtime.action_trace_flushes == 1


def test_serial_policy_trace_continues_global_step_numbers(tmp_path: Path) -> None:
    """A later local Task cannot make step one appear to be a second reset."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._serial_steps = 5
    runtime._config = SimpleNamespace(max_steps=3, step_period_ms=0, episode_id="3", agent_id=0)
    habitat_env = SimpleNamespace(episode_over=False)
    runtime._habitat_env = habitat_env
    invocation = CanonicalMobilityInvocation.from_request(_request("mission", "target", "task"))
    with pytest.raises(RuntimeError, match="actor failure sentinel"):
        runtime._policy_loop(
            {"agent_0_has_finished_oracle_nav": [0]},
            {"episode_id": "3"},
            {"agent_0": object(), "agent_1": object()},
            invocation,
            (0.0, 0.0, 0.0),
            "scene",
            PolicyActor(fail_at=2),
            SimpleNamespace(masks_shape=(1,)),
            StepEnvironment(fail_at=3),
            habitat_env,
            lambda: False,
        )
    assert runtime.trace_steps == [6]
    assert runtime._serial_steps == 6


def test_serial_policy_flush_failure_preserves_original_actor_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed trace flush cannot replace the original serial Stage2 failure."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._serial_steps = 2
    runtime._config = SimpleNamespace(max_steps=3, step_period_ms=0, episode_id="3", agent_id=0)
    habitat_env = SimpleNamespace(episode_over=False)
    runtime._habitat_env = habitat_env

    def fail_flush() -> None:
        """Simulate a writer regression after the physical policy has failed."""
        raise OSError("trace flush failure sentinel")

    monkeypatch.setattr(runtime, "_flush_action_trace", fail_flush)
    invocation = CanonicalMobilityInvocation.from_request(_request("mission", "target", "task"))
    with pytest.raises(RuntimeError, match="actor failure sentinel"):
        runtime._policy_loop(
            {"agent_0_has_finished_oracle_nav": [0]},
            {"episode_id": "3"},
            {"agent_0": object(), "agent_1": object()},
            invocation,
            (0.0, 0.0, 0.0),
            "scene",
            PolicyActor(fail_at=2),
            SimpleNamespace(masks_shape=(1,)),
            StepEnvironment(fail_at=3),
            habitat_env,
            lambda: False,
        )
    assert runtime._serial_steps == 3
    assert runtime.trace_steps == [3]


def test_action_trace_flush_failure_preserves_actor_error_and_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected trace flush failure cannot replace the physical failure."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)

    def fail_flush() -> None:
        """Emulate a writer regression outside its ordinary fail-soft path."""
        raise OSError("action trace flush failure sentinel")

    monkeypatch.setattr(runtime, "_flush_action_trace", fail_flush)
    with pytest.raises(RuntimeError, match="actor failure sentinel"):
        _run_failing_loop(runtime, PolicyActor(fail_at=2), StepEnvironment(fail_at=3))
    assert diagnostics.persisted_steps == [1]
    assert diagnostics.terminals == [(1, "execution_exception:actor_act:RuntimeError")]


def test_successful_outcome_survives_action_trace_flush_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal trace writer failure cannot turn local completion into failure."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config = SimpleNamespace(episode_id="3", agent_id=0)
    runtime._actor = None
    runtime._habitat_env = SimpleNamespace(
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    runtime._agent_position = lambda: (1.0, 0.0, 2.0)  # type: ignore[method-assign]

    def fail_flush() -> None:
        """Inject the evidence-only failure after the local skill completed."""
        raise OSError("successful trace flush failure sentinel")

    monkeypatch.setattr(runtime, "_flush_action_trace", fail_flush)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "target", "task"))
    outcome = runtime._outcome(
        "COMPLETED",
        "local completion",
        invocation,
        "scene",
        3,
        (0.0, 0.0, 0.0),
        [],
        local_skill_completed=True,
        terminal_basis="oracle-nav-skill",
    )
    assert outcome.state == "COMPLETED"
    assert outcome.terminal_basis == "oracle-nav-skill"


def test_successful_outcome_survives_controlled_artifact_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A controlled-outcome snapshot failure cannot rewrite the physical result."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config = SimpleNamespace(episode_id="3", agent_id=0)
    runtime._actor = None
    runtime._habitat_env = SimpleNamespace(
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    runtime._agent_position = lambda: (1.0, 0.0, 2.0)  # type: ignore[method-assign]

    def fail_write(name: str, value: object) -> None:
        """Inject a failure only for the optional terminal sidecar."""
        del value
        if name == "controlled-outcome.json":
            raise OSError("controlled outcome write failure sentinel")

    monkeypatch.setattr(runtime, "_write_json", fail_write)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "target", "task"))
    outcome = runtime._outcome(
        "COMPLETED",
        "local completion",
        invocation,
        "scene",
        3,
        (0.0, 0.0, 0.0),
        [],
        local_skill_completed=True,
        terminal_basis="oracle-nav-skill",
    )
    assert outcome.state == "COMPLETED"


def test_serial_controlled_outcome_write_failure_does_not_change_terminal_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A serial controlled-outcome write failure remains observational."""
    runtime = SerialRuntimeHarness(tmp_path)

    def fail_write(name: str, value: object) -> None:
        """Inject one terminal artifact storage failure."""
        del value
        if name.startswith("controlled-outcome-"):
            raise OSError("controlled outcome write failure sentinel")

    monkeypatch.setattr(runtime, "_write_json", fail_write)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "target", "task"))
    outcome, _ = runtime.execute_serial(
        invocation,
        0,
        lambda: False,
        lambda agent_id, detail: None,
        True,
    )
    assert outcome.state == "COMPLETED"


def test_video_close_failure_does_not_mask_gym_failure(tmp_path: Path) -> None:
    """Optional video finalization cannot change the original Gym failure."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)

    class BrokenVideo:
        """Emulate a recorder that fails while closing after a Gym exception."""

        enabled = False

        def close(self, reason: str) -> None:
            """Raise after retaining the termination reason as an input."""
            assert reason == "execution_exception:gym_env_step:RuntimeError"
            raise OSError("video close failure sentinel")

    runtime._video = cast(Any, BrokenVideo())
    with pytest.raises(RuntimeError, match="gym step failure sentinel"):
        _run_failing_loop(runtime, PolicyActor(), StepEnvironment(fail_at=2))
    assert diagnostics.terminals == [(1, "execution_exception:gym_env_step:RuntimeError")]


def test_post_reset_setup_exception_records_terminal_evidence(tmp_path: Path) -> None:
    """A failure after reset but before the policy loop still closes diagnostics."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)
    habitat_env = SimpleNamespace(
        episodes=[],
        task=SimpleNamespace(
            get_task_text_context=lambda: (_ for _ in ()).throw(
                RuntimeError("task context failure sentinel")
            )
        ),
    )
    gym_env = SimpleNamespace(reset=lambda: {})
    runtime._episode = object()
    runtime._config = SimpleNamespace(episode_id="51", seed=40, max_steps=3, step_period_ms=0)
    runtime._require_initialized = lambda: (  # type: ignore[method-assign]
        gym_env,
        habitat_env,
        PolicyActor(),
        SimpleNamespace(masks_shape=(1,)),
    )
    with pytest.raises(IntegrationError, match="task context failure sentinel"):
        runtime.execute_pair({}, lambda: False, lambda agent_id, detail: None)
    assert diagnostics.reset_calls == 1
    assert diagnostics.terminals == [(0, "execution_exception:task_context")]


class ContractModel:
    """Return a correct navigation followed by a configurable target violation."""

    def __init__(
        self, target: str, violate_at: int | None, extra_tools_at: int | None = None
    ) -> None:
        """Retain selected-action and raw-response faults for the joint-loop test."""
        self.target = target
        self.violate_at = violate_at
        self.extra_tools_at = extra_tools_at
        self.calls = 0
        self.model = "offline-model"
        self.chat_history: list[list[dict[str, Any]]] = []

    def chat(self, observation: str, crab_planning: bool = False) -> Any:
        """Return one raw selected tool without changing the fake simulator."""
        del observation, crab_planning
        self.calls += 1
        target = "wrong-target" if self.calls == self.violate_at else self.target
        self.chat_history.append(
            [
                {"role": "user", "content": "observation"},
                {
                    "role": "assistant",
                    "tool_calls": [{"name": "nav_to_obj"}]
                    * (2 if self.calls == self.extra_tools_at else 1),
                },
                {"role": "tool", "content": "first action only"},
            ]
        )
        return "nav_to_obj", {"target_obj": target}


class ContractAgent:
    """Expose the instance hook and track dispatches after raw model selection."""

    def __init__(self, name: str, model: ContractModel) -> None:
        """Install the scripted model behind a vendor-shaped agent."""
        self.name = name
        self.llm_model = model
        self.dispatches = 0

    def chat(self, observation: str) -> Any:
        """Dispatch only after the raw model call has returned through the guard."""
        result = self.llm_model.chat(observation)
        self.dispatches += 1
        return result


class ContractActor(PolicyActor):
    """Run guarded agent selection from the real pair loop's actor boundary."""

    def __init__(self, agents: list[ContractAgent]) -> None:
        """Expose the same active-policy agent lookup as the EMOS actor."""
        super().__init__()
        self._active_policies = [
            SimpleNamespace(_high_level_policy=SimpleNamespace(llm_agent=agent)) for agent in agents
        ]

    def act(self, *args: object, **kwargs: object) -> object:
        """Select actions before permitting the policy loop to step Gym."""
        for policy in self._active_policies:
            policy._high_level_policy.llm_agent.chat("observation")
        return super().act(*args, **kwargs)


class ContractLoopHarness(LoopHarness):
    """Use production contract installation and outcome reduction with fake physics."""

    def _install_execution_contract(
        self, contracts: dict[str, Stage2ExecutionContract], completed_steps: Callable[[], int]
    ) -> Callable[[], None]:
        """Exercise real instance hooks, audit output, and restoration."""
        return EmosStage2Runtime._install_execution_contract(self, contracts, completed_steps)


@pytest.mark.parametrize("completed_first", [False, True])
@pytest.mark.parametrize("violation", ["wrong-target", "multiple-tools"])
def test_contract_violation_stops_before_gym_and_preserves_prior_completion(
    tmp_path: Path, completed_first: bool, violation: str
) -> None:
    """The real pair loop reports a local violation without erasing completed work."""
    diagnostics = RecordingDiagnostics()
    runtime = ContractLoopHarness(tmp_path, diagnostics)
    runtime._config = SimpleNamespace(max_steps=3, step_period_ms=0, episode_id="generic")
    agents = [
        ContractAgent("agent_0", ContractModel("north", None)),
        ContractAgent(
            "agent_1",
            ContractModel(
                "south",
                (2 if completed_first else 1) if violation == "wrong-target" else None,
                (2 if completed_first else 1) if violation == "multiple-tools" else None,
            ),
        ),
    ]
    actor = ContractActor(agents)
    runtime._actor = actor
    habitat_env = SimpleNamespace(
        current_episode=SimpleNamespace(scene_id="scene"),
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    runtime._habitat_env = habitat_env
    gym_calls: list[object] = []

    def gym_step(action: object) -> Any:
        """Observe one legitimate step and optionally complete the first agent."""
        gym_calls.append(action)
        return (
            {
                "agent_0_has_finished_oracle_nav": [int(completed_first)],
                "agent_1_has_finished_oracle_nav": [0],
            },
            0.0,
            False,
            {},
        )

    invocations = {
        index: CanonicalMobilityInvocation.from_request(_request("m", target, f"t{index}"))
        for index, target in enumerate(["north", "south"])
    }
    outcomes, steps, done, _ = runtime._pair_loop(
        {},
        {"episode_id": "generic"},
        {},
        invocations,
        actor,
        SimpleNamespace(masks_shape=(1,)),
        SimpleNamespace(step=gym_step),
        habitat_env,
        lambda: False,
        lambda agent_id, detail: None,
    )
    assert steps == len(gym_calls) == int(completed_first)
    assert done is False
    assert outcomes[1].state == "FAILED"
    assert outcomes[1].terminal_basis == "local-contract-failure"
    assert outcomes[0].state == ("COMPLETED" if completed_first else "FAILED")
    assert outcomes[0].terminal_basis == (
        "oracle-nav-skill" if completed_first else "sibling-local-contract-failure"
    )
    assert not outcomes[1].benchmark_task_achieved
    assert agents[1].dispatches == int(completed_first)
    assert all("chat" not in vars(agent) for agent in agents)
    assert diagnostics.terminals == [(steps, "local_contract_failure")]
    evidence = tmp_path / "evidence"
    rows = [
        json.loads(line) for line in (evidence / "stage2-actions.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["decision"] == "rejected"
    assert rows[-1]["agent_name"] == "agent_1"
    assert rows[-1]["completed_simulator_steps"] == steps
    assert json.loads((evidence / "stage2-action-audit.json").read_text())["complete"]


def test_single_loop_contract_violation_is_local_failure_before_step(tmp_path: Path) -> None:
    """The non-shared production loop uses the same guard and terminal classification."""
    runtime = ContractLoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config = SimpleNamespace(
        max_steps=3, step_period_ms=0, episode_id="generic", agent_id=0
    )
    agent = ContractAgent("agent_0", ContractModel("north", 1))
    actor = ContractActor([agent])
    runtime._actor = actor
    runtime._agent_position = lambda: (0.0, 0.0, 0.0)  # type: ignore[method-assign]
    habitat_env = SimpleNamespace(episode_over=False, get_metrics=lambda: {"pddl_success": False})
    runtime._habitat_env = habitat_env
    gym_env = StepEnvironment(1)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "north", "t"))
    outcome = runtime._policy_loop(
        {},
        {"episode_id": "generic"},
        {"agent_0": object()},
        invocation,
        (0.0, 0.0, 0.0),
        "scene",
        actor,
        SimpleNamespace(masks_shape=(1,)),
        gym_env,
        habitat_env,
        lambda: False,
    )
    assert outcome.state == "FAILED"
    assert outcome.terminal_basis == "local-contract-failure"
    assert gym_env.calls == 0
    assert agent.dispatches == 0
    assert "chat" not in vars(agent)


class StubRuntime:
    """Deterministic shared-world runtime double."""

    def __init__(self, pair_wait_outcome: str = "COMPLETED") -> None:
        """Record calls and preset the terminal outcome."""
        self.calls = 0
        self.serial_calls: list[tuple[int, str]] = []
        self.pair_wait_outcome = pair_wait_outcome

    def initialize(self) -> None:
        """Accept thread-owning initialization without simulator work."""

    def is_ready(self) -> bool:
        """Report the stub as always ready."""
        return True

    def readiness_detail(self) -> str:
        """Describe the stub world."""
        return "stub shared world"

    def final_metrics(self) -> dict[str, object]:
        """Return official benchmark metrics."""
        return {"pddl_success": True}

    def execute_pair(
        self,
        invocations: dict[int, Any],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, object]]:
        """Run one fake shared episode for both committed assignments."""
        self.calls += 1
        for agent_id in invocations:
            running(agent_id, "stub running")
        return (
            {
                agent_id: LocalExecutionOutcome(
                    state=self.pair_wait_outcome,
                    detail="stub",
                    episode_id="51",
                    scene_id="scene",
                    destination=invocation.destination,
                    simulator_steps=10,
                    initial_position=(0.0, 0.0, 0.0),
                    final_position=(1.0, 0.0, 0.0),
                    local_skill_completed=True,
                    benchmark_task_achieved=True,
                    local_llm_calls=1,
                    terminal_basis="stub",
                )
                for agent_id, invocation in invocations.items()
            },
            {"identity": {"simulator_worlds": 1, "episode_reset_count": 1}},
        )

    def execute_serial(
        self,
        invocation: CanonicalMobilityInvocation,
        agent_id: int,
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
        final_slot: bool,
    ) -> tuple[LocalExecutionOutcome, dict[str, object]]:
        """Observe successive calls without claiming a second simulator reset."""
        del cancellation_requested
        self.serial_calls.append((agent_id, invocation.task_id))
        running(agent_id, "stub serial running")
        return (
            LocalExecutionOutcome(
                state="COMPLETED",
                detail="stub serial completion",
                episode_id="51",
                scene_id="scene",
                destination=invocation.destination,
                simulator_steps=10 * len(self.serial_calls),
                initial_position=(0.0, 0.0, 0.0),
                final_position=(1.0, 0.0, 0.0),
                local_skill_completed=True,
                benchmark_task_achieved=final_slot,
                terminal_basis="stub",
            ),
            {
                "identity": {
                    "simulator_worlds": 1,
                    "episode_reset_count": 1,
                    "simulator_steps": 10 * len(self.serial_calls),
                    "episode_terminated": final_slot,
                }
            },
        )


def _execution(store: ExecutionStore, execution_id: str) -> dict[str, object]:
    """Narrow one store read to a present execution for assertions."""
    execution = store.get(execution_id)
    assert execution is not None
    return dict(execution)


def _request(mission: str, destination: str, task: str) -> dict[str, object]:
    """Build one canonical workflow request."""
    return {
        "invocation": {
            "mission_id": mission,
            "task_id": task,
            "group_id": "group",
            "role_id": "role",
            "operation": "mobility.navigate@v1",
            "objective": "objective",
            "parameters": {"destination": destination},
            "resource_ids": ["slot"],
        }
    }


def _session_request(
    mission: str, destination: str, task: str, slots: list[dict[str, object]]
) -> dict[str, object]:
    """Bind one canonical request to a complete digest-bound topology."""
    request = _request(mission, destination, task)
    session: dict[str, object] = {
        "schema_version": "roboguide.execution-session/v0.1",
        "mission_id": mission,
        "group_id": "group",
        "slots": slots,
    }
    encoded = json.dumps(session, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    session["digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    cast(dict[str, object], request["invocation"])["execution_session"] = session
    return request


def _slot(task: str, actor: str, dependencies: list[str] | None = None) -> dict[str, object]:
    """Create one independent accepted-plan slot for coordinator tests."""
    return {
        "task_id": task,
        "role_id": "role",
        "actor_id": actor,
        "dependencies": dependencies or [],
        "independent": True,
    }


def test_execution_session_digest_agrees_with_core_canonical_json() -> None:
    """Node-admitted topology uses the same canonical digest in Rust and Python."""
    slots = [
        _slot("first", "participant"),
        _slot("second", "participant", ["first"]),
    ]
    request = _session_request("mission", "target", "second", slots)
    invocation = CanonicalMobilityInvocation.from_request(request)
    assert invocation.execution_session is not None
    assert invocation.execution_session.digest == (
        "sha256:7569dee8adc832c5811a78d034ada84e166014586cca5c8689317900a04e7099"
    )


def _world(
    tmp_path: Path,
    pair_wait: float = 5.0,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    wait_poll: float = 0.2,
) -> tuple[Any, Any, NodeEndpoint, NodeEndpoint, Path]:
    """Create one coordinator with two endpoints over stub runtime."""
    runtime = StubRuntime()
    evidence = tmp_path / "evidence"
    coordinator = SharedWorldCoordinator(
        InProcessWorldService(runtime),
        pair_wait,
        evidence,
        monotonic=monotonic,
        wait_poll_s=wait_poll,
    )
    endpoint_a = NodeEndpoint("node-a", 0, ExecutionStore(tmp_path / "a.sqlite3"), coordinator)
    endpoint_b = NodeEndpoint("node-b", 1, ExecutionStore(tmp_path / "b.sqlite3"), coordinator)
    return runtime, coordinator, endpoint_a, endpoint_b, evidence


def test_lone_assignment_waits_then_fails_closed(tmp_path: Path) -> None:
    """A sibling-less assignment must fail closed without a shared episode."""
    clock_values = iter((0.0, 2.0))
    runtime, coordinator, endpoint_a, _, evidence = _world(
        tmp_path,
        pair_wait=1.0,
        monotonic=lambda: next(clock_values, 2.0),
        wait_poll=0.001,
    )
    response = endpoint_a.submit(_request("m", "any_targets|0", "t"))
    for _ in range(1000):
        execution = _execution(endpoint_a.store(), str(response["execution_id"]))
        if execution["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert str(execution["state"]) == "FAILED"
    assert "pair never assembled" in str(execution["detail"])
    assert runtime.calls == 0
    assert not (evidence / "shared-world-summary.json").exists()
    admission = json.loads(
        (evidence / "shared-world-start-admission.json").read_text(encoding="utf-8")
    )
    assert admission["state"] == "REJECTED"
    assert admission["required_distinct_endpoint_assignments"] == 2
    assert admission["sequential_endpoint_reuse_supported"] is True
    assert admission["arrived_assignments"] == [
        {
            "agent_id": 0,
            "endpoint": "node-a",
            "execution_id": response["execution_id"],
            "group_id": "group",
            "mission_id": "m",
            "resource_ids": ["slot"],
            "role_id": "role",
            "task_id": "t",
        }
    ]
    assert coordinator.deployment_contract()["episode_scope"] == "one-official-shared-episode"


def test_one_actor_reuses_one_endpoint_without_another_reset(tmp_path: Path) -> None:
    """Control can release the first Task and dispatch the second into one world."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    slots = [_slot("ta", "participant"), _slot("tb", "participant")]
    first = endpoint_a.submit(_session_request("m", "any_targets|0", "ta", slots))
    for _ in range(1000):
        first_state = _execution(endpoint_a.store(), str(first["execution_id"]))
        if first_state["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert first_state["state"] == "COMPLETED"
    assert runtime.serial_calls == [(0, "ta")]
    assert runtime.calls == 0
    assert not (evidence / "shared-world-summary.json").exists()
    with pytest.raises(IntegrationError, match="consumed"):
        endpoint_b.submit(_session_request("m", "TARGET_any_targets|0", "tb", slots))
    second = endpoint_a.submit(_session_request("m", "TARGET_any_targets|0", "tb", slots))
    for _ in range(1000):
        second_state = _execution(endpoint_a.store(), str(second["execution_id"]))
        if second_state["state"] in TERMINAL and (evidence / "shared-world-summary.json").exists():
            break
        time.sleep(0.001)
    assert second_state["state"] == "COMPLETED"
    assert runtime.serial_calls == [(0, "ta"), (0, "tb")]
    summary = json.loads((evidence / "shared-world-summary.json").read_text())
    assert summary["identity"]["episode_reset_count"] == 1
    assert {(item["task_id"], item["role_id"]) for item in summary["serial_task_outcomes"]} == {
        ("ta", "role"),
        ("tb", "role"),
    }
    assert summary["official_pddl_success"] is True


def test_serial_runtime_retains_reset_observations_and_global_step_count(tmp_path: Path) -> None:
    """Original shared runtime continues one world across Task boundaries."""
    runtime = SerialRuntimeHarness(tmp_path)
    slots = [_slot("ta", "participant"), _slot("tb", "participant")]
    first = CanonicalMobilityInvocation.from_request(
        _session_request("m", "any_targets|0", "ta", slots)
    )
    second = CanonicalMobilityInvocation.from_request(
        _session_request("m", "TARGET_any_targets|0", "tb", slots)
    )
    original_config = runtime._config
    first_outcome, first_summary = runtime.execute_serial(
        first, 0, lambda: False, lambda agent_id, detail: None, False
    )
    second_outcome, final_summary = runtime.execute_serial(
        second, 0, lambda: False, lambda agent_id, detail: None, True
    )
    assert first_outcome.state == second_outcome.state == "COMPLETED"
    assert first_summary["identity"]["simulator_steps"] == 1
    assert final_summary["identity"]["simulator_steps"] == 2
    gym = cast(SerialGym, runtime._gym_env)
    assert gym.resets == 1
    assert gym.steps == 2
    assert runtime.observation_inputs == [0, 1]
    assert cast(RecordingDiagnostics, runtime._diagnostics).persisted_steps == [1, 2]
    assert cast(RecordingDiagnostics, runtime._diagnostics).terminals == [
        (2, "serial_session_completed")
    ]
    assert runtime._config == original_config


def test_serial_session_timeout_keeps_unfinished_topology_explicit(tmp_path: Path) -> None:
    """A missing follow-on Task never becomes a fabricated shared-world success."""
    now = [0.0]
    runtime, coordinator, endpoint_a, _, evidence = _world(
        tmp_path, pair_wait=1.0, monotonic=lambda: now[0], wait_poll=0.001
    )
    slots = [_slot("ta", "participant"), _slot("tb", "participant")]
    first = endpoint_a.submit(_session_request("m", "any_targets|0", "ta", slots))
    for _ in range(1000):
        state = _execution(endpoint_a.store(), str(first["execution_id"]))
        if state["state"] == "COMPLETED":
            break
        time.sleep(0.001)
    assert state["state"] == "COMPLETED"
    now[0] = 2.0
    for _ in range(1000):
        if coordinator._serial_finished:
            break
        time.sleep(0.001)
    assert coordinator._serial_finished
    assert runtime.serial_calls == [(0, "ta")]
    assert not (evidence / "shared-world-summary.json").exists()
    admission = json.loads((evidence / "shared-world-start-admission.json").read_text())
    for _ in range(1000):
        if admission["state"] == "INCOMPLETE":
            break
        time.sleep(0.001)
        admission = json.loads((evidence / "shared-world-start-admission.json").read_text())
    assert admission["state"] == "INCOMPLETE"
    with pytest.raises(IntegrationError, match="consumed"):
        endpoint_a.submit(_session_request("m", "TARGET_any_targets|0", "tb", slots))


def test_serial_session_rejects_tampered_or_foreign_slot(tmp_path: Path) -> None:
    """Digest and exact Task/Role identity fence a session before local admission."""
    _, _, endpoint_a, _, _ = _world(tmp_path)
    slots = [_slot("ta", "participant"), _slot("tb", "participant")]
    request = _session_request("m", "any_targets|0", "ta", slots)
    invocation = cast(dict[str, object], request["invocation"])
    session = cast(dict[str, object], invocation["execution_session"])
    session["slots"] = [_slot("ta", "someone-else"), _slot("tb", "participant")]
    with pytest.raises(IntegrationError, match="digest"):
        endpoint_a.accept(request)
    foreign = _session_request("m", "any_targets|0", "not-in-plan", slots)
    with pytest.raises(IntegrationError, match="Task/Role"):
        endpoint_a.accept(foreign)


def test_serial_session_rejects_dependency_cycle(tmp_path: Path) -> None:
    """A self-consistent cyclic session cannot strand the serial coordinator."""
    _, _, endpoint_a, _, _ = _world(tmp_path)
    slots = [
        _slot("first", "participant", ["second"]),
        _slot("second", "participant", ["first"]),
    ]
    with pytest.raises(IntegrationError, match="dependency graph contains a cycle"):
        endpoint_a.accept(_session_request("m", "any_targets|0", "first", slots))


def test_unsupported_topology_fails_before_world_reset(tmp_path: Path) -> None:
    """A non-independent topology cannot silently enter pair or serial execution."""
    runtime, _, endpoint_a, _, evidence = _world(tmp_path)
    slots = [_slot("ta", "participant"), _slot("tb", "participant")]
    slots[0]["independent"] = False
    handle = endpoint_a.submit(_session_request("m", "any_targets|0", "ta", slots))
    for _ in range(1000):
        state = _execution(endpoint_a.store(), str(handle["execution_id"]))
        if state["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert state["state"] == "FAILED"
    assert runtime.calls == 0 and runtime.serial_calls == []
    admission = json.loads((evidence / "shared-world-start-admission.json").read_text())
    assert admission["state"] == "REJECTED"


def test_pair_runs_one_episode_with_two_handles(tmp_path: Path) -> None:
    """Both assignments must share exactly one episode with distinct handles."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    handle_a = endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    time.sleep(0.5)
    assert _execution(endpoint_a.store(), str(handle_a["execution_id"]))["state"] == "ACCEPTED", (
        "pre-pair assignment must not start alone"
    )
    handle_b = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    summary = None
    for _ in range(80):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            # Local terminal publication precedes the separate benchmark artifact write.
            # Wait for both boundaries; a local completion is not benchmark availability.
            try:
                summary = json.loads(
                    (evidence / "shared-world-summary.json").read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            else:
                break
        time.sleep(0.1)
    assert state_a["state"] == "COMPLETED"
    assert state_b["state"] == "COMPLETED"
    assert handle_a["execution_id"] != handle_b["execution_id"]
    assert runtime.calls == 1
    assert summary is not None, "benchmark summary was not published within the test budget"
    assert summary["identity"]["episode_reset_count"] == 1
    assert summary["identity"]["simulator_worlds"] == 1
    assert summary["official_pddl_success"] is True
    admission = json.loads(
        (evidence / "shared-world-start-admission.json").read_text(encoding="utf-8")
    )
    assert admission["state"] == "ADMITTED"
    assert {entry["endpoint"] for entry in admission["arrived_assignments"]} == {
        "node-a",
        "node-b",
    }


def test_two_actor_metadata_keeps_distinct_endpoint_start_barrier(tmp_path: Path) -> None:
    """Plan-derived two-Actor topology retains the original concurrent path."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    slots = [_slot("ta", "actor-a"), _slot("tb", "actor-b")]
    left = endpoint_a.submit(_session_request("m", "any_targets|0", "ta", slots))
    assert _execution(endpoint_a.store(), str(left["execution_id"]))["state"] == "ACCEPTED"
    right = endpoint_b.submit(_session_request("m", "TARGET_any_targets|0", "tb", slots))
    for _ in range(1000):
        states = [
            _execution(endpoint.store(), str(handle["execution_id"]))["state"]
            for endpoint, handle in ((endpoint_a, left), (endpoint_b, right))
        ]
        if all(state in TERMINAL for state in states):
            break
        time.sleep(0.001)
    assert states == ["COMPLETED", "COMPLETED"]
    assert runtime.calls == 1 and not runtime.serial_calls
    admission = json.loads((evidence / "shared-world-start-admission.json").read_text())
    assert admission["state"] == "ADMITTED"
    assert {item["endpoint"] for item in admission["arrived_assignments"]} == {"node-a", "node-b"}


def test_start_admission_evidence_failure_does_not_change_pair_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional topology evidence I/O cannot block an otherwise admitted pair."""
    runtime, coordinator, endpoint_a, endpoint_b, _ = _world(tmp_path)
    write_json = coordinator._write_json

    def fail_write(name: str, value: object) -> None:
        """Raise only for the new admission document in this failure injection."""
        if name == "shared-world-start-admission.json":
            raise OSError("read-only evidence directory")
        write_json(name, value)

    monkeypatch.setattr(coordinator, "_write_json", fail_write)
    handle_a = endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    handle_b = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(1000):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert state_a["state"] == state_b["state"] == "COMPLETED"
    assert runtime.calls == 1


def test_summary_evidence_failure_does_not_change_pair_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal summary storage failure cannot change completed executions."""
    runtime, coordinator, endpoint_a, endpoint_b, _ = _world(tmp_path)
    write_json = coordinator._write_json

    def fail_summary(name: str, value: object) -> None:
        """Raise only while publishing the terminal shared-world snapshot."""
        if name == "shared-world-summary.json":
            raise OSError("summary storage failure sentinel")
        write_json(name, value)

    monkeypatch.setattr(coordinator, "_write_json", fail_summary)
    handle_a = endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    handle_b = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(1000):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert state_a["state"] == state_b["state"] == "COMPLETED"
    assert runtime.calls == 1


def test_summary_is_complete_before_pair_terminal_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visible terminal Node fact always follows the complete summary snapshot."""
    _, coordinator, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    original_write = coordinator._write_json
    observed: list[tuple[str, bool, bool]] = []

    def delayed_write(name: str, value: object) -> None:
        """Expose the publication order while the summary write is paused."""
        if name == "shared-world-summary.json":
            observed.append(
                (
                    "before-summary",
                    endpoint_a.store().active_execution() is not None,
                    endpoint_b.store().active_execution() is not None,
                )
            )
        original_write(name, value)

    monkeypatch.setattr(coordinator, "_write_json", delayed_write)
    left = endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    right = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(1000):
        left_state = _execution(endpoint_a.store(), str(left["execution_id"]))
        right_state = _execution(endpoint_b.store(), str(right["execution_id"]))
        if left_state["state"] in TERMINAL and right_state["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert observed == [("before-summary", True, True)]
    assert json.loads((evidence / "shared-world-summary.json").read_text())["official_pddl_success"]
    assert left_state["state"] == right_state["state"] == "COMPLETED"


def test_duplicate_assignment_is_idempotent(tmp_path: Path) -> None:
    """Duplicate accept and dispatch reuse one handle and never re-run."""
    runtime, _, endpoint_a, endpoint_b, _ = _world(tmp_path)
    request = _request("m", "any_targets|0", "ta")
    handle = endpoint_a.submit(request)
    endpoint_a.dispatch(str(handle["execution_id"]))
    duplicate = endpoint_a.accept(request)
    assert duplicate["execution_id"] == handle["execution_id"]
    endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(80):
        execution = _execution(endpoint_a.store(), str(handle["execution_id"]))
        if str(execution["state"]) in TERMINAL:
            break
        time.sleep(0.1)
    assert str(execution["state"]) == "COMPLETED"
    assert runtime.calls == 1
    repeat = endpoint_a.accept(request)
    assert repeat["execution_id"] == handle["execution_id"]
    time.sleep(0.3)
    assert runtime.calls == 1


@pytest.mark.parametrize("mismatch", ["different-group", "same-slot"])
def test_pair_requires_one_group_with_distinct_logical_slots(tmp_path: Path, mismatch: str) -> None:
    """A valid Node/agent pair alone cannot authorize inconsistent Group work."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    left = _request("mission-a", "any_targets|0", "ta")
    right = _request("mission-a", "TARGET_any_targets|0", "tb")
    right_invocation = cast(dict[str, object], right["invocation"])
    if mismatch == "different-group":
        right_invocation["group_id"] = "other-group"
    else:
        right_invocation["task_id"] = "ta"
    handle_a = endpoint_a.submit(left)
    handle_b = endpoint_b.submit(right)
    for _ in range(1000):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert state_a["state"] == state_b["state"] == "FAILED"
    assert runtime.calls == 0
    admission = json.loads(
        (evidence / "shared-world-start-admission.json").read_text(encoding="utf-8")
    )
    assert admission["state"] == "REJECTED"
    if mismatch == "different-group":
        assert "different Mission" in admission["reason"]
    else:
        assert "duplicate one logical" in admission["reason"]


def test_cross_mission_pair_is_rejected_before_habitat_reset(tmp_path: Path) -> None:
    """The coordinator never combines unrelated Missions into one shared episode."""
    runtime, _, endpoint_a, endpoint_b, evidence = _world(tmp_path)
    handle_a = endpoint_a.submit(_request("mission-a", "any_targets|0", "ta"))
    handle_b = endpoint_b.submit(_request("mission-b", "TARGET_any_targets|0", "tb"))
    for _ in range(1000):
        state_a = _execution(endpoint_a.store(), str(handle_a["execution_id"]))
        state_b = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if state_a["state"] in TERMINAL and state_b["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert state_a["state"] == state_b["state"] == "FAILED"
    assert runtime.calls == 0
    admission = json.loads(
        (evidence / "shared-world-start-admission.json").read_text(encoding="utf-8")
    )
    assert admission["state"] == "REJECTED"
    assert "different Mission" in admission["reason"]
    journal = (evidence / "shared-world-start-admission.jsonl").read_text(encoding="utf-8")
    assert '"state": "REJECTED"' in journal


def test_new_invocation_after_consumed_episode_rejected(tmp_path: Path) -> None:
    """A fresh third assignment cannot fabricate a second shared episode."""
    runtime, _, endpoint_a, endpoint_b, _ = _world(tmp_path)
    endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    handle_b = endpoint_b.submit(_request("m", "TARGET_any_targets|0", "tb"))
    for _ in range(80):
        execution = _execution(endpoint_b.store(), str(handle_b["execution_id"]))
        if str(execution["state"]) in TERMINAL:
            break
        time.sleep(0.1)
    with pytest.raises(IntegrationError, match="consumed"):
        endpoint_a.submit(_request("m3", "other|0", "tc"))
    assert runtime.calls == 1


def test_conflicting_active_assignment_rejected(tmp_path: Path) -> None:
    """One endpoint refuses a second distinct active execution."""
    _, _, endpoint_a, _, _ = _world(tmp_path)
    endpoint_a.submit(_request("m", "any_targets|0", "ta"))
    with pytest.raises(IntegrationError, match="another active execution"):
        endpoint_a.submit(_request("m-different", "elsewhere|0", "tb"))
