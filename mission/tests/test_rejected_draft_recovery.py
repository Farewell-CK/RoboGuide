"""Rejected-draft evidence and bounded pre-validation recovery regressions."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.contract_values import MissionPlanError
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.rejected_draft import (
    RejectedDraftEvidence,
    RejectedPlanError,
    build_rejected_draft_evidence,
)
from mission.request_engine import MissionRequestEngine
from mission.request_record import IntentAssessment
from mission.request_store import MissionRequestStore
from mission.submission_evidence import canonical_plan_digest
from test_planners import _current_catalog
from test_requests import (
    FakeController,
    FakeInterpreter,
    SequenceClock,
    SequenceIds,
)

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
CATALOG = CanonicalCapabilityCatalog.load(Path("contracts/capability/v0.3/catalog.json"))


class ScriptedRecoveryPlanner:
    """Script plan() rejections and regenerate() outcomes for engine tests."""

    def __init__(
        self,
        rejections: list[RejectedPlanError],
        regenerated: list[MissionPlan | RejectedPlanError] | None = None,
        plan_factory: Callable[[str], MissionPlan] | None = None,
    ) -> None:
        """Queue the initial rejection and each regeneration outcome in order."""
        self._rejections = list(rejections)
        self._regenerated = list(regenerated or [])
        self._plan_factory = plan_factory
        self.plan_calls = 0
        self.regenerate_calls: list[dict[str, Any]] = []

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Fail with the next scripted rejection, or return a valid plan."""
        del grounded_intent, capability_catalog, grounding_context
        self.plan_calls += 1
        if self._rejections:
            raise self._rejections.pop(0)
        if self._plan_factory is None:
            raise AssertionError("scripted planner has no successful plan")
        return self._plan_factory(mission_id)

    def regenerate(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
        previous_provider_output: JSONObject,
        validation_errors: list[JSONObject],
    ) -> MissionPlan:
        """Consume rejection feedback and yield the next scripted outcome."""
        self.regenerate_calls.append(
            {
                "mission_id": mission_id,
                "grounded_intent": grounded_intent,
                "capability_catalog": capability_catalog,
                "grounding_context": grounding_context,
                "previous_provider_output": previous_provider_output,
                "validation_errors": validation_errors,
            }
        )
        outcome = self._regenerated.pop(0) if self._regenerated else None
        if isinstance(outcome, RejectedPlanError):
            raise outcome
        if self._plan_factory is None:
            raise AssertionError("scripted planner has no regeneration outcome")
        return self._plan_factory(mission_id)


class ProviderFailingPlanner:
    """Fail like a transport/authentication provider fault, not a draft defect."""

    def __init__(self) -> None:
        """Count calls for recovery-attempt assertions."""
        self.calls = 0

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Raise the provider fault class that must never trigger recovery."""
        del mission_id, grounded_intent, capability_catalog, grounding_context
        self.calls += 1
        from mission.responses import MissionProviderError

        raise MissionProviderError("provider returned HTTP 401: unauthorized")


class NoRegenerationPlanner:
    """Produce one invalid initial draft without an optional regeneration port."""

    def __init__(self) -> None:
        """Start with no model attempts."""
        self.calls = 0

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Return one rejected model draft, leaving recovery unavailable."""
        del mission_id, grounded_intent, capability_catalog, grounding_context
        self.calls += 1
        raise _rejection()


