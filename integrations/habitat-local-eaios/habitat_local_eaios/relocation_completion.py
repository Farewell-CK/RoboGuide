"""Execution-scoped place observations for the controlled EMOS relocation profile.

Only instance-local observation/termination hooks change. The original place
action emits its own release command; no action, model or Gym call is added.
This Local How difference is opt-in and never computes official PDDL truth.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .model import IntegrationError

if TYPE_CHECKING:
    from .stage2_contract import Stage2ExecutionContract

COMPLETION_PROFILE = "exact-object-released-place/v0.1"
LOCAL_PLACE_THRESHOLD_M = 0.02
_MISSING = object()


def _position(value: Any) -> list[float]:
    """Read three finite coordinates without sampling or mutating the world."""
    if len(value) != 3:
        raise ValueError("position must have three coordinates")
    result = [float(value[index]) for index in range(3)]
    if not all(math.isfinite(coordinate) for coordinate in result):
        raise ValueError("position is not finite")
    return result


def _place_skill(policy: Any) -> Any:
    """Resolve the actual configured place instance, not a guessed integer slot."""
    skills, names = policy._skills, policy._idx_to_name
    entries = names.items() if isinstance(names, Mapping) else enumerate(names)
    matches = [skills[index] for index, name in entries if name == "place"]
    if len(matches) != 1 or any(
        not callable(getattr(matches[0], name, None))
        for name in ("_is_skill_done", "_internal_act", "should_terminate")
    ):
        raise IntegrationError("bound place requires one observable original place skill")
    return matches[0]


def _agent_id(name: str) -> int:
    """Resolve the adapter's actual policy agent identity without ordinal assumptions."""
    if not name.startswith("agent_") or not name[6:].isdigit():
        raise IntegrationError("bound place policy has an unsupported agent identity")
    return int(name[6:])


def inspect_completion_interfaces(policies: Sequence[Any], environment: Any) -> None:
    """Fail startup on absent original interfaces; never execute a skill or simulator step."""
    problem = environment.task.pddl_problem
    if not callable(getattr(problem, "get_entity", None)) or not callable(
        getattr(problem.sim_info, "get_entity_pos", None)
    ):
        raise IntegrationError("bound place requires exact PDDL entity position readers")
    if not isinstance(problem.sim_info.obj_ids, Mapping) or not problem.sim_info.obj_ids:
        raise IntegrationError("bound place requires loaded movable object identities")
    if not policies:
        raise IntegrationError("bound place requires loaded policies")
    for policy in policies:
        _place_skill(policy)
        manager = environment.sim.get_agent_data(
            _agent_id(policy._high_level_policy.llm_agent.name)
        ).grasp_mgr
        if not isinstance(manager.is_grasped, bool) or (
            manager.snap_idx is not None
            and (isinstance(manager.snap_idx, bool) or not isinstance(manager.snap_idx, int))
        ):
            raise IntegrationError("bound place requires readable exact grasp identities")


