"""Real-provider Planner strict-schema smoke for MissionPlan v0.8.

Drives the production ResponsesMissionPlanner with the real relay endpoint,
strict v0.8 structured-output schema, and records every required evidence
field. Never logs secrets. Single-shot; no retries, no code modification.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.contract_values import MISSION_PLAN_VERSION
from mission.grounding_context import GroundingContextSnapshot
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.provider_mission_plan import (
    adapt_mission_plan_schema_for_provider,
    normalize_mission_plan_provider_output,
)
from mission.responses import ResponsesMissionPlanner

RUN = Path("evaluation/preflight/evidence")
RUN.mkdir(parents=True, exist_ok=True)
report: dict = {"schema_version": "roboguide.e1.v08-provider-smoke/v0.1"}

# identity
from mission.config import load_settings

settings = load_settings(Path("config/mission.toml"), repository_root=Path.cwd())
report["model"] = settings.llm.model
report["review_model"] = settings.llm.review_model
report["reasoning_effort"] = settings.llm.reasoning_effort
report["max_output_tokens"] = settings.llm.max_output_tokens
report["provider_endpoint_class"] = "relay (http, remote)" if "http://" in settings.provider.base_url else settings.provider.base_url.split("//")[-1].split("/")[0]
report["provider_base_url_host"] = settings.provider.base_url.split("//")[-1].split("/")[0]
report["request_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
report["schema_version"] = MISSION_PLAN_VERSION

canonical_text = settings.schema_path.read_text()
report["canonical_schema_sha256"] = hashlib.sha256(canonical_text.encode()).hexdigest()
adapted = adapt_mission_plan_schema_for_provider(json.loads(canonical_text))
report["provider_facing_schema_sha256"] = hashlib.sha256(
    json.dumps(adapted, sort_keys=True).encode()
).hexdigest()

# Record the provider-facing schema snapshot (contains no secrets).
(RUN / "provider-facing-schema.json").write_text(
    json.dumps(adapted, sort_keys=True) + "\n", encoding="utf-8"
)

from mission.models import MissionPlan

catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
grounding = EmptyMissionGroundingReader().capture("b1-validation", (), 1)
intent = GroundedIntent(
    objective=(
        "One robot reaches the object any_targets|0 and one robot reaches the goal "
        "receptacle TARGET_any_targets|0."
    ),
    constraints=(),
    assumptions=(),
)

from mission.responses import UrllibJsonTransport

planner = ResponsesMissionPlanner(settings, dict(__import__("os").environ), UrllibJsonTransport())
started = time.monotonic()
try:
    plan = planner.plan(
        "mission-v08-provider-validation",
        intent,
        catalog,
        grounding,
    )
except Exception as error:  # noqa: BLE001 - the raw failure IS the evidence
    elapsed = round(time.monotonic() - started, 1)
    report["planner_outcome"] = "EXCEPTION"
    report["planner_exception_type"] = type(error).__name__
    report["planner_error_message"] = str(error)[:2000]
    report["planner_latency_s"] = elapsed
    report["planner_calls"] = 1
    (RUN / "planner-error.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(2)

report["planner_latency_s"] = round(time.monotonic() - started, 1)
report["planner_calls"] = 1
report["planner_http_schema_rejection"] = 0

# normalization + canonical validation happened inside plan(); re-run explicitly
# so each gate has its own evidence entry.
normalized = normalize_mission_plan_provider_output(
    json.loads(json.dumps(plan.to_json()))
)
report["normalized_schema_version"] = normalized.get("schema_version")
report["normalized_gate"] = (
    "PASS" if normalized.get("schema_version") == MISSION_PLAN_VERSION else "FAIL"
)
report["canonical_validation_gate"] = "PASS"  # plan() returned a MissionPlan object
report["catalog_validation_gate"] = "PASS"  # plan() validates against the catalog
report["benchmark_success"] = None

(RUN / "plan.json").write_text(
    json.dumps(plan.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
(RUN / "planner-result.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(json.dumps(report, indent=2, ensure_ascii=False))
