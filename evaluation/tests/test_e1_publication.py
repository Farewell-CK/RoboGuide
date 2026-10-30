"""Fence publication identity, denominator, redaction and reproducibility failures offline."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.e1_publication import (
    seal_package,
    summarize,
    validate_package,
    validate_records,
)
from roboguide_eval.e1_staged_import import provider_projection
from roboguide_eval.publication_io import ArchiveReader, encode, public_safe, sha256, write_rows


def records() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Build one early MI failure, one native success and an explicitly unexecuted workload."""
    workloads = [
        {
            "job_id": "job-a",
            "family": "fixture",
            "pair_id": "pair-a",
            "episode_id": "7",
            "scene_id": "scene-a",
            "seed": "40",
            "population_digest": "sha256:" + "a" * 64,
            "state": "ended",
            "roboguide_run": "run-rg",
            "emos_run": "run-emos",
            "gap_code": "",
            "source_ref": "a-source",
        },
        {
            "job_id": "job-b",
            "family": "fixture",
            "pair_id": "pair-b",
            "episode_id": "8",
            "scene_id": "scene-b",
            "seed": "40",
            "population_digest": "sha256:" + "a" * 64,
            "state": "not_started",
            "roboguide_run": "",
            "emos_run": "",
            "gap_code": "supervisor_paused",
            "source_ref": "a-source",
        },
    ]
    runs: list[dict[str, Any]] = []
    for arm, key in (("roboguide", "run-rg"), ("emos", "run-emos")):
        official = None if arm == "roboguide" else True
        runs.append(
            {
                "run_key": key,
                "job_id": "job-a",
                "family": "fixture",
                "episode_id": "7",
                "arm": arm,
                "purpose": "primary",
                "execution_mode": "fresh",
                "original_run_id": "repeated-local-run-id",
                "original_namespace": "fixture-original",
                "source_directory": "job-a/" + arm,
                "code_sha": "1" * 40,
                "code_identity_basis": "original launch",
                "system_outcome": "Failed" if official is None else "Completed",
                "official_pddl_success": official,
                "official_outcome": "unavailable" if official is None else "true",
                "physical_episode_executed": official is not None,
                "simulator_steps": None if official is None else 9,
                "admission": {
                    "valid_for_formal_population": True,
                    "valid_for_benchmark_population": False,
                }
                if arm == "roboguide"
                else None,
                "elapsed_seconds": None,
                "provider": {
                    "calls_reported": None,
                    "total_tokens": None,
                    "latency_ms_sum": None,
                    "requested_models": [],
                    "reported_models": [],
                },
                "failure_codes": ["MODEL"] if official is None else [],
                "failure_stage": None,
                "retry_of": None,
                "source_ref": "a-source#/arms/0",
                "evidence_refs": ["a-source"],
                "gaps": [],
                "started_utc": None,
                "ended_utc": None,
            }
        )
    pairs = [
        {
            "job_id": "job-a",
            "family": "fixture",
            "runs": ["run-emos", "run-rg"],
            "comparability": "pair_not_comparable",
            "reset_status": "unavailable",
            "recorded_comparison_eligible": False,
        }
    ]
    artifacts = [
        {
            "artifact_id": "a-source",
            "namespace": "fixture-original",
            "logical_path": "original.json",
            "available": True,
            "sha256": "b" * 64,
            "size_bytes": 1,
            "backup_status": "local_only",
            "remote_uri": None,
        }
    ]
    return workloads, runs, pairs, artifacts


def test_early_failure_is_formal_but_not_official_false() -> None:
    """Retain early model failures and unstarted slots without survivor-only admission."""
    workloads, runs, pairs, artifacts = records()
    validate_records(workloads, runs, pairs, artifacts)
    stats = summarize(workloads, runs, pairs)["families"]["fixture"]
    rg = stats["groups"]["primary"]["roboguide"]
    assert stats["planned_workloads"] == 2 and stats["primary_arms_without_run"]["roboguide"] == 1
    assert rg["formal_denominator"] == 1 and rg["official"] == {
        "true": 0,
        "false": 0,
        "unavailable": 1,
    }
    assert rg["provider_calls"]["unknown_runs"] == 1 and rg["elapsed_seconds"]["known"] == 0
    assert stats["eligible_both_official_available"] == 0


