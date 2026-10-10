"""Offline real-loop conformance for sparse, concurrent and sequential endpoint activation."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios import live_bindings  # noqa: E402
from habitat_local_eaios.backend import LocalExecutionOutcome  # noqa: E402
from habitat_local_eaios.endpoint_registry import build_endpoint_registry  # noqa: E402
from habitat_local_eaios.idle_endpoint import PassiveIdleAgent  # noqa: E402
from habitat_local_eaios.live_world import (  # noqa: E402
    LiveEmosStage2Runtime,
    LiveSlotLedger,
    LiveWorldCoordinator,
)
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalInvocation,
    IntegrationError,
    parse_canonical_invocation,
)
from habitat_local_eaios.shared_world import NodeEndpoint  # noqa: E402
from habitat_local_eaios.store import ExecutionStore  # noqa: E402
from test_endpoint_registry import node_sources  # noqa: E402
from test_shared_world import (  # noqa: E402
    ContractAgent,
    ContractModel,
    FakeTensor,
    FakeTorch,
    RecordingDiagnostics,
    WaitSkillPolicy,
)


def invocation(
    task: int, actors: tuple[str, ...], *, observation: bool = False, coupled: bool = False
) -> CanonicalInvocation:
    """Use the real parser and immutable session digest for each dispatched logical slot."""
    body: dict[str, Any] = {
        "schema_version": "roboguide.execution-session/v0.1",
        "mission_id": "mission",
        "group_id": "group",
        "slots": [
            {
                "task_id": f"task-{i}",
                "role_id": "role",
                "actor_id": actor,
                "dependencies": [],
                "independent": not coupled,
            }
            for i, actor in enumerate(actors)
        ],
    }
    body["digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return parse_canonical_invocation(
        {
            "invocation": {
                "mission_id": "mission",
                "group_id": "group",
                "task_id": f"task-{task}",
                "role_id": "role",
                "attempt_id": f"attempt-{task}",
                "operation": "observation.verify@v1" if observation else "mobility.move@v1",
                "objective": "Observe object-a" if observation else f"Navigate to target-{task}",
                "parameters": {"expected": "detected(object-a)"}
                if observation
                else {"destination": f"target-{task}"},
                "resource_ids": ["slot"],
                "execution_session": body,
            }
        }
    )


@dataclass
class Arguments:
    """Match original AgentArguments fields without importing the vendor runtime."""

    robot_id: str
    robot_type: str
    task_description: str
    subtask_description: str
    chat_history: list[Any]


class Flag:
    """Observe the actual high-level transition requested at a Task boundary."""

    def __init__(self) -> None:
        """Start with no forced high-level transition."""
        self.calls = 0

    def fill_(self, value: bool) -> None:
        """Record a policy-local transition without touching peers."""
        assert value is True
        self.calls += 1


class Agent(ContractAgent):
    """Vendor-shaped original model agent with counted per-attempt initialization."""

    def __init__(self, name: str) -> None:
        """Keep original chat dispatch and a zero-call initial model."""
        super().__init__(name, ContractModel("target-0", None))
        self.initialized = False
        self.initializations = 0

    def init_agent(
        self,
        robot_type: str,
        task_description: str,
        subtask_description: str,
        chat_history: Any = None,
        enable_logging: bool = False,
        logging_file: str = "",
    ) -> None:
        """Initialize one fresh attempt and retain its actual task description."""
        del robot_type, chat_history, enable_logging, logging_file
        self.initializations += 1
        self.initialized = True
        self.task_description = task_description
        self.subtask_description = subtask_description
        self.llm_model = ContractModel(task_description.removeprefix("Navigate to "), None)


class Actor:
    """Drive the same joint actor boundary with per-endpoint original model counters."""

    policy_action_space = object()
    hidden_state_shape = (1,)
    hidden_state_shape_lens = (1,)
    policy_action_space_shape_lens = (1,)

    def __init__(self, count: int, *, fail_at: int | None = None) -> None:
        """Build distinct original policies and an optional original action failure."""
        self.calls, self.fail_at = 0, fail_at
        self.originals = [Agent(f"agent_{agent}") for agent in range(count)]
        self._active_policies = [
            SimpleNamespace(
                _name_to_idx={"wait": 1},
                _skills={1: WaitSkillPolicy()},
                _cur_call_high_level=Flag(),
                _high_level_policy=SimpleNamespace(llm_agent=agent, _skill_name_to_idx={"wait": 1}),
            )
            for agent in self.originals
        ]
        self.assignment: dict[str, Any] = {}

    def act(self, *args: Any, **kwargs: Any) -> Any:
        """Emulate original zero-action reinit, then call only current policy agents."""
        del args, kwargs
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("original actor sentinel")
        for agent, policy in enumerate(self._active_policies):
            llm = policy._high_level_policy.llm_agent
            # Native code may invalidate all agents after an all-zero previous action.
            llm.initialized = False
            arguments = self.assignment[f"agent_{agent}"]
            llm.init_agent(
                arguments.robot_type, arguments.task_description, arguments.subtask_description, []
            )
            llm.chat("current observation")
        return SimpleNamespace(
            actions=FakeTensor(),
            env_actions=FakeTensor(),
            rnn_hidden_states=FakeTensor(),
            should_inserts=None,
        )


class Gym:
    """Advance exactly one joint step with scripted original navigation finish facts."""

    def __init__(self, finish_at: Mapping[int, int], *, done_at: int | None = None) -> None:
        """Retain physical counters and original cached observation source."""
        self.steps, self.finish_at, self.done_at = 0, finish_at, done_at
        self._last_obs: dict[str, Any] = {"detected_objects": []}

    def step(self, action: Any) -> tuple[Any, float, bool, dict[str, bool]]:
        """Return actual fake sensor facts without another action or reset."""
        del action
        self.steps += 1
        return (
            {
                f"agent_{agent}_has_finished_oracle_nav": [int(self.steps >= deadline)]
                for agent, deadline in self.finish_at.items()
            },
            0.0,
            self.steps == self.done_at,
            {"pddl_success": False},
        )


class Runtime(LiveEmosStage2Runtime):
    """Keep the production live loop and bindings behind deterministic original API facades."""

    def __init__(self, directory: Path, count: int, gym: Gym, actor: Actor) -> None:
        """Prepare one already-reset world without Habitat, GPU or Provider calls."""
        self._agent_ids = tuple(range(count))
        self._config = SimpleNamespace(
            max_steps=8,
            step_period_ms=0,
            episode_id="episode",
            seed=40,
            relocation_completion_binding=False,
            subtask_mode="natural-objective",
        )
        self._runtime = {
            "torch": FakeTorch,
            "device": "cpu",
            "get_action_space_info": lambda unused: ((1,), False),
        }
        self._gym_env, self._actor = gym, actor
        self._habitat_env = SimpleNamespace(
            current_episode=SimpleNamespace(scene_id="scene", episode_id="episode"),
            episode_over=False,
            get_metrics=lambda: {"pddl_success": False},
            task=SimpleNamespace(actions={}, get_task_text_context=lambda: {}),
        )
        self._agent_access = SimpleNamespace(masks_shape=(1,))
        self._prepared_observations = {}
        self.test_diagnostics = RecordingDiagnostics()
        self._diagnostics = cast(Any, self.test_diagnostics)
        self._initial_positions = {str(agent): [float(agent), 0.0, 0.0] for agent in range(count)}
        self._stage2_accounting_agents = {}
        self._detection_sensors = {agent: "detected_objects" for agent in range(count)}
        self.registry = {
            "endpoints": [
                {
                    "agent_id": agent,
                    "robot_type": "robot",
                    "operations": ["mobility.move@v1", "observation.verify@v1"],
                }
                for agent in range(count)
            ]
        }
        self.directory = directory
        directory.mkdir(exist_ok=True)
        (directory / "authoritative-semantic-evidence.json").write_text(
            '{"digest":"sha256:semantic"}'
        )
        self.trace: list[int] = []

    def _require_initialized(self) -> tuple[Any, Any, Any, Any]:
        """Return one immutable set of loaded test facades."""
        return self._gym_env, self._habitat_env, self._actor, self._agent_access

    def _evidence_dir(self) -> Path:
        """Write only test-local evidence."""
        return self.directory

    def _batch(self, observations: Any) -> Any:
        """Preserve supplied observations through the fake batching facade."""
        return observations

    def _assignment_robot_types(self, text: Any) -> dict[str, str]:
        """Expose independently verified endpoint classes."""
        del text
        return {f"agent_{agent}": "robot" for agent in self._agent_ids}

    def _install_assignment(self, assignment: dict[str, Any]) -> tuple[Any, Any]:
        """Use actual mutable assignment objects at the original Stage1 boundary."""
        assert self._actor is not None
        self._actor.assignment = assignment
        return SimpleNamespace(group_discussion="bound"), "original"

    def _admit_spatial_feasibility(self, *args: Any) -> None:
        """Keep route eligibility out of this policy lifecycle test."""

    def _admit_relocation_source(self, *args: Any) -> None:
        """No relocation is dispatched in the navigation loop test."""

    def _prepare_navigation_step(self, *args: Any) -> Callable[[], None]:
        """Observe the original single-step boundary without a second simulator call."""
        return lambda: None

    def _current_skills(self, actor: Any) -> list[str]:
        """Report wait exactly for policies made passive by actual local completion."""
        return [
            "wait"
            if isinstance(policy._high_level_policy.llm_agent, PassiveIdleAgent)
            else "nav_to_obj"
            for policy in actor._active_policies
        ]

    def _oracle_nav_finished_for(self, agent: int) -> bool:
        """Only explicit returned finish sensors drive local completion."""
        del agent
        return False

    def _agent_position_for(self, agent: int) -> tuple[float, float, float]:
        """Retain observed test positions independently of completion."""
        return float(agent), 0.0, 0.0

    def _append_action_trace(self, step: int, *args: Any) -> None:
        """Record each real fake Gym step once."""
        self.trace.append(step)

    def _flush_action_trace(self) -> None:
        """No disk buffering is needed for this counted test trace."""

    def _action_trace_stats(self) -> dict[str, Any]:
        """Report the number of physical steps without inventing samples."""
        return {"steps": len(self.trace)}

    def _record_video(self, *args: Any) -> None:
        """Keep rendering absent from deterministic conformance."""

    def _retain_action_audit(self, summary: Any) -> None:
        """Retain the real audit reducer's output for inspection."""
        self.audit = summary


