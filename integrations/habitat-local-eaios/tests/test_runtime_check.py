"""Run the actual deployment-Python HTTP boundary without Habitat or a Provider."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from habitat_local_eaios.runtime_check import OPERATIONS, check_runtime  # noqa: E402


def test_runtime_check_covers_operations_generations_and_actual_http() -> None:
    """All operation parsers preserve exact generations and reject malformed values."""
    result = check_runtime()
    assert result["state"] == "PASS"
    assert result["provider_calls"] == 0
    assert result["physical_episode_executed"] is False
    assert result["rejected_generation_cases"] == 7 * len(OPERATIONS)
    assert result["cases"] == [
        {"operation": operation, "execution_generation": generation}
        for operation in OPERATIONS
        for generation in (None, 1, 2**64 - 1)
    ]


def test_deployment_python_executes_runtime_check(tmp_path: Path) -> None:
    """An explicitly configured Habitat Python must actually pass, never silently skip."""
    python = os.environ.get("ROBOGUIDE_HABITAT_PYTHON") or shutil.which("python3.9")
    if python is None:
        pytest.skip("set ROBOGUIDE_HABITAT_PYTHON to exercise the deployment interpreter")
    output = tmp_path / "runtime-check.json"
    completed = subprocess.run(
        [python, "-m", "habitat_local_eaios.runtime_check", "--output", str(output)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(output.read_text())
    assert result["state"] == "PASS"
    assert result["provider_calls"] == 0
    assert len(result["cases"]) == 3 * len(OPERATIONS)


def test_runtime_check_never_routes_loopback_through_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated proxy cannot redirect the probe or cause a false runtime failure."""
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    assert check_runtime()["state"] == "PASS"


@pytest.mark.parametrize("probe_exit", [0, 1])
def test_runner_stops_before_startup_and_preserves_failure_attribution(
    tmp_path: Path, probe_exit: int
) -> None:
    """Execute the actual shell gate with a failed probe or a later occupied port."""
    source = (
        ROOT.parents[1] / "scenarios/e1-shared-world-episode-51/run-b1-roboguide.sh"
    ).read_text()
    start = source.index('HABITAT_PYTHON="$(conda run')
    end = source.index('clean_port "${CONTROLLER_GRPC_PORT}"')
    gate = source[start:end] + 'clean_port "${CONTROLLER_GRPC_PORT}"\n'
    probe = tmp_path / "fixture-python"
    probe.write_text(
        "#!/bin/bash\nset -eu\n"
        '[[ "$1" == -m && "$2" == habitat_local_eaios.runtime_check && "$3" == --output ]]\n'
        'printf \'{"scope":"offline test fixture"}\\n\' > "$4"\n'
        f"exit {probe_exit}\n"
    )
    probe.chmod(0o755)
    fixture = (
        'set -euo pipefail\nRUN="$1" REPO="$2" PROBE="$3"\n'
        "HABITAT_ENV=fixture CONTROLLER_GRPC_PORT=0\n"
        "FAILURE_OWNER=EXTERNAL_INFRA FAILURE_COMPONENT=environment FAILURE_REASON=setup\n"
        'conda() { printf "%s\\n" "$PROBE"; }\n'
        'clean_port() { touch "$RUN/port-checked"; '
        "FAILURE_REASON=configured_port_occupied; return 9; }\n"
        'trap \'code=$?; printf "%s|%s|%s\\n" "$FAILURE_OWNER" "$FAILURE_COMPONENT" '
        '"$FAILURE_REASON" > "$RUN/attribution"; exit "$code"\' EXIT\n' + gate
    )
    completed = subprocess.run(
        ["bash", "-s", "--", str(tmp_path), str(ROOT.parents[1]), str(probe)],
        input=fixture,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if probe_exit:
        assert completed.returncode == probe_exit, completed.stderr
        assert not (tmp_path / "port-checked").exists()
        assert (tmp_path / "attribution").read_text().strip() == (
            "SUT_SYSTEM|local_eaios|local_eaios_runtime_incompatible"
        )
    else:
        assert completed.returncode == 9, completed.stderr
        assert (tmp_path / "port-checked").exists()
        assert (tmp_path / "attribution").read_text().strip() == (
            "EXTERNAL_INFRA|environment|configured_port_occupied"
        )
    assert (tmp_path / "habitat-runtime-check.json").exists()
