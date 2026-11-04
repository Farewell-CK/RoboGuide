"""Exercise native task attribution through real isolated Atlas and Liaison RPCs."""

import json
import tempfile
import time
import unittest
from pathlib import Path

import grpc
from robonix_task.adapter import Adapter
from robonix_task.protocol import Liaison, event_dict, message, task
from test_protocol import NativeFixture, request, wait_for


class TaskFixture(NativeFixture):
    """Finish an original task after its last plan, with optional malformed evidence."""

    def submit(self, req, context):
        """Emit a real protobuf turn receipt and task result without robot operations."""
        if json.loads(req.context_json).get("abort_turn"):
            for raw in super().submit(req, context):
                if self.mode in {
                    "planning-error",
                    "finished-plan-error",
                    "running-plan-error",
                    "failed-plan-error",
                    "canceled-plan-error",
                    "skipped-plan-error",
                }:
                    yield self.encode(
                        message(
                            "robonix.pilot.PilotEvent",
                            event_kind=3,
                            session_id=req.session_id,
                            status={
                                "session_id": req.session_id,
                                "state": 1,
                                "message": "no active turn or running plan exists for this session",
                            },
                        )
                    )
                else:
                    yield raw
            return
        if self.mode == "planning-error":
            yield next(super().submit(req, context))
            context.abort(grpc.StatusCode.INTERNAL, "truncated JSON response")
        if self.mode in {"planning-cancel", "planning-cancel-with-plan"}:
            yield next(super().submit(req, context))
            if self.mode == "planning-cancel-with-plan":
                yield self.encode(
                    message(
                        "robonix.pilot.PilotEvent",
                        event_kind=1,
                        session_id=req.session_id,
                        plan={"plan_id": "running-plan", "session_id": req.session_id},
                    )
                )
            while not self.aborts and context.is_active():
                time.sleep(0.01)
            yield self.encode(
                message(
                    "robonix.pilot.PilotEvent",
                    event_kind=3,
                    session_id=req.session_id,
                    status={"session_id": req.session_id, "state": 2, "message": "interrupted"},
                )
            )
            return
        for raw in super().submit(req, context):
            if event_dict(raw)["kind"] != "final_text":
                yield raw
        if self.mode in {
            "finished-plan-error",
            "failed-plan-error",
            "canceled-plan-error",
            "skipped-plan-error",
        }:
            root_state = {
                "finished-plan-error": 2,
                "failed-plan-error": 3,
                "canceled-plan-error": 4,
                "skipped-plan-error": 5,
            }[self.mode]
            for plan_id in ("plan-1", "plan-2"):
                yield self.encode(
                    message(
                        "robonix.pilot.PilotEvent",
                        event_kind=5,
                        session_id=req.session_id,
                        node_state={"plan_id": plan_id, "node_index": 0, "state": root_state},
                    )
                )
            context.abort(grpc.StatusCode.INTERNAL, "pilot stream error: truncated JSON response")
        if self.mode == "running-plan-error":
            context.abort(grpc.StatusCode.INTERNAL, "pilot stream error: truncated JSON response")
        task_state = {
            "task_id": "T1",
            "status": "done",
            "tasks": [
                {
                    "task_id": "T1",
                    "status": "done",
                    "message_ids": ["m1"],
                    "plan_ids": ["plan-1", "plan-2"],
                    "status_reason": "native fixture result",
                }
            ],
        }
        if self.mode == "failed-task":
            task_state["status"] = "failed"
            task_state["tasks"][0]["status"] = "failed"
        if self.mode == "foreign-message":
            task_state["tasks"][0]["message_ids"] = ["m2"]
        if self.mode == "still-running":
            task_state["status"] = "in_progress"
            task_state["tasks"][0]["status"] = "in_progress"
        if self.mode == "missing-plan":
            task_state["tasks"][0]["plan_ids"].append("unobserved-plan")
        if self.mode != "stale-task":
            if self.mode != "plan-running":
                for plan_id in ("plan-1", "plan-2"):
                    yield self.encode(
                        message(
                            "robonix.pilot.PilotEvent",
                            event_kind=5,
                            session_id=req.session_id,
                            node_state={"plan_id": plan_id, "node_index": 0, "state": 2},
                        )
                    )
            yield self.encode(
                message(
                    "robonix.pilot.PilotEvent",
                    event_kind=6,
                    session_id=req.session_id,
                    task_state=task_state,
                )
            )
        if self.mode != "no-final-boundary":
            yield self.encode(
                message(
                    "robonix.pilot.PilotEvent",
                    event_kind=4,
                    session_id=req.session_id,
                    final_text="fixture result",
                )
            )