@pytest.fixture(autouse=True)
def vendor_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve only required original type boundaries without a vendor process."""
    monkeypatch.setattr(live_bindings, "_original_wait_skill_type", lambda: WaitSkillPolicy)
    module = ModuleType("habitat_mas.utils")
    module.AgentArguments = Arguments  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "habitat_mas.utils", module)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_sparse_and_sequential_tasks_use_only_assigned_model(tmp_path: Path, count: int) -> None:
    """No endpoint barrier or extra reset; original model initializes exactly once per Task."""
    actor, gym = Actor(count), Gym({0: 1})
    runtime = Runtime(tmp_path, count, gym, actor)
    first, second = invocation(0, ("one", "one")), invocation(1, ("one", "one"))
    outcomes: list[LocalExecutionOutcome] = []
    ready = False
    sent = False

    def arrivals() -> Mapping[int, CanonicalInvocation]:
        """Deliver the next authentic Control assignment only after first local completion."""
        nonlocal sent
        if ready and not sent:
            sent = True
            return {0: second}
        return {}

    def completed(agent: int, request: CanonicalInvocation, outcome: LocalExecutionOutcome) -> None:
        """Count exact completion and enable the simulated next Control dispatch."""
        nonlocal ready
        assert agent == 0 and request in (first, second)
        ready = True
        outcomes.append(outcome)

    _, summary = runtime.execute_live(
        {0: first}, arrivals, lambda: False, lambda *args: None, completed, 0.1
    )
    assert gym.steps == actor.calls == 2
    assert actor.originals[0].initializations == 2
    assert all(agent.initializations == agent.dispatches == 0 for agent in actor.originals[1:])
    assert all(
        outcome.state == "COMPLETED" and not outcome.benchmark_task_achieved for outcome in outcomes
    )
    assert summary["identity"]["episode_reset_count"] == 1
    assert runtime.test_diagnostics.terminals == [(2, "all_local_slots_finished")]
    assert [
        policy._high_level_policy.llm_agent for policy in actor._active_policies
    ] == actor.originals


def test_later_endpoint_activation_preserves_running_peer(tmp_path: Path) -> None:
    """An assignment arriving between steps leaves a peer's model and skill state intact."""
    actor, gym = Actor(3), Gym({0: 3, 1: 2})
    runtime = Runtime(tmp_path, 3, gym, actor)
    first, second = invocation(0, ("left", "right")), invocation(1, ("left", "right"))
    sent = False
    results: dict[str, int] = {}

    def arrivals() -> Mapping[int, CanonicalInvocation]:
        """Simulate the second real dispatch after the first physical step."""
        nonlocal sent
        if gym.steps == 1 and not sent:
            sent = True
            return {1: second}
        return {}

    def completed(agent: int, request: CanonicalInvocation, outcome: LocalExecutionOutcome) -> None:
        """Record exact Task/step identity; completion does not stop its active peer."""
        del agent
        results[request.task_id] = outcome.simulator_steps

    _, summary = runtime.execute_live(
        {0: first}, arrivals, lambda: False, lambda *args: None, completed, 0.1
    )
    assert results == {"task-0": 3, "task-1": 2}
    assert actor.originals[0].initializations == actor.originals[1].initializations == 1
    assert actor.originals[0].llm_model.calls == 3
    assert actor.originals[1].llm_model.calls == 1
    assert actor.originals[2].initializations == 0
    assert summary["identity"]["simulator_steps"] == 3


