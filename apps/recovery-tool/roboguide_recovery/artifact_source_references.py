"""Inspect original Controller content claims under complete quiescent writers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import archive_aborts, archive_references, catalog_references
from . import artifact_gc_journal as gc
from .profile import real_directory
from .snapshot_references import OperationBudget, canonical, digest


def catalog_rows(
    connection: sqlite3.Connection,
    budget: OperationBudget,
    profile_schema: str = gc.SCHEMA,
) -> list[dict[str, Any]]:
    """Keep original archived envelopes and the consistent tail inside fixed bounds."""
    result = archive_references.catalog_history(connection, profile_schema, budget)
    total = sum(len(canonical(row)) for row in result)
    connection.row_factory = sqlite3.Row
    for index, row in enumerate(
        connection.execute(
            "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,"
            "payload_schema,payload_json "
            "FROM events ORDER BY sequence LIMIT 16385"
        )
    ):
        budget.check()
        total += len(row["payload_json"].encode())
        if index >= 16384 or total > 64 * 1024 * 1024:
            raise ValueError("original source content evidence exceeds its fixed scan bound")
        result.append(dict(row))
    connection.row_factory = None
    return result


def upload_record(record: Any) -> None:
    """Validate the existing original upload contract without inferring byte or tenant ownership."""
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "upload_id",
            "principal_id",
            "tenant_id",
            "original_certificate",
            "admitted_at_ms",
            "deadline_ms",
            "phase",
            "content_digest",
            "byte_size",
            "prepared_at_ms",
            "completed_at_ms",
        }
        or (
            not isinstance(record["upload_id"], str)
            or gc.COMMAND.fullmatch(record["upload_id"]) is None
            or any(
                not gc._text(record[key], maximum)
                for key, maximum in (
                    ("principal_id", 256),
                    ("tenant_id", 128),
                    ("original_certificate", 256),
                )
            )
            or not all(gc._integer(record[key]) for key in ("admitted_at_ms", "deadline_ms"))
            or not 0 < record["deadline_ms"] - record["admitted_at_ms"] <= 1_800_000
        )
    ):
        raise ValueError("original source upload identity/budget is invalid")
    proof = (
        isinstance(record["content_digest"], str)
        and gc.CHECKSUM.fullmatch(record["content_digest"]) is not None
        and gc._integer(record["byte_size"])
        and record["byte_size"] <= 4 * 1024**3
        and gc._integer(record["prepared_at_ms"])
        and record["admitted_at_ms"] <= record["prepared_at_ms"] < record["deadline_ms"]
    )
    empty = all(record[key] is None for key in ("content_digest", "byte_size", "prepared_at_ms"))
    if not (
        (record["phase"] == "started" and empty and record["completed_at_ms"] is None)
        or (record["phase"] == "prepared" and proof and record["completed_at_ms"] is None)
        or (
            record["phase"] == "completed"
            and proof
            and gc._integer(record["completed_at_ms"])
            and record["completed_at_ms"] >= record["prepared_at_ms"]
        )
        or (
            record["phase"] == "abandoned"
            and (proof or empty)
            and record["completed_at_ms"] is None
        )
    ):
        raise ValueError("original source upload lacks exact original phase/byte proof")


def upload_history(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Retain original uploads and independent exact retirement commands without reviving either."""
    uploads: dict[str, dict[str, Any]] = {}
    retirements: dict[str, dict[str, Any]] = {}
    commands: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if "ArtifactUploadTransition" in payload:
            value = payload["ArtifactUploadTransition"]["record"]
            upload_record(value)
            original = uploads.get(value["upload_id"])
            if original is None:
                if value["phase"] != "started" or len(uploads) >= 8192:
                    raise ValueError("original upload admission is absent or full")
            elif original != value:
                mutable = {
                    "phase",
                    "content_digest",
                    "byte_size",
                    "prepared_at_ms",
                    "completed_at_ms",
                }
                if (
                    any(value[key] != original[key] for key in original.keys() - mutable)
                    or (original["phase"], value["phase"])
                    not in {
                        ("started", "prepared"),
                        ("started", "abandoned"),
                        ("prepared", "completed"),
                        ("prepared", "abandoned"),
                    }
                    or (
                        original["content_digest"] is not None
                        and any(
                            value[key] != original[key]
                            for key in ("content_digest", "byte_size", "prepared_at_ms")
                        )
                    )
                ):
                    raise ValueError("original upload receipt was changed or revived")
            uploads[value["upload_id"]] = value
        if "ArtifactUploadRetired" not in payload:
            continue
        receipt = payload["ArtifactUploadRetired"]["receipt"]
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "schema",
                "command_id",
                "store_owner_id",
                "upload",
                "principal_id",
                "tenant_id",
                "certificate_digest",
                "canonical_grant_json",
                "admitted_at_ms",
                "reason",
            }
            or (
                row["payload_schema"] != "domain.EventPayload.json/v31"
                or receipt["schema"] != "roboguide.artifact-upload-retirement/v0.1"
                or not gc._text(receipt["command_id"], 128)
                or not gc._text(receipt["principal_id"], 256)
                or not gc._text(receipt["reason"], 1024)
                or not isinstance(receipt["store_owner_id"], str)
                or gc.OWNER.fullmatch(receipt["store_owner_id"]) is None
                or not isinstance(receipt["upload"], dict)
            )
        ):
            raise ValueError("original upload retirement authority is invalid")
        original = receipt["upload"]
        upload_record(original)
        if (
            uploads.get(original["upload_id"]) != original
            or original["phase"] != "completed"
            or receipt["tenant_id"] != original["tenant_id"]
            or not gc._integer(receipt["admitted_at_ms"])
            or receipt["admitted_at_ms"] < original["completed_at_ms"]
            or not isinstance(receipt["canonical_grant_json"], str)
            or len(receipt["canonical_grant_json"].encode()) > 64 * 1024
        ):
            raise ValueError("upload retirement lost its complete original source/tenant/time")
        grant = json.loads(receipt["canonical_grant_json"])
        if (
            not isinstance(grant, dict)
            or canonical(grant).decode() != receipt["canonical_grant_json"]
        ):
            raise ValueError("upload retirement current grant evidence is not canonical")
        leaf = receipt["certificate_digest"]
        if leaf is not None and (not isinstance(leaf, str) or gc.CHECKSUM.fullmatch(leaf) is None):
            raise ValueError("upload retirement current TLS evidence is invalid")
        key, command = original["upload_id"], receipt["command_id"]
        if (key in retirements and retirements[key] != receipt) or (
            command in commands and commands[command] != receipt
        ):
            raise ValueError("upload retirement changed original receipt or reused another command")
        retirements[key] = commands[command] = receipt
    return uploads, retirements


