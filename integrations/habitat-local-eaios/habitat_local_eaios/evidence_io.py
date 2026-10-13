"""Small atomic filesystem helpers for deployment-owned evidence snapshots."""

from __future__ import annotations

import os
import json
import math
import numbers
import tempfile
from pathlib import Path
from typing import Any


def json_safe(value: Any, *, _depth: int = 0, _max_depth: int = 32) -> Any:
    """Convert deployment evidence values to strict JSON-compatible data.

    Habitat and vendor libraries frequently return NumPy scalar values.  The
    standard JSON encoder rejects those values (and silently accepting NaN
    would produce non-standard evidence), so archive writers normalize them at
    the boundary.  Unsupported values still fail explicitly; evidence is
    never converted to an arbitrary string that could look authoritative.
    """
    if _depth > _max_depth:
        raise TypeError("evidence JSON nesting exceeds the bounded limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, numbers.Real):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("non-finite numeric evidence is not archivable")
        return numeric if isinstance(value, float) else int(value)
    if isinstance(value, dict):
        return {
            str(key): json_safe(item, _depth=_depth + 1, _max_depth=_max_depth)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            json_safe(item, _depth=_depth + 1, _max_depth=_max_depth)
            for item in value
        ]
    item = getattr(value, "item", None)
    if callable(item):
        return json_safe(item(), _depth=_depth + 1, _max_depth=_max_depth)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return json_safe(tolist(), _depth=_depth + 1, _max_depth=_max_depth)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def dumps_json_safe(value: Any) -> str:
    """Serialize evidence with strict JSON semantics and deterministic order."""
    return json.dumps(json_safe(value), indent=2, sort_keys=True, allow_nan=False)


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
