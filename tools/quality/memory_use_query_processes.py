"""Exercise original revoke recovery using real providers and deliberate transport reply loss."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, save, stop, wait_for
from memory_preparation_profile import mission, node_environment
from memory_use_profile import UnixConnection, request
from spatial_content_policy import digest


def proxy(directory: Path, upstream: str, endpoint: str) -> None:
    """Forward actual Unix HTTP; fault files only drop replies and never create backend evidence."""

    class Handler(BaseHTTPRequestHandler):
        """Retain actual request effects and drop selected original responses after forwarding."""

        def log_message(self, format: str, *args: object) -> None:
            """Keep opaque original command bodies out of incidental access logs."""

        def forward(self) -> None:
            """Call the real original backend once, then apply only declared transport faults."""
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw or b"{}")
            operation = body.get("control", {}).get("operation")
            if self.path == "/v1/memory-use/query" and (directory / "drop-queries").exists():
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            connection = UnixConnection(upstream.removeprefix("unix://"))
            try:
                connection.request(
                    self.command, self.path, raw, {"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                payload = response.read(1024 * 1024)
                status = response.status
            finally:
                connection.close()
            if self.path == "/v1/memory-use/control":
                record = {"status": status, "operation": operation, "body": body}
                with (directory / "transport.jsonl").open("a") as stream:
                    stream.write(json.dumps(record) + "\n")
                if operation == "grant" and (directory / "hold-grant").exists():
                    (directory / "grant-observed").touch()
                    until = time.monotonic() + 2
                    while (directory / "hold-grant").exists() and time.monotonic() < until:
                        time.sleep(0.01)
                if operation == "revoke" and (directory / "drop-revoke-reply").exists():
                    (directory / "revoke-committed-reply-lost").touch()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802
            """Forward only actual original-provider reads."""
            self.forward()

        def do_POST(self) -> None:  # noqa: N802
            """Forward writes once; reply loss never reissues them."""
            self.forward()

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        """Own bounded-lifetime local forwarding threads, with no authored provider state."""

        daemon_threads = True

    with Server(endpoint, Handler) as server:
        os.chmod(endpoint, 0o600)
        server.serve_forever()


@dataclass
class Setup:
    """Configure real providers with distinct schemas and immutable source files."""

    directory: Path
    manifests: list[dict[str, Any]]
    case: str = "manual"

    @classmethod
    def create(cls, directory: Path, manifest: dict[str, Any], case: str = "manual") -> Setup:
        """Freeze additional source manifests before any Node or provider starts."""
        manifests = [manifest]
        for index in (1, 2):
            value = copy.deepcopy(manifest)
            value["selector"] = {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex}
            value["provider_id"] = f"files-{index}"
            value["payload_schema"] = f"roboguide.software-source-file-{index}/v0.1"
            owned = directory / f"source-extra-{index}"
            owned.mkdir()
            filename = f"extra-{index}.bin"
            original = (
                ROOT
                / "core/domain/src"
                / ("memory_use.rs" if index == 1 else "memory_preparation.rs")
            )
            actual = directory / "source/node-state/memory/files" / filename
            shutil.copyfile(original, actual)
            value["artifact"] = {
                "content_digest": "sha256:" + hashlib.sha256(actual.read_bytes()).hexdigest(),
                "byte_size": actual.stat().st_size,
            }
            save(owned / "exports.json", [{"manifest": value, "artifact_path": filename}])
            node_source = directory / f"source/node-state/memory/files-{index}"
            node_source.mkdir(parents=True)
            shutil.copyfile(actual, node_source / filename)
            manifests.append(value)
        artifact = json.loads((directory / "artifact-authorization.json").read_bytes())
        for grant in artifact["grants"].values():
            grant["memory_ids"] = [value["selector"]["memory_id"] for value in manifests]
            if grant.get("provider_ids"):
                grant["provider_ids"] = [value["provider_id"] for value in manifests]
        save(directory / "artifact-authorization.json", artifact)
        save(directory / "query-manifests.json", manifests)
        return cls(directory, manifests, case)

    def configure(self, text: str, consumer: bool) -> str:
        """Extend deployment declarations without provider-count rules in Core."""
        if consumer:
            text = text.replace("node-config/v0.11", "node-config/v0.12")
        head, provider = text.split("[[memory_providers]]", 1)
        providers = ["[[memory_providers]]" + provider]
        for index in (1, 2):
            value = self.manifests[index]
            endpoint = self.directory / (
                f"consumer-extra-{index}/local.sock"
                if consumer
                else f"source-extra-{index}/backend/local.sock"
            )
            head += f"""\n[[connections]]
