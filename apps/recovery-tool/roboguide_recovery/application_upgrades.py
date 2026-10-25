"""Join original application upgrade authority, local clocks and actual wait history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .profile import (
    valid_process_instance,
    valid_transfer_epoch,
    validate_controller_drain,
    validate_controller_process,
)
from .replica_content import _budget
from .snapshot_references import canonical

PROFILE = "roboguide.recovery-profile/v0.25"
AUTHORITY = "application_upgrade_authority_v1"
OPERATIONS = "application_upgrade_operations_v1"
BASE = "application_upgrade_base_parent_v1"
ORIGINAL_POLICY = "application_upgrade_original_policy_v1"
SCHEMAS = {f"roboguide.controller-checkpoint/v{version}" for version in range(53, 59)}


def _requires_scoped_profile(value: Any) -> bool:
    """Keep new application identities out of the original v0.25 recovery interpretation."""
    if isinstance(value, dict):
        return any(
            (
                key in {"application_schema", "from_schema"}
                and item == "roboguide.controller-checkpoint/v57"
            )
            or _requires_scoped_profile(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_requires_scoped_profile(item) for item in value)
    return False


def _requires_topology_profile(value: Any) -> bool:
    """Fence exact new target or source identities even before an upgrade becomes active."""
    if isinstance(value, dict):
        return any(
            (
                key in {"application_schema", "from_schema"}
                and item == "roboguide.controller-checkpoint/v58"
            )
            or _requires_topology_profile(item)
            for key, item in value.items()
        )
    return isinstance(value, list) and any(_requires_topology_profile(item) for item in value)


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    """Require the complete original closed record without adding absent authority fields."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("original application upgrade record is incomplete or not closed")
    return value


def _hex(value: Any, prefix: str = "sha256:") -> bool:
    """Match complete original lowercase digests independently of caller labels."""
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) == len(prefix) + 64
        and all(character in "0123456789abcdef" for character in value[len(prefix) :])
    )


def _uint(value: Any) -> bool:
    """Keep booleans and unsigned overflow out of original sequence and generation fields."""
    return type(value) is int and 0 <= value < 2**64


def _binary(value: Any) -> dict[str, Any]:
    """Retain actual target schema, executable hash and independent complete target policy."""
    result = _closed(value, {"application_schema", "binary_digest", "policy_digest"})
    if (
        result["application_schema"] not in SCHEMAS
        or not _hex(result["binary_digest"])
        or not _hex(result["policy_digest"], "")
    ):
        raise ValueError("original application upgrade binary/schema/policy differs")
    return result


def _intent(value: Any) -> dict[str, Any]:
    """Check exact original all-member support, membership and complete predecessor bindings."""
    result = _closed(value, {"request", "epoch", "membership", "predecessor", "supports"})
    request = _closed(
        result["request"],
        {"schema", "command_id", "expected_revision", "from_schema", "target", "budget_ms"},
    )
    target = _binary(request["target"])
    identifier = request["command_id"]
    if (
        request["schema"] != "roboguide.application-upgrade/v0.1"
        or not isinstance(identifier, str)
        or not 0 < len(identifier) <= 128
        or not identifier.strip()
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in identifier)
        or request["from_schema"] not in SCHEMAS
        or int(target["application_schema"].rsplit("v", 1)[1])
        < int(request["from_schema"].rsplit("v", 1)[1])
        or not _uint(request["expected_revision"])
        or not _uint(request["budget_ms"])
        or not 100 <= request["budget_ms"] <= 300000
        or not valid_transfer_epoch(result["epoch"])
    ):
        raise ValueError("original application upgrade request or epoch differs")
    predecessor = _closed(
        result["predecessor"], {"revision", "event_sequence", "checkpoint_digest"}
    )
    if (
        not _uint(predecessor["revision"])
        or not _uint(predecessor["event_sequence"])
        or (
            predecessor["checkpoint_digest"] is None
            and (predecessor["revision"] != 0 or predecessor["event_sequence"] != 0)
        )
        or (
            predecessor["checkpoint_digest"] is not None
            and (not _hex(predecessor["checkpoint_digest"], "") or predecessor["revision"] == 0)
        )
    ):
        raise ValueError("original application upgrade predecessor is incomplete")
    membership = _closed(result["membership"], {"log_id", "membership"})
    configuration = _closed(membership["membership"], {"configs", "nodes"})
    nodes = configuration["nodes"]
    if (
        not isinstance(nodes, dict)
        or not 3 <= len(nodes) <= 9
        or str(result["epoch"]["replica_id"]) not in nodes
        or not isinstance(result["supports"], list)
        or len(result["supports"]) != len(nodes)
    ):
        raise ValueError("original upgrade complete membership/support is absent")
    members: set[int] = set()
    for proof in result["supports"]:
        _closed(
            proof,
            {
                "member",
                "leaf",
                "infrastructure_binary_digest",
                "target",
                "membership",
                "predecessor",
            },
        )
        member = proof["member"]
        if (
            not _uint(member)
            or member in members
            or str(member) not in nodes
            or not _hex(proof["leaf"])
            or not _hex(proof["infrastructure_binary_digest"])
            or proof["target"] != target
            or proof["membership"] != membership
            or proof["predecessor"] != predecessor
        ):
            raise ValueError("original upgrade member proof lost its exact immutable target")
        members.add(member)
    return result


