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

        def exchange(peer: Any, path: str, body: Any = None) -> Any:
            """Return the matching projection; record only actual goal submissions."""
            if path == "/v1/mission-goal-evidence":
                posted.append(body)
                return {"receipt": {"evidence": body, "authority_subject": "observer"}}
            return {
                "/v1/missions/trial/plan-revisions": view,
                "/v1/missions/trial": {"status": "Running", "group_id": view["current_group_id"]},
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
    fields = arrival.policy_fields(original)
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