def _contains(value: Any, checksum: str) -> bool:
    """Observe complete bounded authority DTOs without interpreting arbitrary labels as releases."""
    if isinstance(value, dict):
        return any(_contains(child, checksum) for child in value.values())
    if isinstance(value, list):
        return any(_contains(child, checksum) for child in value)
    return bool(value == checksum)


def _use_revoked(use: dict[str, Any], preparation_record: dict[str, Any]) -> bool:
    """Require each original provider proof, including confirmed readonly observations."""
    revokes = [
        value for value in use["controls"].values() if value["command"]["operation"] == "revoke"
    ]
    if not use["revoked"] or len(revokes) != 1:
        return False
    delivery = revokes[0]
    command = delivery["command"]
    proofs: dict[str, dict[str, Any]] = {}
    receipt = delivery.get("receipt")
    if receipt is not None:
        if (
            receipt.get("command_id") != command["command_id"]
            or receipt.get("generation") != 0
            or delivery.get("received_at_ms") is None
        ):
            return False
        for proof in receipt["providers"]:
            if proof["provider_id"] in proofs:
                return False
            proofs[proof["provider_id"]] = proof
    reconciliation = delivery.get("reconciliation")
    if reconciliation is not None:
        for observation in reconciliation["queries"].values():
            if (
                observation.get("current_authority_at_receipt") is not True
                or observation.get("receipt") is None
            ):
                continue
            for item in observation["receipt"]["observations"]:
                if item.get("status") != "confirmed" or item.get("proof") is None:
                    continue
                proof = item["proof"]
                if proof["provider_id"] in proofs and proofs[proof["provider_id"]] != proof:
                    return False
                proofs[proof["provider_id"]] = proof
    preparation = preparation_record["preparation"]
    expected = {value["consumer"]["provider_id"] for value in preparation["inputs"]}
    if set(proofs) != expected:
        return False
    for key, proof in proofs.items():
        inputs = {
            value["import_id"]
            for value in preparation["inputs"]
            if value["consumer"]["provider_id"] == key
        }
        availability = {}
        for delivered in preparation_record["controls"].values():
            available = delivered.get("receipt")
            if available is None or available.get("status") != "available":
                continue
            for observed in available["observations"]:
                if observed["import_id"] in inputs:
                    availability[observed["import_id"]] = observed
        if set(availability) != inputs or any(
            observed["provider_instance_id"] != proof["provider_instance_id"]
            or observed["provider_contract_digest"] != proof["provider_contract_digest"]
            for observed in availability.values()
        ):
            return False
        original = proof.get("original_receipt")
        if not isinstance(original, dict) or (
            proof.get("import_ids") != []
            or original.get("import_ids") != []
            or original.get("status") != "revoked"
            or original.get("command_id") != command["command_id"]
            or original.get("lease_id") != command["lease_id"]
            or original.get("generation") != 0
            or original.get("provider_id") != key
            or original.get("preparation_digest") != command["preparation_digest"]
            or proof.get("provider_instance_id") != original.get("provider_instance_id")
            or original.get("configuration_digest") != command["configuration_digest"]
            or not isinstance(proof.get("provider_contract_digest"), str)
            or gc.CHECKSUM.fullmatch(proof["provider_contract_digest"]) is None
            or proof.get("receipt_digest") != digest(canonical(original))
        ):
            return False
    return True