def test_post_reset_profile_failure_flushes_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live registry/sensor failure after the one reset retains its original exception."""
    from habitat_local_eaios.shared_world import SharedEmosStage2Runtime

    runtime = Runtime(tmp_path, 2, Gym({}), Actor(2))
    monkeypatch.setattr(SharedEmosStage2Runtime, "initialize", lambda self: None)

    def fail_profile() -> None:
        """Represent failed actual loaded sensor readiness without a second reset."""
        raise IntegrationError("sensor readiness sentinel")

    monkeypatch.setattr(runtime, "_initialize_live_profile", fail_profile)
    with pytest.raises(IntegrationError, match="sensor readiness sentinel"):
        runtime.initialize()
    assert runtime.test_diagnostics.terminals == [
        (0, "execution_exception:live_initialization:IntegrationError")
    ]
    with pytest.raises(IntegrationError, match="cannot restart"):
        runtime.initialize()


def test_successful_live_initialization_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated readiness setup neither resets again nor nests the archived Local How profile."""
    from habitat_local_eaios.shared_world import SharedEmosStage2Runtime

    runtime = Runtime(tmp_path, 2, Gym({}), Actor(2))
    calls: list[str] = []
    monkeypatch.setattr(SharedEmosStage2Runtime, "initialize", lambda self: calls.append("world"))
    monkeypatch.setattr(runtime, "_initialize_live_profile", lambda: calls.append("profile"))
    runtime.initialize()
    runtime.initialize()
    assert calls == ["world", "profile"]


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_live_pipe_relays_later_assignments_and_early_terminal_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    """Exercise real parent/child IPC with one reset and two sequential Control dispatches."""
    import multiprocessing
    import threading

    from habitat_local_eaios import live_world, shared_world
    from habitat_local_eaios.crabagent_backend import CrabAgentBackendConfig

    actor, gym = Actor(count), Gym({0: 1})
    runtime = Runtime(tmp_path, count, gym, actor)
    runtime._config.endpoint_registry_path = tmp_path / "registry.json"
    starts: list[bool] = []
    monkeypatch.setattr(runtime, "initialize", lambda: starts.append(True))
    monkeypatch.setattr(runtime, "supported_operations", lambda: ("mobility.move@v1",))
    monkeypatch.setattr(runtime, "close", lambda: None)
    monkeypatch.setattr(runtime, "final_metrics", lambda: {"pddl_success": False})
    monkeypatch.setattr(live_world, "LiveEmosStage2Runtime", lambda config, ids: runtime)
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(
        target=shared_world._child_world_process,
        args=(child, runtime._config, tuple(range(count))),
        daemon=True,
    )
    thread.start()
    assert parent.poll(2)
    ready = parent.recv()
    assert ready[0] == "READY", ready
    assert "original EMOS Stage2 episode episode" in ready[1]["detail"]
    world = shared_world.ProcessWorldService(
        cast(CrabAgentBackendConfig, runtime._config), tuple(range(count))
    )
    world._connection, world._process, world._ready = parent, thread, True
    first, second = invocation(0, ("one", "one")), invocation(1, ("one", "one"))
    observed: list[str] = []
    sent = False

    def arrivals() -> Mapping[int, CanonicalInvocation]:
        """Send the second committed Task only after reading the first early terminal."""
        nonlocal sent
        if observed and not sent:
            sent = True
            return {0: second}
        return {}

    def completed(agent: int, request: CanonicalInvocation, outcome: LocalExecutionOutcome) -> None:
        """Consume each exact child terminal once, before the shared world ends."""
        assert agent == 0 and outcome.state == "COMPLETED"
        observed.append(request.task_id)

    try:
        _, summary = world.run_live(
            {0: first}, arrivals, lambda: False, lambda *args: None, completed, 1.0
        )
        assert starts == [True]
        assert observed == ["task-0", "task-1"]
        assert gym.steps == actor.calls == 2
        assert [agent.initializations for agent in actor.originals] == [2] + [0] * (count - 1)
        assert summary["official_metrics"]["pddl_success"] is False
    finally:
        parent.send(("CLOSE", None))
        thread.join(timeout=2)
        parent.close()
    assert not thread.is_alive()


