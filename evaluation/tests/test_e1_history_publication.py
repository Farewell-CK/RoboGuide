"""Exercise historical imports and four-family tables without model or simulator dependencies."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.e1_fairness import PopulationManifest, RunPairingEvidence, validate_pair
from roboguide_eval.e1_history_import import export_history, historical_details
from roboguide_eval.e1_overview import export_overview, overview_inputs, validate_overview
from roboguide_eval.e1_publication import seal_package, validate_package
from roboguide_eval.publication_io import encode, sha256
from test_e1_publication import records


def _save(path: Path, value: Any) -> None:
    """Create isolated source-shaped evidence without overwriting any real archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


def navigation_archive(root: Path) -> Path:
    """Build original and continued code partitions with one unavailable physical outcome."""
    rows: list[dict[str, Any]] = [
        {"pair_id": f"pair-{i}", "episode_id": str(i), "scene_id": "scene-a", "seed": 40}
        for i in range(2)
    ]
    _save(root / "original/PRE_REGISTRATION.json", {"pairs": rows, "code_sha": "1" * 40})
    _save(root / "continued/PRE_REGISTRATION.json", {"pairs": rows[1:], "code_sha": "2" * 40})
    arms = []
    for index, row in enumerate(rows):
        for arm in ("native", "roboguide"):
            directory = (
                root / ("original" if index == 0 else "continued") / "pairs" / row["pair_id"] / arm
            )
            outcome = {
                "arm": arm,
                "pair_id": row["pair_id"],
                "directory": str(directory),
                "elapsed_seconds": 12.5,
                "official_pddl_success": None if index == 1 and arm == "roboguide" else False,
                "simulator_steps": None if index == 1 and arm == "roboguide" else 3,
                "controlled_stop_reason": None,
                "terminal_reason": "native_rl_done",
            }
            _save(directory / "OUTCOME.json", outcome)
            _save(directory / "RUN_MANIFEST.json", {"pair": row, "code_sha": str(index + 1) * 40})
            _save(
                directory / "accounting/chat.jsonl",
                {
                    "schema_version": "roboguide.provider-call-observation/v0.1",
                    "requested_model": "fixture-model",
                    "response_model": "fixture-model",
                    "path": "/chat/completions",
                    "status": 200,
                    "request_utc": "2026-01-01T00:00:00Z",
                    "usage": {"total_tokens": 5},
                },
            )
            arms.append(outcome)
    _save(
        root / "continued/BATCH_REPORT.json",
        {"state": "completed", "active": [], "model": "fixture-model", "arms": arms},
    )
    bindings = root.parent / "bindings.json"
    _save(
        bindings,
        {
            "archives": {"fixture": str(root)},
            "history": {
                "navigation_root": str(root / "continued"),
                "navigation_original_root": str(root / "original"),
            },
        },
    )
    return bindings


def test_navigation_preserves_complete_plan_conditions_and_unknowns(tmp_path: Path) -> None:
    """Import all planned navigation rows, with code partitions and no invented comparability."""
    bindings = navigation_archive(tmp_path / "archive")
    result = export_history(bindings, "navigation", tmp_path / "output", "3" * 40)
    assert result["workloads"] == 2 and result["runs"] == 4 and result["pairs"] == 2
    summary = json.loads((tmp_path / "output/summary.json").read_bytes())
    stats = summary["families"]["navigation"]
    assert stats["groups"]["primary"]["roboguide"]["official"] == {
        "true": 0,
        "false": 1,
        "unavailable": 1,
    }
    assert stats["pair_eligibility"] == {"true": 0, "false": 0, "unavailable": 2}
    assert set(summary["historical_details"]["primary_by_original_code"]) == {"1" * 40, "2" * 40}
    records_ = [
        json.loads(line) for line in (tmp_path / "output/runs-0001.jsonl").read_text().splitlines()
    ]
    assert all("native_rl_done" not in r["failure_codes"] for r in records_)
    assert all(r["admission"] is None for r in records_)
    assert validate_package(tmp_path / "output")["state"] == "PASS"


@pytest.mark.parametrize("defect", ["wrong_code", "duplicate", "wrong_workload", "wrong_outcome"])
def test_navigation_rejects_conflicting_originals(tmp_path: Path, defect: str) -> None:
    """An original collision or changed workload cannot be hidden in a rebuilt phase table."""
    bindings = navigation_archive(tmp_path / "archive")
    report = tmp_path / "archive/continued/BATCH_REPORT.json"
    value = json.loads(report.read_bytes())
    if defect == "duplicate":
        value["arms"].append(copy.deepcopy(value["arms"][0]))
        _save(report, value)
    else:
        directory = Path(value["arms"][0]["directory"])
        name = "OUTCOME.json" if defect == "wrong_outcome" else "RUN_MANIFEST.json"
        file = directory / name
        doc = json.loads(file.read_bytes())
        if defect == "wrong_code":
            doc["code_sha"] = "f" * 40
        elif defect == "wrong_workload":
            doc["pair"]["scene_id"] = "other-scene"
        else:
            doc["official_pddl_success"] = True
        _save(file, doc)
    with pytest.raises(ValueError):
        export_history(bindings, "navigation", tmp_path / "output", "3" * 40)