def _valid_plan(mission_id: str, objective: str) -> MissionPlan:
    """Build one valid independent-mode fixture plan for the given identity."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    mission = cast(JSONObject, raw["mission"])
    mission["id"] = mission_id
    mission["objective"] = objective
    plan = MissionPlan.from_json(raw)
    CATALOG.validate_plan(plan)
    return plan


def _concurrent_plan(mission_id: str, objective: str) -> MissionPlan:
    """Build one valid concurrent-cooperation plan with view and relation."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    raw["schema_version"] = "roboguide.mission-plan/v0.4"
    tasks = cast(list[JSONObject], raw["tasks"])
    inspect: JSONObject = json.loads(json.dumps(tasks[0]))
    inspect["id"] = "inspect"
    inspect["description"] = "parallel inspection"
    role = cast(JSONObject, cast(list[JSONValue], inspect["roles"])[0])
    role["id"] = "inspect-compute"
    tasks.append(inspect)
    mission = cast(JSONObject, raw["mission"])
    mission["id"] = mission_id
    mission["objective"] = objective
    context = cast(list[JSONObject], raw["contexts"])[0]
    context["coupling_mode"] = "concurrent-cooperation"
    context_roles = cast(list[JSONObject], context["roles"])
    context["shared_view"] = {
        "bindings": [
            {
                "context_role_id": context_roles[0]["id"],
                "field": "pose",
                "state_export_id": "role-pose",
                "payload_schema": "roboguide.pose/v1",
            }
        ],
        "include_freshness": True,
    }
    context["relations"] = [
        {
            "id": "co-navigate",
            "kind": "shared-spatial-reference",
            "reference": {"map_id": "campus", "revision_id": "r1", "frame_id": "map"},
            "source": {"task_id": "inspect", "role_id": "inspect-compute"},
            "target": {"task_id": "prepare", "role_id": "prepare-compute"},
        }
    ]
    return MissionPlan.from_json(raw)


def _rejection(
    message: str = "contexts[0] mode concurrent-cooperation requires a Group shared view",
    stage: str = "plan_validation",
) -> RejectedPlanError:
    """Build one structure rejection carrying a raw provider draft."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    return RejectedPlanError(
        message,
        stage=stage,
        provider_output=raw,
        normalized_output=raw if stage == "plan_validation" else None,
        generated_at_ms=1000,
    )


def _assessment(*questions: str) -> IntentAssessment:
    """Build one deterministic intent assessment."""
    from test_requests import _assessment as requests_assessment

    return requests_assessment(*questions)


def _engine(
    tmp_path: Path,
    planner: Any,
    *,
    budget: int = 0,
    assessments: int = 1,
) -> MissionRequestEngine:
    """Compose one engine with a real store and the given recovery budget."""
    return MissionRequestEngine(
        MissionRequestStore(tmp_path / "requests.sqlite3"),
        FakeInterpreter([_assessment() for _ in range(assessments)]),
        planner,
        FakeController(_controller_inventory()),
        CATALOG,
        frozenset(),
        SequenceIds(),
        SequenceClock(),
        prevalidation_recovery_attempts=budget,
        provider_identity={"provider": "OpenAI", "model": "test-model"},
    )


def _controller_inventory() -> Any:
    """Reuse the requests-test controller inventory for fixture contracts."""
    from test_requests import _fixture_contracts, _inventory

    return _inventory(*_fixture_contracts())


def test_valid_independent_plan_never_triggers_recovery(tmp_path: Path) -> None:
    """A valid first draft succeeds with exactly one planner call (case 1)."""
    planner = ScriptedRecoveryPlanner(
        [],
        [],
        plan_factory=lambda mission_id: _valid_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is not None
    assert planner.plan_calls == 1
    assert planner.regenerate_calls == []
    assert record.rejected_drafts == ()
    assert record.lifecycle.value != "Failed"


def test_valid_concurrent_cooperation_plan_passes_unchanged(tmp_path: Path) -> None:
    """A legitimate concurrent-cooperation draft needs no recovery (case 2)."""
    planner = ScriptedRecoveryPlanner(
        [],
        [],
        plan_factory=lambda mission_id: _concurrent_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is not None
    assert planner.regenerate_calls == []


def test_missing_shared_view_is_rejected_with_full_evidence(tmp_path: Path) -> None:
    """The Episode51 failure shape is rejected and preserved verbatim (case 3)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert planner.plan_calls == 1
    drafts = record.rejected_drafts
    assert len(drafts) == 1
    evidence = drafts[0]
    assert evidence.stage == "plan_validation"
    first_error: JSONObject = dict(evidence.validation_errors[0])
    message = first_error["message"]
    assert isinstance(message, str)
    assert "requires a Group shared view" in message
    assert evidence.provider_output_digest == canonical_plan_digest(evidence.provider_output)
    assert evidence.attempt_index == 1
    assert evidence.request_id == record.request_id
    assert evidence.mission_id == record.mission_id


