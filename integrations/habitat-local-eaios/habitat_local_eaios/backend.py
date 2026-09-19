"""Habitat/EMOS-owned navigation execution behind the Local EAIOS boundary."""

from __future__ import annotations

import importlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .model import CanonicalMobilityInvocation, IntegrationError


@dataclass(frozen=True)
class HabitatBackendConfig:
    """Deployment-owned Habitat configuration for one deterministic local robot."""

    config_path: Path
    episode_id: str
    agent_id: int
    max_steps: int
    step_period_ms: int
    seed: int | None = None


def habitat_config_overrides(seed: int | None) -> list[str]:
    """Return the fixed Habitat config overrides for one bridge process.

    The overrides pin single-environment headless evaluation for both
    backends. A nonnull ``seed`` is forwarded as the ``habitat.seed``
    override — the same mechanism the EMOS evaluation arm uses — so the
    simulator RNG (including ``randomize_agent_start`` agent placement) is
    reproducible when the deployment supplies one. ``None`` keeps the
    config-file default; the consumed value is always recorded in run
    evidence by the shared-world backend, never silently assumed.
    """
    overrides = [
        "habitat_baselines.num_environments=1",
        "habitat_baselines.eval.video_option=[]",
        "habitat.simulator.concur_render=False",
    ]
    if seed is not None:
        overrides.append(f"habitat.seed={seed}")
    return overrides


def initial_agent_positions(habitat_env: Any, agent_ids: tuple[int, ...]) -> dict[str, list[float]]:
    """Observe the actual per-agent base positions after one episode reset.

    ``randomize_agent_start`` samples agent starts from the simulator RNG,
    and the two evaluation arms may consume different RNG streams, so a
    shared seed never proves equal initial states. This reads each agent's
    articulated base position directly from the live environment and
    returns plain floats keyed by agent id so the shared-world summary can
    carry the observed facts as evidence for later cross-arm comparison.
    """
    positions: dict[str, list[float]] = {}
    for agent_id in sorted(agent_ids):
        position = habitat_env.sim.get_agent_data(agent_id).articulated_agent.base_pos
        positions[str(agent_id)] = [float(component) for component in position]
    return positions


@dataclass(frozen=True)
class LocalExecutionOutcome:
    """Terminal Habitat evidence retained separately from RoboGuide outcomes."""

    state: str
    detail: str
    episode_id: str
    scene_id: str
    destination: str
    simulator_steps: int
    initial_position: tuple[float, float, float]
    final_position: tuple[float, float, float]
    local_skill_completed: bool = False
    benchmark_task_achieved: bool = False
    episode_terminated: bool = False
    skill_sequence: tuple[str, ...] = ()
    local_llm_calls: int = 0
    local_tokens: int = 0
    local_replans: int = 0
    invalid_outputs: int = 0
    send_request_count: int = 0
    message_pipe_activity_count: int = 0
    terminal_basis: str = "unspecified"

    def as_dict(self) -> dict[str, object]:
        """Return a stable JSON-shaped local outcome for status evidence."""
        return {
            "benchmark_task_achieved": self.benchmark_task_achieved,
            "destination": self.destination,
            "episode_id": self.episode_id,
            "episode_terminated": self.episode_terminated,
            "final_position": list(self.final_position),
            "initial_position": list(self.initial_position),
            "local_llm_calls": self.local_llm_calls,
            "local_replans": self.local_replans,
            "local_skill_completed": self.local_skill_completed,
            "local_tokens": self.local_tokens,
            "invalid_outputs": self.invalid_outputs,
            "message_pipe_activity_count": self.message_pipe_activity_count,
            "scene_id": self.scene_id,
            "simulator_steps": self.simulator_steps,
            "send_request_count": self.send_request_count,
            "skill_sequence": list(self.skill_sequence),
            "state": self.state,
            "terminal_basis": self.terminal_basis,
        }


