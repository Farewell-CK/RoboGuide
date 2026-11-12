"""Run real isolated Controller/Node processes with a synthetic readonly Mapping source.

This gate never connects to a robot, Pilot, production SQL or deployment authority.
It proves software wiring only; its reported coordinates are explicitly fixture data.
"""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from robonix_task.journal import canonical
from robonix_task.origin import OriginStore


def free_port():
    """Select a loopback test port without probing any remote host."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def await_value(read, predicate, children, timeout=45):
    """Wait within one bounded process gate while retaining actual child failures."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError("owned software process exited; inspect its retained log")
        try:
            value = read()
            if predicate(value):
                return value
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise TimeoutError("software gate observation timed out")


def run(args):
    """Execute one no-motion Task and independently verify its published immutable bytes."""
    package = Path(__file__).resolve().parents[1]
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = OriginStore(root / "node-state/memory/reported-origins", "origin-fixture")
    children, logs = [], []

    class FixtureHandler(BaseHTTPRequestHandler):
        """Supply only bounded origin operations and explicitly synthetic observations."""

        def log_message(self, *_args):
            """Keep the finite gate evidence in its owned files."""

        def reply(self, code, body):
            """Send one finite JSON test response."""
            raw = canonical(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            """Report test provider health, readiness or its authorized Memory exports."""
            if self.path == "/health":
                self.reply(200, {"state": "ONLINE"})
            elif self.path == "/v1/origins/readiness":
                self.reply(200, {"state": "READY", "detail": "synthetic origin store"})
            elif self.path == "/v1/origins/discover":
                self.reply(200, source.discover())
            else:
                self.reply(404, {"error": "fixture route absent"})

        def do_POST(self):
            """Run the real origin store while supplying no actuator or Pilot interface."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("bounded body required")
                body = json.loads(self.rfile.read(length))
                if self.path == "/v1/origins/capture":
                    value = source.capture(
                        body,
                        {
                            "mapping": {
                                "received_at_ns": time.time_ns(),
                                "value": {
                                    "has_map": True,
                                    "map_id": "fixture-map",
                                    "mode": "localization",
                                    "pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
                                },
                            }
                        },
                    )
                elif self.path in ("/v1/origins/status", "/v1/origins/cancel"):
                    value = source.status(body["handle"])
                elif self.path == "/v1/origins/export":
                    value = source.export(body["manifest"])
                else:
                    raise ValueError("fixture route absent")
                self.reply(200, value)
            except Exception as error:
                self.reply(409, {"error": str(error)})

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=fixture.serve_forever)
    thread.start()
    grpc_port, http_port, artifact_port = free_port(), free_port(), free_port()
    origin, artifact_origin = f"http://127.0.0.1:{http_port}", f"http://127.0.0.1:{artifact_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}

    def exchange(base, path, body=None):
        """Read or mutate only the gate-owned loopback services and retain exact replies."""
        request = urllib.request.Request(
            base + path,
            data=None if body is None else canonical(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = json.load(response)
            status = response.status
        with (root / "http.jsonl").open("a") as stream:
            stream.write(
                canonical(
                    {"at_ns": time.time_ns(), "path": path, "http_status": status, "value": value}
                )
                + "\n"
            )
        if status >= 400:
            raise ValueError(f"gate-owned {path} returned HTTP {status}: {value}")
        return value

    def launch(binary, argv, name):
        """Launch an explicitly owned binary with fresh private SQL and environment."""
        output = (root / (name + ".log")).open("wb")
        logs.append(output)
        child = subprocess.Popen(
            [str(binary), *argv], cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT
        )
        children.append(child)

    try:
        launch(
            args.controller,
            [
                f"127.0.0.1:{grpc_port}",
                str(root / "controller.sqlite3"),
                f"127.0.0.1:{http_port}",
                f"127.0.0.1:{artifact_port}",
                str(root / "artifacts"),
            ],
            "controller",
        )
        await_value(lambda: exchange(origin, "/readyz"), lambda v: v["ready"], children)
        fragment = (package / "deploy/origin-node.fragment.toml").read_text()
        fragment = fragment.replace("https://192.168.2.20:8090", artifact_origin)
        fragment = fragment.replace(
            "/home/nvidia/rg-lab/lite3-20261102/node-state/artifact-cache", str(root / "cache")
        )
        config = root / "node.toml"
        config.write_text(
            'schema="roboguide.node-config/v0.14"\nnode_id="origin-fixture"\n'
            + f'server_endpoint="http://127.0.0.1:{grpc_port}"\n'
            + f"state_directory={json.dumps(str(root / 'node-state'))}\nreconnect_delay_ms=100\n"
            + '[[local_systems]]\nid="robot-observer"\nruntime_name="synthetic-origin-fixture"\n'
            + 'runtime_version="test-v1"\n[local_systems.health]\nstate_pointer="/state"\n'
            + 'online=["ONLINE"]\ndegraded=["DEGRADED"]\noffline=["OFFLINE"]\n'
            + '[local_systems.health.step]\nid="health"\nconnection="observer-http"\n'
            + '[local_systems.health.step.operation]\nkind="http"\nmethod="GET"\npath="/health"\n'
            + '[[connections]]\nid="observer-http"\ndriver="http"\nlocal_system="robot-observer"\n'
            + f'endpoint="http://127.0.0.1:{fixture.server_port}"\ntimeout_ms=3000\n'
            + '[[resources]]\nid="aaa-unrelated-camera-slot"\nkind="compute"\n'
            + 'capacity=1\nowner="robot-observer"\n'
            + fragment
        )
        validation = subprocess.run(
            [str(args.node), "--validate", str(config)], capture_output=True, text=True
        )
        (root / "node-validation-error.log").write_text(validation.stderr)
        validation.check_returncode()
        (root / "node-validation.json").write_text(validation.stdout)
        launch(args.node, [str(config)], "node")
        await_value(
            lambda: exchange(origin, "/v1/inventory"),
            lambda v: any(n["liveness"] == "Reachable" for n in v["nodes"]),
            children,
        )
        plan = json.loads((package / "deploy/origin-mission-plan.json").read_text())
        plan["mission"]["id"] = "origin-process-fixture"
        role = plan["tasks"][0]["roles"][0]
        role["execution_intent"]["parameters"].update(
            memory_id="fixture-origin", expected_map_id="fixture-map"
        )
        (root / "submitted-plan.json").write_text(canonical(plan))
        exchange(origin, "/v1/missions/assess-initial-support", plan)
        exchange(origin, "/v1/missions", plan)
        mission = await_value(
            lambda: exchange(origin, "/v1/missions/origin-process-fixture"),
            lambda v: v.get("status") == "Completed",
            children,
        )
        memories = await_value(
            lambda: exchange(artifact_origin, "/v1/memories"),
            lambda v: any(m["selector"]["memory_id"] == "fixture-origin" for m in v["memories"]),
            children,
        )
        manifest = next(
            m for m in memories["memories"] if m["selector"]["memory_id"] == "fixture-origin"
        )
        digest = manifest["artifact"]["content_digest"]
        with opener.open(
            artifact_origin + "/v1/artifacts/" + digest.removeprefix("sha256:"), timeout=5
        ) as response:
            raw = response.read(65537)
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == digest
        content = json.loads(raw)
        assert content["position"]["x_m"] == 1.25 and content["motion_authorized"] is False
        assert content["source_execution_id"] == manifest["source_execution_id"]
        (root / "published-origin.json").write_bytes(raw)
        # This is the production MI reader over the real gate-owned Artifact HTTP/CAS.
        # No language model is substituted and no model call is claimed by this gate.
        from mission.grounding_reader import HttpMissionGroundingReader
        from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind

        reader = HttpMissionGroundingReader(
            origin,
            artifact_origin,
            5,
            8,
            8,
            admitted_memory_content_schemas=frozenset({"roboguide.reported-origin/v0.1"}),
        )
        grounding = reader.capture(
            "fixture-return-request",
            (
                DialogueTurn(
                    "instruction",
                    DialogueSpeaker.USER,
                    DialogueTurnKind.INSTRUCTION,
                    "生成返回共享起点 fixture-origin 的计划，等待审阅。",
                    time.time_ns() // 1000000,
                ),
            ),
            time.time_ns() // 1000000,
        )
        assert grounding.schema_version == "roboguide.grounding-context/v0.4"
        assert len(grounding.memory_evidence) == 1
        assert grounding.memory_evidence[0].content == content and not grounding.gaps
        (root / "mi-grounding.json").write_text(canonical(grounding.to_json()))
        allocations = exchange(origin, "/v1/resource-allocations")
        assert allocations["allocations"] == []
        result = {
            "status": "passed",
            "physical_robot_used": False,
            "synthetic_mapping": True,
            "mission": mission,
            "manifest": manifest,
            "source_sha256": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    args.controller,
                    args.node,
                    Path(__file__),
                    package / "robonix_task/origin.py",
                )
            },
        }
        (root / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"status": "passed", "physical_robot_used": False, "output": str(root)}))
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        fixture.shutdown()
        fixture.server_close()
        thread.join()
        source.close()
        for output in logs:
            output.close()


def main():
    """Require explicit frozen software binaries and a new private evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
