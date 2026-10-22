"""Validate explicit storage topology and acquire the actual original process writer leases."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from itertools import pairwise
from pathlib import Path
from typing import Any

IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
KINDS = {"controller", "replica", "node", "mission", "runtime-mi", "artifact", "memory", "files"}
MAX_PROFILE_BYTES = 1024 * 1024
MAX_COMPONENTS = 1024


def relative(value: object) -> str:
    """Require a bounded POSIX relative path without symlink or traversal normalization."""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode()) > 4096
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("recovery path must be a bounded, unambiguous relative POSIX path")
    return value


def real_directory(path: Path) -> Path:
    """Reject symlinks at every existing ancestor and require a real configured directory."""
    path = path.absolute()
    for parent in (*reversed(path.parents), path):
        metadata = parent.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("recovery directory and its ancestors must be real directories")
    return path


def validate(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate complete configured components and explicit content dependencies without Nodes."""
    expected = {
        "schema",
        "profile_id",
        "revision",
        "max_bytes",
        "max_files",
        "budget_seconds",
        "components",
    }
    if profile.get("schema") in {
        "roboguide.recovery-profile/v0.2",
        "roboguide.recovery-profile/v0.3",
        "roboguide.recovery-profile/v0.4",
        "roboguide.recovery-profile/v0.5",
        "roboguide.recovery-profile/v0.6",
        "roboguide.recovery-profile/v0.7",
        "roboguide.recovery-profile/v0.8",
        "roboguide.recovery-profile/v0.9",
        "roboguide.recovery-profile/v0.10",
        "roboguide.recovery-profile/v0.11",
    }:
        expected.add("artifact_replica_sets")
    if set(profile) != expected or profile["schema"] not in {
        "roboguide.recovery-profile/v0.1",
        "roboguide.recovery-profile/v0.2",
        "roboguide.recovery-profile/v0.3",
        "roboguide.recovery-profile/v0.4",
        "roboguide.recovery-profile/v0.5",
        "roboguide.recovery-profile/v0.6",
        "roboguide.recovery-profile/v0.7",
        "roboguide.recovery-profile/v0.8",
        "roboguide.recovery-profile/v0.9",
        "roboguide.recovery-profile/v0.10",
        "roboguide.recovery-profile/v0.11",
    }:
        raise ValueError("recovery profile is not closed or versioned")
    if (
        not isinstance(profile["profile_id"], str)
        or IDENTIFIER.fullmatch(profile["profile_id"]) is None
    ):
        raise ValueError("recovery profile identity is invalid")
    for name, maximum in (
        ("revision", 2**63 - 1),
        ("max_bytes", 2**50),
        ("max_files", 1_000_000),
        ("budget_seconds", 3600),
    ):
        if type(profile[name]) is not int or not 0 < profile[name] <= maximum:
            raise ValueError("recovery profile bound is invalid: " + name)
    components = profile["components"]
    if not isinstance(components, list) or not 1 <= len(components) <= MAX_COMPONENTS:
        raise ValueError("recovery requires a bounded explicit storage component list")
    identifiers: set[str] = set()
    managed_paths: list[Path] = []
    for item in components:
        if not isinstance(item, dict) or set(item) != {
            "component_id",
            "kind",
            "root",
            "database",
            "trees",
            "depends_on",
        }:
            raise ValueError("recovery storage component is not closed")
        identifier = item["component_id"]
        if (
            not isinstance(identifier, str)
            or IDENTIFIER.fullmatch(identifier) is None
            or identifier in identifiers
            or not isinstance(item["kind"], str)
            or item["kind"] not in KINDS
        ):
            raise ValueError("recovery component identity/kind is invalid or repeated")
        identifiers.add(identifier)
        if item["kind"] == "replica" and profile["schema"] not in {
            "roboguide.recovery-profile/v0.2",
            "roboguide.recovery-profile/v0.3",
            "roboguide.recovery-profile/v0.4",
            "roboguide.recovery-profile/v0.5",
            "roboguide.recovery-profile/v0.6",
            "roboguide.recovery-profile/v0.7",
            "roboguide.recovery-profile/v0.8",
            "roboguide.recovery-profile/v0.9",
            "roboguide.recovery-profile/v0.10",
            "roboguide.recovery-profile/v0.11",
        }:
            raise ValueError("replica supervisor leases require explicit recovery profile v0.2")
        if item["kind"] == "runtime-mi" and profile["schema"] not in {
            "roboguide.recovery-profile/v0.3",
            "roboguide.recovery-profile/v0.4",
            "roboguide.recovery-profile/v0.5",
            "roboguide.recovery-profile/v0.6",
            "roboguide.recovery-profile/v0.7",
            "roboguide.recovery-profile/v0.8",
            "roboguide.recovery-profile/v0.9",
            "roboguide.recovery-profile/v0.10",
            "roboguide.recovery-profile/v0.11",
        }:
            raise ValueError("runtime MI leases require explicit recovery profile v0.3")
        if (
            not isinstance(item["root"], str)
            or not Path(item["root"]).is_absolute()
            or len(item["root"].encode()) > 4096
            or any(ord(character) < 32 for character in item["root"])
        ):
            raise ValueError("recovery source root must be an explicit absolute path")
        root = Path(item["root"])
        if ".." in root.parts:
            raise ValueError("recovery source root contains ambiguous parent traversal")
        if item["kind"] in {"controller", "replica", "node", "mission", "runtime-mi"}:
            relative(item["database"])
            if "/" in item["database"]:
                raise ValueError("authority database must be directly inside its configured root")
        elif item["database"] is not None:
            raise ValueError("this storage kind cannot select an arbitrary authority database")
        for name in ("trees", "depends_on"):
            if not isinstance(item[name], list) or len(item[name]) > MAX_COMPONENTS:
                raise ValueError("recovery component list is unavailable or unbounded")
            if any(not isinstance(value, str) for value in item[name]):
                raise ValueError("recovery component list must contain text")
            if len(set(item[name])) != len(item[name]):
                raise ValueError("recovery component list repeats identities")
        selected = [relative(value) for value in item["trees"]]
        for index, tree in enumerate(selected):
            if any(
                Path(tree).is_relative_to(Path(other)) or Path(other).is_relative_to(Path(tree))
                for other in selected[:index]
            ):
                raise ValueError("recovery content tree selections overlap")
        if item["kind"] in {"artifact", "memory"} and selected:
            raise ValueError("Artifact/Memory managed directories are selected by their exact kind")
        if item["kind"] == "files" and not item["depends_on"]:
            raise ValueError("file/archive root requires actual writer dependencies")
        if item["database"] is not None and any(
            Path(item["database"]).is_relative_to(Path(tree)) for tree in selected
        ):
            raise ValueError("authority database cannot be duplicated inside a content tree")
        databases, trees, locks = layout(item, profile_schema=profile["schema"])
        from .artifact_layout import metadata_files

        selected_paths = {
            root / path
            for path in databases + trees + locks + metadata_files(item, profile["schema"])
        }
        managed_paths.extend(selected_paths)
    ordered_paths = sorted(path.parts for path in managed_paths)
    if any(current[: len(previous)] == previous for previous, current in pairwise(ordered_paths)):
        raise ValueError("recovery managed storage paths overlap")
    for item in components:
        if any(
            value not in identifiers or value == item["component_id"]
            for value in item["depends_on"]
        ):
            raise ValueError("recovery dependency is missing or refers to itself")
    pending = {item["component_id"]: set(item["depends_on"]) for item in components}
    visited: set[str] = set()
    while pending:
        ready = {
            identifier for identifier, dependencies in pending.items() if dependencies <= visited
        }
        if not ready:
            raise ValueError("recovery writer dependency graph contains a cycle")
        visited.update(ready)
        for identifier in ready:
            del pending[identifier]
    replica_sets = profile.get("artifact_replica_sets", [])
    if not isinstance(replica_sets, list) or len(replica_sets) > 64:
        raise ValueError("Artifact replica sets must be a bounded explicit list")
    selected_artifacts: set[str] = set()
    by_id = {component["component_id"]: component for component in components}
    for group in replica_sets:
        if (
            not isinstance(group, list)
            or not 3 <= len(group) <= 9
            or any(not isinstance(identity, str) for identity in group)
        ):
            raise ValueError("Artifact replica set must identify 3-9 explicit components")
        for identity in group:
            if (
                identity in selected_artifacts
                or identity not in by_id
                or by_id[identity]["kind"] != "artifact"
            ):
                raise ValueError("Artifact replica set repeats or names a non-Artifact component")
            selected_artifacts.add(identity)
            owners = [
                component
                for component in components
                if component["kind"] == "replica" and identity in component["depends_on"]
            ]
            if len(owners) != 1:
                raise ValueError(
                    "each Artifact replica requires one explicit original replica writer dependency"
                )
    return sorted(components, key=lambda value: str(value["component_id"]))


