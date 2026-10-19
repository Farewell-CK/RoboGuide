"""Exercise canonical HTTP admission with the deployment Python and no simulator or model."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from .adapter import HabitatLocalAdapter
from .backend import LocalExecutionOutcome
from .http_service import HabitatBridgeServer
from .model import (
    OBSERVATION_ACQUIRE_OPERATION,
    OBSERVATION_OPERATION,
    RELOCATION_OPERATION,
    SUPPORTED_OPERATIONS,
    CanonicalInvocation,
    parse_canonical_invocation,
)
from .store import ExecutionStore

OPERATIONS = (
    *SUPPORTED_OPERATIONS,
    OBSERVATION_OPERATION,
    OBSERVATION_ACQUIRE_OPERATION,
    RELOCATION_OPERATION,
)


class ProbeBackend:
    """Terminate synthetic requests without loading Habitat or returning physical success."""

    def __init__(self) -> None:
        """Count dispatched requests to detect duplicate local work."""
        self.executions = 0

    def initialize(self) -> None:
        """Require no external environment, model, credentials or device."""

    def supported_operations(self) -> tuple[str, ...]:
        """Exercise every maintained canonical parser through the real adapter."""
        return OPERATIONS

    def readiness_detail(self) -> str:
        """Identify readiness as an offline software probe only."""
        return "offline runtime compatibility probe; no physical execution"

    def execute(
        self,
        invocation: CanonicalInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Return a labelled fixture failure, never a device or benchmark outcome."""
        self.executions += 1
        running("offline runtime compatibility probe")
        return LocalExecutionOutcome(
            state="FAILED",
            detail="offline runtime compatibility probe; no physical execution",
            episode_id="runtime-check-fixture",
            scene_id="runtime-check-fixture",
            destination=invocation.destination,
            simulator_steps=0,
            initial_position=(0.0, 0.0, 0.0),
            final_position=(0.0, 0.0, 0.0),
            terminal_basis="offline-runtime-check",
        )

    def close(self) -> None:
        """Own no external process or simulator to close."""


def _request(operation: str, generation: object, *, present: bool = True) -> dict[str, object]:
    """Build generic fixture identities independent of any benchmark or robot."""
    if operation == RELOCATION_OPERATION:
        parameters = {
            "object": "object:probe",
            "source": "place:source",
            "destination": "place:goal",
        }
    elif operation in (OBSERVATION_OPERATION, OBSERVATION_ACQUIRE_OPERATION):
        parameters = {"expected": "detected(object:probe)"}
    else:
        parameters = {"destination": "place:goal"}
    invocation: dict[str, object] = {
        "mission_id": "runtime-check-fixture",
        "group_id": "runtime-check-fixture",
        "task_id": f"{operation}-{generation}-{present}",
        "role_id": "probe",
        "attempt_id": "runtime-check-fixture",
        "operation": operation,
        "objective": "Check canonical request compatibility without physical execution.",
        "parameters": parameters,
        "resource_ids": ["runtime-check-fixture"],
    }
    if present:
        invocation["execution_generation"] = generation
    return {"invocation": invocation}


def _post(base: str, route: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    """Send one bounded loopback request and retain explicit contract rejections."""
    request = Request(
        base + route,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=3.0) as response:
            status, raw = response.status, response.read(65537)
    except HTTPError as error:
        with error:
            status, raw = error.code, error.read(65537)
    if len(raw) > 65536:
        raise RuntimeError("runtime-check HTTP response exceeded its bound")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("runtime-check HTTP response is not an object")
    return status, result


def check_runtime() -> dict[str, object]:
    """Exercise parsing, durable round trips, HTTP rejection and idempotent dispatch."""
    cases: list[dict[str, object]] = []
    invalid_count = 0
    with tempfile.TemporaryDirectory(prefix="roboguide-runtime-check-") as temporary:
        store = ExecutionStore(Path(temporary) / "probe.sqlite3")
        backend = ProbeBackend()
        adapter = HabitatLocalAdapter(store, lambda: backend, initialization_timeout_s=3.0)
        try:
            server = HabitatBridgeServer(("127.0.0.1", 0), adapter)
        except BaseException:
            adapter.close()
            raise
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for operation in OPERATIONS:
                for invalid in (None, True, 0, -1, "1", 1.5, 2**64):
                    before = len(store.all_executions())
                    status, rejected = _post(base, "/v1/executions", _request(operation, invalid))
                    if status != 409 or "execution_generation" not in str(rejected.get("error")):
                        raise RuntimeError(f"{operation}: malformed generation did not fail closed")
                    if len(store.all_executions()) != before:
                        raise RuntimeError("rejected runtime-check request created an execution")
                    invalid_count += 1
                for generation in (None, 1, 2**64 - 1):
                    request = _request(operation, generation, present=generation is not None)
                    parsed = parse_canonical_invocation(request)
                    if parsed.execution_generation != generation:
                        raise RuntimeError("canonical parsing changed the execution generation")
                    status, accepted = _post(base, "/v1/executions", request)
                    if status != 200 or accepted.get("state") != "ACCEPTED":
                        raise RuntimeError(
                            f"{operation}: valid runtime-check HTTP admission failed"
                        )
                    handle = str(accepted["execution_id"])
                    deadline = time.monotonic() + 3.0
                    while True:
                        status, terminal = _post(
                            base, "/v1/executions/status", {"execution_id": handle}
                        )
                        if status != 200:
                            raise RuntimeError("runtime-check status observation failed")
                        if terminal["state"] == "FAILED":
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError("runtime-check fixture did not terminate")
                        time.sleep(0.001)
                    restored = store.get(handle)
                    if restored is None or restored["invocation"].as_dict() != parsed.as_dict():
                        raise RuntimeError("durable canonical request round trip changed identity")
                    status, duplicate = _post(base, "/v1/executions", request)
                    if status != 200 or duplicate.get("execution_id") != handle:
                        raise RuntimeError(
                            "duplicate runtime-check request changed its local handle"
                        )
                    cases.append({"operation": operation, "execution_generation": generation})
            if backend.executions != len(cases) or len(store.all_executions()) != len(cases):
                raise RuntimeError(
                    "duplicate runtime-check request dispatched additional local work"
                )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3.0)
            adapter.close()
    return {
        "schema_version": "roboguide.habitat-runtime-check/v0.1",
        "state": "PASS",
        "python_executable": sys.executable,
        "python_version": list(sys.version_info[:3]),
        "cases": cases,
        "rejected_generation_cases": invalid_count,
        "physical_episode_executed": False,
        "provider_calls": 0,
        "scope": "canonical parsing, SQLite round trip and loopback HTTP; not simulator readiness",
    }


def main() -> int:
    """Save explicit software-check evidence before any expensive deployment starts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        result = check_runtime()
    except Exception as error:
        result = {
            "schema_version": "roboguide.habitat-runtime-check/v0.1",
            "state": "FAIL",
            "python_executable": sys.executable,
            "python_version": list(sys.version_info[:3]),
            "error_type": type(error).__name__,
            "error": str(error),
            "physical_episode_executed": False,
            "provider_calls": 0,
        }
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if arguments.output is not None:
        with arguments.output.open("x", encoding="utf-8") as target:
            target.write(encoded)
    print(encoded, end="")
    return 0 if result["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
