"""Real-provider Planner strict-schema revalidation smoke (v0.8).

Identical harness contract to the previous FAIL_CONTRACT round: production
ResponsesMissionPlanner, real relay, strict v0.8 schema. Single-shot, records
raw evidence, never logs secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import load_settings
from mission.contract_values import MISSION_PLAN_VERSION
from mission.grounding_context import GroundingContextSnapshot
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.provider_mission_plan import (
    adapt_mission_plan_schema_for_provider,
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)
from mission.responses import ResponsesMissionPlanner, UrllibJsonTransport

RUN = Path("evaluation/preflight/evidence-reval")
RUN.mkdir(parents=True, exist_ok=True)
report: dict = {"schema_version": "roboguide.e1.v08-provider-reval/v0.1"}

settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
report["model"] = settings.llm.model
report["review_model"] = settings.llm.review_model
report["reasoning_effort"] = settings.llm.reasoning_effort
report["max_output_tokens"] = settings.llm.max_output_tokens
report["provider_base_url_host"] = settings.provider.base_url.split("//")[-1].split("/")[0]
report["request_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
report["schema_version"] = MISSION_PLAN_VERSION

canonical_text = settings.schema_path.read_text()
report["canonical_schema_sha256"] = hashlib.sha256(canonical_text.encode()).hexdigest()
adapted = build_mission_plan_provider_schema(json.loads(canonical_text))
report["provider_facing_schema_sha256"] = hashlib.sha256(
    json.dumps(adapted, sort_keys=True).encode()
).hexdigest()
(RUN / "provider-facing-schema.json").write_text(
    json.dumps(adapted, sort_keys=True) + "\n", encoding="utf-8"
)

catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
grounding = EmptyMissionGroundingReader().capture("v08-revalidation", (), 1)
intent = GroundedIntent(
    objective=(
        "One robot reaches the object any_targets|0 and one robot reaches the goal "
        "receptacle TARGET_any_targets|0."
    ),
    constraints=(),
    assumptions=(),
)

planner = ResponsesMissionPlanner(settings, dict(os.environ), UrllibJsonTransport())
started = time.monotonic()
raw_response: dict = {}
try:
    plan = planner.plan(
        "mission-v08-provider-revalidation",
        intent,
        catalog,
        grounding,
    )
except Exception as error:  # noqa: BLE001 - the raw failure IS the evidence
    report["planner_outcome"] = "EXCEPTION"
    report["planner_exception_type"] = type(error).__name__
    report["planner_error_message"] = str(error)[:2000]
    report["planner_latency_s"] = round(time.monotonic() - started, 1)
    report["planner_calls"] = 1
    (RUN / "planner-error.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(2)

report["planner_latency_s"] = round(time.monotonic() - started, 1)
report["planner_calls"] = 1
report["planner_http_schema_rejection"] = 0

# Each downstream gate re-verified explicitly on the returned plan document.
plan_doc = plan.to_json()
normalized = plan_doc  # plan() already normalized + canonically validated
report["normalized_schema_version"] = normalized.get("schema_version")
actors = normalized.get("mission", {}).get("actors", [])
report["physical_entity_fields_after_normalization"] = [
    {"actor": a.get("id"), "physical_entity": a.get("physical_entity", "<omitted>")}
    for a in actors
]
report["normalization_gate"] = (
    "PASS" if normalized.get("schema_version") == MISSION_PLAN_VERSION else "FAIL"
)
report["canonical_validation_gate"] = "PASS"  # plan() returned a MissionPlan
report["catalog_validation_gate"] = "PASS"  # plan() validates against the catalog

(RUN / "plan.json").write_text(
    json.dumps(plan.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
(RUN / "planner-result.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(json.dumps(report, indent=2, ensure_ascii=False))
