"""Offline preflight: provider-facing schema derives from v0.8 canonical."""
import hashlib, json, sys
from pathlib import Path

sys.path.insert(0, ".")
canonical_text = Path("contracts/mission/v0.8/mission-plan.schema.json").read_text()
canonical = json.loads(canonical_text)
from mission.contract_values import MISSION_PLAN_VERSION
from mission.provider_mission_plan import adapt_mission_plan_schema_for_provider

canonical_digest = hashlib.sha256(canonical_text.encode()).hexdigest()
adapted = adapt_mission_plan_schema_for_provider(json.loads(canonical_text))
adapted_digest = hashlib.sha256(json.dumps(adapted, sort_keys=True).encode()).hexdigest()

actor_items = adapted["properties"]["mission"]["properties"]["actors"]["items"]
physical_entity = actor_items["properties"].get("physical_entity")
contexts_items = adapted["properties"]["contexts"]
# contexts may be $ref or inline; resolve through $defs if needed
if "$ref" in contexts_items:
    ref = contexts_items["$ref"].split("/")[-1]
    ctx = adapted["$defs"][ref]
else:
    ctx = contexts_items
ctx = ctx.get("items", ctx)
if "$ref" in ctx:
    ctx = adapted["$defs"][ctx["$ref"].split("/")[-1]]
ec = ctx.get("properties", {}).get("executor_constraints")
ec_items = ec.get("items", {}) if ec else {}
if "$ref" in ec_items:
    ec_items = adapted["$defs"][ec_items["$ref"].split("/")[-1]]
kind = ec_items.get("properties", {}).get("kind", {}).get("const")

report = {
    "canonical_schema_sha256": canonical_digest,
    "provider_facing_schema_sha256": adapted_digest,
    "provider_schema_version_const": adapted["properties"]["schema_version"].get("const"),
    "expected_version": MISSION_PLAN_VERSION,
    "has_physical_entity_in_actors": physical_entity is not None,
    "has_executor_constraints_in_contexts": ec is not None,
    "executor_constraint_kind_const": kind,
    "derives_from_canonical_v08": (
        adapted["properties"]["schema_version"].get("const") == MISSION_PLAN_VERSION
        and physical_entity is not None
        and ec is not None
        and kind == "distinct-physical-entities"
    ),
}
print(json.dumps(report, indent=2))
Path("evaluation/preflight/evidence").mkdir(parents=True, exist_ok=True)
Path("evaluation/preflight/evidence/schema-preflight.json").write_text(
    json.dumps(report, indent=2) + "\n")
Path("evaluation/preflight/evidence/provider-facing-schema-snapshot.json").write_text(
    json.dumps(adapted, sort_keys=True) + "\n")
