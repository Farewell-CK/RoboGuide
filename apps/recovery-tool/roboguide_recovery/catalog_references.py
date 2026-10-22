"""Verify original Controller Catalog/lineage claims against exact owners in one cold bundle."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import archive_references, artifact_layout
from .snapshot_references import OperationBudget, canonical

SCHEMA = "roboguide.recovery-profile/v0.10"
SUPPORTED = {SCHEMA, "roboguide.recovery-profile/v0.11", archive_references.SCHEMA}
WRAPPER = "roboguide.controller-checkpoint/v48"
WRAPPERS = {WRAPPER, archive_references.WRAPPER}
CODEC = "domain.EventPayload.json/v29"
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _key(value: Any) -> tuple[str, str]:
    """Retain exact semantic selectors without treating their names as content paths."""
    if (
        not isinstance(value, dict)
        or set(value) != {"memory_id", "revision_id"}
        or any(not isinstance(item, str) or not 0 < len(item) <= 1024 for item in value.values())
    ):
        raise ValueError("original Catalog reference selector is invalid")
    return value["memory_id"], value["revision_id"]


def _digest(value: object) -> str:
    """Use canonical JSON for generic metadata, which contains no floating-point fields."""
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _record(value: Any) -> dict[str, Any]:
    """Refuse incomplete original authority, dependency identities or retirement evidence."""
    fields = {
        "schema",
        "selector",
        "tenant_id",
        "principal_id",
        "authority_digest",
        "generation",
        "admitted_at_ms",
        "dependencies",
        "retirement",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["schema"] != "roboguide.artifact-catalog-reference/v0.1"
        or type(value["generation"]) is not int
        or value["generation"] != 1
        or type(value["admitted_at_ms"]) is not int
        or not 0 <= value["admitted_at_ms"] < 2**64
        or any(
            not isinstance(value[key], str) or not value[key].strip()
            for key in ("tenant_id", "principal_id")
        )
        or not isinstance(value["authority_digest"], str)
        or SHA256.fullmatch(value["authority_digest"]) is None
        or not isinstance(value["dependencies"], list)
        or len(value["dependencies"]) > 65
    ):
        raise ValueError("original Catalog reference authority is invalid")
    own = _key(value["selector"])
    seen = set()
    for dependency in value["dependencies"]:
        if (
            not isinstance(dependency, dict)
            or set(dependency)
            != {
                "kind",
                "store_owner_id",
                "provider_id",
                "selector",
                "manifest_digest",
                "typed_map",
                "content_digest",
                "byte_size",
            }
            or dependency["kind"] not in {"catalog", "lineage"}
            or not isinstance(dependency["store_owner_id"], str)
            or re.fullmatch(r"artifact-store:[0-9a-f]{64}", dependency["store_owner_id"]) is None
            or not isinstance(dependency["provider_id"], str)
            or not dependency["provider_id"].strip()
            or any(
                not isinstance(dependency[key], str) or SHA256.fullmatch(dependency[key]) is None
                for key in ("manifest_digest", "content_digest")
            )
            or type(dependency["byte_size"]) is not int
            or not 0 <= dependency["byte_size"] <= 4 * 1024**3
        ):
            raise ValueError("original Catalog dependency identity is invalid")
        key = dependency["kind"], _key(dependency["selector"])
        if key in seen or (key[0] == "catalog") != (key[1] == own):
            raise ValueError("original Catalog dependency purpose or selector conflicts")
        seen.add(key)
        typed = dependency["typed_map"]
        if typed is not None and (
            not isinstance(typed, dict)
            or set(typed) != {"canonical_json", "manifest_digest"}
            or not isinstance(typed["canonical_json"], str)
            or len(typed["canonical_json"].encode()) > 65_536
            or typed["manifest_digest"]
            != "sha256:" + hashlib.sha256(typed["canonical_json"].encode()).hexdigest()
            or not isinstance(json.loads(typed["canonical_json"]), dict)
        ):
            raise ValueError("complete original Spatial reference provenance is invalid")
    retirement = value["retirement"]
    if retirement is not None and (
        not isinstance(retirement, dict)
        or set(retirement) != {"command_id", "principal_id", "proof_digest", "retired_at_ms"}
        or any(
            not isinstance(retirement[key], str) or not retirement[key].strip()
            for key in ("command_id", "principal_id")
        )
        or not isinstance(retirement["proof_digest"], str)
        or SHA256.fullmatch(retirement["proof_digest"]) is None
        or type(retirement["retired_at_ms"]) is not int
        or not value["admitted_at_ms"] <= retirement["retired_at_ms"] < 2**64
    ):
        raise ValueError("Catalog retirement lacks its complete original proof")
    return dict(value)


def _controller(
    database: Path,
    profile_schema: str,
    budget: OperationBudget,
) -> tuple[
    dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], tuple[str, dict[str, Any]]]
]:
    """Replay bounded original heads without deriving replacement time, tenant or permissions."""
    heads: dict[tuple[str, str], dict[str, Any]] = {}
    publications: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    released_commands: dict[str, tuple[str, str]] = {}
    total = 0

    def progress() -> int:
        """Keep every SQL scan inside the original operation deadline."""
        budget.check()
        return 0

    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_EVIDENCE_BYTES)
        connection.set_progress_handler(progress, 1000)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        checkpoint = (
            connection.execute(
                "SELECT schema FROM controller_checkpoint WHERE singleton=1"
            ).fetchone()
            if "controller_checkpoint" in tables
            else None
        )
        current = checkpoint is not None and checkpoint[0] in WRAPPERS
        if "events" not in tables:
            if current:
                raise ValueError("mandatory original Catalog reference authority table is missing")
            return heads, publications
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
        if not {"sequence", "payload_schema", "payload_json"} <= columns:
            if current or profile_schema in SUPPORTED:
                raise ValueError("mandatory Catalog reference event columns are missing")
            return heads, publications
        has_reference_codec = (
            connection.execute(
                "SELECT 1 FROM events WHERE payload_schema=? LIMIT 1", (CODEC,)
            ).fetchone()
            is not None
        )
        if has_reference_codec and (not current or profile_schema not in SUPPORTED):
            raise ValueError(
                "Catalog reference authority requires original wrapper and recovery v0.10"
            )
        if profile_schema not in SUPPORTED:
            return heads, publications
        archived = archive_references.catalog_history(connection, profile_schema, budget)
        archive_references.operations(
            connection,
            profile_schema,
            budget,
            current=checkpoint is not None and checkpoint[0] == archive_references.WRAPPER,
        )
        history = ((row["payload_schema"], row["payload_json"]) for row in archived)
        from itertools import chain

        for schema, raw in chain(
            history,
            connection.execute("SELECT payload_schema,payload_json FROM events ORDER BY sequence"),
        ):
            budget.check()
            total += len(raw.encode())
            if total > budget.max_bytes:
                raise ValueError("Catalog authority scan exceeds its original evidence byte bound")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or len(payload) != 1:
                raise ValueError("Catalog authority scan found an invalid original event envelope")
            if "ArtifactCatalogReferenceTransition" in payload:
                if not current or schema != CODEC:
                    raise ValueError("Catalog reference lacks its original wrapper/codec authority")
                record = _record(payload["ArtifactCatalogReferenceTransition"]["record"])
                key = _key(record["selector"])
                original = heads.get(key)
                if original is None:
                    if record["retirement"] is not None:
                        raise ValueError("Catalog retirement has no original acquisition")
                elif original != record:
                    unchanged, previous = dict(record), dict(original)
                    unchanged["retirement"] = previous["retirement"] = None
                    if (
                        unchanged != previous
                        or original["retirement"] is not None
                        or record["retirement"] is None
                    ):
                        raise ValueError("Catalog reference changed original authority or revived")
                if record["retirement"] is not None:
                    command = record["retirement"]["command_id"]
                    if command in released_commands and released_commands[command] != key:
                        raise ValueError(
                            "Catalog retirement command names another original revision"
                        )
                    released_commands[command] = key
                heads[key] = record
            for kind in ("MemoryManifestPublished", "MapArtifactPublished"):
                if kind not in payload:
                    continue
                manifest = payload[kind]["manifest"]
                key = (
                    _key(manifest["selector"])
                    if kind == "MemoryManifestPublished"
                    else (manifest["map_id"], manifest["revision_id"])
                )
                if key in publications and publications[key] != (kind, manifest):
                    raise ValueError("Catalog publication replaced its immutable original source")
                publications[key] = kind, manifest
        if publications and not current:
            raise ValueError("legacy Catalog ownership is unaccounted; explicit migration required")
        if current and set(heads) != set(publications):
            raise ValueError("Catalog publications and original retained references are incomplete")
    return heads, publications


def validate(
    root: Path,
    components: list[dict[str, Any]],
    files: dict[str, dict[str, Any]],
    budget: OperationBudget,
    profile_schema: str,
) -> None:
    """Resolve each active direct/lineage claim to its own actual bundled owner and CAS file."""
    owners: dict[str, str] = {}
    for component in components:
        budget.check()
        if component["kind"] == "artifact" and artifact_layout.supports(profile_schema):
            owner = artifact_layout.validate_storage(
                root / "stores" / component["component_id"], profile_schema
            )
            assert owner is not None
            if owner["owner_id"] in owners:
                raise ValueError("distinct Artifact components duplicate one original owner")
            owners[owner["owner_id"]] = component["component_id"]
    for component in components:
        budget.check()
        if component["kind"] != "controller":
            continue
        name = f"stores/{component['component_id']}/{component['database']}"
        if name not in files:
            raise ValueError("Catalog authority database is missing")
        heads, publications = _controller(root / name, profile_schema, budget)
        for key, reference in heads.items():
            if key not in publications:
                raise ValueError("original Catalog reference has no actual publication")
            kind, manifest = publications[key]
            direct = [item for item in reference["dependencies"] if item["kind"] == "catalog"]
            artifact = manifest.get("artifact") if kind == "MemoryManifestPublished" else manifest
            if len(direct) != int(artifact is not None):
                raise ValueError("Catalog reference lost its original direct content dependency")
            if direct and not isinstance(artifact, dict):
                raise ValueError("Catalog direct dependency has no original artifact identity")
            if artifact is None:
                artifact = {}
            if direct and (
                direct[0]["content_digest"] != artifact["content_digest"]
                or direct[0]["byte_size"] != artifact["byte_size"]
            ):
                raise ValueError("Catalog reference differs from published original CAS identity")
            for dependency in reference["dependencies"]:
                budget.check()
                source = publications.get(_key(dependency["selector"]))
                if source is None:
                    raise ValueError("Catalog lineage source publication is missing")
                source_kind, source_manifest = source
                source_artifact = (
                    source_manifest.get("artifact")
                    if source_kind == "MemoryManifestPublished"
                    else source_manifest
                )
                if (
                    not isinstance(source_artifact, dict)
                    or source_artifact.get("content_digest") != dependency["content_digest"]
                    or source_artifact.get("byte_size") != dependency["byte_size"]
                ):
                    raise ValueError(
                        "Catalog dependency differs from its exact original source bytes"
                    )
                source_provider = (
                    source_manifest.get("provider_id")
                    if source_kind == "MemoryManifestPublished"
                    else (source_manifest.get("producer_provider_id") or "typed-map-catalog")
                )
                if source_provider != dependency["provider_id"]:
                    raise ValueError("Catalog dependency changed its original semantic provider")
                parent = heads.get(_key(dependency["selector"]))
                if parent is None or parent["tenant_id"] != reference["tenant_id"]:
                    raise ValueError("Catalog lineage lost its original tenant authority")
                if dependency["kind"] == "lineage" and not any(
                    entry["kind"] == "catalog"
                    and all(
                        entry[field] == dependency[field]
                        for field in (
                            "store_owner_id",
                            "provider_id",
                            "selector",
                            "manifest_digest",
                            "typed_map",
                            "content_digest",
                            "byte_size",
                        )
                    )
                    for entry in parent["dependencies"]
                ):
                    raise ValueError("Catalog lineage changed original parent ownership")
                typed = dependency["typed_map"]
                if source_kind == "MapArtifactPublished":
                    if typed is None or json.loads(typed["canonical_json"]) != source_manifest:
                        raise ValueError(
                            "Catalog reference lost complete original Spatial manifest"
                        )
                elif typed is not None or dependency["manifest_digest"] != _digest(source_manifest):
                    raise ValueError(
                        "Catalog reference changed its complete original generic manifest"
                    )
                if reference["retirement"] is not None:
                    continue
                owner_component = owners.get(dependency["store_owner_id"])
                if owner_component is None:
                    raise ValueError("active Catalog reference's actual Artifact owner is absent")
                checksum = dependency["content_digest"][7:]
                path = f"stores/{owner_component}/objects-v1/sha256/{checksum[:2]}/{checksum}"
                content = files.get(path)
                if (
                    content is None
                    or content["kind"] != "content"
                    or content["content_digest"] != dependency["content_digest"]
                    or content["byte_size"] != dependency["byte_size"]
                ):
                    raise ValueError(
                        "active Catalog/lineage reference lacks its exact bundled CAS bytes"
                    )
