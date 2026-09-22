"""Shared-world multi-node Local EAIOS execution for one Habitat episode.

One process owns exactly one Habitat simulator world, one official episode
state, and one original EMOS Stage2 multi-agent policy.  Two independent
RoboGuide Nodes reach this world through two loopback Local EAIOS endpoints;
each endpoint keeps its own durable execution store and local handles.  The
coordinator waits until both Control-committed assignments have arrived
(bounded benchmark episode-start synchronization, never a Mission semantic
dependency), resets the episode exactly once, runs the original joint policy
loop, and projects per-agent terminal facts back to each Node's handle.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backend import (
    HabitatBackendConfig,
    LocalExecutionOutcome,
    _observation_true,
    initial_agent_positions,
)
from .crabagent_backend import CrabAgentBackendConfig
from .diagnostics import create_physical_diagnostics, diagnostics_enabled
from .emos_stage2 import EmosStage2Runtime
from .model import CanonicalMobilityInvocation, IntegrationError
from .semantic_evidence import build_authoritative_semantic_evidence
from .stage2_contract import Stage2ContractViolation, Stage2ExecutionContract
from .store import TERMINAL_STATES, ExecutionStore, StoredExecution
from .video_capture import HabitatVideoCapture

_LOG = logging.getLogger("roboguide.habitat_local_eaios.shared_world")


class SharedEmosStage2Runtime(EmosStage2Runtime):
    """One Habitat world and one original EMOS Stage2 policy for two agents."""

    def __init__(self, config: HabitatBackendConfig, agent_ids: tuple[int, int]) -> None:
        """Retain both Habitat agent identities served by the shared world."""
        super().__init__(config)
        self._agent_ids = agent_ids
        self._diagnostics = create_physical_diagnostics(
            self._evidence_dir(),
            agent_ids,
            diagnostics_enabled(),
            # The original loop can settle for at most 50 extra steps after
            # both local skills finish, so this is the complete stream bound.
            config.max_steps + 50,
        )
        self._video = HabitatVideoCapture(config.video_path, config.video_fps)

    def initialize(self) -> None:
        """Initialize the environment and publish authoritative semantics before readiness."""
        super().initialize()
        _, habitat_env, _, _ = self._require_initialized()
        try:
            document = build_authoritative_semantic_evidence(
                habitat_env,
                run_id=getattr(self._config, "run_id", ""),
                episode_id=self._config.episode_id,
                agent_ids=self._agent_ids,
                episode=self._episode,
            )
            self._write_json("authoritative-semantic-evidence.json", document)
        except Exception as error:
            self.close()
            raise IntegrationError(
                f"authoritative semantic evidence initialization failed: {error}"
            ) from error

    def execute_pair(
        self,
        invocations: dict[int, CanonicalMobilityInvocation],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Run one shared episode with both committed assignments.

        ``invocations`` maps each Habitat agent id to the canonical invocation
        committed by its RoboGuide Node.  ``running`` publishes per-agent
        RUNNING facts after the single episode reset.
        """
        gym_env, habitat_env, actor, access = self._require_initialized()
        agent_ids = self._agent_ids
        loop_owns_terminal_diagnostics = False
        setup_phase = "gym_reset"
        try:
            habitat_env.episodes = [self._episode]
            observations = gym_env.reset()
            if isinstance(observations, tuple):
                observations = observations[0]
            self._diagnostics.record_reset(habitat_env, self._config)
            self._record_video(0, observations, {})
            setup_phase = "task_context"
            episode_started_at = time.time()
            text_context = habitat_env.task.get_task_text_context()
            text_context["episode_id"] = habitat_env.current_episode.episode_id
            self._write_text("scene_description.txt", str(text_context["scene_description"]))
            for agent_id in agent_ids:
                self._write_text(
                    f"subtask-agent-{agent_id}.txt",
                    self._subtask(invocations[agent_id]),
                )
            assignment = self._pair_arguments(text_context, invocations)
            for agent_id in agent_ids:
                running(
                    agent_id,
                    f"shared EMOS Stage2 episode {self._config.episode_id} started "
                    f"for {invocations[agent_id].destination}",
                )
            identity = {
                "episode_id": self._config.episode_id,
                "episode_reset_count": 1,
                "episode_started_unix": episode_started_at,
                "pid": os.getpid(),
                "scene_id": str(habitat_env.current_episode.scene_id),
                "simulator_worlds": 1,
                # The consumed simulator seed and the actually observed agent
                # base positions after this reset are benchmark-condition
                # evidence: paired arms must compare these recorded facts,
                # never assume equal seeds imply equal initial states.
                "habitat_seed": self._config.seed,
                "initial_agent_positions": initial_agent_positions(habitat_env, agent_ids),
            }
            loop_owns_terminal_diagnostics = True
            outcomes, steps, done, info = self._pair_loop(
                observations,
                text_context,
                assignment,
                invocations,
                actor,
                access,
                gym_env,
                habitat_env,
                cancellation_requested,
                running,
            )
            identity["episode_terminated"] = bool(done or habitat_env.episode_over)
            identity["simulator_steps"] = steps
            return outcomes, {
                "action_trace_collection": self._action_trace_stats(),
                "identity": identity,
                "final_info": info,
            }
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(f"shared EMOS Stage2 execution failed: {error}") from error
        finally:
            if not loop_owns_terminal_diagnostics:
                self._record_terminal_diagnostics(
                    habitat_env,
                    0,
                    f"execution_exception:{setup_phase}",
                )

    def _pair_loop(
        self,
        observations: Any,
        text_context: dict[str, Any],
        assignment: dict[str, Any],
        invocations: dict[int, CanonicalMobilityInvocation],
        actor: Any,
        access: Any,
        gym_env: Any,
        habitat_env: Any,
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], int, bool, dict[str, Any]]:
        """Drive the original joint policy loop with per-agent completion."""
        steps = 0
        done = False
        info: dict[str, Any] = {}
        termination_reason = "execution_exception:pair_loop_setup"
        exception_phase = "pair_loop_setup"
        primary_error: BaseException | None = None
        skill_sequence: list[str] = []
        outcomes: dict[int, LocalExecutionOutcome] = {}
        terminal_bases: dict[int, str] = {}
        module: Any = None
        original_group_discussion: Any = None
        contract_restore: Callable[[], None] | None = None
        contract_failure: Stage2ContractViolation | None = None
        cancelled = False
        try:
            torch = self._runtime["torch"]
            device = self._runtime["device"]
            batch = self._batch(observations)
            action_shape, discrete = self._runtime["get_action_space_info"](
                actor.policy_action_space
            )
            hidden = torch.zeros((1, *actor.hidden_state_shape), device=device)
            previous = torch.zeros(
                1,
                *action_shape,
                device=device,
                dtype=torch.long if discrete else torch.float,
            )
            masks = torch.zeros(1, *access.masks_shape, device=device, dtype=torch.bool)
            hidden_lengths = actor.hidden_state_shape_lens
            action_lengths = actor.policy_action_space_shape_lens
            agent_ids = self._agent_ids
            initials = {agent_id: self._agent_position_for(agent_id) for agent_id in agent_ids}
            scene_id = str(habitat_env.current_episode.scene_id)
            chat_history_root = self._evidence_dir() / "chat-history"
            (chat_history_root / str(text_context["episode_id"])).mkdir(parents=True, exist_ok=True)
            module, original_group_discussion = self._install_assignment(assignment)
            contract_restore = self._install_execution_contract(
                {
                    f"agent_{agent_id}": Stage2ExecutionContract.for_invocation(
                        invocations[agent_id]
                    )
                    for agent_id in invocations
                },
                lambda: steps,
            )
            while steps < self._config.max_steps:
                if cancellation_requested():
                    cancelled = True
                    break
                policy_input_observations = observations
                exception_phase = "actor_act"
                action_data = actor.act(
                    batch,
                    hidden,
                    previous,
                    masks,
                    deterministic=False,
                    envs_text_context=[text_context],
                    index_len_recurrent_hidden_states=hidden_lengths,
                    index_len_prev_actions=action_lengths,
                    save_chat_history=False,
                    save_chat_history_dir=str(chat_history_root),
                )
                current_skills = self._current_skills(actor)
                self._extend_skill_sequence(skill_sequence, current_skills)
                env_action = action_data.env_actions.detach().cpu()[0].numpy()
                exception_phase = "gym_env_step"
                step_result = gym_env.step(env_action)
                observations, done, info = self._gym_step_result(step_result)
                steps += 1
                exception_phase = "post_step_observation"
                if action_data.should_inserts is None:
                    hidden = action_data.rnn_hidden_states
                    previous.copy_(action_data.actions)
                else:
                    actor.update_hidden_state(hidden, previous, action_data)
                batch = self._batch(observations)
                masks = torch.tensor([[not done]], dtype=torch.bool, device=device).repeat(
                    1, *access.masks_shape
                )
                self._append_action_trace(steps, current_skills, info)
                self._diagnostics.record_step(
                    steps,
                    current_skills,
                    env_action,
                    habitat_env,
                    actor,
                    done,
                    info,
                    observations,
                    policy_input_observations,
                )
                self._record_video(steps, observations, info)
                for agent_id in agent_ids:
                    if agent_id in outcomes:
                        continue
                    finished_key = f"agent_{agent_id}_has_finished_oracle_nav"
                    skill = current_skills[agent_id]
                    if skill in {
                        "nav_to_obj",
                        "nav_to_goal",
                        "nav_to_receptacle_by_name",
                    } and (
                        _observation_true(observations, finished_key)
                        or self._oracle_nav_finished_for(agent_id)
                    ):
                        outcomes[agent_id] = self._pair_outcome(
                            "COMPLETED",
                            "original EMOS OracleNavPolicy reached its skill terminal "
                            "measure in the shared world",
                            invocations[agent_id],
                            scene_id,
                            steps,
                            initials[agent_id],
                            agent_id,
                            skill_sequence,
                            local_skill_completed=True,
                            episode_terminated=bool(done or habitat_env.episode_over),
                            terminal_basis="oracle-nav-skill",
                        )
                        terminal_bases[agent_id] = "oracle-nav-skill"
                if done:
                    pddl_success = bool(info.get("pddl_success", False))
                    for agent_id in agent_ids:
                        if agent_id in outcomes:
                            continue
                        if pddl_success:
                            outcomes[agent_id] = self._pair_outcome(
                                "COMPLETED",
                                "Habitat semantic task success ended the shared episode",
                                invocations[agent_id],
                                scene_id,
                                steps,
                                initials[agent_id],
                                agent_id,
                                skill_sequence,
                                local_skill_completed=False,
                                episode_terminated=True,
                                terminal_basis="habitat-pddl-success",
                            )
                        else:
                            outcomes[agent_id] = self._pair_outcome(
                                "FAILED",
                                "Habitat episode terminated before the assigned shared "
                                "navigation completed",
                                invocations[agent_id],
                                scene_id,
                                steps,
                                initials[agent_id],
                                agent_id,
                                skill_sequence,
                                local_skill_completed=False,
                                episode_terminated=True,
                                terminal_basis="episode-terminated-before-success",
                            )
                    break
                if all(agent_id in outcomes for agent_id in agent_ids):
                    # Both assigned skills finished; step briefly until the episode
                    # settles so official pddl_success is measured on terminal state.
                    for _ in range(50):
                        if done or habitat_env.episode_over:
                            break
                        exception_phase = "settle_gym_env_step"
                        step_result = gym_env.step(env_action * 0)
                        observations, done, info = self._gym_step_result(step_result)
                        steps += 1
                        exception_phase = "settle_post_step_observation"
                        self._diagnostics.record_step(
                            steps,
                            current_skills,
                            env_action * 0,
                            habitat_env,
                            actor,
                            done,
                            info,
                            observations,
                            None,
                        )
                        self._record_video(steps, observations, info)
                    break
                if self._config.step_period_ms:
                    time.sleep(self._config.step_period_ms / 1_000)
            termination_reason = (
                "cancellation"
                if cancelled
                else "episode_done"
                if done
                else "step_budget_exhausted"
                if steps >= self._config.max_steps
                else "skills_completed_settled"
            )
        except Stage2ContractViolation as error:
            contract_failure = error
            termination_reason = "local_contract_failure"
        except BaseException as error:
            primary_error = error
            termination_reason = f"execution_exception:{exception_phase}:{type(error).__name__}"
            raise
        finally:
            try:
                if contract_restore is not None:
                    contract_restore()
                if module is not None:
                    module.group_discussion = original_group_discussion
            except Exception:
                if primary_error is None:
                    raise
                _LOG.exception(
                    "failed to restore EMOS group_discussion while preserving the primary error"
                )
            finally:
                self._flush_action_trace()
                self._record_terminal_diagnostics(habitat_env, steps, termination_reason)
        if contract_failure is not None:
            for agent_id in agent_ids:
                # A later joint-policy stop cannot erase an already observed
                # local completion, even when that agent proposed the violation.
                if agent_id in outcomes:
                    continue
                is_offending_agent = f"agent_{agent_id}" == contract_failure.agent_name
                outcomes[agent_id] = self._pair_outcome(
                    "FAILED",
                    str(contract_failure)
                    if is_offending_agent
                    else "shared episode stopped after a sibling local contract failure",
                    invocations[agent_id],
                    scene_id,
                    steps,
                    initials[agent_id],
                    agent_id,
                    skill_sequence,
                    local_skill_completed=False,
                    episode_terminated=bool(done or habitat_env.episode_over),
                    terminal_basis=(
                        "local-contract-failure"
                        if is_offending_agent
                        else "sibling-local-contract-failure"
                    ),
                )
            return outcomes, steps, bool(done or habitat_env.episode_over), info
        if cancelled:
            for agent_id in agent_ids:
                if agent_id not in outcomes:
                    outcomes[agent_id] = self._pair_outcome(
                        "CANCELLED",
                        "shared episode stopped after an accepted cancellation request",
                        invocations[agent_id],
                        scene_id,
                        steps,
                        initials[agent_id],
                        agent_id,
                        skill_sequence,
                        local_skill_completed=False,
                        episode_terminated=bool(done or habitat_env.episode_over),
                        terminal_basis="cancellation",
                    )
        for agent_id in agent_ids:
            if agent_id not in outcomes:
                outcomes[agent_id] = self._pair_outcome(
                    "FAILED",
                    f"shared EMOS Stage2 exceeded {self._config.max_steps} simulator steps",
                    invocations[agent_id],
                    scene_id,
                    steps,
                    initials[agent_id],
                    agent_id,
                    skill_sequence,
                    local_skill_completed=False,
                    episode_terminated=bool(done or habitat_env.episode_over),
                    terminal_basis="step-budget-exhausted",
                )
        return outcomes, steps, bool(done or habitat_env.episode_over), info

    def _record_terminal_diagnostics(self, habitat_env: Any, steps: int, reason: str) -> None:
        """Persist best-effort terminal evidence without changing SUT failure semantics."""
        try:
            self._diagnostics.record_terminal(habitat_env, steps, reason)
        except Exception:  # noqa: BLE001 - optional evidence cannot mask execution
            _LOG.exception("physical diagnostics failed while recording terminal evidence")
        video = getattr(self, "_video", None)
        if video is not None:
            video.close(reason)

    def _record_video(
        self,
        step: int,
        observations: Any,
        info: dict[str, Any],
    ) -> None:
        """Forward existing observations only when RGB evidence is explicitly enabled."""
        video = getattr(self, "_video", None)
        if video is None or not video.enabled:
            return
        video.record(
            step,
            observations,
            info,
            self._runtime.get("habitat_config"),
            str(self._config.episode_id),
        )

    def close(self) -> None:
        """Finalize optional video evidence before releasing the shared simulator."""
        video = getattr(self, "_video", None)
        if video is not None:
            video.close("runtime_close")
        super().close()

    def final_metrics(self) -> dict[str, Any]:
        """Read the shared episode's official terminal metrics once."""
        if self._habitat_env is None:
            return {}
        return dict(self._habitat_env.get_metrics())

    def is_ready(self) -> bool:
        """Report whether the shared Habitat world is initialized."""
        return self._actor is not None

    def readiness_detail(self) -> str:
        """Describe the shared world serving both Habitat agents."""
        if self._actor is None:
            return "EMOS Stage2 policy is not initialized"
        return (
            f"original EMOS Stage2 episode {self._config.episode_id} is shared by "
            f"agents {self._agent_ids[0]} and {self._agent_ids[1]}"
        )

    def _pair_arguments(
        self,
        text_context: dict[str, Any],
        invocations: dict[int, CanonicalMobilityInvocation],
    ) -> dict[str, Any]:
        """Translate both committed assignments into EMOS Stage1's output type."""
        from habitat_mas.utils import AgentArguments  # type: ignore[import-not-found]

        raw_resumes = text_context.get("robot_resume")
        if not isinstance(raw_resumes, str):
            raise IntegrationError("EMOS task context lacks robot_resume assignments")
        try:
            resumes = json.loads(raw_resumes)
        except json.JSONDecodeError as error:
            raise IntegrationError("EMOS robot_resume is not valid JSON") from error
        if not isinstance(resumes, dict):
            raise IntegrationError("EMOS robot_resume does not contain an object")
        assigned: dict[str, Any] = {}
        for agent_name, resume in resumes.items():
            if not isinstance(agent_name, str) or not isinstance(resume, dict):
                raise IntegrationError("EMOS robot_resume has invalid structure")
            robot_type = resume.get("robot_type")
            if not isinstance(robot_type, str) or not robot_type:
                raise IntegrationError("EMOS robot_resume lacks robot_type")
            agent_index = int(agent_name.rsplit("_", 1)[-1])
            invocation = invocations.get(agent_index)
            if invocation is None:
                raise IntegrationError(
                    f"shared world has no committed assignment for {agent_name!r}"
                )
            assigned[agent_name] = AgentArguments(
                robot_id=agent_name,
                robot_type=robot_type,
                task_description=invocation.objective,
                subtask_description=self._subtask(invocation),
                chat_history=[],
            )
        for agent_id in self._agent_ids:
            if f"agent_{agent_id}" not in assigned:
                raise IntegrationError(f"assigned EMOS agent agent_{agent_id!r} is unavailable")
        return assigned

    def _pair_outcome(
        self,
        state: str,
        detail: str,
        invocation: CanonicalMobilityInvocation,
        scene_id: str,
        steps: int,
        initial: tuple[float, float, float],
        agent_id: int,
        skill_sequence: list[str],
        *,
        local_skill_completed: bool,
        episode_terminated: bool,
        terminal_basis: str,
    ) -> LocalExecutionOutcome:
        """Capture one agent's terminal fact inside the shared world."""
        habitat_env = self._habitat_env
        metrics = habitat_env.get_metrics() if habitat_env is not None else {}
        benchmark_achieved = bool(metrics.get("pddl_success", False))
        evidence = self._policy_evidence_for(agent_id)
        return LocalExecutionOutcome(
            state=state,
            detail=detail,
            episode_id=self._config.episode_id,
            scene_id=scene_id,
            destination=invocation.destination,
            simulator_steps=steps,
            initial_position=initial,
            final_position=self._agent_position_for(agent_id),
            local_skill_completed=local_skill_completed,
            benchmark_task_achieved=benchmark_achieved,
            episode_terminated=episode_terminated,
            skill_sequence=tuple(skill_sequence),
            local_llm_calls=int(evidence["local_llm_calls"]),
            local_tokens=int(evidence["local_tokens"]),
            local_replans=int(evidence["local_replans"]),
            invalid_outputs=int(evidence["invalid_outputs"]),
            send_request_count=int(evidence["send_request_count"]),
            message_pipe_activity_count=int(evidence["message_pipe_activity_count"]),
            terminal_basis=terminal_basis,
        )

    def _policy_evidence_for(self, agent_id: int) -> dict[str, Any]:
        """Collect per-agent model evidence from the original policy state."""
        actor = self._actor
        policies = [] if actor is None else list(actor._active_policies)
        index = self._agent_ids.index(agent_id)
        policy = policies[index] if index < len(policies) else None
        agent = getattr(policy, "_high_level_policy", None)
        agent = getattr(agent, "llm_agent", None)
        model = getattr(agent, "llm_model", None)
        if model is None:
            return {
                "invalid_outputs": 0,
                "local_llm_calls": 0,
                "local_replans": 0,
                "local_tokens": 0,
                "message_pipe_activity_count": 0,
                "send_request_count": 0,
            }
        history = getattr(model, "chat_history", [])
        action_calls = max(len(history) - 1, 0)
        send_request_count = 0
        message_pipe_activity_count = 0
        invalid_outputs = 0
        for name, arguments in self._tool_calls(history):
            if name != "send_request":
                continue
            send_request_count += 1
            target = arguments.get("target_agent")
            if isinstance(target, str) and target == getattr(agent, "name", None):
                invalid_outputs += 1
            elif isinstance(target, str):
                message_pipe_activity_count += 1
        return {
            "invalid_outputs": invalid_outputs,
            "local_llm_calls": len(history),
            "local_replans": max(action_calls - 1, 0),
            "local_tokens": int(getattr(model, "token_usage", 0) or 0),
            "message_pipe_activity_count": message_pipe_activity_count,
            "send_request_count": send_request_count,
        }

    def _agent_position_for(self, agent_id: int) -> tuple[float, float, float]:
        """Read one shared-world agent's base position."""
        if self._habitat_env is None:
            raise IntegrationError("Habitat environment is not initialized")
        position = self._habitat_env.sim.get_agent_data(agent_id).articulated_agent.base_pos
        return (float(position[0]), float(position[1]), float(position[2]))

    def _oracle_nav_finished_for(self, agent_id: int) -> bool:
        """Read one agent's oracle navigation finish flag in the shared world."""
        habitat_env = self._habitat_env
        if habitat_env is None:
            return False
        actions = getattr(getattr(habitat_env, "task", None), "actions", {})
        if not isinstance(actions, dict):
            return False
        action = actions.get(f"agent_{agent_id}_oracle_nav_action")
        return bool(getattr(action, "skill_done", False))


