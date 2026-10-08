"""Deterministic tests for the shared-world coordinator and node endpoints."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios import idle_endpoint  # noqa: E402
from habitat_local_eaios import shared_world as shared_world_module  # noqa: E402
from habitat_local_eaios.backend import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig  # noqa: E402
from habitat_local_eaios.diagnostics import BufferedJsonlWriter  # noqa: E402
from habitat_local_eaios.emos_stage2 import EmosStage2Runtime  # noqa: E402
from habitat_local_eaios.goal_region_navigation import GoalRegionSearchMiss  # noqa: E402
from habitat_local_eaios.http_service import HabitatBridgeServer  # noqa: E402
from habitat_local_eaios.idle_endpoint import PassiveIdleAgent  # noqa: E402
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalInvocation,
    CanonicalMobilityInvocation,
    IntegrationError,
)
from habitat_local_eaios.shared_world import (  # noqa: E402
    InProcessWorldService,
    NodeEndpoint,
    ProcessWorldService,
    SharedEmosStage2Runtime,
    SharedWorldCoordinator,
)
from habitat_local_eaios.stage2_contract import Stage2ExecutionContract  # noqa: E402
from habitat_local_eaios.store import ExecutionStore  # noqa: E402

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class PreparedAction:
    """Observe the production pre-motion boundary without querying Habitat."""

    def __init__(self, agent_id: int, *, fail_at: int | None = None) -> None:
        """Configure one bounded path miss and count preparation versus motion."""
        self.agent_id = agent_id
        self.fail_at = fail_at
        self.preparations = self.moves = 0
        self.pending = False

    def prepare_navigation_step(self, **arguments: Any) -> None:
        """Cache one command; a miss occurs before any robot motion."""
        self.preparations += 1
        self.pending = True
        if self.preparations == self.fail_at:
            raise GoalRegionSearchMiss(
                "bounded local miss",
                {"path_queries": 2, "search_truncated": False},
            )

    def discard_prepared_navigation(self) -> None:
        """Remove an unconsumed command without changing physical state."""
        self.pending = False

    def step(self) -> None:
        """Move only after production preparation admitted the complete joint command."""
        assert self.pending
        self.pending = False
        self.moves += 1


@pytest.mark.parametrize("completed_first", [False, True])
@pytest.mark.parametrize("evidence_fault", [False, True])
def test_pair_preparation_failure_precedes_motion_and_preserves_completed_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed_first: bool, evidence_fault: bool
) -> None:
    """The actual loop attributes a miss, flushes diagnostics and never partly dispatches it."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)
    runtime._config.episode_id = "generic"
    runtime._config.spatial_navigation_arrival = True
    actor = PolicyActor()
    runtime._actor = actor
    actions = {
        f"agent_{agent}_oracle_nav_action": PreparedAction(
            agent, fail_at=(2 if completed_first else 1) if agent == 1 else None
        )
        for agent in (0, 1)
    }
    habitat = SimpleNamespace(
        current_episode=SimpleNamespace(scene_id="scene", episode_id="generic"),
        task=SimpleNamespace(actions=actions),
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    runtime._habitat_env = habitat
    decodes: list[object] = []

    def decode(original: object, flat: object, action: object) -> dict[str, Any]:
        """Receive precisely the spaces and action used by the Gym facade."""
        assert original is gym.original_action_space and flat is gym.action_space
        decodes.append(action)
        return {"action": tuple(actions), "action_args": {}}

    runtime._runtime.update(
        decode_navigation_action=decode,
        habitat_config=SimpleNamespace(
            habitat=SimpleNamespace(simulator=SimpleNamespace(agents_order=["agent_0", "agent_1"]))
        ),
    )

    def step(action: object) -> Any:
        """Consume each cached command once after the whole preparation succeeded."""
        assert action is decodes[-1]
        for local in actions.values():
            local.step()
        return (
            {"agent_0_has_finished_oracle_nav": [1], "agent_1_has_finished_oracle_nav": [0]},
            0.0,
            False,
            {"pddl_success": False},
        )

    gym = SimpleNamespace(original_action_space=object(), action_space=object(), step=step)
    if evidence_fault:

        def fail_write(_name: str, _value: object) -> None:
            """A diagnostic storage fault cannot authorize motion or mask the local failure."""
            raise OSError("evidence storage sentinel")

        monkeypatch.setattr(runtime, "_write_json", fail_write)
    invocations = {
        agent: replace(
            CanonicalMobilityInvocation.from_request(_request("m", goal, f"task-{agent}")),
            attempt_id=f"attempt-{agent}",
        )
        for agent, goal in enumerate(["north", "south"])
    }
    outcomes, steps, done, _ = runtime._pair_loop(
        {},
        {"episode_id": "generic"},
        {},
        invocations,
        actor,
        SimpleNamespace(masks_shape=(1,)),
        gym,
        habitat,
        lambda: False,
        lambda *unused: None,
    )
    assert steps == int(completed_first) and done is False
    assert actor.calls == len(decodes) == 1 + int(completed_first)
    assert [local.moves for local in actions.values()] == [steps, steps]
    assert all(not local.pending for local in actions.values())
    assert outcomes[1].terminal_basis == "local-navigation-preparation-failure"
    assert outcomes[0].state == ("COMPLETED" if completed_first else "FAILED")
    assert outcomes[0].terminal_basis == (
        "oracle-nav-skill" if completed_first else "sibling-navigation-preparation-failure"
    )
    assert diagnostics.terminals == [(steps, "local_navigation_preparation_failure")]
    evidence = runtime._last_navigation_preparation_failure
    assert evidence is not None
    assert evidence["failure"]["agent_id"] == 1
    assert evidence["failure"]["search"]["path_queries"] == 2
    assert evidence["failure"]["proves_physical_impossibility"] is False
    assert evidence["invocations"]["1"] == invocations[1].as_dict()
    assert evidence["simulator_steps"] == steps
    body = {key: value for key, value in evidence.items() if key != "digest"}
    assert (
        evidence["digest"]
        == "sha256:"
        + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )
    path = tmp_path / "evidence" / f"navigation-preparation-failure-{steps}.json"
    assert path.is_file() is not evidence_fault
    if not evidence_fault:
        assert json.loads(path.read_text()) == evidence


@pytest.mark.parametrize("assigned_agent", [0, 1])
def test_single_policy_preparation_failure_is_local_and_never_calls_gym(
    tmp_path: Path, assigned_agent: int
) -> None:
    """A single Actor keeps its original model path, but an unusable command never moves."""
    runtime, actor, agents = _single_idle_setup(tmp_path, assigned_agent_id=assigned_agent)
    runtime._config.spatial_navigation_arrival = True
    local = PreparedAction(assigned_agent, fail_at=1)
    assert runtime._habitat_env is not None
    runtime._habitat_env.current_episode = SimpleNamespace(scene_id="scene", episode_id="generic")
    runtime._habitat_env.task = SimpleNamespace(
        actions={f"agent_{assigned_agent}_oracle_nav_action": local}
    )
    runtime._runtime.update(
        decode_navigation_action=lambda *_unused: {
            "action": f"agent_{assigned_agent}_oracle_nav_action",
            "action_args": {},
        },
        habitat_config=SimpleNamespace(
            habitat=SimpleNamespace(simulator=SimpleNamespace(agents_order=["agent_0", "agent_1"]))
        ),
    )
    gym = StepEnvironment(1)
    gym.original_action_space = object()  # type: ignore[attr-defined]
    gym.action_space = object()  # type: ignore[attr-defined]
    goal = "north" if assigned_agent == 0 else "south"
    invocation = CanonicalMobilityInvocation.from_request(_request("m", goal, "task"))
    outcome = _single_idle_loop(runtime, actor, invocation, gym, lambda: False)
    assert outcome.state == "FAILED"
    assert outcome.terminal_basis == "local-navigation-preparation-failure"
    assert gym.calls == local.moves == 0
    assert actor.calls == agents[assigned_agent].llm_model.calls == 1
    assert agents[1 - assigned_agent].llm_model.calls == 0
    assert not local.pending


def test_disabled_preparation_does_not_read_habitat_or_decode_the_command(tmp_path: Path) -> None:
    """Legacy execution needs no preparation hooks, new imports, or world-state reads."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config.spatial_navigation_arrival = False
    discard = runtime._prepare_navigation_step(object(), object(), object(), {}, 0)
    discard()
    assert not (tmp_path / "evidence/navigation-preparation-failure-0.json").exists()


def test_successful_pair_uses_one_model_iteration_and_one_original_joint_step(
    tmp_path: Path,
) -> None:
    """Preparation cannot add model iterations, target preparation, motion or simulator steps."""
    runtime = LoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config.episode_id = "generic"
    runtime._config.spatial_navigation_arrival = True
    actor = PolicyActor()
    runtime._actor = actor
    actions = {f"agent_{agent}_oracle_nav_action": PreparedAction(agent) for agent in (0, 1)}
    habitat = SimpleNamespace(
        current_episode=SimpleNamespace(scene_id="scene", episode_id="generic"),
        task=SimpleNamespace(actions=actions),
        episode_over=True,
        get_metrics=lambda: {"pddl_success": True},
    )
    runtime._habitat_env = habitat
    runtime._runtime.update(
        decode_navigation_action=lambda *_unused: {"action": tuple(actions), "action_args": {}},
        habitat_config=SimpleNamespace(
            habitat=SimpleNamespace(simulator=SimpleNamespace(agents_order=["agent_0", "agent_1"]))
        ),
    )
    gym_calls: list[object] = []

    def step(action: object) -> Any:
        """Complete the one actual shared step; no settling step is needed."""
        gym_calls.append(action)
        for local in actions.values():
            local.step()
        return (
            {"agent_0_has_finished_oracle_nav": [0], "agent_1_has_finished_oracle_nav": [0]},
            0.0,
            True,
            {"pddl_success": True},
        )

    invocations = {
        agent: CanonicalMobilityInvocation.from_request(_request("m", goal, f"task-{agent}"))
        for agent, goal in enumerate(["north", "south"])
    }
    outcomes, steps, done, _ = runtime._pair_loop(
        {},
        {"episode_id": "generic"},
        {},
        invocations,
        actor,
        SimpleNamespace(masks_shape=(1,)),
        SimpleNamespace(original_action_space=object(), action_space=object(), step=step),
        habitat,
        lambda: False,
        lambda *unused: None,
    )
    assert actor.calls == len(gym_calls) == steps == 1 and done
    assert all(local.preparations == local.moves == 1 for local in actions.values())
    assert all(outcome.state == "COMPLETED" for outcome in outcomes.values())
    assert runtime._last_navigation_preparation_failure is None


@pytest.mark.parametrize("steps", [0, 1])
def test_reset_metric_is_not_a_final_outcome_after_preparation_failure(steps: int) -> None:
    """Only a real physical step permits the existing terminal official metric path."""
    calls: list[int] = []

    def metrics() -> dict[str, bool]:
        """Expose a misleading reset result to prove it is not promoted to benchmark truth."""
        calls.append(1)
        return {"pddl_success": False}

    summary: dict[str, Any] = {
        "identity": {"simulator_steps": steps},
        "navigation_preparation_failure": {
            "simulator_steps": steps,
            "failed_before_gym_step": True,
        },
    }
    shared_world_module._archive_official_metrics(SimpleNamespace(final_metrics=metrics), summary)
    assert calls == ([1] if steps else [])
    assert summary["official_metrics"] == ({"pddl_success": False} if steps else {})
    assert ("official_pddl_success_unavailable_reason" in summary) is (steps == 0)


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
        self.stops: list[tuple[int, str, int]] = []

    def record_reset(self, habitat_env: object, config: object) -> None:
        """Count one reset observation without reading the fake environment."""
        del habitat_env, config
        self.reset_calls += 1

    def install_nav_probes(self, habitat_env: object) -> None:
        """Accept the optional post-reset Oracle observer boundary."""
        del habitat_env

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

    def record_stop(self, habitat_env: object, steps: int, reason: str, segment: int) -> None:
        """Flush an actual stopped-world snapshot without terminating the diagnostic stream."""
        del habitat_env
        self.flush_boundary()
        self.stops.append((steps, reason, segment))


class WaitSkillPolicy:
    """Represent the existing EMOS wait skill in policy-loop doubles."""


@pytest.fixture(autouse=True)
def installed_wait_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the original skill identity to this vendor-shaped fake."""
    monkeypatch.setattr(idle_endpoint, "_original_wait_skill_type", lambda: WaitSkillPolicy)


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
        self._active_policies = [
            SimpleNamespace(
                _name_to_idx={"wait": 1},
                _skills={1: WaitSkillPolicy()},
                _high_level_policy=SimpleNamespace(
                    llm_agent=SimpleNamespace(name=f"agent_{agent_id}"),
                    _skill_name_to_idx={"wait": 1},
                ),
            )
            for agent_id in range(2)
        ]

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
            task=SimpleNamespace(
                get_task_text_context=lambda: {"scene_description": "scene"}, actions={}
            ),
            sim=SimpleNamespace(get_agent_data=agent_data),
            get_metrics=lambda: {"pddl_success": self._gym_env.steps == 2},
        )
        self._episode = object()
        self._actor = object()
        self._agent_access = object()
        self._diagnostics = cast(Any, RecordingDiagnostics())
        self.observation_inputs: list[int] = []
        self._prepare_reset()

    def _assigned_arguments(
        self, text_context: dict[str, Any], invocation: CanonicalInvocation
    ) -> dict[str, Any]:
        """Avoid importing the vendor AgentArguments class in this offline test."""
        del text_context, invocation
        return {"agent_0": object(), "agent_1": object()}

    def _policy_loop(
        self,
        observations: Any,
        text_context: dict[str, Any],
        assignment: dict[str, Any],
        invocation: CanonicalInvocation,
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


@pytest.mark.parametrize("binding_failure", [False, True])
def test_pair_loop_optional_diagnostic_binding_cannot_block_original_actor(
    tmp_path: Path, binding_failure: bool
) -> None:
    """The production loop passes existing invocation metadata but never grants it authority."""
    diagnostics = RecordingDiagnostics()
    calls: list[dict[int, CanonicalMobilityInvocation]] = []

    def bind_navigation_invocations(invocations: dict[int, CanonicalMobilityInvocation]) -> None:
        """Observe the real loop boundary and optionally fail only diagnostic binding."""
        calls.append(invocations)
        if binding_failure:
            raise OSError("diagnostic binding failure")

    cast(Any, diagnostics).bind_navigation_invocations = bind_navigation_invocations
    runtime = LoopHarness(tmp_path, diagnostics)
    gym = StepEnvironment(fail_at=3)
    actor = PolicyActor(fail_at=1)
    with pytest.raises(RuntimeError, match="actor failure sentinel"):
        _run_failing_loop(runtime, actor, gym)
    assert calls == [{}]
    assert gym.calls == 0
    assert diagnostics.terminals == [(0, "execution_exception:actor_act:RuntimeError")]


@pytest.mark.parametrize("completed_first", [False, True])
def test_joint_policy_cancel_stops_both_without_erasing_completed_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed_first: bool
) -> None:
    """One cancellation breaks the real joint loop; no extra step or sibling retry is invented."""
    diagnostics = RecordingDiagnostics()
    runtime = LoopHarness(tmp_path, diagnostics)
    runtime._config = SimpleNamespace(max_steps=3, step_period_ms=0, episode_id="generic")
    actor = PolicyActor()
    gym = StepEnvironment(fail_at=3)
    original_step = gym.step

    def step(action: object) -> tuple[dict[str, list[int]], float, bool, dict[str, bool]]:
        """Return one real-loop observation with optional existing sibling local completion."""
        observations, reward, done, info = original_step(action)
        observations["agent_0_has_finished_oracle_nav"] = [int(completed_first)]
        return observations, reward, done, info

    monkeypatch.setattr(gym, "step", step)
    habitat_env = SimpleNamespace(
        current_episode=SimpleNamespace(scene_id="scene"),
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    runtime._habitat_env = habitat_env
    runtime._actor = actor
    invocations = {
        agent: CanonicalMobilityInvocation.from_request(
            _request("m", f"target-{agent}", f"t-{agent}")
        )
        for agent in (0, 1)
    }
    outcomes, steps, done, info = runtime._pair_loop(
        {},
        {"episode_id": "generic"},
        {},
        invocations,
        actor,
        SimpleNamespace(masks_shape=(1,)),
        gym,
        habitat_env,
        lambda: gym.calls >= 1,
        lambda agent_id, detail: None,
    )
    assert steps == gym.calls == actor.calls == 1
    assert not done and info["pddl_success"] is False
    assert outcomes[1].state == "CANCELLED"
    assert outcomes[1].terminal_basis == "cancellation"
    assert outcomes[0].state == ("COMPLETED" if completed_first else "CANCELLED")
    assert outcomes[0].terminal_basis == ("oracle-nav-skill" if completed_first else "cancellation")
    assert all(not outcome.benchmark_task_achieved for outcome in outcomes.values())
    assert diagnostics.terminals == [(1, "cancellation")]


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
        current_episode=SimpleNamespace(episode_id="51", scene_id="scene"),
        sim=SimpleNamespace(
            get_agent_data=lambda _agent_id: SimpleNamespace(
                articulated_agent=SimpleNamespace(base_pos=(0.0, 0.0, 0.0))
            )
        ),
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
    runtime._prepared_observations = None
    runtime._prepare_reset()
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
        self.selected_tool = "nav_to_obj"
        self.requests: list[dict[str, Any]] = []
        self.model = "offline-model"
        self.chat_history: list[list[dict[str, Any]]] = []
        self.actions = [
            {
                "name": "nav_to_obj",
                "parameters": {
                    "type": "object",
                    "properties": {"target_obj": {"type": "string"}},
                    "required": ["target_obj"],
                },
            }
        ]

    def chat(self, observation: str, crab_planning: bool = False) -> Any:
        """Return one raw selected tool without changing the fake simulator."""
        del crab_planning
        self.requests.append({"content": observation, "history": deepcopy(self.chat_history)})
        self.calls += 1
        target = "wrong-target" if self.calls == self.violate_at else self.target
        arguments = {"target_obj": target} if self.selected_tool == "nav_to_obj" else {}
        identity = f"call-{self.calls}"
        self.chat_history.append(
            [
                {"role": "user", "content": "observation"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": identity,
                            "function": {
                                "name": self.selected_tool,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ]
                    * (2 if self.calls == self.extra_tools_at else 1),
                },
                {
                    "role": "tool",
                    "tool_call_id": identity,
                    "name": self.selected_tool,
                    "content": "Success",
                },
            ]
        )
        return self.selected_tool, arguments


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
            SimpleNamespace(
                _name_to_idx={"wait": 1},
                _skills={1: WaitSkillPolicy()},
                _high_level_policy=SimpleNamespace(
                    llm_agent=agent,
                    _skill_name_to_idx={"wait": 1},
                ),
            )
            for agent in agents
        ]

    def act(self, *args: object, **kwargs: object) -> object:
        """Select actions before permitting the policy loop to step Gym."""
        for policy in self._active_policies:
            agent = policy._high_level_policy.llm_agent
            if isinstance(agent, PassiveIdleAgent) and not agent.initialized:
                agent.init_agent("FetchRobot", "joint objective", "Nothing to do", [])
            agent.chat("observation")
        return super().act(*args, **kwargs)


