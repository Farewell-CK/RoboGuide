"""Replay exact archived planning inputs and drafts without any live service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mission.grounding_context import GroundingContextSnapshot
from mission.models import JSONObject
from mission.provider_mission_plan import normalize_mission_plan_provider_output
from mission.request_engine import MissionRequestEngine
from mission.request_record import DialogueTurn, IntentAssessment
from mission.request_store import MissionRequestStore
from mission.responses import ResponsesMissionPlanner, _validate_plan_output
from mission.submission_evidence import canonical_plan_digest
from test_planners import (
    FakeTransport,
    _current_catalog,
    _current_schema,
    _local_settings,
    _provider_plan,
    _response,
    _shared_world_execution_profile,
)
from test_requests import FakeController, FakeInterpreter, _inventory

FIXTURE = Path(__file__).parent / "fixtures" / "coordination-history.json"


def _case(name: str) -> JSONObject:
    """Load a fresh exact evidence extract so mutation cannot contaminate another replay."""
    return cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8"))[name])


@pytest.mark.parametrize("name", ["A", "B"])
def test_historical_independent_plans_pass_full_mi_validation(name: str) -> None:
    """Accepted plans pass identity, profile, support, grounding, Catalog and policy."""
    case = _case(name)
    raw = cast(JSONObject, case["plan"])
    assert canonical_plan_digest(raw) == case["plan_digest"]
    mission = cast(JSONObject, raw["mission"])
    plan = _validate_plan_output(
        raw,
        cast(str, mission["id"]),
        IntentAssessment.from_json(cast(JSONObject, case["assessment"])).grounded_intent(),
        _current_catalog(),
        _local_settings().satisfaction_policy,
        GroundingContextSnapshot.from_json(case["grounding_context"]),
        _shared_world_execution_profile(),
    )
    assert plan.to_json() == raw
    assert all(context.coupling_mode == "independent" for context in plan.contexts)
    assert all(context.shared_view is None and not context.relations for context in plan.contexts)
    assert all(not task.depends_on for task in plan.tasks)


class ArchivedGroundingReader:
    """Supply one immutable historical snapshot, never a live inventory or source."""

    def __init__(self, snapshot: GroundingContextSnapshot) -> None:
        """Retain the exact snapshot for engine dialogue-binding validation."""
        self.snapshot = snapshot
        self.calls = 0

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Return archived evidence; the real engine checks Request and dialogue identity."""
        del dialogue, captured_at_ms
        assert request_id == self.snapshot.request_id
        self.calls += 1
        return self.snapshot


def test_three_historical_drafts_fail_without_rewriting_or_submission(tmp_path: Path) -> None:
    """Real adapters and engine retain every reject across retries and durable-store reload."""
    case = _case("D")
    drafts = cast(list[JSONObject], case["rejected_drafts"])
    transport = FakeTransport([_response(cast(JSONObject, d["provider_output"])) for d in drafts])
    profile = _shared_world_execution_profile()
    planner = ResponsesMissionPlanner(
        _local_settings(), {"OPENAI_API_KEY": "test-only-key"}, transport, profile
    )
    reader = ArchivedGroundingReader(GroundingContextSnapshot.from_json(case["grounding_context"]))
    controller = FakeController(_inventory())
    tokens = iter([cast(str, case[key]).split("-", 1)[1] for key in ("request_id", "mission_id")])
    dialogue = cast(list[JSONObject], case["dialogue"])
    store_path = tmp_path / "replay.sqlite3"
    engine = MissionRequestEngine(
        MissionRequestStore(store_path),
        FakeInterpreter([IntentAssessment.from_json(cast(JSONObject, case["assessment"]))]),
        planner,
        controller,
        _current_catalog(),
        frozenset(),
        id_generator=lambda: next(tokens),
        clock=lambda: cast(int, dialogue[0]["created_at_ms"]),
        grounding_reader=reader,
        prevalidation_recovery_attempts=2,
    )
    record = engine.create(cast(str, dialogue[0]["content"]))
    assert record.lifecycle.value == "Failed"
    assert record.plan is None and record.draft_revision == 0 and record.draft_digest is None
    assert record.failure_evidence is not None
    assert controller.submissions == [] and controller.inventory_calls == 0
    assert reader.calls == 1
    assert len(transport.requests) == len(record.rejected_drafts) == 3
    restored = MissionRequestStore(store_path).get(record.request_id)
    assert restored is not None
    assert restored.rejected_drafts == record.rejected_drafts
    for index, (evidence, archived) in enumerate(zip(record.rejected_drafts, drafts, strict=True)):
        evidence.verify_integrity()
        assert evidence.attempt_index == archived["attempt_index"]
        assert evidence.provider_output == archived["provider_output"]
        assert evidence.provider_output_digest == archived["provider_output_digest"]
        assert evidence.normalized_output_digest == archived["normalized_output_digest"]
        assert list(evidence.validation_errors) == archived["validation_errors"]
        assert evidence.grounding_context_digest == reader.snapshot.context_digest
        assert evidence.normalized_output == normalize_mission_plan_provider_output(
            evidence.provider_output, _current_schema()
        )
        payload = transport.requests[index][2]
        model_input = json.loads(cast(str, payload["input"]))
        assert model_input["grounding_context"] == case["grounding_context"]
        assert (
            model_input["grounded_intent"]
            == IntentAssessment.from_json(cast(JSONObject, case["assessment"]))
            .grounded_intent()
            .to_json()
        )
        assert model_input["deployment_execution_profile"] == profile.to_json()
        assert model_input["capability_catalog"] == _current_catalog().to_json()
        assert payload["instructions"] == transport.requests[0][2]["instructions"]
        if index:
            feedback = model_input["prevalidation_recovery_feedback"]
            assert (
                feedback["previous_rejected_provider_output"]
                == drafts[index - 1]["provider_output"]
            )
            assert feedback["validation_errors"] == drafts[index - 1]["validation_errors"]
        else:
            assert "prevalidation_recovery_feedback" not in model_input


