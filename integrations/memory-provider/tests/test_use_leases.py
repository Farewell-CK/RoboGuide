"""Test local lease authority with real bytes/SQLite, without devices or physical outcome claims."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from roboguide_memory_provider import use_leases
from roboguide_memory_provider.__main__ import UnixServer, handler_type
from roboguide_memory_provider.jobs import FileJobs
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
        "source_session_id": uuid.uuid4().hex,
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
    _, leases, request, invocation = prepared
    boot, time_namespace, lower, uncertainty = kernel_clock()
    now = [lower]
    monkeypatch.setattr(
        use_leases, "kernel_clock", lambda: (boot, time_namespace, now[0], uncertainty)
    )
    request = advance(request, 1, lower)
    first = leases.apply(request)
    for generation in range(2, 11):
        now[0] += 1000
        leases.apply(advance(request, generation, now[0]))
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
    _, leases, request, invocation = prepared
    if not before_grant:
        leases.apply(request)
    fence = leases.apply(revoke(request))
    assert fence["status"] == "revoked" and fence["receiver_budget"] is None
    assert leases.apply(revoke(request)) == fence
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
    jobs = FileJobs(store, leases)
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