class ContractLoopHarness(LoopHarness):
    """Use production contract installation and outcome reduction with fake physics."""

    def _install_execution_contract(
        self, contracts: dict[str, Stage2ExecutionContract], completed_steps: Callable[[], int]
    ) -> Callable[[], None]:
        """Exercise real instance hooks, audit output, and restoration."""
        return EmosStage2Runtime._install_execution_contract(self, contracts, completed_steps)


class SingleIdleContractLoopHarness(ContractLoopHarness):
    """Exercise the real single-assignment loop with one passive endpoint."""

    def _current_skills(self, actor: Any) -> list[str]:
        """Report wait for the actual passive policy and navigation for its owner."""
        return [
            "wait"
            if isinstance(policy._high_level_policy.llm_agent, PassiveIdleAgent)
            else "nav_to_obj"
            for policy in actor._active_policies
        ]

    def _record_video(self, step: int, observations: Any, info: dict[str, Any]) -> None:
        """Omit recording while retaining the physical step observation boundary."""
        del step, observations, info


def test_real_pair_loop_budget_feedback_does_not_mark_waiting_navigation_completed(
    tmp_path: Path,
) -> None:
    """Budget feedback is truthful while Node outcome still requires local completion."""

    class BudgetSkill:
        """Expose the existing skill's false arrival and positive budget stop decision."""

        _cur_skill_step = [1]
        _max_skill_steps = 1

        def _is_skill_done(self) -> list[bool]:
            """Keep actual local arrival false independently of the budget."""
            return [False]

        def should_terminate(self, **kwargs: Any) -> Any:
            """Run one original-shaped completion calculation before returning control."""
            del kwargs
            return (
                [self._is_skill_done()[0] or self._cur_skill_step[0] >= self._max_skill_steps],
                [False],
                object(),
            )

    class BudgetActor(ContractActor):
        """Select navigation once, then model-selected wait after the original budget exit."""

        def act(self, *args: object, **kwargs: object) -> object:
            """Keep one actor call and one model call per assigned endpoint in the fake loop."""
            for policy in self._active_policies:
                agent = policy._high_level_policy.llm_agent
                if self.calls:
                    agent.llm_model.selected_tool = "wait"
                agent.chat("You have completed your previous action. Select your next action.")
                if not self.calls:
                    policy._skills[0].should_terminate(
                        skill_name=["nav_to_obj"], batch_idx=[0], hl_wants_skill_term=[False]
                    )
            return PolicyActor.act(self, *args, **kwargs)

    class BudgetHarness(ContractLoopHarness):
        """Retain the production pair loop, contract hooks and outcome reduction."""

        def _current_skills(self, actor: Any) -> list[str]:
            """Read the actual selected tool without inventing navigation completion."""
            return [
                p._high_level_policy.llm_agent.llm_model.selected_tool
                for p in actor._active_policies
            ]

    runtime = BudgetHarness(tmp_path, RecordingDiagnostics())
    agents = [
        ContractAgent(f"agent_{agent}", ContractModel(target, None))
        for agent, target in enumerate(("north", "south"))
    ]
    actor = BudgetActor(agents)
    for policy in actor._active_policies:
        policy._skills[0] = BudgetSkill()
    runtime._actor = actor
    steps = 0

    def gym_step(action: object) -> Any:
        """The first endpoint reaches its local measure; the second never does."""
        nonlocal steps
        del action
        steps += 1
        return (
            {"agent_0_has_finished_oracle_nav": [1], "agent_1_has_finished_oracle_nav": [0]},
            0.0,
            False,
            {"pddl_success": False},
        )

    environment = SimpleNamespace(
        episode_over=False,
        current_episode=SimpleNamespace(scene_id="scene", episode_id="generic"),
        get_metrics=lambda: {"pddl_success": False},
        task=SimpleNamespace(actions={}),
    )
    runtime._config.episode_id = "generic"
    runtime._habitat_env = environment
    outcomes, _, _, _ = runtime._pair_loop(
        {},
        {"episode_id": "generic"},
        {},
        _retained_invocations(),
        actor,
        SimpleNamespace(masks_shape=(1,)),
        SimpleNamespace(step=gym_step),
        environment,
        lambda: False,
        lambda agent_id, detail: None,
    )
    assert steps == actor.calls == 3
    assert [agent.llm_model.calls for agent in agents] == [3, 3]
    assert outcomes[0].state == "COMPLETED" and outcomes[0].local_skill_completed is True
    assert outcomes[1].state == "FAILED" and outcomes[1].local_skill_completed is False
    assert outcomes[1].terminal_basis == "step-budget-exhausted"
    received = json.loads(agents[1].llm_model.requests[1]["history"][0][-1]["content"])
    assert received["status"] == "skill-budget-exhausted"
    assert received["local_skill_completed"] is False
    assert received["benchmark_goal_satisfied"] is None
    completed = json.loads(agents[0].llm_model.requests[1]["history"][0][-1]["content"])
    assert completed["status"] == "local-skill-completed"
    summary = json.loads(
        (tmp_path / "evidence" / "stage2-execution-feedback-audit.json").read_text()
    )
    assert summary["complete"] is True
    assert summary["records_unavailable"] == summary["write_failures"] == 0
    assert all(
        "should_terminate" not in vars(policy._skills[0]) for policy in actor._active_policies
    )