def test_retry_success_does_not_replace_failed_primary() -> None:
    """Keep a retry's success and original unavailable in independent result groups."""
    workloads, runs, pairs, artifacts = records()
    retry = copy.deepcopy(runs[0])
    retry.update(
        run_key="run-retry",
        purpose="retry",
        retry_of="run-rg",
        official_pddl_success=True,
        official_outcome="true",
    )
    retry["source_directory"] = "job-a/retry/roboguide"
    runs.append(retry)
    validate_records(workloads, runs, pairs, artifacts)
    stats = summarize(workloads, runs, pairs)["families"]["fixture"]["groups"]
    assert stats["primary"]["roboguide"]["official"]["unavailable"] == 1
    assert stats["retry"]["roboguide"]["official"]["true"] == 1


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "wrong_official",
        "native_admission",
        "retry_as_primary",
        "unresolved_ref",
        "missing_reset",
        "unstarted_fake_run",
        "backup_claim",
        "pair_string",
        "admission_number",
        "availability_number",
        "negative_elapsed",
        "partial_code",
        "physical_string",
    ],
)
def test_invalid_publication_refuses(change: str) -> None:
    """Reject projections that fabricate identity, authority, execution or remote proof."""
    workloads, runs, pairs, artifacts = records()
    if change == "duplicate":
        runs.append(copy.deepcopy(runs[0]))
    elif change == "wrong_official":
        runs[0]["official_outcome"] = "false"
    elif change == "native_admission":
        runs[1]["admission"] = {"valid_for_formal_population": True}
    elif change == "retry_as_primary":
        workloads[0]["roboguide_run"] = "run-emos"
    elif change == "unresolved_ref":
        runs[0]["source_ref"] = "missing"
    elif change == "missing_reset":
        pairs[0]["recorded_comparison_eligible"] = True
    elif change == "unstarted_fake_run":
        workloads[0]["state"] = "not_started"
    elif change == "pair_string":
        pairs[0]["recorded_comparison_eligible"] = "false"
    elif change == "admission_number":
        runs[0]["admission"]["valid_for_formal_population"] = 1
    elif change == "availability_number":
        artifacts[0]["available"] = 1
    elif change == "negative_elapsed":
        runs[0]["elapsed_seconds"] = -1
    elif change == "partial_code":
        runs[0]["code_sha"] = "1234567"
    elif change == "physical_string":
        runs[0]["physical_episode_executed"] = "true"
    else:
        artifacts[0]["backup_status"] = "backup_verified"
    with pytest.raises(ValueError):
        validate_records(workloads, runs, pairs, artifacts)


def test_reused_native_run_not_counted_as_new_execution() -> None:
    """Preserve original identity and reuse status while retaining both system results."""
    workloads, runs, pairs, artifacts = records()
    runs[1]["execution_mode"] = "reused"
    validate_records(workloads, runs, pairs, artifacts)
    values = summarize(workloads, runs, pairs)["families"]["fixture"]["groups"]["primary"]["emos"]
    assert values["fresh_runs"] == 0 and values["reused_runs"] == 1
    assert runs[0]["original_run_id"] == runs[1]["original_run_id"]
    assert len({r["run_key"] for r in runs}) == 2


def test_unknown_pair_eligibility_remains_unavailable() -> None:
    """Missing historical comparability is neither an accepted pair nor a recorded refusal."""
    workloads, runs, pairs, artifacts = records()
    pairs[0]["recorded_comparison_eligible"] = None
    validate_records(workloads, runs, pairs, artifacts)
    stats = summarize(workloads, runs, pairs)["families"]["fixture"]
    assert stats["pair_eligibility"] == {"true": 0, "false": 0, "unavailable": 1}


def test_official_true_can_coexist_with_system_failure() -> None:
    """Publish official authority independently of local and Mission outcomes."""
    workloads, runs, pairs, artifacts = records()
    runs[0].update(official_pddl_success=True, official_outcome="true")
    validate_records(workloads, runs, pairs, artifacts)
    assert (
        summarize(workloads, runs, pairs)["families"]["fixture"]["groups"]["primary"]["roboguide"][
            "official"
        ]["true"]
        == 1
    )
    assert runs[0]["system_outcome"] == "Failed"


def test_package_recomputes_without_original_sources(tmp_path: Path) -> None:
    """Produce byte-identical revisions and validate them without accessing an archive."""
    workloads, runs, pairs, artifacts = records()
    for name in ("first", "second"):
        seal_package(tmp_path / name, {"status": "staging"}, workloads, runs, pairs, artifacts)
        assert validate_package(tmp_path / name)["runs"] == 2
        assert b"\r\n" not in (tmp_path / name / "coverage.csv").read_bytes()
        assert b"\r\n" not in (tmp_path / name / "workloads.csv").read_bytes()
    assert (tmp_path / "first/SHA256SUMS").read_bytes() == (
        tmp_path / "second/SHA256SUMS"
    ).read_bytes()
    with pytest.raises(ValueError, match="new directory"):
        seal_package(tmp_path / "first", {}, workloads, runs, pairs, artifacts)


