"""Deterministic tests for strict deployment evidence serialization."""

from __future__ import annotations

import json

import pytest

from habitat_local_eaios.evidence_io import dumps_json_safe, json_safe


class Scalar:
    def item(self) -> float:
        return 1.25


def test_json_safe_normalizes_vendor_scalars_and_nested_sequences() -> None:
    document = {"metrics": [Scalar(), (1, 2)], "ok": True}
    assert json.loads(dumps_json_safe(document)) == {
        "metrics": [1.25, [1, 2]],
        "ok": True,
    }


def test_json_safe_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        json_safe(float("nan"))


def test_json_safe_rejects_unsupported_values_instead_of_stringifying() -> None:
    with pytest.raises(TypeError, match="unsupported evidence"):
        json_safe(object())
