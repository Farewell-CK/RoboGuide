"""Return observed local skill outcomes to Stage2 without editing vendor policy.

The vendor model writes a synthetic ``Success`` receipt before a skill runs.
This execution-scoped bridge replaces that receipt with attributed observations;
it never selects an action, retries a skill, or declares benchmark success.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .model import IntegrationError

if TYPE_CHECKING:
    from .relocation_completion import RelocationCompletionBinding
    from .stage2_contract import Stage2ExecutionContract

_LOG = logging.getLogger(__name__)
_MISSING = object()
FEEDBACK_PROFILE = "observed-local-skill-feedback/v0.1"
BOUND_FEEDBACK_PROFILE = "observed-local-skill-feedback/v0.4"
_SCHEMA = "roboguide.stage2-execution-feedback/v0.1"
_COMPLETED_PREFIX = "You have completed your previous action. "


def _field(value: Any, name: str) -> Any:
    """Read either a raw JSON message or the original SDK message object."""
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _restore(instance: Any, name: str, previous: Any) -> None:
    """Remove only this instance's override, retaining the original class method."""
    if previous is _MISSING:
        delattr(instance, name)
    else:
        setattr(instance, name, previous)


def _scalar(value: Any, index: int = 0) -> Any:
    """Read one CPU policy value without accepting arbitrary truthy containers."""
    selected = value[index]
    item = getattr(selected, "item", None)
    return item() if callable(item) else selected


def _boolean(value: Any, index: int = 0) -> bool | None:
    """Keep missing or non-boolean termination evidence unknown."""
    try:
        result = _scalar(value, index)
        return result if isinstance(result, bool) else None
    except Exception:  # noqa: BLE001 - failed reads cannot invent completion
        return None


def _budget(skill: Any, index: int) -> dict[str, Any]:
    """Read the original counter and bound without renewing either value."""
    try:
        current = _scalar(skill._cur_skill_step, index)
        maximum = skill._max_skill_steps
        if (
            isinstance(current, bool)
            or not isinstance(current, (int, float))
            or not math.isfinite(current)
            or current < 0
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
        ):
            raise ValueError("skill budget is not readable")
        return {
            "current_skill_steps": current,
            "max_skill_steps": maximum,
            "over_max_len": maximum > 0 and current >= maximum,
        }
    except Exception:  # noqa: BLE001 - unexposed bookkeeping remains unknown
        return {"current_skill_steps": None, "max_skill_steps": None, "over_max_len": None}


