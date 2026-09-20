"""Deterministic offline validation of read-only physical diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.diagnostics import (  # noqa: E402
    DIAGNOSTICS_SCHEMA,
    PhysicalDiagnostics,
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


class FakePredicate:
    """One official goal conjunct stub with independently controllable truth."""

    def __init__(self, name: str, truth: bool) -> None:
        """Store the label and the truth this conjunct reports."""
        self.name = name
        self.truth = truth
        self.evaluated = 0
        self._arg_values = [type("Entity", (), {"name": name})()]

    def __repr__(self) -> str:
        """Render the conjunct label the way a real predicate would."""
        return f"any_at({self.name})"

    def is_true(self, sim_info: Any) -> bool:
        """Report official truth and count evaluations."""
        self.evaluated += 1
        return self.truth


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
        self.base_rot = [0.0, 0.0, 0.0, 1.0]


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
        self._idx_to_name = [name]
        self.defined_skills = {name: skill}
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
    return PhysicalDiagnostics(tmp_path / "evidence", (0, 1), enabled)


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
    assert first.evaluated >= 1 and second.evaluated >= 1
    assert document["schema_version"] == DIAGNOSTICS_SCHEMA
    assert document["seed"] is None  # config stub carries no seed
    assert document["habitat_seed_config"] == 40


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
    diagnostics.record_step(
        1000,
        ["wait", "nav_to_obj"],
        [FakeVec([0.0, 1.0]), FakeVec([0.0, 1.0])],
        env,
        actor,
        False,
        {},
        {},
    )
    row = json.loads((tmp_path / "evidence/diagnostics-steps.jsonl").read_text().splitlines()[0])
    fetch = row["agents"]["1"]["skill_state"]
    spot = row["agents"]["0"]["skill_state"]
    assert fetch["cur_skill_step"] == 1000.0 and fetch["max_skill_steps"] == 1000
    assert fetch["force_end_on_timeout"] is False
    assert spot["cur_skill_step"] == 3.0
    assert row["agents"]["1"]["oracle_flags"]["oracle_skill_done"] is False


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
    assert len(diagnostics.__dict__) == 5  # no per-step state was accumulated