def _drain(value: Any, intent: dict[str, Any], process: Any) -> None:
    """Join original member proof to exact wait/absence without observing a replacement process."""
    result = _closed(value, {"member", "leaf", "intent_digest", "proof"})
    support = [proof for proof in intent["supports"] if proof["member"] == result["member"]]
    if (
        len(support) != 1
        or result["leaf"] != support[0]["leaf"]
        or result["intent_digest"] != "sha256:" + hashlib.sha256(canonical(intent)).hexdigest()
    ):
        raise ValueError("original upgrade drain lost its actual member/leaf/intent")
    validate_controller_drain(result["proof"], intent["epoch"], process)


def _authority(value: Any, original: str) -> list[dict[str, Any]]:
    """Validate original monotonic upgrade authority independently of current writer labels."""
    if value is None:
        return []
    if (
        not isinstance(value, dict)
        or not {"schema", "original_schema", "receipts"} <= value.keys()
        or value.keys() - {"schema", "original_schema", "receipts", "aborts"}
    ):
        raise ValueError("original application upgrade authority is not closed")
    result = value
    aborts = result.get("aborts", [])
    if (
        result["schema"] != "roboguide.application-upgrade-authority/v0.1"
        or result["original_schema"] != original
        or original not in SCHEMAS
        or not isinstance(result["receipts"], list)
        or not isinstance(aborts, list)
        or not 1 <= len(result["receipts"]) + len(aborts) <= 32
    ):
        raise ValueError("original application upgrade authority head differs")
    previous, identities, binaries = original, set(), set()
    for index, receipt in enumerate(result["receipts"]):
        _closed(receipt, {"intent", "drains", "revision"})
        intent = _intent(receipt["intent"])
        request = intent["request"]
        if (
            not _uint(receipt["revision"])
            or receipt["revision"] != index + 1
            or request["expected_revision"] != index
            or request["from_schema"] != previous
            or request["command_id"] in identities
            or request["target"]["binary_digest"] in binaries
            or not isinstance(receipt["drains"], list)
            or len(receipt["drains"]) != len(intent["supports"])
            or {proof["member"] for proof in receipt["drains"]}
            != {proof["member"] for proof in intent["supports"]}
        ):
            raise ValueError("original application upgrade history or complete drains differ")
        for proof in receipt["drains"]:
            process = proof["proof"].get("outcome", {}).get("process")
            _drain(proof, intent, process)
        identities.add(request["command_id"])
        binaries.add(request["target"]["binary_digest"])
        previous = request["target"]["application_schema"]
    abort_ids: set[str] = set()
    for abort in aborts:
        _closed(abort, {"command_id", "intent", "epoch", "membership"})
        intent = _intent(abort["intent"])
        if (
            not isinstance(abort["command_id"], str)
            or not 0 < len(abort["command_id"].strip()) <= 128
            or abort["command_id"] in abort_ids
            or intent["request"]["command_id"] in identities
            or not valid_transfer_epoch(abort["epoch"])
            or abort["membership"] != intent["membership"]
        ):
            raise ValueError("original upgrade abandonment overlaps activation or changed source")
        abort_ids.add(abort["command_id"])
        identities.add(intent["request"]["command_id"])
    return list(result["receipts"])


