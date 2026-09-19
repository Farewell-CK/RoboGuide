"""E1 fairness and reproducibility manifest foundation.

This module makes the E1 paired-comparison methodology machine-checkable. It
defines three versioned artifacts:

1. ``PopulationManifest`` — the frozen experiment-level definition of one E1
   population: dataset, task, benchmark authority, embodiment profile,
   held-constant surfaces (Stage2, simulator, model), the allowed
   organization-axis differences, and the population selector with explicit
   workload rows. No single episode is baked into the abstraction; rows
   carry expectations, and the episode-51 example is only a fixture.
2. ``RunPairingEvidence`` — one EMOS or RoboGuide run's pairing evidence. It
   strictly separates ``requested`` (declared intent) from ``observed``
   (runtime evidence), and every observed field carries a value, a source,
   and an evidence status (``AVAILABLE``/``UNAVAILABLE``/``INVALID``).
3. ``PairManifest`` — the dimension-by-dimension comparability verdict for
   one EMOS run plus one RoboGuide run against one population manifest.

Invariants this module enforces (see ``evaluation/docs`` for rationale):

- Fairness comparability answers only "did both arms run the same
  workload?". System success or failure of either arm never affects
  comparability; outcomes are recorded verbatim in the pair manifest.
- ``UNAVAILABLE`` evidence is never treated as equality. A missing fact on a
  gating dimension fails the pair closed with a machine-stable reason.
- Malformed (``INVALID``) evidence is distinguished from explicit
  unavailability and from confirmed mismatch; none of the three is ever
  interpreted as equality.
- Declared configuration equality never substitutes for runtime evidence;
  the validator additionally compares each arm's observed facts against the
  population's declared identities, so two arms that drift *together* away
  from the frozen definition are still caught.
- Fairness verdicts are independent from Formal population admission (the
  ``valid_for_formal_population`` rule owned by the B1 evidence modules).
  This module never reads or writes that decision.

Digests use the repository canonicalization: SHA-256 over sorted-key,
compact, UTF-8, ``allow_nan=False`` JSON, formatted as ``sha256:<hex>``. A
manifest's digest field never participates in its own digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

from roboguide_eval.models import JSONObject, JSONValue

POPULATION_MANIFEST_SCHEMA = "roboguide.e1.population-manifest/v0.1"
RUN_PAIRING_EVIDENCE_SCHEMA = "roboguide.e1.run-pairing-evidence/v0.1"
PAIR_MANIFEST_SCHEMA = "roboguide.e1.pair-manifest/v0.1"

ArmName = Literal["emos", "roboguide"]

ComparisonState = Literal["equal", "mismatch", "unavailable", "invalid"]

_DIGEST_PREFIX = "sha256:"
_HEX_DIGITS = frozenset("0123456789abcdef")
_ARMS: tuple[ArmName, ...] = ("emos", "roboguide")


class FairnessError(ValueError):
    """Report manifest construction, parsing, or digest-integrity failures.

    Attributes:
        code: Machine-stable error family (for example ``digest_mismatch``
            or ``schema_version_unsupported``); the message carries detail.
    """

    def __init__(self, code: str, message: str) -> None:
        """Store the stable code and the descriptive message."""
        super().__init__(message)
        self.code = code


def canonical_json_bytes(value: JSONValue) -> bytes:
    """Serialize one finite JSON value with the fixed fairness canonicalization.

    Args:
        value: The JSON value to canonicalize.

    Returns:
        The canonical UTF-8 bytes: sorted keys, compact separators,
        ``ensure_ascii=False``, ``allow_nan=False``.

    Raises:
        FairnessError: When the value is not finite JSON.
    """
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise FairnessError("non_finite_json", f"value is not finite JSON: {error}") from error


def digest(value: JSONValue) -> str:
    """Return the ``sha256:<hex>`` digest of one canonicalized JSON value."""
    return _DIGEST_PREFIX + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_digest_string(value: JSONValue, field_name: str) -> str:
    """Require one ``sha256:<hex>`` string field and return it.

    Raises:
        FairnessError: When the value is not a well-formed digest string.
    """
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        raise FairnessError("invalid_digest_field", f"{field_name} must be 'sha256:<64 hex>'")
    hex_part = value[len(_DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(character not in _HEX_DIGITS for character in hex_part):
        raise FairnessError("invalid_digest_field", f"{field_name} must be 'sha256:<64 hex>'")
    return value


def _require_text(value: JSONValue, field_name: str) -> str:
    """Require one nonblank string field and return it.

    Raises:
        FairnessError: When the value is not a nonblank string.
    """
    if not isinstance(value, str) or not value.strip():
        raise FairnessError("invalid_text_field", f"{field_name} must be a nonblank string")
    return value


def _require_object(value: JSONValue, field_name: str) -> JSONObject:
    """Require one string-keyed mapping field and return a copy.

    Raises:
        FairnessError: When the value is not a JSON object.
    """
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FairnessError("invalid_object_field", f"{field_name} must be a JSON object")
    return dict(value)


def _require_list(value: JSONValue, field_name: str) -> list[JSONValue]:
    """Require one list field and return a copy.

    Raises:
        FairnessError: When the value is not a list.
    """
    if not isinstance(value, list):
        raise FairnessError("invalid_list_field", f"{field_name} must be a list")
    return list(value)


def _require_bool(value: JSONValue, field_name: str) -> bool:
    """Require one strict boolean field and return it.

    Raises:
        FairnessError: When the value is not a boolean.
    """
    if not isinstance(value, bool):
        raise FairnessError("invalid_bool_field", f"{field_name} must be a boolean")
    return value


def _require_optional_int(value: JSONValue, field_name: str) -> int | None:
    """Require one integer-or-null field and return it.

    Raises:
        FairnessError: When the value is neither an integer nor ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise FairnessError("invalid_int_field", f"{field_name} must be an integer or null")
    return value


def _require_optional_text(value: JSONValue, field_name: str) -> str | None:
    """Require one nonblank-string-or-null field and return it.

    Raises:
        FairnessError: When the value is neither a nonblank string nor
            ``None``.
    """
    if value is None:
        return None
    return _require_text(value, field_name)


