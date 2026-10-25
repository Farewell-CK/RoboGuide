"""Compose actual archived v4 Snapshot, native voter loss, learner admission and joint consensus."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from check_complete_producer_gc_processes import leader
from check_network_content_processes import Actor
from check_replica_content_processes import digest, save
from check_replica_content_recovery_processes import assemble, command, profile
from network_content_membership_processes import run_learner_joint


def run(bundle: Path, checksum: str, probe: Path, supervisor: Path, output: Path) -> dict[str, Any]:
    """Exercise original stores and real native waits through a purged Snapshot."""
    assert checksum == "sha256:" + digest(bundle / "manifest.json")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    command(output, "verify", ["verify", "--bundle", str(bundle), "--manifest-digest", checksum])
    command(
        output,
        "restore",
        [
            "restore",
            "--bundle",
            str(bundle),
            "--manifest-digest",
            checksum,
            "--destination",
            str(output / "restored"),
            "--command-id",
            "actual-archived-member-composition",
        ],
    )
    owned: list[Actor] = []
    actors: dict[int, Actor] = {}
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "original_manifest_digest": checksum,
    }
    frozen = {str(path): digest(path) for path in (probe, supervisor, Path(__file__))}
    try:
        with (
            tempfile.TemporaryDirectory(prefix="rg-archive-joint-") as temporary,
            ExitStack() as logs,
        ):
            configs = assemble(output / "restored", Path(temporary), supervisor)
            paths: dict[int, Path] = {}
            declarations: dict[int, dict[str, Any]] = {}
            for member, (_, config) in configs.items():
                assert config["coordinated_archive_base"] in {
                    "roboguide.controller-replica-process/v0.6",
                    "roboguide.controller-replica-process/v0.7",
                }
                declaration = {
                    "replica": config["replica"],
                    "content_root": config["content"]["root"],
                    "parent_sql": str(Path(config["storage_directory"]) / "replica.sqlite3"),
                    "member_stores": config["content"]["bootstrap_owners"],
                    "staging_gc": True,
                    "restore_binding_digest": config["content"]["restore_binding_digest"],
                    "initialization_parent": config["coordinated_archive_base"].endswith("/v0.7"),
                    "coordinated_archive": True,
                    "initialization": config["content"].get("initialization"),
                }
                paths[member] = output / f"infrastructure-{member}.json"
                declarations[member] = declaration
                save(paths[member], declaration)
                log = logs.enter_context((output / f"member-{member}-original.log").open("wb"))
                actors[member] = Actor(probe, paths[member], log, owned)
            current = leader(actors)
            original = actors[current].ask({"action": "quorum"})
            assert set(original["complete_members"]) == set(configs)
            lagging = max(member for member in configs if member != current)
            actors[lagging].stop(kill=True)
            survivors = sorted(set(configs) - {lagging})
            changed = actors[current].ask(
                {"action": "change-voters", "voters": survivors, "retain_removed_as_learners": True}
            )
            assert changed["voters"] == survivors
            compact = actors[current].ask({"action": "compact-snapshot", "budget_ms": 30000})
            assert (
                compact["actual_snapshot"]
                == compact["actual_purged"]
                == compact["original_applied"]
            )
            result["actual_missed_membership_snapshot"] = compact
            log = logs.enter_context((output / f"member-{lagging}-snapshot-return.log").open("wb"))
            actors[lagging] = Actor(probe, paths[lagging], log, owned)
            result["actual_learner_joint"] = run_learner_joint(
                actors, declarations, [*survivors, lagging], current
            )
            # The helper actually stops the removed voter. Restore only that original member,
            # then admit the complete initial set through the unchanged real quorum gate.
            for member, actor in list(actors.items()):
                if actor.process.poll() is not None:
                    log = logs.enter_context(
                        (output / f"member-{member}-original-return.log").open("wb")
                    )
                    actors[member] = Actor(probe, paths[member], log, owned)
            current = leader(actors)
            restored = actors[current].ask(
                {
                    "action": "change-voters",
                    "voters": sorted(configs),
                    "retain_removed_as_learners": True,
                }
            )
            assert set(restored["voters"]) == set(configs)
            final = actors[current].ask({"action": "quorum"})
            assert set(final["complete_members"]) == set(configs)
            assert final["target"]["batch_digest"] == original["target"]["batch_digest"]
            result["complete_original_v4_target_unchanged"] = final
            for actor in reversed(owned):
                actor.stop()
            deployment = output / "deployment"
            deployment.mkdir(mode=0o700)
            inputs = output / "restored/stores/inputs"
            for tree in ("tls", "node-tls"):
                if (inputs / tree).exists():
                    shutil.copytree(inputs / tree, deployment / tree)
            for member, (_, config) in configs.items():
                save(deployment / f"configuration-{member}.json", config)
            backup = output / "post-membership"
            backup.mkdir(mode=0o700)
            declaration = profile(deployment, backup)
            save(backup / "profile.json", declaration)
            receipt = command(
                backup,
                "backup",
                [
                    "backup",
                    "--profile",
                    str(backup / "profile.json"),
                    "--destination",
                    str(backup / "bundle"),
                    "--command-id",
                    "actual-archived-member-completion",
                ],
            )
            result["post_membership_bundle"] = str(backup / "bundle")
            result["post_membership_manifest_digest"] = receipt["manifest_digest"]
            assert {
                str(path): digest(path) for path in (probe, supervisor, Path(__file__))
            } == frozen
            result["passed"] = True
    finally:
        for actor in reversed(owned):
            if actor.process.poll() is None:
                actor.process.kill()
                actor.record["returncode"] = actor.process.wait(timeout=10)
                actor.record["forced_cleanup"] = True
        result["actual_processes"] = [actor.record for actor in owned]
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require frozen existing binaries and one explicit original complete-content bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "probe", "supervisor", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-digest", required=True)
    args = parser.parse_args()
    run(
        args.bundle.resolve(strict=True),
        args.manifest_digest,
        args.probe.resolve(strict=True),
        args.supervisor.resolve(strict=True),
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
