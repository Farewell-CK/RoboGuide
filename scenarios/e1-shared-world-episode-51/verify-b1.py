#!/usr/bin/env python3
"""Verify Formal B1 protocol admission independently of system/benchmark success."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evaluation" / "src"))

from roboguide_eval.b1_run import write_b1_verdict  # noqa: E402


def verify(run: Path) -> dict[str, Any]:
    """Persist the single canonical admission result consumed by the Harness."""
    return write_b1_verdict(run)


def main() -> None:
    """Print protocol admission without running any provider or benchmark."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-b1.py RUN_DIRECTORY")
    print(json.dumps(verify(Path(sys.argv[1])), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
