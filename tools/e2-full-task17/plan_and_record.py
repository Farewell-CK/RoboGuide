#!/usr/bin/env python3
"""Run RoboGuide Mission Intelligence once and retain raw provider evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import current_environment, load_settings
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.intent import GroundedIntent
from mission.models import JSONObject
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.responses import (
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
    UrllibJsonTransport,
)
from mission.review import MissionReviewRoute, route_mission_review


class RecordingTransport:
    """Record provider requests and responses without persisting authorization."""

    def __init__(self, output: Path) -> None:
        """Create a recorder that writes redacted evidence under ``output``."""
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.inner = UrllibJsonTransport()
        self.count = 0
        self.usage: list[JSONObject] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Send one JSON request and persist its redacted request and raw response."""
        self.count += 1
        call = self.count
        started = time.time()
        request_record = {
            "call": call,
            "url": url,
            "headers": {
                key: value for key, value in headers.items() if key.lower() != "authorization"
            },
            "authorization_present": "Authorization" in headers,
            "payload": payload,
            "started_at_unix": started,
        }
        (self.output / f"call-{call:02d}-request.json").write_text(
            json.dumps(request_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            response = self.inner.post_json(url, headers, payload, timeout_seconds)
        except Exception as error:
            (self.output / f"call-{call:02d}-error.json").write_text(
                json.dumps(
                    {"call": call, "error_type": type(error).__name__, "error": str(error)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise
        response_record = {
            "call": call,
            "elapsed_seconds": time.time() - started,
            "response": response,
        }
        (self.output / f"call-{call:02d}-response.json").write_text(
            json.dumps(response_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        usage = response.get("usage")
        if isinstance(usage, dict):
            self.usage.append(usage)
        return response


def sha256_json(value: JSONObject) -> str:
    """Return a stable SHA-256 digest for a JSON object."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one frozen input artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *arguments: str) -> str:
    """Capture repository provenance without mutating the worktree."""
    return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()


def main() -> int:
    """Generate, review, and record one autonomous MissionPlan."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--objective", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--public-task", default="env4/task17")
    parser.add_argument(
        "--adapter-scope",
        default="COHERENT env4/task17 graph phases through original Get_env_info.step",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.time()
    (output / "run-command.json").write_text(
        json.dumps({"argv": sys.argv}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    settings = load_settings(args.config, repository_root=args.repo)
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    objective = args.objective.read_text(encoding="utf-8").strip()
    (output / "task-input.txt").write_text(objective + "\n", encoding="utf-8")
    provenance = {
        "roboguide_commit": git(args.repo, "rev-parse", "HEAD"),
        "roboguide_status": git(args.repo, "status", "--short"),
        "config_sha256": sha256_file(args.config),
        "catalog_sha256": sha256_file(settings.capability_catalog_path),
        "objective_sha256": sha256_file(args.objective),
        "credentials_recorded": False,
        "adapter_scope": args.adapter_scope,
    }
    (output / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    grounded_intent = GroundedIntent(objective, (), ())
    dialogue = (
        DialogueTurn(
            "turn-e2-full-0001",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            objective,
            0,
        ),
    )
    grounding = EmptyMissionGroundingReader().capture(f"request-{args.mission_id}", dialogue, 0)
    transport = RecordingTransport(output / "llm")
    environment = current_environment()
    planner = ResponsesMissionPlanner(settings, environment, transport)
    reviewer = ResponsesMissionReviewer(settings, environment, transport)
    repairer = ResponsesMissionRepairer(settings, environment, transport)
    reviews: list[JSONObject] = []
    repair_count = 0

    try:
        plan = planner.plan(args.mission_id, grounded_intent, catalog, grounding)
        (output / "draft-01.json").write_text(
            json.dumps(plan.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        while settings.review_enabled:
            review = reviewer.review(grounded_intent, plan, catalog, grounding)
            route = route_mission_review(review)
            reviews.append(
                {
                    "draft": repair_count + 1,
                    "draft_sha256": sha256_json(plan.to_json()),
                    "route": route.value,
                    "review": review.to_json(),
                }
            )
            if route is MissionReviewRoute.APPROVED:
                break
            if route is not MissionReviewRoute.REPAIR:
                raise RuntimeError(f"Mission review terminated with route {route.value}")
            if repair_count >= settings.max_repair_attempts:
                raise RuntimeError("Mission review repair attempts exhausted")
            plan = repairer.repair(
                args.mission_id, grounded_intent, plan, review, catalog, grounding
            )
            repair_count += 1
            (output / f"draft-{repair_count + 1:02d}.json").write_text(
                json.dumps(plan.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        plan_json = plan.to_json()
        (output / "mission-plan.json").write_text(
            json.dumps(plan_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / "reviews.json").write_text(
            json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema": "roboguide.e2-full-planning-run/v0.1",
            "mission_id": args.mission_id,
            "public_task": args.public_task,
            "model": settings.llm.model,
            "review_model": settings.llm.review_model,
            "reasoning_effort": settings.llm.reasoning_effort,
            "prompt_version": settings.prompts.version,
            "model_calls": transport.count,
            "repair_count": repair_count,
            "review_count": len(reviews),
            "usage": transport.usage,
            "wall_time_seconds": time.time() - started,
            "plan_sha256": sha256_json(plan_json),
            "success": True,
        }
        (output / "planning-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        failure = {
            "schema": "roboguide.e2-full-planning-run/v0.1",
            "mission_id": args.mission_id,
            "public_task": args.public_task,
            "model": settings.llm.model,
            "model_calls": transport.count,
            "wall_time_seconds": time.time() - started,
            "success": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        (output / "planning-failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