def _receipt(model: Any, action: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the receipt to exactly the selected raw call before vendor mutation."""
    history = getattr(model, "chat_history", None)
    if not isinstance(history, list) or not history or not isinstance(history[-1], list):
        raise IntegrationError("Stage2 execution feedback requires raw tool history")
    exchange = history[-1]
    assistants = [message for message in exchange if _field(message, "role") == "assistant"]
    if len(assistants) != 1:
        raise IntegrationError("Stage2 execution feedback requires one raw assistant response")
    calls = _field(assistants[0], "tool_calls")
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)) or len(calls) != 1:
        raise IntegrationError("Stage2 execution feedback requires exactly one raw tool call")
    call = calls[0]
    identity, function = _field(call, "id"), _field(call, "function")
    try:
        arguments = json.loads(_field(function, "arguments"))
    except (TypeError, ValueError) as error:
        raise IntegrationError(
            "Stage2 execution feedback cannot bind raw tool arguments"
        ) from error
    if (
        not isinstance(identity, str)
        or not identity
        or len(identity) > 1024
        or _field(function, "name") != action["name"]
        or arguments != action["arguments"]
    ):
        raise IntegrationError("Stage2 execution feedback raw call does not match selected action")
    receipts = [
        message
        for message in exchange
        if isinstance(message, dict)
        and message.get("role") == "tool"
        and message.get("tool_call_id") == identity
        and message.get("name") == action["name"]
    ]
    if len(receipts) != 1 or not isinstance(receipts[0].get("content"), str):
        raise IntegrationError("Stage2 execution feedback requires an exact tool receipt")
    return receipts[0]


@dataclass
class _PendingAction:
    """Retain only the latest selected action for one canonical execution."""

    model: Any
    receipt: dict[str, Any]
    document: dict[str, Any]
    action: dict[str, Any]
    physical_steps: int = 0
    place_physical_steps: int = 0
    deferred_completion: bool = False
    reset_action_call: tuple[str, int] | None = None
    deferred_reset_exit: bool = False


class Stage2ExecutionFeedback:
    """Observe original termination calls and supply bounded, attempt-scoped feedback."""

    def __init__(
        self,
        contracts: Mapping[str, Stage2ExecutionContract],
        record: Callable[[dict[str, Any]], None],
        *,
        completion: Callable[[str, str, bool], None] | None = None,
        completion_binding: RelocationCompletionBinding | None = None,
        peer_contracts: Callable[[], Mapping[str, Stage2ExecutionContract]] | None = None,
    ) -> None:
        """Freeze own contracts, optionally reading a deployment-owned active-peer projection."""
        self._contracts = dict(contracts)
        self._peer_contracts = peer_contracts
        self._record = record
        self._completion = completion
        self._completion_binding = completion_binding
        self._pending: dict[str, _PendingAction] = {}
        self._sequences: dict[str, int] = {}
        self._operation_completed: dict[str, bool] = {}
        self._restores: list[Callable[[], None]] = []

    def _emit(self, event: str, document: dict[str, Any]) -> None:
        """Keep archival failures separate from the actual model input and physics."""
        try:
            self._record(dict(document, event=event))
        except Exception:  # noqa: BLE001 - feedback remains truthful if storage fails
            _LOG.exception("Stage2 execution feedback evidence unavailable")

    @staticmethod
    def _write_observed_receipt(pending: _PendingAction) -> None:
        """Keep observation serialization faults from replacing physical results or errors."""
        try:
            pending.receipt["content"] = json.dumps(
                pending.document, allow_nan=False, sort_keys=True
            )
        except Exception:  # noqa: BLE001 - terminal observations must remain fail-soft
            pending.document = dict(pending.document, receipt_update_status="unavailable")
            _LOG.exception("Stage2 observed receipt serialization unavailable")

    def admit(self, agent_name: str, model: Any, action: Mapping[str, Any]) -> None:
        """Replace an optimistic receipt only after the independent guard admits the call."""
        receipt = _receipt(model, action)
        previous = self._pending.get(agent_name)
        sequence = self._sequences.get(agent_name, 0) + 1
        self._sequences[agent_name] = sequence
        document = {
            "schema_version": (
                "roboguide.stage2-execution-feedback/v0.4" if self._completion_binding else _SCHEMA
            ),
            "profile": BOUND_FEEDBACK_PROFILE if self._completion_binding else FEEDBACK_PROFILE,
            "agent_name": agent_name,
            "contract": self._contracts[agent_name].as_dict(),
            "action_sequence": sequence,
            "tool_call_id": receipt["tool_call_id"],
            "tool_name": action["name"],
            "status": "accepted-awaiting-observation",
            "local_skill_completed": None,
            "benchmark_goal_satisfied": None,
            "source": "before-crab-agent-dispatch",
        }
        exact_action = deepcopy(dict(action))
        if (
            previous is not None
            and previous.model is model
            and previous.action == exact_action
            and previous.physical_steps > 0
            and previous.document["contract"] == document["contract"]
            and previous.document["status"] in {"skill-budget-exhausted", "high-level-interrupted"}
            and previous.document.get("local_skill_completed") is False
            and previous.document.get("termination", {}).get("bad_terminate") is False
        ):
            document["retry_of_tool_call_id"] = previous.document["tool_call_id"]
            document["retry_of_action_sequence"] = previous.document["action_sequence"]
            document["prior_call_physical_steps"] = previous.physical_steps
        self._pending[agent_name] = _PendingAction(model, receipt, document, exact_action)
        if self._completion_binding is not None:
            document["manipulation_observation"] = self._observe_manipulation(
                agent_name, "before-crab-agent-dispatch"
            )
            if action["name"] == "send_request":
                document["peer_request"] = {
                    "target_agent": action["arguments"]["target_agent"],
                    "delivery_status": "unobserved",
                    "peer_action_completed": None,
                    "operation_delegation_supported": False,
                    "wait_completion_is_acknowledgement": False,
                }
        if self._completion_binding is not None and action["name"] == "place":
            self._completion_binding.bind_call(agent_name, receipt["tool_call_id"], sequence)
            document["place_binding"] = self._completion_binding.observe(agent_name)
        original_content = receipt["content"]
        receipt["content"] = json.dumps(document, allow_nan=False, sort_keys=True)
        self._emit("admitted", dict(document, original_vendor_receipt=original_content))

    def before_call(self, agent_name: str, model: Any, content: str) -> str:
        """Deliver observed feedback without preserving the vendor's false completion claim."""
        pending = self._pending.get(agent_name)
        if pending is not None and pending.model is model:
            if pending.document["status"] == "accepted-awaiting-observation":
                pending.document = dict(
                    pending.document,
                    status="termination-result-unavailable",
                    source="next-model-call-without-observed-skill-termination",
                )
            pending.document = dict(
                pending.document, physical_steps_since_call=pending.physical_steps
            )
            if self._completion_binding is not None:
                pending.document["current_manipulation_observation"] = self._observe_manipulation(
                    agent_name, "before-next-model-call"
                )
            # Reassert the attributed receipt if vendor logging copied or edited its content.
            pending.receipt["content"] = json.dumps(
                pending.document, allow_nan=False, sort_keys=True
            )
            self._emit("model-input-prepared", pending.document)
        if content.startswith(_COMPLETED_PREFIX):
            content = (
                "Use the recorded local execution feedback for the previous action. "
                + content[len(_COMPLETED_PREFIX) :]
            )
        if pending is not None and pending.model is model:
            # An explicit current observation remains visible with any vendor history window.
            content += "\n\nLocal execution feedback:\n" + json.dumps(
                pending.document, allow_nan=False, sort_keys=True
            )
        if self._completion_binding is not None or self.has_live_peer_contracts:
            content += "\n\nCommitted peer execution scope:\n" + json.dumps(
                self.peer_execution_scope(agent_name), allow_nan=False, sort_keys=True
            )
        return content

    def _observe_manipulation(self, agent_name: str, timing: str) -> dict[str, Any]:
        """Keep sparse observation faults and oversized data out of model/physical decisions."""
        try:
            assert self._completion_binding is not None
            record = dict(
                self._completion_binding.observe_manipulation(agent_name), source_timing=timing
            )
            encoded = json.dumps(record, allow_nan=False, sort_keys=True)
            if len(encoded.encode("utf-8")) > 8192:
                raise ValueError("manipulation observation exceeds its byte bound")
            result: dict[str, Any] = json.loads(encoded)
            return result
        except Exception as error:  # noqa: BLE001 - optional feedback cannot invent sensor values
            return {
                "status": "unavailable",
                "reason": type(error).__name__,
                "source_timing": timing,
            }

    def request_peer_names(self, agent_name: str) -> frozenset[str]:
        """Expose model-bearing peers only; completion-idle cannot receive original messages."""
        return frozenset(
            name
            for name, contract in self._peer_contract_snapshot().items()
            if name != agent_name
            and contract.operation != "unassigned"
            and not self.operation_completed(name)
        )

    def _peer_contract_snapshot(self) -> Mapping[str, Stage2ExecutionContract]:
        """Read peer metadata separately from immutable owner contracts and skill receipts."""
        return dict(self._peer_contracts() if self._peer_contracts is not None else self._contracts)

    @property
    def has_live_peer_contracts(self) -> bool:
        """Identify explicit dynamic endpoint bindings without changing legacy peer rules."""
        return self._peer_contracts is not None

    def peer_execution_scope(self, agent_name: str) -> dict[str, Any]:
        """Describe existing local commitments without transferring Control's ownership."""
        eligible = self.request_peer_names(agent_name)
        return {
            "schema_version": "roboguide.stage2-peer-execution-scope/v0.1",
            "agent_name": agent_name,
            "operation_delegation_supported": False,
            "request_effect": "message-only; peers retain their committed canonical operations",
            "wait_completion_is_acknowledgement": False,
            "peers": [
                {
                    "agent_name": name,
                    "accepting_model_messages": name in eligible,
                    "model_status": (
                        "unassigned"
                        if contract.operation == "unassigned"
                        else "completed-idle"
                        if self.operation_completed(name)
                        else "assigned"
                    ),
                    "canonical_contract": dict(contract.as_dict(), attempt_id=contract.attempt_id),
                }
                for name, contract in sorted(self._peer_contract_snapshot().items())
                if name != agent_name
            ],
        }

    def physical_step(self) -> None:
        """Count existing Gym steps and confirm only an exact retry's deferred terminal fact.

        This runs after the existing Gym call succeeds. Ordinary steps only
        increment counters; an attributed retry boundary emits one receipt.
        No skill, sensor, action, model, or Gym method is called here.
        """
        for agent_name, pending in self._pending.items():
            pending.physical_steps += 1
            if self._completion_binding is not None and pending.document["tool_name"] == "place":
                self._post_step_place(agent_name, pending)
                continue
            reset_call, pending.reset_action_call = pending.reset_action_call, None
            if pending.deferred_reset_exit:
                pending.deferred_reset_exit = False
                if reset_call == (
                    pending.document["tool_call_id"],
                    pending.document["action_sequence"],
                ):
                    pending.document = dict(
                        pending.document,
                        status="skill-exited-completion-unconfirmed",
                        local_skill_completed=None,
                        source="completed-gym-step-after-observed-reset-exit",
                        source_timing="after-completed-gym-step-with-prior-policy-input-evidence",
                        physical_steps_since_call=pending.physical_steps,
                    )
                    self._write_observed_receipt(pending)
                    self._emit("reset-exit-confirmed", pending.document)
                    self._notify_completion(agent_name, pending)
                    continue
            if not pending.deferred_completion:
                continue
            pending.deferred_completion = False
            pending.document = dict(
                pending.document,
                status=(
                    "wait-finished"
                    if pending.document["tool_name"] in {"wait", "send_request"}
                    else "local-skill-completed"
                ),
                local_skill_completed=True,
                source="completed-gym-step-after-exact-retry-terminal",
                source_timing="after-completed-gym-step-with-prior-policy-input-evidence",
                physical_steps_since_call=pending.physical_steps,
            )
            self._write_observed_receipt(pending)
            self._emit("retry-terminal-confirmed", pending.document)
            self._notify_completion(agent_name, pending)

    def action_failure(self, agent_name: str, action: Mapping[str, Any]) -> str | None:
        """Reject contradictory grasp bindings before dispatch under the explicit new profile."""
        if self._completion_binding is None or not self._contracts[agent_name].is_relocation:
            return None
        observation = self._completion_binding.observe(agent_name)
        if observation["status"] == "contradictory":
            return "actual grasp does not match the canonical relocation object"
        if action["name"] in {"place", "reset_arm"} and observation["status"] != "observed":
            return "actual relocation object/grasp observation is unavailable"
        if action["name"] == "place" and observation["object_released"] is True:
            return "place requires an actually held canonical object"
        return None

    @property
    def has_bound_relocation_completion(self) -> bool:
        """Identify the explicit new local profile without changing historical default behavior."""
        return self._completion_binding is not None

    def _post_step_place(self, agent_name: str, pending: _PendingAction) -> None:
        """Confirm actual release after the existing step, retaining earlier termination causes."""
        binding = self._completion_binding
        if binding is None or pending.document.get("local_skill_completed") is True:
            return
        if not binding.consume_action(
            agent_name, pending.document["tool_call_id"], pending.document["action_sequence"]
        ):
            return
        pending.place_physical_steps += 1
        observation = binding.observe(agent_name)
        policy_input = binding.last_check(agent_name)
        if policy_input is not None and (
            policy_input.get("tool_call_id") != pending.document["tool_call_id"]
            or policy_input.get("action_sequence") != pending.document["action_sequence"]
        ):
            policy_input = None
        pending.document = dict(
            pending.document,
            place_binding=observation,
            place_policy_input={
                "source_timing": "before-completed-gym-step",
                "observation": policy_input,
            },
            place_physical_steps=pending.place_physical_steps,
        )
        if (
            observation.get("tool_call_id") != pending.document["tool_call_id"]
            or observation.get("action_sequence") != pending.document["action_sequence"]
            or observation["qualified_local_completion"] is not True
            or pending.document.get("termination", {}).get("bad_terminate") is True
        ):
            return
        pending.deferred_completion = False
        pending.document = dict(
            pending.document,
            status="local-skill-completed",
            local_skill_completed=True,
            source="post-step-bound-object-release",
            source_timing="after-completed-gym-step",
            physical_steps_since_call=pending.physical_steps,
        )
        self._write_observed_receipt(pending)
        self._emit("bound-place-completed", pending.document)
        self._notify_completion(agent_name, pending)

    def install(self, policies: Sequence[Any]) -> None:
        """Observe supported skill instances; unavailable interfaces never imply success."""
        owners: dict[int, str] = {}
        try:
            if self._completion_binding is not None:
                self._completion_binding.install(policies)
            for policy in policies:
                agent_name = policy._high_level_policy.llm_agent.name
                if agent_name not in self._contracts:
                    continue
                skills = getattr(policy, "_skills", None)
                if not isinstance(skills, dict):
                    continue
                seen: set[int] = set()
                names = getattr(policy, "_idx_to_name", {})
                for index, skill in skills.items():
                    if id(skill) in seen:
                        continue
                    seen.add(id(skill))
                    if id(skill) in owners and owners[id(skill)] != agent_name:
                        raise IntegrationError("Stage2 skill feedback cannot share agent ownership")
                    owners[id(skill)] = agent_name
                    skill_name = (
                        names.get(index)
                        if isinstance(names, Mapping)
                        else names[index]
                        if isinstance(names, (list, tuple)) and 0 <= index < len(names)
                        else None
                    )
                    if (
                        self._completion_binding is not None
                        and skill_name == "reset_arm"
                        and callable(getattr(skill, "_internal_act", None))
                    ):
                        self._restores.append(self._observe_reset_action(agent_name, skill))
                    if callable(getattr(skill, "should_terminate", None)) and callable(
                        getattr(skill, "_is_skill_done", None)
                    ):
                        self._restores.append(self._observe_skill(agent_name, skill))
        except BaseException:
            self.close()
            raise

    def _observe_reset_action(self, agent_name: str, skill: Any) -> Callable[[], None]:
        """Witness the assigned original reset action once without changing its return value."""
        original = skill._internal_act
        previous = vars(skill).get("_internal_act", _MISSING)

        def observed(*args: Any, **kwargs: Any) -> Any:
            """Bind a generated reset to the pending exact call, never a peer or earlier call."""
            pending = self._pending.get(agent_name)
            result = original(*args, **kwargs)
            if (
                pending is not None
                and pending is self._pending.get(agent_name)
                and pending.document["tool_name"] == "reset_arm"
            ):
                pending.reset_action_call = (
                    pending.document["tool_call_id"],
                    pending.document["action_sequence"],
                )
            return result

        skill._internal_act = observed

        def restore() -> None:
            """Remove only this action witness after normal, exceptional or cancelled exit."""
            _restore(skill, "_internal_act", previous)

        return restore

    def _observe_skill(self, agent_name: str, skill: Any) -> Callable[[], None]:
        """Tap one existing termination decision, calling each vendor method exactly once."""
        original = skill.should_terminate
        previous = vars(skill).get("should_terminate", _MISSING)

        def observed(*args: Any, **kwargs: Any) -> Any:
            """Retain the actual base result separately from budget and high-level termination."""
            base_count = 0
            base_done: bool | None = None
            check = skill._is_skill_done
            previous_check = vars(skill).get("_is_skill_done", _MISSING)

            def observed_check(*check_args: Any, **check_kwargs: Any) -> Any:
                """Capture the one original completion calculation without repeating it."""
                nonlocal base_count, base_done
                result = check(*check_args, **check_kwargs)
                base_count += 1
                base_done = _boolean(result)
                return result

            skill._is_skill_done = observed_check
            try:
                result = original(*args, **kwargs)
            except BaseException as error:
                pending = self._pending.get(agent_name)
                if pending is not None:
                    pending.deferred_completion = False
                    pending.document = dict(
                        pending.document,
                        status="original-skill-exception",
                        source="original-skill-should-terminate",
                        error_type=type(error).__name__,
                    )
                    self._write_observed_receipt(pending)
                    self._emit("skill-exception", pending.document)
                raise
            finally:
                _restore(skill, "_is_skill_done", previous_check)
            try:
                self._termination(agent_name, skill, kwargs, result, base_done, base_count)
            except Exception:  # noqa: BLE001 - observation cannot change the returned decision
                _LOG.exception("Stage2 termination feedback unavailable")
            return result

        skill.should_terminate = observed

        def restore() -> None:
            """Restore the skill instance after normal, exceptional, or cancelled exit."""
            _restore(skill, "should_terminate", previous)

        return restore

    def _termination(
        self,
        agent_name: str,
        skill: Any,
        inputs: dict[str, Any],
        result: Any,
        base_done: bool | None,
        base_count: int,
    ) -> None:
        """Attribute an actual skill exit, retaining simultaneous causes and unknowns."""
        pending = self._pending.get(agent_name)
        names, indices = inputs.get("skill_name"), inputs.get("batch_idx")
        if pending is None or names is None or indices != [0] or len(names) != 1:
            return
        expected_skill = {
            "nav_to_obj": "nav_to_obj",
            "pick": "pick",
            "place": "place",
            "reset_arm": "reset_arm",
            "wait": "wait",
            "send_request": "wait",
        }.get(str(pending.document["tool_name"]))
        if expected_skill is None or names[0] != expected_skill:
            return
        pending.deferred_completion = False
        pending.deferred_reset_exit = False
        if not isinstance(result, tuple) or len(result) != 3:
            return
        returned_control = _boolean(result[0])
        bad_terminate = _boolean(result[1])
        if returned_control is not True and bad_terminate is not True:
            return
        budget = _budget(skill, 0)
        high_level = _boolean(inputs.get("hl_wants_skill_term"))
        base_done = base_done if base_count == 1 else None
        if base_done is True and bad_terminate is False:
            pending.deferred_reset_exit = (
                self._completion_binding is not None
                and expected_skill == "reset_arm"
                and pending.physical_steps == 0
                and returned_control is True
                and high_level is False
                and budget["current_skill_steps"] == 1
                and budget["over_max_len"] is False
                and pending.reset_action_call
                == (pending.document["tool_call_id"], pending.document["action_sequence"])
            )
            pending.deferred_completion = (
                not pending.deferred_reset_exit
                and pending.physical_steps == 0
                and "retry_of_tool_call_id" in pending.document
                and returned_control is True
                and budget["current_skill_steps"] == 1
                and budget["over_max_len"] is False
            )
            status = (
                "completion-evidence-unavailable"
                if pending.physical_steps == 0
                else "local-skill-completed"
                if expected_skill != "wait"
                else "wait-finished"
            )
        elif base_done is False and budget["over_max_len"] is True:
            status = "skill-budget-exhausted"
        elif base_done is False and high_level is True:
            status = "high-level-interrupted"
        else:
            status = "termination-result-unavailable"
        pending.document = dict(
            pending.document,
            status=status,
            local_skill_completed=(
                True
                if status in {"local-skill-completed", "wait-finished"}
                else False
                if base_done is False
                else None
            ),
            source="original-skill-should-terminate",
            source_timing="policy-input-before-following-gym-step",
            physical_steps_since_call=pending.physical_steps,
            termination={
                "base_is_skill_done": base_done,
                "returned_control": returned_control,
                "bad_terminate": bad_terminate,
                "high_level_requested_termination": high_level,
                **budget,
            },
        )
        if self._completion_binding is not None:
            pending.document["manipulation_observation"] = self._observe_manipulation(
                agent_name, "policy-input-before-following-gym-step"
            )
        if self._completion_binding is not None and expected_skill == "place":
            pending.document["place_binding"] = self._completion_binding.last_check(agent_name)
            # A fresh released-object read is required even when the original geometric
            # result is true. Unknown observations must not clear a guard phase.
            observation = pending.document["place_binding"]
            if observation is None or observation.get("qualified_local_completion") is None:
                pending.document.update(
                    status="termination-result-unavailable", local_skill_completed=None
                )
            elif base_done is True and pending.place_physical_steps == 0:
                pending.document.update(
                    status="completion-evidence-unavailable", local_skill_completed=None
                )
        self._write_observed_receipt(pending)
        self._emit("skill-terminated", pending.document)
        self._notify_completion(agent_name, pending)

    def _notify_completion(self, agent_name: str, pending: _PendingAction) -> None:
        """Advance local guard state only from definite original skill outcome evidence."""
        status = pending.document["status"]
        completed = status in {"local-skill-completed", "wait-finished"}
        if completed and pending.document["tool_name"] == "place":
            contract = self._contracts.get(agent_name)
            if contract is not None and contract.is_relocation:
                self._operation_completed[agent_name] = True
        if self._completion is not None and status in {
            "local-skill-completed",
            "wait-finished",
            "skill-budget-exhausted",
            "high-level-interrupted",
            "skill-exited-completion-unconfirmed",
        }:
            try:
                self._completion(agent_name, str(pending.document["tool_name"]), completed)
            except Exception:  # noqa: BLE001 - feedback cannot alter the vendor decision
                _LOG.exception("Stage2 relocation state update unavailable")

    def operation_completed(self, agent_name: str) -> bool:
        """Report a definite relocation completion observed from the original place skill."""
        return self._operation_completed.get(agent_name, False)

    def local_completion(self, agent_name: str) -> None:
        """Retain the loop's existing post-step terminal measure, never a PDDL assertion.

        The original budget decision can precede the final Gym step. If that
        step actually reaches the local terminal measure, the existing loop's
        observation supersedes the earlier incomplete policy-input evidence.
        """
        pending = self._pending.get(agent_name)
        if pending is None or pending.document["tool_name"] != "nav_to_obj":
            return
        pending.deferred_completion = False
        pending.document = dict(
            pending.document,
            status="local-skill-completed",
            local_skill_completed=True,
            source="post-step-oracle-nav-terminal-measure",
            source_timing="after-completed-gym-step",
            physical_steps_since_call=pending.physical_steps,
        )
        self._write_observed_receipt(pending)
        self._emit("local-terminal-measure", pending.document)
        if self._completion is not None:
            try:
                self._completion(agent_name, "nav_to_obj", True)
            except Exception:  # noqa: BLE001 - feedback cannot alter the vendor decision
                _LOG.exception("Stage2 navigation state update unavailable")

    def close(self) -> None:
        """Remove hooks and close unresolved receipts without inventing an execution outcome."""
        for restore in reversed(self._restores):
            restore()
        self._restores.clear()
        if self._completion_binding is not None:
            self._completion_binding.close()
        for pending in self._pending.values():
            if pending.document["status"] == "accepted-awaiting-observation":
                pending.document = dict(
                    pending.document,
                    status="segment-ended-without-observed-skill-termination",
                    source="execution-segment-exit",
                )
            pending.document = dict(
                pending.document, physical_steps_since_call=pending.physical_steps
            )
            self._write_observed_receipt(pending)
            self._emit("segment-ended", pending.document)
        self._pending.clear()
