"""Enforce canonical operation boundaries around EMOS Stage2 tool calls.

The original EMOS model and policy remain responsible for selecting a local
tool.  This module observes that selection at the model-client boundary,
records it durably, and rejects calls that exceed the operation committed by
RoboGuide.  It never substitutes a different tool or argument.
"""

# ruff: noqa: UP045 -- this module is imported by the deployment's Python 3.9 runtime.

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .model import CanonicalMobilityInvocation, IntegrationError

CONTRACT_SCHEMA = "roboguide.local-eaios.stage2-tool-contract/v0.1"
EVIDENCE_SCHEMA = "roboguide.local-eaios.stage2-tool-call/v0.1"
EVIDENCE_FILENAME = "stage2-contract-calls.jsonl"
_MAX_ARGUMENT_BYTES = 16 * 1024


class LocalContractViolation(IntegrationError):
    """Report a Stage2 tool call that the committed operation does not authorize."""


@dataclass(frozen=True)
class _ToolDecision:
    """Describe one deterministic contract decision before local execution."""

    accepted: bool
    code: str
    detail: str


@dataclass
class _InstalledHook:
    """Retain one mutable EMOS hook so the original object can be restored."""

    agent: Any
    original_init_agent: Any
    model: Optional[Any] = None
    original_model_chat: Optional[Any] = None


