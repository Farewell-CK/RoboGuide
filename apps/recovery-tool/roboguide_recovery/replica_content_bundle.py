"""Complete cold-bundle Root/parent coverage, original recovery proofs and new backing receipts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import replica_content as content
from .artifact_source_references import upload_history, upload_record
from .snapshot_references import OperationBudget, canonical


def _membership(raw: str, expected: dict[int, str]) -> None:
    """Require all original committed members' exact Root owners in this complete cold profile."""
    value = json.loads(raw)
    nodes = value.get("membership", {}).get("nodes", {}) if isinstance(value, dict) else {}
    actual = {int(key): node.get("content_store_owner") for key, node in nodes.items()}
    if actual != expected or not expected:
        raise ValueError(
            "complete Replica recovery omitted or changed a committed member/Root owner"
        )


def capture_sources(
    components: list[dict[str, Any]], budget: OperationBudget, *, allow_standalone: bool = False
) -> list[dict[str, Any]]:
    """Observe exact source backing only while the caller owns every actual original writer."""
    content.validate_pairing(components, allow_standalone=allow_standalone)
    by_id = {item["component_id"]: item for item in components}
    result = []
    members: dict[int, str] = {}
    for component in components:
        if component["kind"] != "replica-content":
            continue
        root = Path(component["root"])
        original = content.inventory(root, component, budget, live=True)
        identity = original["identity"]
        if identity["member"] in members or identity["store_owner"] in members.values():
            raise ValueError("original Replica content source repeats member/Root ownership")
        members[identity["member"]] = identity["store_owner"]
        parent = by_id[component["depends_on"][0]]
        actual = (Path(parent["root"]) / parent["database"]).stat()
        backing = content._backing(root) | {
            "parent_device": actual.st_dev,
            "parent_inode": actual.st_ino,
        }
        if original["history"] and json.loads(original["history"][-1])["backing"] != backing:
            raise ValueError("original restored Root is paired with another actual parent")
        result.append(
            {
                "component_id": component["component_id"],
                "parent_id": parent["component_id"],
                "source_backing": backing,
            }
        )
    for value in result:
        parent = by_id[value["parent_id"]]
        path = Path(parent["root"]) / parent["database"]
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
            row = sql.execute("SELECT value FROM replica_v1_meta WHERE key='membership'").fetchone()
            _membership(row[0] if row is not None else "null", members)
    return sorted(result, key=lambda value: value["component_id"])


