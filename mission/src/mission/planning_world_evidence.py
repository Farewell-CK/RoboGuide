"""Versioned environment facts that are safe for Mission planning."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mission.models import JSONObject, JSONValue

PLANNING_WORLD_EVIDENCE_SCHEMA = "roboguide.authoritative-planning-world-evidence/v0.1"
RESET_PLANNING_WORLD_EVIDENCE_SCHEMA = "roboguide.authoritative-planning-world-evidence/v0.2"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class PlanningWorldEvidenceError(ValueError):
    """Report malformed, incomplete, or tampered environment planning evidence."""


@dataclass(frozen=True, slots=True)
class PlanningSpatialFact:
    """Describe one environment entity's authoritative region and floor."""

    entity_id: str
    region_id: str
    floor_id: str

    def __post_init__(self) -> None:
        """Reject non-identity facts before they enter a frozen snapshot."""
        for field, value in (
            ("entity_id", self.entity_id),
            ("region_id", self.region_id),
            ("floor_id", self.floor_id),
        ):
            _require_text(value, field)

    def to_json(self) -> JSONObject:
        """Serialize one neutral spatial fact without exposing deployment handles."""
        return {
            "entity_id": self.entity_id,
            "region_id": self.region_id,
            "floor_id": self.floor_id,
        }

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningSpatialFact:
        """Restore one exact spatial fact and reject unknown fields."""
        item = _object(value, path)
        _exact_keys(item, {"entity_id", "region_id", "floor_id"}, path)
        return cls(
            entity_id=_text(item["entity_id"], f"{path}.entity_id"),
            region_id=_text(item["region_id"], f"{path}.region_id"),
            floor_id=_text(item["floor_id"], f"{path}.floor_id"),
        )


@dataclass(frozen=True, slots=True)
class PlanningWorldGap:
    """Record one missing world fact without allowing MI to infer it."""

    code: str
    detail: str

    def __post_init__(self) -> None:
        """Reject empty diagnostic fields before they enter a frozen snapshot."""
        _require_text(self.code, "gap.code")
        _require_text(self.detail, "gap.detail")

    def to_json(self) -> JSONObject:
        """Serialize one explicit unknown-world diagnostic."""
        return {"code": self.code, "detail": self.detail}

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningWorldGap:
        """Restore one exact world-evidence gap."""
        item = _object(value, path)
        _exact_keys(item, {"code", "detail"}, path)
        return cls(_text(item["code"], f"{path}.code"), _text(item["detail"], f"{path}.detail"))


@dataclass(frozen=True, slots=True)
class PlanningWorldRelation:
    """Describe one environment-owned relation between two neutral entities."""

    subject_entity_id: str
    relation: str
    object_entity_id: str

    def __post_init__(self) -> None:
        """Allow only versioned floor relations with distinct endpoints."""
        _require_text(self.subject_entity_id, "relation.subject_entity_id")
        _require_text(self.object_entity_id, "relation.object_entity_id")
        if self.subject_entity_id == self.object_entity_id:
            raise PlanningWorldEvidenceError("planning relation endpoints must be distinct")
        if self.relation not in {"same_floor", "different_floor"}:
            raise PlanningWorldEvidenceError("unsupported planning world relation")

    def to_json(self) -> JSONObject:
        """Serialize one exact relation without deployment or PDDL implementation fields."""
        return {
            "subject_entity_id": self.subject_entity_id,
            "relation": self.relation,
            "object_entity_id": self.object_entity_id,
        }

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningWorldRelation:
        """Restore one exact relation and reject unknown fields."""
        item = _object(value, path)
        _exact_keys(item, {"subject_entity_id", "relation", "object_entity_id"}, path)
        return cls(
            subject_entity_id=_text(item["subject_entity_id"], f"{path}.subject_entity_id"),
            relation=_text(item["relation"], f"{path}.relation"),
            object_entity_id=_text(item["object_entity_id"], f"{path}.object_entity_id"),
        )