class RetainedGym:
    """Advance one counted world; actual skill observations are independent of official success."""

    def __init__(self, *, completed_first: bool, final_step: int, official: bool) -> None:
        """Configure terminal observations and optional parent/child cancellation handshake."""
        self.resets = 0
        self.steps = 0
        self.completed_first = completed_first
        self.final_step = final_step
        self.official = official
        self.episode_over = False
        self.on_step: Callable[[], None] = lambda: None

    def reset(self) -> dict[str, Any]:
        """Return the sole reset observation; continuation must never invoke this again."""
        self.resets += 1
        return {"step": 0}

    def step(self, action: object) -> tuple[dict[str, Any], float, bool, dict[str, bool]]:
        """Emit navigation completion at specified actual simulator steps."""
        del action
        self.steps += 1
        self.on_step()
        self.episode_over = self.steps >= self.final_step
        return (
            {
                "step": self.steps,
                "agent_0_has_finished_oracle_nav": [int(self.completed_first or self.episode_over)],
                "agent_1_has_finished_oracle_nav": [int(self.episode_over)],
            },
            0.0,
            self.episode_over,
            {"pddl_success": self.official and self.episode_over},
        )

    def close(self) -> None:
        """Release the fake world without advancing or resetting it."""


class RecordingVideo:
    """Count continuous frame observations and closure without producing experiment media."""

    def __init__(self) -> None:
        """Start an open trace stream."""
        self.steps: list[int] = []
        self.closures: list[str] = []

    def close(self, reason: str) -> None:
        """Observe the boundary that closes a real video writer."""
        self.closures.append(reason)


