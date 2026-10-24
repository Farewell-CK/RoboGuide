"""Exercise actual spatial CPU computation through formal Node workflows, CAS and verification."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import sqlite3
import ssl
import struct
import subprocess
import sys
import time
import tomllib
import urllib.request
import uuid
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO

from check_artifact_authority_processes import call as tls_call
from check_spatial_local_processes import request as local_request
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for
from spatial_content_policy import check as check_content_policy
from spatial_memory_consumer import SpatialMemoryConsumer, await_verifier_application
from spatial_process_tls import configure as configure_tls


def checker_identity() -> dict[str, str]:
    """Pin maintained checker modules without claiming the interpreter's full dependency closure."""
    names = (
        "check_spatial_node_processes.py",
        "check_spatial_local_processes.py",
        "check_state_verifier_processes.py",
        "check_identity_processes.py",
        "check_artifact_authority_processes.py",
        "spatial_process_tls.py",
        "spatial_content_policy.py",
        "spatial_memory_consumer.py",
        "memory_preparation_profile.py",
        "memory_use_profile.py",
        "check_memory_processes.py",
        "check_spatial_upgrade_gates.py",
        "spatial_revision_processes.py",
        "memory_revision_processes.py",
        "memory_group_lifecycle.py",
        "memory_handoff_processes.py",
    )
    result = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in names
    }
    result.update(
        {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                (ROOT / "integrations/memory-provider/roboguide_memory_provider").glob("*.py")
            )
        }
    )
    return result


