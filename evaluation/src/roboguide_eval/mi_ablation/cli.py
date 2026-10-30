"""Freeze and consume explicitly selected MI-only trials without implicit reruns."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import random
import subprocess
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings, load_settings
from mission.models import JSONObject, JSONValue

from roboguide_eval.mi_ablation.blind import export_blind
from roboguide_eval.mi_ablation.evidence import digest, read_object, write_new
from roboguide_eval.mi_ablation.runner import run_trial
from roboguide_eval.mi_ablation.scoring import object_value, objects


def file_digest(path: Path) -> str:
    """Read exact file bytes for experiment source identity."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configured_settings(repository: Path, manifest: JSONObject) -> MissionSettings:
    """Apply the common frozen model and budgets without editing production configuration."""
    base = load_settings(repository / "config/mission.toml", repository_root=repository)
    provider = object_value(manifest["provider"])
    return dataclasses.replace(
        base,
        review_enabled=True,
        max_repair_attempts=2,
        prevalidation_recovery_attempts=2,
        llm=dataclasses.replace(
            base.llm,
            model=str(provider["model"]),
            review_model=str(provider["model"]),
            reasoning_effort=str(provider["reasoning_effort"]),
            max_output_tokens=int(cast(int, provider["max_output_tokens"])),
            timeout_seconds=float(cast(float, provider["timeout_seconds"])),
            disable_response_storage=True,
        ),
        provider=dataclasses.replace(
            base.provider,
            base_url=str(provider["base_url"]),
            responses_path=str(provider["responses_path"]),
        ),
    )


