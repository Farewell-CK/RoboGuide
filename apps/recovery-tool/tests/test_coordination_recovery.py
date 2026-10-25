"""Synthetic SQL hazard counterexamples create no deployment or device facts."""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.coordination_recovery import WRAPPER, validate
from roboguide_recovery.snapshot_references import canonical
from test_capacity_recovery import database, wrapper


def evidence() -> dict[str, Any]:
    """Build a labelled original-attempt/gate graph for SQL mutation checks."""
    result = wrapper()
    result["schema"] = WRAPPER
    command = {
        "group_id": "unit-group",
        "task_ref": {"mission_id": "unit-mission", "task_id": "unit-task"},
        "role_id": "worker",
        "node_id": "unit-node",
        "execution_generation": 1,
    }
    registry = {
        "schema_version": "roboguide.decision-policy-registry/v0.3",
        "revision": 1,
        "gates": {
            "unit-hazard": {
                "active_stop_timeout_ms": 100,
                "policy": {"allowed_decisions": ["readiness"]},
            }
        },
    }
    watch = {
        "execution_id": "unit-attempt",
        "command_digest": "sha256:" + hashlib.sha256(canonical(command)).hexdigest(),
        "generation": 1,
        "registry_revision": 1,
        "registry_digest": "sha256:" + hashlib.sha256(canonical(registry)).hexdigest(),
        "gates": ["unit-hazard"],
        "admitted_at": 10,
        "trip": None,
    }
    control = json.loads(result["integration_json"])["control"]
    control.update(decision_policy_registry=registry, decision_policy_history={"1": registry})
    result["integration_json"] = json.dumps(
        {
            "control": control,
            "runtime": {
                "executions": {
                    "unit-attempt": {"command": command, "resource_ids": ["unit-resource"]}
                }
            },
        }
    )
    result["hazards"] = {"watches": {"unit-attempt": watch}}
    result["safety_stops"] = {}
    result["runtime_revisions"] = {}
    return result


def test_original_hazard_references_are_checked_without_mutating_sql(tmp_path: Path) -> None:
    """Reading the original reference graph changes neither its rows nor its immutable hashes."""
    with closing(database(tmp_path / "original.sqlite3", evidence())) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        original = list(sql.iterdump())
        validate(sql)
        assert list(sql.iterdump()) == original


@pytest.mark.parametrize(
    "damage",
    [
        "missing-table",
        "command",
        "generation",
        "policy",
        "gate",
        "trip",
        "old-wrapper",
        "old-generation",
    ],
)
def test_missing_or_changed_hazard_authority_refuses(tmp_path: Path, damage: str) -> None:
    """Refuse missing original tables, altered proofs and hidden legacy authority."""
    result = evidence()
    integration = json.loads(result["integration_json"])
    watch = result["hazards"]["watches"]["unit-attempt"]
    if damage == "missing-table":
        del result["hazards"]
    elif damage == "command":
        integration["runtime"]["executions"]["unit-attempt"]["command"]["node_id"] = "other"
    elif damage == "generation":
        watch["generation"] = 2
    elif damage == "policy":
        integration["control"]["decision_policy_history"] = {}
    elif damage == "gate":
        watch["gates"].append("absent-gate")
    elif damage == "trip":
        watch["trip"] = {
            "observed_at": 11,
            "failed": ["unit-hazard"],
            "receipt_digest": "sha256:" + "a" * 64,
        }
    else:
        result["schema"] = (
            "roboguide.controller-checkpoint/v58"
            if damage == "old-wrapper"
            else "roboguide.controller-checkpoint/v55"
        )
    result["integration_json"] = json.dumps(integration)
    with closing(database(tmp_path / (damage + ".sqlite3"), result)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (result["schema"],))
        sql.commit()
        before = list(sql.iterdump())
        with pytest.raises(ValueError):
            validate(sql)
        assert list(sql.iterdump()) == before


def test_legacy_empty_hazard_absence_creates_no_authority(tmp_path: Path) -> None:
    """A genuine earlier empty graph remains readable without adding a watch or a clock."""
    value = copy.deepcopy(evidence())
    value["schema"] = "roboguide.controller-checkpoint/v55"
    value.pop("hazards")
    integration = json.loads(value["integration_json"])
    integration["control"].update(decision_policy_registry=None, decision_policy_history={})
    value["integration_json"] = json.dumps(integration)
    with closing(database(tmp_path / "legacy.sqlite3", value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (value["schema"],))
        sql.commit()
        before = list(sql.iterdump())
        validate(sql)
        assert list(sql.iterdump()) == before
