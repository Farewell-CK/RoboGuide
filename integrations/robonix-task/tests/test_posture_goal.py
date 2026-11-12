"""Test ordering and attribution failures that must never become goal satisfaction."""

from copy import deepcopy

import pytest
from robonix_task.posture import inspect_posture
from robonix_task.posture_goal import assess_goal


def observations():
    """Build distinct Controller and Robonix sample identities without physical claims."""
    inventory = {
        "schema_version": "roboguide.inventory/v0.1",
        "nodes": [{"node_id": "fixture", "liveness": "Reachable"}],
    }
    state = {
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": 1001,
        "records": [
            {
                "object": {"class": "node", "object_type": "robot", "object_id": "fixture"},
                "source": "node:fixture/robot-observer",
                "channel_id": "robot-state",
                "semantic": "reported",
                "stale": False,
                "value": {
                    "payload_schema": "lite3.robot-state/v0.1",
                    "received_at_ms": 1000,
                    "valid_for_ms": 2000,
                    "source_epoch": "epoch-1",
                    "sequence": 1,
                    "value": {
                        "chassis": {
                            "contract_id": "robonix/primitive/chassis/state",
                            "availability": "current",
                            "age_ms": 1,
                            "provider_id": "fixture-provider",
                            "registration_id": "reg-1",
                            "received_at_ns": 1000000000,
                            "value": {"posture": "standing", "stale": False, "stale_ms": 5000},
                        }
                    },
                },
            }
        ],
    }
    anchor = inspect_posture(state, inventory, node_id="fixture")
    current = deepcopy(state)
    current["observed_at_ms"] = 1101
    envelope = current["records"][0]["value"]
    envelope.update(sequence=2, received_at_ms=1100)
    envelope["value"]["chassis"]["received_at_ns"] = 1100000000
    envelope["value"]["chassis"]["value"]["posture"] = "down"
    return anchor, current, inventory


@pytest.mark.parametrize("reported,verdict", [("down", "satisfied"), ("standing", "unsatisfied")])
def test_fresh_reported_goal(reported, verdict):
    """A newer contradictory report is a final rejection rather than successful execution."""
    anchor, current, inventory = observations()
    current["records"][0]["value"]["value"]["chassis"]["value"]["posture"] = reported
    result = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    assert result["verdict"] == verdict
    assert result["finality"] == "final"
    assert 0 < result["valid_for_ms"] < 2000
    assert result["physical_effect_verified"] is False


@pytest.mark.parametrize(
    "failure",
    [
        "offline",
        "outer-stale",
        "inner-stale",
        "cached-native",
        "old-sequence",
        "before-anchor",
        "epoch",
        "provider",
        "missing-receipt",
        "clock-rollback",
        "unknown",
    ],
)
def test_unqualified_goal_stays_unknown(failure):
    """Offline, replayed and incomparable reports never release a goal or authorize a retry."""
    anchor, current, inventory = observations()
    record = current["records"][0]
    envelope = record["value"]
    chassis = envelope["value"]["chassis"]
    if failure == "offline":
        inventory["nodes"][0]["liveness"] = "Unreachable"
    elif failure == "outer-stale":
        record["stale"] = True
    elif failure == "inner-stale":
        chassis["age_ms"] = 5000
    elif failure == "cached-native":
        chassis["received_at_ns"] = 1000000000
    elif failure == "old-sequence":
        envelope["sequence"] = 1
    elif failure == "before-anchor":
        envelope["received_at_ms"] = 1001
    elif failure == "epoch":
        envelope["source_epoch"] = "epoch-2"
    elif failure == "provider":
        chassis["registration_id"] = "reg-2"
    elif failure == "missing-receipt":
        chassis.pop("received_at_ns")
    elif failure == "clock-rollback":
        current["observed_at_ms"] = 1000
    else:
        chassis["value"]["posture"] = "unknown"
    result = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    assert result["verdict"] == "unknown"
    assert result["finality"] == "provisional"
    assert result["valid_for_ms"] == 0
    assert result["motion_authorized"] is False


def test_reading_again_spends_original_budget():
    """Repeated inspection cannot renew the native or Controller observation budget."""
    anchor, current, inventory = observations()
    first = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    current["observed_at_ms"] += 100
    second = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    assert second["valid_for_ms"] == first["valid_for_ms"] - 100


@pytest.mark.parametrize("source_ttl", [None, True, 0, -1, float("nan"), float("inf"), 1])
def test_native_expiry_is_not_extended_by_rg(source_ttl):
    """Missing, invalid or expired native budgets cannot inherit RG's five-second ceiling."""
    anchor, current, inventory = observations()
    current["records"][0]["value"]["value"]["chassis"]["value"]["stale_ms"] = source_ttl
    result = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    assert result["verdict"] == "unknown"


def test_shorter_native_budget_limits_assessment():
    """A live native budget shorter than State TTL remains the limiting deadline."""
    anchor, current, inventory = observations()
    current["records"][0]["value"]["value"]["chassis"]["value"]["stale_ms"] = 100
    result = assess_goal(anchor, current, inventory, node_id="fixture", expected_posture="down")
    assert result["verdict"] == "satisfied"
    assert result["valid_for_ms"] == 98
