"""Exercise goal rejection and stale-state recovery with real private Controller/Node processes.

Every provider response is synthetic loopback data. No robot, Pilot, deployment SQL,
model or actuator is contacted. Retain both source and process evidence in --output.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from origin_process_gate import free_port
from robonix_task.posture import inspect_posture
from robonix_task.posture_goal import VERIFIER, assess_goal, predicate_for

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/quality"))
from check_state_verifier_processes import node_config, plan  # noqa: E402

NODE = "posture-software-fixture"
SOURCE = "reported-posture-fixture"
PREDICATE = predicate_for("down")


def run(args):
    """Retain contradictory completion, goal barriers, original budgets and cold restart proof."""
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    processes, logs, calls = [], [], []
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
    environment.update(
        ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN="1",
        ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA=args.storage_schema,
        ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS="200",
        ROBOGUIDE_NODE_LEASE_DURATION_MS="1200",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    sample = {"posture": "standing", "received_at_ns": 0, "frozen": False}

    def save(name, value):
        """Retain one inspectable finite observation separately from polling logs."""
        (root / (name + ".json")).write_text(json.dumps(value, indent=2), encoding="utf-8")
        return value

    def exchange(path, body=None):
        """Use only the owned loopback public API, without transparent mutation retry."""
        request = urllib.request.Request(
            origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, value = response.code, json.load(response)
        with (root / "http.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"path": path, "input": body, "status": status, "value": value}) + "\n"
            )
        return status, value

    def get(path):
        """Require a successful private Controller read."""
        status, value = exchange(path)
        assert status == 200, value
        return value

    def wait(read, predicate, reason):
        """Bound each wait and fail with the last actual response for diagnosis."""
        deadline, last = time.monotonic() + 25, None
        while time.monotonic() < deadline:
            try:
                last = read()
                if predicate(last):
                    return last
            except (OSError, ValueError) as error:
                last = str(error)
            time.sleep(0.08)
        raise TimeoutError(f"{reason}: {last}")

    def launch(binary, argv, name):
        """Own each child and its private log and storage; never alter a deployment process."""
        output = (root / (name + ".log")).open("wb")
        logs.append(output)
        process = subprocess.Popen(
            [str(binary), *argv],
            cwd=root,
            env=environment,
            stdout=output,
            stderr=output,
        )
        processes.append(process)
        return process

    def stop(process):
        """Reap only the selected child, keeping its durable journal for restart."""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    class Handler(BaseHTTPRequestHandler):
        """Supply invented posture reports and record readonly software executions."""

        def log_message(self, *_args):
            """Use retained structured observations instead of repetitive access logs."""

        def reply(self, value):
            """Send one finite JSON fixture response."""
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            """Publish synthetic State; freezing keeps native identity while RG keeps exporting."""
            if self.path == "/health":
                self.reply({"state": "ONLINE", "detail": "software fixture"})
            elif self.path == "/capability":
                self.reply({"state": "READY", "detail": "readonly fixture"})
            elif self.path == "/admission":
                if not sample["frozen"]:
                    sample["received_at_ns"] = time.time_ns()
                self.reply(
                    {
                        "chassis": {
                            "contract_id": "robonix/primitive/chassis/state",
                            "provider_id": "fixture-provider",
                            "registration_id": "fixture-registration",
                            "received_at_ns": sample["received_at_ns"],
                            "age_ms": (time.time_ns() - sample["received_at_ns"]) / 1_000_000,
                            # Deliberately retain the inner cached label.
                            "availability": "current",
                            "value": {
                                "posture": sample["posture"],
                                "stale": False,
                                "stale_ms": 5000,
                            },
                        }
                    }
                )
            else:
                self.send_error(404)

        def do_POST(self):
            """Complete only a synthetic software operation; no actuator API exists."""
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/execute":
                calls.append(body["attempt_id"])
                self.reply({"execution_id": body["attempt_id"]})
            elif self.path == "/status":
                self.reply(
                    {"state": "COMPLETED", "detail": "software call returned; no physical effect"}
                )
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ports = set()
    while len(ports) < 3:
        candidate = free_port()
        if candidate != server.server_port:
            ports.add(candidate)
    grpc, http, artifact = ports
    origin = f"http://127.0.0.1:{http}"
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    command = [
        f"127.0.0.1:{grpc}",
        str(root / "controller.sqlite3"),
        f"127.0.0.1:{http}",
        f"127.0.0.1:{artifact}",
        str(root / "artifacts"),
    ]

    def snapshot():
        """Read exact inventory and State through the production Controller projection."""
        inventory = get("/v1/inventory")
        state = get("/v1/state/records?object_class=node&object_type=robot")
        return {
            "inventory": inventory,
            "state": state,
            "inspection": inspect_posture(state, inventory, node_id=NODE),
        }

    def fresh():
        """Await a qualified aggregate while keeping all unknown observations in the trace."""
        return wait(snapshot, lambda v: v["inspection"]["status"] == "reported", "fresh State")

    def new_observation(anchor):
        """Wait for a genuinely newer native sample rather than just a republished cache."""
        return wait(
            snapshot,
            lambda v: (
                assess_goal(
                    anchor, v["state"], v["inventory"], node_id=NODE, expected_posture="down"
                )["finality"]
                == "final"
            ),
            "post-completion observation",
        )

    def submit(identity, verified, dependent):
        """Use existing generic Mission/Verifier contracts with only a readonly lab operation."""
        document = plan(identity)
        operation = {"namespace": "lab", "name": "report-posture", "version": "v1"}
        document["mission"]["objective"] = "Software goal settlement only; no robot action"
        for index, task in enumerate(document["tasks"]):
            task["id"] = "posture-goal" if index == 0 else "after-goal"
            task["depends_on"] = [] if index == 0 else ["posture-goal"]
            task["description"] = "Synthetic local completion; independently assess reported State"
            role = task["roles"][0]
            role["requirements"]["capabilities"][0]["contract"] = operation
            role["execution_intent"].update(
                operation=operation, objective="Readonly software fixture"
            )
            task["satisfaction"] = {
                "basis": "execution-report",
                "expected_effect": "Call completed",
            }
            if index == 0 and verified:
                task["satisfaction"] = {
                    "basis": "verifier-evidence",
                    "expected_effect": PREDICATE,
                    "verifier": {
                        "contract": VERIFIER,
                        "predicate": PREDICATE,
                        "max_evidence_age_ms": 5000,
                    },
                }
        if not dependent:
            document["tasks"] = document["tasks"][:1]
        save(identity + "-plan", document)
        status, response = exchange("/v1/missions", document)
        assert status in (200, 202), response
        return wait(
            lambda: get("/v1/missions/" + identity),
            lambda v: task_status(v) == ("AwaitingSatisfaction" if verified else "Completed"),
            identity + " local completion",
        )

    def task_status(mission):
        """Select the exact Task identity without relying on projection array order."""
        return next(t["status"] for t in mission["tasks"] if t["task_id"] == "posture-goal")

    def evidence(identity, assessment, sequence):
        """Bind only this private assessment to its exact accepted plan and current attempt set."""
        attempts = [
            a
            for a in get("/v1/execution-attempts")["attempts"]
            if a["mission_id"] == identity and a["task_id"] == "posture-goal"
        ]
        assert len(attempts) == 1 and attempts[0]["status"] == "Completed", attempts
        return {
            "evidence_id": identity + "-" + str(sequence),
            "source_id": SOURCE,
            "source_revision": 1,
            "tenant_id": "local",
            "task_ref": {"mission_id": identity, "task_id": "posture-goal"},
            "plan_digest": get(f"/v1/missions/{identity}/tenant")["source_plan_digest"],
            "attempts": [
                {"role_id": a["role_id"], "attempt_id": a["execution_id"]} for a in attempts
            ],
            "predicate": PREDICATE,
            "verdict": assessment["verdict"],
            "finality": assessment["finality"],
            "source_observed_at_ms": time.time_ns() // 1_000_000,
            "valid_for_ms": assessment["valid_for_ms"],
            "sequence": sequence,
            "artifact_digest": None,
            "artifact_size_bytes": None,
        }

    try:
        controller = launch(args.controller, command, "controller")
        wait(lambda: get("/readyz"), lambda v: v["ready"], "Controller startup")
        config = node_config(
            root, NODE, f"127.0.0.1:{grpc}", f"http://127.0.0.1:{server.server_port}"
        )
        replacements = {
            "compute.hash-files@v1": "lab.report-posture@v1",
            'id = "admission"': 'id = "robot-state"',
            "posix-compute": "robot-observer",
            'object_class = "world"': 'object_class = "node"',
            'object_type = "software-work-admission"': 'object_type = "robot"',
            'semantic = "observed"': 'semantic = "reported"',
            'payload_schema = "roboguide.software-work-admission/v0.1"': (
                'payload_schema = "lite3.robot-state/v0.1"'
            ),
            "valid_for_ms = 3000": "valid_for_ms = 1200",
        }
        for old, new in replacements.items():
            config = config.replace(old, new)
        config_path = root / "node.toml"
        config_path.write_text(config, encoding="utf-8")
        node = launch(args.node, [str(config_path)], "node")
        save("initial-state", fresh())
        registry = {
            "command_id": "install-fixture-verifier",
            "expected_revision": 0,
            "registry": {
                "schema_version": "roboguide.semantic-verifier-registry/v0.1",
                "registry_id": "posture-fixture",
                "revision": 1,
                "sources": {
                    SOURCE: {
                        "source_id": SOURCE,
                        "revision": 1,
                        "tenant_id": "local",
                        "producer_principals": ["trusted-loopback:operator"],
                        "verifier": VERIFIER,
                        "predicates": [PREDICATE],
                        "max_age_ms": 5000,
                        "stop_timeout_ms": 5000,
                        "enabled": True,
                        "continuous": False,
                        "require_artifact": False,
                    }
                },
            },
        }
        assert exchange("/v1/verifier-sources", registry)[0] == 200

        save("baseline-completed-with-standing", submit("baseline", False, False))
        assert fresh()["inspection"]["posture"] == "standing"
        save("negative-awaiting", submit("negative", True, True))
        anchor = fresh()["inspection"]
        current = new_observation(anchor)
        negative = assess_goal(
            anchor, current["state"], current["inventory"], node_id=NODE, expected_posture="down"
        )
        assert negative["verdict"] == "unsatisfied"
        save("negative-assessment", negative)
        status, receipt = exchange("/v1/verifier-evidence", evidence("negative", negative, 1))
        assert status == 200 and receipt["receipt"]["applied"], receipt
        rejected = wait(
            lambda: get("/v1/missions/negative"), lambda v: v["status"] == "Failed", "rejection"
        )
        save("negative-rejected", rejected)
        assert task_status(rejected) == "SatisfactionRejected", rejected
        assert len(calls) == 2, calls
        wait(
            lambda: get("/v1/resource-allocations"),
            lambda v: v["allocations"] == [],
            "negative release",
        )

        save("positive-awaiting", submit("positive", True, True))
        wait(
            lambda: get("/v1/resource-allocations"),
            lambda v: bool(v["allocations"]),
            "goal ownership held",
        )
        provisional = evidence(
            "positive",
            {
                "verdict": "unknown",
                "finality": "provisional",
                "valid_for_ms": 5000,
            },
            1,
        )
        status, provisional_receipt = exchange("/v1/verifier-evidence", provisional)
        assert status == 200 and provisional_receipt["receipt"]["applied"] is False
        assert exchange("/v1/verifier-evidence", provisional) == (status, provisional_receipt)
        save("unknown-provisional-receipt", provisional_receipt)
        assert task_status(get("/v1/missions/positive")) == "AwaitingSatisfaction"
        sample["frozen"] = True
        anchor = fresh()["inspection"]
        time.sleep(0.25)
        cached = snapshot()
        unchanged = assess_goal(
            anchor, cached["state"], cached["inventory"], node_id=NODE, expected_posture="down"
        )
        save("cached-native-refused", unchanged)
        assert unchanged["verdict"] == "unknown"
        assert task_status(get("/v1/missions/positive")) == "AwaitingSatisfaction"
        assert len(calls) == 3

        expired = wait(
            snapshot,
            lambda v: v["inspection"].get("reason") == "chassis_state_stale_or_age_missing",
            "inner aggregate expires while Node continues publishing",
        )
        save("online-node-expired-native-sample", expired)
        assert expired["inventory"]["nodes"][0]["liveness"] == "Reachable"
        assert expired["state"]["records"][0]["stale"] is False
        assert task_status(get("/v1/missions/positive")) == "AwaitingSatisfaction"
        assert len(calls) == 3

        stop(node)
        offline = wait(
            snapshot,
            lambda v: v["inventory"]["nodes"][0]["liveness"] == "Unreachable",
            "offline lease",
        )
        save("offline-state", offline)
        assert offline["inspection"]["status"] == "unavailable"
        assert (
            offline["state"]["records"][0]["value"]["value"]["chassis"]["availability"] == "current"
        )
        assert get("/v1/missions/positive")["status"] != "Completed"
        assert len(calls) == 3

        stop(controller)
        controller = launch(args.controller, command, "controller-recovered")
        wait(lambda: get("/readyz"), lambda v: v["ready"], "Controller recovery")
        save("after-controller-recovery", get("/v1/missions/positive"))
        assert task_status(get("/v1/missions/positive")) == "AwaitingSatisfaction"
        sample.update(frozen=False, posture="down")
        node = launch(args.node, [str(config_path)], "node-recovered")
        save("first-node-recovery-projection", snapshot())
        recovered = wait(
            snapshot,
            lambda v: (
                v["inspection"]["status"] == "reported"
                and v["inspection"]["evidence"]["source_epoch"]
                != anchor["evidence"]["source_epoch"]
            ),
            "new Node epoch State, not merely reachable inventory",
        )
        save("after-node-recovery", recovered)
        assert (
            recovered["inspection"]["evidence"]["source_epoch"]
            != anchor["evidence"]["source_epoch"]
        )
        assert (
            assess_goal(
                anchor,
                recovered["state"],
                recovered["inventory"],
                node_id=NODE,
                expected_posture="down",
            )["verdict"]
            == "unknown"
        )
        assert len(calls) == 3, "completed local attempt repeated on restart"
        anchor = recovered["inspection"]  # Exact local attempt remains terminal after recovery.
        current = new_observation(anchor)
        positive = assess_goal(
            anchor, current["state"], current["inventory"], node_id=NODE, expected_posture="down"
        )
        save("positive-assessment", positive)
        assert positive["verdict"] == "satisfied"
        valid = evidence("positive", positive, 3)
        for field in ("plan_digest", "attempts"):
            wrong = deepcopy(valid)
            wrong["evidence_id"] += "-wrong-" + field
            if field == "plan_digest":
                wrong[field] = "sha256:" + "0" * 64
            else:
                wrong[field][0]["attempt_id"] = "foreign-attempt"
            status, response = exchange("/v1/verifier-evidence", wrong)
            save("refused-" + field, {"status": status, "response": response})
            assert status == 409, response
        status, receipt = exchange("/v1/verifier-evidence", valid)
        assert status == 200 and receipt["receipt"]["applied"], receipt
        assert exchange("/v1/verifier-evidence", valid) == (status, receipt)
        save("positive-receipt", receipt)
        completed = wait(
            lambda: get("/v1/missions/positive"),
            lambda v: v["status"] == "Completed",
            "positive goal",
        )
        save("positive-completed", completed)
        wait(
            lambda: get("/v1/resource-allocations"),
            lambda v: not v["allocations"],
            "final resource release",
        )
        assert len(calls) == 4 and len(set(calls)) == 4, calls
        save("final-attempts", get("/v1/execution-attempts"))
        save("final-allocations", get("/v1/resource-allocations"))
        assert get("/v1/missions/baseline")["status"] == "Completed"
        assert get("/v1/missions/negative")["status"] == "Failed"
        result = {
            "passed": True,
            "physical_robot_used": False,
            "field_deployment_changed": False,
            "checks": [
                "legacy completion reproduced",
                "goal waits for evidence",
                "fresh contradiction rejects goal",
                "dependency blocked on rejection",
                "cache republication refused",
                "unknown provisional evidence cannot satisfy goal",
                "online Node cannot refresh expired native aggregate",
                "offline inner-current label refused",
                "Controller and Node cold restart retain goal barrier",
                "restart does not repeat local execution",
                "old epoch refused",
                "foreign plan and attempt refused",
                "fresh reported goal completes",
                "duplicate evidence returns original receipt",
                "dependency executes once",
                "resources released through ordinary Control",
                "historical lifecycles unchanged after recovery",
            ],
            "software_execute_calls": calls,
            "binary_sha256": {
                str(p): hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
                for p in (args.controller, args.node)
            },
        }
        save("result", result)
        print(json.dumps({"passed": True, "checks": len(result["checks"]), "output": str(root)}))
    finally:
        for child in reversed(processes):
            stop(child)
        server.shutdown()
        server.server_close()
        thread.join()
        for output in logs:
            output.close()


def main():
    """Require explicit software binaries and a fresh evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-schema", choices=("legacy", "1", "2", "3"), default="legacy")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