def layout(
    component: dict[str, Any], *, profile_schema: str = "roboguide.recovery-profile/v0.1"
) -> tuple[list[str], list[str], list[str]]:
    """Derive maintained databases, content roots and actual lease paths for one component kind."""
    kind, database = component["kind"], component["database"]
    if kind == "memory":
        return (
            [
                "memory.sqlite3",
                "memory-use.sqlite3",
                "file-jobs.sqlite3",
                "active-map/active-map.sqlite3",
            ],
            ["content"],
            ["writer.lock"],
        )
    if kind == "artifact":
        blobs = (
            "objects-v1"
            if profile_schema
            in {
                "roboguide.recovery-profile/v0.9",
                "roboguide.recovery-profile/v0.10",
                "roboguide.recovery-profile/v0.11",
            }
            else "blobs"
        )
        return (
            [],
            [blobs, "staging"]
            + (["maintenance-v1"] if profile_schema == "roboguide.recovery-profile/v0.11" else []),
            [".writer.lock"],
        )
    if kind == "files":
        return [], list(component["trees"]) or ["."], []
    if kind == "runtime-mi":
        return [database], list(component["trees"]), [database + ".worker.lock", database]
    lock = database + (".service.lock" if kind == "mission" else ".writer.lock")
    if kind == "replica":
        return [database], list(component["trees"]), [lock, "supervisor.writer.lock"]
    return [database], list(component["trees"]), [lock, database] if kind == "node" else [lock]


