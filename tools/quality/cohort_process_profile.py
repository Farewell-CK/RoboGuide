"""Owned original CPU cohorts for coordination conformance; no devices or synthetic terminal facts.

One actual coordinator owns/reaps every actual CPU child in its declared Group. Two enrolled
software Node services may share this co-located Local EAIOS. The process-exit v0.3 proof is
published only after the original parent exits and all original child waits are retained.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_state_verifier_processes import save
from plan_revision_process_profile import configuration


def atomic(path: Path, value: Any) -> None:
    """Publish a complete original observation without exposing partial JSON."""
    temporary = path.with_name(path.name + ".pending")
    save(temporary, value)
    temporary.replace(path)


class CohortBackend(ThreadingHTTPServer):
    """Own original CPU coordinators and immutable invocation history."""

    def __init__(self, root: Path, members: int) -> None:
        """Freeze real input identities and retain bounded original process ownership."""
        self.directory = root
        self.members = members
        self.group_sizes: dict[str, int] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.cancel_receipts: dict[str, int] = {}
        self.lock = threading.RLock()
        self.original_inputs = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / "inputs").iterdir())
        }
        super().__init__(("127.0.0.1", 0), CohortHandler)

    @property
    def origin(self) -> str:
        """Expose only this owned loopback Local EAIOS process boundary."""
        return f"http://127.0.0.1:{self.server_address[1]}"

    def rows(self, group: dict[str, Any]) -> list[dict[str, Any]]:
        """Read original coordinator-owned child PIDs and actual native waits."""
        path: Path = group["root"] / "children.json"
        result: list[dict[str, Any]] = json.loads(path.read_bytes()) if path.exists() else []
        return result

    def snapshot(self) -> dict[str, Any]:
        """Retain independent SQL, HTTP, invocation and native process evidence."""
        with self.lock:
            return {
                "groups": {
                    identity: {
                        "pid": group["process"].pid,
                        "native_wait": group["process"].poll(),
                        "instance_id": group["instance_id"],
                        "members": group["members"],
                        "children": self.rows(group),
                    }
                    for identity, group in self.groups.items()
                },
                "invocations": self.jobs,
                "cancel_receipts": self.cancel_receipts,
            }

    def release(self, identity: str) -> None:
        """Permit only the exact original CPU child to read the authored immutable inputs."""
        with self.lock:
            job = self.jobs[identity]
            (self.groups[job["invocation"]["group_id"]]["root"] / (job["key"] + ".compute")).touch(
                exist_ok=False
            )

    def stop_group(self, group_id: str) -> None:
        """Request original-child draining without treating the flag as exit proof."""
        with self.lock:
            (self.groups[group_id]["root"] / "stop").touch(exist_ok=True)

    def close(self) -> None:
        """Drain owned processes before closing software stores and listeners."""
        with self.lock:
            groups = list(self.groups.values())
            for group in groups:
                (group["root"] / "stop").touch(exist_ok=True)
        for group in groups:
            group["process"].wait(timeout=10)
        self.shutdown()
        self.server_close()


class CohortHandler(BaseHTTPRequestHandler):
    """Expose configured workflows using actual files and original native waits."""

    def respond(self, value: Any, status: int = 200) -> None:
        """Frame one local observation without authorizing or retrying an effect."""
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        """Report actual integrity and complete original native waits only."""
        server: Any = self.server
        if self.path == "/health":
            self.respond({"state": "ONLINE"})
        elif self.path in {"/ready", "/capability"}:
            self.respond({"state": "READY"})
        elif self.path == "/hazard":
            current = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((server.directory / "inputs").iterdir())
            }
            self.respond({"intact": current == server.original_inputs})
        elif self.path == "/safe-state":
            observations = []
            with server.lock:
                for group_id, group in server.groups.items():
                    code = group["process"].poll()
                    rows = server.rows(group)
                    jobs = [
                        job
                        for job in server.jobs.values()
                        if job["invocation"]["group_id"] == group_id
                    ]
                    if (
                        code is None
                        or len(rows) != group["members"]
                        or len(jobs) != group["members"]
                        or any(row["native_wait"] is None for row in rows)
                    ):
                        continue
                    attempts = [
                        {
                            "execution_id": job["identity"],
                            "execution_generation": job["invocation"]["execution_generation"],
                        }
                        for job in jobs
                    ]
                    for job in jobs:
                        row = next(row for row in rows if row["identity"] == job["identity"])
                        state = (
                            "COMPLETED"
                            if row["native_wait"] == 0
                            else "FAILED"
                            if row["native_wait"] > 0
                            else "CANCELLED"
                        )
                        observations.append(
                            {
                                "execution_id": job["identity"],
                                "execution_generation": job["invocation"]["execution_generation"],
                                "command_id": None,
                                "safe_state": True,
                                "assurance": "software-process",
                                "terminal_state": state,
                                "process_exit": {
                                    "instance_id": group["instance_id"],
                                    "pid": group["process"].pid,
                                    "exit_code": code,
                                    "group_id": group_id,
                                    "attempts": attempts,
                                },
                            }
                        )
            self.respond({"observations": observations})
        else:
            self.respond({"error": "unknown fixed workflow"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """Persist invocations before spawn; Cancel acceptance supplies no exit proof."""
        server: Any = self.server
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 131072:
            self.respond({"error": "bounded invocation required"}, 400)
            return
        body = json.loads(self.rfile.read(length))
        with server.lock:
            if self.path == "/execute":
                identity, invocation = body["attempt_id"], body["invocation"]
                if identity in server.jobs:
                    self.respond({"execution_id": identity})
                    return
                group_id = invocation["group_id"]
                if group_id not in server.groups:
                    if len(server.groups) >= 8:
                        self.respond({"error": "original cohort history capacity exhausted"}, 409)
                        return
                    root = server.directory / ("group-" + uuid.uuid4().hex)
                    root.mkdir()
                    members = server.group_sizes.get(group_id, server.members)
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__)),
                            "coordinator",
                            str(server.directory),
                            str(root),
                            str(members),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    server.groups[group_id] = {
                        "root": root,
                        "members": members,
                        "process": process,
                        "instance_id": uuid.uuid4().hex,
                    }
                group = server.groups[group_id]
                if group["process"].poll() is not None:
                    self.respond({"error": "original cohort already exited"}, 409)
                    return
                key = uuid.uuid4().hex
                job = {
                    "identity": identity,
                    "invocation": invocation,
                    "key": key,
                    "output": str(group["root"] / (key + ".result.json")),
                }
                server.jobs[identity] = job
                atomic(group["root"] / (key + ".invocation.json"), job)
                atomic(server.directory / "original-invocations.json", server.jobs)
                self.respond({"execution_id": identity})
            elif self.path in {"/status", "/cancel"}:
                identity = body["execution_id"]
                job = server.jobs[identity]
                group = server.groups[job["invocation"]["group_id"]]
                if self.path == "/cancel":
                    server.cancel_receipts[identity] = server.cancel_receipts.get(identity, 0) + 1
                    atomic(server.directory / "cancel-receipts.json", server.cancel_receipts)
                row = next((row for row in server.rows(group) if row["identity"] == identity), None)
                code = None if row is None else row["native_wait"]
                state = (
                    "RUNNING"
                    if code is None
                    else "COMPLETED"
                    if code == 0 and Path(job["output"]).is_file()
                    else "CANCELLED"
                    if code < 0
                    else "FAILED"
                )
                self.respond(
                    {
                        "state": state,
                        "execution_id": identity,
                        "detail": f"original CPU native wait={code}",
                    }
                )
            else:
                self.respond({"error": "unknown fixed operation"}, 404)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep local workflow polling and full invocation content out of console output."""


