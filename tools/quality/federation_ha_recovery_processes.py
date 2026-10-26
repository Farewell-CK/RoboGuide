"""Recover both actual original domains in one complete quiescent multi-cluster bundle."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import signal
import sqlite3
import tempfile
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from check_identity_processes import client
from check_replica_content_processes import digest, save
from check_replica_content_recovery_processes import assemble, command, profile
from check_state_verifier_processes import address, wait_for
from federation_ha_processes import ReplicatedCase, RoutingEndpoint


class RestoredDomain(ReplicatedCase):
    """Run restored infrastructure with original explicit TLS inputs and zero Nodes."""

    def __init__(
        self, original: ReplicatedCase, configurations: dict[int, dict[str, Any]], root: Path
    ) -> None:
        """Retain actual original credentials and routes without creating Nodes or CPU jobs."""
        root.mkdir(mode=0o700)
        self.root = root
        self.configurations = configurations
        self.supervisor = original.supervisor
        self.environment = original.environment
        self.contexts = original.contexts
        self.origin = original.origin
        self.grpc, self.http, self.artifact = original.grpc, original.http, original.artifact
        self.supervisors = {}
        self.processes = []
        self.logs = []
        self.routers = []
        self.routing_threads = []
        self.closed = False

    def boot(self) -> None:
        """Start only declared original restored voters and actual TLS passthrough."""
        for member, configuration in self.configurations.items():
            path = self.root / f"configuration-{member}.json"
            save(path, configuration)
            self.supervisors[member] = self.start(
                [str(self.supervisor), str(path)], "restored-supervisor-" + str(member)
            )
        wait_for(lambda: self.current(), "actual original restored domain leader", 60)
        for endpoint, plane in [
            (self.grpc, "grpc"),
            (self.http, "http"),
            (self.artifact, "artifact"),
        ]:
            router = RoutingEndpoint(self, endpoint, plane)
            thread = threading.Thread(target=router.serve_forever, daemon=True)
            thread.start()
            self.routers.append(router)
            self.routing_threads.append(thread)
        wait_for(lambda: self.exchange("/healthz"), "actual restored original domain HTTP", 30)

    def close(self) -> None:
        """Reap every actual restored supervisor and close only this owned transport."""
        if self.closed:
            return
        self.closed = True
        try:
            for process, record in reversed(self.processes):
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                record["actual_wait"] = process.wait(timeout=10)
            save(self.root / "owned-processes.json", [record for _, record in self.processes])
        finally:
            for router in self.routers:
                router.close()
            for thread in self.routing_threads:
                thread.join(timeout=5)
            for log in self.logs:
                log.close()


def prepare(cases: list[ReplicatedCase], output: Path) -> dict[str, Any]:
    """Declare all actual original parents, Roots, child SQL/CAS, Node journals and file inputs."""
    components = []
    credentials = {}
    for index, case in enumerate(cases):
        prefix = f"domain-{index}-"
        temporary = output / ("profile-" + str(index))
        temporary.mkdir(mode=0o700)
        # Stable credential mount paths are deployment inputs, separate from restored stores.
        # Copy exact owned input bytes into the bundle and verify them independently on activation.
        target = case.root / "node-tls"
        target.mkdir(mode=0o700)
        paths = list(case.certificates.glob("*"))
        paths += [
            case.authorization,
            case.root / "artifact-authorization.json",
            case.root / "node-identities.json",
        ]
        paths += [
            Path(value)
            for key, value in case.environment.items()
            if key
            in {
                "ROBOGUIDE_FEDERATION_DOMAIN_GRANTS",
                "ROBOGUIDE_FEDERATION_PEERS",
                "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA",
            }
        ]
        for source in paths:
            if source.is_file():
                copied = target / source.name
                if copied.exists():
                    assert digest(copied) == digest(source)
                else:
                    shutil.copy2(source, copied)
                credentials[str(source)] = digest(source)
        declaration = profile(case.root, temporary)
        for component in declaration["components"]:
            component["component_id"] = prefix + component["component_id"]
            component["depends_on"] = [prefix + name for name in component["depends_on"]]
            components.append(component)
        controllers = [
            component["component_id"]
            for component in components
            if component["kind"] == "controller" and component["component_id"].startswith(prefix)
        ]
        for number, node in enumerate(case.node_roots):
            components.append(
                {
                    "component_id": prefix + "node-" + str(number),
                    "kind": "node",
                    "root": str(node),
                    "database": "execution-journal.sqlite3",
                    "trees": [],
                    "depends_on": controllers,
                }
            )
        components.append(
            {
                "component_id": prefix + "original-files",
                "kind": "files",
                "root": str(case.root),
                "database": None,
                "trees": ["local-eaios"],
                "depends_on": controllers,
            }
        )
    save(output / "original-credential-inputs.json", credentials)
    return {
        "schema": "roboguide.recovery-profile/v0.31",
        "profile_id": "actual-two-domain-originals",
        "revision": 1,
        "max_bytes": 2 * 1024 * 1024 * 1024,
        "max_files": 100000,
        "budget_seconds": 600,
        "artifact_replica_sets": [],
        "components": components,
    }


def recover(
    cases: list[ReplicatedCase],
    output: Path,
    *,
    retained: Path | None = None,
    command_id: str = "actual-global-domain-backup",
) -> dict[str, Any]:
    """Verify the full original bundle and restart real domain leaders with zero Nodes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if retained is None:
        declaration = prepare(cases, output)
    else:
        declaration = json.loads((retained / "profile.json").read_bytes())
        credentials = json.loads((retained / "original-credential-inputs.json").read_bytes())
        assert credentials == {path: digest(Path(path)) for path in credentials}
        save(output / "original-credential-inputs.json", credentials)
    save(output / "profile.json", declaration)
    backed = command(
        output,
        "backup",
        [
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            command_id,
        ],
    )
    checksum = backed["manifest_digest"]
    command(
        output,
        "verify",
        ["verify", "--bundle", str(output / "bundle"), "--manifest-digest", checksum],
    )
    arguments = [
        "restore",
        "--bundle",
        str(output / "bundle"),
        "--manifest-digest",
        checksum,
        "--destination",
        str(output / "restored"),
        "--command-id",
        "actual-global-domain-restore",
    ]
    restored = command(output, "restore", arguments)
    assert command(output, "readonly-restore-repeat", arguments) == restored
    assert len(restored["replica_content_bindings"]) == sum(len(c.configurations) for c in cases)
    credentials = json.loads((output / "original-credential-inputs.json").read_bytes())
    assert credentials == {path: digest(Path(path)) for path in credentials}
    # A separately rehashed incomplete original cluster must still refuse before publication.
    invalid = copy.deepcopy(declaration)
    missing = next(item for item in invalid["components"] if item["kind"] == "replica-content")
    invalid["components"].remove(missing)
    save(output / "missing-original-root.json", invalid)
    command(
        output,
        "missing-original-root-refused",
        [
            "backup",
            "--profile",
            str(output / "missing-original-root.json"),
            "--destination",
            str(output / "refused-bundle"),
            "--command-id",
            "actual-missing-domain-root",
        ],
        refused=True,
    )
    assert not (output / "refused-bundle").exists()
    readers = []
    result: dict[str, Any] = {
        "passed": False,
        "manifest_digest": checksum,
        "restore_starts_services": False,
        "new_nodes": 0,
        "new_cpu_jobs": 0,
        "devices": 0,
        "original_credential_mounts_preserved": True,
        "actual_domains": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="rg-fed-cold-") as temporary:
            for index, case in enumerate(cases):
                ipc = Path(temporary) / str(index)
                ipc.mkdir(mode=0o700)
                configs = assemble(
                    output / "restored", ipc, case.supervisor, component_prefix=f"domain-{index}-"
                )
                reader = RestoredDomain(
                    case,
                    {member: row for member, (_path, row) in configs.items()},
                    output / ("domain-" + str(index)),
                )
                readers.append(reader)
            for reader in reversed(readers):
                reader.boot()
            for index, reader in enumerate(readers):
                original = cases[index]
                # The actual final leader was sampled before drain. An arbitrary older child's
                # nonempty SQL is insufficient to cover every original global transaction.
                wrapper = json.loads(
                    (original.root / "final-original-application.json").read_bytes()
                )
                expected = wrapper["federation"]["coordinations"]["transactions"]
                transactions = list(expected)
                views = [
                    reader.exchange("/v1/federation/transactions/" + identity)
                    for identity in transactions
                ]
                from check_federation_ha_processes import transfer

                leaders = []
                for target in reader.configurations:
                    current, _ = reader.current()
                    if current != target:
                        transfer(reader, "actual-cold-" + str(target), target)
                    observations = [
                        reader.exchange("/v1/federation/transactions/" + identity)
                        for identity in transactions
                    ]
                    leaders.append(
                        {
                            "actual_leader_status": reader.current()[1],
                            "original_transaction_views": observations,
                        }
                    )
                member, _ = reader.current()
                path = (
                    Path(reader.configurations[member]["storage_directory"]) / "controller.sqlite3"
                )
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
                    actual = json.loads(
                        sql.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                            0
                        ]
                    )
                for identity, before in expected.items():
                    after = actual["federation"]["coordinations"]["transactions"][identity]
                    for field in ("manifest", "root_epoch", "budget", "nonces", "decision"):
                        assert before[field] == after[field]
                    for domain, phases in before["intents"].items():
                        for phase, intent in phases.items():
                            later = after["intents"][domain][phase]
                            assert {
                                key: value for key, value in intent.items() if key != "observations"
                            } == {
                                key: value for key, value in later.items() if key != "observations"
                            }
                            assert (
                                later["observations"][: len(intent["observations"])]
                                == intent["observations"]
                            )
                packages = []
                for config in reader.configurations.values():
                    parent = Path(config["storage_directory"]) / "replica.sqlite3"
                    with closing(sqlite3.connect(parent.as_uri() + "?mode=ro", uri=True)) as sql:
                        state = json.loads(
                            sql.execute(
                                "SELECT value FROM replica_v1_meta WHERE key='content_state'"
                            ).fetchone()[0]
                        )
                    for original_archive in state["candidate"]["recovery"]["coordinated_archives"]:
                        if original_archive["completed_ms"] is None:
                            continue
                        key = original_archive["intent"]["package"]
                        content = (
                            Path(config["content"]["root"])
                            / "content-v1"
                            / key["source_owner"][15:]
                            / key["content_digest"][7:]
                        )
                        assert content.stat().st_size == key["byte_size"]
                        assert "sha256:" + digest(content) == key["content_digest"]
                        packages.append(
                            {"member": config["replica"]["replica_id"], "original_package": key}
                        )
                assert packages
                result["actual_domains"].append(
                    {
                        "leader": reader.current()[1],
                        "original_transaction_views": views,
                        "actual_original_leaders": leaders,
                        "original_software_nodes_restarted": False,
                        "independently_read_original_packages": packages,
                    }
                )
            result["passed"] = True
    finally:
        for reader in reversed(readers):
            reader.close()
        result["actual_processes"] = [
            record for reader in readers for _, record in reader.processes
        ]
        if any(record.get("actual_wait") != 0 for record in result["actual_processes"]):
            result["passed"] = False
        save(output / "result.json", result)
    return result