def _revision_goal(
    checkpoint: dict[str, Any], mission_id: str, mission: dict[str, Any], state: dict[str, Any]
) -> None:
    """Check original Control goal/source receipts and their unchanged kernel completion budgets."""
    control = json.loads(checkpoint["integration_json"])["control"]
    contract = control["mission_goal_contracts"].get(mission_id)
    if not isinstance(contract, dict) or (
        contract["mission_id"] != mission_id
        or contract["digest"] != state["goal_contract_digest"]
        or digest(canonical(contract | {"digest": ""})) != contract["digest"]
        or contract["context_digest"] != digest(canonical(state["policy"]["goal_context"]))
        or contract["spec"] != state["policy"]["goal_verifier"]
        or state["authority"] in contract["source"]["producer_principals"]
        or state["policy"]["proposer"] in contract["source"]["producer_principals"]
    ):
        raise ValueError("original runtime revision changed its independent goal/source contract")
    receipts = {
        key: receipt
        for key, receipt in control["mission_goal_receipts"].items()
        if receipt["evidence"]["mission_id"] == mission_id
    }
    if set(receipts) != set(state["goal_evidence"]):
        raise ValueError("original runtime revision lost goal receive-budget coverage")
    for key, receipt in receipts.items():
        evidence = receipt["evidence"]
        progress = state["goal_evidence"][key]
        budget = progress["budget"]
        issued, duration = budget["issued"], budget["duration_ms"]
        if (
            receipt["input_digest"] != key
            or digest(canonical(evidence)) != key
            or evidence["contract_digest"] != contract["digest"]
            or evidence["tenant_id"] != contract["tenant_id"]
            or evidence["source_id"] != contract["source"]["source_id"]
            or evidence["source_revision"] != contract["source"]["revision"]
            or receipt["authority_subject"] not in contract["source"]["producer_principals"]
            or duration != evidence["valid_for_ms"]
            or not gc._integer(duration, positive=True)
            or duration > 3_600_000
            or not isinstance(issued["boot_id"], str)
            or re.fullmatch(r"[0-9a-fA-F-]{36}", issued["boot_id"]) is None
            or len(issued["namespace"]) != 2
            or not all(gc._integer(value) for value in issued["namespace"])
            or issued["namespace"][1] == 0
            or not gc._integer(issued["elapsed_ms"])
            or issued["resolution_ms"] not in {1, 10, 100}
        ):
            raise ValueError("original runtime revision goal receipt/budget is inconsistent")
        completed = progress["completed_time"]
        if completed is not None and (
            mission["lifecycle"] != "Completed"
            or mission["goal_receipt"] != key
            or completed["boot_id"] != issued["boot_id"]
            or completed["namespace"] != issued["namespace"]
            or not gc._integer(completed["elapsed_ms"])
            or completed["elapsed_ms"] < issued["elapsed_ms"]
            or not gc._integer(completed["resolution_ms"], positive=True)
            or completed["resolution_ms"] > 100
            or completed["elapsed_ms"]
            - issued["elapsed_ms"]
            + max(issued["resolution_ms"], completed["resolution_ms"])
            >= duration
        ):
            raise ValueError(
                "original runtime revision goal completion lost its original kernel budget"
            )
    if mission["lifecycle"] == "Completed":
        selected = receipts.get(mission["goal_receipt"])
        if selected is None or (
            selected["evidence"]["verdict"] != "satisfied"
            or selected["evidence"]["finality"] != "final"
            or selected["evidence"]["group_id"] != mission["group_id"]
            or selected["received_at"] > mission["goal_completed_at"]
            or mission["goal_completed_at"] - selected["received_at"]
            >= selected["evidence"]["valid_for_ms"]
        ):
            raise ValueError("original runtime revision lacks exact final-positive goal completion")


