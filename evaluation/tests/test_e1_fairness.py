"""Adversarial regression suite for the E1 fairness foundation.

The tests map one-to-one onto the E1 protocol's statistical-admission
anti-pattern list and fixture matrix: same-seed/different-episode, same-
episode/different-dataset, system failure versus fairness failure,
``unavailable != equal``, ``unknown != mismatch``, declared-versus-observed
drift (including both arms drifting together), cross-population replay,
digest tampering, and unmodeled dimensions. The episode-51 JSON fixtures in
``fixtures/e1_fairness`` are examples proving the schema expresses a real
workload; no test treats a specific episode as the population abstraction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.e1_fairness import (
    DIMENSION_SPECS,
    ArmName,
    BenchmarkAuthorityIdentity,
    DatasetIdentity,
    DimensionClass,
    EmbodimentAgent,
    EmbodimentProfile,
    EvidenceStatus,
    FairnessError,
    FairnessReason,
    ModelConfigurationIdentity,
    ObservedField,
    PairComparability,
    PairManifest,
    PopulationManifest,
    PopulationRow,
    PopulationSelector,
    RequestedIntent,
    RunPairingEvidence,
    SimulatorIdentity,
    Stage2Identity,
    TaskIdentity,
    digest,
    load_population_manifest,
    load_run_pairing_evidence,
    validate_pair,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "e1_fairness"


def sha(seed: str) -> str:
    """Build one deterministic ``sha256:<64 hex>`` fixture digest."""
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def json_document(value: Any) -> dict[str, Any]:
    """Deep-copy one manifest/evidence document into untyped JSON for tampering."""
    document: dict[str, Any] = json.loads(json.dumps(value))
    return document


def population() -> PopulationManifest:
    """Build the baseline two-agent mobility population manifest."""
    return PopulationManifest.create(
        population_id="e1-mobility-test-population",
        protocol="controlled-v0.1",
        dataset=DatasetIdentity("mobility-dataset", "rev-1", sha("dataset")),
        task=TaskIdentity(
            benchmark="habitat-mas",
            task="mobility-task",
            task_spec_digest=sha("task-spec"),
            habitat_config_digest=sha("habitat-config"),
        ),
        benchmark_authority=BenchmarkAuthorityIdentity(
            measure="pddl_success",
            implementation_digest=sha("pddl-measure"),
            parameters={"must_call_stop": False, "robot_at_thresh_m": 0.3},
        ),
        embodiment_profile=EmbodimentProfile(
            (EmbodimentAgent(0, "RobotA"), EmbodimentAgent(1, "RobotB"))
        ),
        stage2_identity=Stage2Identity("stage2-checkout-base", {"policy.py": sha("stage2-policy")}),
        simulator_identity=SimulatorIdentity("simulator-base", "benchmark-env"),
        model_configuration=ModelConfigurationIdentity(
            provider="openai", model="model-x", reasoning_effort="medium"
        ),
        allowed_differences=("organization_axis",),
        required_differences=(),
        selector=PopulationSelector(
            "explicit_set",
            (
                PopulationRow("pair-1", 40, "51", "scene-one"),
                PopulationRow("pair-2", 7, "12", "scene-two"),
            ),
        ),
    )


def observed_fields(manifest: PopulationManifest) -> dict[str, ObservedField]:
    """Build a fully-available observed map that matches the manifest."""
    episode = "51"
    scene = "scene-one"
    for row in manifest.selector.rows:
        if row.pair_id == "pair-1":
            episode = row.expected_episode_id or episode
            scene = row.expected_scene_id or scene
    return {
        "dataset_identity": ObservedField(
            sha("dataset"), "runtime-file-digest", EvidenceStatus.AVAILABLE
        ),
        "episode_identity": ObservedField(episode, "official-banner", EvidenceStatus.AVAILABLE),
        "scene_identity": ObservedField(scene, "dataset-lookup", EvidenceStatus.AVAILABLE),
        "task_spec_identity": ObservedField(
            {
                "benchmark": "habitat-mas",
                "task": "mobility-task",
                "task_spec_digest": sha("task-spec"),
            },
            "config-probe",
            EvidenceStatus.AVAILABLE,
        ),
        "embodiment_profile": ObservedField(
            {"agents": [{"index": 0, "handle": "RobotA"}, {"index": 1, "handle": "RobotB"}]},
            "env-config",
            EvidenceStatus.AVAILABLE,
        ),
        "habitat_config_identity": ObservedField(
            {"habitat_config_digest": sha("habitat-config")},
            "config-probe",
            EvidenceStatus.AVAILABLE,
        ),
        "benchmark_authority_identity": ObservedField(
            {
                "measure": "pddl_success",
                "implementation_digest": sha("pddl-measure"),
                "parameters": {"must_call_stop": False, "robot_at_thresh_m": 0.3},
            },
            "env-config",
            EvidenceStatus.AVAILABLE,
        ),
        "stage2_identity": ObservedField(
            {
                "checkout_commit": "stage2-checkout-base",
                "file_digests": {"policy.py": sha("stage2-policy")},
            },
            "file-digest",
            EvidenceStatus.AVAILABLE,
        ),
        "simulator_identity": ObservedField(
            {"habitat_lab_commit": "simulator-base", "conda_environment": "benchmark-env"},
            "env-probe",
            EvidenceStatus.AVAILABLE,
        ),
        "model_configuration_identity": ObservedField(
            {"provider": "openai", "model": "model-x", "reasoning_effort": "medium"},
            "manifest",
            EvidenceStatus.AVAILABLE,
        ),
        "population_manifest_identity": ObservedField(
            manifest.digest, "manifest", EvidenceStatus.AVAILABLE
        ),
    }


def evidence(
    arm: ArmName,
    run_id: str,
    manifest: PopulationManifest,
    **overrides: Any,
) -> RunPairingEvidence:
    """Build one arm's baseline evidence with optional field overrides."""
    options: dict[str, Any] = {
        "arm": arm,
        "run_id": run_id,
        "pair_id": "pair-1",
        "population_manifest_digest": manifest.digest,
        "requested": RequestedIntent(seed=40),
        "observed": observed_fields(manifest),
    }
    options.update(overrides)
    return RunPairingEvidence.create(**options)