driver = "http"
id = "memory-{index}"
local_system = "posix-compute"
endpoint = "unix://{endpoint}"
timeout_ms = 3000
"""
            providers.append(
                ("[[memory_providers]]" + provider)
                .replace('"files"', json.dumps(value["provider_id"]))
                .replace('"memory-http"', f'"memory-{index}"')
                .replace('"memory/files"', f'"memory/files-{index}"')
                .replace(self.manifests[0]["payload_schema"], value["payload_schema"])
            )
        return head + "".join(providers)

    def start_extra(self, start: Any) -> None:
        """Start four actual providers and three transparent Unix proxies under the parent owner."""
        for index in (1, 2):
            for side in ("source", "consumer"):
                owned = self.directory / f"{side}-extra-{index}"
                owned.mkdir(exist_ok=True)
                command = [
                    sys.executable,
                    "-m",
                    "roboguide_memory_provider",
                    "--root",
                    str(owned / "backend"),
                    "--provider-id",
                    f"files-{index}",
                    "--input-root",
                    str(self.directory),
                    "--listen-unix",
                    "--enable-use-leases",
                ]
                if side == "source":
                    command += [
                        "--export-config",
                        str(owned / "exports.json"),
                        "--export-directory",
                        str(self.directory / "source/node-state/memory/files"),
                    ]
                start(command, f"{side}-extra-{index}-backend")
                origin = "unix://" + str(owned / "backend/local.sock")
                wait_for(
                    lambda origin=origin: request(origin, "/v1/instance")[0] == 200,
                    "actual additional backend instance",
                )
        for index in range(3):
            owned = self.directory / ("consumer" if index == 0 else f"consumer-extra-{index}")
            if index == 0:
                # The primary backend itself retains its original endpoint; proxy replaces only
                # the configured Memory connection, never operation/health routes.
                endpoint = owned / "memory-proxy.sock"
            else:
                endpoint = owned / "local.sock"
            if index == 0:
                (owned / "hold-grant").touch()
            if index == 1:
                (owned / "drop-revoke-reply").touch()
                (owned / "drop-queries").touch()
            start(
                [
                    sys.executable,
                    str(Path(__file__)),
                    "--proxy",
                    str(owned),
                    "unix://" + str(owned / "backend/local.sock"),
                    str(endpoint),
                ],
                f"consumer-{index}-transport",
            )
            wait_for(lambda endpoint=endpoint: endpoint.is_socket(), "private proxy readiness")


def run(
    setup: Setup,
    scenario: Any,
    node_processes: list[subprocess.Popen[bytes]],
    environment: dict[str, str],
) -> dict[str, Any]:
    """Cancel before consumer Execute, then recover an actual committed-but-lost revoke reply."""
    directory = setup.directory
    manifests = setup.manifests
    identifier = uuid.uuid4().hex
    accepted = mission(identifier, manifests[0])
    accepted["tasks"][1]["roles"][0]["requirements"]["memories"] = [
        {key: value[key] for key in ("selector", "kind", "payload_schema", "media_type")}
        for value in manifests
    ]
    save(directory / "mission.json", accepted)
    status, value = scenario.exchange("/v1/missions", accepted)
    assert status == 202, value
    group = scenario.get(f"/v1/missions/{identifier}")["group_id"]
    registry = scenario.get("/v1/memory-authorizations")["registry"]
    original_policy = next(iter(registry["policies"].values()))
    policies = {}
    for index, value in enumerate(manifests):
        policy = copy.deepcopy(original_policy)
        policy.update(
            policy_id=original_policy["policy_id"] if index == 0 else uuid.uuid4().hex,
            selector=value["selector"],
            manifest_digest=digest(value),
        )
        policy["groups"] = [
            {
                "group_id": group,
                "operations": ["read", "import"],
                "providers": [
                    {
                        "provider_id": None,
                        "local_system_id": None,
                        "kind": value["kind"],
                        "payload_schema": value["payload_schema"],
                        "media_type": value["media_type"],
                    }
                ],
            }
        ]
        policies[value["selector"]["memory_id"] + "/" + value["selector"]["revision_id"]] = policy
    registry.update(revision=registry["revision"] + 1, policies=policies)
    status, value = scenario.exchange(
        "/v1/memory-authorizations",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": registry["revision"] - 1,
            "registry": registry,
        },
    )
    assert status == 200, value
    source_attempt = wait_for(
        lambda: next(
            (
                row
                for row in scenario.get("/v1/execution-attempts")["attempts"]
                if row["task_id"] == "hash-0" and row["status"] == "Completed"
            ),
            None,
        ),
        "actual original source CPU computation",
    )
    save(directory / "attempt-0.json", source_attempt)
    status, computation = request(
        scenario.backends[0],
        "/v1/file-jobs/status",
        {"execution_id": source_attempt["execution_id"]},
        method="GET",
    )
    assert status == 200 and computation["state"] == "COMPLETED", computation
    save(directory / "computation-0.json", computation)
    checked = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("check_memory_preparation_processes.py")),
            "--verify",
            str(directory),
            "0",
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    independent = json.loads(checked.stdout)
    assert independent["passed"] is True
    save(directory / "independent-verifier-0.json", independent)
    evidence = {
        "evidence_id": uuid.uuid4().hex,
        "source_id": "independent-file-reader",
        "source_revision": 1,
        "tenant_id": "local",
        "task_ref": {"mission_id": identifier, "task_id": "hash-0"},
        "plan_digest": scenario.get(f"/v1/missions/{identifier}/tenant")["source_plan_digest"],
        "attempts": [
            {"role_id": source_attempt["role_id"], "attempt_id": source_attempt["execution_id"]}
        ],
        "predicate": "all file digests match",
        "verdict": "satisfied",
        "finality": "final",
        "source_observed_at_ms": time.monotonic_ns() // 1_000_000,
        "valid_for_ms": 10000,
        "sequence": 1,
    }
    status, value = scenario.exchange(
        "/v1/verifier-evidence", evidence, context=scenario.verifier_context
    )
    assert status == 200, value
    wait_for(
        lambda: (directory / "consumer/grant-observed").exists(),
        "actual provider grant committed before consumer dispatch",
    )
    status, value = scenario.exchange(f"/v1/missions/{identifier}/cancel", {})
    assert status in (200, 202), value
    (directory / "consumer/hold-grant").unlink(missing_ok=True)

    if setup.case == "automatic":
        wait_for(
            lambda: (directory / "consumer-extra-1/revoke-committed-reply-lost").exists(),
            "actual revoke reply loss",
        )
        (directory / "consumer-extra-1/drop-queries").unlink()

        def automatic_ack() -> dict[str, Any] | None:
            """Observe the original automatic result without requesting independent recovery."""
            for record in scenario.get("/v1/memory/use-leases")["records"].values():
                for delivery in record["controls"].values():
                    if delivery["command"]["operation"] == "revoke" and delivery["receipt"]:
                        assert delivery["receipt"]["status"] == "revoked", delivery
                        assert len(delivery["receipt"]["providers"]) == 3
                        return dict(delivery)
            return None

        original = wait_for(
            automatic_ack, "all original providers recover in the original automatic budget", 15
        )
        save(directory / "automatic-revoke.json", original)
        return finish(setup, scenario, identifier, original, None)

    def original_unknown() -> dict[str, Any] | None:
        """Observe ambiguity alongside independent original provider confirmations."""
        for record in scenario.get("/v1/memory/use-leases")["records"].values():
            for delivery in record["controls"].values():
                receipt = delivery["receipt"]
                if delivery["command"]["operation"] == "revoke" and receipt:
                    assert receipt["status"] == "unknown", receipt
                    assert {item["provider_id"] for item in receipt["providers"]} == {
                        "files",
                        "files-2",
                    }
                    return dict(delivery)
        return None

    original = wait_for(
        original_unknown, "partial revoke survives its original automatic budget", 30
    )
    save(directory / "original-unknown.json", original)
    assert (directory / "consumer-extra-1/revoke-committed-reply-lost").exists()
    assert scenario.get(f"/v1/missions/{identifier}")["status"] == "Cancelling"
    node_database = directory / "consumer/node-state/execution-journal.sqlite3"
    with sqlite3.connect(f"file:{node_database}?mode=ro", uri=True) as connection:
        original_budgets = connection.execute(
            "SELECT command_id,budget_json FROM memory_use_commands ORDER BY command_id"
        ).fetchall()
    stop(scenario.controller)
    scenario.start(scenario.controller_command, "controller-query-restored")
    wait_for(lambda: scenario.exchange("/healthz")[0] == 200, "actual Controller restore")
    before_received = next(
        row["received_at_ms"]
        for row in scenario.get("/v1/inventory")["nodes"]
        if row["node_id"] == scenario.nodes[1]
    )
    stop(node_processes[1])
    scenario.start(
        [str(ROOT / "target/debug/roboguide-node"), str(directory / "consumer/node.toml")],
        "consumer-query-restored",
        node_environment(environment, directory, "enrolment-next"),
    )
    wait_for(
        lambda: any(
            row["node_id"] == scenario.nodes[1]
            and row["liveness"] == "Reachable"
            and row["received_at_ms"] > before_received
            for row in scenario.get("/v1/inventory")["nodes"]
        ),
        "original Node reconnects after actual restart",
    )
    if setup.case != "unresolved":
        (directory / "consumer-extra-1/drop-queries").unlink()
    request_body = {
        "command_id": original["command"]["command_id"],
        "expected_generation": 0,
        "provider_ids": ["files", "files-1", "files-2"],
        "deadline_ms": time.time_ns() // 1_000_000
        + (2000 if setup.case == "unresolved" else 60_000),
        "query_timeout_ms": 500 if setup.case == "unresolved" else 10_000,
    }
    denied_status, denied = scenario.exchange(
        "/v1/memory/use-queries", request_body, context=scenario.verifier_context
    )
    assert denied_status == 403, denied
    save(directory / "operator-query-denied.json", denied)
    status, result = scenario.exchange("/v1/memory/use-queries", request_body)
    assert status == 202, result
    status, duplicate = scenario.exchange("/v1/memory/use-queries", request_body)
    assert status == 202, duplicate

    def observed_query() -> dict[str, Any] | None:
        """Observe a committed independent receipt rather than HTTP admission."""
        view = scenario.get("/v1/memory/use-queries")
        for row in view["records"]:
            receipt = row["intent"]["receipt"]
            if receipt is not None:
                statuses = {item["provider_id"]: item["status"] for item in receipt["observations"]}
                expected = {
                    "files": "confirmed",
                    "files-1": "unknown" if setup.case == "unresolved" else "confirmed",
                    "files-2": "confirmed",
                }
                assert statuses == expected, view
                return dict(view)
        return None

    view = wait_for(observed_query, "original independent provider observation", 15)
    save(directory / "readonly-use-queries.json", view)
    if setup.case == "unresolved":
        wait_for(
            lambda: any(
                value["observation_exhausted"]
                for value in scenario.get("/v1/memory/use-leases")["provider_progress"].values()
            ),
            "original independent session expires",
            5,
        )
        later = {**request_body, "expected_generation": 1}
        status, rejected = scenario.exchange("/v1/memory/use-queries", later)
        assert status == 409, rejected
        save(directory / "exhausted-query-rejection.json", rejected)
    current = next(iter(scenario.get("/v1/memory/use-leases")["records"].values()))
    assert current["controls"][original["command"]["command_id"]]["receipt"] == original["receipt"]
    with sqlite3.connect(f"file:{node_database}?mode=ro", uri=True) as connection:
        assert (
            connection.execute(
                "SELECT command_id,budget_json FROM memory_use_commands ORDER BY command_id"
            ).fetchall()
            == original_budgets
        )
    return finish(setup, scenario, identifier, original, view)


def verify_imports(directory: Path) -> dict[str, Any]:
    """Independently hash imported files and inspect original SQL effects."""
    manifests = json.loads((directory / "query-manifests.json").read_bytes())
    results = []
    for index, value in enumerate(manifests):
        owned = directory / ("consumer" if index == 0 else f"consumer-extra-{index}")
        content = owned / "backend/content" / value["artifact"]["content_digest"][7:]
        with content.open("rb") as stream:
            observed = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        assert observed == value["artifact"]["content_digest"]
        assert content.stat().st_size == value["artifact"]["byte_size"]
        with sqlite3.connect(
            f"file:{owned / 'backend/memory-use.sqlite3'}?mode=ro", uri=True
        ) as connection:
            effects = connection.execute(
                "SELECT receipt_json FROM commands WHERE generation=0"
            ).fetchall()
            assert len(effects) == 1
            assert json.loads(effects[0][0])["status"] == "revoked"
        traffic = [
            json.loads(line) for line in (owned / "transport.jsonl").read_text().splitlines()
        ]
        assert sum(item["operation"] == "revoke" for item in traffic) == 1
        results.append(
            {"provider_id": value["provider_id"], "actual_digest": observed, "revoke_posts": 1}
        )
    with sqlite3.connect(
        f"file:{directory / 'consumer/backend/file-jobs.sqlite3'}?mode=ro", uri=True
    ) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    return {"passed": True, "providers": results, "consumer_jobs": 0}


def finish(
    setup: Setup,
    scenario: Any,
    identifier: str,
    original: dict[str, Any],
    view: dict[str, Any] | None,
) -> dict[str, Any]:
    """Require real effects and retain ownership behind the independent stop gate."""
    directory = setup.directory
    checked = subprocess.run(
        [sys.executable, str(Path(__file__)), "--verify-imports", str(directory)],
        capture_output=True,
        check=True,
        timeout=15,
    )
    independent = json.loads(checked.stdout)
    assert independent["passed"] is True
    save(directory / "independent-provider-verifier.json", independent)
    save(directory / "preparations.json", scenario.get("/v1/memory/preparations")["records"])
    save(directory / "use-leases.json", scenario.get("/v1/memory/use-leases")["records"])
    final = scenario.get(f"/v1/missions/{identifier}")
    assert final["status"] == "Cancelling", final
    save(directory / "final-mission.json", final)
    with sqlite3.connect(
        f"file:{directory / 'controller.sqlite3'}?mode=ro", uri=True
    ) as connection:
        saved = json.loads(
            connection.execute("SELECT checkpoint_json FROM controller_checkpoint").fetchone()[0]
        )
    assert saved["safety_stops"] and all(
        row["record"]["confirmed_at"] is None for row in saved["safety_stops"].values()
    )
    integration = json.loads(saved["integration_json"])
    assert any(
        group["lifecycle"] != "Released"
        and any(task[1]["assignments"] for task in group["task_executions"])
        for group in integration["control"]["groups"]
    )
    return {
        "passed": True,
        "case": setup.case,
        "software_nodes": 2,
        "actual_receiving_providers": 3,
        "actual_imports": 3,
        "original_unknown_retained": setup.case != "automatic",
        "all_provider_revoke_posts": 1,
        "original_budgets_unchanged": True,
        "controller_restarts": int(setup.case != "automatic"),
        "node_restarts": int(setup.case != "automatic"),
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "consumer_cancelled_before_execute": True,
        "independent_verifiers": 2,
        "content_revoke_confirmed": setup.case != "unresolved",
        "independent_stop_hold_retained": True,
        "expected_unresolved_block": setup.case == "unresolved",
    }


def main() -> None:
    """Run only the explicitly selected local transparent proxy process."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", nargs=3)
    parser.add_argument("--verify-imports", type=Path)
    args = parser.parse_args()
    if args.proxy:
        proxy(Path(args.proxy[0]), args.proxy[1], args.proxy[2])
    elif args.verify_imports:
        print(json.dumps(verify_imports(args.verify_imports)))
    else:
        parser.error("--proxy or --verify-imports is required")


if __name__ == "__main__":
    main()
