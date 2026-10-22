"""Exercise actual Catalog publication/retirement inside the existing owned file-process profile."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import sqlite3
import ssl
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from check_artifact_authority_processes import call, grant
from check_state_verifier_processes import save, wait_for


def configure(directory: Path, admin_leaf: str) -> None:
    """Declare an explicit actual tenant administrator only in this requested software profile."""
    path = directory / "artifact-authorization.json"
    profile = json.loads(path.read_bytes())
    profile["grants"]["sha256:" + admin_leaf] = grant("file-administrator", "admin", "local")
    save(path, profile)


class CatalogRetentionCheck:
    """Observe production Node/TLS/SQL/CAS effects without creating a second execution authority."""

    def __init__(
        self,
        directory: Path,
        cas: str,
        contexts: dict[str, ssl.SSLContext],
        manifest: dict[str, Any],
        exchange: Callable[..., tuple[int, dict[str, Any]]],
    ) -> None:
        """Retain the exact original file publication and actual service credentials."""
        self.directory = directory
        self.cas = cas
        self.contexts = contexts
        self.manifest = manifest
        self.exchange = exchange
        self.database = directory / "controller.sqlite3"
        self.path = self.reference_path(manifest)
        self.original: dict[str, Any] = {}

    @staticmethod
    def reference_path(manifest: dict[str, Any]) -> str:
        """Address one exact original revision without trusting a blob alias as owner identity."""
        selector = manifest["selector"]
        return f"/v1/artifact-references/{selector['memory_id']}/{selector['revision_id']}"

    def observe(self, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read the actual tenant administrator's committed observation, not a generated fixture."""
        path = self.reference_path(manifest or self.manifest)
        status, value = self.exchange(path, endpoint=self.cas)
        assert status == 200, value
        return value

    def rows(self, selector: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
        """Independently read authority events through a readonly native SQLite connection."""
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as connection:
            return [
                (row[0], json.loads(row[1])["ArtifactCatalogReferenceTransition"]["record"])
                for row in connection.execute(
                    "SELECT sequence,payload_json FROM events "
                    "WHERE payload_schema='domain.EventPayload.json/v29' "
                    "AND json_extract(payload_json,'$.ArtifactCatalogReferenceTransition.record."
                    "selector.memory_id')=? AND json_extract(payload_json,"
                    "'$.ArtifactCatalogReferenceTransition.record.selector.revision_id')=? "
                    "ORDER BY sequence",
                    (selector["memory_id"], selector["revision_id"]),
                )
            ]

    def publisher_headers(self) -> dict[str, str]:
        """Use the source's actual Controller-accepted heartbeat session."""
        node = self.manifest["owner"]["node_id"]

        def current_session() -> str | None:
            """Observe only the actual Controller-accepted session for this owned source process."""
            with closing(
                sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
            ) as connection:
                row = connection.execute(
                    "SELECT json_extract(payload_json,'$.NodeSessionLivenessAccepted.session_id') "
                    "FROM events WHERE json_extract(payload_json,"
                    "'$.NodeSessionLivenessAccepted.node_id')=? ORDER BY sequence DESC LIMIT 1",
                    (node,),
                ).fetchone()
            return None if row is None else str(row[0])

        session = str(wait_for(current_session, "actual original source heartbeat session"))
        return {
            "content-type": "application/json",
            "x-roboguide-node-id": node,
            "x-roboguide-session-id": session,
        }

    def publish(self, manifest: dict[str, Any]) -> tuple[int, bytes]:
        """Use the original actual registered Node certificate, provider and upload-owned bytes."""
        selector = manifest["selector"]
        path = f"/v1/memories/{selector['memory_id']}/revisions/{selector['revision_id']}"
        return call(
            self.cas,
            self.contexts["enrolment-first"],
            "POST",
            path,
            json.dumps(manifest).encode(),
            self.publisher_headers(),
        )

    def recover_writer(self, stage: str) -> None:
        """Recover the original failed generation after removing the actual SQL fault."""
        status, before = self.exchange("/v1/authority-status")
        assert status == 200 and before["write_enabled"] is False, before
        command = {
            "schema_version": "roboguide.authority-write-recovery/v0.1",
            "command_id": "catalog-recovery-" + uuid.uuid4().hex,
            **{
                field: before[field]
                for field in (
                    "process_id",
                    "generation",
                    "checkpoint_identity",
                    "authority_revision",
                )
            },
        }
        status, result = self.exchange("/v1/authority/recover", command)
        assert status == 200, result
        save(
            self.directory / ("catalog-" + stage + "-writer-recovery.json"),
            {"before": before, "command": command, "result": result},
        )

    def begin(self, publication_faults: bool = True) -> None:
        """Verify real acquisition and atomic rollback before a distinct publication."""
        self.original = self.observe()
        record = self.original["record"]
        assert record["retirement"] is None and record["generation"] == 1
        assert record["tenant_id"] == "local" and record["principal_id"] == "file-node-0"
        assert len(record["dependencies"]) == 1
        dependency = record["dependencies"][0]
        owner = json.loads((self.directory / "cas/.retention-owner.json").read_bytes())
        assert dependency["store_owner_id"] == owner["owner_id"]
        assert dependency["provider_id"] == self.manifest["provider_id"]
        assert dependency["content_digest"] == self.manifest["artifact"]["content_digest"]
        checksum = dependency["content_digest"][7:]
        content = self.directory / "cas/objects-v1/sha256" / checksum[:2] / checksum
        assert (
            "sha256:" + hashlib.sha256(content.read_bytes()).hexdigest()
            == dependency["content_digest"]
        )
        assert content.stat().st_size == dependency["byte_size"]
        rows = self.rows(record["selector"])
        assert len(rows) == 1 and rows[0][1] == record
        save(self.directory / "catalog-original-acquisition.json", self.original)
        if not publication_faults:
            return
        assert self.publish(self.manifest)[0] == 201
        assert self.observe() == self.original and self.rows(record["selector"]) == rows
        assert (
            self.exchange(self.path, context=self.contexts["viewer"], endpoint=self.cas)[0] == 403
        )
        other = copy.deepcopy(self.manifest)
        other["selector"]["revision_id"] = "sql-rollback-" + uuid.uuid4().hex
        payload = (
            self.directory / "source/node-state/memory/files/source.bin"
        ).read_bytes() + b"\n"
        checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        certificate = self.directory / "pki/enrolment-first.crt"
        leaf = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest()
        upload = "cert-" + leaf + "-catalog-" + uuid.uuid4().hex
        uploader = self.contexts["enrolment-first"]
        assert (
            call(
                self.cas,
                uploader,
                "POST",
                "/v1/artifact-uploads",
                json.dumps({"upload_id": upload}).encode(),
            )[0]
            == 201
        )
        assert (
            call(self.cas, uploader, "POST", f"/v1/artifact-uploads/{upload}/content", payload)[0]
            == 202
        )
        assert (
            call(
                self.cas,
                uploader,
                "POST",
                f"/v1/artifact-uploads/{upload}/finalize",
                json.dumps({"content_digest": checksum, "byte_size": len(payload)}).encode(),
            )[0]
            == 201
        )
        other["artifact"] = {"content_digest": checksum, "byte_size": len(payload)}
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_catalog_reference BEFORE INSERT ON events "
                "WHEN json_type(NEW.payload_json,"
                "'$.ArtifactCatalogReferenceTransition') IS NOT NULL "
                "BEGIN SELECT RAISE(ABORT,'actual Catalog reference fault'); END"
            )
        try:
            status, response = self.publish(other)
            assert status in (500, 503), response
            assert self.rows(other["selector"]) == []
            with closing(sqlite3.connect(self.database)) as connection:
                assert (
                    connection.execute(
                        "SELECT count(*) FROM events WHERE json_extract(payload_json,"
                        "'$.MemoryManifestPublished.manifest.selector.revision_id')=?",
                        (other["selector"]["revision_id"],),
                    ).fetchone()[0]
                    == 0
                )
        finally:
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("DROP TRIGGER reject_catalog_reference")
        self.recover_writer("publication")
        assert self.publish(other)[0] == 201
        other_head = self.observe(other)
        command = {
            "command_id": "retire-extra-" + uuid.uuid4().hex,
            "original_digest": other_head["original_digest"],
        }
        status, retired = self.exchange(
            self.reference_path(other) + "/retire", command, endpoint=self.cas
        )
        assert status == 200 and retired["record"]["retirement"] is not None, retired
        assert self.observe() == self.original
        save(self.directory / "catalog-independent-publication-retired.json", retired)

    def lineages(self) -> dict[str, Any]:
        """Check committed handoff/revision publications against separate parent pins."""
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM events "
                "WHERE payload_schema='domain.EventPayload.json/v29' ORDER BY sequence"
            ).fetchall()
        observed = []
        for row in rows:
            record = json.loads(row[0])["ArtifactCatalogReferenceTransition"]["record"]
            if record["retirement"] is not None:
                continue
            dependencies = record["dependencies"]
            parents = [item for item in dependencies if item["kind"] == "lineage"]
            if not parents:
                continue
            manifest = {"selector": record["selector"]}
            assert self.observe(manifest)["record"] == record
            assert len([item for item in dependencies if item["kind"] == "catalog"]) == 1
            assert all(
                item["store_owner_id"]
                == self.original["record"]["dependencies"][0]["store_owner_id"]
                for item in dependencies
            )
            assert all(item["selector"] != record["selector"] for item in parents)
            observed.append(record)
        assert observed, "actual child workflow did not commit any independent lineage ownership"
        save(self.directory / "catalog-actual-child-lineages.json", observed)
        return {
            "actual_child_publications": len(observed),
            "original_parent_dependencies": sum(
                len([item for item in record["dependencies"] if item["kind"] == "lineage"])
                for record in observed
            ),
        }

    def blocked(self) -> None:
        """Require the running Mission to retain its original Catalog."""
        command = {
            "command_id": "blocked-retirement-" + uuid.uuid4().hex,
            "original_digest": self.original["original_digest"],
        }
        status, value = self.exchange(self.path + "/retire", command, endpoint=self.cas)
        assert status == 409 and self.observe() == self.original, value
        save(self.directory / "catalog-running-retirement-refused.json", value)

    def finish(self) -> dict[str, Any]:
        """Reconcile an unread retirement response after restart and complete revoke proofs."""
        assert self.observe() == self.original, "restart changed original Catalog ownership/time"
        command = {
            "command_id": "retire-original-" + uuid.uuid4().hex,
            "original_digest": self.original["original_digest"],
        }
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER reject_catalog_retirement BEFORE INSERT ON events "
                "WHEN json_extract(NEW.payload_json,"
                "'$.ArtifactCatalogReferenceTransition.record.retirement') IS NOT NULL "
                "BEGIN SELECT RAISE(ABORT,'actual Catalog retirement fault'); END"
            )
        try:
            status, response = self.exchange(self.path + "/retire", command, endpoint=self.cas)
            assert status in (500, 503), response
            rows = self.rows(self.manifest["selector"])
            assert len(rows) == 1 and rows[0][1] == self.original["record"]
        finally:
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("DROP TRIGGER reject_catalog_retirement")
        self.recover_writer("retirement")
        endpoint = urlsplit(self.cas)
        assert endpoint.hostname and endpoint.port
        body = json.dumps(command).encode()
        with self.contexts["admin"].wrap_socket(
            socket.create_connection((endpoint.hostname, endpoint.port), timeout=5),
            server_hostname="localhost",
        ) as transport:
            transport.sendall(
                (
                    f"POST {self.path}/retire HTTP/1.1\r\nHost: localhost\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + body
            )

            def committed() -> dict[str, Any] | None:
                """Observe original committed state while the first client consumes no response."""
                value = self.observe()
                return value if value["record"]["retirement"] is not None else None

            observed = wait_for(committed, "actual unread retirement response reconciliation")
        status, repeated = self.exchange(self.path + "/retire", command, endpoint=self.cas)
        assert status == 200 and repeated["replayed"] is True, repeated
        assert repeated["record"] == observed["record"]
        assert self.publish(self.manifest)[0] in (403, 409)
        rows = self.rows(self.manifest["selector"])
        assert (
            len(rows) == 2
            and rows[0][1] == self.original["record"]
            and rows[1][1] == observed["record"]
        )
        save(self.directory / "catalog-original-retirement.json", observed)
        return {
            "actual_store_owner": self.original["record"]["dependencies"][0]["store_owner_id"],
            "actual_producer_acquisitions": 1,
            "original_generation": 1,
            "retirement_sql_rollback_preserved_reference": True,
            "publication_sql_rollback_left_no_manifest_or_reference": True,
            "unread_retirement_response_reconciled": True,
            "original_ownership_unchanged_after_restart": True,
            "retired_publication_cannot_revive": True,
        }
