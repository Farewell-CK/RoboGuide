"""Exercise durable real cross-domain Control/TLS/SQL/CPU coordination without devices or models."""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import subprocess
import threading
import uuid
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from check_execution_coordination_processes import cold as recover
from check_execution_coordination_processes import complete
from check_identity_processes import request
from check_plan_revision_processes import checkpoint
from check_state_verifier_processes import ROOT, save, stop, wait_for
from coordination_processes import Case, semantic_plan

SCHEMA = "roboguide.federation-execution/v0.1"


class FederatedCase(Case):
    """Keep every actual cross-domain request within one explicit immutable tenant."""

    def exchange(
        self, path: str, body: dict[str, Any] | None = None, who: str = "admin"
    ) -> dict[str, Any]:
        """Send once with the actual TLS principal and exact tenant selector."""
        status, value = request(
            self.origin,
            self.contexts[who],
            path,
            body,
            {"X-RoboGuide-Tenant": "local"} if path.startswith("/v1/federation/") else {},
        )
        if not 200 <= status < 300:
            raise ValueError(f"actual {path}: HTTP {status}: {value}")
        return value


class ReplyLoss(ThreadingHTTPServer):
    """Own an explicit actual TLS relay that loses one real committed Prepare response."""

    def __init__(
        self,
        participant: Case,
        coordinator: Case,
        lose_reply: bool = False,
        interrupt: bool = False,
    ) -> None:
        """Use the actual declared software peer identity and preserve every forwarded call."""
        super().__init__(("127.0.0.1", 0), Relay)
        self.participant = participant
        self.context = ssl.create_default_context(cafile=str(participant.certificates / "ca.crt"))
        self.context.load_cert_chain(
            coordinator.certificates / "admin.crt", coordinator.certificates / "admin.key"
        )
        self.records: list[dict[str, Any]] = []
        self.lost = False
        self.lose_reply = lose_reply
        self.interrupt = interrupt
        self.prepare_held = threading.Event()
        self.commit_held = threading.Event()
        self.release_reply = threading.Event()
        self.partition = threading.Event()
        self.request_timeout_seconds = 3.0
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            participant.certificates / "server.crt", participant.certificates / "server.key"
        )
        context.load_verify_locations(str(coordinator.certificates / "ca.crt"))
        context.verify_mode = ssl.CERT_REQUIRED
        self.socket = context.wrap_socket(self.socket, server_side=True)

    def replace_tls(self, context: ssl.SSLContext) -> ssl.SSLContext:
        """Change actual future TLS handshakes and retain the original listener context."""
        socket = cast(ssl.SSLSocket, self.socket)
        previous = socket.context
        socket.context = context
        return previous


class Relay(BaseHTTPRequestHandler):
    """Forward once to the real participant, then deliberately close one response connection."""

    def log_message(self, *_arguments: Any) -> None:
        """Keep bounded exact request records instead of noisy unauthenticated access logs."""

    def forward(self, body: dict[str, Any] | None) -> None:
        """Keep the actual backend response and lose only the first successful Prepare reply."""
        relay: ReplyLoss = self.server  # type: ignore[assignment]
        if relay.partition.is_set():
            relay.records.append({"path": self.path, "forwarded": False, "actual_closed": True})
            self.close_connection = True
            return
        status, result = request(
            relay.participant.origin,
            relay.context,
            self.path,
            body,
            {"X-RoboGuide-Tenant": "local"},
            timeout_seconds=relay.request_timeout_seconds,
        )
        relay.records.append(
            {"path": self.path, "status": status, "body": body, "response": result}
        )
        save(relay.participant.root / "actual-relay.json", relay.records)
        if self.path.endswith("/prepare") and status == 202 and relay.lose_reply and not relay.lost:
            relay.lost = True
            self.close_connection = True
            return
        if status == 202 and (
            (relay.interrupt and self.path.endswith("/prepare"))
            or (not relay.interrupt and self.path.endswith("/commit"))
        ):
            (relay.prepare_held if relay.interrupt else relay.commit_held).set()
            if not relay.release_reply.wait(timeout=12):
                self.close_connection = True
                return
        if relay.partition.is_set():
            self.close_connection = True
            return
        encoded = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        """Pass one exact authorized original command; never retry backend mutations."""
        self.forward(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

    def do_GET(self) -> None:  # noqa: N802
        """Pass only the coordinator's independent readonly observation."""
        self.forward(None)


def registry(case: Case) -> None:
    """Enroll the actual independent verifier source without submitting any local Mission."""
    source = {
        "source_id": "original-cpu-files",
        "revision": 1,
        "tenant_id": "local",
        "producer_principals": ["independent-goal-producer"],
        "verifier": {"namespace": "observation", "name": "verify-original-files", "version": "v1"},
        "predicates": ["all original file digests independently match"],
        "max_age_ms": 10000,
        "enabled": True,
        "continuous": False,
        "stop_timeout_ms": 30000,
        "require_artifact": False,
    }
    case.exchange(
        "/v1/verifier-sources",
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                "registry_id": uuid.uuid4().hex,
                "revision": 1,
                "sources": {source["source_id"]: source},
            },
        },
    )