def test_shared_view_without_relation_reports_the_next_error(tmp_path: Path) -> None:
    """Adding a view but keeping relations empty yields the relation error (case 4)."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["schema_version"] = "roboguide.mission-plan/v0.4"
    context = raw["contexts"][0]
    context["coupling_mode"] = "concurrent-cooperation"
    context["shared_view"] = {"bindings": [], "include_freshness": True}
    with pytest.raises(MissionPlanError, match="bindings must not be empty"):
        MissionPlan.from_json(cast(JSONObject, raw))
    context["shared_view"] = {
        "bindings": [
            {
                "context_role_id": context["roles"][0]["id"],
                "field": "pose",
                "state_export_id": "role-pose",
                "payload_schema": "roboguide.pose/v1",
            }
        ],
        "include_freshness": True,
    }
    context["relations"] = []
    with pytest.raises(MissionPlanError, match="requires an execution relation"):
        MissionPlan.from_json(cast(JSONObject, raw))


def test_tightly_coupled_mode_requires_all_mechanisms() -> None:
    """Missing peer channel or second role is strictly rejected (case 5)."""
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base["schema_version"] = "roboguide.mission-plan/v0.4"
    tasks = cast(list[JSONObject], base["tasks"])
    inspect: JSONObject = json.loads(json.dumps(tasks[0]))
    inspect["id"] = "inspect"
    inspect["description"] = "parallel inspection"
    cast(JSONObject, cast(list[JSONValue], inspect["roles"])[0])["id"] = "inspect-compute"
    tasks.append(inspect)
    context = base["contexts"][0]
    context["coupling_mode"] = "tightly-coupled-cooperation"
    context["shared_view"] = {
        "bindings": [
            {
                "context_role_id": context["roles"][0]["id"],
                "field": "pose",
                "state_export_id": "role-pose",
                "payload_schema": "roboguide.pose/v1",
            }
        ],
        "include_freshness": True,
    }
    context["relations"] = [
        {
            "id": "co",
            "kind": "shared-spatial-reference",
            "reference": {"map_id": "campus", "revision_id": "r1", "frame_id": "map"},
            "source": {"task_id": "inspect", "role_id": "inspect-compute"},
            "target": {"task_id": "prepare", "role_id": "prepare-compute"},
        }
    ]
    with pytest.raises(MissionPlanError, match="requires a direct peer channel"):
        MissionPlan.from_json(cast(JSONObject, base))
    context["peer_channel"] = {"profile_id": "p", "message_schema": "roboguide.p/v1"}
    context["roles"] = [context["roles"][0]]
    with pytest.raises(MissionPlanError, match="requires at least two context roles"):
        MissionPlan.from_json(cast(JSONObject, base))


def test_rejected_drafts_survive_store_restart(tmp_path: Path) -> None:
    """Evidence restores fully from a fresh store handle (case 6)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    reopened = MissionRequestStore(tmp_path / "requests.sqlite3")
    restored = reopened.get(record.request_id)
    assert restored is not None
    assert len(restored.rejected_drafts) == 1
    assert restored.rejected_drafts[0].attempt_id == record.rejected_drafts[0].attempt_id


def test_planner_without_regeneration_port_records_one_rejection(tmp_path: Path) -> None:
    """A missing optional Planner retry port does not duplicate one rejected draft."""
    planner = NoRegenerationPlanner()
    record = _engine(tmp_path, planner, budget=2).create("deliver the payload")
    assert record.lifecycle.value == "Failed"
    assert planner.calls == 1
    assert len(record.rejected_drafts) == 1
    assert record.rejected_drafts[0].deliberation_stage == "planner"


def test_raw_and_normalized_digests_bind_their_documents() -> None:
    """Digests bind the exact raw and normalized payloads (case 7)."""
    raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    normalized = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
    normalized["mission"] = dict(cast(JSONObject, normalized["mission"]))  # distinct identity
    error = RejectedPlanError(
        "boom",
        stage="plan_validation",
        provider_output=raw,
        normalized_output=normalized,
        generated_at_ms=5,
    )
    evidence = build_rejected_draft_evidence(
        request_id="r1",
        mission_id="m1",
        attempt_index=1,
        error=error,
        grounding_context_digest="sha256:" + "a" * 64,
        semantic_evidence_digest=None,
        provider_identity={"provider": "OpenAI", "model": "m"},
        persisted_at_ms=9,
    )
    assert evidence.provider_output_digest == canonical_plan_digest(raw)
    assert evidence.normalized_output_digest == canonical_plan_digest(normalized)
    round_trip = RejectedDraftEvidence.from_json(evidence.to_json())
    assert round_trip.provider_output_digest == evidence.provider_output_digest
    assert round_trip.normalized_output_digest == evidence.normalized_output_digest
    assert round_trip.deliberation_stage == "planner"


