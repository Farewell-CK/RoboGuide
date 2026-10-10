"""Read-only visual observations retain exact identity and unknown evidence gaps."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.model import (  # noqa: E402
    CanonicalObservationInvocation,
    IntegrationError,
    parse_canonical_invocation,
)
from habitat_local_eaios.perception import (  # noqa: E402
    cached_detection_observations,
    inspect_perception,
    observe_detection,
)


def invocation(expected: str = "detected(object-a)") -> CanonicalObservationInvocation:
    """Parse one exact neutral condition with a real canonical attempt identity."""
    result = parse_canonical_invocation(
        {
            "invocation": {
                "mission_id": "mission",
                "group_id": "group",
                "task_id": "observe",
                "role_id": "observer",
                "attempt_id": "attempt",
                "operation": "observation.verify@v1",
                "objective": "Observe the object",
                "parameters": {"expected": expected},
                "resource_ids": ["slot"],
            }
        }
    )
    assert isinstance(result, CanonicalObservationInvocation)
    return result


def environment() -> Any:
    """Expose existing sensor metadata and entity mapping, with no active sensor API."""
    sensor = type("DetectedObjectsSensor", (), {"pixel_threshold": 10})()
    return SimpleNamespace(
        sim=SimpleNamespace(
            _sensors={"agent_0_head_semantic": object(), "agent_1_semantic": object()},
            habitat_config=SimpleNamespace(object_ids_start=100),
        ),
        task=SimpleNamespace(
            sensor_suite=SimpleNamespace(sensors={"detected_objects": sensor}),
            pddl_problem=SimpleNamespace(
                get_ordered_entities_list=lambda: [SimpleNamespace(name="object-a")],
                sim_info=SimpleNamespace(search_for_entity=lambda entity: 7),
            ),
        ),
    )


@pytest.mark.parametrize("seen", [True, False])
def test_visual_observation_does_not_fabricate_affirmative_completion(seen: bool) -> None:
    """Both positive and negative cached reads are observations, not benchmark success."""
    env = environment()
    assert inspect_perception(env, (0, 1)) == {0: "detected_objects", 1: "detected_objects"}
    request = invocation()
    result = observe_detection(
        request,
        env,
        {"detected_objects": [107] if seen else []},
        0,
        "detected_objects",
        12,
        "sha256:goal",
    )
    assert result["status"] == "observed" and result["condition_holds"] is seen
    assert result["sensor_scope"] == "world"
    assert result["invocation"]["attempt_id"] == "attempt"
    assert "pddl_success" not in result
    assert request.as_dict()["parameters"] == {"expected": "detected(object-a)"}


@pytest.mark.parametrize(
    "observations", [{}, {"detected_objects": [True]}, {"detected_objects": list(range(4097))}]
)
def test_missing_or_invalid_visual_read_remains_unknown(observations: object) -> None:
    """Missing, mistyped and oversized streams cannot imply a detected object."""
    result = observe_detection(
        invocation(), environment(), observations, 0, "detected_objects", 0, "sha256:goal"
    )
    assert result["status"] == "unavailable" and result["condition_holds"] is None


@pytest.mark.parametrize("identity", [7.5, True, -101, "7"])
def test_nonintegral_entity_identity_cannot_be_truncated(identity: object) -> None:
    """An invalid original entity lookup cannot fabricate a matching cached semantic ID."""
    env = environment()
    env.task.pddl_problem.sim_info.search_for_entity = lambda entity: identity
    result = observe_detection(
        invocation(), env, {"detected_objects": [107]}, 0, "detected_objects", 0, "sha256:goal"
    )
    assert result["status"] == "unavailable" and result["condition_holds"] is None


@pytest.mark.parametrize(
    "condition",
    [
        "is_detected(object-a)",
        "detected()",
        "detected(a)(b)",
        "detected(a\nb)",
        "detected(" + "x" * 512 + ")",
    ],
)
def test_observation_profile_rejects_unsupported_conditions(condition: str) -> None:
    """Only explicit bounded entity observations enter the deployed read-only workflow."""
    with pytest.raises(IntegrationError, match="detected"):
        invocation(condition)


def test_perception_readiness_requires_sensor_and_camera() -> None:
    """Deployment readiness inspects actual instances instead of trusting a capability label."""
    env = environment()
    env.sim._sensors.pop("agent_1_semantic")
    with pytest.raises(IntegrationError, match="camera"):
        inspect_perception(env, (0, 1))
    env.task.sensor_suite.sensors.clear()
    with pytest.raises(IntegrationError, match="sensor"):
        inspect_perception(env, (0,))


def test_detection_uses_original_prefilter_cache_without_sensor_calls() -> None:
    """The existing wrapper cache survives the policy filter without a fresh sensor read."""
    raw = {"detected_objects": [107], "rgb": object()}
    wrapper = SimpleNamespace(env=SimpleNamespace(_last_obs=raw))
    cached = cached_detection_observations(wrapper, {"policy": 1}, "detected_objects")
    assert cached is raw
    result = observe_detection(
        invocation(), environment(), cached, 0, "detected_objects", 1, "sha256:goal"
    )
    assert result["condition_holds"] is True
    del wrapper.env._last_obs
    assert cached_detection_observations(wrapper, {}, "detected_objects") == {}


def test_cache_wrapper_cycles_are_bounded() -> None:
    """Unsupported wrappers remain unavailable instead of looping or probing simulator state."""
    wrapper = SimpleNamespace()
    wrapper.env = wrapper
    assert cached_detection_observations(wrapper, {}, "detected_objects") == {}