def with_observed(
    base: RunPairingEvidence,
    dimension_id: str,
    replacement: ObservedField | None,
) -> RunPairingEvidence:
    """Rebuild one evidence with one observed field replaced or removed."""
    observed = dict(base.observed)
    if replacement is None:
        observed.pop(dimension_id, None)
    else:
        observed[dimension_id] = replacement
    return RunPairingEvidence.create(
        arm=base.arm,
        run_id=base.run_id,
        pair_id=base.pair_id,
        population_manifest_digest=base.population_manifest_digest,
        requested=base.requested,
        observed=observed,
        additional_observations=base.additional_observations,
        system_outcome=base.system_outcome,
        environment_fingerprint=base.environment_fingerprint,
    )


def reason_values(pair: PairManifest) -> list[str]:
    """Return the pair's machine-stable reasons as sorted strings."""
    return sorted(reason.value for reason in pair.reasons)


def test_digest_is_key_order_independent() -> None:
    """The same semantic object with different key order digests equally."""
    assert digest({"b": 1, "a": {"y": 2, "x": 1}}) == digest({"a": {"x": 1, "y": 2}, "b": 1})


def test_digest_changes_on_any_content_change() -> None:
    """Changing required semantic content changes the digest."""
    assert digest({"episode": "51", "scene": "s"}) != digest({"episode": "69", "scene": "s"})
    assert digest({"episode": "51", "scene": "s"}) != digest({"episode": "51", "scene": "t"})


def test_population_manifest_tamper_is_rejected() -> None:
    """A mutated population manifest fails its digest check on load."""
    document = json_document(population().to_json())
    document["dataset"]["file_sha256"] = sha("other-dataset")
    with pytest.raises(FairnessError) as error:
        PopulationManifest.from_json(document)
    assert error.value.code == "digest_mismatch"


def test_run_evidence_tamper_is_rejected() -> None:
    """A mutated run evidence fails its digest check on load."""
    document = json_document(evidence("emos", "run-1", population()).to_json())
    document["observed"]["episode_identity"]["value"] = "69"
    with pytest.raises(FairnessError) as error:
        RunPairingEvidence.from_json(document)
    assert error.value.code == "digest_mismatch"


def test_population_manifest_digest_excludes_itself() -> None:
    """The digest field never participates in its own digest."""
    manifest = population()
    body = {key: value for key, value in manifest.to_json().items() if key != "digest"}
    assert digest(body) == manifest.digest


