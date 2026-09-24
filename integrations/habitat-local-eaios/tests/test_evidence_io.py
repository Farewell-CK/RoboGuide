"""Deterministic tests for complete publication of local evidence snapshots."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.evidence_io import write_text_atomic  # noqa: E402


def test_atomic_snapshot_keeps_previous_contents_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed final replace leaves an existing complete artifact readable."""
    destination = tmp_path / "evidence.json"
    destination.write_text('{"state":"old"}\n', encoding="utf-8")

    def fail_replace(source: str, target: Path) -> None:
        """Inject failure after the new payload has been fully staged."""
        assert target == destination
        assert Path(source).read_text(encoding="utf-8") == '{"state":"new"}\n'
        assert destination.read_text(encoding="utf-8") == '{"state":"old"}\n'
        raise OSError("replace failure sentinel")

    monkeypatch.setattr("habitat_local_eaios.evidence_io.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failure sentinel"):
        write_text_atomic(destination, '{"state":"new"}\n')
    assert destination.read_text(encoding="utf-8") == '{"state":"old"}\n'
    assert sorted(path.name for path in tmp_path.iterdir()) == ["evidence.json"]
