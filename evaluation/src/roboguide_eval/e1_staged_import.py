"""Import an archived staged E1 batch through bounded, read-only source adapters."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from roboguide_eval.e1_fairness import PairManifest, PopulationManifest
from roboguide_eval.e1_publication import seal_package, validate_package
from roboguide_eval.publication_io import (
    ArchiveReader,
    boolean,
    code_identity,
    encode,
    number,
    object_value,
    public_safe,
    sha256,
    write_new,
)


def _label(value: Any) -> str | None:
    """Retain bounded non-sensitive identifiers, never arbitrary error text or local paths."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:@+ /-]{1,160}", value):
        return None
    try:
        public_safe(value)
    except ValueError:
        return None
    return value


def provider_projection(reader: ArchiveReader, directory: Path) -> tuple[dict[str, Any], list[str]]:
    """Aggregate actual accounting metadata without copying prompts, responses or headers."""
    raw, reference = reader.read(directory / "provider-accounting.jsonl", optional=True)
    calls: list[dict[str, Any]] = []
    if raw is not None:
        for line in raw.splitlines():
            if line.strip():
                if len(line) > 1024 * 1024 or len(calls) >= 20000:
                    raise ValueError("provider accounting exceeds bounded source format")
                row = object_value(json.loads(line))
                if row.get("schema_version") != "roboguide.provider-call-observation/v0.1":
                    raise ValueError("unsupported accounting observation schema")
                calls.append(row)
    usage = [number((row.get("usage") or {}).get("total_tokens")) for row in calls]
    latency = [number(row.get("latency_ms")) for row in calls]
    grouped: Counter[tuple[Any, ...]] = Counter()
    for row in calls:
        grouped[
            (
                row.get("path"),
                _label(row.get("requested_model")),
                _label(row.get("response_model")),
                row.get("status"),
            )
        ] += 1
    projection = {
        "calls_archived": len(calls) if raw is not None else None,
        "total_tokens": sum(v for v in usage if v is not None)
        if any(v is not None for v in usage)
        else None,
        "usage_known_calls": sum(v is not None for v in usage),
        "latency_ms_sum": round(sum(v for v in latency if v is not None), 6)
        if any(v is not None for v in latency)
        else None,
        "requested_models": sorted(
            {v for row in calls if (v := _label(row.get("requested_model")))}
        ),
        "reported_models": sorted({v for row in calls if (v := _label(row.get("response_model")))}),
        "api_counts": [
            {
                "path": path
                if path in ("/responses", "/v1/chat/completions", "/chat/completions")
                else "other",
                "requested_model": requested,
                "reported_model": reported,
                "http_status": status,
                "count": count,
            }
            for (path, requested, reported, status), count in sorted(
                grouped.items(), key=lambda item: str(item[0])
            )
        ],
        "first_request_utc": calls[0].get("request_utc") if calls else None,
        "last_response_utc": calls[-1].get("response_utc") if calls else None,
        "parameter_variants": [],
    }
    variants: dict[str, dict[str, Any]] = {}
    for row in calls:
        params = row.get("request_parameters") or {}
        allowed = {
            key: params[key]
            for key in (
                "max_output_tokens",
                "max_tokens",
                "temperature",
                "top_p",
                "reasoning_effort",
                "store",
            )
            if key in params
        }
        public_safe(allowed)
        variants[sha256(encode(allowed))] = allowed
    projection["parameter_variants"] = [variants[key] for key in sorted(variants)]
    return projection, [reference]


def _admission(value: Any) -> dict[str, Any] | None:
    """Project only original admission fields without recomputing or inventing permission."""
    if value is None:
        return None
    raw = object_value(value)
    allowed = (
        "schema_version",
        "provenance_valid",
        "valid_for_formal_population",
        "valid_for_benchmark_population",
        "benchmark_authority_available",
        "benchmark_outcome",
        "run_validity",
        "failure_owner",
        "system_failure",
        "model_failure",
    )
    return {key: raw.get(key) for key in allowed}


