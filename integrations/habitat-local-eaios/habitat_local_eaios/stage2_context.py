"""Bound one controlled Stage2 Provider request without editing its raw model history.

System instructions, the initial local planning exchange and the current observed
input are mandatory. Recent history retains whole exchanges, including complete
tool-call/receipt pairs. The vendor still owns response parsing and archival of
the full original history; native EMOS models never install this request scope.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from .model import IntegrationError

_LOG = logging.getLogger(__name__)
_MISSING = object()
MAX_RECENT_EXCHANGES = 8
MAX_CONTEXT_BYTES = 131_072
MAX_HISTORY_EXCHANGES = 16_384


def context_declaration() -> dict[str, Any]:
    """Publish the exact working-context limits separately from full evidence retention."""
    return {
        "profile": "bounded-stage2-working-context/v0.1",
        "scope": "controlled-live-attempt-only",
        "mandatory": ["system", "initial-planning-exchange", "current-observed-input"],
        "max_recent_complete_exchanges": MAX_RECENT_EXCHANGES,
        "max_serialized_message_bytes": MAX_CONTEXT_BYTES,
        "max_original_exchanges_per_attempt": MAX_HISTORY_EXCHANGES,
        "history_policy": "whole-exchanges-newest-contiguous-tail",
        "oversized_mandatory_input": "local-contract-failure-before-provider",
        "original_history": "unchanged-and-fully-archived",
        "tools_model_and_parameters": "unchanged",
        "model_response_rewritten": False,
    }


def _message_bytes(message: Any) -> int:
    """Measure only selected messages using JSON-compatible SDK data, never string fallbacks."""
    value = message
    if not isinstance(value, Mapping):
        dump = getattr(value, "model_dump", None)
        if not callable(dump):
            raise IntegrationError("Stage2 context contains an unsupported message")
        value = dump(mode="json", exclude_none=True)
    try:
        return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as error:
        raise IntegrationError("Stage2 context message cannot be serialized") from error


def _complete_exchange(exchange: list[Any]) -> None:
    """Refuse orphan or duplicate tool receipts instead of forwarding a split conversation."""
    pending: set[str] = set()
    for message in exchange:
        role = (
            message.get("role") if isinstance(message, Mapping) else getattr(message, "role", None)
        )
        if role == "assistant":
            if pending:
                raise IntegrationError("Stage2 context exchange contains unanswered tools")
            calls = (
                message.get("tool_calls")
                if isinstance(message, Mapping)
                else getattr(message, "tool_calls", None)
            )
            if calls is not None and (not isinstance(calls, list) or len(calls) > 32):
                raise IntegrationError("Stage2 context tool-call collection is invalid")
            for call in calls or []:
                identity = (
                    call.get("id") if isinstance(call, Mapping) else getattr(call, "id", None)
                )
                if (
                    not isinstance(identity, str)
                    or not 1 <= len(identity) <= 1024
                    or identity in pending
                ):
                    raise IntegrationError("Stage2 context tool identity is invalid")
                pending.add(identity)
        elif role == "tool":
            identity = message.get("tool_call_id") if isinstance(message, Mapping) else None
            if not isinstance(identity, str) or identity not in pending:
                raise IntegrationError("Stage2 context exchange contains an orphan tool receipt")
            pending.remove(identity)
        elif role != "user" or pending:
            raise IntegrationError("Stage2 context exchange has unsupported message ordering")
    if pending:
        raise IntegrationError("Stage2 context exchange contains unanswered tools")


def bounded_messages(model: Any, messages: Any) -> tuple[list[Any], dict[str, Any]]:
    """Keep mandatory input and a bounded complete tail from the actual vendor request."""
    history = getattr(model, "chat_history", None)
    if (
        not isinstance(messages, list)
        or not messages
        or not isinstance(history, list)
        or not 1 <= len(history) <= MAX_HISTORY_EXCHANGES
        or any(
            not isinstance(exchange, list) or not 1 <= len(exchange) <= 32 for exchange in history
        )
        or len(history[-1]) != 1
        or not isinstance(history[-1][0], dict)
        or history[-1][0].get("role") != "user"
        or messages[0] is not getattr(model, "system_message", None)
        or getattr(model, "window_size", None) is not None
    ):
        raise IntegrationError("Stage2 working context needs the exact original request/history")
    expected = [model.system_message, *(item for exchange in history for item in exchange)]
    if len(messages) != len(expected) or any(
        messages[index] is not original for index, original in enumerate(expected)
    ):
        raise IntegrationError("Stage2 working context cannot relabel changed or detached input")
    initial = history[0] if len(history) > 1 else []
    _complete_exchange(initial)
    mandatory = [messages[0], *initial, *history[-1]]
    # Two array brackets and conservative comma/space overhead form the actual JSON bound.
    byte_count = 2 + sum(_message_bytes(message) + 2 for message in mandatory)
    if byte_count > MAX_CONTEXT_BYTES:
        raise IntegrationError("mandatory Stage2 input exceeds the declared context budget")
    retained: list[list[Any]] = []
    for exchange in reversed(history[1:-1][-MAX_RECENT_EXCHANGES:]):
        _complete_exchange(exchange)
        extra = sum(_message_bytes(message) + 2 for message in exchange)
        if byte_count + extra > MAX_CONTEXT_BYTES:
            break
        retained.append(exchange)
        byte_count += extra
    selected = [messages[0], *initial]
    selected.extend(message for exchange in reversed(retained) for message in exchange)
    selected.extend(history[-1])
    return selected, {
        "profile": "bounded-stage2-working-context/v0.1",
        "original_exchanges": len(history),
        "original_messages": len(messages),
        "retained_recent_exchanges": len(retained),
        "omitted_complete_exchanges": max(0, len(history) - 2 - len(retained)),
        "mandatory_initial_exchange_retained": bool(initial),
        "selected_messages": len(selected),
        "serialized_message_bytes_upper_bound": byte_count,
        "max_serialized_message_bytes": MAX_CONTEXT_BYTES,
        "raw_history_changed": False,
    }


@contextmanager
def model_request_scope(model: Any, record: Callable[[dict[str, Any]], None]) -> Iterator[None]:
    """Scope the original SDK method to one synchronous request and always remove the hook."""
    try:
        completions = model.client.chat.completions
        original = completions.create
        previous = vars(completions).get("create", _MISSING)
    except AttributeError as error:
        raise IntegrationError(
            "bounded Stage2 context requires the original SDK boundary"
        ) from error
    if not callable(original) or getattr(original, "_roboguide_context_scope", False):
        raise IntegrationError("Stage2 request context boundary is unavailable or already owned")
    issued = False

    def create(*args: Any, **kwargs: Any) -> Any:
        """Change only request messages; preserve endpoint, model, tools and original errors."""
        nonlocal issued
        if issued or args or "messages" not in kwargs:
            raise IntegrationError("Stage2 request context requires explicit original messages")
        selected, observation = bounded_messages(model, kwargs["messages"])
        try:
            record(dict(observation, event="model-working-context"))
        except Exception:  # noqa: BLE001 - archival failure does not replace the actual request
            _LOG.exception("Stage2 working context evidence unavailable")
        issued = True
        return original(**dict(kwargs, messages=selected))

    create._roboguide_context_scope = True  # type: ignore[attr-defined]
    completions.create = create
    try:
        yield
    finally:
        if previous is _MISSING:
            vars(completions).pop("create", None)
        else:
            completions.create = previous
