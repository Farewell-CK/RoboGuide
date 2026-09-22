"""Recheck two frozen participation plans with the refined production Reviewer."""
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

ROOT = Path(os.environ["PARTICIPATION_REVIEW_ROOT"])
REPO = Path("/tmp/roboguide-stage2-execution-contract")
SOURCE = Path("/data/workspace/code/roboguide-participation-plan-review-20260922T045143Z")
INTERPRETER_SOURCE = SOURCE / "interpreter-results-source.json"
EXPECTED_HEAD = "7dfdf3891487af51cd59f97cabc1796800d073d8"
sys.path.insert(0, str(REPO / "mission/src"))

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import load_settings
from mission.execution_profile import DeploymentExecutionProfile
from mission.grounding_context import GroundingContextSnapshot
from mission.models import JSONObject, MissionPlan
from mission.request_record import IntentAssessment
from mission.responses import ResponsesMissionReviewer, UrllibJsonTransport

CASES = ("minimum", "universal")
SOURCE_ATTEMPT = {"minimum": 2, "universal": 3}


def encoded(value: object) -> bytes:
    """Encode public evidence as stable JSON."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data: bytes) -> str:
    """Return one prefixed SHA-256 digest."""
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


def write(path: Path, value: object) -> None:
    """Write one immutable evidence artifact."""
    with path.open("xb") as stream:
        stream.write(encoded(value))


class EvidenceTransport:
    """Archive one production Provider exchange without retrying it."""

    def __init__(self, directory: Path, secret: str) -> None:
        """Bind the transport to one immutable attempt directory."""
        self._directory = directory
        self._secret = secret
        self._delegate = UrllibJsonTransport()

    def post_json(self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float) -> JSONObject:
        """Capture nonsecret request and response fields around the HTTP call."""
        started = time.monotonic()
        write(self._directory / "provider-request.json", {
            "url": url,
            "timeout_seconds": timeout_seconds,
            "authorization_present": "Authorization" in headers,
            "payload": sanitize(copy.deepcopy(payload), self._secret),
        })
        response = self._delegate.post_json(url, headers, payload, timeout_seconds)
        write(self._directory / "provider-response.json", {
            "elapsed_seconds": time.monotonic() - started,
            "response": sanitize(copy.deepcopy(response), self._secret),
        })
        return response


def main() -> None:
    """Run exactly two Reviewer calls over unchanged archived canonical plans."""
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True)
    if head != EXPECTED_HEAD or dirty:
        raise RuntimeError(f"code identity mismatch: {head!r}, dirty={bool(dirty)}")
    secret = os.environ.get("OPENAI_API_KEY", "")
    if not secret:
        raise RuntimeError("OPENAI_API_KEY is unavailable")
    settings = load_settings(REPO / "config/mission.toml", repository_root=REPO)
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    profile = DeploymentExecutionProfile.load(REPO / "scenarios/e1-shared-world-episode-51/execution-profile.json")
    interpreter = json.loads(INTERPRETER_SOURCE.read_text())
    selected = {case: next(item for item in interpreter if item["attempt"] == SOURCE_ATTEMPT[case]) for case in CASES}
    write(ROOT / "manifest.json", {
        "schema": "roboguide.participation-review-recheck/v0.1",
        "code_head": head,
        "cases": list(CASES),
        "provider_calls": 2,
        "retries": 0,
        "repairer_calls": 0,
        "source_plans": {case: str(SOURCE / case / "planner/canonical-plan.json") for case in CASES},
        "source_plan_sha256": {case: digest((SOURCE / case / "planner/canonical-plan.json").read_bytes()) for case in CASES},
        "reviewer_prompt_sha256": digest(settings.prompts.reviewer_path.read_bytes()),
        "configured_model": settings.llm.review_model,
        "known_current_response_identity": "gpt-6-luna",
        "purpose": "Verify that unbound pairwise distinctness is not confused with invented concrete physical identity.",
    })
    results = []
    environment = {"OPENAI_API_KEY": secret, "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"}
    for case in CASES:
        directory = ROOT / case
        directory.mkdir()
        assessment = IntentAssessment.from_json(selected[case]["assessment"])
        grounding = GroundingContextSnapshot.from_json(selected[case]["grounding_context"])
        plan = MissionPlan.from_json(json.loads((SOURCE / case / "planner/canonical-plan.json").read_text()))
        reviewer = ResponsesMissionReviewer(settings, environment, EvidenceTransport(directory, secret), profile)
        review = reviewer.review(assessment.grounded_intent(), plan, catalog, grounding)
        result = {"case": case, "review": review.to_json()}
        write(directory / "result.json", result)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    write(ROOT / "results.json", results)
    checksums = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sorted(ROOT.rglob("*")) if path.is_file() and path.name != "SHA256SUMS.json"}
    write(ROOT / "SHA256SUMS.json", checksums)


if __name__ == "__main__":
    main()
