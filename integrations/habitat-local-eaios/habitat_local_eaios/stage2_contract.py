"""Deployment-owned enforcement of canonical navigation at the EMOS tool boundary.

The original model selects its action; this module never edits or retries it.
Only the selected action is admitted to CrabAgent and its local skill mapping.
This profile is deliberately specific to EMOS's implemented navigation tools,
while the binding is generic over canonical invocations and destination IDs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .diagnostics import BufferedJsonlWriter
from .model import CanonicalMobilityInvocation, IntegrationError

_LOG = logging.getLogger(__name__)
_MAX_RECORD_BYTES = 65_536
_MISSING = object()
_NAVIGATION_OPERATIONS = frozenset({"mobility.move@v1", "mobility.navigate@v1"})


class Stage2ContractViolation(IntegrationError):
    """Report a rejected local action without misclassifying it as Provider failure."""

    def __init__(self, agent_name: str, action: object, reason: str) -> None:
        """Retain the selected model action and the contract failure reason."""
        self.agent_name = agent_name
        self.action = action
        self.reason = reason
        super().__init__(f"Stage2 local execution contract failed for {agent_name}: {reason}")


@dataclass(frozen=True)
class Stage2ExecutionContract:
    """Bind one agent's navigation authority to a committed invocation digest."""

    operation: str
    expected_destination: str | None
    invocation_digest: str | None

    @classmethod
    def for_invocation(cls, invocation: CanonicalMobilityInvocation) -> Stage2ExecutionContract:
        """Freeze the canonical destination without interpreting its entity spelling."""
        if invocation.operation not in _NAVIGATION_OPERATIONS:
            raise IntegrationError(f"no Stage2 execution profile for {invocation.operation!r}")
        return cls(invocation.operation, invocation.destination, invocation.request_key())

    @classmethod
    def idle(cls) -> Stage2ExecutionContract:
        """Allow only non-physical actions for an agent with no committed work."""
        return cls("unassigned", None, None)

    def as_dict(self) -> dict[str, object]:
        """Expose the immutable semantic binding beside each action decision."""
        return {
            "profile": "emos-navigation/v0.1",
            "operation": self.operation,
            "expected_destination": self.expected_destination,
            "invocation_digest": self.invocation_digest,
        }

    def validate(self, agent_name: str, action: object, peer_names: frozenset[str]) -> None:
        """Reject unauthorized targets, physical tools, or malformed tool arguments."""
        if not isinstance(action, dict) or set(action) != {"name", "arguments"}:
            self._fail(agent_name, action, "selected action requires exactly name and arguments")
        name, arguments = action["name"], action["arguments"]
        if not isinstance(name, str) or name not in {"nav_to_obj", "wait", "send_request"}:
            self._fail(agent_name, action, "tool is not authorized by this execution profile")
        if not isinstance(arguments, dict):
            self._fail(agent_name, action, "raw tool arguments must be an object")
        if name == "nav_to_obj":
            if self.expected_destination is None:
                self._fail(agent_name, action, "unassigned agent cannot navigate")
            if set(arguments) != {"target_obj"}:
                self._fail(agent_name, action, "nav_to_obj requires exactly target_obj")
            if arguments["target_obj"] != self.expected_destination:
                self._fail(agent_name, action, "target does not match canonical destination")
        elif name == "wait":
            # The raw wait tool takes no arguments; CrabAgent later maps it to ["500"].
            if arguments:
                self._fail(agent_name, action, "wait accepts no tool arguments")
        else:
            if set(arguments) != {"request", "target_agent"}:
                self._fail(agent_name, action, "send_request requires request and target_agent")
            target, request = arguments["target_agent"], arguments["request"]
            if not isinstance(request, str) or not request.strip():
                self._fail(agent_name, action, "peer request must be non-empty text")
            if not isinstance(target, str) or target not in peer_names or target == agent_name:
                self._fail(agent_name, action, "peer request target is not another active agent")

    @staticmethod
    def _fail(agent_name: str, action: object, reason: str) -> NoReturn:
        """Raise a typed local failure without repairing the candidate action."""
        raise Stage2ContractViolation(agent_name, action, reason)


