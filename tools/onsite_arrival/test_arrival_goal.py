"""Regression tests for independent locations, Role handoff and human confirmation races."""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import Mock

import arrival_goal as arrival
import pytest
from mission.submission_evidence import canonical_plan_digest


def plan(location: str, entity: str = "dog2", task_id: str = "visit") -> dict[str, Any]:
    """Produce the normalized Controller projection of one navigation Role."""
    return {
        "mission": {
            "id": "trial",
            "objective": "巡检机器人到达并停稳",
            "actors": [{"id": "navigator", "physical_entity": entity}],
        },
        "contexts": [{"id": "trip", "roles": [{"id": "driver", "actor": "navigator"}]}],
        "tasks": [
            {
                "id": task_id,
                "context_id": "trip",
                "roles": [
                    {
                        "id": "move",
                        "context_role": "driver",
                        "execution_intent": {
                            "operation": arrival.NAVIGATE,
                            "objective": "go",
                            "parameters": {"destination": location, "frame": "map", "x": 13.99},
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def fixture(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Model one authenticated revision view, attempt history and the receipt ingress."""

    def build(allow_handoff: bool) -> tuple[Any, Any, Any, Any]:
        """Freeze the original and provide mutable current facts for race tests."""
        original = plan("电梯口")
        fields = arrival.policy_fields(original, allow_handoff=allow_handoff)
        view = {
            "current_plan": original,
            "mission_id": "trial",
            "current_group_id": "G1",
            "goal_context": fields["goal_context"],
            "goal_contract": {
                "tenant_id": "lab",
                "mission_id": "trial",
                "digest": "contract",
                "context_digest": canonical_plan_digest(fields["goal_context"]),
                "spec": fields["goal_verifier"],
                "source": {
                    "source_id": "observer",
                    "revision": 1,
                    "max_age_ms": arrival.AGE_MS,
                    "require_artifact": False,
                    "producer_principals": ["observer"],
                },
            },
            "current_plan_digest": "plan",
            "current_plan_revision": 0,
            "goal_source_current": True,
        }
        rows = [
            {
                "group_id": "G1",
                "task_id": "visit",
                "role_id": "move",
                "node_id": "thor2",
                "status": "Completed",
                "mission_id": "trial",
                "execution_id": "attempt-a",
            }
        ]
        posted: list[dict[str, Any]] = []

        def exchange(
            peer: Any, path: str, body: Any = None, *, deadline_ns: int | None = None
        ) -> Any:
            """Return the matching projection; record only actual goal submissions."""
            if path == "/v1/mission-goal-evidence":
                posted.append(body)
                return {
                    "receipt": {
                        "evidence": body,
                        "authority_subject": "observer",
                        "input_digest": canonical_plan_digest(body),
                    }
                }
            return {
                "/v1/missions/trial/plan-revisions": view,
                "/v1/missions/trial": {
                    "status": "Running",
                    "group_id": view["current_group_id"],
                    "mission_id": "trial",
                    "goal_gate": view["goal_contract"]["digest"],
                    "isolation": None,
                    "plan_revision": view["current_plan_revision"],
                },
                "/v1/missions/trial/execution-attempts": {"attempts": rows},
            }[path]

        monkeypatch.setattr(arrival, "exchange", exchange)
        return Mock(tenant="lab", principal="observer"), view, rows, posted

    return build


def switch(view: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Switch to a reviewed replacement plan while retaining the original attempt history."""
    view.update(
        current_plan=plan("电梯口", "dog1", "takeover"),
        current_group_id="G2",
        current_plan_digest="new-plan",
        current_plan_revision=1,
    )
    rows.append(
        {
            **rows[0],
            "task_id": "takeover",
            "node_id": "thor1",
            "group_id": "G2",
            "execution_id": "attempt-b",
        }
    )


def test_two_locations_reuse_one_predicate() -> None:
    """Two original targets need distinct contexts and the same registry spec."""
    a, b = [arrival.policy_fields(plan(site)) for site in ["电梯口", "会议室"]]
    assert a["goal_verifier"] == b["goal_verifier"]
    assert a["goal_context"]["target"] != b["goal_context"]["target"]


def test_delegate_preserves_map_and_original_text() -> None:
    """Retain the actual deployed local objective without parsing labels or adding parameters."""
    original = plan("电梯口")
    intent = original["tasks"][0]["roles"][0]["execution_intent"]
    intent.update(
        operation=arrival.DELEGATE,
        parameters={"profile": "local-task-v1"},
        objective="到电梯口 (13.99, 0.26)，地图 A，到达后保持停止。",
    )
    untouched = copy.deepcopy(original)
    fields = arrival.policy_fields(original, task_id="visit")
    assert fields["goal_context"]["target"]["objective"] == intent["objective"]
    assert original == untouched


def test_reviewed_replacement_shows_actual_robot(fixture: Any) -> None:
    """A Role-scoped observation must identify the new Actor and current attempt only."""
    peer, view, rows, posted = fixture(True)
    switch(view, rows)
    display, _ = arrival.observation(peer, "trial")
    assert "dog1" in display and "电梯口" in display
    arrival.confirm(peer, "trial", lambda _: "yes")
    assert posted[0]["attempts"] == [
        {
            "task_ref": {"mission_id": "trial", "task_id": "takeover"},
            "role_id": "move",
            "attempt_id": "attempt-b",
        }
    ]


def test_replacement_cannot_satisfy_fixed_entity(fixture: Any) -> None:
    """A fixed-dog goal must refuse an otherwise valid replacement."""
    peer, view, rows, posted = fixture(False)
    switch(view, rows)
    with pytest.raises(ValueError, match="different robot"):
        arrival.observation(peer, "trial")
    assert not posted


def test_switch_during_confirmation_requires_new_observation(fixture: Any) -> None:
    """A human answer based on dog2 cannot become a report about dog1."""
    peer, view, rows, posted = fixture(True)

    def answer(prompt: str) -> str:
        """Switch the actual execution after displaying the original question."""
        assert "dog2" in prompt
        switch(view, rows)
        return "yes"

    with pytest.raises(ValueError, match="changed during confirmation"):
        arrival.confirm(peer, "trial", answer)
    assert not posted


def test_changed_target_refused_after_switch(fixture: Any) -> None:
    """Even an approved replacement must preserve the original arrival target."""
    peer, view, rows, posted = fixture(True)
    switch(view, rows)
    view["current_plan"] = plan("会议室", "dog1", "takeover")
    with pytest.raises(ValueError, match="destination differs"):
        arrival.observation(peer, "trial")
    assert not posted


def test_unknown_does_not_publish(fixture: Any) -> None:
    """An unavailable human observation never emits a positive verdict."""
    peer, _, _, posted = fixture(False)
    assert arrival.confirm(peer, "trial", lambda _: "unknown") == {
        "observation": "unknown",
        "submitted": False,
    }
    assert not posted


def test_handoff_with_another_destination_in_the_same_role(fixture: Any) -> None:
    """Find the original goal even when the reviewed itinerary includes another stop."""
    peer, view, rows, _ = fixture(True)
    switch(view, rows)
    current = view["current_plan"]
    current["tasks"].extend(plan("会议室", "dog1", "other-stop")["tasks"])
    display, _ = arrival.observation(peer, "trial")
    assert "电梯口" in display


def test_mutating_exported_policy_cannot_change_future_contracts() -> None:
    """An experiment adjusting its copy cannot alter source enrollment or later goals."""
    first = arrival.policy_fields(plan("电梯口"))
    first["goal_verifier"]["verifier"] = {"name": "foreign"}
    first["goal_context"]["target"]["operation"]["name"] = "foreign"
    second = arrival.policy_fields(plan("会议室"))
    assert second["goal_verifier"]["verifier"]["name"] == "onsite"
    assert second["goal_context"]["target"]["operation"]["name"] == "navigate"


def test_generic_task_needs_explicit_selection() -> None:
    """Do not automatically select a photo or posture delegation as the arrival Task."""
    original = plan("电梯口")
    role = original["tasks"][0]["roles"][0]
    role["execution_intent"] = {
        "operation": arrival.DELEGATE,
        "parameters": {"profile": "local-task-v1"},
        "objective": "拍照并汇报电量",
    }
    with pytest.raises(ValueError, match="--task"):
        arrival.policy_fields(original)


def test_second_read_cannot_ignore_isolation(fixture: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold any new report if the Mission entered isolation before the answer."""
    peer, _, _, posted = fixture(False)
    exchange = arrival.exchange
    isolated = False

    def read(*args: Any, **kwargs: Any) -> Any:
        """Return actual-shaped Running with an active isolation after the first read."""
        result = exchange(*args, **kwargs)
        if args[1] == "/v1/missions/trial" and isolated:
            result["isolation"] = {"kind": "safety"}
        return result

    def answer(_: str) -> str:
        """Move the Mission into isolation while the prompt is displayed."""
        nonlocal isolated
        isolated = True
        return "yes"

    monkeypatch.setattr(arrival, "exchange", read)
    with pytest.raises(ValueError, match="isolation"):
        arrival.confirm(peer, "trial", answer)
    assert not posted


def test_duplicate_destinations_still_require_unique_attempt(fixture: Any) -> None:
    """A matching label and responsibility must not choose between identical stops."""
    peer, view, rows, posted = fixture(True)
    switch(view, rows)
    view["current_plan"]["tasks"].extend(plan("电梯口", "dog1", "another-stop")["tasks"])
    with pytest.raises(ValueError, match="found 2"):
        arrival.observation(peer, "trial")
    assert not posted


@pytest.mark.parametrize("read_ms", [800, 1001])
def test_confirmation_shares_the_shorter_source_budget(
    fixture: Any, monkeypatch: pytest.MonkeyPatch, read_ms: int
) -> None:
    """Charge the recheck, the outbound call and the receive TTL against the source limit."""
    peer, view, _, posted = fixture(False)
    view["goal_contract"]["source"]["max_age_ms"] = 1000
    clock = Mock()
    clock.monotonic_ns.return_value = 0
    clock.time_ns.return_value = 1234567890123456789
    monkeypatch.setattr(arrival, "time", clock)
    ordinary = arrival.exchange

    def exchange(peer: Any, path: str, body: Any = None, *, deadline_ns: int | None = None) -> Any:
        """Delay the reread and capture the actual latest send deadline."""
        if path.endswith("/execution-attempts") and deadline_ns is not None:
            clock.monotonic_ns.return_value = read_ms * 1_000_000
            clock.time_ns.return_value += read_ms * 1_000_000
        if body is not None:
            assert deadline_ns is not None and clock.monotonic_ns() < deadline_ns
            assert deadline_ns + body["valid_for_ms"] * 1_000_000 <= 1_000_000_000
            assert body["sequence"] == 1234567890123456789
        return ordinary(peer, path, body, deadline_ns=deadline_ns)

    monkeypatch.setattr(arrival, "exchange", exchange)
    if read_ms < 1000:
        arrival.confirm(peer, "trial", lambda _: "yes")
        assert len(posted) == 1
    else:
        with pytest.raises(ValueError, match="expired"):
            arrival.confirm(peer, "trial", lambda _: "yes")
        assert not posted


@pytest.mark.parametrize("recorded", [True, False])
def test_lost_reply_checks_only_the_original_input(
    fixture: Any, monkeypatch: pytest.MonkeyPatch, recorded: bool
) -> None:
    """Recover a recorded observation by digest and leave unconfirmed delivery unresolved."""
    from roboguide_workbench.client import WorkbenchError

    peer, view, _, posted = fixture(False)
    ordinary = arrival.exchange

    def exchange(peer: Any, path: str, body: Any = None, **kwargs: Any) -> Any:
        """Drop the only POST response and expose only the selected retained input."""
        result = ordinary(peer, path, body, **kwargs)
        if body is not None:
            view["goal_evidence"] = [
                {
                    "input_digest": canonical_plan_digest(body) if recorded else "different",
                    "completed": False,
                    "incident": None,
                }
            ]
            raise WorkbenchError("response lost")
        return result

    monkeypatch.setattr(arrival, "exchange", exchange)
    if recorded:
        result = arrival.confirm(peer, "trial", lambda _: "yes")
        assert result == {"submission": "recorded", "goal_evidence": view["goal_evidence"][0]}
    else:
        with pytest.raises(ValueError, match="/v1/missions/trial/plan-revisions"):
            arrival.confirm(peer, "trial", lambda _: "yes")
    assert len(posted) == 1