def verify(directory: Path) -> dict[str, Any]:
    """Independently read CAS bytes, sources and binary tiles without provider imports."""
    envelope = json.loads((directory / "catalog-manifest.json").read_bytes())["manifest"]
    profile = json.loads((directory / "profile.json").read_bytes())["profiles"][0]
    source_root = Path((directory / "source-root.txt").read_text())
    payload = (directory / "downloaded.bundle").read_bytes()
    assert len(payload) == envelope["byte_size"]
    assert "sha256:" + hashlib.sha256(payload).hexdigest() == envelope["content_digest"]
    assert envelope["root_frame"] == profile["job"]["frame_id"]
    assert envelope["spatial_anchor"]["id"] == profile["spatial_anchor_id"]
    assert envelope["revision_id"] == profile["revision_id"]
    configuration = tomllib.loads((directory / "node.toml").read_text())
    assert configuration["schema"] == "roboguide.node-config/v0.9"
    assert envelope["schema"] == "roboguide.map-manifest/v0.2"
    assert envelope["producer_node_id"] == configuration["node_id"]
    binding = next(
        item
        for item in configuration["artifacts"]["output_bindings"]
        if item["id"] == profile["profile_id"]
    )
    declaration = next(
        item
        for item in configuration["memory_providers"]
        if item["id"] == binding["producer_provider_id"]
    )
    assert envelope["producer_provider_id"] == binding["producer_provider_id"]
    assert envelope["payload_schema"] == binding["payload_schema"] == declaration["payload_schema"]
    assert envelope["producer_local_system_id"] == declaration["owner"]
    assert envelope["media_type"] == declaration["media_type"]
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "roboguide.spatial-file-map/v0.1"
        assert manifest["frame_id"] == envelope["root_frame"]
        assert manifest["revision_id"] == envelope["revision_id"]
        assert manifest["units"] == "m" and manifest["unobserved_space"] == "unknown"
        assert manifest["current_node_localization"] is None and manifest["covariance"] is None
        assert manifest["algorithm"]["policy"] == profile["job"]["policy"]
        assert len(manifest["sources"]) == len(profile["job"]["inputs"])
        for observed, expected in zip(manifest["sources"], profile["job"]["inputs"], strict=True):
            source = (source_root / expected["path"]).read_bytes()
            assert observed["byte_size"] == len(source)
            assert observed["content_digest"] == expected["content_digest"]
            assert expected["content_digest"] == "sha256:" + hashlib.sha256(source).hexdigest()
            for key in ("source_id", "frame_id", "units", "format"):
                assert observed[key] == expected[key]
        transforms = manifest["transformations_to_anchor"]
        assert len(transforms) == len(manifest["sources"])
        for transform in transforms:
            assert len(transform) == 4 and all(len(row) == 4 for row in transform)
            assert all(math.isfinite(value) for row in transform for value in row)
            assert transform[3] == [0, 0, 0, 1]
            for row in range(3):
                for column in range(3):
                    dot = sum(transform[k][row] * transform[k][column] for k in range(3))
                    assert abs(dot - (1 if row == column else 0)) < 1e-6
        assert transforms[0] == [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        policy = profile["job"]["policy"]
        assert manifest["registration"]
        for edge in manifest["registration"]:
            assert edge["bidirectional_fitness"] >= policy["min_fitness"]
            assert edge["max_direction_rmse_m"] <= policy["max_rmse"]
            assert edge["information_is_covariance"] is False
        total = 0
        for tile in manifest["tiles"]:
            raw = archive.read(tile["path"])
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == tile["content_digest"]
            assert tile["path"] == f"tiles/{tile['content_digest'][7:]}.ply"
            header, data = raw.split(b"end_header\n", 1)
            lines = header.decode("ascii").splitlines()
            assert lines[0] == "ply" and "format binary_little_endian 1.0" in lines
            count = int(
                next(line.split()[2] for line in lines if line.startswith("element vertex "))
            )
            assert count == tile["point_count"] and count > 0
            properties = [line for line in lines if line.startswith("property ")]
            assert properties in (
                [f"property double {axis}" for axis in "xyz"],
                [f"property float {axis}" for axis in "xyz"],
            )
            code = "ddd" if properties[0].split()[1] == "double" else "fff"
            assert len(data) == struct.calcsize("<" + code) * count
            points = list(struct.iter_unpack("<" + code, data))
            assert all(math.isfinite(value) for point in points for value in point)
            for axis in range(3):
                assert (
                    abs(min(point[axis] for point in points) - tile["bounds"]["min"][axis]) < 1e-6
                )
                assert (
                    abs(max(point[axis] for point in points) - tile["bounds"]["max"][axis]) < 1e-6
                )
            total += count
        assert total == manifest["point_count"] and total > 0
        assert set(names) == {"manifest.json", *(tile["path"] for tile in manifest["tiles"])}
    return {
        "passed": True,
        "verification": "independent-standard-library-source-bundle-tile-check",
        "content_digest": envelope["content_digest"],
        "byte_size": len(payload),
        "point_count": total,
        "tiles": len(manifest["tiles"]),
        "sources": len(manifest["sources"]),
        "physical_truth": "unavailable",
    }


def plan(mission: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Require independent CAS evidence after actual build and Node-owned publication."""
    tasks = []
    for name, directive in (("build", "prepare-output"), ("publish", "publish")):
        operation = {"namespace": "spatial.map", "name": name, "version": "v0"}
        satisfaction: dict[str, Any] = {
            "expected_effect": "configured immutable file bundle was produced",
            "basis": "execution-report",
            "verifier": None,
        }
        if name == "publish":
            satisfaction = {
                "expected_effect": "CAS bundle matches actual source files and immutable tiles",
                "basis": "verifier-evidence",
                "verifier": {
                    "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                    "predicate": "source and CAS bundle integrity",
                    "max_evidence_age_ms": 30000,
                },
            }
        tasks.append(
            {
                "id": name,
                "description": "Actual configured file " + name,
                "context_id": "files",
                "depends_on": [] if name == "build" else ["build"],
                "timing": {
                    "earliest_start_offset_ms": 0,
                    "latest_start_offset_ms": None,
                    "completion_deadline_offset_ms": None,
                },
                "satisfaction": satisfaction,
                "roles": [
                    {
                        "id": "worker",
                        "context_role": "cpu",
                        "requirements": {
                            "capabilities": [{"contract": operation, "constraints": []}],
                            "resources": [{"kind": "compute", "units": 1}],
                        },
                        "execution_intent": {
                            "operation": operation,
                            "objective": "compute and retain an immutable file map",
                            "parameters": {
                                "map_id": profile["map_id"],
                                "revision_id": profile["revision_id"],
                                "artifact_slot": profile["profile_id"],
                                "spatial_anchor_id": profile["spatial_anchor_id"],
                                "artifact_operation": directive,
                            },
                        },
                        "resource_scope": "task",
                    }
                ],
            }
        )
    return {
        "schema_version": "roboguide.mission-plan/v0.8",
        "mission": {
            "id": mission,
            "objective": "independently verify actual file computation and CAS publication",
            "actors": [{"id": "file-process"}],
        },
        "contexts": [
            {
                "id": "files",
                "roles": [{"id": "cpu", "actor": "file-process"}],
                "relations": [],
                "coupling_mode": "independent",
                "executor_constraints": [],
            }
        ],
        "tasks": tasks,
    }


def run(
    python: Path,
    profile_file: Path,
    source_root: Path,
    output: Path,
    *,
    mtls: bool = False,
    memory_consumer: bool = False,
    typed_revision: str | None = None,
    old_controller: Path | None = None,
) -> dict[str, Any]:
    """Start isolated maintained processes and verify restore without redispatch."""
    if memory_consumer and not mtls:
        raise ValueError("prepared map consumption requires the actual mTLS profile")
    if typed_revision and not (memory_consumer and mtls):
        raise ValueError("typed revision requires the actual mTLS map consumer")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    original_checker = checker_identity()
    save(output / "checker-identity.json", original_checker)
    profile = json.loads(profile_file.read_bytes())["profiles"][0]
    save(
        output / "profile.json",
        {
            "schema_version": "roboguide.spatial-local-service/v0.1",
            "max_jobs": 16,
            "max_active": 1,
            "profiles": [profile],
        },
    )
    (output / "source-root.txt").write_text(str(source_root))
    grpc, http, artifact = (address() for _ in range(3))
    scheme = "https://" if mtls else "http://"
    origin, cas = scheme + http, scheme + artifact
    node, mission = "cpu-" + uuid.uuid4().hex, "file-map-" + uuid.uuid4().hex
    socket_path = output / "backend/local.sock"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment.update(
        {
            "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN": "1",
            "PYTHONPATH": str(ROOT / "integrations/spatial-provider"),
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
    )
    contexts, node_leaf = (
        configure_tls(output, node, profile["map_id"], environment) if mtls else ({}, "")
    )
    if typed_revision:
        authorization_path = output / "operator-authorization.json"
        authorization = json.loads(authorization_path.read_bytes())
        next_leaf = hashlib.sha256(
            ssl.PEM_cert_to_DER_cert((output / "pki/admin-next.crt").read_text())
        ).hexdigest()
        authorization["grants"]["sha256:" + next_leaf] = {
            "principal_id": "independent-revision-goal-producer",
            "role": "operator",
            "tenant_ids": ["local"],
        }
        save(authorization_path, authorization)
    consumer = (
        SpatialMemoryConsumer(
            output,
            python,
            grpc,
            cas,
            environment,
            profile,
            process_workers=typed_revision is not None,
        )
        if memory_consumer
        else None
    )
    node_count = 2 if consumer else 1

    def exchange(endpoint: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Use actual current client certificates for the owned HTTPS services, with no retry."""
        if not mtls:
            return request(endpoint, path, body)
        context = contexts[
            "viewer" if endpoint == cas or path == "/v1/verifier-evidence" else "admin"
        ]
        status, raw = tls_call(
            endpoint,
            context,
            "GET" if body is None else "POST",
            path,
            None if body is None else json.dumps(body).encode(),
            {
                "Content-Type": "application/json",
                **({"X-RoboGuide-Tenant": "local"} if path.startswith("/v1/missions") else {}),
            },
        )
        return status, json.loads(raw)

    config = output / "node.toml"
    subprocess.run(
        [
            str(python),
            "-m",
            "roboguide_spatial_provider.node_profile",
            "--profile",
            str(output / "profile.json"),
            "--node-id",
            node,
            "--server-endpoint",
            scheme + grpc,
            "--artifact-endpoint",
            cas,
            "--state-root",
            str(output / "node-state"),
            "--cache-root",
            str(output / "node-cache"),
            "--socket-path",
            str(socket_path),
            "--output",
            str(config),
        ],
        env=environment,
        cwd=ROOT,
        check=True,
        timeout=15,
    )
    binaries = {
        name: hashlib.sha256((ROOT / "target/debug" / name).read_bytes()).hexdigest()
        for name in ("integration-server", "roboguide-node")
    }
    processes: list[subprocess.Popen[bytes]] = []
    logs: list[BinaryIO] = []

    def start(
        command: list[str], name: str, override_environment: dict[str, str] | None = None
    ) -> subprocess.Popen[bytes]:
        """Pin actual binaries and track only the processes and diagnostics owned by this run."""
        assert checker_identity() == original_checker, "checker source changed during original run"
        for binary, checksum in binaries.items():
            assert (
                hashlib.sha256((ROOT / "target/debug" / binary).read_bytes()).hexdigest()
                == checksum
            )
        log = (output / (name + ".log")).open("xb")
        logs.append(log)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment if override_environment is None else override_environment,
            stdout=log,
            stderr=log,
        )
        processes.append(process)
        return process

    controller_command = [
        str(ROOT / "target/debug/integration-server"),
        grpc,
        str(output / "controller.sqlite3"),
        http,
        artifact,
        str(output / "cas"),
    ]
    try:
        start(
            [
                str(python),
                "-m",
                "roboguide_spatial_provider.http_service",
                "--root",
                str(output / "backend"),
                "--source-root",
                str(source_root),
                "--output-root",
                str(output / "node-cache"),
                "--config",
                str(output / "profile.json"),
            ],
            "spatial-service",
        )
        wait_for(
            lambda: local_request(socket_path, "/v1/instance")[0] == 200, "actual Local EAIOS", 20
        )
        controller = start(controller_command, "controller")
        wait_for(lambda: exchange(origin, "/healthz")[0] == 200, "Controller startup")
        if mtls:
            status, enrolled = exchange(
                origin,
                "/v1/node-identities/commands",
                {
                    "command_id": uuid.uuid4().hex,
                    "node_id": node,
                    "expected_revision": 0,
                    "action": {
                        "kind": "register",
                        "tenant_id": "local",
                        "domain_id": "software-file-domain",
                        "digest": node_leaf,
                        "expires_at_ms": 0,
                    },
                },
            )
            assert status == 200, enrolled
        verifier = {
            "source_id": "independent-file-reader",
            "revision": 1,
            "tenant_id": "local",
            "producer_principals": [
                "independent-file-reader" if mtls else "trusted-loopback:operator"
            ],
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicates": ["source and CAS bundle integrity"],
            "max_age_ms": 30000,
            "stop_timeout_ms": 5000,
            "enabled": True,
            "continuous": False,
            "require_artifact": True,
        }
        status, receipt = exchange(
            origin,
            "/v1/verifier-sources",
            {
                "command_id": uuid.uuid4().hex,
                "expected_revision": 0,
                "registry": {
                    "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                    "registry_id": "actual-spatial-file-verifier",
                    "revision": 1,
                    "sources": {"independent-file-reader": verifier},
                },
            },
        )
        assert status == 200, receipt
        if consumer:
            consumer.start(origin, exchange, start)
        node_process = start([str(ROOT / "target/debug/roboguide-node"), str(config)], "node")
        wait_for(
            lambda: len(exchange(origin, "/v1/inventory")[1]["nodes"]) == node_count,
            "actual software Nodes",
        )
        accepted = plan(mission, profile)
        if consumer:
            accepted = consumer.plan(accepted)
        save(output / "mission.json", accepted)
        status, receipt = exchange(origin, "/v1/missions", accepted)
        assert status == 202, receipt

        def completed_publication() -> list[dict[str, Any]]:
            """Return actual completed publication or immediately retain its terminal failure."""
            observed = exchange(origin, "/v1/execution-attempts")[1]["attempts"]
            terminal = [
                item
                for item in observed
                if item["task_id"] == "publish"
                and item["status"] in {"Failed", "Cancelled", "Unknown"}
            ]
            if terminal:
                save(output / "rejected-publication-attempts.json", terminal)
                raise RuntimeError("formal publication failed or became physically ambiguous")
            return [
                entry
                for entry in observed
                if entry["task_id"] == "publish" and entry["status"] == "Completed"
            ]

        attempts = wait_for(
            completed_publication,
            "formal Node publication after actual CPU worker exit",
            120,
        )
        assert len(attempts) == 1
        attempt = attempts[0]
        assert exchange(origin, f"/v1/missions/{mission}")[1]["status"] != "Completed"
        path = f"/v1/maps/{profile['map_id']}/revisions/{profile['revision_id']}"
        status, snapshot = exchange(cas, path)
        assert status == 200, snapshot
        save(output / "catalog-manifest.json", snapshot)
        manifest = snapshot["manifest"]
        assert manifest["producer_node_id"] == node
        assert manifest["producer_local_system_id"] == "spatial-files"
        assert manifest["source_task_ref"] == {"mission_id": mission, "task_id": "build"}
        with (
            closing(
                sqlite3.connect(f"file:{output / 'controller.sqlite3'}?mode=ro", uri=True)
            ) as connection,
            connection,
        ):
            original_checkpoint = json.loads(
                connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                    0
                ]
            )
        original_runtime = json.loads(original_checkpoint["integration_json"])["runtime"][
            "executions"
        ]
        declarations = original_runtime[attempt["execution_id"]]["command"].get(
            "registration_evidence"
        )
        assert declarations and declarations["contract_version"] == "roboguide.node.v0.8"
        source_declarations = original_runtime[manifest["source_execution_id"]]["command"].get(
            "registration_evidence"
        )
        assert source_declarations and source_declarations["source_session_id"]
        save(
            output / "dispatch-declarations.json",
            {"source": source_declarations, "publication": declarations},
        )
        rejected_claims = []
        if mtls:
            headers = {
                "Content-Type": "application/json",
                "X-RoboGuide-Node-Id": node,
                "X-RoboGuide-Session-Id": declarations["source_session_id"],
            }
            current_body = {
                "schema": "roboguide.map-publication/v0.1",
                "manifest": manifest,
                "execution_id": attempt["execution_id"],
            }
            for case in (
                "forged-node",
                "stale-session",
                "unregistered-provider",
                "incompatible-schema",
                "legacy-map-write",
                "viewer-write",
            ):
                body = copy.deepcopy(current_body)
                supplied_headers = headers.copy()
                if case == "forged-node":
                    supplied_headers["X-RoboGuide-Node-Id"] = "unauthorized-" + uuid.uuid4().hex
                elif case == "stale-session":
                    supplied_headers["X-RoboGuide-Session-Id"] = "expired-" + uuid.uuid4().hex
                elif case == "unregistered-provider":
                    body["manifest"]["producer_provider_id"] = "spatial-occupancy-maps"
                elif case == "incompatible-schema":
                    body["manifest"]["payload_schema"] = "unregistered-content/v1"
                elif case == "legacy-map-write":
                    body = copy.deepcopy(manifest)
                    body["schema"] = "roboguide.map-manifest/v0.1"
                    body.pop("producer_provider_id")
                    body.pop("payload_schema")
                status, rejected_body = tls_call(
                    cas,
                    contexts["viewer" if case == "viewer-write" else "enrolment-first"],
                    "POST",
                    path,
                    json.dumps(body).encode(),
                    supplied_headers,
                )
                assert status == 403, (case, status, rejected_body)
                rejected_claims.append(
                    {"case": case, "status": status, "response": json.loads(rejected_body)}
                )
            assert len(exchange(cas, "/v1/maps")[1]["revisions"]) == 1
            assert exchange(cas, path)[1] == snapshot
            save(output / "admission-rejections.json", rejected_claims)
        content_policy = (
            check_content_policy(exchange, origin, cas, contexts, manifest, output)
            if mtls
            else None
        )
        if consumer:
            consumer.authorize(origin, exchange, attempt["group_id"])
        with urllib.request.urlopen(
            cas + "/v1/artifacts/" + manifest["content_digest"],
            timeout=5,
            context=contexts.get("viewer"),
        ) as response:  # noqa: S310
            payload = response.read(536870913)
        (output / "downloaded.bundle").write_bytes(payload)
        assert checker_identity() == original_checker
        checked = subprocess.run(
            [sys.executable, str(Path(__file__)), "--verify", str(output)],
            capture_output=True,
            check=True,
            timeout=15,
        )
        independent = json.loads(checked.stdout)
        save(output / "independent-verifier.json", independent)
        assert independent["passed"] is True
        corrupt = output / "corrupt-bundle-check"
        corrupt.mkdir()
        for name in ("catalog-manifest.json", "profile.json", "source-root.txt", "node.toml"):
            (corrupt / name).write_bytes((output / name).read_bytes())
        damaged = bytearray(payload)
        damaged[len(damaged) // 2] ^= 1
        (corrupt / "downloaded.bundle").write_bytes(damaged)
        rejected = subprocess.run(
            [sys.executable, str(Path(__file__)), "--verify", str(corrupt)],
            capture_output=True,
            timeout=15,
        )
        assert rejected.returncode != 0, "independent verifier accepted corrupt CAS bytes"
        save(corrupt / "rejection.json", {"returncode": rejected.returncode})
        records_before = {
            p.name: p.read_bytes() for p in (output / "backend/records").glob("*.json")
        }
        assert len(records_before) == 2
        workers = list((output / "node-cache/outputs").glob("*/*/worker-started.json"))
        assert len(workers) == 1
        original_worker = workers[0].read_bytes()
        build_record = next(
            json.loads(value)
            for value in records_before.values()
            if json.loads(value)["request"]["operation"] == "build-map"
        )
        exit_path = workers[0].parent / "worker-exit.json"
        original_exit = exit_path.read_bytes()
        wrong_exit = json.loads(original_exit)
        wrong_exit["worker_pid"] += 1
        save(exit_path, wrong_exit)
        assert (
            local_request(
                socket_path, "/v1/executions/status", {"execution_id": build_record["handle"]}
            )[1]["state"]
            == "UNKNOWN"
        )
        exit_path.write_bytes(original_exit)
        assert (
            local_request(
                socket_path, "/v1/executions/status", {"execution_id": build_record["handle"]}
            )[1]["state"]
            == "COMPLETED"
        )
        stop(node_process)
        stop(controller)
        controller = start(controller_command, "controller-restored")
        wait_for(lambda: exchange(origin, "/healthz")[0] == 200, "Controller restored")
        start([str(ROOT / "target/debug/roboguide-node"), str(config)], "node-restored")
        wait_for(
            lambda: len(exchange(origin, "/v1/inventory")[1]["nodes"]) == node_count,
            "Nodes restored",
        )
        time.sleep(0.3)
        assert exchange(cas, path)[1] == snapshot
        assert workers[0].read_bytes() == original_worker
        assert {
            p.name: p.read_bytes() for p in (output / "backend/records").glob("*.json")
        } == records_before
        evidence = {
            "evidence_id": uuid.uuid4().hex,
            "source_id": "independent-file-reader",
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": mission, "task_id": "publish"},
            "plan_digest": exchange(origin, f"/v1/missions/{mission}/tenant")[1][
                "source_plan_digest"
            ],
            "attempts": [{"role_id": attempt["role_id"], "attempt_id": attempt["execution_id"]}],
            "predicate": "source and CAS bundle integrity",
            "verdict": "satisfied",
            "finality": "final",
            "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
            "valid_for_ms": 30000,
            "sequence": 1,
            "artifact_digest": manifest["content_digest"],
            "artifact_size_bytes": manifest["byte_size"],
        }
        status, receipt = exchange(origin, "/v1/verifier-evidence", evidence)
        assert status == 200, receipt
        applied_receipt = await_verifier_application(origin, exchange, receipt)
        replay = exchange(origin, "/v1/verifier-evidence", evidence)[1]["receipt"]
        assert replay == applied_receipt
        consumed = (
            consumer.finish(origin, exchange, mission, "independent-file-reader")
            if consumer
            else None
        )
        wait_for(
            lambda: exchange(origin, f"/v1/missions/{mission}")[1]["status"] == "Completed",
            "independent satisfaction",
        )
        revision_result = None
        if typed_revision:
            assert consumer is not None
            from spatial_revision_processes import run as run_typed_revision

            scenario, revision_result = run_typed_revision(
                consumer,
                origin,
                cas,
                exchange,
                controller,
                controller_command,
                start,
                contexts,
                node,
                typed_revision,
                old_controller,
            )
            controller = scenario.controller
        if consumer and consumed:
            memory_path = f"/v1/memories/{profile['map_id']}/revisions/{profile['revision_id']}"
            status, original_replicas = exchange(cas, memory_path)
            assert status == 200 and len(original_replicas["replicas"]) == 1
            assert original_replicas["replicas"][0]["consumer_provider_id"] == "files"
            assert original_replicas["replicas"][0]["status"] == "imported"
            received_before = {
                item["node_id"]: item["received_at_ms"]
                for item in exchange(origin, "/v1/inventory")[1]["nodes"]
            }
            stop(controller)
            controller = start(controller_command, "controller-after-consumption")
            wait_for(lambda: exchange(origin, "/healthz")[0] == 200, "typed replicas restored")
            consumed["receiver_restart"] = consumer.restart(start)
            wait_for(
                lambda: (
                    len(fresh := exchange(origin, "/v1/inventory")[1]["nodes"]) == node_count
                    and all(
                        item["liveness"] == "Reachable"
                        and item["received_at_ms"] > received_before[item["node_id"]]
                        for item in fresh
                    )
                ),
                "new registration receipts from both software Nodes after consumption restart",
            )
            assert exchange(cas, memory_path) == (200, original_replicas)
            assert workers[0].read_bytes() == original_worker
            consumed["typed_replicas_restored"] = True
            save(output / "consumer/replicas-restored.json", original_replicas)
            save(
                output / "consumer/registrations-restored.json",
                exchange(origin, "/v1/inventory")[1],
            )
        with (
            closing(
                sqlite3.connect(f"file:{output / 'controller.sqlite3'}?mode=ro", uri=True)
            ) as connection,
            connection,
        ):
            checkpoint = json.loads(
                connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                    0
                ]
            )
        remaining = json.loads(checkpoint["integration_json"])["control"]["reservations"]
        if typed_revision in {"revision-policy", "revision-expiry"}:
            assert revision_result is not None and revision_result["ownership_retained"] is True
            assert remaining
        else:
            assert not remaining
        result = {
            "passed": True,
            "devices": 0,
            "software_nodes": node_count,
            "models": 0,
            "simulators": 0,
            "actual_native_workers": 1,
            "node_cas_publications": 1,
            "independent_verifier_processes": 2 if consumer else 1,
            "prepared_map_consumer": consumed,
            "typed_map_revision": revision_result,
            "controller_and_node_restart_no_second_worker": True,
            "independent_corrupt_bundle_rejected": True,
            "wrong_worker_exit_identity_unavailable": True,
            "binaries": binaries,
            "checker_modules_sha256": original_checker,
            "configuration_digest": "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest(),
            "independent": independent,
            "rejected_remote_claims": rejected_claims,
            "full_map_content_policy": content_policy,
            "verifier_receipt": receipt,
            "applied_verifier_receipt": applied_receipt,
            "transport_profile": "actual-mutual-tls-and-enrolled-node"
            if mtls
            else "explicit-trusted-loopback-compatibility",
            "typed_map_remote_provider_authority": "checked-current-route-provider-and-attempt"
            if mtls
            else "loopback-current-route-check-only",
            "hardware_evidence": "HardwareEvidencePending",
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)
        assert all(process.returncode is not None for process in processes)
        save(
            output / "owned-process-exits.json",
            {
                "all_owned_processes_reaped": True,
                "processes": [
                    {"pid": process.pid, "returncode": process.returncode} for process in processes
                ],
            },
        )
        for log in logs:
            log.close()


