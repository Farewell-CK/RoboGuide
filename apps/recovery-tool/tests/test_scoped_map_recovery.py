"""Independent actual SQLite validation of synthetic scoped histories, never deployment evidence."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.scoped_map_recovery import WRAPPER, _projection, validate, validate_catalog
from test_capacity_recovery import database, wrapper


def lineage() -> dict[str, Any]:
    """Build legacy and negotiated transitive children under one original typed root."""
    typed = {
        "schema": "roboguide.map-manifest/v0.2",
        "map_id": "unit-map",
        "revision_id": "root",
        "producer_node_id": "unit-producer",
        "producer_local_system_id": "unit-files",
        "producer_provider_id": "unit-provider",
        "source_mission_id": "unit-production",
        "created_at_ms": 1,
        "media_type": "unit/map",
        "payload_schema": "unit.map/v1",
        "content_digest": "sha256:" + "a" * 64,
        "byte_size": 4,
        "root_frame": "unit-frame",
        "spatial_anchor": {"kind": "fixed", "id": "unit-anchor"},
    }
    root = _projection(typed)
    first = root | {
        "selector": {"memory_id": "unit-map", "revision_id": "first"},
        "scope": {"kind": "execution_group", "execution_group_id": "unit-first"},
        "created_at": 2,
    }
    second = first | {
        "selector": {"memory_id": "unit-map", "revision_id": "second"},
        "scope": {"kind": "execution_group", "execution_group_id": "unit-second"},
        "created_at": 3,
    }
    value = wrapper()
    value.update(
        schema=WRAPPER,
        memory_handoffs={
            "first": {
                "request": {
                    "schema_version": "roboguide.memory-handoff/v0.2",
                    "child": first,
                    "typed_map": typed,
                },
                "parent": root,
                "published_at_ms": 2,
            },
            "second": {
                "request": {
                    "schema_version": "roboguide.memory-handoff/v0.3",
                    "child": second,
                    "typed_map": typed,
                    "source_group_id": "unit-first",
                    "source_execution_ids": ["unit-source"],
                },
                "parent": first,
                "published_at_ms": 3,
            },
        },
        runtime_revisions={},
        memory_preparation={
            "records": {
                "source": {
                    "preparation": {
                        "schema": "roboguide.memory-preparation/v0.2",
                        "group_id": "unit-first",
                        "execution_id": "unit-source",
                        "inputs": [{"manifest": first, "typed_map": None}],
                    }
                },
                "target": {
                    "preparation": {
                        "schema": "roboguide.memory-preparation/v0.3",
                        "group_id": "unit-second",
                        "execution_id": "unit-target",
                        "inputs": [
                            {
                                "manifest": second,
                                "typed_map": typed,
                                "consumer": {
                                    "preparation_contract": (
                                        "roboguide.memory-provider-availability/v0.2"
                                    )
                                },
                                "requirement": {"source_contract": "scoped_typed_map"},
                            }
                        ],
                    }
                },
            }
        },
    )
    return dict(json.loads(json.dumps(value)))


def test_transitive_scoped_recovery_preserves_legacy_meaning_without_writes(tmp_path: Path) -> None:
    """A retained generic legacy preparation can prove source metadata without gaining typed use."""
    value = lineage()
    with closing(database(tmp_path / "history.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        before = list(sql.iterdump())
        validate(sql)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize("present", [False, True])
def test_omitted_empty_revision_history_matches_actual_serde(tmp_path: Path, present: bool) -> None:
    """Ordinary non-revision workloads can recover without inventing an empty-history field."""
    value = lineage()
    if not present:
        del value["runtime_revisions"]
    typed = value["memory_handoffs"]["first"]["request"]["typed_map"]
    publications = {(typed["map_id"], typed["revision_id"]): ("MapArtifactPublished", typed)}
    with closing(database(tmp_path / "ordinary.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        before = list(sql.iterdump())
        validate(sql)
        validate_catalog(sql, publications)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize("invalid", [None, [], "unavailable"])
def test_present_invalid_revision_history_is_not_treated_as_empty(
    tmp_path: Path, invalid: Any
) -> None:
    """A present corrupt authority table refuses instead of gaining omitted-empty semantics."""
    value = lineage()
    value["runtime_revisions"] = invalid
    with closing(database(tmp_path / "invalid.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        with pytest.raises(ValueError, match="revision history"):
            validate(sql)
        with pytest.raises(ValueError, match="revision history"):
            validate_catalog(sql, {})


@pytest.mark.parametrize(
    "damage",
    [
        "missing-parent",
        "parent-metadata",
        "root-frame",
        "missing-source",
        "old-context",
        "owner",
        "provider",
        "cycle",
    ],
)
def test_corrupt_scoped_ancestry_refuses_before_bundle_publication(
    tmp_path: Path, damage: str
) -> None:
    """Malformed original joins cannot become current map evidence through a valid bundle hash."""
    value = copy.deepcopy(lineage())
    second = value["memory_handoffs"]["second"]
    target = value["memory_preparation"]["records"]["target"]["preparation"]
    if damage == "missing-parent":
        del value["memory_handoffs"]["first"]
    elif damage == "parent-metadata":
        second["parent"]["created_at"] = 1
    elif damage == "root-frame":
        second["request"]["typed_map"]["root_frame"] = "substituted-frame"
    elif damage == "missing-source":
        del value["memory_preparation"]["records"]["source"]
    elif damage == "old-context":
        target["schema"] = "roboguide.memory-preparation/v0.2"
    elif damage == "owner":
        target["inputs"][0]["manifest"]["owner"]["node_id"] = "another-owner"
    elif damage == "cycle":
        second["request"]["child"]["created_at"] = 2
        target["inputs"][0]["manifest"]["created_at"] = 2
        first = value["memory_handoffs"]["first"]
        first["parent"] = copy.deepcopy(second["request"]["child"])
        first["request"].update(
            schema_version="roboguide.memory-handoff/v0.3",
            source_group_id="unit-second",
            source_execution_ids=["unit-target"],
        )
    else:
        target["inputs"][0]["consumer"]["preparation_contract"] = (
            "roboguide.memory-provider-availability/v0.1"
        )
    with closing(database(tmp_path / "history.sqlite3", value)) as sql:
        sql.execute(
            "UPDATE controller_checkpoint SET schema=?,checkpoint_json=?",
            (WRAPPER, json.dumps(value)),
        )
        sql.commit()
        with pytest.raises(ValueError):
            validate(sql)
