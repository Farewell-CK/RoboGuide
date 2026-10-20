"""Inject real SQL write failures while two actual software Nodes retain their own sessions."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import sqlite3
import ssl
import subprocess
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from check_artifact_authority_processes import call
from check_state_verifier_processes import save, wait_for


def sql_evidence(database: Path) -> tuple[str, dict[str, Any]]:
    """Independently capture complete bounded committed SQL rows and their checkpoint identity."""
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        rows = {}
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            values = connection.execute("SELECT * FROM " + quoted + " LIMIT 32769").fetchall()
            if len(values) > 32768:
                raise ValueError("authority SQL oracle exceeds its original bound")
            rows[name] = sorted(json.dumps(value, sort_keys=True) for value in values)
        encoded = json.dumps(rows, sort_keys=True).encode()
        if len(encoded) > 16 * 1024 * 1024:
            raise ValueError("authority SQL oracle exceeds its original byte bound")
        checkpoint = connection.execute(
            "SELECT event_sequence,schema,checkpoint_json FROM controller_checkpoint "
            "WHERE singleton=1"
        ).fetchone()
        assert checkpoint is not None and "checkpoint-chain" not in checkpoint[1]
        sequence, schema, payload = checkpoint
        independent = hashlib.sha256(b"roboguide.application-commit/v1")
        independent.update(len(schema.encode()).to_bytes(8, "little"))
        independent.update(schema.encode())
        independent.update(sequence.to_bytes(8, "little"))
        independent.update(len(payload.encode()).to_bytes(8, "little"))
        independent.update(payload.encode())
        return hashlib.sha256(encoded).hexdigest(), {
            "checkpoint_identity": independent.hexdigest(),
            "event_sequence": sequence,
            "checkpoint_json_digest": "sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
        }


def run(
    directory: Path,
    exchange: Callable[..., tuple[int, dict[str, Any]]],
    origin: str,
    administrator: ssl.SSLContext,
    viewer: ssl.SSLContext,
    controller: subprocess.Popen[bytes],
    candidate: dict[str, Any],
    cas: str,
    source: ssl.SSLContext,
    source_leaf: str,
    monitoring: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Require real degraded reads, unavailable Node facts and exact idempotent recovery twice."""
    database = directory / "controller.sqlite3"
    records: list[dict[str, Any]] = []
    previous: tuple[dict[str, Any], dict[str, Any]] | None = None
    for kind in ("save", "commit"):
        log_offsets = {
            name: (directory / (name + "-node.log")).stat().st_size
            for name in ("source", "consumer")
        }
        target = "controller_checkpoint" if kind == "save" else "event_log_metadata"
        condition = "" if kind == "save" else "WHEN NEW.key='journal_committed_revision' "

        def disabled() -> dict[str, Any] | None:
            """Observe the actual first failure without requesting a state transition."""
            status, value = exchange("/v1/authority-status")
            assert status == 200, value
            return value if value["write_enabled"] is False else None

        slow = None
        if kind == "save":
            upload_id = "cert-" + source_leaf + "-write-failure-" + uuid.uuid4().hex
            assert (
                call(
                    cas,
                    source,
                    "POST",
                    "/v1/artifact-uploads",
                    json.dumps({"upload_id": upload_id}).encode(),
                )[0]
                == 201
            )
            endpoint = urlsplit(cas)
            assert endpoint.hostname is not None and endpoint.port is not None
            slow = source.wrap_socket(
                socket.create_connection((endpoint.hostname, endpoint.port), timeout=5),
                server_hostname="localhost",
            )
            slow.sendall(
                (
                    f"POST /v1/artifact-uploads/{upload_id}/content HTTP/1.1\r\n"
                    "Host: localhost\r\nContent-Length: 2\r\nConnection: close\r\n\r\na"
                ).encode()
            )
            partial = directory / "cas/staging" / (upload_id + ".partial")
            wait_for(
                lambda path=partial: path.stat().st_size == 1,
                "actual upload holds one accepted staged byte",
            )
        try:
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TRIGGER actual_authority_failure BEFORE INSERT ON "
                    + target
                    + " "
                    + condition
                    + "BEGIN SELECT RAISE(ABORT,'actual authority SQL fault'); END"
                )
            original = wait_for(disabled, "actual Controller write-disabled mode")
            if slow is not None:
                slow.sendall(b"b")
                response = http.client.HTTPResponse(slow)
                response.begin()
                assert response.status == 503, response.read(2048)
                response.close()
                assert not partial.exists()
        finally:
            if slow is not None:
                slow.close()
        assert controller.poll() is None
        assert original["failure"]["restart_required"] is False, original
        frozen_digest, frozen = sql_evidence(database)
        assert original["checkpoint_identity"] == frozen["checkpoint_identity"]
        command = {
            "schema_version": "roboguide.authority-write-recovery/v0.1",
            "command_id": "actual-authority-repair-" + uuid.uuid4().hex,
            "process_id": original["process_id"],
            "generation": original["generation"],
            "checkpoint_identity": original["checkpoint_identity"],
            "authority_revision": original["authority_revision"],
        }
        save(directory / f"authority-{kind}-disabled.json", original)
        assert exchange("/healthz")[0] == 200
        assert exchange("/readyz")[0] == 503
        views = {path: exchange(path) for path in ("/v1/execution-attempts", "/v1/inventory")}
        save(directory / f"authority-{kind}-views.json", views)
        assert all(status == 200 for status, _ in views.values()), views
        assert exchange("/v1/missions", candidate)[0] == 503
        assert exchange("/v1/authority/recover", command, context=viewer)[0] == 403
        for field, changed in (
            ("process_id", "different-process"),
            ("generation", command["generation"] + 1),
            ("checkpoint_identity", "0" * 64),
            ("authority_revision", command["authority_revision"] + 1),
        ):
            assert exchange("/v1/authority/recover", {**command, field: changed})[0] == 409
        assert exchange("/v1/authority/recover", command)[0] == 503
        artifact_digest = json.loads((directory / "manifest.json").read_bytes())["artifact"][
            "content_digest"
        ]
        assert call(cas, viewer, "GET", "/v1/artifacts/" + artifact_digest)[0] == 503
        if previous is not None:
            assert exchange("/v1/authority/recover", previous[0]) == (200, previous[1])
            assert disabled() == original
        status, metrics = call(origin, administrator, "GET", "/metrics")
        assert status == 200 and b"roboguide_authority_write_enabled 0\n" in metrics

        def node_unavailable(offsets: dict[str, int] = log_offsets) -> bool:
            """Read original real Node logs; no fabricated protocol facts or device observations."""
            return all(
                "Controller fact acceptance unavailable"
                in (directory / (name + "-node.log")).read_bytes()[offsets[name] :].decode()
                for name in ("source", "consumer")
            )

        wait_for(
            node_unavailable, "actual software Nodes observe unavailable application acceptance"
        )
        later_views = {path: exchange(path) for path in views}
        save(directory / f"authority-{kind}-later-views.json", later_views)
        assert later_views["/v1/execution-attempts"] == views["/v1/execution-attempts"]
        original_inventory, later_inventory = (
            values["/v1/inventory"] for values in (views, later_views)
        )
        assert later_inventory[0] == original_inventory[0] == 200
        assert (
            set(later_inventory[1])
            == set(original_inventory[1])
            == {"schema_version", "observed_at_ms", "nodes"}
        )
        assert later_inventory[1]["nodes"] == original_inventory[1]["nodes"]
        assert later_inventory[1]["schema_version"] == original_inventory[1]["schema_version"]
        # Query time may advance; every original source/receive/liveness time above remains exact.
        assert later_inventory[1]["observed_at_ms"] >= original_inventory[1]["observed_at_ms"]
        assert disabled() == original
        assert sql_evidence(database) == (frozen_digest, frozen)
        if monitoring is not None:
            monitoring("firing", kind)
            assert sql_evidence(database) == (frozen_digest, frozen)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DROP TRIGGER actual_authority_failure")
        status, receipt = exchange("/v1/authority/recover", command)
        assert status == 200 and receipt["command"] == command, receipt
        assert receipt["authority_subject"] == "file-administrator", receipt
        assert receipt["authority_revision"] == original["authority_revision"] + 1
        assert exchange("/v1/authority/recover", command) == (200, receipt)
        assert (
            exchange("/v1/authority/recover", {**command, "generation": command["generation"] + 1})[
                0
            ]
            == 409
        )
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            journal = connection.execute(
                "SELECT resulting_state_digest,checkpoint_event_sequence,authority_revision "
                "FROM control_command_journal WHERE command_id=?",
                ("write-recovery:" + command["command_id"],),
            ).fetchall()
        assert journal == [
            (
                frozen["checkpoint_json_digest"],
                frozen["event_sequence"],
                receipt["authority_revision"],
            )
        ]
        wait_for(lambda: exchange("/readyz")[0] == 200, "original authority explicitly recovers")
        if monitoring is not None:
            monitoring("resolved", kind)
        assert controller.poll() is None
        record = {
            "kind": kind,
            "original": original,
            "command": command,
            "receipt": receipt,
            "original_sql_digest": frozen_digest,
            "original_checkpoint": frozen,
            "original_node_acceptance_unavailable": True,
            "disabled_writes_committed": 0,
        }
        save(directory / f"authority-{kind}-recovered.json", record)
        records.append(record)
        previous = command, receipt
    return {
        "passed": True,
        "actual_sql_faults": 2,
        "actual_recovery_commands": 2,
        "inflight_upload_fenced_before_second_byte": True,
        "same_controller_process": True,
        "devices": 0,
        "records": records,
    }
