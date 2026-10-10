"""Frozen process pairing, tri-state outcome and evidence-boundary regressions."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from roboguide_eval.e1_batch import file_digest, write_json
from roboguide_eval.e1_fairness import (
    BenchmarkAuthorityIdentity,
    DatasetIdentity,
    EmbodimentAgent,
    EmbodimentProfile,
    ModelConfigurationIdentity,
    PopulationManifest,
    PopulationRow,
    PopulationSelector,
    SimulatorIdentity,
    Stage2Identity,
    TaskIdentity,
    digest,
)
from roboguide_eval.e1_pair import (
    PairSpec,
    arm_command,
    arm_environment,
    collect_arm,
    compare_reset,
    private_vendor_view,
    run_pair,
)
from roboguide_eval.models import JSONObject


def setup_pair(root: Path) -> tuple[Path, Path]:
    """Freeze a portable two-row population with real public file digests and no credentials."""
    code, vendor, inputs = root / "code", root / "vendor", root / "inputs"
    inputs.mkdir()
    code.mkdir()
    vendor.mkdir()
    sources: JSONObject = {}
    for relative in (
        "target/debug/integration-server",
        "target/debug/roboguide-node",
        "run.sh",
        "mission.toml",
    ):
        path = code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '[mission.llm]\nmodel = "test-model"\nreview_model = "test-model"\n'
            'model_provider = "test"\n'
            '[model_providers.test]\nbase_url = "http://127.0.0.1:9"\nwire_api = "responses"\n'
        )
        sources[str(path)] = file_digest(path)
    dataset = root / "dataset.gz"
    dataset.write_bytes(b"frozen native dataset")
    sources[str(dataset)] = file_digest(dataset)
    sources[sys.executable] = file_digest(Path(sys.executable))
    groups: JSONObject = {}
    hashes: dict[str, JSONObject] = {}
    for group in ("task_spec", "habitat_config", "benchmark_authority", "stage2"):
        path = vendor / group
        path.write_text(group)
        sources[str(path)] = file_digest(path)
        groups[group] = {group: str(path)}
        hashes[group] = {group: "sha256:" + file_digest(path)}
    for name in ("data", "habitat-lab", "habitat-baselines", "habitat-mas"):
        (vendor / name).mkdir()
    native_config = vendor / "habitat-baselines/habitat_baselines/config/test.yaml"
    native_config.parent.mkdir(parents=True)
    native_config.write_text("unchanged native config")
    sources[str(native_config)] = file_digest(native_config)
    population = PopulationManifest.create(
        population_id="process-pair-test",
        protocol="Protocol-A",
        dataset=DatasetIdentity("dataset", "original", "sha256:" + file_digest(dataset)),
        task=TaskIdentity(
            "habitat", "test", digest(hashes["task_spec"]), digest(hashes["habitat_config"])
        ),
        benchmark_authority=BenchmarkAuthorityIdentity(
            "pddl_success", digest(hashes["benchmark_authority"]), {}
        ),
        embodiment_profile=EmbodimentProfile(
            (EmbodimentAgent(0, "RobotA"), EmbodimentAgent(1, "RobotB"))
        ),
        stage2_identity=Stage2Identity("original", {"stage2": str(hashes["stage2"]["stage2"])}),
        simulator_identity=SimulatorIdentity("original", "test"),
        model_configuration=ModelConfigurationIdentity("test", "test-model"),
        allowed_differences=("organization_axis", "local_execution_profile", "actual_reset"),
        selector=PopulationSelector(
            "explicit_set",
            (
                PopulationRow("pair-0", 40, "7", "scene"),
                PopulationRow("pair-1", 40, "8", "scene-two"),
            ),
        ),
    )
    population_path = root / "population.json"
    write_json(population_path, population.to_json())
    for row in population.selector.rows:
        path = inputs / f"{row.pair_id}.json"
        write_json(
            path,
            {
                "schema": "roboguide.e1.b1-input/v0.1",
                "dataset_revision": "original",
                "dataset_sha256": file_digest(dataset),
                "episode_id": row.expected_episode_id,
                "scene_id": row.expected_scene_id,
                "seed": row.seed,
                "instruction": "Perform the unchanged joint goal.",
            },
        )
        sources[str(path)] = file_digest(path)
    sockets = [socket.socket() for _ in range(7)]
    for listener in sockets:
        listener.bind(("127.0.0.1", 0))
    ports: JSONObject = {
        name: listener.getsockname()[1]
        for name, listener in zip(
            ("proxy", "grpc", "controller", "artifact", "endpoint_a", "endpoint_b", "mission"),
            sockets,
            strict=True,
        )
    }
    for listener in sockets:
        listener.close()
    config: JSONObject = {
        "schema_version": "roboguide.e1.pair-worker/v0.1",
        "population_digest": population.digest,
        "code_root": str(code),
        "vendor_root": str(vendor),
        "habitat_python": sys.executable,
        "dataset_path": str(dataset),
        "input_directory": str(inputs),
        "code_sha": "a" * 40,
        "native_config": "test.yaml",
        "b1_runner": "run.sh",
        "mission_config": "mission.toml",
        "arm_timeout_seconds": 30,
        "mi_observation_seconds": 20,
        "mission_observation_seconds": 20,
        "provider_timeout_seconds": 3,
        "provider_upstream": "http://127.0.0.1:9",
        "gpu_device": 1,
        "ports": ports,
        "source_sha256": sources,
        "identity_files": groups,
        "runtime_modules": {"original.stage2": str(vendor / "stage2")},
    }
    config_path = root / "worker.json"
    write_json(config_path, config)
    return population_path, config_path


def write_observations(
    spec: PairSpec, arm: str, directory: Path, *, official: bool | None, mi_failed: bool = False
) -> JSONObject:
    """Supply fake original evidence to the actual archive/validator path without model calls."""
    directory.mkdir(parents=True, exist_ok=True)
    run = directory / "run" if arm == "roboguide" else directory / "native-evidence/worker-1"
    run.mkdir(parents=True)
    initial: JSONObject = {
        "episode_id": spec.row.expected_episode_id,
        "scene_id": spec.row.expected_scene_id,
        "habitat_seed_config": spec.row.seed,
        "agents": {"0": {"position": [0, 1, 2], "rotation": 0}},
        "goal_entity_positions": {"goal": [1, 2, 3]},
        "goal_conjuncts": ["goal"],
    }
    identity: JSONObject = {
        "dataset_sha256": spec.population.dataset.file_sha256.removeprefix("sha256:"),
        "loaded_robot_types": {"agent_0": "RobotA", "agent_1": "RobotB"},
    }
    source = spec.path("vendor_root") / "stage2"
    runtime: JSONObject = {
        "python_executable": str(spec.path("habitat_python").resolve()),
        "modules": {"original.stage2": {"path": str(source), "sha256": file_digest(source)}},
    }
    if arm == "roboguide":
        (run / "evidence").mkdir()
        write_json(run / "evidence/runtime-source-manifest.json", runtime)
        write_json(run / "evidence/diagnostics-initial.json", initial)
        write_json(run / "evidence/relocation-episode-start.json", {"identity": identity})
        write_json(
            run / "evidence/shared-world-summary.json",
            {
                "official_pddl_success": official,
                "identity": {"simulator_steps": 0 if mi_failed else 30},
            },
        )
        write_json(
            run / "evidence/stage2-agent-identity.json",
            {"robot_types": identity["loaded_robot_types"]},
        )
        write_json(run / "mission.json", {"status": "Failed" if mi_failed else "Completed"})
        write_json(
            run / "b1-verdict.json",
            {
                "admission": {
                    "provenance_valid": True,
                    "valid_for_formal_population": True,
                    "valid_for_benchmark_population": official is not None,
                },
                "context": {"semantic_goal_diagnostic": []},
            },
        )
    else:
        write_json(directory / "native-evidence/runtime-source-manifest.json", runtime)
        write_json(run / "diagnostics-initial.json", initial)
        write_json(run / "workload-selection.json", identity)
        write_json(
            run / "native-outcome.json",
            {"official_pddl_success": official, "simulator_steps": 30, "episode_terminal": True},
        )
    (directory / "provider-accounting.jsonl").write_text(
        json.dumps(
            {
                "method": "POST",
                "status": 200,
                "requested_model": "test-model",
                "response_model": "test-model",
                "path": "/v1/chat/completions",
            }
        )
        + "\n"
    )
    return {
        "harness_error": None,
        "exit_code": 0,
        "provider_closure": {
            "drained": True,
            "accounting": {"complete": True},
            "body_capture": {"complete": True},
        },
        "log_capture": [{"closed": True}],
        "resource_usage": {"samples": 3, "peak_gpu_mib": 200},
    }


def test_contract_freezes_selection_order_ports_and_model_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select the original episode and remove inherited experiment flags."""
    population, config = setup_pair(tmp_path)
    a, b = PairSpec.load(population, config, "pair-0"), PairSpec.load(population, config, "pair-1")
    assert a.order() == ("emos", "roboguide") and b.order() == ("roboguide", "emos")
    argv = arm_command(a, "emos", tmp_path / "arm")
    assert argv[argv.index("--episode-id") + 1] == "7"
    assert "habitat_baselines.num_environments=1" in argv and "habitat.seed=40" in argv
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-value")
    monkeypatch.setenv("ROBOGUIDE_B1_GOAL_REGION_NAVIGATION", "1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://wrong")
    env = arm_environment(a, tmp_path / "arm", a.ports["proxy"])
    assert "ROBOGUIDE_B1_GOAL_REGION_NAVIGATION" not in env
    assert env["EMOS_LLM_MODEL"] == "test-model" and env["CUDA_VISIBLE_DEVICES"] == "1"
    assert env["ROBOGUIDE_B1_HABITAT_PORT_B"] == str(a.ports["endpoint_b"])


