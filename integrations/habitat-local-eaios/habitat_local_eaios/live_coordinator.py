"""Relay Control-delivered live dispatches over one persistent original shared world."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .backend import LocalExecutionOutcome
from .endpoint_registry import LIVE_PROFILE, load_endpoint_registry
from .live_session import LiveSlotLedger
from .model import CanonicalInvocation, IntegrationError
from .shared_world import SharedWorldCoordinator

_LOG = logging.getLogger(__name__)


class LiveWorldCoordinator(SharedWorldCoordinator):
    """Project actual dispatches and local outcomes without scheduling or a pairing barrier."""

    def __init__(
        self, world: Any, registry_path: Path, wait_seconds: float, evidence_dir: Path
    ) -> None:
        """Start exactly one child world on an owned thread with bounded assignment waiting."""
        import threading

        if not 0 < wait_seconds <= 86400:
            raise IntegrationError("live assignment wait must be in (0, 86400]")
        self._world, self._evidence_dir = world, evidence_dir
        self.registry = load_endpoint_registry(registry_path)
        self._ledger = LiveSlotLedger(
            tuple(record["agent_id"] for record in self.registry["endpoints"])
        )
        self._wait_seconds = wait_seconds
        self._condition = threading.Condition()
        self._closing = threading.Event()
        self._retain_stopped_session = False
        self._queue: list[tuple[Any, str]] = []
        self._entries: dict[int, tuple[Any, str]] = {}
        self._history: list[dict[str, Any]] = []
        self._finished = False
        self._initialization_error: str | None = None
        self._worker = threading.Thread(
            target=self._run, name="shared-world-live-coordinator", daemon=True
        )
        self._worker.start()

    def health(self) -> dict[str, object]:
        """Report actual child readiness without implying an official episode outcome."""
        return {
            "state": "ONLINE" if self.runtime_ready() else "OFFLINE",
            "detail": self.readiness_detail(),
        }

    def runtime_ready(self) -> bool:
        """Keep startup failures offline and never respawn a consumed world."""
        return self._initialization_error is None and self._world.is_ready()

    def readiness_detail(self) -> str:
        """Describe the immutable deployment or the actual initialization failure."""
        return self._initialization_error or str(self._world.readiness_detail())

    def supported_operations(self) -> tuple[str, ...]:
        """Return only loaded-world operations, independently of individual Node profiles."""
        return tuple(self._world.supported_operations())

    def operations_for(self, agent: int) -> tuple[str, ...]:
        """Intersect exact configured endpoint support with loaded runtime readiness."""
        if agent not in self._ledger.agent_ids:
            return ()
        return tuple(
            name
            for name in self.registry["endpoints"][agent]["operations"]
            if name in self.supported_operations()
        )

    def deployment_contract(self) -> dict[str, object]:
        """Expose bounded live topology facts, not Mission semantics or placement authority."""
        return {
            "schema_version": "roboguide.e1.shared-world-start-admission/v0.3",
            "execution_profile": LIVE_PROFILE,
            "endpoint_count": len(self._ledger.agent_ids),
            "endpoint_registry_digest": self.registry["digest"],
            "all_endpoint_barrier": False,
            "sequential_endpoint_reuse_supported": True,
            "assignment_wait_seconds": self._wait_seconds,
        }

    def can_accept(self, endpoint: Any, invocation: CanonicalInvocation) -> bool:
        """Check immutable session compatibility without reserving a physical executor."""
        from copy import deepcopy

        with self._condition:
            if (
                self._finished
                or self._closing.is_set()
                or not self.runtime_ready()
                or invocation.operation not in self.operations_for(endpoint.agent_id)
            ):
                return False
            try:
                trial = deepcopy(self._ledger)
                trial.admit(endpoint.agent_id, invocation)
            except IntegrationError:
                return False
            return True

    def record_arrival(self, node_name: str, invocation: CanonicalInvocation) -> None:
        """Retain the real assignment identity before execution dispatch."""
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        with (self._evidence_dir / "assignment-arrival.jsonl").open(
            "a", encoding="utf-8"
        ) as output:
            output.write(
                json.dumps(
                    {"node": node_name, "invocation": invocation.as_dict(), "unix": time.time()},
                    sort_keys=True,
                )
                + "\n"
            )

    def submit(self, endpoint: Any, execution_id: str) -> None:
        """Queue only the endpoint's durable handle and wake a step-boundary receiver."""
        execution = endpoint.store().get(execution_id)
        if execution is None:
            raise IntegrationError("live dispatch has no durable accepted execution")
        with self._condition:
            if self._finished or self._closing.is_set():
                endpoint.store().mark_failed(execution_id, "shared world has ended")
                return
            try:
                self._ledger.admit(endpoint.agent_id, execution["invocation"])
            except IntegrationError as error:
                endpoint.store().mark_failed(execution_id, str(error))
                raise
            self._entries[endpoint.agent_id] = (endpoint, execution_id)
            self._queue.append((endpoint, execution_id))
            self._condition.notify_all()

    def episode_consumed(self) -> bool:
        """Expose local session consumption without granting ownership or success."""
        with self._condition:
            return self._ledger.session is not None

    def _take_arrivals(self) -> dict[int, CanonicalInvocation]:
        """Drain a bounded set of admitted commands; no adapter-authored work is created."""
        with self._condition:
            queued, self._queue = self._queue, []
            return {
                endpoint.agent_id: endpoint.store().get(handle)["invocation"]
                for endpoint, handle in queued
            }

    def _cancelled(self) -> bool:
        """Observe actual group-wide cancel requests without issuing new commands."""
        with self._condition:
            return self._closing.is_set() or any(
                endpoint.store().cancellation_requested(handle)
                for endpoint, handle in self._entries.values()
            )

    def _running(self, agent: int, detail: str) -> None:
        """Publish only the child's observed activation for the current durable handle."""
        with self._condition:
            endpoint, handle = self._entries[agent]
            execution = endpoint.store().get(handle)
            if execution is not None and execution["state"] == "ACCEPTED":
                endpoint.store().mark_running(handle, detail)

    def _completed(
        self, agent: int, invocation: CanonicalInvocation, outcome: LocalExecutionOutcome
    ) -> None:
        """Preserve exact attempt attribution before exposing a local terminal fact."""
        with self._condition:
            endpoint, handle = self._entries[agent]
            execution = endpoint.store().get(handle)
            if execution is None or execution["invocation"] != invocation:
                raise IntegrationError("live outcome is not bound to the current endpoint attempt")
            if self._ledger.active.get(agent) != invocation:
                raise IntegrationError("live outcome is duplicated or stale")
            self._ledger.finish(agent)
            self._history.append(
                {
                    "invocation": invocation.as_dict(),
                    "node": endpoint.name,
                    "local_execution_id": handle,
                    "outcome": outcome.as_dict(),
                }
            )
            self._write_state("running")
            endpoint.store().mark_terminal(handle, outcome)

    def _write_state(self, state: str) -> None:
        """Archive exact local facts best-effort; missing archival never rewrites execution."""
        try:
            self._evidence_dir.mkdir(parents=True, exist_ok=True)
            (self._evidence_dir / "live-session-state.json").write_text(
                json.dumps(
                    {
                        "schema_version": "roboguide.shared-world-live-session/v0.1",
                        "execution_profile": LIVE_PROFILE,
                        "endpoint_registry_digest": self.registry["digest"],
                        "state": state,
                        "session": self._ledger.session.as_dict()
                        if self._ledger.session is not None
                        else None,
                        "task_outcomes": self._history,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            _LOG.exception("live local evidence archival unavailable")

    def _run(self) -> None:
        """Own one world lifecycle; infrastructure errors never trigger a replacement reset."""
        try:
            self._world.start()
        except Exception as error:
            self._initialization_error = str(error)
            return
        try:
            with self._condition:
                while not self._queue and not self._closing.is_set():
                    self._condition.wait(timeout=0.2)
            if self._closing.is_set():
                return
            outcomes, summary = self._world.run_live(
                self._take_arrivals(),
                self._take_arrivals,
                self._cancelled,
                self._running,
                self._completed,
                self._wait_seconds,
            )
            with self._condition:
                self._finished = True
                invocations = [value for _, value in self._ledger.seen.values()]
            # Reuse the same official summary/verifier projection, not a new success rule.
            self._publish_summary_best_effort(outcomes, summary, serial_task_outcomes=self._history)
            self._publish_verifier_verdict_best_effort(summary, invocations)
            self._write_state("ended")
        except Exception as error:
            with self._condition:
                self._finished = True
                for agent in list(self._ledger.active):
                    endpoint, handle = self._entries[agent]
                    current = endpoint.store().get(handle)
                    if current is not None and current["state"] not in {
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                    }:
                        endpoint.store().mark_failed(handle, f"shared live world failed: {error}")
                self._write_state("failed")
            try:
                failure = json.loads((self._evidence_dir / "live-world-failure.json").read_bytes())
                self._publish_summary_best_effort({}, failure, serial_task_outcomes=self._history)
                self._publish_verifier_verdict_best_effort(
                    failure, [value for _, value in self._ledger.seen.values()]
                )
            except Exception:
                _LOG.exception("live world failure summary unavailable")
        finally:
            with self._condition:
                self._finished = True
                for agent in list(self._ledger.active):
                    endpoint, handle = self._entries[agent]
                    current = endpoint.store().get(handle)
                    if current is not None and current["state"] not in {
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                    }:
                        endpoint.store().mark_failed(
                            handle, "shared world ended before this dispatch activated"
                        )

    def _write_json(self, name: str, value: object) -> None:
        """Write deployment evidence without changing original vendor files."""
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        (self._evidence_dir / name).write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def shutdown(self) -> None:
        """Stop only this owned session and child process, never a neighboring experiment."""
        self._closing.set()
        with self._condition:
            self._condition.notify_all()
        self._worker.join(timeout=30)
        self._world.shutdown()
        self._worker.join(timeout=5)
