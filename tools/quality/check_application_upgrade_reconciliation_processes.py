"""Reconcile a real retained uncertain upgrade after restart without resending activation."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from check_application_upgrade_processes import metadata
from check_network_content_processes import Actor
from check_replica_content_processes import digest, save
from check_state_verifier_processes import wait_for


def run(source: Path, output: Path, probe: Path, original_binary: Path) -> dict[str, Any]:
    """Retain original SQL inodes, clocks and pending commands through restart."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    configurations: dict[int, dict[str, Any]] = {}
    for path in source.glob("configuration-*-incarnation-*.json"):
        value = json.loads(path.read_bytes())
        member = value["replica"]["replica_id"]
        if value.get("initialization_parent"):
            configurations[member] = value
    assert len(configurations) >= 3
    originals = {
        member: metadata(Path(config["parent_sql"])) for member, config in configurations.items()
    }
    intents = [row["application_upgrade_operations_v1"] for row in originals.values()]
    assert all(len(operations) == 1 for operations in intents)
    intent = intents[0][0]["intent"]
    assert all(operations[0]["intent"] == intent for operations in intents)
    assert all(row["application_upgrade_authority_v1"] is None for row in originals.values())
    assert "sha256:" + digest(original_binary) == intent["request"]["target"]["binary_digest"]
    identities = {
        member: Path(config["parent_sql"]).stat().st_ino
        for member, config in configurations.items()
    }
    save(
        output / "original-local-evidence.json",
        {str(member): row for member, row in originals.items()},
    )
    save(
        output / "source-identity.json",
        {str(path): digest(path) for path in [probe, original_binary, Path(__file__)]},
    )
    actors: dict[int, Actor] = {}
    owned: list[Actor] = []
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    with ExitStack() as scope:
        try:
            for member, config in configurations.items():
                config["upgrade_binary"] = str(original_binary)
                path = output / f"configuration-{member}.json"
                save(path, config)
                log = scope.enter_context((output / f"member-{member}.log").open("xb"))
                actors[member] = Actor(probe, path, log, owned)

            def leader() -> int | None:
                """Observe current infrastructure leadership beside the application hold."""
                for member, actor in actors.items():
                    observed = actor.ask({"action": "status"})
                    epoch = observed.get("epoch")
                    if epoch is not None and epoch["replica_id"] == member:
                        return member
                return None

            source_member = wait_for(leader, "actual restarted upgrade infrastructure leader", 30)
            deadline = time.monotonic() + 10
            authority = None
            while time.monotonic() < deadline:
                authority = actors[source_member].ask({"action": "observe-application-upgrade"})[
                    "authority"
                ]
                if authority is not None:
                    break
                time.sleep(0.1)
            if authority is None:
                abandoned = actors[source_member].ask(
                    {
                        "action": "abort-application-upgrade",
                        "original_command_id": intent["request"]["command_id"],
                        "command_id": "actual-original-unknown-upgrade-abandonment",
                    }
                )["receipt"]
                assert abandoned["intent"] == intent
                result["original_outcome"] = "explicit-abandonment"
            else:
                assert (
                    len(authority["receipts"]) == 1 and authority["receipts"][0]["intent"] == intent
                )
                result["original_outcome"] = "original-log-committed-after-restart"
            wait_for(
                lambda: all(
                    metadata(Path(config["parent_sql"]))["application_upgrade_authority_v1"]
                    is not None
                    for config in configurations.values()
                ),
                "same original decision on every actual member",
                30,
            )
            for member, config in configurations.items():
                current = metadata(Path(config["parent_sql"]))
                assert Path(config["parent_sql"]).stat().st_ino == identities[member]
                assert (
                    current["application_upgrade_operations_v1"]
                    == originals[member]["application_upgrade_operations_v1"]
                )
                assert current["identity"] == originals[member]["identity"]
                assert current.get("checkpoint") == originals[member].get("checkpoint")
                assert current.get("revision") == originals[member].get("revision")
            result.update(
                passed=True,
                original_intent=intent,
                original_clocks_and_waits_unchanged=True,
                activation_never_resent=True,
                original_store_inodes=identities,
            )
        except BaseException as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            for actor in reversed(owned):
                try:
                    actor.stop()
                except BaseException as error:
                    result.setdefault("cleanup_errors", []).append(str(error))
                    result["passed"] = False
            result["actual_processes"] = [actor.record for actor in owned]
            save(output / "result.json", result)
    return result


def main() -> None:
    """Require explicit real Unknown storage and byte-identical original target input."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["source", "output", "probe", "original-binary"]:
        parser.add_argument("--" + name, type=Path, required=True)
    arguments = parser.parse_args()
    result = run(
        arguments.source.resolve(strict=True),
        arguments.output,
        arguments.probe.resolve(strict=True),
        arguments.original_binary.resolve(strict=True),
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"actual_processes", "original_intent"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