def validate_parent(connection: sqlite3.Connection, profile_schema: str) -> None:
    """Refuse absent histories and original wait/authority mismatches before cold publication."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "replica_v1_meta" not in names:
        if version == 10:
            raise ValueError("application upgrade parent lost its mandatory original metadata")
        return
    metadata = dict(connection.execute("SELECT key,value FROM replica_v1_meta"))
    original_identity = json.loads(metadata.get("identity", "null"))
    if profile_schema != "roboguide.recovery-profile/v0.27" and (
        isinstance(original_identity, list)
        and len(original_identity) == 2
        and original_identity[1] == "roboguide.controller-checkpoint/v58"
        or any(
            _requires_topology_profile(json.loads(metadata[key]))
            for key in (AUTHORITY, OPERATIONS)
            if key in metadata
        )
    ):
        raise ValueError("spatial/calendar application generation requires explicit recovery v0.27")
    if profile_schema not in {
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
    } and (
        isinstance(original_identity, list)
        and len(original_identity) == 2
        and original_identity[1] == "roboguide.controller-checkpoint/v57"
        or any(
            _requires_scoped_profile(json.loads(metadata[key]))
            for key in (AUTHORITY, OPERATIONS)
            if key in metadata
        )
    ):
        raise ValueError("scoped application generation requires explicit recovery v0.26")
    if version != 10:
        if {AUTHORITY, OPERATIONS, BASE} & metadata.keys():
            raise ValueError("old parent contains hidden application upgrade authority")
        return
    if (
        profile_schema
        not in {PROFILE, "roboguide.recovery-profile/v0.26", "roboguide.recovery-profile/v0.27"}
        or not {AUTHORITY, OPERATIONS, BASE} <= metadata.keys()
    ):
        raise ValueError("application upgrade parent10 requires complete explicit recovery v0.25")
    original = json.loads(metadata["identity"])
    if not isinstance(original, list) or len(original) != 2 or original[1] not in SCHEMAS:
        raise ValueError("application upgrade changed the original parent identity")
    if json.loads(metadata[BASE]) not in {8, 9}:
        raise ValueError("application upgrade original parent generation differs")
    authority = json.loads(metadata[AUTHORITY])
    receipts = _authority(authority, original[1])
    original_policy = json.loads(metadata[ORIGINAL_POLICY]) if ORIGINAL_POLICY in metadata else None
    if original_policy is not None and not _hex(original_policy, ""):
        raise ValueError("original application upgrade policy witness is incomplete")
    for receipt in receipts:
        request = receipt["intent"]["request"]
        if request["target"]["policy_digest"] == original_policy or (
            request["from_schema"] == request["target"]["application_schema"]
            and original_policy is None
        ):
            raise ValueError("same-schema upgrade lacks original policy or restores original code")
    root = json.loads(metadata.get("content_root_identity", "null"))
    if not isinstance(root, dict) or not _uint(root.get("member")):
        raise ValueError("upgrade lost its original member-local content root identity")
    member = root["member"]
    operations = json.loads(metadata[OPERATIONS])
    if not isinstance(operations, list) or len(operations) > 32:
        raise ValueError("original application upgrade operations are absent or unbounded")
    history = json.loads(metadata["controller_processes_v1"])
    identities: set[str] = set()
    for operation in operations:
        _closed(
            operation,
            {"intent", "clock", "process", "process_history_head", "supervisor_instance", "drain"},
        )
        intent = _intent(operation["intent"])
        identifier = intent["request"]["command_id"]
        if identifier in identities:
            raise ValueError("original application upgrade operation ID repeats")
        identities.add(identifier)
        _budget(operation["clock"])
        if not valid_process_instance(operation["supervisor_instance"]):
            raise ValueError("original upgrade supervisor instance is incomplete")
        if operation["clock"]["duration_ms"] > intent["request"]["budget_ms"]:
            raise ValueError("original member upgrade clock exceeds its original request")
        process = operation["process"]
        head = operation["process_history_head"]
        if not _uint(head) or head > len(history):
            raise ValueError("original upgrade process journal prefix is absent")
        prefix = history[:head]
        if process is not None:
            validate_controller_process(process)
            if not any(record["process"] == process for record in prefix):
                raise ValueError("original upgrade drain lost its actual process journal")
        elif any(record["outcome"] is None for record in prefix):
            raise ValueError("original empty-process upgrade prefix contains an unreaped child")
        if operation["drain"] is not None:
            if operation["drain"]["member"] != member:
                raise ValueError("original local upgrade wait belongs to another member")
            _drain(operation["drain"], intent, process)
            proof = operation["drain"]["proof"]
            if (
                proof["kind"] == "no-application-process"
                and proof["supervisor_instance"] != operation["supervisor_instance"]
            ):
                raise ValueError("original upgrade empty-process proof borrowed a restart instance")
            if proof["kind"] == "process-exit" and not any(
                record["process"] == process and record["outcome"] == proof["outcome"]
                for record in prefix
            ):
                raise ValueError("original upgrade wait differs from the actual retained journal")
        for receipt in receipts:
            if receipt["intent"]["request"]["command_id"] == identifier:
                drains = [proof for proof in receipt["drains"] if proof["member"] == member]
                if receipt["intent"] != intent or drains != [operation["drain"]]:
                    raise ValueError("committed upgrade differs from original local intent/wait")
        for abort in [] if authority is None else authority.get("aborts", []):
            if abort["intent"]["request"]["command_id"] == identifier and abort["intent"] != intent:
                raise ValueError("abandoned upgrade differs from original local operational intent")
