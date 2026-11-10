"""Verify explicit photo publication, original provenance and immutable restart behavior."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import struct
import threading
import unittest
import uuid
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from robonix_task.photo_capture_memory import PhotoCaptureMemories, PhotoCaptureMemory


def png() -> bytes:
    """Build one valid bounded RGB PNG fixture with original chunk CRCs."""

    def chunk(kind: bytes, value: bytes) -> bytes:
        """Encode a fixed fixture chunk without bypassing PNG structure checks."""
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


class Captures:
    """Supply the real capture journal shape with an independent synthetic database."""

    def __init__(self, root: Path) -> None:
        """Own fixed image paths and original request/detail records used by the publisher."""
        self.root = root
        (root / "images").mkdir(parents=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / "captures.sqlite3")
        self.db.execute(
            "CREATE TABLE captures(id TEXT,attempt TEXT,request TEXT,state TEXT,"
            "detail TEXT,created_ns INTEGER)"
        )

    def add(self, memory_id: str = "photo-memory", *, state: str = "SUCCEEDED") -> str:
        """Persist one explicit source attempt and the actual immutable PNG under its UUID."""
        capture_id = str(uuid.uuid4())
        raw = png()
        digest = hashlib.sha256(raw).hexdigest()
        request = {
            "attempt_id": "attempt-" + capture_id,
            "invocation": {
                "operation": "lite3.capture-rgb@v1",
                "mission_id": "mission-photo",
                "task_id": "capture",
                "role_id": "camera",
                "parameters": {"photo_memory_id": memory_id},
            },
        }
        detail = {
            "sha256": digest,
            "byte_size": len(raw),
            "media_type": "image/png",
            "received_wall_ns": 1794300000123456789,
            "artifact_publication": "finalized",
            "image_path": "/untrusted/ignored.png",
            "artifact": {
                "artifact_finalized": True,
                "artifact_content_digest": "sha256:" + digest,
                "artifact_byte_size": len(raw),
                "artifact_upload_id": "upload-" + capture_id,
                "artifact_receipt": {
                    "phase": "completed",
                    "content_digest": "sha256:" + digest,
                    "byte_size": len(raw),
                    "upload_id": "upload-" + capture_id,
                },
            },
        }
        self.db.execute(
            "INSERT INTO captures VALUES(?,?,?,?,?,?)",
            (
                capture_id,
                request["attempt_id"],
                json.dumps(request),
                state,
                json.dumps(detail),
                1,
            ),
        )
        self.db.commit()
        (self.root / "images" / (capture_id + ".png")).write_bytes(raw)
        return capture_id

    def change(self, capture_id: str, column: str, value: dict[str, Any]) -> None:
        """Update one fixture's original metadata only for explicit negative test cases."""
        if column not in {"request", "detail"}:
            raise ValueError("fixture_column_not_allowed")
        self.db.execute(
            "UPDATE captures SET " + column + "=? WHERE id=?", (json.dumps(value), capture_id)
        )
        self.db.commit()


def publisher(root: Path) -> PhotoCaptureMemory:
    """Create the explicit deployment profile shared across fixture restarts."""
    return PhotoCaptureMemory(root, "lite3-dog2", "photo-memory", "capture", "camera")


