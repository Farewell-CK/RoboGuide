"""Exercise durable photo-job admission, nonreplaying recovery and honest cancellation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from typing import Any

from robonix_task.image_inspection import ImageInspector
from robonix_task.journal import Conflict
from robonix_task.photo_jobs import PhotoInspectionJobs


def png() -> bytes:
    """Encode a real tiny PNG independently of the consumer's container parser."""
    chunks = [
        (b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
        (b"IDAT", zlib.compress(b"\x00\x10\x80\xff")),
        (b"IEND", b""),
    ]
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
        for kind, value in chunks
    )


def request(attempt: str = "original-attempt") -> dict[str, Any]:
    """Supply the original accepted identity and exact producer task binding."""
    return {
        "attempt_id": attempt,
        "invocation": {
            "mission_id": "photo-mission",
            "group_id": "photo-group",
            "task_id": "inspect",
            "role_id": "reader",
            "operation": "image.inspect@v1",
            "execution_id": attempt,
            "parameters": {
                "producer_node_id": "dog2",
                "producer_task_id": "capture",
                "producer_role_id": "camera",
            },
        },
    }


def wait_terminal(jobs: PhotoInspectionJobs, handle: str) -> dict[str, Any]:
    """Bound fixture observation without creating retries or manipulating worker scheduling."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = jobs.status(handle)
        if result["state"] in {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}:
            return result
        time.sleep(0.01)
    raise AssertionError("fixture job did not reach a terminal state")


class PhotoJobsTests(unittest.TestCase):
    """Use real worker threads and SQLite while resolving only private fixture image bytes."""

    def test_durable_admission_replay_restart_and_changed_body(self) -> None:
        """Resolver sees the committed request; one accepted attempt is fetched only once."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            path = inputs / "original.png"
            raw = png()
            path.write_bytes(raw)
            calls: list[dict[str, Any]] = []
            body = request()

            def resolve(invocation: dict[str, Any], consumer_attempt_id: str) -> dict[str, Any]:
                """Require the accepted original identity to be durable before any fetch."""
                db = sqlite3.connect(root / "jobs/jobs.sqlite3")
                try:
                    row = db.execute("SELECT request,state FROM jobs").fetchone()
                    self.assertEqual(json.loads(row[0]), body)
                    self.assertEqual(row[1], "RUNNING")
                finally:
                    db.close()
                calls.append(invocation)
                return {
                    "artifact_path": str(path),
                    "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "producer_receipt": {
                        "source_node_id": "dog2",
                        "attempt_id": "producer",
                        "consumer_attempt_id": consumer_attempt_id,
                    },
                }

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve)
            try:
                accepted = jobs.submit(body)
                result = wait_terminal(jobs, accepted["handle"])
                self.assertEqual(result["state"], "SUCCEEDED")
                self.assertEqual(jobs.submit(body), result)
                changed = copy.deepcopy(body)
                changed["invocation"]["task_id"] = "another-task"
                with self.assertRaises(Conflict):
                    jobs.submit(changed)
                self.assertEqual(jobs.latest()["inspection"]["handle"], result["handle"])
                self.assertEqual(len(calls), 1)
                path.unlink()
            finally:
                jobs.close()
                inspector.close()
            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve)
            try:
                self.assertEqual(jobs.submit(body), result)
                self.assertEqual(jobs.cancel(result["handle"]), result)
                self.assertEqual(len(calls), 1)
            finally:
                jobs.close()
                inspector.close()

    def test_failed_resolution_is_not_retried(self) -> None:
        """A foreign or unavailable producer causes one retained failure, without inspection."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            calls: list[dict[str, Any]] = []

            def resolve(invocation: dict[str, Any], consumer_attempt_id: str) -> dict[str, Any]:
                """Model an authorization-bound producer mismatch before content reading."""
                calls.append(invocation)
                raise ValueError("producer_attempt_binding_mismatch")

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve)
            try:
                accepted = jobs.submit(request())
                result = wait_terminal(jobs, accepted["handle"])
                self.assertEqual(result["state"], "FAILED")
                self.assertIn("producer_attempt_binding_mismatch", result["detail"])
                self.assertEqual(jobs.submit(request()), result)
                self.assertEqual(len(calls), 1)
                self.assertEqual(list((root / "inspection/receipts").glob("*.json")), [])
            finally:
                jobs.close()
                inspector.close()

    def test_running_cancel_waits_for_fetch_exit_and_bounds_queue(self) -> None:
        """Cancel remains RUNNING during an in-flight read, then completes without inspection."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            entered, release = threading.Event(), threading.Event()

            def resolve(_invocation: dict[str, Any], consumer_attempt_id: str) -> dict[str, Any]:
                """Block a synthetic read until the test explicitly observes cancellation."""
                entered.set()
                release.wait()
                return {
                    "artifact_path": str(inputs / "unused.png"),
                    "content_digest": "sha256:" + "0" * 64,
                    "producer_receipt": {
                        "source_node_id": "dog2",
                        "consumer_attempt_id": consumer_attempt_id,
                    },
                }

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve, max_pending=1)
            try:
                accepted = jobs.submit(request())
                self.assertTrue(entered.wait(2))
                canceled = jobs.cancel(accepted["handle"])
                self.assertEqual(canceled["state"], "RUNNING")
                self.assertTrue(canceled["cancel_requested"])
                with self.assertRaisesRegex(Conflict, "pending_bound"):
                    jobs.submit(request("second-attempt"))
                release.set()
                result = wait_terminal(jobs, accepted["handle"])
                self.assertEqual(result["state"], "CANCELED")
                self.assertEqual(jobs.submit(request()), result)
                self.assertEqual(list((root / "inspection/receipts").glob("*.json")), [])
            finally:
                release.set()
                jobs.close()
                inspector.close()

    def test_actual_process_loss_fences_without_refetch(self) -> None:
        """Kill a child during resolution; restart must retain UNKNOWN with zero new fetches."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inputs").mkdir()
            (root / "request.json").write_text(json.dumps(request()), encoding="utf-8")
            script = '''import json,pathlib,sys,threading
from robonix_task.image_inspection import ImageInspector
from robonix_task.photo_jobs import PhotoInspectionJobs
root=pathlib.Path(sys.argv[1])
def resolve(invocation, consumer_attempt_id):
    """Signal an in-flight synthetic read and remain blocked until process termination."""
    (root/'fetch-started').write_text('original fetch')
    threading.Event().wait()
inspector=ImageInspector(root/'inspection','dog1',input_root=root/'inputs')
jobs=PhotoInspectionJobs(root/'jobs',inspector,resolve)
jobs.submit(json.loads((root/'request.json').read_text()))
threading.Event().wait()
'''
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            process = subprocess.Popen(
                [sys.executable, "-B", "-c", script, str(root)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not (root / "fetch-started").exists():
                    if process.poll() is not None:
                        self.fail(process.communicate()[1].decode())
                    time.sleep(0.02)
                self.assertTrue((root / "fetch-started").exists())
            finally:
                process.kill()
                process.communicate(timeout=5)
            called: list[dict[str, Any]] = []

            def resolve_after(
                invocation: dict[str, Any], consumer_attempt_id: str
            ) -> dict[str, Any]:
                """Expose any forbidden automatic recovery fetch immediately to the test."""
                called.append(invocation)
                raise AssertionError("recovery must not refetch")

            inspector = ImageInspector(root / "inspection", "dog1", input_root=root / "inputs")
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve_after)
            try:
                result = jobs.submit(request())
                self.assertEqual(result["state"], "UNKNOWN")
                self.assertEqual(jobs.cancel(result["handle"]), result)
                self.assertEqual(called, [])
            finally:
                jobs.close()
                inspector.close()

    def test_queued_cancel_never_resolves_and_close_joins_running_read(self) -> None:
        """Pending cancellation skips fetching; shutdown waits for the already-running read."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            entered, release, closed = threading.Event(), threading.Event(), threading.Event()
            calls: list[dict[str, Any]] = []

            def resolve(invocation: dict[str, Any], consumer_attempt_id: str) -> dict[str, Any]:
                """Keep the first original read active while a second job is canceled."""
                calls.append(invocation)
                entered.set()
                release.wait()
                raise ValueError("fixture_read_finished_without_image")

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve, max_pending=2)

            def close_jobs() -> None:
                """Signal only after the worker has returned and its journal is closed."""
                jobs.close()
                closed.set()

            closer = threading.Thread(target=close_jobs)
            try:
                jobs.submit(request())
                self.assertTrue(entered.wait(2))
                queued = jobs.submit(request("queued-attempt"))
                self.assertEqual(queued["state"], "PENDING")
                canceled = jobs.cancel(queued["handle"])
                self.assertEqual(canceled["state"], "CANCELED")
                self.assertEqual(jobs.cancel(queued["handle"]), canceled)
                closer.start()
                self.assertFalse(closed.wait(0.05))
                self.assertIsNotNone(inspector.db)
                release.set()
                self.assertTrue(closed.wait(2))
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(3)
                else:
                    jobs.close()
                inspector.close()

    def test_request_and_state_size_bounds_keep_original_status(self) -> None:
        """Oversized commands are refused and large proofs stay out of repeated State payloads."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            raw = png()
            path = inputs / "image.png"
            path.write_bytes(raw)

            def resolve(_invocation: dict[str, Any], attempt: str) -> dict[str, Any]:
                """Return bounded but large source evidence to exercise selective projection."""
                return {
                    "artifact_path": str(path),
                    "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "producer_receipt": {
                        "consumer_attempt_id": attempt,
                        "source_evidence_padding": "x" * 40000,
                        "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    },
                }

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve)
            try:
                oversized = request()
                oversized["invocation"]["objective"] = "x" * 16384
                with self.assertRaisesRegex(ValueError, "limit"):
                    jobs.submit(oversized)
                self.assertIsNone(jobs.latest()["inspection"])
                body = request()
                body["invocation"]["objective"] = "x" * 12000
                result = wait_terminal(jobs, jobs.submit(body)["handle"])
                self.assertEqual(result["state"], "SUCCEEDED")
                projection = jobs.latest()["inspection"]
                self.assertNotIn("invocation", projection)
                self.assertNotIn("invocation", projection["result"])
                self.assertLess(len(json.dumps(projection).encode()), 49152)
                self.assertEqual(
                    result["result"]["producer_receipt"]["source_evidence_padding"], "x" * 40000
                )
                self.assertEqual(jobs.status(result["handle"]), result)
            finally:
                jobs.close()
                inspector.close()

    def test_consumer_binding_mismatch_fails_before_inspection(self) -> None:
        """Wrong consumer proof cannot reach the image inspector even for valid image bytes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()

            def resolve(_invocation: dict[str, Any], _attempt: str) -> dict[str, Any]:
                """Model a miswired reader returning another valid attempt's handoff proof."""
                return {
                    "artifact_path": str(inputs / "not-read.png"),
                    "content_digest": "sha256:" + "0" * 64,
                    "producer_receipt": {"consumer_attempt_id": "foreign-attempt"},
                }

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve)
            try:
                result = wait_terminal(jobs, jobs.submit(request())["handle"])
                self.assertEqual(result["state"], "FAILED")
                self.assertIn("consumer_attempt_mismatch", result["detail"])
                self.assertIsNone(result["result"])
                self.assertEqual(list((root / "inspection/receipts").glob("*.json")), [])
            finally:
                jobs.close()
                inspector.close()

    def test_canceled_queue_entries_cannot_leave_new_durable_job_unqueued(self) -> None:
        """A full physical queue refuses admission before writing another PENDING record."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            entered, release = threading.Event(), threading.Event()

            def resolve(_invocation: dict[str, Any], _attempt: str) -> dict[str, Any]:
                """Hold the running read while canceled handles still occupy queue entries."""
                entered.set()
                release.wait()
                raise ValueError("fixture_read_done")

            inspector = ImageInspector(root / "inspection", "dog1", input_root=inputs)
            jobs = PhotoInspectionJobs(root / "jobs", inspector, resolve, max_pending=2)
            try:
                jobs.submit(request())
                self.assertTrue(entered.wait(2))
                for attempt in ("queued-one", "queued-two"):
                    queued = jobs.submit(request(attempt))
                    self.assertEqual(jobs.cancel(queued["handle"])["state"], "CANCELED")
                with self.assertRaisesRegex(Conflict, "pending_bound"):
                    jobs.submit(request("must-not-be-inserted"))
                db = sqlite3.connect(root / "jobs/jobs.sqlite3")
                try:
                    self.assertEqual(
                        db.execute(
                            "SELECT COUNT(*) FROM jobs WHERE attempt=?", ("must-not-be-inserted",)
                        ).fetchone()[0],
                        0,
                    )
                finally:
                    db.close()
            finally:
                release.set()
                jobs.close()
                inspector.close()


if __name__ == "__main__":
    unittest.main()