def test_episode_end_and_exception_preserve_original_terminal_evidence(tmp_path: Path) -> None:
    """Early episode termination fails unfinished work; action exceptions remain original faults."""
    actor, gym = Actor(2), Gym({0: 99}, done_at=1)
    runtime = Runtime(tmp_path, 2, gym, actor)
    request = invocation(0, ("one",))
    observed: list[LocalExecutionOutcome] = []
    runtime.execute_live(
        {0: request},
        lambda: {},
        lambda: False,
        lambda *args: None,
        lambda agent, req, outcome: observed.append(outcome),
        0.1,
    )
    assert observed[0].state == "FAILED" and not observed[0].benchmark_task_achieved
    actor, gym = Actor(2, fail_at=2), Gym({0: 99})
    runtime = Runtime(tmp_path / "fault", 2, gym, actor)
    with pytest.raises(RuntimeError, match="original actor sentinel"):
        runtime.execute_live(
            {0: request}, lambda: {}, lambda: False, lambda *args: None, lambda *args: None, 0.1
        )
    assert gym.steps == 1
    assert runtime.test_diagnostics.terminals == [(1, "execution_exception:RuntimeError")]
    assert runtime.test_diagnostics.persisted_steps == [1]
    failure = json.loads((runtime.directory / "live-world-failure.json").read_bytes())
    assert failure["identity"]["simulator_steps"] == 1
    assert failure["official_metrics"]["pddl_success"] is False