def main() -> None:
    """Run maintained software conformance or the isolated, provider-independent verifier."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--mtls", action="store_true")
    parser.add_argument("--memory-consumer", action="store_true")
    parser.add_argument(
        "--typed-revision",
        choices=(
            "revision",
            "revision-sql",
            "revision-restart",
            "revision-policy",
            "revision-expiry",
        ),
    )
    parser.add_argument("--old-controller", type=Path)
    arguments = parser.parse_args()
    if arguments.verify:
        print(json.dumps(verify(arguments.verify)))
    elif arguments.python and arguments.profile and arguments.source_root and arguments.output:
        if arguments.output.exists():
            parser.error("output directory must be new")
        try:
            result = run(
                arguments.python,
                arguments.profile,
                arguments.source_root,
                arguments.output,
                mtls=arguments.mtls,
                memory_consumer=arguments.memory_consumer,
                typed_revision=arguments.typed_revision,
                old_controller=arguments.old_controller,
            )
        except Exception as error:
            if arguments.output.is_dir():
                save(
                    arguments.output / "failure.json",
                    {"passed": False, "reason": str(error)[:4096]},
                )
            raise
        print(json.dumps(result))
    else:
        parser.error("--python, --profile, --source-root and --output are required")


if __name__ == "__main__":
    main()