class RetainedDomain(ReplicatedCase):
    """Read a drained domain without constructing a Node, backend or new original store."""

    def __init__(self, root: Path, identities: dict[str, str]) -> None:
        """Use retained configurations, original credentials and a proven exited supervisor."""
        self.root = root
        self.configurations = {
            configuration["replica"]["replica_id"]: configuration
            for path in root.glob("configuration-*.json")
            for configuration in [json.loads(path.read_bytes())]
        }
        assert len(self.configurations) == 3
        records = json.loads((root / "owned-processes.json").read_bytes())
        supervisors = [row for row in records if row["name"].startswith("supervisor-")]
        assert len(supervisors) == 3 and all(row["native_wait"] == 0 for row in supervisors)
        executables = {row["arguments"][0] for row in supervisors}
        assert len(executables) == 1
        self.supervisor = Path(next(iter(executables)))
        for binary in {str(self.supervisor)} | {
            configuration["controller_binary"] for configuration in self.configurations.values()
        }:
            assert digest(Path(binary)) == identities[binary]
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
        } | {"TOKIO_WORKER_THREADS": "1"}
        self.contexts = {"admin": client(root / "pki", "admin")}
        self.grpc, self.http, self.artifact = (address() for _ in range(3))
        configuration = next(iter(self.configurations.values()))
        peer_path = configuration["federation"]["peers"]
        if peer_path is not None:
            peers = json.loads(Path(peer_path).read_bytes())
            self.http = peers["peers"][configuration["federation"]["domain"]]["address"]
        assert self.http.startswith("127.0.0.1:")
        self.origin = "https://" + self.http