def test_ledger_rejects_duplicate_overlap_migration_and_coupled_tasks() -> None:
    """Logical bindings remain immutable; this profile never downgrades collaboration."""
    ledger = LiveSlotLedger((0, 1, 2))
    first, second = invocation(0, ("one", "one")), invocation(1, ("one", "one"))
    ledger.admit(0, first)
    with pytest.raises(IntegrationError, match="overlaps"):
        ledger.admit(0, second)
    ledger.finish(0)
    with pytest.raises(IntegrationError, match="repeats"):
        ledger.admit(0, first)
    with pytest.raises(IntegrationError, match="migrate"):
        ledger.admit(1, second)
    with pytest.raises(IntegrationError, match="independent"):
        LiveSlotLedger((0,)).admit(0, invocation(0, ("one",), coupled=True))
    ledger.admit(0, second)
    ledger.finish(0)
    assert ledger.complete()


def test_perception_negative_read_completes_only_observation(tmp_path: Path) -> None:
    """A cached negative observation calls neither a model nor actor/Gym nor official success."""
    actor, gym = Actor(2), Gym({})
    runtime = Runtime(tmp_path, 2, gym, actor)
    assert runtime._habitat_env is not None
    simulator = SimpleNamespace(
        habitat_config=SimpleNamespace(object_ids_start=100), scene_obj_ids=list(range(8))
    )
    runtime._habitat_env.task.pddl_problem = SimpleNamespace(
        get_ordered_entities_list=lambda: [SimpleNamespace(name="object-a")],
        sim_info=SimpleNamespace(search_for_entity=lambda entity: 7, sim=simulator),
    )
    runtime._habitat_env.sim = simulator

    def forbidden_context() -> Any:
        """Original text acquisition can sample RNG and is forbidden for a cached read."""
        raise AssertionError("observation activated original text context acquisition")

    runtime._habitat_env.task.get_task_text_context = forbidden_context
    completed: list[LocalExecutionOutcome] = []
    runtime.execute_live(
        {1: invocation(0, ("observer",), observation=True)},
        lambda: {},
        lambda: False,
        lambda *args: None,
        lambda agent, request, outcome: completed.append(outcome),
        0.1,
    )
    assert actor.calls == gym.steps == 0
    assert all(agent.initializations == 0 for agent in actor.originals)
    assert completed[0].state == "COMPLETED" and not completed[0].local_skill_completed
    observation = json.loads(next(tmp_path.glob("observation-*.json")).read_bytes())
    assert observation["condition_holds"] is False and observation["status"] == "observed"
    assert "pddl_success" not in observation


