"""Fence omitted or contradictory loaded completion profiles before production MI."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

INTEGRATION_ROOT = Path(__file__).parents[1]
if str(INTEGRATION_ROOT) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_ROOT))

from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.relocation_preflight import main, preflight_relocation  # noqa: E402
from habitat_local_eaios.relocation_start import MAX_ARTIFACT_BYTES  # noqa: E402
from test_relocation_start import preflight_run  # noqa: E402


def bound_run(tmp_path: Path) -> Path:
    """Select the bound deployment while preserving fake reset sources from production builders."""
    run = preflight_run(tmp_path)
    used_path = run / "b1-deployment-used.json"
    used = json.loads(used_path.read_bytes())
    used["declaration"].update(
        schema_version="roboguide.e1.b1-deployment/v0.2", relocation_completion_binding=True
    )
    used["relocation_completion_binding"] = True
    used_path.write_text(json.dumps(used))
    required_path = run / "b1-local-execution-profile-required.json"
    required = json.loads(required_path.read_bytes())
    required.update(
        deployment_sha256=hashlib.sha256(used_path.read_bytes()).hexdigest(),
        relocation_completion_binding=True,
    )
    required_path.write_text(json.dumps(required))
    actual_path = run / "evidence/local-how-profile.json"
    actual = json.loads(actual_path.read_bytes())
    actual.update(
        schema_version="roboguide.habitat-local-how-profile/v0.8",
        stage2_execution_feedback_profile="observed-local-skill-feedback/v0.4",
        relocation_completion_profile="exact-object-released-place/v0.1",
    )
    actual_path.write_text(json.dumps(actual))
    (run / "evidence/relocation-completion-readiness.json").write_text(
        json.dumps(
            {
                "schema_version": "roboguide.relocation-completion-readiness/v0.1",
                "profile": "exact-object-released-place/v0.1",
                "ready": True,
                "source": "loaded-place-skills-and-reset-world-readers",
            }
        )
    )
    return run


def test_loaded_bound_profile_is_required_and_evidence_is_preserved(tmp_path: Path) -> None:
    """A matching actual child profile passes read-only and records exact evidence digests."""
    run = bound_run(tmp_path)
    before = {path: path.read_bytes() for path in run.rglob("*.json")}
    result = preflight_relocation(run)
    assert result["valid"] is True
    assert result["schema_version"] == "roboguide.habitat-relocation-preflight/v0.3"
    assert result["local_execution_profile"]["relocation_completion_binding"] is True
    assert (
        result["local_execution_profile"]["completion_readiness_sha256"]
        == hashlib.sha256(
            (run / "evidence/relocation-completion-readiness.json").read_bytes()
        ).hexdigest()
    )
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("local-how-profile.json", "schema_version", "roboguide.habitat-local-how-profile/v0.2"),
        (
            "local-how-profile.json",
            "stage2_execution_feedback_profile",
            "observed-local-skill-feedback/v0.1",
        ),
        ("local-how-profile.json", "relocation_completion_profile", "invented"),
        ("local-how-profile.json", "official_success_authority", "local-completion"),
        ("local-how-profile.json", "reset_route_support_enabled", 0),
        ("relocation-completion-readiness.json", "ready", False),
        ("relocation-completion-readiness.json", "ready", 1),
        ("relocation-completion-readiness.json", "ready", "true"),
        ("relocation-completion-readiness.json", "schema_version", "unknown"),
        ("relocation-completion-readiness.json", "profile", "unbound"),
        ("relocation-completion-readiness.json", "source", "parent-environment"),
        ("relocation-completion-readiness.json", "invented", "field"),
    ],
)
def test_configured_flag_cannot_substitute_actual_readiness(
    tmp_path: Path, name: str, field: str, value: Any
) -> None:
    """Launch flags do not prove loaded code, available readers or actual readiness."""
    run = bound_run(tmp_path)
    path = run / "evidence" / name
    document = json.loads(path.read_bytes())
    document[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(IntegrationError):
        preflight_relocation(run)


@pytest.mark.parametrize("name", ["local-how-profile.json", "relocation-completion-readiness.json"])
@pytest.mark.parametrize("failure", ["missing", "invalid", "oversize"])
def test_loaded_profile_artifacts_are_required_and_bounded(
    tmp_path: Path, name: str, failure: str
) -> None:
    """Missing, malformed and excessive loaded evidence fail before any model or physical action."""
    run = bound_run(tmp_path)
    path = run / "evidence" / name
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"[" if failure == "invalid" else b" " * (MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(IntegrationError):
        preflight_relocation(run)


@pytest.mark.parametrize(
    "mutation",
    [
        "run",
        "deployment-digest",
        "input-digest",
        "integer",
        "missing",
        "schema",
        "used-choice",
        "declaration-choice",
        "rehash-downgrade",
    ],
)
def test_frozen_requirement_cannot_be_substituted(tmp_path: Path, mutation: str) -> None:
    """A stale requirement or rehashed downgrade cannot hide a deployment/child mismatch."""
    run = bound_run(tmp_path)
    path = run / "b1-local-execution-profile-required.json"
    required = json.loads(path.read_bytes())
    if mutation == "missing":
        path.unlink()
    else:
        if mutation == "run":
            required["run_id"] = "another-run"
        elif mutation in {"deployment-digest", "input-digest"}:
            key = "deployment_sha256" if mutation == "deployment-digest" else "frozen_input_sha256"
            required[key] = "0" * 64
        elif mutation == "integer":
            required["relocation_completion_binding"] = 1
        elif mutation == "schema":
            required["schema_version"] = "unknown"
        else:
            used_path = run / "b1-deployment-used.json"
            used = json.loads(used_path.read_bytes())
            if mutation == "declaration-choice":
                used["declaration"]["relocation_completion_binding"] = False
            else:
                used["relocation_completion_binding"] = False
            if mutation == "rehash-downgrade":
                required["relocation_completion_binding"] = False
                used["declaration"]["relocation_completion_binding"] = False
            used_path.write_text(json.dumps(used))
            required["deployment_sha256"] = hashlib.sha256(used_path.read_bytes()).hexdigest()
        path.write_text(json.dumps(required))
    with pytest.raises(IntegrationError):
        preflight_relocation(run)


def test_disabled_profile_does_not_accept_an_unexpected_bound_child(tmp_path: Path) -> None:
    """An explicit original-path arm must not silently load bound completion."""
    run = preflight_run(tmp_path)
    (run / "evidence/relocation-completion-readiness.json").write_text("{}")
    with pytest.raises(IntegrationError, match="unexpected completion"):
        preflight_relocation(run)


def test_profile_failure_is_archived_as_preflight_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve raw child evidence and stop without inventing an official result."""
    run = bound_run(tmp_path)
    actual_path = run / "evidence/local-how-profile.json"
    actual_path.write_text("{}")
    before = {path: path.read_bytes() for path in run.rglob("*.json")}
    monkeypatch.setattr(sys, "argv", ["relocation_preflight", "--run", str(run)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    failure = json.loads((run / "relocation-preflight.json").read_bytes())
    assert failure["valid"] is False
    assert "Local How" in failure["reason"]
    assert "pddl_success" not in failure
    assert not (run / "b1-request-record.json").exists()
    assert {path: path.read_bytes() for path in before} == before


def test_runner_gate_stops_mismatched_child_before_controller_or_mi(tmp_path: Path) -> None:
    """Execute the real shell gate and check harness attribution before submission."""
    run = bound_run(tmp_path)
    actual = run / "evidence/local-how-profile.json"
    actual.write_text("{}")
    root = Path(__file__).resolve().parents[3]
    source = (root / "scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh").read_text()
    start = source.index(
        'if [[ "$RELOCATION_ENABLED" == 1 ]]; then', source.index("wait_http http://")
    )
    end = source.index(
        '\nuv run --project "$REPO" python -m roboguide_eval.b1_deployment_feasibility', start
    )
    assert start < source.index('"$SERVER" 127.0.0.1:') < source.index("--submit-and-wait")
    script = (
        "set -euo pipefail\n"
        + "REPO="
        + shlex.quote(str(root))
        + "\n"
        + "RUN="
        + shlex.quote(str(run))
        + "\n"
        + "RELOCATION_ENABLED=1\n"
        + (
            'trap \'printf "%s %s %s\\n" "${FAILURE_OWNER:-NONE}" '
            '"${FAILURE_COMPONENT:-none}" "${FAILURE_REASON:-none}"\' EXIT\n'
        )
        + source[start:end]
        + '\nprintf "unexpected-controller-or-mi-start\\n"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], cwd=root, capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode != 0
    assert "EXTERNAL_INFRA harness relocation_reset_evidence_invalid" in result.stdout
    assert "unexpected-controller-or-mi-start" not in result.stdout
    assert json.loads((run / "relocation-preflight.json").read_bytes())["valid"] is False