class MobilityBackend(Protocol):
    """Local navigation backend contract consumed by the HTTP adapter."""

    def initialize(self) -> None:
        """Establish and validate the real Local EAIOS execution environment."""

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Run one navigation operation and return only a true terminal outcome."""

    def readiness_detail(self) -> str:
        """Describe the initialized local backend without changing it."""

    def close(self) -> None:
        """Release simulator resources after local work has stopped."""


class HabitatMobilityBackend:
    """Executes one semantic destination through EMOS' Habitat Oracle navigation action."""

    def __init__(self, config: HabitatBackendConfig) -> None:
        """Retain immutable deployment choices; initialization occurs on the worker thread."""
        if config.agent_id < 0:
            raise IntegrationError("agent_id must be non-negative")
        if config.max_steps < 1:
            raise IntegrationError("max_steps must be positive")
        if config.step_period_ms < 0:
            raise IntegrationError("step_period_ms must be non-negative")
        self._config = config
        self._gym_env: Any | None = None
        self._habitat_env: Any | None = None
        self._episode: Any | None = None
        self._numpy: Any | None = None
        self._agent_count = 0

    def initialize(self) -> None:
        """Load the configured real scene and pin one deterministic Habitat episode."""
        if self._gym_env is not None:
            return
        try:
            config_module = importlib.import_module("habitat_baselines.config.default")
            gym_module = importlib.import_module("habitat.gym")
            self._numpy = importlib.import_module("numpy")
            config = config_module.get_config(
                str(self._config.config_path),
                overrides=habitat_config_overrides(self._config.seed),
            )
            gym_env = gym_module.make_gym_from_config(config)
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
            self._gym_env = gym_env
            self._habitat_env = habitat_env
            self._episode = matches[0]
            self._agent_count = len(config.habitat.simulator.agents_order)
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(f"Habitat initialization failed: {error}") from error

    def execute(
        self,
        invocation: CanonicalMobilityInvocation,
        cancellation_requested: Callable[[], bool],
        running: Callable[[str], None],
    ) -> LocalExecutionOutcome:
        """Resolve a semantic PDDL entity, step Oracle navigation, and observe its terminal fact."""
        habitat_env, episode, numpy = self._require_initialized()
        try:
            habitat_env.episodes = [episode]
            observations = habitat_env.reset()
            entity_index = self._entity_index(invocation.destination)
            initial = self._agent_position()
            scene_id = str(habitat_env.current_episode.scene_id)
            running(
                f"Habitat episode {self._config.episode_id} started navigation to "
                f"{invocation.destination}"
            )
            steps = 0
            finished_key = f"agent_{self._config.agent_id}_has_finished_oracle_nav"
            action_name = f"agent_{self._config.agent_id}_oracle_nav_action"
            while steps < self._config.max_steps:
                if cancellation_requested():
                    return self._outcome(
                        "CANCELLED",
                        "Habitat navigation stopped after the accepted cancellation request",
                        invocation.destination,
                        scene_id,
                        steps,
                        initial,
                    )
                observations = habitat_env.step(
                    {
                        "action": action_name,
                        "action_args": self._action_arguments(numpy, action_name, entity_index),
                    }
                )
                steps += 1
                if self._config.step_period_ms:
                    time.sleep(self._config.step_period_ms / 1_000)
                if _observation_true(observations, finished_key):
                    return self._outcome(
                        "COMPLETED",
                        "Habitat Oracle navigation reported the destination reached",
                        invocation.destination,
                        scene_id,
                        steps,
                        initial,
                    )
                if habitat_env.episode_over:
                    return self._outcome(
                        "FAILED",
                        "Habitat episode terminated before navigation completed",
                        invocation.destination,
                        scene_id,
                        steps,
                        initial,
                    )
            return self._outcome(
                "FAILED",
                f"Habitat navigation exceeded {self._config.max_steps} simulator steps",
                invocation.destination,
                scene_id,
                steps,
                initial,
            )
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(f"Habitat execution failed: {error}") from error

    def readiness_detail(self) -> str:
        """Describe the pinned real Habitat environment used by this adapter."""
        if self._gym_env is None:
            return "Habitat backend is not initialized"
        return (
            f"Habitat episode {self._config.episode_id} is loaded for agent {self._config.agent_id}"
        )

    def close(self) -> None:
        """Close the real Habitat environment on its owning worker thread."""
        if self._gym_env is not None:
            self._gym_env.close()
        self._gym_env = None
        self._habitat_env = None
        self._episode = None
        self._numpy = None
        self._agent_count = 0

    def _require_initialized(self) -> tuple[Any, Any, Any]:
        """Return initialized backend objects or reject execution before dispatch."""
        if self._habitat_env is None or self._episode is None or self._numpy is None:
            raise IntegrationError("Habitat backend is not initialized")
        return self._habitat_env, self._episode, self._numpy

    def _entity_index(self, destination: str) -> int:
        """Map the semantic destination to the current episode's Local EAIOS entity index."""
        habitat_env, _, _ = self._require_initialized()
        entity = habitat_env.task.pddl_problem.get_entity(destination)
        if entity is None:
            raise IntegrationError(
                f"destination {destination!r} is not an entity in the configured Habitat episode"
            )
        entities = habitat_env.task.pddl_problem.get_ordered_entities_list()
        return int(entities.index(entity))

    def _agent_position(self) -> tuple[float, float, float]:
        """Read the current simulated base position for local execution evidence."""
        habitat_env, _, _ = self._require_initialized()
        position = habitat_env.sim.get_agent_data(self._config.agent_id).articulated_agent.base_pos
        return (float(position[0]), float(position[1]), float(position[2]))

    def _action_arguments(self, numpy: Any, action_name: str, entity_index: int) -> dict[str, Any]:
        """Supply the selected Oracle action plus explicit non-stop values for all local agents."""
        arguments = {
            f"agent_{agent_id}_rearrange_stop": numpy.asarray([0], dtype=numpy.float32)
            for agent_id in range(self._agent_count)
        }
        arguments[action_name] = numpy.asarray([entity_index + 1], dtype=numpy.float32)
        return arguments

    def _outcome(
        self,
        state: str,
        detail: str,
        destination: str,
        scene_id: str,
        steps: int,
        initial: tuple[float, float, float],
    ) -> LocalExecutionOutcome:
        """Capture one terminal local outcome with initial and final physical positions."""
        episode_terminated = (
            bool(self._habitat_env.episode_over) if self._habitat_env is not None else False
        )
        return LocalExecutionOutcome(
            state=state,
            detail=detail,
            episode_id=self._config.episode_id,
            scene_id=scene_id,
            destination=destination,
            simulator_steps=steps,
            initial_position=initial,
            final_position=self._agent_position(),
            local_skill_completed=state == "COMPLETED",
            benchmark_task_achieved=self._benchmark_task_achieved(),
            episode_terminated=episode_terminated,
            skill_sequence=("direct-oracle-nav",),
            terminal_basis=(
                "oracle-nav-skill"
                if state == "COMPLETED"
                else "cancellation"
                if state == "CANCELLED"
                else "local-failure"
            ),
        )

    def _benchmark_task_achieved(self) -> bool:
        """Read the independent Habitat PDDL success measure when available."""
        if self._habitat_env is None:
            return False
        metrics = self._habitat_env.get_metrics()
        return bool(metrics.get("pddl_success", False))


def _observation_true(observations: object, key: str) -> bool:
    """Interpret one Habitat boolean sensor without assuming an array implementation."""
    if not isinstance(observations, dict) or key not in observations:
        raise IntegrationError(f"Habitat observation {key!r} is missing")
    value = observations[key]
    try:
        return bool(value[0])
    except (IndexError, KeyError, TypeError) as error:
        raise IntegrationError(f"Habitat observation {key!r} has invalid shape") from error
