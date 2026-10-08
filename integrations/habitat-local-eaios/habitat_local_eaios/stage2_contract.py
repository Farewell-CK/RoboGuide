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
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .diagnostics import BufferedJsonlWriter
from .model import (
    CanonicalMobilityInvocation,
    CanonicalRelocationInvocation,
    IntegrationError,
)
from .stage2_feedback import Stage2ExecutionFeedback

_LOG = logging.getLogger(__name__)
_MAX_RECORD_BYTES = 65_536
_MISSING = object()
_NAVIGATION_OPERATIONS = frozenset({"mobility.move@v1", "mobility.navigate@v1"})
_RELOCATION_OPERATIONS = frozenset({"object.relocate@v1"})


class Stage2ContractViolation(IntegrationError):
    """Report a rejected local action without misclassifying it as Provider failure."""

    def __init__(self, agent_name: str, action: object, reason: str) -> None:
        """Retain the selected model action and the contract failure reason."""
        self.agent_name = agent_name
        self.action = action
        self.reason = reason
        super().__init__(f"Stage2 local execution contract failed for {agent_name}: {reason}")


@dataclass
class RelocationExecutionState:
    """Track admitted and observed actions for one relocation attempt."""

    phase: str = "before_pick"
    pending_action: str | None = None
    last_completed_action: str | None = None

    def admit(self, action_name: str) -> None:
        """Fence the next semantic transition until its local skill is observed."""
        self.pending_action = action_name
        self.last_completed_action = None

    def complete(self, action_name: str, succeeded: bool) -> None:
        """Apply a phase transition only from definite local skill evidence."""
        if self.pending_action != action_name:
            return
        self.pending_action = None
        self.last_completed_action = action_name
        if not succeeded:
            return
        if action_name == "pick":
            self.phase = "holding"
        elif action_name == "place":
            self.phase = "placed"


