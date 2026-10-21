"""Test local lease authority with real bytes/SQLite, without devices or physical outcome claims."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider import use_leases
from roboguide_memory_provider.__main__ import UnixServer, handler_type
from roboguide_memory_provider.content_references import ContentReferences
from roboguide_memory_provider.jobs import FileJobs
from roboguide_memory_provider.process_jobs import ProcessFileJobs
from roboguide_memory_provider.reclamation import ContentReclamation
from roboguide_memory_provider.store import MemoryStore, canonical, original_content_digest
from roboguide_memory_provider.use_leases import UseLeases, kernel_clock


@pytest.fixture
def prepared(
    tmp_path: Path,
) -> Iterator[tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]]]:
    """Import an actual source file and supply metadata only for local-authority unit checks."""
    source = tmp_path / "source"
    payload = Path(__file__).read_bytes()
    source.write_bytes(payload)
    store = MemoryStore(tmp_path / "backend", "files", (tmp_path,), len(payload) + 1)
    identifiers = {
        key: uuid.uuid4().hex
        for key in (
            "preparation_id",
            "node_id",
            "execution_id",
            "group_id",
            "mission_id",
            "task_id",
            "role_id",
            "source_session_id",
        )
    }
    manifest = {
        "schema": "roboguide.memory-manifest/v0.1",
        "kind": "artifact",
        "selector": {"memory_id": uuid.uuid4().hex, "revision_id": "original"},
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "artifact": {
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "byte_size": len(payload),
        },
    }
    import_id = uuid.uuid4().hex
    original_invocation = {
        key: identifiers[key]
        for key in ("mission_id", "group_id", "task_id", "role_id", "execution_id")
    } | {
        "import_id": import_id,
        "command_id": "memory-import-" + import_id,
        "consumer_provider_id": store.provider_id,
        "manifest_json": canonical(manifest).decode(),
        "memory_preparation_id": identifiers["preparation_id"],
        "session_id": identifiers["source_session_id"],
    }
    original = {
        "import_id": import_id,
        "command_id": original_invocation["command_id"],
        "provider_id": store.provider_id,
        "provider_instance_id": store.instance_id,
        "identity_digest": hashlib.sha256(canonical(original_invocation)).hexdigest(),
        "provider_contract_digest": "sha256:"
        + hashlib.sha256(b"metadata-only unit contract").hexdigest(),
        "original_invocation": original_invocation,
        "admitted_at_ms": 1,
        "original_content_digest": original_content_digest(original_invocation),
    }
    store.import_artifact(original, manifest, str(source))
    invocation = {
        key: identifiers[key]
        for key in ("mission_id", "group_id", "task_id", "role_id", "execution_id")
    } | {"operation": "compute.hash-files@v1", "parameters": {"rounds": 2}}
    preparation = {
        "schema": "roboguide.memory-preparation/v0.2",
        **identifiers,
        "tenant_id": "local",
        "source_session_id": identifiers["source_session_id"],
        "registry_id": uuid.uuid4().hex,
        "inputs": [
            {
                "manifest": manifest,
                "typed_map": None,
                "consumer": {"provider_id": "files"},
                "import_id": import_id,
            }
        ],
        "invocation": invocation,
    }
    encoded = canonical(preparation).decode()
    control = {
        key: preparation[key]
        for key in (
            "preparation_id",
            "tenant_id",
            "group_id",
            "execution_id",
            "node_id",
            "source_session_id",
            "registry_id",
        )
    } | {
        "schema": "roboguide.memory-use-control/v0.1",
        "generation": 1,
        "operation": "grant",
        "lease_id": "use-" + hashlib.sha256(identifiers["preparation_id"].encode()).hexdigest(),
        "preparation_digest": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
        "configuration_digest": "sha256:" + hashlib.sha256(b"unit configuration").hexdigest(),
        "registry_revision": 1,
        "membership": {"generation": 1, "bindings_digest": "sha256:" + "a" * 64},
        "valid_for_ms": 30_000,
    }
    control["command_id"] = f"memory-use-grant-{control['lease_id']}-1"
    boot, time_namespace, lower, _ = kernel_clock()
    request = {
        "schema": "roboguide.memory-use-provider-command/v0.1",
        "control": control,
        "preparation_json": encoded,
        "imports": [original],
        "receiver_budget": {
            "boot_id": boot,
            "time_namespace": time_namespace,
            "received_lower_ms": lower,
            "deadline_lower_ms": lower + 30_000,
        },
    }
    invocation["memory_preparation"] = {
        "preparation_id": identifiers["preparation_id"],
        "preparation_digest": control["preparation_digest"],
        "use_lease_id": control["lease_id"],
        "tenant_id": "local",
        "inputs": preparation["inputs"],
    }
    leases = UseLeases(store)
    try:
        yield store, leases, request, invocation
    finally:
        leases.close()
        store.close()


def advance(request: dict[str, Any], generation: int, lower: int) -> dict[str, Any]:
    """Construct the next explicit unit-test authorization, preserving the original source."""
    changed = copy.deepcopy(request)
    control = changed["control"]
    control["generation"] = generation
    control["command_id"] = f"memory-use-grant-{control['lease_id']}-{generation}"
    changed["receiver_budget"]["received_lower_ms"] = lower
    changed["receiver_budget"]["deadline_lower_ms"] = lower + control["valid_for_ms"]
    return changed


def revoke(request: dict[str, Any]) -> dict[str, Any]:
    """Construct an irreversible unit-test fence carrying no content or time grant."""
    changed = copy.deepcopy(request)
    control = changed["control"]
    control.update(operation="revoke", generation=0, valid_for_ms=0)
    control["command_id"] = f"memory-use-revoke-{control['lease_id']}-0"
    changed.update(preparation_json="", receiver_budget=None, imports=[])
    return changed


def test_actual_import_grant_query_replay_and_reopen_preserve_budget(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Real grants and readonly queries retain the first budget after SQLite reopen."""
    store, leases, request, invocation = prepared
    first = leases.apply(request)
    assert first["status"] == "granted" and first["receiver_budget"] == request["receiver_budget"]
    assert leases.require(invocation) == request["control"]["lease_id"]
    reference_rows = store._db.execute(
        "SELECT reference_id,kind,owner,generation,active FROM content_references"
    ).fetchall()
    assert len(reference_rows) == 1
    assert reference_rows[0][1:] == ("use_lease", request["control"]["lease_id"], 1, 1)
    assert leases.apply(request) == first
    before = list(leases._db.iterdump())
    original_commands = leases._db.execute("SELECT * FROM commands").fetchall()
    original_budget = leases._db.execute("SELECT budget_json FROM leases").fetchone()
    with pytest.raises(BlockingIOError):
        UseLeases(store)
    assert leases.query(request) == {"receipt": first, "effective": True}
    assert list(leases._db.iterdump()) == before
    changed = copy.deepcopy(request)
    changed["receiver_budget"]["deadline_lower_ms"] += 1
    with pytest.raises(ValueError, match="original.*changed"):
        leases.apply(changed)
    leases.close()
    reopened = UseLeases(store)
    try:
        assert reopened.apply(request) == first
        assert reopened.query(request)["effective"] is False
        assert (
            store._db.execute(
                "SELECT active FROM content_references WHERE reference_id=?",
                (reference_rows[0][0],),
            ).fetchone()
            is None
        )
        assert reopened._db.execute("SELECT * FROM commands").fetchall() == original_commands
        assert reopened._db.execute("SELECT budget_json FROM leases").fetchone() == original_budget
        with pytest.raises(ValueError, match="revoked"):
            reopened.require(invocation)
    finally:
        reopened.close()