class RetainedRuntimeHarness(SingleIdleContractLoopHarness):
    """Use production stop/resume and guard code with vendor-shaped policy and physics doubles."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        completed_first: bool = True,
        final_step: int = 3,
        max_steps: int = 3,
    ) -> None:
        """Construct an unreset world with real continuation admission and original loop code."""
        super().__init__(tmp_path, RecordingDiagnostics())
        self._config = CrabAgentBackendConfig(
            config_path=tmp_path / "unused.yaml",
            episode_id="generic",
            agent_id=0,
            max_steps=max_steps,
            step_period_ms=0,
            evidence_dir=tmp_path / "evidence",
            retain_stopped_session=True,
        )
        self._gym_env = RetainedGym(
            completed_first=completed_first, final_step=final_step, official=False
        )
        self._habitat_env = SimpleNamespace(
            episodes=[],
            current_episode=SimpleNamespace(episode_id="generic", scene_id="scene"),
            episode_over=False,
            task=SimpleNamespace(get_task_text_context=lambda: {"scene_description": "scene"}),
            sim=SimpleNamespace(
                get_agent_data=lambda unused: SimpleNamespace(
                    articulated_agent=SimpleNamespace(base_pos=(0.0, 0.0, 0.0))
                )
            ),
            get_metrics=lambda: {"pddl_success": False},
        )
        self.agents = [
            ContractAgent(f"agent_{agent}", ContractModel(target, None))
            for agent, target in enumerate(("north", "south"))
        ]
        self._actor = ContractActor(self.agents)
        self._agent_access = SimpleNamespace(masks_shape=(1,))
        self._episode = object()
        self._prepared_observations = None
        self._reset_started = False
        self._pair_session = None
        self._serial_session = None
        self._video = cast(Any, RecordingVideo())
        self.batch_inputs: list[int] = []

    def initialize(self) -> None:
        """Exercise the real one-reset preparer without importing vendor environments."""
        if self._prepared_observations is None:
            self._prepare_reset()

    @property
    def gym(self) -> RetainedGym:
        """Expose the concrete counted simulator double for deterministic assertions."""
        return cast(RetainedGym, self._gym_env)

    @property
    def actor(self) -> ContractActor:
        """Expose the original policy-shaped double without weakening production types."""
        return cast(ContractActor, self._actor)

    @property
    def video_observer(self) -> RecordingVideo:
        """Expose continuous-video evidence counters for boundary checks."""
        return cast(RecordingVideo, self._video)

    @property
    def diagnostic_observer(self) -> RecordingDiagnostics:
        """Expose actual diagnostic calls around the production policy loop."""
        return cast(RecordingDiagnostics, self._diagnostics)

    def _batch(self, observations: Any) -> Any:
        """Expose the exact observation from which each original policy segment resumes."""
        self.batch_inputs.append(int(observations.get("step", -1)))
        return observations

    def _record_video(self, step: int, observations: Any, info: dict[str, Any]) -> None:
        """Retain globally numbered sampling positions without simulated media."""
        del observations, info
        cast(RecordingVideo, self._video).steps.append(step)

    def _pair_arguments(
        self, text_context: dict[str, Any], invocations: Mapping[int, CanonicalInvocation]
    ) -> dict[str, Any]:
        """Use fresh vendor-shaped argument objects while preserving each frozen target."""
        del text_context
        return {
            f"agent_{agent}": SimpleNamespace(subtask_description=self._subtask(value))
            for agent, value in invocations.items()
        }

    def _assigned_arguments(
        self, text_context: dict[str, Any], invocation: CanonicalInvocation
    ) -> dict[str, Any]:
        """Keep the real single-policy path independent of vendor AgentArguments import."""
        del text_context, invocation
        return {"agent_0": object(), "agent_1": object()}


def _retained_invocations() -> dict[int, CanonicalMobilityInvocation]:
    """Bind both unrelated targets to complete independent topology and exact attempts."""
    slots = [_slot("first", "first-actor"), _slot("second", "second-actor")]
    return {
        agent: replace(
            CanonicalMobilityInvocation.from_request(_session_request("m", target, task, slots)),
            attempt_id=f"original-{agent}",
        )
        for agent, (target, task) in enumerate((("north", "first"), ("south", "second")))
    }


@pytest.mark.parametrize("completed_first", [False, True])
def test_real_pair_loop_continues_one_world_with_global_budget_and_completed_peer(
    tmp_path: Path, completed_first: bool
) -> None:
    """Stop before the next act, then repeat only cancelled slots from actual saved observations."""
    runtime = RetainedRuntimeHarness(tmp_path, completed_first=completed_first)
    runtime.initialize()
    invocations = _retained_invocations()
    outcomes, first = runtime.execute_pair(
        invocations, lambda: runtime.gym.steps >= 1, lambda agent, detail: None
    )
    assert first["continuation"]["phase"] == "stopped"
    assert runtime.gym.resets == runtime.gym.steps == runtime.actor.calls == 1
    assert runtime.video_observer.closures == []
    assert runtime.diagnostic_observer.stops == [(1, "cancellation", 0)]
    assert runtime.diagnostic_observer.terminals == []
    replacements = {
        agent: replace(invocations[agent], attempt_id=f"retry-{agent}")
        for agent, outcome in outcomes.items()
        if outcome.state == "CANCELLED"
    }
    before = [agent.llm_model.calls for agent in runtime.agents]
    later, final = runtime.resume_pair(replacements, lambda: False, lambda agent, detail: None)
    assert runtime.gym.resets == 1
    assert runtime.gym.steps == final["identity"]["simulator_steps"] == 3
    assert runtime.actor.calls == 3
    assert runtime.batch_inputs == [0, 1, 1, 2, 3]
    assert final["continuation"]["phase"] == "closed"
    assert later[1].state == "COMPLETED"
    if completed_first:
        assert later[0] == outcomes[0]
        assert runtime.agents[0].llm_model.calls == before[0]
        assigned = json.loads((tmp_path / "evidence/stage2-assignment-segment-1.json").read_text())
        assert assigned["assignments"]["0"]["provider_active"] is False
        assert assigned["assignments"]["0"]["subtask_description"] == "Nothing to do"
        assert assigned["assignments"]["1"]["provider_active"] is True
    assert [
        policy._high_level_policy.llm_agent for policy in runtime.actor._active_policies
    ] == runtime.agents
    assert runtime.diagnostic_observer.persisted_steps == [1, 2, 3]
    assert runtime.video_observer.steps == [0, 1, 2, 3]
    assert runtime.video_observer.closures == ["episode_done"]
    assert not any(outcome.benchmark_task_achieved for outcome in later.values())


def test_continued_pair_wrong_target_still_fails_before_another_step(tmp_path: Path) -> None:
    """Continuation reinstalls exact new-attempt guards without repairing model output."""
    runtime = RetainedRuntimeHarness(tmp_path)
    runtime.initialize()
    invocations = _retained_invocations()
    runtime.execute_pair(invocations, lambda: runtime.gym.steps >= 1, lambda agent, detail: None)
    runtime.agents[1].llm_model.target = "north"
    outcomes, summary = runtime.resume_pair(
        {1: replace(invocations[1], attempt_id="retry")}, lambda: False, lambda agent, detail: None
    )
    assert outcomes[0].state == "COMPLETED"
    assert outcomes[1].state == "FAILED"
    assert outcomes[1].terminal_basis == "local-contract-failure"
    assert runtime.gym.steps == 1
    assert summary["continuation"]["phase"] == "closed"


def test_pair_budget_is_not_renewed_and_failure_cannot_continue(tmp_path: Path) -> None:
    """The resumed policy gets only remaining world steps, without another episode reset."""
    runtime = RetainedRuntimeHarness(tmp_path, final_step=100, completed_first=False)
    runtime.initialize()
    invocations = _retained_invocations()
    runtime.execute_pair(invocations, lambda: runtime.gym.steps >= 1, lambda agent, detail: None)
    replacements = {
        agent: replace(value, attempt_id=f"new-{agent}") for agent, value in invocations.items()
    }
    outcomes, summary = runtime.resume_pair(replacements, lambda: False, lambda agent, detail: None)
    assert runtime.gym.resets == 1 and runtime.gym.steps == 3
    assert {outcome.terminal_basis for outcome in outcomes.values()} == {"step-budget-exhausted"}
    assert summary["continuation"]["phase"] == "closed"
    with pytest.raises(IntegrationError):
        runtime.resume_pair(
            {
                agent: replace(value, attempt_id=f"extra-{agent}")
                for agent, value in invocations.items()
            },
            lambda: False,
            lambda agent, detail: None,
        )
    assert runtime.gym.steps == 3


@pytest.mark.parametrize("stopped_step", [0, 4, 5])
def test_completed_pair_settling_respects_retained_global_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stopped_step: int
) -> None:
    """Settling cannot step beyond the original budget even if Gym never reports done."""
    runtime = RetainedRuntimeHarness(tmp_path, completed_first=False, final_step=100, max_steps=6)
    runtime.initialize()
    invocations = _retained_invocations()
    runtime.execute_pair(
        invocations, lambda: runtime.gym.steps >= stopped_step, lambda agent, detail: None
    )
    assert runtime.gym.steps == stopped_step

    def finish_skills(action: object) -> Any:
        """Finish both local skills while keeping official success and episode done false."""
        del action
        runtime.gym.steps += 1
        return (
            {
                "step": runtime.gym.steps,
                "agent_0_has_finished_oracle_nav": [1],
                "agent_1_has_finished_oracle_nav": [1],
            },
            0.0,
            False,
            {"pddl_success": False},
        )

    monkeypatch.setattr(runtime.gym, "step", finish_skills)
    replacements = {
        agent: replace(value, attempt_id=f"resumed-{agent}") for agent, value in invocations.items()
    }
    outcomes, summary = runtime.resume_pair(replacements, lambda: False, lambda agent, detail: None)
    assert runtime.gym.resets == 1
    assert runtime.gym.steps == summary["identity"]["simulator_steps"] == 6
    assert runtime.actor.calls == stopped_step + 1
    assert summary["final_info"]["pddl_success"] is False
    assert all(outcome.local_skill_completed for outcome in outcomes.values())
    assert all(not outcome.benchmark_task_achieved for outcome in outcomes.values())
    assert runtime.video_observer.steps == list(range(7))
    assert runtime.diagnostic_observer.terminals == [(6, "step_budget_exhausted")]


def test_stale_pair_resume_preserves_stopped_world_without_action(tmp_path: Path) -> None:
    """Invalid local admission does not erase actual Cancelled or step the world."""
    runtime = RetainedRuntimeHarness(tmp_path)
    runtime.initialize()
    invocations = _retained_invocations()
    original, _ = runtime.execute_pair(
        invocations, lambda: runtime.gym.steps >= 1, lambda agent, detail: None
    )
    with pytest.raises(IntegrationError):
        runtime.resume_pair(
            {1: replace(invocations[1], attempt_id="new", parameters={"destination": "north"})},
            lambda: False,
            lambda agent, detail: None,
        )
    assert runtime._pair_session is not None and runtime._pair_session.phase == "stopped"
    assert runtime._pair_session.outcomes == original
    assert runtime.gym.steps == 1


def test_serial_stop_resumes_same_task_in_same_world(tmp_path: Path) -> None:
    """A one-Actor cancelled Task repeats its exact intent without jumping to a later Task."""
    runtime = RetainedRuntimeHarness(tmp_path, completed_first=False)
    runtime.initialize()
    slots = [_slot("first", "actor"), _slot("next", "actor", ["first"])]
    invocation = replace(
        CanonicalMobilityInvocation.from_request(_session_request("m", "north", "first", slots)),
        attempt_id="original",
    )
    outcome, first = runtime.execute_serial(
        invocation, 0, lambda: runtime.gym.steps >= 1, lambda agent, detail: None, False
    )
    assert outcome.state == "CANCELLED"
    assert first["continuation"]["phase"] == "stopped"
    other = replace(invocation, task_id="next", attempt_id="wrong-next")
    with pytest.raises(IntegrationError):
        runtime.execute_serial(other, 0, lambda: False, lambda agent, detail: None, True)
    assert runtime.gym.steps == 1
    result, final = runtime.execute_serial(
        replace(invocation, attempt_id="replacement"),
        0,
        lambda: False,
        lambda agent, detail: None,
        False,
    )
    assert result.state == "COMPLETED"
    assert final["identity"]["simulator_steps"] == 3
    assert final["identity"]["episode_terminated"] is True
    assert final["continuation"]["continuations"] == 1
    assert runtime.gym.resets == 1
    assert runtime.diagnostic_observer.stops == [(1, "cancellation", 0)]


def _wait_terminal(endpoint: NodeEndpoint, handle: str) -> dict[str, object]:
    """Wait boundedly for real test worker projection, never synthesizing a terminal fact."""
    for _ in range(1000):
        record = _execution(endpoint.store(), handle)
        if record["state"] in TERMINAL:
            return record
        time.sleep(0.001)
    raise AssertionError("local execution did not reach its observed terminal")


def _retained_endpoints(
    tmp_path: Path, *, completed_first: bool = True, monotonic: Callable[[], float] = time.monotonic
) -> tuple[RetainedRuntimeHarness, SharedWorldCoordinator, NodeEndpoint, NodeEndpoint]:
    """Connect actual coordinator and runtime to two durable Node endpoint stores."""
    runtime = RetainedRuntimeHarness(tmp_path, completed_first=completed_first)
    coordinator = SharedWorldCoordinator(
        InProcessWorldService(runtime),
        2.0,
        tmp_path / "evidence",
        retain_stopped_session=True,
        max_steps=3,
        wait_poll_s=0.001,
        monotonic=monotonic,
    )
    endpoints = [
        NodeEndpoint(
            f"node-{agent}",
            agent,
            ExecutionStore(tmp_path / f"{agent}.sqlite3"),
            coordinator,
            retain_stopped_session=True,
        )
        for agent in range(2)
    ]
    return runtime, coordinator, endpoints[0], endpoints[1]


@pytest.mark.parametrize("completed_first", [False, True])
def test_coordinator_continuation_preserves_old_handles_and_final_evidence(
    tmp_path: Path, completed_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only genuine stop enables fresh handles; paused metrics never become final evidence."""
    runtime, coordinator, first, second = _retained_endpoints(
        tmp_path, completed_first=completed_first
    )
    invocations = _retained_invocations()
    original = runtime.execute_pair

    def stop_after_first(
        values: dict[int, CanonicalMobilityInvocation],
        cancellation: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Inject a durable real endpoint cancel only after one actual shared step."""

        def stop() -> bool:
            """Accept cancel intent and then observe it at the normal loop boundary."""
            if runtime.gym.steps >= 1:
                active = second.store().active_execution()
                assert active is not None
                second.cancel({"execution_id": active["execution_id"]})
            return cancellation()

        return original(values, stop, running)

    monkeypatch.setattr(runtime, "execute_pair", stop_after_first)
    endpoints = {0: first, 1: second}
    handles = {
        agent: str(endpoints[agent].submit({"invocation": value.as_dict()})["execution_id"])
        for agent, value in invocations.items()
    }
    records = {agent: _wait_terminal(endpoints[agent], handle) for agent, handle in handles.items()}
    evidence = tmp_path / "evidence"
    assert not (evidence / "shared-world-summary.json").exists()
    assert not (evidence / "task-verifier-verdict.json").exists()
    assert json.loads((evidence / "shared-world-segment-0.json").read_text())["is_final"] is False
    monkeypatch.setattr(runtime, "execute_pair", original)
    replacements = {
        agent: replace(invocations[agent], attempt_id=f"replacement-{agent}")
        for agent, record in records.items()
        if record["state"] == "CANCELLED"
    }
    fresh = {
        agent: str(endpoints[agent].submit({"invocation": value.as_dict()})["execution_id"])
        for agent, value in replacements.items()
    }
    try:
        for agent, handle in fresh.items():
            assert _wait_terminal(endpoints[agent], handle)["state"] == "COMPLETED"
        assert {
            agent: _execution(endpoints[agent].store(), handle) for agent, handle in handles.items()
        } == records
        final = json.loads((evidence / "shared-world-summary.json").read_text())
        assert final["official_pddl_success"] is False
        assert final["identity"]["episode_reset_count"] == 1
        assert final["identity"]["simulator_steps"] == 3
        assert len(final["execution_segments"]) == 2
        assert runtime.gym.resets == 1
        assert first.accept({"invocation": invocations[0].as_dict()})["execution_id"] == handles[0]
        with pytest.raises(IntegrationError, match="consumed"):
            second.accept({"invocation": replace(invocations[1], attempt_id="extra").as_dict()})
    finally:
        coordinator.shutdown()


def test_process_transport_resumes_existing_child_without_another_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise actual Pipe commands and the child handler with no vendor/model dependencies."""
    import multiprocessing

    runtime = RetainedRuntimeHarness(tmp_path)
    monkeypatch.setattr(
        shared_world_module, "SharedEmosStage2Runtime", lambda config, agents: runtime
    )
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(
        target=shared_world_module._child_world_process,
        args=(child, runtime._config, (0, 1)),
        daemon=True,
    )
    thread.start()
    assert parent.poll(2)
    assert parent.recv()[0] == "READY"
    world = ProcessWorldService(cast(CrabAgentBackendConfig, runtime._config), (0, 1))
    world._connection = parent
    world._process = thread
    world._ready = True
    invocations = _retained_invocations()
    first_step = threading.Event()

    def pause_first_step() -> None:
        """Let the parent observe cancel during a counted action without polling the Provider."""
        if runtime.gym.steps == 1:
            first_step.wait(timeout=2)

    runtime.gym.on_step = pause_first_step

    def cancellation_requested() -> bool:
        """Release the step and send one actual child CANCEL command from the parent."""
        if runtime.gym.steps >= 1:
            first_step.set()
            return True
        return False

    try:
        outcomes, stopped = world.run_pair(
            invocations, cancellation_requested, lambda agent, detail: None
        )
        assert stopped["continuation"]["phase"] == "stopped"
        assert outcomes[1].state == "CANCELLED"
        assert runtime.gym.steps == 1
        current_world = world._process
        outcomes, final = world.resume_pair(
            {1: replace(invocations[1], attempt_id="retry")},
            lambda: False,
            lambda agent, detail: None,
        )
        assert world._process is current_world
        assert outcomes[1].state == "COMPLETED"
        assert runtime.gym.resets == 1 and runtime.gym.steps == 3
        assert final["identity"]["simulator_steps"] == 3
        assert final["official_metrics"]["pddl_success"] is False
    finally:
        parent.send(("CLOSE", None))
        thread.join(timeout=2)
        parent.close()
    assert not thread.is_alive()


def test_incomplete_continuation_expires_without_running_half_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing second replacement cannot renew the wait window or manufacture success."""
    now = [0.0]
    runtime, coordinator, first, second = _retained_endpoints(
        tmp_path, completed_first=False, monotonic=lambda: now[0]
    )
    original = runtime.execute_pair

    def stop_after_one(
        values: dict[int, CanonicalMobilityInvocation],
        unused: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Drive one actual cancelled production segment, isolated from timing policy."""
        del unused
        return original(values, lambda: runtime.gym.steps >= 1, running)

    monkeypatch.setattr(runtime, "execute_pair", stop_after_one)
    invocations = _retained_invocations()
    first_handle = str(first.submit({"invocation": invocations[0].as_dict()})["execution_id"])
    second_handle = str(second.submit({"invocation": invocations[1].as_dict()})["execution_id"])
    assert _wait_terminal(first, first_handle)["state"] == "CANCELLED"
    assert _wait_terminal(second, second_handle)["state"] == "CANCELLED"
    pending = str(
        first.submit({"invocation": replace(invocations[0], attempt_id="retry").as_dict()})[
            "execution_id"
        ]
    )
    assert first.store().get(pending) is not None
    assert runtime.gym.steps == 1
    gym = runtime.gym
    now[0] = 3.0
    try:
        assert _wait_terminal(first, pending)["state"] == "FAILED"
        assert gym.resets == 1 and gym.steps == 1
        assert coordinator._pair_session is not None and coordinator._pair_session.phase == "closed"
        assert not (tmp_path / "evidence/shared-world-summary.json").exists()
        assert (
            json.loads((tmp_path / "evidence/retained-session-state.json").read_text())["reason"]
            == "joint continuation window expired"
        )
    finally:
        coordinator.shutdown()


def _http_post(server: HabitatBridgeServer, path: str, body: object) -> dict[str, Any]:
    """Use the actual loopback HTTP workflow boundary with no authenticated remote calls."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        value: Any = json.load(response)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _http_get(server: HabitatBridgeServer, path: str) -> dict[str, Any]:
    """Read one capability route through the actual loopback HTTP boundary."""
    with urllib.request.urlopen(
        f"http://127.0.0.1:{server.server_address[1]}{path}", timeout=2
    ) as response:
        value: Any = json.load(response)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_http_capability_route_preserves_exact_operation_identity(tmp_path: Path) -> None:
    """The HTTP path identity is checked before readiness can be reported as READY."""
    runtime, coordinator, endpoint_a, _, _ = _world(tmp_path)
    server = HabitatBridgeServer(("127.0.0.1", 0), endpoint_a)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        supported = _http_get(server, "/v1/capabilities/mobility.navigate@v1")
        legacy_route = _http_get(server, "/v1/capabilities/mobility.navigate")
        unsupported = _http_get(server, "/v1/capabilities/object.relocate@v1")
        assert supported["state"] == "READY"
        assert supported["operation"] == "mobility.navigate@v1"
        assert legacy_route["state"] == "READY"
        assert legacy_route["operation"] == "mobility.navigate@v1"
        assert unsupported["state"] == "UNAVAILABLE"
        assert unsupported["operation"] == "object.relocate@v1"
    finally:
        server.shutdown()
        server.server_close()
        coordinator.shutdown()


def test_http_cancel_receipt_precedes_real_group_stop_and_fresh_continuation(
    tmp_path: Path,
) -> None:
    """HTTP acceptance, observed stop and resumed execution remain distinct measured facts."""
    runtime, coordinator, first, second = _retained_endpoints(tmp_path)
    first_step = threading.Event()
    release_step = threading.Event()

    def wait_for_http_cancel() -> None:
        """Hold one real step until the external test caller observes the cancellation receipt."""
        if runtime.gym.steps == 1:
            first_step.set()
            assert release_step.wait(2)

    runtime.gym.on_step = wait_for_http_cancel
    servers = [HabitatBridgeServer(("127.0.0.1", 0), endpoint) for endpoint in (first, second)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    invocations = _retained_invocations()
    try:
        handles = [
            _http_post(
                servers[agent], "/v1/executions", {"invocation": invocations[agent].as_dict()}
            )["execution_id"]
            for agent in range(2)
        ]
        assert first_step.wait(2)
        receipt = _http_post(servers[1], "/v1/executions/cancel", {"execution_id": handles[1]})
        assert receipt["cancel_requested"] is True
        assert receipt["state"] == "RUNNING"
        assert (
            _http_post(servers[1], "/v1/executions/status", {"execution_id": handles[1]})["state"]
            == "RUNNING"
        )
        release_step.set()
        assert _wait_terminal(first, handles[0])["state"] == "COMPLETED"
        assert _wait_terminal(second, handles[1])["state"] == "CANCELLED"
        replacement = replace(invocations[1], attempt_id="new-attempt")
        new_handle = _http_post(
            servers[1], "/v1/executions", {"invocation": replacement.as_dict()}
        )["execution_id"]
        assert new_handle != handles[1]
        assert _wait_terminal(second, new_handle)["state"] == "COMPLETED"
        assert (
            _http_post(servers[1], "/v1/executions/status", {"execution_id": handles[1]})["state"]
            == "CANCELLED"
        )
        assert runtime.gym.resets == 1 and runtime.gym.steps == 3
        summary = json.loads((tmp_path / "evidence/shared-world-summary.json").read_text())
        assert summary["official_pddl_success"] is False
    finally:
        release_step.set()
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        coordinator.shutdown()


def test_retained_world_restart_is_rejected_before_reset(tmp_path: Path) -> None:
    """A second coordinator cannot reset a replacement world behind old continuation evidence."""
    runtime, coordinator, _, _ = _retained_endpoints(tmp_path)
    replacement = RetainedRuntimeHarness(tmp_path)
    try:
        with pytest.raises(IntegrationError, match="unused durable"):
            SharedWorldCoordinator(
                InProcessWorldService(replacement),
                2.0,
                tmp_path / "evidence",
                retain_stopped_session=True,
                max_steps=3,
            )
        assert replacement.gym.resets == 0
        assert runtime._prepared_observations is not None
    finally:
        coordinator.shutdown()


def test_lost_child_continuation_cannot_spawn_replacement_world(tmp_path: Path) -> None:
    """Unavailable child means unavailable retained context, never a reset fallback."""
    runtime = RetainedRuntimeHarness(tmp_path)
    world = ProcessWorldService(cast(CrabAgentBackendConfig, runtime._config), (0, 1))
    world._process = SimpleNamespace(is_alive=lambda: False)
    world._connection = object()
    world._ready = True
    assert not world.is_ready()
    with pytest.raises(IntegrationError, match="not alive"):
        world.resume_pair(
            {1: replace(_retained_invocations()[1], attempt_id="new")},
            lambda: False,
            lambda agent, detail: None,
        )
    assert runtime.gym.resets == 0


def test_actual_resume_exception_keeps_primary_cause_and_closes_session(tmp_path: Path) -> None:
    """An original actor fault cannot become Cancelled, Completed or a new reset opportunity."""
    runtime = RetainedRuntimeHarness(tmp_path)
    runtime.initialize()
    invocations = _retained_invocations()
    runtime.execute_pair(invocations, lambda: runtime.gym.steps >= 1, lambda agent, detail: None)
    runtime.actor.fail_at = 2
    with pytest.raises(IntegrationError, match="actor failure sentinel") as caught:
        runtime.resume_pair(
            {1: replace(invocations[1], attempt_id="new")},
            lambda: False,
            lambda agent, detail: None,
        )
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert runtime.gym.resets == 1 and runtime.gym.steps == 1
    assert runtime._pair_session is not None and runtime._pair_session.phase == "closed"
    assert runtime.diagnostic_observer.terminals == [
        (1, "execution_exception:actor_act:RuntimeError")
    ]
    assert [
        policy._high_level_policy.llm_agent for policy in runtime.actor._active_policies
    ] == runtime.agents


def test_stop_snapshot_and_segment_write_failures_do_not_change_actual_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional failed archival cannot hide stop evidence or create a simulator failure."""
    runtime, coordinator, first, second = _retained_endpoints(tmp_path)
    original_execute = runtime.execute_pair

    def stop(
        values: dict[int, CanonicalMobilityInvocation],
        unused: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Use a measured stop despite intentionally unavailable diagnostic storage."""
        del unused
        return original_execute(values, lambda: runtime.gym.steps >= 1, running)

    def broken_stop(*args: object) -> None:
        """Emulate diagnostic disk failure after the simulator has really stopped."""
        del args
        raise OSError("test storage unavailable")

    original_write = coordinator._write_json

    def broken_archive(name: str, value: object) -> None:
        """Fail only optional segment/state archival; final summary remains independent."""
        if name.startswith(("shared-world-segment-", "retained-session-state")):
            raise OSError("test segment write unavailable")
        original_write(name, value)

    monkeypatch.setattr(runtime, "execute_pair", stop)
    monkeypatch.setattr(runtime.diagnostic_observer, "record_stop", broken_stop)
    monkeypatch.setattr(coordinator, "_write_json", broken_archive)
    values = _retained_invocations()
    old_first = str(first.submit({"invocation": values[0].as_dict()})["execution_id"])
    old_second = str(second.submit({"invocation": values[1].as_dict()})["execution_id"])
    try:
        assert _wait_terminal(first, old_first)["state"] == "COMPLETED"
        assert _wait_terminal(second, old_second)["state"] == "CANCELLED"
        monkeypatch.setattr(runtime, "execute_pair", original_execute)
        new_handle = str(
            second.submit({"invocation": replace(values[1], attempt_id="new").as_dict()})[
                "execution_id"
            ]
        )
        assert _wait_terminal(second, new_handle)["state"] == "COMPLETED"
        assert runtime.gym.resets == 1 and runtime.gym.steps == 3
        assert len(coordinator._segment_history) == 2
        summary = json.loads((tmp_path / "evidence/shared-world-summary.json").read_text())
        assert summary["continuation_archival"] == {
            "complete": False,
            "failure_counts": {"segment": 2, "state": 2},
        }
    finally:
        coordinator.shutdown()


def test_serial_next_task_carries_used_world_continuation_budget(tmp_path: Path) -> None:
    """A normal next Task cannot reset the retry ceiling or step history of this world."""
    runtime = RetainedRuntimeHarness(tmp_path, max_steps=10, final_step=100)
    runtime.initialize()
    slots = [_slot("first", "actor"), _slot("next", "actor", ["first"])]
    first = replace(
        CanonicalMobilityInvocation.from_request(_session_request("m", "north", "first", slots)),
        attempt_id="initial",
    )
    runtime.execute_serial(first, 0, lambda: True, lambda agent, detail: None, False)
    complete, _ = runtime.execute_serial(
        replace(first, attempt_id="retry-first"),
        0,
        lambda: False,
        lambda agent, detail: None,
        False,
    )
    assert complete.state == "COMPLETED"
    assert runtime.gym.steps == 1
    next_task = replace(
        CanonicalMobilityInvocation.from_request(_session_request("m", "north", "next", slots)),
        attempt_id="next-initial",
    )
    cancelled, snapshot = runtime.execute_serial(
        next_task, 0, lambda: True, lambda agent, detail: None, True
    )
    assert cancelled.state == "CANCELLED"
    assert snapshot["continuation"]["continuations"] == 1
    assert snapshot["identity"]["simulator_steps"] == 1
    assert runtime.diagnostic_observer.stops == [(0, "cancellation", 0), (1, "cancellation", 1)]
    assert runtime.gym.resets == 1


def test_retained_configuration_mismatch_is_rejected_before_world_start(tmp_path: Path) -> None:
    """Startup mode/budget agreement precedes readiness and any physical reset."""
    runtime = RetainedRuntimeHarness(tmp_path)
    with pytest.raises(IntegrationError, match="does not match"):
        SharedWorldCoordinator(
            InProcessWorldService(runtime),
            2.0,
            tmp_path / "evidence",
            retain_stopped_session=True,
            max_steps=100,
        )
    assert runtime.gym.resets == 0
    assert not (tmp_path / "evidence/retained-world-owner.json").exists()


def test_serial_coordinator_resumes_cancelled_task_before_releasing_next_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parent/child admission agrees on serial continuation and preserves exact attempts."""
    runtime = RetainedRuntimeHarness(tmp_path, final_step=100, max_steps=5)
    coordinator = SharedWorldCoordinator(
        InProcessWorldService(runtime),
        2.0,
        tmp_path / "evidence",
        retain_stopped_session=True,
        max_steps=5,
        wait_poll_s=0.001,
    )
    endpoint = NodeEndpoint(
        "node",
        0,
        ExecutionStore(tmp_path / "serial.sqlite3"),
        coordinator,
        retain_stopped_session=True,
    )
    slots = [_slot("first", "actor"), _slot("next", "actor", ["first"])]
    first = replace(
        CanonicalMobilityInvocation.from_request(_session_request("m", "north", "first", slots)),
        attempt_id="original",
    )
    following = replace(
        CanonicalMobilityInvocation.from_request(_session_request("m", "north", "next", slots)),
        attempt_id="following",
    )
    original = runtime.execute_serial

    def cancelled_first(
        invocation: CanonicalMobilityInvocation,
        agent_id: int,
        cancellation: Callable[[], bool],
        running: Callable[[int, str], None],
        final_slot: bool,
    ) -> tuple[LocalExecutionOutcome, dict[str, Any]]:
        """Accept durable cancellation after RUNNING, before the first physical action."""

        def cancel_on_running(agent: int, detail: str) -> None:
            """Record the actual accepted cancel while preserving normal terminal reduction."""
            running(agent, detail)
            active = endpoint.store().active_execution()
            assert active is not None
            endpoint.cancel({"execution_id": active["execution_id"]})

        return original(invocation, agent_id, cancellation, cancel_on_running, final_slot)

    monkeypatch.setattr(runtime, "execute_serial", cancelled_first)
    try:
        old = str(endpoint.submit({"invocation": first.as_dict()})["execution_id"])
        original_record = _wait_terminal(endpoint, old)
        assert original_record["state"] == "CANCELLED"
        assert runtime.gym.steps == 0
        with pytest.raises(IntegrationError, match="consumed"):
            endpoint.accept({"invocation": following.as_dict()})
        monkeypatch.setattr(runtime, "execute_serial", original)
        fresh = str(
            endpoint.submit({"invocation": replace(first, attempt_id="fresh").as_dict()})[
                "execution_id"
            ]
        )
        assert _wait_terminal(endpoint, fresh)["state"] == "COMPLETED"
        last = str(endpoint.submit({"invocation": following.as_dict()})["execution_id"])
        assert _wait_terminal(endpoint, last)["state"] == "COMPLETED"
        assert _execution(endpoint.store(), old) == original_record
        summary = json.loads((tmp_path / "evidence/shared-world-summary.json").read_text())
        assert [segment["attempt_id"] for segment in summary["serial_task_outcomes"]] == [
            "original",
            "fresh",
            "following",
        ]
        assert summary["identity"]["episode_reset_count"] == 1
        assert summary["identity"]["simulator_steps"] == 2
        assert summary["official_pddl_success"] is False
        assert len(summary["execution_segments"]) == 3
    finally:
        coordinator.shutdown()


def _single_idle_loop(
    runtime: SingleIdleContractLoopHarness,
    actor: ContractActor,
    invocation: CanonicalMobilityInvocation,
    gym_env: Any,
    cancellation_requested: Callable[[], bool],
) -> LocalExecutionOutcome:
    """Run one assigned task through the production single-policy boundary."""
    return runtime._policy_loop(
        {},
        {"episode_id": "generic"},
        {"agent_0": object(), "agent_1": object()},
        invocation,
        (0.0, 0.0, 0.0),
        "scene",
        actor,
        SimpleNamespace(masks_shape=(1,)),
        gym_env,
        runtime._habitat_env,
        cancellation_requested,
    )


def _single_idle_setup(
    tmp_path: Path, *, assigned_agent_id: int = 0, violation_at: int | None = None
) -> tuple[SingleIdleContractLoopHarness, ContractActor, list[ContractAgent]]:
    """Build one fake shared world with exactly one Control-assigned agent."""
    runtime = SingleIdleContractLoopHarness(tmp_path, RecordingDiagnostics())
    runtime._config = SimpleNamespace(
        max_steps=3, step_period_ms=0, episode_id="generic", agent_id=assigned_agent_id
    )
    runtime._serial_steps = 0
    runtime._agent_position = lambda: (0.0, 0.0, 0.0)  # type: ignore[method-assign]
    runtime._habitat_env = SimpleNamespace(
        episode_over=False,
        get_metrics=lambda: {"pddl_success": False},
    )
    agents = [
        ContractAgent(
            f"agent_{agent_id}",
            ContractModel(
                ("north" if agent_id == 0 else "south")
                if agent_id == assigned_agent_id
                else "unassigned-wrong-target",
                violation_at if agent_id == assigned_agent_id else None,
            ),
        )
        for agent_id in range(2)
    ]
    actor = ContractActor(agents)
    runtime._actor = actor
    return runtime, actor, agents


@pytest.mark.parametrize("assigned_agent_id", [0, 1])
@pytest.mark.parametrize("progress_enabled", [False, True])
def test_unassigned_model_is_not_called_while_assigned_task_completes(
    tmp_path: Path, assigned_agent_id: int, progress_enabled: bool
) -> None:
    """One Task can finish locally without an idle model veto or fabricated PDDL success."""
    runtime, actor, agents = _single_idle_setup(tmp_path, assigned_agent_id=assigned_agent_id)
    runtime._config.progress_directory = tmp_path / "progress" if progress_enabled else None
    gym_calls: list[object] = []
    destination = "north" if assigned_agent_id == 0 else "south"

    def step(action: object) -> Any:
        """Return a real local skill finish but an officially false benchmark state."""
        gym_calls.append(action)
        return (
            {f"agent_{assigned_agent_id}_has_finished_oracle_nav": [1]},
            0.0,
            False,
            {"pddl_success": False},
        )

    invocation = replace(
        CanonicalMobilityInvocation.from_request(_request("m", destination, "first")),
        attempt_id="single-attempt",
    )
    outcome = _single_idle_loop(
        runtime, actor, invocation, SimpleNamespace(step=step), lambda: False
    )
    assert outcome.state == "COMPLETED"
    assert outcome.local_skill_completed and not outcome.benchmark_task_achieved
    assert actor.calls == len(gym_calls) == 1
    assert outcome.skill_sequence == (
        "nav_to_obj|wait" if assigned_agent_id == 0 else "wait|nav_to_obj",
    )
    assert agents[assigned_agent_id].dispatches == 1
    idle_agent_id = 1 - assigned_agent_id
    assert agents[idle_agent_id].dispatches == agents[idle_agent_id].llm_model.calls == 0
    assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == agents
    idle = json.loads(
        (tmp_path / "evidence" / f"idle-endpoint-{invocation.request_key()[:16]}.json").read_text()
    )
    assert idle["idle_agents"][0]["local_wait_selections"] == 1
    assert idle["idle_agents"][0]["provider_calls"] == 0
    assert idle["idle_agents"][0]["agent_name"] == f"agent_{idle_agent_id}"
    assert (
        json.loads((tmp_path / "evidence" / "controlled-outcome.json").read_text())[
            "benchmark_task_achieved"
        ]
        is False
    )


def test_assigned_wrong_target_still_fails_before_physical_step(tmp_path: Path) -> None:
    """Passive siblings do not weaken the assigned agent's canonical target guard."""
    runtime, actor, agents = _single_idle_setup(tmp_path, violation_at=1)
    gym_env = StepEnvironment(1)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "north", "wrong"))
    outcome = _single_idle_loop(runtime, actor, invocation, gym_env, lambda: False)
    assert outcome.state == "FAILED"
    assert outcome.terminal_basis == "local-contract-failure"
    assert gym_env.calls == 0
    assert agents[0].dispatches == 0
    assert agents[1].llm_model.calls == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "evidence" / "stage2-actions.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["agent_name"] == "agent_0" and rows[-1]["decision"] == "rejected"
    assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == agents