def test_private_checkout_passes_production_containment_and_isolates_writes(tmp_path: Path) -> None:
    """A real private config survives B1 deployment admission and never mutates shared code."""
    from roboguide_eval.b1_deployment import freeze_deployment

    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    view = tmp_path / "private-vendor"
    private_vendor_view(spec.path("vendor_root"), view)
    scenario, run = tmp_path / "scenario", tmp_path / "run"
    scenario.mkdir()
    run.mkdir()
    relative = "habitat-baselines/habitat_baselines/config/test.yaml"
    write_json(
        scenario / "b1-deployment.json",
        {
            "schema_version": "roboguide.e1.b1-deployment/v0.1",
            "habitat_config": relative,
            "max_steps": 4000,
            "enable_relocation": False,
        },
    )
    for name in ("node-a", "node-b"):
        (scenario / f"{name}.toml").write_text(f'node_id = "{name}"\n')
    (run / "b1-input-used.json").write_bytes(
        (spec.path("input_directory") / "pair-0.json").read_bytes()
    )
    spec.verify_vendor_view(view)
    result = freeze_deployment(run, scenario, view)
    assert Path(str(result["HABITAT_CONFIG"])).is_relative_to(view.resolve())
    original = spec.path("vendor_root") / relative
    assert (view / relative).read_bytes() == original.read_bytes()
    assert not (view / "habitat-baselines").is_symlink()
    (view / relative).write_text("local changed config")
    assert original.read_text() == "unchanged native config"
    with pytest.raises(ValueError, match="private vendor source"):
        spec.verify_vendor_view(view)