class EvidenceStatus(StrEnum):
    """Evidence status of one observed pairing fact.

    ``AVAILABLE`` carries a concrete observed value. ``UNAVAILABLE`` means
    the fact could not be observed (value is ``None``); it is never treated
    as equality. ``INVALID`` means evidence existed but was malformed; it is
    distinct from both unavailability and confirmed mismatch.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class DimensionClass(StrEnum):
    """Fairness classification of one comparison dimension.

    ``PAIRING_KEY`` and ``REQUIRED_HELD_CONSTANT`` gate pair comparability.
    ``REPRODUCIBILITY_METADATA`` is recorded and warned on drift but never
    gates. ``ALLOWED_DIFFERENCE`` belongs to the experiment's organization
    axis or outcomes and is recorded only.
    """

    PAIRING_KEY = "pairing_key"
    REQUIRED_HELD_CONSTANT = "required_held_constant"
    REPRODUCIBILITY_METADATA = "reproducibility_metadata"
    ALLOWED_DIFFERENCE = "allowed_difference"


class PairComparability(StrEnum):
    """Overall comparability verdict for one candidate pair."""

    PAIR_COMPARABLE = "pair_comparable"
    PAIR_NOT_COMPARABLE = "pair_not_comparable"


class FairnessReason(StrEnum):
    """Machine-stable pair-exclusion and warning reasons.

    The values are stable codes; free text never carries decision authority.
    The codes required by the E1 protocol are declared first; the remaining
    per-dimension codes complete the set so every gating dimension has an
    explicit mismatch and an explicit unavailable reason.
    """

    # Protocol-required codes.
    DATASET_IDENTITY_MISMATCH = "dataset_identity_mismatch"
    DATASET_IDENTITY_UNAVAILABLE = "dataset_identity_unavailable"
    EPISODE_IDENTITY_MISMATCH = "episode_identity_mismatch"
    EPISODE_IDENTITY_UNAVAILABLE = "episode_identity_unavailable"
    SCENE_IDENTITY_MISMATCH = "scene_identity_mismatch"
    SCENE_IDENTITY_UNAVAILABLE = "scene_identity_unavailable"
    EMBODIMENT_MISMATCH = "embodiment_mismatch"
    TASK_SPEC_MISMATCH = "task_spec_mismatch"
    HABITAT_CONFIG_MISMATCH = "habitat_config_mismatch"
    BENCHMARK_AUTHORITY_MISMATCH = "benchmark_authority_mismatch"
    STAGE2_IDENTITY_MISMATCH = "stage2_identity_mismatch"
    SIMULATOR_IDENTITY_MISMATCH = "simulator_identity_mismatch"
    MODEL_IDENTITY_UNAVAILABLE = "model_identity_unavailable"
    POPULATION_IDENTITY_MISMATCH = "population_identity_mismatch"
    POPULATION_ROW_UNMATCHED = "population_row_unmatched"
    CROSS_POPULATION_PAIR = "cross_population_pair"
    SEED_EQUAL_EPISODE_DIFFERENT = "seed_equal_episode_different"
    OBSERVED_EVIDENCE_MISSING = "observed_evidence_missing"
    UNLISTED_REQUIRED_DIFFERENCE = "unlisted_required_difference"
    # Completing codes (documented additions for full dimension coverage).
    EMBODIMENT_UNAVAILABLE = "embodiment_unavailable"
    TASK_SPEC_UNAVAILABLE = "task_spec_unavailable"
    HABITAT_CONFIG_UNAVAILABLE = "habitat_config_unavailable"
    BENCHMARK_AUTHORITY_UNAVAILABLE = "benchmark_authority_unavailable"
    STAGE2_IDENTITY_UNAVAILABLE = "stage2_identity_unavailable"
    SIMULATOR_IDENTITY_UNAVAILABLE = "simulator_identity_unavailable"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    OBSERVED_EVIDENCE_INVALID = "observed_evidence_invalid"
    ENVIRONMENT_FINGERPRINT_DRIFT = "environment_fingerprint_drift"
    POPULATION_ROW_EXPECTATION_MISSING = "population_row_expectation_missing"


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    """One pairing dimension's classification and stable reason mapping.

    Attributes:
        dimension_id: Stable identifier used in run evidence and pair
            manifests.
        dimension_class: The fairness classification controlling gating.
        mismatch_reason: Reason emitted on confirmed inequality.
        unavailable_reason: Reason emitted when the dimension is explicitly
            unobservable on an arm.
    """

    dimension_id: str
    dimension_class: DimensionClass
    mismatch_reason: FairnessReason
    unavailable_reason: FairnessReason


DIMENSION_SPECS: Mapping[str, DimensionSpec] = {
    "dataset_identity": DimensionSpec(
        "dataset_identity",
        DimensionClass.PAIRING_KEY,
        FairnessReason.DATASET_IDENTITY_MISMATCH,
        FairnessReason.DATASET_IDENTITY_UNAVAILABLE,
    ),
    "episode_identity": DimensionSpec(
        "episode_identity",
        DimensionClass.PAIRING_KEY,
        FairnessReason.EPISODE_IDENTITY_MISMATCH,
        FairnessReason.EPISODE_IDENTITY_UNAVAILABLE,
    ),
    "scene_identity": DimensionSpec(
        "scene_identity",
        DimensionClass.PAIRING_KEY,
        FairnessReason.SCENE_IDENTITY_MISMATCH,
        FairnessReason.SCENE_IDENTITY_UNAVAILABLE,
    ),
    "task_spec_identity": DimensionSpec(
        "task_spec_identity",
        DimensionClass.PAIRING_KEY,
        FairnessReason.TASK_SPEC_MISMATCH,
        FairnessReason.TASK_SPEC_UNAVAILABLE,
    ),
    "embodiment_profile": DimensionSpec(
        "embodiment_profile",
        DimensionClass.REQUIRED_HELD_CONSTANT,
        FairnessReason.EMBODIMENT_MISMATCH,
        FairnessReason.EMBODIMENT_UNAVAILABLE,
    ),
    "habitat_config_identity": DimensionSpec(
        "habitat_config_identity",
        DimensionClass.REQUIRED_HELD_CONSTANT,
        FairnessReason.HABITAT_CONFIG_MISMATCH,
        FairnessReason.HABITAT_CONFIG_UNAVAILABLE,
    ),
    "benchmark_authority_identity": DimensionSpec(
        "benchmark_authority_identity",
        DimensionClass.REQUIRED_HELD_CONSTANT,
        FairnessReason.BENCHMARK_AUTHORITY_MISMATCH,
        FairnessReason.BENCHMARK_AUTHORITY_UNAVAILABLE,
    ),
    "stage2_identity": DimensionSpec(
        "stage2_identity",
        DimensionClass.REQUIRED_HELD_CONSTANT,
        FairnessReason.STAGE2_IDENTITY_MISMATCH,
        FairnessReason.STAGE2_IDENTITY_UNAVAILABLE,
    ),
    "simulator_identity": DimensionSpec(
        "simulator_identity",
        DimensionClass.REPRODUCIBILITY_METADATA,
        FairnessReason.SIMULATOR_IDENTITY_MISMATCH,
        FairnessReason.SIMULATOR_IDENTITY_UNAVAILABLE,
    ),
    "model_configuration_identity": DimensionSpec(
        "model_configuration_identity",
        DimensionClass.REQUIRED_HELD_CONSTANT,
        FairnessReason.MODEL_IDENTITY_MISMATCH,
        FairnessReason.MODEL_IDENTITY_UNAVAILABLE,
    ),
    "population_manifest_identity": DimensionSpec(
        "population_manifest_identity",
        DimensionClass.PAIRING_KEY,
        FairnessReason.POPULATION_IDENTITY_MISMATCH,
        FairnessReason.OBSERVED_EVIDENCE_MISSING,
    ),
}

DIMENSION_ORDER: tuple[str, ...] = (
    "dataset_identity",
    "episode_identity",
    "scene_identity",
    "task_spec_identity",
    "embodiment_profile",
    "habitat_config_identity",
    "benchmark_authority_identity",
    "stage2_identity",
    "simulator_identity",
    "model_configuration_identity",
    "population_manifest_identity",
)

_GATE_CLASSES = frozenset({DimensionClass.PAIRING_KEY, DimensionClass.REQUIRED_HELD_CONSTANT})


def dimension_gates(dimension_id: str) -> bool:
    """Return whether one dimension's non-equal outcome gates comparability.

    Raises:
        KeyError: When the dimension id is unknown.
    """
    return DIMENSION_SPECS[dimension_id].dimension_class in _GATE_CLASSES


@dataclass(frozen=True, slots=True)
class ObservedField:
    """One observed pairing fact with its source and evidence status.

    Attributes:
        value: The observed value; ``None`` exactly when unavailable.
        source: Machine-stable provenance of the observation (for example
            ``official-banner`` or ``bridge-identity``); required for audit,
            never a decision authority by itself.
        status: The evidence status of this fact.
    """

    value: JSONValue
    source: str
    status: EvidenceStatus

    @classmethod
    def from_json(cls, value: JSONValue) -> ObservedField:
        """Restore one observed field from its JSON form.

        Raises:
            FairnessError: When the shape or the value/status contract is
                violated (``UNAVAILABLE`` must carry a null value; the other
                statuses must carry a value).
        """
        item = _require_object(value, "observed field")
        if set(item) != {"value", "source", "status"}:
            raise FairnessError(
                "invalid_observed_field",
                "observed field must have exactly value/source/status",
            )
        status = EvidenceStatus(_require_text(item["status"], "observed field status"))
        if status is EvidenceStatus.UNAVAILABLE and item["value"] is not None:
            raise FairnessError(
                "invalid_observed_field",
                "unavailable observed field must carry value null",
            )
        if status is not EvidenceStatus.UNAVAILABLE and item["value"] is None:
            raise FairnessError(
                "invalid_observed_field",
                "available or invalid observed field must carry a value",
            )
        return ObservedField(
            value=item["value"],
            source=_require_text(item["source"], "observed field source"),
            status=status,
        )

    def to_json(self) -> JSONObject:
        """Serialize the observed field."""
        return {"value": self.value, "source": self.source, "status": self.status.value}


def _available(value: JSONValue, source: str) -> ObservedField:
    """Build one ``AVAILABLE`` observed field (test and builder helper)."""
    return ObservedField(value=value, source=source, status=EvidenceStatus.AVAILABLE)


def _unavailable(source: str) -> ObservedField:
    """Build one ``UNAVAILABLE`` observed field (test and builder helper)."""
    return ObservedField(value=None, source=source, status=EvidenceStatus.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """Declared benchmark dataset identity (name, revision, content digest)."""

    name: str
    revision: str
    file_sha256: str

    @classmethod
    def from_json(cls, value: JSONValue) -> DatasetIdentity:
        """Restore one dataset identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "dataset")
        if set(item) != {"name", "revision", "file_sha256"}:
            raise FairnessError(
                "invalid_dataset", "dataset must have exactly name/revision/file_sha256"
            )
        return cls(
            name=_require_text(item["name"], "dataset.name"),
            revision=_require_text(item["revision"], "dataset.revision"),
            file_sha256=_require_digest_string(item["file_sha256"], "dataset.file_sha256"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the dataset identity."""
        return {"name": self.name, "revision": self.revision, "file_sha256": self.file_sha256}


@dataclass(frozen=True, slots=True)
class TaskIdentity:
    """Declared benchmark task identity with its spec and config digests."""

    benchmark: str
    task: str
    task_spec_digest: str
    habitat_config_digest: str

    @classmethod
    def from_json(cls, value: JSONValue) -> TaskIdentity:
        """Restore one task identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "task")
        required = {"benchmark", "task", "task_spec_digest", "habitat_config_digest"}
        if set(item) != required:
            raise FairnessError("invalid_task", f"task must have exactly {sorted(required)}")
        return cls(
            benchmark=_require_text(item["benchmark"], "task.benchmark"),
            task=_require_text(item["task"], "task.task"),
            task_spec_digest=_require_digest_string(
                item["task_spec_digest"], "task.task_spec_digest"
            ),
            habitat_config_digest=_require_digest_string(
                item["habitat_config_digest"], "task.habitat_config_digest"
            ),
        )

    def to_json(self) -> JSONObject:
        """Serialize the task identity."""
        return {
            "benchmark": self.benchmark,
            "task": self.task,
            "task_spec_digest": self.task_spec_digest,
            "habitat_config_digest": self.habitat_config_digest,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkAuthorityIdentity:
    """Declared benchmark success authority (measure, code, parameters).

    ``parameters`` carries evaluator-only facts (for example
    ``must_call_stop`` or the robot-at threshold). These are fairness
    metadata for the evaluation plane; they are never semantic-ingress
    material for Mission Intelligence.
    """

    measure: str
    implementation_digest: str
    parameters: Mapping[str, JSONValue] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: JSONValue) -> BenchmarkAuthorityIdentity:
        """Restore one benchmark authority identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "benchmark_authority")
        required = {"measure", "implementation_digest", "parameters"}
        if set(item) != required:
            raise FairnessError(
                "invalid_benchmark_authority",
                f"benchmark_authority must have exactly {sorted(required)}",
            )
        parameters = _require_object(item["parameters"], "benchmark_authority.parameters")
        return cls(
            measure=_require_text(item["measure"], "benchmark_authority.measure"),
            implementation_digest=_require_digest_string(
                item["implementation_digest"], "benchmark_authority.implementation_digest"
            ),
            parameters=parameters,
        )

    def to_json(self) -> JSONObject:
        """Serialize the benchmark authority identity."""
        return {
            "measure": self.measure,
            "implementation_digest": self.implementation_digest,
            "parameters": dict(sorted(self.parameters.items())),
        }


@dataclass(frozen=True, slots=True)
class EmbodimentAgent:
    """One declared embodied agent (simulator index and handle)."""

    index: int
    handle: str
    robot_type: str | None = None

    @classmethod
    def from_json(cls, value: JSONValue) -> EmbodimentAgent:
        """Restore one embodied agent from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "embodiment agent")
        known = {"index", "handle", "robot_type"}
        if not {"index", "handle"} <= set(item) or not set(item) <= known:
            raise FairnessError(
                "invalid_embodiment_agent",
                "embodiment agent must have index/handle and optional robot_type",
            )
        index = item["index"]
        if not isinstance(index, int) or isinstance(index, bool):
            raise FairnessError("invalid_embodiment_agent", "agent index must be an integer")
        robot_type = item.get("robot_type")
        return cls(
            index=index,
            handle=_require_text(item["handle"], "agent handle"),
            robot_type=_require_optional_text(robot_type, "agent robot_type"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the embodied agent."""
        item: JSONObject = {"index": self.index, "handle": self.handle}
        if self.robot_type is not None:
            item["robot_type"] = self.robot_type
        return item


@dataclass(frozen=True, slots=True)
class EmbodimentProfile:
    """Declared multi-agent embodiment profile."""

    agents: tuple[EmbodimentAgent, ...]

    @classmethod
    def from_json(cls, value: JSONValue) -> EmbodimentProfile:
        """Restore one embodiment profile from JSON.

        Raises:
            FairnessError: On missing or malformed fields, or duplicate
                agent indexes.
        """
        item = _require_object(value, "embodiment_profile")
        if set(item) != {"agents"} or not isinstance(item["agents"], list):
            raise FairnessError(
                "invalid_embodiment_profile", "embodiment_profile must have agents[]"
            )
        agents = tuple(EmbodimentAgent.from_json(agent) for agent in item["agents"])
        indexes = [agent.index for agent in agents]
        if len(set(indexes)) != len(indexes):
            raise FairnessError("invalid_embodiment_profile", "agent indexes must be unique")
        return cls(agents=agents)

    def to_json(self) -> JSONObject:
        """Serialize the embodiment profile."""
        return {"agents": [agent.to_json() for agent in self.agents]}


@dataclass(frozen=True, slots=True)
class Stage2Identity:
    """Declared held-constant Stage2/local-execution stack identity.

    ``checkout_commit`` records the whole external checkout;
    ``file_digests`` pins the precise held-constant file surface (see the
    ``stage2_surface.json`` design fixture). The file digest set is the
    authoritative Stage2 equality surface; the checkout commit is
    reproducibility context.
    """

    checkout_commit: str
    file_digests: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: JSONValue) -> Stage2Identity:
        """Restore one Stage2 identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "stage2_identity")
        known = {"checkout_commit", "file_digests"}
        if "checkout_commit" not in item or not set(item) <= known:
            raise FairnessError(
                "invalid_stage2_identity",
                "stage2_identity must have checkout_commit and optional file_digests",
            )
        digests = _require_object(item.get("file_digests", {}), "stage2_identity.file_digests")
        validated_digests: dict[str, str] = {}
        for path, path_digest in digests.items():
            validated_digests[_require_text(path, "stage2 file path")] = _require_digest_string(
                path_digest, f"stage2 file digest for {path}"
            )
        return cls(
            checkout_commit=_require_text(
                item["checkout_commit"], "stage2_identity.checkout_commit"
            ),
            file_digests=validated_digests,
        )

    def to_json(self) -> JSONObject:
        """Serialize the Stage2 identity."""
        return {
            "checkout_commit": self.checkout_commit,
            "file_digests": dict(sorted(self.file_digests.items())),
        }


@dataclass(frozen=True, slots=True)
class SimulatorIdentity:
    """Declared simulator/runtime identity (reproducibility metadata).

    Drift here is diagnosed through reproducibility warnings; it does not by
    itself invalidate a pair, because both arms on one machine structurally
    share the simulator checkout.
    """

    habitat_lab_commit: str
    conda_environment: str
    extra: Mapping[str, JSONValue] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: JSONValue) -> SimulatorIdentity:
        """Restore one simulator identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "simulator_identity")
        known = {"habitat_lab_commit", "conda_environment", "extra"}
        if not {"habitat_lab_commit", "conda_environment"} <= set(item) or not set(item) <= known:
            raise FairnessError(
                "invalid_simulator_identity",
                "simulator_identity must have habitat_lab_commit/conda_environment "
                "and optional extra",
            )
        return cls(
            habitat_lab_commit=_require_text(
                item["habitat_lab_commit"], "simulator_identity.habitat_lab_commit"
            ),
            conda_environment=_require_text(
                item["conda_environment"], "simulator_identity.conda_environment"
            ),
            extra=_require_object(item.get("extra", {}), "simulator_identity.extra"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the simulator identity."""
        item: JSONObject = {
            "habitat_lab_commit": self.habitat_lab_commit,
            "conda_environment": self.conda_environment,
        }
        if self.extra:
            item["extra"] = dict(sorted(self.extra.items()))
        return item


