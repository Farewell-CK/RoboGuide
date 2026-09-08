"""Tests for the CLI commands: doctor, run, and summarize."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import helpers
import pytest
from roboguide_eval.cli import main

LocalConfigFactory = Callable[..., Path]


@pytest.fixture(autouse=True)
def pinned_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin Git provenance for every CLI test so checks stay deterministic.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    monkeypatch.setenv("ROBOGUIDE_EVAL_GIT_SHA", "clifixture000000000000000000000000")


def test_doctor_passes_with_resolvable_fixture_environment(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_local_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor reports PASS for a spec whose system environment resolves."""
    results_root = tmp_path / "results"
    code = main(
        [
            "doctor",
            "--spec",
            str(make_spec()),
            "--config",
            str(fixture_local_config),
            "--results-root",
            str(results_root),
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "PASS experiment spec valid" in output
    assert "PASS results root writable" in output


def test_doctor_fails_when_working_directory_is_missing(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_workdir: Path,
    make_local_config: LocalConfigFactory,
) -> None:
    """Doctor fails with a clear message when the workdir does not exist."""
    gone = fixture_workdir / "gone"
    gone.mkdir()
    config_path = make_local_config(helpers.local_config_yaml(gone))
    gone.rmdir()
    code = main(
        [
            "doctor",
            "--spec",
            str(make_spec()),
            "--config",
            str(config_path),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )
    assert code == 1


def test_doctor_catches_unresolvable_credential_references(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_workdir: Path,
    make_local_config: LocalConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor fails before any episode when a ${VAR} reference is unset."""
    monkeypatch.delenv("ROBOGUIDE_EVAL_MISSING_TOKEN", raising=False)
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            environment_variables={"FIXTURE_TOKEN": "${ROBOGUIDE_EVAL_MISSING_TOKEN}"},
        )
    )
    code = main(
        [
            "doctor",
            "--spec",
            str(make_spec()),
            "--config",
            str(config_path),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )
    assert code == 1
    output = capsys.readouterr().out
    assert "unresolved environment reference" in output
    assert "ROBOGUIDE_EVAL_MISSING_TOKEN" in output


def test_doctor_passes_when_credential_references_resolve(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_workdir: Path,
    make_local_config: LocalConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Doctor reports resolving references when the variables are exported."""
    monkeypatch.setenv("ROBOGUIDE_EVAL_MISSING_TOKEN", "present")
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            environment_variables={"FIXTURE_TOKEN": "${ROBOGUIDE_EVAL_MISSING_TOKEN}"},
        )
    )
    code = main(
        [
            "doctor",
            "--spec",
            str(make_spec()),
            "--config",
            str(config_path),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )
    assert code == 0
    assert "environment references resolve" in capsys.readouterr().out


def test_doctor_fails_when_spec_is_invalid(tmp_path: Path) -> None:
    """Doctor rejects an invalid spec before checking anything else."""
    spec_path = tmp_path / "broken.yaml"
    spec_path.write_text("schema: roboguide-eval.experiment-spec/v0.1\n", encoding="utf-8")
    code = main(
        [
            "doctor",
            "--spec",
            str(spec_path),
            "--results-root",
            str(tmp_path / "results"),
        ]
    )
    assert code == 1


def test_run_command_executes_fixture_system_end_to_end(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_local_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run command produces run directories and a zero exit code."""
    code = main(
        [
            "run",
            "--spec",
            str(make_spec()),
            "--system",
            "emos",
            "--config",
            str(fixture_local_config),
            "--results-root",
            str(tmp_path / "results"),
            "--episode",
            "ep-000",
            "--seed",
            "7",
        ]
    )
    assert code == 0
    run_roots = list((tmp_path / "results" / "fixture-experiment").iterdir())
    assert len(run_roots) == 1
    output = capsys.readouterr().out
    assert "completed 1/1 episode runs" in output


def test_run_command_reports_failure_without_losing_evidence(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_workdir: Path,
    make_local_config: LocalConfigFactory,
) -> None:
    """A failing configured system yields exit code 1 plus full evidence."""
    config_path = make_local_config(
        helpers.local_config_yaml(
            fixture_workdir,
            arguments=["-u", "-c", helpers.FIXTURE_FAILING_SCRIPT, "{output_dir}"],
        )
    )
    code = main(
        [
            "run",
            "--spec",
            str(make_spec()),
            "--system",
            "emos",
            "--config",
            str(config_path),
            "--results-root",
            str(tmp_path / "results"),
            "--episode",
            "ep-000",
            "--seed",
            "7",
        ]
    )
    assert code == 1
    run_roots = list((tmp_path / "results" / "fixture-experiment").iterdir())
    assert len(run_roots) == 1
    assert {item.name for item in run_roots[0].iterdir()} >= {
        "manifest.json",
        "metrics.json",
        "trace.jsonl",
        "stdout.log",
        "stderr.log",
    }


def test_run_command_rejects_undeclared_episode(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_local_config: Path,
) -> None:
    """Undeclared episode overrides fail before any process starts."""
    code = main(
        [
            "run",
            "--spec",
            str(make_spec()),
            "--system",
            "emos",
            "--config",
            str(fixture_local_config),
            "--results-root",
            str(tmp_path / "results"),
            "--episode",
            "missing-episode",
        ]
    )
    assert code == 1
    assert not (tmp_path / "results" / "fixture-experiment").exists()


def test_summarize_json_reports_completed_runs(
    tmp_path: Path,
    make_spec: Callable[..., Path],
    fixture_local_config: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Summarize emits parseable JSON over the runs of a finished experiment."""
    assert (
        main(
            [
                "run",
                "--spec",
                str(make_spec()),
                "--system",
                "emos",
                "--config",
                str(fixture_local_config),
                "--results-root",
                str(tmp_path / "results"),
                "--episode",
                "ep-000",
                "--seed",
                "7",
            ]
        )
        == 0
    )
    capsys.readouterr()
    code = main(
        ["summarize", "--results", str(tmp_path / "results"), "--json"],
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["aggregate"]["total"] == 1
    assert report["aggregate"]["completed"] == 1


def test_summarize_text_reports_empty_results(tmp_path: Path) -> None:
    """Summarize over an empty results root exits nonzero with a report."""
    assert main(["summarize", "--results", str(tmp_path / "empty")]) == 1