def test_rehashed_wrong_aggregate_still_refuses(tmp_path: Path) -> None:
    """Hashes alone cannot make a statistically inconsistent report pass recomputation."""
    seal_package(tmp_path / "release", {"status": "staging"}, *records())
    root = tmp_path / "release"
    doc = json.loads((root / "summary.json").read_bytes())
    doc["families"]["fixture"]["planned_workloads"] = 99
    (root / "summary.json").write_bytes(encode(doc))
    (root / "SHA256SUMS").write_text(
        "".join(
            f"{sha256(p.read_bytes())}  {p.name}\n"
            for p in sorted(root.iterdir())
            if p.name != "SHA256SUMS"
        )
    )
    with pytest.raises(ValueError, match="recompute"):
        validate_package(root)


@pytest.mark.parametrize(
    "value",
    [
        {"Authorization": "redacted"},
        {"argv": []},
        "Bearer example-token",
        "/home/private/raw.log",
        "https://example.test/?token=secret",
    ],
)
def test_sensitive_projection_refuses(value: Any) -> None:
    """Reject unsafe metadata instead of trusting filenames or directory ignore rules."""
    with pytest.raises(ValueError):
        public_safe(value)


def test_reader_detects_drift_missing_and_symlink(tmp_path: Path) -> None:
    """A source changing after reading or escaping through a link cannot seal a publication."""
    root = tmp_path / "archive"
    root.mkdir()
    p = root / "original.json"
    p.write_text("{}")
    reader = ArchiveReader({"original": root})
    reader.document(p)
    assert reader.document(root / "missing.json", optional=True)[0] is None
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        reader.verify_unchanged()
    (root / "link.json").symlink_to(p)
    with pytest.raises(ValueError, match="symlink"):
        reader.document(root / "link.json")


def test_provider_metadata_keeps_usage_gaps_and_hides_message_body(tmp_path: Path) -> None:
    """Extract only actual safe metadata, never credentials, raw errors or message content."""
    raw = {
        "schema_version": "roboguide.provider-call-observation/v0.1",
        "requested_model": "fixture-model",
        "response_model": None,
        "path": "/responses",
        "status": 502,
        "usage": None,
        "latency_ms": 900.0,
        "request_parameters": {"max_output_tokens": 100, "Authorization": "must-not-export"},
        "error": "private body",
        "messages": [{"content": "must-not-export"}],
    }
    (tmp_path / "provider-accounting.jsonl").write_bytes(encode(raw))
    data, _ = provider_projection(ArchiveReader({"fixture": tmp_path}), tmp_path)
    assert data["total_tokens"] is None and data["reported_models"] == []
    assert "must-not-export" not in json.dumps(data)
    assert data["api_counts"][0]["http_status"] == 502


def test_large_records_shard_without_dropping_failures(tmp_path: Path) -> None:
    """Enforce per-file size while retaining complete, ordered source records."""
    rows: list[dict[str, Any]] = [
        {"value": "x" * 700000, "official": False},
        {"value": "y" * 700000, "official": None},
    ]
    files = write_rows(tmp_path, "records", rows)
    assert len(files) == 2
    assert [json.loads((tmp_path / f).read_bytes()) for f in files] == rows


def test_relabelled_copy_cannot_count_as_new_execution() -> None:
    """Changing a run key cannot make the same original directory a distinct retry."""
    workloads, runs, pairs, artifacts = records()
    repeated = copy.deepcopy(runs[0])
    repeated.update(run_key="new-name", purpose="retry", retry_of="run-rg")
    runs.append(repeated)
    with pytest.raises(ValueError, match="relabelled"):
        validate_records(workloads, runs, pairs, artifacts)


