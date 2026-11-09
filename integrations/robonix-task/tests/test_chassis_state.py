"""Exercise the fixed readonly MCP boundary and stale/unknown RG posture refusal."""

import json
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from robonix_task.chassis_state import (
    CONTRACT,
    _endpoint,
    _rpc_result,
    chassis_view,
    read_chassis_state,
)
from robonix_task.posture import inspect_posture


def source():
    """Return explicitly synthetic Robonix aggregate evidence for boundary tests."""
    return {
        "posture": "standing",
        "battery": {"percent": 75},
        "ts_ms": 1000,
        "age_ms": 1,
        "stale_ms": 5000,
        "stale": False,
    }


class ChassisTests(unittest.TestCase):
    """Verify fixed contract/tool use, cleanup and independent evidence expiration."""

    def test_real_http_calls_only_state_and_releases_sessions(self):
        """A real loopback MCP exchange performs no Pilot, motion, mode or lifecycle command."""
        calls = []

        class Handler(BaseHTTPRequestHandler):
            """Serve only the finite fixture MCP protocol and record every actual request."""

            def log_message(self, *_args):
                """Keep repeated fixture requests out of user-facing output."""

            def do_POST(self):
                """Record initialization/state payloads and supply the original String wrapper."""
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body)
                method = body["method"]
                result = (
                    {}
                    if method == "initialize"
                    else {
                        "content": [
                            {"type": "text", "text": json.dumps({"data": json.dumps(source())})}
                        ]
                    }
                )
                raw = (
                    b""
                    if method == "notifications/initialized"
                    else (
                        "data: "
                        + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
                        + "\n\n"
                    ).encode()
                )
                self.send_response(202 if not raw else 200)
                self.send_header("Mcp-Session-Id", "fixture-session")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_DELETE(self):
                """Confirm the original HTTP session is explicitly closed."""
                calls.append({"delete_session": self.headers.get("Mcp-Session-Id")})
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        atlas_calls = []

        class Atlas:
            """Expose only the exact state discovery and connection bookkeeping methods."""

            consumer = "fixture-observer"

            def unary(self, route, request, _response):
                """Record exact RPC request fields without contacting any hardware."""
                atlas_calls.append((route, request))
                if route == "Query":
                    return SimpleNamespace(
                        providers=[
                            SimpleNamespace(
                                id="fixture",
                                registration_id="r1",
                                state=3,
                                capabilities=[SimpleNamespace(contract_id=CONTRACT, transport=3)],
                            )
                        ]
                    )
                if route == "ConnectCapability":
                    return SimpleNamespace(
                        endpoint=f"http://127.0.0.1:{server.server_port}/mcp", channel_id="edge1"
                    )
                return SimpleNamespace(was_open=True)

        try:
            result = read_chassis_state(Atlas())
            self.assertEqual(result["value"]["posture"], "standing")
            self.assertEqual(
                [c[0] for c in atlas_calls], ["Query", "ConnectCapability", "DisconnectCapability"]
            )
            self.assertEqual(atlas_calls[1][1].contract_id, CONTRACT)
            self.assertEqual(
                [c["params"] for c in calls if c.get("method") == "tools/call"],
                [{"name": "state", "arguments": {}}],
            )
            self.assertEqual(calls[-1], {"delete_session": "fixture-session"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cache_does_not_refresh_source(self):
        """Repeated cache reads cannot refresh original aggregate age or clock rollback."""
        obs = {"value": source(), "received_at_ns": 1000000000, "request_duration_ms": 2}
        self.assertEqual(chassis_view(obs, 1000000000)["availability"], "current")
        self.assertEqual(chassis_view(obs, 6000000000)["availability"], "stale")
        self.assertNotEqual(chassis_view(obs, 999999999)["availability"], "current")
        self.assertEqual(obs["value"]["age_ms"], 1)

    def test_endpoint_and_failed_response_refused(self):
        """Remote URLs, credentials, redirects-as-JSON and JSON-RPC errors are not observations."""
        self.assertEqual(_endpoint("http://127.0.0.1:1234/mcp/"), "http://127.0.0.1:1234/mcp")
        for endpoint in (
            "http://example.com:80/mcp",
            "http://user@127.0.0.1:80/mcp",
            "http://127.0.0.1:80/action",
        ):
            with self.assertRaises(ValueError):
                _endpoint(endpoint)
        with self.assertRaises(ValueError):
            _rpc_result(b'{"id":2,"error":{"code":-1}}', 2)

    def test_controller_reader_refuses_old_unknown_and_foreign_sources(self):
        """Controller freshness alone never promotes expired aggregate or an unknown posture."""
        chassis = chassis_view(
            {
                "value": source(),
                "received_at_ns": 1000000000,
                "request_duration_ms": 1,
                "contract_id": CONTRACT,
            },
            1000000000,
        )
        record = {
            "object": {"class": "node", "object_type": "robot", "object_id": "dog"},
            "channel_id": "robot-state",
            "source": "node:dog/robot-observer",
            "semantic": "reported",
            "stale": False,
            "value": {
                "payload_schema": "lite3.robot-state/v0.1",
                "received_at_ms": 1000,
                "valid_for_ms": 10000,
                "source_epoch": "session1",
                "sequence": 1,
                "value": {"chassis": chassis},
            },
        }
        state = {
            "schema": "roboguide.state-query/v0.1",
            "observed_at_ms": 1001,
            "records": [record],
        }
        inventory = {
            "schema_version": "roboguide.inventory/v0.1",
            "nodes": [{"node_id": "dog", "liveness": "Reachable"}],
        }
        self.assertEqual(inspect_posture(state, inventory, node_id="dog")["posture"], "standing")
        for change in ("old", "unknown", "source", "duplicate", "offline"):
            s, inv = deepcopy(state), deepcopy(inventory)
            if change == "old":
                s["observed_at_ms"] = 6001
            elif change == "unknown":
                s["records"][0]["value"]["value"]["chassis"]["value"]["posture"] = "unknown"
            elif change == "source":
                s["records"][0]["source"] = "node:other/robot-observer"
            elif change == "duplicate":
                s["records"].append(deepcopy(record))
            else:
                inv["nodes"][0]["liveness"] = "Unreachable"
            result = inspect_posture(s, inv, node_id="dog")
            self.assertEqual(result["status"], "unavailable", change)
            self.assertIsNone(result["posture"])
            self.assertIs(result["motion_authorized"], False)


if __name__ == "__main__":
    unittest.main()
