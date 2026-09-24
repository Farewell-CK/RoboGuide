"""Deterministic offline validation of read-only physical diagnostics."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.diagnostics import (  # noqa: E402
    DIAGNOSTICS_ENV_FLAG,
    DIAGNOSTICS_MAX_RECORD_BYTES,
    DIAGNOSTICS_SCHEMA,
    BufferedJsonlWriter,
    PhysicalDiagnostics,
    create_physical_diagnostics,
    diagnostics_enabled,
)


class FakeVec:  # minimal numpy-like 1-D view supporting argmax/!=0.
    """Emulate one action row with argmax and nonzero scanning."""

    def __init__(self, values: list[float]) -> None:
        """Store the raw action values."""
        self._values = values

    def argmax(self) -> int:
        """Return the index of the largest value."""
        return self._values.index(max(self._values))

    def __len__(self) -> int:
        """Return the action width."""
        return len(self._values)

    def __getitem__(self, index: int) -> float:
        """Return one action component."""
        return self._values[index]


class FakeArrayScalar:
    """Emulate one NumPy scalar without requiring NumPy in offline tests."""

    def __init__(self, value: float | bool) -> None:
        """Store the scalar value returned by ``item``."""
        self._value = value

    def item(self) -> float | bool:
        """Return the corresponding built-in JSON scalar."""
        return self._value

    def __float__(self) -> float:
        """Match array-library scalar conversion used for pose components."""
        return float(self._value)


class FakeFlatAction:
    """Emulate the flat joint action layout observed in the real deployment."""

    def __init__(self, values: list[float]) -> None:
        """Store a fixed flat vector and its array-style shape."""
        self._values = values
        self.shape = (len(values),)

    def __getitem__(self, index: int) -> FakeArrayScalar:
        """Return a scalar so the per-agent row interpretation fails safely."""
        return FakeArrayScalar(self._values[index])

    def tolist(self) -> list[float]:
        """Return the flat numeric action values."""
        return list(self._values)


class FakePredicate:
    """One official goal conjunct stub with independently controllable truth."""

    def __init__(self, name: str, truth: bool) -> None:
        """Store the label and the truth this conjunct reports."""
        self.name = "any_at"
        self.target = name
        self.truth = truth
        self.evaluated = 0
        self._arg_values = [type("Entity", (), {"name": name})()]

    def __repr__(self) -> str:
        """Render the conjunct label the way a real predicate would."""
        return f"{self.name}({self.target})"

    def is_true(self, sim_info: Any) -> bool:
        """Report official truth and count evaluations."""
        self.evaluated += 1
        return self.truth

    def clone(self) -> FakePredicate:
        """Return an isolated expression instance for diagnostic evaluation."""
        return type(self)(self.target, self.truth)


class FakeProblem:
    """Stub PDDL problem exposing goal conjuncts and entity lookups."""

    def __init__(self, conjuncts: list[FakePredicate]) -> None:
        """Hold the conjuncts and a bound sim_info."""
        self.goal = type("Goal", (), {"sub_exprs": conjuncts})()
        self.sim_info = type("SimInfo", (), {"bound": True})()

    def get_entity(self, name: str) -> Any:
        """Resolve every requested entity to a stub object."""
        return type("Entity", (), {"name": name})()


class FakeAgent:
    """One articulated agent with readable position and rotation."""

    def __init__(self) -> None:
        """Start at a distinct base pose."""
        self.base_pos = [0.5, 1.0, 2.0]
        self.base_rot = 0.25


class FakeSim:
    """Simulator stub keyed by agent id."""

    def __init__(self) -> None:
        """Create the served agents."""
        self._agents = {0: FakeAgent(), 1: FakeAgent()}

    def get_agent_data(self, agent_id: int) -> Any:
        """Return one agent's data record."""
        return type("Data", (), {"articulated_agent": self._agents[agent_id]})()


class FakeTask:
    """Task stub joining the PDDL problem and oracle nav actions."""

    def __init__(self, problem: FakeProblem, skill_done: dict[int, bool]) -> None:
        """Bind the problem and per-agent skill_done flags."""
        self.pddl_problem = problem
        self.actions = {
            f"agent_{agent_id}_oracle_nav_action": type("Action", (), {"skill_done": done})()
            for agent_id, done in skill_done.items()
        }


