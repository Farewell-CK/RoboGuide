"""Verify original image bounds, rooted input reads and durable no-action replay."""

from __future__ import annotations

import copy
import hashlib
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

from robonix_task.image_inspection import (
    MAX_IMAGE_BYTES,
    ImageInspector,
    inspect_image_bytes,
)
from robonix_task.journal import Conflict


def chunk(kind: bytes, value: bytes) -> bytes:
    """Encode one original fixture PNG chunk with its real CRC."""
    return (
        struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    )


def png(width: int = 2, height: int = 1) -> bytes:
    """Produce a tiny RGB PNG fixture with real compressed scanline bytes."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x80\xff\xff\x80\x00"))
        + chunk(b"IEND", b"")
    )


def digest(raw: bytes) -> str:
    """Return the same canonical content identity used by Node Artifact imports."""
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request(path: Path, raw: bytes, *, attempt: str = "attempt-original") -> dict[str, Any]:
    """Build a deployment-mapped request, without granting provenance to caller metadata."""
    return {
        "attempt_id": attempt,
        "invocation": {
            "mission_id": "photo-mission",
            "task_id": "inspect-original",
            "execution_id": attempt,
            "operation": "image.inspect@v1",
            "parameters": {
                "producer_task_id": "capture-original",
                "producer_role_id": "camera",
                "producer_node_id": "dog2",
            },
        },
        "artifact_path": str(path),
        "content_digest": digest(raw),
        "producer_receipt": {"source_node_id": "dog2", "source_attempt": "capture-original"},
    }


class ImageBytesTests(unittest.TestCase):
    """Check only what the bounded container reader can establish, never pixel quality."""

    def test_png_original_crc_and_dimensions_without_pixel_claim(self) -> None:
        """A valid fixture reports byte identity and dimensions with explicit quality limits."""
        raw = png()
        value = inspect_image_bytes(raw, digest(raw))
        self.assertEqual((value["width"], value["height"], value["format"]), (2, 1, "PNG"))
        self.assertEqual(value["byte_size"], len(raw))
        self.assertFalse(value["pixel_decode_verified"])
        self.assertEqual(value["visual_quality"], "not_evaluated")

    def test_jpeg_framing_handles_stuffed_entropy_bytes(self) -> None:
        """A synthetic JPEG marker fixture checks framing, not decoded-image validity."""
        sof = b"\x08\x00\x02\x00\x03\x01\x01\x11\x00"
        scan = b"\x01\x01\x00\x00\x3f\x00"
        raw = (
            b"\xff\xd8\xff\xc0"
            + struct.pack(">H", len(sof) + 2)
            + sof
            + b"\xff\xda"
            + struct.pack(">H", len(scan) + 2)
            + scan
            + b"\x12\xff\x00\x23\xff\xd0\x34\xff\xd9"
        )
        result = inspect_image_bytes(raw, digest(raw))
        self.assertEqual((result["width"], result["height"], result["format"]), (3, 2, "JPEG"))
        self.assertFalse(result["pixel_decode_verified"])
        for damaged in (raw[:-1], raw + b"trailing", raw[:5]):
            with self.subTest(raw=damaged), self.assertRaises(ValueError):
                inspect_image_bytes(damaged, digest(damaged))

    def test_digest_crc_dimensions_and_unsupported_data_are_rejected(self) -> None:
        """Malformed, changed and oversized declarations never become a success receipt."""
        raw = png()
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            inspect_image_bytes(raw, "sha256:" + "0" * 64)
        broken = bytearray(raw)
        broken[20] ^= 1
        cases = [bytes(broken), png(0, 1), png(8192, 8192), raw[:-2], b"not-an-image"]
        for case in cases:
            with self.subTest(size=len(case)), self.assertRaises(ValueError):
                inspect_image_bytes(case, digest(case))
        with self.assertRaisesRegex(ValueError, "byte_limit"):
            inspect_image_bytes(b"x" * (MAX_IMAGE_BYTES + 1), digest(b"oversized"))


class ImageStoreTests(unittest.TestCase):
    """Exercise actual SQLite, file identity and restart behavior in a private fixture root."""

    def test_replay_after_input_removal_and_restart_preserves_original(self) -> None:
        """A retained original receipt needs neither a fresh fetch nor another image read."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "original.png"
            raw = png()
            path.write_bytes(raw)
            body = request(path, raw)
            store = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                first = store.inspect(body)
                self.assertEqual(first["state"], "SUCCEEDED")
                self.assertFalse(first["motion_authorized"])
                self.assertFalse(first["producer_authority_verified_by_inspector"])
                path.unlink()
                self.assertEqual(store.inspect(body), first)
                self.assertEqual(store.status(first["handle"]), first)
            finally:
                store.close()
            restored = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                self.assertEqual(restored.inspect(body), first)
                self.assertEqual(len(list((root / "store/receipts").glob("*.json"))), 1)
            finally:
                restored.close()

    def test_changed_attempt_body_and_fixed_capacity_preserve_receipts(self) -> None:
        """A conflicting request or a full journal never overwrites the first decision."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "original.png"
            raw = png()
            path.write_bytes(raw)
            body = request(path, raw)
            store = ImageInspector(root / "store", "dog1", input_root=inputs, max_records=1)
            try:
                first = store.inspect(body)
                changed = copy.deepcopy(body)
                changed["producer_receipt"]["source_attempt"] = "another-source"
                with self.assertRaises(Conflict):
                    store.inspect(changed)
                with self.assertRaises(Conflict):
                    store.inspect(request(path, raw, attempt="attempt-second"))
                self.assertEqual(store.inspect(body), first)
            finally:
                store.close()

    def test_failed_input_is_terminal_even_after_file_appears(self) -> None:
        """A missing original input records failure, and retry cannot silently redo computation."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "missing.png"
            raw = png()
            store = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                body = request(path, raw)
                original = store.inspect(body)
                self.assertEqual(original["state"], "FAILED")
                path.write_bytes(raw)
                self.assertEqual(store.inspect(body), original)
            finally:
                store.close()

    def test_path_boundary_rejects_remote_parent_foreign_and_large_files(self) -> None:
        """Only bounded regular files in the fixed input directory may be inspected."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            raw = png()
            outside = root / "foreign.png"
            outside.write_bytes(raw)
            large = inputs / "large.png"
            with large.open("wb") as stream:
                stream.truncate(MAX_IMAGE_BYTES + 1)
            store = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                for index, path in enumerate(
                    [str(outside), "../foreign.png", "http://example/x", str(large)]
                ):
                    body = request(inputs / "unused", raw, attempt=f"attempt-{index}")
                    body["artifact_path"] = path
                    self.assertEqual(store.inspect(body)["state"], "FAILED")
            finally:
                store.close()

    @unittest.skipIf(sys.platform == "win32", "POSIX descriptor and FIFO boundary")
    def test_symlink_directory_and_fifo_are_rejected_without_blocking(self) -> None:
        """No-follow reads reject both final and intermediate links plus nonregular files."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            foreign = root / "foreign"
            foreign.mkdir()
            raw = png()
            (foreign / "image.png").write_bytes(raw)
            (inputs / "link").symlink_to(foreign, target_is_directory=True)
            (inputs / "image.png").symlink_to(foreign / "image.png")
            if sys.platform != "win32":
                os.mkfifo(inputs / "pipe")
            store = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                for index, path in enumerate(
                    [inputs / "link/image.png", inputs / "image.png", inputs / "pipe"]
                ):
                    value = store.inspect(request(path, raw, attempt=f"attempt-{index}"))
                    self.assertEqual(value["state"], "FAILED")
            finally:
                store.close()

    def test_receipt_materialization_retry_uses_committed_original_bytes(self) -> None:
        """A filesystem publication failure retains SQLite's original receipt for exact retry."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "image.png"
            raw = png()
            path.write_bytes(raw)
            body = request(path, raw)
            store = ImageInspector(root / "store", "dog1", input_root=inputs)
            try:
                with patch(
                    "robonix_task.image_inspection.os.link", side_effect=OSError("disk full")
                ):
                    with self.assertRaises(OSError):
                        store.inspect(body)
                path.unlink()
                result = store.inspect(body)
                self.assertEqual(result["state"], "SUCCEEDED")
                self.assertEqual(len(list((root / "store/receipts").glob("*.json"))), 1)
            finally:
                store.close()

    def test_corrupt_receipt_and_unknown_generation_fail_closed(self) -> None:
        """Startup rejects changed durable receipts and unknown schema without rewriting them."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "image.png"
            raw = png()
            path.write_bytes(raw)
            store_root = root / "store"
            store = ImageInspector(store_root, "dog1", input_root=inputs)
            store.inspect(request(path, raw))
            store.close()
            db = sqlite3.connect(store_root / "inspection.sqlite3")
            with db:
                db.execute("UPDATE receipts SET digest=?", ("0" * 64,))
            db.close()
            with self.assertRaises(Conflict):
                ImageInspector(store_root, "dog1", input_root=inputs)
            db = sqlite3.connect(store_root / "inspection.sqlite3")
            db.execute("PRAGMA user_version=999")
            db.close()
            with self.assertRaisesRegex(Conflict, "generation"):
                ImageInspector(store_root, "dog1", input_root=inputs)


if __name__ == "__main__":
    unittest.main()