class Stage2ActionAudit:
    """Stream bounded selected-tool evidence; I/O failure never permits a rejected action."""

    def __init__(self, directory: Path) -> None:
        """Initialize per-execution audit accounting without touching disk."""
        self._directory = directory
        self._sequence = 0
        self._unavailable = 0
        self._writer = BufferedJsonlWriter(directory / "stage2-actions.jsonl", batch_records=1)

    def record(self, document: dict[str, Any]) -> None:
        """Freeze evidence before vendor mutation, bounding each record to 64 KiB."""
        self._sequence += 1
        row = dict(document, sequence=self._sequence)
        try:
            encoded = json.dumps(row, allow_nan=False, ensure_ascii=True)
            if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
                raise ValueError("selected action evidence exceeds byte limit")
            frozen = json.loads(encoded)
        except (TypeError, ValueError, OverflowError):
            self._unavailable += 1
            frozen = {
                "sequence": self._sequence,
                "schema_version": "roboguide.stage2-action/v0.1",
                "decision": document.get("decision"),
                "evidence_status": "unavailable",
                "reason": "action evidence is not bounded JSON",
            }
        self._writer.append(frozen)

    def close(self) -> None:
        """Save explicit completeness accounting without masking the execution outcome."""
        self._writer.flush()
        stats = self._writer.stats()
        summary = {
            "schema_version": "roboguide.stage2-action-audit/v0.1",
            "records_seen": self._sequence,
            "records_unavailable": self._unavailable,
            "max_record_bytes": _MAX_RECORD_BYTES,
            "complete": self._unavailable == 0 and stats["records_written"] == self._sequence,
            **stats,
        }
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            (self._directory / "stage2-action-audit.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError:
            _LOG.exception("Stage2 action audit summary unavailable")


def _restore_attribute(instance: Any, name: str, previous: Any) -> None:
    """Restore an instance override or remove it to reveal the original class method."""
    if previous is _MISSING:
        delattr(instance, name)
    else:
        setattr(instance, name, previous)


def _guard_agent(
    agent: Any,
    contract: Stage2ExecutionContract,
    peer_names: frozenset[str],
    record: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Wrap one agent instance, deferring its model lookup until lazy initialization."""
    original_chat = agent.chat
    previous_chat = vars(agent).get("chat", _MISSING)
    agent_name = agent.name

    def guarded_chat(observation: str) -> Any:
        """Validate raw model output before CrabAgent transforms or dispatches it."""
        model = getattr(agent, "llm_model", None)
        if model is None or not callable(getattr(model, "chat", None)):
            raise IntegrationError("Stage2 contract requires an initialized model")
        # These vendor options execute tools internally rather than returning an action.
        if getattr(model, "planning_stage", False) or getattr(model, "code_execution", False):
            raise IntegrationError("Stage2 contract does not support internally executing models")
        original_model_chat = model.chat
        previous_model_chat = vars(model).get("chat", _MISSING)

        def guarded_model_chat(content: str, crab_planning: bool = False) -> Any:
            """Keep the original response intact and fence the selected execution tool."""
            result = original_model_chat(content, crab_planning=crab_planning)
            if crab_planning:
                return result
            action = (
                {"name": result[0], "arguments": result[1]}
                if isinstance(result, tuple) and len(result) == 2
                else result
            )
            error = None
            try:
                if not isinstance(result, tuple) or len(result) != 2:
                    raise Stage2ContractViolation(
                        agent_name, action, "execution model must return an action tuple"
                    )
                contract.validate(agent_name, action, peer_names)
            except Stage2ContractViolation as violation:
                error = violation
            document = {
                "schema_version": "roboguide.stage2-action/v0.1",
                "agent_name": agent_name,
                "contract": contract.as_dict(),
                "selected_action": action,
                "decision": "rejected" if error else "allowed",
                "reason": error.reason if error else None,
                "boundary": "before-crab-agent-dispatch",
            }
            try:
                record(document)
            except Exception:  # noqa: BLE001 - failed evidence never authorizes a tool
                _LOG.exception("Stage2 selected action evidence unavailable")
            if error is not None:
                raise error
            return result

        model.chat = guarded_model_chat
        try:
            return original_chat(observation)
        finally:
            _restore_attribute(model, "chat", previous_model_chat)

    agent.chat = guarded_chat

    def restore() -> None:
        """Remove the execution-scoped instance hook."""
        _restore_attribute(agent, "chat", previous_chat)

    return restore


def install_stage2_contract_guard(
    agents: Sequence[Any],
    contracts: Mapping[str, Stage2ExecutionContract],
    record: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Bind all active agent instances without changing any vendor class or Prompt.

    EMOS calls these agents synchronously within ``actor.act``. Their models
    initialize lazily; instance hooks intercept only execution calls, after that
    initialization. Installation rejects missing or duplicate agent identities
    before applying any hooks. Restoration occurs on every policy-loop exit.
    """
    names = [getattr(agent, "name", None) for agent in agents]
    if (
        not all(isinstance(name, str) for name in names)
        or len(set(names)) != len(names)
        or set(names) != set(contracts)
        or any(not callable(getattr(agent, "chat", None)) for agent in agents)
    ):
        raise IntegrationError("active EMOS agents must match execution contracts exactly")
    callbacks: list[Callable[[], None]] = []
    peer_names = frozenset(contracts)
    try:
        for agent in agents:
            callbacks.append(_guard_agent(agent, contracts[agent.name], peer_names, record))
    except BaseException:
        for callback in reversed(callbacks):
            callback()
        raise

    def restore() -> None:
        """Restore all hooks once; repeated cleanup calls are harmless."""
        for callback in reversed(callbacks):
            callback()
        callbacks.clear()

    return restore
