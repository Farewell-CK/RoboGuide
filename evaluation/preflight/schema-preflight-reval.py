import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, ".")
canonical_text = Path("contracts/mission/v0.8/mission-plan.schema.json").read_text()
canonical_digest = hashlib.sha256(canonical_text.encode()).hexdigest()
from mission.provider_mission_plan import build_mission_plan_provider_schema
adapted = build_mission_plan_provider_schema(json.loads(canonical_text))
adapted_digest = hashlib.sha256(json.dumps(adapted, sort_keys=True).encode()).hexdigest()
sv = adapted["properties"]["schema_version"]
version_via_enum = sv.get("enum") == ["roboguide.mission-plan/v0.8"]
report = {
    "canonical_schema_sha256": canonical_digest,
    "provider_facing_schema_sha256": adapted_digest,
    "provider_schema_version_enforced_via_enum": version_via_enum,
    "expected_version": "roboguide.mission-plan/v0.8",
    "matches": version_via_enum,
}
Path("evaluation/preflight/evidence-reval/schema-preflight.json").write_text(
    json.dumps(report, indent=2) + "\n")
Path("evaluation/preflight/evidence-reval/provider-facing-schema.json").write_text(
    json.dumps(adapted, sort_keys=True) + "\n")
print(json.dumps(report, indent=2))
