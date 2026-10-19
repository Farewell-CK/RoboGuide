"""Explicit independent live endpoint execution over one original EMOS joint actor.

Only Control-delivered attempts activate a policy. Idle endpoints select the
original wait skill without a model. Assignment changes occur between original
Gym steps; no all-endpoint barrier, extra reset or adapter-owned DAG exists.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable, Mapping
from typing import Any

from .backend import LocalExecutionOutcome, _observation_true
from .endpoint_registry import FINALITY_PROFILE, LIVE_PROFILE, load_endpoint_registry
from .evidence_io import json_observation
from .live_bindings import LivePolicyBindings as LivePolicyBindings
from .live_coordinator import LiveWorldCoordinator as LiveWorldCoordinator
from .live_session import LiveSlotLedger as LiveSlotLedger
from .model import (
    OBSERVATION_ACQUIRE_OPERATION,
    OBSERVATION_OPERATION,
    CanonicalInvocation,
    CanonicalObservationAcquisitionInvocation,
    CanonicalObservationInvocation,
    CanonicalRelocationInvocation,
    IntegrationError,
)
from .operation_admission import OPERATION_FEASIBILITY_SCHEMA
from .perception import cached_detection_observations, inspect_perception, observe_detection
from .preassignment_feasibility import preassignment_digest
from .shared_world import SharedEmosStage2Runtime

_LOG = logging.getLogger(__name__)
LIVE_FEASIBILITY_SCHEMA = "roboguide.deployment-intent-feasibility/v0.6"


class LiveEmosStage2Runtime(SharedEmosStage2Runtime):
    """Opt-in one-to-four-agent execution; legacy shared-world runtime is unchanged."""

    def initialize(self) -> None:
        """Verify actual configured robot classes and source bytes before exposing endpoints."""
        if getattr(self, "_live_initialization_complete", False):
            return
        if getattr(self, "_live_initialization_started", False):
            raise IntegrationError("live profile initialization failed and cannot restart")
        super().initialize()
        self._live_initialization_started = True
        try:
            self._initialize_live_profile()
            self._live_initialization_complete = True
        except BaseException as error:
            self._best_effort_write_json(
                "live-initialization-failure.json",
                {
                    "schema_version": "roboguide.local-initialization-failure/v0.1",
                    "stage": "post-reset-live-profile",
                    "exception_type": type(error).__name__,
                    "source_frames": [
                        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(error.__traceback__, limit=8)
                    ],
                    "physical_episode_executed": False,
                },
                "post-reset live initialization",
            )
            self._record_terminal_diagnostics(
                self._habitat_env,
                0,
                f"execution_exception:live_initialization:{type(error).__name__}",
            )
            raise

    def _initialize_live_profile(self) -> None:
        """Bind the new profile to the already reset world without another reset or model call."""
        path = self._config.endpoint_registry_path
        if path is None:
            raise IntegrationError("live runtime needs an explicit endpoint registry")
        self.registry = load_endpoint_registry(path)
        _, environment, actor, _ = self._require_initialized()
        if len(actor._active_policies) != len(self.registry["endpoints"]):
            raise IntegrationError("loaded policy count differs from endpoint registry")
        robot_types = {}
        for record in self.registry["endpoints"]:
            robot = environment.sim.get_agent_data(record["agent_id"]).articulated_agent
            if type(robot).__name__ != record["robot_type"]:
                raise IntegrationError("loaded robot class differs from endpoint registration")
            robot_types[f"agent_{record['agent_id']}"] = record["robot_type"]
        self._relocation_agent_identity = {
            "registration_profile_digest": self.registry["digest"],
            "robot_types": robot_types,
        }
        observer_ids = tuple(
            record["agent_id"]
            for record in self.registry["endpoints"]
            if any(
                operation in {OBSERVATION_OPERATION, OBSERVATION_ACQUIRE_OPERATION}
                for operation in record["operations"]
            )
        )
        self._detection_sensors = (
            inspect_perception(environment, observer_ids) if self._config.enable_observation else {}
        )
        acquisition = getattr(self._config, "enable_observation_acquisition", False)
        recoveries = getattr(self._config, "contract_recovery_attempts", 0)
        if acquisition and any(
            policy._skills.get(policy._name_to_idx.get("nav_to_obj")) is None
            for agent, policy in enumerate(actor._active_policies)
            if agent in observer_ids
        ):
            raise IntegrationError("active observation requires original navigation skills")
        self._write_json("endpoint-registry-used.json", self.registry)
        self._write_json(
            "perception-readiness.json",
            {
                "enabled": self._config.enable_observation,
                "sensors": {
                    str(agent): sensor for agent, sensor in self._detection_sensors.items()
                },
                "source": "loaded-original-sensors-and-cameras",
            },
        )
        path = self._evidence_dir() / "preassignment-feasibility.json"
        if path.is_file():
            matrix = json.loads(path.read_bytes())
            if matrix["schema_version"] not in (
                OPERATION_FEASIBILITY_SCHEMA,
                "roboguide.deployment-intent-feasibility/v0.3",
            ):
                raise IntegrationError("live feasibility has an unsupported parent profile")
            body = {key: value for key, value in matrix.items() if key != "digest"}
            body.update(
                schema_version=LIVE_FEASIBILITY_SCHEMA,
                finality_profile=FINALITY_PROFILE,
                execution_profile={
                    "mode": LIVE_PROFILE,
                    "registry_digest": self.registry["digest"],
                    "endpoints": [
                        {
                            "node_id": record["node_id"],
                            "agent_id": record["agent_id"],
                            "operations": record["operations"],
                        }
                        for record in self.registry["endpoints"]
                    ],
                },
            )
            self._write_json(
                "preassignment-feasibility.json", {**body, "digest": preassignment_digest(body)}
            )
        how = json.loads((self._evidence_dir() / "local-how-profile.json").read_bytes())
        self._write_json(
            "local-how-profile.json",
            {
                "schema_version": "roboguide.habitat-local-how-profile/v0.10"
                if acquisition or recoveries
                else "roboguide.habitat-local-how-profile/v0.9",
                "base_profile": how,
                "execution_profile": LIVE_PROFILE,
                "endpoint_registry_digest": self.registry["digest"],
                "observation_enabled": self._config.enable_observation,
                "unassigned_policy": "original-wait-model-free",
                "official_success_authority": "habitat-pddl",
                **(
                    {
                        "execution_options": {
                            "enable_observation_acquisition": acquisition,
                            "observation_acquisition_max_steps": (
                                self._config.observation_acquisition_max_steps
                            ),
                            "contract_recovery_attempts": recoveries,
                        }
                    }
                    if acquisition or recoveries
                    else {}
                ),
            },
        )

    def supported_operations(self) -> tuple[str, ...]:
        """Advertise observation only after actual sensor readiness has been inspected."""
        operations = super().supported_operations()
        if getattr(self, "_detection_sensors", {}):
            return (
                (*operations, OBSERVATION_OPERATION, OBSERVATION_ACQUIRE_OPERATION)
                if getattr(self._config, "enable_observation_acquisition", False)
                else (*operations, OBSERVATION_OPERATION)
            )
        return operations

    def _initial_position(self, agent: int) -> tuple[float, float, float]:
        """Read the frozen reset position without inferring or resampling an initial state."""
        value = self._initial_positions[str(agent)]
        return value[0], value[1], value[2]

    def execute_live(
        self,
        initial: Mapping[int, CanonicalInvocation],
        arrivals: Callable[[], Mapping[int, CanonicalInvocation]],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
        completed: Callable[[int, CanonicalInvocation, LocalExecutionOutcome], None],
        wait_seconds: float,
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Preserve primary faults and physical evidence even when post-reset setup fails."""
        self._live_terminal_recorded = False
        self._live_completed_steps = 0
        self._live_final_info: dict[str, Any] = {}
        try:
            return self._execute_live(
                initial, arrivals, cancellation_requested, running, completed, wait_seconds
            )
        except BaseException as error:
            if not self._live_terminal_recorded:
                self._record_terminal_diagnostics(
                    self._habitat_env,
                    self._live_completed_steps,
                    f"execution_exception:{type(error).__name__}",
                )
            try:
                from .shared_world import _archive_official_metrics

                assert self._habitat_env is not None

                summary = {
                    "identity": {
                        "episode_id": self._config.episode_id,
                        "scene_id": str(self._habitat_env.current_episode.scene_id),
                        "habitat_seed": self._config.seed,
                        "episode_reset_count": 1,
                        "simulator_worlds": 1,
                        "initial_agent_positions": self._initial_positions,
                        "simulator_steps": self._live_completed_steps,
                        "episode_terminated": bool(self._habitat_env.episode_over),
                    },
                    "final_info": json_observation(self._live_final_info),
                    "action_trace_collection": self._action_trace_stats(),
                    "execution_profile": LIVE_PROFILE,
                    "termination_reason": f"execution_exception:{type(error).__name__}",
                }
                _archive_official_metrics(self, summary)
                self._write_json("live-world-failure.json", summary)
            except Exception:
                _LOG.exception("live failure snapshot unavailable")
            raise

    def _execute_live(
        self,
        initial: Mapping[int, CanonicalInvocation],
        arrivals: Callable[[], Mapping[int, CanonicalInvocation]],
        cancellation_requested: Callable[[], bool],
        running: Callable[[int, str], None],
        completed: Callable[[int, CanonicalInvocation, LocalExecutionOutcome], None],
        wait_seconds: float,
    ) -> tuple[dict[int, LocalExecutionOutcome], dict[str, Any]]:
        """Drive one original act/step per physical step, accepting dispatches at boundaries.

        With no active assignment, wait without advancing Habitat or querying a
        model. Local terminal facts are delivered early so Control may satisfy
        prerequisites and dispatch the next Task. Official verdicts are only
        archived from the final real world metric, never inferred from those facts.
        """
        from habitat_mas.utils import AgentArguments  # type: ignore[import-not-found]

        gym, environment, actor, access = self._require_initialized()
        torch, device = self._runtime["torch"], self._runtime["device"]
        action_shape, discrete = self._runtime["get_action_space_info"](actor.policy_action_space)
        hidden = torch.zeros((1, *actor.hidden_state_shape), device=device)
        previous = torch.zeros(
            1, *action_shape, device=device, dtype=torch.long if discrete else torch.float
        )
        masks = torch.zeros(1, *access.masks_shape, device=device, dtype=torch.bool)
        observations = self._prepared_observations
        batch = self._batch(observations)
        # Original task text acquisition can sample a missing zero-position
        # start. Observation-only dispatch must never activate that path.
        text: dict[str, Any] = {}
        robot_types = {
            f"agent_{record['agent_id']}": record["robot_type"]
            for record in self.registry["endpoints"]
        }
        stage2_context_loaded = False
        assignment = {
            name: AgentArguments(
                robot_id=name,
                robot_type=robot_type,
                task_description="",
                subtask_description="Nothing to do",
                chat_history=[],
            )
            for name, robot_type in robot_types.items()
        }
        if set(assignment) != {f"agent_{agent}" for agent in self._agent_ids}:
            raise IntegrationError("live Stage2 argument identities do not cover the registry")
        ledger = LiveSlotLedger(self._agent_ids)
        pending = dict(initial)
        latest: dict[int, LocalExecutionOutcome] = {}
        history: list[dict[str, Any]] = []
        acquisition_starts: dict[int, int] = {}
        acquisition_digests: dict[int, str] = {}
        sequence: list[str] = []
        steps, done = 0, False
        info: dict[str, Any] = {}
        reason = "execution_exception:live_setup"
        module, original = self._install_assignment(assignment)
        bindings: LivePolicyBindings | None = None
        waited_from: float | None = None
        try:
            bindings = LivePolicyBindings(self, actor, assignment, lambda: steps)
            while steps < self._config.max_steps:
                if cancellation_requested():
                    reason = "cancellation"
                    break
                incoming = dict(arrivals())
                if cancellation_requested():
                    reason = "cancellation"
                    break
                if set(incoming) & set(pending):
                    raise IntegrationError("live boundary contains overlapping dispatches")
                pending.update(incoming)
                for agent, invocation in pending.items():
                    record = self.registry["endpoints"][agent] if agent in self._agent_ids else {}
                    if invocation.operation not in record.get("operations", []):
                        raise IntegrationError(
                            "dispatch operation is not registered on its endpoint"
                        )
                    ledger.admit(agent, invocation)
                    self._bind_navigation_diagnostics(ledger.active)
                    running(
                        agent, f"Control assignment {invocation.task_id} activated at step {steps}"
                    )
                    if isinstance(invocation, CanonicalObservationInvocation) and not isinstance(
                        invocation, CanonicalObservationAcquisitionInvocation
                    ):
                        semantic = json.loads(
                            (
                                self._evidence_dir() / "authoritative-semantic-evidence.json"
                            ).read_bytes()
                        )
                        observed = observe_detection(
                            invocation,
                            environment,
                            cached_detection_observations(
                                gym, observations, self._detection_sensors[agent]
                            ),
                            agent,
                            self._detection_sensors[agent],
                            steps,
                            semantic["digest"],
                        )
                        self._write_json(f"observation-{invocation.request_key()}.json", observed)
                        outcome = self._pair_outcome(
                            "COMPLETED" if observed["status"] == "observed" else "FAILED",
                            "condition observation: " + observed["status"],
                            invocation,
                            str(environment.current_episode.scene_id),
                            steps,
                            self._initial_position(agent),
                            agent,
                            sequence,
                            local_skill_completed=False,
                            episode_terminated=False,
                            terminal_basis="condition-observed"
                            if observed["status"] == "observed"
                            else "condition-observation-unavailable",
                        )
                        completed(agent, ledger.finish(agent), outcome)
                        latest[agent] = outcome
                        history.append(
                            {
                                "invocation": invocation.as_dict(),
                                "outcome": outcome.as_dict(),
                                "observation": observed,
                            }
                        )
                        continue
                    if isinstance(invocation, CanonicalObservationAcquisitionInvocation):
                        if not getattr(self._config, "enable_observation_acquisition", False):
                            raise IntegrationError("active observation profile is disabled")
                        acquisition_starts[agent] = steps
                        acquisition_digests[agent] = json.loads(
                            (
                                self._evidence_dir() / "authoritative-semantic-evidence.json"
                            ).read_bytes()
                        )["digest"]
                    else:
                        self._admit_spatial_feasibility(agent, invocation, environment)
                    self._admit_relocation_source(agent, invocation, environment)
                    self._write_text(
                        f"subtask-agent-{agent}-{invocation.request_key()[:16]}.txt",
                        self._subtask(invocation),
                    )
                    if not stage2_context_loaded:
                        text = environment.task.get_task_text_context()
                        text["episode_id"] = environment.current_episode.episode_id
                        if self._assignment_robot_types(text) != robot_types:
                            raise IntegrationError(
                                "Stage2 identities differ from the live registry"
                            )
                        stage2_context_loaded = True
                    arguments = AgentArguments(
                        robot_id=f"agent_{agent}",
                        robot_type=robot_types[f"agent_{agent}"],
                        task_description=invocation.objective,
                        subtask_description=self._subtask(invocation),
                        chat_history=[],
                    )
                    bindings.bind(agent, invocation, arguments)
                pending.clear()
                if ledger.complete():
                    reason = "all_local_slots_finished"
                    break
                if not ledger.active:
                    if waited_from is None:
                        waited_from = time.monotonic()
                    if time.monotonic() - waited_from >= wait_seconds:
                        reason = "assignment_wait_timeout"
                        break
                    time.sleep(0.05)
                    continue
                waited_from = None
                policy_input = observations
                action = actor.act(
                    batch,
                    hidden,
                    previous,
                    masks,
                    deterministic=False,
                    envs_text_context=[text],
                    index_len_recurrent_hidden_states=actor.hidden_state_shape_lens,
                    index_len_prev_actions=actor.policy_action_space_shape_lens,
                    save_chat_history=False,
                    save_chat_history_dir=str(self._evidence_dir() / "chat-history"),
                )
                skills = self._current_skills(actor)
                self._extend_skill_sequence(sequence, skills)
                physical = action.env_actions.detach().cpu()[0].numpy()
                discard = self._prepare_navigation_step(
                    physical, gym, environment, ledger.active, steps
                )
                try:
                    observations, done, info = self._gym_step_result(gym.step(physical))
                finally:
                    discard()
                steps += 1
                self._live_completed_steps, self._live_final_info = steps, info
                for feedback in bindings.feedbacks.values():
                    feedback.physical_step()
                if action.should_inserts is None:
                    hidden = action.rnn_hidden_states
                    previous.copy_(action.actions)
                else:
                    actor.update_hidden_state(hidden, previous, action)
                batch = self._batch(observations)
                masks = torch.tensor([[not done]], dtype=torch.bool, device=device).repeat(
                    1, *access.masks_shape
                )
                self._append_action_trace(steps, skills, info)
                self._diagnostics.record_step(
                    steps,
                    skills,
                    physical,
                    environment,
                    actor,
                    done,
                    info,
                    observations,
                    policy_input,
                )
                self._record_video(steps, observations, info)
                for agent, invocation in list(ledger.active.items()):
                    current_feedback = bindings.feedbacks.get(agent)
                    terminal_failure = (
                        current_feedback.terminal_failure(f"agent_{agent}")
                        if current_feedback is not None
                        else None
                    )
                    if terminal_failure is not None:
                        self._best_effort_write_json(
                            "live-world-failure.json",
                            {
                                "schema_version": "roboguide.live-world-failure/v0.1",
                                "failure_owner": "SUT_SYSTEM",
                                "failure_stage": "stage2_local_skill",
                                "failure_basis": terminal_failure.get("failure_basis"),
                                "invocation": invocation.as_dict(),
                                "local_failure": terminal_failure,
                                "simulator_steps": steps,
                                "final_info": json_observation(info),
                            },
                            "Stage2 local failure",
                        )
                        outcome = self._pair_outcome(
                            "FAILED",
                            "original Stage2 skill requested episode termination",
                            invocation,
                            str(environment.current_episode.scene_id),
                            steps,
                            self._initial_position(agent),
                            agent,
                            sequence,
                            local_skill_completed=False,
                            episode_terminated=bool(done or environment.episode_over),
                            terminal_basis="original-skill-bad-terminate",
                        )
                        completed(agent, ledger.finish(agent), outcome)
                        latest[agent] = outcome
                        history.append(
                            {
                                "invocation": invocation.as_dict(),
                                "outcome": outcome.as_dict(),
                                "local_failure": terminal_failure,
                            }
                        )
                        bindings.finish(agent)
                        self._bind_navigation_diagnostics(ledger.active)
                        continue
                    acquisition = isinstance(invocation, CanonicalObservationAcquisitionInvocation)
                    acquisition_receipt = None
                    acquisition_expired = False
                    if isinstance(invocation, CanonicalObservationAcquisitionInvocation):
                        acquisition_receipt = observe_detection(
                            invocation,
                            environment,
                            cached_detection_observations(
                                gym, observations, self._detection_sensors[agent]
                            ),
                            agent,
                            self._detection_sensors[agent],
                            steps,
                            acquisition_digests[agent],
                        )
                        budget = self._config.observation_acquisition_max_steps
                        acquisition_expired = steps - acquisition_starts[agent] >= budget
                        acquisition_receipt.update(
                            acquisition_profile="bounded-visual-acquisition/v0.1",
                            acquisition_start_step=acquisition_starts[agent],
                            acquisition_steps=steps - acquisition_starts[agent],
                            acquisition_max_steps=budget,
                        )
                    local_finished = (
                        acquisition_receipt["condition_holds"] is True
                        if acquisition_receipt is not None
                        else current_feedback.operation_completed(f"agent_{agent}")
                        if isinstance(invocation, CanonicalRelocationInvocation)
                        and current_feedback is not None
                        else skills[agent]
                        in {"nav_to_obj", "nav_to_goal", "nav_to_receptacle_by_name"}
                        and (
                            _observation_true(
                                observations, f"agent_{agent}_has_finished_oracle_nav"
                            )
                            or self._oracle_nav_finished_for(agent)
                        )
                    )
                    if not local_finished and not done and not acquisition_expired:
                        continue
                    successful = local_finished or (
                        not acquisition and info.get("pddl_success") is True
                    )
                    if acquisition_receipt is not None:
                        try:
                            self._write_json(
                                f"observation-{invocation.request_key()}.json", acquisition_receipt
                            )
                        except Exception:
                            _LOG.exception("active observation receipt archival unavailable")
                    outcome = self._pair_outcome(
                        "COMPLETED" if successful else "FAILED",
                        "condition detected from actual visual cache"
                        if acquisition and successful
                        else "active observation budget exhausted"
                        if acquisition_expired
                        else "original local skill completed"
                        if local_finished
                        else "original Habitat episode ended",
                        invocation,
                        str(environment.current_episode.scene_id),
                        steps,
                        self._initial_position(agent),
                        agent,
                        sequence,
                        local_skill_completed=bool(local_finished) and not acquisition,
                        episode_terminated=done,
                        terminal_basis="condition-observed-true"
                        if acquisition and successful
                        else "condition-acquisition-budget-exhausted"
                        if acquisition_expired
                        else "original-local-skill"
                        if local_finished
                        else "habitat-pddl-success"
                        if successful
                        else "episode-terminated-before-success",
                    )
                    completed(agent, ledger.finish(agent), outcome)
                    latest[agent] = outcome
                    history.append(
                        {
                            "invocation": invocation.as_dict(),
                            "outcome": outcome.as_dict(),
                            **(
                                {"observation": acquisition_receipt}
                                if acquisition_receipt is not None
                                else {}
                            ),
                        }
                    )
                    bindings.finish(agent)
                    self._bind_navigation_diagnostics(ledger.active)
                if done or environment.episode_over:
                    reason = "episode_done"
                    break
                if self._config.step_period_ms:
                    time.sleep(self._config.step_period_ms / 1000)
            else:
                reason = "step_budget_exhausted"
        except BaseException as error:
            reason = f"execution_exception:{type(error).__name__}"
            raise
        finally:
            try:
                if bindings is not None:
                    bindings.close()
            finally:
                module.group_discussion = original
                try:
                    self._flush_action_trace()
                except Exception:
                    _LOG.exception("live action trace flush unavailable")
                self._record_terminal_diagnostics(environment, steps, reason)
                self._live_terminal_recorded = True
        for agent, invocation in list(ledger.active.items()):
            outcome = self._pair_outcome(
                "CANCELLED" if reason == "cancellation" else "FAILED",
                reason,
                invocation,
                str(environment.current_episode.scene_id),
                steps,
                self._initial_position(agent),
                agent,
                sequence,
                local_skill_completed=False,
                episode_terminated=bool(done or environment.episode_over),
                terminal_basis=reason,
            )
            completed(agent, ledger.finish(agent), outcome)
            latest[agent] = outcome
            history.append({"invocation": invocation.as_dict(), "outcome": outcome.as_dict()})
        return latest, {
            "identity": {
                "episode_id": self._config.episode_id,
                "scene_id": str(environment.current_episode.scene_id),
                "habitat_seed": self._config.seed,
                "episode_reset_count": 1,
                "simulator_worlds": 1,
                "initial_agent_positions": self._initial_positions,
                "simulator_steps": steps,
                "episode_terminated": bool(done or environment.episode_over),
            },
            "final_info": json_observation(info),
            "action_trace_collection": self._action_trace_stats(),
            "task_outcomes": history,
            "execution_profile": LIVE_PROFILE,
            "termination_reason": reason,
        }