@pytest.mark.parametrize(
    "problem", [None, "origin", "reported-digest", "copied-bytes", "external-link"]
)
def test_runtime_identity_binds_the_exact_private_copy(tmp_path: Path, problem: str | None) -> None:
    """Only the owned private path and unchanged frozen bytes prove a runtime source match."""
    population, config = setup_pair(tmp_path)
    document = json.loads(config.read_text())
    source = tmp_path / "vendor/habitat-mas/stage2.py"
    source.write_text("original stage2 source")
    document["source_sha256"][str(source)] = file_digest(source)
    document["runtime_modules"] = {"original.stage2": str(source)}
    write_json(config, document)
    spec = PairSpec.load(population, config, "pair-0")
    view = tmp_path / "private-vendor"
    private_vendor_view(spec.path("vendor_root"), view)
    private = view / "habitat-mas/stage2.py"
    manifest: JSONObject = {
        "python_executable": sys.executable,
        "modules": {
            "original.stage2": {
                "path": str(source if problem == "origin" else private),
                "sha256": "0" * 64 if problem == "reported-digest" else file_digest(source),
            }
        },
    }
    if problem == "copied-bytes":
        private.write_text("mutated after import")
    if problem == "external-link":
        private.unlink()
        private.symlink_to(source)
    failures = spec.runtime_failures(manifest, view)
    assert failures == (
        [] if problem is None else ["runtime_module_identity_unconfirmed:original.stage2"]
    )


