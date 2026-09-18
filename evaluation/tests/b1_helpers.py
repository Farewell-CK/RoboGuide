"""Offline B1 artifacts using the real Mission Engine and HTTP submission adapter."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.controller import HttpMissionController
from mission.grounding_context import GroundingContextSnapshot, dialogue_digest
from mission.models import MissionPlan
from mission.request_engine import MissionRequestEngine
from mission.request_record import DialogueTurn, IntentAssessment
from mission.request_store import MissionRequestStore
from mission.review import MissionPlanReview, MissionReviewIssue, ReviewIssueAction
from mission.semantic_evidence import AuthoritativeSemanticEvidence, SemanticExpression
from roboguide_eval.b1_provenance import build_b1_provenance_record, write_b1_provenance

ROOT = Path(__file__).resolve().parents[2]
INSTRUCTION = "Two robots cover both goals — deterministic offline observation."


def write_json(path: Path, document: Any) -> None:
    """Persist one test artifact in the same JSON shape as production."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


class StaticSemanticGroundingReader:
    """Supply one immutable adapter snapshot to every MI phase in an offline run."""

    def __init__(self, evidence: AuthoritativeSemanticEvidence) -> None:
        """Retain the exact semantic evidence object shared by the lifecycle."""
        self._evidence = evidence

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Create one request-bound context without reading Control or Node state."""
        return GroundingContextSnapshot.create(
            request_id=request_id,
            dialogue_digest=dialogue_digest(tuple(turn.to_json() for turn in dialogue)),
            captured_at_ms=captured_at_ms,
            semantic_evidence=self._evidence,
        )


@contextmanager
def controller_server(status: int = 202) -> Iterator[tuple[str, list[bytes]]]:
    """Capture real POST bytes at an offline Controller-shaped HTTP endpoint."""
    bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        """Record HTTP requests without invoking a simulator or Control authority."""

        def do_POST(self) -> None:
            """Return the production Controller identity shape after recording bytes."""
            assert self.path == "/v1/missions"
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            bodies.append(raw)
            plan = json.loads(raw)
            mission_id = plan["mission"]["id"]
            response = (
                {"mission_id": mission_id, "group_id": "group-" + mission_id, "status": "Running"}
                if status == 202
                else {"error": "Control rejected assignment"}
            )
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            """Keep deterministic tests free of server access logs."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def make_run(
    root: Path, *, case: str = "A", repaired: bool = False, omit_second_goal: bool = False
) -> Path:
    """Build evidence through actual MI orchestration and HTTP boundary, stopping on failure."""
    run = root / f"run-{case}"
    run.mkdir(parents=True)
    write_json(run / "b1-input-used.json", {"instruction": INSTRUCTION, "episode_id": "51"})
    semantic = AuthoritativeSemanticEvidence.create(
        run_id=run.name,
        episode_id="51",
        revision="goal-revision-1",
        goal=SemanticExpression.logical(
            "and",
            (
                SemanticExpression.predicate("any_at", ("any_targets|0",)),
                SemanticExpression.predicate("any_at", ("TARGET_any_targets|0",)),
            ),
        ),
        world_context={"scene_id": "scene-51", "agent_ids": [0, 1], "entity_catalog": []},
    )
    write_json(run / "evidence/authoritative-semantic-evidence.json", semantic.to_json())
    raw = json.loads((ROOT / "scenarios/e1-shared-world-episode-51/mission-plan.json").read_text())
    raw["mission"]["objective"] = INSTRUCTION
    raw["tasks"][0]["description"] = "offline MI-generated task"
    if omit_second_goal:
        raw["tasks"][1]["description"] = "offline MI-generated task with omitted goal coverage"
        raw["tasks"][1]["roles"][0]["execution_intent"]["parameters"]["destination"] = (
            "unrelated-target"
        )
        raw["tasks"][1]["satisfaction"]["expected_effect"] = (
            "The robot completed the unrelated target operation."
        )
    planner = Mock()
    interpreter = Mock(interpret=Mock(return_value=IntentAssessment(INSTRUCTION, (), (), ())))

    def generate(**kwargs: Any) -> MissionPlan:
        """Generate a canonical fixture plan using the engine-owned Mission identity."""
        raw["mission"]["id"] = kwargs["mission_id"]
        return MissionPlan.from_json(raw)

    planner.plan.side_effect = RuntimeError("model output invalid") if case == "D" else generate
    reviewer = Mock()
    reviewer.review.side_effect = [
        MissionPlanReview(
            False,
            (
                MissionReviewIssue(
                    "objective_fix", "/tasks/0", "repair wording", ReviewIssueAction.REPAIR_PLAN
                ),
            ),
        ),
        MissionPlanReview(True, ()),
    ]
    repairer = Mock()

    def repair(*args: Any) -> MissionPlan:
        """Change plan content so stale pre-repair digests cannot match the final draft."""
        updated = args[2].to_json()
        updated["tasks"][0]["description"] = "repaired description"
        return MissionPlan.from_json(updated)

    repairer.repair.side_effect = repair
    with controller_server(409 if case == "C-rejected" else 202) as (endpoint, bodies):
        engine = MissionRequestEngine(
            MissionRequestStore(run / "mi.sqlite3"),
            interpreter,
            planner,
            HttpMissionController(endpoint, 2),
            CanonicalCapabilityCatalog.load(ROOT / "contracts/capability/v0.3/catalog.json"),
            frozenset(),
            reviewer=reviewer if repaired else None,
            repairer=repairer if repaired else None,
            max_repair_attempts=1 if repaired else 0,
            grounding_reader=StaticSemanticGroundingReader(semantic),
        )
        record = engine.create(INSTRUCTION)
        write_json(run / "b1-request-record.json", record.to_json())
        write_json(run / "b1-request-observations.json", record.observations().to_json())
        if bodies:
            (run / "actual-controller-body.json").write_bytes(bodies[-1])
    request = record.to_json()
    if case == "D":
        assert request["lifecycle"] == "Failed"
        assert record.failure_evidence and record.failure_evidence["failure_owner"] == "MODEL"
    elif case == "C-rejected":
        assert request["lifecycle"] == "Blocked"
    else:
        assert request["lifecycle"] == "Accepted", request["issues"]
        mission_id = record.mission_id
        group_id = "group-" + mission_id
        write_json(
            run / "mission.json",
            {
                "mission_id": mission_id,
                "group_id": group_id,
                "status": "Failed" if case == "C" else "Completed",
                "tasks": [],
            },
        )
        task_ids = [task["id"] for task in raw["tasks"]]
        events: list[dict[str, Any]] = [
            {"payload": {"ExecutionGroupCreated": {"mission_id": mission_id, "group_id": group_id}}}
        ]
        events.extend(
            {
                "payload": {
                    "TaskExecutionRegistered": {
                        "group_id": group_id,
                        "task_ref": {"mission_id": mission_id, "task_id": task_id},
                        "context_id": "ctx",
                    }
                }
            }
            for task_id in task_ids
        )
        write_json(run / "events.json", {"events": events})
        write_json(
            run / "execution-attempts.json",
            {
                "attempts": []
                if case == "C"
                else [
                    {
                        "execution_id": f"exec-{index}",
                        "mission_id": mission_id,
                        "group_id": group_id,
                        "task_id": task_id,
                        "role_id": "role",
                        "node_id": "node",
                        "status": "Completed",
                    }
                    for index, task_id in enumerate(task_ids)
                ]
            },
        )
    if case in {"A", "B", "F", "E"}:
        write_json(
            run / "evidence/shared-world-summary.json",
            {
                "official_pddl_success": case != "B",
                "identity": {"episode_id": "51", "episode_terminated": True},
                "outcomes": {},
            },
        )
    if case == "E":
        write_json(
            run / "run-failure.json",
            {
                "schema_version": "roboguide.e1.run-failure/v0.1",
                "run_id": run.name,
                "failure_owner": "EXTERNAL_INFRA",
                "component": "host",
                "reason": "host storage corrupted",
            },
        )
    write_json(
        run / "manifest.json",
        {
            "experiment_id": "offline-protocol",
            "system": "roboguide",
            "run_id": run.name,
            "episode_id": "51",
            "seed": 40,
            "process_status": "completed",
            "exit_code": 0,
        },
    )
    build_provenance(run)
    if case == "F":
        record_json = json.loads((run / "b1-provenance.json").read_text())
        record_json["accepted_plan_digest"] = "0" * 64
        write_json(run / "b1-provenance.json", record_json)
    return run


def build_provenance(run: Path) -> None:
    """Persist v0.3 links before invoking any verifier or metrics consumer."""
    record = build_b1_provenance_record(
        run_id=run.name,
        frozen_input_path=run / "b1-input-used.json",
        request_record_path=run / "b1-request-record.json",
        controller_mission_path=run / "mission.json",
        controller_events_path=run / "events.json",
        execution_attempts_path=run / "execution-attempts.json",
        shared_world_summary_path=run / "evidence/shared-world-summary.json",
        semantic_evidence_path=run / "evidence/authoritative-semantic-evidence.json",
        failure_evidence_path=run / "run-failure.json",
    )
    write_b1_provenance(record, run / "b1-provenance.json")
