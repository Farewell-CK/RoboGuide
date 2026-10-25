"""Exercise actual parent8/Root cold backup, new-inode restore and zero-Node native restart."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import http.client as http_client
import json
import os
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from check_replica_content_processes import digest, save
from check_runnable_content_processes import http

ROOT = Path(__file__).resolve().parents[2]


def archive_inputs(source: Path, components: list[dict[str, Any]]) -> None:
    """Declare this owned fixture's original Archive outputs without granting product authority."""
    existing = {value["root"] for value in components}
    for path in sorted(source.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        original = config.get("content", {}).get("initialization_source")
        if original is None or original["source_cas"] in existing:
            continue
        components.append(
            {
                "component_id": f"initialization-cas-{config['replica']['replica_id']}",
                "kind": "artifact",
                "root": original["source_cas"],
                "database": None,
                "trees": [],
                "depends_on": [],
                "storage_schema": "roboguide.artifact-storage/v3",
            }
        )
        existing.add(original["source_cas"])
    destinations: set[str] = set()
    for component in components:
        if component["kind"] != "controller":
            continue
        database = Path(component["root"]) / component["database"]
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
            rows = sql.execute(
                "SELECT intent_json FROM event_archive_operations LIMIT 16385"
            ).fetchall()
        assert len(rows) <= 16384
        for (raw,) in rows:
            assert len(raw.encode()) <= 256 * 1024 * 1024
            destinations.add(json.loads(raw)["request"]["destination"])
    dependencies = [
        value["component_id"] for value in components if value["kind"] in {"controller", "artifact"}
    ]
    for index, destination in enumerate(sorted(destinations)):
        components.append(
            {
                "component_id": f"original-archive-{index}",
                "kind": "archive",
                "root": destination,
                "database": None,
                "trees": [],
                "depends_on": dependencies,
            }
        )


def profile(source: Path, output: Path) -> dict[str, Any]:
    """Declare only actual original members/stores and copy explicit deployment inputs privately."""
    components: list[dict[str, Any]] = []
    inputs = output / "inputs"
    inputs.mkdir(mode=0o700)
    shutil.copytree(source / "tls", inputs / "tls")
    input_trees = ["tls", "configs"]
    if (source / "node-tls").exists():
        shutil.copytree(source / "node-tls", inputs / "node-tls")
        input_trees.append("node-tls")
    configs = inputs / "configs"
    configs.mkdir(mode=0o700)
    parent_versions = set()
    scoped_generation = False
    topology_generation = False
    binary_inputs: dict[str, dict[str, str]] = {}
    for path in sorted(source.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        if config.get("application_upgrade") is not None:
            binary_root = inputs / "binaries"
            binary_root.mkdir(mode=0o700, exist_ok=True)
            if "binaries" not in input_trees:
                input_trees.append("binaries")
            for declared in [
                config["controller_binary"],
                config["application_upgrade"]["binary"],
                *[
                    item["binary"]
                    for item in config["application_upgrade"].get("retained_binaries", [])
                ],
            ]:
                actual = Path(declared).resolve(strict=True)
                checksum = digest(actual)
                name = checksum + "-" + actual.name
                copied = binary_root / name
                if not copied.exists():
                    shutil.copyfile(actual, copied)
                if digest(copied) != checksum:
                    raise ValueError("original declared upgrade executable changed while copying")
                binary_inputs[declared] = {"name": name, "sha256": checksum}
        member = config["replica"]["replica_id"]
        shutil.copyfile(path, configs / path.name)
        directory = Path(config["storage_directory"])
        with closing(
            sqlite3.connect((directory / "replica.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as parent_sql:
            parent_versions.add(parent_sql.execute("PRAGMA user_version").fetchone()[0])
            metadata = dict(parent_sql.execute("SELECT key,value FROM replica_v1_meta"))
            original_identity = json.loads(metadata["identity"])
            upgrade = json.loads(metadata.get("application_upgrade_authority_v1", "null"))
            topology_generation |= original_identity[
                1
            ] == "roboguide.controller-checkpoint/v58" or (
                upgrade is not None
                and upgrade.get("target", {}).get("application_schema")
                == "roboguide.controller-checkpoint/v58"
            )
            scoped_generation |= original_identity[1] == "roboguide.controller-checkpoint/v57" or (
                upgrade is not None
                and upgrade.get("target", {}).get("application_schema")
                == "roboguide.controller-checkpoint/v57"
            )
        dependencies = []
        if (directory / "controller.sqlite3").exists():
            components.append(
                {
                    "component_id": f"controller-{member}",
                    "kind": "controller",
                    "root": str(directory),
                    "database": "controller.sqlite3",
                    "trees": [],
                    "depends_on": [f"cas-{member}"],
                    "storage_schema": "roboguide.controller-storage/v1",
                }
            )
            components.append(
                {
                    "component_id": f"cas-{member}",
                    "kind": "artifact",
                    "root": str(directory / "artifacts"),
                    "database": None,
                    "trees": [],
                    "depends_on": [],
                    "storage_schema": "roboguide.artifact-storage/v3",
                }
            )
            dependencies = [f"controller-{member}", f"cas-{member}"]
        components.append(
            {
                "component_id": f"parent-{member}",
                "kind": "replica",
                "root": str(directory),
                "database": "replica.sqlite3",
                "trees": [],
                "depends_on": dependencies,
            }
        )
        content_root = Path(config["content"]["root"])
        with closing(
            sqlite3.connect((content_root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as sql:
            layout = sql.execute("SELECT value FROM metadata WHERE key='layout'").fetchone()[0]
        root_component = {
            "component_id": f"root-{member}",
            "kind": "replica-content",
            "root": str(content_root),
            "database": None,
            "trees": [],
            "depends_on": [f"parent-{member}"],
            "storage_schema": layout,
        }
        if layout in {
            "roboguide.replica-content-layout/v0.6",
            "roboguide.replica-content-layout/v0.7",
            "roboguide.replica-content-layout/v0.8",
        }:
            root_component["restore_binding_digest"] = config["content"].get(
                "restore_binding_digest"
            )
        components.append(root_component)
    archive_inputs(source, components)
    if binary_inputs:
        save(inputs / "binaries/index.json", binary_inputs)
    components.append(
        {
            "component_id": "inputs",
            "kind": "files",
            "root": str(inputs),
            "database": None,
            "trees": input_trees,
            "depends_on": [
                value["component_id"] for value in components if value["kind"] == "replica"
            ],
        }
    )
    generation = (
        27
        if topology_generation
        else 26
        if scoped_generation
        else 25
        if 10 in parent_versions
        else 23
        if 9 in parent_versions
        else 22
        if any(
            value.get("storage_schema") == "roboguide.replica-content-layout/v0.8"
            for value in components
        )
        else 21
        if any(
            value.get("storage_schema") == "roboguide.replica-content-layout/v0.7"
            for value in components
        )
        else (
            20
            if any(
                value.get("storage_schema") == "roboguide.replica-content-layout/v0.6"
                for value in components
            )
            else 19
        )
    )
    return {
        "schema": f"roboguide.recovery-profile/v0.{generation}",
        "profile_id": "actual-complete-content-roots",
        "revision": 1,
        "max_bytes": (3 if binary_inputs else 1) * 1024**3,
        "max_files": 20000,
        "budget_seconds": 180,
        "artifact_replica_sets": [],
        "components": components,
    }


def command(
    output: Path, label: str, arguments: list[str], *, refused: bool = False
) -> dict[str, Any]:
    """Run the actual CLI once, retaining its real native wait and bounded raw response."""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    process = subprocess.run(
        [sys.executable, "-m", "roboguide_recovery", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        timeout=190,
    )
    record = {
        "arguments": arguments,
        "returncode": process.returncode,
        "stdout": process.stdout.decode(),
        "stderr": process.stderr.decode(),
    }
    save(output / (label + ".json"), record)
    assert (process.returncode != 0) == refused, record
    if refused:
        return record
    result = json.loads(process.stdout)
    assert isinstance(result, dict)
    return result


def assemble(restored: Path, ipc: Path, supervisor: Path) -> dict[int, tuple[Path, dict[str, Any]]]:
    """Move child projections/CAS into parents, preserving every bound Root/SQL inode."""
    stores = restored / "stores"
    receipts = json.loads((restored / "restore-receipt.json").read_text())
    bindings = {
        value["component_id"]: value["binding_digest"]
        for value in receipts["replica_content_bindings"]
    }
    result = {}
    configured = restored.parent / "restored-configurations"
    configured.mkdir(mode=0o700)
    for path in sorted((stores / "inputs/configs").glob("configuration-*.json")):
        config = json.loads(path.read_text())
        member = config["replica"]["replica_id"]
        parent = stores / f"parent-{member}"
        child = stores / f"controller-{member}"
        if child.exists():
            for file in child.iterdir():
                file.rename(parent / file.name)
        cas = stores / f"cas-{member}"
        if cas.exists():
            cas.rename(parent / "artifacts")
        binding = next(
            value["binding"]
            for value in receipts["replica_content_bindings"]
            if value["component_id"] == f"root-{member}"
        )
        original_process = config["schema"]
        config["schema"] = (
            "roboguide.controller-replica-process/v0.7"
            if config["schema"] == "roboguide.controller-replica-process/v0.7"
            else "roboguide.controller-replica-process/v0.6"
            if binding["source_root_version"] == 8
            else "roboguide.controller-replica-process/v0.5"
            if binding["source_root_version"] == 7
            else "roboguide.controller-replica-process/v0.4"
            if binding["source_root_version"] == 6
            else "roboguide.controller-replica-process/v0.3"
        )
        if config.get("application_upgrade") is not None:
            config["schema"] = original_process
            binary_root = stores / "inputs/binaries"
            index = json.loads((binary_root / "index.json").read_bytes())
            for container, key in [
                (config, "controller_binary"),
                (config["application_upgrade"], "binary"),
                *[
                    (item, "binary")
                    for item in config["application_upgrade"].get("retained_binaries", [])
                ],
            ]:
                record = index[container[key]]
                name = record["name"]
                if Path(name).name != name:
                    raise ValueError("original upgrade executable input escapes its declared root")
                executable = binary_root / name
                if digest(executable) != record["sha256"]:
                    raise ValueError("restored original upgrade executable bytes differ")
                executable.chmod(0o700)
                container[key] = str(executable)
        config["storage_directory"] = str(parent)
        config["content"]["root"] = str(stores / f"root-{member}")
        config["content"]["socket"] = str(ipc / f"{member}.sock")
        config["content"]["restore_binding_digest"] = bindings[f"root-{member}"]
        for plane in ("node_tls", "operator_tls", "artifact_tls"):
            if config.get(plane) is not None:
                config[plane] = dict(config[plane])
                for name in ("server_cert", "server_key", "client_ca", "profile"):
                    config[plane][name] = str(
                        stores / "inputs/node-tls" / Path(config[plane][name]).name
                    )
        config["replica"]["tls"].update(
            {
                "ca_cert_path": str(stores / "inputs/tls/ca.crt"),
                "cert_path": str(
                    stores / "inputs/tls" / Path(config["replica"]["tls"]["cert_path"]).name
                ),
                "key_path": str(
                    stores / "inputs/tls" / Path(config["replica"]["tls"]["key_path"]).name
                ),
            }
        )
        target = configured / f"configuration-{member}.json"
        save(target, config)
        actual_policy = (
            subprocess.check_output([str(supervisor), "--policy-digest", str(target)], timeout=10)
            .decode()
            .strip()
        )
        assert actual_policy == config["replica"]["application_policy_digest"]
        if config.get("application_upgrade") is not None:
            actual_target = subprocess.check_output(
                [str(supervisor), "--upgrade-policy-digest", str(target)], text=True, timeout=10
            ).strip()
            assert actual_target == config["application_upgrade"]["target"]["policy_digest"]
        result[member] = (target, config)
    return result


def prepare_bundle(
    source: Path,
    output: Path,
    operation: str,
    frozen_bundle: Path | None,
    frozen_digest: str | None,
) -> tuple[Path, str]:
    """Reuse only a declared pinned bundle, or run the original complete source backup gate."""
    if frozen_bundle is not None:
        if frozen_digest is None:
            raise ValueError("frozen recovery input requires its original manifest digest")
        save(
            output / "frozen-bundle-input.json",
            {"bundle": str(frozen_bundle), "manifest_digest": frozen_digest},
        )
        return frozen_bundle, frozen_digest
    declaration = profile(source, output)
    save(output / "profile.json", declaration)
    if declaration["schema"] == "roboguide.recovery-profile/v0.23":
        old = {**declaration, "schema": "roboguide.recovery-profile/v0.22"}
        save(output / "old-profile.json", old)
        refused = command(
            output,
            "old-profile-parent9-refused",
            [
                "backup",
                "--profile",
                str(output / "old-profile.json"),
                "--destination",
                str(output / "old-profile-refused-bundle"),
                "--command-id",
                "actual-parent9-old-profile-refusal",
            ],
            refused=True,
        )
        assert "Replica tables/version differ" in refused["stderr"]
        assert not (output / "old-profile-refused-bundle").exists()
    if declaration["schema"] == "roboguide.recovery-profile/v0.22":
        old = {**declaration, "schema": "roboguide.recovery-profile/v0.21"}
        save(output / "old-profile.json", old)
        refused = command(
            output,
            "old-profile-root8-refused",
            [
                "backup",
                "--profile",
                str(output / "old-profile.json"),
                "--destination",
                str(output / "old-profile-refused-bundle"),
                "--command-id",
                "actual-root8-old-profile-refusal",
            ],
            refused=True,
        )
        assert "staging Root8 requires explicit recovery v0.22" in refused["stderr"]
        assert not (output / "old-profile-refused-bundle").exists()
    # Actual inode lock must refuse before creating any new Snapshot producer or bundle.
    root_sql = (
        Path(
            next(
                value["root"]
                for value in declaration["components"]
                if value["kind"] == "replica-content"
            )
        )
        / "content.sqlite3"
    )
    with root_sql.open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command(
            output,
            "active-inode-refused",
            [
                "backup",
                "--profile",
                str(output / "profile.json"),
                "--destination",
                str(output / "refused-bundle"),
                "--command-id",
                "actual-busy",
            ],
            refused=True,
        )
    created = command(
        output,
        "backup",
        [
            "backup",
            "--profile",
            str(output / "profile.json"),
            "--destination",
            str(output / "bundle"),
            "--command-id",
            "actual-content-backup-" + operation,
        ],
    )
    checksum = created["manifest_digest"]
    return output / "bundle", checksum


def staging_history(root: Path) -> dict[str, dict[str, str]] | None:
    """Read exact original Root8 command/item rows independently of product receipt parsing."""
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        if sql.execute("PRAGMA user_version").fetchone()[0] != 8:
            return None
        result: dict[str, dict[str, str]] = {}
        for table, key in (
            ("content_staging_gc_commands", "operation_id"),
            ("content_staging_gc_items", "item_id"),
        ):
            rows = sql.execute(f"SELECT {key},record FROM {table} LIMIT 16385").fetchall()
            assert len(rows) <= 16384
            result[table] = dict(rows)
    return result


def initialization_history(directory: Path) -> dict[str, dict[str, str]] | None:
    """Compare original parent9 policy, mandatory inventory and raw member-local operation bytes."""
    with closing(
        sqlite3.connect((directory / "replica.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        if sql.execute("PRAGMA user_version").fetchone() != (9,):
            return None
        return {
            "metadata": dict(
                sql.execute(
                    "SELECT key,value FROM replica_v1_meta WHERE key IN "
                    "('content_initialization_policy','content_initialization_ids')"
                )
            ),
            "commands": dict(sql.execute("SELECT command_id,record FROM content_initializations")),
        }


def hold_initialization_sources(
    source: Path, output: Path, supervisor: Path, held: ExitStack
) -> list[dict[str, Any]]:
    """Keep original SQL/CAS leases unavailable throughout accepted cold startup and reads."""
    observations = []
    for path in sorted(source.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        original = config.get("content", {}).get("initialization_source")
        if original is None:
            continue
        sql = Path(original["source_sql"])
        paths = [
            Path(str(sql) + ".writer.lock"),
            sql,
            Path(original["source_cas"]) / ".writer.lock",
        ]
        leases = []
        for lease in paths:
            descriptor = held.enter_context(lease.open("rb"))
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            actual = os.fstat(descriptor.fileno())
            leases.append({"path": str(lease), "device": actual.st_dev, "inode": actual.st_ino})
        request = output / f"locked-original-source-{config['replica']['replica_id']}.json"
        save(request, original)
        refused = subprocess.run(
            [str(supervisor), "--inspect-initialization-source", str(request)],
            capture_output=True,
            timeout=15,
        )
        observation = {
            "leases": leases,
            "returncode": refused.returncode,
            "stdout": refused.stdout.decode(),
            "stderr": refused.stderr.decode(),
        }
        save(request.with_suffix(".refused.json"), observation)
        assert refused.returncode != 0, (
            "actual original source remained reopenable despite held leases"
        )
        assert b"original source writer is active" in refused.stderr
        observations.append(observation)
    return observations


def private_staging_refusal(output: Path, bundle: Path) -> dict[str, Any] | None:
    """Reject a fully declared and correctly hashed private payload before restore publication."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["profile"]["schema"] not in {
        "roboguide.recovery-profile/v0.22",
        "roboguide.recovery-profile/v0.23",
        "roboguide.recovery-profile/v0.24",
        "roboguide.recovery-profile/v0.25",
        "roboguide.recovery-profile/v0.26",
        "roboguide.recovery-profile/v0.27",
    }:
        return None
    root = next(
        value
        for value in manifest["profile"]["components"]
        if value.get("storage_schema") == "roboguide.replica-content-layout/v0.8"
    )
    counter = output / "private-content-counter-bundle"
    shutil.copytree(bundle, counter)
    name = "stores/" + root["component_id"] + "/staging-v1/explicit-private-contract-file"
    payload = b"actual declared private payload; no operation or physical fact"
    (counter / name).write_bytes(payload)
    manifest["files"].append(
        {
            "path": name,
            "kind": "content",
            "byte_size": len(payload),
            "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
    )
    manifest["files"].sort(key=lambda value: value["path"])
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (counter / "manifest.json").write_bytes(raw)
    checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
    for action in ("verify", "restore"):
        arguments = [action, "--bundle", str(counter), "--manifest-digest", checksum]
        if action == "restore":
            arguments += [
                "--destination",
                str(output / "private-content-refused-restore"),
                "--command-id",
                "actual-private-content-refused-restore",
            ]
        refused = command(output, "private-content-" + action + "-refused", arguments, refused=True)
        assert "private Root8 staging payload is outside recovery content" in refused["stderr"]
    assert not (output / "private-content-refused-restore").exists()
    return {
        "private_content_declared_and_hashed": True,
        "verify_refused": True,
        "restore_refused_before_publication": True,
        "manifest_digest": checksum,
    }


def initialization_inventory_refusal(output: Path, bundle: Path) -> dict[str, Any] | None:
    """Reject a correctly rehashed bundle with a missing original mandatory parent9 inventory."""
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["profile"]["schema"] != "roboguide.recovery-profile/v0.23":
        return None
    parent = next(
        value for value in manifest["profile"]["components"] if value["kind"] == "replica"
    )
    counter = output / "initialization-inventory-counter-bundle"
    shutil.copytree(bundle, counter)
    name = "stores/" + parent["component_id"] + "/" + parent["database"]
    database = counter / name
    database.chmod(0o600)
    with closing(sqlite3.connect(database)) as sql:
        assert (
            sql.execute(
                "DELETE FROM replica_v1_meta WHERE key='content_initialization_ids'"
            ).rowcount
            == 1
        )
        sql.commit()
    entry = next(value for value in manifest["files"] if value["path"] == name)
    entry["byte_size"] = database.stat().st_size
    entry["content_digest"] = "sha256:" + digest(database)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "apps/recovery-tool")
    inspection = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "import json, sys\n"
            "from roboguide_recovery.bundle import Budget, _identity\n"
            "profile = json.loads(Path(sys.argv[2]).read_text())['profile']\n"
            "print(json.dumps(_identity(Path(sys.argv[1]), Budget(profile))))\n",
            str(database),
            str(bundle / "manifest.json"),
        ],
        env=environment,
        timeout=30,
    )
    entry["sqlite"] = json.loads(inspection)
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (counter / "manifest.json").write_bytes(raw)
    checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
    destination = output / "initialization-inventory-refused-restore"
    for action in ("verify", "restore"):
        arguments = [action, "--bundle", str(counter), "--manifest-digest", checksum]
        if action == "restore":
            arguments += [
                "--destination",
                str(destination),
                "--command-id",
                "actual-missing-initialization-inventory",
            ]
        refused = command(
            output, "initialization-inventory-" + action + "-refused", arguments, refused=True
        )
        assert "parent9 original initialization policy/inventory is missing" in refused["stderr"]
    assert not destination.exists()
    return {
        "database_and_manifest_rehashed": True,
        "verify_refused": True,
        "restore_refused_before_publication": True,
        "manifest_digest": checksum,
    }


def original_read(
    request: Callable[..., tuple[int, bytes]], output: Path, label: str, origin: str, path: str
) -> tuple[int, bytes]:
    """Observe bounded readonly recovery readiness, retaining every failed real GET connection."""
    started = time.monotonic()
    deadline = started + 60
    observations = []
    while time.monotonic() < deadline:
        sample: dict[str, Any] = {"elapsed_seconds": time.monotonic() - started}
        try:
            code, raw = request(origin, path, timeout_seconds=max(0.1, deadline - time.monotonic()))
            sample["response_status"] = code
            sample["response_bytes"] = len(raw)
            sample["response_sha256"] = hashlib.sha256(raw).hexdigest()
            if code != 200:
                sample["response_body"] = raw.decode(errors="replace")[:4096]
        except (OSError, http_client.HTTPException) as error:
            code, raw = 0, b""
            sample["error_type"] = type(error).__name__
            sample["error"] = str(error)
        observations.append(sample)
        save(output / (label + ".json"), {"origin": origin, "path": path, "attempts": observations})
        if code == 200 and time.monotonic() < deadline:
            return code, raw
        if code not in (0, 503):
            raise ValueError("actual readonly recovery GET refused or exceeded its original clock")
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise ValueError("actual restored readonly readiness exhausted its original budget: " + label)


def run(
    source: Path,
    output: Path,
    supervisor: Path,
    old_supervisor: Path | None,
    cold_only: bool,
    frozen_bundle: Path | None = None,
    frozen_digest: str | None = None,
) -> dict[str, Any]:
    """Require actual source waits and complete product backup/restore before restart."""
    original = json.loads((source / "result.json").read_text())
    assert original["passed"] and all(
        value["returncode"] == 0 for value in original["actual_supervisors"]
    )
    assert (
        original["embodied_nodes"]
        == original["devices"]
        == original["models"]
        == original["simulators"]
        == 0
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")) + [
        Path(__file__),
        supervisor,
    ]
    if old_supervisor is not None:
        files.append(old_supervisor)
    if frozen_bundle is not None:
        files.append(frozen_bundle / "manifest.json")
    identities = {str(path): digest(path) for path in files}
    save(output / "source-identity.json", identities)
    operation = hashlib.sha256(str(output).encode()).hexdigest()[:16]
    bundle, checksum = prepare_bundle(source, output, operation, frozen_bundle, frozen_digest)
    command(
        output,
        "verify",
        ["verify", "--bundle", str(bundle), "--manifest-digest", checksum],
    )
    private_counter = private_staging_refusal(output, bundle)
    initialization_counter = initialization_inventory_refusal(output, bundle)
    restore_args = [
        "restore",
        "--bundle",
        str(bundle),
        "--manifest-digest",
        checksum,
        "--destination",
        str(output / "restored"),
        "--command-id",
        "actual-content-restore-" + operation,
    ]
    restored = command(output, "restore", restore_args)
    assert len(restored["replica_content_bindings"]) == len(
        original["original_complete_member_views"]
    )
    assert command(output, "restore-readonly-repeat", restore_args) == restored
    original_staging = []
    original_initializations = []
    for path in sorted(source.glob("configuration-*.json")):
        config = json.loads(path.read_text())
        member = config["replica"]["replica_id"]
        initialization = initialization_history(Path(config["storage_directory"]))
        if initialization is not None:
            assert (
                initialization_history(output / "restored/stores" / f"parent-{member}")
                == initialization
            )
            original_initializations.append(
                {"member": member, "exact_original_rows": initialization}
            )
        source_root = Path(config["content"]["root"])
        target_root = output / "restored/stores" / f"root-{member}"
        reference_root = (
            frozen_bundle / "stores" / f"root-{member}"
            if frozen_bundle is not None
            else source_root
        )
        history = staging_history(reference_root)
        if history is None:
            continue
        member = config["replica"]["replica_id"]
        assert staging_history(target_root) == history
        assert not list((target_root / "staging-v1").iterdir())
        unknown = sum(
            json.loads(raw)["outcome"] is None
            for raw in history["content_staging_gc_items"].values()
        )
        original_staging.append(
            {
                "member": member,
                "unknown": unknown,
                "source_private_files": len(list((reference_root / "staging-v1").iterdir())),
                "reference": (
                    "original SQL in verified pinned bundle"
                    if frozen_bundle is not None
                    else "original quiescent source"
                ),
                "restored_private_files": 0,
                "exact_original_rows": history,
            }
        )
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "source": str(source),
        "bundle": str(bundle),
        "manifest_digest": checksum,
        "bindings": restored["replica_content_bindings"],
        "actual_processes": [],
        "observed_leaders": [],
        "original_staging_recovery": original_staging,
        "original_initialization_recovery": original_initializations,
        "private_content_refusal": private_counter,
        "initialization_inventory_refusal": initialization_counter,
    }
    if cold_only:
        result["passed"] = True
        save(output / "result.json", result)
        return result
    with (
        tempfile.TemporaryDirectory(prefix="rg-root-restore-") as temporary,
        ExitStack() as held_sources,
    ):
        configs = assemble(output / "restored", Path(temporary), supervisor)
        result["unavailable_original_sources"] = hold_initialization_sources(
            source, output, supervisor, held_sources
        )
        deployed = output / "restored/stores/inputs/node-tls"
        transport_context = None
        if deployed.exists():
            transport_context = ssl.create_default_context(cafile=str(deployed / "ca.crt"))
            transport_context.load_cert_chain(deployed / "admin.crt", deployed / "admin.key")

        def request_http(
            origin: str, path: str, *, timeout_seconds: float = 60
        ) -> tuple[int, bytes]:
            """Use only declared restored listener TLS and retained original client input files."""
            secured = any(
                origin == config[address] and config.get(plane) is not None
                for _, config in configs.values()
                for plane, address in (
                    ("operator_tls", "http_address"),
                    ("artifact_tls", "artifact_address"),
                )
            )
            return http(
                "https://" + origin if secured else origin,
                path,
                timeout_seconds=timeout_seconds,
                tls_context=transport_context if secured else None,
            )

        first_path = configs[min(configs)][0]
        legacy = dict(configs[min(configs)][1])
        legacy["content"] = dict(legacy["content"])
        del legacy["content"]["restore_binding_digest"]
        legacy["schema"] = "roboguide.controller-replica-process/v0.2"
        legacy_path = output / "original-root-entry.json"
        save(legacy_path, legacy)
        rejected = subprocess.run(
            [str(supervisor), str(legacy_path)], capture_output=True, timeout=10
        )
        save(
            output / "original-root-entry-refused.json",
            {
                "returncode": rejected.returncode,
                "stdout": rejected.stdout.decode(),
                "stderr": rejected.stderr.decode(),
                "actual_binary_sha256": digest(supervisor),
            },
        )
        assert rejected.returncode != 0
        result["actual_old_binary_proof"] = None
        if old_supervisor is not None and digest(old_supervisor) != digest(supervisor):
            original_binaries = json.loads((source / "source-identity.json").read_text())
            assert original_binaries.get(str(old_supervisor)) == digest(old_supervisor)
            original_writer_path = first_path
            if configs[min(configs)][1].get("application_upgrade") is not None:
                original_writer = copy.deepcopy(configs[min(configs)][1])
                original_writer["schema"] = original_writer["application_upgrade"][
                    "base_process_schema"
                ]
                del original_writer["application_upgrade"]
                original_writer_path = output / "original-application-writer.json"
                save(original_writer_path, original_writer)
            rejected = subprocess.run(
                [str(old_supervisor), str(original_writer_path)], capture_output=True, timeout=10
            )
            proof = {
                "returncode": rejected.returncode,
                "stdout": rejected.stdout.decode(),
                "stderr": rejected.stderr.decode(),
                "actual_binary_sha256": digest(old_supervisor),
            }
            save(output / "old-supervisor-refused.json", proof)
            assert rejected.returncode != 0
            result["actual_old_binary_proof"] = proof
        owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []
        try:
            for member, (path, _) in configs.items():
                log = (output / f"supervisor-{member}.log").open("wb")
                try:
                    process = subprocess.Popen(
                        [str(supervisor), str(path)],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                finally:
                    log.close()
                row: dict[str, Any] = {
                    "member": member,
                    "pid": process.pid,
                    "configuration": str(path),
                }
                owned.append((process, row))
                result["actual_processes"].append(row)

            def current(expected: int | None = None) -> tuple[int, dict[str, Any]]:
                """Observe an actual live child and health, never a stale status-file ready flag."""
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    for member, (_, config) in configs.items():
                        if expected is not None and member != expected:
                            continue
                        path = Path(config["storage_directory"]) / "status.json"
                        try:
                            status = json.loads(path.read_text())
                            if (
                                status["child_pid"] is not None
                                and status["epoch"]["replica_id"] == member
                            ):
                                os.kill(status["child_pid"], 0)
                                if (
                                    request_http(
                                        config["http_address"], "/healthz", timeout_seconds=5
                                    )[0]
                                    == 200
                                ):
                                    return member, status
                        except (
                            OSError,
                            ValueError,
                            KeyError,
                            TypeError,
                            http_client.HTTPException,
                        ):
                            pass
                    time.sleep(0.1)
                raise ValueError("actual restored Controller did not become healthy")

            expected = None
            prepared = original.get("actual_prepared_recovery")
            prepared_completion = (
                None if prepared is None else prepared["recovered"]["completion"]["replacement"]
            )
            published_digest = (
                None if prepared_completion is None else prepared_completion["content_digest"]
            )
            published_size = (
                None if prepared_completion is None else prepared_completion["byte_size"]
            )
            prepared_unknown = None if prepared is None else prepared["prepared_unknown"]
            if prepared is None:
                assert "actual_generation_receipt" in original and "original_quota" in original
            for turn in range(len(configs)):
                member, status = current(expected)
                _, config = configs[member]
                if prepared is not None:
                    assert (
                        published_digest is not None
                        and published_size is not None
                        and prepared_unknown is not None
                    )
                    code, raw = original_read(
                        request_http,
                        output,
                        f"actual-content-get-{member}",
                        config["artifact_address"],
                        "/v1/artifacts/" + published_digest[7:],
                    )
                    assert code == 200 and len(raw) == published_size
                    assert "sha256:" + hashlib.sha256(raw).hexdigest() == published_digest
                    code, assessment = original_read(
                        request_http,
                        output,
                        f"actual-assessment-get-{member}",
                        config["artifact_address"],
                        "/v1/artifact-uploads/"
                        + prepared_unknown["upload"]["upload_id"]
                        + "/recoveries/"
                        + prepared_unknown["command_id"]
                        + "/assessment",
                    )
                    assert code == 200 and json.loads(assessment)["record"] == prepared_unknown
                    result["observed_leaders"].append(
                        {
                            "member": member,
                            "status": status,
                            "actual_digest": published_digest,
                            "actual_bytes": len(raw),
                        }
                    )
                else:
                    code, raw = original_read(
                        request_http,
                        output,
                        f"actual-quota-get-{member}",
                        config["http_address"],
                        "/v1/resource-quotas",
                    )
                    assert code == 200 and json.loads(raw)["registry"] == original["original_quota"]
                    database = Path(config["storage_directory"]) / "replica.sqlite3"
                    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as sql:
                        values = dict(sql.execute("SELECT key,value FROM replica_v1_meta"))
                    authority = json.loads(values["application_upgrade_authority_v1"])
                    assert original["actual_generation_receipt"] in authority["receipts"]
                    code, inventory = original_read(
                        request_http,
                        output,
                        f"actual-nodes-get-{member}",
                        config["http_address"],
                        "/v1/inventory",
                    )
                    assert code == 200 and json.loads(inventory)["nodes"] == []
                    result["observed_leaders"].append(
                        {
                            "member": member,
                            "status": status,
                            "original_generation_receipt": original["actual_generation_receipt"],
                            "original_quota": original["original_quota"],
                            "embodied_nodes": 0,
                        }
                    )
                remaining = set(configs) - {value["member"] for value in result["observed_leaders"]}
                if not remaining:
                    break
                expected = min(remaining)
                source_sql = Path(config["storage_directory"]) / "replica.sqlite3"
                with closing(sqlite3.connect(source_sql.as_uri() + "?mode=ro", uri=True)) as sql:
                    sql.execute("BEGIN")
                    floor = dict(
                        sql.execute(
                            "SELECT key,value FROM replica_v1_meta "
                            "WHERE key IN ('applied','revision','checkpoint_digest')"
                        )
                    )
                deadline = time.monotonic() + 45
                target_sql = Path(configs[expected][1]["storage_directory"]) / "replica.sqlite3"
                caught = None
                while time.monotonic() < deadline:
                    with closing(
                        sqlite3.connect(target_sql.as_uri() + "?mode=ro", uri=True)
                    ) as sql:
                        sql.execute("BEGIN")
                        facts = dict(
                            sql.execute(
                                "SELECT key,value FROM replica_v1_meta "
                                "WHERE key IN ('applied','revision','checkpoint_digest')"
                            )
                        )
                    if json.loads(facts["applied"])["index"] >= json.loads(floor["applied"])[
                        "index"
                    ] and int(facts["revision"]) >= int(floor["revision"]):
                        caught = facts
                        break
                    observed = Path(configs[expected][1]["storage_directory"]) / "status.json"
                    if observed.exists():
                        status = json.loads(observed.read_text())
                        if status.get("raft_error"):
                            save(output / f"failure-status-{expected}.json", status)
                            raise RuntimeError(
                                "actual target Raft failure: " + status["raft_error"]
                            )
                    time.sleep(0.1)
                save(
                    output / f"catchup-before-transfer-{turn}.json",
                    {"source_floor": floor, "target": expected, "target_observation": caught},
                )
                assert caught is not None, (
                    "actual original target did not reach frozen source position"
                )
                transfer = subprocess.run(
                    [
                        str(supervisor),
                        "--admin",
                        str(configs[member][0]),
                        "--leader",
                        str(member),
                        "--transfer-leader",
                        str(expected),
                        "--transfer-id",
                        f"actual-restored-transfer-{operation}-{turn}",
                    ],
                    capture_output=True,
                    timeout=100,
                )
                save(
                    output / f"transfer-{turn}.json",
                    {
                        "returncode": transfer.returncode,
                        "stdout": transfer.stdout.decode(),
                        "stderr": transfer.stderr.decode(),
                    },
                )
                assert transfer.returncode == 0
            assert len({value["member"] for value in result["observed_leaders"]}) == len(configs)
            for original_initialization in original_initializations:
                member = original_initialization["member"]
                assert (
                    initialization_history(Path(configs[member][1]["storage_directory"]))
                    == (original_initialization["exact_original_rows"])
                )
            assert all(digest(Path(path)) == checksum for path, checksum in identities.items())
            result["passed"] = True
        finally:
            for process, row in reversed(owned):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
                try:
                    row["returncode"] = process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    row["returncode"] = process.wait(timeout=10)
                    row["forced_cleanup"] = True
                if row["returncode"] != 0:
                    result["passed"] = False
            save(output / "result.json", result)
    assert result["passed"], result
    return result


def main() -> None:
    """Require explicit source evidence and frozen current/original supervisor binaries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supervisor", type=Path, required=True)
    parser.add_argument("--old-supervisor", type=Path)
    parser.add_argument("--cold-only", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--manifest-digest")
    args = parser.parse_args()
    if (args.bundle is None) != (args.manifest_digest is None):
        parser.error("--bundle and --manifest-digest must be supplied together")
    result = run(
        args.source.resolve(strict=True),
        args.output.absolute(),
        args.supervisor.resolve(strict=True),
        args.old_supervisor.resolve(strict=True) if args.old_supervisor is not None else None,
        args.cold_only,
        args.bundle.resolve(strict=True) if args.bundle is not None else None,
        args.manifest_digest,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
