"""Validate two real isolated Nodes and one two-role readonly Task, without any robot."""

import argparse
import json
import os
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from origin_process_gate import await_value, free_port
from robonix_task.host import inspect_host


def run(args):
    """Exercise real registration, joint allocation, execution and release on private storage."""
    package = Path(__file__).resolve().parents[1]
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    children, logs, servers, threads = [], [], [], []
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ROBOGUIDE_")}
    grpc_port, http_port, artifact_port = free_port(), free_port(), free_port()
    origin = f"http://127.0.0.1:{http_port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def exchange(path, body=None):
        """Record exact replies from only the owned loopback Controller."""
        request = urllib.request.Request(
            origin + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=5) as response:
            value = json.load(response)
        with (root / "http.jsonl").open("a") as stream:
            stream.write(json.dumps({"path": path, "value": value}) + "\n")
        return value

    def launch(binary, argv, name):
        """Launch only a selected software binary with a private state directory."""
        output = (root / (name + ".log")).open("wb")
        logs.append(output)
        children.append(
            subprocess.Popen(
                [str(binary), *argv], cwd=root, env=environment, stdout=output, stderr=output
            )
        )

    def handler(node_id):
        """Bind each test deployment identity to the production readonly host reader."""

        class Handler(BaseHTTPRequestHandler):
            """Expose only the host reader; no actuator or POST interface exists here."""

            def log_message(self, *_args):
                """Keep finite owned logs free of repeated health polling."""

            def do_GET(self):
                """Return unavailable robot observations without changing their meaning."""
                if self.path != "/v1/host/status":
                    self.send_error(404)
                    return
                raw = json.dumps(inspect_host(node_id, {})).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

        return Handler

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
        await_value(lambda: exchange("/readyz"), lambda v: v["ready"], children)
        for node_id in ("host-fixture-a", "host-fixture-b"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(node_id))
            thread = threading.Thread(target=server.serve_forever)
            servers.append(server)
            threads.append(thread)
            thread.start()
            fragment = (package / "deploy/host-node.fragment.toml").read_text()
            fragment = fragment.replace("NODE_ID", node_id).replace(
                "127.0.0.1:18112", f"127.0.0.1:{server.server_port}"
            )
            config = root / (node_id + ".toml")
            config.write_text(
                f'schema="roboguide.node-config/v0.14"\nnode_id="{node_id}"\n'
                f'server_endpoint="http://127.0.0.1:{grpc_port}"\n'
                f'state_directory="{root / node_id}"\nreconnect_delay_ms=100\n' + fragment
            )
            check = subprocess.run(
                [str(args.node), "--validate", str(config)], capture_output=True, text=True
            )
            (root / (node_id + "-validation.log")).write_text(check.stdout + check.stderr)
            check.check_returncode()
            launch(args.node, [str(config)], node_id)
        await_value(
            lambda: exchange("/v1/inventory"),
            lambda v: len([n for n in v["nodes"] if n["liveness"] == "Reachable"]) == 2,
            children,
        )
        plan = json.loads((package / "deploy/host-mission-plan.json").read_text())
        (root / "submitted-plan.json").write_text(json.dumps(plan, indent=2))
        exchange("/v1/missions/assess-initial-support", plan)
        exchange("/v1/missions", plan)
        mission_id = plan["mission"]["id"]
        mission = await_value(
            lambda: exchange("/v1/missions/" + mission_id),
            lambda v: v.get("status") == "Completed",
            children,
        )
        attempts = exchange("/v1/execution-attempts")["attempts"]
        assert len(attempts) == 2
        assert {a["node_id"] for a in attempts} == {"host-fixture-a", "host-fixture-b"}
        assert all(a["status"] == "Completed" for a in attempts)
        allocations = exchange("/v1/resource-allocations")
        assert allocations["allocations"] == []
        (root / "result.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "physical_robot_used": False,
                    "mission": mission,
                    "attempts": attempts,
                },
                indent=2,
            )
        )
        print(json.dumps({"passed": True, "physical_robot_used": False, "output": str(root)}))
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()
        for output in logs:
            output.close()


def main():
    """Require explicit process binaries and a new private evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
