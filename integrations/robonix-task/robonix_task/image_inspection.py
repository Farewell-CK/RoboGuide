"""Inspect bounded Node-staged image bytes without network or robot authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import sys
import tempfile
import threading
import zlib
from pathlib import Path
from typing import Any, cast

from robonix_task.journal import Conflict, canonical

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
_PNG = b"\x89PNG\r\n\x1a\n"
_SOF = frozenset({0xC0, 0xC1, 0xC2})


def _dimensions(width: int, height: int) -> None:
    """Reject empty or oversized declarations without allocating decoded pixel buffers."""
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > MAX_PIXELS:
        raise ValueError("image_dimensions_exceed_bound")


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    """Check the bounded PNG container, original chunk CRCs and dimension declaration."""
    offset = 8
    dimensions: tuple[int, int] | None = None
    saw_data = False
    chunks = 0
    while offset < len(raw):
        if offset + 12 > len(raw) or chunks >= 65536:
            raise ValueError("invalid_png_chunks")
        size = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(raw) or any(
            c not in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for c in kind
        ):
            raise ValueError("invalid_png_chunk_size_or_type")
        data = raw[offset + 8 : end - 4]
        crc = struct.unpack_from(">I", raw, end - 4)[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise ValueError("png_chunk_crc_mismatch")
        if chunks == 0 and kind != b"IHDR":
            raise ValueError("png_ihdr_required")
        if kind == b"IHDR":
            if dimensions is not None or size != 13:
                raise ValueError("invalid_png_ihdr")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (
                colour not in depths
                or depth not in depths[colour]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise ValueError("unsupported_png_header")
            _dimensions(width, height)
            dimensions = (width, height)
        elif kind == b"IDAT":
            saw_data = True
        elif kind == b"IEND":
            if size or end != len(raw) or not saw_data or dimensions is None:
                raise ValueError("invalid_png_end")
            return dimensions
        offset = end
        chunks += 1
    raise ValueError("png_end_missing")


def _jpeg_dimensions(raw: bytes) -> tuple[int, int]:
    """Check bounded JPEG marker framing and SOF dimensions without entropy decoding."""
    offset = 2
    dimensions: tuple[int, int] | None = None
    saw_scan = False
    in_scan = False
    markers = 0
    while offset < len(raw) and markers < 65536:
        if in_scan:
            found = raw.find(b"\xff", offset)
            if found < 0:
                break
            offset = found
        if raw[offset] != 0xFF:
            raise ValueError("invalid_jpeg_marker")
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            break
        kind = raw[offset]
        offset += 1
        if in_scan and (kind == 0 or 0xD0 <= kind <= 0xD7):
            continue
        in_scan = False
        markers += 1
        if kind == 0xD9:
            if dimensions is None or not saw_scan or offset != len(raw):
                raise ValueError("invalid_jpeg_end")
            return dimensions
        if kind in {0, 0xD8, 0x01} or 0xD0 <= kind <= 0xD7 or offset + 2 > len(raw):
            raise ValueError("invalid_jpeg_marker_sequence")
        size = struct.unpack_from(">H", raw, offset)[0]
        end = offset + size
        if size < 2 or end > len(raw):
            raise ValueError("invalid_jpeg_segment_length")
        data = raw[offset + 2 : end]
        if kind in _SOF:
            if dimensions is not None or len(data) < 6:
                raise ValueError("invalid_jpeg_sof")
            depth, height, width, components = struct.unpack_from(">BHHB", data)
            if depth != 8 or components not in {1, 3} or len(data) != 6 + 3 * components:
                raise ValueError("unsupported_jpeg_sof")
            _dimensions(width, height)
            dimensions = (width, height)
        elif 0xC0 <= kind <= 0xCF and kind not in {0xC4, 0xC8, 0xCC}:
            raise ValueError("unsupported_jpeg_encoding")
        if kind == 0xDA:
            if dimensions is None or not data or len(data) != 1 + 2 * data[0] + 3:
                raise ValueError("invalid_jpeg_scan_header")
            saw_scan = True
            in_scan = True
        offset = end
    raise ValueError("jpeg_end_missing")


def inspect_image_bytes(raw: bytes, content_digest: str) -> dict[str, Any]:
    """Verify original bytes and bounded container dimensions, never pixel or semantic quality."""
    if not isinstance(content_digest, str) or _DIGEST.fullmatch(content_digest) is None:
        raise ValueError("canonical_sha256_required")
    if not 0 < len(raw) <= MAX_IMAGE_BYTES:
        raise ValueError("image_byte_limit_exceeded")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != content_digest:
        raise ValueError("artifact_digest_mismatch")
    if raw.startswith(_PNG):
        width, height = _png_dimensions(raw)
        encoding, media_type = "PNG", "image/png"
    elif raw.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(raw)
        encoding, media_type = "JPEG", "image/jpeg"
    else:
        raise ValueError("unsupported_image_container")
    return {
        "content_digest": content_digest,
        "byte_size": len(raw),
        "format": encoding,
        "media_type": media_type,
        "width": width,
        "height": height,
        "container_structure_checked": True,
        "pixel_decode_verified": False,
        "visual_quality": "not_evaluated",
    }


def _no_links(path: Path) -> None:
    """Reject existing symbolic links and Windows reparse points in an absolute path."""
    for part in [*reversed(path.parents), path]:
        information = part.lstat()
        if stat.S_ISLNK(information.st_mode) or getattr(
            information, "st_file_attributes", 0
        ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("symbolic_or_reparse_path_forbidden")


class ImageInspector:
    """Keep immutable local inspection receipts while consuming only deployment-owned inputs."""

    def __init__(
        self, root: Path, node_id: str, *, input_root: Path, max_records: int = 1000
    ) -> None:
        """Fix allowed paths and identity; acquire one owner and refuse unknown journal formats."""
        if not isinstance(node_id, str) or not node_id.strip() or max_records <= 0:
            raise ValueError("explicit_node_and_positive_receipt_bound_required")
        self.root = root.absolute()
        self.input_root = input_root.absolute()
        self.node_id = node_id
        self.max_records = max_records
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _no_links(self.root)
        _no_links(self.input_root)
        if not self.input_root.is_dir():
            raise ValueError("input_directory_required")
        self.receipts = self.root / "receipts"
        self.receipts.mkdir(exist_ok=True, mode=0o700)
        _no_links(self.receipts)
        self.lock = threading.RLock()
        self.owner = (self.root / "owner.lock").open("a+b")
        self.db: sqlite3.Connection | None = None
        self.input_fd: int | None = None
        try:
            if sys.platform == "win32":
                import msvcrt

                self.owner.seek(0)
                if not self.owner.read(1):
                    self.owner.write(b"0")
                    self.owner.flush()
                self.owner.seek(0)
                msvcrt.locking(self.owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.input_fd = os.open(
                    self.input_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
            db_path = self.root / "inspection.sqlite3"
            if db_path.is_symlink():
                raise Conflict("inspection_journal_symlink_forbidden")
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version not in {0, 1} or (version == 0 and tables):
                raise Conflict("unsupported_inspection_journal_generation")
            if version == 1 and {row[0] for row in tables} != {"meta", "receipts"}:
                raise Conflict("incomplete_inspection_journal")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            profile = canonical(
                {
                    "schema": "roboguide.image-inspection-store/v0.1",
                    "node_id": node_id,
                    "root": str(self.root),
                    "input_root": str(self.input_root),
                    "max_records": max_records,
                }
            )
            if version == 0:
                with self.db:
                    self.db.execute("BEGIN IMMEDIATE")
                    self.db.execute("CREATE TABLE meta (profile TEXT NOT NULL)")
                    self.db.execute(
                        "CREATE TABLE receipts (attempt TEXT PRIMARY KEY, "
                        "request TEXT NOT NULL, receipt TEXT NOT NULL, digest TEXT NOT NULL)"
                    )
                    self.db.execute("INSERT INTO meta VALUES (?)", (profile,))
                    self.db.execute("PRAGMA user_version=1")
            elif self.db.execute("SELECT profile FROM meta").fetchall() != [(profile,)]:
                raise Conflict("inspection_store_identity_changed")
            rows = self.db.execute("SELECT attempt,request,receipt,digest FROM receipts").fetchall()
            if len(rows) > max_records:
                raise Conflict("inspection_receipt_bound_exceeded")
            for attempt, request, receipt, digest in rows:
                self._verify_receipt(attempt, request, receipt, digest)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release this store after handlers stop; original terminal receipts remain on disk."""
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.input_fd is not None:
            os.close(self.input_fd)
            self.input_fd = None
        self.owner.close()

    def _connection(self) -> sqlite3.Connection:
        """Refuse any operation after the single store owner was closed."""
        if self.db is None:
            raise Conflict("inspection_store_closed")
        return self.db

    def _read_input(self, value: object) -> bytes:
        """Read one bounded regular file through anchored no-follow directory descriptors."""
        if not isinstance(value, str) or not value or "://" in value:
            raise ValueError("local_artifact_path_required")
        path = Path(value)
        if ".." in path.parts:
            raise ValueError("artifact_path_traversal_forbidden")
        path = path if path.is_absolute() else self.input_root / path
        try:
            relative = path.relative_to(self.input_root)
        except ValueError:
            raise ValueError("artifact_path_outside_input_root") from None
        if not relative.parts:
            raise ValueError("artifact_file_required")
        descriptor: int | None = None
        directory: int | None = None
        try:
            if sys.platform != "win32" and self.input_fd is not None:
                directory = os.dup(self.input_fd)
                for part in relative.parts[:-1]:
                    following = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                    os.close(directory)
                    directory = following
                descriptor = os.open(
                    relative.parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
            else:
                _no_links(path)
                descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
            information = os.fstat(descriptor)
            if (
                not stat.S_ISREG(information.st_mode)
                or not 0 < information.st_size <= MAX_IMAGE_BYTES
            ):
                raise ValueError("regular_bounded_artifact_required")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                raw = stream.read(MAX_IMAGE_BYTES + 1)
                if (
                    len(raw) != information.st_size
                    or os.fstat(stream.fileno()).st_mtime_ns != information.st_mtime_ns
                ):
                    raise ValueError("artifact_changed_during_read")
            return raw
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)

    def _verify_receipt(
        self, attempt: str, request: str, receipt: str, digest: str
    ) -> dict[str, Any]:
        """Verify retained bytes and their original input identity without rereading an image."""
        value = json.loads(receipt)
        if (
            not isinstance(value, dict)
            or canonical(value) != receipt
            or hashlib.sha256(receipt.encode()).hexdigest() != digest
            or value.get("attempt_id") != attempt
            or value.get("request_digest")
            != "sha256:" + hashlib.sha256(request.encode()).hexdigest()
            or value.get("node_id") != self.node_id
            or value.get("state") not in {"SUCCEEDED", "FAILED"}
        ):
            raise Conflict("retained_inspection_receipt_corrupt")
        self._materialize(value, receipt.encode())
        return value

    def _materialize(self, receipt: dict[str, Any], raw: bytes) -> None:
        """Reproduce only exact committed receipt bytes at a fixed owner-selected file name."""
        path = self.receipts / (
            hashlib.sha256(receipt["attempt_id"].encode()).hexdigest() + ".json"
        )
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.read_bytes() != raw:
                raise Conflict("inspection_result_file_corrupt")
            return
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.receipts, prefix=".receipt-", delete=False
            ) as out:
                temporary = Path(out.name)
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.link(temporary, path)
            if sys.platform != "win32":
                descriptor = os.open(self.receipts, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def inspect(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return the original terminal receipt on retry; a changed accepted attempt conflicts."""
        if set(body) != {
            "attempt_id",
            "invocation",
            "artifact_path",
            "content_digest",
            "producer_receipt",
        }:
            raise ValueError("exact_image_inspection_fields_required")
        request = canonical(body)
        if len(request.encode()) > 65536:
            raise ValueError("inspection_request_limit_exceeded")
        body = cast(dict[str, Any], json.loads(request))
        attempt, invocation, producer = (
            body["attempt_id"],
            body["invocation"],
            body["producer_receipt"],
        )
        if not isinstance(attempt, str) or not 0 < len(attempt) <= 1024 or not attempt.strip():
            raise ValueError("original_attempt_required")
        if not isinstance(invocation, dict) or not isinstance(producer, dict) or not producer:
            raise ValueError("original_invocation_and_producer_receipt_required")
        intent = invocation.get("intent", invocation)
        if not isinstance(intent, dict) or intent.get("operation") not in (
            "image.inspect@v1",
            {"namespace": "image", "name": "inspect", "version": "v1"},
        ):
            raise ValueError("image_inspection_operation_required")
        parameters = intent.get("parameters")
        if (
            not isinstance(parameters, dict)
            or set(parameters) != {"producer_task_id", "producer_role_id", "producer_node_id"}
            or any(
                not isinstance(value, str) or not 0 < len(value) <= 1024 or not value.strip()
                for value in parameters.values()
            )
        ):
            raise ValueError("exact_producer_binding_parameters_required")
        if "execution_id" in invocation and invocation["execution_id"] != attempt:
            raise ValueError("attempt_and_execution_differ")
        for field in ("mission_id", "task_id"):
            if not isinstance(invocation.get(field), str) or not invocation[field].strip():
                raise ValueError("original_mission_and_task_required")
        content_digest = body["content_digest"]
        if not isinstance(content_digest, str) or _DIGEST.fullmatch(content_digest) is None:
            raise ValueError("canonical_sha256_required")
        with self.lock:
            db = self._connection()
            row = db.execute(
                "SELECT request,receipt,digest FROM receipts WHERE attempt=?", (attempt,)
            ).fetchone()
            if row is not None:
                if row[0] != request:
                    raise Conflict("inspection_attempt_request_changed")
                return self._verify_receipt(attempt, *row)
            if db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] >= self.max_records:
                raise Conflict("inspection_receipt_bound_exceeded")
            receipt: dict[str, Any] = {
                "schema_version": "roboguide.image-inspection/v0.1",
                "node_id": self.node_id,
                "attempt_id": attempt,
                "handle": hashlib.sha256(attempt.encode()).hexdigest(),
                "request_digest": "sha256:" + hashlib.sha256(request.encode()).hexdigest(),
                "invocation": invocation,
                "producer_receipt": producer,
                "producer_authority_verified_by_inspector": False,
                "content_digest": content_digest,
                "motion_authorized": False,
                "state": "FAILED",
                "result": None,
                "detail": "artifact_unavailable_or_unsafe",
            }
            try:
                receipt["result"] = inspect_image_bytes(
                    self._read_input(body["artifact_path"]), content_digest
                )
                receipt.update(
                    state="SUCCEEDED", detail="digest_and_container_checked_pixels_unverified"
                )
            except ValueError as error:
                receipt["detail"] = str(error)
            except OSError:
                pass
            encoded = canonical(receipt)
            with db:
                db.execute(
                    "INSERT INTO receipts VALUES (?,?,?,?)",
                    (attempt, request, encoded, hashlib.sha256(encoded.encode()).hexdigest()),
                )
            self._materialize(receipt, encoded.encode())
            return cast(dict[str, Any], json.loads(encoded))

    def status(self, handle: str) -> dict[str, Any]:
        """Read one durable receipt by its fixed attempt hash; never perform another inspection."""
        if not isinstance(handle, str) or re.fullmatch(r"[a-f0-9]{64}", handle) is None:
            raise ValueError("inspection_handle_required")
        with self.lock:
            for attempt, request, receipt, digest in self._connection().execute(
                "SELECT attempt,request,receipt,digest FROM receipts"
            ):
                if hashlib.sha256(attempt.encode()).hexdigest() == handle:
                    return self._verify_receipt(attempt, request, receipt, digest)
        raise ValueError("unknown_inspection_handle")
