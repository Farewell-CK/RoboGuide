"""Deterministic grounding guard against a minimal v0.8 two-role plan."""
import json, sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, ".")
raw = json.loads(Path("scenarios/mission-front-half-v0.7/mission-plan.json").read_text())
raw["schema_version"] = "roboguide.mission-plan/v0.8"
raw["mission"]["actors"] = [
    {"id": "courier", "physical_entity": None},
    {"id": "reviewer"},
]
first_role = raw["tasks"][0]["roles"][0]
second_role = deepcopy(first_role)
second_role["id"] = "delivery-role-b"
second_role["context_role"] = "reviewer"
raw["tasks"][0]["roles"].append(second_role)
raw["contexts"][0]["roles"].append({"id": "reviewer", "actor": "reviewer"})
raw["contexts"][0]["executor_constraints"] = [{
    "kind": "distinct-physical-entities",
    "context_roles": ["carrier", "reviewer"],
}]

from mission.models import MissionPlan

def build(actor_entity):
    doc = deepcopy(raw)
    doc["mission"]["actors"][0]["physical_entity"] = actor_entity
    return MissionPlan.from_json(doc)

plan = build("physical.spot-1")
admitted = frozenset({"physical.spot-1"})
try:
    plan.validate_physical_entity_grounding(admitted)
    print(json.dumps({"admitted_guard": "PASS",
                       "detail": "fresh admitted physical entity accepted"}))
except Exception as e:
    print(json.dumps({"admitted_guard": "FAIL", "error": str(e)[:200]}))
    sys.exit(1)

unadmitted = frozenset({"physical.other-robot"})
try:
    plan.validate_physical_entity_grounding(unadmitted)
    print(json.dumps({"unadmitted_guard": "FAIL",
                       "detail": "unadmitted entity was accepted!"}))
    sys.exit(1)
except Exception as e:
    print(json.dumps({"unadmitted_guard": "PASS",
                       "rejected_error": str(e)[:200]}))