def _source_map(sources: Any, components: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Require complete closed pinned source backing, including genuinely empty original Roots."""
    if not isinstance(sources, list) or len(sources) > 1024:
        raise ValueError("original Replica content source coverage is absent or unbounded")
    result = {}
    for value in sources:
        value = content._closed(value, {"component_id", "parent_id", "source_backing"})
        backing = content._closed(
            value["source_backing"],
            {
                "root_device",
                "root_inode",
                "database_device",
                "database_inode",
                "parent_device",
                "parent_inode",
            },
        )
        if (
            not isinstance(value["component_id"], str)
            or value["component_id"] in result
            or any(type(part) is not int or not 0 <= part < 2**64 for part in backing.values())
            or any(backing[key] == 0 for key in ("root_inode", "database_inode", "parent_inode"))
        ):
            raise ValueError("original Replica content source backing conflicts")
        result[value["component_id"]] = value
    roots = {
        value["component_id"]: value for value in components if value["kind"] == "replica-content"
    }
    if set(result) != set(roots) or any(
        result[key]["parent_id"] != roots[key]["depends_on"][0] for key in roots
    ):
        raise ValueError("original Replica content source/root/parent coverage differs")
    return result


def _required(original: dict[str, Any]) -> set[tuple[str, str, int]]:
    """Keep independently active pins; an unfinished receive never invents finalized bytes."""
    tables = original["tables"]
    result = set()
    for value in tables["content_references"].values():
        reference = value["reference"]
        if not value["active"]:
            continue
        if reference["kind"] in {"stream", "receive"}:
            table = "content_streams" if reference["kind"] == "stream" else "content_operations"
            row = tables[table][reference["reference_id"].split(":", 1)[1]]
            if row["receipt"] is None:
                continue
        if reference["kind"] == "snapshot-receive":
            row = tables["content_snapshot_receives"][reference["reference_id"].split(":")[1]]
            if row["ready"] is None:
                continue
        result.add(content._content(reference["content"]))
    return result


def _require_bytes(
    identifier: str,
    required: set[tuple[str, str, int]],
    files: dict[str, dict[str, Any]],
    objects: dict[str, Any],
) -> None:
    """Require exact qualified current bytes in this same Root, never another owner or member."""
    for owner, checksum, size in required:
        key = {"source_owner": owner, "content_digest": checksum, "byte_size": size}
        receipt = objects.get(content._digest(key))
        if receipt is None or receipt["content"] != key:
            raise ValueError("active Replica input lost its exact original object receipt")
        name = "stores/" + identifier + "/content-v1/" + owner[15:] + "/" + checksum[7:]
        row = files.get(name)
        if row is None or (row["content_digest"], row["byte_size"]) != (checksum, size):
            raise ValueError("active Replica content reference lacks exact same-Root bundled bytes")


def _parent(
    path: Path,
    original: dict[str, Any],
    source: dict[str, Any],
    budget: OperationBudget,
    members: dict[int, str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Validate original parent8 heads, same-member producers and ingress rows."""
    identity, tables = original["identity"], original["tables"]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024 * 1024)
        metadata = dict(connection.execute("SELECT key,value FROM replica_v1_meta LIMIT 4097"))
        _membership(metadata.get("membership", "null"), members)
        if (
            len(metadata) > 4096
            or json.loads(metadata.get("content_root_identity", "null")) != identity
            or json.loads(metadata.get("identity", "null"))
            not in (
                [identity["profile_identity"], "roboguide.controller-checkpoint/v53"],
                [identity["profile_identity"], "roboguide.controller-checkpoint/v54"],
                [identity["profile_identity"], "roboguide.controller-checkpoint/v55"],
                [identity["profile_identity"], "roboguide.controller-checkpoint/v56"],
            )
            or json.loads(metadata.get("network_content_membership_schema", "null"))
            != "roboguide.replica-storage/v0.7"
            or json.loads(metadata.get("controller_content_ingress_schema", "null"))
            != "roboguide.controller-content-ingress/v0.1"
            or "content_state" not in metadata
            or "content_producer_heads" not in metadata
        ):
            raise ValueError("original parent8/Root/member/profile or mandatory marker differs")
        heads = content._closed(
            json.loads(metadata["content_producer_heads"]), {"schema", "view", "snapshot"}
        )
        state = json.loads(metadata["content_state"])
        from .replica_initialization import validate_content, validate_parent

        parent_version = connection.execute("PRAGMA user_version").fetchone()[0]
        from .application_upgrades import validate_parent as validate_upgrades

        validate_upgrades(
            connection,
            content.UPGRADE_PROFILE if parent_version == 10 else content.INITIALIZATION_PROFILE,
        )
        base_version = (
            json.loads(metadata.get("application_upgrade_base_parent_v1", "null"))
            if parent_version == 10
            else parent_version
        )
        if base_version == 9:
            validate_parent(connection)
        else:
            validate_content(state, None)
        if heads["schema"] != "roboguide.content-producer-heads/v0.1" or (
            heads["view"] is None
        ) != (state is None):
            raise ValueError("original parent8 mandatory current producer heads differ")
        expected_parents = {
            (source["source_backing"]["parent_device"], source["source_backing"]["parent_inode"])
        }
        for encoded in original["history"]:
            binding = json.loads(encoded)
            expected_parents.add((binding["source_parent_device"], binding["source_parent_inode"]))
        for producer in tables["content_producers"].values():
            intent = producer["intent"]
            if (intent["sql_device"], intent["sql_inode"]) not in expected_parents:
                raise ValueError("original Root producer belongs to another actual parent SQL")
        for name, kinds in (
            ("view", {"view"}),
            ("snapshot", {"snapshot-build", "snapshot-install"}),
        ):
            head = heads[name]
            if head is None:
                if (
                    name == "snapshot"
                    and connection.execute("SELECT 1 FROM replica_v1_snapshot LIMIT 1").fetchone()
                ):
                    raise ValueError("original parent Snapshot lost its independent producer head")
                continue
            head = content._closed(head, {"operation_id", "record_digest"})
            producer = tables["content_producers"].get(content._digest(head["operation_id"]))
            if (
                producer is None
                or producer["intent"]["source"]["kind"] not in kinds
                or producer["intent"]["source"]["parent_record_digest"] != head["record_digest"]
                or producer["resolution"] is not None
            ):
                raise ValueError("original parent head lost its exact current Root producer")
            if name == "view":
                actual = hashlib.sha256(metadata["content_state"].encode()).hexdigest()
            else:
                row = connection.execute(
                    "SELECT meta,data FROM replica_v1_snapshot WHERE singleton=1"
                ).fetchone()
                if row is None:
                    raise ValueError("original parent Snapshot head lost its bytes")
                actual = hashlib.sha256(
                    json.dumps(
                        [json.loads(row[0]), hashlib.sha256(row[1]).hexdigest()],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            if head["record_digest"] != actual:
                raise ValueError("original parent producer head changed its exact source record")
        ids = json.loads(metadata.get("controller_content_ingress_ids", "null"))
        if not isinstance(ids, list) or len(ids) > 1024 or ids != sorted(set(ids)):
            raise ValueError("original parent8 ingress inventory is missing or unbounded")
        actual_ids = []
        for key, checksum, raw in connection.execute(
            "SELECT * FROM controller_content_ingress LIMIT 1025"
        ):
            budget.check()
            if len(raw.encode()) > 256 * 1024 * 1024:
                raise ValueError("original parent8 ingress record exceeds its bound")
            value = content._closed(json.loads(raw), {"intent", "request_digest", "receipt"})
            if (
                value["request_digest"] != checksum
                or hashlib.sha256(
                    json.dumps(value["intent"], ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                != checksum
                or value["intent"]["command_id"] != key
            ):
                raise ValueError("original parent8 ingress immutable request digest differs")
            content._budget(value["intent"]["budget"])
            actual_ids.append(key)
        if sorted(actual_ids) != ids:
            raise ValueError("original parent8 ingress lost mandatory original history")
        rows: list[dict[str, Any]] = []
        if state is not None:
            candidate = state["candidate"]
            if candidate["profile_identity"] != identity["profile_identity"]:
                raise ValueError("original current content view changed its profile")
            recovery = candidate["recovery"]
            if recovery is not None:
                rows.extend(recovery["catalog_archive_events"] or [])
        total = sum(len(canonical(row)) for row in rows)
        for index, (sequence, event, raw, checksum) in enumerate(
            connection.execute(
                "SELECT sequence,event_id,data,digest FROM replica_v1_events "
                "ORDER BY sequence LIMIT 32769"
            )
        ):
            budget.check()
            total += len(raw.encode())
            if (
                index >= 32768
                or total > 64 * 1024 * 1024
                or hashlib.sha256(raw.encode()).hexdigest() != checksum
            ):
                raise ValueError("original parent event evidence exceeds bounds or changed digest")
            value = json.loads(raw)
            if value["sequence"] != sequence or value["event_id"] != event:
                raise ValueError("original parent event SQL/envelope identity differs")
            rows.append(value)
    if len(rows) > 32768 or len({row["sequence"] for row in rows}) != len(rows):
        raise ValueError("original parent protected/tail event coverage conflicts")
    return state, sorted(rows, key=lambda row: row["sequence"])


def _recoveries(rows: list[dict[str, Any]], roots: list[dict[str, Any]]) -> None:
    """Verify original recovery transitions and independent exact Root closes."""
    uploads, _ = upload_history(rows)
    history: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if "ArtifactUploadRecoveryTransition" not in payload:
            continue
        if row["payload_schema"] != "domain.EventPayload.json/v32":
            raise ValueError("original Prepared recovery event lacks its explicit codec")
        record = content._closed(
            payload["ArtifactUploadRecoveryTransition"]["record"],
            {
                "schema",
                "command_id",
                "budget_ms",
                "reader_id",
                "upload",
                "source_owner_id",
                "replacement_admission",
                "destination_owner_id",
                "destination_device",
                "destination_inode",
                "controller_process_json",
                "kernel_budget_json",
                "canonical_grant_json",
                "completion",
            },
        )
        upload_record(record["upload"])
        upload_record(record["replacement_admission"])
        process = json.loads(record["controller_process_json"])
        clock = json.loads(record["kernel_budget_json"])
        content._budget(clock)
        source, replacement = record["upload"], record["replacement_admission"]
        if (
            record["schema"] != "roboguide.artifact-upload-recovery/v0.1"
            or source["phase"] != "prepared"
            or replacement["phase"] != "started"
            or source["upload_id"] == replacement["upload_id"]
            or any(source[field] != replacement[field] for field in ("principal_id", "tenant_id"))
            or clock["duration_ms"] != record["budget_ms"]
            or any(clock[key] != process[key] for key in ("boot_id", "pid", "start_ticks"))
            or record["source_owner_id"] == record["destination_owner_id"]
            or record["destination_inode"] <= 0
        ):
            raise ValueError("original Prepared recovery identity/process/budget differs")
        previous = history.get(record["command_id"])
        if previous is None:
            if record["completion"] is not None or len(history) >= 1024:
                raise ValueError("original Prepared recovery intent is absent or unbounded")
        elif (record | {"completion": previous["completion"]}) != previous or (
            previous["completion"] is not None and record != previous
        ):
            raise ValueError("original Prepared recovery changed or repeated immutable outcome")
        completion = record["completion"]
        if completion is not None:
            completed = completion["replacement"]
            started = completed | {
                "phase": "started",
                "content_digest": None,
                "byte_size": None,
                "prepared_at_ms": None,
                "completed_at_ms": None,
            }
            receipt = json.loads(completion["reader_receipt_json"])
            if (
                started != replacement
                or uploads.get(completed["upload_id"]) != completed
                or completed["phase"] != "completed"
                or any(
                    completed[field] != source[field] for field in ("content_digest", "byte_size")
                )
                or receipt["reader_id"] != record["reader_id"]
                or not receipt["complete"]
                or receipt.get("retention_only", False)
                or receipt["bytes_read"] != source["byte_size"]
                or receipt["observed_digest"] != source["content_digest"]
                or receipt["content"]
                != {
                    "source_owner": record["source_owner_id"],
                    "content_digest": source["content_digest"],
                    "byte_size": source["byte_size"],
                }
            ):
                raise ValueError(
                    "original Prepared recovery completion lacks exact replacement/bytes"
                )
            matches = [
                value
                for root in roots
                for value in root["tables"]["content_readers"].values()
                if value["receipt"] == receipt
                and value["intent"]["authority_digest"] == completion["reader_authority_digest"]
            ]
            if len(matches) != 1:
                raise ValueError(
                    "original Prepared recovery lacks its independent exact Root SQL close"
                )
        history[record["command_id"]] = record


def projection_authority(
    root: Path,
    component: dict[str, Any],
    components: list[dict[str, Any]],
    authority: str,
    budget: OperationBudget,
) -> str:
    """Match a local projection to its paired committed Raft authority/event prefix."""
    from .artifact_source_references import catalog_rows

    parents = [
        value
        for value in components
        if value["kind"] == "replica" and component["component_id"] in value["depends_on"]
    ]
    if len(parents) != 1:
        raise ValueError("replicated Controller projection lacks its exact original parent")
    parent = parents[0]
    path = root / "stores" / parent["component_id"] / parent["database"]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as sql:
        metadata = dict(sql.execute("SELECT key,value FROM replica_v1_meta LIMIT 4097"))
        state = json.loads(metadata.get("content_state", "null"))
        if state is None or state["candidate"]["recovery"] is None:
            raise ValueError("replicated projection lacks original complete recovery authority")
        candidate = state["candidate"]
        recovery = candidate["recovery"]
        if recovery["metadata"].get("archive_authority_id") != authority:
            raise ValueError("replicated projection changed its original Controller authority")
        rows = list(recovery["catalog_archive_events"] or [])
        for (raw,) in sql.execute(
            "SELECT data FROM replica_v1_events ORDER BY sequence LIMIT 32769"
        ):
            budget.check()
            rows.append(json.loads(raw))
    by_sequence = {value["sequence"]: value for value in rows}
    if len(rows) > 32768 or len(by_sequence) != len(rows):
        raise ValueError("replicated projection's original parent event coverage differs")
    path = root / "stores" / component["component_id"] / component["database"]
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as sql:
        for value in catalog_rows(sql, budget, content.PROFILE):
            if by_sequence.get(value["sequence"]) != value:
                raise ValueError("local Controller projection changed its paired committed prefix")
    return str(candidate["profile_identity"])


def _upload_inputs(rows: list[dict[str, Any]], state: dict[str, Any] | None) -> None:
    """Require current uploads' exact original qualified inputs across owners."""
    uploads, retired = upload_history(rows)
    events = {}
    for row in rows:
        payload = json.loads(row["payload_json"])
        if "ArtifactUploadTransition" in payload:
            events[payload["ArtifactUploadTransition"]["record"]["upload_id"]] = row["event_id"]
    for key, upload in uploads.items():
        if upload["phase"] not in {"prepared", "completed"} or key in retired:
            continue
        candidate = state["candidate"] if state is not None else None
        if candidate is None:
            raise ValueError("active replicated upload has no original complete content view")
        origin = candidate["origins"].get(events[key])
        if (
            origin is None
            or origin["content_digest"] != upload["content_digest"]
            or origin["byte_size"] != upload["byte_size"]
            or origin not in candidate["inputs"]
        ):
            raise ValueError("active replicated upload lost its exact original qualified input")


def validate_bundle(
    root: Path,
    components: list[dict[str, Any]],
    files: dict[str, dict[str, Any]],
    budget: OperationBudget,
    schema: str,
    sources: Any,
) -> None:
    """Validate paired original stores and active qualified bytes before publication."""
    if schema not in content.PROFILES:
        if sources is not None:
            raise ValueError("old recovery profile contains hidden Replica content authority")
        return
    standalone = schema in {content.CAPACITY_PROFILE, content.UPGRADE_PROFILE} and not any(
        item["kind"] in {"replica", "replica-content"} for item in components
    )
    content.validate_pairing(components, allow_standalone=standalone)
    source_map = _source_map(sources, components)
    if standalone:
        from .capacity_recovery import validate_standalone

        for component in components:
            if component["kind"] != "controller":
                continue
            path = root / "stores" / component["component_id"] / component["database"]
            budget.check()
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as sql:
                validate_standalone(sql)
        return
    by_id = {item["component_id"]: item for item in components}
    originals, states = [], []
    expected_members = {}
    for component in components:
        if component["kind"] == "replica-content":
            identity = content._identity(
                json.loads(
                    content._raw(
                        root / "stores" / component["component_id"] / ".retention-owner.json"
                    )
                )
            )
            if identity["member"] in expected_members:
                raise ValueError("original Replica content repeats a committed member")
            expected_members[identity["member"]] = identity["store_owner"]
    owners, members, profiles = set(), set(), set()
    for component in components:
        if component["kind"] != "replica-content":
            continue
        identifier = component["component_id"]
        original = content.inventory(root / "stores" / identifier, component, budget)
        source = source_map[identifier]
        if original["history"]:
            backing = json.loads(original["history"][-1])["backing"]
        else:
            backing = {
                key: original["identity"][key]
                for key in content._backing(root / "stores" / identifier)
            }
        if any(
            backing[key] != value
            for key, value in source["source_backing"].items()
            if key in backing
        ):
            raise ValueError("original Replica source provenance conflicts with its pinned bundle")
        parent = by_id[source["parent_id"]]
        state, rows = _parent(
            root / "stores" / parent["component_id"] / parent["database"],
            original,
            source,
            budget,
            expected_members,
        )
        _upload_inputs(rows, state)
        required = _required(original)
        if state is not None:
            required.update(content._content(value) for value in state["candidate"]["inputs"])
        _require_bytes(identifier, required, files, original["tables"]["content_objects"])
        identity = original["identity"]
        if identity["store_owner"] in owners or identity["member"] in members:
            raise ValueError("original Replica content member/Root ownership is repeated")
        owners.add(identity["store_owner"])
        members.add(identity["member"])
        profiles.add(identity["profile_identity"])
        originals.append(original)
        states.append(rows)
    if len(profiles) != 1:
        raise ValueError("complete Replica content bundle mixes original clusters/profiles")
    for rows in states:
        _recoveries(rows, originals)


def bind_restored(
    root: Path,
    manifest: dict[str, Any],
    manifest_digest: str,
    command_id: str,
    budget: OperationBudget,
) -> list[dict[str, Any]]:
    """Add only explicit new backing history after complete source verification and copying."""
    if manifest["profile"]["schema"] not in content.PROFILES:
        return []
    components = manifest["profile"]["components"]
    sources = _source_map(manifest["replica_content_sources"], components)
    by_id = {item["component_id"]: item for item in components}
    files = {value["path"]: value for value in manifest["files"]}
    result = []
    for component in components:
        if component["kind"] != "replica-content":
            continue
        budget.check()
        identifier = component["component_id"]
        directory = root / "stores" / identifier
        original = content.inventory(directory, component, budget)
        history = list(original["history"])
        if len(history) >= 64:
            raise ValueError("original Replica restore history reached its fixed bound")
        source = sources[identifier]
        parent = by_id[source["parent_id"]]
        parent_sql = (root / "stores" / parent["component_id"] / parent["database"]).stat()
        binding = {
            "schema": "roboguide.replica-content-restore-binding/v0.4"
            if original["version"] == 8
            else "roboguide.replica-content-restore-binding/v0.3"
            if original["version"] == 7
            else "roboguide.replica-content-restore-binding/v0.2"
            if original["version"] == 6
            else "roboguide.replica-content-restore-binding/v0.1",
            "command_id": command_id,
            "manifest_digest": manifest_digest,
            "source_database_digest": files["stores/" + identifier + "/content.sqlite3"][
                "content_digest"
            ],
            "source_root_version": original["version"],
            "source_parent_device": source["source_backing"]["parent_device"],
            "source_parent_inode": source["source_backing"]["parent_inode"],
            "original_identity": original["identity"],
            "ordinal": len(history) + 1,
            "previous_binding_digest": content._sha(history[-1]) if history else None,
            "backing": content._backing(directory)
            | {
                "parent_device": parent_sql.st_dev,
                "parent_inode": parent_sql.st_ino,
            },
        }
        raw = canonical(binding).decode()
        history.append(raw)
        content.bindings(json.dumps(history), original["identity"])
        with closing(sqlite3.connect(directory / "content.sqlite3")) as connection:
            with connection:
                connection.execute(
                    "UPDATE metadata SET value=? WHERE key='layout'",
                    (
                        f"roboguide.replica-content-layout/v0.{original['version']}"
                        if original["version"] >= 6
                        else content.LAYOUT5,
                    ),
                )
                connection.execute(
                    "INSERT INTO metadata(key,value) VALUES ('restore_bindings',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(history),),
                )
                if original["version"] >= 6:
                    connection.execute(
                        "UPDATE metadata SET value='restored' WHERE key='backing_mode'"
                    )
                connection.execute(
                    "PRAGMA user_version="
                    + str(original["version"] if original["version"] >= 6 else 5)
                )
        path = directory / content.BINDING_FILE
        flags = (
            os.O_WRONLY | os.O_NOFOLLOW | (os.O_TRUNC if history[:-1] else os.O_CREAT | os.O_EXCL)
        )
        with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
            handle.write(raw.encode())
            handle.flush()
            os.fsync(handle.fileno())
        with (directory / "content.sqlite3").open("rb") as handle:
            os.fsync(handle.fileno())
        restored_component = component | (
            {
                "storage_schema": f"roboguide.replica-content-layout/v0.{original['version']}",
                "restore_binding_digest": content._sha(raw),
            }
            if original["version"] >= 6
            else {"storage_schema": content.LAYOUT5}
        )
        opened = content.inventory(directory, restored_component, budget, live=True)
        if opened["history"] != history or opened["tables"] != original["tables"]:
            raise ValueError("restoration changed original Replica operational history")
        result.append(
            {"component_id": identifier, "binding_digest": content._sha(raw), "binding": binding}
        )
    return result


def validate_restored(
    bundle: Path,
    target: Path,
    manifest: dict[str, Any],
    manifest_digest: str,
    command_id: str,
    receipts: Any,
    budget: OperationBudget,
) -> set[str]:
    """Compare original operational rows and exact current bindings on read-only retry."""
    if manifest["profile"]["schema"] not in content.PROFILES:
        if receipts:
            raise ValueError("old restore receipt contains hidden Replica backing authority")
        return set()
    roots = [
        value for value in manifest["profile"]["components"] if value["kind"] == "replica-content"
    ]
    if not isinstance(receipts, list) or len(receipts) != len(roots):
        raise ValueError("restored Replica binding coverage is missing")
    by_id = {value["component_id"]: value for value in receipts}
    if set(by_id) != {value["component_id"] for value in roots}:
        raise ValueError("restored Replica binding coverage differs")
    sources = _source_map(manifest["replica_content_sources"], manifest["profile"]["components"])
    components = {value["component_id"]: value for value in manifest["profile"]["components"]}
    files = {value["path"]: value for value in manifest["files"]}
    adjusted = set()
    for component in roots:
        identifier = component["component_id"]
        source = content.inventory(bundle / "stores" / identifier, component, budget)
        row = content._closed(by_id[identifier], {"component_id", "binding_digest", "binding"})
        restored_component = component | (
            {
                "storage_schema": f"roboguide.replica-content-layout/v0.{source['version']}",
                "restore_binding_digest": row["binding_digest"],
            }
            if source["version"] >= 6
            else {"storage_schema": content.LAYOUT5}
        )
        restored = content.inventory(
            target / "stores" / identifier, restored_component, budget, live=True
        )
        raw = restored["history"][-1]
        binding = json.loads(raw)
        parent = components[sources[identifier]["parent_id"]]
        actual_parent = (target / "stores" / parent["component_id"] / parent["database"]).stat()
        if (
            row["binding_digest"] != content._sha(raw)
            or row["binding"] != binding
            or binding["command_id"] != command_id
            or binding["manifest_digest"] != manifest_digest
            or binding["source_database_digest"]
            != files["stores/" + identifier + "/content.sqlite3"]["content_digest"]
            or binding["source_parent_device"]
            != sources[identifier]["source_backing"]["parent_device"]
            or binding["source_parent_inode"]
            != sources[identifier]["source_backing"]["parent_inode"]
            or (binding["backing"]["parent_device"], binding["backing"]["parent_inode"])
            != (actual_parent.st_dev, actual_parent.st_ino)
            or restored["identity"] != source["identity"]
            or restored["tables"] != source["tables"]
            or restored["history"][:-1] != source["history"]
        ):
            raise ValueError(
                "restored Replica original history/backing changed; retry is read-only"
            )
        adjusted.add("stores/" + identifier + "/content.sqlite3")
        if source["history"]:
            adjusted.add("stores/" + identifier + "/" + content.BINDING_FILE)
    return adjusted