@dataclass(frozen=True)
class Stage2ExecutionContract:
    """Bind one agent's local tool authority to a committed invocation digest."""

    operation: str
    expected_destination: str | None
    invocation_digest: str | None
    expected_object: str | None = None
    expected_source: str | None = None

    @classmethod
    def for_invocation(
        cls, invocation: CanonicalMobilityInvocation | CanonicalRelocationInvocation
    ) -> Stage2ExecutionContract:
        """Freeze the exact semantic fields required by the operation profile."""
        if invocation.operation in _NAVIGATION_OPERATIONS:
            if not isinstance(invocation, CanonicalMobilityInvocation):
                raise IntegrationError("navigation invocation has an incompatible type")
            return cls(invocation.operation, invocation.destination, invocation.request_key())
        if invocation.operation in _RELOCATION_OPERATIONS:
            if not isinstance(invocation, CanonicalRelocationInvocation):
                raise IntegrationError("relocation invocation has an incompatible type")
            return cls(
                invocation.operation,
                invocation.destination,
                invocation.request_key(),
                invocation.object_ref,
                invocation.source,
            )
        raise IntegrationError(f"no Stage2 execution profile for {invocation.operation!r}")

    @classmethod
    def idle(cls) -> Stage2ExecutionContract:
        """Allow only non-physical actions for an agent with no committed work."""
        return cls("unassigned", None, None)

    @property
    def is_relocation(self) -> bool:
        """Return whether this contract admits the object-relocation profile."""
        return self.operation in _RELOCATION_OPERATIONS

    def as_dict(self) -> dict[str, object]:
        """Expose the immutable semantic binding beside each action decision."""
        result: dict[str, object] = {
            "profile": "emos-relocation/v0.1" if self.is_relocation else "emos-navigation/v0.1",
            "operation": self.operation,
            "expected_destination": self.expected_destination,
            "invocation_digest": self.invocation_digest,
        }
        if self.is_relocation:
            result["expected_object"] = self.expected_object
            result["expected_source"] = self.expected_source
        return result

    def validate(
        self,
        agent_name: str,
        action: object,
        peer_names: frozenset[str],
        state: RelocationExecutionState | None = None,
    ) -> None:
        """Reject unauthorized or malformed tools before CrabAgent dispatch."""
        if self.is_relocation:
            self._validate_relocation(agent_name, action, peer_names, state)
            return
        self._validate_navigation(agent_name, action, peer_names)

    def advance(self, action: object, state: RelocationExecutionState) -> None:
        """Record admission without claiming that the local skill has completed."""
        if not self.is_relocation or not isinstance(action, dict):
            return
        name = action.get("name")
        if isinstance(name, str):
            state.admit(name)

    def _validate_navigation(
        self, agent_name: str, action: object, peer_names: frozenset[str]
    ) -> None:
        """Validate the existing navigation-only tool profile."""
        name, arguments = self._shape(agent_name, action)
        if name not in {"nav_to_obj", "wait", "send_request"}:
            self._fail(agent_name, action, "tool is not authorized by this execution profile")
        if name == "nav_to_obj":
            if self.expected_destination is None:
                self._fail(agent_name, action, "unassigned agent cannot navigate")
            if set(arguments) != {"target_obj"}:
                self._fail(agent_name, action, "nav_to_obj requires exactly target_obj")
            if arguments["target_obj"] != self.expected_destination:
                self._fail(agent_name, action, "target does not match canonical destination")
        elif name == "wait":
            if arguments:
                self._fail(agent_name, action, "wait accepts no tool arguments")
        else:
            self._validate_send_request(agent_name, action, arguments, peer_names)

    def _validate_relocation(
        self,
        agent_name: str,
        action: object,
        peer_names: frozenset[str],
        state: RelocationExecutionState | None,
    ) -> None:
        """Validate an exact relocation workflow and its current phase."""
        if state is None:
            state = RelocationExecutionState()
        if state.pending_action is not None:
            self._fail(
                agent_name,
                action,
                f"previous relocation action {state.pending_action!r} has no observed completion",
            )
        name, arguments = self._shape(agent_name, action)
        if name not in {
            "nav_to_obj",
            "pick",
            "place",
            "reset_arm",
            "wait",
            "send_request",
        }:
            self._fail(agent_name, action, "tool is not authorized by this relocation profile")
        if name == "wait":
            if arguments:
                self._fail(agent_name, action, "wait accepts no tool arguments")
            return
        if name == "send_request":
            self._validate_send_request(agent_name, action, arguments, peer_names)
            return
        if name == "reset_arm":
            if arguments:
                self._fail(agent_name, action, "reset_arm accepts no tool arguments")
            if state.phase == "holding":
                self._fail(agent_name, action, "reset_arm is not allowed while holding the object")
            return
        if name == "nav_to_obj":
            if set(arguments) != {"target_obj"}:
                self._fail(agent_name, action, "nav_to_obj requires exactly target_obj")
            if state.phase == "before_pick":
                expected = self.expected_object
            elif state.phase == "holding":
                expected = self.expected_destination
            else:
                self._fail(agent_name, action, "navigation is not allowed after placement")
            if expected is None or arguments["target_obj"] != expected:
                self._fail(agent_name, action, "navigation target does not match relocation phase")
            return
        if name == "pick":
            if state.phase != "before_pick":
                self._fail(agent_name, action, "pick is only allowed before the object is held")
            if set(arguments) != {"target_obj"} or arguments["target_obj"] != self.expected_object:
                self._fail(agent_name, action, "pick target does not match canonical object")
            return
        if state.phase != "holding":
            self._fail(agent_name, action, "place requires the object to be held")
        if set(arguments) != {"target_obj", "target_location"}:
            self._fail(agent_name, action, "place requires exactly target_obj and target_location")
        if (
            arguments["target_obj"] != self.expected_object
            or arguments["target_location"] != self.expected_destination
        ):
            self._fail(agent_name, action, "place object or destination does not match relocation")

    @staticmethod
    def _shape(agent_name: str, action: object) -> tuple[str, dict[str, object]]:
        """Require the raw action envelope before interpreting any tool fields."""
        if not isinstance(action, dict) or set(action) != {"name", "arguments"}:
            Stage2ExecutionContract._fail(
                agent_name, action, "selected action requires exactly name and arguments"
            )
        name, arguments = action["name"], action["arguments"]
        if not isinstance(name, str) or not isinstance(arguments, dict):
            Stage2ExecutionContract._fail(
                agent_name, action, "raw tool name and arguments have invalid types"
            )
        return name, arguments

    @staticmethod
    def _validate_send_request(
        agent_name: str,
        action: object,
        arguments: dict[str, object],
        peer_names: frozenset[str],
    ) -> None:
        """Validate a peer message without allowing arbitrary local tool payloads."""
        if set(arguments) != {"request", "target_agent"}:
            Stage2ExecutionContract._fail(
                agent_name, action, "send_request requires request and target_agent"
            )
        target, request = arguments["target_agent"], arguments["request"]
        if not isinstance(request, str) or not request.strip():
            Stage2ExecutionContract._fail(agent_name, action, "peer request must be non-empty text")
        if not isinstance(target, str) or target not in peer_names or target == agent_name:
            Stage2ExecutionContract._fail(
                agent_name, action, "peer request target is not another active agent"
            )

    @staticmethod
    def _fail(agent_name: str, action: object, reason: str) -> NoReturn:
        """Raise a typed local failure without repairing the candidate action."""
        raise Stage2ContractViolation(agent_name, action, reason)


