"""Original EMOS Stage2 policy execution behind the Local EAIOS boundary."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .backend import LocalExecutionOutcome, _observation_true
from .model import CanonicalMobilityInvocation, IntegrationError


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

    def initialize(self) -> None:
        """Build the policy and transformed environment used by EMOS evaluation."""
        if self._gym_env is not None:
            return
        try:
            import torch  # type: ignore[import-not-found]
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
                overrides=[
                    "habitat_baselines.num_environments=1",
                    "habitat_baselines.eval.video_option=[]",
                    "habitat.simulator.concur_render=False",
                ],
            )
            gym_env = make_gym_from_config(config)
            habitat_env = gym_env.habitat_env
            matches = [
                episode
                for episode in habitat_env.episodes
                if str(episode.episode_id) == self._config.episode_id
            ]
            if len(matches) != 1:
                gym_env.close()
                raise IntegrationError(
                    f"Habitat episode {self._config.episode_id!r} is not uniquely available"
                )
            if self._config.agent_id >= len(config.habitat.simulator.agents_order):
                gym_env.close()
                raise IntegrationError("configured Habitat agent_id is out of range")
            habitat_env.episodes = matches
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
            self._episode = matches[0]
            self._actor = actor
            self._agent_access = access
            self._runtime = {
                "apply_transforms": apply_obs_transforms_batch,
                "batch_obs": batch_obs,
                "device": device,
                "get_action_space_info": get_action_space_info,
                "torch": torch,
                "transforms": transforms,
            }
        except IntegrationError:
            raise
        except Exception as error:
            self.close()
            raise IntegrationError(f"EMOS Stage2 initialization failed: {error}") from error

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Run an assigned subtask using the unmodified EMOS Stage2 decision path."""
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
        invocation: CanonicalMobilityInvocation,
        initial: tuple[float, float, float],
        scene_id: str,
        actor: Any,
        access: Any,
        gym_env: Any,
        habitat_env: Any,
        cancellation_requested: Callable[[], bool],
    ) -> LocalExecutionOutcome:
        """Mirror the EMOS evaluator loop while preserving RoboGuide cancellation."""
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
        skill_sequence: list[str] = []
        chat_history_root = self._evidence_dir() / "chat-history"
        (chat_history_root / str(text_context["episode_id"])).mkdir(parents=True, exist_ok=True)
        module, original_group_discussion = self._install_assignment(assignment)
        try:
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
                step_result = gym_env.step(env_action)
                observations, done, info = self._gym_step_result(step_result)
                steps += 1
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
                target_skill = current_skills[self._config.agent_id]
                finished_key = f"agent_{self._config.agent_id}_has_finished_oracle_nav"
                if target_skill in {
                    "nav_to_obj",
                    "nav_to_goal",
                    "nav_to_receptacle_by_name",
                } and (
                    _observation_true(observations, finished_key) or self._oracle_nav_finished()
                ):
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
        finally:
            module.group_discussion = original_group_discussion
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

    def _assigned_arguments(
        self,
        text_context: dict[str, Any],
        invocation: CanonicalMobilityInvocation,
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
        invocation: CanonicalMobilityInvocation,
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
        habitat_env = self._habitat_env
        metrics = habitat_env.get_metrics() if habitat_env is not None else {}
        benchmark_achieved = bool(metrics.get("pddl_success", False))
        episode_terminated = episode_terminated or (
            bool(habitat_env.episode_over) if habitat_env is not None else False
        )
        policy_evidence = self._policy_evidence()
        self._write_json(
            "controlled-outcome.json",
            {
                "benchmark_task_achieved": benchmark_achieved,
                "episode_terminated": episode_terminated,
                "local_skill_completed": local_skill_completed,
                "local_state": state,
                "policy": policy_evidence,
                "simulator_steps": steps,
                "skill_sequence": skill_sequence,
            },
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
        """Persist one compact observation of original policy skill choices."""
        record = {
            "benchmark_task_achieved": bool(info.get("pddl_success", False)),
            "simulator_step": steps,
            "skills": skills,
        }
        path = self._evidence_dir() / "action_trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")

    def _subtask(self, invocation: CanonicalMobilityInvocation) -> str:
        """Return the configured semantic assignment without Local How."""
        if self._config.subtask_mode == "natural-objective":
            return invocation.objective
        return f"Navigate to {invocation.destination}."

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def _write_json(self, name: str, value: object) -> None:
        """Persist deterministic JSON evidence without creating authority."""
        self._write_text(name, json.dumps(value, indent=2, sort_keys=True) + "\n")