class NodeEndpoint:
    """One RoboGuide Node's Local EAIOS surface over the shared world."""

    def __init__(
        self,
        name: str,
        agent_id: int,
        store: ExecutionStore,
        coordinator: SharedWorldCoordinator,
    ) -> None:
        """Bind one durable store and agent mapping to the shared coordinator."""
        self.name = name
        self.agent_id = agent_id
        self._store = store
        self._coordinator = coordinator
        self._lock = threading.RLock()
        self._scheduled: set[str] = set()
        self._known_keys: dict[str, str] = {
            execution["request_key"]: execution["execution_id"]
            for execution in store.all_executions()
        }

    def health(self) -> dict[str, object]:
        """Report shared-world health for this endpoint."""
        return self._coordinator.health()

    def readiness(self) -> dict[str, object]:
        """Report exact operation readiness for this endpoint."""
        healthy = self._coordinator.runtime_ready()
        return {
            "detail": self._coordinator.readiness_detail(),
            "operation": "mobility.navigate@v1",
            "state": "READY" if healthy else "UNAVAILABLE",
        }

    def accept(self, request: object) -> dict[str, object]:
        """Durably accept one exact invocation without starting simulator work."""
        invocation = CanonicalMobilityInvocation.from_request(request)
        with self._lock:
            key = invocation.request_key()
            if key in self._known_keys:
                existing = self._store.get(self._known_keys[key])
                if existing is not None:
                    return self._execution_response(existing)
            active = self._store.active_execution()
            if active is not None:
                raise IntegrationError(
                    "this shared-world endpoint already owns another active execution"
                )
            if self._coordinator.episode_consumed():
                raise IntegrationError(
                    "the shared-world episode has already been consumed by a prior pair"
                )
            execution, created = self._store.create_or_get(invocation)
            if created:
                self._known_keys[key] = execution["execution_id"]
                self._coordinator.record_arrival(self.name, invocation)
            return self._execution_response(execution)

    def dispatch(self, execution_id: str) -> None:
        """Idempotently schedule one durably accepted handle on the coordinator."""
        with self._lock:
            execution = self._store.get(execution_id)
            if execution is None:
                raise IntegrationError(f"unknown local execution {execution_id!r}")
            if execution["state"] != "ACCEPTED":
                return
            if execution_id in self._scheduled:
                return
            self._scheduled.add(execution_id)
        self._coordinator.submit(self, execution_id)

    def submit(self, request: object) -> dict[str, object]:
        """Accept and schedule one invocation for tests."""
        response = self.accept(request)
        self.dispatch(str(response["execution_id"]))
        return response

    def status(self, request: object) -> dict[str, object]:
        """Return the current durable local fact for one stable handle."""
        execution_id = _execution_id(request)
        execution = self._store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        _LOG.info("%s status observed for %s: %s", self.name, execution_id, execution["state"])
        return self._execution_response(execution)

    def cancel(self, request: object) -> dict[str, object]:
        """Accept cancellation intent without synthesizing a terminal fact."""
        execution_id = _execution_id(request)
        before = self._store.get(execution_id)
        if before is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        execution = self._store.request_cancel(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown local execution {execution_id!r}")
        response = self._execution_response(execution)
        response["accepted"] = before["state"] not in TERMINAL_STATES
        return response

    def store(self) -> ExecutionStore:
        """Expose the durable store for coordinator terminal projection."""
        return self._store

    @staticmethod
    def _execution_response(execution: StoredExecution) -> dict[str, object]:
        """Expose local facts plus the exact retained semantic invocation."""
        invocation = execution["invocation"]
        return {
            "cancel_requested": execution["cancel_requested"],
            "detail": execution["detail"],
            "execution_id": execution["execution_id"],
            "group_id": invocation.group_id,
            "local_outcome": execution["local_outcome"],
            "mission_id": invocation.mission_id,
            "objective": invocation.objective,
            "operation": invocation.operation,
            "parameters": dict(invocation.parameters),
            "resource_ids": list(invocation.resource_ids),
            "role_id": invocation.role_id,
            "state": execution["state"],
            "task_id": invocation.task_id,
            "updated_at_unix_ms": execution["updated_at_unix_ms"],
        }


class InProcessWorldService:
    """World service that drives a runtime on the caller's thread (tests)."""

    def __init__(self, runtime: Any) -> None:
        """Retain the runtime double that owns the shared world."""
        self._runtime = runtime

    def start(self) -> str:
        """Initialize the world on the current thread."""
        self._runtime.initialize()
        return str(self._runtime.readiness_detail())

    def is_ready(self) -> bool:
        """Report whether the world initialized."""
        return bool(self._runtime.is_ready())

    def readiness_detail(self) -> str:
        """Describe the world."""
        return str(self._runtime.readiness_detail())

    def run_pair(
        self,
        invocations: dict[int, CanonicalMobilityInvocation],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Run one shared episode in-process."""
        outcomes, summary = self._runtime.execute_pair(invocations, cancellation_requested, running)
        summary["official_metrics"] = self._runtime.final_metrics()
        return outcomes, summary

    def shutdown(self) -> None:
        """Release the in-process world."""
        closer = getattr(self._runtime, "close", None)
        if closer is not None:
            closer()


def _child_world_process(
    connection: Any,
    config: CrabAgentBackendConfig,
    agent_ids: tuple[int, int],
) -> None:
    """Own the shared Habitat world in one child process.

    Habitat-sim's GL context and the busy simulator loop must stay off the
    HTTP-serving parent so bridge health observations never starve.
    """
    runtime = SharedEmosStage2Runtime(config, agent_ids)
    try:
        try:
            runtime.initialize()
            connection.send(("READY", runtime.readiness_detail()))
        except Exception as error:  # noqa: BLE001 - relayed to the parent
            connection.send(("INITIALIZATION_FAILED", str(error)))
            return
        while True:
            try:
                kind, payload = _receive_message(connection)
            except EOFError:
                return
            if kind == "CLOSE":
                return
            if kind != "EXECUTE_PAIR" or not isinstance(payload, dict):
                connection.send(("WORLD_ERROR", "invalid world process command"))
                continue

            def cancellation_requested() -> bool:
                """Consume cancellation or shutdown commands between world steps."""
                while connection.poll():
                    nested_kind, _ = _receive_message(connection)
                    if nested_kind in {"CANCEL", "CLOSE"}:
                        return True
                return False

            def running(agent_id: int, detail: str) -> None:
                """Relay one agent's RUNNING fact to the parent."""
                connection.send(("RUNNING", (agent_id, detail)))

            try:
                outcomes, summary = runtime.execute_pair(payload, cancellation_requested, running)
                summary["official_metrics"] = runtime.final_metrics()
                connection.send(("TERMINAL", (outcomes, summary)))
            except Exception as error:  # noqa: BLE001 - terminal failure for both nodes
                connection.send(("WORLD_ERROR", str(error)))
    finally:
        runtime.close()
        connection.close()


class ProcessWorldService:
    """World service that keeps the shared Habitat world in one child process."""

    def __init__(self, config: CrabAgentBackendConfig, agent_ids: tuple[int, int]) -> None:
        """Retain deployment config; the world starts on demand."""
        import multiprocessing

        self._config = config
        self._agent_ids = agent_ids
        self._context = multiprocessing.get_context("spawn")
        self._connection: Any = None
        self._process: Any = None
        self._ready_detail = "shared world process is not initialized"
        self._ready = False

    def start(self) -> str:
        """Spawn the world process and wait for explicit readiness evidence."""
        parent_connection, child_connection = self._context.Pipe()
        process = self._context.Process(
            target=_child_world_process,
            args=(child_connection, self._config, self._agent_ids),
            name="habitat-shared-world",
        )
        process.start()
        child_connection.close()
        self._connection = parent_connection
        self._process = process
        deadline = time.monotonic() + 3600.0
        while time.monotonic() < deadline:
            if parent_connection.poll(0.5):
                kind, payload = _receive_message(parent_connection)
                if kind == "READY":
                    self._ready_detail = str(payload)
                    self._ready = True
                    return self._ready_detail
                if kind == "INITIALIZATION_FAILED":
                    raise IntegrationError(f"shared world initialization failed: {payload}")
                raise IntegrationError(f"unexpected world message {kind!r}")
            if not process.is_alive():
                raise IntegrationError("shared world process exited during initialization")
        raise IntegrationError("shared world process initialization timed out")

    def is_ready(self) -> bool:
        """Report whether the child world reported readiness."""
        return self._ready

    def readiness_detail(self) -> str:
        """Describe the child-owned shared world."""
        return self._ready_detail

    def run_pair(
        self,
        invocations: dict[int, CanonicalMobilityInvocation],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Execute one shared episode in the child and relay lifecycle evidence."""
        connection, process = self._require_live()
        connection.send(("EXECUTE_PAIR", invocations))
        cancel_sent = False
        while True:
            if cancellation_requested() and not cancel_sent:
                try:
                    connection.send(("CANCEL", None))
                except (BrokenPipeError, OSError):
                    pass
                cancel_sent = True
            if connection.poll(0.05):
                kind, payload = _receive_message(connection)
                if kind == "RUNNING":
                    agent_id, detail = payload
                    running(agent_id, detail)
                    continue
                if kind == "TERMINAL":
                    outcomes, summary = payload
                    return outcomes, summary
                if kind == "WORLD_ERROR":
                    raise IntegrationError(f"shared world execution failed: {payload}")
                raise IntegrationError(f"unexpected world message {kind!r}")
            if not process.is_alive():
                raise IntegrationError("shared world process exited during the episode")

    def shutdown(self) -> None:
        """Request clean world shutdown with bounded termination fallback."""
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        self._ready = False
        if connection is not None:
            try:
                connection.send(("CLOSE", None))
            except (BrokenPipeError, EOFError, OSError):
                pass
            connection.close()
        if process is not None:
            process.join(timeout=30.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=30.0)

    def _require_live(self) -> tuple[Any, Any]:
        """Return live IPC state or reject before pairing."""
        if self._connection is None or self._process is None or not self._process.is_alive():
            raise IntegrationError("shared world process is not initialized")
        return self._connection, self._process


class SharedWorldCoordinator:
    """Serialize one shared episode across two independent Node endpoints."""

    def __init__(
        self,
        world: Any,
        pair_wait_s: float,
        evidence_dir: Path,
    ) -> None:
        """Own the single-episode state machine and evidence log."""
        if pair_wait_s <= 0:
            raise IntegrationError("pair_wait_s must be positive")
        self._world = world
        self._pair_wait_s = pair_wait_s
        self._evidence_dir = evidence_dir
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._queue: list[tuple[NodeEndpoint, str]] = []
        self._episode_consumed = False
        self._initialization_error: str | None = None
        self._worker = threading.Thread(
            target=self._run, name="shared-world-coordinator", daemon=True
        )
        self._arrival_log = evidence_dir / "assignment-arrival.jsonl"
        self._worker.start()

    def record_arrival(self, node_name: str, invocation: CanonicalMobilityInvocation) -> None:
        """Persist one assignment-arrival timestamp for benchmark evidence."""
        record = {
            "node": node_name,
            "mission_id": invocation.mission_id,
            "task_id": invocation.task_id,
            "destination": invocation.destination,
            "unix": time.time(),
        }
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        with self._arrival_log.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")

    def submit(self, endpoint: NodeEndpoint, execution_id: str) -> None:
        """Enqueue one dispatched handle and wake the pairing barrier."""
        with self._condition:
            self._queue.append((endpoint, execution_id))
            self._condition.notify_all()

    def health(self) -> dict[str, object]:
        """Report shared-world process health."""
        if self._initialization_error is not None:
            return {"detail": self._initialization_error, "state": "OFFLINE"}
        return {
            "detail": self.readiness_detail(),
            "state": "ONLINE" if self.runtime_ready() else "OFFLINE",
        }

    def readiness_detail(self) -> str:
        """Describe the shared world without mutating execution state."""
        return str(self._world.readiness_detail())

    def runtime_ready(self) -> bool:
        """Report whether the shared world initialized on its owning thread."""
        return self._initialization_error is None and self._world.is_ready()

    def episode_consumed(self) -> bool:
        """Report whether the single shared episode has already been used."""
        with self._condition:
            return self._episode_consumed

    def shutdown(self) -> None:
        """Stop the world service cooperatively."""
        self._world.shutdown()

    def _run(self) -> None:
        """Start the world, wait for a complete pair, run one episode."""
        try:
            self._world.start()
        except Exception as error:  # noqa: BLE001 - surfaced through health
            self._initialization_error = str(error)
            return
        waited_from: float | None = None
        pair: list[tuple[NodeEndpoint, str]] = []
        while True:
            with self._condition:
                if not self._queue:
                    self._condition.wait(timeout=0.2)
                entries = list(self._queue)
            for entry in entries:
                if len(pair) < 2 and all(node is not entry[0] for node, _ in pair):
                    pair.append(entry)
                    self._dequeue(entry)
                elif entry not in pair:
                    self._fail_extra(entry)
            if len(pair) == 1:
                if waited_from is None:
                    waited_from = time.monotonic()
                elif time.monotonic() - waited_from >= self._pair_wait_s:
                    self._fail_unpaired(pair[0])
                    pair = []
                    waited_from = None
                continue
            if len(pair) == 2:
                with self._condition:
                    # The deployment owns exactly one simulator episode. Claim
                    # it before execution so terminal publication cannot race a
                    # third accept through an obsolete "not consumed" view.
                    self._episode_consumed = True
                self._execute_pair(pair)
                pair = []
                waited_from = None

    def _dequeue(self, entry: tuple[NodeEndpoint, str]) -> None:
        """Remove one paired entry from the pending queue."""
        with self._condition:
            if entry in self._queue:
                self._queue.remove(entry)

    def _fail_extra(self, entry: tuple[NodeEndpoint, str]) -> None:
        """Fail closed a dispatch that cannot join the current episode."""
        endpoint, execution_id = entry
        self._dequeue(entry)
        store = endpoint.store()
        current = store.get(execution_id)
        if current is not None and current["state"] not in TERMINAL_STATES:
            store.mark_failed(
                execution_id,
                "shared world rejected a dispatch that cannot join the current episode",
            )

    def _fail_unpaired(self, entry: tuple[NodeEndpoint, str]) -> None:
        """Fail closed a lone assignment whose sibling never arrived (S1)."""
        endpoint, execution_id = entry
        store = endpoint.store()
        current = store.get(execution_id)
        if current is not None and current["state"] not in TERMINAL_STATES:
            store.mark_failed(
                execution_id,
                "shared-world pair never assembled; refusing to fake a paired episode",
            )

    def _execute_pair(self, pair: list[tuple[NodeEndpoint, str]]) -> None:
        """Run the single shared episode and project per-agent terminal facts."""
        endpoints = {endpoint.agent_id: endpoint for endpoint, _ in pair}
        handles: dict[int, str] = {}
        invocations: dict[int, CanonicalMobilityInvocation] = {}
        for endpoint, execution_id in pair:
            execution = endpoint.store().get(execution_id)
            if execution is None:
                raise IntegrationError("paired execution disappeared before the episode")
            handles[endpoint.agent_id] = str(execution["execution_id"])
            invocations[endpoint.agent_id] = execution["invocation"]

        def cancellation_requested() -> bool:
            """Observe either node's cancellation between shared steps."""
            for endpoint, execution_id in pair:
                if endpoint.store().cancellation_requested(execution_id):
                    return True
            return False

        def running(agent_id: int, detail: str) -> None:
            """Publish one agent's RUNNING fact after the single episode reset."""
            execution = endpoints[agent_id].store().get(handles[agent_id])
            if execution is not None and execution["state"] == "ACCEPTED":
                endpoints[agent_id].store().mark_running(handles[agent_id], detail)

        try:
            outcomes, summary = self._world.run_pair(invocations, cancellation_requested, running)
            for agent_id, outcome in outcomes.items():
                endpoints[agent_id].store().mark_terminal(handles[agent_id], outcome)
            metrics = summary.get("official_metrics", {})
            # Strict authority semantics: write official_pddl_success only when
            # the episode actually produced a strict-bool Habitat pddl_success.
            # Missing or malformed metrics stay unavailable (key omitted) so
            # downstream consumers never read a synthetic false.
            raw_pddl = metrics.get("pddl_success")
            summary_document: dict[str, object] = {
                "action_trace_collection": summary.get("action_trace_collection", {}),
                "identity": summary["identity"],
                "outcomes": {
                    str(agent_id): outcome.as_dict() for agent_id, outcome in outcomes.items()
                },
                "stage1_assignment": "RoboGuide committed assignments "
                "(original EMOS group_discussion replaced per execution)",
            }
            semantic_evidence = self._evidence_dir / "authoritative-semantic-evidence.json"
            if semantic_evidence.is_file():
                semantic_document = json.loads(semantic_evidence.read_text(encoding="utf-8"))
                if isinstance(semantic_document, dict) and isinstance(
                    semantic_document.get("digest"), str
                ):
                    summary_document["authoritative_semantic_evidence_digest"] = semantic_document[
                        "digest"
                    ]
            if isinstance(raw_pddl, bool):
                summary_document["official_pddl_success"] = raw_pddl
            else:
                summary_document["official_pddl_success_unavailable_reason"] = (
                    "habitat metrics did not report a strict-bool pddl_success"
                )
            self._write_json("shared-world-summary.json", summary_document)
        except Exception as error:  # noqa: BLE001 - terminal failure must reach both nodes
            for endpoint, execution_id in pair:
                store = endpoint.store()
                current = store.get(execution_id)
                if current is not None and current["state"] not in TERMINAL_STATES:
                    store.mark_failed(execution_id, f"shared episode failed: {error}")

    def _write_json(self, name: str, value: object) -> None:
        """Persist one deterministic JSON evidence file."""
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        path = self._evidence_dir / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _receive_message(connection: Any) -> tuple[str, Any]:
    """Receive and validate one fixed two-field local IPC message."""
    value = connection.recv()
    if not isinstance(value, tuple) or len(value) != 2 or not isinstance(value[0], str):
        raise IntegrationError("shared world process emitted an invalid IPC message")
    return value[0], value[1]


def _execution_id(request: object) -> str:
    """Validate the fixed status/cancel request body and return its local handle."""
    if not isinstance(request, dict) or set(request) != {"execution_id"}:
        raise IntegrationError("request must contain only execution_id")
    value = request["execution_id"]
    if not isinstance(value, str) or not value:
        raise IntegrationError("execution_id must be a non-empty string")
    return value