def test_authoritative_executor_strengthening_recovers_before_submission(tmp_path: Path) -> None:
    """The production adapter rejects unsupported distinctness and preserves the joint goal."""
    historical_plan = cast(JSONObject, _case("B")["plan"])
    current = _case("D")
    assessment = IntentAssessment.from_json(cast(JSONObject, current["assessment"]))
    mission_id = cast(str, current["mission_id"])
    rejected = cast(JSONObject, json.loads(json.dumps(historical_plan)))
    rejected_mission = cast(JSONObject, rejected["mission"])
    rejected_mission["id"] = mission_id
    rejected_mission["objective"] = assessment.objective
    rejected_context = cast(list[JSONObject], rejected["contexts"])[0]
    rejected_context["executor_constraints"] = [
        {
            "kind": "distinct-physical-entities",
            "context_roles": ["reach-executor-a", "reach-executor-b"],
        }
    ]
    accepted = cast(JSONObject, json.loads(json.dumps(rejected)))
    accepted_context = cast(list[JSONObject], accepted["contexts"])[0]
    accepted_context["executor_constraints"] = []
    rejected_provider = _provider_plan(rejected)
    accepted_provider = _provider_plan(accepted)
    transport = FakeTransport([_response(rejected_provider), _response(accepted_provider)])
    planner = ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        transport,
        _shared_world_execution_profile(),
    )
    reader = ArchivedGroundingReader(
        GroundingContextSnapshot.from_json(cast(JSONObject, current["grounding_context"]))
    )
    controller = FakeController(_inventory())
    tokens = iter(
        [cast(str, current[key]).split("-", 1)[1] for key in ("request_id", "mission_id")]
    )
    dialogue = cast(list[JSONObject], current["dialogue"])
    engine = MissionRequestEngine(
        MissionRequestStore(tmp_path / "semantic-admission.sqlite3"),
        FakeInterpreter([assessment]),
        planner,
        controller,
        _current_catalog(),
        frozenset(),
        id_generator=lambda: next(tokens),
        clock=lambda: cast(int, dialogue[0]["created_at_ms"]),
        grounding_reader=reader,
        prevalidation_recovery_attempts=2,
    )

    record = engine.create(cast(str, dialogue[0]["content"]))

    assert record.lifecycle.value == "Accepted", record.issues
    assert record.plan is not None
    assert record.plan.contexts[0].executor_constraints == ()
    assert [task.task_id for task in record.plan.tasks] == [
        "reach-any-targets-0",
        "reach-target-any-targets-0",
    ]
    assert record.grounding_context is not None
    assert record.grounding_context.semantic_evidence == reader.snapshot.semantic_evidence
    assert len(record.rejected_drafts) == 1
    assert "lacks authoritative physical-entity grounding" in str(
        record.rejected_drafts[0].validation_errors[0]["message"]
    )
    assert len(transport.requests) == 2
    recovery_input = json.loads(cast(str, transport.requests[1][2]["input"]))
    feedback = cast(JSONObject, recovery_input["prevalidation_recovery_feedback"])
    assert feedback["previous_rejected_provider_output"] == rejected_provider
    assert controller.submissions == [record.plan]
