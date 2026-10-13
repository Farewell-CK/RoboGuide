"""Deterministic tests for strict deployment evidence serialization."""

from __future__ import annotations

import json
import numbers
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from habitat_local_eaios.evidence_io import (  # noqa: E402
    dumps_json_safe,
    json_observation,
    json_safe,
    write_text_atomic,
)


class Scalar:
    """Represent an external scalar without depending on the simulator's NumPy install."""

    def item(self) -> float:
        """Return the original finite scalar value."""
        return 1.25


def test_json_safe_normalizes_vendor_scalars_and_nested_sequences() -> None:
    """Nested vendor values retain their types and finite fractional values."""
    document = {"metrics": [Scalar(), (1, 2)], "ok": True}
    assert json.loads(dumps_json_safe(document)) == {
        "metrics": [1.25, [1, 2]],
        "ok": True,
    }


def test_json_safe_rejects_non_finite_values() -> None:
    """Nonstandard numeric values never enter authoritative JSON as NaN/Infinity."""
    with pytest.raises(ValueError, match="non-finite"):
        json_safe(float("nan"))


def test_unavailable_measurement_does_not_erase_official_boolean_sibling() -> None:
    """Invalid auxiliary measurements stay explicit without replacing valid benchmark facts."""
    result = json_observation({"pddl_success": False, "distance": float("nan"), "ratio": Scalar()})
    assert result["pddl_success"] is False and result["ratio"] == 1.25
    assert result["distance"]["status"] == "unavailable"
    assert json.loads(dumps_json_safe(result)) == result


def test_json_safe_rejects_unsupported_values_instead_of_stringifying() -> None:
    """Opaque objects cannot masquerade as observed JSON strings."""
    with pytest.raises(TypeError, match="unsupported evidence"):
        json_safe(object())


def test_vendor_real_preserves_fractional_value() -> None:
    """A NumPy-shaped Real is converted to float rather than truncating to int."""

    class VendorReal:
        """Expose Real conversion methods with a nonintegral value."""

        def __float__(self) -> float:
            """Read the actual fractional metric."""
            return 1.75

        def __int__(self) -> int:
            """Expose the truncation that evidence must never accidentally use."""
            return 1

    numbers.Real.register(VendorReal)
    assert json_safe(VendorReal()) == 1.75


def test_array_uses_tolist_without_calling_scalar_item() -> None:
    """An array's scalar item method is not a valid multi-element conversion."""

    class Array:
        """Represent the existing array-to-JSON boundary without a NumPy dependency."""

        def item(self) -> None:
            """Fail if a multi-element array is mistaken for a scalar."""
            raise ValueError("not a scalar")

        def tolist(self) -> list[float]:
            """Return the retained finite entries without stringification."""
            return [1.25, 2.5]

    assert json_safe(Array()) == [1.25, 2.5]


def test_json_keys_and_depth_fail_explicitly() -> None:
    """Key coercion cannot merge identities, and recursive objects remain bounded."""
    with pytest.raises(TypeError, match="keys"):
        json_safe({1: "first", "1": "second"})
    recursive: list[object] = []
    recursive.append(recursive)
    with pytest.raises(TypeError, match="nesting"):
        json_safe(recursive)


def test_array_value_budget_is_checked_before_materializing() -> None:
    """An oversized vendor array cannot allocate an unbounded Python copy."""

    class OversizedArray:
        """Expose a declared size while forbidding any array materialization."""

        size = 100_001

        def tolist(self) -> list[int]:
            """Fail if the pre-conversion budget check is bypassed."""
            raise AssertionError("array must not be copied")

    with pytest.raises(TypeError, match="value count"):
        json_safe(OversizedArray())
    assert json_observation({"pddl_success": False, "array": OversizedArray()}) == {
        "pddl_success": False,
        "array": {"status": "unavailable", "reason": "non_json_measurement"},
    }


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