def _run(
    reader: ArchiveReader,
    batch: Path,
    job: dict[str, Any],
    raw: dict[str, Any],
    source_ref: str,
    purpose: str,
    reused: bool,
    retry_of: str | None,
) -> dict[str, Any]:
    """Join one original execution and its raw artifacts, preserving all outcome layers."""
    arm = raw["arm"]
    directory = Path(object_value(raw["process"])["directory"])
    namespace, logical = reader.locate(directory)
    run_key = "run-" + sha256((namespace + "/" + logical + "/" + arm).encode())[:32]
    launch, launch_ref = reader.document(directory / "launch.json", optional=True)
    summary, summary_ref = reader.document(directory / "arm-summary.json", optional=True)
    if summary is not None:
        for field in ("arm", "official_pddl_success", "system_outcome"):
            if summary.get(field) != raw.get(field):
                raise ValueError("embedded and original arm facts disagree")
    else:
        summary = raw
    launch = object_value(launch or {})
    provider, evidence_refs = provider_projection(reader, directory)
    evidence_refs += [launch_ref, summary_ref]
    provider["calls_reported"] = number(raw.get("provider_calls"))
    provider["model_identity_verified_as_recorded"] = boolean(raw.get("model_identity_verified"))
    closure = (raw.get("process") or {}).get("provider_closure") or {}
    provider["accounting_complete_as_recorded"] = boolean(
        (closure.get("accounting") or {}).get("complete")
    )
    provider["body_capture_complete_as_recorded"] = boolean(
        (closure.get("body_capture") or {}).get("complete")
    )
    code = code_identity(launch.get("code_sha"))
    gaps = []
    if not code:
        gaps.append("original_launch_code_identity_unavailable")
    if provider["calls_archived"] != provider["calls_reported"]:
        gaps.append("provider_accounting_count_unavailable_or_mismatch")
    process = object_value(raw.get("process") or {})
    admission = _admission(raw.get("admission"))
    failure_codes = [_label(value) for value in raw.get("fatal_reasons", [])]
    failure_codes = [value for value in failure_codes if value]
    if admission:
        if admission.get("run_validity") not in (None, "VALID_RUN"):
            failure_codes.append(str(admission["run_validity"]))
        if admission.get("failure_owner") not in (None, "NONE"):
            failure_codes.append(str(admission["failure_owner"]))
    for call in provider["api_counts"]:
        if type(call["http_status"]) is int and call["http_status"] >= 400:
            failure_codes.append("provider_http_" + str(call["http_status"]))
    evidence = Path(raw.get("evidence_directory") or str(directory / "run"))
    if arm == "roboguide":
        terminal_path = evidence / "evidence/shared-world-summary.json"
        official_path = terminal_path
    else:
        official_path = evidence / "native-outcome.json"
    official, official_ref = reader.document(official_path, optional=True)
    evidence_refs.append(official_ref)
    if official is not None and boolean(official.get("official_pddl_success")) != boolean(
        raw.get("official_pddl_success")
    ):
        gaps.append("original_summary_and_terminal_official_disagree")
    if raw.get("official_pddl_success") is not None and official is None:
        gaps.append("direct_terminal_outcome_unavailable_use_recorded_arm_authority")
    failure_stage = None
    if arm == "roboguide":
        for name in ("run-failure.json", "evidence/live-world-failure.json"):
            failure, reference = reader.document(evidence / name, optional=True)
            evidence_refs.append(reference)
            if failure:
                failure_stage = (
                    _label(failure.get("failure_stage") or failure.get("stage")) or failure_stage
                )
                value = _label(failure.get("failure_basis"))
                if value:
                    failure_codes.append(value)
        verdict, reference = reader.document(evidence / "b1-verdict.json", optional=True)
        evidence_refs.append(reference)
        if (
            verdict is not None
            and verdict.get("admission") is not None
            and _admission(verdict["admission"]) != admission
        ):
            gaps.append("recorded_verdict_and_summary_admission_disagree")
    original_id = (closure.get("accounting") or {}).get("run_id")
    if not original_id:
        original_id = raw.get("run_id")
    if not original_id:
        gaps.append("original_run_id_unavailable_directory_qualified_import_reference")
    if (
        raw.get("system_outcome") in ("Failed", "FAILURE", "UNKNOWN", "unknown")
        and not failure_stage
    ):
        gaps.append("exact_failure_stage_unavailable")
    return {
        "run_key": run_key,
        "job_id": job["job_id"],
        "family": job["family"],
        "episode_id": job["expected_episode_id"],
        "arm": arm,
        "purpose": purpose,
        "execution_mode": "reused" if reused else "fresh",
        "original_run_id": original_id,
        "original_namespace": namespace,
        "source_directory": logical,
        "code_sha": code,
        "code_identity_basis": "original launch.code_sha"
        if code
        else "unavailable; never inferred from current main",
        "system_outcome": raw.get("system_outcome") or "UNKNOWN",
        "official_pddl_success": boolean(raw.get("official_pddl_success")),
        "official_outcome": "unavailable"
        if type(raw.get("official_pddl_success")) is not bool
        else str(raw["official_pddl_success"]).lower(),
        "physical_episode_executed": boolean(raw.get("physical_episode_executed")),
        "simulator_steps": number(raw.get("simulator_steps")),
        "admission": admission,
        "elapsed_seconds": number(process.get("elapsed_seconds")),
        "provider": provider,
        "failure_codes": sorted(set(failure_codes)),
        "failure_stage": failure_stage,
        "retry_of": retry_of,
        "source_ref": source_ref,
        "evidence_refs": sorted(set(evidence_refs)),
        "gaps": sorted(set(gaps)),
        "started_utc": provider["first_request_utc"],
        "ended_utc": provider["last_response_utc"],
    }