@pytest.mark.parametrize("problem", [None, "missing", "origin", "digest"])
def test_compiled_simulator_identity_requires_actual_child_evidence(
    tmp_path: Path, problem: str | None
) -> None:
    """A readable frozen extension alone cannot replace an exact child path and digest."""
    population, config = setup_pair(tmp_path)
    document = json.loads(config.read_text())
    name = "habitat_sim._ext.habitat_sim_bindings"
    source = tmp_path / "habitat_sim_bindings.so"
    source.write_bytes(b"unchanged compiled simulator")
    document["source_sha256"][str(source)] = file_digest(source)
    document["runtime_modules"] = {name: str(source)}
    write_json(config, document)
    spec = PairSpec.load(population, config, "pair-0")
    manifest: JSONObject = {
        "python_executable": sys.executable,
        "modules": (
            {}
            if problem == "missing"
            else {
                name: {
                    "path": str(tmp_path / "other.so" if problem == "origin" else source),
                    "sha256": "0" * 64 if problem == "digest" else file_digest(source),
                }
            }
        ),
    }
    assert spec.runtime_failures(manifest, tmp_path / "private-vendor") == (
        [] if problem is None else ["runtime_module_identity_unconfirmed:" + name]
    )


@pytest.mark.parametrize("problem", [None, "scene", "step-type", "schema"])
def test_native_worker_interruption_preserves_observed_step_lower_bound(
    tmp_path: Path, problem: str | None
) -> None:
    """Partial progress proves execution, never terminal truth; invalid progress remains unknown."""
    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    directory = tmp_path / "arm"
    process = write_observations(spec, "emos", directory, official=None)
    process["exit_code"] = 1
    run = directory / "native-evidence/worker-1"
    (run / "native-outcome.json").unlink()
    write_json(
        run / "native-progress.json",
        {
            "schema_version": "bad"
            if problem == "schema"
            else "roboguide.native-execution-progress/v0.1",
            "basis": "last_durable_successful_step_lower_bound",
            "episode_id": spec.row.expected_episode_id,
            "scene_id": "wrong" if problem == "scene" else spec.row.expected_scene_id,
            "simulator_steps": True if problem == "step-type" else 992,
            "official_pddl_success": True,
        },
    )
    result, _ = collect_arm(spec, "emos", directory, process)
    assert result["official_pddl_success"] is None
    assert result["system_outcome"] == "Failed"
    assert result["simulator_steps"] == (992 if problem is None else None)
    assert result["physical_episode_executed"] is (True if problem is None else None)
    assert result["simulator_steps_basis"] == (
        "last_durable_successful_step_lower_bound" if problem is None else "unavailable"
    )