@contextmanager
def acquire_all(
    components: list[dict[str, Any]], *, profile_schema: str = "roboguide.recovery-profile/v0.1"
) -> Iterator[None]:
    """Hold every real process lease before scanning any data; active or unknown owners refuse."""
    with ExitStack() as stack:
        paths: list[Path] = []
        for component in components:
            root = real_directory(Path(component["root"]))
            databases, _, locks = layout(component, profile_schema=profile_schema)
            paths.extend(root / name for name in locks)
            if component["kind"] == "memory" and profile_schema in {
                "roboguide.recovery-profile/v0.4",
                "roboguide.recovery-profile/v0.5",
                "roboguide.recovery-profile/v0.6",
                "roboguide.recovery-profile/v0.7",
                "roboguide.recovery-profile/v0.8",
                "roboguide.recovery-profile/v0.9",
                "roboguide.recovery-profile/v0.10",
                "roboguide.recovery-profile/v0.11",
            }:
                paths.extend(root / name for name in [".memory-use.lock", *databases])
            if component["kind"] == "replica" and profile_schema in {
                "roboguide.recovery-profile/v0.5",
                "roboguide.recovery-profile/v0.6",
                "roboguide.recovery-profile/v0.7",
                "roboguide.recovery-profile/v0.8",
                "roboguide.recovery-profile/v0.9",
                "roboguide.recovery-profile/v0.10",
                "roboguide.recovery-profile/v0.11",
            }:
                paths.append(root / component["database"])
        for path in sorted(paths):
            real_directory(path.parent)
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("original recovery writer lease is not a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("original service writer is still active: " + str(path)) from error
            stack.callback(fcntl.flock, descriptor, fcntl.LOCK_UN)
        for component in components:
            if component["kind"] == "artifact":
                from .artifact_layout import validate_storage

                validate_storage(Path(component["root"]), profile_schema)
            if component["kind"] == "memory":
                path = Path(component["root"]) / "memory.sqlite3"
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    validate_memory_storage(connection, profile_schema)
            if component["kind"] == "replica":
                path = Path(component["root"]) / component["database"]
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    validate_replica_storage(connection, profile_schema)
            if component["kind"] == "runtime-mi":
                path = Path(component["root"]) / component["database"]
                try:
                    with closing(
                        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                    ) as connection:
                        marker = connection.execute(
                            "SELECT schema,instance_id FROM format"
                        ).fetchall()
                        columns = {
                            row[1] for row in connection.execute("PRAGMA table_info(effects)")
                        }
                    if (
                        len(marker) != 1
                        or marker[0][0] != "roboguide.runtime-mi-work/v0.1"
                        or re.fullmatch(r"[a-f0-9]{32}", str(marker[0][1])) is None
                        or not {"nonce", "request_digest", "kernel_json", "finished_kernel_json"}
                        <= columns
                    ):
                        raise ValueError("runtime MI original writer/nonce marker is unavailable")
                except sqlite3.Error as error:
                    raise ValueError("runtime MI original storage schema is unavailable") from error
            if component["kind"] == "node":
                path = Path(component["root"]) / component["database"]
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    if connection.execute("PRAGMA user_version").fetchone() != (13,):
                        raise ValueError("Node backup requires the actual v13 writer-lease fence")
                    if connection.execute(
                        "SELECT version FROM node_journal_storage_schema"
                    ).fetchone() != (13,):
                        raise ValueError("Node writer-lease storage marker is unavailable")
        yield


def validate_memory_storage(connection: sqlite3.Connection, profile_schema: str) -> None:
    """Require the original composite writer marker during backup and immutable bundle reads."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if profile_schema in {
        "roboguide.recovery-profile/v0.4",
        "roboguide.recovery-profile/v0.5",
        "roboguide.recovery-profile/v0.6",
        "roboguide.recovery-profile/v0.7",
        "roboguide.recovery-profile/v0.8",
        "roboguide.recovery-profile/v0.9",
        "roboguide.recovery-profile/v0.10",
        "roboguide.recovery-profile/v0.11",
    }:
        snapshot = profile_schema in {
            "roboguide.recovery-profile/v0.8",
            "roboguide.recovery-profile/v0.9",
            "roboguide.recovery-profile/v0.10",
            "roboguide.recovery-profile/v0.11",
        }
        expected_version = 4 if snapshot else 3
        message = f"Memory recovery requires its actual v{expected_version} composite writer marker"
        if version != expected_version:
            raise ValueError(message)
        try:
            marker = connection.execute(
                "SELECT schema,databases_json FROM provider_writer_format"
            ).fetchall()
            databases = json.loads(marker[0][1]) if len(marker) == 1 else None
        except (sqlite3.DatabaseError, TypeError, json.JSONDecodeError) as error:
            raise ValueError(message) from error
        expected = [
            "memory.sqlite3",
            "memory-use.sqlite3",
            "file-jobs.sqlite3",
            "active-map/active-map.sqlite3",
        ]
        if (
            len(marker) != 1
            or marker[0][0]
            != (
                "roboguide.memory-provider-writer/v0.2"
                if snapshot
                else "roboguide.memory-provider-writer/v0.1"
            )
            or databases != expected
        ):
            raise ValueError(message)
        if (
            snapshot
            and connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='snapshot_operations'"
            ).fetchone()
            is None
        ):
            raise ValueError("Memory snapshot operation authority is missing")
        if snapshot:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(snapshot_operations)")
            }
            if columns != {"operation_id", "command_id", "request_json", "state", "receipt_json"}:
                raise ValueError("Memory snapshot operation authority columns are missing")
    elif version >= 3:
        raise ValueError("Memory composite writers require explicit recovery profile v0.4")


def validate_replica_storage(connection: sqlite3.Connection, profile_schema: str) -> None:
    """Fence new Replica storage unless the declared recovery protocol owns its actual inode."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    expected_version = {
        "roboguide.recovery-profile/v0.5": 1,
        "roboguide.recovery-profile/v0.6": 2,
        "roboguide.recovery-profile/v0.7": 3,
        "roboguide.recovery-profile/v0.8": 3,
        "roboguide.recovery-profile/v0.9": 3,
        "roboguide.recovery-profile/v0.10": 3,
        "roboguide.recovery-profile/v0.11": 3,
    }.get(profile_schema)
    if expected_version is None:
        if version != 0:
            raise ValueError("Replica storage v1 requires explicit recovery profile v0.5")
        return
    expected_tables = {
        "replica_storage_format",
        *("replica_v1_" + name for name in ("meta", "commands", "log", "events", "snapshot")),
    }
    actual_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if version != expected_version or actual_tables != expected_tables:
        raise ValueError("Replica tables/version differ from the original recovery profile")
    if connection.execute("SELECT version,schema FROM replica_storage_format").fetchall() != [
        (expected_version, f"roboguide.replica-storage/v0.{expected_version}")
    ]:
        raise ValueError("Replica original storage marker differs")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger'").fetchone():
        raise ValueError("Replica storage contains unrecognized writer triggers")
    views = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    if views != {"replica_" + name for name in ("meta", "commands", "log", "events", "snapshot")}:
        raise ValueError("Replica old-writer readonly views are incomplete")
    if expected_version >= 2:
        row = connection.execute(
            "SELECT value FROM replica_v1_meta WHERE key='leader_transfers_v2'"
        ).fetchone()
        history = json.loads(row[0]) if row is not None else None
        validate_transfer_history(history, expected_version)
        if expected_version == 3:
            validate_controller_process_history(connection)
    for name in ("meta", "commands", "log", "events", "snapshot"):
        expected_sql = f"CREATE VIEW replica_{name} AS SELECT * FROM replica_v1_{name}"
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name=?", ("replica_" + name,)
        ).fetchone()
        actual_sql = " ".join(str(row[0]).lower().split()) if row is not None else ""
        if actual_sql != expected_sql.lower():
            raise ValueError("Replica old-writer readonly fence is unavailable")


