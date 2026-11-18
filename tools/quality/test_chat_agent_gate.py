"""Accept truthful expiry after actual model latency without weakening fresh-value assertions."""

from __future__ import annotations

import pytest
from check_chat_agent_processes import state_view_kind


@pytest.mark.parametrize("real_model", [False, True])
def test_gate_accepts_current_fixture_values(real_model: bool) -> None:
    """A promptly consumed fixture must show all three current host-rendered facts."""
    assert state_view_kind("82 % fixture-map 站立", 0.1, real_model=real_model) == "current"


def test_gate_accepts_a_real_latency_expiry_refusal() -> None:
    """Inference may outlast the source without making truthful expiry a product failure."""
    assert state_view_kind("电量、位置、姿态：已过期", 12.4, real_model=True) == "expired-refused"


@pytest.mark.parametrize(
    "text,age,real_model",
    [
        ("82 % fixture-map 站立", 12.4, True),
        ("已过期", 0.1, True),
        ("已过期", 12.4, False),
        ("已过期，但当前电量 82 %", 12.4, True),
        ("电量未知", 0.1, True),
    ],
)
def test_gate_refuses_false_current_or_unexplained_state_results(
    text: str, age: float, real_model: bool
) -> None:
    """The real-model exception cannot accept old values, premature refusal or missing facts."""
    with pytest.raises(AssertionError):
        state_view_kind(text, age, real_model=real_model)
