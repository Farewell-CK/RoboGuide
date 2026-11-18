"""Check immutable origin capture, explicit sharing and restart refusal boundaries."""

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from robonix_task.journal import Conflict
from robonix_task.origin import OriginStore


def request():
    """Build the actual semantic Node invocation shape without any supplied coordinates."""
    return {
        "attempt_id": "attempt-46:group-one-13:capture-13:origin-role-1",
        "invocation": {
            "mission_id": "mission-one",
            "task_id": "capture",
            "operation": "spatial.origin.capture@v1",
            "objective": "记录狗2当前位置为共享实验起点",
            "parameters": {
                "memory_id": "experiment-origin",
                "expected_map_id": "map-one",
                "scope": "global",
            },
        },
    }


def snapshot():
    """Provide original receive time and a finite local Mapping response."""
    return {
        "mapping": {
            "received_at_ns": time.time_ns() - 1_000_000,
            "value": {
                "has_map": True,
                "mode": "localization",
                "map_id": "map-one",
                "pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
            },
        }
    }


class OriginTests(unittest.TestCase):
    """Exercise the real immutable file store rather than a dictionary substitute."""

    def test_replay_restart_and_changed_position_preserve_original(self):
        """A new current pose cannot overwrite the original execution or Memory identity."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            original = store.capture(request(), snapshot())
            self.assertEqual(store.capture(request(), {}), original)
            manifest = original["manifest"]
            blob = Path(directory) / store.export(manifest)["path"]
            raw = blob.read_bytes()
            changed = request()
            changed["invocation"]["parameters"]["expected_map_id"] = "other-map"
            with self.assertRaises(Conflict):
                store.capture(changed, snapshot())
            store.close()
            restored = OriginStore(Path(directory), "dog-2")
            try:
                self.assertEqual(restored.capture(request(), {}), original)
                self.assertEqual(blob.read_bytes(), raw)
                self.assertEqual(len(restored.discover()["manifests"]), 1)
            finally:
                restored.close()

    def test_capture_freshness_map_and_coordinate_admission(self):
        """Wrong maps, missing/stale observations and caller coordinates create no record."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            try:
                wrong_map, nonfinite, unknown_mode = snapshot(), snapshot(), snapshot()
                wrong_map["mapping"]["value"]["map_id"] = "wrong-map"
                nonfinite["mapping"]["value"]["pose"]["x"] = float("nan")
                unknown_mode["mapping"]["value"]["mode"] = "mapping"
                for bad in ({}, wrong_map, nonfinite, unknown_mode):
                    with self.assertRaises(ValueError):
                        store.capture(request(), bad)
                stale = snapshot()
                stale["mapping"]["received_at_ns"] -= 20_000_000_000
                with self.assertRaises(ValueError):
                    store.capture(request(), stale)
                supplied = request()
                supplied["invocation"]["parameters"]["x"] = 42
                with self.assertRaises(ValueError):
                    store.capture(supplied, snapshot())
                self.assertEqual(store.discover(), {"manifests": []})
            finally:
                store.close()

    def test_private_local_origin_is_not_published(self):
        """A local origin may finish its own Task without becoming shared MI evidence."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            try:
                body = request()
                body["invocation"]["parameters"]["scope"] = "local"
                result = store.capture(body, snapshot())
                self.assertEqual(result["state"], "SUCCEEDED")
                self.assertEqual(store.discover(), {"manifests": []})
                with self.assertRaises(ValueError):
                    store.export(result["manifest"])
            finally:
                store.close()

    def test_corruption_and_future_store_refuse_restore(self):
        """Unavailable original content and an unsupported writer generation stay unavailable."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            result = store.capture(request(), snapshot())
            blob = Path(directory) / store.export(result["manifest"])["path"]
            blob.write_bytes(b"changed")
            with self.assertRaises(Conflict):
                store.discover()
            store.close()
            with self.assertRaises(Conflict):
                OriginStore(Path(directory), "dog-2")
            profile = Path(directory) / "profile.json"
            profile.write_text(json.dumps({"schema": "future", "node_id": "dog-2"}))
            with self.assertRaises(Conflict):
                OriginStore(Path(directory), "dog-2")

    def test_exclusive_owner_and_manifest_relabel_refused(self):
        """A second process owner or edited manifest cannot export the original bytes."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            try:
                with self.assertRaises(Conflict):
                    OriginStore(Path(directory), "dog-2")
                result = store.capture(request(), snapshot())
                edited = copy.deepcopy(result["manifest"])
                edited["source_execution_id"] = "other"
                with self.assertRaises(ValueError):
                    store.export(edited)
            finally:
                store.close()

    def test_local_receipt_cannot_be_relabelled_for_publication(self):
        """A changed scope in retained metadata never promotes an original private capture."""
        with tempfile.TemporaryDirectory() as directory:
            store = OriginStore(Path(directory), "dog-2")
            try:
                body = request()
                body["invocation"]["parameters"]["scope"] = "local"
                store.capture(body, snapshot())
                path = next(Path(directory).glob("origin-*.json"))
                row = json.loads(path.read_text())
                row["manifest"]["scope"] = {"kind": "global"}
                path.write_text(json.dumps(row))
                with self.assertRaises(Conflict):
                    store.discover()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