@dataclass(frozen=True, slots=True)
class PlanningGoalWitness:
    """Record one reset-state location that satisfies several terminal predicates.

    The claim concerns exact occupancy of the named location at the snapshot.
    It never proves a path, a Local EAIOS completion, or future predicate truth.
    Distances are conservatively rounded to millimetres by the environment.
    """

    predicate_paths: tuple[str, ...]
    destination_entity_id: str
    max_distance_mm: int
    tolerance_mm: int

    def __post_init__(self) -> None:
        """Reject noncanonical or geometrically unsupported witness claims."""
        if (
            len(self.predicate_paths) < 2
            or self.predicate_paths != tuple(sorted(set(self.predicate_paths)))
            or any(
                re.fullmatch(r"/goal/operands/[0-9]+", path) is None
                for path in self.predicate_paths
            )
        ):
            raise PlanningWorldEvidenceError("goal witness predicate paths are invalid")
        _require_text(self.destination_entity_id, "goal witness destination_entity_id")
        if (
            isinstance(self.max_distance_mm, bool)
            or isinstance(self.tolerance_mm, bool)
            or not isinstance(self.max_distance_mm, int)
            or not isinstance(self.tolerance_mm, int)
            or self.max_distance_mm < 0
            or self.tolerance_mm <= 0
            or self.max_distance_mm > self.tolerance_mm
        ):
            raise PlanningWorldEvidenceError("goal witness distance exceeds tolerance")

    def to_json(self) -> JSONObject:
        """Expose a neutral bounded geometric witness to MI."""
        return {
            "predicate_paths": list(self.predicate_paths),
            "destination_entity_id": self.destination_entity_id,
            "max_distance_mm": self.max_distance_mm,
            "tolerance_mm": self.tolerance_mm,
        }

    @classmethod
    def from_json(cls, value: object, path: str) -> PlanningGoalWitness:
        """Parse an exact witness without accepting invented extra claims."""
        item = _object(value, path)
        _exact_keys(
            item,
            {"predicate_paths", "destination_entity_id", "max_distance_mm", "tolerance_mm"},
            path,
        )
        return cls(
            predicate_paths=tuple(
                _text(raw, f"{path}.predicate_paths[{index}]")
                for index, raw in enumerate(
                    _array(item["predicate_paths"], f"{path}.predicate_paths")
                )
            ),
            destination_entity_id=_text(
                item["destination_entity_id"], f"{path}.destination_entity_id"
            ),
            max_distance_mm=_integer(item["max_distance_mm"], f"{path}.max_distance_mm"),
            tolerance_mm=_integer(item["tolerance_mm"], f"{path}.tolerance_mm"),
        )


