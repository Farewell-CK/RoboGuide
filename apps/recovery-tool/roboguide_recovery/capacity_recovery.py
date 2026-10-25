"""Independently verify original v56 capacity authority, including exact checkpoint byte chains."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from typing import Any

from .snapshot_references import canonical

WRAPPER = "roboguide.controller-checkpoint/v56"
WRAPPERS = {WRAPPER, "roboguide.controller-checkpoint/v57"}
CHAIN = "roboguide.checkpoint-chain/v1"
MAXIMUM = 64 * 1024 * 1024


def _integer(value: Any, maximum: int = (1 << 64) - 1) -> bool:
    """Keep booleans and out-of-range values out of original unsigned authority fields."""
    return type(value) is int and 0 <= value <= maximum


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    """Refuse absent or hidden members without normalizing original serialized authority."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("quantitative recovery record is not closed")
    return value


def _digest(schema: str, sequence: int, revision: int, content: bytes) -> str:
    """Match the original Rust length-delimited checkpoint identity over exact UTF-8 bytes."""
    encoded = schema.encode()
    return hashlib.sha256(
        CHAIN.encode()
        + len(encoded).to_bytes(8, "little")
        + encoded
        + sequence.to_bytes(8, "little")
        + revision.to_bytes(8, "little")
        + len(content).to_bytes(8, "little")
        + content
    ).hexdigest()


