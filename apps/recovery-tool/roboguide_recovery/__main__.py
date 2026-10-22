"""Back up, verify or restore explicitly configured RoboGuide authority and content stores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .archive_reclamation import run as maintain_archive
from .artifact_reclamation import run as reclaim_artifact
from .artifact_snapshot_retirement import run as retire_artifact_snapshot
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
    for operation in ("retire-archive", "gc-archive"):
        maintenance = subcommands.add_parser(operation)
        maintenance.add_argument("--profile", type=Path, required=True)
        maintenance.add_argument("--controller", required=True)
        maintenance.add_argument("--archive-command", required=True)
        maintenance.add_argument("--original-intent-digest", required=True)
        maintenance.add_argument("--command-id", required=True)
        maintenance.add_argument("--reason", required=True)
    reclaim = subcommands.add_parser("gc-artifact")
    reclaim.add_argument("--profile", type=Path, required=True)
    reclaim.add_argument("--artifact", required=True)
    reclaim.add_argument("--content-digest", required=True)
    reclaim.add_argument("--byte-size", type=int, required=True)
    reclaim.add_argument("--command-id", required=True)
    reclaim.add_argument("--reason", required=True)
    for operation in ("retire-artifact-snapshot", "reconcile-artifact-snapshot-retirement"):
        snapshot_retirement = subcommands.add_parser(operation)
        snapshot_retirement.add_argument("--profile", type=Path, required=True)
        snapshot_retirement.add_argument("--original-profile", type=Path, required=True)
        snapshot_retirement.add_argument("--artifact", required=True)
        snapshot_retirement.add_argument("--operation-id", required=True)
        snapshot_retirement.add_argument("--original-intent-digest", required=True)
        snapshot_retirement.add_argument("--command-id", required=True)
        snapshot_retirement.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.operation in {
        "backup",
        "reconcile-snapshots",
        "abort-snapshots",
        "retire-archive",
        "gc-archive",
        "gc-artifact",
        "retire-artifact-snapshot",
        "reconcile-artifact-snapshot-retirement",
    }:
        raw = args.profile.read_bytes()
        if len(raw) > MAX_PROFILE_BYTES:
            parser.error("recovery profile exceeds its byte bound")
        profile = json.loads(raw)
        if args.operation in {"retire-artifact-snapshot", "reconcile-artifact-snapshot-retirement"}:
            original_raw = args.original_profile.read_bytes()
            if len(original_raw) > MAX_PROFILE_BYTES:
                parser.error("original recovery profile exceeds its byte bound")
            result = retire_artifact_snapshot(
                profile,
                json.loads(original_raw),
                args.artifact,
                args.operation_id,
                args.original_intent_digest,
                args.command_id,
                args.reason,
                reconcile=args.operation == "reconcile-artifact-snapshot-retirement",
            )
        elif args.operation == "gc-artifact":
            result = reclaim_artifact(
                profile,
                args.artifact,
                args.content_digest,
                args.byte_size,
                args.command_id,
                args.reason,
            )
        elif args.operation in {"retire-archive", "gc-archive"}:
            result = maintain_archive(
                profile,
                args.controller,
                args.archive_command,
                args.original_intent_digest,
                args.command_id,
                args.reason,
                reclaim=args.operation == "gc-archive",
            )
        elif args.operation == "backup":
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
