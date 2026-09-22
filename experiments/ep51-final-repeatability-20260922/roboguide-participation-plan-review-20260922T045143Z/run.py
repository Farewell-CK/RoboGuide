"""Run one bounded Planner and Reviewer validation for participation semantics."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping

ROOT = Path(os.environ["PARTICIPATION_PLAN_STUDY_ROOT"])
REPO = Path("/tmp/roboguide-stage2-execution-contract")
EXPECTED_HEAD = "219ba23ca454d11bcb718a9bbf09e72a86cab61c"
sys.path.insert(0, str(REPO / "mission/src"))

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import load_settings
from mission.execution_profile import DeploymentExecutionProfile
from mission.grounding_context import GroundingContextSnapshot
from mission.models import JSONObject, MissionPlan
from mission.request_record import IntentAssessment
from mission.responses import ResponsesMissionPlanner, ResponsesMissionReviewer, UrllibJsonTransport

CASES = ("available", "minimum", "universal")
SOURCE_ATTEMPT = {"available": 1, "minimum": 2, "universal": 3}
MISSION_IDS = {
    "available": "mission-participation-available",
    "minimum": "mission-participation-minimum",
    "universal": "mission-participation-universal",
}


def canonical_bytes(value: object) -> bytes:
    """Encode evidence JSON deterministically."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data: bytes) -> str:
    """Return a prefixed SHA-256 digest."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sanitize(value: object, secret: str) -> object:
    """Redact the in-memory credential from recursive evidence."""
    if isinstance(value, str):
        return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[REDACTED_CREDENTIAL]", value.replace(secret, "[REDACTED_CREDENTIAL]"))
    if isinstance(value, list):
        return [sanitize(item, secret) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize(item, secret) for key, item in value.items()}
    return value


def write_json(path: Path, value: object) -> None:
    """Write one immutable evidence file."""
    with path.open("xb") as stream:
        stream.write(canonical_bytes(value))


class EvidenceTransport:
    """Capture one exact Provider exchange without adding retries."""

    def __init__(self, directory: Path, secret: str) -> None:
        """Bind the production transport to an immutable evidence directory."""
        self._directory = directory
        self._secret = secret
        self._delegate = UrllibJsonTransport()

    def post_json(self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float) -> JSONObject:
        """Persist a redacted request and response around one production call."""
        started = time.monotonic()
        write_json(self._directory / "provider-request.json", {
            "url": url,
            "timeout_seconds": timeout_seconds,
            "authorization_present": "Authorization" in headers,
            "payload": sanitize(copy.deepcopy(payload), self._secret),
        })
        try:
            response = self._delegate.post_json(url, headers, payload, timeout_seconds)
        except Exception as error:
            write_json(self._directory / "provider-error.json", {
                "elapsed_seconds": time.monotonic() - started,
                "type": type(error).__name__,
                "message": sanitize(str(error), self._secret),
            })
            raise
        write_json(self._directory / "provider-response.json", {
            "elapsed_seconds": time.monotonic() - started,
            "response": sanitize(copy.deepcopy(response), self._secret),
        })
        return response


class ReplayTransport:
    """Replay one already archived Provider response without network access."""

    def __init__(self, response_path: Path) -> None:
        """Load the prior sanitized response envelope for deterministic adapter replay."""
        self._response = json.loads(response_path.read_text())["response"]

    def post_json(self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float) -> JSONObject:
        """Return the archived response and perform no I/O outside the evidence directory."""
        del url, headers, payload, timeout_seconds
        return self._response


def active_actor_ids(plan: MissionPlan) -> set[str]:
    """Return actors that are connected through a ContextRole to an executable TaskRole."""
    context_actor = {
        (context.context_id, role.role_id): role.actor_id
        for context in plan.contexts
        for role in context.roles
    }
    return {
        context_actor[(task.context_id, role.context_role)]
        for task in plan.tasks
        for role in task.roles
    }


def main() -> None:
    """Execute the preregistered three-case Planner and Reviewer study."""
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True)
    if head != EXPECTED_HEAD or dirty:
        raise RuntimeError(f"study code identity mismatch: head={head!r}, dirty={bool(dirty)}")
    secret = os.environ.get("OPENAI_API_KEY", "")
    if not secret:
        raise RuntimeError("OPENAI_API_KEY is unavailable")
    settings = load_settings(REPO / "config/mission.toml", repository_root=REPO)
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    profile = DeploymentExecutionProfile.load(REPO / "scenarios/e1-shared-world-episode-51/execution-profile.json")
    source = json.loads((ROOT / "interpreter-results-source.json").read_text())
    selected = {
        case: next(item for item in source if item["attempt"] == SOURCE_ATTEMPT[case])
        for case in CASES
    }
    manifest = {
        "schema": "roboguide.participation-plan-review-study/v0.1",
        "code_head": head,
        "components": ["ResponsesMissionPlanner", "ResponsesMissionReviewer"],
        "case_order": list(CASES),
        "planner_calls_maximum": 3,
        "reviewer_calls_maximum": 3,
        "provider_retries": 0,
        "repairer_calls": 0,
        "mission_requests_created": 0,
        "services_started": [],
        "control_node_habitat_started": False,
        "configured_model": settings.llm.model,
        "configured_review_model": settings.llm.review_model,
        "known_provider_response_identity": "gpt-6-luna in immediately preceding Interpreter study; retained as observed drift, not assumed equivalence",
        "prompt_sha256": {
            "planner": digest(settings.prompts.planner_path.read_bytes()),
            "reviewer": digest(settings.prompts.reviewer_path.read_bytes()),
        },
        "input_source": str(ROOT / "interpreter-results-source.json"),
        "selected_interpreter_attempts": SOURCE_ATTEMPT,
        "acceptance": {
            "available": "No all-candidates obligation; any nonempty active subset is semantically permitted.",
            "minimum": "At least two active logical participants remain represented.",
            "universal": "All three required active logical participants remain represented.",
            "all": "Reviewer reports no unsupported weakening or added participation requirement.",
        },
    }
    if not (ROOT / "manifest.json").exists():
        write_json(ROOT / "manifest.json", manifest)
    results = []
    environment = {"OPENAI_API_KEY": secret, "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"}
    for case in CASES:
        case_dir = ROOT / case
        plan_dir = case_dir / "planner"
        review_dir = case_dir / "reviewer"
        plan_dir.mkdir(parents=True, exist_ok=True)
        assessment = IntentAssessment.from_json(selected[case]["assessment"])
        grounding = GroundingContextSnapshot.from_json(selected[case]["grounding_context"])
        archived_plan_response = plan_dir / "provider-response.json"
        plan_transport = (
            ReplayTransport(archived_plan_response)
            if archived_plan_response.exists()
            else EvidenceTransport(plan_dir, secret)
        )
        planner = ResponsesMissionPlanner(settings, environment, plan_transport, profile)
        started = time.monotonic()
        try:
            plan = planner.plan(MISSION_IDS[case], assessment.grounded_intent(), catalog, grounding)
        except Exception as error:
            write_json(case_dir / "result.json", {
                "case": case,
                "stage": "planner",
                "status": "failed",
                "type": type(error).__name__,
                "message": sanitize(str(error), secret),
                "elapsed_seconds": time.monotonic() - started,
            })
            results.append(json.loads((case_dir / "result.json").read_text()))
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            continue
        active = sorted(active_actor_ids(plan))
        write_json(plan_dir / "canonical-plan.json", plan.to_json())
        review_dir.mkdir(parents=True, exist_ok=True)
        archived_review_response = review_dir / "provider-response.json"
        review_transport = (
            ReplayTransport(archived_review_response)
            if archived_review_response.exists()
            else EvidenceTransport(review_dir, secret)
        )
        reviewer = ResponsesMissionReviewer(settings, environment, review_transport, profile)
        review = reviewer.review(assessment.grounded_intent(), plan, catalog, grounding)
        result = {
            "case": case,
            "stage": "reviewer",
            "status": "completed",
            "mission_actor_count": len(plan.mission.actors),
            "active_actor_count": len(active),
            "active_actor_ids": active,
            "task_count": len(plan.tasks),
            "review": review.to_json(),
            "elapsed_seconds": time.monotonic() - started,
        }
        write_json(case_dir / "result.json", result)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    write_json(ROOT / "results.json", results)
    checksums = {}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.json":
            checksums[str(path.relative_to(ROOT))] = digest(path.read_bytes())
    write_json(ROOT / "SHA256SUMS.json", checksums)


if __name__ == "__main__":
    main()
