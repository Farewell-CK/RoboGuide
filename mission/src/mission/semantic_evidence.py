"""Benchmark-neutral authoritative semantic evidence for Mission Intelligence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import cast

from mission.models import JSONObject, JSONValue

SEMANTIC_EVIDENCE_SCHEMA = "roboguide.authoritative-semantic-evidence/v0.1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_LOGICAL_OPERATORS = {"and", "or", "nand", "nor"}


class SemanticEvidenceError(ValueError):
    """Report missing, malformed, or tampered authoritative semantic evidence."""


@dataclass(frozen=True, slots=True)
class SemanticExpression:
    """Represent one logical objective without exposing a benchmark expression type."""

    kind: str
    name: str | None = None
    arguments: tuple[str, ...] = ()
    operator: str | None = None
    operands: tuple[SemanticExpression, ...] = ()
    quantifier: str | None = None
    variables: tuple[str, ...] = ()

    @classmethod
    def predicate(cls, name: str, arguments: tuple[str, ...] = ()) -> SemanticExpression:
        """Create one atomic predicate with stable argument ordering."""
        return cls(kind="predicate", name=name, arguments=arguments)

    @classmethod
    def logical(
        cls,
        operator: str,
        operands: tuple[SemanticExpression, ...],
        *,
        quantifier: str | None = None,
        variables: tuple[str, ...] = (),
    ) -> SemanticExpression:
        """Create one logical expression while preserving its tree structure."""
        return cls(
            kind="logical",
            operator=operator,
            operands=operands,
            quantifier=quantifier,
            variables=variables,
        )

    def __post_init__(self) -> None:
        """Reject expression shapes that could lose the authoritative objective semantics."""
        if self.kind == "predicate":
            if (
                not isinstance(self.name, str)
                or not self.name.strip()
                or self.operator is not None
                or self.operands
                or self.quantifier is not None
                or self.variables
            ):
                raise SemanticEvidenceError("predicate expression is malformed")
            _require_texts(self.arguments, "predicate.arguments")
            return
        if self.kind == "logical":
            if (
                self.name is not None
                or self.arguments
                or self.operator not in _LOGICAL_OPERATORS
                or not self.operands
            ):
                raise SemanticEvidenceError("logical expression is malformed")
            if self.quantifier is None and self.variables:
                raise SemanticEvidenceError("logical variables require a quantifier")
            if self.quantifier is not None:
                _require_text(self.quantifier, "logical.quantifier")
            _require_texts(self.variables, "logical.variables")
            return
        raise SemanticEvidenceError("semantic expression kind is unsupported")

    def to_json(self) -> JSONObject:
        """Serialize the exact expression tree used for MI input and provenance."""
        if self.kind == "predicate":
            return {"kind": "predicate", "name": self.name, "arguments": list(self.arguments)}
        return {
            "kind": "logical",
            "operator": self.operator,
            "operands": [operand.to_json() for operand in self.operands],
            "quantifier": self.quantifier,
            "variables": list(self.variables),
        }

    @classmethod
    def from_json(cls, value: object) -> SemanticExpression:
        """Restore one exact expression tree and reject extra or missing structure."""
        item = _object(value, "semantic expression")
        kind = item.get("kind")
        if kind == "predicate":
            _require_fields(item, {"kind", "name", "arguments"}, "predicate")
            arguments = _array(item["arguments"], "predicate.arguments")
            return cls.predicate(_text(item["name"], "predicate.name"), tuple(_texts(arguments)))
        if kind == "logical":
            _require_fields(
                item,
                {"kind", "operator", "operands", "quantifier", "variables"},
                "logical",
            )
            operands = _array(item["operands"], "logical.operands")
            if not operands:
                raise SemanticEvidenceError("logical.operands must not be empty")
            quantifier = item["quantifier"]
            if quantifier is not None and not isinstance(quantifier, str):
                raise SemanticEvidenceError("logical.quantifier must be text or null")
            variables = _array(item["variables"], "logical.variables")
            return cls.logical(
                _text(item["operator"], "logical.operator"),
                tuple(cls.from_json(operand) for operand in operands),
                quantifier=quantifier,
                variables=tuple(_texts(variables)),
            )
        raise SemanticEvidenceError("semantic expression kind is unsupported")


@dataclass(frozen=True, slots=True)
class AuthoritativeSemanticEvidence:
    """Freeze one environment-authoritative joint objective for a Mission lifecycle."""

    run_id: str
    episode_id: str
    revision: str
    objective_scope: str
    goal: SemanticExpression
    world_context: JSONObject
    evidence_digest: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        episode_id: str,
        revision: str,
        goal: SemanticExpression,
        world_context: JSONObject,
    ) -> AuthoritativeSemanticEvidence:
        """Create evidence and bind its digest to identity, goal, and world context."""
        body: JSONObject = {
            "schema_version": SEMANTIC_EVIDENCE_SCHEMA,
            "authority": "environment-authoritative",
            "identity": {"run_id": run_id, "episode_id": episode_id, "revision": revision},
            "objective_scope": "joint_terminal_state",
            "goal": goal.to_json(),
            "world_context": _clone_object(world_context),
        }
        return cls(
            run_id=run_id,
            episode_id=episode_id,
            revision=revision,
            objective_scope="joint_terminal_state",
            goal=goal,
            world_context=_clone_object(world_context),
            evidence_digest=_digest(body),
        )

    def __post_init__(self) -> None:
        """Reject non-joint or internally inconsistent semantic snapshots."""
        _require_text(self.run_id, "identity.run_id")
        _require_text(self.episode_id, "identity.episode_id")
        _require_text(self.revision, "identity.revision")
        if self.objective_scope != "joint_terminal_state":
            raise SemanticEvidenceError("semantic evidence must describe a joint terminal state")
        _clone_object(self.world_context)
        if _DIGEST.fullmatch(self.evidence_digest) is None:
            raise SemanticEvidenceError("semantic evidence digest is invalid")
        if self.evidence_digest != _digest(self._body()):
            raise SemanticEvidenceError("semantic evidence digest does not match its content")

    def _body(self) -> JSONObject:
        """Return the digest body without the self-referential evidence digest."""
        return {
            "schema_version": SEMANTIC_EVIDENCE_SCHEMA,
            "authority": "environment-authoritative",
            "identity": {
                "run_id": self.run_id,
                "episode_id": self.episode_id,
                "revision": self.revision,
            },
            "objective_scope": self.objective_scope,
            "goal": self.goal.to_json(),
            "world_context": _clone_object(self.world_context),
        }

    def to_json(self) -> JSONObject:
        """Serialize the immutable semantic snapshot with its content digest."""
        return {**self._body(), "digest": self.evidence_digest}

    @classmethod
    def from_json(cls, value: object) -> AuthoritativeSemanticEvidence:
        """Restore and cryptographically validate one semantic snapshot."""
        item = _object(value, "authoritative semantic evidence")
        _require_fields(
            item,
            {
                "schema_version",
                "authority",
                "identity",
                "objective_scope",
                "goal",
                "world_context",
                "digest",
            },
            "authoritative semantic evidence",
        )
        if item["schema_version"] != SEMANTIC_EVIDENCE_SCHEMA:
            raise SemanticEvidenceError("unsupported semantic evidence schema")
        if item["authority"] != "environment-authoritative":
            raise SemanticEvidenceError("semantic evidence authority is unsupported")
        identity = _object(item["identity"], "semantic identity")
        _require_fields(identity, {"run_id", "episode_id", "revision"}, "semantic identity")
        return cls(
            run_id=_text(identity["run_id"], "identity.run_id"),
            episode_id=_text(identity["episode_id"], "identity.episode_id"),
            revision=_text(identity["revision"], "identity.revision"),
            objective_scope=_text(item["objective_scope"], "objective_scope"),
            goal=SemanticExpression.from_json(item["goal"]),
            world_context=_clone_object(_object(item["world_context"], "world_context")),
            evidence_digest=_text(item["digest"], "digest"),
        )


def semantic_goal_review_payload(
    evidence: AuthoritativeSemanticEvidence | None,
) -> JSONObject | None:
    """Expose the frozen joint goal as explicit Reviewer/Repairer guidance."""
    if evidence is None:
        return None
    return {
        "schema_version": SEMANTIC_EVIDENCE_SCHEMA,
        "evidence_digest": evidence.evidence_digest,
        "objective_scope": evidence.objective_scope,
        "goal": evidence.goal.to_json(),
        "review_requirements": {
            "preserve_logical_tree": True,
            "preserve_all_predicates": True,
            "do_not_split_joint_objective_into_independent_missions": True,
        },
    }


def semantic_evidence_digest(value: JSONObject) -> str:
    """Return the canonical digest of a complete evidence document excluding its digest field."""
    body = {key: item for key, item in value.items() if key != "digest"}
    return _digest(body)


def _digest(value: JSONValue) -> str:
    """Hash one finite JSON value using the shared MI canonicalization."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SemanticEvidenceError("semantic evidence must be finite JSON") from error
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _clone_object(value: JSONObject) -> JSONObject:
    """Copy one JSON object so callers cannot mutate frozen semantic evidence."""
    cloned = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if not isinstance(cloned, dict) or not all(isinstance(key, str) for key in cloned):
        raise SemanticEvidenceError("world_context must be a JSON object")
    return cast(JSONObject, cloned)


