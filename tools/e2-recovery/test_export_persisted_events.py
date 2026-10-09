"""Test sealed evidence export with synthetic databases only."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from export_persisted_events import export_events


class ExportTests(unittest.TestCase):
    """Verify evidence preservation and fail-closed handling of incomplete logs."""

    def database(self, directory: str, sequence: int = 1) -> Path:
        """Create a minimal synthetic event store."""
        path = Path(directory) / "events.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE events (sequence INTEGER,event_id TEXT,"
                               "timestamp_ms INTEGER,correlation_id TEXT,causation_id TEXT,"
                               "payload_schema TEXT,payload_json TEXT)")
            connection.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                               (sequence, "synthetic", 1, "c", None, "test", '{"ok":true}'))
            connection.commit()
        return path

    def test_source_unchanged_and_output_not_overwritten(self) -> None:
        """Export preserves source bytes and refuses an existing destination."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.database(directory)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            output = Path(directory) / "archive.json"
            export_events(source, output)
            archive = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(archive["complete_through_sequence"], 1)
            self.assertEqual(archive["events"][0]["payload"], {"ok": True})
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            saved = output.read_bytes()
            with self.assertRaises(FileExistsError):
                export_events(source, output)
            self.assertEqual(saved, output.read_bytes())

    def test_gap_creates_no_archive(self) -> None:
        """A missing initial event must not be labeled a complete export."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.database(directory, 2)
            output = Path(directory) / "archive.json"
            with self.assertRaisesRegex(ValueError, "sequence gap"):
                export_events(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
