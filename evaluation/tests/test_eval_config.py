"""Tests for ExperimentSpec loading, validation, digesting, and resolution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import helpers
import pytest
from helpers import interpreter, local_config_yaml
from roboguide_eval.config import (
    EnvironmentOverrides,
    EvaluationConfigError,
    load_experiment_spec,
    load_local_config,
    resolve_process_spec,
    spec_digest,
)
from roboguide_eval.metrics import MetricsError
from roboguide_eval.models import EXPERIMENT_SPEC_VERSION, ExperimentSpecError
from roboguide_eval.process import conda_run_prefix

SpecFactory = Callable[..., Path]
LocalConfigFactory = Callable[..., Path]

DIGEST_SPEC_KEY_ORDER_A: str = """\
schema: roboguide-eval.experiment-spec/v0.1
experiment_id: digest-check
benchmark: benchx
task: mobility
episodes: [ep-a]
systems: [emos]
llm:
  provider: openai
  model: model-a
seeds: [3]
metrics: [success, wall_time]
timeout_seconds: 60
"""

DIGEST_SPEC_KEY_ORDER_B: str = """\
timeout_seconds: 60
metrics: [success, wall_time]
seeds: [3]
llm:
  model: model-a
  provider: openai
systems: [emos]
episodes: [ep-a]
task: mobility
benchmark: benchx
experiment_id: digest-check
schema: roboguide-eval.experiment-spec/v0.1
"""


def committed_spec_text() -> str:
    """Return the committed E1 smoke spec YAML text.

    Returns:
        The spec file content read relative to the repository root.
    """
    return Path("evaluation/specs/e1/mobility-smoke.yaml").read_text(encoding="utf-8")


def test_committed_e1_smoke_spec_loads_with_expected_shape() -> None:
    """The committed E1 smoke spec parses into the documented experiment."""
    spec = load_experiment_spec(Path("evaluation/specs/e1/mobility-smoke.yaml"))
    assert spec.experiment_id == "e1-habitat-mas-mobility"
    assert spec.benchmark == "habitat-mas"
    assert spec.task == "mobility"
    assert spec.systems == ("emos", "roboguide")
    assert spec.episodes == ("seed-pinned-sample",)
    assert spec.context == {"robots": "spot+fetch"}
    assert spec.llm.provider == "openai"
    assert spec.llm.model == "gpt-5.6-luna"
    assert spec.llm.reasoning_effort == "medium"
    assert spec.seeds == (7, 11, 13)
    assert spec.dataset.identity() == "habitat-mas-mp3d-mobility@mobility_episodes_1"
    assert spec.dataset.digest is not None and len(spec.dataset.digest) == 64
    assert spec.metrics == (
        "success",
        "subgoal_success",
        "simulation_steps",
        "token_usage",
        "wall_time",
        "coordination_latency",
        "invalid_assignment_count",
    )
    assert spec.environments["emos"].metrics_source_path == "emos-metrics.json"
    assert spec.environments["roboguide"].metrics_source_path == "roboguide-metrics.json"
    assert spec.timeout_seconds == 900


def test_spec_rejects_wrong_schema_version(make_spec: SpecFactory) -> None:
    """Specs written for another schema version are rejected explicitly."""
    wrong_version = helpers.BASE_SPEC_YAML.replace(
        "schema: roboguide-eval.experiment-spec/v0.1",
        "schema: roboguide-eval.experiment-spec/v9.9",
    )
    with pytest.raises(ExperimentSpecError, match="unsupported experiment spec schema"):
        load_experiment_spec(make_spec(wrong_version))


def test_spec_rejects_unknown_and_missing_keys(make_spec: SpecFactory) -> None:
    """Unknown keys and missing required keys both fail with explicit paths."""
    base = committed_spec_text()
    with pytest.raises(ExperimentSpecError, match="unknown"):
        load_experiment_spec(make_spec(base + "surprise_key: 1\n"))
    stripped = "\n".join(line for line in base.splitlines() if not line.startswith("benchmark:"))
    with pytest.raises(ExperimentSpecError, match=r"missing=\['benchmark'\]"):
        load_experiment_spec(make_spec(stripped))


def test_spec_rejects_unknown_metric_name(make_spec: SpecFactory) -> None:
    """Metric names outside the canonical registry are rejected at load."""
    base = committed_spec_text()
    with pytest.raises(MetricsError, match="unknown canonical metric"):
        load_experiment_spec(
            make_spec(base.replace("  - success\n", "  - success\n  - not_a_metric\n"))
        )


def test_spec_rejects_invalid_enumerated_fields(make_spec: SpecFactory) -> None:
    """Identity patterns, duplicates, timeouts, and seeds fail closed."""
    base = committed_spec_text()
    with pytest.raises(ExperimentSpecError, match="experiment_id"):
        load_experiment_spec(
            make_spec(
                base.replace("experiment_id: e1-habitat-mas-mobility", "experiment_id: Bad Id")
            )
        )
    with pytest.raises(ExperimentSpecError, match="duplicate"):
        load_experiment_spec(make_spec(base.replace("- emos\n  - roboguide", "- emos\n  - emos")))
    with pytest.raises(ExperimentSpecError, match="positive number"):
        load_experiment_spec(make_spec(base.replace("timeout_seconds: 900", "timeout_seconds: 0")))
    with pytest.raises(ExperimentSpecError, match="nonnegative"):
        load_experiment_spec(make_spec(base.replace("seeds:\n  - 7", "seeds:\n  - -1")))


def test_config_digest_is_stable_under_key_order(make_spec: SpecFactory) -> None:
    """Digests ignore YAML key order and only track experiment semantics."""
    first = load_experiment_spec(make_spec(DIGEST_SPEC_KEY_ORDER_A))
    second = load_experiment_spec(make_spec(DIGEST_SPEC_KEY_ORDER_B))
    assert first.experiment_id == second.experiment_id
    assert spec_digest(first) == spec_digest(second)


def test_config_digest_changes_when_semantics_change(make_spec: SpecFactory) -> None:
    """Changing any experiment field changes the recorded digest."""
    first = load_experiment_spec(make_spec(DIGEST_SPEC_KEY_ORDER_A))
    changed_text = DIGEST_SPEC_KEY_ORDER_A.replace("seeds: [3]", "seeds: [4]")
    changed = load_experiment_spec(make_spec(changed_text))
    assert spec_digest(first) != spec_digest(changed)


def test_spec_version_constant_matches_committed_spec() -> None:
    """The spec version constant stays aligned with the committed contract."""
    assert f"schema: {EXPERIMENT_SPEC_VERSION}" in committed_spec_text()


def test_episode_and_seed_selection_overrides(make_spec: SpecFactory) -> None:
    """Selection overrides narrow declared values and reject unknown ones."""
    spec = load_experiment_spec(Path("evaluation/specs/e1/mobility-smoke.yaml"))
    assert spec.selected_episodes(("seed-pinned-sample",)) == ("seed-pinned-sample",)
    assert spec.selected_episodes(()) == spec.episodes
    assert spec.selected_seeds((7,)) == (7,)
    with pytest.raises(ExperimentSpecError, match="not declared"):
        spec.selected_episodes(("missing-episode",))
    with pytest.raises(ExperimentSpecError, match="not declared"):
        spec.selected_seeds((99,))


def test_local_config_default_is_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing default local config yields empty overrides, not an error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROBOGUIDE_EVAL_CONFIG", raising=False)
    assert load_local_config(None) == {}
    with pytest.raises(EvaluationConfigError, match="not found"):
        load_local_config(tmp_path / "missing.yaml")


def test_environment_variable_overrides_local_config(
    tmp_path: Path,
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
) -> None:
    """ROBOGUIDE_EVAL_* variables take precedence over file configuration."""
    other_dir = tmp_path / "other-workdir"
    other_dir.mkdir()
    spec = load_experiment_spec(make_spec())
    config_path = make_local_config(local_config_yaml(fixture_workdir))
    assert load_local_config(config_path)["emos"].working_directory == fixture_workdir.as_posix()
    merged_environment = {
        "ROBOGUIDE_EVAL_EMOS_WORKDIR": other_dir.as_posix(),
        "ROBOGUIDE_EVAL_EMOS_EXECUTABLE": "python99",
        "ROBOGUIDE_EVAL_EMOS_CONDA_ENV": "emos-env",
    }
    overrides = load_local_config(config_path, environment=merged_environment)["emos"]
    resolved = resolve_process_spec(spec, "emos", overrides, environment=merged_environment)
    assert resolved.working_directory == other_dir
    assert resolved.argv[:5] == conda_run_prefix("conda", "emos-env")
    assert resolved.argv[5] == "python99"


def test_resolve_process_spec_builds_direct_and_conda_argv(
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
) -> None:
    """Resolution merges layers and wraps argv when a Conda env is declared."""
    spec = load_experiment_spec(make_spec())
    overrides = load_local_config(make_local_config(local_config_yaml(fixture_workdir)))["emos"]
    direct = resolve_process_spec(spec, "emos", overrides, environment={})
    assert direct.argv[0] == interpreter()
    assert direct.argv[1] == "-u"
    assert direct.working_directory == fixture_workdir
    assert direct.timeout_seconds == 30  # environment timeout beats spec timeout
    assert direct.version_probe_command is not None
    assert direct.conda_environment is None
    conda_text = local_config_yaml(
        fixture_workdir,
        extra_lines="    conda_environment: emos-env\n    conda_command: /opt/conda/bin/conda\n",
    )
    conda_overrides = load_local_config(make_local_config(conda_text))["emos"]
    wrapped = resolve_process_spec(spec, "emos", conda_overrides, environment={})
    assert wrapped.argv[:5] == conda_run_prefix("/opt/conda/bin/conda", "emos-env")
    assert wrapped.argv[5] == interpreter()
    assert wrapped.conda_environment == "emos-env"


def test_spec_rejects_empty_seed_and_metric_lists(make_spec: SpecFactory) -> None:
    """Empty seeds or metrics fail closed instead of running zero episodes."""
    base = helpers.BASE_SPEC_YAML
    with pytest.raises(ExperimentSpecError, match="at least one seed"):
        load_experiment_spec(make_spec(base.replace("seeds: [7, 11]", "seeds: []")))
    with pytest.raises(ExperimentSpecError, match="at least one canonical metric"):
        load_experiment_spec(
            make_spec(base.replace("metrics: [success, wall_time]", "metrics: []"))
        )


def test_spec_rejects_paths_escaping_the_run_directory(make_spec: SpecFactory) -> None:
    """Declared run-relative paths may not traverse or target reserved files."""
    base = helpers.BASE_SPEC_YAML
    for bad_source in ("../outside.json", "/etc/passwd", "stdout.log"):
        replaced = base.replace(
            "metrics_source_path: raw-metrics.json", f"metrics_source_path: {bad_source}"
        )
        with pytest.raises(ExperimentSpecError, match="metrics_source_path"):
            load_experiment_spec(make_spec(replaced))
    replaced_outputs = base.replace(
        "expected_output_paths: [raw-metrics.json, episode.log]",
        "expected_output_paths: [nested/../../escape.json]",
    )
    with pytest.raises(ExperimentSpecError, match="expected_output_paths"):
        load_experiment_spec(make_spec(replaced_outputs))
    replaced_reserved = base.replace(
        "expected_output_paths: [raw-metrics.json, episode.log]",
        "expected_output_paths: [manifest.json]",
    )
    with pytest.raises(ExperimentSpecError, match="harness-owned"):
        load_experiment_spec(make_spec(replaced_reserved))
    nested = base.replace(
        "metrics_source_path: raw-metrics.json", "metrics_source_path: nested/dir/metrics.json"
    )
    assert load_experiment_spec(make_spec(nested)).environments["emos"].metrics_source_path == (
        "nested/dir/metrics.json"
    )


def test_resolve_process_spec_fails_closed_on_missing_machine_config(
    make_spec: SpecFactory,
) -> None:
    """Missing workdir/executable names the exact remediation fields."""
    spec = load_experiment_spec(make_spec())
    with pytest.raises(EvaluationConfigError) as error_info:
        resolve_process_spec(spec, "emos", EnvironmentOverrides(), environment={})
    message = str(error_info.value)
    assert "working_directory" in message
    assert "executable" in message
    assert "ROBOGUIDE_EVAL_EMOS_WORKDIR" in message


def test_resolve_process_spec_rejects_missing_workdir(
    fixture_workdir: Path,
    make_spec: SpecFactory,
    make_local_config: LocalConfigFactory,
) -> None:
    """A configured working directory that vanished fails before spawning."""
    spec = load_experiment_spec(make_spec())
    gone = fixture_workdir / "gone"
    gone.mkdir()
    config_path = make_local_config(local_config_yaml(gone))
    gone.rmdir()
    overrides = load_local_config(config_path)["emos"]
    with pytest.raises(EvaluationConfigError, match="does not exist"):
        resolve_process_spec(spec, "emos", overrides, environment={})