def validate_transfer_history(history: Any, storage_version: int = 2) -> None:
    """Revalidate every original operation without inventing quorum or renewing effect authority."""
    if not isinstance(history, list) or len(history) > 512:
        raise ValueError("Replica original bounded transfer history is unavailable")
    identifiers: set[str] = set()
    pending = 0
    for record in history:
        required = {"transfer_id", "source_epoch", "target", "required_last_log_index", "state"}
        allowed = required | {"confirmed_epoch"}
        if storage_version == 3:
            allowed |= {"source_process", "source_drain"}
        if not isinstance(record, dict) or not required <= set(record) <= allowed:
            raise ValueError("Replica transfer operation is not closed")
        identifier, source, target = record["transfer_id"], record["source_epoch"], record["target"]
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or len(identifier.encode()) > 128
            or identifier in identifiers
            or not valid_transfer_epoch(source)
            or type(target) is not int
            or not 0 < target < 2**64
            or target == source["replica_id"]
        ):
            raise ValueError("Replica transfer original identity is invalid or repeated")
        identifiers.add(identifier)
        log = record["required_last_log_index"]
        if log is not None and (type(log) is not int or not 0 <= log < 2**64):
            raise ValueError("Replica transfer original log is invalid")
        if storage_version == 3 and record.get("source_drain") is not None:
            validate_controller_drain(record["source_drain"], source, record.get("source_process"))
        state = record["state"]
        if state not in (
            "prepared",
            "accepted",
            "target-elected",
            "unknown",
            "completed",
            "rejected",
        ):
            raise ValueError("Replica transfer original state is invalid")
        confirmed = record.get("confirmed_epoch")
        if state == "completed":
            if storage_version == 3 and record.get("source_drain") is None:
                raise ValueError("Replica completed transfer lacks original source drain")
            if (
                not isinstance(confirmed, dict)
                or not valid_transfer_epoch(confirmed)
                or confirmed["replica_id"] != target
                or confirmed["term"] <= source["term"]
            ):
                raise ValueError("Replica transfer completed quorum proof is unavailable")
        elif confirmed is not None:
            raise ValueError("Replica pending transfer cannot claim completed quorum")
        if state not in ("completed", "rejected"):
            pending += 1
    if pending > 1:
        raise ValueError("Replica original transfer history contains overlapping holds")