class RelocationCompletionBinding:
    """Bind small real-world reads and original place checks to one immutable attempt."""

    def __init__(self, environment: Any, contracts: Mapping[str, Stage2ExecutionContract]) -> None:
        """Freeze exact entity and absolute rigid-object identities before installing hooks."""
        self._environment = environment
        self._contracts = {
            name: contract for name, contract in contracts.items() if contract.is_relocation
        }
        self._world = self._world_identity()
        self._entities: dict[str, tuple[Any, Any, int]] = {}
        self._calls: dict[str, dict[str, Any]] = {}
        self._actions: dict[str, dict[str, Any]] = {}
        self._last: dict[str, dict[str, Any]] = {}
        self._terminating: set[str] = set()
        self._restores: list[Callable[[], None]] = []
        problem = environment.task.pddl_problem
        for name, contract in self._contracts.items():
            if contract.attempt_id is None or not contract.invocation_digest:
                raise IntegrationError(
                    "bound place requires an explicit canonical attempt identity"
                )
            obj, destination = (
                problem.get_entity(contract.expected_object),
                problem.get_entity(contract.expected_destination),
            )
            if (
                obj is None
                or destination is None
                or not problem.sim_info.check_type_matches(obj, "movable_entity_type")
            ):
                raise IntegrationError(
                    "bound place cannot resolve its canonical object or destination"
                )
            if not (
                problem.sim_info.check_type_matches(destination, "goal_entity_type")
                or problem.sim_info.check_type_matches(destination, "static_receptacle_entity_type")
            ):
                raise IntegrationError("bound place destination is not a supported PDDL location")
            index = problem.sim_info.obj_ids.get(contract.expected_object)
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise IntegrationError("bound place movable object index is unavailable")
            rigid_id = environment.sim.scene_obj_ids[index]
            if isinstance(rigid_id, bool) or not isinstance(rigid_id, int):
                raise IntegrationError("bound place absolute rigid object identity is unavailable")
            self._entities[name] = obj, destination, rigid_id
            if self.observe(name)["status"] != "observed":
                raise IntegrationError(
                    "bound place actual object/destination/grasp observation unavailable"
                )

    def _world_identity(self) -> tuple[str, str]:
        """Fence a binding against episode or scene replacement in a retained world."""
        episode = self._environment.current_episode
        return str(episode.episode_id), str(episode.scene_id)

    def bind_call(self, agent_name: str, tool_call_id: str, sequence: int) -> None:
        """Replace only this attempt's current selected place call identity."""
        if agent_name in self._contracts:
            self._calls[agent_name] = {"tool_call_id": tool_call_id, "action_sequence": sequence}
            self._actions.pop(agent_name, None)

    def consume_action(self, agent_name: str, tool_call_id: str, sequence: int) -> bool:
        """Attribute an existing Gym step to one actually generated original place action."""
        return self._actions.pop(agent_name, None) == {
            "tool_call_id": tool_call_id,
            "action_sequence": sequence,
        }

    def observe(self, agent_name: str) -> dict[str, Any]:
        """Read exact object distance and grasp state; missing evidence remains unavailable."""
        contract = self._contracts[agent_name]
        record: dict[str, Any] = {
            "profile": COMPLETION_PROFILE,
            "agent_name": agent_name,
            "attempt_id": contract.attempt_id,
            "invocation_digest": contract.invocation_digest,
            "object_entity_id": contract.expected_object,
            "destination_entity_id": contract.expected_destination,
            "local_threshold_m": LOCAL_PLACE_THRESHOLD_M,
            "status": "unavailable",
            "reason": None,
            "object_position": None,
            "destination_position": None,
            "distance_m": None,
            "is_grasped": None,
            "snap_object_id": None,
            "bound_object_id": self._entities[agent_name][2],
            "object_released": None,
            "release_eligible": None,
            "qualified_local_completion": None,
            **self._calls.get(agent_name, {}),
        }
        try:
            if self._world_identity() != self._world:
                raise ValueError("world identity changed")
            obj, destination, rigid_id = self._entities[agent_name]
            problem = self._environment.task.pddl_problem
            index = problem.sim_info.obj_ids[contract.expected_object]
            if self._environment.sim.scene_obj_ids[index] != rigid_id:
                raise ValueError("bound object identity changed")
            object_position = _position(problem.sim_info.get_entity_pos(obj))
            destination_position = _position(problem.sim_info.get_entity_pos(destination))
            manager = self._environment.sim.get_agent_data(_agent_id(agent_name)).grasp_mgr
            grasped, snap = manager.is_grasped, manager.snap_idx
            if not isinstance(grasped, bool) or (
                snap is not None and (isinstance(snap, bool) or not isinstance(snap, int))
            ):
                raise ValueError("grasp identity unavailable")
            distance = math.sqrt(
                sum(
                    (object_position[index] - destination_position[index]) ** 2
                    for index in range(3)
                )
            )
            record.update(
                object_position=object_position,
                destination_position=destination_position,
                distance_m=distance,
                is_grasped=grasped,
                snap_object_id=snap,
            )
            if (not grasped and snap is not None) or (grasped and snap != rigid_id):
                record.update(
                    status="contradictory", reason="grasp does not match the bound object"
                )
                return record
            released, near = not grasped and snap is None, distance < LOCAL_PLACE_THRESHOLD_M
            record.update(
                status="observed",
                object_released=released,
                release_eligible=near,
                qualified_local_completion=near and released,
            )
        except Exception as error:  # noqa: BLE001 - unavailable reads never become success
            record["reason"] = type(error).__name__
        return record

    def last_check(self, agent_name: str) -> dict[str, Any] | None:
        """Expose a detached bounded check without claiming an official goal result."""
        record = self._last.get(agent_name)
        return dict(record) if record is not None else None

    def install(self, policies: Sequence[Any]) -> None:
        """Scope place checks to assigned instances and restore partial installation on failure."""
        owners: set[int] = set()
        try:
            for policy in policies:
                name = policy._high_level_policy.llm_agent.name
                if name not in self._contracts:
                    continue
                skill = _place_skill(policy)
                if id(skill) in owners:
                    raise IntegrationError("bound place skill cannot have multiple agent owners")
                owners.add(id(skill))
                self._install_skill(name, skill)
        except BaseException:
            self.close()
            raise

    def _install_skill(self, name: str, skill: Any) -> None:
        """Pass the bound distance to original release logic and separately qualify termination."""
        original_check, original_terminate, original_act = (
            skill._is_skill_done,
            skill.should_terminate,
            skill._internal_act,
        )
        previous_check, previous_terminate, previous_act = (
            vars(skill).get("_is_skill_done", _MISSING),
            vars(skill).get("should_terminate", _MISSING),
            vars(skill).get("_internal_act", _MISSING),
        )

        def check(observations: Any, *args: Any, **kwargs: Any) -> Any:
            """Call the original geometric check once with this invocation's distance."""
            record = self.observe(name)
            if record["status"] == "contradictory":
                raise IntegrationError("bound place actual grasp contradicts its canonical object")
            obs_key = next(
                (key for key in observations if "object_to_goal_distance_sensor" in key), None
            )
            corrected = dict(observations)
            if record["status"] == "observed" and obs_key is not None:
                template = observations[obs_key]
                corrected[obs_key] = template.new_tensor([[record["distance_m"]]])
                try:
                    raw = template.detach().cpu().reshape(-1).tolist()
                    native_distance = math.sqrt(sum(float(item) ** 2 for item in raw))
                    record.update(
                        native_sensor_distance_m=native_distance,
                        native_sensor_under_threshold=native_distance < LOCAL_PLACE_THRESHOLD_M,
                    )
                except Exception:  # noqa: BLE001 - native diagnostic comparison may be unavailable
                    record.update(native_sensor_distance_m=None, native_sensor_under_threshold=None)
            result = original_check(corrected, *args, **kwargs)
            if record["status"] != "observed" or obs_key is None:
                result = result & False
                record.update(status="unavailable", qualified_local_completion=None)
            elif name in self._terminating and record["object_released"] is not True:
                result = result & False
            record["check_purpose"] = (
                "termination" if name in self._terminating else "original-release-action"
            )
            self._last[name] = record
            return result

        def terminate(*args: Any, **kwargs: Any) -> Any:
            """Retain original budget/high-level behavior while requiring observed release."""
            self._terminating.add(name)
            try:
                return original_terminate(*args, **kwargs)
            finally:
                self._terminating.discard(name)

        def act(*args: Any, **kwargs: Any) -> Any:
            """Return the original action unchanged and mark only its current call attribution."""
            result = original_act(*args, **kwargs)
            if name in self._calls:
                self._actions[name] = dict(self._calls[name])
            return result

        skill._is_skill_done, skill.should_terminate, skill._internal_act = check, terminate, act

        def restore() -> None:
            """Remove instance hooks after normal, exceptional, cancelled or serial segment exit."""
            for field, previous in (
                ("_is_skill_done", previous_check),
                ("should_terminate", previous_terminate),
                ("_internal_act", previous_act),
            ):
                if previous is _MISSING:
                    delattr(skill, field)
                else:
                    setattr(skill, field, previous)

        self._restores.append(restore)

    def close(self) -> None:
        """Restore all hooks once and discard call identities before any endpoint reuse."""
        for restore in reversed(self._restores):
            restore()
        self._restores.clear()
        self._calls.clear()
        self._actions.clear()
        self._last.clear()
        self._terminating.clear()
