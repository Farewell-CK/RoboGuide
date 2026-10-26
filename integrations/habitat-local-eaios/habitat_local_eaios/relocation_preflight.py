"""Cross-check fresh relocation reset evidence before the B1 runner starts MI."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .endpoint_registry import FINALITY_PROFILE
from .model import IntegrationError
from .operation_admission import OPERATION_FEASIBILITY_SCHEMA, attach_relocation_admission
from .preassignment_feasibility import PREASSIGNMENT_FEASIBILITY_SCHEMA, preassignment_digest
from .relocation_capability import RELOCATION_READINESS_PROFILE
from .relocation_completion import COMPLETION_PROFILE
from .relocation_deployment import load_relocation_profile, verify_relocation_profile_sources
from .relocation_start import MAX_ARTIFACT_BYTES, planning_object_sources, validate_relocation_start
from .semantic_evidence import _digest
from .stage2_feedback import BOUND_FEEDBACK_PROFILE, FEEDBACK_PROFILE

PREFLIGHT_SCHEMA = "roboguide.habitat-relocation-preflight/v0.3"


def _read_artifact(path: Path) -> tuple[dict[str, Any], str]:
    """Read a bounded evidence object; missing, malformed and over-budget files fail closed."""
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact exceeds the byte budget")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("artifact is not an object")
    except (OSError, ValueError) as error:
        raise IntegrationError(f"relocation preflight cannot read {path.name}") from error
    return document, hashlib.sha256(raw).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    """Read a bounded JSON object without changing the original evidence."""
    return _read_artifact(path)[0]


def _check_execution_profile(run: Path, frozen_digest: str) -> dict[str, Any]:
    """Check the frozen deployment choice against the actual loaded child profile.

    Configuration presence alone does not prove that the child loaded the
    selected implementation. Enabled completion requires both the versioned
    Local How and successful loaded-interface/reset-reader checks. Legacy
    declarations retain their explicit environment override compatibility.
    """
    required, requirement_digest = _read_artifact(run / "b1-local-execution-profile-required.json")
    used, deployment_digest = _read_artifact(run / "b1-deployment-used.json")
    enabled = required.get("relocation_completion_binding")
    if (
        set(required)
        != {
            "schema_version",
            "run_id",
            "deployment_sha256",
            "frozen_input_sha256",
            "relocation_completion_binding",
        }
        or required.get("schema_version") != "roboguide.e1.b1-local-execution-profile-required/v0.1"
        or required.get("run_id") != run.name
        or type(enabled) is not bool
        or required.get("deployment_sha256") != deployment_digest
        or required.get("frozen_input_sha256") != frozen_digest
        or used.get("schema_version")
        not in (
            "roboguide.e1.b1-deployment-used/v0.2",
            "roboguide.e1.b1-deployment-used/v0.3",
            "roboguide.e1.b1-deployment-used/v0.4",
        )
        or used.get("frozen_input_sha256") != frozen_digest
        or used.get("relocation_completion_binding") is not enabled
    ):
        raise IntegrationError("frozen local execution profile requirement is invalid")
    declaration = used.get("declaration")
    if (
        not isinstance(declaration, dict)
        or declaration.get("enable_relocation") is not True
        or declaration.get("schema_version")
        not in (
            "roboguide.e1.b1-deployment/v0.1",
            "roboguide.e1.b1-deployment/v0.2",
            "roboguide.e1.b1-deployment/v0.3",
            "roboguide.e1.b1-deployment/v0.4",
        )
        or (
            declaration.get("schema_version") != "roboguide.e1.b1-deployment/v0.1"
            and declaration.get("relocation_completion_binding") is not enabled
        )
    ):
        raise IntegrationError("frozen deployment and local execution profile choice differ")
    actual, actual_digest = _read_artifact(run / "evidence/local-how-profile.json")
    actual_schema = actual.get("schema_version")
    live = declaration["schema_version"] in {
        "roboguide.e1.b1-deployment/v0.3",
        "roboguide.e1.b1-deployment/v0.4",
    }
    if live:
        from .endpoint_registry import LIVE_PROFILE, verify_endpoint_sources

        registry = verify_endpoint_sources(run / "endpoint-registry.json")
        execution_options = {
            "enable_observation_acquisition": declaration.get(
                "enable_observation_acquisition", False
            ),
            "observation_acquisition_max_steps": declaration.get(
                "observation_acquisition_max_steps", 256
            ),
            "contract_recovery_attempts": declaration.get("contract_recovery_attempts", 0),
        }
        extended = (
            execution_options["enable_observation_acquisition"]
            or execution_options["contract_recovery_attempts"] > 0
        )
        geometry_preferences = actual_schema == "roboguide.habitat-local-how-profile/v0.18"
        compound_observation = (
            geometry_preferences or actual_schema == "roboguide.habitat-local-how-profile/v0.17"
        )
        optics_recovery = (
            compound_observation or actual_schema == "roboguide.habitat-local-how-profile/v0.16"
        )
        viewpoint_recovery = (
            optics_recovery or actual_schema == "roboguide.habitat-local-how-profile/v0.15"
        )
        motion_feedback = actual_schema in {
            "roboguide.habitat-local-how-profile/v0.14",
            "roboguide.habitat-local-how-profile/v0.15",
            "roboguide.habitat-local-how-profile/v0.16",
            "roboguide.habitat-local-how-profile/v0.17",
            "roboguide.habitat-local-how-profile/v0.18",
        }
        view_search = actual_schema in {
            "roboguide.habitat-local-how-profile/v0.13",
            "roboguide.habitat-local-how-profile/v0.14",
            "roboguide.habitat-local-how-profile/v0.15",
            "roboguide.habitat-local-how-profile/v0.16",
            "roboguide.habitat-local-how-profile/v0.17",
            "roboguide.habitat-local-how-profile/v0.18",
        }
        navigation_dispatch = actual_schema in {
            "roboguide.habitat-local-how-profile/v0.12",
            "roboguide.habitat-local-how-profile/v0.13",
            "roboguide.habitat-local-how-profile/v0.14",
            "roboguide.habitat-local-how-profile/v0.15",
            "roboguide.habitat-local-how-profile/v0.16",
            "roboguide.habitat-local-how-profile/v0.17",
            "roboguide.habitat-local-how-profile/v0.18",
        }
        observation_control = actual_schema in {
            "roboguide.habitat-local-how-profile/v0.11",
            "roboguide.habitat-local-how-profile/v0.12",
            "roboguide.habitat-local-how-profile/v0.13",
            "roboguide.habitat-local-how-profile/v0.14",
            "roboguide.habitat-local-how-profile/v0.15",
            "roboguide.habitat-local-how-profile/v0.16",
            "roboguide.habitat-local-how-profile/v0.17",
            "roboguide.habitat-local-how-profile/v0.18",
        }
        if viewpoint_recovery:
            from .view_recovery import view_recovery_declaration

            if json.dumps(
                actual.get("observation_viewpoint_recovery"), sort_keys=True
            ) != json.dumps(view_recovery_declaration(optics=optics_recovery), sort_keys=True):
                raise IntegrationError("actual observation viewpoint recovery contract is invalid")
        if motion_feedback:
            from .view_motion import motion_feedback_declaration

            expected_motion = motion_feedback_declaration()
            actual_motion = actual.get("observation_motion_feedback")
            if (
                actual_motion != expected_motion
                or not isinstance(actual_motion, dict)
                or any(
                    type(actual_motion[key]) is not type(value)
                    for key, value in expected_motion.items()
                )
            ):
                raise IntegrationError("actual observation motion feedback contract is invalid")
        if view_search:
            from .view_scan import view_scan_declaration

            expected_scan = view_scan_declaration()
            actual_scan = actual.get("observation_view_search")
            if (
                actual_scan != expected_scan
                or not isinstance(actual_scan, dict)
                or any(
                    type(actual_scan[key]) is not type(value)
                    for key, value in expected_scan.items()
                )
            ):
                raise IntegrationError("actual observation view search contract is invalid")
        if navigation_dispatch:
            from .navigation_dispatch import navigation_dispatch_declaration

            if actual.get("navigation_dispatch") != navigation_dispatch_declaration():
                raise IntegrationError("actual acquisition navigation dispatch contract is invalid")
        if observation_control:
            from .observation_control import observation_control_declaration

            if (
                execution_options["enable_observation_acquisition"] is not True
                or actual.get("observation_control")
                != observation_control_declaration(compound=compound_observation)
                or type(actual["observation_control"].get("max_pause_simulator_steps")) is not int
                or type(actual["observation_control"].get("condition_persistence_guaranteed"))
                is not bool
            ):
                raise IntegrationError("actual observation feedback/pause contract is invalid")
        if (
            used["schema_version"]
            not in {"roboguide.e1.b1-deployment-used/v0.3", "roboguide.e1.b1-deployment-used/v0.4"}
            or actual_schema
            != (
                "roboguide.habitat-local-how-profile/v0.18"
                if geometry_preferences
                else "roboguide.habitat-local-how-profile/v0.17"
                if compound_observation
                else "roboguide.habitat-local-how-profile/v0.16"
                if optics_recovery
                else "roboguide.habitat-local-how-profile/v0.15"
                if viewpoint_recovery
                else "roboguide.habitat-local-how-profile/v0.14"
                if motion_feedback
                else "roboguide.habitat-local-how-profile/v0.13"
                if view_search
                else "roboguide.habitat-local-how-profile/v0.12"
                if navigation_dispatch
                else "roboguide.habitat-local-how-profile/v0.11"
                if observation_control
                else "roboguide.habitat-local-how-profile/v0.10"
                if extended
                else "roboguide.habitat-local-how-profile/v0.9"
            )
            or (extended and actual.get("execution_options") != execution_options)
            or set(actual)
            != {
                "schema_version",
                "base_profile",
                "execution_profile",
                "endpoint_registry_digest",
                "observation_enabled",
                "unassigned_policy",
                "official_success_authority",
                *({"execution_options"} if extended else set()),
                *({"observation_control"} if observation_control else set()),
                *({"navigation_dispatch"} if navigation_dispatch else set()),
                *({"observation_view_search"} if view_search else set()),
                *({"observation_motion_feedback"} if motion_feedback else set()),
                *({"observation_viewpoint_recovery"} if viewpoint_recovery else set()),
                *({"initial_observation_preferences"} if geometry_preferences else set()),
            }
            or actual.get("execution_profile") != LIVE_PROFILE
            or actual.get("endpoint_registry_digest") != registry["digest"]
            or actual.get("observation_enabled") is not declaration.get("enable_observation")
            or _read_object(run / "evidence/endpoint-registry-used.json") != registry
        ):
            raise IntegrationError("actual live Local How differs from frozen endpoint sources")
        if geometry_preferences:
            from .observation_preferences import preference_declaration, validate_preference_source

            if actual.get("initial_observation_preferences") != preference_declaration():
                raise IntegrationError("actual observation preference profile is invalid")
            validate_preference_source(
                _read_object(run / "evidence/preassignment-feasibility.json"),
                registry,
                _read_object(run / "evidence/perception-readiness.json"),
            )
        if optics_recovery and declaration.get("enable_observation") is True:
            from .observation_optics import read_optics_declaration, validate_optics_readiness

            optics = read_optics_declaration(run / "observation-optics-declaration.json", registry)
            validate_optics_readiness(
                _read_object(run / "evidence/perception-readiness.json"), optics
            )
        actual = actual.get("base_profile", {})
    expected = {
        "schema_version": "roboguide.habitat-local-how-profile/v0.8"
        if enabled
        else "roboguide.habitat-local-how-profile/v0.2",
        "navigation_point_resolver": "original-emos-oracle",
        "official_success_authority": "habitat-pddl",
        "stage2_execution_feedback_profile": BOUND_FEEDBACK_PROFILE
        if enabled
        else FEEDBACK_PROFILE,
        "reset_route_support_enabled": False,
    }
    if enabled:
        expected["relocation_completion_profile"] = COMPLETION_PROFILE
    if actual != expected or actual.get("reset_route_support_enabled") is not False:
        raise IntegrationError("actual loaded Local How differs from the frozen deployment choice")
    proof: dict[str, Any] = {
        "relocation_completion_binding": enabled,
        "requirement_sha256": requirement_digest,
        "deployment_sha256": deployment_digest,
        "local_how_sha256": actual_digest,
        "local_how_schema": actual_schema,
    }
    readiness_path = run / "evidence/relocation-completion-readiness.json"
    if enabled:
        readiness, readiness_digest = _read_artifact(readiness_path)
        if (
            readiness
            != {
                "schema_version": "roboguide.relocation-completion-readiness/v0.1",
                "profile": COMPLETION_PROFILE,
                "ready": True,
                "source": "loaded-place-skills-and-reset-world-readers",
            }
            or readiness.get("ready") is not True
        ):
            raise IntegrationError("actual relocation completion readiness is unavailable")
        proof["completion_readiness_sha256"] = readiness_digest
    elif readiness_path.exists():
        raise IntegrationError("unexpected completion readiness in an unbound deployment")
    return proof


def preflight_relocation(run: Path) -> dict[str, Any]:
    """Bind reset sources and actual readiness to the caller-validated frozen B1 workload.

    The runner first uses the authoritative B1 input parser. This local
    check consumes its identity fields without changing that input contract
    or Formal admission. It never loads Habitat, calls a model, mutates the
    original evidence or infers that manipulation will succeed.
    """
    frozen, frozen_digest = _read_artifact(run / "b1-input-used.json")
    execution_profile = _check_execution_profile(run, frozen_digest)
    profile_path = run / "relocation-registration-profile.json"
    verify_relocation_profile_sources(profile_path)
    profile = load_relocation_profile(profile_path)
    evidence = run / "evidence"
    semantic = _read_object(evidence / "authoritative-semantic-evidence.json")
    start = _read_object(evidence / "relocation-episode-start.json")
    planning = _read_object(evidence / "authoritative-planning-world-evidence.json")
    readiness = _read_object(evidence / "relocation-readiness.json")
    used_profile = _read_object(evidence / "relocation-registration-profile-used.json")
    for document in (semantic, planning):
        if document.get("digest") != _digest(
            {key: value for key, value in document.items() if key != "digest"}
        ):
            raise IntegrationError("relocation semantic or planning evidence digest is invalid")
    try:
        expected = {
            "run_id": run.name,
            "episode_id": frozen["episode_id"],
            "scene_id": frozen["scene_id"],
            "dataset_revision": frozen["dataset_revision"],
            "dataset_sha256": frozen["dataset_sha256"],
            "habitat_seed": frozen["seed"],
        }
        validate_relocation_start(start, semantic)
        planning_identity = {
            key: planning["identity"][key] for key in expected if key != "habitat_seed"
        }
    except (KeyError, TypeError, ValueError) as error:
        raise IntegrationError("relocation reset identity or structure is invalid") from error
    if (
        start["identity"] != expected
        or type(start["reset_count"]) is not int
        or type(start["simulator_steps"]) is not int
        or planning_identity
        != {key: value for key, value in expected.items() if key != "habitat_seed"}
        or start["registration_profile_digest"] != profile["digest"]
        or used_profile != profile
        or [agent["agent_id"] for agent in start["agents"]]
        != [agent["agent_id"] for agent in profile["agents"]]
    ):
        raise IntegrationError("relocation reset differs from frozen workload or registration")
    if (
        planning.get("schema_version")
        not in {
            "roboguide.authoritative-planning-world-evidence/v0.3",
            "roboguide.authoritative-planning-world-evidence/v0.4",
        }
        or planning.get("authority") != "environment-authoritative"
        or planning.get("object_sources") != planning_object_sources(start, semantic)
    ):
        raise IntegrationError("MI planning sources differ from the actual relocation reset")
    if (
        readiness.get("schema_version") != RELOCATION_READINESS_PROFILE
        or readiness.get("ready") is not True
        or readiness.get("policy_count") != len(profile["agents"])
        or type(readiness.get("policy_count")) is not int
        or readiness.get("missing_tools") != []
        or readiness.get("missing_skills") != []
    ):
        raise IntegrationError("actual relocation skill readiness is unavailable")
    matrix = _read_object(evidence / "preassignment-feasibility.json")
    live = matrix.get("schema_version") in {
        "roboguide.deployment-intent-feasibility/v0.5",
        "roboguide.deployment-intent-feasibility/v0.6",
        "roboguide.deployment-intent-feasibility/v0.7",
    }
    if not live and matrix.get("schema_version") != OPERATION_FEASIBILITY_SCHEMA:
        raise IntegrationError("relocation requires versioned deployment operation admission")
    navigation = {
        key: value
        for key, value in matrix.items()
        if key
        not in {
            "digest",
            "operation_admission",
            "execution_profile",
            "finality_profile",
            "observation_preferences",
        }
    }
    navigation["schema_version"] = PREASSIGNMENT_FEASIBILITY_SCHEMA
    navigation["digest"] = preassignment_digest(navigation)
    expected_matrix = attach_relocation_admission(navigation, semantic, start, profile)
    if live:
        expected_matrix["schema_version"] = matrix["schema_version"]
        expected_matrix["execution_profile"] = matrix["execution_profile"]
        if matrix["schema_version"] in {
            "roboguide.deployment-intent-feasibility/v0.6",
            "roboguide.deployment-intent-feasibility/v0.7",
        }:
            finality = matrix.get("finality_profile")
            if (
                not isinstance(finality, dict)
                or type(finality.get("endpoint_capacity")) is not int
                or finality != FINALITY_PROFILE
            ):
                raise IntegrationError("deployment operation admission finality profile is invalid")
            expected_matrix["finality_profile"] = FINALITY_PROFILE
        if matrix["schema_version"] == "roboguide.deployment-intent-feasibility/v0.7":
            from .observation_preferences import validate_preference_source

            validate_preference_source(
                matrix,
                _read_object(run / "endpoint-registry.json"),
                _read_object(evidence / "perception-readiness.json"),
            )
            expected_matrix["observation_preferences"] = matrix["observation_preferences"]
        expected_matrix["digest"] = preassignment_digest(
            {key: value for key, value in expected_matrix.items() if key != "digest"}
        )
    if matrix != expected_matrix:
        raise IntegrationError("deployment operation admission differs from actual reset sources")
    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "valid": True,
        "identity": expected,
        "registration_profile_digest": profile["digest"],
        "start_digest": start["digest"],
        "semantic_evidence_digest": semantic["digest"],
        "planning_evidence_digest": planning["digest"],
        "reset_count": start["reset_count"],
        "simulator_steps": start["simulator_steps"],
        "deployment_admission_digest": matrix["digest"],
        "local_execution_profile": execution_profile,
    }


def main() -> None:
    """Preserve pass/failure evidence and return a failing status before any Mission submission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = preflight_relocation(args.run)
    except (IntegrationError, KeyError, TypeError, ValueError) as error:
        result = {
            "schema_version": PREFLIGHT_SCHEMA,
            "valid": False,
            "reason": str(error),
        }
        (args.run / "relocation-preflight.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        parser.exit(1, "relocation reset preflight failed; see relocation-preflight.json\n")
    (args.run / "relocation-preflight.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
