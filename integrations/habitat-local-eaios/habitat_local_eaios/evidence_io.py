"""Small atomic filesystem helpers for deployment-owned evidence snapshots."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