def test_continuous_renewal_keeps_four_receipts_and_fences_replayed_generations(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use a virtual unit clock for bounded history and stale or expired renewals."""
    store, leases, request, invocation = prepared
    boot, time_namespace, lower, uncertainty = kernel_clock()
    now = [lower]
    monkeypatch.setattr(
        use_leases, "kernel_clock", lambda: (boot, time_namespace, now[0], uncertainty)
    )
    request = advance(request, 1, lower)
    first = leases.apply(request)
    for generation in range(2, 21):
        now[0] += 1000
        leases.apply(advance(request, generation, now[0]))
    active_generations = store._db.execute(
        "SELECT generation,active FROM content_references WHERE kind='use_lease' "
        "AND owner=? ORDER BY generation",
        (request["control"]["lease_id"],),
    ).fetchall()
    assert active_generations == [(20, 1)]
    assert leases._db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 4
    assert leases.query(request) == {"receipt": None, "effective": False}
    with pytest.raises(ValueError, match="discontinuous"):
        leases.apply(request)
    leases.guard(request["control"]["lease_id"], invocation["execution_id"])
    now[0] += 30_000
    with pytest.raises(ValueError, match="permission ended"):
        leases.guard(request["control"]["lease_id"], invocation["execution_id"])
    with pytest.raises(ValueError, match="expired"):
        leases.apply(advance(request, 11, now[0]))
    assert first["receiver_budget"] == request["receiver_budget"]


@pytest.mark.parametrize("before_grant", [True, False])
def test_revoke_is_irreversible_even_when_it_arrives_first(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    before_grant: bool,
) -> None:
    """A real durable tombstone rejects late grants and never becomes physical stop evidence."""
    store, leases, request, invocation = prepared
    if not before_grant:
        leases.apply(request)
    fence = leases.apply(revoke(request))
    assert fence["status"] == "revoked" and fence["receiver_budget"] is None
    assert leases.apply(revoke(request)) == fence
    assert store._db.execute(
        "SELECT COUNT(*) FROM content_references WHERE kind='use_lease'"
    ).fetchone() == (0,)
    with pytest.raises(ValueError):
        leases.require(invocation)
    if before_grant:
        with pytest.raises(ValueError, match="revoked"):
            leases.apply(request)
    else:
        assert leases.query(request)["effective"] is False
        assert leases.apply(request)["status"] == "granted"  # Retained historical receipt only.


def test_sql_failure_cannot_leave_a_permission_without_its_receipt(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """An actual SQLite trigger abort atomically removes both the candidate lease and command."""
    _, leases, request, invocation = prepared
    leases._db.execute(
        "CREATE TRIGGER abort_receipt BEFORE INSERT ON commands "
        "BEGIN SELECT RAISE(ABORT,'injected lease receipt failure'); END"
    )
    leases._db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        leases.apply(request)
    assert leases.query(request) == {"receipt": None, "effective": False}
    assert leases._db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    with pytest.raises(ValueError):
        leases.require(invocation)


def test_committed_use_grant_reconciles_reference_insert_after_sql_failure(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """The active lease remains a GC blocker until exact grant replay repairs its reference row."""
    store, leases, request, _ = prepared
    store._db.execute(
        "CREATE TRIGGER reject_use_reference BEFORE INSERT ON content_references "
        "WHEN NEW.kind='use_lease' BEGIN SELECT RAISE(ABORT,'use reference insert fault'); END"
    )
    store._db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="use reference insert fault"):
        leases.apply(request)
    assert leases.query(request)["effective"] is True
    assert store._db.execute(
        "SELECT COUNT(*) FROM content_references WHERE kind='use_lease'"
    ).fetchone() == (0,)
    store._db.execute("DROP TRIGGER reject_use_reference")
    store._db.commit()
    receipt = leases.apply(request)
    assert receipt["status"] == "granted"
    assert store._db.execute(
        "SELECT COUNT(*) FROM content_references WHERE kind='use_lease' AND active=1"
    ).fetchone() == (1,)


def test_failed_renewal_keeps_old_projection_until_exact_retry(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed replacement cannot delete the old GC fence before installing its successor."""
    store, leases, request, _ = prepared
    budget = request["receiver_budget"]
    fixed_lower = budget["received_lower_ms"] + 2
    monkeypatch.setattr(
        use_leases,
        "kernel_clock",
        lambda: (budget["boot_id"], budget["time_namespace"], fixed_lower, 1),
    )
    leases.apply(request)
    next_request = advance(request, 2, request["receiver_budget"]["received_lower_ms"] + 1)
    store._db.execute(
        "CREATE TRIGGER reject_next_use_reference BEFORE INSERT ON content_references "
        "WHEN NEW.kind='use_lease' AND NEW.generation=2 "
        "BEGIN SELECT RAISE(ABORT,'replacement reference fault'); END"
    )
    store._db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="replacement reference fault"):
        leases.apply(next_request)
    assert store._db.execute(
        "SELECT generation,active FROM content_references WHERE kind='use_lease'"
    ).fetchall() == [(1, 1)]
    assert leases.content_retention_blockers(
        json.loads(request["preparation_json"])["inputs"][0]["manifest"]["artifact"][
            "content_digest"
        ]
    ) == ["current_or_unconfirmed_use_lease:" + request["control"]["lease_id"]]
    store._db.execute("DROP TRIGGER reject_next_use_reference")
    store._db.commit()
    assert leases.apply(next_request)["generation"] == 2
    assert store._db.execute(
        "SELECT generation,active FROM content_references WHERE kind='use_lease'"
    ).fetchall() == [(2, 1)]


