"""Exercise actual spatial CPU computation through formal Node workflows, CAS and verification."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from check_spatial_local_processes import request as local_request
from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for


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


def run(python: Path, profile_file: Path, source_root: Path, output: Path) -> dict[str, Any]:
    """Start isolated maintained processes and verify restore without redispatch."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
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
    origin, cas = "http://" + http, "http://" + artifact
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
            "http://" + grpc,
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

    def start(command: list[str], name: str) -> subprocess.Popen[bytes]:
        """Pin actual binaries and track only the processes and diagnostics owned by this run."""
        for binary, checksum in binaries.items():
            assert (
                hashlib.sha256((ROOT / "target/debug" / binary).read_bytes()).hexdigest()
                == checksum
            )
        log = (output / (name + ".log")).open("xb")
        logs.append(log)
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log)
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
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller startup")
        verifier = {
            "source_id": "independent-file-reader",
            "revision": 1,
            "tenant_id": "local",
            "producer_principals": ["trusted-loopback:operator"],
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicates": ["source and CAS bundle integrity"],
            "max_age_ms": 30000,
            "stop_timeout_ms": 5000,
            "enabled": True,
            "continuous": False,
            "require_artifact": True,
        }
        status, receipt = request(
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
        node_process = start([str(ROOT / "target/debug/roboguide-node"), str(config)], "node")
        wait_for(lambda: len(request(origin, "/v1/inventory")[1]["nodes"]) == 1, "actual CPU Node")
        save(output / "mission.json", plan(mission, profile))
        status, receipt = request(origin, "/v1/missions", plan(mission, profile))
        assert status == 202, receipt
        attempts = wait_for(
            lambda: [
                entry
                for entry in request(origin, "/v1/execution-attempts")[1]["attempts"]
                if entry["task_id"] == "publish" and entry["status"] == "Completed"
            ],
            "formal Node publication after actual CPU worker exit",
            120,
        )
        assert len(attempts) == 1
        attempt = attempts[0]
        assert request(origin, f"/v1/missions/{mission}")[1]["status"] != "Completed"
        path = f"/v1/maps/{profile['map_id']}/revisions/{profile['revision_id']}"
        status, snapshot = request(cas, path)
        assert status == 200, snapshot
        save(output / "catalog-manifest.json", snapshot)
        manifest = snapshot["manifest"]
        assert manifest["producer_node_id"] == node
        assert manifest["producer_local_system_id"] == "spatial-files"
        assert manifest["source_task_ref"] == {"mission_id": mission, "task_id": "build"}
        with urllib.request.urlopen(
            cas + "/v1/artifacts/" + manifest["content_digest"], timeout=5
        ) as response:  # noqa: S310
            payload = response.read(536870913)
        (output / "downloaded.bundle").write_bytes(payload)
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
        for name in ("catalog-manifest.json", "profile.json", "source-root.txt"):
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
        wait_for(lambda: request(origin, "/healthz")[0] == 200, "Controller restored")
        start([str(ROOT / "target/debug/roboguide-node"), str(config)], "node-restored")
        wait_for(lambda: len(request(origin, "/v1/inventory")[1]["nodes"]) == 1, "Node restored")
        time.sleep(0.3)
        assert request(cas, path)[1] == snapshot
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
            "plan_digest": request(origin, f"/v1/missions/{mission}/tenant")[1][
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
        status, receipt = request(origin, "/v1/verifier-evidence", evidence)
        assert status == 200 and receipt["receipt"]["applied"] is True, receipt
        assert request(origin, "/v1/verifier-evidence", evidence)[1] == receipt
        wait_for(
            lambda: request(origin, f"/v1/missions/{mission}")[1]["status"] == "Completed",
            "independent satisfaction",
        )
        with sqlite3.connect(
            f"file:{output / 'controller.sqlite3'}?mode=ro", uri=True
        ) as connection:
            checkpoint = json.loads(
                connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[
                    0
                ]
            )
        assert not json.loads(checkpoint["integration_json"])["control"]["reservations"]
        result = {
            "passed": True,
            "devices": 0,
            "software_nodes": 1,
            "models": 0,
            "simulators": 0,
            "actual_native_workers": 1,
            "node_cas_publications": 1,
            "independent_verifier_processes": 1,
            "controller_and_node_restart_no_second_worker": True,
            "independent_corrupt_bundle_rejected": True,
            "wrong_worker_exit_identity_unavailable": True,
            "binaries": binaries,
            "configuration_digest": "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest(),
            "independent": independent,
            "verifier_receipt": receipt,
            "transport_profile": "explicit-trusted-loopback-compatibility",
            "typed_map_remote_provider_authority": "pending-versioned-contract",
            "hardware_evidence": "HardwareEvidencePending",
        }
        save(output / "result.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)
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
    arguments = parser.parse_args()
    if arguments.verify:
        print(json.dumps(verify(arguments.verify)))
    elif arguments.python and arguments.profile and arguments.source_root and arguments.output:
        print(
            json.dumps(
                run(arguments.python, arguments.profile, arguments.source_root, arguments.output)
            )
        )
    else:
        parser.error("--python, --profile, --source-root and --output are required")


if __name__ == "__main__":
    main()
