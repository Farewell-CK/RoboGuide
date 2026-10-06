"""Offline regressions for single-call, bounded motion evidence and exception isolation."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_INTEGRATION_ROOT = Path(__file__).parents[1]
if str(_INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_INTEGRATION_ROOT))

from habitat_local_eaios.model import CanonicalMobilityInvocation, IntegrationError  # noqa: E402
from habitat_local_eaios.motion_diagnostics import (  # noqa: E402
    MAX_CALLS_PER_STEP,
    MotionDiagnostics,
)


class FakePathfinder:
    """Count original motion queries without introducing any simulator APIs."""

    def __init__(self, clamp: bool) -> None:
        """Provide a fixed mesh model and explicit loaded state."""
        self.clamp = clamp
        self.is_loaded = True
        self.sliding_calls = 0
        self.non_sliding_calls = 0
        self.nav_mesh_settings = SimpleNamespace(
            agent_height=1.0,
            agent_radius=0.05,
            agent_max_climb=0.02,
            agent_max_slope=5.0,
            cell_size=0.05,
            cell_height=0.2,
        )

    def try_step(self, start: list[float], end: list[float]) -> list[float]:
        """Return the exact selected object and count one original sliding call."""
        self.sliding_calls += 1
        return start if self.clamp else end

    def try_step_no_sliding(self, start: list[float], end: list[float]) -> list[float]:
        """Return the exact selected object and count one original non-sliding call."""
        self.non_sliding_calls += 1
        return start if self.clamp else end


class FakeAction:
    """Represent the vendor's filter-then-update order, including later return mutation."""

    def __init__(self, *, clamp: bool = False, sliding: bool = True, revert: bool = False) -> None:
        """Create one motion object with observable capability and actual base state."""
        self.pathfinder = FakePathfinder(clamp)
        self.config = SimpleNamespace(
            agent_height=1.0,
            agent_radius=0.0,
            agent_max_climb=0.02,
            agent_max_slope=5.0,
            allow_dyn_slide=sliding,
        )
        self._allow_dyn_slide = sliding
        self.cur_articulated_agent = SimpleNamespace(base_pos=[0.0, 0.0, 0.0])
        self.revert = revert
        self.update_calls = 0
        self.return_token = object()

    def step_filter(self, start_pos: list[float], end_pos: list[float]) -> list[float]:
        """Use the same original loaded/sliding branches without an extra query."""
        if self.pathfinder.is_loaded:
            if self.config.allow_dyn_slide:
                return self.pathfinder.try_step(start_pos, end_pos)
            return self.pathfinder.try_step_no_sliding(start_pos, end_pos)
        return end_pos

    def update_base(self, amount: float = 1.0) -> object:
        """Apply the returned end unless local behavior restores the previous position."""
        self.update_calls += 1
        before = list(self.cur_articulated_agent.base_pos)
        end = [before[0] + amount, before[1], before[2]]
        filtered = self.step_filter(before, end)
        self.cur_articulated_agent.base_pos = before if self.revert else list(filtered)
        return self.return_token


def invocation(
    task: str, destination: str, attempt: str | None = "attempt-1"
) -> CanonicalMobilityInvocation:
    """Build an exact logical attempt without any physical-node selection."""
    return CanonicalMobilityInvocation(
        mission_id="mission",
        task_id=task,
        group_id="group",
        role_id="role",
        operation="mobility.move@v1",
        objective="Reach the canonical destination",
        parameters={"destination": destination},
        resource_ids=("space-test",),
        attempt_id=attempt,
    )


