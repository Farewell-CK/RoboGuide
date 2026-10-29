"""Bound real-shaped model requests while keeping raw evidence and task authority intact."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_ROOT = Path(__file__).parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.stage2_context import (  # noqa: E402
    MAX_CONTEXT_BYTES,
    MAX_RECENT_EXCHANGES,
    bounded_messages,
    model_request_scope,
)


class Client:
    """Count SDK requests independently from original vendor history assembly."""

    def __init__(self) -> None:
        """Record exact supplied arguments without networking or response rewriting."""
        self.requests: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def create(self, **kwargs: Any) -> str:
        """Keep a unique sentinel response and the original infrastructure error."""
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return "original-provider-response"


def model(exchanges: int = 300) -> Any:
    """Keep the initial generated local plan, complete tools and one pending user input."""
    history: list[list[dict[str, Any]]] = [
        [
            {"role": "user", "content": "Plan the bound observation task."},
            {"role": "assistant", "content": "Search for the assigned parcel."},
        ]
    ]
    for index in range(exchanges):
        identity = f"call-{index}"
        history.append(
            [
                {"role": "user", "content": "Current observed sample: false."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {"name": "wait", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": identity,
                    "name": "wait",
                    "content": json.dumps({"condition_holds": False, "step": index}),
                },
            ]
        )
    history.append([{"role": "user", "content": "Latest real sample and remaining budget."}])
    return SimpleNamespace(
        system_message={"role": "system", "content": "Observe parcel A; preserve both goals."},
        chat_history=history,
        window_size=None,
        client=SimpleNamespace(chat=SimpleNamespace(completions=Client())),
    )


def messages(value: Any) -> list[Any]:
    """Mirror the original client's exact full-history request assembly."""
    return [
        value.system_message,
        *(message for exchange in value.chat_history for message in exchange),
    ]


def test_bounded_complete_tail_preserves_full_evidence() -> None:
    """Hundreds of exchanges cannot evict the task or split a raw tool/receipt pair."""
    value = model()
    original = deepcopy(value.chat_history)
    selected, evidence = bounded_messages(value, messages(value))
    assert selected[0] is value.system_message
    assert selected[1:3] == value.chat_history[0]
    assert selected[-1] is value.chat_history[-1][0]
    assert len(selected) == 1 + 2 + 3 * MAX_RECENT_EXCHANGES + 1
    assert evidence["omitted_complete_exchanges"] == 300 - MAX_RECENT_EXCHANGES
    assert evidence["serialized_message_bytes_upper_bound"] <= MAX_CONTEXT_BYTES
    assert value.chat_history == original and len(value.chat_history) == 302
    calls = {call["id"] for message in selected for call in message.get("tool_calls", [])}
    assert calls == {
        message["tool_call_id"] for message in selected if message.get("role") == "tool"
    }


def test_request_parameters_response_and_raw_history_are_unchanged() -> None:
    """Only Provider-visible context changes; tools, model and original parsing stay original."""
    value = model()
    completions = value.client.chat.completions
    records: list[dict[str, Any]] = []
    before = deepcopy(value.chat_history)
    tools = [{"type": "function", "function": {"name": "wait"}}]
    original_create = completions.create
    with model_request_scope(value, records.append):
        result = completions.create(
            messages=messages(value), model="test-model", tools=tools, tool_choice="required"
        )
    assert result == "original-provider-response"
    assert completions.create == original_create
    assert completions.requests[0]["model"] == "test-model"
    assert completions.requests[0]["tools"] is tools
    assert completions.requests[0]["tool_choice"] == "required"
    assert value.chat_history == before
    assert records[0]["event"] == "model-working-context"


@pytest.mark.parametrize("location", ["system", "initial", "current"])
def test_mandatory_input_is_never_silently_truncated(location: str) -> None:
    """Oversized goals or observations refuse before a request rather than lose semantics."""
    value = model(1)
    target = (
        value.system_message
        if location == "system"
        else value.chat_history[0][0]
        if location == "initial"
        else value.chat_history[-1][0]
    )
    target["content"] = "x" * MAX_CONTEXT_BYTES
    with (
        model_request_scope(value, lambda _: None),
        pytest.raises(IntegrationError, match="budget"),
    ):
        value.client.chat.completions.create(messages=messages(value), model="test-model")
    assert value.client.chat.completions.requests == []


def test_byte_budget_keeps_only_a_contiguous_tail() -> None:
    """Large older exchanges cannot displace the newest complete context or exceed bytes."""
    value = model(10)
    for exchange in value.chat_history[1:-1]:
        exchange[0]["content"] = "x" * 40_000
    selected, evidence = bounded_messages(value, messages(value))
    assert 0 < evidence["retained_recent_exchanges"] < MAX_RECENT_EXCHANGES
    assert evidence["serialized_message_bytes_upper_bound"] <= MAX_CONTEXT_BYTES
    ids = [message["tool_call_id"] for message in selected if message.get("role") == "tool"]
    assert ids[-1] == "call-9" and ids[0] == f"call-{10 - len(ids)}"


@pytest.mark.parametrize("change", ["orphan", "missing", "duplicate"])
def test_incomplete_tool_exchanges_refuse(change: str) -> None:
    """An invalid historical tool chain cannot become a superficially valid request."""
    value = model(1)
    if change == "orphan":
        value.chat_history[1][-1]["tool_call_id"] = "another-call"
    elif change == "missing":
        value.chat_history[1].pop()
    else:
        value.chat_history[1].append(deepcopy(value.chat_history[1][-1]))
    with pytest.raises(IntegrationError, match="tool"):
        bounded_messages(value, messages(value))


def test_detached_or_already_windowed_request_refuses() -> None:
    """The boundary cannot bless an altered request as the exact original model input."""
    value = model(1)
    with pytest.raises(IntegrationError):
        bounded_messages(value, deepcopy(messages(value)))
    value.window_size = 2
    with pytest.raises(IntegrationError, match="original request"):
        bounded_messages(value, messages(value))


def test_original_provider_error_and_cleanup_are_retained() -> None:
    """No retry, extra call or leaked SDK override follows an infrastructure exception."""
    value = model(1)
    client = value.client.chat.completions
    error = TimeoutError("original provider timeout")
    client.error = error
    original = client.create
    with pytest.raises(TimeoutError) as raised, model_request_scope(value, lambda _: None):
        client.create(messages=messages(value), model="test-model")
    assert raised.value is error and len(client.requests) == 1
    assert client.create == original


def test_recording_failure_is_not_a_provider_retry() -> None:
    """A failed evidence sink cannot change the original request's result or multiplicity."""
    value = model(1)

    def unavailable(_: dict[str, Any]) -> None:
        """Represent an unavailable evidence volume."""
        raise OSError("evidence unavailable")

    with model_request_scope(value, unavailable):
        assert value.client.chat.completions.create(messages=messages(value)) == (
            "original-provider-response"
        )
    assert len(value.client.chat.completions.requests) == 1