@dataclass(frozen=True, slots=True)
class ModelConfigurationIdentity:
    """Declared model configuration shared by both arms."""

    provider: str
    model: str
    reasoning_effort: str | None = None
    reasoning_options: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: JSONValue) -> ModelConfigurationIdentity:
        """Restore one model configuration identity from JSON.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "model_configuration")
        known = {"provider", "model", "reasoning_effort", "reasoning_options"}
        if not {"provider", "model"} <= set(item) or not set(item) <= known:
            raise FairnessError(
                "invalid_model_configuration",
                "model_configuration must have provider/model and optional reasoning fields",
            )
        reasoning_options = _require_object(
            item.get("reasoning_options", {}), "model_configuration.reasoning_options"
        )
        return cls(
            provider=_require_text(item["provider"], "model_configuration.provider"),
            model=_require_text(item["model"], "model_configuration.model"),
            reasoning_effort=_require_optional_text(
                item.get("reasoning_effort"), "model_configuration.reasoning_effort"
            ),
            reasoning_options={
                _require_text(key, "reasoning option name"): _require_text(
                    option_value, f"reasoning option {key}"
                )
                for key, option_value in reasoning_options.items()
            },
        )

    def to_json(self) -> JSONObject:
        """Serialize the model configuration identity."""
        item: JSONObject = {"provider": self.provider, "model": self.model}
        if self.reasoning_effort is not None:
            item["reasoning_effort"] = self.reasoning_effort
        if self.reasoning_options:
            item["reasoning_options"] = dict(sorted(self.reasoning_options.items()))
        return item


@dataclass(frozen=True, slots=True)
class PopulationRow:
    """One explicit workload row: the pair identity plus expectations.

    ``seed`` is the designated seed for this pair. ``expected_episode_id``
    and ``expected_scene_id`` are frozen expectations used to detect
    selection drift; they may be ``None`` when a row legitimately cannot
    declare them. ``derived_metadata`` (for example ``same_floor`` or
    geodesic distance) is reproducibility metadata only and never a
    workload identity.
    """

    pair_id: str
    seed: int | None = None
    expected_episode_id: str | None = None
    expected_scene_id: str | None = None
    derived_metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    @classmethod
    def from_json(cls, value: JSONValue) -> PopulationRow:
        """Restore one population row from JSON.

        Raises:
            FairnessError: On unknown fields or malformed values.
        """
        item = _require_object(value, "population row")
        known = {"pair_id", "seed", "expected_episode_id", "expected_scene_id", "derived_metadata"}
        if "pair_id" not in item or not set(item) <= known:
            raise FairnessError(
                "invalid_population_row", f"row fields must be within {sorted(known)}"
            )
        return cls(
            pair_id=_require_text(item["pair_id"], "row.pair_id"),
            seed=_require_optional_int(item.get("seed"), "row.seed"),
            expected_episode_id=_require_optional_text(
                item.get("expected_episode_id"), "row.expected_episode_id"
            ),
            expected_scene_id=_require_optional_text(
                item.get("expected_scene_id"), "row.expected_scene_id"
            ),
            derived_metadata=_require_object(
                item.get("derived_metadata", {}), "row.derived_metadata"
            ),
        )

    def to_json(self) -> JSONObject:
        """Serialize the population row, omitting unset expectations."""
        item: JSONObject = {"pair_id": self.pair_id}
        if self.seed is not None:
            item["seed"] = self.seed
        if self.expected_episode_id is not None:
            item["expected_episode_id"] = self.expected_episode_id
        if self.expected_scene_id is not None:
            item["expected_scene_id"] = self.expected_scene_id
        if self.derived_metadata:
            item["derived_metadata"] = dict(sorted(self.derived_metadata.items()))
        return item


@dataclass(frozen=True, slots=True)
class PopulationSelector:
    """Population selector; ``explicit_set`` is the v0.1 policy.

    Future policies (``all``, ``predicate``) extend this dataclass without
    changing the population abstraction; rows remain the workload source of
    truth for explicit sets.
    """

    policy: Literal["explicit_set"]
    rows: tuple[PopulationRow, ...]

    @classmethod
    def from_json(cls, value: JSONValue) -> PopulationSelector:
        """Restore one selector from JSON.

        Raises:
            FairnessError: On unsupported policies, empty rows, or duplicate
                pair ids.
        """
        item = _require_object(value, "selector")
        if set(item) != {"policy", "rows"}:
            raise FairnessError("invalid_selector", "selector must have exactly policy/rows")
        policy = _require_text(item["policy"], "selector.policy")
        if policy != "explicit_set":
            raise FairnessError(
                "schema_version_unsupported",
                f"unsupported selector policy {policy!r}; v0.1 supports explicit_set",
            )
        if not isinstance(item["rows"], list) or not item["rows"]:
            raise FairnessError("invalid_selector", "explicit_set rows must be a nonempty list")
        rows = tuple(PopulationRow.from_json(row) for row in item["rows"])
        pair_ids = [row.pair_id for row in rows]
        if len(set(pair_ids)) != len(pair_ids):
            raise FairnessError("invalid_selector", "row pair_id values must be unique")
        return cls(policy="explicit_set", rows=rows)

    def row(self, pair_id: str) -> PopulationRow | None:
        """Return the row for one pair id, or ``None`` when unmatched."""
        return next((candidate for candidate in self.rows if candidate.pair_id == pair_id), None)

    def to_json(self) -> JSONObject:
        """Serialize the selector."""
        return {"policy": self.policy, "rows": [row.to_json() for row in self.rows]}


@dataclass(frozen=True, slots=True)
class PopulationManifest:
    """The frozen experiment-level definition of one E1 population.

    The digest binds every declared identity, the held-constant surfaces,
    and the selector rows; any semantic change yields a new digest and
    therefore a different population (cross-population pairing is rejected
    downstream).
    """

    population_id: str
    protocol: str
    dataset: DatasetIdentity
    task: TaskIdentity
    benchmark_authority: BenchmarkAuthorityIdentity
    embodiment_profile: EmbodimentProfile
    stage2_identity: Stage2Identity
    simulator_identity: SimulatorIdentity
    model_configuration: ModelConfigurationIdentity
    allowed_differences: tuple[str, ...]
    required_differences: tuple[str, ...]
    selector: PopulationSelector
    digest: str = ""

    @classmethod
    def create(
        cls,
        *,
        population_id: str,
        protocol: str,
        dataset: DatasetIdentity,
        task: TaskIdentity,
        benchmark_authority: BenchmarkAuthorityIdentity,
        embodiment_profile: EmbodimentProfile,
        stage2_identity: Stage2Identity,
        simulator_identity: SimulatorIdentity,
        model_configuration: ModelConfigurationIdentity,
        allowed_differences: tuple[str, ...] = (),
        required_differences: tuple[str, ...] = (),
        selector: PopulationSelector,
    ) -> PopulationManifest:
        """Create one manifest and bind its digest to the full declaration."""
        manifest = cls(
            population_id=population_id,
            protocol=protocol,
            dataset=dataset,
            task=task,
            benchmark_authority=benchmark_authority,
            embodiment_profile=embodiment_profile,
            stage2_identity=stage2_identity,
            simulator_identity=simulator_identity,
            model_configuration=model_configuration,
            allowed_differences=allowed_differences,
            required_differences=required_differences,
            selector=selector,
        )
        object.__setattr__(manifest, "digest", digest(manifest._body()))
        return manifest

    def _body(self) -> JSONObject:
        """Return the digest body (everything except the digest itself)."""
        return {
            "schema_version": POPULATION_MANIFEST_SCHEMA,
            "population_id": self.population_id,
            "protocol": self.protocol,
            "dataset": self.dataset.to_json(),
            "task": self.task.to_json(),
            "benchmark_authority": self.benchmark_authority.to_json(),
            "embodiment_profile": self.embodiment_profile.to_json(),
            "stage2_identity": self.stage2_identity.to_json(),
            "simulator_identity": self.simulator_identity.to_json(),
            "model_configuration": self.model_configuration.to_json(),
            "allowed_differences": cast(JSONValue, sorted(self.allowed_differences)),
            "required_differences": cast(JSONValue, sorted(self.required_differences)),
            "selector": self.selector.to_json(),
        }

    def __post_init__(self) -> None:
        """Validate identity text fields and the declared difference surfaces.

        Raises:
            FairnessError: On blank identities or a dimension declared both
                allowed and required to differ.
        """
        _require_text(self.population_id, "population_id")
        _require_text(self.protocol, "protocol")
        for difference in self.allowed_differences + self.required_differences:
            _require_text(difference, "declared difference name")
        if set(self.allowed_differences) & set(self.required_differences):
            raise FairnessError(
                "invalid_population_manifest",
                "a dimension cannot be both allowed and required to differ",
            )

    def to_json(self) -> JSONObject:
        """Serialize the manifest with its content digest."""
        return {**self._body(), "digest": self.digest}

    @classmethod
    def from_json(cls, value: JSONValue) -> PopulationManifest:
        """Restore one manifest and revalidate its digest integrity.

        Raises:
            FairnessError: On unsupported schema version, malformed fields,
                field-set mismatch, or a digest that does not match the
                content (tamper).
        """
        item = _require_object(value, "population manifest")
        if item.get("schema_version") != POPULATION_MANIFEST_SCHEMA:
            raise FairnessError(
                "schema_version_unsupported",
                f"unsupported population manifest schema {item.get('schema_version')!r}",
            )
        expected_fields = {
            "schema_version",
            "population_id",
            "protocol",
            "dataset",
            "task",
            "benchmark_authority",
            "embodiment_profile",
            "stage2_identity",
            "simulator_identity",
            "model_configuration",
            "allowed_differences",
            "required_differences",
            "selector",
            "digest",
        }
        _require_exact_fields(item, expected_fields, "population manifest")
        manifest = cls(
            population_id=_require_text(item["population_id"], "population_id"),
            protocol=_require_text(item["protocol"], "protocol"),
            dataset=DatasetIdentity.from_json(item["dataset"]),
            task=TaskIdentity.from_json(item["task"]),
            benchmark_authority=BenchmarkAuthorityIdentity.from_json(item["benchmark_authority"]),
            embodiment_profile=EmbodimentProfile.from_json(item["embodiment_profile"]),
            stage2_identity=Stage2Identity.from_json(item["stage2_identity"]),
            simulator_identity=SimulatorIdentity.from_json(item["simulator_identity"]),
            model_configuration=ModelConfigurationIdentity.from_json(item["model_configuration"]),
            allowed_differences=tuple(
                _require_text(difference, "allowed difference")
                for difference in _require_list(item["allowed_differences"], "allowed_differences")
            ),
            required_differences=tuple(
                _require_text(difference, "required difference")
                for difference in _require_list(
                    item["required_differences"], "required_differences"
                )
            ),
            selector=PopulationSelector.from_json(item["selector"]),
            digest=_require_digest_string(item["digest"], "digest"),
        )
        if manifest.digest != digest(manifest._body()):
            raise FairnessError(
                "digest_mismatch", "population manifest digest does not match its content"
            )
        return manifest

    def declared_value(self, dimension_id: str) -> JSONValue | None:
        """Return the declared identity for one dimension, or ``None``.

        The declared value is expressed in the shape the run evidence
        observes: the dataset dimension declares the file digest (the only
        runtime-observable fact), the identity dimensions declare their
        digest or structured identity, and episode/scene identities are
        declared per selector row. The population manifest's own digest is
        the declared value of the population dimension.
        """
        declared: dict[str, JSONValue] = {
            "dataset_identity": self.dataset.file_sha256,
            "task_spec_identity": {
                "benchmark": self.task.benchmark,
                "task": self.task.task,
                "task_spec_digest": self.task.task_spec_digest,
            },
            "habitat_config_identity": {"habitat_config_digest": self.task.habitat_config_digest},
            "benchmark_authority_identity": self.benchmark_authority.to_json(),
            "embodiment_profile": self.embodiment_profile.to_json(),
            "stage2_identity": self.stage2_identity.to_json(),
            "simulator_identity": self.simulator_identity.to_json(),
            "model_configuration_identity": self.model_configuration.to_json(),
            "population_manifest_identity": self.digest,
        }
        return declared.get(dimension_id)


def _require_exact_fields(
    item: JSONObject, expected: frozenset[str] | set[str], label: str
) -> None:
    """Require one decoded object to carry exactly the expected field set.

    Raises:
        FairnessError: On any missing or extra field.
    """
    missing = expected - set(item)
    extra = set(item) - expected
    if missing or extra:
        raise FairnessError(
            "invalid_field_set",
            f"{label} field mismatch; missing={sorted(missing)} extra={sorted(extra)}",
        )


@dataclass(frozen=True, slots=True)
class RequestedIntent:
    """One run's declared intent (recorded; never compared as evidence)."""

    seed: int | None = None
    episode_id: str | None = None

    @classmethod
    def from_json(cls, value: JSONValue) -> RequestedIntent:
        """Restore one requested-intent block from JSON.

        Raises:
            FairnessError: On unknown fields or malformed values.
        """
        item = _require_object(value, "requested")
        if not set(item) <= {"seed", "episode_id"}:
            raise FairnessError("invalid_requested", "requested must have optional seed/episode_id")
        return cls(
            seed=_require_optional_int(item.get("seed"), "requested.seed"),
            episode_id=_require_optional_text(item.get("episode_id"), "requested.episode_id"),
        )

    def to_json(self) -> JSONObject:
        """Serialize the requested intent."""
        return {"seed": self.seed, "episode_id": self.episode_id}


