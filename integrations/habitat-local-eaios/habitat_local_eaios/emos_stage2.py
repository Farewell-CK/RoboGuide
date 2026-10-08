"""Original EMOS Stage2 policy execution behind the Local EAIOS boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .backend import LocalExecutionOutcome, _observation_true, habitat_config_overrides
from .diagnostics import BufferedJsonlWriter
from .evidence_io import write_text_atomic
from .execution_progress import NavigationProgressPublisher
from .idle_endpoint import PassiveIdleAgent, PassiveIdleBinding, install_passive_idle_agents
from .model import (
    CanonicalInvocation,
    CanonicalRelocationInvocation,
    IntegrationError,
)
from .navigation_preparation import (
    NAVIGATION_PREPARATION_PROFILE,
    NavigationPreparationFailure,
    prepare_navigation_actions,
)
from .navmesh_profile import STEP_AWARE_PROFILE
from .source_provenance import build_runtime_source_manifest
from .spatial_navigation import (
    GOAL_AWARE_ARRIVAL_PROFILE,
    GOAL_AWARE_POINT_RESOLVER,
    SPATIAL_ARRIVAL_PROFILE,
)
from .stage2_contract import (
    RelocationExecutionState,
    Stage2ActionAudit,
    Stage2ContractViolation,
    Stage2ExecutionContract,
    install_stage2_contract_guard,
)
from .stage2_feedback import FEEDBACK_PROFILE, Stage2ExecutionFeedback

_LOG = logging.getLogger(__name__)


def _configure_goal_region_navigation(
    config: Any,
    read_write: Callable[[Any], Any],
    *,
    step_aware: bool = False,
    spatial_arrival: bool = False,
    goal_aware_arrival: bool = False,
) -> None:
    """Select the adapter-owned Oracle subclass before constructing Habitat.

    Only the configured original differential-base navigation action is
    supported. A different vendor action fails closed instead of silently
    changing an unrelated skill or running an undisclosed Local How profile.
    """
    from .goal_region_action import GoalRegionOracleNavDiffBaseAction

    if goal_aware_arrival:
        if not spatial_arrival or not step_aware:
            raise IntegrationError(
                "goal-aware navigation arrival requires spatial navigation arrival"
            )
        from .spatial_navigation_action import LiveGoalArrivalGoalRegionOracleNavDiffBaseAction

        action_name = LiveGoalArrivalGoalRegionOracleNavDiffBaseAction.__name__
    elif spatial_arrival:
        if not step_aware:
            raise IntegrationError("spatial navigation arrival requires step-aware navmesh")
        from .spatial_navigation_action import SpatialArrivalGoalRegionOracleNavDiffBaseAction

        action_name = SpatialArrivalGoalRegionOracleNavDiffBaseAction.__name__
    elif step_aware:
        from .goal_region_action import StepAwareGoalRegionOracleNavDiffBaseAction

        action_name = StepAwareGoalRegionOracleNavDiffBaseAction.__name__
    else:
        action_name = GoalRegionOracleNavDiffBaseAction.__name__
    actions = config.habitat.task.actions
    agent_count = len(config.habitat.simulator.agents_order)
    keys = [f"agent_{agent_id}_oracle_nav_action" for agent_id in range(agent_count)]
    for key in keys:
        if key not in actions or actions[key].type != "OracleNavDiffBaseAction":
            raise IntegrationError(
                f"goal-region navigation requires original OracleNavDiffBaseAction at {key}"
            )
    with read_write(config):
        for key in keys:
            actions[key].type = action_name


def format_stage2_subtask(invocation: CanonicalInvocation, mode: str) -> str:
    """Preserve the objective while binding Stage2 to the committed operation.

    The EMOS model still chooses its own tool call. This text exposes the
    committed semantic fields that the local contract guard will enforce; it
    never changes a model-selected action or supplies a replacement action.
    """
    if mode not in {"entity-grounded", "natural-objective"}:
        raise IntegrationError(f"unsupported Stage2 subtask mode {mode!r}")
    if isinstance(invocation, CanonicalRelocationInvocation):
        object_ref = json.dumps(invocation.object_ref, ensure_ascii=False)
        source = json.dumps(invocation.source, ensure_ascii=False)
        destination = json.dumps(invocation.destination, ensure_ascii=False)
        if mode == "entity-grounded":
            return (
                f"Relocate object {object_ref} from source {source} to destination {destination}. "
                "Use the committed object and destination exactly."
            )
        return (
            f"{invocation.objective}\n\n"
            f"Committed relocation object: {object_ref}; source: {source}; "
            f"destination: {destination}. Use these exact semantic identities for "
            "nav_to_obj, pick, and place. Do not substitute another object or location."
        )
    if mode == "entity-grounded":
        return f"Navigate to {invocation.destination}."
    destination = json.dumps(invocation.destination, ensure_ascii=False)
    return (
        f"{invocation.objective}\n\n"
        f"Assigned navigation destination for this execution: {destination}. "
        "If you call nav_to_obj, use this exact entity as target_obj. "
        "Other entities in the overall objective are not alternative destinations "
        "for this execution."
    )


def _make_episode_gym_environment(
    config: Any,
    episode_id: str,
    make_dataset: Callable[..., Any],
    make_gym_from_config: Callable[..., Any],
) -> tuple[Any, Any, Any]:
    """Construct Habitat with exactly the requested episode loaded from startup.

    Habitat-Sim initializes scene-specific state while the Gym environment is
    constructed.  Replacing ``habitat_env.episodes`` afterwards changes the
    episode used by ``reset()``, but it does not reproduce a simulator that was
    created with that episode initially.  Load and uniquely select the frozen
    episode before creating the simulator so seed-controlled agent placement is
    comparable with the native EMOS path.

    Raises:
        IntegrationError: If the configured dataset does not contain exactly
            one episode with the requested identity, or if the constructed
            environment does not retain that exact selection.
    """
    dataset = make_dataset(
        id_dataset=config.habitat.dataset.type,
        config=config.habitat.dataset,
    )
    matches = [episode for episode in dataset.episodes if str(episode.episode_id) == episode_id]
    if len(matches) != 1:
        raise IntegrationError(f"Habitat episode {episode_id!r} is not uniquely available")
    episode = matches[0]
    dataset.episodes = [episode]
    gym_env = make_gym_from_config(config, dataset=dataset)
    habitat_env = gym_env.habitat_env
    loaded = [
        candidate for candidate in habitat_env.episodes if str(candidate.episode_id) == episode_id
    ]
    if len(habitat_env.episodes) != 1 or len(loaded) != 1:
        gym_env.close()
        raise IntegrationError(
            f"Habitat episode {episode_id!r} was not retained as the sole startup episode"
        )
    return gym_env, habitat_env, loaded[0]


def _add_operator_view_sensors(
    config: Any,
    get_agent_config: Callable[[Any, int], Any],
    read_write: Callable[[Any], Any],
) -> None:
    """Enable EMOS' declared third-person sensors for explicit operator capture.

    This mirrors the original Habitat Baselines evaluator's video setup: the
    deployment config already owns ``extra_sim_sensors``; capture only attaches
    those sensors to each configured agent before simulator construction.  It
    never changes task sensors, policy inputs, actions, goals, or success rules.
    """
    extra_sensors = config.habitat_baselines.eval.extra_sim_sensors
    agent_count = len(config.habitat.simulator.agents_order)
    for agent_id in range(agent_count):
        agent_config = get_agent_config(config.habitat.simulator, agent_id)
        with read_write(agent_config.sim_sensors):
            agent_config.sim_sensors.update(extra_sensors)
    if config.habitat.gym.obs_keys is None:
        return
    with read_write(config):
        for agent_id in range(agent_count):
            agent_name = config.habitat.simulator.agents_order[agent_id]
            for sensor in extra_sensors.values():
                key = f"{agent_name}_{sensor.uuid}" if agent_count > 1 else sensor.uuid
                if key not in config.habitat.gym.obs_keys:
                    config.habitat.gym.obs_keys.append(key)


class EmosStage2Runtime:
    """Own one official EMOS policy, skill stack, and Habitat environment."""

    def __init__(self, config: Any) -> None:
        """Retain config while deferring Habitat imports to the simulator process."""
        self._config = config
        self._gym_env: Any | None = None
        self._habitat_env: Any | None = None
        self._episode: Any | None = None
        self._actor: Any | None = None
        self._agent_access: Any | None = None
        self._runtime: dict[str, Any] = {}
        self._stage2_feedback: Stage2ExecutionFeedback | None = None
        self._last_navigation_preparation_failure: dict[str, Any] | None = None
        self._action_trace_writer = BufferedJsonlWriter(self._evidence_dir() / "action_trace.jsonl")

    def initialize(self) -> None:
        """Build the policy and transformed environment used by EMOS evaluation."""
        if self._gym_env is not None:
            return
        try:
            import torch  # type: ignore[import-not-found]
            from habitat import make_dataset  # type: ignore[import-not-found]
            from habitat.config import read_write  # type: ignore[import-not-found]
            from habitat.config.default import (  # type: ignore[import-not-found]
                get_agent_config,
            )
            from habitat.gym import make_gym_from_config  # type: ignore[import-not-found]
            from habitat_baselines.common.env_spec import (  # type: ignore[import-not-found]
                EnvironmentSpec,
            )
            from habitat_baselines.common.obs_transformers import (  # type: ignore[import-not-found]
                apply_obs_transforms_batch,
                apply_obs_transforms_obs_space,
                get_active_obs_transforms,
            )
            from habitat_baselines.config.default import (  # type: ignore[import-not-found]
                get_config,
            )
            from habitat_baselines.rl.multi_agent.multi_agent_access_mgr import (  # type: ignore[import-not-found]
                MultiAgentAccessMgr,
            )
            from habitat_baselines.utils.common import (  # type: ignore[import-not-found]
                batch_obs,
                get_action_space_info,
            )

            config = get_config(
                str(self._config.config_path),
                overrides=habitat_config_overrides(self._config.seed),
            )
            goal_region_enabled = bool(getattr(self._config, "goal_region_navigation", False))
            step_aware_enabled = bool(getattr(self._config, "step_aware_navmesh", False))
            spatial_arrival_enabled = bool(
                getattr(self._config, "spatial_navigation_arrival", False)
            )
            goal_aware_arrival_enabled = bool(
                getattr(self._config, "goal_aware_navigation_arrival", False)
            )
            if goal_region_enabled:
                _configure_goal_region_navigation(
                    config,
                    read_write,
                    step_aware=step_aware_enabled,
                    spatial_arrival=spatial_arrival_enabled,
                    goal_aware_arrival=goal_aware_arrival_enabled,
                )
            if spatial_arrival_enabled:
                from habitat.gym.gym_wrapper import (  # type: ignore[import-not-found]
                    continuous_vector_action_to_hab_dict,
                )
            if self._config.video_path is not None or self._config.live_preview_path is not None:
                _add_operator_view_sensors(config, get_agent_config, read_write)
            gym_env, habitat_env, episode = _make_episode_gym_environment(
                config,
                self._config.episode_id,
                make_dataset,
                make_gym_from_config,
            )
            if self._config.agent_id >= len(config.habitat.simulator.agents_order):
                gym_env.close()
                raise IntegrationError("configured Habitat agent_id is out of range")
            transforms = get_active_obs_transforms(config)
            observation_space = apply_obs_transforms_obs_space(
                gym_env.observation_space, transforms
            )
            env_spec = EnvironmentSpec(
                observation_space=observation_space,
                action_space=gym_env.action_space,
                orig_action_space=gym_env.original_action_space,
            )
            device = torch.device("cpu")
            access = MultiAgentAccessMgr(
                config,
                env_spec,
                False,
                device,
                1,
                lambda: 0.0,
            )
            actor = access.actor_critic
            access.eval()
            self._gym_env = gym_env
            self._habitat_env = habitat_env
            self._episode = episode
            self._actor = actor
            self._agent_access = access
            self._runtime = {
                "apply_transforms": apply_obs_transforms_batch,
                "batch_obs": batch_obs,
                "device": device,
                "get_action_space_info": get_action_space_info,
                "torch": torch,
                "transforms": transforms,
                "habitat_config": config,
            }
            if spatial_arrival_enabled:
                self._runtime["decode_navigation_action"] = continuous_vector_action_to_hab_dict
            self._write_json(
                "local-how-profile.json",
                {
                    "schema_version": (
                        "roboguide.habitat-local-how-profile/v0.7"
                        if goal_aware_arrival_enabled
                        else "roboguide.habitat-local-how-profile/v0.6"
                        if spatial_arrival_enabled
                        else "roboguide.habitat-local-how-profile/v0.4"
                        if step_aware_enabled
                        else "roboguide.habitat-local-how-profile/v0.3"
                        if getattr(self._config, "reset_route_geometry", False)
                        else "roboguide.habitat-local-how-profile/v0.2"
                    ),
                    "navigation_point_resolver": (
                        GOAL_AWARE_POINT_RESOLVER
                        if goal_aware_arrival_enabled
                        else "official-any-at-agent-navmesh/v0.1"
                        if goal_region_enabled
                        else "original-emos-oracle"
                    ),
                    "official_success_authority": "habitat-pddl",
                    "stage2_execution_feedback_profile": FEEDBACK_PROFILE,
                    "reset_route_support_enabled": bool(
                        getattr(self._config, "reset_route_support", False)
                    ),
                    **(
                        {"reset_route_geometry_enabled": True}
                        if getattr(self._config, "reset_route_geometry", False)
                        else {}
                    ),
                    **(
                        {"navmesh_resolution_profile": STEP_AWARE_PROFILE}
                        if step_aware_enabled
                        else {}
                    ),
                    **(
                        {
                            "navigation_arrival_profile": (
                                GOAL_AWARE_ARRIVAL_PROFILE
                                if goal_aware_arrival_enabled
                                else SPATIAL_ARRIVAL_PROFILE
                            ),
                            "navigation_preparation_profile": NAVIGATION_PREPARATION_PROFILE,
                        }
                        if spatial_arrival_enabled
                        else {}
                    ),
                    **(
                        {"reset_route_probe_policy": "conservative-stop-envelope/v0.1"}
                        if goal_aware_arrival_enabled
                        else {}
                    ),
                },
            )
            self._write_json(
                "runtime-source-manifest.json",
                build_runtime_source_manifest(
                    (
                        "habitat.tasks.rearrange.actions.habitat_mas_actions",
                        "habitat.tasks.rearrange.actions.oracle_nav_action",
                        "habitat_sim",
                        "habitat_baselines.rl.hrl.hl.llm_policy",
                        "habitat_baselines.rl.hrl.skills.wait",
                        "habitat_baselines.rl.multi_agent.multi_agent_access_mgr",
                        "habitat_baselines.rl.multi_agent.multi_llm_policy",
                        "habitat_mas.agents.crab_agent",
                        "habitat_mas.utils.models",
                        "habitat_local_eaios.stage2_feedback",
                        "habitat_local_eaios.emos_stage2",
                        "habitat_local_eaios.goal_region_action",
                        "habitat_local_eaios.goal_region_navigation",
                        "habitat_local_eaios.navmesh_profile",
                        "habitat_local_eaios.reset_route_support",
                        "habitat_local_eaios.navmesh_region",
                        "habitat_local_eaios.idle_endpoint",
                        "habitat_local_eaios.shared_world",
                        "habitat_local_eaios.stage2_contract",
                        *(
                            (
                                "habitat.tasks.rearrange.actions.actions",
                                "habitat_local_eaios.spatial_navigation",
                                "habitat_local_eaios.spatial_navigation_action",
                                "habitat.gym.gym_wrapper",
                                "habitat_local_eaios.navigation_preparation",
                            )
                            if spatial_arrival_enabled
                            else ()
                        ),
                    )
                ),
            )
        except IntegrationError:
            raise
        except Exception as error:
            self.close()
            raise IntegrationError(f"EMOS Stage2 initialization failed: {error}") from error

    def execute(
        self,
        invocation: CanonicalInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Run the assigned agent's original Stage2 path and keep peers passive."""
        gym_env, habitat_env, actor, access = self._require_initialized()
        try:
            habitat_env.episodes = [self._episode]
            observations = gym_env.reset()
            if isinstance(observations, tuple):
                observations = observations[0]
            initial = self._agent_position()
            scene_id = str(habitat_env.current_episode.scene_id)
            text_context = habitat_env.task.get_task_text_context()
            text_context["episode_id"] = habitat_env.current_episode.episode_id
            self._write_text("scene_description.txt", str(text_context["scene_description"]))
            self._write_text("subtask.txt", self._subtask(invocation))
            running(
                f"original EMOS Stage2 started episode {self._config.episode_id} for "
                f"{invocation.destination}"
            )
            assignment = self._assigned_arguments(text_context, invocation)
            return self._policy_loop(
                observations,
                text_context,
                assignment,
                invocation,
                initial,
                scene_id,
                actor,
                access,
                gym_env,
                habitat_env,
                cancellation_requested,
            )
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(f"original EMOS Stage2 execution failed: {error}") from error

    def readiness_detail(self) -> str:
        """Describe the pinned original EMOS Stage2 execution environment."""
        if self._actor is None:
            return "EMOS Stage2 policy is not initialized"
        return (
            f"original EMOS Stage2 episode {self._config.episode_id} is loaded for "
            f"agent {self._config.agent_id}"
        )

    def close(self) -> None:
        """Close the environment and discard process-local policy state."""
        if self._gym_env is not None:
            self._gym_env.close()
        self._gym_env = None
        self._habitat_env = None
        self._episode = None
        self._actor = None
        self._agent_access = None
        self._runtime = {}

    def _policy_loop(
        self,
        observations: Any,
        text_context: dict[str, Any],
        assignment: dict[str, Any],
        invocation: CanonicalInvocation,
        initial: tuple[float, float, float],
        scene_id: str,
        actor: Any,
        access: Any,
        gym_env: Any,
        habitat_env: Any,
        cancellation_requested: Callable[[], bool],
    ) -> LocalExecutionOutcome:
        """Mirror the EMOS evaluator loop while preserving RoboGuide cancellation."""
        self._last_navigation_preparation_failure = None
        self._last_policy_observations = observations
        torch = self._runtime["torch"]
        device = self._runtime["device"]
        batch = self._batch(observations)
        action_shape, discrete = self._runtime["get_action_space_info"](actor.policy_action_space)
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
        steps = 0
        step_offset = self._policy_step_offset()
        progress = NavigationProgressPublisher(
            getattr(self._config, "progress_directory", None), invocation, self._config.agent_id
        )
        is_relocation = isinstance(invocation, CanonicalRelocationInvocation)
        skill_sequence: list[str] = []
        chat_history_root = self._evidence_dir() / "chat-history"
        (chat_history_root / str(text_context["episode_id"])).mkdir(parents=True, exist_ok=True)
        module, original_group_discussion = self._install_assignment(assignment)
        idle_binding: PassiveIdleBinding | None = None
        contract_restore: Callable[[], None] | None = None
        contract_failure: Stage2ContractViolation | None = None
        navigation_failure: NavigationPreparationFailure | None = None
        try:
            idle_binding = install_passive_idle_agents(
                actor, assignment, f"agent_{self._config.agent_id}"
            )
            contract_restore = self._install_execution_contract(
                self._single_execution_contracts(assignment, invocation),
                lambda: step_offset + steps,
            )
            while steps < self._config.max_steps:
                if cancellation_requested():
                    return self._outcome(
                        "CANCELLED",
                        "EMOS Stage2 stopped after the accepted cancellation request",
                        invocation,
                        scene_id,
                        steps,
                        initial,
                        skill_sequence,
                        local_skill_completed=False,
                        terminal_basis="cancellation",
                    )
                policy_input_observations = observations
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
                discard = self._prepare_navigation_step(
                    env_action,
                    gym_env,
                    habitat_env,
                    {self._config.agent_id: invocation},
                    step_offset + steps,
                )
                try:
                    step_result = gym_env.step(env_action)
                finally:
                    discard()
                observations, done, info = self._gym_step_result(step_result)
                self._last_policy_observations = observations
                steps += 1
                self._record_completed_physical_step()
                progress.observe(habitat_env, current_skills[self._config.agent_id])
                if action_data.should_inserts is None:
                    hidden = action_data.rnn_hidden_states
                    previous.copy_(action_data.actions)
                else:
                    actor.update_hidden_state(hidden, previous, action_data)
                batch = self._batch(observations)
                masks = torch.tensor([[not done]], dtype=torch.bool, device=device).repeat(
                    1, *access.masks_shape
                )
                self._append_action_trace(step_offset + steps, current_skills, info)
                self._observe_policy_step(
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
                target_skill = current_skills[self._config.agent_id]
                finished_key = f"agent_{self._config.agent_id}_has_finished_oracle_nav"
                if target_skill in {
                    "nav_to_obj",
                    "nav_to_goal",
                    "nav_to_receptacle_by_name",
                } and (
                    _observation_true(observations, finished_key) or self._oracle_nav_finished()
                ):
                    self._record_local_skill_completion(self._config.agent_id)
                    if not is_relocation:
                        return self._outcome(
                            "COMPLETED",
                            "original EMOS OracleNavPolicy reached its skill terminal measure",
                            invocation,
                            scene_id,
                            steps,
                            initial,
                            skill_sequence,
                            local_skill_completed=True,
                            episode_terminated=done,
                            terminal_basis="oracle-nav-skill",
                        )
                if is_relocation and self._operation_completed(self._config.agent_id):
                    return self._outcome(
                        "COMPLETED",
                        "original EMOS relocation workflow observed a completed place skill",
                        invocation,
                        scene_id,
                        steps,
                        initial,
                        skill_sequence,
                        local_skill_completed=True,
                        episode_terminated=done,
                        terminal_basis="relocation-place-skill",
                    )
                if done and bool(info.get("pddl_success", False)):
                    return self._outcome(
                        "COMPLETED",
                        "Habitat semantic task success ended the assigned navigation",
                        invocation,
                        scene_id,
                        steps,
                        initial,
                        skill_sequence,
                        local_skill_completed=False,
                        episode_terminated=True,
                        terminal_basis="habitat-pddl-success",
                    )
                if done or habitat_env.episode_over:
                    return self._outcome(
                        "FAILED",
                        "Habitat episode terminated before the assigned navigation skill completed",
                        invocation,
                        scene_id,
                        steps,
                        initial,
                        skill_sequence,
                        local_skill_completed=False,
                        episode_terminated=True,
                        terminal_basis="episode-terminated-before-success",
                    )
                if self._config.step_period_ms:
                    time.sleep(self._config.step_period_ms / 1_000)
        except Stage2ContractViolation as error:
            contract_failure = error
        except NavigationPreparationFailure as error:
            navigation_failure = error
        finally:
            try:
                if contract_restore is not None:
                    contract_restore()
            finally:
                try:
                    if idle_binding is not None:
                        self._best_effort_write_json(
                            f"idle-endpoint-{invocation.request_key()[:16]}.json",
                            idle_binding.evidence(),
                            "Stage2 termination",
                        )
                        idle_binding.restore()
                finally:
                    module.group_discussion = original_group_discussion
                    self._best_effort_flush_action_trace("Stage2 termination")
        if navigation_failure is not None:
            return self._outcome(
                "FAILED",
                str(navigation_failure),
                invocation,
                scene_id,
                steps,
                initial,
                skill_sequence,
                local_skill_completed=False,
                terminal_basis="local-navigation-preparation-failure",
            )
        if contract_failure is not None:
            return self._outcome(
                "FAILED",
                str(contract_failure),
                invocation,
                scene_id,
                steps,
                initial,
                skill_sequence,
                local_skill_completed=False,
                terminal_basis="local-contract-failure",
            )
        return self._outcome(
            "FAILED",
            f"EMOS Stage2 exceeded {self._config.max_steps} simulator steps",
            invocation,
            scene_id,
            steps,
            initial,
            skill_sequence,
            local_skill_completed=False,
            terminal_basis="step-budget-exhausted",
        )

    def _prepare_navigation_step(
        self,
        env_action: Any,
        gym_env: Any,
        habitat_env: Any,
        invocations: Mapping[int, CanonicalInvocation],
        steps: int,
    ) -> Callable[[], None]:
        """Prepare actual selected Local How commands before the one original Gym step.

        The default-off profile leaves the legacy path untouched. Expected
        bounded path failures retain exact attempt attribution and cause; other
        exceptions propagate unchanged. Evidence I/O cannot authorize movement
        or replace a preparation failure. Target/mesh preparation is execution,
        not a read-only observer or a route-feasibility authority.
        """
        if not getattr(self._config, "spatial_navigation_arrival", False):
            return lambda: None
        decoded = self._runtime["decode_navigation_action"](
            gym_env.original_action_space, gym_env.action_space, env_action
        )
        agent_count = len(self._runtime["habitat_config"].habitat.simulator.agents_order)
        try:
            return prepare_navigation_actions(
                habitat_env.task.actions, decoded, agent_ids=tuple(range(agent_count))
            )
        except NavigationPreparationFailure as error:
            document = {
                "schema_version": "roboguide.habitat-navigation-preparation-failure/v0.1",
                "profile": NAVIGATION_PREPARATION_PROFILE,
                "episode_id": str(habitat_env.current_episode.episode_id),
                "scene_id": str(habitat_env.current_episode.scene_id),
                "simulator_steps": steps,
                "failed_before_gym_step": True,
                "failure": error.as_dict(),
                "invocations": {
                    str(agent_id): invocation.as_dict()
                    for agent_id, invocation in invocations.items()
                },
            }
            document["digest"] = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                ).hexdigest()
            )
            self._last_navigation_preparation_failure = document
            self._best_effort_write_json(
                f"navigation-preparation-failure-{steps}.json",
                document,
                "pre-motion navigation failure",
            )
            raise

    def _observe_policy_step(
        self,
        step: int,
        skills: list[str],
        env_action: Any,
        habitat_env: Any,
        actor: Any,
        done: bool,
        info: dict[str, Any],
        observations: Any,
        policy_input_observations: Any,
    ) -> None:
        """Permit a shared-world observer to capture existing post-step state only."""
        del step, skills, env_action, habitat_env, actor, done, info, observations
        del policy_input_observations

    def _policy_step_offset(self) -> int:
        """Return the number of steps already consumed by the same simulator world."""
        return 0

    def _assigned_arguments(
        self,
        text_context: dict[str, Any],
        invocation: CanonicalInvocation,
    ) -> dict[str, Any]:
        """Translate one committed assignment into EMOS Stage1's output type."""
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
        target = f"agent_{self._config.agent_id}"
        for agent_name, resume in resumes.items():
            if not isinstance(agent_name, str) or not isinstance(resume, dict):
                raise IntegrationError("EMOS robot_resume has invalid structure")
            robot_type = resume.get("robot_type")
            if not isinstance(robot_type, str) or not robot_type:
                raise IntegrationError("EMOS robot_resume lacks robot_type")
            assigned[agent_name] = AgentArguments(
                robot_id=agent_name,
                robot_type=robot_type,
                task_description=invocation.objective,
                subtask_description=(
                    self._subtask(invocation) if agent_name == target else "Nothing to do"
                ),
                chat_history=[],
            )
        if target not in assigned:
            raise IntegrationError(f"assigned EMOS agent {target!r} is unavailable")
        return assigned

    def _install_assignment(self, assignment: dict[str, Any]) -> tuple[Any, Any]:
        """Replace only Stage1 assignment production for one execution."""
        from habitat_baselines.rl.multi_agent import (  # type: ignore[import-not-found]
            multi_llm_policy,
        )

        original = multi_llm_policy.group_discussion

        def committed_assignment(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """Return Control's immutable assignment instead of rerunning EMOS Stage1."""
            del args, kwargs
            return assignment

        multi_llm_policy.group_discussion = committed_assignment
        return multi_llm_policy, original

    def _single_execution_contracts(
        self,
        assignment: dict[str, Any],
        invocation: CanonicalInvocation,
    ) -> dict[str, Stage2ExecutionContract]:
        """Guard the only agent authorized to request a physical Stage2 action."""
        target = f"agent_{self._config.agent_id}"
        if target not in assignment:
            raise IntegrationError(f"assigned EMOS agent {target!r} is unavailable")
        return {target: Stage2ExecutionContract.for_invocation(invocation)}

    def _install_execution_contract(
        self,
        contracts: dict[str, Stage2ExecutionContract],
        completed_steps: Callable[[], int],
    ) -> Callable[[], None]:
        """Install one scoped guard around the original EMOS action boundary."""
        if self._actor is None:
            raise IntegrationError("Stage2 contract requires an initialized actor")
        all_agents = [
            policy._high_level_policy.llm_agent for policy in self._actor._active_policies
        ]
        agents = [agent for agent in all_agents if not isinstance(agent, PassiveIdleAgent)]
        if {agent.name for agent in agents} != set(contracts) or any(
            agent.name in contracts or agent.llm_model is not None
            for agent in all_agents
            if isinstance(agent, PassiveIdleAgent)
        ):
            raise IntegrationError("active Stage2 models must match committed execution contracts")
        audit = Stage2ActionAudit(self._evidence_dir(), self._previous_action_audit())
        feedback_audit = Stage2ActionAudit(
            self._evidence_dir(),
            getattr(self, "_feedback_audit_summary", None),
            execution_feedback=True,
        )

        def record(document: dict[str, Any]) -> None:
            """Bind the action decision to the last completed simulator step and local time."""
            audit.record(
                dict(
                    document, completed_simulator_steps=completed_steps(), observed_unix=time.time()
                )
            )

        def record_feedback(document: dict[str, Any]) -> None:
            """Archive local observations separately from model-selected action admission."""
            feedback_audit.record(
                dict(
                    document, completed_simulator_steps=completed_steps(), observed_unix=time.time()
                )
            )

        relocation_states = {
            name: RelocationExecutionState()
            for name, contract in contracts.items()
            if contract.is_relocation
        }

        def complete_relocation_action(agent_name: str, action_name: str, succeeded: bool) -> None:
            """Apply only definite local-skill evidence to the execution-scoped phase."""
            state = relocation_states.get(agent_name)
            if state is not None:
                state.complete(action_name, succeeded)

        feedback = Stage2ExecutionFeedback(
            contracts, record_feedback, completion=complete_relocation_action
        )
        try:
            feedback.install(self._actor._active_policies)
            restore_guard = install_stage2_contract_guard(
                agents,
                contracts,
                record,
                feedback=feedback,
                relocation_states=relocation_states,
            )
        except BaseException:
            feedback.close()
            feedback_audit.close()
            audit.close()
            raise
        self._stage2_feedback = feedback

        def restore() -> None:
            """Close per-call evidence and restore instance hooks on every exit."""
            try:
                restore_guard()
            finally:
                try:
                    feedback.close()
                finally:
                    self._stage2_feedback = None
                    feedback_audit.close()
                    self._feedback_audit_summary = feedback_audit.summary
                    audit.close()
                    self._retain_action_audit(audit.summary)

        return restore

    def _operation_completed(self, agent_id: int) -> bool:
        """Return only the feedback bridge's observed relocation completion signal."""
        feedback = getattr(self, "_stage2_feedback", None)
        return bool(feedback is not None and feedback.operation_completed(f"agent_{agent_id}"))

    def _record_local_skill_completion(self, agent_id: int) -> None:
        """Forward an already-observed local completion without rereading sensors or truth."""
        feedback = getattr(self, "_stage2_feedback", None)
        if feedback is not None:
            feedback.local_completion(f"agent_{agent_id}")

    def _record_completed_physical_step(self) -> None:
        """Advance only local feedback attribution after the existing successful Gym step."""
        feedback = getattr(self, "_stage2_feedback", None)
        if feedback is not None:
            feedback.physical_step()

    def _previous_action_audit(self) -> dict[str, Any] | None:
        """Return prior segment accounting when the local world is intentionally retained."""
        return None

    def _retain_action_audit(self, summary: dict[str, Any] | None) -> None:
        """Leave independent executions without cross-episode audit state."""
        del summary

    def _batch(self, observations: Any) -> Any:
        """Apply the same batching and transforms as the EMOS evaluator."""
        batch = self._runtime["batch_obs"]([observations], device=self._runtime["device"])
        return self._runtime["apply_transforms"](batch, self._runtime["transforms"])

    def _current_skills(self, actor: Any) -> list[str]:
        """Read actual active EMOS skill names after policy selection."""
        names = []
        for policy in actor._active_policies:
            index = int(policy._cur_skills[0])
            names.append(policy._idx_to_name[index])
        return names

    @staticmethod
    def _extend_skill_sequence(sequence: list[str], current: list[str]) -> None:
        """Append skill transitions without replacing the per-step trace."""
        value = "|".join(current)
        if not sequence or sequence[-1] != value:
            sequence.append(value)

    @staticmethod
    def _gym_step_result(value: Any) -> tuple[Any, bool, dict[str, Any]]:
        """Normalize Gym's four- and five-element step APIs."""
        if not isinstance(value, tuple):
            raise IntegrationError("Habitat Gym step returned an invalid result")
        if len(value) == 4:
            observations, _, done, info = value
        elif len(value) == 5:
            observations, _, terminated, truncated, info = value
            done = bool(terminated or truncated)
        else:
            raise IntegrationError("Habitat Gym step returned an unsupported tuple")
        if not isinstance(info, dict):
            raise IntegrationError("Habitat Gym step info is not an object")
        return observations, bool(done), info

    def _outcome(
        self,
        state: str,
        detail: str,
        invocation: CanonicalInvocation,
        scene_id: str,
        steps: int,
        initial: tuple[float, float, float],
        skill_sequence: list[str],
        *,
        local_skill_completed: bool,
        episode_terminated: bool = False,
        terminal_basis: str,
    ) -> LocalExecutionOutcome:
        """Capture distinct local-skill, benchmark, episode, and model evidence."""
        self._best_effort_flush_action_trace("local outcome")
        habitat_env = self._habitat_env
        metrics = habitat_env.get_metrics() if habitat_env is not None else {}
        benchmark_achieved = bool(metrics.get("pddl_success", False))
        episode_terminated = episode_terminated or (
            bool(habitat_env.episode_over) if habitat_env is not None else False
        )
        policy_evidence = self._policy_evidence()
        self._best_effort_write_json(
            "controlled-outcome.json",
            {
                "benchmark_task_achieved": benchmark_achieved,
                "action_trace_collection": self._action_trace_stats(),
                "episode_terminated": episode_terminated,
                "local_skill_completed": local_skill_completed,
                "local_state": state,
                "policy": policy_evidence,
                "simulator_steps": steps,
                "skill_sequence": skill_sequence,
            },
            "local outcome",
        )
        return LocalExecutionOutcome(
            state=state,
            detail=detail,
            episode_id=self._config.episode_id,
            scene_id=scene_id,
            destination=invocation.destination,
            simulator_steps=steps,
            initial_position=initial,
            final_position=self._agent_position(),
            local_skill_completed=local_skill_completed,
            benchmark_task_achieved=benchmark_achieved,
            episode_terminated=episode_terminated,
            skill_sequence=tuple(skill_sequence),
            local_llm_calls=int(policy_evidence["local_llm_calls"]),
            local_tokens=int(policy_evidence["local_tokens"]),
            local_replans=int(policy_evidence["local_replans"]),
            invalid_outputs=int(policy_evidence["invalid_outputs"]),
            send_request_count=int(policy_evidence["send_request_count"]),
            message_pipe_activity_count=int(policy_evidence["message_pipe_activity_count"]),
            terminal_basis=terminal_basis,
        )

    def _oracle_nav_finished(self) -> bool:
        """Read the same local action authority projected by the finish sensor."""
        habitat_env = self._habitat_env
        if habitat_env is None:
            return False
        actions = getattr(getattr(habitat_env, "task", None), "actions", {})
        if not isinstance(actions, dict):
            return False
        action = actions.get(f"agent_{self._config.agent_id}_oracle_nav_action")
        if action is None:
            action = actions.get("oracle_nav_action")
        return bool(getattr(action, "skill_done", False))

    def _policy_evidence(self) -> dict[str, Any]:
        """Collect model and accounting evidence without changing policy behavior."""
        actor = self._actor
        agents = (
            []
            if actor is None
            else [policy._high_level_policy.llm_agent for policy in actor._active_policies]
        )
        models = sorted(
            {
                str(agent.llm_model.model)
                for agent in agents
                if getattr(agent, "llm_model", None) is not None
            }
        )
        calls = 0
        tokens = 0
        local_replans = 0
        invalid_outputs = 0
        send_request_count = 0
        message_pipe_activity_count = 0
        for agent in agents:
            model = getattr(agent, "llm_model", None)
            if model is None:
                continue
            history = getattr(model, "chat_history", [])
            action_calls = max(len(history) - 1, 0)
            calls += len(history)
            local_replans += max(action_calls - 1, 0)
            tokens += int(getattr(model, "token_usage", 0) or 0)
            tool_calls = self._tool_calls(history)
            for name, arguments in tool_calls:
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
            "local_llm_calls": calls,
            "local_replans": local_replans,
            "local_tokens": tokens,
            "message_pipe_activity_count": message_pipe_activity_count,
            "models": models,
            "sampling": "provider defaults used by the shared original EMOS OpenAIModel",
            "send_request_count": send_request_count,
            "stage1_assignment": "RoboGuide committed assignment",
            "stage2_policy": "original EMOS MultiLLMPolicy/HierarchicalPolicy",
        }

    @staticmethod
    def _tool_calls(history: object) -> list[tuple[str, dict[str, Any]]]:
        """Extract action names and arguments from original EMOS chat evidence.

        The method is observation-only: malformed or absent tool-call evidence
        is omitted rather than changing the local policy's behavior.
        """
        if not isinstance(history, list):
            return []
        calls: list[tuple[str, dict[str, Any]]] = []
        for exchange in history:
            if not isinstance(exchange, list):
                continue
            for message in exchange:
                for tool_call in getattr(message, "tool_calls", None) or []:
                    function = getattr(tool_call, "function", None)
                    name = getattr(function, "name", None)
                    raw_arguments = getattr(function, "arguments", None)
                    if not isinstance(name, str) or not isinstance(raw_arguments, str):
                        continue
                    try:
                        arguments = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(arguments, dict):
                        calls.append((name, arguments))
        return calls

    def _append_action_trace(self, steps: int, skills: list[str], info: dict[str, Any]) -> None:
        """Queue one compact policy observation without per-step serialization or I/O."""
        record = {
            "benchmark_task_achieved": bool(info.get("pddl_success", False)),
            "simulator_step": steps,
            "skills": skills,
        }
        self._action_trace_writer.append(record)

    def _flush_action_trace(self) -> None:
        """Best-effort flush action evidence without affecting physical execution."""
        self._action_trace_writer.flush()

    def _best_effort_flush_action_trace(self, phase: str) -> None:
        """Flush action evidence without allowing storage failure to alter outcome."""
        try:
            self._flush_action_trace()
        except Exception:  # noqa: BLE001 - evidence cannot become execution authority
            _LOG.exception("action trace flush failed at %s", phase)

    def _best_effort_write_json(self, name: str, value: object, phase: str) -> None:
        """Write one optional JSON artifact without changing the local outcome."""
        try:
            self._write_json(name, value)
        except Exception:  # noqa: BLE001 - evidence cannot become execution authority
            _LOG.exception("JSON evidence %s unavailable at %s", name, phase)

    def _action_trace_stats(self) -> dict[str, Any]:
        """Expose action-trace buffering and loss accounting as evidence."""
        return {
            "sampling_period_simulator_steps": 1,
            **self._action_trace_writer.stats(),
        }

    def _subtask(self, invocation: CanonicalInvocation) -> str:
        """Return the configured assignment with its exact committed destination."""
        return format_stage2_subtask(invocation, self._config.subtask_mode)

    def _agent_position(self) -> tuple[float, float, float]:
        """Read the assigned simulated robot's current base position."""
        if self._habitat_env is None:
            raise IntegrationError("Habitat environment is not initialized")
        position = self._habitat_env.sim.get_agent_data(
            self._config.agent_id
        ).articulated_agent.base_pos
        return (float(position[0]), float(position[1]), float(position[2]))

    def _require_initialized(self) -> tuple[Any, Any, Any, Any]:
        """Return initialized policy state or fail before dispatch."""
        if (
            self._gym_env is None
            or self._habitat_env is None
            or self._actor is None
            or self._agent_access is None
        ):
            raise IntegrationError("EMOS Stage2 runtime is not initialized")
        return self._gym_env, self._habitat_env, self._actor, self._agent_access

    def _evidence_dir(self) -> Path:
        """Return the deployment-owned evidence directory."""
        return Path(self._config.evidence_dir)

    def _write_text(self, name: str, value: str) -> None:
        """Persist UTF-8 evidence beside adapter-local artifacts."""
        path = self._evidence_dir() / name
        write_text_atomic(path, value)

    def _write_json(self, name: str, value: object) -> None:
        """Persist deterministic JSON evidence without creating authority."""
        self._write_text(name, json.dumps(value, indent=2, sort_keys=True) + "\n")