def configure(
    cases: Sequence[Case],
    lost_reply: bool,
    interrupt: bool = False,
    *,
    request_budget_ms: int = 3000,
    transaction_budget_ms: int = 120000,
    max_clock_uncertainty_ms: int = 1500,
) -> tuple[dict[str, Any], ReplyLoss | None]:
    """Bind declared domains to actual independent endpoints and TLS credentials."""
    assert 100 <= request_budget_ms <= 10000
    assert 1000 <= transaction_budget_ms <= 300000
    assert 1 <= max_clock_uncertainty_ms <= 10000
    assert max_clock_uncertainty_ms < transaction_budget_ms
    coordinator, participant = cases
    domains = ["domain-" + uuid.uuid4().hex[:10] for _ in cases]
    trust = participant.root / "federation-client-ca.crt"
    trust.write_bytes(
        (participant.certificates / "ca.crt").read_bytes()
        + (coordinator.certificates / "ca.crt").read_bytes()
    )
    participant.environment["ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA"] = str(trust)
    grants = json.loads(participant.authorization.read_text())
    grants["grants"]["sha256:" + coordinator.leaves["admin"]] = {
        "principal_id": "cohort-administrator",
        "role": "recovery-admin",
        "tenant_ids": ["*"],
    }
    save(participant.authorization, grants)
    for domain, case in zip(domains, cases, strict=True):
        policy = {
            "schema_version": "roboguide.federation-authorization/v0.2",
            "registry_id": uuid.uuid4().hex,
            "revision": 1,
            "local_domain": domain,
            "domain_epochs": dict.fromkeys(domains, 1),
            "grants": {
                "sha256:" + leaf: {
                    "principal_id": "cohort-administrator",
                    "tenant_ids": ["local"],
                    "domain_ids": domains,
                    "revoked": False,
                    "execution_coordinator": True,
                }
                for leaf in {case.leaves["admin"], coordinator.leaves["admin"]}
            },
        }
        path = case.root / "federation-policy.json"
        save(path, policy)
        case.environment.update(
            {
                "ROBOGUIDE_CONTROL_DOMAIN": domain,
                "ROBOGUIDE_FEDERATION_DOMAIN_GRANTS": str(path),
            }
        )
    relay = ReplyLoss(participant, coordinator, lost_reply, interrupt)
    relay.request_timeout_seconds = request_budget_ms / 1000
    if relay is not None:
        threading.Thread(target=relay.serve_forever, daemon=True).start()
    profile = {
        "schema_version": "roboguide.federation-peers/v0.1",
        "profile_id": uuid.uuid4().hex,
        "revision": 1,
        "local_domain": domains[0],
        "max_drift_ppm": 100,
        "request_budget_ms": request_budget_ms,
        "peers": {
            domain: {
                "address": f"127.0.0.1:{relay.server_address[1]}" if index and relay else case.http,
                "server_name": "localhost",
                "server_leaf": "sha256:" + case.leaves["server"],
                "ca_file": str(case.certificates / "ca.crt"),
                "ca_digest": "sha256:"
                + hashlib.sha256((case.certificates / "ca.crt").read_bytes()).hexdigest(),
                "client_cert": str(coordinator.certificates / "admin.crt"),
                "client_leaf": "sha256:" + coordinator.leaves["admin"],
                "client_key": str(coordinator.certificates / "admin.key"),
                "principal_id": "cohort-administrator",
            }
            for index, (domain, case) in enumerate(zip(domains, cases, strict=True))
        },
    }
    path = coordinator.root / "federation-peers.json"
    save(path, profile)
    coordinator.environment["ROBOGUIDE_FEDERATION_PEERS"] = str(path)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA,
        "transaction_id": uuid.uuid4().hex,
        "coordinator_domain": domains[0],
        "tenant_id": "local",
        "objective": "independently verify original files in every declared Control domain",
        "budget_ms": transaction_budget_ms,
        "max_clock_uncertainty_ms": max_clock_uncertainty_ms,
        "participants": {},
    }
    for index, (domain, _case) in enumerate(zip(domains, cases, strict=True)):
        plan = semantic_plan("federated-cpu-" + uuid.uuid4().hex[:8])
        if index == 0:
            plan["mission"]["objective"] = manifest["objective"]
        manifest["participants"][domain] = {
            "plan": plan,
            "goal_verifier": {
                "verifier": {
                    "namespace": "observation",
                    "name": "verify-original-files",
                    "version": "v1",
                },
                "predicate": "all original file digests independently match",
                "max_evidence_age_ms": 10000,
            },
        }
    save(coordinator.root / "original-global-manifest.json", manifest)
    return manifest, relay