@pytest.mark.parametrize("provider_failure", [False, True])
def test_native_failure_after_reset_keeps_its_failure_owner(
    tmp_path: Path, provider_failure: bool
) -> None:
    """A native policy exception is a SUT failure; actual Provider failure stays infrastructure."""
    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    directory = tmp_path / "arm"
    process = write_observations(spec, "emos", directory, official=None)
    process["exit_code"] = 1
    write_json(
        directory / "native-evidence/worker-1/native-outcome.json",
        {"official_pddl_success": None, "simulator_steps": 0, "episode_terminal": False},
    )
    if provider_failure:
        path = directory / "provider-accounting.jsonl"
        call = json.loads(path.read_text())
        call.update(status=502, response_model=None)
        path.write_text(json.dumps(call) + "\n")
    result, _ = collect_arm(spec, "emos", directory, process)
    assert result["system_outcome"] == "Failed"
    assert result["official_pddl_success"] is None
    assert result["infrastructure_failure"] is provider_failure
    assert result["fatal_reasons"] == []


@pytest.mark.parametrize("field", ["workload", "binary", "port", "source-group"])
def test_invalid_configuration_stops_before_processes(tmp_path: Path, field: str) -> None:
    """Missing binary gates, source drift, port aliasing and workload identity all fail closed."""
    population, config = setup_pair(tmp_path)
    document: JSONObject = json.loads(config.read_text())
    if field == "workload":
        (tmp_path / "inputs/pair-0.json").write_text("{}")
    elif field == "binary":
        sources = document["source_sha256"]
        assert isinstance(sources, dict)
        del sources[str(tmp_path / "code/target/debug/roboguide-node")]
    elif field == "port":
        ports = document["ports"]
        assert isinstance(ports, dict)
        ports["mission"] = ports["grpc"]
    else:
        document["identity_files"] = {}
    write_json(config, document)
    with pytest.raises(ValueError):
        PairSpec.load(population, config, "pair-0")


