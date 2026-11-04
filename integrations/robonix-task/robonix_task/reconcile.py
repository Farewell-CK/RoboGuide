"""Re-evaluate retained native task evidence offline without sending any native request."""

import argparse
import json
import sqlite3
from pathlib import Path

from .journal import Journal


def main() -> None:
    """Require exact original identity and sequence before applying repaired result parsing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("handle")
    parser.add_argument("--digest", required=True)
    parser.add_argument("--sequence", required=True, type=int)
    args = parser.parse_args()
    with sqlite3.connect(
        (args.directory / "pilot.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        metadata = json.loads(
            db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()[0]
        )
    counts, events, size = metadata["limits"]
    journal = Journal(
        args.directory, metadata["profile"], max_records=counts, max_events=events, max_bytes=size
    )
    try:
        row = journal.get(args.handle)
        if row["digest"] != args.digest or row["sequence"] != args.sequence:
            raise ValueError("original identity or sequence changed")
        if not journal.finish_task(args.handle, reconcile=True):
            raise ValueError(
                "retained native events do not establish a complete original task result"
            )
        result = journal.get(args.handle)
        print(
            json.dumps(
                {
                    "handle": args.handle,
                    "state": result["state"],
                    "detail": json.loads(result["detail"]),
                },
                ensure_ascii=False,
            )
        )
    finally:
        journal.close()


if __name__ == "__main__":
    main()
