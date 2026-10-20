"""Durable bounded Memory-use authority for an explicitly cohosted Local EAIOS backend."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, cast

from .store import DIGEST, MAX_METADATA_BYTES, MemoryStore, canonical, content_digest

MAX_LEASE_MS = 30_000
MAX_LEASES = 4096


class UsePermissionEnded(ValueError):
    """The current local content-use interval ended; it is not a physical stop certificate."""


def kernel_clock() -> tuple[str, str, int, int]:
    """Read the same real Linux boot/uptime clock used by the cohosted Node journal."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    uuid.UUID(boot)
    time_namespace = str(Path("/proc/self/ns/time").readlink())
    if re.fullmatch(r"time:\[[0-9]+\]", time_namespace) is None:
        raise ValueError("shared kernel time namespace is unavailable")
    uptime = Path("/proc/uptime").read_text().split()[0]
    matched = re.fullmatch(r"([0-9]+)\.([0-9]{1,3})", uptime)
    if matched is None:
        raise ValueError("shared kernel boot clock precision is unavailable")
    whole, fraction = matched.groups()
    quantum = 10 ** (3 - len(fraction))
    return boot, time_namespace, int(whole) * 1000 + int(fraction) * quantum, quantum


def remaining(budget: dict[str, Any]) -> int:
    """Conservatively expire foreign boots, rollback, malformed bounds and the original deadline."""
    if set(budget) != {"boot_id", "time_namespace", "received_lower_ms", "deadline_lower_ms"}:
        return 0
    start, end = budget["received_lower_ms"], budget["deadline_lower_ms"]
    if any(type(value) is not int for value in (start, end)):
        return 0
    if start < 0 or not 0 < end - start <= MAX_LEASE_MS:
        return 0
    boot, time_namespace, lower, uncertainty = kernel_clock()
    if budget["boot_id"] != boot or budget["time_namespace"] != time_namespace or lower < start:
        return 0
    return max(0, int(end) - lower - uncertainty)


def text(value: object, maximum: int = 256) -> str:
    """Require bounded nonempty identity text without normalizing its immutable bytes."""
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise ValueError("Memory-use identity is empty or exceeds its bound")
    return value