class Stage2ActionAudit:
    """Stream bounded selected-tool evidence; I/O failure never permits a rejected action."""

    def __init__(
        self,
        directory: Path,
        previous_summary: dict[str, Any] | None = None,
        *,
        execution_feedback: bool = False,
    ) -> None:
        """Initialize append-only accounting for a new segment of one episode."""
        self._directory = directory
        self._previous_summary = previous_summary or {}
        self._sequence = int(self._previous_summary.get("records_seen", 0))
        self._unavailable = 0
        self._closed = False
        self.summary: dict[str, Any] | None = None
        self._stem = "stage2-execution-feedback" if execution_feedback else "stage2-action"
        self._record_schema = f"roboguide.{self._stem}/v0.1"
        filename = f"{self._stem}.jsonl" if execution_feedback else "stage2-actions.jsonl"
        self._writer = BufferedJsonlWriter(directory / filename, batch_records=1)

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
                "schema_version": self._record_schema,
                "decision": document.get("decision"),
                "evidence_status": "unavailable",
                "reason": "action evidence is not bounded JSON",
            }
        self._writer.append(frozen)

    def note_unavailable_record(self) -> None:
        """Fence completeness after a caller-observed archival fault outside normal accounting."""
        self._unavailable += 1

    def close(self) -> None:
        """Save bounded completeness accounting without masking execution outcomes."""
        if self._closed:
            return
        self._closed = True
        flush_failed = False
        try:
            self._writer.flush()
        except Exception:  # noqa: BLE001 - evidence finalization must be fail-soft
            flush_failed = True
            _LOG.exception("Stage2 action audit flush unavailable")
        try:
            stats = self._writer.stats()
        except Exception:  # noqa: BLE001 - evidence accounting must be fail-soft
            stats = {
                "batch_capacity_records": 1,
                "flushes": 0,
                "pending_records": self._sequence,
                "records_dropped": 0,
                "records_written": 0,
                "write_failures": 1,
                "write_seconds": 0.0,
            }
            flush_failed = True
            _LOG.exception("Stage2 action audit stats unavailable")
        previous = self._previous_summary
        records_unavailable = int(previous.get("records_unavailable", 0)) + self._unavailable
        records_written = int(previous.get("records_written", 0)) + stats["records_written"]
        records_dropped = int(previous.get("records_dropped", 0)) + stats["records_dropped"]
        write_failures = int(previous.get("write_failures", 0)) + stats["write_failures"]
        summary = {
            "schema_version": f"roboguide.{self._stem}-audit/v0.1",
            "records_seen": self._sequence,
            "records_unavailable": records_unavailable,
            "max_record_bytes": _MAX_RECORD_BYTES,
            "complete": (
                not flush_failed
                and records_unavailable == 0
                and write_failures == 0
                and records_written == self._sequence
            ),
            **stats,
            "records_written": records_written,
            "records_dropped": records_dropped,
            "write_failures": write_failures,
        }
        self.summary = summary
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            (self._directory / f"{self._stem}-audit.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - evidence summary must be fail-soft
            _LOG.exception("Stage2 action audit summary unavailable")


def _raw_history_length(model: Any) -> int | None:
    """Return the vendor history length only when its exchange format is readable."""
    history = getattr(model, "chat_history", None)
    if not isinstance(history, list):
        return None
    return len(history)


def _latest_tool_call_count(model: Any, previous_length: int | None) -> int | None:
    """Verify exactly one new raw Provider exchange and count its tool calls."""
    history = getattr(model, "chat_history", None)
    if (
        previous_length is None
        or not isinstance(history, list)
        or len(history) != previous_length + 1
    ):
        return None
    exchange = history[-1]
    if not isinstance(exchange, list):
        return None
    for message in reversed(exchange):
        if isinstance(message, Mapping):
            role = message.get("role")
            calls = message.get("tool_calls", _MISSING)
        else:
            role = getattr(message, "role", None)
            calls = getattr(message, "tool_calls", _MISSING)
        if role != "assistant" or calls is _MISSING:
            continue
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            return None
        return len(calls)
    return None


def _restore_attribute(instance: Any, name: str, previous: Any) -> None:
    """Restore an instance override or remove it to reveal the original class method."""
    if previous is _MISSING:
        delattr(instance, name)
    else:
        setattr(instance, name, previous)


def _bound_navigation_tools(actions: object, destination: str) -> list[dict[str, Any]]:
    """Narrow only the original navigation argument schema to this execution's target.

    The EMOS model and tool names remain intact. The selected action still passes
    the independent raw-output contract guard before any vendor dispatch.
    """
    if not isinstance(actions, list) or not all(isinstance(action, dict) for action in actions):
        raise IntegrationError("Stage2 model tool declarations are unavailable")
    offered = deepcopy(actions)
    navigation = [action for action in offered if action.get("name") == "nav_to_obj"]
    if len(navigation) != 1:
        raise IntegrationError("Stage2 navigation tool declaration is ambiguous")
    parameters = navigation[0].get("parameters")
    properties = parameters.get("properties") if isinstance(parameters, dict) else None
    target = properties.get("target_obj") if isinstance(properties, dict) else None
    required = parameters.get("required") if isinstance(parameters, dict) else None
    if (
        not isinstance(target, dict)
        or target.get("type") != "string"
        or not isinstance(required, list)
        or "target_obj" not in required
        or (
            "enum" in target
            and (not isinstance(target["enum"], list) or destination not in target["enum"])
        )
    ):
        raise IntegrationError(
            "Stage2 navigation target schema cannot bind the committed destination"
        )
    target["enum"] = [destination]
    return offered


def _bound_relocation_tools(
    actions: object, object_ref: str, destination: str
) -> list[dict[str, Any]]:
    """Bind the original EMOS relocation arguments to exact semantic identities."""
    if not isinstance(actions, list) or not all(isinstance(action, dict) for action in actions):
        raise IntegrationError("Stage2 model tool declarations are unavailable")
    offered = deepcopy(actions)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for action in offered:
        name = action.get("name")
        if isinstance(name, str):
            by_name.setdefault(name, []).append(action)
    required_tools = ("nav_to_obj", "pick", "place")
    if any(len(by_name.get(name, [])) != 1 for name in required_tools):
        raise IntegrationError("Stage2 relocation tool declarations are ambiguous")

    def bind_string_enum(name: str, field: str, value: str) -> None:
        """Restrict one required string argument to the committed semantic identity."""
        action = by_name[name][0]
        parameters = action.get("parameters")
        properties = parameters.get("properties") if isinstance(parameters, dict) else None
        target = properties.get(field) if isinstance(properties, dict) else None
        required = parameters.get("required") if isinstance(parameters, dict) else None
        if (
            not isinstance(target, dict)
            or target.get("type") != "string"
            or not isinstance(required, list)
            or field not in required
            or (
                "enum" in target
                and (not isinstance(target["enum"], list) or value not in target["enum"])
            )
        ):
            raise IntegrationError(f"Stage2 relocation {name} schema cannot bind committed {field}")
        target["enum"] = [value]

    bind_string_enum("nav_to_obj", "target_obj", object_ref)
    bind_string_enum("pick", "target_obj", object_ref)
    bind_string_enum("place", "target_obj", object_ref)
    bind_string_enum("place", "target_location", destination)
    reset = by_name.get("reset_arm", [])
    if reset:
        if len(reset) != 1:
            raise IntegrationError("Stage2 relocation reset_arm declaration is ambiguous")
        parameters = reset[0].get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise IntegrationError("Stage2 relocation reset_arm schema is invalid")
    return offered


def _guard_agent(
    agent: Any,
    contract: Stage2ExecutionContract,
    peer_names: frozenset[str],
    record: Callable[[dict[str, Any]], None],
    feedback: Stage2ExecutionFeedback | None,
    relocation_state: RelocationExecutionState | None,
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
            if not crab_planning and feedback is not None:
                content = feedback.before_call(agent_name, model, content)
            history_length = _raw_history_length(model) if not crab_planning else None
            original_actions = getattr(model, "actions", _MISSING)
            bound_actions = not crab_planning and contract.expected_destination is not None
            if bound_actions:
                if contract.is_relocation:
                    if contract.expected_object is None or contract.expected_destination is None:
                        raise IntegrationError("relocation contract is missing semantic bindings")
                    model.actions = _bound_relocation_tools(
                        original_actions, contract.expected_object, contract.expected_destination
                    )
                else:
                    destination = contract.expected_destination
                    if destination is None:
                        raise IntegrationError("navigation contract is missing destination")
                    model.actions = _bound_navigation_tools(original_actions, destination)
            try:
                result = original_model_chat(content, crab_planning=crab_planning)
            finally:
                if bound_actions:
                    _restore_attribute(model, "actions", original_actions)
            if crab_planning:
                return result
            action = (
                {"name": result[0], "arguments": result[1]}
                if isinstance(result, tuple) and len(result) == 2
                else result
            )
            error = None
            provider_tool_call_count = _latest_tool_call_count(model, history_length)
            try:
                if provider_tool_call_count is None:
                    raise Stage2ContractViolation(
                        agent_name, action, "raw Provider tool-call count is unavailable"
                    )
                if provider_tool_call_count != 1:
                    raise Stage2ContractViolation(
                        agent_name,
                        action,
                        "Provider returned exactly one tool call per execution step; "
                        f"observed {provider_tool_call_count}",
                    )
                if not isinstance(result, tuple) or len(result) != 2:
                    raise Stage2ContractViolation(
                        agent_name, action, "execution model must return an action tuple"
                    )
                contract.validate(agent_name, action, peer_names, relocation_state)
            except Stage2ContractViolation as violation:
                error = violation
            document = {
                "schema_version": "roboguide.stage2-action/v0.1",
                "agent_name": agent_name,
                "contract": contract.as_dict(),
                "selected_action": action,
                "provider_tool_call_count": provider_tool_call_count,
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
            if feedback is not None:
                feedback.admit(agent_name, model, action)
            if relocation_state is not None:
                contract.advance(action, relocation_state)
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
    *,
    feedback: Stage2ExecutionFeedback | None = None,
    relocation_states: Mapping[str, RelocationExecutionState] | None = None,
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
    states = dict(relocation_states or {})
    try:
        for agent in agents:
            contract = contracts[agent.name]
            state = states.get(agent.name) if contract.is_relocation else None
            if contract.is_relocation and state is None:
                state = RelocationExecutionState()
            callbacks.append(_guard_agent(agent, contract, peer_names, record, feedback, state))
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