def _reconstruct(previous: bytes, encoded: str) -> bytes:
    """Apply only bounded exact copy/literal operations, never a semantic checkpoint migration."""
    if len(encoded.encode()) > 2 * MAXIMUM:
        raise ValueError("quantitative checkpoint delta exceeds its original bound")
    delta = _closed(json.loads(encoded), {"codec", "length", "pieces"})
    if (
        delta["codec"] != "roboguide.checkpoint-byte-delta/v1"
        or not _integer(delta["length"], MAXIMUM)
        or not isinstance(delta["pieces"], list)
        or len(delta["pieces"]) > 2 * MAXIMUM // 1024 + 1
    ):
        raise ValueError("quantitative checkpoint delta identity or bounds differ")
    result = bytearray()
    for piece in delta["pieces"]:
        if piece.get("kind") == "copy":
            _closed(piece, {"kind", "offset", "length"})
            if not _integer(piece["offset"], len(previous)) or not _integer(
                piece["length"], len(previous) - piece["offset"]
            ):
                raise ValueError("original checkpoint copy exceeds its source")
            value = previous[piece["offset"] : piece["offset"] + piece["length"]]
        else:
            _closed(piece, {"kind", "bytes"})
            if (
                piece["kind"] != "literal"
                or not isinstance(piece["bytes"], str)
                or len(piece["bytes"]) > ((delta["length"] - len(result) + 2) // 3) * 4
            ):
                raise ValueError("original checkpoint literal exceeds its target")
            value = base64.b64decode(piece["bytes"], validate=True)
            if base64.b64encode(value).decode() != piece["bytes"]:
                raise ValueError("original checkpoint literal is not canonical base64")
        if len(value) > delta["length"] - len(result):
            raise ValueError("original checkpoint reconstruction exceeds its declared target")
        result.extend(value)
    if len(result) != delta["length"]:
        raise ValueError("original checkpoint reconstruction length differs")
    return bytes(result)


def _checkpoint(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Reconstruct bounded v56 authority under the already held original writer leases."""
    saved = connection.execute(
        "SELECT schema,event_sequence,checkpoint_json FROM controller_checkpoint WHERE singleton=1"
    ).fetchone()
    head = connection.execute(
        "SELECT base_revision,revision,event_sequence,schema,base_digest,digest "
        "FROM controller_checkpoint_head WHERE singleton=1"
    ).fetchone()
    if saved is None:
        if head is not None:
            raise ValueError("original capacity checkpoint chain lacks its baseline")
        return None
    schema, sequence, raw = saved
    if schema != CHAIN:
        if schema not in WRAPPERS:
            return None
        if (
            head is not None
            or connection.execute("SELECT 1 FROM controller_checkpoint_deltas LIMIT 1").fetchone()
            is not None
        ):
            raise ValueError("plain original capacity checkpoint has hidden chain authority")
        content = raw.encode()
    else:
        if head is None:
            raise ValueError("original capacity checkpoint chain lacks its head")
        base, revision, target_sequence, schema, base_digest, target_digest = head
        if schema not in WRAPPERS:
            return None
        if not all(_integer(value) for value in (sequence, base, revision, target_sequence)):
            raise ValueError("original capacity checkpoint sequence is invalid")
        count, total = connection.execute(
            "SELECT count(*),coalesce(sum(length(CAST(delta_json AS BLOB))),0) "
            "FROM controller_checkpoint_deltas"
        ).fetchone()
        if count > 32 or total > 2 * MAXIMUM or revision - base != count:
            raise ValueError("original capacity checkpoint chain is incomplete or unbounded")
        content = raw.encode()
        current = _digest(schema, sequence, base, content)
        if current != base_digest:
            raise ValueError("original capacity checkpoint baseline digest differs")
        position = base
        for (
            following,
            prior,
            next_sequence,
            next_schema,
            source_digest,
            result_digest,
            delta,
        ) in connection.execute(
            "SELECT revision,previous_revision,event_sequence,schema,base_digest,digest,delta_json "
            "FROM controller_checkpoint_deltas ORDER BY revision"
        ):
            if (
                following != position + 1
                or prior != position
                or not _integer(next_sequence)
                or next_sequence < sequence
                or next_schema != schema
                or source_digest != current
            ):
                raise ValueError("original capacity checkpoint delta order or source differs")
            content = _reconstruct(content, delta)
            current = _digest(schema, next_sequence, following, content)
            if current != result_digest:
                raise ValueError("original capacity checkpoint delta result digest differs")
            position, sequence = following, next_sequence
        if (position, sequence, current) != (revision, target_sequence, target_digest):
            raise ValueError("original capacity checkpoint head differs from its complete chain")
    if len(content) > MAXIMUM:
        raise ValueError("original capacity checkpoint exceeds its bound")
    value: dict[str, Any] = json.loads(content)
    if value.get("schema") != schema:
        raise ValueError("original capacity wrapper differs from its storage identity")
    return value


def validate_standalone(connection: sqlite3.Connection) -> None:
    """Refuse omitting original Replica owners from an explicitly standalone v24 bundle."""
    wrapper = _checkpoint(connection)
    if (
        wrapper is None
        or "replication_profile" not in wrapper
        or wrapper["replication_profile"] is not None
    ):
        raise ValueError(
            "standalone quantitative recovery requires exact non-Replica v56 authority"
        )


def _validate(connection: sqlite3.Connection) -> None:
    """Refuse missing original parents, quotas or semantic demands before bundle publication."""
    wrapper = _checkpoint(connection)
    if wrapper is None:
        return
    integration = json.loads(wrapper["integration_json"])
    control = integration["control"]
    ledger = _closed(control.get("capacity_ledger"), {"generation", "allocations"})
    if (
        not _integer(ledger["generation"])
        or not isinstance(ledger["allocations"], dict)
        or len(ledger["allocations"]) > 16384
    ):
        raise ValueError("original capacity ledger bounds or generation differ")
    groups = {group["group_id"]: group for group in control["groups"]}
    claims = {
        claim["allocation_id"]: claim
        for claim in control["quota_ledger"]["claims"]
        if claim.get("allocation_id") is not None
    }
    if (
        len(claims)
        != sum(
            claim.get("allocation_id") is not None for claim in control["quota_ledger"]["claims"]
        )
        or claims.keys() != ledger["allocations"].keys()
    ):
        raise ValueError("original capacity ledger and exact quota parents differ")
    generations = set()
    for identity, record in ledger["allocations"].items():
        _closed(record, {"original", "reservation", "borrowers"})
        original = _closed(
            record["original"],
            {
                "schema",
                "allocation_id",
                "generation",
                "resource_id",
                "resource_class",
                "node_id",
                "group_id",
                "owner",
                "tenant_id",
                "task_ref",
                "role_id",
                "units",
                "allocation_units",
                "admitted_capacity",
            },
        )
        context = isinstance(original["owner"], dict) and set(original["owner"]) == {"Context"}
        role = None if context else original["role_id"]
        expected = (
            "allocation:sha256:"
            + hashlib.sha256(
                canonical(
                    [
                        original["schema"],
                        original["generation"],
                        original["resource_id"],
                        original["resource_class"],
                        original["node_id"],
                        original["group_id"],
                        original["owner"],
                        role,
                        original["tenant_id"],
                        original["allocation_units"],
                        original["admitted_capacity"],
                    ]
                )
            ).hexdigest()
        )
        if (
            identity != original["allocation_id"]
            or identity != expected
            or original["schema"] != "roboguide.resource-allocation-grant/v0.1"
            or not all(
                _integer(original[key], (1 << 32) - 1)
                for key in ("units", "allocation_units", "admitted_capacity")
            )
            or not 0
            < original["units"]
            <= original["allocation_units"]
            <= original["admitted_capacity"]
            or not _integer(original["generation"], ledger["generation"])
            or original["generation"] == 0
            or original["generation"] in generations
            or original["resource_id"] in control["reservations"]
            or control["mission_tenants"].get(original["task_ref"]["mission_id"])
            != original["tenant_id"]
        ):
            raise ValueError("original capacity identity, units, tenant or generation differ")
        generations.add(original["generation"])
        group = groups[original["group_id"]]
        claim = claims[identity]
        if (
            group["mission_id"] != original["task_ref"]["mission_id"]
            or group["lifecycle"] == "Released"
            or any(
                claim[key] != value
                for key, value in {
                    "claim_id": identity,
                    "allocation_id": identity,
                    "tenant_id": original["tenant_id"],
                    "resource_id": original["resource_id"],
                    "kind": "Compute",
                    "units": original["allocation_units"],
                }.items()
            )
        ):
            raise ValueError("original capacity parent lost its Group or exact quota attribution")
        _reservation(record, group, control)
        requirements = {
            (canonical(task), role): value for task, role, value in group["role_requirements"]
        }
        seen = set()
        used = 0
        if not isinstance(record["borrowers"], list) or len(record["borrowers"]) > 16384:
            raise ValueError("original capacity borrowers are not bounded")
        for borrower in record["borrowers"]:
            _closed(borrower, {"task_ref", "role_id", "units"})
            key = (canonical(borrower["task_ref"]), borrower["role_id"])
            requirement = requirements[key]
            if (
                key in seen
                or not _integer(borrower["units"], original["allocation_units"])
                or borrower["units"] == 0
                or not any(
                    demand.get("allocation_mode") == "divisible"
                    and demand["kind"] == "Compute"
                    and demand["units"] == borrower["units"]
                    and demand.get("resource_class") == original["resource_class"]
                    for demand in requirement["resources"]
                )
            ):
                raise ValueError("original capacity borrower changed its complete semantic demand")
            _borrower(original, record["reservation"], borrower, group, control)
            seen.add(key)
            used += borrower["units"]
        if used > original["allocation_units"]:
            raise ValueError("original capacity borrowers exceed their original parent")


def _reservation(record: dict[str, Any], group: dict[str, Any], control: dict[str, Any]) -> None:
    """Join the original parent, first borrower and explicit Context envelope."""
    original = record["original"]
    reservation = _closed(
        record["reservation"], {"task_ref", "role_id", "group_id", "scope", "owner"}
    )
    owner = original["owner"]
    context = isinstance(owner, dict) and set(owner) == {"Context"}
    if (
        reservation["task_ref"] != original["task_ref"]
        or reservation["role_id"] != original["role_id"]
        or reservation["owner"] != owner
        or reservation["group_id"] not in (None, original["group_id"])
        or reservation["scope"] != ("Context" if context else "Task")
    ):
        raise ValueError("original capacity parent reservation changed its immutable owner")
    if context:
        declaration = _closed(owner["Context"], {"mission_id", "context_id", "context_role_id"})
        if declaration["mission_id"] != original["task_ref"]["mission_id"]:
            raise ValueError("original Context capacity changed Mission")
        envelopes = [
            role
            for context_id, role in group.get("context_resource_envelopes", [])
            if context_id == declaration["context_id"]
            and role["context_role_id"] == declaration["context_role_id"]
        ]
        if len(envelopes) != 1 or envelopes[0]["resource_envelope"] != [
            {
                "kind": "Compute",
                "units": original["allocation_units"],
                "allocation_mode": "divisible",
                "resource_class": original["resource_class"],
            }
        ]:
            raise ValueError("original Context capacity lost its exact semantic envelope")
        if reservation["group_id"] is not None:
            bindings = [
                binding
                for binding in group["context_bindings"].values()
                if binding["context_id"] == declaration["context_id"]
                and binding["context_role_id"] == declaration["context_role_id"]
            ]
            if (
                len(bindings) != 1
                or bindings[0]["origin_task_ref"] != original["task_ref"]
                or bindings[0]["assignment"]["role_id"] != original["role_id"]
                or bindings[0]["assignment"]["node_id"] != original["node_id"]
                or original["resource_id"] not in bindings[0]["assignment"]["resource_ids"]
            ):
                raise ValueError("original Context allocation lost its exact bound origin")
    elif (
        owner != {"Task": original["task_ref"]}
        or original["allocation_units"] != original["units"]
        or record["borrowers"]
        != [
            {
                "task_ref": original["task_ref"],
                "role_id": original["role_id"],
                "units": original["units"],
            }
        ]
    ):
        raise ValueError("original Task allocation changed borrower or units")
    if (
        reservation["group_id"] is None
        and {
            "task_ref": original["task_ref"],
            "role_id": original["role_id"],
            "units": original["units"],
        }
        not in record["borrowers"]
    ):
        raise ValueError("unbound original capacity parent lost its first borrower")


def _borrower(
    original: dict[str, Any],
    reservation: dict[str, Any],
    borrower: dict[str, Any],
    group: dict[str, Any],
    control: dict[str, Any],
) -> None:
    """Check original bound or pending ownership without inventing current State."""
    executions = [
        execution for key, execution in group["task_executions"] if key == borrower["task_ref"]
    ]
    if len(executions) != 1 or borrower["task_ref"]["mission_id"] != group["mission_id"]:
        raise ValueError("original capacity borrower lost its exact TaskExecution")
    execution = executions[0]
    scopes = dict(execution["role_scopes"])
    contexts = dict(execution["context_roles"])
    expected_owner = (
        {
            "Context": {
                "mission_id": group["mission_id"],
                "context_id": execution["context_id"],
                "context_role_id": contexts[borrower["role_id"]],
            }
        }
        if scopes[borrower["role_id"]] == "Context"
        else {"Task": borrower["task_ref"]}
    )
    if original["owner"] != expected_owner:
        raise ValueError("original capacity borrower changed its lifetime owner")
    if reservation["group_id"] is not None:
        bound = any(
            assignment["role_id"] == borrower["role_id"]
            and assignment["node_id"] == original["node_id"]
            and original["resource_id"] in assignment["resource_ids"]
            for assignment in execution["assignments"]
        )
        pending = any(
            commit["group_id"] == original["group_id"]
            and commit["task_ref"] == borrower["task_ref"]
            and commit["role_id"] == borrower["role_id"]
            and commit["replacement_node_id"] == original["node_id"]
            and original["resource_id"] in commit["committed_resource_ids"]
            for commit in control["pending_recovery_commitments"]
        )
        if bound == pending:
            raise ValueError("original capacity borrower is orphaned or both bound and pending")


def validate(connection: sqlite3.Connection) -> None:
    """Refuse every malformed retained original record before any backup or restore publication."""
    try:
        _validate(connection)
    except (KeyError, TypeError, AttributeError, OverflowError, UnicodeError) as error:
        raise ValueError(
            "original quantitative recovery authority is incomplete or malformed"
        ) from error