def run(
    root: Path, lost_reply: bool, previous: Path | None, interrupt: bool = False
) -> dict[str, Any]:
    """Verify actual two-domain resources, decisions, CPU work and independent goals."""
    cases: list[Case] = [FederatedCase(root / name, 1, 1) for name in ("root", "peer")]
    manifest, relay = configure(cases, lost_reply, interrupt)
    result: dict[str, Any] = {}
    try:
        for case in reversed(cases):
            case.boot()
            registry(case)
        root_case = cases[0]
        path = "/v1/federation/transactions/" + manifest["transaction_id"]
        original = root_case.exchange("/v1/federation/transactions", manifest)
        assert not original["global_completed"] and not original["global_dispatch_ready"]
        assert relay is not None
        if interrupt:
            wait_for(
                relay.prepare_held.is_set,
                "actual committed remote preparation with missing original reply",
                45,
            )
            before_kill = checkpoint(root_case.root)["federation"]
            assert all(not case.backend.jobs for case in cases)
            root_case.controller.kill()
            actual_wait = root_case.controller.wait(timeout=5)
            assert actual_wait == -9
            relay.release_reply.set()
            root_case.controller = root_case.start(
                root_case.arguments, "interrupted-original-restart"
            )
            wait_for(
                lambda: root_case.exchange("/healthz"),
                "original root restart after real native SIGKILL",
            )
            wait_for(
                lambda: len(root_case.exchange(path)["stopped"]) == 2,
                "original distributed pre-Commit abort and resource release",
                45,
            )
            progress = root_case.exchange(path)
            assert progress["decision"] is None and not progress["global_completed"]
            after_kill = checkpoint(root_case.root)["federation"]
            original_record = before_kill["coordinations"]["transactions"][
                manifest["transaction_id"]
            ]
            restored_record = after_kill["coordinations"]["transactions"][
                manifest["transaction_id"]
            ]
            assert (
                original_record["budget"] == restored_record["budget"]
                and original_record["nonces"] == restored_record["nonces"]
            )
            assert all(not case.backend.jobs for case in cases)
            result = {
                "passed": True,
                "original_root_wait": actual_wait,
                "original_budget_nonce_retained": True,
                "original_global_commit": "not_decided",
                "stopped_domains": progress["stopped"],
                "cpu_children": 0,
                "software_nodes": 2,
                "devices": 0,
                "external_models": 0,
            }
        else:
            wait_for(
                relay.commit_held.is_set,
                "actual local Commit with missing complete coordinator proof",
                45,
            )
            assert all(not case.backend.jobs for case in cases)
            held = root_case.exchange(path)
            assert (
                held["decision"] is not None
                and len(held["commits"]) < 2
                and not held["global_completed"]
            )
            relay.release_reply.set()
            wait_for(
                lambda: len(root_case.exchange(path)["released"]) == len(cases),
                "complete original distributed Commit and release",
                60,
            )
            progress = root_case.exchange(path)
            assert (
                progress["decision"]
                and len(progress["commits"]) == 2
                and len(progress["clock_translations"]) == 2
            )
            assert all(
                proof["epoch"].startswith("standalone:") for proof in progress["prepared"].values()
            )
            for case, domain in zip(cases, manifest["participants"], strict=True):
                prepared = progress["prepared"][domain]
                assert (
                    prepared["bindings"] and prepared["bindings"][0]["assignment"]["resource_ids"]
                )
                wait_for(
                    lambda case=case: len(case.backend.jobs) == 1,
                    "actual original domain CPU invocation",
                )
                job = next(iter(case.backend.jobs.values()))
                complete(case, job)
            # Independently recompute both domains before admitting the global goal.
            for case, domain in reversed(list(zip(cases, manifest["participants"], strict=True))):
                case.finish_goal(manifest["participants"][domain]["plan"]["mission"]["id"])
            wait_for(
                lambda: root_case.exchange(path)["global_completed"],
                "all independent original local and global goals",
                45,
            )
            final = root_case.exchange(path)
            save(root / "actual-global-completion.json", final)
            if lost_reply:
                assert relay is not None and relay.lost
                peer = list(manifest["participants"])[1]
                intent = final["original_intents"][peer]["prepare"]
                assert intent["error"] and intent["reply"] is None and intent["observations"]
                assert sum(row["path"].endswith("/prepare") for row in relay.records) == 1
            before = [checkpoint(case.root)["federation"] for case in cases]
            counts = [len(case.backend.jobs) for case in cases]
            for case in cases:
                stop(case.controller)
                case.controller = case.start(case.arguments, "completed-original-restart")
                wait_for(
                    lambda case=case: case.exchange("/healthz"),
                    "original completed participant restart",
                )
            assert [len(case.backend.jobs) for case in cases] == counts
            assert [checkpoint(case.root)["federation"] for case in cases] == before
            result = {
                "passed": True,
                "lost_prepare_response": lost_reply,
                "global_manifest": manifest,
                "complete_original_commit": True,
                "independent_global_goal": True,
                "original_owner_restart": True,
                "software_nodes": 2,
                "cpu_children": sum(counts),
                "devices": 0,
                "external_models": 0,
                "physical_continuity": "unavailable",
            }
    finally:
        for case in cases:
            case.close()
        if relay is not None:
            relay.shutdown()
            relay.server_close()
    # Preserve each independent owner and original files; restore starts no service.
    result["cold_recovery"] = [recover(case, previous) for case in cases]
    save(root / "result.json", result)
    return result


