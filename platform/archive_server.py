#!/usr/bin/env python3
"""Navigation archive reference platform: SQLite indexes and durable file storage.

No third-party dependencies. Run ``python3 archive_server.py --help``.
The service is deliberately separate from the existing live-monitor WebSocket.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlsplit

MAX_CHUNK = 4 * 1024 * 1024
MAX_MANIFEST = 16 * 1024 * 1024
MAX_FILES = 20_000
MAX_FILE_SIZE = 32 * 1024**3
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class ArchiveError(Exception):
    def __init__(self, status: int, message: str, **details):
        self.status, self.message, self.details = status, message, details
        super().__init__(message)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_session_id(value: str) -> str:
    if not SESSION_ID.fullmatch(value):
        raise ArchiveError(400, "Invalid session_id")
    return value


def validate_file_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise ArchiveError(400, "Invalid file path")
    if value.startswith("/") or "\\" in value or any(ord(c) < 32 for c in value):
        raise ArchiveError(400, "Invalid file path")
    if any(part in ("", ".", "..") or ":" in part or len(part.encode("utf-8")) > 160
           for part in value.split("/")):
        raise ArchiveError(400, "Invalid file path")
    if str(PurePosixPath(value)) != value:
        raise ArchiveError(400, "Invalid file path")
    return value


def canonical_json(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ArchiveError(400, "Invalid JSON values") from None


def sync_directory(directory: Path) -> None:
    # The containing directory must be durable after an atomic rename.
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_mkdir(directory: Path) -> None:
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        sync_directory(path.parent)


class ArchiveStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_root = self.root / "sessions"
        self.sessions_root.mkdir(exist_ok=True)
        self.db_path = self.root / "archive.sqlite3"
        self.lock = threading.RLock()
        with self._db() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'uploading',
                    created_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER
                );
            """)

    @contextmanager
    def _db(self):
        database = sqlite3.connect(self.db_path, timeout=30)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=FULL")
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

    def _row(self, session_id: str):
        validate_session_id(session_id)
        with self._db() as database:
            row = database.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise ArchiveError(404, "Session not found")
        return row

    def _path(self, session_id: str, relative: str, partial: bool = False) -> Path:
        validate_session_id(session_id)
        validate_file_path(relative)
        # Separate staging prevents a user file named x.part from conflicting with x.
        base = self.sessions_root / session_id / ("partial" if partial else "files")
        candidate = base.joinpath(*relative.split("/"))
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise ArchiveError(400, "File path escapes session")
        for ancestor in [candidate, *candidate.parents]:
            if ancestor == self.root:
                break
            if ancestor.is_symlink():
                raise ArchiveError(400, "Symbolic links are not allowed")
        return candidate

    @staticmethod
    def _validate_manifest(session_id: str, manifest: dict) -> None:
        if not isinstance(manifest, dict) or manifest.get("session_id") != session_id:
            raise ArchiveError(400, "Manifest session_id does not match URL")
        if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
            raise ArchiveError(400, "Unsupported schema_version")
        if manifest.get("state") not in {"recording", "completed", "interrupted", "incomplete"}:
            raise ArchiveError(400, "Invalid navigation archive state")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ArchiveError(400, "Invalid files manifest")
        seen = set()
        for file in files:
            if not isinstance(file, dict):
                raise ArchiveError(400, "Invalid file entry")
            path = validate_file_path(file.get("path"))
            size = file.get("size")
            digest = file.get("sha256")
            if type(size) is not int or size < 0 or size > MAX_FILE_SIZE:
                raise ArchiveError(400, "Invalid file size", path=path)
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise ArchiveError(400, "Invalid file sha256", path=path)
            if path in seen:
                raise ArchiveError(400, "Duplicate file path", path=path)
            if size == 0 and digest != hashlib.sha256(b"").hexdigest():
                raise ArchiveError(400, "Invalid checksum for empty file", path=path)
            seen.add(path)
        for path in seen:
            parts = path.split("/")
            if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
                raise ArchiveError(400, "Conflicting file paths", path=path)

    def create(self, session_id: str, manifest: dict) -> dict:
        validate_session_id(session_id)
        self._validate_manifest(session_id, manifest)
        encoded = canonical_json(manifest)
        if len(encoded) > MAX_MANIFEST:
            raise ArchiveError(413, "Manifest is too large")
        digest = hashlib.sha256(encoded).hexdigest()
        with self.lock:
            with self._db() as database:
                previous = database.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if previous:
                    if previous["manifest_sha256"] != digest:
                        old = json.loads(previous["manifest"])
                        old_files = {entry["path"]: entry for entry in old["files"]}
                        new_files = {entry["path"]: entry for entry in manifest["files"]}
                        annotations_only = self._annotation_append(old, manifest)
                        if any(new_files.get(path) != entry for path, entry in old_files.items()) or (not annotations_only and (old["state"] != "recording" or previous["state"] == "complete")):
                            raise ArchiveError(409, "Previously declared files are immutable")
                        directory = self.sessions_root / session_id
                        temporary = directory / "manifest.tmp"
                        with temporary.open("wb") as stream:
                            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
                        os.replace(temporary, directory / "manifest.json"); sync_directory(directory)
                        database.execute("UPDATE sessions SET manifest=?,manifest_sha256=?,state='uploading',completed_at_ms=NULL WHERE session_id=?", (encoded.decode("utf-8"), digest, session_id))
                    # Query after this transaction commits, not through a second connection.
                    if previous["manifest_sha256"] == digest:
                        return self.status(session_id)
                    updated = True
                else:
                    updated = False
                if updated:
                    pass
                else:
                    directory = self.sessions_root / session_id
                    directory.mkdir(exist_ok=True)
                    (directory / "files").mkdir(exist_ok=True)
                    (directory / "partial").mkdir(exist_ok=True)
                    temporary = directory / "manifest.tmp"
                    with temporary.open("wb") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, directory / "manifest.json")
                    sync_directory(directory)
                    sync_directory(self.sessions_root)
                    database.execute("""INSERT INTO sessions
                        (session_id,manifest,manifest_sha256,state,created_at_ms) VALUES (?,?,?,'uploading',?)""",
                        (session_id, encoded.decode("utf-8"), digest, now_ms()))
            # Zero-byte files still need a durable, verifiable entry.
            for file in manifest["files"]:
                if file["size"] == 0:
                    self.put_chunk(session_id, file["path"], 0, file["sha256"], b"")
            return self.status(session_id)

    @staticmethod
    def _annotation_append(old: dict, new: dict) -> bool:
        """Sealed observations stay immutable; only human annotation files may be appended."""
        if old.get("state") == "recording" or new.get("state") != old.get("state"):
            return False
        old_paths = {entry["path"] for entry in old["files"]}
        added = [entry for entry in new["files"] if entry["path"] not in old_paths]
        if not added or any(entry.get("type") != "experiment_annotation" or not re.fullmatch(r"artifacts/experiment_annotation/revision-[0-9]{8}-[A-Za-z0-9-]+\.json", entry["path"]) or entry["size"] > 32768 for entry in added):
            return False
        mutable = {"files", "type_counts", "artifacts_count", "experiment_annotation_revision"}
        if {k: v for k, v in old.items() if k not in mutable} != {k: v for k, v in new.items() if k not in mutable}:
            return False
        before, after = old.get("type_counts", {}), new.get("type_counts", {})
        if {k: v for k, v in before.items() if k != "experiment_annotation"} != {k: v for k, v in after.items() if k != "experiment_annotation"}:
            return False
        return (after.get("experiment_annotation", 0) == before.get("experiment_annotation", 0) + len(added)
                and new.get("artifacts_count", 0) == old.get("artifacts_count", 0) + len(added)
                and new.get("experiment_annotation_revision", 0) == old.get("experiment_annotation_revision", 0) + len(added))

    def status(self, session_id: str) -> dict:
        with self.lock:
            row = self._row(session_id)
            manifest = json.loads(row["manifest"])
            files = []
            for entry in manifest["files"]:
                final = self._path(session_id, entry["path"])
                partial = self._path(session_id, entry["path"], partial=True)
                # Recover a crash between the final chunk fsync and its rename.
                if not final.exists() and partial.is_file() and partial.stat().st_size >= entry["size"]:
                    if partial.stat().st_size == entry["size"] and sha256_file(partial) == entry["sha256"]:
                        durable_mkdir(final.parent)
                        os.replace(partial, final)
                        sync_directory(final.parent)
                    else:
                        partial.unlink()
                    sync_directory(partial.parent)
                complete = final.is_file()
                received = final.stat().st_size if complete else partial.stat().st_size if partial.is_file() else 0
                files.append({**entry, "received_bytes": received, "complete": complete})
            return {"session_id": session_id, "state": row["state"],
                    "archive_state": manifest["state"], "files": files,
                    "completed_at_ms": row["completed_at_ms"]}

    def manifest(self, session_id: str) -> dict:
        return json.loads(self._row(session_id)["manifest"])

    def put_chunk(self, session_id: str, relative: str, offset: int, digest: str, body: bytes) -> dict:
        if type(offset) is not int or offset < 0 or len(body) > MAX_CHUNK:
            raise ArchiveError(400, "Invalid chunk offset or length")
        with self.lock:
            row = self._row(session_id)
            manifest = json.loads(row["manifest"])
            entry = next((file for file in manifest["files"] if file["path"] == relative), None)
            if entry is None:
                raise ArchiveError(404, "File is not in manifest")
            if digest != entry["sha256"]:
                raise ArchiveError(409, "File checksum differs from manifest")
            if offset + len(body) > entry["size"]:
                raise ArchiveError(409, "Chunk exceeds declared file size")
            final = self._path(session_id, relative)
            partial = self._path(session_id, relative, partial=True)
            existing = final if final.exists() else partial
            received = existing.stat().st_size if existing.exists() else 0
            if offset > received:
                raise ArchiveError(409, "Chunk offset has a gap", received_bytes=received)
            if offset < received or final.exists():
                if offset + len(body) > received:
                    raise ArchiveError(409, "Retry overlaps only part of a received chunk", received_bytes=received)
                with existing.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(body)) != body:
                        raise ArchiveError(409, "Retried chunk contains different bytes", received_bytes=received)
                return {"path": relative, "received_bytes": received, "complete": final.exists()}
            if row["state"] == "complete":
                raise ArchiveError(409, "Completed session is immutable")
            if not body and entry["size"] != 0:
                raise ArchiveError(400, "Empty chunk makes no progress")
            durable_mkdir(partial.parent)
            with partial.open("ab") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(partial.parent)
            received += len(body)
            if received == entry["size"]:
                if sha256_file(partial) != digest:
                    # Discard corrupt staging bytes so a subsequent upload can restart.
                    partial.unlink()
                    sync_directory(partial.parent)
                    raise ArchiveError(422, "File checksum verification failed", received_bytes=0)
                durable_mkdir(final.parent)
                os.replace(partial, final)
                sync_directory(final.parent)
                sync_directory(partial.parent)
            return {"path": relative, "received_bytes": received, "complete": final.exists()}

    def complete(self, session_id: str) -> dict:
        with self.lock:
            row = self._row(session_id)
            manifest = json.loads(row["manifest"])
            if manifest["state"] == "recording":
                raise ArchiveError(409, "Navigation is still recording")
            missing, corrupt = [], []
            for entry in manifest["files"]:
                path = self._path(session_id, entry["path"])
                if not path.is_file():
                    missing.append(entry["path"])
                elif path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
                    corrupt.append(entry["path"])
            if missing or corrupt:
                raise ArchiveError(409, "Archive is not complete or has damaged files", missing=missing, corrupt=corrupt)
            with self._db() as database:
                database.execute("""UPDATE sessions SET state='complete',
                    completed_at_ms=COALESCE(completed_at_ms,?) WHERE session_id=?""", (now_ms(), session_id))
            return self.status(session_id)

    def list_sessions(self) -> list[dict]:
        with self.lock, self._db() as database:
            rows = database.execute("SELECT * FROM sessions ORDER BY created_at_ms DESC LIMIT 1000").fetchall()
            result = []
            for row in rows:
                manifest = json.loads(row["manifest"])
                result.append({"session_id": row["session_id"], "state": row["state"],
                               "archive_state": manifest["state"], "started_at_ms": manifest.get("started_at_ms"),
                               "ended_at_ms": manifest.get("ended_at_ms"), "file_count": len(manifest["files"]),
                               "total_bytes": sum(file["size"] for file in manifest["files"]),
                               "metadata": manifest.get("metadata", {}),
                               "completed_at_ms": row["completed_at_ms"]})
            return result

    def download(self, session_id: str, relative: str) -> Path:
        with self.lock:
            manifest = self.manifest(session_id)
            entry = next((file for file in manifest["files"] if file["path"] == relative), None)
            path = self._path(session_id, relative)
            if entry is None or not path.is_file():
                raise ArchiveError(404, "File is not completely uploaded")
            if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
                raise ArchiveError(409, "Stored file failed integrity verification")
            return path

    def events(self, session_id: str, limit: int = 200, offset: int | None = None,
               since_ms: float | None = None, until_ms: float | None = None) -> list:
        manifest = self.manifest(session_id)
        files = sorted((f for f in manifest["files"] if f["path"].endswith(".jsonl")), key=lambda f:f["path"])
        result = deque(maxlen=max(1,min(limit,2000))) if offset is None else []
        matched=0
        for entry in files:
            try: path=self.download(session_id,entry["path"])
            except ArchiveError: continue
            with path.open("rb") as stream:
                for line in stream:
                    try: value=json.loads(line)
                    except (ValueError,UnicodeError): continue
                    timestamp=value.get("elapsed_time_ns",0)/1e6
                    if since_ms is not None and timestamp<since_ms: continue
                    if until_ms is not None and timestamp>until_ms: continue
                    if offset is None: result.append(value)
                    elif matched>=offset:
                        result.append(value)
                        if len(result)>=max(1,min(limit,2000)): return result
                    matched+=1
        return list(result)


class ArchiveHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store: ArchiveStore, token: str = ""):
        self.store, self.token = store, token
        super().__init__(address, ArchiveHandler)


class ArchiveHandler(BaseHTTPRequestHandler):
    server_version = "NavigationArchive/1"
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, format, *args):
        # Do not log URL queries or headers (which may include authentication).
        print(f"{self.log_date_time_string()} {self.command} {self.client_address[0]} {getattr(self, 'response_code', '-')}", flush=True)

    def send_response(self, code, message=None):
        self.response_code = code
        super().send_response(code, message)

    def _json(self, status: int, payload):
        data = canonical_json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, maximum: int) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise ArchiveError(400, "Use Content-Length, not Transfer-Encoding")
        if len(self.headers.get_all("Content-Length", [])) != 1:
            raise ArchiveError(400, "Exactly one Content-Length header is required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ArchiveError(400, "Invalid Content-Length") from None
        if length < 0 or length > maximum:
            raise ArchiveError(413, "Request body is too large")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ArchiveError(400, "Request body was interrupted")
        return data

    def _json_body(self):
        try:
            return json.loads(self._body(MAX_MANIFEST))
        except (ValueError, UnicodeError):
            raise ArchiveError(400, "Invalid JSON body") from None

    def _authorize(self):
        token = self.server.token
        if token and not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"),
                                             ("Bearer " + token).encode("utf-8")):
            raise ArchiveError(401, "Bearer token required")

    def _route(self):
        parsed = urlsplit(self.path)
        route = unquote(parsed.path, errors="strict")
        method = self.command
        if method == "GET" and route == "/":
            page = (Path(__file__).parent / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; img-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)
            return
        self._authorize()
        if method == "GET" and route == "/api/v1/sessions":
            return self._json(200, {"sessions": self.server.store.list_sessions()})
        prefix = "/api/v1/sessions/"
        if not route.startswith(prefix):
            raise ArchiveError(404, "Unknown endpoint")
        rest = route[len(prefix):]
        session_id, separator, tail = rest.partition("/")
        validate_session_id(session_id)
        store = self.server.store
        if method == "PUT" and not separator:
            return self._json(200, store.create(session_id, self._json_body()))
        if method == "GET" and not separator:
            return self._json(200, {"manifest": store.manifest(session_id), "upload": store.status(session_id)})
        if method == "GET" and tail == "upload-status":
            return self._json(200, store.status(session_id))
        if method == "POST" and tail == "complete":
            payload = self._json_body()
            if not isinstance(payload, dict):
                raise ArchiveError(400, "Expected an object")
            return self._json(200, store.complete(session_id))
        if method == "GET" and tail == "manifest":
            return self._json(200, store.manifest(session_id))
        if method == "GET" and tail == "events":
            query=parse_qs(parsed.query)
            try:
                limit=int(query.get("limit",[200])[0]); offset=int(query["offset"][0]) if "offset" in query else None
                since=float(query["since_ms"][0]) if "since_ms" in query else None
                until=float(query["until_ms"][0]) if "until_ms" in query else None
                if offset is not None and offset<0: raise ValueError()
            except ValueError: raise ArchiveError(400,"Invalid event pagination")
            return self._json(200,{"events":store.events(session_id,limit,offset,since,until)})
        if tail.startswith("files/"):
            relative = validate_file_path(tail[len("files/"):])
            if method == "PUT":
                query = parse_qs(parsed.query, keep_blank_values=True)
                try:
                    if set(query) != {"offset", "sha256"} or any(len(value) != 1 for value in query.values()):
                        raise ValueError()
                    offset, digest = int(query["offset"][0]), query["sha256"][0]
                except (KeyError, ValueError):
                    raise ArchiveError(400, "Expected offset and sha256 query parameters") from None
                return self._json(200, store.put_chunk(session_id, relative, offset, digest, self._body(MAX_CHUNK)))
            if method == "GET":
                path = store.download(session_id, relative)
                stream = path.open("rb")
                with stream:
                    size=os.fstat(stream.fileno()).st_size
                    start,end=0,size-1
                    requested=self.headers.get("Range")
                    if requested:
                        match=re.fullmatch(r"bytes=(\d*)-(\d*)",requested)
                        if not match or not any(match.groups()): raise ArchiveError(416,"Invalid range")
                        first,last=match.groups()
                        if first:
                            start=int(first);end=min(int(last),size-1) if last else size-1
                        else:
                            start=max(0,size-int(last));end=size-1
                        if start>=size or end<start: raise ArchiveError(416,"Range outside file")
                    self.send_response(206 if requested else 200)
                    self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    self.send_header("Content-Length",str(max(0,end-start+1)))
                    self.send_header("Accept-Ranges","bytes")
                    if requested:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                    self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(path.name, safe=""))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers();stream.seek(start);remaining=end-start+1
                    while remaining>0:
                        block=stream.read(min(64*1024,remaining))
                        if not block:break
                        self.wfile.write(block);remaining-=len(block)
                return
        raise ArchiveError(404, "Unknown endpoint")

    def _dispatch(self):
        try:
            self._route()
        except ArchiveError as error:
            # An error may happen before the body has been consumed; do not reuse it.
            self.close_connection = True
            self._json(error.status, {"error": error.message, **error.details})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except (UnicodeError, ValueError):
            self.close_connection = True
            self._json(400, {"error": "Malformed request"})
        except Exception:
            self.close_connection = True
            self._json(500, {"error": "Storage operation failed; archive has not been confirmed"})

    do_GET = _dispatch
    do_PUT = _dispatch
    do_POST = _dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="./archive-data", help="SQLite and navigation files directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get("NAV_ARCHIVE_TOKEN", "")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not token:
        parser.error("Set NAV_ARCHIVE_TOKEN when listening beyond loopback")
    server = ArchiveHTTPServer((args.host, args.port), ArchiveStore(args.data_dir), token)
    print(f"Navigation archive platform: http://{args.host}:{server.server_port}/", flush=True)
    print(f"Storage: {server.store.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