def test_historical_reporter_scope_does_not_replace_raw_pair_flag() -> None:
    """Retain old raw eligibility and an explicitly narrower original reporter projection."""
    _, runs, pairs, _ = records()
    pairs[0]["original_continuation_eligible"] = True
    manifest = {
        "original_reporter_eligible_pairs": 1,
        "legacy_assessments": [{"historical_assessment": True}],
    }
    details = historical_details(manifest, runs, pairs)
    assert details["original_reporter_eligible_pairs"] == 1
    assert details["original_reporter_both_official_available"] == 0
    assert pairs[0]["recorded_comparison_eligible"] is False
    manifest["original_reporter_eligible_pairs"] = 0
    with pytest.raises(ValueError, match="eligible count"):
        historical_details(manifest, runs, pairs)


def test_manipulation_retains_all_selector_rows_and_separate_assessment(tmp_path: Path) -> None:
    """An older independent official recheck must not overwrite a missing raw outcome."""
    root = tmp_path / "archive"
    fixtures = Path(__file__).parent / "fixtures/e1_fairness"
    population = json.loads((fixtures / "population-mobility-example.json").read_bytes())
    row = population["selector"]["rows"][0]
    identifier = row["pair_id"]
    pair = validate_pair(
        PopulationManifest.from_json(population),
        RunPairingEvidence.from_json(json.loads((fixtures / "run-emos-example.json").read_bytes())),
        RunPairingEvidence.from_json(
            json.loads((fixtures / "run-roboguide-example.json").read_bytes())
        ),
    ).to_json()
    _save(
        root / "MANIFEST.json",
        {"code_sha": "1" * 40, "population_digest": population["digest"], "model": "fixture-model"},
    )
    _save(root / "frozen/population.json", population)
    _save(root / "frozen/MIGRATION_HISTORY.json", {"retained_pairs": {}})
    _save(root / "CONTINUATION_STATUS.json", {"state": "completed"})
    _save(
        root / "FINAL_BATCH_SUMMARY.json",
        {"registered_pairs": len(population["selector"]["rows"]), "comparison_eligible_pairs": 0},
    )
    (root / "continue_and_report.py").write_text(
        'identifier != "pair-0000"\noutcome.get("fatal_failure") is False\n'
    )
    _, arms, _, _ = records()
    for arm in arms:
        arm.update(official_pddl_success=None, system_outcome="Failed")
        directory = root / "pairs" / identifier / arm["arm"]
        arm["process"] = {"directory": str(directory), "elapsed_seconds": 1.0}
        arm["evidence_directory"] = str(directory / "run")
        _save(directory / "launch.json", {"code_sha": "1" * 40})
        _save(directory / "arm-summary.json", arm)
    _save(
        root / "pairs" / identifier / "pair-outcome.json",
        {"arms": arms, "comparison_eligible": False, "fatal_failure": True},
    )
    _save(root / "pairs" / identifier / "pair-manifest.json", pair)
    _save(
        root / "NATIVE_OFFICIAL_RESULT_RECHECK.json",
        {
            "records": [
                {
                    "pair_id": identifier,
                    "episode_id": row["expected_episode_id"],
                    "raw_structured_pddl_success": None,
                    "verified_official_pddl_success": True,
                    "basis": "fixture assessment",
                }
            ]
        },
    )
    bindings = tmp_path / "bindings.json"
    _save(bindings, {"archives": {"original": str(root)}, "history": {"height_root": str(root)}})
    result = export_history(bindings, "height_man", tmp_path / "published", "2" * 40)
    assert result["workloads"] == len(population["selector"]["rows"])
    summary = json.loads((tmp_path / "published/summary.json").read_bytes())
    assert (
        summary["families"]["height_man"]["groups"]["primary"]["emos"]["official"]["unavailable"]
        == 1
    )
    assert summary["historical_details"]["historical_assessment"] == {"true": 1}


def four_family_sources(base: Path) -> list[dict[str, Any]]:
    """Publish four disjoint synthetic families as the complete overview's only input."""
    entries = []
    for family in ("navigation", "height_man", "perception", "rearrangement"):
        jobs, runs, pairs, artifacts = records()
        for row in [*jobs, *runs, *pairs]:
            row["family"] = family
        relative = "cohorts/" + family + "/revisions/r0001"
        root = base / relative
        seal_package(root, {"status": "staging"}, jobs, runs, pairs, artifacts)
        entries.append(
            {
                "kind": "cohort",
                "path": relative,
                "manifest_sha256": sha256((root / "manifest.json").read_bytes()),
            }
        )
    _save(base / "index.json", {"entries": entries})
    return entries


def test_four_family_overview_requires_complete_disjoint_sources(tmp_path: Path) -> None:
    """All four families must be present and exact, rather than silently accepting two."""
    base = tmp_path / "published"
    entries = four_family_sources(base)
    with pytest.raises(ValueError, match="all four"):
        overview_inputs(base, entries[:2])
    with pytest.raises(ValueError, match="duplicate"):
        overview_inputs(base, [*entries, entries[0]])
    assert export_overview(base, base / "overviews/r0001", "1" * 40)["families"] == 4
    assert validate_overview(base, base / "overviews/r0001")["planned_workloads"] == 8
    table = (base / "overviews/r0001/coverage.csv").read_text()
    assert all(
        family in table for family in ("navigation", "height_man", "perception", "rearrangement")
    )


def test_overview_refuses_source_change_after_publication(tmp_path: Path) -> None:
    """Even a self-consistent replacement source cannot borrow a previously pinned revision."""
    base = tmp_path / "published"
    entries = four_family_sources(base)
    export_overview(base, base / "overviews/r0001", "1" * 40)
    manifest = base / entries[0]["path"] / "manifest.json"
    doc = json.loads(manifest.read_bytes())
    doc["changed"] = True
    _save(manifest, doc)
    with pytest.raises(ValueError, match="manifest changed"):
        validate_overview(base, base / "overviews/r0001")