def freeze(
    repository: Path, cases_path: Path, directory: Path, split: str, repetitions: int
) -> None:
    """Freeze source, private rubrics, provider settings and all planned slots before calls."""
    if split not in {"development", "evaluation"} or not 1 <= repetitions <= 3:
        raise ValueError("invalid split or repetitions")
    document = read_object(cases_path)
    if document.get("schema_version") != "roboguide.mi-ablation-cases/v0.1":
        raise ValueError("invalid case schema")
    cases = objects(document["cases"])
    if not cases or len(cases) > 64 or len({str(c["id"]) for c in cases}) != len(cases):
        raise ValueError("empty, oversized or duplicate case set")
    if any(c.get("split") != split for c in cases):
        raise ValueError("case split does not match frozen experiment")
    for case in cases:
        name = str(case["id"])
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
            raise ValueError("unsafe case identity")
        intent = object_value(case["grounded_intent"])
        if not intent.get("objective") or not objects(case["checks"]):
            raise ValueError("empty objective or private rubric")
    sources: dict[str, JSONValue] = {}
    for folder in ("mission/src", "mission/prompts", "evaluation/src/roboguide_eval/mi_ablation"):
        for path in sorted((repository / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md"}:
                sources[str(path.relative_to(repository))] = file_digest(path)
    for relative in (
        "config/mission.toml",
        "contracts/mission/v0.8/mission-plan.schema.json",
        "contracts/capability/v0.3/catalog.json",
    ):
        sources[relative] = file_digest(repository / relative)
    slots: list[JSONObject] = [
        {"id": f"{case['id']}-r{replicate}", "case_id": case["id"], "replicate": replicate}
        for case in cases
        for replicate in range(1, repetitions + 1)
    ]
    random.Random(20261030).shuffle(slots)
    manifest: JSONObject = {
        "schema_version": "roboguide.mi-ablation-manifest/v0.1",
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip(),
        "source_sha256": sources,
        "case_digest": digest(document),
        "split": split,
        "replicates": repetitions,
        "slots": slots,
        "shuffle_seed": 20261030,
        "provider": {
            "model": "gpt-6.1-sol",
            "base_url": "http://101.43.45.215:8080",
            "responses_path": "/responses",
            "reasoning_effort": "xhigh",
            "max_output_tokens": 16384,
            "timeout_seconds": 1200,
        },
        "budgets": {
            "prevalidation_regenerations": 2,
            "repair_rounds": 2,
            "max_calls_per_trial": 12,
            "provider_transport_retries": 0,
        },
        "claim_scope": "paired_planner_reviewer_repairer_components_not_end_to_end",
        "semantic_label_status": "private_rubric_requires_independent_blind_review",
        "no_controller_or_devices": True,
    }
    manifest["digest"] = digest(manifest)
    directory.mkdir(parents=True, exist_ok=False)
    write_new(directory / "cases.json", document)
    write_new(directory / "manifest.json", manifest)


def load_frozen(repository: Path, directory: Path) -> tuple[JSONObject, list[JSONObject]]:
    """Refuse source/case drift before any real Provider request."""
    manifest = read_object(directory / "manifest.json")
    payload = {k: v for k, v in manifest.items() if k != "digest"}
    if manifest.get("digest") != digest(payload):
        raise ValueError("manifest digest mismatch")
    for relative, expected in object_value(manifest["source_sha256"]).items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or file_digest(repository / path) != expected:
            raise ValueError("frozen source drift: " + relative)
    cases = read_object(directory / "cases.json")
    if digest(cases) != manifest["case_digest"]:
        raise ValueError("frozen case/rubric drift")
    return manifest, objects(cases["cases"])


def run_slot(repository: Path, directory: Path, slot_id: str) -> JSONObject:
    """Run one predeclared slot once; no shared-prefix work is repeated across branches."""
    manifest, cases = load_frozen(repository, directory)
    slot = next((s for s in objects(manifest["slots"]) if s["id"] == slot_id), None)
    if slot is None:
        raise ValueError("slot is not in the frozen manifest")
    case = next(c for c in cases if c["id"] == slot["case_id"])
    settings = configured_settings(repository, manifest)
    settings.provider.api_key(os.environ)
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    return run_trial(
        case,
        cast(int, slot["replicate"]),
        directory / "trials" / slot_id,
        settings,
        catalog,
        os.environ,
    )


def summarize(directory: Path) -> JSONObject:
    """Retain all planned slots, unavailable outcomes and unreviewed semantics in totals."""
    manifest = read_object(directory / "manifest.json")
    counts: dict[str, JSONObject] = {name: {} for name in ("A", "B", "C")}
    planned, closed, claimed, calls = 0, 0, 0, 0
    for slot in objects(manifest["slots"]):
        planned += 1
        trial = directory / "trials" / str(slot["id"])
        claimed += int((trial / "claim.json").exists())
        if not (trial / "result.json").exists():
            continue
        result = read_object(trial / "result.json")
        closed += 1
        calls += int(cast(int, result["actual_provider_calls"]))
        for name, raw in object_value(result["branches"]).items():
            branch = object_value(raw)
            status = str(branch["status"])
            counts[name][status] = cast(int, counts[name].get(status, 0)) + 1
    return {
        "planned": planned,
        "claimed": claimed,
        "closed": closed,
        "unclosed_claims": claimed - closed,
        "unstarted": planned - claimed,
        "actual_provider_calls": calls,
        "branches": cast(JSONObject, counts),
        "semantic_correctness": "pending_blind_human_review",
        "success_rate": None,
    }


def main() -> None:
    """Expose explicit freeze/run/summary commands; never start a suite implicitly."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("freeze")
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--cases", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--split", choices=["development", "evaluation"], required=True)
    prepare.add_argument("--repetitions", type=int, default=1)
    run = sub.add_parser("run")
    run.add_argument("--repo", type=Path, required=True)
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--slot", required=True)
    blind = sub.add_parser("blind")
    blind.add_argument("--directory", type=Path, required=True)
    blind.add_argument("--output", type=Path, required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.repo.resolve(), args.cases, args.output, args.split, args.repetitions)
    elif args.command == "run":
        run_slot(args.repo.resolve(), args.directory, args.slot)
    elif args.command == "blind":
        print(json.dumps(export_blind(args.directory, args.output), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summarize(args.directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
