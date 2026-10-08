"""Read-only readiness evidence for the original EMOS relocation action path."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RELOCATION_READINESS_PROFILE = "roboguide.habitat-relocation-readiness/v0.1"
_REQUIRED_TOOLS = frozenset({"nav_to_obj", "pick", "place"})
_REQUIRED_SKILLS = frozenset({"nav_to_obj", "pick", "place"})


def _action_name(action: Any) -> str | None:
    """Read one vendor action name without importing the vendor action class."""
    if isinstance(action, dict):
        value = action.get("name")
    else:
        value = getattr(action, "name", None)
    return value if isinstance(value, str) and value else None


def _skill_names(policy: Any) -> set[str]:
    """Read declared policy skill names from either of EMOS' stable containers."""
    names: set[str] = set()
    index_names = getattr(policy, "_idx_to_name", None)
    if isinstance(index_names, Sequence) and not isinstance(index_names, (str, bytes)):
        names.update(name for name in index_names if isinstance(name, str))
    skills = getattr(policy, "_skills", None)
    if isinstance(skills, dict):
        names.update(name for name in skills if isinstance(name, str))
    return names


@dataclass(frozen=True)
class RelocationCapabilityEvidence:
    """Bounded, read-only readiness result for one loaded Stage2 policy set."""

    ready: bool
    detail: str
    policy_count: int
    missing_tools: tuple[str, ...] = ()
    missing_skills: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """Return a stable readiness artifact without exposing vendor objects."""
        return {
            "schema_version": RELOCATION_READINESS_PROFILE,
            "ready": self.ready,
            "detail": self.detail,
            "policy_count": self.policy_count,
            "missing_tools": list(self.missing_tools),
            "missing_skills": list(self.missing_skills),
        }


def inspect_relocation_capability(policies: Sequence[Any]) -> RelocationCapabilityEvidence:
    """Check actual loaded action and skill declarations without executing them.

    The check is intentionally deployment-local. It does not infer manipulation
    ability from robot names, prompts, or MissionPlan goals, and it never calls
    a model, simulator action, or skill method.
    """
    if not policies:
        return RelocationCapabilityEvidence(False, "no active EMOS policies are loaded", 0)
    missing_tools: set[str] = set()
    missing_skills: set[str] = set()
    for policy in policies:
        agent = getattr(getattr(policy, "_high_level_policy", None), "llm_agent", None)
        actions = getattr(agent, "actions", None)
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
            missing_tools.update(_REQUIRED_TOOLS)
        else:
            declared = {name for name in map(_action_name, actions) if name}
            missing_tools.update(_REQUIRED_TOOLS - declared)
        missing_skills.update(_REQUIRED_SKILLS - _skill_names(policy))
    if missing_tools or missing_skills:
        details: list[str] = []
        if missing_tools:
            details.append("missing tools=" + ",".join(sorted(missing_tools)))
        if missing_skills:
            details.append("missing skills=" + ",".join(sorted(missing_skills)))
        return RelocationCapabilityEvidence(
            False,
            "; ".join(details),
            len(policies),
            tuple(sorted(missing_tools)),
            tuple(sorted(missing_skills)),
        )
    return RelocationCapabilityEvidence(
        True,
        "loaded EMOS policies expose the required relocation tools and skills",
        len(policies),
    )