def test_legacy_rejected_planner_evidence_remains_readable() -> None:
    """The v0.1 observation shape restores without mislabeling old Planner attempts."""
    evidence = build_rejected_draft_evidence(
        request_id="r1",
        mission_id="m1",
        attempt_index=1,
        error=_rejection(),
        grounding_context_digest=None,
        semantic_evidence_digest=None,
        provider_identity={},
        persisted_at_ms=9,
    )
    legacy = evidence.to_json()
    legacy["schema_version"] = "roboguide.mission.rejected-draft/v0.1"
    del legacy["deliberation_stage"]
    restored = RejectedDraftEvidence.from_json(legacy)
    assert restored.schema_version == legacy["schema_version"]
    assert restored.deliberation_stage == "planner"
    assert restored.to_json() == legacy
    legacy["deliberation_stage"] = "repairer"
    with pytest.raises(ValueError, match="fields do not match"):
        RejectedDraftEvidence.from_json(legacy)


def test_v02_rejected_draft_requires_explicit_origin() -> None:
    """Current evidence cannot omit or rename its model-generation stage."""
    evidence = build_rejected_draft_evidence(
        request_id="r1",
        mission_id="m1",
        attempt_index=1,
        error=_rejection(),
        grounding_context_digest=None,
        semantic_evidence_digest=None,
        provider_identity={},
        persisted_at_ms=9,
    )
    current = evidence.to_json()
    del current["deliberation_stage"]
    with pytest.raises(ValueError, match="fields do not match"):
        RejectedDraftEvidence.from_json(current)


def test_invalid_drafts_do_not_advance_revision_or_approval(tmp_path: Path) -> None:
    """Rejected drafts leave the formal draft lifecycle untouched (case 8)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is None
    assert record.draft_revision == 0
    assert record.draft_digest is None
    assert record.review_history == ()
    assert not record.approval_required


def test_recoverable_structure_error_recovers_within_budget(tmp_path: Path) -> None:
    """Regeneration with feedback yields a fully validated plan (case 9)."""
    planner = ScriptedRecoveryPlanner(
        [_rejection()],
        [],
        plan_factory=lambda mission_id: _valid_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=1)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is not None
    assert record.draft_revision == 1
    assert len(record.rejected_drafts) == 1
    call = planner.regenerate_calls[0]
    rejection = _rejection()
    assert call["previous_provider_output"] == rejection.provider_output
    assert call["validation_errors"][0]["stage"] == "plan_validation"


def test_each_failed_regeneration_consumes_budget_and_keeps_evidence(
    tmp_path: Path,
) -> None:
    """Repeated rejections persist one evidence document per attempt (case 10)."""
    planner = ScriptedRecoveryPlanner(
        [_rejection()],
        [_rejection(message="still missing shared view"), _rejection()],
    )
    engine = _engine(tmp_path, planner, budget=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert planner.plan_calls == 1
    assert len(planner.regenerate_calls) == 2
    indexes = [draft.attempt_index for draft in record.rejected_drafts]
    assert indexes == [1, 2, 3]


def test_exhausted_budget_keeps_mi_failure_evidence(tmp_path: Path) -> None:
    """Budget exhaustion records the MI failure with full history (case 11)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert record.issues
    assert "Group shared view" in record.issues[0]
    assert record.failure_evidence is not None


def test_provider_faults_never_trigger_recovery(tmp_path: Path) -> None:
    """Transport/auth failures are not draft defects (case 12)."""
    planner = ProviderFailingPlanner()
    engine = _engine(tmp_path, planner, budget=3)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert planner.calls == 1
    assert record.rejected_drafts == ()
    assert record.failure_evidence is not None


