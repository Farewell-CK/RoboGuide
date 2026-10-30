"""Read bounded original archives and publish only explicitly selected metadata."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PUBLIC_BYTES = 1024 * 1024
_SECRET = re.compile(
    r"(?i)(?<![a-z0-9_])sk-[a-z0-9_-]{12,}|bearer\s+\S+|/data/workspace/|/home/|/tmp/"
)
_PRIVATE_KEYS = {"authorization", "api_key", "openai_api_key", "environment_overrides", "argv"}


def encode(value: Any) -> bytes:
    """Produce stable finite UTF-8 JSON bytes for publication and independent recomputation."""
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    """Hash content bytes without interpreting them as authority or a digital signature."""
    return hashlib.sha256(raw).hexdigest()


def object_value(value: Any) -> dict[str, Any]:
    """Require an actual JSON object rather than guessing an absent record's meaning."""
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def number(value: Any) -> int | float | None:
    """Retain finite nonnegative measurements; unknown data never becomes zero."""
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value  # type: ignore[no-any-return]


def boolean(value: Any) -> bool | None:
    """Keep only strict original boolean facts, preserving unavailable separately."""
    return value if type(value) is bool else None


def code_identity(value: Any) -> str | None:
    """Accept only an observed full Git object identity, never today's fallback commit."""
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None


def publication_metadata(local: dict[str, Any]) -> dict[str, Any]:
    """Retain a frozen export receipt separately from historical execution identity."""
    fields = ("prepared_utc", "prepared_by", "python_version", "dependency_lock_sha256")
    supplied = object_value(local.get("publication") or {})
    result = {key: supplied.get(key) for key in fields}
    if result["prepared_utc"] is not None:
        stamp = datetime.fromisoformat(result["prepared_utc"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("publication receipt needs an explicit timezone")
    digest = result["dependency_lock_sha256"]
    if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid exporter dependency lock digest")
    result["review_status"] = "awaiting-human-paper-review"
    result["missing_receipt_fields"] = [key for key in fields if result[key] is None]
    public_safe(result)
    return result


def public_safe(value: Any) -> None:
    """Reject private keys, machine paths and credential text in allowlisted projections."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _PRIVATE_KEYS:
                raise ValueError("private configuration key in publication")
            public_safe(key)
            public_safe(child)
    elif isinstance(value, list):
        for child in value:
            public_safe(child)
    elif isinstance(value, str):
        if _SECRET.search(value) or re.search(
            r"https?://[^/\s]*@|[?&](?:token|key|signature)=", value, re.I
        ):
            raise ValueError("sensitive text in publication")


def safe_relative(value: str) -> Path:
    """Refuse absolute, parent, empty and platform-ambiguous publication paths."""
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(p in ("", ".", "..") for p in value.split("/"))
    ):
        raise ValueError("unsafe relative publication path")
    return Path(*path.parts)


class ArchiveReader:
    """Bind original file bytes to explicit local namespaces without writing any source."""

    def __init__(self, roots: dict[str, Path]) -> None:
        """Freeze operator-supplied namespaces; local path bindings never enter public files."""
        if not roots or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", key) for key in roots
        ):
            raise ValueError("invalid archive namespaces")
        self.roots = {key: path.resolve() for key, path in roots.items()}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.paths: dict[str, Path] = {}

    def locate(self, path: Path) -> tuple[str, str]:
        """Resolve only explicitly bound source roots and refuse symlink-based source escape."""
        absolute = path.absolute()
        matches = [(key, root) for key, root in self.roots.items() if absolute.is_relative_to(root)]
        if not matches:
            # Accept a root's canonical spelling, never an archive-provided arbitrary target.
            resolved = absolute.resolve()
            matches = [
                (key, root) for key, root in self.roots.items() if resolved.is_relative_to(root)
            ]
            absolute = resolved
        if not matches:
            raise ValueError("source is outside explicitly bound archives")
        key, root = max(matches, key=lambda item: len(item[1].parts))
        relative = absolute.relative_to(root)
        if any(part in {".env", "emos.env"} for part in relative.parts):
            raise ValueError("credential files are not publication inputs")
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise ValueError("unexpected archive symlink")
        return key, relative.as_posix()

    def read(self, path: Path, *, optional: bool = False) -> tuple[bytes | None, str]:
        """Read at most the source bound and retain a durable missing-evidence observation."""
        namespace, relative = self.locate(path)
        artifact_id = "a-" + sha256((namespace + "/" + relative).encode())[:24]
        original = self.roots[namespace] / relative
        if original.is_file():
            with original.open("rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise ValueError("archive input exceeds bounded reader")
        elif optional:
            raw = None
        else:
            raise ValueError("required archive evidence is inaccessible")
        record = {
            "artifact_id": artifact_id,
            "namespace": namespace,
            "logical_path": relative,
            "available": raw is not None,
            "sha256": sha256(raw) if raw is not None else None,
            "size_bytes": len(raw) if raw is not None else None,
            "backup_status": "local_only" if raw is not None else "unavailable",
            "remote_uri": None,
        }
        if artifact_id in self.artifacts and self.artifacts[artifact_id] != record:
            raise ValueError("source changed during export")
        self.artifacts[artifact_id] = record
        self.paths[artifact_id] = original
        return raw, artifact_id

    def document(self, path: Path, *, optional: bool = False) -> tuple[Any, str]:
        """Decode one original JSON value, recording raw bytes independently of projections."""
        raw, reference = self.read(path, optional=optional)
        return (json.loads(raw) if raw is not None else None), reference

    def verify_unchanged(self) -> None:
        """Rehash every consumed source, including absence, before publishing an index."""
        for artifact_id, path in tuple(self.paths.items()):
            self.read(path, optional=not self.artifacts[artifact_id]["available"])


def write_new(path: Path, raw: bytes) -> None:
    """Create one bounded publication file once, refusing replacement and symbolic links."""
    if len(raw) > MAX_PUBLIC_BYTES:
        raise ValueError("publication file exceeds 1 MiB; shard instead of truncating")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def write_rows(root: Path, stem: str, rows: list[dict[str, Any]]) -> list[str]:
    """Write deterministic bounded JSONL shards without dropping any input record."""
    files: list[str] = []
    block = bytearray()
    for row in rows:
        public_safe(row)
        raw = encode(row)
        if len(raw) > MAX_PUBLIC_BYTES:
            raise ValueError("one publication record exceeds byte bound")
        if block and len(block) + len(raw) > MAX_PUBLIC_BYTES:
            name = f"{stem}-{len(files) + 1:04d}.jsonl"
            write_new(root / name, bytes(block))
            files.append(name)
            block.clear()
        block.extend(raw)
    if block or not files:
        name = f"{stem}-{len(files) + 1:04d}.jsonl"
        write_new(root / name, bytes(block))
        files.append(name)
    return files