def test_unsupported_population_schema_fails_closed() -> None:
    """Unsupported population schema versions are rejected, not reinterpreted."""
    document = json_document(population().to_json())
    document["schema_version"] = "roboguide.e1.population-manifest/v9.9"
    with pytest.raises(FairnessError) as error:
        PopulationManifest.from_json(document)
    assert error.value.code == "schema_version_unsupported"


def test_unsupported_selector_policy_fails_closed() -> None:
    """Selector policies outside v0.1 are rejected instead of reinterpreted."""
    document = json_document(population().to_json())
    document["selector"]["policy"] = "all"
    with pytest.raises(FairnessError) as error:
        PopulationManifest.from_json(document)
    assert error.value.code == "schema_version_unsupported"


def test_valid_pair_from_fixture_files_is_comparable() -> None:
    """The episode-51 example fixture pair round-trips to PAIR_COMPARABLE."""
    manifest = load_population_manifest(FIXTURE_DIR / "population-mobility-example.json")
    emos = load_run_pairing_evidence(FIXTURE_DIR / "run-emos-example.json")
    roboguide = load_run_pairing_evidence(FIXTURE_DIR / "run-roboguide-example.json")
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE
    assert pair.reasons == ()
    assert pair.population_row.expected_episode_id == "51"


def test_same_seed_different_episode_is_not_comparable() -> None:
    """Equal seeds resolving to different episodes never pass as one workload."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "episode_identity",
        ObservedField("12", "official-banner", EvidenceStatus.AVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    reasons = reason_values(pair)
    assert "episode_identity_mismatch" in reasons
    assert "seed_equal_episode_different" in reasons


def test_same_episode_different_dataset_is_not_comparable() -> None:
    """The same episode id under different datasets is not one workload."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "dataset_identity",
        ObservedField(sha("dataset-b"), "runtime-file-digest", EvidenceStatus.AVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.DATASET_IDENTITY_MISMATCH in pair.reasons


def test_same_episode_different_scene_is_not_comparable() -> None:
    """The same episode id under different scenes is not one workload."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "scene_identity",
        ObservedField("scene-nine", "dataset-lookup", EvidenceStatus.AVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.SCENE_IDENTITY_MISMATCH in pair.reasons


def test_roboguide_system_failure_stays_fairness_valid() -> None:
    """A RoboGuide system failure never invalidates pair comparability."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = evidence(
        "roboguide",
        "run-2",
        manifest,
        system_outcome=ObservedField(
            {
                "mission_status": "Failed",
                "process_status": "completed",
                "failure_location": "control-plane",
            },
            "events",
            EvidenceStatus.AVAILABLE,
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE
    outcome = pair.arms["roboguide"].system_outcome
    assert outcome is not None and outcome.status is EvidenceStatus.AVAILABLE


def test_emos_system_failure_stays_fairness_valid() -> None:
    """An EMOS system failure never invalidates pair comparability."""
    manifest = population()
    emos = evidence(
        "emos",
        "run-1",
        manifest,
        system_outcome=ObservedField(
            {"process_status": "failed", "exit_code": 2}, "process", EvidenceStatus.AVAILABLE
        ),
    )
    roboguide = evidence("roboguide", "run-2", manifest)
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE


def test_benchmark_unavailable_from_sut_failure_stays_fairness_valid() -> None:
    """An unobservable benchmark result is not a fairness mismatch."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = evidence(
        "roboguide",
        "run-2",
        manifest,
        system_outcome=ObservedField(
            {"benchmark_tri_state": "BENCHMARK_UNAVAILABLE", "mission_status": "Failed"},
            "benchmark-assessment",
            EvidenceStatus.AVAILABLE,
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE
    assert not any("benchmark" in reason for reason in reason_values(pair))


def test_malformed_harness_evidence_fails_the_pair_closed() -> None:
    """External evidence corruption (INVALID status) blocks comparability."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "dataset_identity",
        ObservedField("corrupted", "runtime-file-digest", EvidenceStatus.INVALID),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert reason_values(pair) == ["observed_evidence_invalid"]


def test_dataset_digest_unavailable_is_never_equal() -> None:
    """An unobservable dataset digest excludes the pair instead of equating."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "dataset_identity",
        ObservedField(None, "runtime-file-digest", EvidenceStatus.UNAVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.DATASET_IDENTITY_UNAVAILABLE in pair.reasons


def test_declared_seed_alone_cannot_prove_workload_identity() -> None:
    """One arm with only a declared seed cannot prove episode identity."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(evidence("roboguide", "run-2", manifest), "episode_identity", None)
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.OBSERVED_EVIDENCE_MISSING in pair.reasons


def test_missing_scene_evidence_is_not_equal() -> None:
    """Explicitly unavailable scene evidence excludes the pair."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "scene_identity",
        ObservedField(None, "bridge-identity", EvidenceStatus.UNAVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.SCENE_IDENTITY_UNAVAILABLE in pair.reasons


def test_invalid_evidence_is_unknown_not_mismatch() -> None:
    """Malformed evidence reports INVALID, never a confirmed mismatch."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "episode_identity",
        ObservedField("not-an-episode", "official-banner", EvidenceStatus.INVALID),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    reasons = reason_values(pair)
    assert "observed_evidence_invalid" in reasons
    assert "episode_identity_mismatch" not in reasons


def test_success_never_rescues_a_mismatched_pair() -> None:
    """Successful outcomes cannot retroactively prove workload equality."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "episode_identity",
        ObservedField("12", "official-banner", EvidenceStatus.AVAILABLE),
    )
    roboguide = RunPairingEvidence.create(
        arm=roboguide.arm,
        run_id=roboguide.run_id,
        pair_id=roboguide.pair_id,
        population_manifest_digest=roboguide.population_manifest_digest,
        requested=roboguide.requested,
        observed=roboguide.observed,
        system_outcome=ObservedField(
            {"mission_status": "Completed", "benchmark_tri_state": "BENCHMARK_TRUE"},
            "events",
            EvidenceStatus.AVAILABLE,
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE


def test_cross_population_pair_is_rejected() -> None:
    """Arms bound to different population manifests fail closed."""
    manifest = population()
    other = PopulationManifest.create(
        population_id="other-population",
        protocol="controlled-v0.1",
        dataset=DatasetIdentity("other-dataset", "rev-1", sha("dataset-2")),
        task=TaskIdentity("habitat-mas", "other-task", sha("task-spec-2"), sha("habitat-config-2")),
        benchmark_authority=BenchmarkAuthorityIdentity(
            "pddl_success", sha("pddl-measure"), {"must_call_stop": False}
        ),
        embodiment_profile=EmbodimentProfile((EmbodimentAgent(0, "RobotA"),)),
        stage2_identity=Stage2Identity("other-base"),
        simulator_identity=SimulatorIdentity("simulator-base", "benchmark-env"),
        model_configuration=ModelConfigurationIdentity("openai", "model-x"),
        selector=PopulationSelector(
            "explicit_set", (PopulationRow("pair-1", 40, "51", "scene-one"),)
        ),
    )
    emos = evidence("emos", "run-1", manifest)
    roboguide = evidence("roboguide", "run-2", other)
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    reasons = reason_values(pair)
    assert "cross_population_pair" in reasons
    assert "population_identity_mismatch" in reasons


def test_population_digest_replay_is_rejected() -> None:
    """Both arms replaying another population's digest fails validation."""
    manifest = population()
    stale_digest = sha("stale-population")
    emos = RunPairingEvidence.create(
        arm="emos",
        run_id="run-1",
        pair_id="pair-1",
        population_manifest_digest=stale_digest,
        requested=RequestedIntent(seed=40),
        observed=observed_fields(manifest),
    )
    roboguide = RunPairingEvidence.create(
        arm="roboguide",
        run_id="run-2",
        pair_id="pair-1",
        population_manifest_digest=stale_digest,
        requested=RequestedIntent(seed=40),
        observed=observed_fields(manifest),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.POPULATION_IDENTITY_MISMATCH in pair.reasons


def test_stage2_drift_excludes_the_pair() -> None:
    """A changed Stage2 file digest is a held-constant violation."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "stage2_identity",
        ObservedField(
            {
                "checkout_commit": "stage2-drifted",
                "file_digests": {"policy.py": sha("drifted")},
            },
            "file-digest",
            EvidenceStatus.AVAILABLE,
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.STAGE2_IDENTITY_MISMATCH in pair.reasons


def test_both_arms_drifting_together_is_caught() -> None:
    """Arms running the same wrong workload are still not comparable."""
    manifest = population()
    wrong_digest = sha("dataset-wrong")
    emos = with_observed(
        evidence("emos", "run-1", manifest),
        "dataset_identity",
        ObservedField(wrong_digest, "runtime-file-digest", EvidenceStatus.AVAILABLE),
    )
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "dataset_identity",
        ObservedField(wrong_digest, "runtime-file-digest", EvidenceStatus.AVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.DATASET_IDENTITY_MISMATCH in pair.reasons


def test_simulator_drift_is_a_warning_not_an_exclusion() -> None:
    """Reproducibility-metadata drift warns without invalidating the pair."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "simulator_identity",
        ObservedField(
            {"habitat_lab_commit": "simulator-drifted", "conda_environment": "benchmark-env"},
            "env-probe",
            EvidenceStatus.AVAILABLE,
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE
    assert any(
        warning.reason is FairnessReason.SIMULATOR_IDENTITY_MISMATCH
        for warning in pair.reproducibility_warnings
    )


def test_model_identity_unavailable_excludes_the_pair() -> None:
    """An unobservable model identity cannot prove the held-constant model."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    roboguide = with_observed(
        evidence("roboguide", "run-2", manifest),
        "model_configuration_identity",
        ObservedField(None, "manifest", EvidenceStatus.UNAVAILABLE),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.MODEL_IDENTITY_UNAVAILABLE in pair.reasons


def test_unlisted_dimension_fails_the_pair_closed() -> None:
    """An unmodeled observed dimension is an unlistable requirement."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    base = evidence("roboguide", "run-2", manifest)
    roboguide = RunPairingEvidence.create(
        arm=base.arm,
        run_id=base.run_id,
        pair_id=base.pair_id,
        population_manifest_digest=base.population_manifest_digest,
        requested=base.requested,
        observed={
            **base.observed,
            "vendor_lock": ObservedField("on", "config", EvidenceStatus.AVAILABLE),
        },
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.UNLISTED_REQUIRED_DIFFERENCE in pair.reasons


def test_unknown_pair_id_is_unmatched() -> None:
    """A pair id outside the explicit selector has no population row."""
    manifest = population()
    emos = RunPairingEvidence.create(
        arm="emos",
        run_id="run-1",
        pair_id="pair-unknown",
        population_manifest_digest=manifest.digest,
        requested=RequestedIntent(seed=40),
        observed=observed_fields(manifest),
    )
    roboguide = RunPairingEvidence.create(
        arm="roboguide",
        run_id="run-2",
        pair_id="pair-unknown",
        population_manifest_digest=manifest.digest,
        requested=RequestedIntent(seed=40),
        observed=observed_fields(manifest),
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.POPULATION_ROW_UNMATCHED in pair.reasons


def test_row_seed_mismatch_is_unmatched() -> None:
    """A requested seed different from the row's seed is not this pair's run."""
    manifest = population()
    emos = RunPairingEvidence.create(
        arm="emos",
        run_id="run-1",
        pair_id="pair-1",
        population_manifest_digest=manifest.digest,
        requested=RequestedIntent(seed=41),
        observed=observed_fields(manifest),
    )
    roboguide = evidence("roboguide", "run-2", manifest)
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert FairnessReason.POPULATION_ROW_UNMATCHED in pair.reasons


def test_derived_metadata_drift_never_gates() -> None:
    """Derived metadata differences are recorded, never gating."""
    manifest = population()
    emos = evidence(
        "emos",
        "run-1",
        manifest,
        additional_observations={
            "same_floor": ObservedField(False, "census", EvidenceStatus.AVAILABLE)
        },
    )
    roboguide = evidence(
        "roboguide",
        "run-2",
        manifest,
        additional_observations={
            "same_floor": ObservedField(True, "census", EvidenceStatus.AVAILABLE)
        },
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_COMPARABLE
    assert pair.unlisted_differences
    assert pair.unlisted_differences[0]["field"] == "same_floor"


def test_pair_manifest_round_trip_preserves_verdict() -> None:
    """A serialized pair manifest restores with identical digest and facts."""
    manifest = population()
    emos = evidence(
        "emos",
        "run-1",
        manifest,
        system_outcome=ObservedField(
            {"mission_status": "Completed"}, "events", EvidenceStatus.AVAILABLE
        ),
    )
    roboguide = evidence(
        "roboguide",
        "run-2",
        manifest,
        system_outcome=ObservedField(
            {"mission_status": "Failed"}, "events", EvidenceStatus.AVAILABLE
        ),
    )
    pair = validate_pair(manifest, emos, roboguide)
    restored = PairManifest.from_json(pair.to_json())
    assert restored.digest == pair.digest
    assert restored.comparability is pair.comparability
    assert restored.arms["roboguide"].system_outcome is not None


def test_reason_enum_covers_protocol_codes() -> None:
    """The protocol's required reason codes exist in the stable enum."""
    required = {
        "dataset_identity_mismatch",
        "dataset_identity_unavailable",
        "episode_identity_mismatch",
        "episode_identity_unavailable",
        "scene_identity_mismatch",
        "scene_identity_unavailable",
        "embodiment_mismatch",
        "task_spec_mismatch",
        "habitat_config_mismatch",
        "benchmark_authority_mismatch",
        "stage2_identity_mismatch",
        "simulator_identity_mismatch",
        "model_identity_unavailable",
        "population_identity_mismatch",
        "population_row_unmatched",
        "cross_population_pair",
        "seed_equal_episode_different",
        "observed_evidence_missing",
        "unlisted_required_difference",
    }
    assert required <= {reason.value for reason in FairnessReason}


def test_dimension_classification_is_frozen() -> None:
    """Pairing keys and held constants gate; simulator identity warns only."""
    assert DIMENSION_SPECS["dataset_identity"].dimension_class is DimensionClass.PAIRING_KEY
    assert DIMENSION_SPECS["episode_identity"].dimension_class is DimensionClass.PAIRING_KEY
    assert DIMENSION_SPECS["scene_identity"].dimension_class is DimensionClass.PAIRING_KEY
    assert DIMENSION_SPECS["task_spec_identity"].dimension_class is DimensionClass.PAIRING_KEY
    assert (
        DIMENSION_SPECS["embodiment_profile"].dimension_class
        is DimensionClass.REQUIRED_HELD_CONSTANT
    )
    assert (
        DIMENSION_SPECS["benchmark_authority_identity"].dimension_class
        is DimensionClass.REQUIRED_HELD_CONSTANT
    )
    assert (
        DIMENSION_SPECS["stage2_identity"].dimension_class is DimensionClass.REQUIRED_HELD_CONSTANT
    )
    assert (
        DIMENSION_SPECS["model_configuration_identity"].dimension_class
        is DimensionClass.REQUIRED_HELD_CONSTANT
    )
    assert (
        DIMENSION_SPECS["simulator_identity"].dimension_class
        is DimensionClass.REPRODUCIBILITY_METADATA
    )


def test_observed_field_status_contracts_are_enforced() -> None:
    """UNAVAILABLE must carry null; AVAILABLE must carry a value."""
    with pytest.raises(FairnessError):
        ObservedField.from_json({"value": "x", "source": "s", "status": "unavailable"})
    with pytest.raises(FairnessError):
        ObservedField.from_json({"value": None, "source": "s", "status": "available"})


def test_requested_is_recorded_but_never_compared() -> None:
    """Declared intent never masks observed facts."""
    manifest = population()
    emos = evidence("emos", "run-1", manifest)
    base = evidence("roboguide", "run-2", manifest)
    roboguide = RunPairingEvidence.create(
        arm=base.arm,
        run_id=base.run_id,
        pair_id=base.pair_id,
        population_manifest_digest=base.population_manifest_digest,
        requested=RequestedIntent(seed=40, episode_id="51"),
        observed=with_observed(
            base,
            "episode_identity",
            ObservedField("12", "official-banner", EvidenceStatus.AVAILABLE),
        ).observed,
    )
    pair = validate_pair(manifest, emos, roboguide)
    assert pair.comparability is PairComparability.PAIR_NOT_COMPARABLE
    assert pair.arms["roboguide"].requested.episode_id == "51"


def test_fixture_documents_are_tamper_checked_on_load() -> None:
    """Loading a tampered example fixture fails its digest validation."""
    document = json.loads((FIXTURE_DIR / "run-emos-example.json").read_text(encoding="utf-8"))
    document["run_id"] = "tampered-run"
    with pytest.raises(FairnessError) as error:
        RunPairingEvidence.from_json(document)
    assert error.value.code == "digest_mismatch"


def test_stage2_surface_fixture_lists_required_files() -> None:
    """The Stage2 surface design fixture covers the held-constant files."""
    surface = json.loads((FIXTURE_DIR / "stage2-surface-example.json").read_text(encoding="utf-8"))
    required_paths = {
        entry["path"] for entry in surface["surface_entries"] if entry["class"] == "required"
    }
    assert "habitat-mas/habitat_mas/agents/crab_agent.py" in required_paths
    assert any("llm_spot_fetch_mobility" in path for path in required_paths)