def cohort_configuration(
    directory: Path, node: str, grpc: str, origin: str, state_interval_ms: int = 100
) -> str:
    """Declare actual CPU continuation, exit and file-integrity support."""
    support = {
        "schema_version": "roboguide.local-execution-recovery/v0.1",
        "operations": [
            {
                "operation": {"namespace": "compute", "name": "hash-files", "version": "v1"},
                "stop_scope": "execution-group",
                "continuation": "repeat-after-stop",
            }
        ],
    }
    text = (
        configuration(directory, node, grpc, origin)
        .replace("interval_ms = 100\n", f"interval_ms = {state_interval_ms}\n")
        .replace(
            'assurance = "software-process"',
            'assurance = "software-process"\n"roboguide.execution-recovery" = '
            + json.dumps(json.dumps(support)),
        )
    )
    return (
        text
        + f'''
[[state_exports]]
id = "file-integrity"
owner = "posix-compute"
object_class = "node"
object_type = "software-file-integrity"
object_id = "{node}"
semantic = "observed"
payload_schema = "roboguide.software-file-integrity/v0.1"
valid_for_ms = 2000
interval_ms = {state_interval_ms}
value_pointer = ""
[state_exports.step]
id = "observe-original-file-integrity"
connection = "compute-http"
[state_exports.step.operation]
kind = "http"
method = "GET"
path = "/hazard"
'''
    )


