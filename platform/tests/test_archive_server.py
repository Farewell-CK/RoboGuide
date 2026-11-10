"""Integration checks for durable navigation archive upload and download."""
from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_server import ArchiveError, ArchiveHTTPServer, ArchiveStore, MAX_CHUNK


@contextmanager
def running_server(store, token=""):
    server = ArchiveHTTPServer(("127.0.0.1", 0), store, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def manifest_for(files, session_id="nav-test", state="completed"):
    return {"session_id": session_id, "schema_version": 1, "state": state,
            "started_at_ms": 1_793_000_000_000, "ended_at_ms": 1_793_000_010_000,
            "metadata": {"model_width": 384, "model_height": 288, "destination": "实验楼"},
            "events_count": 2, "dropped_records": 0, "write_errors": 0,
            "files": [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "type": "event" if path.endswith("jsonl") else "semantic"}
                      for path, data in files.items()]}


class PlatformIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ArchiveStore(self.root)

    def request(self, server, method, path, payload=None, token=None):
        headers = {}
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if payload is None and method in {"PUT", "POST"}:
            payload = b""
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            body = response.read()
            status = response.status
            content_type = response.getheader("Content-Type", "")
        finally:
            connection.close()
        return status, json.loads(body) if content_type.startswith("application/json") else body

    def chunk(self, server, entry, body, offset=0, session_id="nav-test"):
        url = (f"/api/v1/sessions/{session_id}/files/" + quote(entry["path"], safe="/")
               + f"?offset={offset}&sha256={entry['sha256']}")
        return self.request(server, "PUT", url, body)

    def test_upload_retry_complete_download_and_restart(self):
        events = b'{"type":"navigation_start"}\n{"type":"navigation_end"}\n'
        image = bytes(range(256)) * 40
        files = {"events.jsonl": events, "semantic/类别.bin": image}
        manifest = manifest_for(files)
        with running_server(self.store) as server:
            status, created = self.request(server, "PUT", "/api/v1/sessions/nav-test", manifest)
            self.assertEqual(status, 200)
            self.assertEqual(created["state"], "uploading")
            entry = manifest["files"][1]
            first = image[:1234]
            self.assertEqual(self.chunk(server, entry, first)[0], 200)
            # Retrying a successfully stored chunk is harmless and does not append it twice.
            self.assertEqual(self.chunk(server, entry, first)[1]["received_bytes"], 1234)
            status, progress = self.request(server, "GET", "/api/v1/sessions/nav-test/upload-status")
            self.assertEqual(progress["files"][1]["received_bytes"], 1234)
        # New store and HTTP server recover offsets from durable staging files.
        restarted = ArchiveStore(self.root)
        with running_server(restarted) as server:
            self.assertEqual(self.request(server, "PUT", "/api/v1/sessions/nav-test", manifest)[0], 200)
            self.assertTrue(self.chunk(server, entry, image[1234:], offset=1234)[1]["complete"])
            self.assertTrue(self.chunk(server, manifest["files"][0], events)[1]["complete"])
            status, completed = self.request(server, "POST", "/api/v1/sessions/nav-test/complete", {})
            self.assertEqual(status, 200)
            self.assertEqual(completed["state"], "complete")
            self.assertEqual(self.request(server, "POST", "/api/v1/sessions/nav-test/complete", {})[0], 200)
            status, downloaded = self.request(server, "GET", "/api/v1/sessions/nav-test/files/semantic/" + quote("类别.bin"))
            self.assertEqual(status, 200)
            self.assertEqual(downloaded, image)
            status, timeline = self.request(server, "GET", "/api/v1/sessions/nav-test/events")
            self.assertEqual(timeline["events"][1]["type"], "navigation_end")
            status, sessions = self.request(server, "GET", "/api/v1/sessions")
            self.assertEqual(sessions["sessions"][0]["state"], "complete")
            self.assertEqual(self.request(server, "GET", "/api/v1/sessions/nav-test/manifest")[1], manifest)
        self.assertEqual(ArchiveStore(self.root).status("nav-test")["state"], "complete")

    def test_missing_files_cannot_be_confirmed(self):
        manifest = manifest_for({"events.jsonl": b"hello\n"})
        self.store.create("nav-test", manifest)
        with running_server(self.store) as server:
            status, value = self.request(server, "POST", "/api/v1/sessions/nav-test/complete", {})
            self.assertEqual(status, 409)
            self.assertEqual(value["missing"], ["events.jsonl"])
            self.assertEqual(self.store.status("nav-test")["state"], "uploading")

    def test_bad_checksum_resets_file_then_accepts_correct_retry(self):
        manifest = manifest_for({"semantic/mask.bin": b"correct"})
        self.store.create("nav-test", manifest)
        entry = manifest["files"][0]
        with running_server(self.store) as server:
            status, value = self.chunk(server, entry, b"corrupt")
            self.assertEqual(status, 422)
            self.assertEqual(value["received_bytes"], 0)
            self.assertEqual(self.store.status("nav-test")["files"][0]["received_bytes"], 0)
            self.assertEqual(self.chunk(server, entry, b"correct")[0], 200)

    def test_gap_overlap_and_conflicting_retry_are_rejected(self):
        manifest = manifest_for({"values.bin": b"abcdefghij"})
        self.store.create("nav-test", manifest)
        entry = manifest["files"][0]
        with running_server(self.store) as server:
            self.assertEqual(self.chunk(server, entry, b"a", offset=2)[0], 409)
            self.assertEqual(self.chunk(server, entry, b"abcd")[0], 200)
            self.assertEqual(self.chunk(server, entry, b"xxxx")[0], 409)
            self.assertEqual(self.chunk(server, entry, b"cdef", offset=2)[0], 409)
            self.assertEqual(self.chunk(server, entry, b"efghij", offset=4)[0], 200)
            # Successful last-chunk retry remains idempotent after rename.
            self.assertEqual(self.chunk(server, entry, b"efghij", offset=4)[0], 200)

    def test_manifest_is_immutable_and_cross_session_mismatch_rejected(self):
        manifest = manifest_for({"values.bin": b"one"})
        self.store.create("nav-test", manifest)
        with running_server(self.store) as server:
            changed = manifest_for({"values.bin": b"two"})
            self.assertEqual(self.request(server, "PUT", "/api/v1/sessions/nav-test", changed)[0], 409)
            self.assertEqual(self.request(server, "PUT", "/api/v1/sessions/other", manifest)[0], 400)

    def test_incomplete_recording_is_distinct_from_complete_upload(self):
        manifest = manifest_for({}, state="incomplete")
        manifest["dropped_records"] = 8
        self.store.create("nav-test", manifest)
        completed = self.store.complete("nav-test")
        self.assertEqual(completed["state"], "complete")
        self.assertEqual(completed["archive_state"], "incomplete")
        self.assertEqual(self.store.manifest("nav-test")["dropped_records"], 8)

    def test_empty_files_are_saved_and_verified(self):
        manifest = manifest_for({"empty.bin": b""})
        result = self.store.create("nav-test", manifest)
        self.assertTrue(result["files"][0]["complete"])
        self.assertEqual(self.store.complete("nav-test")["state"], "complete")

    def test_crash_after_last_write_before_rename_is_recovered(self):
        data = b"last chunk was durable but rename did not happen"
        manifest = manifest_for({"nested/map.bin": data})
        self.store.create("nav-test", manifest)
        partial = self.store._path("nav-test", "nested/map.bin", partial=True)
        partial.parent.mkdir(parents=True)
        partial.write_bytes(data)
        restarted = ArchiveStore(self.root)
        self.assertTrue(restarted.status("nav-test")["files"][0]["complete"])
        self.assertEqual(restarted.complete("nav-test")["state"], "complete")

    def test_corrupted_full_staging_file_is_reset_after_restart(self):
        manifest = manifest_for({"map.bin": b"123456"})
        self.store.create("nav-test", manifest)
        self.store._path("nav-test", "map.bin", partial=True).write_bytes(b"xxxxxx")
        restarted = ArchiveStore(self.root)
        self.assertEqual(restarted.status("nav-test")["files"][0]["received_bytes"], 0)

    def test_corruption_after_upload_is_detected_before_confirmation_or_download(self):
        manifest = manifest_for({"map.bin": b"123456"})
        self.store.create("nav-test", manifest)
        entry = manifest["files"][0]
        self.store.put_chunk("nav-test", entry["path"], 0, entry["sha256"], b"123456")
        self.store._path("nav-test", "map.bin").write_bytes(b"654321")
        with running_server(self.store) as server:
            self.assertEqual(self.request(server, "POST", "/api/v1/sessions/nav-test/complete", {})[0], 409)
            self.assertEqual(self.request(server, "GET", "/api/v1/sessions/nav-test/files/map.bin")[0], 409)

    def test_authentication_is_required_for_every_data_endpoint(self):
        self.store.create("nav-test", manifest_for({}))
        with running_server(self.store, "private-token") as server:
            for method, path, payload in [
                ("GET", "/api/v1/sessions", None),
                ("GET", "/api/v1/sessions/nav-test", None),
                ("GET", "/api/v1/sessions/nav-test/manifest", None),
                ("GET", "/api/v1/sessions/nav-test/upload-status", None),
                ("GET", "/api/v1/sessions/nav-test/events", None),
                ("PUT", "/api/v1/sessions/nav-other", manifest_for({}, "nav-other")),
                ("POST", "/api/v1/sessions/nav-test/complete", {}),
            ]:
                self.assertEqual(self.request(server, method, path, payload)[0], 401)
                self.assertEqual(self.request(server, method, path, payload, token="wrong")[0], 401)
            self.assertEqual(self.request(server, "GET", "/api/v1/sessions", token="private-token")[0], 200)
            status, page = self.request(server, "GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("导航数据档案".encode(), page)

    def test_path_traversal_duplicate_and_prefix_conflicts_rejected(self):
        for bad in ["../escape", "/absolute", "a//b", "a/../b", "a/./b", "a\\b", "a:b", "a\n"]:
            with self.subTest(path=bad), self.assertRaises(ArchiveError):
                self.store.create("nav-test", manifest_for({bad: b""}))
        manifest = manifest_for({"a": b"", "a-b": b"", "a/b": b""})
        with self.assertRaises(ArchiveError):
            self.store.create("nav-test", manifest)
        with running_server(self.store) as server:
            self.assertEqual(self.request(server, "GET", "/api/v1/sessions/nav-test/files/%2e%2e/escape")[0], 400)

    def test_chunk_limit_and_undeclared_file_rejected(self):
        payload = b"a" * (MAX_CHUNK + 1)
        manifest = manifest_for({"big.bin": payload})
        self.store.create("nav-test", manifest)
        entry = manifest["files"][0]
        with running_server(self.store) as server:
            # Oversized bodies are rejected from headers without reading/allocating them.
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                connection.putrequest("PUT", "/api/v1/sessions/nav-test/files/big.bin?offset=0&sha256=" + entry["sha256"])
                connection.putheader("Content-Length", str(len(payload)))
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 413)
                response.read()
            finally:
                connection.close()
            unknown = {"path": "unknown.bin", "sha256": entry["sha256"]}
            self.assertEqual(self.chunk(server, unknown, b"a")[0], 404)

    def test_symlink_cannot_redirect_uploaded_files(self):
        manifest = manifest_for({"nested/file.bin": b"test"})
        self.store.create("nav-test", manifest)
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "sessions/nav-test/partial/nested").symlink_to(outside)
        entry = manifest["files"][0]
        with self.assertRaises(ArchiveError):
            self.store.put_chunk("nav-test", entry["path"], 0, entry["sha256"], b"test")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