class PhotoCaptureMemoryTests(unittest.TestCase):
    """Keep discovered Memory restricted to the one original successful selected capture."""

    def test_historical_failed_or_pending_captures_are_not_published(self) -> None:
        """Explicit Memory identity gates discovery; completion alone grants no publication."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for state in ("FAILED", "PENDING", "RUNNING", "UNKNOWN"):
                with self.subTest(state=state):
                    captures = Captures(root / state / "capture")
                    captures.add("old-history")
                    captures.add(state=state)
                    store = publisher(root / state / "memory")
                    try:
                        store.refresh(captures)
                        self.assertEqual(store.discover(), {"manifests": []})
                    finally:
                        store.close()
                        captures.db.close()

    def test_original_png_publication_and_restart_preserve_manifest_and_created_time(self) -> None:
        """The producer exports original bytes and original receipt time across a cold restart."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            captures.add("old-history")
            capture_id = captures.add()
            store = publisher(root / "memory")
            try:
                store.refresh(captures)
                manifest = store.discover()["manifests"][0]
                output = store.export({"manifest": manifest})
                self.assertEqual((store.root / output["path"]).read_bytes(), png())
                self.assertEqual(manifest["created_at"], 1794300000123)
                self.assertEqual(manifest["source_execution_id"], "attempt-" + capture_id)
                self.assertEqual(
                    manifest["source_task_ref"],
                    {"mission_id": "mission-photo", "task_id": "capture"},
                )
                original_record = store.record_path.read_bytes()
            finally:
                store.close()
            store = publisher(root / "memory")
            try:
                store.refresh(captures)
                self.assertEqual(store.discover(), {"manifests": [manifest]})
                self.assertEqual(store.record_path.read_bytes(), original_record)
                self.assertEqual(store.export({"manifest": manifest}), output)
            finally:
                store.close()
                captures.db.close()

    def test_wrong_digest_or_foreign_task_does_not_publish(self) -> None:
        """Neither a valid PNG nor a finalized upload can replace exact original task evidence."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for case in ("digest", "task", "role", "attempt", "upload"):
                with self.subTest(case=case):
                    captures = Captures(root / case / "capture")
                    capture_id = captures.add()
                    row = captures.db.execute("SELECT request,detail FROM captures").fetchone()
                    request, detail = json.loads(row[0]), json.loads(row[1])
                    if case == "digest":
                        detail["sha256"] = "0" * 64
                    elif case == "upload":
                        detail["artifact"]["artifact_receipt"]["phase"] = "prepared"
                    elif case == "attempt":
                        request["attempt_id"] = "foreign"
                    else:
                        request["invocation"][case + "_id"] = "foreign"
                    captures.change(capture_id, "request", request)
                    captures.change(capture_id, "detail", detail)
                    store = publisher(root / case / "memory")
                    try:
                        with self.assertRaises(ValueError):
                            store.refresh(captures)
                        self.assertEqual(store.discover(), {"manifests": []})
                        self.assertFalse(store.record_path.exists())
                    finally:
                        store.close()
                        captures.db.close()

    def test_selector_cannot_be_overwritten_and_duplicate_initial_matches_refuse(self) -> None:
        """A retained publication stays original; an initially ambiguous selector grants nothing."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            captures.add()
            store = publisher(root / "memory")
            try:
                store.refresh(captures)
                manifest = store.discover()["manifests"][0]
                captures.add()
                store.refresh(captures)
                self.assertEqual(store.discover(), {"manifests": [manifest]})
                changed = copy.deepcopy(manifest)
                changed["selector"]["revision_id"] = "r2"
                with self.assertRaisesRegex(ValueError, "exact_manifest"):
                    store.export({"manifest": changed})
            finally:
                store.close()
            store = publisher(root / "fresh-memory")
            try:
                with self.assertRaisesRegex(ValueError, "multiple"):
                    store.refresh(captures)
                self.assertEqual(store.discover(), {"manifests": []})
            finally:
                store.close()
                captures.db.close()

    def test_corrupt_png_profile_or_export_is_refused(self) -> None:
        """Every export rehashes current bytes and restart cannot reinterpret the fixed selector."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            captures.add()
            store = publisher(root / "memory")
            try:
                store.refresh(captures)
                manifest = store.discover()["manifests"][0]
                output = store.export({"manifest": manifest})
                (store.root / output["path"]).write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    store.discover()
                with self.assertRaises(ValueError):
                    store.export({"manifest": manifest})
            finally:
                store.close()
                captures.db.close()
            with self.assertRaises(ValueError):
                publisher(root / "memory")
            with self.assertRaisesRegex(ValueError, "profile_changed"):
                PhotoCaptureMemory(root / "memory", "lite3-dog2", "new-photo", "capture", "camera")

    def test_single_owner_and_source_symlink_refuse_substitution(self) -> None:
        """An active store has one owner and source image paths cannot follow external links."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            capture_id = captures.add()
            store = publisher(root / "memory")
            try:
                with self.assertRaises(OSError):
                    publisher(root / "memory")
                path = captures.root / "images" / (capture_id + ".png")
                path.unlink()
                outside = root / "outside.png"
                outside.write_bytes(png())
                try:
                    path.symlink_to(outside)
                except OSError:
                    self.skipTest("symlink creation unavailable on this host")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    store.refresh(captures)
            finally:
                store.close()
                captures.db.close()