def _object(value: object, path: str) -> JSONObject:
    """Require one string-keyed JSON object at a validation boundary."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SemanticEvidenceError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: object, path: str) -> list[JSONValue]:
    """Require one JSON array at a validation boundary."""
    if not isinstance(value, list):
        raise SemanticEvidenceError(f"{path} must be an array")
    return cast(list[JSONValue], value)


def _text(value: object, path: str) -> str:
    """Require one nonblank string."""
    if not isinstance(value, str) or not value.strip():
        raise SemanticEvidenceError(f"{path} must be nonblank text")
    return value


def _texts(value: list[JSONValue]) -> list[str]:
    """Validate a list of nonblank text values."""
    return [_text(item, "text item") for item in value]


def _require_text(value: str, path: str) -> None:
    """Validate one constructor text field."""
    if not isinstance(value, str) or not value.strip():
        raise SemanticEvidenceError(f"{path} must be nonblank text")


def _require_texts(value: tuple[str, ...], path: str) -> None:
    """Validate one constructor tuple of nonblank text values."""
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SemanticEvidenceError(f"{path} must contain nonblank text")


def _require_fields(value: JSONObject, expected: set[str], path: str) -> None:
    """Reject missing or unexpected fields in a versioned semantic object."""
    if set(value) != expected:
        raise SemanticEvidenceError(f"{path} fields do not match the semantic contract")
