"""Actual SQLite contract counterexamples; synthetic rows establish no deployment authority."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.capacity_recovery import CHAIN, WRAPPER, validate, validate_standalone
from roboguide_recovery.replica_content import validate_pairing
from roboguide_recovery.snapshot_references import canonical


def wrapper() -> dict[str, Any]:
    """Construct one exact synthetic Task allocation with independent quota and Group links."""
    task = {"mission_id": "unit-mission", "task_id": "unit-task"}
    grant = {
        "schema": "roboguide.resource-allocation-grant/v0.1",
        "generation": 1,
        "resource_id": "unit-resource",
        "resource_class": "unit.compute/v1",
        "node_id": "unit-node",
        "group_id": "unit-group",
        "owner": {"Task": task},
        "tenant_id": "unit-tenant",
        "task_ref": task,
        "role_id": "worker",
        "units": 2,
        "allocation_units": 2,
        "admitted_capacity": 4,
    }
    identity = (
        "allocation:sha256:"
        + hashlib.sha256(
            canonical(
                [
                    grant[key]
                    for key in (
                        "schema",
                        "generation",
                        "resource_id",
                        "resource_class",
                        "node_id",
                        "group_id",
                        "owner",
                        "role_id",
                        "tenant_id",
                        "allocation_units",
                        "admitted_capacity",
                    )
                ]
            )
        ).hexdigest()
    )
    grant["allocation_id"] = identity
    assignment = {"role_id": "worker", "node_id": "unit-node", "resource_ids": ["unit-resource"]}
    control = {
        "capacity_ledger": {
            "generation": 1,
            "allocations": {
                identity: {
                    "original": grant,
                    "reservation": {
                        "task_ref": task,
                        "role_id": "worker",
                        "group_id": "unit-group",
                        "scope": "Task",
                        "owner": grant["owner"],
                    },
                    "borrowers": [{"task_ref": task, "role_id": "worker", "units": 2}],
                }
            },
        },
        "reservations": {},
        "mission_tenants": {"unit-mission": "unit-tenant"},
        "quota_ledger": {
            "claims": [
                {
                    "claim_id": identity,
                    "allocation_id": identity,
                    "tenant_id": "unit-tenant",
                    "resource_id": "unit-resource",
                    "kind": "Compute",
                    "units": 2,
                }
            ]
        },
        "groups": [
            {
                "group_id": "unit-group",
                "mission_id": "unit-mission",
                "lifecycle": "Bound",
                "role_requirements": [
                    [
                        task,
                        "worker",
                        {
                            "resources": [
                                {
                                    "kind": "Compute",
                                    "units": 2,
                                    "allocation_mode": "divisible",
                                    "resource_class": "unit.compute/v1",
                                }
                            ]
                        },
                    ]
                ],
                "task_executions": [
                    [
                        task,
                        {
                            "context_id": "unit-context",
                            "context_roles": [],
                            "role_scopes": [["worker", "Task"]],
                            "assignments": [assignment],
                        },
                    ]
                ],
            }
        ],
        "pending_recovery_commitments": [],
    }
    return {
        "schema": WRAPPER,
        "replication_profile": None,
        "integration_json": json.dumps({"control": control}),
    }


def database(path: Path, value: dict[str, Any]) -> sqlite3.Connection:
    """Create actual SQL fixture storage; no Controller, Node or authority writer starts."""
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE controller_checkpoint("
        "singleton INTEGER,schema TEXT,event_sequence INTEGER,checkpoint_json TEXT);"
        "CREATE TABLE controller_checkpoint_head("
        "singleton INTEGER,base_revision INTEGER,revision INTEGER,event_sequence INTEGER,"
        "schema TEXT,base_digest TEXT,digest TEXT);"
        "CREATE TABLE controller_checkpoint_deltas("
        "revision INTEGER,previous_revision INTEGER,event_sequence INTEGER,"
        "schema TEXT,base_digest TEXT,digest TEXT,delta_json TEXT);"
    )
    connection.execute(
        "INSERT INTO controller_checkpoint VALUES (1,?,0,?)", (WRAPPER, json.dumps(value))
    )
    connection.commit()
    return connection


def frame_digest(content: bytes, revision: int) -> str:
    """Encode the published frame independently of the recovery reader."""
    schema = WRAPPER.encode()
    frame = CHAIN.encode() + len(schema).to_bytes(8, "little") + schema
    frame += (0).to_bytes(8, "little") + revision.to_bytes(8, "little")
    frame += len(content).to_bytes(8, "little") + content
    return hashlib.sha256(frame).hexdigest()


def test_original_parent_and_quota_validate_without_changing_sql(tmp_path: Path) -> None:
    """Exact retained quantity and ownership survive independent readonly validation."""
    with closing(database(tmp_path / "source.sqlite3", wrapper())) as connection:
        before = connection.total_changes
        validate(connection)
        assert connection.total_changes == before


def test_explicit_standalone_never_omits_an_original_replica(tmp_path: Path) -> None:
    """Only the new explicit profile accepts no parents; actual Replica authority still refuses."""
    components = [{"component_id": "controller", "kind": "controller"}]
    with pytest.raises(ValueError, match="paired Root"):
        validate_pairing(components)
    validate_pairing(components, allow_standalone=True)
    with pytest.raises(ValueError, match="paired Root"):
        validate_pairing(
            components + [{"component_id": "parent", "kind": "replica"}], allow_standalone=True
        )
    with closing(database(tmp_path / "source.sqlite3", wrapper())) as connection:
        validate_standalone(connection)
        value = wrapper()
        value["replication_profile"] = "original-replica-profile"
        connection.execute(
            "UPDATE controller_checkpoint SET checkpoint_json=?", (json.dumps(value),)
        )
        connection.commit()
        with pytest.raises(ValueError, match="non-Replica"):
            validate_standalone(connection)


@pytest.mark.parametrize(
    "damage", ["missing-ledger", "missing-quota", "borrower", "owner", "grant"]
)
def test_incomplete_original_capacity_refuses_before_publication(
    tmp_path: Path, damage: str
) -> None:
    """Missing tables and cross-linked original authority cannot be normalized into availability."""
    value = wrapper()
    integration = json.loads(value["integration_json"])
    control = integration["control"]
    if damage == "missing-ledger":
        del control["capacity_ledger"]
    elif damage == "missing-quota":
        control["quota_ledger"]["claims"] = []
    else:
        record = next(iter(control["capacity_ledger"]["allocations"].values()))
        if damage == "borrower":
            record["borrowers"][0]["units"] = 1
        elif damage == "owner":
            record["reservation"]["role_id"] = "other-role"
        else:
            record["original"]["node_id"] = "other-node"
    value["integration_json"] = json.dumps(integration)
    with closing(database(tmp_path / "source.sqlite3", value)) as connection:
        with pytest.raises(ValueError):
            validate(connection)


def test_exact_chain_reconstruction_preserves_grant_and_refuses_changed_result(
    tmp_path: Path,
) -> None:
    """A zero-event delta retains original parent bytes and an independently hashed head."""
    source = wrapper()
    target = dict(source, unrelated_diagnostic="retained diagnostic")
    original, result = json.dumps(source).encode(), json.dumps(target).encode()
    delta = {
        "codec": "roboguide.checkpoint-byte-delta/v1",
        "length": len(result),
        "pieces": [{"kind": "literal", "bytes": base64.b64encode(result).decode()}],
    }
    with closing(database(tmp_path / "source.sqlite3", source)) as connection:
        connection.execute("UPDATE controller_checkpoint SET schema=?", (CHAIN,))
        connection.execute(
            "INSERT INTO controller_checkpoint_head VALUES (1,1,2,0,?,?,?)",
            (WRAPPER, frame_digest(original, 1), frame_digest(result, 2)),
        )
        connection.execute(
            "INSERT INTO controller_checkpoint_deltas VALUES (2,1,0,?,?,?,?)",
            (WRAPPER, frame_digest(original, 1), frame_digest(result, 2), json.dumps(delta)),
        )
        connection.commit()
        validate(connection)
        connection.execute("UPDATE controller_checkpoint_deltas SET digest=?", ("0" * 64,))
        connection.commit()
        with pytest.raises(ValueError, match="digest differs"):
            validate(connection)
