"""Check real zero-Node Controller topology and TLS through the deployment inspector."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from check_artifact_authority_processes import grant
from check_identity_processes import client, pki
from check_identity_processes import request as https
from check_state_verifier_processes import ROOT, address, save, stop, wait_for


def pin(path: Path) -> dict[str, str]:
    """Pin one actual original public input or maintained binary without copying credentials."""
    return {"path": str(path), "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def run(directory: Path) -> dict[str, Any]:
    """Use the actual Controller composition and original TLS peers; register zero Nodes."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory = directory.resolve()
    leaves = pki(directory / "pki")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    for plane in ("NODE", "OPERATOR", "ARTIFACT"):
        for key, name in (
            ("SERVER_CERT", "server.crt"),
            ("SERVER_KEY", "server.key"),
            ("CLIENT_CA", "ca.crt"),
        ):
            environment[f"ROBOGUIDE_{plane}_MTLS_{key}"] = str(directory / "pki" / name)
    save(
        directory / "operator.json",
        {
            "schema_version": "roboguide.operator-authorization/v0.2",
            "registry_id": uuid.uuid4().hex,
            "revision": 1,
            "grants": {
                "sha256:" + leaves["viewer"]: {
                    "principal_id": "deployment-inspector",
                    "role": "viewer",
                    "tenant_ids": ["local"],
                }
            },
        },
    )
    save(
        directory / "artifact.json",
        {
            "schema_version": "roboguide.artifact-authorization/v0.1",
            "registry_id": uuid.uuid4().hex,
            "revision": 1,
            "grants": {
                "sha256:" + leaves["viewer"]: grant("deployment-inspector", "viewer", "local")
            },
        },
    )
    environment["ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP"] = str(directory / "operator.json")
    environment["ROBOGUIDE_ARTIFACT_MTLS_ROLE_MAP"] = str(directory / "artifact.json")
    node_registry = {
        "schema": "roboguide.node-identity-registry/v0.1",
        "registry_id": uuid.uuid4().hex,
        "records": {},
    }
    save(directory / "node-registry.json", node_registry)
    environment["ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS"] = json.dumps(node_registry)
    grpc, operator, artifact = (address() for _ in range(3))
    binary = ROOT / "target/debug/integration-server"
    save(
        directory / "composition.json",
        {
            "binary": pin(binary),
            "arguments": [
                grpc,
                str(directory / "controller.sqlite3"),
                operator,
                artifact,
                str(directory / "cas"),
            ],
            "explicit_environment": {
                key: val for key, val in environment.items() if key.startswith("ROBOGUIDE_")
            },
        },
    )
    tool = ROOT / "tools/deployment/deployment_profile.py"
    schema_path = ROOT / "contracts/deployment/profile-v0.1/schema.json"
    code_paths = [binary, tool, schema_path, Path(__file__)]
    original_code = {str(path): pin(path)["sha256"] for path in code_paths}
    save(directory / "source-identity.json", original_code)
    log = (directory / "controller.log").open("wb")
    controller = subprocess.Popen(
        [
            str(binary),
            grpc,
            str(directory / "controller.sqlite3"),
            operator,
            artifact,
            str(directory / "cas"),
        ],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=log,
    )
    try:
        context = client(directory / "pki", "viewer")
        origin = "https://" + operator
        wait_for(
            lambda: https(origin, context, "/healthz")[0] == 200, "actual zero-Node Controller"
        )
        status, inventory = https(origin, context, "/v1/inventory")
        assert status == 200 and inventory["nodes"] == []
        save(directory / "original-inventory.json", inventory)
        actual_schema = (
            subprocess.run(
                [str(binary), "--application-schema"], capture_output=True, check=True, timeout=10
            )
            .stdout.decode()
            .strip()
        )
        contracts = [
            {"id": name, "version": version, "file": pin(path)}
            for name, version, path in (
                ("application", actual_schema, ROOT / "core/state/src/lib.rs"),
                (
                    "node",
                    "roboguide.node-contract/v0.12",
                    ROOT / "contracts/node/v0.15/roboguide-node.proto",
                ),
                (
                    "operator",
                    "roboguide.operator-authorization/v0.2",
                    ROOT / "contracts/operator/authorization-v0.2/schema.json",
                ),
                (
                    "artifact",
                    "roboguide.artifact-authorization/v0.1",
                    ROOT / "contracts/memory/authorization-v0.1/artifact-registry.schema.json",
                ),
                (
                    "recovery",
                    "roboguide.recovery-profile/v0.4",
                    ROOT / "contracts/recovery/bundle-v0.4/profile.schema.json",
                ),
            )
        ]
        components = []
        for name, kind in (
            ("control", "controller"),
            ("artifact", "artifact"),
            ("inspector", "operator-client"),
        ):
            components.append(
                {
                    "id": name,
                    "kind": kind,
                    "process_id": "inspector" if name == "inspector" else "controller-composition",
                    "host": "actual-local-host",
                    "domain": "owned-software",
                    "tenants": ["local"],
                    "binary": None if name == "inspector" else pin(binary),
                    "inputs": [
                        pin(directory / name)
                        for name in (
                            "operator.json",
                            "artifact.json",
                            "node-registry.json",
                            "composition.json",
                        )
                    ],
                    "application_schema": actual_schema if name == "control" else None,
                }
            )
        credentials = {
            "ca": pin(directory / "pki/ca.crt"),
            "certificate": pin(directory / "pki/viewer.crt"),
            "key_file": str(directory / "pki/viewer.key"),
            "server_name": "localhost",
            "server_leaf": "sha256:" + leaves["server"],
        }
        profile: dict[str, Any] = {
            "schema": "roboguide.deployment-profile/v0.1",
            "profile_id": uuid.uuid4().hex,
            "revision": 1,
            "inspection_budget_ms": 30000,
            "mode": "single-controller",
            "local_host": "actual-local-host",
            "hosts": [
                {
                    "id": "actual-local-host",
                    "fault_domain": "actual-single-host",
                    "trust_domain": "owned-software",
                }
            ],
            "components": components,
            "stores": [
                {
                    "id": name,
                    "owner": owner,
                    "path": str(directory / path),
                    "kind": kind,
                    "writer_contract": writer,
                    "recovery_contract": "recovery",
                }
                for name, owner, path, kind, writer in (
                    ("controller-sql", "control", "controller.sqlite3", "sqlite", "application"),
                    ("cas", "artifact", "cas", "directory", "artifact"),
                )
            ],
            "links": [
                {
                    "id": name,
                    "source": "inspector",
                    "target": target,
                    "protocol": protocol,
                    "contract": contract,
                    "endpoint": endpoint,
                    "tls": credentials,
                    "probe": probe,
                }
                for name, target, protocol, contract, endpoint, probe in (
                    ("operator-health", "control", "operator-http", "operator", origin, "health"),
                    ("inventory", "control", "operator-http", "operator", origin, "inventory"),
                    (
                        "artifact-health",
                        "artifact",
                        "artifact-http",
                        "artifact",
                        "https://" + artifact,
                        "health",
                    ),
                    ("node-transport", "control", "node-grpc", "node", "tls://" + grpc, "tls"),
                )
            ],
            "contracts": contracts,
            "criteria": [
                {
                    "id": "zero-node",
                    "stage": "F0",
                    "assurance": "software",
                    "required": True,
                    "evidence": pin(directory / "original-inventory.json"),
                },
                {
                    "id": "sql-recovery",
                    "stage": "F10",
                    "assurance": "software",
                    "required": True,
                    "evidence": None,
                },
                {
                    "id": "device-proof",
                    "stage": "F7",
                    "assurance": "hardware",
                    "required": True,
                    "evidence": None,
                },
            ],
            "faults": [
                {
                    "id": "sql-failure",
                    "affected": ["control"],
                    "owner": "control",
                    "trigger": "sql-failure",
                    "rto_ms": 120000,
                    "rpo": {"amount": 0, "unit": "committed-commands"},
                    "expected_fence": "write-disabled",
                    "criteria": ["sql-recovery"],
                }
            ],
        }
        validator = importlib.import_module("jsonschema").Draft202012Validator(
            json.loads(schema_path.read_bytes())
        )
        validator.validate(profile)
        save(directory / "profile.json", profile)

        def invoke(selected: dict[str, Any], label: str, *, passed: bool) -> dict[str, Any]:
            """Call the shipped CLI separately; failed inspections must publish no receipt."""
            source = directory / (label + ".json")
            output = directory / (label + ".receipt.json")
            save(source, selected)
            command = subprocess.run(
                [
                    sys.executable,
                    str(tool),
                    "--profile",
                    str(source),
                    "--output",
                    str(output),
                    "--probe",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=40,
            )
            (directory / (label + ".stderr")).write_bytes(command.stderr)
            if passed:
                assert command.returncode == 0, command.stderr.decode()
                receipt = json.loads(output.read_bytes())
                assert isinstance(receipt, dict)
                return receipt
            assert command.returncode != 0 and not output.exists()
            return {
                "returncode": command.returncode,
                "error_sha256": pin(directory / (label + ".stderr"))["sha256"],
            }

        observed = invoke(profile, "actual", passed=True)
        assert observed["link_observations"]["inventory"]["reported_node_count"] == 0
        assert all(row["observed"] for row in observed["link_observations"].values())
        assert observed["binary_schemas"]["control"] == actual_schema
        rejected = {}
        for case in ("leaf", "configuration", "alias", "budget"):
            changed = copy.deepcopy(profile)
            if case == "leaf":
                changed["links"][0]["tls"]["server_leaf"] = "sha256:" + "0" * 64
            elif case == "configuration":
                changed["components"][0]["inputs"][0]["sha256"] = "sha256:" + "0" * 64
            elif case == "alias":
                alias = directory / "aliased-controller.sqlite3"
                os.link(directory / "controller.sqlite3", alias)
                changed["stores"].append(
                    {**changed["stores"][0], "id": "alias", "path": str(alias)}
                )
            else:
                changed["inspection_budget_ms"] = 1
            rejected[case] = invoke(changed, "rejected-" + case, passed=False)
        status, final_inventory = https(origin, context, "/v1/inventory")
        assert status == 200 and final_inventory["nodes"] == inventory["nodes"] == []
        save(directory / "final-inventory.json", final_inventory)
        assert {str(path): pin(path)["sha256"] for path in code_paths} == original_code
        result = {
            "passed": True,
            "devices": 0,
            "nodes_registered": 0,
            "actual_software_nodes": 0,
            "actual_controller_processes": 1,
            "actual_authenticated_listeners": 3,
            "actual_readonly_probes": len(observed["link_observations"]),
            "actual_application_schema": actual_schema,
            "original_code": original_code,
            "profile_digest": observed["profile_digest"],
            "refusals": rejected,
            "hardware_assurance": "pending",
            "software_complete_asserted": False,
            "rto_rpo_achievement_asserted": False,
        }
        save(directory / "RESULT.json", result)
        return result
    finally:
        stop(controller)
        log.close()
        save(
            directory / "owned-process-exits.json",
            {
                "controller_pid": controller.pid,
                "all_owned_processes_reaped": controller.poll() is not None,
            },
        )


def main() -> None:
    """Run only the explicitly requested real software configuration inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), sort_keys=True))


if __name__ == "__main__":
    main()