def valid_transfer_epoch(value: Any) -> bool:
    """Recognize one exact positive member/term without inferring that quorum accepted it."""
    return (
        isinstance(value, dict)
        and set(value) == {"term", "replica_id"}
        and all(type(item) is int and 0 < item < 2**64 for item in value.values())
    )


def validate_replica_sets(profile: dict[str, Any], components: list[dict[str, Any]]) -> None:
    """Check locked original cluster/schema/bootstrap before offline CAS placement."""
    for group in profile.get("artifact_replica_sets", []):
        cluster: tuple[Any, Any] | None = None
        replicas: set[str] = set()
        for artifact in group:
            owner = next(
                component
                for component in components
                if component["kind"] == "replica" and artifact in component["depends_on"]
            )
            if owner["component_id"] in replicas:
                raise ValueError("Artifact set repeats the same original replica authority")
            replicas.add(owner["component_id"])
            path = Path(owner["root"]) / owner["database"]
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
                database.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024)
                identity = database.execute(
                    "SELECT value FROM replica_meta WHERE key='identity'"
                ).fetchone()
                trust = database.execute(
                    "SELECT value FROM replica_meta WHERE key='certificate_trust'"
                ).fetchone()
            if identity is None or trust is None:
                raise ValueError(
                    "Artifact reconciliation requires original explicit v0.3 replica trust"
                )
            parsed_identity, parsed_trust = json.loads(identity[0]), json.loads(trust[0])
            if (
                not isinstance(parsed_identity, list)
                or len(parsed_identity) != 2
                or not isinstance(parsed_trust, dict)
            ):
                raise ValueError("original replica identity/trust shape differs")
            bootstrap = parsed_trust.get("bootstrap")
            if not isinstance(bootstrap, dict) or len(bootstrap) != len(group):
                raise ValueError(
                    "Artifact set does not cover the complete original member universe"
                )
            actual = (parsed_identity, bootstrap)
            if cluster is not None and cluster != actual:
                raise ValueError(
                    "Artifact set crosses original cluster/schema/bootstrap boundaries"
                )
            cluster = actual


