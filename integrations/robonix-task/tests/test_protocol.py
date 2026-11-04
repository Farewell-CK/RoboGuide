"""Offline native gRPC/HTTP/process fault acceptance; no robot endpoints are used."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import grpc
from robonix_task.adapter import Adapter, Server
from robonix_task.journal import Conflict, Journal
from robonix_task.protocol import SUBMIT_CONTRACT, Liaison, event_dict, message


def request(attempt="attempt-1", budget=3000):
    """Supply a fixture-only semantic operation, unrelated to registered robot capabilities."""
    return {
        "attempt_id": attempt,
        "budget_ms": budget,
        "invocation": {
            "execution_id": "rg-" + attempt,
            "intent": {
                "operation": {
                    "namespace": "lab",
                    "name": "protocol-probe",
                    "version": "v1",
                },
                "objective": "offline native protocol fixture",
                "parameters": {},
            },
        },
    }


def wait_for(predicate, timeout=6):
    """Wait a short bounded interval for an independent observable condition."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition did not become observable")


def http(port, route, body=None):
    """Call only a fixture's explicit loopback HTTP port, without desktop proxies."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + route,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


class NativeFixture:
    """Expose protobuf gRPC methods on loopback with a durable fixture effect sink."""

    def __init__(self, root, mode="full", wrapped=False):
        """Create isolated fake Atlas/Liaison services; tasks never invoke a robot or shell."""
        self.root, self.mode, self.wrapped = Path(root), mode, wrapped
        self.lock = threading.Lock()
        self.tasks, self.aborts, self.connections, self.disconnections = [], [], [], []
        self.registration = "fixture-registration-1"
        self.query_delay = 0
        self.first_event_delay = 0
        self.active = True
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.server = grpc.server(self.pool)
        handlers = {
            "Query": grpc.unary_unary_rpc_method_handler(
                self.query,
                request_deserializer=message("robonix.atlas.QueryRequest").FromString,
                response_serializer=lambda value: value.SerializeToString(),
            ),
            "ConnectCapability": grpc.unary_unary_rpc_method_handler(
                self.connect,
                request_deserializer=message("robonix.atlas.ConnectCapabilityRequest").FromString,
                response_serializer=lambda value: value.SerializeToString(),
            ),
            "DisconnectCapability": grpc.unary_unary_rpc_method_handler(
                self.disconnect,
                request_deserializer=message(
                    "robonix.atlas.DisconnectCapabilityRequest"
                ).FromString,
                response_serializer=lambda value: value.SerializeToString(),
            ),
        }
        self.server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler("robonix.atlas.Atlas", handlers),
                grpc.method_handlers_generic_handler(
                    "robonix.contracts.RobonixSystemLiaisonSubmit",
                    {
                        "SubmitTask": grpc.unary_stream_rpc_method_handler(
                            self.submit,
                            request_deserializer=message("robonix.pilot.Task").FromString,
                            response_serializer=lambda value: value,
                        ),
                    },
                ),
            )
        )
        self.port = self.server.add_insecure_port("127.0.0.1:0")
        self.server.start()

    @property
    def endpoint(self):
        """Return the unique local fixture transport, never a Tailscale address."""
        return f"127.0.0.1:{self.port}"

    def query(self, req, context):
        """Model exact discovery with configurable readiness and discovery latency."""
        if req.contract_id != SUBMIT_CONTRACT or req.transport != 1:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "wrong discovery contract")
        time.sleep(self.query_delay)
        provider = message(
            "robonix.atlas.CapabilityProvider",
            id="liaison",
            state=3 if self.active else 1,
            registration_id=self.registration,
            capabilities=[{"contract_id": SUBMIT_CONTRACT, "transport": 1}],
        )
        return message("robonix.atlas.QueryResponse", providers=[provider])

    def connect(self, req, context):
        """Record Atlas edges separately from executable task admission."""
        if req.provider_id != "liaison" or req.contract_id != SUBMIT_CONTRACT:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "wrong connection")
        with self.lock:
            self.connections.append(req)
            channel_id = f"edge-{len(self.connections)}"
        return message(
            "robonix.atlas.ConnectCapabilityResponse",
            channel_id=channel_id,
            endpoint=self.endpoint,
        )

    def disconnect(self, req, context):
        """Observe actual transport cleanup without pretending that work stopped."""
        with self.lock:
            self.disconnections.append(req.channel_id)
        return message("robonix.atlas.DisconnectCapabilityResponse", was_open=True)

    def encode(self, event):
        """Use either native raw PilotEvent or the documented response wrapper."""
        if self.wrapped:
            return message("robonix.liaison.SubmitTask_Response", event=event).SerializeToString()
        return event.SerializeToString()

    def submit(self, req, context):
        """Persist one fake effect before response loss, and keep abort ACK nonconclusive."""
        abort = json.loads(req.context_json).get("abort_turn", False)
        with self.lock:
            target = self.aborts if abort else self.tasks
            target.append(req)
            with (self.root / ("abort.jsonl" if abort else "effects.jsonl")).open(
                "a", encoding="utf-8"
            ) as sink:
                sink.write(
                    json.dumps({"task_id": req.task_id, "session_id": req.session_id}) + "\n"
                )
                sink.flush()
                os.fsync(sink.fileno())
        if abort:
            yield self.encode(
                message(
                    "robonix.pilot.PilotEvent",
                    event_kind=3,
                    session_id=req.session_id,
                    status={
                        "session_id": req.session_id,
                        "state": 1,
                        "message": "stop requested; being cancelled",
                    },
                )
            )
            return
        if self.mode == "empty":
            return
        if self.mode == "lost-response":
            context.abort(grpc.StatusCode.UNAVAILABLE, "effect admitted; response lost")
        time.sleep(self.first_event_delay)
        sid = "foreign-session" if self.mode == "foreign" else req.session_id
        events = [
            message(
                "robonix.pilot.PilotEvent",
                event_kind=3,
                session_id=sid,
                status={
                    "session_id": sid,
                    "state": 0,
                    "message": "turn_id=" + req.task_id,
                },
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=1,
                session_id=sid,
                plan={"plan_id": "plan-1", "session_id": sid, "round": 1},
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=5,
                session_id=sid,
                node_state={"plan_id": "plan-1", "node_index": 0, "state": 7},
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=6,
                session_id=sid,
                task_state={
                    "task_id": "T1",
                    "goal": "fixture",
                    "status": "done",
                    "tasks": [
                        {
                            "task_id": "T1",
                            "status": "done",
                            "message_ids": ["m1"],
                            "plan_ids": ["plan-1", "plan-2"],
                            "status_reason": "fixture only",
                        }
                    ],
                },
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=2,
                session_id=sid,
                batch_result={
                    "plan_id": "plan-1",
                    "session_id": sid,
                    "any_failed": True,
                },
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=1,
                session_id=sid,
                plan={"plan_id": "plan-2", "session_id": sid, "round": 2},
            ),
            message(
                "robonix.pilot.PilotEvent",
                event_kind=4,
                session_id=sid,
                final_text="completed",
            ),
        ]
        for event in events:
            yield self.encode(event)
        if self.mode == "changed-provider":
            self.registration = "replacement-registration"
        if self.mode == "blocked":
            while context.is_active():
                time.sleep(0.02)

    def close(self):
        """Stop fixture RPCs and join all fixture handlers."""
        self.server.stop(0).wait()
        self.pool.shutdown(wait=True)


class Acceptance(unittest.TestCase):
    """Check transport, identity, uncertainty and real process boundaries independently."""

    def setUp(self):
        """Isolate every test's native effects, journal and temporary HTTP server."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = NativeFixture(self.root)
        self.app = None

    def tearDown(self):
        """Detach the adapter before stopping its fake remote and removing owned files."""
        if self.app:
            self.app.close()
        self.fixture.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        """Enable only the fixture operation on an independently allocated local endpoint."""
        self.app = Adapter(
            self.root / "journal",
            Liaison(self.fixture.endpoint, "rg/fixture", "local:fixture"),
            allowed_operations=("lab.protocol-probe@v1",),
            enabled=True,
            **kwargs,
        )
        return self.app

    def settled(self, handle):
        """Wait until retained ambiguity and the bounded abort attempt are both observable."""
        return wait_for(
            lambda: (
                self.app.status(handle)["state"] == "UNKNOWN"
                and self.app.status(handle)["abort_sent"]
            )
        )

    def test_protocol_provenance_and_complete_raw_wrapped_schema(self):
        """Validate pinned bytes and preserve fields that the older GUI drops."""
        vendor = Path(__file__).resolve().parents[1] / "robonix_task" / "vendor"
        provenance = json.loads((vendor / "provenance.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(provenance["commit"], "c9c159ec2c74522f275e62fe79ec5e73e34e1028")
        for entry in provenance["files"]:
            self.assertEqual(
                hashlib.sha256((vendor / entry["name"]).read_bytes()).hexdigest(),
                entry["sha256"],
            )
        event = message(
            "robonix.pilot.PilotEvent",
            event_kind=6,
            session_id="s",
            task_state={
                "task_id": "T1",
                "tasks": [{"task_id": "T1", "message_ids": ["m1"], "plan_ids": ["p1"]}],
            },
        )
        for wrapped in (False, True):
            self.fixture.wrapped = wrapped
            decoded = event_dict(self.fixture.encode(event))
            self.assertEqual(decoded["task_state"]["tasks"][0]["message_ids"], ["m1"])
            self.assertEqual(decoded["task_state"]["tasks"][0]["plan_ids"], ["p1"])

    def test_native_stream_retains_all_events_and_never_promotes_text(self):
        """Exercise real gRPC discovery, task wire shape, multi-plan progress and cleanup."""
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        wait_for(lambda: len(self.fixture.aborts) == 1)
        status = app.status(row["handle"])
        self.assertEqual(status["progress"]["plan_ids"], ["plan-1", "plan-2"])
        self.assertEqual(status["progress"]["turn_id"], status["task_id"])
        self.assertEqual(status["progress"]["task_state"]["task_id"], "T1")
        self.assertEqual(status["state"], "UNKNOWN")
        events = app.journal.events(row["handle"])
        self.assertEqual(len([e for e in events if e["source"] == "task"]), 7)
        self.assertTrue(all(e["raw"] for e in events))
        self.assertEqual(self.fixture.tasks[0].source, 2)
        self.assertEqual(self.fixture.tasks[0].task_id, status["task_id"])
        wait_for(lambda: len(self.fixture.connections) == len(self.fixture.disconnections))

    def test_wrapped_native_stream(self):
        """Accept a wrapper-producing service using the same endpoint and journal behavior."""
        self.fixture.wrapped = True
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        self.assertEqual(app.status(row["handle"])["progress"]["plan_ids"], ["plan-1", "plan-2"])

    def test_concurrent_retries_reuse_one_identity_and_one_effect(self):
        """Sixteen real callers must not duplicate native dispatch or renew admission."""
        app = self.create()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            rows = list(pool.map(lambda _: app.submit(request()), range(16)))
        self.assertEqual(len({r["handle"] for r in rows}), 1)
        self.assertEqual(len({r["created_ns"] for r in rows}), 1)
        self.settled(rows[0]["handle"])
        self.assertEqual(len(self.fixture.tasks), 1)
        with self.assertRaises(Conflict):
            app.submit(request(budget=4000))

    def test_unknown_retains_slot_and_fresh_ids_do_not_bypass_it(self):
        """Ambiguous work blocks a different attempt without pretending to be cancelled."""
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        with self.assertRaises(Conflict):
            app.submit(request("attempt-2"))
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_cancel_before_dispatch_has_no_effect(self):
        """The journal's serialized no-effect boundary suppresses a later worker."""
        app = self.create()
        row, _ = app.journal.admit(request(), app.validate)
        app.cancel(row["handle"])
        app.run(row["handle"], time.monotonic() + 1)
        self.assertEqual(app.status(row["handle"])["state"], "CANCELED")
        self.assertEqual((self.fixture.tasks, self.fixture.aborts), ([], []))

    def test_cancel_ack_does_not_prove_stop_or_release(self):
        """A completed abort stream while the original stream remains active stays Unknown."""
        self.fixture.mode = "blocked"
        app = self.create()
        row = app.submit(request())
        wait_for(lambda: app.status(row["handle"])["progress"].get("last_kind") == "final_text")
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: app.cancel(row["handle"]), range(12)))
        self.settled(row["handle"])
        wait_for(lambda: len(self.fixture.aborts) == 1)
        self.assertEqual(self.fixture.aborts[0].session_id, row["session_id"])
        self.assertNotEqual(self.fixture.aborts[0].task_id, row["task_id"])
        self.assertEqual(len(self.fixture.aborts), 1)
        with self.assertRaises(Conflict):
            app.submit(request("replacement"))

    def test_response_lost_after_effect_is_not_retried(self):
        """A server-side durable effect followed by UNAVAILABLE retains one dispatch."""
        self.fixture.mode = "lost-response"
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        self.assertEqual(app.submit(request())["handle"], row["handle"])
        self.assertEqual(len((self.root / "effects.jsonl").read_text().splitlines()), 1)

    def test_cancel_during_discovery_suppresses_native_task(self):
        """A cancellation arriving before endpoint resolution cannot leave a task queued to send."""
        self.fixture.query_delay = 0.2
        app = self.create()
        body = request()
        body["invocation"]["execution_generation"] = 1
        row = app.submit(body)
        wait_for(lambda: app.status(row["handle"])["dispatched"])
        app.cancel(row["handle"])
        wait_for(lambda: app.status(row["handle"])["state"] == "CANCELED")
        self.assertEqual(self.fixture.tasks, [])
        self.assertEqual(self.fixture.aborts, [])
        self.assertEqual(len(app.journal.no_effects_observations()["observations"]), 1)

    def test_cancel_waits_for_original_task_observation_before_abort(self):
        """A separate abort stream must not overtake an original task still awaiting acceptance."""
        self.fixture.first_event_delay = 0.3
        self.fixture.mode = "blocked"
        app = self.create()
        row = app.submit(request())
        wait_for(lambda: self.fixture.tasks)
        app.cancel(row["handle"])
        self.assertEqual(self.fixture.aborts, [])
        self.settled(row["handle"])
        wait_for(lambda: len(self.fixture.aborts) == 1)

    def test_empty_stream_is_unknown(self):
        """Pilot duplicate suppression and an ordinary empty stream cannot imply success."""
        self.fixture.mode = "empty"
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        self.assertEqual(app.status(row["handle"])["progress"].get("plan_ids", []), [])

    def test_foreign_session_events_cannot_change_ownership(self):
        """Wrong-session bytes fence the attempt; cleanup still names only its original session."""
        self.fixture.mode = "foreign"
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        wait_for(lambda: self.fixture.aborts)
        self.assertEqual(self.fixture.aborts[0].session_id, row["session_id"])
        self.assertEqual(app.status(row["handle"])["progress"].get("plan_ids", []), [])

    def test_provider_replacement_cannot_redirect_original_abort(self):
        """A new registration is not accepted as the owner of an old task session."""
        self.fixture.mode = "changed-provider"
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        wait_for(lambda: "registration changed" in app.status(row["handle"])["detail"])
        self.assertEqual(self.fixture.aborts, [])

    def test_budget_includes_discovery_and_never_refreshes(self):
        """A slow Query consumes the original budget before SubmitTask can send."""
        self.fixture.query_delay = 0.2
        app = self.create()
        row = app.submit(request(budget=100))
        self.settled(row["handle"])
        self.assertEqual(len(self.fixture.tasks), 0)
        self.assertEqual(app.submit(request(budget=100))["created_ns"], row["created_ns"])

    def test_event_overflow_is_bounded_and_retains_ambiguity(self):
        """Exhausted audit capacity cannot produce terminal success or an unbounded journal."""
        app = self.create(limits={"max_events": 2})
        row = app.submit(request())
        self.settled(row["handle"])
        self.assertEqual(len(app.journal.events(row["handle"])), 2)
        self.assertEqual(app.status(row["handle"])["state"], "UNKNOWN")

    def test_disk_latency_does_not_extend_original_dispatch_budget(self):
        """A slow durable send-boundary commit cannot restart the native RPC clock."""
        app = self.create()
        original = app.journal.begin

        def slow_begin(handle):
            """Model durable storage latency between budget calculation and native initiation."""
            time.sleep(0.15)
            return original(handle)

        app.journal.begin = slow_begin
        row = app.submit(request(budget=100))
        self.settled(row["handle"])
        self.assertEqual(self.fixture.tasks, [])

    def test_send_guard_wait_does_not_renew_native_budget(self):
        """Time waiting for final admission serialization is charged to the same original budget."""
        app = self.create()
        original = app.guard_send

        @contextmanager
        def delayed_guard(handle):
            """Model a queued final local admission check after discovery already completed."""
            time.sleep(0.15)
            with original(handle):
                yield

        app.guard_send = delayed_guard
        row = app.submit(request(budget=100))
        self.settled(row["handle"])
        self.assertEqual(self.fixture.tasks, [])

    def test_single_process_owner_and_profile_continuity(self):
        """Reject concurrent writers and refuse a different deployment on the same store."""
        app = self.create()
        with self.assertRaises(Conflict):
            Journal(self.root / "journal", {})
        app.close()
        self.app = None
        with self.assertRaises(Conflict):
            Journal(self.root / "journal", {"different": "deployment"})

    def test_inactive_provider_never_falls_back_to_a_fixed_port(self):
        """Registered but inactive discovery is unavailable without native execution."""
        self.fixture.active = False
        app = self.create()
        row = app.submit(request())
        self.settled(row["handle"])
        self.assertEqual(self.fixture.tasks, [])
        self.assertEqual(self.fixture.connections, [])

    def test_default_http_candidate_is_not_dispatch_ready(self):
        """An online process cannot advertise motion capability or execute arbitrary tasks."""
        self.app = Adapter(
            self.root / "journal",
            Liaison(self.fixture.endpoint, "rg/fixture", "local:fixture"),
        )
        server = Server(0, self.app)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            port = server.server_address[1]
            self.assertEqual(http(port, "/v1/health")[1]["state"], "ONLINE")
            self.assertEqual(http(port, "/v1/readiness")[1]["state"], "UNAVAILABLE")
            self.assertEqual(http(port, "/v1/executions", request())[0], 400)
            self.assertEqual(http(port, "/v1/executions/status", {"handle": "unknown"})[0], 404)
            self.assertEqual(self.fixture.tasks, [])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_real_http_process_kill_and_restart_never_replays(self):
        """Kill a serving process after one fake effect, reopen SQLite and query the same handle."""
        self.fixture.mode = "blocked"
        port_file = self.root / "port.txt"
        directory = self.root / "process-journal"
        children = []
        try:
            for index in range(2):
                # Each child writes a separate startup marker to avoid trusting an old port.
                marker = port_file.with_name(f"port-{index}.txt")
                child = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        str(directory),
                        self.fixture.endpoint,
                        str(marker),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                children.append(child)
                wait_for(marker.exists)
                port = int(marker.read_text())
                if index == 0:
                    code, original = http(port, "/v1/executions", request(budget=30000))
                    self.assertEqual(code, 200)
                    wait_for(lambda: len(self.fixture.tasks) == 1)
                    wait_for(
                        lambda port=port, original=original: (
                            http(
                                port,
                                "/v1/executions/status",
                                {"handle": original["handle"]},
                            )[1]["progress"].get("event_count", 0)
                            >= 4
                        )
                    )
                    child.kill()
                    child.communicate(timeout=5)
                else:
                    code, restored = http(port, "/v1/executions/find", {"attempt_id": "attempt-1"})
                    self.assertEqual(code, 200)
                    self.assertEqual(restored["state"], "UNKNOWN")
                    for key in (
                        "handle",
                        "task_id",
                        "session_id",
                        "created_ns",
                        "digest",
                    ):
                        self.assertEqual(restored[key], original[key])
                    code, retry = http(port, "/v1/executions", request(budget=30000))
                    self.assertEqual((code, retry["handle"]), (200, original["handle"]))
                    self.assertEqual(len(self.fixture.tasks), 1)
                    self.assertEqual(self.fixture.aborts, [])
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)


def fixture_worker(directory, endpoint, marker):
    """Run only a fake loopback peer, allowing parent tests to kill the actual HTTP process."""
    host = endpoint.rsplit(":", 1)[0]
    if host != "127.0.0.1":
        raise ValueError("fixture worker refuses non-loopback endpoints")
    app = Adapter(
        directory,
        Liaison(endpoint, "rg/fixture-process", "local:fixture"),
        allowed_operations=("lab.protocol-probe@v1",),
        enabled=True,
    )
    server = Server(0, app)
    Path(marker).write_text(str(server.server_address[1]))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        fixture_worker(*sys.argv[2:])
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        Path("acceptance.json").write_text(
            json.dumps(
                {
                    "scope": "offline real gRPC/HTTP/process fixtures; zero robot calls",
                    "platform": sys.platform,
                    "python": sys.version,
                    "tests": result.testsRun,
                    "failures": len(result.failures),
                    "errors": len(result.errors),
                    "passed": result.wasSuccessful(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        sys.exit(not result.wasSuccessful())