def resume(source: Path, output: Path, command_id: str) -> dict[str, Any]:
    """Repeat cold acceptance on exact original data after actual online success and drain."""
    original = json.loads((source / "result.json").read_bytes())
    assert original.get("online_cases_passed") is True
    assert original["cleanup_errors"] == [] and not original.get("final_observation_errors")
    identities = json.loads((source / "source-identity.json").read_bytes())
    cases: list[ReplicatedCase] = [
        RetainedDomain(source / side, identities) for side in ("root", "peer")
    ]
    retained = source / "complete-recovery"
    inputs = {
        str(path): digest(path)
        for path in [
            source / "result.json",
            source / "source-identity.json",
            retained / "profile.json",
            retained / "original-credential-inputs.json",
            *source.glob("*/configuration-*.json"),
            *source.glob("*/final-original-application.json"),
        ]
    }
    current = {
        str(path): digest(path)
        for path in [
            Path(__file__),
            *(Path(__file__).resolve().parents[2] / "apps/recovery-tool/roboguide_recovery").glob(
                "*.py"
            ),
        ]
    }
    result = recover(cases, output, retained=retained, command_id=command_id)
    assert inputs == {path: digest(Path(path)) for path in inputs}
    assert current == {path: digest(Path(path)) for path in current}
    result["retained_original_inputs"] = inputs
    result["current_recovery_code"] = current
    result["retained_online_result_changed"] = False
    result["new_backup_command_id"] = command_id
    save(output / "result.json", result)
    return result


def main() -> None:
    """Resume only the maintained complete cold gate; never reconstruct original online effects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--command-id", required=True)
    arguments = parser.parse_args()
    if not resume(arguments.source.absolute(), arguments.output.absolute(), arguments.command_id)[
        "passed"
    ]:
        raise SystemExit("original complete cold recovery failed")


if __name__ == "__main__":
    main()
