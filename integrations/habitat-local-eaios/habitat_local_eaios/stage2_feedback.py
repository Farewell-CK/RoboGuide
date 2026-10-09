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
    from .stage2_contract import Stage2ExecutionContract

_LOG = logging.getLogger(__name__)
_MISSING = object()
FEEDBACK_PROFILE = "observed-local-skill-feedback/v0.1"
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
    deferred_completion: bool = False


class Stage2ExecutionFeedback:
    """Observe original termination calls and supply bounded, attempt-scoped feedback."""

    def __init__(
        self,
        contracts: Mapping[str, Stage2ExecutionContract],
        record: Callable[[dict[str, Any]], None],
        *,
        completion: Callable[[str, str, bool], None] | None = None,
    ) -> None:
        """Freeze contracts while retaining no simulator or control authority."""
        self._contracts = dict(contracts)
        self._record = record
        self._completion = completion
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
            "schema_version": _SCHEMA,
            "profile": FEEDBACK_PROFILE,
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
        return content

    def physical_step(self) -> None:
        """Count existing Gym steps and confirm only an exact retry's deferred terminal fact.

        This runs after the existing Gym call succeeds. Ordinary steps only
        increment counters; an attributed retry boundary emits one receipt.
        No skill, sensor, action, model, or Gym method is called here.
        """
        for agent_name, pending in self._pending.items():
            pending.physical_steps += 1
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

    def install(self, policies: Sequence[Any]) -> None:
        """Observe supported skill instances; unavailable interfaces never imply success."""
        owners: dict[int, str] = {}
        try:
            for policy in policies:
                agent_name = policy._high_level_policy.llm_agent.name
                if agent_name not in self._contracts:
                    continue
                skills = getattr(policy, "_skills", None)
                if not isinstance(skills, dict):
                    continue
                seen: set[int] = set()
                for skill in skills.values():
                    if id(skill) in seen:
                        continue
                    seen.add(id(skill))
                    if id(skill) in owners and owners[id(skill)] != agent_name:
                        raise IntegrationError("Stage2 skill feedback cannot share agent ownership")
                    owners[id(skill)] = agent_name
                    if callable(getattr(skill, "should_terminate", None)) and callable(
                        getattr(skill, "_is_skill_done", None)
                    ):
                        self._restores.append(self._observe_skill(agent_name, skill))
        except BaseException:
            self.close()
            raise

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
            pending.deferred_completion = (
                pending.physical_steps == 0
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