@pytest.mark.parametrize("sliding", [True, False])
@pytest.mark.parametrize(
    ("clamp", "revert", "amount", "filtered_end", "actual_end"),
    [
        (False, False, 1.0, [1.0, 0.0, 0.0], [1.0, 0.0, 0.0]),
        (True, False, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
        (False, True, 1.0, [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
        (False, False, 0.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
    ],
)
def test_motion_tap_preserves_actual_updates_and_distinguishes_observed_stages(
    sliding: bool,
    clamp: bool,
    revert: bool,
    amount: float,
    filtered_end: list[float],
    actual_end: list[float],
) -> None:
    """Observe zero requests, mesh restriction and post-filter state without causal invention."""
    action = FakeAction(clamp=clamp, sliding=sliding, revert=revert)
    baseline = FakeAction(clamp=clamp, sliding=sliding, revert=revert)
    collector = MotionDiagnostics()
    collector.bind({0: invocation("reach", "entity")})
    restores = collector.install(action, 0)
    assert action.update_base(amount) is action.return_token
    baseline.update_base(amount)
    assert action.update_calls == baseline.update_calls == 1
    assert (
        action.cur_articulated_agent.base_pos
        == baseline.cur_articulated_agent.base_pos
        == actual_end
    )
    assert action.pathfinder.sliding_calls == baseline.pathfinder.sliding_calls == int(sliding)
    assert (
        action.pathfinder.non_sliding_calls
        == baseline.pathfinder.non_sliding_calls
        == int(not sliding)
    )
    record = collector.sample(0, 1)
    assert record["call_count"] == len(record["calls"]) == 2
    assert record["call_records_complete"] is True
    update, filtered = record["calls"]
    assert update["base_position_before"] == [0.0, 0.0, 0.0]
    assert update["base_position_after"] == actual_end
    assert filtered["requested_start"] == [0.0, 0.0, 0.0]
    assert filtered["requested_end"] == [amount, 0.0, 0.0]
    assert filtered["returned_end"] == filtered_end
    assert filtered["derived_comparison"]["requested_distance_m"] == amount
    assert record["invocation"]["attempt_id"] == "attempt-1"
    assert record["navigation_profile"]["action_config"]["agent_radius"] == 0.0
    assert record["navigation_profile"]["active_navmesh_settings"]["agent_radius"] == 0.05
    assert "collision" not in record and "success" not in record
    for restore in reversed(restores):
        restore()
    assert "step_filter" not in vars(action) and "update_base" not in vars(action)


def test_endpoint_copy_precedes_original_argument_and_return_mutation() -> None:
    """Copied evidence stays immutable while the original receives and returns its exact objects."""
    action = FakeAction()
    start, end = [0.0, 0.0, 0.0], [2.0, 0.0, 0.0]

    def mutate(start_pos: Any, end_pos: Any) -> Any:
        """Mutate the original arguments to exercise the observation timing boundary."""
        assert start_pos is start and end_pos is end
        start_pos[0] = 5.0
        end_pos[0] = 3.0
        return end_pos

    action.step_filter = mutate  # type: ignore[method-assign]
    collector = MotionDiagnostics()
    restores = collector.install(action, 0)
    returned = action.step_filter(start_pos=start, end_pos=end)
    assert returned is end
    returned[0] = 9.0
    filtered = collector.sample(0, 7)["calls"][0]
    assert filtered["requested_start"] == [0.0, 0.0, 0.0]
    assert filtered["requested_end"] == [2.0, 0.0, 0.0]
    assert filtered["returned_end"] == [3.0, 0.0, 0.0]
    for restore in reversed(restores):
        restore()
    assert action.step_filter is mutate


@pytest.mark.parametrize("keyword", ["_original", "_name"])
def test_wrapper_metadata_never_intercepts_original_keyword_arguments(keyword: str) -> None:
    """All keyword values reach the original method intact, including names used by observers."""
    action = FakeAction()
    marker = object()
    calls: list[tuple[Any, Any, dict[str, Any]]] = []

    def original(start_pos: Any, end_pos: Any, **kwargs: Any) -> Any:
        """Return the same end object after retaining all actual received arguments."""
        calls.append((start_pos, end_pos, kwargs))
        return end_pos

    action.step_filter = original  # type: ignore[method-assign]
    collector = MotionDiagnostics()
    collector.install(action, 0)
    start, end = [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]
    assert action.step_filter(start, end, **{keyword: marker}) is end
    assert len(calls) == 1
    assert calls[0][0] is start and calls[0][1] is end
    assert calls[0][2][keyword] is marker
    assert collector.sample(0, 1)["calls"][0]["returned_end"] == end


@pytest.mark.parametrize("error", [RuntimeError("original sentinel"), KeyboardInterrupt()])
def test_failed_original_step_is_retained_pending_and_rethrows_same_error(
    error: BaseException,
) -> None:
    """A failed step has no invented simulator number and never loses its original exception."""
    action = FakeAction()
    calls = []

    def fail(start_pos: Any, end_pos: Any) -> Any:
        """Raise the precise physical exception after one actual invocation."""
        calls.append((start_pos, end_pos))
        raise error

    action.step_filter = fail  # type: ignore[method-assign]
    collector = MotionDiagnostics()
    collector.install(action, 0)
    with pytest.raises(type(error)) as caught:
        action.update_base()
    assert caught.value is error and len(calls) == action.update_calls == 1
    pending = collector.boundary(0)["pending_since_last_post_step"]
    assert "simulator_step" not in pending
    assert pending["calls"][1]["exception_type"] == type(error).__name__
    assert pending["calls"][1]["returned_end"]["_status"] == "unavailable"
    assert pending["calls"][0]["base_position_after"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("stage", ["_begin", "_finish", "_observe_finish"])
def test_observation_failures_never_replace_original_motion_or_error(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read/callback/timing failures remain diagnostics even during a physical exception."""
    collector = MotionDiagnostics()
    action = FakeAction()
    collector.install(action, 0)

    def broken(*args: Any, **kwargs: Any) -> Any:
        """Fail only observation, before or after the original physical call."""
        raise OSError("observer sentinel")

    monkeypatch.setattr(collector, stage, broken)
    assert action.update_base() is action.return_token
    assert action.update_calls == action.pathfinder.sliding_calls == 1
    assert action.cur_articulated_agent.base_pos == [1.0, 0.0, 0.0]
    original_error = RuntimeError("physical sentinel")

    def fail(start_pos: Any, end_pos: Any) -> Any:
        """Raise the original error that a failed observer must not mask."""
        raise original_error

    # The update wrapper remains installed; its original update calls this failing filter.
    action.step_filter = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as caught:
        action.update_base()
    assert caught.value is original_error
    assert collector.stats()["observation_failures"] > 0


@pytest.mark.parametrize("point", [[], [0.0, 1.0], [float("nan"), 0.0, 0.0], [True, 0.0, 0.0]])
def test_unreadable_positions_do_not_change_original_return(point: list[Any]) -> None:
    """Malformed observation stays unavailable rather than becoming a synthetic point."""
    action = FakeAction()
    action.pathfinder.is_loaded = False
    collector = MotionDiagnostics()
    collector.install(action, 0)
    assert action.step_filter([0.0, 0.0, 0.0], point) is point
    filtered = collector.sample(0, 1)["calls"][0]
    assert filtered["requested_end"]["_status"] == "unavailable"
    assert filtered["returned_end"]["_status"] == "unavailable"
    assert "derived_comparison" not in filtered


def test_many_original_calls_are_bounded_and_loss_is_explicit() -> None:
    """Recording stops at its fixed cap without suppressing any original motion call."""
    action = FakeAction()
    collector = MotionDiagnostics()
    collector.install(action, 3)
    for _ in range(MAX_CALLS_PER_STEP + 5):
        action.step_filter([0.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    assert action.pathfinder.sliding_calls == MAX_CALLS_PER_STEP + 5
    record = collector.sample(3, 4)
    assert record["call_count"] == MAX_CALLS_PER_STEP + 5
    assert len(record["calls"]) == MAX_CALLS_PER_STEP
    assert record["dropped_calls"] == collector.stats()["calls_dropped"] == 5
    assert record["call_records_complete"] is False
    assert collector.sample(3, 5)["_status"] == "unavailable"
    assert collector.boundary(3)["last_post_step"]["simulator_step"] == 4


def test_rebinding_cannot_attribute_previous_task_motion_to_new_attempt() -> None:
    """Single-Actor reuse and recovery begin with fresh Task/attempt evidence."""
    action = FakeAction()
    collector = MotionDiagnostics()
    collector.install(action, 0)
    collector.bind({0: invocation("first", "first-target")})
    action.update_base()
    collector.sample(0, 1)
    action.update_base()
    collector.bind({0: invocation("second", "second-target", "attempt-2")})
    assert collector.boundary(0)["last_post_step"]["_status"] == "unavailable"
    assert collector.boundary(0)["pending_since_last_post_step"] is None
    assert collector.stats()["pending_calls_cleared_at_bind"] == 2
    action.update_base()
    assert collector.sample(0, 3)["invocation"] == {
        "mission_id": "mission",
        "task_id": "second",
        "group_id": "group",
        "role_id": "role",
        "attempt_id": "attempt-2",
        "operation": "mobility.move@v1",
        "canonical_destination": "second-target",
    }


def test_missing_attempt_or_invocation_is_explicit_not_synthesized() -> None:
    """Legacy/native evidence cannot fabricate a durable execution identity."""
    action = FakeAction()
    collector = MotionDiagnostics()
    collector.install(action, 0)
    action.update_base()
    assert collector.sample(0, 1)["invocation"]["_status"] == "unavailable"
    collector.bind({0: invocation("task", "target", None)})
    action.update_base()
    assert collector.sample(0, 2)["invocation"]["attempt_id"]["_status"] == "unavailable"


def test_failed_rebinding_cannot_retain_old_attempt_identity() -> None:
    """Even unreadable new metadata invalidates old Task/attempt evidence before physical motion."""
    action = FakeAction()
    collector = MotionDiagnostics()
    collector.install(action, 0)
    first = invocation("first", "north")
    collector.bind({0: first})
    action.update_base()
    collector.sample(0, 1)
    action.update_base()
    with pytest.raises(IntegrationError):
        collector.bind({0: replace(first, task_id="next", parameters={"destination": 42})})
    assert collector.boundary(0)["last_post_step"]["_status"] == "unavailable"
    assert collector.boundary(0)["pending_since_last_post_step"] is None
    assert collector.stats()["pending_calls_cleared_at_bind"] == 2
    action.update_base()
    assert collector.sample(0, 2)["invocation"]["_status"] == "unavailable"
    assert action.update_calls == action.pathfinder.sliding_calls == 3


def test_unsupported_method_override_is_rolled_back_without_partial_motion_hook() -> None:
    """A failure to install the second method restores the first original method."""

    class RestrictedAction(FakeAction):
        """Refuse a motion-update override while allowing normal original calls."""

        def __setattr__(self, name: str, value: Any) -> None:
            """Fail only diagnostic method installation, not original motion state writes."""
            if name == "update_base":
                raise AttributeError("method override unsupported")
            super().__setattr__(name, value)

    action = RestrictedAction()
    collector = MotionDiagnostics()
    assert collector.install(action, 0) == []
    assert "step_filter" not in vars(action)
    assert action.update_base() is action.return_token
    assert action.pathfinder.sliding_calls == action.update_calls == 1
    assert collector.sample(0, 1)["_status"] == "unavailable"


@pytest.mark.parametrize("agent_count", [1, 2, 4])
def test_each_configured_agent_retains_only_its_own_motion_and_identity(agent_count: int) -> None:
    """Instance taps remain isolated for any configured diagnostic agent set."""
    collector = MotionDiagnostics()
    actions = [FakeAction() for _ in range(agent_count)]
    collector.bind(
        {agent: invocation(f"task-{agent}", f"target-{agent}") for agent in range(agent_count)}
    )
    for agent, action in enumerate(actions):
        collector.install(action, agent)
        action.update_base(float(agent + 1))
    for agent in range(agent_count):
        record = collector.sample(agent, 8)
        assert record["agent_id"] == agent
        assert record["invocation"]["task_id"] == f"task-{agent}"
        assert record["invocation"]["canonical_destination"] == f"target-{agent}"
        assert record["calls"][0]["base_position_after"] == [float(agent + 1), 0.0, 0.0]


def test_unavailable_capability_reads_never_change_or_guess_the_motion_model() -> None:
    """A bad declaration remains unknown while independently observed mesh settings are retained."""
    action = FakeAction()
    action.config.agent_max_slope = float("nan")
    collector = MotionDiagnostics()
    collector.install(action, 0)
    action.update_base()
    profile = collector.sample(0, 1)["navigation_profile"]
    assert profile["action_config"]["agent_max_slope"]["_status"] == "unavailable"
    assert profile["active_navmesh_settings"]["agent_max_slope"] == 5.0
    assert action.cur_articulated_agent.base_pos == [1.0, 0.0, 0.0]
    assert action.pathfinder.sliding_calls == 1