@dataclass(frozen=True, slots=True)
class RunPairingEvidence:
    """One EMOS or RoboGuide run's pairing evidence.

    ``observed`` maps dimension ids to runtime-observed facts. Unknown
    dimension ids are tolerated at parse time so the pair validator can fail
    the pair closed with ``unlisted_required_difference`` instead of the
    loader silently reinterpreting them. ``additional_observations`` carries
    free-form recorded facts that are diffed into the unlisted-differences
    report but never gate. ``system_outcome`` is recorded verbatim and never
    affects comparability.
    """

    arm: ArmName
    run_id: str
    pair_id: str
    population_manifest_digest: str
    requested: RequestedIntent
    observed: Mapping[str, ObservedField]
    additional_observations: Mapping[str, ObservedField] = field(default_factory=dict)
    system_outcome: ObservedField | None = None
    environment_fingerprint: Mapping[str, JSONValue] = field(default_factory=dict)
    digest: str = ""

    @classmethod
    def create(
        cls,
        *,
        arm: ArmName,
        run_id: str,
        pair_id: str,
        population_manifest_digest: str,
        requested: RequestedIntent,
        observed: Mapping[str, ObservedField],
        additional_observations: Mapping[str, ObservedField] | None = None,
        system_outcome: ObservedField | None = None,
        environment_fingerprint: Mapping[str, JSONValue] | None = None,
    ) -> RunPairingEvidence:
        """Create one evidence object and bind its digest to the full content."""
        evidence = cls(
            arm=arm,
            run_id=run_id,
            pair_id=pair_id,
            population_manifest_digest=population_manifest_digest,
            requested=requested,
            observed=dict(observed),
            additional_observations=dict(additional_observations or {}),
            system_outcome=system_outcome,
            environment_fingerprint=dict(environment_fingerprint or {}),
        )
        object.__setattr__(evidence, "digest", digest(evidence._body()))
        return evidence

    def _body(self) -> JSONObject:
        """Return the digest body (everything except the digest itself)."""
        return {
            "schema_version": RUN_PAIRING_EVIDENCE_SCHEMA,
            "arm": self.arm,
            "run_id": self.run_id,
            "pair_id": self.pair_id,
            "population_manifest_digest": self.population_manifest_digest,
            "requested": self.requested.to_json(),
            "observed": {
                dimension_id: observed.to_json()
                for dimension_id, observed in sorted(self.observed.items())
            },
            "additional_observations": {
                field_name: observed.to_json()
                for field_name, observed in sorted(self.additional_observations.items())
            },
            "system_outcome": self.system_outcome.to_json() if self.system_outcome else None,
            "environment_fingerprint": dict(sorted(self.environment_fingerprint.items())),
        }

    def __post_init__(self) -> None:
        """Validate identity fields, the arm name, and observed-field keys.

        Raises:
            FairnessError: On invalid arm names, blank identities, malformed
                digests, or non-string field names.
        """
        if self.arm not in ("emos", "roboguide"):
            raise FairnessError(
                "invalid_arm", f"arm must be 'emos' or 'roboguide', got {self.arm!r}"
            )
        _require_text(self.run_id, "run_id")
        _require_text(self.pair_id, "pair_id")
        _require_digest_string(self.population_manifest_digest, "population_manifest_digest")
        for dimension_id in self.observed:
            _require_text(dimension_id, "observed dimension id")
        for field_name in self.additional_observations:
            _require_text(field_name, "additional observation name")

    def to_json(self) -> JSONObject:
        """Serialize the evidence with its content digest."""
        return {**self._body(), "digest": self.digest}

    @classmethod
    def from_json(cls, value: JSONValue) -> RunPairingEvidence:
        """Restore one evidence object and revalidate its digest integrity.

        Raises:
            FairnessError: On unsupported schema version, malformed fields,
                field-set mismatch, or a digest that does not match the
                content (tamper).
        """
        item = _require_object(value, "run pairing evidence")
        if item.get("schema_version") != RUN_PAIRING_EVIDENCE_SCHEMA:
            raise FairnessError(
                "schema_version_unsupported",
                f"unsupported run pairing evidence schema {item.get('schema_version')!r}",
            )
        _require_exact_fields(item, _EVIDENCE_FIELDS, "run pairing evidence")
        arm = item["arm"]
        if arm not in _ARMS:
            raise FairnessError("invalid_arm", f"arm must be 'emos' or 'roboguide', got {arm!r}")
        system_outcome_value = item["system_outcome"]
        evidence = cls(
            arm=arm,
            run_id=_require_text(item["run_id"], "run_id"),
            pair_id=_require_text(item["pair_id"], "pair_id"),
            population_manifest_digest=_require_digest_string(
                item["population_manifest_digest"], "population_manifest_digest"
            ),
            requested=RequestedIntent.from_json(item["requested"]),
            observed={
                _require_text(dimension_id, "observed dimension id"): ObservedField.from_json(
                    observed
                )
                for dimension_id, observed in _require_object(item["observed"], "observed").items()
            },
            additional_observations={
                _require_text(field_name, "additional observation name"): ObservedField.from_json(
                    observed
                )
                for field_name, observed in _require_object(
                    item["additional_observations"], "additional_observations"
                ).items()
            },
            system_outcome=(
                ObservedField.from_json(system_outcome_value)
                if system_outcome_value is not None
                else None
            ),
            environment_fingerprint=_require_object(
                item["environment_fingerprint"], "environment_fingerprint"
            ),
            digest=_require_digest_string(item["digest"], "digest"),
        )
        if evidence.digest != digest(evidence._body()):
            raise FairnessError(
                "digest_mismatch", "run pairing evidence digest does not match content"
            )
        return evidence