def export_staged(
    bindings: Path, source_id: str, output: Path, exporter_sha: str
) -> dict[str, Any]:
    """Consume every planned slot, preserving pre-arm failure and separate retries."""
    if code_identity(exporter_sha) is None:
        raise ValueError("exporter needs a complete committed source identity")
    local = object_value(json.loads(bindings.read_bytes()))
    reader = ArchiveReader(
        {key: Path(path) for key, path in object_value(local["archives"]).items()}
    )
    batch = reader.roots[source_id]
    if any(
        output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve())
        for root in reader.roots.values()
    ):
        raise ValueError("publication output must not overlap a source archive")
    original, original_ref = reader.document(batch / "RUN_MANIFEST.json")
    status, status_ref = reader.document(batch / "STATUS.json")
    cases, cases_ref = reader.document(batch / "CASES.json")
    if original.get("schema_version") != "roboguide.e1.staged-cohort/v0.1" or status.get("active"):
        raise ValueError("unsupported or still-active original batch")
    if original["code_sha"] != status["source_sha"] or original["primary_roboguide_arms"] != len(
        cases
    ):
        raise ValueError("frozen batch identity or complete plan count disagrees")
    references, reference_ref = reader.document(batch / "NATIVE_REFERENCE_SELECTION.json")
    populations = {}
    for family in sorted({case["family"] for case in cases}):
        doc, ref = reader.document(batch / "frozen" / family / "population.json")
        parsed = PopulationManifest.from_json(doc)
        declared = {
            (row["pair_id"], row["expected_episode_id"], row["expected_scene_id"], row["seed"])
            for row in doc["selector"]["rows"]
        }
        selected = [row for row in cases if row["family"] == family]
        actual = {
            (row["pair_id"], row["expected_episode_id"], row["expected_scene_id"], row["seed"])
            for row in selected
        }
        if declared != actual or len(actual) != len(selected):
            raise ValueError("frozen case list does not cover the original population selector")
        populations[family] = {
            "digest": parsed.digest,
            "source_ref": ref,
            "population_id": doc["population_id"],
            "dataset": doc["dataset"],
            "task": doc["task"],
            "model_configuration": doc["model_configuration"],
            "protocol": doc["protocol"],
            "benchmark_authority_digest": sha256(encode(doc["benchmark_authority"])),
            "stage2_identity_digest": sha256(encode(doc["stage2_identity"])),
        }
    workloads = []
    runs = []
    pairs = []
    for ordinal, job in enumerate(cases):
        row = {
            "job_id": job["job_id"],
            "family": job["family"],
            "pair_id": job["pair_id"],
            "episode_id": job["expected_episode_id"],
            "scene_id": job["expected_scene_id"],
            "seed": str(job["seed"]),
            "population_digest": populations[job["family"]]["digest"],
            "state": "unknown",
            "roboguide_run": "",
            "emos_run": "",
            "gap_code": "",
            "source_ref": cases_ref + f"#/{ordinal}",
        }
        directory = batch / "cases" / job["job_id"]
        outcome, outcome_ref = reader.document(directory / "pair-outcome.json", optional=True)
        if outcome is None:
            row["state"] = (
                "not_started"
                if status.get("state") == "paused" and not directory.exists()
                else "unknown"
            )
            row["gap_code"] = (
                "supervisor_paused_no_job_directory"
                if row["state"] == "not_started"
                else "original_outcome_inaccessible"
            )
            workloads.append(row)
            continue
        if (
            outcome.get("code_sha") != original["code_sha"]
            or str(outcome.get("episode_id")) != job["expected_episode_id"]
            or outcome.get("job_id") != job["job_id"]
        ):
            raise ValueError("original outcome detached from frozen plan")
        row["state"] = "ended" if outcome.get("arms") else "pre_arm_failure"
        if not outcome.get("arms"):
            row["gap_code"] = "original_pre_arm_" + (_label(outcome.get("error_type")) or "unknown")
        for index, raw in enumerate(outcome.get("arms", [])):
            reused = (
                raw["arm"] == "emos" and outcome["native_reference_mode"] == "reuse_original_native"
            )
            record = _run(
                reader, batch, job, raw, outcome_ref + f"#/arms/{index}", "primary", reused, None
            )
            if reused:
                selection = references[job["job_id"]]
                if (
                    Path(selection["original_directory"]).resolve()
                    != Path(raw["process"]["directory"]).resolve()
                ):
                    raise ValueError("native reuse detached from frozen reference selection")
                record["evidence_refs"].append(reference_ref + "#/" + job["job_id"])
            runs.append(record)
            row[raw["arm"] + "_run"] = record["run_key"]
        for index, raw in enumerate(outcome.get("retry_arms", [])):
            origin = row[raw["arm"] + "_run"]
            runs.append(
                _run(
                    reader,
                    batch,
                    job,
                    raw,
                    outcome_ref + f"#/retry_arms/{index}",
                    "retry",
                    False,
                    origin or None,
                )
            )
        if row["emos_run"] and row["roboguide_run"]:
            doc, pair_ref = reader.document(directory / "pair-manifest.json", optional=True)
            if doc is not None:
                parsed_pair = PairManifest.from_json(doc)
                if (
                    parsed_pair.population_manifest_digest != populations[job["family"]]["digest"]
                    or parsed_pair.pair_id != job["pair_id"]
                ):
                    raise ValueError("pair manifest detached from population")
            reset = outcome.get("reset_comparison") or {}
            pairs.append(
                {
                    "job_id": job["job_id"],
                    "family": job["family"],
                    "runs": [row["emos_run"], row["roboguide_run"]],
                    "comparability": doc.get("comparability") if doc else None,
                    "recorded_comparison_eligible": outcome.get("comparison_eligible") is True,
                    "reset_status": reset.get("status"),
                    "orientation_evidence": "legacy-rotation-scalar-only",
                    "source_refs": [outcome_ref, pair_ref],
                    "strict_organization_only_claim": False,
                }
            )
        workloads.append(row)
    # Smoke is a real diagnostic execution, never another primary result for Episode19.
    smoke, smoke_ref = reader.document(batch / "smoke/pair-outcome.json", optional=True)
    if smoke:
        diagnostic_job = next(
            job
            for job in cases
            if job["family"] == smoke["family"]
            and job["expected_episode_id"] == str(smoke["episode_id"])
        )
        for index, raw in enumerate(smoke.get("arms", [])):
            if raw["arm"] == "roboguide":
                runs.append(
                    _run(
                        reader,
                        batch,
                        diagnostic_job,
                        raw,
                        smoke_ref + f"#/arms/{index}",
                        "diagnostic",
                        False,
                        None,
                    )
                )
    reader.verify_unchanged()
    manifest = {
        "cohort_id": source_id,
        "revision": "r0001",
        "status": "staging",
        "purpose": "primary",
        "exporter_code_sha": exporter_sha,
        "source_code_sha": original["code_sha"],
        "original_registered_utc": original["registered_utc"],
        "original_closed_status_utc": status["updated_utc"],
        "source_refs": [original_ref, status_ref, cases_ref, reference_ref],
        "populations": populations,
        "selection": (
            "complete frozen batch selected for accessible plan, accounting and pair evidence; "
            "not selected by scores"
        ),
        "source_format": "roboguide.e1.staged-cohort/v0.1",
        "import_mode": "retrospective_read_only",
        "raw_backup_status": "local_only",
        "claim_scope": (
            "Protocol-A whole-system descriptive comparison; "
            "no organization-only causal claim or exact paper reproduction"
        ),
        "new_provider_simulator_runs": 0,
    }
    seal_package(output, manifest, workloads, runs, pairs, list(reader.artifacts.values()))
    reader.verify_unchanged()
    # This local receipt binds namespaces to actual files and never belongs in the publication.
    local_receipt = bindings.parent / (source_id + "-" + output.name + "-local-source-witness.json")
    write_new(
        local_receipt,
        encode(
            {
                "archives": {key: str(root) for key, root in reader.roots.items()},
                "sources": {key: str(path) for key, path in reader.paths.items()},
                "original_bytes_unchanged": True,
            }
        ),
    )
    return validate_package(output)