def test_structural_schema_matches_runtime_record_generation() -> None:
    """Publish a versioned machine-readable shape without replacing relational checks."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
    from roboguide_eval.e1_publication import RUN_FIELDS

    schema = json.loads(
        (Path(__file__).parents[1] / "schemas/e1-result-publication-v0.1.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    assert set(schema["$defs"]["run"]["required"]) == RUN_FIELDS
    _, runs, _, artifacts = records()
    for row in [*runs, *artifacts]:
        Draft202012Validator(schema).validate(row)


def test_committed_phase_packages_are_recomputable() -> None:
    """CI recomputes every committed revision with zero models, archives or simulators."""
    from roboguide_eval.e1_census import validate_census

    base = Path(__file__).parents[1] / "reports/e1"
    index = base / "index.json"
    if index.exists():
        for entry in json.loads(index.read_bytes())["entries"]:
            relative = Path(entry["path"])
            assert not relative.is_absolute() and ".." not in relative.parts
            manifest = base / relative / "manifest.json"
            assert sha256(manifest.read_bytes()) == entry["manifest_sha256"]
    for manifest in sorted((base / "cohorts").glob("*/revisions/*/manifest.json")):
        assert validate_package(manifest.parent)["state"] == "PASS"
    for manifest in sorted((base / "inventories").glob("*/manifest.json")):
        assert validate_census(manifest.parent)["state"] == "PASS"


def test_staged_adapter_imports_full_plan_without_launching(tmp_path: Path) -> None:
    """Exercise actual import over isolated original-shaped files, including unstarted rows."""
    from roboguide_eval.e1_staged_import import export_staged

    root = tmp_path / "original"
    root.mkdir()
    population = json.loads(
        (
            Path(__file__).parent / "fixtures/e1_fairness/population-mobility-example.json"
        ).read_bytes()
    )
    path = root / "frozen/fixture/population.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(encode(population))
    jobs = [
        {"family": "fixture", "job_id": f"job-{i}", **row}
        for i, row in enumerate(population["selector"]["rows"])
    ]
    (root / "CASES.json").write_bytes(encode(jobs))
    (root / "RUN_MANIFEST.json").write_bytes(
        encode(
            {
                "schema_version": "roboguide.e1.staged-cohort/v0.1",
                "code_sha": "1" * 40,
                "primary_roboguide_arms": len(jobs),
                "registered_utc": "2026-01-01T00:00:00Z",
            }
        )
    )
    (root / "STATUS.json").write_bytes(
        encode(
            {
                "active": [],
                "source_sha": "1" * 40,
                "state": "paused",
                "updated_utc": "2026-01-01T00:01:00Z",
            }
        )
    )
    (root / "NATIVE_REFERENCE_SELECTION.json").write_text("{}")
    _, arms, _, _ = records()
    job = jobs[0]
    for arm in arms:
        directory = root / "cases" / job["job_id"] / arm["arm"]
        directory.mkdir(parents=True)
        arm["process"] = {"directory": str(directory), "elapsed_seconds": 1.0}
        arm["evidence_directory"] = str(directory / "run")
        (directory / "arm-summary.json").write_bytes(encode(arm))
        (directory / "launch.json").write_bytes(encode({"code_sha": "1" * 40}))
    outcome = {
        "job_id": job["job_id"],
        "episode_id": job["expected_episode_id"],
        "code_sha": "1" * 40,
        "arms": arms,
        "retry_arms": [],
        "native_reference_mode": "fresh_original_native",
    }
    (root / "cases" / job["job_id"] / "pair-outcome.json").write_bytes(encode(outcome))
    bindings = tmp_path / "bindings.json"
    bindings.write_bytes(encode({"archives": {"original": str(root)}}))
    result = export_staged(bindings, "original", tmp_path / "published", "2" * 40)
    assert result["workloads"] == len(jobs) and result["runs"] == 2
    summary = json.loads((tmp_path / "published/summary.json").read_bytes())
    assert summary["families"]["fixture"]["coverage_states"]["not_started"] == len(jobs) - 1
    assert not list((tmp_path / "published").glob("*local-source-witness*"))
    assert (bindings.parent / "original-published-local-source-witness.json").is_file()
    manifest = json.loads((tmp_path / "published/manifest.json").read_bytes())
    assert manifest["publication"]["prepared_utc"] is None
    assert "prepared_utc" in manifest["publication"]["missing_receipt_fields"]
    emitted = [
        json.loads(line)
        for name in manifest["record_files"]["runs"]
        for line in (tmp_path / "published" / name).read_text().splitlines()
    ]
    assert all(row["started_utc"] is None and row["ended_utc"] is None for row in emitted)


def test_export_receipt_is_separate_frozen_metadata() -> None:
    """Freeze actual export provenance without changing or guessing historical run clocks."""
    from roboguide_eval.publication_io import publication_metadata

    receipt = {
        "prepared_utc": "2026-10-30T01:00:00Z",
        "prepared_by": "fixture-maintainer",
        "python_version": "3.13.0",
        "dependency_lock_sha256": "a" * 64,
    }
    metadata = publication_metadata({"publication": receipt})
    assert metadata["missing_receipt_fields"] == []
    assert {k: metadata[k] for k in receipt} == receipt
    receipt["prepared_utc"] = "2026-10-30T01:00:00"
    with pytest.raises(ValueError, match="timezone"):
        publication_metadata({"publication": receipt})
