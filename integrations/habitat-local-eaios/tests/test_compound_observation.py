"""Compound observation contracts preserve terminal semantics without extra world effects."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.endpoint_registry import build_endpoint_registry  # noqa: E402
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalObservationAcquisitionInvocation,
    IntegrationError,
    parse_canonical_invocation,
)
from habitat_local_eaios.observation_conditions import (  # noqa: E402
    build_condition_declaration,
    read_condition_declaration,
)
from habitat_local_eaios.observation_control import ObservationAcquisitionControl  # noqa: E402
from habitat_local_eaios.perception import observe_detection  # noqa: E402
from habitat_local_eaios.stage2_contract import Stage2ExecutionContract  # noqa: E402
from habitat_local_eaios.view_recovery_binding import permits_terminal_arrival  # noqa: E402
from test_perception import environment  # noqa: E402


def compound(expected: str = "detected(object-a) AND at(object-a)", *, verify: bool = False) -> Any:
    """Parse the actual canonical request rather than constructing an unchecked condition."""
    return parse_canonical_invocation(
        {
            "invocation": {
                "mission_id": "mission",
                "task_id": "acquire",
                "group_id": "group",
                "role_id": "observer",
                "attempt_id": "attempt",
                "operation": "observation.verify@v1" if verify else "observation.acquire@v1",
                "objective": "Observe the object while at its location in the terminal state",
                "parameters": {"expected": expected},
                "resource_ids": ["space"],
            }
        }
    )


@pytest.mark.parametrize(
    "detected,position,holds",
    [
        (True, [0.0, 0.0, 0.0], True),
        (True, [0.0, 2.0, 0.0], False),
        (True, [1.0, 0.0, 0.0], True),
        (False, [0.0, 0.0, 0.0], False),
    ],
)
def test_completion_joins_same_step_detection_and_three_dimensional_arrival(
    detected: bool, position: list[float], holds: bool
) -> None:
    """A positive visual read cannot hide lost arrival or substitute X/Z-only success."""
    invocation = compound()
    env = environment()
    env.sim.get_agent_data = lambda _agent: SimpleNamespace(
        articulated_agent=SimpleNamespace(
            base_transformation=SimpleNamespace(translation=position),
        )
    )
    info = env.task.pddl_problem.sim_info
    info.robot_at_thresh = 1.0
    info.get_entity_pos = lambda _entity: [0.0, 0.0, 0.0]
    before = list(position)
    receipt = observe_detection(
        invocation,
        env,
        {"detected_objects": [107] if detected else []},
        0,
        "detected_objects",
        12,
        "sha256:world",
    )
    assert receipt["condition_holds"] is holds and receipt["status"] == "observed"
    assert receipt["detection_condition_holds"] is detected
    assert receipt["simulator_steps"] == 12 and position == before
    assert "pddl_success" not in receipt
    control = ObservationAcquisitionControl(invocation, 0, 10, 16, "sha256:world")
    control.update(receipt)
    assert control.snapshot()["sample"]["condition_holds"] is holds
    assert control.snapshot()["remaining_simulator_steps"] == 14
    contract = Stage2ExecutionContract.for_invocation(invocation)
    assert contract.expected_destination == "object-a"


def test_compound_failure_and_intermediate_departure_do_not_latch_success() -> None:
    """Unchanged budget allows real later recovery, while missing geometry stays unavailable."""
    request = compound()
    env = environment()
    env.task.pddl_problem.sim_info.robot_at_thresh = 1.0
    env.task.pddl_problem.sim_info.get_entity_pos = lambda _entity: [0.0, 0.0, 0.0]
    position = [0.0, 0.0, 0.0]
    env.sim.get_agent_data = lambda _agent: SimpleNamespace(
        articulated_agent=SimpleNamespace(
            base_transformation=SimpleNamespace(translation=position),
        )
    )
    control = ObservationAcquisitionControl(request, 0, 0, 32, "sha256:world")
    for step, x, expected in [(1, 0, True), (2, 2, False), (3, 0, True)]:
        position[0] = x
        receipt = observe_detection(
            request, env, {"detected_objects": [107]}, 0, "detected_objects", step, "sha256:world"
        )
        control.update(receipt)
        assert control.snapshot()["sample"]["condition_holds"] is expected
    env.sim.get_agent_data = lambda _agent: None
    missing = observe_detection(
        request, env, {"detected_objects": [107]}, 0, "detected_objects", 4, "sha256:world"
    )
    assert missing["condition_holds"] is None and missing["status"] == "unavailable"
    assert missing["detection_condition_holds"] is True
    control.update(missing)
    assert control.snapshot()["remaining_simulator_steps"] == 28


@pytest.mark.parametrize(
    "condition,verify",
    [
        ("detected(object-a) AND at(object-b)", False),
        ("detected(object-a) AND at(object-a) AND at(object-b)", False),
        ("detected(object-a) AND at(object-a)", True),
    ],
)
def test_unpublished_or_mismatched_conditions_are_rejected(condition: str, verify: bool) -> None:
    """Read-only verification never gains movement or accepts a different arrival target."""
    with pytest.raises(IntegrationError):
        compound(condition, verify=verify)


def test_legacy_acquisition_does_not_gain_an_implicit_arrival_requirement() -> None:
    """Old requests preserve detection-only semantics without a geometric query."""
    request = compound("detected(object-a)")
    assert isinstance(request, CanonicalObservationAcquisitionInvocation)
    assert not request.requires_arrival
    receipt = observe_detection(
        request,
        environment(),
        {"detected_objects": [107]},
        0,
        "detected_objects",
        0,
        "sha256:world",
    )
    assert receipt["condition_holds"] is True
    assert "arrival_condition_holds" not in receipt


def test_viewpoint_terminal_envelope_retains_height_and_original_stop_margin() -> None:
    """A view search must not select a stance that cannot satisfy its explicit joint effect."""
    assert permits_terminal_arrival((0.0, 0.0, 0.0), (0.0, 0.5, 0.0), 1.0, 0.2)
    assert not permits_terminal_arrival((0.0, 2.0, 0.0), (0.0, 0.5, 0.0), 1.0, 0.2)
    assert not permits_terminal_arrival((0.9, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0.2)
    with pytest.raises(IntegrationError):
        permits_terminal_arrival((0.0, 0.0, 0.0), (0.0, 0.5, 0.0), float("nan"), 0.2)


def test_condition_capability_is_bound_to_exact_configured_endpoints(tmp_path: Path) -> None:
    """Missing, changed or other-agent declarations cannot grant compound support."""
    scenario = Path(__file__).resolve().parents[3] / "scenarios/e1-shared-world-perception"
    sources = []
    for agent, letter in enumerate(("a", "b")):
        path = tmp_path / f"node-{letter}.toml"
        path.write_bytes((scenario / path.name).read_bytes())
        sources.append((agent, path))
    registry = build_endpoint_registry(tuple(sources), tmp_path)
    document = build_condition_declaration(tuple(sources))
    path = tmp_path / "conditions.json"
    path.write_text(json.dumps(document))
    assert all(
        "AND at" in value for value in read_condition_declaration(path, registry).values() if value
    )
    document["agents"][0]["node_config_digest"] = "sha256:" + "f" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(IntegrationError, match="Node sources"):
        read_condition_declaration(path, registry)