def test_live_coordinator_has_no_unused_endpoint_barrier(tmp_path: Path) -> None:
    """One of three registered endpoints can finish its one-slot session without peer dispatch."""
    import threading

    sources = node_sources(tmp_path, 3)
    registry = build_endpoint_registry(sources, tmp_path)
    path = tmp_path / "endpoint-registry.json"
    path.write_text(json.dumps(registry))
    terminal = threading.Event()

    class World:
        """Use actual coordinator callbacks with one synthetic local result."""

        def start(self) -> None:
            """No real vendor process is started for coordinator conformance."""

        def is_ready(self) -> bool:
            """Report the fake loaded world as ready."""
            return True

        def readiness_detail(self) -> str:
            """Expose a diagnostic identifying the synthetic source."""
            return "synthetic"

        def supported_operations(self) -> tuple[str, ...]:
            """Advertise exactly the fake world's navigation support."""
            return ("mobility.move@v1",)

        def run_live(
            self,
            initial: Any,
            arrivals: Any,
            cancelled: Any,
            running: Any,
            completed: Any,
            budget: Any,
        ) -> Any:
            """Return one exact assigned local terminal; no waiting for other endpoints."""
            del arrivals, cancelled, budget
            assert set(initial) == {0}
            running(0, "synthetic running")
            outcome = LocalExecutionOutcome(
                "COMPLETED",
                "synthetic local completion",
                "episode",
                "scene",
                "target-0",
                1,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            )
            completed(0, initial[0], outcome)
            terminal.set()
            return {0: outcome}, {
                "identity": {"simulator_steps": 1},
                "official_metrics": {"pddl_success": False},
            }

        def shutdown(self) -> None:
            """Release only the synthetic instance."""

    coordinator = LiveWorldCoordinator(World(), path, 0.1, tmp_path)
    endpoint = NodeEndpoint("node-a", 0, ExecutionStore(tmp_path / "store.sqlite3"), coordinator)
    request = invocation(0, ("one",))
    try:
        response = endpoint.accept({"invocation": request.as_dict()})
        endpoint.dispatch(str(response["execution_id"]))
        assert terminal.wait(2)
        record = endpoint.store().get(str(response["execution_id"]))
        assert record is not None and record["state"] == "COMPLETED"
    finally:
        coordinator.shutdown()


def test_missing_followup_dispatch_times_out_without_fabricating_completion(tmp_path: Path) -> None:
    """A released local endpoint can wait for Control, but an absent Task remains unfinished."""
    actor, gym = Actor(2), Gym({0: 1})
    runtime = Runtime(tmp_path, 2, gym, actor)
    _, summary = runtime.execute_live(
        {0: invocation(0, ("one", "one"))},
        lambda: {},
        lambda: False,
        lambda *args: None,
        lambda *args: None,
        0.01,
    )
    assert summary["termination_reason"] == "assignment_wait_timeout"
    assert len(summary["task_outcomes"]) == 1
    assert gym.steps == actor.calls == 1
    assert actor.originals[0].initializations == 1


def test_action_trace_cleanup_fault_does_not_mask_actor_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final buffered evidence is attempted even if action audit flushing also fails."""
    actor, gym = Actor(2, fail_at=2), Gym({0: 99})
    runtime = Runtime(tmp_path, 2, gym, actor)

    def fail_flush() -> None:
        """Emulate an archival error independent of the original action exception."""
        raise OSError("trace flush sentinel")

    monkeypatch.setattr(runtime, "_flush_action_trace", fail_flush)
    with pytest.raises(RuntimeError, match="original actor sentinel"):
        runtime.execute_live(
            {0: invocation(0, ("one",))},
            lambda: {},
            lambda: False,
            lambda *args: None,
            lambda *args: None,
            0.1,
        )
    assert runtime.test_diagnostics.persisted_steps == [1]
    assert runtime.test_diagnostics.terminals == [(1, "execution_exception:RuntimeError")]


def test_unknown_observation_never_becomes_condition_or_official_false(tmp_path: Path) -> None:
    """Missing sensor cache produces explicit acquisition failure with zero physical calls."""
    runtime = Runtime(tmp_path, 1, Gym({}), Actor(1))
    outcomes, summary = runtime.execute_live(
        {0: invocation(0, ("one",), observation=True)},
        lambda: {},
        lambda: False,
        lambda *args: None,
        lambda *args: None,
        0.1,
    )
    assert outcomes[0].state == "FAILED"
    assert outcomes[0].terminal_basis == "condition-observation-unavailable"
    assert summary["identity"]["simulator_steps"] == 0
    assert runtime._actor is not None and runtime._actor.calls == 0
