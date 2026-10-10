"""Export sealed Controller evidence to a new sidecar without changing original results."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def export_events(database: Path, output: Path) -> None:
    """Read one SQLite snapshot, preserve event identity and exclusively create an archive."""
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,"
            "payload_schema,payload_json FROM events ORDER BY sequence"
        ).fetchall()
    events = []
    for index, row in enumerate(rows, 1):
        event = dict(row)
        if event["sequence"] != index:
            raise ValueError("persisted events contain a sequence gap")
        event["payload"] = json.loads(event.pop("payload_json"))
        events.append(event)
    sources = {}
    for path in (database, Path(str(database) + "-wal")):
        if path.exists():
            sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    archive = {
        "schema": "roboguide.e2-persisted-event-export/v0.1",
        "source": str(database), "source_sha256": sources,
        "complete_through_sequence": len(events), "events": events,
        "note": "Read-only export from a finished run; original verdict and archive unchanged.",
    }
    with output.open("x", encoding="utf-8") as destination:
        json.dump(archive, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps({"event_count": len(events), "output": str(output)}))


def main() -> None:
    """Require explicit source and new output paths for a sealed-run evidence audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    export_events(args.database, args.output)


if __name__ == "__main__":
    main()