_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "arm",
        "run_id",
        "pair_id",
        "population_manifest_digest",
        "requested",
        "observed",
        "additional_observations",
        "system_outcome",
        "environment_fingerprint",
        "digest",
    }
)


@dataclass(frozen=True, slots=True)
class DimensionComparison:
    """One dimension's recorded comparison inside a pair manifest.

    Attributes:
        dimension_id: The compared dimension.
        dimension_class: The dimension's fairness classification.
        population_declared: The population's declared identity (or the
            row expectation for episode/scene), or ``None`` when undeclared.
        emos: The EMOS arm's observed field, or ``None`` when absent.
        roboguide: The RoboGuide arm's observed field.
        comparison: ``equal`` / ``mismatch`` / ``unavailable`` / ``invalid``.
        reasons: Machine-stable reasons contributed by this dimension.
        gating: Whether this dimension can fail pair comparability.
    """

    dimension_id: str
    dimension_class: DimensionClass
    population_declared: JSONValue
    emos: ObservedField | None
    roboguide: ObservedField | None
    comparison: ComparisonState
    reasons: tuple[FairnessReason, ...] = ()
    gating: bool = True

    def to_json(self) -> JSONObject:
        """Serialize the dimension comparison."""
        return {
            "dimension_id": self.dimension_id,
            "dimension_class": self.dimension_class.value,
            "population_declared": self.population_declared,
            "emos": self.emos.to_json() if self.emos else None,
            "roboguide": self.roboguide.to_json() if self.roboguide else None,
            "comparison": self.comparison,
            "reasons": cast(JSONValue, sorted(reason.value for reason in self.reasons)),
            "gating": self.gating,
        }

    @classmethod
    def from_json(cls, value: JSONValue) -> DimensionComparison:
        """Restore one dimension comparison from a pair manifest body.

        Raises:
            FairnessError: On missing fields, unknown dimensions, or
                malformed nested values.
        """
        item = _require_object(value, "dimension comparison")
        _require_exact_fields(
            item,
            {
                "dimension_id",
                "dimension_class",
                "population_declared",
                "emos",
                "roboguide",
                "comparison",
                "reasons",
                "gating",
            },
            "dimension comparison",
        )
        dimension_id = _require_text(item["dimension_id"], "dimension_id")
        spec = DIMENSION_SPECS.get(dimension_id)
        if spec is None:
            raise FairnessError(
                "invalid_dimension_comparison", f"unknown dimension {dimension_id!r}"
            )
        comparison = _require_text(item["comparison"], "comparison")
        if comparison not in ("equal", "mismatch", "unavailable", "invalid"):
            raise FairnessError(
                "invalid_dimension_comparison", f"unknown comparison state {comparison!r}"
            )
        emos_value = item["emos"]
        roboguide_value = item["roboguide"]
        return cls(
            dimension_id=dimension_id,
            dimension_class=spec.dimension_class,
            population_declared=item["population_declared"],
            emos=ObservedField.from_json(emos_value) if emos_value is not None else None,
            roboguide=(
                ObservedField.from_json(roboguide_value) if roboguide_value is not None else None
            ),
            comparison=cast(ComparisonState, comparison),
            reasons=tuple(
                FairnessReason(_require_text(reason, "dimension reason"))
                for reason in _require_list(item["reasons"], "dimension reasons")
            ),
            gating=_require_bool(item["gating"], "gating"),
        )


