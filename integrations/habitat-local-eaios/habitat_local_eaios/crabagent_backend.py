"""EMOS CrabAgent-backed Local How backend for the Habitat bridge.

Deployment-selected alternative to the direct Oracle backend.  The bridge
imports and drives the original EMOS ``CrabAgent`` (never a copy) with the
canonical invocation's semantic fields, feeds it the same scene description
EMOS itself generates, and dispatches the selected skill through the same
Oracle navigation action the EMOS skill stack uses.  Node and Core never
observe CrabAgent, skill names, PDDL entities, or Habitat action names.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backend import (
    HabitatBackendConfig,
    HabitatMobilityBackend,
    LocalExecutionOutcome,
    _observation_true,
)
from .model import CanonicalMobilityInvocation, IntegrationError

# Natural-objective is the Controlled-protocol main candidate; entity-grounded
# is the ablation path.  Selection is deployment-owned Local How configuration.
SUBTASK_MODES = ("natural-objective", "entity-grounded")
_KNOWN_UNSUPPORTED_SKILLS = frozenset({"send_request", "pick", "place", "reset_arm", "get_agents"})
_MAX_CONSECUTIVE_INVALID_OUTPUTS = 5
_PROMPT_TEMPLATE_HASH_ALGORITHM = "sha256"

# The two prompt templates below are copied verbatim from
# `habitat-baselines/rl/hrl/hl/llm_policy.py::LLMHighLevelPolicy.get_next_skill`
# so the local agent receives exactly the observations EMOS feeds it.  Their
# hashes are recorded with the run evidence for model/prompt freeze.
START_ACTION_PROMPT = (
    "You are just starting the task to take actions. "
    'Here is the current environment description: """\n{scene_description}\n"""\n\n'
    "Based on the task and environment, generate the most appropriate next action. \n"
    "Make sure that each action strictly adheres to the tool call's parameter list for "
    "that specific action. "
    "Before providing the action, validate that both the action and its parameters "
    "conform exactly to the defined structure. "
    "Ensure that all required parameters are included and correctly formatted."
)
STEP_ACTION_PROMPT = (
    "You have completed your previous action. "
    "Based on the task, generate the most appropriate next action. \n"
    "Make sure that each action strictly adheres to the tool call's parameter list for "
    "that specific action. "
    "Before providing the action, validate that both the action and its parameters "
    "conform exactly to the defined structure. "
    "Ensure that all required parameters are included and correctly formatted."
)


@dataclass(frozen=True)
class CrabAgentBackendConfig(HabitatBackendConfig):
    """Deployment configuration extending the direct backend with CrabAgent choices."""

    subtask_mode: str = "natural-objective"
    robot_type: str = "SpotRobot"
    evidence_dir: Path = Path("crabagent-evidence")

    def __post_init__(self) -> None:
        """Reject deployment configurations that would blur the subtask boundary."""
        if self.subtask_mode not in SUBTASK_MODES:
            raise IntegrationError(
                f"subtask mode {self.subtask_mode!r} must be one of {SUBTASK_MODES}"
            )
        if not self.robot_type.strip():
            raise IntegrationError("robot type must be a non-empty string")


class CrabAgentMobilityBackend(HabitatMobilityBackend):
    """Drives the original EMOS CrabAgent as the local execution policy."""

    def __init__(self, config: CrabAgentBackendConfig) -> None:
        """Retain immutable deployment choices; initialization happens on the worker."""
        super().__init__(config)
        self._config = config
        self._action_trace: list[dict[str, Any]] = []

    def execute(  # type: ignore[override]
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Any,
        running: Any,
    ) -> LocalExecutionOutcome:
        """Run one navigation through repeated local LLM decisions and EMOS skills."""
        crab_agent = self._build_crab_agent()
        self._initialize_agent(crab_agent, invocation)
        self._action_trace = []
        habitat_env, _, numpy = self._require_initialized()
        try:
            habitat_env.episodes = [self._episode]
            habitat_env.reset()
            scene_description = self._scene_description()
            self._write_evidence_text("scene_description.txt", scene_description)
            self._write_evidence_text("subtask.txt", self._subtask(invocation))
            initial = self._agent_position()
            scene_id = str(habitat_env.current_episode.scene_id)
            running(
                f"CrabAgent episode {self._config.episode_id} started navigation to "
                f"{invocation.destination}"
            )
            return self._agent_loop(
                crab_agent=crab_agent,
                invocation=invocation,
                scene_description=scene_description,
                initial=initial,
                scene_id=scene_id,
                numpy=numpy,
                cancellation_requested=cancellation_requested,
            )
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(f"CrabAgent execution failed: {error}") from error

    # ------------------------------------------------------------------
    # Local agent loop and skill dispatch
    # ------------------------------------------------------------------

    def _agent_loop(
        self,
        crab_agent: Any,
        invocation: CanonicalMobilityInvocation,
        scene_description: str,
        initial: tuple[float, float, float],
        scene_id: str,
        numpy: Any,
        cancellation_requested: Any,
    ) -> LocalExecutionOutcome:
        """Alternate local LLM decisions with dispatched skills until a terminal fact."""
        habitat_env, _, _ = self._require_initialized()
        finished_key = f"agent_{self._config.agent_id}_has_finished_oracle_nav"
        steps_used = 0
        consecutive_invalid = 0
        call_index = 0
        while steps_used < self._config.max_steps:
            if cancellation_requested():
                return self._finish(
                    "CANCELLED",
                    "Habitat navigation stopped after the accepted cancellation request",
                    invocation.destination,
                    scene_id,
                    steps_used,
                    initial,
                )
            call_index += 1
            prompt = (
                START_ACTION_PROMPT.format(scene_description=scene_description)
                if call_index == 1
                else STEP_ACTION_PROMPT
            )
            decision, token_delta, latency_ms = self._chat_once(crab_agent, prompt)
            pipe = self._message_pipe_snapshot()
            self._append_trace(
                {
                    "call_index": call_index,
                    "stage": "local-agent",
                    "decision": decision,
                    "token_delta": token_delta,
                    "latency_ms": latency_ms,
                    "message_pipe_entries": pipe,
                    "simulator_steps_before": steps_used,
                }
            )
            if decision is None or not isinstance(decision, dict):
                outcome, consecutive_invalid = self._handle_invalid(
                    consecutive_invalid,
                    invocation,
                    scene_id,
                    steps_used,
                    initial,
                    "local agent produced no parsable action",
                )
                if outcome is not None:
                    return outcome
                steps_used += self._step_wait(numpy, min(1, self._config.max_steps - steps_used))
                continue
            name = decision.get("name")
            arguments = decision.get("arguments")
            if name == "nav_to_obj":
                consecutive_invalid = 0
                nav_steps, finished, cancelled = self._dispatch_nav(
                    numpy=numpy,
                    arguments=arguments,
                    steps_used=steps_used,
                    finished_key=finished_key,
                    cancellation_requested=cancellation_requested,
                )
                steps_used += nav_steps
                if cancelled:
                    return self._finish(
                        "CANCELLED",
                        "Habitat navigation stopped after the accepted cancellation request",
                        invocation.destination,
                        scene_id,
                        steps_used,
                        initial,
                    )
                if finished:
                    return self._finish(
                        "COMPLETED",
                        "Habitat Oracle navigation reported the destination reached",
                        invocation.destination,
                        scene_id,
                        steps_used,
                        initial,
                    )
                if steps_used >= self._config.max_steps:
                    break
                return self._finish(
                    "FAILED",
                    "Habitat episode terminated before the selected navigation completed",
                    invocation.destination,
                    scene_id,
                    steps_used,
                    initial,
                )
            if name == "wait":
                consecutive_invalid = 0
                remaining = self._config.max_steps - steps_used
                steps_used += self._step_wait(numpy, min(self._wait_steps(arguments), remaining))
                continue
            if isinstance(name, str) and name in _KNOWN_UNSUPPORTED_SKILLS:
                return self._finish(
                    "FAILED",
                    f"local agent selected unsupported skill {name!r}; failing closed",
                    invocation.destination,
                    scene_id,
                    steps_used,
                    initial,
                )
            outcome, consecutive_invalid = self._handle_invalid(
                consecutive_invalid,
                invocation,
                scene_id,
                steps_used,
                initial,
                f"local agent produced malformed action {name!r}",
            )
            if outcome is not None:
                return outcome
            steps_used += self._step_wait(numpy, min(1, self._config.max_steps - steps_used))
        return self._finish(
            "FAILED",
            f"CrabAgent navigation exceeded {self._config.max_steps} simulator steps",
            invocation.destination,
            scene_id,
            steps_used,
            initial,
        )

    def _dispatch_nav(
        self,
        numpy: Any,
        arguments: Any,
        steps_used: int,
        finished_key: str,
        cancellation_requested: Any,
    ) -> tuple[int, bool, bool]:
        """Execute one nav_to_obj selection and report steps, finished, and cancelled."""
        target = None
        if isinstance(arguments, dict):
            value = arguments.get("target_obj")
            if isinstance(value, str) and value.strip():
                target = value
        if target is None:
            raise IntegrationError("nav_to_obj selection lacks a string target_obj")
        habitat_env, _, _ = self._require_initialized()
        try:
            entity = habitat_env.task.pddl_problem.get_entity(target)
        except Exception as error:  # noqa: BLE001 - PDDL entity lookup is env-specific
            raise IntegrationError(f"PDDL entity lookup failed: {error}") from error
        if entity is None:
            raise IntegrationError(f"local agent selected non-entity navigation target {target!r}")
        entities = habitat_env.task.pddl_problem.get_ordered_entities_list()
        index = entities.index(entity)  # the agent, not the plan, picked this target
        action_name = f"agent_{self._config.agent_id}_oracle_nav_action"
        steps = 0
        budget = self._config.max_steps - steps_used
        while steps < budget:
            if cancellation_requested():
                return steps, False, True
            observations = habitat_env.step(
                {
                    "action": action_name,
                    "action_args": self._action_arguments(numpy, action_name, index),
                }
            )
            steps += 1
            if self._config.step_period_ms:
                time.sleep(self._config.step_period_ms / 1_000)
            if _observation_true(observations, finished_key):
                return steps, True, False
            if habitat_env.episode_over:
                return steps, False, False
        return steps, False, False

    def _step_wait(self, numpy: Any, count: int) -> int:
        """Step the wait action for one agent and return the consumed simulator steps."""
        habitat_env, _, _ = self._require_initialized()
        action_name = f"agent_{self._config.agent_id}_wait"
        for _ in range(count):
            habitat_env.step(
                {
                    "action": action_name,
                    "action_args": {action_name: numpy.asarray([True], dtype=numpy.bool_)},
                }
            )
            if self._config.step_period_ms:
                time.sleep(self._config.step_period_ms / 1_000)
        return count

    def _wait_steps(self, arguments: Any) -> int:
        """Parse the EMOS-style wait count and bound it to a sane maximum."""
        default = 1
        maximum = 500
        if isinstance(arguments, list) and arguments:
            value = arguments[0]
        elif isinstance(arguments, dict):
            value = arguments.get("steps", default)
        else:
            value = arguments
        try:
            return max(1, min(int(str(value)), maximum))
        except (TypeError, ValueError):
            return default

    def _handle_invalid(
        self,
        consecutive_invalid: int,
        invocation: CanonicalMobilityInvocation,
        scene_id: str,
        steps_used: int,
        initial: tuple[float, float, float],
        reason: str,
    ) -> tuple[LocalExecutionOutcome | None, int]:
        """Mirror EMOS wait-on-invalid while bounding consecutive malformed outputs."""
        consecutive_invalid += 1
        if consecutive_invalid > _MAX_CONSECUTIVE_INVALID_OUTPUTS:
            return (
                self._finish(
                    "FAILED",
                    f"{reason}; exceeded {_MAX_CONSECUTIVE_INVALID_OUTPUTS} consecutive "
                    "invalid local agent outputs",
                    invocation.destination,
                    scene_id,
                    steps_used,
                    initial,
                ),
                consecutive_invalid,
            )
        return None, consecutive_invalid

    # ------------------------------------------------------------------
    # CrabAgent construction, prompts, and accounting
    # ------------------------------------------------------------------

    def _build_crab_agent(self) -> Any:
        """Import and initialize the original EMOS CrabAgent for this execution."""
        from habitat_mas.agents.actions.arm_actions import (  # noqa: PLC0415
            pick,
            place,
            reset_arm,
        )
        from habitat_mas.agents.actions.base_actions import (  # noqa: PLC0415
            nav_to_obj,
            send_request,
            wait,
        )
        from habitat_mas.agents.crab_agent import CrabAgent  # noqa: PLC0415

        self._evidence_dir().mkdir(parents=True, exist_ok=True)
        agent = CrabAgent(
            f"agent_{self._config.agent_id}",
            [send_request, nav_to_obj, pick, place, reset_arm, wait],
        )
        return agent

    def _initialize_agent(self, crab_agent: Any, invocation: CanonicalMobilityInvocation) -> None:
        """Initialize the EMOS agent with the deployment-selected semantic subtask."""
        crab_agent.init_agent(
            robot_type=self._config.robot_type,
            task_description=invocation.objective,
            subtask_description=self._subtask(invocation),
            chat_history=None,
            enable_logging=True,
            logging_file=str(
                self._evidence_dir() / f"agent_{self._config.agent_id}-chat-history.json"
            ),
        )

    def _subtask(self, invocation: CanonicalMobilityInvocation) -> str:
        """Derive the natural-language subtask for the configured deployment mode."""
        if self._config.subtask_mode == "natural-objective":
            return invocation.objective
        return f"Navigate to {invocation.destination}."

    def _chat_once(self, crab_agent: Any, prompt: str) -> tuple[Any, int, float]:
        """Run one local LLM decision and return its output, token delta, and latency."""
        before = int(crab_agent.get_token_usage() or 0)
        started = time.monotonic()
        decision = crab_agent.chat(prompt)
        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        after = int(crab_agent.get_token_usage() or 0)
        return decision, after - before, latency_ms

    def _message_pipe_snapshot(self) -> list[str]:
        """Observe the class-level inter-agent pipe without mutating EMOS behavior."""
        from habitat_mas.agents.crab_agent import CrabAgent  # noqa: PLC0415

        entries = CrabAgent.message_pipe.get(f"agent_{self._config.agent_id}", [])
        return list(entries)

    def _scene_description(self) -> str:
        """Reuse the exact EMOS scene-description source for the current episode."""
        habitat_env, _, _ = self._require_initialized()
        context = habitat_env.task.get_task_text_context()
        value = context.get("scene_description") if isinstance(context, dict) else None
        if not isinstance(value, str) or not value:
            raise IntegrationError("EMOS task context lacks a scene description")
        return value

    # ------------------------------------------------------------------
    # Evidence and terminal facts
    # ------------------------------------------------------------------

    def _evidence_dir(self) -> Path:
        """Return the configured local evidence directory."""
        return self._config.evidence_dir

    def _write_evidence_text(self, name: str, text: str) -> None:
        """Persist one evidence text file beside the execution evidence."""
        self._evidence_dir().mkdir(parents=True, exist_ok=True)
        (self._evidence_dir() / name).write_text(text, encoding="utf-8")

    def _append_trace(self, record: dict[str, Any]) -> None:
        """Append one action-trace record in memory and on disk."""
        self._action_trace.append(record)
        self._evidence_dir().mkdir(parents=True, exist_ok=True)
        with open(self._evidence_dir() / "action_trace.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _finish(
        self,
        state: str,
        detail: str,
        destination: str,
        scene_id: str,
        steps: int,
        initial: tuple[float, float, float],
    ) -> LocalExecutionOutcome:
        """Emit the terminal outcome and persist the prompt/model freeze evidence."""
        self._write_evidence_text(
            "prompt_freeze.json",
            json.dumps(
                {
                    "start_action_prompt_sha256": _sha256(
                        START_ACTION_PROMPT.replace("{scene_description}", "")
                    ),
                    "step_action_prompt_sha256": _sha256(
                        STEP_ACTION_PROMPT.replace("{scene_description}", "")
                    ),
                    "hash_algorithm": _PROMPT_TEMPLATE_HASH_ALGORITHM,
                    "subtask_mode": self._config.subtask_mode,
                    "robot_type": self._config.robot_type,
                    "model": "not explicitly controlled (EMOS OpenAIModel reads "
                    "EMOS_LLM_MODEL from the deployment environment)",
                },
                indent=2,
            ),
        )
        return self._outcome(state, detail, destination, scene_id, steps, initial)


def _sha256(text: str) -> str:
    """Hash one prompt template for the freeze evidence."""
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