@dataclass(frozen=True, slots=True)
class AuthoritativePlanningWorldEvidence:
    """Freeze environment-owned spatial facts before a Mission is planned."""

    run_id: str
    episode_id: str
    scene_id: str
    dataset_revision: str
    dataset_sha256: str
    source_revision: str
    facts: tuple[PlanningSpatialFact, ...]
    relations: tuple[PlanningWorldRelation, ...]
    gaps: tuple[PlanningWorldGap, ...]
    evidence_digest: str
    schema_version: str = PLANNING_WORLD_EVIDENCE_SCHEMA
    goal_witnesses: tuple[PlanningGoalWitness, ...] = ()

    def __post_init__(self) -> None:
        """Reject malformed, ambiguous, or tampered environment facts."""
        if (
            not isinstance(self.schema_version, str)
            or self.schema_version
            not in {
                PLANNING_WORLD_EVIDENCE_SCHEMA,
                RESET_PLANNING_WORLD_EVIDENCE_SCHEMA,
            }
            or (self.schema_version == PLANNING_WORLD_EVIDENCE_SCHEMA and self.goal_witnesses)
        ):
            raise PlanningWorldEvidenceError("unsupported planning world evidence schema")
        for field, value in (
            ("identity.run_id", self.run_id),
            ("identity.episode_id", self.episode_id),
            ("identity.scene_id", self.scene_id),
            ("identity.dataset_revision", self.dataset_revision),
            ("identity.source_revision", self.source_revision),
        ):
            _require_text(value, field)
        if (
            not isinstance(self.dataset_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", self.dataset_sha256) is None
        ):
            raise PlanningWorldEvidenceError("identity.dataset_sha256 must be a lowercase SHA-256")
        if not isinstance(self.facts, tuple) or any(
            not isinstance(fact, PlanningSpatialFact) for fact in self.facts
        ):
            raise PlanningWorldEvidenceError("planning facts must be typed tuples")
        if not isinstance(self.relations, tuple) or any(
            not isinstance(relation, PlanningWorldRelation) for relation in self.relations
        ):
            raise PlanningWorldEvidenceError("planning relations must be typed tuples")
        if not isinstance(self.gaps, tuple) or any(
            not isinstance(gap, PlanningWorldGap) for gap in self.gaps
        ):
            raise PlanningWorldEvidenceError("planning gaps must be typed tuples")
        if self.facts != tuple(sorted(self.facts, key=lambda item: item.entity_id)):
            raise PlanningWorldEvidenceError("planning facts must use canonical order")
        fact_ids = [fact.entity_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise PlanningWorldEvidenceError("planning facts must have unique entity ids")
        if self.relations != tuple(
            sorted(
                self.relations,
                key=lambda item: (
                    item.subject_entity_id,
                    item.relation,
                    item.object_entity_id,
                ),
            )
        ):
            raise PlanningWorldEvidenceError("planning relations must use canonical order")
        relation_keys = [
            (relation.subject_entity_id, relation.relation, relation.object_entity_id)
            for relation in self.relations
        ]
        if len(relation_keys) != len(set(relation_keys)):
            raise PlanningWorldEvidenceError("planning relations must be unique")
        if self.gaps != tuple(sorted(self.gaps, key=lambda item: (item.code, item.detail))):
            raise PlanningWorldEvidenceError("planning gaps must use canonical order")
        if (
            not isinstance(self.goal_witnesses, tuple)
            or any(not isinstance(witness, PlanningGoalWitness) for witness in self.goal_witnesses)
            or self.goal_witnesses
            != tuple(sorted(self.goal_witnesses, key=lambda witness: witness.destination_entity_id))
            or len({w.destination_entity_id for w in self.goal_witnesses})
            != len(self.goal_witnesses)
        ):
            raise PlanningWorldEvidenceError("planning goal witnesses must be unique and ordered")
        if _DIGEST.fullmatch(self.evidence_digest) is None:
            raise PlanningWorldEvidenceError("planning world evidence digest is invalid")
        if self.evidence_digest != _digest(self._body()):
            raise PlanningWorldEvidenceError(
                "planning world evidence digest does not match content"
            )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        episode_id: str,
        scene_id: str,
        dataset_revision: str,
        dataset_sha256: str,
        source_revision: str,
        facts: tuple[PlanningSpatialFact, ...] = (),
        relations: tuple[PlanningWorldRelation, ...] = (),
        gaps: tuple[PlanningWorldGap, ...] = (),
        schema_version: str = PLANNING_WORLD_EVIDENCE_SCHEMA,
        goal_witnesses: tuple[PlanningGoalWitness, ...] = (),
    ) -> AuthoritativePlanningWorldEvidence:
        """Create one digest-bound spatial snapshot with deterministic ordering."""
        ordered_facts = tuple(sorted(facts, key=lambda item: item.entity_id))
        ordered_relations = tuple(
            sorted(
                relations,
                key=lambda item: (
                    item.subject_entity_id,
                    item.relation,
                    item.object_entity_id,
                ),
            )
        )
        ordered_gaps = tuple(sorted(gaps, key=lambda item: (item.code, item.detail)))
        ordered_witnesses = tuple(
            sorted(goal_witnesses, key=lambda item: item.destination_entity_id)
        )
        body: JSONObject = {
            "schema_version": schema_version,
            "authority": "environment-authoritative",
            "identity": {
                "run_id": run_id,
                "episode_id": episode_id,
                "scene_id": scene_id,
                "dataset_revision": dataset_revision,
                "dataset_sha256": dataset_sha256,
                "source_revision": source_revision,
            },
            "facts": [fact.to_json() for fact in ordered_facts],
            "relations": [relation.to_json() for relation in ordered_relations],
            "gaps": [gap.to_json() for gap in ordered_gaps],
        }
        if schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA:
            body["goal_witnesses"] = [witness.to_json() for witness in ordered_witnesses]
        return cls(
            run_id=run_id,
            episode_id=episode_id,
            scene_id=scene_id,
            dataset_revision=dataset_revision,
            dataset_sha256=dataset_sha256,
            source_revision=source_revision,
            facts=ordered_facts,
            relations=ordered_relations,
            gaps=ordered_gaps,
            evidence_digest=_digest(body),
            schema_version=schema_version,
            goal_witnesses=ordered_witnesses,
        )

    @classmethod
    def from_json(cls, value: object) -> AuthoritativePlanningWorldEvidence:
        """Restore and verify one fixed environment evidence artifact."""
        item = _object(value, "authoritative planning world evidence")
        schema_version = item.get("schema_version")
        if not isinstance(schema_version, str) or schema_version not in {
            PLANNING_WORLD_EVIDENCE_SCHEMA,
            RESET_PLANNING_WORLD_EVIDENCE_SCHEMA,
        }:
            raise PlanningWorldEvidenceError("unsupported planning world evidence schema")
        expected = {
            "schema_version",
            "authority",
            "identity",
            "facts",
            "relations",
            "gaps",
            "digest",
        }
        if schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA:
            expected.add("goal_witnesses")
        _exact_keys(
            item,
            expected,
            "authoritative planning world evidence",
        )
        if item["authority"] != "environment-authoritative":
            raise PlanningWorldEvidenceError("planning world evidence authority is unsupported")
        identity = _object(item["identity"], "planning world identity")
        _exact_keys(
            identity,
            {
                "run_id",
                "episode_id",
                "scene_id",
                "dataset_revision",
                "dataset_sha256",
                "source_revision",
            },
            "planning world identity",
        )
        facts = tuple(
            PlanningSpatialFact.from_json(raw, f"planning world facts[{index}]")
            for index, raw in enumerate(_array(item["facts"], "planning world facts"))
        )
        relations = tuple(
            PlanningWorldRelation.from_json(raw, f"planning world relations[{index}]")
            for index, raw in enumerate(_array(item["relations"], "planning world relations"))
        )
        gaps = tuple(
            PlanningWorldGap.from_json(raw, f"planning world gaps[{index}]")
            for index, raw in enumerate(_array(item["gaps"], "planning world gaps"))
        )
        goal_witnesses = (
            tuple(
                PlanningGoalWitness.from_json(raw, f"planning world goal_witnesses[{index}]")
                for index, raw in enumerate(
                    _array(item["goal_witnesses"], "planning world goal_witnesses")
                )
            )
            if schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA
            else ()
        )
        evidence = cls.create(
            run_id=_text(identity["run_id"], "identity.run_id"),
            episode_id=_text(identity["episode_id"], "identity.episode_id"),
            scene_id=_text(identity["scene_id"], "identity.scene_id"),
            dataset_revision=_text(identity["dataset_revision"], "identity.dataset_revision"),
            dataset_sha256=_text(identity["dataset_sha256"], "identity.dataset_sha256"),
            source_revision=_text(identity["source_revision"], "identity.source_revision"),
            facts=facts,
            relations=relations,
            gaps=gaps,
            schema_version=schema_version,
            goal_witnesses=goal_witnesses,
        )
        supplied_digest = _text(item["digest"], "planning world digest")
        if _DIGEST.fullmatch(supplied_digest) is None:
            raise PlanningWorldEvidenceError("planning world evidence digest is invalid")
        if evidence.evidence_digest != supplied_digest:
            raise PlanningWorldEvidenceError(
                "planning world evidence digest does not match content"
            )
        return evidence

    @classmethod
    def load(cls, path: Path) -> AuthoritativePlanningWorldEvidence:
        """Load one deployment-fixed artifact without accepting request paths."""
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlanningWorldEvidenceError(
                f"cannot load planning world evidence {path}: {error}"
            ) from error
        return cls.from_json(decoded)

    def _body(self) -> JSONObject:
        """Return the digest body without its self-referential digest field."""
        body: JSONObject = {
            "schema_version": self.schema_version,
            "authority": "environment-authoritative",
            "identity": {
                "run_id": self.run_id,
                "episode_id": self.episode_id,
                "scene_id": self.scene_id,
                "dataset_revision": self.dataset_revision,
                "dataset_sha256": self.dataset_sha256,
                "source_revision": self.source_revision,
            },
            "facts": [fact.to_json() for fact in self.facts],
            "relations": [relation.to_json() for relation in self.relations],
            "gaps": [gap.to_json() for gap in self.gaps],
        }
        if self.schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA:
            body["goal_witnesses"] = [witness.to_json() for witness in self.goal_witnesses]
        return body

    def to_json(self) -> JSONObject:
        """Serialize the frozen evidence with its content digest."""
        return {**self._body(), "digest": self.evidence_digest}


def planning_world_review_payload(
    evidence: AuthoritativePlanningWorldEvidence | None,
) -> JSONObject | None:
    """Expose world facts with explicit unknown and non-assignment guidance."""
    if evidence is None:
        return None
    return {
        "schema_version": evidence.schema_version,
        "evidence_digest": evidence.evidence_digest,
        "identity": {
            "episode_id": evidence.episode_id,
            "scene_id": evidence.scene_id,
            "dataset_revision": evidence.dataset_revision,
            "dataset_sha256": evidence.dataset_sha256,
            "source_revision": evidence.source_revision,
        },
        "facts": [fact.to_json() for fact in evidence.facts],
        "relations": [relation.to_json() for relation in evidence.relations],
        "gaps": [gap.to_json() for gap in evidence.gaps],
        **(
            {"goal_witnesses": [witness.to_json() for witness in evidence.goal_witnesses]}
            if evidence.schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA
            else {}
        ),
        "guidance": {
            "facts_describe_world_only": True,
            "facts_do_not_select_nodes_or_physical_entities": True,
            "unknown_world_facts_must_not_be_guessed": True,
            **(
                {"goal_witness_is_snapshot_not_execution_guarantee": True}
                if evidence.schema_version == RESET_PLANNING_WORLD_EVIDENCE_SCHEMA
                else {}
            ),
        },
    }


def _digest(value: JSONObject) -> str:
    """Return the canonical SHA-256 identity for one finite evidence body."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlanningWorldEvidenceError("planning world evidence must be finite JSON") from error
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _object(value: object, path: str) -> JSONObject:
    """Return one string-keyed object or reject it at the contract boundary."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PlanningWorldEvidenceError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: object, path: str) -> list[JSONValue]:
    """Return one JSON array or reject it."""
    if not isinstance(value, list):
        raise PlanningWorldEvidenceError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    """Return one nonblank string from a versioned evidence field."""
    if not isinstance(value, str) or not value.strip():
        raise PlanningWorldEvidenceError(f"{path} must be nonblank text")
    return value


def _integer(value: object, path: str) -> int:
    """Return an exact JSON integer without accepting booleans or coercion."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanningWorldEvidenceError(f"{path} must be an integer")
    return value


def _require_text(value: str, path: str) -> None:
    """Reject blank constructor text without coercion."""
    if not isinstance(value, str) or not value.strip():
        raise PlanningWorldEvidenceError(f"{path} must be nonblank text")


def _exact_keys(value: JSONObject, expected: set[str], path: str) -> None:
    """Reject unknown or missing keys so contract evolution is explicit."""
    actual = set(value)
    if actual != expected:
        raise PlanningWorldEvidenceError(
            f"{path} keys mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