def coordinator(directory: Path, root: Path, members: int) -> None:
    """Own and reap all original CPU children before this parent may exit."""
    children: dict[str, tuple[subprocess.Popen[bytes], dict[str, Any]]] = {}
    deadline = time.monotonic() + 240
    stopped = False
    try:
        while time.monotonic() < deadline:
            for path in root.glob("*.invocation.json"):
                job = json.loads(path.read_bytes())
                if job["identity"] in children:
                    continue
                if len(children) >= members:
                    raise ValueError("original cohort exceeds configured actual worker capacity")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__)),
                        "worker",
                        str(directory),
                        str(root),
                        job["key"],
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                children[job["identity"]] = (process, job)
            stopped = (root / "stop").exists()
            if stopped:
                for process, _ in children.values():
                    if process.poll() is None:
                        process.terminate()
            rows = []
            for identity, (process, job) in children.items():
                code = process.poll()
                rows.append(
                    {
                        "identity": identity,
                        "pid": process.pid,
                        "native_wait": code,
                        "output": job["output"],
                    }
                )
            atomic(root / "children.json", rows)
            if (stopped or len(children) == members) and all(
                row["native_wait"] is not None for row in rows
            ):
                break
            time.sleep(0.02)
        else:
            raise TimeoutError("original actual CPU cohort budget expired")
    finally:
        for process, _ in children.values():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        atomic(
            root / "children.json",
            [
                {
                    "identity": identity,
                    "pid": process.pid,
                    "native_wait": process.returncode,
                    "output": job["output"],
                }
                for identity, (process, job) in children.items()
            ],
        )
    if stopped:
        os.kill(os.getpid(), signal.SIGTERM)
    if len(children) != members or any(process.returncode != 0 for process, _ in children.values()):
        raise SystemExit(8)


def worker(directory: Path, root: Path, key: str) -> None:
    """Wait for the original permit, then hash actual immutable files."""
    deadline = time.monotonic() + 230
    while not (root / (key + ".compute")).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("original software CPU read permit expired")
        time.sleep(0.02)
    job = json.loads((root / (key + ".invocation.json")).read_bytes())
    selection = job["invocation"]["parameters"].get("file", "*")
    selected = (
        sorted((directory / "inputs").iterdir())
        if selection == "*"
        else [directory / "inputs" / selection]
    )
    if (
        not selected
        or len(selected) > 32
        or any(
            path.parent != directory / "inputs" or not path.is_file() or path.is_symlink()
            for path in selected
        )
    ):
        raise ValueError("actual CPU file selector exceeds its configured root")
    results = []
    for path in selected:
        original = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(32768):
                digest.update(chunk)
        after = path.stat()
        if (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("actual source changed during original CPU computation")
        results.append({"name": path.name, "size": original.st_size, "sha256": digest.hexdigest()})
    atomic(
        root / (key + ".result.json"),
        {"schema_version": "roboguide.software-file-result/v0.1", "files": results},
    )


def verify(directory: Path, output: Path, selection: str, expected: dict[str, str]) -> None:
    """Independently recompute selected files and check their original hashes."""
    names = sorted(expected) if selection == "*" else [selection]
    rows = []
    for name in names:
        if name not in expected or Path(name).name != name:
            raise ValueError("independent verifier rejects an unconfigured source")
        path = directory / "inputs" / name
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected[name]:
            raise ValueError("independent verifier observed changed original content")
        rows.append({"name": name, "size": path.stat().st_size, "sha256": digest})
    if json.loads(output.read_bytes()) != {
        "schema_version": "roboguide.software-file-result/v0.1",
        "files": rows,
    }:
        raise ValueError("independent actual file recomputation rejected the result")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "coordinator":
        coordinator(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    elif len(sys.argv) == 5 and sys.argv[1] == "worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    elif len(sys.argv) == 6 and sys.argv[1] == "verify":
        verify(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], json.loads(sys.argv[5]))
    else:
        raise SystemExit("explicit original coordinator/worker/verifier arguments required")
