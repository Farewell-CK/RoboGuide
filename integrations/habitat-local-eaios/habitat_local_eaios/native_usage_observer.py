"""Isolate the optional native usage-detail callback without editing vendor code.

The original client owns requests, response decoding, history, and total-token
accounting. Only its added ``_record_usage`` instrumentation is replaced in the
explicit adapter/evaluator process. Malformed required usage still raises; an
optional read, JSON, or file failure cannot discard a valid model response.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_io import write_text_atomic

_UTC = timezone.utc  # noqa: UP017 - the independent Habitat environment uses Python 3.9


def _unavailable(directory: Path, failures: int, error: Exception) -> None:
    """Best-effort record bounded capture loss, without error text or credential-bearing inputs."""
    try:
        write_text_atomic(
            directory / "usage-observer-unavailable.json",
            json.dumps(
                {
                    "schema_version": "roboguide.native-usage-observation/v0.1",
                    "status": "unavailable",
                    "failed_records": failures,
                    "last_error_type": type(error).__name__[:64],
                    "failure_boundary": "optional usage detail read/serialization/storage",
                    "model_response_changed": False,
                },
                allow_nan=False,
            ),
        )
    except Exception:  # noqa: BLE001 - capture loss cannot replace a valid model response
        pass


def install_usage_observer(module: Any, directory: Path) -> Callable[[], None]:
    """Replace only the added usage callback, preserving required accounting and restore ownership.

    An uninstrumented original client has no callback and remains untouched.
    Each returned response increments its original total exactly once, outside
    the diagnostic exception boundary. Optional detail records retain the
    existing fields and vendor JSON encoder. The accounting proxy remains the
    independent authority for complete Provider-call evidence.
    """
    model_type: Any = getattr(module, "OpenAIModel", None)
    original = getattr(model_type, "_record_usage", None)
    failures = 0

    def record_usage(model: Any, response: Any, started_at: float) -> None:
        """Count required official usage once, then isolate optional instrumentation failures."""
        nonlocal failures
        model.token_usage += response.usage.total_tokens
        try:
            if not model.enable_logging or not model.usage_details_file:
                return
            usage = response.usage
            record = {
                "agent_name": model.agent_name,
                "model": model.model,
                "request_utc": datetime.fromtimestamp(started_at, _UTC)
                .replace(tzinfo=None)
                .isoformat(),
                "response_utc": datetime.now(_UTC).replace(tzinfo=None).isoformat(),
                "latency_ms": round((time.time() - started_at) * 1000.0, 1),
                "usage": {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                    "prompt_tokens_details": getattr(usage, "prompt_tokens_details", None),
                    "completion_tokens_details": getattr(usage, "completion_tokens_details", None),
                },
            }
            value = json.dumps(record, cls=module.CustomJSONEncoder) + "\n"
            with open(model.usage_details_file, "a", encoding="utf-8") as handle:
                handle.write(value)
        except Exception as error:  # noqa: BLE001 - only added detail collection is fail-soft
            failures += 1
            _unavailable(directory, failures, error)

    if callable(original):
        model_type._record_usage = record_usage

    def restore() -> None:
        """Remove only this process-local callback, leaving later owners' hooks intact."""
        if callable(original) and model_type._record_usage is record_usage:
            model_type._record_usage = original

    return restore