def test_cancellation_before_step_restores_passive_binding(tmp_path: Path) -> None:
    """Control cancellation performs no model call or Gym step on either endpoint."""
    runtime, actor, agents = _single_idle_setup(tmp_path)
    gym_env = StepEnvironment(1)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "north", "cancel"))
    outcome = _single_idle_loop(runtime, actor, invocation, gym_env, lambda: True)
    assert outcome.state == "CANCELLED"
    assert gym_env.calls == 0
    assert [agent.llm_model.calls for agent in agents] == [0, 0]
    assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == agents


def test_gym_failure_preserves_original_error_and_idle_evidence(tmp_path: Path) -> None:
    """An execution exception still restores both policies and records prior idle selection."""
    runtime, actor, agents = _single_idle_setup(tmp_path)
    invocation = CanonicalMobilityInvocation.from_request(_request("m", "north", "failure"))
    with pytest.raises(RuntimeError, match="gym step failure sentinel"):
        _single_idle_loop(runtime, actor, invocation, StepEnvironment(1), lambda: False)
    assert agents[0].dispatches == 1
    assert agents[1].llm_model.calls == 0
    assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == agents
    idle = json.loads(
        (tmp_path / "evidence" / f"idle-endpoint-{invocation.request_key()[:16]}.json").read_text()
    )
    assert idle["idle_agents"][0]["local_wait_selections"] == 1


