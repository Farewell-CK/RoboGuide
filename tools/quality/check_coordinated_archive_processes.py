"""Run online Archive, producer, membership and cold recovery in their required order."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from check_complete_producer_gc_processes import run as collect
from check_complete_producer_gc_processes import verify_recovery
from check_coordinated_archive_membership_processes import run as membership
from check_coordinated_archive_recovery_processes import run as negatives
from check_replica_content_processes import save
from check_replica_content_recovery_processes import run as recover
from check_runnable_content_processes import run as online


def run(
    output: Path,
    controller: Path,
    supervisor: Path,
    storage: Path,
    network: Path,
    previous: Path,
) -> dict[str, Any]:
    """Serialize original endpoint-bearing profiles; only independent unit checks may overlap."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
    }
    try:
        source = output / "online"
        result["online"] = online(
            source, supervisor, controller, storage, True, True, True, True, None, True, True
        )
        cold = recover(source, output / "original-cold", supervisor, None, False)
        result["original_cold"] = cold
        bundle = Path(cold["bundle"])
        checksum = cold["manifest_digest"]
        result["original_refusals"] = negatives(bundle, checksum, output / "refusals", previous)
        collected = collect(source, bundle, checksum, network, supervisor, output / "producers")
        result["producer_gc"] = collected
        members = membership(
            Path(collected["post_gc_bundle"]),
            collected["post_gc_manifest_digest"],
            network,
            supervisor,
            output / "membership",
        )
        result["membership"] = members
        final = output / "final-cold"
        result["final_cold"] = recover(
            source,
            final,
            supervisor,
            None,
            False,
            frozen_bundle=Path(members["post_membership_bundle"]),
            frozen_digest=members["post_membership_manifest_digest"],
        )
        # The final bundle includes later membership evidence; original reclamation evidence
        # and all qualified current package bytes must still survive independently.
        result["independent_final_gc"] = verify_recovery(
            output / "producers",
            final,
            expected_manifest=members["post_membership_manifest_digest"],
        )
        result["passed"] = all(
            value["passed"] for value in result.values() if isinstance(value, dict)
        )
    finally:
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require actual maintained executables and a genuine prior same-wrapper writer."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "controller", "supervisor", "storage", "network", "previous"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        args.controller.resolve(strict=True),
        args.supervisor.resolve(strict=True),
        args.storage.resolve(strict=True),
        args.network.resolve(strict=True),
        args.previous.resolve(strict=True),
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
