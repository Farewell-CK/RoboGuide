"""Attempt-scoped original Stage2 policies and model-free unassigned endpoint bindings."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .idle_endpoint import PassiveIdleAgent, _original_wait_skill_type
from .model import CanonicalInvocation, IntegrationError
from .relocation_completion import RelocationCompletionBinding
from .stage2_contract import (
    RelocationExecutionState,
    Stage2ActionAudit,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from .stage2_feedback import Stage2ExecutionFeedback

_LOG = logging.getLogger(__name__)


class LivePolicyBindings:
    """Keep each endpoint's original model and feedback isolated across Task reuse."""

    def __init__(
        self, runtime: Any, actor: Any, assignment: dict[str, Any], step: Callable[[], int]
    ) -> None:
        """Prevalidate original wait skills, then replace only currently unassigned agents."""
        self.runtime, self.actor, self.assignment, self.step = runtime, actor, assignment, step
        self.originals: dict[int, Any] = {}
        self.restores: dict[int, Callable[[], None]] = {}
        self.feedbacks: dict[int, Stage2ExecutionFeedback] = {}
        self.contracts: dict[int, Stage2ExecutionContract] = {}
        wait_type = _original_wait_skill_type()
        for agent, policy in enumerate(actor._active_policies):
            original = policy._high_level_policy.llm_agent
            wait = policy._name_to_idx.get("wait")
            if (
                original.name != f"agent_{agent}"
                or type(policy._skills.get(wait)) is not wait_type
                or policy._high_level_policy._skill_name_to_idx.get("wait") != wait
                or not callable(getattr(policy._cur_call_high_level, "fill_", None))
            ):
                raise IntegrationError(
                    "live profile requires exact original policies and wait skills"
                )
            self.originals[agent] = original
        self.audit = Stage2ActionAudit(runtime._evidence_dir())
        self.feedback_audit = Stage2ActionAudit(runtime._evidence_dir(), execution_feedback=True)
        for agent in self.originals:
            self.idle(agent)

    def record(self, document: dict[str, Any]) -> None:
        """Retain action identity and the last completed physical step in bounded evidence."""
        self.audit.record(
            dict(document, completed_simulator_steps=self.step(), observed_unix=time.time())
        )

    def record_feedback(self, document: dict[str, Any]) -> None:
        """Archive original skill observations separately from action admission."""
        self.feedback_audit.record(
            dict(document, completed_simulator_steps=self.step(), observed_unix=time.time())
        )

    def peer_contracts(self) -> dict[str, Stage2ExecutionContract]:
        """Project current model-bearing attempts without changing any endpoint's own contract."""
        return {
            self.originals[agent].name: contract
            for agent, contract in self.contracts.items()
            if not self.feedbacks[agent].operation_completed(self.originals[agent].name)
        }

    def idle(self, agent: int) -> None:
        """Use original wait after local completion or before the first assignment."""
        policy = self.actor._active_policies[agent]
        passive = PassiveIdleAgent(f"agent_{agent}")
        passive.init_agent("", "", "Nothing to do")
        previous = self.assignment[f"agent_{agent}"]
        self.assignment[f"agent_{agent}"] = type(previous)(
            robot_id=previous.robot_id,
            robot_type=previous.robot_type,
            task_description="",
            subtask_description="Nothing to do",
            chat_history=[],
        )
        policy._high_level_policy.llm_agent = passive
        policy._cur_call_high_level.fill_(True)

    def bind(self, agent: int, invocation: CanonicalInvocation, arguments: Any) -> None:
        """Initialize only the newly committed endpoint, retaining every peer's skill state."""
        policy = self.actor._active_policies[agent]
        original = self.originals[agent]
        policy._high_level_policy.llm_agent = original
        self.assignment[f"agent_{agent}"] = arguments
        contract = Stage2ExecutionContract.for_invocation(invocation)
        contracts = {original.name: contract}
        states = (
            {
                original.name: RelocationExecutionState(
                    allow_holding_reset=bool(
                        getattr(self.runtime._config, "relocation_completion_binding", False)
                    )
                )
            }
            if contract.is_relocation
            else {}
        )
        completion = (
            RelocationCompletionBinding(self.runtime._habitat_env, contracts)
            if contract.is_relocation
            and getattr(self.runtime._config, "relocation_completion_binding", False)
            else None
        )

        def observed(name: str, action: str, succeeded: bool) -> None:
            """Advance relocation phases only from the original observed skill outcome."""
            state = states.get(name)
            if state is not None:
                state.complete(action, succeeded)

        feedback = Stage2ExecutionFeedback(
            contracts,
            self.record_feedback,
            completion=observed,
            completion_binding=completion,
            peer_contracts=self.peer_contracts,
        )
        feedback.install([policy])
        try:
            guard = install_stage2_contract_guard(
                [original], contracts, self.record, feedback=feedback, relocation_states=states
            )
        except BaseException:
            try:
                feedback.close()
            except Exception:
                _LOG.exception("live feedback rollback unavailable")
            raise
        # Native MultiLLMPolicy treats an all-zero previous action as a new
        # episode and reinitializes every agent. This deployment keeps one reset
        # and initializes each exact Control attempt once, retaining active peers.
        prior_init = vars(original).get("init_agent")
        try:
            original.init_agent(
                robot_type=arguments.robot_type,
                task_description=arguments.task_description,
                subtask_description=arguments.subtask_description,
                chat_history=[],
                enable_logging=True,
                logging_file=str(
                    self.runtime._evidence_dir()
                    / f"stage2-agent-{agent}-{invocation.request_key()[:16]}.json"
                ),
            )
        except BaseException:
            for rollback in (guard, feedback.close):
                try:
                    rollback()
                except Exception:
                    _LOG.exception("live initialization rollback unavailable")
            raise

        def existing_attempt_init(
            robot_type: str,
            task_description: str,
            subtask_description: str,
            chat_history: Any = None,
            enable_logging: bool = False,
            logging_file: str = "",
        ) -> None:
            """Retain this initialized attempt when the native zero-action heuristic repeats.

            No changed assignment, model input or new Task is accepted here. A
            later Control dispatch restores the original method and initializes
            its own attempt. This adds no Provider call and changes no action.
            """
            del enable_logging, logging_file
            if (
                robot_type != arguments.robot_type
                or task_description != arguments.task_description
                or subtask_description != arguments.subtask_description
                or chat_history not in (None, [])
            ):
                raise IntegrationError("native reinitialization disagrees with the bound attempt")
            original.initialized = True

        original.init_agent = existing_attempt_init
        self.feedbacks[agent] = feedback
        self.runtime._stage2_accounting_agents[f"agent_{agent}"] = original

        def restore() -> None:
            """Detach only this attempt's hooks; peers keep their model and termination taps."""
            try:
                if prior_init is None:
                    vars(original).pop("init_agent", None)
                else:
                    original.init_agent = prior_init
                guard()
            finally:
                feedback.close()

        self.restores[agent] = restore
        policy._cur_call_high_level.fill_(True)
        self.contracts[agent] = contract

    def finish(self, agent: int) -> None:
        """Close one observed attempt and make the released local endpoint passive."""
        self.contracts.pop(agent, None)
        restore = self.restores.pop(agent, None)
        if restore is not None:
            restore()
        self.feedbacks.pop(agent, None)
        self.idle(agent)

    def close(self) -> None:
        """Best-effort hook/evidence cleanup preserves the original execution exception."""
        for agent in list(self.restores):
            try:
                self.finish(agent)
            except Exception:
                _LOG.exception("live attempt hook cleanup failed")
        for agent, original in self.originals.items():
            self.actor._active_policies[agent]._high_level_policy.llm_agent = original
        for audit in (self.audit, self.feedback_audit):
            try:
                audit.close()
            except Exception:
                _LOG.exception("live action evidence flush unavailable")
        try:
            self.runtime._retain_action_audit(self.audit.summary)
            self.runtime._feedback_audit_summary = self.feedback_audit.summary
        except Exception:
            _LOG.exception("live action evidence summary unavailable")
