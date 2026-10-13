"""Small atomic filesystem helpers for deployment-owned evidence snapshots."""

from __future__ import annotations

import json
import math
import numbers
import os
import tempfile
from pathlib import Path
from typing import Any

MAX_EVIDENCE_VALUES = 100_000


def json_safe(
    value: Any, *, _depth: int = 0, _max_depth: int = 32, _budget: list[int] | None = None
) -> Any:
    """Convert deployment evidence values to strict JSON-compatible data.

    Habitat and vendor libraries frequently return NumPy scalar values.  The
    standard JSON encoder rejects those values (and silently accepting NaN
    would produce non-standard evidence), so archive writers normalize them at
    the boundary.  Unsupported values still fail explicitly; evidence is
    never converted to an arbitrary string that could look authoritative.
    """
    if _budget is None:
        _budget = [MAX_EVIDENCE_VALUES]
    _budget[0] -= 1
    if _budget[0] < 0:
        raise TypeError("evidence JSON exceeds the bounded value count")
    if _depth > _max_depth:
        raise TypeError("evidence JSON nesting exceeds the bounded limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("non-finite numeric evidence is not archivable")
        return numeric
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("evidence JSON object keys must be strings")
        return {
            key: json_safe(item, _depth=_depth + 1, _max_depth=_max_depth, _budget=_budget)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            json_safe(item, _depth=_depth + 1, _max_depth=_max_depth, _budget=_budget)
            for item in value
        ]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        size = getattr(value, "size", None)
        if isinstance(size, numbers.Integral) and size > _budget[0]:
            raise TypeError("evidence array exceeds the bounded value count")
        return json_safe(tolist(), _depth=_depth + 1, _max_depth=_max_depth, _budget=_budget)
    item = getattr(value, "item", None)
    if callable(item):
        return json_safe(item(), _depth=_depth + 1, _max_depth=_max_depth, _budget=_budget)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def dumps_json_safe(value: Any) -> str:
    """Serialize evidence with strict JSON semantics and deterministic order."""
    return json.dumps(json_safe(value), indent=2, sort_keys=True, allow_nan=False)


def json_observation(value: Any, *, _depth: int = 0, _budget: list[int] | None = None) -> Any:
    """Isolate unavailable raw measurement fields while preserving valid siblings.

    Only observation/metric payloads use this helper. Structural identity and
    contract artifacts keep strict serialization. A tagged unavailable value
    is never a Boolean result, success, fabricated zero or stringified object.
    """
    if _depth > 32:
        return {"status": "unavailable", "reason": "measurement_depth_limit"}
    if _budget is None:
        _budget = [MAX_EVIDENCE_VALUES]
    _budget[0] -= 1
    if _budget[0] < 0 or (isinstance(value, (dict, list, tuple)) and len(value) > _budget[0]):
        return {"status": "unavailable", "reason": "measurement_value_limit"}
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            key: json_observation(item, _depth=_depth + 1, _budget=_budget)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_observation(item, _depth=_depth + 1, _budget=_budget) for item in value]
    try:
        return json_safe(value, _budget=_budget)
    except (TypeError, ValueError, OverflowError):
        return {"status": "unavailable", "reason": "non_json_measurement"}


def write_text_atomic(path: Path, value: str) -> None:
    """Replace one text snapshot only after its complete contents are written.

    The temporary file is created beside the destination so ``os.replace`` is
    atomic on the deployment filesystem. A failed write removes the temporary
    file and re-raises; callers decide whether evidence failure is fail-soft.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            output.write(value)
            output.flush()
        os.replace(temporary_name, path)
        temporary_name = ""
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise
