"""Check actual offline CLI and Controller mTLS source receipts with zero execution workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from check_identity_processes import client, pki, port, request, stop

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "complete-sources/v0.1"


def digest(path: Path) -> str:
    """Bind evidence to actual source or executable bytes without disclosing their contents."""
    with path.open("rb") as source:
        return "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()


def descriptor(name: bytes) -> bytes:
    """Encode a real minimal proto3 descriptor containing one short empty file."""
    assert len(name) < 100
    file = b"\x0a" + bytes([len(name)]) + name + b"\x62\x06proto3"
    return b"\x0a" + bytes([len(file)]) + file


def history(path: Path) -> dict[str, list[Any]]:
    """Observe event, command and checkpoint rows through an independent readonly connection."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("events", "control_command_journal", "controller_checkpoint")
        }


def run(output: Path, node: Path, server: Path) -> dict[str, Any]:
    """Exercise owned processes and preserve receipts without creating execution workers."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
    environment["TOKIO_WORKER_THREADS"] = "1"
    config = output / "node.toml"
    plan = output / "plan.json"
    schema = output / "local.pb"
    source = (
        (ROOT / "config/node.toml").read_text().replace("compute.noop@v1", "object.relocate@v1")
    )
    source += '\n[[connections]]\ndriver="grpc"\nid="descriptor"\nlocal_system="local-runtime"\n'
    source += 'endpoint="http://127.0.0.1:8200"\ndescriptor_set="local.pb"\nreflection=false\n'
    config.write_text(source)
    schema.write_bytes(descriptor(b"original.proto"))
    document = json.loads(
        (ROOT / "scenarios/mission-front-half-v0.7/mission-plan.json").read_text()
    )
    document["tasks"][0]["roles"][0]["requirements"]["resources"] = [
        {"kind": "compute", "units": 1}
    ]
    plan.write_text(json.dumps(document))
    source_paths = [
        node,
        server,
        config,
        schema,
        plan,
        Path(__file__),
        ROOT / "core/node-service/src/preflight.rs",
        ROOT / "core/node-service/src/source_io.rs",
        ROOT / "apps/integration-server/src/application/plan_validation.rs",
    ]
    original = {str(p): digest(p) for p in source_paths}

    def cli(profile: str | None = PROFILE) -> dict[str, Any]:
        """Run the actual executable; success requires a real emitted source-bound report."""
        arguments = [str(node), "--validate", str(config), "--plan", str(plan)]
        if profile:
            arguments += ["--source-profile", profile]
        actual = subprocess.run(arguments, env=environment, capture_output=True, timeout=10)
        assert actual.returncode == 0, actual.stderr.decode()
        report = json.loads(actual.stdout)
        assert isinstance(report, dict)
        return report

    offline = cli()
    legacy = cli(None)
    assert offline["schema_version"] == "roboguide.node-plan-preflight/v0.2"
    assert legacy["schema_version"] == "roboguide.node-plan-preflight/v0.1"
    assert offline["decision"] == "not_blocked"
    assert offline["source_digest"] != legacy["source_digest"]
    certificates = output / "pki"
    leaves = pki(certificates)
    profile = output / "operator.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "roboguide.operator-authorization/v0.1",
                "registry_id": "actual-preflight",
                "revision": 1,
                "grants": {
                    "sha256:" + leaves["admin"]: {
                        "principal_id": "actual-preflight-admin",
                        "role": "recovery-admin",
                        "tenant_ids": ["*"],
                    }
                },
            }
        )
    )
    for plane in ("NODE", "OPERATOR"):
        for variable, name in (
            ("SERVER_CERT", "server.crt"),
            ("SERVER_KEY", "server.key"),
            ("CLIENT_CA", "ca.crt"),
        ):
            environment[f"ROBOGUIDE_{plane}_MTLS_{variable}"] = str(certificates / name)
    environment["ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP"] = str(profile)
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(
        {
            "schema": "roboguide.node-identity-registry/v0.1",
            "registry_id": "actual-empty",
            "records": {},
        }
    )
    environment["ROBOGUIDE_NODE_PREFLIGHT_CONFIGS"] = json.dumps([str(config)])
    environment["ROBOGUIDE_NODE_PREFLIGHT_PROFILE"] = PROFILE
    context = client(certificates, "admin")
    receipts: list[dict[str, Any]] = []
    for generation in range(3):
        if generation == 2:
            environment.pop("ROBOGUIDE_NODE_PREFLIGHT_PROFILE")
        database = output / f"controller-{generation}.sqlite3"
        http_port = port()
        origin = f"https://127.0.0.1:{http_port}"
        arguments = [
            str(server),
            f"127.0.0.1:{port()}",
            str(database),
            f"127.0.0.1:{http_port}",
            f"127.0.0.1:{port()}",
            str(output / f"artifacts-{generation}"),
        ]
        with (output / f"controller-{generation}.log").open("wb") as log:
            process = subprocess.Popen(arguments, env=environment, cwd=ROOT, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, "actual Controller exited; inspect retained log"
                try:
                    if request(origin, context, "/healthz")[0] == 200:
                        break
                except OSError:
                    pass
                assert time.monotonic() < deadline, "actual Controller readiness timeout"
                time.sleep(0.05)
            before = history(database)
            status, report = request(origin, context, "/v1/missions/validate-plan", document)
            expected = offline if generation < 2 else legacy
            assert status == 200
            assert report["schema_version"] == (
                "roboguide.plan-validation/v0.4"
                if generation < 2
                else "roboguide.plan-validation/v0.3"
            )
            assert report["node_preflight"]["scope"] == expected["scope"]
            assert report["node_preflight"]["source_digest"] == expected["source_digest"]
            assert not any(i["code"].startswith("node_preflight.") for i in report["issues"])
            schema.write_bytes(descriptor(b"changed.proto"))
            frozen = request(origin, context, "/v1/missions/validate-plan", document)[1]
            assert frozen == report, "running Controller must retain its exact startup snapshot"
            assert history(database) == before
            receipts.append({"pid": process.pid, "report": report, "history_unchanged": True})
        finally:
            stop(process)
        if generation == 0:
            changed = cli()
            assert changed["source_digest"] != offline["source_digest"]
            assert cli(None)["source_digest"] == legacy["source_digest"]
            offline = changed
    startup_refusals = []
    for name in ("unknown-profile", "complete-without-configs"):
        configured = dict(environment)
        configured["ROBOGUIDE_NODE_PREFLIGHT_PROFILE"] = (
            "complete-sources/v99" if name == "unknown-profile" else PROFILE
        )
        if name == "complete-without-configs":
            configured.pop("ROBOGUIDE_NODE_PREFLIGHT_CONFIGS")
        arguments = [
            str(server),
            f"127.0.0.1:{port()}",
            str(output / f"refused-{name}.sqlite3"),
            f"127.0.0.1:{port()}",
            f"127.0.0.1:{port()}",
            str(output / f"refused-{name}-artifacts"),
        ]
        with (output / f"refused-{name}.log").open("wb") as log:
            actual = subprocess.run(
                arguments, env=configured, cwd=ROOT, stdout=log, stderr=log, timeout=5
            )
        assert actual.returncode != 0, name
        startup_refusals.append({"case": name, "returncode": actual.returncode})
    refusals = []
    for name in (
        "unknown-profile",
        "oversized-plan",
        "fifo-config",
        "oversized-descriptor",
        "missing-descriptor",
    ):
        config.write_text(source)
        plan.write_text(json.dumps(document))
        schema.write_bytes(descriptor(b"changed.proto"))
        arguments = [str(node), "--validate", str(config), "--plan", str(plan)]
        if name == "unknown-profile":
            arguments += ["--source-profile", "complete-sources/v99"]
        elif name == "oversized-plan":
            with plan.open("wb") as file:
                file.truncate(1024 * 1024 + 1)
        elif name == "fifo-config":
            config.unlink()
            os.mkfifo(config)
        elif name == "oversized-descriptor":
            with schema.open("wb") as file:
                file.truncate(16 * 1024 * 1024 + 1)
        else:
            schema.unlink()
        started = time.monotonic()
        actual = subprocess.run(arguments, env=environment, capture_output=True, timeout=5)
        assert actual.returncode != 0 and not actual.stdout, name
        refusals.append(
            {"case": name, "returncode": actual.returncode, "seconds": time.monotonic() - started}
        )
        if name == "fifo-config":
            config.unlink()
    assert not (output / "node-state").exists()
    assert not (output / "artifact-cache").exists()
    assert digest(node) == original[str(node)] and digest(server) == original[str(server)]
    return {
        "passed": True,
        "original_sources": original,
        "controllers": receipts,
        "refusals": refusals,
        "startup_refusals": startup_refusals,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "live_node_attestation": False,
        "cross_host": False,
    }


def main() -> None:
    """Require a fresh retained evidence directory and explicit built binary paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node", type=Path, default=ROOT / "target/debug/roboguide-node")
    parser.add_argument("--server", type=Path, default=ROOT / "target/debug/integration-server")
    arguments = parser.parse_args()
    result = run(arguments.output.resolve(), arguments.node.resolve(), arguments.server.resolve())
    (arguments.output / "result.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps({"passed": result["passed"], "evidence": str(arguments.output / "result.json")})
    )


if __name__ == "__main__":
    main()