class Stage2ContractGuard:
    """Validate and archive original EMOS Stage2 tool selections.

    The guard attaches only to the deployment adapter's existing EMOS agent
    instances.  Provider planning calls are left untouched.  Each executable
    tool selection is checked after the provider returns and before CrabAgent
    can translate it into a Habitat skill or mutate its message pipe.
    """

    def __init__(
        self,
        evidence_dir: Path,
        invocations: dict[int, Optional[CanonicalMobilityInvocation]],
    ) -> None:
        """Bind exact logical assignments and an append-only evidence path."""
        self._evidence_path = evidence_dir / EVIDENCE_FILENAME
        self._invocations = dict(invocations)
        self._sequence = 0
        self._hooks: list[_InstalledHook] = []

    def install(self, actor: Any) -> None:
        """Install model-call guards for all active EMOS policies.

        Raises:
            LocalContractViolation: If the policy topology cannot be guarded
                before the first executable model call.
        """
        policies = getattr(actor, "_active_policies", None)
        if not isinstance(policies, (list, tuple)):
            raise LocalContractViolation(
                "local contract failure: EMOS active policy topology is unavailable"
            )
        try:
            for agent_id, policy in enumerate(policies):
                high_level = getattr(policy, "_high_level_policy", None)
                agent = getattr(high_level, "llm_agent", None)
                original_init = getattr(agent, "init_agent", None)
                if agent is None or not callable(original_init):
                    raise LocalContractViolation(
                        f"local contract failure: agent {agent_id} has no guardable Stage2 client"
                    )
                hook = _InstalledHook(agent=agent, original_init_agent=original_init)

                def guarded_init(
                    *args: Any,
                    _agent_id: int = agent_id,
                    _hook: _InstalledHook = hook,
                    **kwargs: Any,
                ) -> Any:
                    """Initialize the original agent, then guard its executable calls."""
                    result = _hook.original_init_agent(*args, **kwargs)
                    self._wrap_model(_agent_id, _hook)
                    return result

                agent.init_agent = guarded_init
                self._hooks.append(hook)
                if getattr(agent, "initialized", False):
                    self._wrap_model(agent_id, hook)
        except BaseException:
            self.restore()
            raise

    def restore(self) -> None:
        """Restore every EMOS method replaced by :meth:`install`."""
        for hook in reversed(self._hooks):
            if hook.model is not None and hook.original_model_chat is not None:
                hook.model.chat = hook.original_model_chat
            hook.agent.init_agent = hook.original_init_agent
        self._hooks.clear()

    def _wrap_model(self, agent_id: int, hook: _InstalledHook) -> None:
        """Wrap one initialized model client without changing provider inputs."""
        model = getattr(hook.agent, "llm_model", None)
        original_chat = getattr(model, "chat", None)
        if model is None or not callable(original_chat):
            raise LocalContractViolation(
                f"local contract failure: agent {agent_id} model client is unavailable"
            )
        if hook.model is model and hook.original_model_chat is not None:
            return
        hook.model = model
        hook.original_model_chat = original_chat

        def guarded_chat(content: str, crab_planning: bool = False) -> Any:
            """Validate a provider-selected tool before CrabAgent consumes it."""
            result = original_chat(content, crab_planning=crab_planning)
            if crab_planning:
                return result
            self._check_and_record(agent_id, result)
            return result

        model.chat = guarded_chat

    def _check_and_record(self, agent_id: int, result: Any) -> None:
        """Validate one raw model result, persist the decision, and fail closed."""
        invocation = self._invocations.get(agent_id)
        name, arguments, malformed = _tool_result(result)
        if malformed is not None:
            decision = _ToolDecision(False, "malformed_tool_call", malformed)
        else:
            decision = _decide(invocation, name, arguments)
        record = self._record(agent_id, invocation, name, arguments, decision)
        self._append_record(record)
        if not decision.accepted:
            raise LocalContractViolation(
                f"local contract failure for agent {agent_id}: {decision.code}: {decision.detail}"
            )

    def _record(
        self,
        agent_id: int,
        invocation: Optional[CanonicalMobilityInvocation],
        name: Optional[str],
        arguments: Optional[dict[str, Any]],
        decision: _ToolDecision,
    ) -> dict[str, Any]:
        """Build one bounded, attribution-complete evidence record."""
        self._sequence += 1
        argument_evidence, argument_digest = _argument_evidence(arguments)
        identity: dict[str, Any]
        if invocation is None:
            identity = {
                "group_id": None,
                "mission_id": None,
                "role_id": None,
                "task_id": None,
            }
            operation = None
            parameters = None
        else:
            identity = {
                "group_id": invocation.group_id,
                "mission_id": invocation.mission_id,
                "role_id": invocation.role_id,
                "task_id": invocation.task_id,
            }
            operation = invocation.operation
            parameters = dict(invocation.parameters)
        return {
            "agent_id": agent_id,
            "canonical_operation": operation,
            "canonical_parameters": parameters,
            "contract_schema": CONTRACT_SCHEMA,
            "decision": "accepted" if decision.accepted else "rejected",
            "decision_code": decision.code,
            "detail": decision.detail,
            **identity,
            "schema_version": EVIDENCE_SCHEMA,
            "sequence": self._sequence,
            "tool_arguments": argument_evidence,
            "tool_arguments_sha256": argument_digest,
            "tool_name": name,
            "unix_time": time.time(),
        }

    def _append_record(self, record: dict[str, Any]) -> None:
        """Durably append one decision before the selected tool can execute."""
        try:
            self._evidence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            with self._evidence_path.open("a", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except Exception as error:
            raise LocalContractViolation(
                "local contract failure: Stage2 tool decision evidence could not be persisted: "
                f"{type(error).__name__}"
            ) from error


def _tool_result(
    result: Any,
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    """Normalize the original model client's executable return shape."""
    if not isinstance(result, tuple) or len(result) != 2:
        return None, None, "model client did not return one (tool, arguments) pair"
    name, arguments = result
    if not isinstance(name, str) or not name:
        return None, None, "tool name is not a non-empty string"
    if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
        return name, None, "tool arguments are not a string-keyed object"
    return name, arguments, None


def _decide(
    invocation: Optional[CanonicalMobilityInvocation],
    name: Optional[str],
    arguments: Optional[dict[str, Any]],
) -> _ToolDecision:
    """Apply the deployment-owned tool contract for one canonical operation."""
    if name is None or arguments is None:
        return _ToolDecision(False, "malformed_tool_call", "tool call is incomplete")
    if invocation is None:
        if name == "wait" and not arguments:
            return _ToolDecision(True, "unassigned_wait", "unassigned agent remains idle")
        return _ToolDecision(
            False,
            "unassigned_agent_action",
            f"unassigned agent selected {name!r} instead of remaining idle",
        )
    if invocation.operation not in {"mobility.move@v1", "mobility.navigate@v1"}:
        return _ToolDecision(
            False,
            "unsupported_operation",
            f"no Stage2 tool contract is defined for {invocation.operation!r}",
        )
    if name == "wait":
        if arguments:
            return _ToolDecision(
                False,
                "invalid_wait_arguments",
                "wait must not carry semantic parameters",
            )
        return _ToolDecision(True, "mobility_wait", "wait preserves the assigned destination")
    if name != "nav_to_obj":
        return _ToolDecision(
            False,
            "tool_not_authorized",
            f"{invocation.operation} does not authorize Stage2 tool {name!r}",
        )
    if set(arguments) != {"target_obj"}:
        return _ToolDecision(
            False,
            "invalid_navigation_arguments",
            "nav_to_obj requires exactly target_obj at the Stage2 tool boundary",
        )
    target = arguments.get("target_obj")
    if not isinstance(target, str) or not target:
        return _ToolDecision(
            False,
            "invalid_navigation_target",
            "nav_to_obj target_obj must be a non-empty semantic entity reference",
        )
    if target != invocation.destination:
        return _ToolDecision(
            False,
            "destination_mismatch",
            f"tool target {target!r} differs from committed destination {invocation.destination!r}",
        )
    return _ToolDecision(
        True,
        "mobility_navigation",
        "navigation tool preserves the committed semantic destination",
    )


def _argument_evidence(arguments: Optional[dict[str, Any]]) -> tuple[Any, Optional[str]]:
    """Bound tool argument evidence while retaining a canonical digest."""
    if arguments is None:
        return None, None
    try:
        canonical = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return {"_status": "unavailable", "reason": "arguments are not JSON serializable"}, None
    digest = hashlib.sha256(canonical).hexdigest()
    if len(canonical) > _MAX_ARGUMENT_BYTES:
        return {
            "_status": "omitted",
            "byte_length": len(canonical),
            "reason": "arguments exceed the bounded evidence payload",
        }, digest
    return arguments, digest
