"""Actual owned CPU-process backend for revision conformance, with zero devices or model calls.

The authored software profile admits one original invocation per Group. Delayed physical
termination is an explicit fault injection: accepting Cancel never fabricates a terminal fact.
Only the original Popen.poll()/wait() result and actual file output determine execution state.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ComputeHandler, ComputeServer, compute, node_config, save


class RevisionBackend(ComputeServer):
    """Retain original real workers, invocation metadata and cancellation receipts separately."""

    def __init__(self, directory: Path) -> None:
        """Own only bounded software computations launched by this checker."""
        super().__init__(directory)
        self.RequestHandlerClass = RevisionHandler
        self.instances: dict[str, str] = {}
        self.cancel_receipts: dict[str, int] = {}

    def snapshot(self) -> list[dict[str, Any]]:
        """Read actual process exits and original invocations without changing their outcomes."""
        with self.lock:
            return [
                {
                    "attempt_id": identity,
                    "pid": process.pid,
                    "exit_code": process.poll(),
                    "output": str(output),
                    "invocation": invocation,
                    "cancel_receipts": self.cancel_receipts.get(identity, 0),
                    "instance_id": self.instances[identity],
                }
                for identity, (process, output, invocation) in self.jobs.items()
            ]


class RevisionHandler(ComputeHandler):
    """Expose actual original-worker exit independently of cancellation delivery."""

    def do_GET(self) -> None:  # noqa: N802
        """Publish a software-only stop certificate only after reaping the exact original child."""
        if self.path != "/safe-state":
            super().do_GET()
            return
        server: Any = self.server
        observations = []
        for row in server.snapshot():
            if row["exit_code"] is None:
                continue
            invocation = row["invocation"]
            state = execution_state(row["exit_code"], Path(row["output"]))
            observations.append(
                {
                    "execution_id": row["attempt_id"],
                    "execution_generation": invocation["execution_generation"],
                    "command_id": None,
                    "safe_state": True,
                    "assurance": "software-process",
                    "terminal_state": state,
                    "process_exit": {
                        "instance_id": row["instance_id"],
                        "pid": row["pid"],
                        "exit_code": row["exit_code"],
                        "group_id": invocation["group_id"],
                        "attempts": [
                            {
                                "execution_id": row["attempt_id"],
                                "execution_generation": invocation["execution_generation"],
                            }
                        ],
                    },
                }
            )
        self.respond({"observations": observations})

    def do_POST(self) -> None:  # noqa: N802
        """Start bounded real workers or observe their original handles."""
        server: Any = self.server
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 65536:
            self.respond({"error": "bounded original invocation required"}, 400)
            return
        body = json.loads(self.rfile.read(length))
        with server.lock:
            if self.path == "/execute":
                identity = body["attempt_id"]
                invocation = body["invocation"]
                if identity in server.jobs:
                    self.respond({"execution_id": identity})
                    return
                if len(server.jobs) >= 8 or any(
                    previous[2]["group_id"] == invocation["group_id"] or previous[0].poll() is None
                    for previous in server.jobs.values()
                ):
                    self.respond({"error": "original software profile capacity/Group bound"}, 409)
                    return
                output = server.directory / (uuid.uuid4().hex + ".result.json")
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__)),
                        "--worker",
                        str(server.directory),
                        str(output),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                server.jobs[identity] = (process, output, invocation)
                server.instances[identity] = uuid.uuid4().hex
                server.invocations.append({"attempt_id": identity, "invocation": invocation})
                save(server.directory / "actual-invocations.json", server.invocations)
                self.respond({"execution_id": identity})
            elif self.path in {"/status", "/cancel"}:
                identity = body["execution_id"]
                process, output, _ = server.jobs[identity]
                if self.path == "/cancel":
                    server.cancel_receipts[identity] = server.cancel_receipts.get(identity, 0) + 1
                    save(server.directory / "cancel-receipts.json", server.cancel_receipts)
                code = process.poll()
                self.respond(
                    {
                        "execution_id": identity,
                        "state": execution_state(code, output),
                        "detail": f"actual original child exit={code}",
                    }
                )
            else:
                self.respond({"error": "unknown fixed software operation"}, 404)


def execution_state(exit_code: int | None, output: Path) -> str:
    """Derive local software status exclusively from the actual original OS result and output."""
    if exit_code is None:
        return "RUNNING"
    if exit_code == 0 and output.is_file():
        return "COMPLETED"
    return "CANCELLED" if exit_code < 0 else "FAILED"


def configuration(directory: Path, node: str, grpc: str, origin: str) -> str:
    """Declare the actual generic CPU workflow and explicit original-process exit contract."""
    text = node_config(directory, node, grpc, origin).replace(
        "node-config/v0.7", "node-config/v0.13"
    )
    prefix, remainder = text.split("[[state_exports]]", 1)
    text = prefix + "[[operations]]" + remainder.split("[[operations]]", 1)[1]
    support = {
        "schema": "roboguide.execution-safety/v0.1",
        "operations": [
            {
                "operation": {"namespace": "compute", "name": "hash-files", "version": "v1"},
                "safe_state_export_id": "original-worker-exit",
                "max_evidence_age_ms": 3000,
                "stop_deadline_ms": 30000,
                "min_confidence_millionths": None,
                "assurance": "software-process",
            }
        ],
    }
    text = text.replace(
        'assurance = "software-process"',
        'assurance = "software-process"\n"roboguide.execution-safety" = '
        + json.dumps(json.dumps(support)),
    )
    return (
        text
        + f'''
[[state_exports]]
id = "original-worker-exit"
owner = "posix-compute"
object_class = "node"
object_type = "original-software-process"
object_id = "{node}"
semantic = "observed"
payload_schema = "roboguide.safe-state-observation/v0.3"
valid_for_ms = 3000
interval_ms = 100
value_pointer = ""
[state_exports.step]
id = "observe-original-worker-exit"
connection = "compute-http"
[state_exports.step.operation]
kind = "http"
method = "GET"
path = "/safe-state"
'''
    )


def worker(directory: Path, output: Path) -> None:
    """Wait on the owned pipe, then hash files or exit with actual failure."""
    if sys.stdin.buffer.readline(16) != b"compute\n":
        raise SystemExit(7)
    compute(directory, output)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("one explicit original --worker input/output pair required")
    worker(Path(sys.argv[2]), Path(sys.argv[3]))