def test_recovery_inputs_stay_frozen(tmp_path: Path) -> None:
    """Regeneration reuses the identical Mission identity and context (case 13)."""
    planner = ScriptedRecoveryPlanner(
        [_rejection()],
        [],
        plan_factory=lambda mission_id: _valid_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=1)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is not None
    call = planner.regenerate_calls[0]
    assert call["mission_id"] == record.mission_id
    assert call["grounded_intent"].objective == "deliver the payload through the approved route"
    assert call["capability_catalog"] is CATALOG
    assert call["grounding_context"].request_id == record.request_id


def test_normal_review_and_repair_flow_is_unchanged(tmp_path: Path) -> None:
    """A valid draft still flows through the existing lifecycle (case 14)."""
    planner = ScriptedRecoveryPlanner(
        [],
        [],
        plan_factory=lambda mission_id: _valid_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.plan is not None
    assert record.draft_revision == 1
    assert record.rejected_drafts == ()
    observations = record.observations()
    assert "rejected_drafts" not in record.to_json()
    assert observations.rejected_drafts == ()


def test_early_failure_evidence_and_projection_stay_compatible(tmp_path: Path) -> None:
    """The public v0.4 projection and failure evidence keep their shapes (case 15)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    public = record.to_json()
    assert "rejected_drafts" not in public
    assert public["lifecycle"] == "Failed"
    assert record.failure_evidence is not None
    assert record.failure_evidence.get("stage") == "planner"


def test_recovery_works_for_arbitrary_non_e1_missions(tmp_path: Path) -> None:
    """No Episode51 entities or goals are baked into the mechanism (case 16)."""
    planner = ScriptedRecoveryPlanner(
        [
            RejectedPlanError(
                "contexts[0] mode concurrent-cooperation requires a Group shared view",
                stage="plan_validation",
                provider_output=cast(
                    JSONObject, {"mission": {"id": "courier-42"}, "tasks": [], "contexts": []}
                ),
                normalized_output=None,
                generated_at_ms=1,
            )
        ],
        [],
        plan_factory=lambda mission_id: _valid_plan(
            mission_id, "deliver the payload through the approved route"
        ),
    )
    engine = _engine(tmp_path, planner, budget=1)
    record = engine.create("deliver the blue parcel to the west office")
    assert record.plan is not None
    assert record.rejected_drafts[0].mission_id == record.mission_id
    serialized = json.dumps(record.rejected_drafts[0].to_json())
    assert "any_targets" not in serialized
    assert "TARGET_any_targets" not in serialized


def test_oversized_provider_output_is_truncated_with_marker() -> None:
    """The raw draft is bounded while its digest still covers the full bytes."""
    huge = cast(JSONObject, {"blob": "x" * (300 * 1024)})
    error = RejectedPlanError(
        "shape",
        stage="normalization",
        provider_output=huge,
        normalized_output=None,
        generated_at_ms=1,
    )
    evidence = build_rejected_draft_evidence(
        request_id="r",
        mission_id="m",
        attempt_index=1,
        error=error,
        grounding_context_digest=None,
        semantic_evidence_digest=None,
        provider_identity={},
        persisted_at_ms=2,
    )
    assert evidence.provider_output["truncated"] is True
    assert evidence.provider_output_digest == canonical_plan_digest(huge)


def _planner_with(transport_responses: list[JSONObject]) -> Any:
    """Build a real ResponsesMissionPlanner over a scripted fake transport."""
    from mission.responses import ResponsesMissionPlanner
    from test_planners import FakeTransport, _local_settings  # noqa: F401

    return ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        FakeTransport(transport_responses),
    )


def _grounding() -> GroundingContextSnapshot:
    """Build one deterministic grounding snapshot for planner calls."""
    from test_planners import _grounding as planners_grounding

    return planners_grounding()


V08_FIXTURE = Path("scenarios/e1-shared-world-episode-51/mission-plan.json")


def _dto_output(entries: list[JSONObject], version: str | None = None) -> JSONObject:
    """Wrap parameter entries as a raw provider MissionPlan output."""
    raw: JSONObject = json.loads(V08_FIXTURE.read_text(encoding="utf-8"))
    raw["schema_version"] = version or "roboguide.mission-plan/v0.8"
    raw_tasks: Any = raw["tasks"]
    raw_roles: Any = raw_tasks[0]["roles"]
    intent: Any = raw_roles[0]["execution_intent"]
    intent["parameters"] = entries
    return raw


def test_duplicate_parameter_keys_become_rejected_normalization_drafts() -> None:
    """Duplicate DTO keys are model-draft defects with full evidence (R1)."""
    from test_planners import _response

    output = _dto_output(
        [
            cast(JSONObject, {"key": "destination", "value": "dock"}),
            cast(JSONObject, {"key": "destination", "value": "bay"}),
        ]
    )
    planner = _planner_with([_response(output)])
    with pytest.raises(RejectedPlanError) as caught:
        planner.plan(
            "m-dup",
            GroundedIntent("objective", (), ()),
            _current_catalog(),
            _grounding(),
        )
    error = caught.value
    assert error.stage == "normalization"
    assert error.normalized_output is None
    assert "duplicate key" in str(error)
    assert error.provider_output == output


def test_non_scalar_parameter_values_are_rejected_normalization() -> None:
    """Nested parameter values classify as model DTO defects (R1)."""
    from test_planners import _response

    output = _dto_output([cast(JSONObject, {"key": "waypoints", "value": [{"x": 1}]})])
    planner = _planner_with([_response(output)])
    with pytest.raises(RejectedPlanError) as caught:
        planner.plan(
            "m-shape",
            GroundedIntent("objective", (), ()),
            _current_catalog(),
            _grounding(),
        )
    assert caught.value.stage == "normalization"
    assert caught.value.provider_output == output


def test_unsupported_output_version_is_rejected_normalization() -> None:
    """A model-emitted unsupported version is a recoverable draft defect (R1)."""
    from test_planners import _response

    output = _dto_output([], version="roboguide.mission-plan/v0.1")
    planner = _planner_with([_response(output)])
    with pytest.raises(RejectedPlanError) as caught:
        planner.plan(
            "m-version",
            GroundedIntent("objective", (), ()),
            _current_catalog(),
            _grounding(),
        )
    assert caught.value.stage == "normalization"
    assert "must use" in str(caught.value)


def test_local_schema_configuration_fault_stays_unrecoverable() -> None:
    """Canonical-schema adaptation faults are not draft defects (R1)."""
    from mission.provider_mission_plan import ProviderMissionPlanError
    from mission.responses import ResponsesMissionPlanner
    from test_planners import FakeTransport, _local_settings

    planner = ResponsesMissionPlanner(
        _local_settings(),
        {"OPENAI_API_KEY": "test-only-key"},
        FakeTransport([]),
    )
    broken = cast(
        JSONObject, json.loads(Path("contracts/mission/v0.8/mission-plan.schema.json").read_text())
    )
    properties = cast(JSONObject, broken["properties"])
    schema_version = cast(JSONObject, properties["schema_version"])
    schema_version["const"] = "roboguide.mission-plan/v99.9"
    planner._client._load_schema = lambda: broken  # type: ignore[method-assign]
    with pytest.raises(ProviderMissionPlanError) as caught:
        planner.plan(
            "m-schema",
            GroundedIntent("objective", (), ()),
            _current_catalog(),
            _grounding(),
        )
    assert not isinstance(caught.value, RejectedPlanError)


class ProviderFaultAfterRejectionPlanner(ScriptedRecoveryPlanner):
    """Fail regeneration like a real HTTP 401 after one rejected draft."""

    def __init__(self) -> None:
        """Schedule one structure rejection and a provider fault."""
        from mission.responses import MissionProviderError

        super().__init__([_rejection()])
        self._provider_error = MissionProviderError("provider returned HTTP 401: unauthorized")

    def regenerate(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
        previous_provider_output: JSONObject,
        validation_errors: list[JSONObject],
    ) -> MissionPlan:
        """Record the call, then fail with the provider fault (R2)."""
        self.regenerate_calls.append({"mission_id": mission_id})
        raise self._provider_error


def test_provider_fault_during_recovery_keeps_prior_evidence(tmp_path: Path) -> None:
    """A 401 during regeneration preserves the first rejection (R2)."""
    planner = ProviderFaultAfterRejectionPlanner()
    engine = _engine(tmp_path, planner, budget=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert planner.plan_calls == 1
    assert len(planner.regenerate_calls) == 1  # no third model call
    assert len(record.rejected_drafts) == 1
    assert record.rejected_drafts[0].attempt_index == 1
    assert "401" in record.issues[0]
    assert record.failure_evidence is not None
    assert "401" in str(record.failure_evidence)
    reopened = MissionRequestStore(tmp_path / "requests.sqlite3")
    restored = reopened.get(record.request_id)
    assert restored is not None and len(restored.rejected_drafts) == 1


def test_retry_cycle_keeps_unique_attempt_identities(tmp_path: Path) -> None:
    """A second Planner cycle after retry extends, not overwrites (R3)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0, assessments=2)
    record = engine.create("deliver the payload through the approved route")
    assert record.lifecycle.value == "Failed"
    assert [draft.attempt_index for draft in record.rejected_drafts] == [1]
    second = ScriptedRecoveryPlanner([_rejection("second rejection")])
    engine._planner = second
    retried = engine.retry(record.request_id)
    assert retried.lifecycle.value == "Failed"
    indexes = [draft.attempt_index for draft in retried.rejected_drafts]
    assert indexes == [1, 2]
    ids = [draft.attempt_id for draft in retried.rejected_drafts]
    assert len(set(ids)) == 2
    reopened = MissionRequestStore(tmp_path / "requests.sqlite3")
    restored = reopened.get(record.request_id)
    assert restored is not None
    assert [draft.attempt_index for draft in restored.rejected_drafts] == [1, 2]