class PhotoCaptureMemoriesTests(unittest.TestCase):
    """Preserve existing original selectors while offering a bounded later experiment round."""

    def test_original_root_and_second_round_discover_export_without_rewriting(self) -> None:
        """One provider exports old root bytes and new child bytes through their exact manifests."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            captures.add("photo-memory")
            first = publisher(root / "memory")
            first.refresh(captures)
            original = first.record_path.read_bytes()
            manifest_one = first.discover()["manifests"][0]
            captures.add("photo-memory-02")
            second = PhotoCaptureMemory(
                root / "memory/round02", "lite3-dog2", "photo-memory-02", "capture", "camera"
            )
            combined = PhotoCaptureMemories([first, second], root / "memory")
            try:
                combined.refresh(captures)
                manifests = combined.discover()["manifests"]
                self.assertEqual(len(manifests), 2)
                self.assertEqual(manifests[0], manifest_one)
                self.assertEqual(first.record_path.read_bytes(), original)
                for index, manifest in enumerate(manifests):
                    path = combined.export({"manifest": manifest})["path"]
                    self.assertEqual((combined.root / path).read_bytes(), png())
                    self.assertEqual(path.startswith("round02/"), index == 1)
                foreign = copy.deepcopy(manifests[1])
                foreign["selector"]["revision_id"] = "r2"
                with self.assertRaisesRegex(ValueError, "missing_or_ambiguous"):
                    combined.export({"manifest": foreign})
            finally:
                combined.close()
                captures.db.close()
            first = publisher(root / "memory")
            second = PhotoCaptureMemory(
                root / "memory/round02", "lite3-dog2", "photo-memory-02", "capture", "camera"
            )
            combined = PhotoCaptureMemories([first, second], root / "memory")
            try:
                self.assertEqual(combined.discover()["manifests"], manifests)
                self.assertEqual(first.record_path.read_bytes(), original)
            finally:
                combined.close()

    def test_duplicate_selector_and_external_root_refuse_without_closing_children(self) -> None:
        """Invalid composition grants no ownership and cannot close a caller's open publication."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = publisher(root / "memory")
            duplicate = publisher(root / "memory/duplicate")
            outside = PhotoCaptureMemory(
                root / "outside", "lite3-dog2", "photo-memory-02", "capture", "camera"
            )
            try:
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    PhotoCaptureMemories([first, duplicate], root / "memory")
                with self.assertRaisesRegex(ValueError, "outside"):
                    PhotoCaptureMemories([first, outside], root / "memory")
                for publication in (first, duplicate, outside):
                    self.assertFalse(publication.owner.closed)
                with self.assertRaisesRegex(ValueError, "bounded"):
                    PhotoCaptureMemories([], root / "memory")
                with self.assertRaisesRegex(ValueError, "bounded"):
                    PhotoCaptureMemories([first] * 17, root / "memory")
            finally:
                outside.close()
                duplicate.close()
                first.close()

    def test_child_unsafe_export_and_reverse_close_are_enforced(self) -> None:
        """Child metadata cannot choose an external path, and successful ownership closes once."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            captures = Captures(root / "capture")
            captures.add()
            first = publisher(root / "memory")
            second = PhotoCaptureMemory(
                root / "memory/round02", "lite3-dog2", "photo-memory-02", "capture", "camera"
            )
            combined = PhotoCaptureMemories([first, second], root / "memory")
            order: list[str] = []
            try:
                combined.refresh(captures)
                manifest = first.discover()["manifests"][0]
                for path in ("../outside.png", str(root / "outside.png")):
                    with patch.object(first, "export", return_value={"path": path}):
                        with self.assertRaisesRegex(ValueError, "unsafe"):
                            combined.export({"manifest": manifest})
                with (
                    patch.object(first, "close", side_effect=lambda: order.append("first")),
                    patch.object(second, "close", side_effect=lambda: order.append("second")),
                ):
                    combined.close()
                    combined.close()
                self.assertEqual(order, ["second", "first"])
            finally:
                first.close()
                second.close()
                captures.db.close()


if __name__ == "__main__":
    unittest.main()