def _application(checkpoint: dict[str, Any], checksum: str) -> None:
    """Retain outstanding original preparation, revoke, handoff and revision readers."""
    if (
        checkpoint.get("schema") not in gc.WRAPPERS
        or checkpoint.get("replication_profile") is not None
    ):
        raise ValueError("source GC requires its exact standalone current Controller authority")
    required = {"memory_preparation", "memory_use", "memory_imports", "memory_handoffs"}
    if not required <= checkpoint.keys():
        raise ValueError("original source Memory authority is unavailable")
    preparations = checkpoint["memory_preparation"]["records"]
    uses = checkpoint["memory_use"]["records"]
    for execution, record in preparations.items():
        preparation = record.get("preparation")
        if not _contains(preparation, checksum):
            continue
        if record["phase"] != "revoked":
            raise ValueError("original preparation still retains source content")
        if execution in uses:
            use = uses[execution]
            if not _use_revoked(use, record):
                raise ValueError("original provider revoke proof still retains source content")
    for value in checkpoint["memory_imports"].values():
        if not _contains(value.get("request"), checksum) or value.get("phase") == "rejected":
            continue
        execution = value["request"]["execution_id"]
        if execution not in preparations or preparations[execution]["phase"] != "revoked":
            raise ValueError("original import retains an unresolved source reader")
    for value in checkpoint["memory_handoffs"].values():
        if value.get("phase") != "committed" and _contains(value, checksum):
            raise ValueError("pending original handoff retains source content")
    missions = {
        value["plan"]["mission"]["id"]: value
        for value in json.loads(checkpoint["orchestration_json"])
    }
    for mission_id, state in checkpoint.get("runtime_revisions", {}).items():
        if not _contains(state, checksum):
            continue
        mission = missions.get(mission_id)
        if (
            state.get("revoked") is not True
            or mission is None
            or (
                mission.get("lifecycle") not in {"Completed", "Failed", "Cancelled"}
                or any(
                    value.get("phase") not in {"applied", "cancelled"}
                    for value in state["switches"]
                )
            )
        ):
            raise ValueError("original runtime revision still retains its source declarations")
        if mission["lifecycle"] == "Completed" and (
            mission.get("goal_gate") != state["goal_contract_digest"]
            or mission.get("goal_completed_at") is None
            or not isinstance(mission.get("goal_receipt"), str)
            or state["goal_evidence"].get(mission["goal_receipt"], {}).get("completed_time") is None
        ):
            raise ValueError(
                "original runtime revision lost independent original-goal completion proof"
            )
        _revision_goal(checkpoint, mission_id, mission, state)