def test_retry_budget_is_not_reduced_by_history(tmp_path: Path) -> None:
    """A retry cycle keeps its full configured recovery budget (R3)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0, assessments=2)
    record = engine.create("deliver the payload through the approved route")
    second = ScriptedRecoveryPlanner(
        [_rejection()],
        [_rejection("a"), _rejection("b")],
    )
    engine._planner = second
    # The first cycle ran under budget 0; a retried cycle gets the full
    # configured budget regardless of historical draft count.
    engine._prevalidation_recovery_attempts = 2
    retried = engine.retry(record.request_id)
    # Budget 2 means this cycle may regenerate twice after the first attempt.
    assert len(second.regenerate_calls) == 2
    assert [d.attempt_index for d in retried.rejected_drafts] == [1, 2, 3, 4]


def test_restored_evidence_rejects_digest_tampering(tmp_path: Path) -> None:
    """A raw payload edited after persistence fails integrity (R4)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    row = (
        store._connect()
        .execute(
            "SELECT document_json FROM mission_requests WHERE request_id = ?",
            (record.request_id,),
        )
        .fetchone()[0]
    )
    document = json.loads(row)
    draft = document["observations"]["rejected_drafts"][0]
    draft["provider_output"]["mission"]["id"] = "tampered"
    connection = store._connect()
    connection.execute(
        "UPDATE mission_requests SET document_json = ? WHERE request_id = ?",
        (json.dumps(document, ensure_ascii=False, sort_keys=True), record.request_id),
    )
    connection.commit()
    reopened = MissionRequestStore(tmp_path / "requests.sqlite3")
    with pytest.raises(ValueError, match="does not match its digest"):
        reopened.get(record.request_id)


def test_restored_evidence_rejects_foreign_request_drafts(tmp_path: Path) -> None:
    """Evidence from another request cannot attach here (R4)."""
    planner = ScriptedRecoveryPlanner([_rejection()])
    engine = _engine(tmp_path, planner, budget=0)
    record = engine.create("deliver the payload through the approved route")
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    row = (
        store._connect()
        .execute(
            "SELECT document_json FROM mission_requests WHERE request_id = ?",
            (record.request_id,),
        )
        .fetchone()[0]
    )
    document = json.loads(row)
    document["observations"]["rejected_drafts"][0]["request_id"] = "request-other"
    connection = store._connect()
    connection.execute(
        "UPDATE mission_requests SET document_json = ? WHERE request_id = ?",
        (json.dumps(document, ensure_ascii=False, sort_keys=True), record.request_id),
    )
    connection.commit()
    reopened = MissionRequestStore(tmp_path / "requests.sqlite3")
    with pytest.raises(Exception, match="another request"):
        reopened.get(record.request_id)
