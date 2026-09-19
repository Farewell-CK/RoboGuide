"""Deterministic tests for the generic Formal B1 workload and runner contract.

These tests pin the runner's genericity without any simulator: the frozen
B1 input alone decides which episode and seed execute, the scenario script
forwards them instead of embedding an episode, and the B1 path never
references the B2 static plan. Workload identity binding (episode, scene,
dataset digest) is enforced downstream by the B1 provenance verifier
against the bridge's runtime semantic evidence; these tests guarantee the
runner actually forwards what the input declares.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from roboguide_eval.b1_workload import B1WorkloadError, extract_b1_workload, load_b1_workload

REPO_ROOT = Path(__file__).parents[2]
SCENARIO = REPO_ROOT / "scenarios" / "e1-shared-world-episode-51"
RUNNER = SCENARIO / "run-b1-roboguide.sh"
B2_RUNNER = SCENARIO / "run-shared-world.sh"


def _input_document(**overrides: object) -> dict[str, object]:
    """Build one valid frozen B1 input with optional field overrides."""
    document: dict[str, object] = {
        "schema": "roboguide.e1.b1-input/v0.1",
        "episode_id": "12",
        "seed": 7,
        "dataset_revision": "mobility_episodes_1",
        "dataset_sha256": "5d2c6aa6608d5611c73d8f6c688e17613a9898afa5f0f668e66db068598191ca",
        "scene_id": "data/scene_datasets/mp3d/2azQ1b91cZZ/2azQ1b91cZZ.glb",
        "instruction": "Complete the benchmark objective for this episode.",
    }
    document.update(overrides)
    return document


def test_valid_workload_extracts_every_field() -> None:
    """A complete frozen input yields the exact workload the runner forwards."""
    workload = extract_b1_workload(_input_document())
    assert workload.episode_id == "12"
    assert workload.seed == 7
    assert workload.dataset_revision == "mobility_episodes_1"
    assert workload.scene_id is not None and workload.scene_id.endswith("2azQ1b91cZZ.glb")
    assert workload.instruction.startswith("Complete")


def test_episode51_fixture_remains_a_valid_workload() -> None:
    """The frozen episode-51 regression input still validates unchanged."""
    workload = load_b1_workload(SCENARIO / "b1-input.json")
    assert workload.episode_id == "51"
    assert workload.seed == 40


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("episode_id", None),
        ("episode_id", 51),
        ("episode_id", "  "),
        ("seed", None),
        ("seed", "40"),
        ("seed", True),
        ("seed", 0),
        ("seed", -3),
        ("dataset_revision", ""),
        ("dataset_sha256", "not-a-digest"),
        ("dataset_sha256", "5D2C6AA6608D5611C73D8F6C688E17613A9898AFA5F0F668E66DB068598191CA"),
        ("scene_id", 51),
        ("scene_id", "  "),
        ("schema", "roboguide.e1.b1-input/v0.9"),
        ("schema", None),
        ("instruction", "   "),
    ],
)
def test_invalid_workload_fields_fail_with_field_reasons(field: str, value: object) -> None:
    """Each malformed workload field is identified before any launch."""
    with pytest.raises(B1WorkloadError) as error:
        extract_b1_workload(_input_document(**{field: value}))
    assert error.value.field == field


def test_non_object_document_is_rejected() -> None:
    """A non-object input document fails with a document-level reason."""
    with pytest.raises(B1WorkloadError) as error:
        extract_b1_workload(["not", "an", "object"])
    assert error.value.field == "document"


def test_missing_scene_is_rejected() -> None:
    """The scene identity is mandatory: absence fails with its field reason."""
    document = _input_document()
    del document["scene_id"]
    with pytest.raises(B1WorkloadError) as error:
        extract_b1_workload(document)
    assert error.value.field == "scene_id"


def test_missing_schema_marker_is_rejected() -> None:
    """A document without the exact v0.1 schema marker is not a B1 workload."""
    document = _input_document()
    del document["schema"]
    with pytest.raises(B1WorkloadError) as error:
        extract_b1_workload(document)
    assert error.value.field == "schema"


def test_cli_reports_validation_failure_before_launch(tmp_path: Path) -> None:
    """The runner-facing CLI exits nonzero with the offending field on stderr."""
    path = tmp_path / "bad-input.json"
    path.write_text(json.dumps(_input_document(seed=0)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "roboguide_eval.b1_workload", str(path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT / "evaluation" / "src",
        check=False,
    )
    assert result.returncode == 2
    assert "seed" in result.stderr


def test_runner_is_driven_by_extracted_workload_not_an_episode() -> None:
    """The B1 script forwards extracted variables and embeds no episode."""
    script = RUNNER.read_text(encoding="utf-8")
    assert '--episode-id "$EPISODE_ID"' in script
    assert '--seed "$SEED"' in script
    assert "roboguide_eval.b1_workload" in script
    assert "--episode-id 51" not in script
    assert "invalid_b1_workload" in script


def test_b1_runner_never_references_the_b2_static_plan() -> None:
    """The Formal B1 path cannot fall back to the hand-authored MissionPlan."""
    script = RUNNER.read_text(encoding="utf-8")
    assert "mission-plan.json" not in script
    assert "mission-plan-negative.json" not in script


def test_b2_runner_remains_the_static_plan_diagnostics_path() -> None:
    """The B2 scenario keeps its authored plan; genericity is B1-only."""
    script = B2_RUNNER.read_text(encoding="utf-8")
    assert "mission-plan.json" in script


@pytest.mark.parametrize(
    ("responses", "success"),
    [
        (["HTTP_ERROR", '{"state":"OFFLINE"}', "{}", "malformed", '{"state":"ONLINE"}'], True),
        (['{"state":"OFFLINE"}', '{"state":"OFFLINE"}'], False),
        (["[]", "{}", "malformed"], False),
    ],
)
def test_runner_waits_for_semantic_publication_readiness(
    tmp_path: Path, responses: list[str], success: bool
) -> None:
    """HTTP 200 cannot admit MI before ONLINE; failed initialization exhausts its budget."""
    script = RUNNER.read_text(encoding="utf-8")
    probe = script[script.index("wait_http() {") : script.index("wait_nodes() {")]
    replies = tmp_path / "replies"
    counter = tmp_path / "counter"
    replies.write_text("\n".join(responses) + "\n", encoding="utf-8")
    counter.write_text("0\n", encoding="utf-8")
    shell = (
        probe
        + r"""
probe_replies="$1"
probe_counter="$2"
probe_budget="$3"
curl() {
    local probe_count probe_reply
    probe_count=$(cat "$probe_counter")
    probe_count=$((probe_count + 1))
    printf '%s\n' "$probe_count" > "$probe_counter"
    probe_reply=$(sed -n "${probe_count}p" "$probe_replies")
    if [[ "$probe_reply" == HTTP_ERROR ]]; then return 22; fi
    printf '%s\n' "$probe_reply"
}
sleep() { :; }
wait_http http://unused/v1/health "$probe_budget" ONLINE
"""
    )
    result = subprocess.run(
        ["bash", "-c", shell, "probe", str(replies), str(counter), str(len(responses))],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (result.returncode == 0) is success
    assert int(counter.read_text(encoding="utf-8")) == len(responses)
    assert "timeout waiting" in result.stderr if not success else result.stderr == ""
    for port, budget in ((28100, 240), (28102, 30)):
        gate = f"wait_http http://127.0.0.1:{port}/v1/health {budget} ONLINE"
        assert script.index(gate) < script.index("# Production Mission Intelligence ingress")