def main() -> None:
    """Freeze actual inputs, retain every case and refuse partial success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-controller", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    options = parser.parse_args()
    options.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not options.skip_build:
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "integration-server", "-p", "roboguide-node"],
            cwd=ROOT,
            check=True,
        )
    sources = [
        ROOT / "target/debug/integration-server",
        ROOT / "target/debug/roboguide-node",
        Path(__file__),
        *sorted((ROOT / "apps/integration-server/src/application/federation").glob("*.rs")),
        *sorted((ROOT / "apps/recovery-tool/roboguide_recovery").glob("*.py")),
    ]
    frozen = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    results: dict[str, Any] = {}
    for name, loss, interruption in (
        ("normal", False, False),
        ("reply-loss", True, False),
        ("prepared-restart", False, True),
    ):
        try:
            results[name] = run(
                options.output / name, loss, options.previous_controller, interruption
            )
        except Exception as error:
            results[name] = {"passed": False, "error": f"{type(error).__name__}: {error}"}
        save(options.output / "progress.json", results)
    unchanged = all(
        hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        for path, digest in frozen.items()
    )
    output = {
        "passed": unchanged and all(result["passed"] for result in results.values()),
        "frozen": frozen,
        "inputs_unchanged": unchanged,
        "cases": results,
    }
    save(options.output / "result.json", output)
    print(json.dumps(output, indent=2))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