def test_serial_tasks_rebind_same_actor_without_idle_model_calls(tmp_path: Path) -> None:
    """Two Control-dispatched segments retain one actor and distinct Task evidence."""
    runtime, actor, agents = _single_idle_setup(tmp_path)
    gym_calls = 0

    def step(action: object) -> Any:
        """Finish the assigned navigation once per Control Task dispatch."""
        nonlocal gym_calls
        del action
        gym_calls += 1
        return ({"agent_0_has_finished_oracle_nav": [1]}, 0.0, False, {"pddl_success": False})

    for task_id, destination in [("first", "north"), ("second", "south")]:
        agents[0].llm_model.target = destination
        invocation = CanonicalMobilityInvocation.from_request(_request("m", destination, task_id))
        outcome = _single_idle_loop(
            runtime, actor, invocation, SimpleNamespace(step=step), lambda: False
        )
        assert outcome.state == "COMPLETED"
        assert (
            tmp_path / "evidence" / f"idle-endpoint-{invocation.request_key()[:16]}.json"
        ).exists()
        assert [policy._high_level_policy.llm_agent for policy in actor._active_policies] == agents
    assert gym_calls == runtime._serial_steps == 2
    assert agents[0].dispatches == 2
    assert agents[1].llm_model.calls == 0


@pytest.mark.parametrize("completed_first", [False, True])
@pytest.mark.parametrize("violation", ["wrong-target", "multiple-tools"])
@pytest.mark.parametrize("progress_enabled", [False, True])
def test_contract_violation_stops_before_gym_and_preserves_prior_completion(
    tmp_path: Path, completed_first: bool, violation: str, progress_enabled: bool
) -> None:
    """The real pair loop reports a local violation without erasing completed work."""
    diagnostics = RecordingDiagnostics()
    runtime = ContractLoopHarness(tmp_path, diagnostics)
    runtime._config = SimpleNamespace(max_steps=3, step_period_ms=0, episode_id="generic")
    runtime._config.progress_directory = tmp_path / "progress" if progress_enabled else None
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
        index: replace(
            CanonicalMobilityInvocation.from_request(_request("m", target, f"t{index}")),
            attempt_id=f"attempt-{index}",
        )
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

    def supported_operations(self) -> tuple[str, ...]:
        """Expose the exact navigation operations implemented by the stub."""
        return ("mobility.navigate@v1", "mobility.move@v1")

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