class FakeEpisode:
    """Episode identity stub."""

    episode_id = "51"
    scene_id = "data/scene_datasets/mp3d/pRbA3pwrgk9/pRbA3pwrgk9.glb"


class FakeEnv:
    """Habitat environment stub for diagnostics-only observation."""

    def __init__(
        self,
        conjuncts: list[FakePredicate],
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Assemble the sim, task, episode, and measure cache."""
        self.sim = FakeSim()
        self.task = FakeTask(FakeProblem(conjuncts), {0: False, 1: False})
        self.current_episode = FakeEpisode()
        self.episode_over = False
        self._metrics = metrics or {}
        self._config = type("Config", (), {"habitat": type("Habitat", (), {"seed": 40})()})()

    def get_metrics(self) -> dict[str, Any]:
        """Return the official measure cache."""
        return dict(self._metrics)


class FakeSkill:
    """Skill bookkeeping stub with readable step accounting."""

    def __init__(self, current: float, maximum: int) -> None:
        """Hold step counters the hierarchical policy would own."""
        self._cur_skill_step = [current]
        self._max_skill_steps = maximum
        self._force_end_on_timeout = False


class FakePolicy:
    """Hierarchical policy stub for one agent."""

    def __init__(self, skill: FakeSkill, name: str) -> None:
        """Expose the active skill and its bookkeeping."""
        self._cur_skills = [0]
        self._idx_to_name = {0: name}
        self._skills = {0: skill}
        self._cur_call_high_level = [False]


class FakeActor:
    """Multi-agent actor stub keyed by agent index."""

    def __init__(self, skills: list[FakeSkill], names: list[str]) -> None:
        """Serve one policy per agent."""
        self._active_policies = [
            FakePolicy(skill, name) for skill, name in zip(skills, names, strict=True)
        ]


def make_diagnostics(tmp_path: Path, enabled: bool = True) -> PhysicalDiagnostics:
    """Build one recorder over a fresh evidence directory."""
    return PhysicalDiagnostics(tmp_path / "evidence", (0, 1), enabled, 3_050, write_batch_records=1)


def test_diagnostics_environment_flag_is_default_off(monkeypatch: Any) -> None:
    """Only the exact deployment opt-in value enables physical diagnostics."""
    monkeypatch.delenv(DIAGNOSTICS_ENV_FLAG, raising=False)
    assert diagnostics_enabled() is False
    monkeypatch.setenv(DIAGNOSTICS_ENV_FLAG, "true")
    assert diagnostics_enabled() is False
    monkeypatch.setenv(DIAGNOSTICS_ENV_FLAG, "1")
    assert diagnostics_enabled() is True


def test_disabled_diagnostics_write_nothing_and_read_nothing(tmp_path: Path) -> None:
    """Default-off diagnostics neither write files nor touch any accessor."""
    diagnostics = make_diagnostics(tmp_path, enabled=False)
    env = FakeEnv([FakePredicate("any_targets|0", True)])
    actor = FakeActor([FakeSkill(0, 1000), FakeSkill(0, 1000)], ["wait", "nav_to_obj"])
    diagnostics.record_reset(env, None)
    diagnostics.record_step(
        1, ["wait", "nav_to_obj"], [FakeVec([1.0, 0.0])], env, actor, False, {}, {}
    )
    diagnostics.record_terminal(env, 1, "episode_done")
    assert not (tmp_path / "evidence").exists() or not any((tmp_path / "evidence").iterdir())
    assert env.task.pddl_problem.goal.sub_exprs[0].evaluated == 0


def test_each_official_conjunct_is_recorded_independently(tmp_path: Path) -> None:
    """Two conjuncts are evaluated and recorded separately, not copied jointly."""
    first = FakePredicate("any_targets|0", True)
    second = FakePredicate("TARGET_any_targets|0", False)
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([first, second])
    diagnostics.record_reset(env, None)
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    values = document["goal_conjunct_values"]
    assert values["any_at(any_targets|0)"] is True
    assert values["any_at(TARGET_any_targets|0)"] is False
    assert first.evaluated == 0 and second.evaluated == 0
    assert document["schema_version"] == DIAGNOSTICS_SCHEMA
    assert document["seed"] is None  # config stub carries no seed
    assert document["habitat_seed_config"] == 40


def test_structured_habitat_seed_shape_is_read_without_nested_habitat_key(
    tmp_path: Path,
) -> None:
    """The current HabitatConfig shape records its direct resolved seed."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)])
    env._config = type("HabitatConfig", (), {"seed": 73})()
    diagnostics.record_reset(env, type("BackendConfig", (), {"seed": 40})())
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    assert document["habitat_seed_config"] == 73


def test_array_scalars_remain_available_in_reset_and_terminal_snapshots(
    tmp_path: Path,
) -> None:
    """Vendor scalar types serialize without degrading complete world snapshots."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv(
        [FakePredicate("any_targets|0", False)],
        metrics={"pddl_success": False, "path_length": FakeArrayScalar(1.25)},
    )
    env.sim._agents[0].base_pos = cast(
        Any,
        [FakeArrayScalar(0.5), FakeArrayScalar(1.0), FakeArrayScalar(2.0)],
    )

    diagnostics.record_reset(env, None)
    diagnostics.record_terminal(env, 0, "episode_done")

    initial = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert initial["agents"]["0"]["position"] == [0.5, 1.0, 2.0]
    assert initial.get("_status") is None
    assert terminal["official_metrics"]["path_length"] == 1.25
    assert terminal["collection_stats"]["writer"]["records_dropped"] == 0


def test_local_completion_and_official_noncompletion_coexist(tmp_path: Path) -> None:
    """A finished local skill and a false official conjunct are both retained."""
    first = FakePredicate("any_targets|0", True)
    second = FakePredicate("TARGET_any_targets|0", False)
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([first, second], metrics={"pddl_success": False})
    env.task.actions["agent_0_oracle_nav_action"].skill_done = True
    actor = FakeActor([FakeSkill(90, 1000), FakeSkill(90, 1000)], ["wait", "nav_to_obj"])
    diagnostics.record_step(
        90,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])],
        env,
        actor,
        False,
        {"pddl_success": False},
        {},
    )
    row = json.loads((tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()[0])
    assert row["agents"]["0"]["oracle_flags"]["oracle_skill_done"] is True
    assert row["agents"]["1"]["oracle_flags"]["oracle_skill_done"] is False
    assert row["goal_conjunct_values"]["any_at(TARGET_any_targets|0)"] is False
    assert row["official_pddl_success_metrics"] is False


def test_skill_timeout_and_real_completion_are_distinguishable(tmp_path: Path) -> None:
    """Over-budget steps and finished sensors land in different fields."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("any_targets|0", False)])
    actor = FakeActor([FakeSkill(3, 1000), FakeSkill(1000, 1000)], ["wait", "nav_to_obj"])
    actor._active_policies[1]._cur_call_high_level = [True]
    observations = {
        "agent_0_has_finished_oracle_nav": [False],
        "agent_1_has_finished_oracle_nav": [False],
    }
    diagnostics.record_step(
        1000,
        ["wait", "nav_to_obj"],
        [FakeVec([0.0, 1.0]), FakeVec([0.0, 1.0])],
        env,
        actor,
        False,
        {},
        observations,
        observations,
    )
    row = json.loads((tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()[0])
    fetch = row["agents"]["1"]["skill_state"]
    spot = row["agents"]["0"]["skill_state"]
    assert fetch["cur_skill_step"] == 1000.0 and fetch["max_skill_steps"] == 1000
    assert fetch["force_end_on_timeout"] is False
    assert fetch["over_max_len"] == {
        "_status": "inferred",
        "basis": "max_skill_steps > 0 and cur_skill_step >= max_skill_steps",
        "value": True,
    }
    exit_reason = row["agents"]["1"]["skill_exit_reason"]
    assert exit_reason["_status"] == "inferred"
    assert exit_reason["candidates"] == ["skill_step_budget"]
    assert spot["cur_skill_step"] == 3.0
    assert row["agents"]["1"]["oracle_flags"]["oracle_skill_done"] is False


def test_current_hierarchical_policy_skill_map_is_observed(tmp_path: Path) -> None:
    """Diagnostics read the current policy's index-keyed private skill map."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(7, 1000), FakeSkill(11, 1000)], ["wait", "nav_to_obj"])
    diagnostics.record_step(
        11,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        False,
        {},
        {},
    )
    row = json.loads((tmp_path / "evidence/diagnostics-steps.jsonl").read_text())
    assert row["agents"]["0"]["skill_state"]["cur_skill_step"] == 7.0
    assert row["agents"]["1"]["skill_state"]["max_skill_steps"] == 1000


def test_flat_joint_action_is_recorded_without_claiming_per_agent_rows(tmp_path: Path) -> None:
    """A production-shaped flat action receives an explicit joint-scope summary."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    diagnostics.record_step(
        1,
        ["wait", "nav_to_obj"],
        FakeFlatAction([0.0, 0.25, 0.0, 1.0]),
        env,
        actor,
        False,
        {},
        {},
    )
    row = json.loads((tmp_path / "evidence/diagnostics-steps.jsonl").read_text())
    assert row["action_summary"] == {
        "_scope": "joint_flat_action",
        "argmax": 3,
        "nonzero": [1, 3],
        "shape": [4],
    }


def test_terminal_state_is_independent_of_early_local_completion(tmp_path: Path) -> None:
    """The terminal record reads the live final pose, not a frozen early one."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("any_targets|0", True)])
    diagnostics.record_step(
        90,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])],
        env,
        FakeActor([FakeSkill(90, 1000), FakeSkill(90, 1000)], ["wait", "nav_to_obj"]),
        False,
        {},
        {},
    )
    env.sim._agents[0].base_pos = [9.0, 9.0, 9.0]  # drift far after local completion
    diagnostics.record_terminal(env, 3000, "step_budget_exhausted")
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert terminal["agents"]["0"]["position"] == [9.0, 9.0, 9.0]
    assert terminal["termination_reason"] == "step_budget_exhausted"
    assert terminal["simulator_steps"] == 3000


def test_unreadable_fields_are_marked_unavailable(tmp_path: Path) -> None:
    """Missing internals surface as unavailable markers, never synthesized values."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("any_targets|0", True)])

    def explode(*args: Any) -> Any:
        """Fail every position read with a deterministic runtime error."""
        raise RuntimeError("sensor gone")

    env.sim.get_agent_data = explode  # type: ignore[assignment]
    diagnostics.record_reset(env, None)
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    position = document["agents"]["0"]["position"]
    assert position["_status"] == "unavailable"
    assert "sensor gone" in position["reason"]


def test_partial_evidence_survives_a_midstream_observation_crash(tmp_path: Path) -> None:
    """Rows written before a crashing record call remain on disk."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("any_targets|0", True)])
    actor = FakeActor([FakeSkill(1, 1000), FakeSkill(1, 1000)], ["wait", "nav_to_obj"])
    for step in range(1, 4):
        diagnostics.record_step(
            step,
            ["wait", "nav_to_obj"],
            [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])],
            env,
            actor,
            False,
            {},
            {},
        )
    env.sim = None  # type: ignore[assignment]
    diagnostics.record_step(
        4,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])],
        env,
        actor,
        False,
        {},
        {},
    )
    lines = (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    assert len(lines) == 4
    degraded = json.loads(lines[3])
    # A broken simulator view degrades fields to unavailable instead of losing the row.
    assert degraded["agents"]["0"]["position"]["_status"] == "unavailable"
    diagnostics.record_terminal(env, 4, "episode_done")
    assert (tmp_path / "evidence/diagnostics-terminal.json").exists()


def test_step_records_stream_without_unbounded_accumulation(tmp_path: Path) -> None:
    """Streaming writes keep every row on disk with no growing in-memory list."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("any_targets|0", True)])
    actor = FakeActor([FakeSkill(0, 1000), FakeSkill(0, 1000)], ["wait", "nav_to_obj"])
    for step in range(1, 21):
        diagnostics.record_step(
            step,
            ["wait", "nav_to_obj"],
            [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])],
            env,
            actor,
            False,
            {},
            {},
        )
    lines = (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    assert len(lines) == 20
    assert len(diagnostics._predicates) == 1
    assert not hasattr(diagnostics, "_step_records")


def test_step_records_use_a_bounded_batch_until_terminal_flush(tmp_path: Path) -> None:
    """Default collection avoids serialization and disk I/O on every simulator step."""
    diagnostics = PhysicalDiagnostics(tmp_path / "evidence", (0, 1), True, 3_050)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    for step in range(1, 4):
        diagnostics.record_step(
            step,
            ["wait", "nav_to_obj"],
            [FakeVec([1.0]), FakeVec([1.0])],
            env,
            actor,
            False,
            {},
            {},
        )
    assert not (tmp_path / "evidence/diagnostics-steps.jsonl").exists()
    diagnostics.record_terminal(env, 3, "episode_done")
    rows = (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert len(rows) == 3
    assert terminal["collection_stats"]["writer"]["records_written"] == 3
    assert terminal["collection_stats"]["dropped_step_records"] == 0
    assert terminal["collection_stats"]["sampling_period_simulator_steps"] == 1


def test_buffered_writer_drops_failed_batches_without_raising(tmp_path: Path) -> None:
    """A blocked evidence path records loss accounting instead of failing execution."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    writer = BufferedJsonlWriter(blocked / "trace.jsonl", batch_records=2)
    writer.append({"simulator_step": 1})
    writer.append({"simulator_step": 2})
    stats = writer.stats()
    assert stats["batch_capacity_records"] == 2
    assert stats["flushes"] == 0
    assert stats["pending_records"] == 0
    assert stats["records_dropped"] == 2
    assert stats["records_written"] == 0
    assert stats["write_failures"] == 1
    assert stats["write_seconds"] >= 0.0


def test_initialization_failure_degrades_to_unavailable(tmp_path: Path, monkeypatch: Any) -> None:
    """A recorder-construction failure produces unavailable evidence, not an exception."""
    original_init = PhysicalDiagnostics.__init__

    def fail_init(*args: Any, **kwargs: Any) -> None:
        """Raise the deterministic initialization failure under test."""
        raise RuntimeError("diagnostics init failed")

    monkeypatch.setattr(PhysicalDiagnostics, "__init__", fail_init)
    diagnostics = create_physical_diagnostics(tmp_path / "evidence", (0, 1), True, 10)
    monkeypatch.setattr(PhysicalDiagnostics, "__init__", original_init)
    diagnostics.record_reset(FakeEnv([FakePredicate("target", False)]), None)
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    assert document["_status"] == "unavailable"
    assert "diagnostics initialization failed" in document["reason"]


def test_reset_and_terminal_top_level_failures_do_not_escape(tmp_path: Path) -> None:
    """Unreadable reset/terminal roots are isolated from the physical execution path."""
    diagnostics = make_diagnostics(tmp_path)

    class BrokenEnv:
        """Expose a simulator property whose read always fails."""

        @property
        def sim(self) -> Any:
            """Raise the deterministic root-read failure under test."""
            raise RuntimeError("sim root unavailable")

    diagnostics.record_reset(BrokenEnv(), None)
    diagnostics.record_terminal(BrokenEnv(), 1, "episode_done")
    assert diagnostics._dropped_records == 2
    initial = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert initial["_status"] == "unavailable"
    assert terminal["_status"] == "unavailable"


def test_unexpected_step_flush_failure_still_records_terminal_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An observer writer regression must not prevent a readable terminal snapshot."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", True)], metrics={"pddl_success": True})
    diagnostics.record_reset(env, None)

    def fail_flush() -> None:
        """Raise beyond the buffered writer's ordinary internal I/O isolation."""
        raise OSError("flush failure sentinel")

    monkeypatch.setattr(diagnostics._step_writer, "flush", fail_flush)
    diagnostics.record_terminal(env, 7, "execution_exception:actor_act:RuntimeError")
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert terminal["simulator_steps"] == 7
    assert terminal["termination_reason"] == "execution_exception:actor_act:RuntimeError"
    assert terminal["official_metrics"]["pddl_success"] is True
    assert terminal["dropped_diagnostic_records"] >= 1


def test_predicate_failure_is_explicitly_unavailable(tmp_path: Path) -> None:
    """One failed official predicate read is recorded as unavailable."""

    class BrokenPredicate(FakePredicate):
        """Official predicate stub whose computation fails."""

        def is_true(self, sim_info: Any) -> bool:
            """Raise the deterministic predicate failure under test."""
            raise RuntimeError("predicate sensor unavailable")

    diagnostics = make_diagnostics(tmp_path)
    diagnostics.record_reset(FakeEnv([BrokenPredicate("target", False)]), None)
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    value = document["goal_conjunct_values"]["any_at(target)"]
    assert value["_status"] == "unavailable"
    assert "predicate sensor unavailable" in value["reason"]


def test_unadmitted_predicate_is_not_evaluated(tmp_path: Path) -> None:
    """Predicates without a proven read-only path remain explicitly unavailable."""
    predicate = FakePredicate("target", True)
    predicate.name = "is_detected"
    diagnostics = make_diagnostics(tmp_path)
    diagnostics.record_reset(FakeEnv([predicate]), None)
    document = json.loads((tmp_path / "evidence/diagnostics-initial.json").read_text())
    value = document["goal_conjunct_values"]["is_detected(target)"]
    assert value["_status"] == "unavailable"
    assert "not admitted" in value["reason"]
    assert predicate.evaluated == 0


def test_predicate_reads_do_not_mutate_official_cache(tmp_path: Path) -> None:
    """Diagnostic Predicate.is_true calls cannot populate Habitat's official cache."""

    @dataclass
    class CachedSimInfo:
        """Minimal dataclass matching Habitat's optional predicate cache field."""

        pred_truth_cache: dict[str, bool] | None

    class CachingPredicate(FakePredicate):
        """Mimic Habitat Predicate.is_true cache population."""

        def is_true(self, sim_info: CachedSimInfo) -> bool:
            """Write only when the supplied diagnostic view exposes a cache."""
            if sim_info.pred_truth_cache is not None:
                sim_info.pred_truth_cache[repr(self)] = self.truth
            return self.truth

    predicate = CachingPredicate("target", True)
    env = FakeEnv([predicate])
    official_cache = {"existing": False}
    env.task.pddl_problem.sim_info = CachedSimInfo(official_cache)
    make_diagnostics(tmp_path).record_reset(env, None)
    assert official_cache == {"existing": False}


def test_serialization_failure_writes_one_unavailable_jsonl_row(tmp_path: Path) -> None:
    """An unserializable step becomes one bounded unavailable JSONL record."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    diagnostics.record_step(
        1,
        [object()],  # type: ignore[list-item]
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        False,
        {},
        {},
    )
    lines = (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["_status"] == "unavailable"


def test_oversized_record_writes_one_unavailable_jsonl_row(tmp_path: Path) -> None:
    """A step over the byte cap becomes one bounded unavailable JSONL record."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    diagnostics.record_step(
        1,
        ["x" * (DIAGNOSTICS_MAX_RECORD_BYTES + 1)],
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        False,
        {},
        {},
    )
    lines = (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    assert len(lines) == 1
    document = json.loads(lines[0])
    assert document["_status"] == "unavailable"
    assert "byte limit" in document["reason"]


def test_file_write_failures_do_not_escape(tmp_path: Path) -> None:
    """Reset, step, and terminal write failures never alter execution control flow."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    diagnostics = PhysicalDiagnostics(blocked / "evidence", (0, 1), True, 10)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    diagnostics.record_reset(env, None)
    diagnostics.record_step(
        1,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        True,
        {},
        {},
    )
    diagnostics.record_terminal(env, 1, "episode_done")
    assert diagnostics._dropped_records == 2
    assert diagnostics._step_writer.stats()["records_dropped"] == 1


def test_early_episode_end_and_continued_shared_world_are_recorded(tmp_path: Path) -> None:
    """Early local completion remains distinct from a later joint episode end."""
    diagnostics = make_diagnostics(tmp_path)
    env = FakeEnv([FakePredicate("target", False)], metrics={"pddl_success": False})
    actor = FakeActor([FakeSkill(2, 10), FakeSkill(2, 10)], ["wait", "nav_to_obj"])
    first_observations = {
        "agent_0_has_finished_oracle_nav": [True],
        "agent_1_has_finished_oracle_nav": [False],
    }
    diagnostics.record_step(
        2,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        False,
        {"pddl_success": False},
        first_observations,
        first_observations,
    )
    env.sim._agents[0].base_pos = [4.0, 5.0, 6.0]
    diagnostics.record_step(
        3,
        ["wait", "nav_to_obj"],
        [FakeVec([1.0]), FakeVec([1.0])],
        env,
        actor,
        True,
        {"pddl_success": False},
        first_observations,
        first_observations,
    )
    diagnostics.record_terminal(env, 3, "episode_done")
    rows = [
        json.loads(line)
        for line in (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    ]
    terminal = json.loads((tmp_path / "evidence/diagnostics-terminal.json").read_text())
    assert len(rows) == 2 and rows[0]["done"] is False and rows[1]["done"] is True
    assert rows[0]["agents"]["0"]["oracle_nav_finished_sensor_post_step"] is True
    assert rows[1]["agents"]["0"]["position"] == [4.0, 5.0, 6.0]
    assert rows[1]["agents"]["0"]["rotation"] == {
        "representation": "yaw",
        "unit": "radians",
        "value": 0.25,
    }
    assert terminal["termination_reason"] == "episode_done"


def test_record_budget_is_bounded_and_explicit(tmp_path: Path) -> None:
    """The configured row budget emits one unavailable marker and stops growth."""
    diagnostics = PhysicalDiagnostics(tmp_path / "evidence", (0, 1), True, 1, write_batch_records=1)
    env = FakeEnv([FakePredicate("target", False)])
    actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
    for step in range(1, 4):
        diagnostics.record_step(
            step,
            ["wait", "nav_to_obj"],
            [FakeVec([1.0]), FakeVec([1.0])],
            env,
            actor,
            False,
            {},
            {},
        )
    rows = [
        json.loads(line)
        for line in (tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 2
    assert rows[1]["_status"] == "unavailable"
    assert "budget 1 exhausted" in rows[1]["reason"]


def test_enabled_and_disabled_recorders_preserve_actions_and_outcome(tmp_path: Path) -> None:
    """Observation does not add policy/step calls or change a deterministic result."""

    def execute(enabled: bool, directory: Path) -> tuple[list[int], bool, int, int]:
        """Run a tiny fixed loop and return its action/result/call evidence."""
        diagnostics = PhysicalDiagnostics(directory, (0, 1), enabled, 2)
        env = FakeEnv([FakePredicate("target", True)], metrics={"pddl_success": True})
        actor = FakeActor([FakeSkill(1, 10), FakeSkill(1, 10)], ["wait", "nav_to_obj"])
        actor_calls = 0
        step_calls = 0
        selected: list[int] = []
        observations = {
            "agent_0_has_finished_oracle_nav": [False],
            "agent_1_has_finished_oracle_nav": [False],
        }
        diagnostics.record_reset(env, None)
        for step in range(1, 3):
            actor_calls += 1
            action = [FakeVec([1.0, 0.0]), FakeVec([0.0, 1.0])]
            selected.extend(int(row.argmax()) for row in action)
            step_calls += 1
            diagnostics.record_step(
                step,
                ["wait", "nav_to_obj"],
                action,
                env,
                actor,
                step == 2,
                {"pddl_success": step == 2},
                observations,
                observations,
            )
        diagnostics.record_terminal(env, 2, "episode_done")
        return selected, bool(env.get_metrics()["pddl_success"]), actor_calls, step_calls

    assert execute(False, tmp_path / "disabled") == execute(True, tmp_path / "enabled")