def valid_process_instance(value: Any) -> bool:
    """Recognize a bounded original process UUID without asserting hardware identity."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) is not None
    )


def validate_controller_process(value: Any) -> None:
    """Require the complete original Linux child identity; missing values never become zero."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {"instance_id", "supervisor_instance", "epoch", "pid", "boot_id", "start_ticks"}
        or not all(
            valid_process_instance(value[key])
            for key in ("instance_id", "supervisor_instance", "boot_id")
        )
        or not valid_transfer_epoch(value["epoch"])
        or type(value["pid"]) is not int
        or not 0 < value["pid"] < 2**32
        or type(value["start_ticks"]) is not int
        or not 0 < value["start_ticks"] < 2**64
    ):
        raise ValueError("Replica original Controller process identity is unavailable")


def validate_controller_drain(value: Any, epoch: Any, process: Any) -> None:
    """Check exact retained wait evidence without inferring OS exit from a PID or terminal row."""
    if not isinstance(value, dict):
        raise ValueError("Replica source drain is not a closed proof")
    if value.get("kind") == "no-application-process":
        if (
            set(value) != {"kind", "supervisor_instance", "epoch"}
            or not valid_process_instance(value["supervisor_instance"])
            or value["epoch"] != epoch
            or process is not None
        ):
            raise ValueError("Replica original empty-process proof differs")
        return
    if set(value) != {"kind", "outcome"} or value["kind"] != "process-exit":
        raise ValueError("Replica source drain kind is unavailable")
    outcome = value["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != {"process", "exit_code", "signal"}:
        raise ValueError("Replica original Controller wait is not closed")
    validate_controller_process(outcome["process"])
    code, signal = outcome["exit_code"], outcome["signal"]
    if (
        outcome["process"] != process
        or process["epoch"] != epoch
        or (code is None) == (signal is None)
        or (code is not None and (type(code) is not int or not -(2**31) <= code < 2**31))
        or (signal is not None and (type(signal) is not int or not 0 < signal < 2**31))
    ):
        raise ValueError("Replica original Controller wait identity/status differs")


def validate_controller_process_history(connection: sqlite3.Connection) -> None:
    """Require original supervision and every pre-spawn/PID/wait record in the complete bundle."""
    rows = dict(
        connection.execute(
            "SELECT key,value FROM replica_v1_meta "
            "WHERE key IN ('controller_supervision_v1','controller_processes_v1',"
            "'legacy_controller_continuity_unknown')"
        ).fetchall()
    )
    enabled = json.loads(rows.get("controller_supervision_v1", "null"))
    legacy_unknown = json.loads(rows.get("legacy_controller_continuity_unknown", "null"))
    records = json.loads(rows.get("controller_processes_v1", "null"))
    if (
        type(enabled) is not bool
        or type(legacy_unknown) is not bool
        or not isinstance(records, list)
        or len(records) > 1024
    ):
        raise ValueError("Replica original Controller supervision/history is unavailable")
    ids: set[str] = set()
    uncertain = 0
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"instance_id", "supervisor_instance", "epoch", "process", "outcome"}
            or not valid_process_instance(record["instance_id"])
            or not valid_process_instance(record["supervisor_instance"])
            or not valid_transfer_epoch(record["epoch"])
            or record["instance_id"] in ids
        ):
            raise ValueError("Replica original Controller spawn history differs")
        ids.add(record["instance_id"])
        process, outcome = record["process"], record["outcome"]
        if process is not None:
            validate_controller_process(process)
            if any(
                process[key] != record[key]
                for key in ("instance_id", "supervisor_instance", "epoch")
            ):
                raise ValueError("Replica original spawn/PID identity differs")
        if outcome is not None:
            validate_controller_drain(
                {"kind": "process-exit", "outcome": outcome}, record["epoch"], process
            )
        else:
            uncertain += 1
    if uncertain > 1 or (records and not enabled):
        raise ValueError("Replica original Controller history overlaps or lost supervision")