def _slot(
    task: str,
    actor: str,
    dependencies: list[str] | None = None,
    *,
    role: str = "role",
) -> dict[str, object]:
    """Create one independent accepted-plan slot for coordinator tests."""
    return {
        "task_id": task,
        "role_id": role,
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


def test_shared_world_readiness_reports_runtime_operations(tmp_path: Path) -> None:
    """Endpoint readiness must come from the world profile, not a hard-coded list."""
    runtime, coordinator, endpoint_a, _, _ = _world(tmp_path)
    try:
        readiness = endpoint_a.readiness()
        assert readiness["state"] == "READY"
        assert readiness["operation"] == "mobility.navigate@v1"
        assert readiness["operations"] == ["mobility.navigate@v1", "mobility.move@v1"]
        assert coordinator.supported_operations() == runtime.supported_operations()
        contract = coordinator.deployment_contract()
        assert contract["supported_operations"] == [
            "mobility.navigate@v1",
            "mobility.move@v1",
        ]
    finally:
        coordinator.shutdown()


def test_shared_world_readiness_is_scoped_to_requested_operation(tmp_path: Path) -> None:
    """Shared-world readiness cannot claim an unsupported relocation operation."""
    runtime, coordinator, endpoint_a, _, _ = _world(tmp_path)
    try:
        relocation = endpoint_a.readiness("object.relocate@v1")
        assert relocation["state"] == "UNAVAILABLE"
        assert relocation["operation"] == "object.relocate@v1"
        assert relocation["operations"] == [
            "mobility.navigate@v1",
            "mobility.move@v1",
        ]
        assert "not supported" in str(relocation["detail"])
        assert runtime.calls == 0
    finally:
        coordinator.shutdown()


def test_shared_world_rejects_relocation_before_queue_or_reset(tmp_path: Path) -> None:
    """The navigation-only shared deployment rejects relocation before durable admission."""
    runtime, coordinator, endpoint_a, _, evidence = _world(tmp_path)
    request = {
        "invocation": {
            "mission_id": "mission-relocation",
            "task_id": "task-relocation",
            "group_id": "group-relocation",
            "role_id": "role-relocation",
            "operation": "object.relocate@v1",
            "objective": "Move the selected object to the selected destination.",
            "parameters": {
                "object": "object:sample",
                "source": "receptacle:source",
                "destination": "receptacle:destination",
            },
            "resource_ids": ["slot-relocation"],
        }
    }
    try:
        with pytest.raises(IntegrationError, match=r"does not support 'object\.relocate@v1'"):
            endpoint_a.submit(request)
        assert runtime.calls == 0
        assert coordinator.episode_consumed() is False
        assert not list(evidence.glob("shared-world-start-admission*"))
        assert endpoint_a.store().all_executions() == []
    finally:
        coordinator.shutdown()


def test_shared_world_ready_payload_keeps_legacy_child_navigation_only() -> None:
    """A legacy string READY message cannot grant relocation capability."""
    detail, operations = shared_world_module._parse_ready_payload("legacy shared world")
    assert detail == "legacy shared world"
    assert operations == ("mobility.navigate@v1", "mobility.move@v1")


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
            "operation": "mobility.navigate@v1",
            "parameters": {"destination": "any_targets|0"},
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


@pytest.mark.parametrize("state", ["CANCELLED", "FAILED"])
def test_interrupted_serial_session_rejects_next_task_and_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """Serial topology cannot grant continuation after an interrupted consumed world."""
    runtime, _, endpoint, _, _ = _world(tmp_path)
    original = runtime.execute_serial

    def interrupted(
        invocation: CanonicalMobilityInvocation,
        agent_id: int,
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
        final_slot: bool,
    ) -> tuple[LocalExecutionOutcome, dict[str, object]]:
        """Return a real-shaped backend interruption, without changing coordinator policy."""
        outcome, summary = original(
            invocation, agent_id, cancellation_requested, running, final_slot
        )
        return replace(outcome, state=state, local_skill_completed=False), summary

    monkeypatch.setattr(runtime, "execute_serial", interrupted)
    slots = [_slot("first", "participant"), _slot("next", "participant")]
    first = endpoint.submit(_session_request("m", "first-target", "first", slots))
    for _ in range(1000):
        record = _execution(endpoint.store(), str(first["execution_id"]))
        if record["state"] in TERMINAL:
            break
        time.sleep(0.001)
    assert record["state"] == state
    for task in ("first", "next"):
        request = _session_request("m", "next-target", task, slots)
        cast(dict[str, object], request["invocation"])["attempt_id"] = "replacement"
        with pytest.raises(IntegrationError, match="consumed"):
            endpoint.accept(request)
    assert runtime.serial_calls == [(0, "first")]


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


@pytest.mark.parametrize("binding_failure", [False, True])
def test_serial_tasks_bind_current_diagnostic_attempt_without_changing_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_failure: bool
) -> None:
    """Each production serial segment binds its own Task before acting, with fail-soft evidence."""
    runtime = SerialRuntimeHarness(tmp_path)
    slots = [_slot("first", "participant"), _slot("next", "participant")]
    invocations = [
        replace(
            CanonicalMobilityInvocation.from_request(_session_request("m", target, task, slots)),
            attempt_id=f"attempt-{task}",
        )
        for task, target in (("first", "north"), ("next", "south"))
    ]
    bindings: list[dict[int, CanonicalMobilityInvocation]] = []

    def bind_navigation_invocations(values: dict[int, CanonicalMobilityInvocation]) -> None:
        """Observe the exact invocation at the production setup boundary, with optional failure."""
        assert runtime.observation_inputs == list(range(len(bindings)))
        bindings.append(dict(values))
        if binding_failure:
            raise OSError("diagnostic binding unavailable")

    monkeypatch.setattr(
        runtime._diagnostics,
        "bind_navigation_invocations",
        bind_navigation_invocations,
        raising=False,
    )
    for index, value in enumerate(invocations):
        outcome, _ = runtime.execute_serial(
            value, 0, lambda: False, lambda agent, detail: None, index == 1
        )
        assert outcome.state == "COMPLETED" and outcome.destination == value.destination
    assert bindings == [{0: invocations[0]}, {0: invocations[1]}]
    assert runtime.observation_inputs == [0, 1]
    assert cast(SerialGym, runtime._gym_env).resets == 1
    assert cast(SerialGym, runtime._gym_env).steps == 2
    assert cast(RecordingDiagnostics, runtime._diagnostics).persisted_steps == [1, 2]


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


