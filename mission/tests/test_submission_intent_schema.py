"""Validate actual opted-in Request projections with current canonical MissionPlan schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, ValidationError  # type: ignore[import-untyped]
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.requests import MissionRequestEngine, MissionRequestLifecycle, MissionRequestStore
from referencing import Registry, Resource
from test_memory_requirements import plan as memory_plan
from test_memory_requirements import requirements
from test_quantitative_resources import plan as quantitative_plan
from test_requests import (
    FakeController,
    FakeInterpreter,
    SequenceClock,
    SequenceIds,
    _assessment,
    _inventory,
)


class CurrentPlanner:
    """Return an actual current semantic fixture with the engine's original Mission identity."""

    def __init__(self, template: JSONObject) -> None:
        """Keep canonical source declarations independent of any fake deployment inventory."""
        self.template = template

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Parse original typed requirements under the normal production Planner port."""
        del grounding_context
        raw = cast(JSONObject, json.loads(json.dumps(self.template)))
        mission = cast(JSONObject, raw["mission"])
        mission.update(id=mission_id, objective=grounded_intent.objective)
        result = MissionPlan.from_json(raw)
        capability_catalog.validate_plan(result)
        return result


def local_contract(uri: str) -> Resource[object]:
    """Resolve only repository-owned contract references without any network retrieval."""
    prefix = "https://roboguide.local/contracts/"
    if not uri.startswith(prefix):
        raise ValueError("contract reference is outside the local repository")
    path = Path("contracts") / uri.removeprefix(prefix)
    return Resource.from_contents(cast(object, json.loads(path.read_bytes())))


@pytest.mark.parametrize("version", ["v0.10", "v0.11"])
def test_v05_contract_accepts_current_production_draft_without_granting_execution(
    tmp_path: Path, version: str
) -> None:
    """Production SQLite drafts retain quantitative/scoped declarations and satisfy public v0.5."""
    template = quantitative_plan() if version == "v0.10" else memory_plan()
    if version == "v0.11":
        template["schema_version"] = "roboguide.mission-plan/v0.11"
        memory = cast(list[JSONObject], requirements(template)["memories"])[0]
        memory.update(kind="spatial", source_contract="scoped_typed_map")
    controller = FakeController(_inventory())
    engine = MissionRequestEngine(
        MissionRequestStore(tmp_path / "requests.sqlite3"),
        FakeInterpreter([_assessment()]),
        CurrentPlanner(template),
        controller,
        CanonicalCapabilityCatalog.load(Path("contracts/capability/v0.3/catalog.json")),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
    )
    record = engine.create("Only plan this goal", "draft_only")
    assert record.lifecycle is MissionRequestLifecycle.AWAITING_EXECUTION
    assert record.plan is not None and record.plan.schema_version.endswith(version)
    assert controller.submissions == []
    assert engine.get(record.request_id) == record
    raw = record.to_json()
    schema = json.loads(
        Path("contracts/mission/request-v0.5/mission-request.schema.json").read_bytes()
    )
    prefix = "https://roboguide.local/contracts/mission/"
    resources = [f"{prefix}v0.{value}/mission-plan.schema.json" for value in range(3, 12)]
    resources.extend(
        f"{prefix}grounding-context-v0.{value}/grounding-context.schema.json" for value in (1, 2)
    )
    registry: Registry[object] = Registry().with_resources(
        (uri, local_contract(uri)) for uri in resources
    )
    validator = Draft202012Validator(schema, registry=registry)
    validator.validate(raw)
    # Adding current closed versions never admits a future or merely relabelled plan.
    changed = cast(JSONObject, json.loads(json.dumps(raw)))
    cast(JSONObject, changed["plan"])["schema_version"] = "roboguide.mission-plan/v0.12"
    with pytest.raises(ValidationError):
        validator.validate(changed)
