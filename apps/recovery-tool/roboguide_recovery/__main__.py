"""Back up, verify or restore explicitly configured RoboGuide authority and content stores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bundle import abort_snapshots, backup, reconcile_snapshots, restore, verify
from .profile import MAX_PROFILE_BYTES


def main() -> None:
    """Perform one explicit administrative operation without starting services or devices."""
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    create = subcommands.add_parser("backup")
    create.add_argument("--profile", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--command-id", required=True)
    inspect = subcommands.add_parser("verify")
    inspect.add_argument("--bundle", type=Path, required=True)
    inspect.add_argument("--manifest-digest", required=True)
    recover = subcommands.add_parser("restore")
    recover.add_argument("--bundle", type=Path, required=True)
    recover.add_argument("--manifest-digest", required=True)
    recover.add_argument("--destination", type=Path, required=True)
    recover.add_argument("--command-id", required=True)
    reconcile = subcommands.add_parser("reconcile-snapshots")
    reconcile.add_argument("--profile", type=Path, required=True)
    reconcile.add_argument("--bundle", type=Path, required=True)
    reconcile.add_argument("--manifest-digest", required=True)
    abort = subcommands.add_parser("abort-snapshots")
    abort.add_argument("--profile", type=Path, required=True)
    abort.add_argument("--destination", type=Path, required=True)
    abort.add_argument("--command-id", required=True)
    args = parser.parse_args()
    if args.operation in {"backup", "reconcile-snapshots", "abort-snapshots"}:
        raw = args.profile.read_bytes()
        if len(raw) > MAX_PROFILE_BYTES:
            parser.error("recovery profile exceeds its byte bound")
        profile = json.loads(raw)
        if args.operation == "backup":
            result = backup(profile, args.destination, args.command_id)
        elif args.operation == "reconcile-snapshots":
            result = reconcile_snapshots(profile, args.bundle, args.manifest_digest)
        else:
            result = abort_snapshots(profile, args.destination, args.command_id)
    elif args.operation == "restore":
        result = restore(args.bundle, args.manifest_digest, args.destination, args.command_id)
    else:
        manifest = verify(args.bundle, args.manifest_digest)
        result = {
            "verified": True,
            "files": len(manifest["files"]),
            "manifest_digest": args.manifest_digest,
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