@pytest.mark.parametrize("official,mi_failed", [(False, False), (None, True), (True, False)])
def test_real_tri_state_results_do_not_filter_population(
    tmp_path: Path, official: bool | None, mi_failed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consume official false and MI early failure once, preserving their outcomes."""
    from roboguide_eval import e1_pair

    population, config = setup_pair(tmp_path)
    seen: list[str] = []

    def fake_arm(spec: PairSpec, arm: str, directory: Path) -> JSONObject:
        """Emulate original arm outputs while exercising the production pair collector."""
        seen.append(arm)
        return write_observations(
            spec, arm, directory, official=official, mi_failed=mi_failed and arm == "roboguide"
        )

    monkeypatch.setattr(e1_pair, "run_arm", fake_arm)
    output = tmp_path / "pair-output"
    result = run_pair(population, config, "pair-0", output)
    assert seen == ["emos", "roboguide"] and result["fatal_failure"] is False
    arms = result["arms"]
    assert isinstance(arms, list) and len(arms) == 2
    rg = arms[1]
    assert isinstance(rg, dict)
    assert rg["official_pddl_success"] is official
    assert rg["admission"] == {
        "provenance_valid": True,
        "valid_for_formal_population": True,
        "valid_for_benchmark_population": official is not None,
    }
    assert (output / "pair-manifest.json").is_file()
    with pytest.raises(FileExistsError):
        run_pair(population, config, "pair-0", output)
    assert seen == ["emos", "roboguide"]


@pytest.mark.parametrize("official", [False, True])
def test_matching_reset_cannot_hide_missing_runtime_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, official: bool
) -> None:
    """Keep real benchmark outcomes while excluding an identity-incomplete pair from comparison."""
    from roboguide_eval import e1_pair

    population, config = setup_pair(tmp_path)

    def fake_arm(spec: PairSpec, arm: str, directory: Path) -> JSONObject:
        """Record matched resets, then independently remove one child module observation."""
        process = write_observations(spec, arm, directory, official=official)
        if arm == "roboguide":
            path = directory / "run/evidence/runtime-source-manifest.json"
            manifest = json.loads(path.read_text())
            del manifest["modules"]["original.stage2"]
            write_json(path, manifest)
        return process

    monkeypatch.setattr(e1_pair, "run_arm", fake_arm)
    result = run_pair(population, config, "pair-0", tmp_path / "pair")
    assert result["fatal_failure"] is True
    assert result["comparison_eligible"] is False
    reset = result["reset_comparison"]
    assert isinstance(reset, dict) and reset["status"] == "matched"
    arms = result["arms"]
    assert isinstance(arms, list) and len(arms) == 2
    for arm in arms:
        assert isinstance(arm, dict) and arm["official_pddl_success"] is official


@pytest.mark.parametrize("problem", ["archive", "model", "http-502"])
def test_archive_model_and_provider_failures_remain_distinct(tmp_path: Path, problem: str) -> None:
    """Pause on identity/archive failure; preserve an isolated 502 as infrastructure evidence."""
    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    directory = tmp_path / "arm"
    process = write_observations(spec, "roboguide", directory, official=None, mi_failed=True)
    if problem == "archive":
        process["provider_closure"] = {"drained": False}
    elif problem == "model":
        (directory / "provider-accounting.jsonl").write_text(
            json.dumps(
                {
                    "method": "POST",
                    "status": 200,
                    "requested_model": "test-model",
                    "response_model": "other",
                }
            )
            + "\n"
        )
    else:
        (directory / "provider-accounting.jsonl").write_text(
            json.dumps({"method": "POST", "status": 502, "requested_model": "test-model"}) + "\n"
        )
    result, _ = collect_arm(spec, "roboguide", directory, process)
    assert result["infrastructure_failure"] is True
    assert bool(result["fatal_reasons"]) is (problem != "http-502")
    assert result["official_pddl_success"] is None


def test_reset_comparison_requires_actual_pose_and_goal_evidence() -> None:
    """Matching seeds cannot substitute missing rotation or a mismatched physical initial state."""
    initial: JSONObject = {
        "episode_id": "7",
        "scene_id": "scene",
        "habitat_seed_config": 40,
        "agents": {"0": {"position": [0, 1, 2], "rotation": 0}},
        "goal_entity_positions": {"goal": [1, 2, 3]},
        "goal_conjuncts": ["goal"],
    }
    assert compare_reset(initial, initial)["status"] == "matched"
    assert (
        compare_reset(
            initial, {**initial, "agents": {"0": {"position": [3, 2, 1], "rotation": 0}}}
        )["status"]
        == "mismatch"
    )
    assert compare_reset(initial, {"habitat_seed_config": 40})["status"] == "unavailable"


def test_owned_process_logs_and_exit_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual isolated child failure retains its exit code and redacts streamed credentials."""
    from roboguide_eval import e1_pair

    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    script = tmp_path / "child.py"
    script.write_text(
        "import os,sys\nprint(os.environ['OPENAI_API_KEY'], flush=True)\nsys.exit(7)\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "private-sample-value")
    monkeypatch.setattr(e1_pair, "arm_command", lambda *args: (sys.executable, str(script)))
    monkeypatch.setattr(e1_pair.ResourceSampler, "observe", lambda self, pid: None)
    directory = tmp_path / "actual-arm"
    result = e1_pair.run_arm(spec, "emos", directory)
    assert result["exit_code"] == 7 and result["harness_error"] is None
    assert (directory / "stdout.log").read_text() == "[REDACTED]\n"
    closure = result["provider_closure"]
    assert isinstance(closure, dict) and closure["drained"] is True
    assert (directory / "vendor-cwd").is_dir() and (directory / "launch.json").is_file()


def test_actual_loaded_source_drift_is_not_declared_identity(tmp_path: Path) -> None:
    """A matching configuration cannot conceal a different module loaded by the child."""
    population, config = setup_pair(tmp_path)
    spec = PairSpec.load(population, config, "pair-0")
    directory = tmp_path / "arm"
    process = write_observations(spec, "emos", directory, official=True)
    path = directory / "native-evidence/runtime-source-manifest.json"
    write_json(
        path,
        {
            "python_executable": sys.executable,
            "modules": {"original.stage2": {"path": "/wrong", "sha256": "0" * 64}},
        },
    )
    result, _ = collect_arm(spec, "emos", directory, process)
    reasons = result["fatal_reasons"]
    assert isinstance(reasons, list)
    assert "runtime_module_identity_unconfirmed:original.stage2" in reasons
    assert result["official_pddl_success"] is True