class TaskResults(unittest.TestCase):
    """Test task-result policy and conservative uncertainty at the real RPC boundary."""

    def run_case(self, mode, expected):
        """Submit once and return retained results after all owned workers detach."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = TaskFixture(root, mode=mode)
            app = Adapter(
                root / "journal",
                Liaison(fixture.endpoint, "rg/test", "local:test"),
                allowed_operations=("lab.protocol-probe@v1",),
                enabled=True,
                task_results=True,
                task_profile="test-profile",
            )
            try:
                body = request()
                body["invocation"] = body["invocation"]["intent"]
                body["invocation"]["operation"] = "lab.protocol-probe@v1"
                body["invocation"]["parameters"] = {"profile": "test-profile"}
                self.assertEqual(app.readiness()["state"], "READY")
                first = app.submit(body)
                wait_for(lambda: app.status(first["handle"])["state"] == expected)
                repeated = app.submit(body)
                self.assertEqual(first["handle"], repeated["handle"])
                self.assertEqual(len(fixture.tasks), 1)
                result = app.status(first["handle"])
                events = [event_dict(row["raw"]) for row in app.journal.events(first["handle"])]
                self.assertTrue(events)
                return result
            finally:
                app.close()
                fixture.close()

    def test_success_and_failure_follow_native_task(self):
        """Task status determines the result even when a previous local plan failed."""
        for mode, expected in (("complete", "SUCCEEDED"), ("failed-task", "FAILED")):
            with self.subTest(mode=mode):
                result = self.run_case(mode, expected)
                self.assertEqual(json.loads(result["detail"])["pilot_task_id"], "T1")
                self.assertFalse(result["abort_sent"])

    def test_incomplete_or_foreign_task_retains_unknown(self):
        """Text, EOF, stale task snapshots and foreign messages never complete a task."""
        for mode in (
            "foreign-message",
            "still-running",
            "stale-task",
            "no-final-boundary",
            "plan-running",
            "missing-plan",
        ):
            with self.subTest(mode=mode):
                self.run_case(mode, "UNKNOWN")

    def test_native_preplan_error_with_idle_receipt_is_failed(self):
        """A native parser failure with zero plans and scoped idle receipt releases its task."""
        result = self.run_case("planning-error", "FAILED")
        detail = json.loads(result["detail"])
        self.assertEqual(detail["basis"], "robonix-preplan-failure-and-idle/v1")
        self.assertTrue(detail["native_idle_confirmed"])

    def test_cancel_original_turn_before_plan(self):
        """Original interruption closes only a canceled turn with no possible emitted plan."""
        for mode, expected in (
            ("planning-cancel", "CANCELED"),
            ("planning-cancel-with-plan", "UNKNOWN"),
        ):
            with self.subTest(mode=mode):
                self.check_canceled_turn(mode, expected)

    def check_canceled_turn(self, mode, expected):
        """Cancel through the real RPC and preserve uncertain plan-bearing work."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TaskFixture(tmp, mode=mode)
            app = Adapter(
                Path(tmp) / "journal",
                Liaison(fixture.endpoint, "rg/test", "local:test"),
                allowed_operations=("lab.protocol-probe@v1",),
                enabled=True,
                task_results=True,
            )
            try:
                row = app.submit(request())
                wait_for(lambda: app.status(row["handle"])["progress"].get("turn_id"))
                if mode == "planning-cancel-with-plan":
                    wait_for(lambda: app.status(row["handle"])["progress"].get("plan_ids"))
                app.cancel(row["handle"])
                wait_for(lambda: app.status(row["handle"])["state"] == expected)
                self.assertEqual(len(fixture.tasks), 1)
                self.assertEqual(len(fixture.aborts), 1)
                if expected == "CANCELED":
                    self.assertEqual(app.status(row["handle"])["progress"]["plan_ids"], [])
            finally:
                app.close()
                fixture.close()

    def test_postplan_failure_requires_all_plan_roots_succeeded(self):
        """Idle and a model error cannot close failed, canceled or unresolved native plans."""
        completed = self.run_case("finished-plan-error", "FAILED")
        self.assertEqual(
            json.loads(completed["detail"])["completed_plan_ids"], ["plan-1", "plan-2"]
        )
        for mode in (
            "running-plan-error",
            "failed-plan-error",
            "canceled-plan-error",
            "skipped-plan-error",
        ):
            with self.subTest(mode=mode):
                self.run_case(mode, "UNKNOWN")

    def test_profile_rejected_before_native_send(self):
        """A deployment profile mismatch cannot become an accepted local execution."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TaskFixture(tmp)
            app = Adapter(
                Path(tmp) / "journal",
                Liaison(fixture.endpoint, "rg/test", "local:test"),
                allowed_operations=("lab.protocol-probe@v1",),
                enabled=True,
                task_results=True,
                task_profile="test-profile",
            )
            try:
                with self.assertRaisesRegex(ValueError, "profile"):
                    app.submit(request())
                self.assertEqual(fixture.tasks, [])
            finally:
                app.close()
                fixture.close()

    def test_reconcile_retained_final_task_after_parser_failure(self):
        """Replay only stored result bytes after an earlier decoder left the task unknown."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TaskFixture(tmp)
            app = Adapter(
                Path(tmp) / "journal",
                Liaison(fixture.endpoint, "rg/test", "local:test"),
                allowed_operations=("lab.protocol-probe@v1",),
                enabled=True,
            )
            try:
                row = app.submit(request())
                wait_for(
                    lambda: (
                        app.status(row["handle"])["state"] == "UNKNOWN"
                        and app.status(row["handle"])["abort_sent"]
                    )
                )
                wait_for(
                    lambda: any(r["source"] == "abort" for r in app.journal.events(row["handle"]))
                )
                self.assertFalse(app.journal.finish_task(row["handle"], reconcile=True))
                # This exact no-active-work receipt is distinct from an accepted stop request.
                original = app.journal.get(row["handle"])
                event = message(
                    "robonix.pilot.PilotEvent",
                    event_kind=3,
                    session_id=original["session_id"],
                    status={
                        "session_id": original["session_id"],
                        "state": 1,
                        "message": "no active turn or running plan exists for this session",
                    },
                )
                raw = event.SerializeToString()
                app.journal.append(
                    row["handle"], "abort", raw, event_dict(raw), original["progress"]["provider"]
                )
                self.assertTrue(app.journal.finish_task(row["handle"], reconcile=True))
                self.assertEqual(app.status(row["handle"])["state"], "SUCCEEDED")
                self.assertEqual(len(fixture.tasks), 1)
            finally:
                app.close()
                fixture.close()

    def test_retained_native_result_reconciles_without_resubmit(self):
        """A parser fence can consume its original terminal evidence while preserving history."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TaskFixture(tmp, mode="complete")
            app = Adapter(
                Path(tmp) / "journal",
                Liaison(fixture.endpoint, "rg/test", "local:test"),
                allowed_operations=("lab.protocol-probe@v1",),
                enabled=True,
                task_results=True,
            )
            try:
                row, created = app.journal.admit(request(), app.validate)
                self.assertTrue(created)
                app.journal.begin(row["handle"])
                provider = {"provider_id": "fixture", "registration_id": "original"}
                app.journal.bind_provider(row["handle"], provider)
                for raw in fixture.submit(task(row, "local:test"), None):
                    app.journal.append(row["handle"], "task", raw, event_dict(raw), provider)
                app.journal.mark_unknown(row["handle"], "old parser required omitted Completed")
                self.assertFalse(app.journal.finish_task(row["handle"]))
                self.assertTrue(app.journal.finish_task(row["handle"], reconcile=True))
                result = app.journal.get(row["handle"])
                self.assertEqual(result["state"], "SUCCEEDED")
                self.assertIn("old parser", json.loads(result["detail"])["previous_detail"])
                self.assertFalse(app.journal.finish_task(row["handle"], reconcile=True))
                self.assertEqual(len(fixture.tasks), 1)
            finally:
                app.close()
                fixture.close()


if __name__ == "__main__":
    unittest.main()
