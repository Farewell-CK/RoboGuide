"""Check the private Unix spatial service with actual files and original process exits."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import signal
import socket
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class UnixConnection(http.client.HTTPConnection):
    """Reach the same private Unix HTTP transport supported by the production Node driver."""

    def __init__(self, path: Path) -> None:
        """Keep one explicitly configured local socket with bounded request time."""
        super().__init__("localhost", timeout=10)
        self.path = path

    def connect(self) -> None:
        """Connect through the actual OS socket; no TCP proxy or synthetic server is used."""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def request(
    path: Path, route: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """Use fixed public local routes and retain actual bounded JSON outcomes."""
    connection = UnixConnection(path)
    try:
        connection.request(
            "GET" if body is None else "POST",
            route,
            body=None if body is None else json.dumps(body),
            headers={} if body is None else {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        value = json.loads(response.read(2 * 1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError("local service response is not an object")
        return response.status, value
    finally:
        connection.close()


def stop(process: subprocess.Popen[bytes], abrupt: bool = False) -> None:
    """Stop only the owned local facade, retaining original child/native outcomes for recovery."""
    if process.poll() is None:
        process.send_signal(signal.SIGKILL if abrupt else signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(python: Path, source_manifest: Path, policy_file: Path, output: Path) -> dict[str, Any]:
    """Verify original compute/cancel/restart without creating a Node, robot, simulator or model."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    descriptions = json.loads(source_manifest.read_bytes())["files"]
    source_root = Path(descriptions[0]["path"]).parent
    inputs = []
    for index, description in enumerate(descriptions[:2]):
        path = Path(description["path"])
        assert path.parent == source_root
        assert hashlib.sha256(path.read_bytes()).hexdigest() == description["sha256"]
        inputs.append(
            {
                "source_id": f"original-{index}",
                "path": path.name,
                "content_digest": "sha256:" + description["sha256"],
                "frame_id": "file-anchor" if index == 0 else "second-file-frame",
                "units": "m",
                "format": "pcd",
            }
        )
    policy = json.loads(policy_file.read_bytes())
    if "policy" in policy:
        policy = policy["policy"]
    profiles = []
    for revision in ("complete", "interrupt", "restart"):
        profiles.append(
            {
                "profile_id": "slot-" + revision,
                "map_id": "file-geometry",
                "revision_id": revision,
                "spatial_anchor_id": "file-coordinate-anchor",
                "job": {
                    "schema_version": "roboguide.spatial-file-job/v0.1",
                    "job_id": revision,
                    "frame_id": "file-anchor",
                    "inputs": inputs,
                    "policy": policy,
                },
            }
        )
    configuration = {
        "schema_version": "roboguide.spatial-local-service/v0.1",
        "max_jobs": 16,
        "max_active": 1,
        "profiles": profiles,
    }
    config_path = output / "profile.json"
    config_path.write_text(json.dumps(configuration, indent=2) + "\n")
    backend = output / "backend"
    cache = output / "node-cache"
    socket_path = backend / "local.sock"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "integrations/spatial-provider"),
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
    )
    arguments = [
        str(python),
        "-m",
        "roboguide_spatial_provider.http_service",
        "--root",
        str(backend),
        "--source-root",
        str(source_root),
        "--output-root",
        str(cache),
        "--config",
        str(config_path),
    ]
    starts: list[str] = []

    def start() -> subprocess.Popen[bytes]:
        """Start the actual immutable backend and wait for its real private socket."""
        path = output / f"service-{len(starts)}.log"
        starts.append(str(path))
        with path.open("xb") as log:
            process = subprocess.Popen(arguments, env=environment, cwd=ROOT, stdout=log, stderr=log)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"spatial facade exited; inspect {path}")
            try:
                if request(socket_path, "/v1/instance")[0] == 200:
                    assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
                    return process
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.02)
        stop(process)
        raise TimeoutError("private spatial socket did not become ready")

    def command(profile: dict[str, Any], operation: str = "build") -> dict[str, Any]:
        """Name the semantic output while deployment owns files and algorithms."""
        selector = hashlib.sha256(
            json.dumps(
                [profile["map_id"], profile["revision_id"]],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        return {
            "operation": operation + "-map",
            "attempt_id": uuid.uuid4().hex,
            "artifact_path": str(
                cache / "outputs" / selector / profile["revision_id"] / "map.bundle"
            ),
            "invocation": {
                "mission_id": "actual-file-software-check",
                "task_id": operation,
                "role_id": "file-work",
                "group_id": "file-software-check",
                "objective": "compute attributed actual point-cloud files",
                "operation": f"spatial.map.{operation}@v0",
                "parameters": {
                    "artifact_operation": "prepare-output" if operation == "build" else "publish",
                    "artifact_slot": profile["profile_id"],
                    "map_id": profile["map_id"],
                    "revision_id": profile["revision_id"],
                    "spatial_anchor_id": profile["spatial_anchor_id"],
                },
            },
        }

    def terminal(handle: str, expected: str) -> dict[str, Any]:
        """Wait only for actual original native proof and its joined-process receipt."""
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            status, value = request(socket_path, "/v1/executions/status", {"execution_id": handle})
            assert status == 200, value
            if value["state"] == expected:
                return value
            if value["state"] in {"FAILED", "CANCELLED"}:
                raise ValueError(f"unexpected original local terminal outcome: {value}")
            time.sleep(0.02)
        raise TimeoutError(f"original {handle} did not reach {expected}")

    process = start()
    try:
        original_instance = request(socket_path, "/v1/instance")[1]
        first = command(profiles[0])
        status, admitted = request(socket_path, "/v1/executions", first)
        assert status == 200 and admitted["state"] == "ACCEPTED", admitted
        complete = terminal(admitted["execution_id"], "COMPLETED")
        job_directory = Path(first["artifact_path"]).parent
        original_worker = (job_directory / "worker-started.json").read_bytes()
        assert request(socket_path, "/v1/executions", first)[1]["state"] == "COMPLETED"
        assert (job_directory / "worker-started.json").read_bytes() == original_worker
        assert (
            request(socket_path, "/v1/executions", {**first, "attempt_id": uuid.uuid4().hex})[0]
            == 409
        )
        publish = command(profiles[0], "publish")
        publication = request(socket_path, "/v1/executions", publish)
        assert publication[0] == 200 and publication[1].get("state") == "COMPLETED", publication
        interrupted = command(profiles[1])
        status, running = request(socket_path, "/v1/executions", interrupted)
        assert status == 200, running
        native = Path(interrupted["artifact_path"]).parent
        deadline = time.monotonic() + 5
        while not (native / "worker-started.json").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert (native / "worker-started.json").exists()
        accepted = request(
            socket_path, "/v1/executions/cancel", {"execution_id": running["execution_id"]}
        )
        assert accepted[0] == 200 and accepted[1]["cancel_accepted"] is True
        assert accepted[1]["state"] == "RUNNING"
        cancelled = terminal(running["execution_id"], "CANCELLED")
        stop_proof = json.loads((native / "cancelled.json").read_bytes())
        assert stop_proof["assurance"] == "software-process"
        assert not Path(f"/proc/{stop_proof['worker_pid']}").exists()
        restart = command(profiles[2])
        status, original = request(socket_path, "/v1/executions", restart)
        assert status == 200, original
        native_restart = Path(restart["artifact_path"]).parent
        deadline = time.monotonic() + 5
        while not (native_restart / "worker-started.json").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        original_identity = (native_restart / "worker-started.json").read_bytes()
        stop(process, abrupt=True)
        process = start()
        assert request(socket_path, "/v1/instance")[1] == original_instance
        restored = terminal(original["execution_id"], "COMPLETED")
        assert request(socket_path, "/v1/executions", restart)[1]["state"] == "COMPLETED"
        assert (native_restart / "worker-started.json").read_bytes() == original_identity
        return {
            "passed": True,
            "devices": 0,
            "nodes": 0,
            "models": 0,
            "simulators": 0,
            "admitted_native_attempts": 3,
            "verified_completed_computations": 2,
            "cancelled_attempts": 1,
            "completed": complete,
            "cancelled": cancelled,
            "restored": restored,
            "readonly_retry_no_second_worker": True,
            "original_backend_instance_preserved": True,
            "software_cancel_actual_process_gone": True,
            "socket_mode": "0600",
            "logs": starts,
            "hardware_evidence": "HardwareEvidencePending",
        }
    finally:
        stop(process)


def main() -> None:
    """Keep every failed/passed private process check with actual file and lifecycle evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.python, args.source_manifest, args.policy, args.output)
    except Exception as error:
        if args.output.is_dir():
            (args.output / "result.json").write_text(
                json.dumps({"passed": False, "error": str(error)[:8192]}, indent=2) + "\n"
            )
        raise
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