@dataclass(frozen=True, slots=True)
class ReproducibilityWarning:
    """One non-gating drift or availability warning."""

    reason: FairnessReason
    detail: str

    def to_json(self) -> JSONObject:
        """Serialize the warning."""
        return {"reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ArmPairingRecord:
    """One arm's recorded identity inside a pair manifest.

    ``system_outcome`` is recorded verbatim; it never influenced the verdict.
    """

    arm: ArmName
    run_id: str
    evidence_digest: str
    requested: RequestedIntent
    system_outcome: ObservedField | None

    def to_json(self) -> JSONObject:
        """Serialize the arm record."""
        return {
            "run_id": self.run_id,
            "evidence_digest": self.evidence_digest,
            "requested": self.requested.to_json(),
            "system_outcome": self.system_outcome.to_json() if self.system_outcome else None,
        }

    @classmethod
    def from_json(cls, value: JSONValue, arm: ArmName) -> ArmPairingRecord:
        """Restore one arm record from a pair manifest body.

        Raises:
            FairnessError: On missing or malformed fields.
        """
        item = _require_object(value, "arm record")
        _require_exact_fields(
            item,
            {"run_id", "evidence_digest", "requested", "system_outcome"},
            "arm record",
        )
        system_outcome_value = item["system_outcome"]
        return cls(
            arm=arm,
            run_id=_require_text(item["run_id"], "arm run_id"),
            evidence_digest=_require_digest_string(item["evidence_digest"], "arm evidence_digest"),
            requested=RequestedIntent.from_json(item["requested"]),
            system_outcome=(
                ObservedField.from_json(system_outcome_value)
                if system_outcome_value is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PairManifest:
    """The comparability verdict for one candidate pair.

    ``comparability`` is the only fairness decision; system outcomes are
    recorded verbatim and never influence it. Formal population admission
    remains a separate authority and is not consulted here.
    """

    pair_id: str
    population_manifest_digest: str
    population_row: PopulationRow
    arms: Mapping[ArmName, ArmPairingRecord]
    dimension_comparisons: tuple[DimensionComparison, ...]
    comparability: PairComparability
    reasons: tuple[FairnessReason, ...]
    reproducibility_warnings: tuple[ReproducibilityWarning, ...]
    unlisted_differences: tuple[JSONObject, ...]
    unlisted_observed_dimensions: tuple[str, ...] = ()
    digest: str = ""

    @classmethod
    def create(
        cls,
        *,
        pair_id: str,
        population_manifest_digest: str,
        population_row: PopulationRow,
        arms: Mapping[ArmName, ArmPairingRecord],
        dimension_comparisons: tuple[DimensionComparison, ...],
        comparability: PairComparability,
        reasons: tuple[FairnessReason, ...],
        reproducibility_warnings: tuple[ReproducibilityWarning, ...],
        unlisted_differences: tuple[JSONObject, ...],
        unlisted_observed_dimensions: tuple[str, ...] = (),
    ) -> PairManifest:
        """Create one pair manifest and bind its digest to the full verdict."""
        pair = cls(
            pair_id=pair_id,
            population_manifest_digest=population_manifest_digest,
            population_row=population_row,
            arms=dict(arms),
            dimension_comparisons=dimension_comparisons,
            comparability=comparability,
            reasons=reasons,
            reproducibility_warnings=reproducibility_warnings,
            unlisted_differences=unlisted_differences,
            unlisted_observed_dimensions=unlisted_observed_dimensions,
        )
        object.__setattr__(pair, "digest", digest(pair._body()))
        return pair

    def _body(self) -> JSONObject:
        """Return the digest body (everything except the digest itself)."""
        return {
            "schema_version": PAIR_MANIFEST_SCHEMA,
            "pair_id": self.pair_id,
            "population_manifest_digest": self.population_manifest_digest,
            "population_row": self.population_row.to_json(),
            "arms": {arm: record.to_json() for arm, record in sorted(self.arms.items())},
            "dimension_comparisons": [item.to_json() for item in self.dimension_comparisons],
            "comparability": self.comparability.value,
            "reasons": cast(JSONValue, sorted(reason.value for reason in self.reasons)),
            "reproducibility_warnings": [
                warning.to_json() for warning in self.reproducibility_warnings
            ],
            "unlisted_differences": cast(JSONValue, list(self.unlisted_differences)),
            "unlisted_observed_dimensions": cast(
                JSONValue, sorted(self.unlisted_observed_dimensions)
            ),
        }

    def to_json(self) -> JSONObject:
        """Serialize the pair manifest with its content digest."""
        return {**self._body(), "digest": self.digest}

    @classmethod
    def from_json(cls, value: JSONValue) -> PairManifest:
        """Restore one pair manifest and revalidate its digest integrity.

        Raises:
            FairnessError: On unsupported schema version, malformed fields,
                field-set mismatch, or a digest that does not match the
                content (tamper).
        """
        item = _require_object(value, "pair manifest")
        if item.get("schema_version") != PAIR_MANIFEST_SCHEMA:
            raise FairnessError(
                "schema_version_unsupported",
                f"unsupported pair manifest schema {item.get('schema_version')!r}",
            )
        _require_exact_fields(item, _PAIR_MANIFEST_FIELDS, "pair manifest")
        arms_items = _require_object(item["arms"], "arms")
        arms: dict[ArmName, ArmPairingRecord] = {}
        for arm_name, record_value in arms_items.items():
            if arm_name not in _ARMS:
                raise FairnessError("invalid_arm", "pair manifest arm must be emos/roboguide")
            arms[arm_name] = ArmPairingRecord.from_json(record_value, arm_name)
        pair = cls(
            pair_id=_require_text(item["pair_id"], "pair_id"),
            population_manifest_digest=_require_digest_string(
                item["population_manifest_digest"], "population_manifest_digest"
            ),
            population_row=PopulationRow.from_json(item["population_row"]),
            arms=arms,
            dimension_comparisons=tuple(
                DimensionComparison.from_json(entry)
                for entry in _require_list(item["dimension_comparisons"], "dimension_comparisons")
            ),
            comparability=PairComparability(_require_text(item["comparability"], "comparability")),
            reasons=tuple(
                FairnessReason(_require_text(reason, "reason"))
                for reason in _require_list(item["reasons"], "reasons")
            ),
            reproducibility_warnings=tuple(
                ReproducibilityWarning(
                    reason=FairnessReason(_require_text(warning_item["reason"], "warning reason")),
                    detail=_require_text(warning_item["detail"], "warning detail"),
                )
                for warning_item in (
                    _require_object(warning, "reproducibility warning")
                    for warning in _require_list(
                        item["reproducibility_warnings"], "reproducibility_warnings"
                    )
                )
            ),
            unlisted_differences=tuple(
                _require_object(difference, "unlisted difference")
                for difference in _require_list(
                    item["unlisted_differences"], "unlisted_differences"
                )
            ),
            unlisted_observed_dimensions=tuple(
                _require_text(dimension, "unlisted observed dimension")
                for dimension in _require_list(
                    item["unlisted_observed_dimensions"], "unlisted_observed_dimensions"
                )
            ),
            digest=_require_digest_string(item["digest"], "digest"),
        )
        if pair.digest != digest(pair._body()):
            raise FairnessError("digest_mismatch", "pair manifest digest does not match content")
        return pair


_PAIR_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "pair_id",
        "population_manifest_digest",
        "population_row",
        "arms",
        "dimension_comparisons",
        "comparability",
        "reasons",
        "reproducibility_warnings",
        "unlisted_differences",
        "unlisted_observed_dimensions",
        "digest",
    }
)


def _normalize_numbers(value: JSONValue) -> JSONValue:
    """Normalize JSON numbers so int and float forms compare equally.

    ``2`` and ``2.0`` are the same JSON number; producers legitimately emit
    either form (for example a threshold read from YAML). Booleans are left
    untouched because ``bool`` subclasses ``int``.
    """
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [_normalize_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_numbers(item) for key, item in value.items()}
    return value


def _value_key(value: JSONValue) -> str:
    """Return the canonical comparison key of one observed value.

    Number forms are normalized (``2`` equals ``2.0``); every other value
    compares by its canonical digest.
    """
    return digest(_normalize_numbers(value))


def _compare_observed_pair(
    spec: DimensionSpec,
    emos_field: ObservedField | None,
    roboguide_field: ObservedField | None,
) -> tuple[ComparisonState, list[FairnessReason], list[str]]:
    """Compare one dimension's two observed fields.

    Missing fields are ``OBSERVED_EVIDENCE_MISSING``; malformed evidence is
    ``OBSERVED_EVIDENCE_INVALID``; explicit unavailability maps to the
    dimension's own unavailable reason; two available values compare by
    canonical digest equality.

    Returns:
        The comparison state, contributed reasons, and detail strings.
    """
    presence = (("emos", emos_field), ("roboguide", roboguide_field))
    absent = [arm for arm, observed in presence if observed is None]
    if absent:
        return (
            "unavailable",
            [FairnessReason.OBSERVED_EVIDENCE_MISSING],
            [f"{spec.dimension_id}: no observed field on arms {sorted(absent)}"],
        )
    invalid = [
        arm
        for arm, observed in presence
        if observed is not None and observed.status is EvidenceStatus.INVALID
    ]
    if invalid:
        return (
            "invalid",
            [FairnessReason.OBSERVED_EVIDENCE_INVALID],
            [f"{spec.dimension_id}: malformed evidence on arms {sorted(invalid)}"],
        )
    unavailable = [
        arm
        for arm, observed in presence
        if observed is not None and observed.status is EvidenceStatus.UNAVAILABLE
    ]
    if unavailable:
        return (
            "unavailable",
            [spec.unavailable_reason],
            [f"{spec.dimension_id}: unavailable on arms {sorted(unavailable)}"],
        )
    assert emos_field is not None and roboguide_field is not None
    if _value_key(emos_field.value) != _value_key(roboguide_field.value):
        return (
            "mismatch",
            [spec.mismatch_reason],
            [f"{spec.dimension_id}: arms disagree"],
        )
    return "equal", [], []


def _declared_check(
    dimension_id: str,
    declared: JSONValue,
    arm_name: ArmName,
    observed: ObservedField,
) -> tuple[list[FairnessReason], list[str]]:
    """Compare one arm's observed value against the population declaration.

    A mismatch here means the arm drifted from the frozen population
    definition — including the case where both arms drifted identically,
    which a cross-arm-only comparison would miss. Absent or malformed
    evidence is handled by the pairwise comparison and is not re-reported.

    Returns:
        Reasons and detail strings.
    """
    if observed.status is not EvidenceStatus.AVAILABLE:
        return [], []
    if _value_key(observed.value) != _value_key(declared):
        return (
            [DIMENSION_SPECS[dimension_id].mismatch_reason],
            [f"{dimension_id}: arm {arm_name} disagrees with the population declaration"],
        )
    return [], []


def _unlisted_difference_entries(
    emos_evidence: RunPairingEvidence,
    roboguide_evidence: RunPairingEvidence,
) -> tuple[JSONObject, ...]:
    """Diff both arms' additional observations into unlisted-difference records.

    Only fields present on both arms with differing available values are
    listed; these are recorded for reviewer scrutiny and never gate.
    """
    entries: list[JSONObject] = []
    shared = sorted(
        set(emos_evidence.additional_observations) & set(roboguide_evidence.additional_observations)
    )
    for field_name in shared:
        emos_field = emos_evidence.additional_observations[field_name]
        roboguide_field = roboguide_evidence.additional_observations[field_name]
        if (
            emos_field.status is EvidenceStatus.AVAILABLE
            and roboguide_field.status is EvidenceStatus.AVAILABLE
            and _value_key(emos_field.value) != _value_key(roboguide_field.value)
        ):
            entries.append(
                {
                    "field": field_name,
                    "emos_value": emos_field.value,
                    "roboguide_value": roboguide_field.value,
                }
            )
    return tuple(entries)


def validate_pair(
    population: PopulationManifest,
    emos_evidence: RunPairingEvidence,
    roboguide_evidence: RunPairingEvidence,
) -> PairManifest:
    """Validate one candidate pair against the frozen population definition.

    The validator answers only "did both arms run the same workload?". It
    never inspects system success or benchmark results except to record
    them, and it never decides Formal population membership.

    Args:
        population: The frozen population manifest.
        emos_evidence: The EMOS arm's pairing evidence.
        roboguide_evidence: The RoboGuide arm's pairing evidence.

    Returns:
        The pair manifest with dimension-by-dimension comparisons, the
        comparability verdict, machine-stable reasons, reproducibility
        warnings, and recorded system outcomes.
    """
    gating_reasons: list[FairnessReason] = []
    warning_entries: list[ReproducibilityWarning] = []
    dimension_comparisons: list[DimensionComparison] = []

    # Pair identity coherence.
    if emos_evidence.pair_id != roboguide_evidence.pair_id:
        gating_reasons.append(FairnessReason.POPULATION_ROW_UNMATCHED)

    # Population identity: each arm must bind to this population; arms bound
    # to different populations are a cross-population pair.
    if (
        emos_evidence.population_manifest_digest != population.digest
        or roboguide_evidence.population_manifest_digest != population.digest
    ):
        gating_reasons.append(FairnessReason.POPULATION_IDENTITY_MISMATCH)
    if emos_evidence.population_manifest_digest != roboguide_evidence.population_manifest_digest:
        gating_reasons.append(FairnessReason.CROSS_POPULATION_PAIR)

    # Unknown observed dimensions are unmodelable requirements: fail closed
    # and record which dimensions were unmodeled.
    unlisted_dimensions: set[str] = set()
    for evidence in (emos_evidence, roboguide_evidence):
        unlisted_dimensions.update(set(evidence.observed) - set(DIMENSION_SPECS))
    if unlisted_dimensions:
        gating_reasons.append(FairnessReason.UNLISTED_REQUIRED_DIFFERENCE)

    # Population row resolution and requested-seed coherence.
    row = population.selector.row(emos_evidence.pair_id)
    if population.selector.policy == "explicit_set" and row is None:
        gating_reasons.append(FairnessReason.POPULATION_ROW_UNMATCHED)
    if row is not None and (row.expected_episode_id is None or row.expected_scene_id is None):
        # A row without frozen expectations weakens the pairing proof to
        # cross-arm agreement only; surface it, but do not gate.
        warning_entries.append(
            ReproducibilityWarning(
                reason=FairnessReason.POPULATION_ROW_EXPECTATION_MISSING,
                detail=f"population row {row.pair_id} lacks expected episode/scene",
            )
        )
    if row is not None and row.seed is not None:
        for evidence in (emos_evidence, roboguide_evidence):
            if evidence.requested.seed is not None and evidence.requested.seed != row.seed:
                gating_reasons.append(FairnessReason.POPULATION_ROW_UNMATCHED)

    for dimension_id in DIMENSION_ORDER:
        spec = DIMENSION_SPECS[dimension_id]
        emos_field = emos_evidence.observed.get(dimension_id)
        roboguide_field = roboguide_evidence.observed.get(dimension_id)
        declared = population.declared_value(dimension_id)
        if dimension_id == "episode_identity" and row is not None and row.expected_episode_id:
            declared = row.expected_episode_id
        if dimension_id == "scene_identity" and row is not None and row.expected_scene_id:
            declared = row.expected_scene_id

        state, reasons, _details = _compare_observed_pair(spec, emos_field, roboguide_field)
        dimension_reasons = list(reasons)

        # Declared-vs-observed: catch arms that drift together away from the
        # frozen definition (or from the row expectation).
        if declared is not None:
            for arm_name, observed in ((_ARMS[0], emos_field), (_ARMS[1], roboguide_field)):
                if observed is None:
                    continue
                declared_reasons, _declared_details = _declared_check(
                    dimension_id, declared, arm_name, observed
                )
                dimension_reasons.extend(declared_reasons)

        # The seed-equal-episode-different anti-pattern gets its dedicated
        # code on top of the generic mismatch.
        if (
            dimension_id == "episode_identity"
            and state == "mismatch"
            and emos_field is not None
            and roboguide_field is not None
            and emos_field.status is EvidenceStatus.AVAILABLE
            and roboguide_field.status is EvidenceStatus.AVAILABLE
        ):
            emos_seed = emos_evidence.requested.seed
            roboguide_seed = roboguide_evidence.requested.seed
            if emos_seed is not None and emos_seed == roboguide_seed:
                dimension_reasons.append(FairnessReason.SEED_EQUAL_EPISODE_DIFFERENT)

        dimension_reasons = sorted(set(dimension_reasons))
        gating = dimension_gates(dimension_id)
        if gating:
            gating_reasons.extend(dimension_reasons)
        elif dimension_reasons:
            warning_entries.append(
                ReproducibilityWarning(
                    reason=dimension_reasons[0],
                    detail=f"{dimension_id}: non-gating dimension flagged",
                )
            )
        dimension_comparisons.append(
            DimensionComparison(
                dimension_id=dimension_id,
                dimension_class=spec.dimension_class,
                population_declared=declared,
                emos=emos_field,
                roboguide=roboguide_field,
                comparison=state,
                reasons=tuple(dimension_reasons),
                gating=gating,
            )
        )

    # Environment fingerprints are reproducibility metadata: shared keys
    # with differing values become warnings, never exclusions.
    for fingerprint_key in sorted(
        set(emos_evidence.environment_fingerprint) & set(roboguide_evidence.environment_fingerprint)
    ):
        emos_value = emos_evidence.environment_fingerprint[fingerprint_key]
        roboguide_value = roboguide_evidence.environment_fingerprint[fingerprint_key]
        if _value_key(emos_value) != _value_key(roboguide_value):
            warning_entries.append(
                ReproducibilityWarning(
                    reason=FairnessReason.ENVIRONMENT_FINGERPRINT_DRIFT,
                    detail=f"environment_fingerprint.{fingerprint_key} differs across arms",
                )
            )

    comparability = (
        PairComparability.PAIR_NOT_COMPARABLE
        if gating_reasons
        else PairComparability.PAIR_COMPARABLE
    )
    arms: dict[ArmName, ArmPairingRecord] = {
        "emos": ArmPairingRecord(
            arm="emos",
            run_id=emos_evidence.run_id,
            evidence_digest=emos_evidence.digest,
            requested=emos_evidence.requested,
            system_outcome=emos_evidence.system_outcome,
        ),
        "roboguide": ArmPairingRecord(
            arm="roboguide",
            run_id=roboguide_evidence.run_id,
            evidence_digest=roboguide_evidence.digest,
            requested=roboguide_evidence.requested,
            system_outcome=roboguide_evidence.system_outcome,
        ),
    }
    return PairManifest.create(
        pair_id=emos_evidence.pair_id,
        population_manifest_digest=population.digest,
        population_row=row or PopulationRow(pair_id=emos_evidence.pair_id),
        arms=arms,
        dimension_comparisons=tuple(dimension_comparisons),
        comparability=comparability,
        reasons=tuple(sorted(set(gating_reasons))),
        reproducibility_warnings=tuple(warning_entries),
        unlisted_differences=_unlisted_difference_entries(emos_evidence, roboguide_evidence),
        unlisted_observed_dimensions=tuple(sorted(unlisted_dimensions)),
    )


def load_population_manifest(path: Path) -> PopulationManifest:
    """Load and validate one population manifest from a JSON file.

    Raises:
        FairnessError: On malformed or tampered files.
        OSError: Propagated from the underlying read when the file cannot
            be opened.
        json.JSONDecodeError: When the file is not valid JSON.
    """
    return PopulationManifest.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def load_run_pairing_evidence(path: Path) -> RunPairingEvidence:
    """Load and validate one run pairing evidence from a JSON file.

    Raises:
        FairnessError: On malformed or tampered files.
        OSError: Propagated from the underlying read when the file cannot
            be opened.
        json.JSONDecodeError: When the file is not valid JSON.
    """
    return RunPairingEvidence.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
