"""Bounded, read-only B1 evidence collection, including early process failures."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path
from typing import Any

from roboguide_eval.b1_admission import FailureOwner
from roboguide_eval.b1_provenance import (
    RUN_FAILURE_SCHEMA,
    build_b1_provenance_record,
    load_document,
    plan_digest,
    write_b1_provenance,
)
from roboguide_eval.b1_run import write_b1_verdict


def _fetch(url: str) -> Any:
    """Read one bounded local status endpoint without changing execution state."""
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return None
        return json.loads(raw)
    except (OSError, ValueError, HTTPException):
        return None


def _write(path: Path, value: Any) -> None:
    """Archive a successful observation; unavailable reads never replace prior evidence."""
    if value is not None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect_b1_artifacts(
    run: Path,
    *,
    request_id: str,
    mission_endpoint: str,
    controller_endpoint: str,
    owner: FailureOwner = FailureOwner.NONE,
    component: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Archive matching request/observations before provenance and canonical admission."""
    request: Any = load_document(run / "b1-request-record.json")
    if request_id:
        for _ in range(3):
            current = _fetch(f"{mission_endpoint}/v1/mission-requests/{request_id}")
            observations = _fetch(
                f"{mission_endpoint}/v1/mission-requests/{request_id}/observations"
            )
            if (
                isinstance(current, dict)
                and isinstance(observations, dict)
                and observations.get("request_record_digest") == plan_digest(current)
            ):
                request = current
                _write(run / "b1-request-record.json", current)
                _write(run / "b1-request-observations.json", observations)
                break
    request = request if isinstance(request, dict) else {}
    mission_id = request.get("mission_id")
    if isinstance(mission_id, str):
        _write(run / "mission.json", _fetch(f"{controller_endpoint}/v1/missions/{mission_id}"))
    for filename, endpoint in (
        ("events.json", "events"),
        ("execution-attempts.json", "execution-attempts"),
    ):
        _write(run / filename, _fetch(f"{controller_endpoint}/v1/{endpoint}"))
    if owner is not FailureOwner.NONE:
        observations = load_document(run / "b1-request-observations.json") or {}
        sent = observations.get("submission_evidence") or {}
        _write(
            run / "run-failure.json",
            {
                "schema_version": RUN_FAILURE_SCHEMA,
                "run_id": run.name,
                "failure_owner": owner.value,
                "component": component,
                "reason": reason,
                "request_id": request.get("request_id"),
                "mission_id": mission_id,
                "group_id": sent.get("controller_group_id"),
                "input_digest": plan_digest(load_document(run / "b1-input-used.json")),
                "observation_source": "scenario_process_boundary",
            },
        )
    record = build_b1_provenance_record(
        run_id=run.name,
        frozen_input_path=run / "b1-input-used.json",
        request_record_path=run / "b1-request-record.json",
        request_observations_path=run / "b1-request-observations.json",
        controller_mission_path=run / "mission.json",
        controller_events_path=run / "events.json",
        execution_attempts_path=run / "execution-attempts.json",
        shared_world_summary_path=run / "evidence/shared-world-summary.json",
        semantic_evidence_path=run / "evidence/authoritative-semantic-evidence.json",
        failure_evidence_path=run / "run-failure.json",
    )
    write_b1_provenance(record, run / "b1-provenance.json")
    return write_b1_verdict(run)


def main() -> None:
    """Collect only already-produced evidence; never invoke MI or the simulator."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--request-id", default="")
    parser.add_argument("--mission-endpoint", default="http://127.0.0.1:8070")
    parser.add_argument("--controller-endpoint", default="http://127.0.0.1:28060")
    parser.add_argument("--failure-owner", type=FailureOwner, default=FailureOwner.NONE)
    parser.add_argument("--component", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    result = collect_b1_artifacts(
        args.run,
        request_id=args.request_id,
        mission_endpoint=args.mission_endpoint,
        controller_endpoint=args.controller_endpoint,
        owner=args.failure_owner,
        component=args.component,
        reason=args.reason,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