class UseLeases:
    """Own only local content-use permission; Control retains Group, resource and stop authority."""

    def __init__(self, store: MemoryStore) -> None:
        """Reopen original command receipts and generation fences without renewing any budget."""
        self.store = store
        self._mutex = threading.RLock()
        self._writer: int | None = os.open(
            store.root / ".memory-use.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self._writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._db = sqlite3.connect(store.root / "memory-use.sqlite3", check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported local Memory-use schema")
            with self._db:
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS leases (lease_id TEXT PRIMARY KEY, "
                    "purpose_json BLOB NOT NULL, preparation_json BLOB, "
                    "generation INTEGER NOT NULL, "
                    "registry_revision INTEGER NOT NULL, membership_json BLOB NOT NULL, "
                    "budget_json BLOB, revoked INTEGER NOT NULL CHECK(revoked IN (0,1)))"
                )
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS commands (command_id TEXT PRIMARY KEY, "
                    "lease_id TEXT NOT NULL, generation INTEGER NOT NULL, "
                    "request_digest TEXT NOT NULL, "
                    "receipt_json BLOB NOT NULL)"
                )
                self._db.execute("PRAGMA user_version=1")
                self._db.execute("UPDATE leases SET revoked=1 WHERE revoked=0")
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            os.close(self._writer)
            self._writer = None
            raise

    def close(self) -> None:
        """Close the local lease database after the owner joins all content users."""
        with self._mutex:
            if self._writer is None:
                return
            self._db.close()
            if self._writer is not None:
                os.close(self._writer)
                self._writer = None

    def _control(self, request: dict[str, Any]) -> dict[str, Any]:
        """Reject open commands, caller paths and replacement source identities."""
        if len(canonical(request)) > MAX_METADATA_BYTES:
            raise ValueError("complete Memory-use provider command exceeds its metadata bound")
        if set(request) != {"schema", "control", "preparation_json", "receiver_budget", "imports"}:
            raise ValueError("Memory-use provider request is not closed")
        if request["schema"] != "roboguide.memory-use-provider-command/v0.1":
            raise ValueError("unsupported Memory-use provider request")
        control = request["control"]
        fields = {
            "schema",
            "command_id",
            "lease_id",
            "generation",
            "operation",
            "preparation_id",
            "preparation_digest",
            "execution_id",
            "node_id",
            "source_session_id",
            "tenant_id",
            "group_id",
            "registry_id",
            "registry_revision",
            "membership",
            "configuration_digest",
            "valid_for_ms",
        }
        if not isinstance(control, dict) or set(control) != fields:
            raise ValueError("Memory-use control is not closed")
        if control["schema"] != "roboguide.memory-use-control/v0.1":
            raise ValueError("unsupported Memory-use control")
        for key in fields - {"generation", "registry_revision", "membership", "valid_for_ms"}:
            text(control[key], 1024 if key in {"execution_id", "source_session_id"} else 256)
        for key in ("preparation_digest", "configuration_digest"):
            if DIGEST.fullmatch(control[key]) is None:
                raise ValueError("Memory-use content/configuration digest is malformed")
        for key in ("generation", "registry_revision", "valid_for_ms"):
            if type(control[key]) is not int or not 0 <= control[key] < 2**63:
                raise ValueError("Memory-use ordinal/budget is invalid")
        membership = control["membership"]
        if (
            not isinstance(membership, dict)
            or set(membership) != {"generation", "bindings_digest"}
            or type(membership["generation"]) is not int
            or not 0 < membership["generation"] < 2**63
            or not isinstance(membership["bindings_digest"], str)
            or DIGEST.fullmatch(membership["bindings_digest"]) is None
            or control["registry_revision"] == 0
        ):
            raise ValueError("current Memory-use policy/membership is unavailable")
        expected_id = (
            f"memory-use-{control['operation']}-{control['lease_id']}-{control['generation']}"
        )
        lease_id = "use-" + hashlib.sha256(control["preparation_id"].encode()).hexdigest()
        if control["command_id"] != expected_id or control["lease_id"] != lease_id:
            raise ValueError("Memory-use immutable command/lease identity differs")
        if control["operation"] == "grant":
            if control["generation"] == 0 or not 0 < control["valid_for_ms"] <= MAX_LEASE_MS:
                raise ValueError("Memory-use grant has no bounded original generation/budget")
        elif control["operation"] == "revoke":
            if control["generation"] != 0 or control["valid_for_ms"] != 0:
                raise ValueError("Memory-use revoke cannot grant time or a generation")
        else:
            raise ValueError("unsupported Memory-use operation")
        return control

    def _purpose(self, control: dict[str, Any]) -> bytes:
        """Bind immutable execution/configuration apart from monotonic grant revisions."""
        return canonical(
            {
                key: value
                for key, value in control.items()
                if key
                not in {
                    "command_id",
                    "operation",
                    "generation",
                    "valid_for_ms",
                    "registry_revision",
                    "membership",
                }
            }
            | {"backend_instance_id": self.store.instance_id, "provider_id": self.store.provider_id}
        )

    def _inputs(self, request: dict[str, Any], control: dict[str, Any]) -> list[str]:
        """Verify the exact Rust-encoded preparation and real original imports in this backend."""
        encoded = text(request["preparation_json"], 256 * 1024)
        if (
            "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
            != control["preparation_digest"]
        ):
            raise ValueError("complete original Memory preparation bytes differ")
        preparation = json.loads(encoded)
        if not isinstance(preparation, dict):
            raise ValueError("Memory preparation is not an object")
        if preparation.get("schema") != "roboguide.memory-preparation/v0.2":
            raise ValueError("unsupported original Memory preparation")
        for key in (
            "preparation_id",
            "tenant_id",
            "group_id",
            "execution_id",
            "node_id",
            "source_session_id",
        ):
            if preparation.get(key) != control[key]:
                raise ValueError("Memory-use command changes original purpose")
        if preparation.get("registry_id") != control["registry_id"]:
            raise ValueError("Memory-use policy authority changed")
        inputs = preparation.get("inputs")
        originals = request["imports"]
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8:
            raise ValueError("Memory-use original inputs are unavailable")
        if not isinstance(originals, list) or not 1 <= len(originals) <= 8:
            raise ValueError("Memory-use backend originals are unavailable")
        selected = {
            item["import_id"]: item
            for item in inputs
            if item["consumer"]["provider_id"] == self.store.provider_id
        }
        if (
            not selected
            or len(selected) != len(originals)
            or len({item["import_id"] for item in inputs}) != len(inputs)
        ):
            raise ValueError("Memory-use input coverage differs from this provider")
        seen = set()
        for original in originals:
            identifier = original["import_id"]
            if identifier in seen or identifier not in selected:
                raise ValueError("Memory-use repeats or substitutes an original import")
            seen.add(identifier)
            item = selected[identifier]
            manifest = self.store._validate_original(original)
            invocation = original["original_invocation"]
            if (
                invocation.get("memory_preparation_id") != control["preparation_id"]
                or invocation.get("session_id") != control["source_session_id"]
                or any(invocation.get(key) != control[key] for key in ("execution_id", "group_id"))
                or any(
                    invocation.get(key) != preparation["invocation"].get(key)
                    for key in ("mission_id", "task_id", "role_id")
                )
            ):
                raise ValueError("Memory-use import differs from its original preparation owner")
            if manifest != item["manifest"] or not self.store.availability(original)["available"]:
                raise ValueError("Memory-use original backend bytes are not currently available")
            raw = original["original_invocation"].get("typed_map_manifest_json", "")
            if (json.loads(raw) if raw else None) != item.get("typed_map"):
                raise ValueError("Memory-use strips complete original Spatial provenance")
            scope = manifest["scope"]
            if scope.get("kind") not in {"local", "execution_group", "global"}:
                raise ValueError("Memory-use semantic scope is unavailable")
            if scope.get("kind") == "local" and (
                manifest.get("owner", {}).get("owner") != "node"
                or manifest.get("owner", {}).get("node_id") != control["node_id"]
            ):
                raise ValueError("Memory-use crosses the immutable local Node scope")
            if (
                scope.get("kind") == "execution_group"
                and scope.get("execution_group_id") != control["group_id"]
            ):
                raise ValueError("Memory-use crosses the immutable semantic Group scope")
        return sorted(seen)

    def apply(self, request: dict[str, Any]) -> dict[str, Any]:
        """Commit one original grant or irreversible revoke before returning its durable receipt."""
        control = self._control(request)
        request_digest = content_digest(request)
        purpose = self._purpose(control)
        with self.store._mutex, self._mutex:
            original = self._db.execute(
                "SELECT request_digest,receipt_json FROM commands WHERE command_id=?",
                (control["command_id"],),
            ).fetchone()
            if original is not None:
                if original[0] != request_digest:
                    raise ValueError("original Memory-use command changed its budget or contents")
                return cast(dict[str, Any], json.loads(original[1]))
            current = self._db.execute(
                "SELECT purpose_json,generation,registry_revision,membership_json,"
                "budget_json,revoked "
                "FROM leases WHERE lease_id=?",
                (control["lease_id"],),
            ).fetchone()
            if current is not None and current[0] != purpose:
                raise ValueError("Memory-use immutable ownership/configuration differs")
            imports: list[str] = []
            if control["operation"] == "grant":
                budget = request["receiver_budget"]
                if not isinstance(budget, dict) or remaining(budget) == 0:
                    raise ValueError(
                        "Memory-use original shared-kernel budget expired or is foreign"
                    )
                if (
                    budget["deadline_lower_ms"] - budget["received_lower_ms"]
                    > control["valid_for_ms"]
                ):
                    raise ValueError("Memory-use backend budget exceeds its admitted grant")
                if current is None:
                    if control["generation"] != 1:
                        raise ValueError("Memory-use first grant must have generation one")
                    count = self._db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
                    if count >= MAX_LEASES:
                        raise ValueError("Memory-use retained authority is full")
                else:
                    previous_membership = json.loads(current[3])
                    previous_budget = json.loads(current[4]) if current[4] is not None else None
                    if (
                        current[5]
                        or current[4] is None
                        or previous_budget is None
                        or remaining(previous_budget) == 0
                        or budget["received_lower_ms"] < previous_budget["received_lower_ms"]
                        or budget["deadline_lower_ms"] <= previous_budget["deadline_lower_ms"]
                        or control["generation"] != current[1] + 1
                        or control["registry_revision"] < current[2]
                        or control["membership"]["generation"] < previous_membership["generation"]
                        or (
                            control["membership"]["generation"] == previous_membership["generation"]
                            and control["membership"] != previous_membership
                        )
                    ):
                        raise ValueError(
                            "Memory-use renewal is expired, revoked, discontinuous or stale"
                        )
                imports = self._inputs(request, control)
                if remaining(budget) == 0:
                    raise ValueError(
                        "Memory-use original budget expired during content verification"
                    )
            else:
                if (
                    request["receiver_budget"] is not None
                    or request["preparation_json"]
                    or request["imports"]
                ):
                    raise ValueError("Memory-use revoke cannot introduce content or a budget")
                budget = None
                if (
                    current is None
                    and self._db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] >= MAX_LEASES
                ):
                    raise ValueError("Memory-use retained revocations are full")
            receipt = {
                "schema": "roboguide.memory-use-provider-receipt/v0.1",
                "command_id": control["command_id"],
                "lease_id": control["lease_id"],
                "generation": control["generation"],
                "preparation_digest": control["preparation_digest"],
                "configuration_digest": control["configuration_digest"],
                "provider_id": self.store.provider_id,
                "provider_instance_id": self.store.instance_id,
                "status": "granted" if control["operation"] == "grant" else "revoked",
                "receiver_budget": budget,
                "import_ids": imports,
            }
            with self._db:
                if control["operation"] == "revoke":
                    if current is None:
                        self._db.execute(
                            "INSERT INTO leases VALUES (?,?,NULL,0,?,?,NULL,1)",
                            (
                                control["lease_id"],
                                purpose,
                                control["registry_revision"],
                                canonical(control["membership"]),
                            ),
                        )
                    else:
                        self._db.execute(
                            "UPDATE leases SET revoked=1 WHERE lease_id=?", (control["lease_id"],)
                        )
                else:
                    self._db.execute(
                        "INSERT INTO leases VALUES (?,?,?,?,?,?,?,0) "
                        "ON CONFLICT(lease_id) DO UPDATE SET "
                        "generation=excluded.generation,registry_revision=excluded.registry_revision,"
                        "membership_json=excluded.membership_json,budget_json=excluded.budget_json",
                        (
                            control["lease_id"],
                            purpose,
                            request["preparation_json"].encode(),
                            control["generation"],
                            control["registry_revision"],
                            canonical(control["membership"]),
                            canonical(budget),
                        ),
                    )
                self._db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?,?)",
                    (
                        control["command_id"],
                        control["lease_id"],
                        control["generation"],
                        request_digest,
                        canonical(receipt),
                    ),
                )
                self._db.execute(
                    "DELETE FROM commands WHERE lease_id=? AND generation>0 AND generation<?",
                    (control["lease_id"], max(0, control["generation"] - 3)),
                )
            return receipt

    def query(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return only the original receipt plus current effectiveness, without a new deadline."""
        control = self._control(request)
        with self._mutex:
            original = self._db.execute(
                "SELECT request_digest,receipt_json FROM commands WHERE command_id=?",
                (control["command_id"],),
            ).fetchone()
            if original is None:
                return {"receipt": None, "effective": False}
            if original[0] != content_digest(request):
                raise ValueError("Memory-use query changes the original complete command")
            return {
                "receipt": json.loads(original[1]),
                "effective": self._effective(control["lease_id"], control["generation"]),
            }

    def _effective(self, lease_id: str, generation: int | None = None) -> bool:
        """Check current generation, irreversible fence and original kernel budget."""
        row = self._db.execute(
            "SELECT generation,budget_json,revoked FROM leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        return bool(
            row is not None
            and not row[2]
            and row[1] is not None
            and (generation is None or generation == row[0])
            and remaining(json.loads(row[1])) > 0
        )

    def require(self, invocation: dict[str, Any]) -> str:
        """Fence local content use unless the exact current attempt owns its complete live lease."""
        memory = invocation.get("memory_preparation")
        if not isinstance(memory, dict):
            raise ValueError("current Memory use requires its original prepared context")
        lease_id = text(memory.get("use_lease_id"), 128)
        with self._mutex:
            row = self._db.execute(
                "SELECT purpose_json,preparation_json FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if row is None or not self._effective(lease_id):
                raise ValueError("Memory-use lease is absent, expired or revoked")
            purpose, preparation = json.loads(row[0]), json.loads(row[1])
            for key in ("execution_id", "group_id", "tenant_id"):
                actual = invocation.get(key) if key != "tenant_id" else memory.get("tenant_id")
                if actual != purpose[key]:
                    raise ValueError("local content use changed the accepted attempt/Group/tenant")
            if (
                memory.get("preparation_id") != purpose["preparation_id"]
                or memory.get("preparation_digest") != purpose["preparation_digest"]
                or canonical(memory.get("inputs")) != canonical(preparation["inputs"])
            ):
                raise ValueError("local content use changed its complete source context")
            for key in ("mission_id", "task_id", "role_id", "operation", "parameters"):
                if canonical(invocation.get(key)) != canonical(preparation["invocation"].get(key)):
                    raise ValueError(
                        "local content use changed canonical operation or logical slot"
                    )
        return lease_id

    def guard(self, lease_id: str, execution_id: str) -> None:
        """Recheck bounded read/compute phases without renewing content use."""
        with self._mutex:
            row = self._db.execute(
                "SELECT purpose_json FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if (
                row is None
                or json.loads(row[0])["execution_id"] != execution_id
                or not self._effective(lease_id)
            ):
                raise UsePermissionEnded("current Memory-use permission ended")

    def compute_budget(self, lease_id: str, execution_id: str) -> dict[str, Any]:
        """Freeze the current original deadline for one bounded child read without renewing it."""
        with self._mutex:
            self.guard(lease_id, execution_id)
            row = self._db.execute(
                "SELECT budget_json FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if row is None or row[0] is None:
                raise UsePermissionEnded("original Memory-use budget unavailable")
            return cast(dict[str, Any], json.loads(row[0]))

    def guard_map(self, context: dict[str, Any]) -> None:
        """Authorize only the original provider's exact typed map and full current attempt owner."""
        with self.store._mutex, self._mutex:
            self.guard(context["use_lease_id"], context["execution_id"])
            row = self._db.execute(
                "SELECT purpose_json,preparation_json FROM leases WHERE lease_id=?",
                (context["use_lease_id"],),
            ).fetchone()
            if row is None or row[1] is None:
                raise UsePermissionEnded("original typed-map preparation is unavailable")
            purpose, preparation = json.loads(row[0]), json.loads(row[1])
            if any(context[key] != purpose[key] for key in ("execution_id", "node_id", "group_id")):
                raise UsePermissionEnded("active-map changed its original Node/Group/attempt")
            if context["attempt_id"] != purpose["execution_id"]:
                raise UsePermissionEnded("active-map attempt differs from its original execution")
            matches = []
            for item in preparation["inputs"]:
                typed = item.get("typed_map")
                if item["consumer"]["provider_id"] == self.store.provider_id and typed is not None:
                    if (
                        typed["map_id"] == context["map_id"]
                        and typed["revision_id"] == context["revision_id"]
                        and typed["content_digest"] == context["content_digest"]
                        and typed["byte_size"] == context["byte_size"]
                        and typed["root_frame"] == context["frame_id"]
                        and typed["spatial_anchor"]["id"] == context["spatial_anchor_id"]
                    ):
                        matches.append(item)
            if len(matches) != 1:
                raise UsePermissionEnded("active-map is not one exact original typed-map input")
            self.store.require_content_live(context["content_digest"])

    def retired_import_evidence(self, original: dict[str, Any]) -> dict[str, Any]:
        """Read an irreversible original revoke receipt covering this exact backend import."""
        with self.store._mutex, self._mutex:
            invocation = original["original_invocation"]
            preparation_id = invocation.get("memory_preparation_id")
            if not isinstance(preparation_id, str) or not preparation_id:
                raise ValueError("legacy original import has no frozen preparation identity")
            lease_id = "use-" + hashlib.sha256(preparation_id.encode()).hexdigest()
            row = self._db.execute(
                "SELECT l.lease_id,l.purpose_json,l.preparation_json,c.receipt_json "
                "FROM leases l JOIN commands c ON c.lease_id=l.lease_id "
                "WHERE l.lease_id=? AND l.revoked=1 AND c.generation=0",
                (lease_id,),
            ).fetchone()
            if row is None:
                raise ValueError("original import lacks a durable original-provider revoke receipt")
            _, purpose_json, preparation_json, receipt_json = row
            purpose, receipt = json.loads(purpose_json), json.loads(receipt_json)
            manifest = self.store._validate_original(original)
            if (
                purpose["backend_instance_id"] != self.store.instance_id
                or purpose["provider_id"] != self.store.provider_id
                or receipt["status"] != "revoked"
                or receipt["provider_instance_id"] != self.store.instance_id
                or receipt["provider_id"] != self.store.provider_id
                or receipt["lease_id"] != lease_id
                or receipt["generation"] != 0
                or receipt["preparation_digest"] != purpose["preparation_digest"]
                or receipt["configuration_digest"] != purpose["configuration_digest"]
                or purpose["preparation_id"] != preparation_id
                or purpose["source_session_id"] != invocation.get("session_id")
                or any(purpose[key] != invocation.get(key) for key in ("execution_id", "group_id"))
            ):
                raise ValueError("original import retirement proof conflicts")
            if preparation_json is not None:
                preparation = json.loads(preparation_json)
                selected = [
                    item
                    for item in preparation["inputs"]
                    if item["import_id"] == original["import_id"]
                    and item["consumer"]["provider_id"] == self.store.provider_id
                ]
                if (
                    len(selected) != 1
                    or selected[0]["manifest"] != manifest
                    or any(
                        preparation["invocation"].get(key) != invocation.get(key)
                        for key in ("mission_id", "task_id", "role_id")
                    )
                ):
                    raise ValueError("original import retirement preparation conflicts")
            return {
                "import_id": original["import_id"],
                "revocations": [
                    {
                        "lease_id": lease_id,
                        "purpose": purpose,
                        "receipt": receipt,
                        "revoke_preceded_first_grant": preparation_json is None,
                    }
                ],
            }

    def content_retention_blockers(self, digest: str) -> list[str]:
        """Inspect current/unconfirmed leases once with bounded streaming metadata coverage."""
        with self.store._mutex, self._mutex:
            blockers, total, count = [], 0, 0
            for lease_id, encoded in self._db.execute(
                "SELECT lease_id,preparation_json FROM leases WHERE revoked=0 ORDER BY lease_id"
            ):
                count += 1
                total += len(encoded) if encoded is not None else 0
                if count > MAX_LEASES or total > 16 * MAX_METADATA_BYTES:
                    return ["current_lease_reference_coverage_exceeds_bound"]
                if encoded is None:
                    blockers.append("unknown_current_lease:" + lease_id)
                    continue
                if any(
                    item["manifest"].get("artifact", {}).get("content_digest") == digest
                    for item in json.loads(encoded)["inputs"]
                ):
                    blockers.append("current_or_unconfirmed_use_lease:" + lease_id)
            return blockers