def test_serial_session_merges_dependencies_across_roles(tmp_path: Path) -> None:
    """Cycle validation covers every role slot belonging to one logical task."""
    _, _, endpoint_a, _, _ = _world(tmp_path)
    slots = [
        _slot("first", "participant"),
        _slot("first", "participant", ["second"], role="role-b"),
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
    assert (
        endpoint_a.recovery_support()["operations"] == endpoint_b.recovery_support()["operations"]
    )
    with pytest.raises(IntegrationError, match="consumed"):
        endpoint_a.submit(_request("m", "new-target", "replacement-task"))
    assert runtime.calls == 1
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


@pytest.mark.parametrize("cancelled_agent", [0, 1])
def test_coordinator_aggregates_either_endpoint_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled_agent: int
) -> None:
    """Only one durable cancel intent is enough to stop the shared execution of both endpoints."""
    runtime, coordinator, endpoint_a, endpoint_b, _ = _world(tmp_path)
    original = runtime.execute_pair

    def cancelled(
        invocations: dict[int, CanonicalMobilityInvocation],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, object]]:
        """Inspect the production coordinator callback and emit its joint stop result."""
        assert cancellation_requested()
        outcomes, summary = original(invocations, cancellation_requested, running)
        stopped = {
            agent_id: replace(outcome, state="CANCELLED", local_skill_completed=False)
            for agent_id, outcome in outcomes.items()
        }
        return stopped, summary

    monkeypatch.setattr(runtime, "execute_pair", cancelled)
    endpoints = (endpoint_a, endpoint_b)
    handles = [
        str(
            endpoint.accept(_request("m", f"target-{agent_id}", f"task-{agent_id}"))["execution_id"]
        )
        for agent_id, endpoint in enumerate(endpoints)
    ]
    endpoints[cancelled_agent].cancel({"execution_id": handles[cancelled_agent]})
    coordinator._execute_pair(list(zip(endpoints, handles, strict=True)))
    for agent_id, (endpoint, handle) in enumerate(zip(endpoints, handles, strict=True)):
        record = _execution(endpoint.store(), handle)
        assert record["state"] == "CANCELLED"
        assert endpoint.store().cancellation_requested(handle) == (agent_id == cancelled_agent)
    assert runtime.calls == 1


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