def inspect(
    database: Path,
    owner: str,
    checksum: str,
    budget: OperationBudget,
    archives: set[str],
    *,
    profile_schema: str = gc.SCHEMA,
) -> dict[str, Any]:
    """Reject every current original claim and return only exact frozen source-evidence identity."""
    heads, _ = catalog_references._controller(database, profile_schema, budget)
    for record in heads.values():
        if record["retirement"] is None and any(
            entry["store_owner_id"] == owner and entry["content_digest"] == checksum
            for entry in record["dependencies"]
        ):
            raise ValueError("active original Catalog/lineage still retains source content")
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
        saved = connection.execute(
            "SELECT schema,event_sequence,checkpoint_json "
            "FROM controller_checkpoint WHERE singleton=1"
        ).fetchone()
        if saved is None or saved[0] not in gc.WRAPPERS:
            raise ValueError("source GC requires original Controller wrapper v52")
        checkpoint = json.loads(saved[2])
        _application(checkpoint, checksum)
        rows = catalog_rows(connection, budget, profile_schema)
        uploads, retirements = upload_history(rows)
        for upload_id, value in uploads.items():
            if (
                value["content_digest"] == checksum
                and value["phase"] in {"prepared", "completed"}
                and (
                    upload_id not in retirements
                    or retirements[upload_id]["store_owner_id"] != owner
                )
            ):
                raise ValueError("original Prepared/Completed upload still retains source content")
        for row in rows:
            payload = json.loads(row["payload_json"])
            for kind in ("SemanticVerifierEvidenceAdmitted", "MissionGoalEvidenceAdmitted"):
                if (
                    kind in payload
                    and payload[kind]["receipt"]["evidence"].get("artifact_digest") == checksum
                ):
                    raise ValueError(
                        "original independent verifier/goal CAS evidence retains source content"
                    )
        originals = archive_references.operations(connection, profile_schema, budget, current=True)
        cleanups = archive_aborts.operations(
            connection, profile_schema, budget, originals, current=True
        )
        for intent, state, _ in originals:
            if intent["request"]["destination"] not in archives:
                raise ValueError(
                    "original Archive writer destination is absent from the complete profile"
                )
            if (
                intent["request"]["source_owner"] != owner
                or state != "pending"
                or not any(value["content_digest"] == checksum for value in intent["inputs"])
            ):
                continue
            aborted = False
            for cleanup, receipt in cleanups:
                if (
                    receipt is None
                    or cleanup["request"]["original_command_id"] != intent["request"]["command_id"]
                ):
                    continue
                root = real_directory(Path(intent["request"]["destination"]))
                prefix = (
                    "archives-v1/"
                    + hashlib.sha256(intent["request"]["authority_id"].encode()).hexdigest()
                    + "/archive-"
                    + hashlib.sha256(intent["request"]["command_id"].encode()).hexdigest()
                )
                name = (
                    prefix
                    + "/abort-"
                    + hashlib.sha256(cleanup["request"]["command_id"].encode()).hexdigest()
                )
                files = {}
                for selected in (name + ".json", name + ".pending", prefix + "/publication.json"):
                    path = root / selected
                    if not path.exists() and not path.is_symlink():
                        continue
                    real_directory(path.parent)
                    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                        raw = stream.read(archive_aborts.MAX_RECORD + 1)
                    files[selected] = {
                        "kind": "content",
                        "byte_size": len(raw),
                        "content_digest": digest(raw),
                    }
                aborted = (
                    archive_aborts.validate_tombstone(root, prefix, cleanup, receipt, files, budget)
                    or aborted
                )
            if not aborted:
                raise ValueError("pending original Archive still retains source content")
        authority = archive_references._metadata(connection, "archive_authority_id")
        if not isinstance(authority, str) or gc.AUTHORITY.fullmatch(authority) is None:
            raise ValueError("original source Controller authority identity is unavailable")
        return {
            "authority_id": authority,
            "event_sequence": saved[1],
            "checkpoint_digest": digest(saved[2].encode()),
            "catalog_digest": digest(canonical(rows)),
        }