def test_committed_revoke_reconciles_reference_release_after_sql_failure(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A durable revoke plus blocked row remains fail-closed until its exact retry releases it."""
    store, leases, request, _ = prepared
    leases.apply(request)
    store._db.execute(
        "CREATE TRIGGER reject_use_release BEFORE DELETE ON content_references "
        "WHEN OLD.kind='use_lease' "
        "BEGIN SELECT RAISE(ABORT,'use reference release fault'); END"
    )
    store._db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="use reference release fault"):
        leases.apply(revoke(request))
    assert leases.query(request)["effective"] is False
    assert store._db.execute(
        "SELECT COUNT(*) FROM content_references WHERE kind='use_lease' AND active=1"
    ).fetchone() == (1,)
    store._db.execute("DROP TRIGGER reject_use_release")
    store._db.commit()
    receipt = leases.apply(revoke(request))
    assert receipt["status"] == "revoked"
    assert store._db.execute(
        "SELECT COUNT(*) FROM content_references WHERE kind='use_lease'"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "field", ["execution_id", "group_id", "operation", "task_id", "role_id", "parameters"]
)
def test_live_lease_does_not_authorize_a_changed_execution(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    field: str,
) -> None:
    """Confine content permission to the original slot and canonical invocation."""
    _, leases, request, invocation = prepared
    leases.apply(request)
    changed = copy.deepcopy(invocation)
    changed[field] = {"rounds": True} if field == "parameters" else "another-value"
    with pytest.raises(ValueError):
        leases.require(changed)


def test_corrupted_actual_content_and_foreign_boot_cannot_gain_permission(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Reject real corruption and a foreign kernel despite retained import history."""
    store, leases, request, _ = prepared
    foreign = copy.deepcopy(request)
    foreign["receiver_budget"]["boot_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="foreign"):
        leases.apply(foreign)
    foreign = copy.deepcopy(request)
    foreign["receiver_budget"]["time_namespace"] = "time:[1]"
    with pytest.raises(ValueError, match="foreign"):
        leases.apply(foreign)
    original = request["imports"][0]
    manifest = json.loads(original["original_invocation"]["manifest_json"])
    content = store.root / "content" / manifest["artifact"]["content_digest"][7:]
    content.chmod(0o600)
    content.write_bytes(b"corrupted actual content")
    with pytest.raises(ValueError, match="not currently available"):
        leases.apply(request)
    assert leases._db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0


def test_concurrent_grant_and_revoke_never_end_with_live_permission(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Serialize actual threads and retain the irreversible fence in either ordering."""
    _, leases, request, invocation = prepared
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def apply(value: dict[str, Any]) -> None:
        """Race this fixture's SQLite authority; a late grant may be denied."""
        barrier.wait(timeout=5)
        try:
            leases.apply(value)
        except ValueError as error:
            failures.append(error)

    workers = [
        threading.Thread(target=apply, args=(value,)) for value in (request, revoke(request))
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert len(failures) <= 1
    assert leases._db.execute("SELECT revoked FROM leases").fetchone()[0] == 1
    with pytest.raises(ValueError):
        leases.require(invocation)


def test_actual_file_computation_checks_the_live_lease_and_retains_historical_outcome(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """An actual file job needs current authority; completed original results remain readonly."""
    store, leases, request, invocation = prepared
    jobs = FileJobs(store, leases)
    try:
        with pytest.raises(ValueError, match="absent"):
            jobs.compute(invocation)
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        leases.apply(request)
        result = jobs.compute(invocation)
        assert result["state"] == "COMPLETED"
        actual = result["result"]
        assert isinstance(actual, dict)
        assert actual["input_digests"] == [
            json.loads(request["preparation_json"])["inputs"][0]["manifest"]["artifact"][
                "content_digest"
            ]
        ]
        leases.apply(revoke(request))
        assert jobs.compute(invocation) == result
        assert jobs.health()["safe"] is True
    finally:
        jobs.close()


def reclamation_request(store: MemoryStore, grant: dict[str, Any]) -> dict[str, Any]:
    """Address actual imported bytes and the persistent original provider, never a Node device."""
    artifact = json.loads(grant["preparation_json"])["inputs"][0]["manifest"]["artifact"]
    return {
        "schema": "roboguide.content-reclamation-command/v0.1",
        "command_id": "reclaim-" + uuid.uuid4().hex,
        "provider_id": store.provider_id,
        "provider_instance_id": store.instance_id,
        "content_digest": artifact["content_digest"],
        "byte_size": artifact["byte_size"],
    }


def content_reference_request(store: MemoryStore, grant: dict[str, Any]) -> dict[str, Any]:
    """Bind one exact imported CAS object to an immutable local owner generation."""
    artifact = json.loads(grant["preparation_json"])["inputs"][0]["manifest"]["artifact"]
    return {
        "schema": "roboguide.content-reference-command/v0.1",
        "operation": "acquire",
        "provider_id": store.provider_id,
        "provider_instance_id": store.instance_id,
        "reference_id": "lineage-" + uuid.uuid4().hex,
        "content_digest": artifact["content_digest"],
        "kind": "lineage",
        "owner": grant["imports"][0]["import_id"],
        "generation": 1,
        "byte_size": artifact["byte_size"],
    }


def test_reclamation_requires_actual_original_revoke_and_preserves_history(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Live, never-granted and revoked content have distinct actual local reclamation gates."""
    store, leases, grant, invocation = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    references = ContentReferences(store)
    command = reclamation_request(store, grant)
    reference = content_reference_request(store, grant)
    original = grant["imports"][0]
    try:
        assert owner.tombstone(command)["state"] == "blocked"
        leases.apply(grant)
        assert jobs.compute(invocation)["state"] == "COMPLETED"
        assert owner.tombstone(command)["state"] == "blocked"
        completion = store.query(original)
        leases.apply(revoke(grant))
        active = references.apply(reference)
        assert active["state"] == "active"
        assert references.apply(reference) == active
        blocked = owner.tombstone(command)
        assert blocked["state"] == "blocked"
        assert (
            "lineage:" + reference["owner"] + ":" + reference["reference_id"]
            in (blocked["reasons"])
        )
        changed_generation = {
            **reference,
            "operation": "release",
            "generation": reference["generation"] + 1,
        }
        changed_generation.pop("byte_size")
        with pytest.raises(ValueError, match="exact original"):
            references.apply(changed_generation)
        forged_use_reference = {
            **reference,
            "reference_id": "forged-use-" + uuid.uuid4().hex,
            "kind": "use_lease",
        }
        with pytest.raises(ValueError, match="kind"):
            references.apply(forged_use_reference)
        released = {**reference, "operation": "release"}
        released.pop("byte_size")
        release_receipt = references.apply(released)
        assert release_receipt["state"] == "released"
        assert references.apply(released) == release_receipt
        observed = references.observe(
            {
                "schema": "roboguide.content-reference-query/v0.1",
                "provider_id": store.provider_id,
                "provider_instance_id": store.instance_id,
                "reference_id": reference["reference_id"],
            }
        )
        assert observed == release_receipt
        assert references.apply(reference)["state"] == "released"
        assert store._db.execute(
            "SELECT COUNT(*) FROM content_references WHERE kind='use_lease'"
        ).fetchone() == (0,)
        tombstone = owner.tombstone(command)
        assert tombstone["state"] == "tombstoned"
        assert store._content.joinpath(command["content_digest"][7:]).is_file()
        assert store.query(original) == completion
        assert store.availability(original)["available"] is False
        with pytest.raises(ValueError, match="tombstoned"):
            store.import_artifact(
                original,
                json.loads(original["original_invocation"]["manifest_json"]),
                str(store.input_roots[0] / "source"),
            )
        assert owner.tombstone(command) == tombstone
        assert references.apply(reference) == release_receipt
        receipt = owner.collect(command)
        assert receipt["state"] == "deleted" and receipt["local_copy_only"] is True
        assert not store._content.joinpath(command["content_digest"][7:]).exists()
        assert owner.collect(command) == receipt
        before = list(store._db.iterdump())
        assert owner.observe(command["command_id"]) == receipt
        assert store.query(original) == completion
        assert list(store._db.iterdump()) == before
        changed = {**command, "byte_size": command["byte_size"] - 1}
        with pytest.raises(ValueError, match="conflicts"):
            owner.tombstone(changed)
    finally:
        jobs.close()


def test_actual_file_job_reference_fences_only_its_input_until_worker_exits(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An actual paused file reader fences its CAS after revoke and releases after thread exit."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    jobs = FileJobs(store, leases, max_job_seconds=30, max_job_rounds=256)
    owner = ContentReclamation(store, leases, jobs, None)
    entered = threading.Event()
    resume = threading.Event()
    original_hash = jobs._hash_inputs

    def pause_before_hash(*args: Any, **kwargs: Any) -> list[str]:
        """Hold the actual opened-input worker at a bounded read boundary for the GC race."""
        entered.set()
        if not resume.wait(timeout=5):
            raise TimeoutError("actual file-job test worker was not released")
        return original_hash(*args, **kwargs)

    monkeypatch.setattr(jobs, "_hash_inputs", pause_before_hash)
    command = reclamation_request(store, grant)
    execution = invocation["execution_id"]
    worker: threading.Thread | None = None
    try:
        assert jobs.submit(invocation)["state"] == "RUNNING"
        assert entered.wait(timeout=5)
        worker = jobs._workers[execution]
        references = store._db.execute(
            "SELECT content_digest,kind,owner,generation,active FROM content_references "
            "WHERE kind='execution' AND active=1"
        ).fetchall()
        assert len(references) == 1
        assert references[0][0] == command["content_digest"]
        assert references[0][1:] == (
            "execution",
            ContentReferences.execution_owner(execution),
            1,
            1,
        )
        leases.apply(revoke(grant))
        blocked = owner.tombstone(command)
        assert blocked["state"] == "blocked"
        assert any(reason.startswith("execution:file-job:") for reason in blocked["reasons"])
        resume.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert jobs.status(execution)["state"] == "CANCELLED"
        assert store._db.execute(
            "SELECT COUNT(*) FROM content_references WHERE kind='execution' AND active=1"
        ).fetchone() == (0,)
        assert owner.tombstone(command)["state"] == "tombstoned"
        assert owner.collect(command)["state"] == "deleted"
    finally:
        resume.set()
        if worker is not None:
            worker.join(timeout=5)
        jobs.close()


@pytest.mark.parametrize("failed_table", ["content-reference", "job-intent"])
def test_file_job_admission_failure_never_leaves_a_dangling_or_unfenced_intent(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    failed_table: str,
) -> None:
    """Injected SQLite failures leave neither a phantom job nor an orphan live execution ref."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    jobs = FileJobs(store, leases)
    try:
        if failed_table == "content-reference":
            store._db.execute(
                "CREATE TRIGGER reject_execution_reference BEFORE INSERT ON content_references "
                "WHEN NEW.kind='execution' "
                "BEGIN SELECT RAISE(ABORT,'execution reference fault'); END"
            )
            store._db.commit()
            error = "execution reference fault"
        else:
            jobs._db.execute(
                "CREATE TRIGGER reject_job_intent BEFORE INSERT ON jobs "
                "BEGIN SELECT RAISE(ABORT,'job intent fault'); END"
            )
            jobs._db.commit()
            error = "job intent fault"
        with pytest.raises(sqlite3.IntegrityError, match=error):
            jobs.compute(invocation)
        assert jobs.status(invocation["execution_id"])["state"] == "UNKNOWN"
        assert store._db.execute(
            "SELECT COUNT(*) FROM content_references WHERE kind='execution' AND active=1"
        ).fetchone() == (0,)
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs WHERE state='RUNNING'").fetchone() == (
            0,
        )
    finally:
        jobs.close()


def test_original_child_wait_receipt_sql_failure_retains_input_gc_reference(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A real reaped PID without committed wait evidence leaves its exact CAS fenced."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    invocation["execution_generation"] = 1
    jobs = ProcessFileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    try:
        jobs._db.execute(
            "CREATE TRIGGER reject_original_wait BEFORE UPDATE OF exit_json ON original_processes "
            "BEGIN SELECT RAISE(ABORT,'original wait receipt fault'); END"
        )
        jobs._db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="original wait receipt fault"):
            jobs.compute(invocation)
        process = jobs._processes[invocation["execution_id"]]
        assert process.returncode is not None
        assert jobs._db.execute(
            "SELECT exit_json FROM original_processes WHERE execution_id=?",
            (invocation["execution_id"],),
        ).fetchone() == (None,)
        assert store._db.execute(
            "SELECT generation,active FROM content_references WHERE kind='execution'"
        ).fetchall() == [(1, 1)]
        leases.apply(revoke(grant))
        assert owner.tombstone(command)["state"] == "blocked"
    finally:
        jobs.close()


def test_actual_child_exit_releases_only_after_original_wait_and_blocks_gc(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real imported-CAS child keeps its reference through revoke until original wait."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    invocation["execution_generation"] = 1
    jobs = ProcessFileJobs(store, leases, max_job_seconds=30, max_job_rounds=256)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    execution = invocation["execution_id"]
    proof_recorded = threading.Event()
    release_worker = threading.Event()
    original_record = jobs._record_quota_proof

    def pause_before_child_read(
        current_execution: str,
        process: subprocess.Popen[bytes],
        proof: dict[str, Any],
    ) -> None:
        """Hold the actual child after kernel proof and before granting its first read."""
        original_record(current_execution, process, proof)
        proof_recorded.set()
        if not release_worker.wait(timeout=5):
            raise TimeoutError("actual process test did not release its child")

    monkeypatch.setattr(jobs, "_record_quota_proof", pause_before_child_read)
    try:
        assert jobs.submit(invocation)["state"] == "RUNNING"
        deadline = time.monotonic() + 5
        while not proof_recorded.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert proof_recorded.is_set()
        process = jobs._processes[execution]
        assert process.poll() is None
        leases.apply(revoke(grant))
        assert owner.tombstone(command)["state"] == "blocked"
        assert store._db.execute(
            "SELECT generation,active FROM content_references WHERE kind='execution'"
        ).fetchall() == [(1, 1)]
        assert jobs.cancel(execution)["accepted"] is True
        release_worker.set()
        deadline = time.monotonic() + 10
        while jobs.status(execution)["state"] not in {"CANCELLED", "FAILED"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("original local child did not reach a terminal result")
            time.sleep(0.01)
        assert process.returncode is not None
        assert (
            jobs._db.execute(
                "SELECT pid,exit_json FROM original_processes WHERE execution_id=?", (execution,)
            ).fetchone()[1]
            is not None
        )
        assert store._db.execute(
            "SELECT COUNT(*) FROM content_references WHERE kind='execution' AND active=1"
        ).fetchone() == (0,)
        assert owner.tombstone(command)["state"] == "tombstoned"
        assert owner.collect(command)["state"] == "deleted"
    finally:
        release_worker.set()
        jobs.close()


def test_restart_rebuilds_unknown_job_reference_without_reexecuting(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Reopen an admitted job after simulated owner death; retain GC fence without replay."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    original_jobs = FileJobs(store, leases)
    command = reclamation_request(store, grant)
    execution = invocation["execution_id"]
    jobs_closed = False
    try:
        admission = original_jobs._admit(invocation)
        assert admission is not None
        leases.apply(revoke(grant))
        leases.close()
        assert store._db.execute(
            "SELECT COUNT(*) FROM content_references WHERE kind='execution' AND active=1"
        ).fetchone() == (1,)
        original_jobs._active.clear()  # Simulate abrupt loss after durable admission.
        original_jobs.close()
        jobs_closed = True
        reopened_leases = UseLeases(store)
        reopened_jobs = FileJobs(store, reopened_leases)
        reopened_owner = ContentReclamation(store, reopened_leases, reopened_jobs, None)
        try:
            assert reopened_jobs.status(execution)["state"] == "UNKNOWN"
            assert reopened_jobs.compute(invocation)["state"] == "UNKNOWN"
            assert store._db.execute(
                "SELECT generation,active FROM content_references "
                "WHERE kind='execution' AND owner=?",
                (ContentReferences.execution_owner(execution),),
            ).fetchall() == [(1, 1)]
            blocked = reopened_owner.tombstone(command)
            assert blocked["state"] == "blocked"
            assert any(reason.startswith("execution:file-job:") for reason in blocked["reasons"])
        finally:
            reopened_jobs.close()
            reopened_leases.close()
    finally:
        if not jobs_closed:
            original_jobs._active.clear()
            original_jobs.close()


def test_process_job_restart_rebuilds_unknown_cas_reference_without_pid_replay(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Restore a durable pre-spawn process intent as UNKNOWN and keep its imported bytes fenced."""
    store, leases, grant, invocation = prepared
    leases.apply(grant)
    invocation["execution_generation"] = 1
    original_jobs = ProcessFileJobs(store, leases)
    execution = invocation["execution_id"]
    jobs_closed = False
    try:
        admission = original_jobs._admit(invocation)
        assert admission is not None
        assert original_jobs._db.execute(
            "SELECT pid,exit_json FROM original_processes WHERE execution_id=?", (execution,)
        ).fetchone() == (None, None)
        original_jobs._active.clear()  # Simulate loss before original Popen/PID commit.
        original_jobs.close()
        jobs_closed = True
        leases.close()

        reopened_leases = UseLeases(store)
        reopened_jobs = ProcessFileJobs(store, reopened_leases)
        try:
            assert reopened_jobs.status(execution)["state"] == "UNKNOWN"
            assert reopened_jobs.compute(invocation)["state"] == "UNKNOWN"
            assert reopened_jobs._processes == {}
            assert reopened_jobs._db.execute(
                "SELECT pid,exit_json FROM original_processes WHERE execution_id=?", (execution,)
            ).fetchone() == (None, None)
            prepared_manifest = json.loads(grant["preparation_json"])["inputs"][0]["manifest"]
            owner = ContentReferences.execution_owner(execution)
            selector_digest = hashlib.sha256(canonical(prepared_manifest["selector"])).hexdigest()
            expected_reference = f"file-job:{owner[9:]}:1:{selector_digest}"
            assert reopened_jobs.reclamation_blockers(
                prepared_manifest["artifact"]["content_digest"]
            ) == [f"execution:{owner}:{expected_reference}"]
        finally:
            reopened_jobs.close()
            reopened_leases.close()
    finally:
        if not jobs_closed:
            original_jobs._active.clear()
            original_jobs.close()


def test_reference_acquire_races_with_tombstone_without_leaving_unfenced_bytes(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Serialize actual acquire and tombstone so one sees the other's committed authority."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    references = ContentReferences(store)
    owner = ContentReclamation(store, leases, jobs, None)
    reference = content_reference_request(store, grant)
    reclaim = reclamation_request(store, grant)
    leases.apply(revoke(grant))
    barrier = threading.Barrier(2)
    outcomes: dict[str, Any] = {}

    def acquire() -> None:
        """Race the provider's actual reference writer against its removal gate."""
        barrier.wait(timeout=5)
        try:
            outcomes["reference"] = references.apply(reference)
        except ValueError as error:
            outcomes["reference_error"] = str(error)

    def tombstone() -> None:
        """Race the original durable reclamation transaction against acquisition."""
        barrier.wait(timeout=5)
        outcomes["reclamation"] = owner.tombstone(reclaim)

    workers = [threading.Thread(target=acquire), threading.Thread(target=tombstone)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()
        state = outcomes["reclamation"]["state"]
        if "reference" in outcomes:
            assert outcomes["reference"]["state"] == "active"
            assert state == "blocked"
            assert (
                "lineage:" + reference["owner"] + ":" + reference["reference_id"]
                in (outcomes["reclamation"]["reasons"])
            )
        else:
            assert "tombstoned" in outcomes["reference_error"]
            assert state == "tombstoned"
    finally:
        jobs.close()


def test_reclamation_final_sql_failure_requires_original_explicit_reconciliation(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A real unlink followed by failed SQL retains uncertainty until the same explicit command."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    try:
        leases.apply(grant)
        leases.apply(revoke(grant))
        tombstone = owner.tombstone(command)
        store._db.execute(
            "CREATE TRIGGER reject_gc_receipt BEFORE UPDATE ON reclamation_commands "
            "WHEN NEW.state='deleted' BEGIN SELECT RAISE(ABORT,'actual receipt fault'); END"
        )
        store._db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="actual receipt fault"):
            owner.collect(command)
        assert not store._content.joinpath(command["content_digest"][7:]).exists()
        assert owner.observe(command["command_id"]) == tombstone
        assert store._db.execute("SELECT state FROM content_tombstones").fetchone() == (
            "tombstoned",
        )
        store._db.execute("DROP TRIGGER reject_gc_receipt")
        store._db.commit()
        receipt = owner.collect(command)
        assert receipt["state"] == "deleted"
        assert receipt["reconciled_missing_after_tombstone"] is True
    finally:
        jobs.close()


def test_reclamation_failed_tombstone_never_deletes_or_retires_imports(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A real tombstone transaction rollback leaves original bytes and availability intact."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    try:
        leases.apply(grant)
        leases.apply(revoke(grant))
        store._db.execute(
            "CREATE TRIGGER reject_tombstone BEFORE INSERT ON content_tombstones "
            "BEGIN SELECT RAISE(ABORT,'actual tombstone fault'); END"
        )
        store._db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="actual tombstone fault"):
            owner.tombstone(command)
        assert store._db.execute("SELECT COUNT(*) FROM retired_imports").fetchone() == (0,)
        assert store.availability(grant["imports"][0])["available"] is True
        assert owner.observe(command["command_id"])["state"] == "requested"
    finally:
        jobs.close()


def test_backend_restart_fence_is_not_an_original_revoke_receipt(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Actual lease reopen fences access but cannot invent the original Control revocation."""
    store, leases, grant, _ = prepared
    leases.apply(grant)
    leases.close()
    reopened = UseLeases(store)
    jobs = FileJobs(store, reopened)
    owner = ContentReclamation(store, reopened, jobs, None)
    try:
        assert reopened.query(grant)["effective"] is False
        assert (
            store._db.execute(
                "SELECT active FROM content_references WHERE kind='use_lease'"
            ).fetchone()
            is None
        )
        assert owner.tombstone(reclamation_request(store, grant))["state"] == "blocked"
    finally:
        jobs.close()
        reopened.close()


def test_original_revoke_before_first_grant_can_retire_an_unused_import(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """An actual original revoke can retire unused imported bytes without inventing a grant."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    try:
        leases.apply(revoke(grant))
        assert leases._db.execute("SELECT preparation_json FROM leases").fetchone() == (None,)
        assert owner.tombstone(command)["state"] == "tombstoned"
        assert owner.collect(command)["state"] == "deleted"
        assert jobs._db.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
        with pytest.raises(ValueError):
            leases.apply(grant)
    finally:
        jobs.close()


def test_reclamation_rejects_changed_or_symlinked_original_bytes(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A same-hash replacement inode or symlink after tombstone is never unlinked by retry."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    try:
        leases.apply(grant)
        leases.apply(revoke(grant))
        owner.tombstone(command)
        path = store._content / command["content_digest"][7:]
        moved = store.root / "actual-original"
        path.rename(moved)
        path.write_bytes(moved.read_bytes())
        with pytest.raises(ValueError, match="inode"):
            owner.collect(command)
        path.unlink()
        path.symlink_to(moved)
        with pytest.raises(OSError):
            owner.collect(command)
        assert path.is_symlink() and moved.is_file()
        assert owner.observe(command["command_id"])["state"] == "tombstoned"
    finally:
        jobs.close()


def test_current_map_requires_original_typed_input_and_complete_owner(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A real non-map lease cannot authorize a map merely through its known lease/attempt IDs."""
    _, leases, grant, _ = prepared
    leases.apply(grant)
    context = {
        "use_lease_id": grant["control"]["lease_id"],
        "execution_id": grant["control"]["execution_id"],
        "attempt_id": grant["control"]["execution_id"],
        "node_id": grant["control"]["node_id"],
        "group_id": grant["control"]["group_id"],
    }
    with pytest.raises(ValueError, match="typed-map input"):
        leases.guard_map(context)
    with pytest.raises(ValueError, match="Node/Group/attempt"):
        leases.guard_map({**context, "node_id": "another-owner"})


def test_shadow_preparation_cannot_reuse_another_original_import(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A different otherwise valid grant cannot borrow imported bytes or their retirement proof."""
    _, leases, grant, _ = prepared
    shadow = copy.deepcopy(grant)
    preparation = json.loads(shadow["preparation_json"])
    replacement = uuid.uuid4().hex
    preparation["preparation_id"] = replacement
    shadow["preparation_json"] = canonical(preparation).decode()
    control = shadow["control"]
    control["preparation_id"] = replacement
    control["preparation_digest"] = (
        "sha256:" + hashlib.sha256(shadow["preparation_json"].encode()).hexdigest()
    )
    control["lease_id"] = "use-" + hashlib.sha256(replacement.encode()).hexdigest()
    control["command_id"] = f"memory-use-grant-{control['lease_id']}-1"
    before = list(leases._db.iterdump())
    with pytest.raises(ValueError, match="original preparation owner"):
        leases.apply(shadow)
    assert list(leases._db.iterdump()) == before
    assert leases.apply(grant)["status"] == "granted"


def test_private_unix_reclamation_consumes_real_original_revocation(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Exercise HTTP progress, durable tombstone and actual unlink through shipped handlers."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    command = reclamation_request(store, grant)
    reference = content_reference_request(store, grant)
    try:
        with tempfile.TemporaryDirectory(prefix="rg-content-") as directory:
            path = Path(directory) / "local.sock"
            server = UnixServer(str(path), handler_type(store, jobs, leases, None, owner))
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                assert (
                    unix_request(path, "/v1/content/tombstone", command, "POST")["state"]
                    == "blocked"
                )
                active = unix_request(path, "/v1/content/references", reference, "POST")
                assert active["state"] == "active"
                assert (
                    unix_request(
                        path,
                        "/v1/content/references",
                        {
                            "schema": "roboguide.content-reference-query/v0.1",
                            "provider_id": store.provider_id,
                            "provider_instance_id": store.instance_id,
                            "reference_id": reference["reference_id"],
                        },
                        "GET",
                    )
                    == active
                )
                unix_request(path, "/v1/memory-use/control", grant, "POST")
                unix_request(path, "/v1/memory-use/control", revoke(grant), "POST")
                assert (
                    unix_request(path, "/v1/content/tombstone", command, "POST")["state"]
                    == "blocked"
                )
                release = {**reference, "operation": "release"}
                release.pop("byte_size")
                assert (
                    unix_request(path, "/v1/content/references", release, "POST")["state"]
                    == "released"
                )
                tombstone = unix_request(path, "/v1/content/tombstone", command, "POST")
                assert tombstone["state"] == "tombstoned"
                assert (
                    unix_request(
                        path,
                        "/v1/content/reclamation",
                        {"command_id": command["command_id"]},
                        "GET",
                    )
                    == tombstone
                )
                assert (
                    unix_request(path, "/v1/content/collect", command, "POST")["state"] == "deleted"
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                assert not thread.is_alive()
    finally:
        jobs.close()


def test_content_references_reject_tcp_even_when_handler_has_reclamation_authority(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """A configured reference owner still refuses every non-Unix transport."""
    store, leases, grant, _ = prepared
    jobs = FileJobs(store, leases)
    owner = ContentReclamation(store, leases, jobs, None)
    reference = content_reference_request(store, grant)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(store, jobs, leases, None, owner))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(
                "POST",
                "/v1/content/references",
                body=canonical(reference),
                headers={"Content-Length": str(len(canonical(reference)))},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        assert response.status == 409
        assert "private Unix transport" in payload["error"]
        assert store._db.execute("SELECT COUNT(*) FROM content_references").fetchone() == (0,)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        jobs.close()


def test_actual_computation_exits_after_revocation_at_a_read_boundary(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revoke during real hashing; report cancellation after the worker exits."""
    store, leases, request, invocation = prepared
    leases.apply(request)
    original_guard = leases.guard
    calls = [0]

    def at_boundary(lease: str, execution: str) -> None:
        """Revoke after an actual chunk, then require the next guarded phase to stop."""
        original_guard(lease, execution)
        calls[0] += 1
        if calls[0] == 5:
            leases.apply(revoke(request))

    monkeypatch.setattr(leases, "guard", at_boundary)
    jobs = FileJobs(store, leases)
    try:
        outcome = jobs.compute(invocation)
        assert calls[0] == 5
        assert outcome["state"] == "CANCELLED" and outcome["result"] is None
        assert jobs.health()["safe"] is True and jobs.health()["active_executions"] == []
    finally:
        jobs.close()


def unix_request(path: Path, route: str, value: dict[str, Any], method: str) -> dict[str, Any]:
    """Exercise actual Unix HTTP framing and kernel peer credentials for this local unit."""
    body = canonical(value)
    head = (
        f"{method} {route} HTTP/1.1\r\nHost: localhost\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        client.sendall(head + body)
        response = bytearray()
        while chunk := client.recv(65536):
            response.extend(chunk)
            assert len(response) < 1024 * 1024
    header, content = bytes(response).split(b"\r\n\r\n", 1)
    assert header.startswith(b"HTTP/1.0 200") or header.startswith(b"HTTP/1.1 200"), content
    result = json.loads(content)
    assert isinstance(result, dict)
    return result


def test_private_unix_endpoint_consumes_actual_file_and_queries_original_receipt(
    prepared: tuple[MemoryStore, UseLeases, dict[str, Any], dict[str, Any]],
) -> None:
    """Check actual local sockets without inventing Controller or Node facts."""
    store, leases, request, invocation = prepared
    jobs = ProcessFileJobs(store, leases)
    try:
        with tempfile.TemporaryDirectory(prefix="rg-memory-use-") as directory:
            path = Path(directory) / "local.sock"
            server = UnixServer(str(path), handler_type(store, jobs, leases))
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                admitted = unix_request(path, "/v1/memory-use/control", request, "POST")
                observed = unix_request(path, "/v1/memory-use/query", request, "GET")
                assert observed == {"receipt": admitted, "effective": True}
                actual = unix_request(path, "/v1/file-jobs/compute", invocation, "POST")
                assert actual["execution_id"] == invocation["execution_id"]
                assert actual["state"] in {"RUNNING", "COMPLETED"}
                for worker in list(jobs._workers.values()):
                    worker.join(timeout=5)
                    assert not worker.is_alive()
                actual = unix_request(
                    path,
                    "/v1/file-jobs/status",
                    {"execution_id": invocation["execution_id"]},
                    "GET",
                )
                assert actual["state"] == "COMPLETED"
                quota = unix_request(
                    path,
                    "/v1/file-jobs/quota",
                    {"execution_id": invocation["execution_id"]},
                    "GET",
                )
                assert quota["pid"] > 0
                assert quota["declaration"]["schema"] == "roboguide.file-worker-os-quota/v0.1"
                assert quota["kernel_proof"]["pid"] == quota["pid"]
                assert quota["kernel_proof"]["cpu_seconds"] == [5, 5]
                fenced = unix_request(path, "/v1/memory-use/control", revoke(request), "POST")
                assert fenced["status"] == "revoked"
                assert (
                    unix_request(path, "/v1/memory-use/query", request, "GET")["effective"] is False
                )
                assert unix_request(path, "/v1/file-jobs/compute", invocation, "POST") == actual
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                assert not thread.is_alive()
    finally:
        jobs.close()