def validate_bundle(
    root: Path,
    components: list[dict[str, Any]],
    files: dict[str, dict[str, Any]],
    budget: OperationBudget,
    profile_schema: str,
) -> None:
    """Check live upload/proof bytes and original tombstone conflicts on cold restore."""
    if profile_schema not in {
        gc.SCHEMA,
        "roboguide.recovery-profile/v0.17",
        "roboguide.recovery-profile/v0.18",
        "roboguide.recovery-profile/v0.19",
        "roboguide.recovery-profile/v0.20",
        "roboguide.recovery-profile/v0.21",
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
    }:
        return
    from . import artifact_layout

    owners = {}
    for component in components:
        if component["kind"] != "artifact":
            continue
        directory = root / "stores" / component["component_id"]
        owner = artifact_layout.validate_storage(directory, profile_schema)
        assert owner is not None
        tombstones = (
            gc.validate(directory, owner["owner_id"], budget)[1]
            if owner["schema"] == gc.STORAGE
            else {}
        )
        owners[owner["owner_id"]] = (
            component["component_id"],
            artifact_layout.reference_authorities(directory, owner),
            tombstones,
        )
    for component in components:
        if component["kind"] != "controller":
            continue
        database = root / "stores" / component["component_id"] / component["database"]
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
            rows = catalog_rows(connection, budget, profile_schema)
            uploads, retired = upload_history(rows)
            authority = archive_references._metadata(connection, "archive_authority_id")
        candidates = {owner: value for owner, value in owners.items() if authority in value[1]}
        required = []
        for key, upload in uploads.items():
            if profile_schema in {
                "roboguide.recovery-profile/v0.19",
                "roboguide.recovery-profile/v0.20",
                "roboguide.recovery-profile/v0.21",
                "roboguide.recovery-profile/v0.22",
                "roboguide.recovery-profile/v0.23",
                "roboguide.recovery-profile/v0.24",
                "roboguide.recovery-profile/v0.25",
                "roboguide.recovery-profile/v0.26",
                "roboguide.recovery-profile/v0.27",
            }:
                # Root's exact committed origins own replicated uploads; local projections may lag.
                continue
            if upload["phase"] not in {"prepared", "completed"}:
                continue
            released = retired.get(key)
            for owner, (identifier, _, tombstones) in candidates.items():
                if released is not None and released["store_owner_id"] == owner:
                    continue
                required.append(
                    (
                        identifier,
                        tombstones,
                        upload["content_digest"],
                        upload["byte_size"],
                        "staging/" + key + ".partial" if upload["phase"] == "prepared" else None,
                    )
                )
            if not candidates:
                raise ValueError(
                    "original upload has no declared actual Controller-bound Artifact owner"
                )
        for row in rows:
            payload = json.loads(row["payload_json"])
            for kind in ("SemanticVerifierEvidenceAdmitted", "MissionGoalEvidenceAdmitted"):
                if kind not in payload:
                    continue
                evidence = payload[kind]["receipt"]["evidence"]
                if evidence.get("artifact_digest") is not None:
                    for identifier, _, tombstones in candidates.values():
                        required.append(
                            (
                                identifier,
                                tombstones,
                                evidence["artifact_digest"],
                                evidence["artifact_size_bytes"],
                                None,
                            )
                        )
                    if not candidates:
                        raise ValueError("original verifier/goal CAS owner is unavailable")
        for identifier, tombstones, checksum, size, staging in required:
            if checksum in tombstones:
                raise ValueError(
                    "active original upload/verifier content conflicts with its GC tombstone"
                )
            names = ["objects-v1/sha256/" + checksum[7:9] + "/" + checksum[7:]]
            if staging is not None:
                names.append(staging)
            if not any(
                files.get("stores/" + identifier + "/" + name, {}).get("content_digest") == checksum
                and files["stores/" + identifier + "/" + name]["byte_size"] == size
                for name in names
            ):
                raise ValueError(
                    "active original upload/verifier lacks exact bundled original bytes"
                )
