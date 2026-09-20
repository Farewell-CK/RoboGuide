"""Read-only physical execution diagnostics for the shared-world bridge.

Diagnostics add observation only. They never call ``actor.act``,
``gym_env.step``, skill transitions, or terminal evaluation differently,
and never invoke any simulator API that mutates state or RNG. Every field
is read through existing read-only attributes of the original EMOS/Habitat
objects; a missing or unreadable field is recorded as ``unavailable`` with
its reason instead of being synthesized.

Official truth values come from the authoritative Habitat PDDL machinery
itself: each conjunct of ``task.pddl_problem.goal`` is evaluated with the
official ``Predicate.is_true(sim_info)`` path used by ``PddlSuccess``.
Diagnostic distances are computed separately from entity positions and are
always labeled as such, never conflated with official predicates.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

DIAGNOSTICS_SCHEMA = "roboguide.e1.physical-diagnostics/v0.1"
DIAGNOSTICS_ENV_FLAG = "ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS"

_UNAVAILABLE = "unavailable"


def diagnostics_enabled() -> bool:
    """Report whether the deployment explicitly enabled read-only diagnostics."""
    return os.environ.get(DIAGNOSTICS_ENV_FLAG, "") == "1"


def _read(accessor: Callable[..., Any], *args: Any) -> Any:
    """Run one read-only accessor, mapping any failure to an unavailable marker."""
    try:
        return accessor(*args)
    except Exception as error:  # noqa: BLE001 - observation must never break execution
        return {"_status": _UNAVAILABLE, "reason": f"{type(error).__name__}: {error}"}


def _position(sim: Any, agent_id: int) -> Any:
    """Read one agent's articulated base position from the live simulator."""
    return list(sim.get_agent_data(agent_id).articulated_agent.base_pos)


def _rotation(sim: Any, agent_id: int) -> Any:
    """Read one agent's articulated base rotation, when the embodiment exposes it."""
    agent = sim.get_agent_data(agent_id).articulated_agent
    rot = getattr(agent, "base_rot", None)
    if rot is None:
        return {"_status": _UNAVAILABLE, "reason": "embodiment exposes no base_rot"}
    return list(rot)


def _goal_conjuncts(problem: Any) -> list[tuple[str, Any]]:
    """Return the goal's top-level conjuncts as (label, predicate) pairs."""
    goal = getattr(problem, "goal", None)
    sub_exprs = getattr(goal, "sub_exprs", None)
    if sub_exprs:
        return [(repr(predicate), predicate) for predicate in sub_exprs]
    return [(repr(goal), goal)]


def _entity_position(sim_info: Any, problem: Any, name: str) -> Any:
    """Read one PDDL entity's world position through the official sim info."""
    entity = problem.get_entity(name)
    if entity is None:
        return {"_status": _UNAVAILABLE, "reason": f"entity {name!r} unresolved"}
    return list(sim_info.get_entity_pos(entity))


class PhysicalDiagnostics:
    """Record initial, per-step, and terminal physical execution evidence.

    All output is appended to JSONL/JSON files under the run evidence
    directory, so evidence produced before a crash or early termination is
    already durable. Per-step records are streamed; memory use does not
    grow with episode length.
    """

    def __init__(self, evidence_dir: Any, agent_ids: tuple[int, ...], enabled: bool) -> None:
        """Bind diagnostics to one run's evidence directory."""
        self._dir = evidence_dir
        self._agent_ids = agent_ids
        self._enabled = enabled
        self._predicates: list[tuple[str, Any]] = []
        self._dropped_records = 0

    def _write_json(self, name: str, document: dict[str, Any]) -> None:
        """Write one diagnostics JSON document, never overwriting traces."""
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _append_jsonl(self, name: str, document: dict[str, Any]) -> None:
        """Stream one diagnostics record without buffering the whole episode."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with (self._dir / name).open("a", encoding="utf-8") as output:
            output.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")

    def _official_conjunct_values(self, problem: Any) -> dict[str, Any]:
        """Evaluate each goal conjunct through the official predicate path."""
        if not self._predicates:
            # The reset record may have failed or been skipped; conjunct labels
            # are pure configuration reads, so recover them lazily.
            self._predicates = _read(lambda: _goal_conjuncts(problem)) or []
        sim_info = getattr(problem, "sim_info", None)
        if sim_info is None:
            return {
                label: {"_status": _UNAVAILABLE, "reason": "sim_info unbound"}
                for label, _ in self._predicates
            }
        values: dict[str, Any] = {}
        for label, predicate in self._predicates:
            values[label] = _read(lambda p=predicate: bool(p.is_true(sim_info)))
        return values

    def _metrics(self, habitat_env: Any) -> dict[str, Any]:
        """Read the official measure cache without recomputation side effects."""
        return _read(lambda: dict(habitat_env.get_metrics())) or {}

    def _agent_skill_state(self, actor: Any, agent_id: int) -> dict[str, Any]:
        """Read one agent's live skill bookkeeping through read-only attributes."""
        policies = getattr(actor, "_active_policies", None)
        if not policies or agent_id >= len(policies):
            return {"_status": _UNAVAILABLE, "reason": "active policy not exposed"}
        policy = policies[agent_id]
        index = int(policy._cur_skills[0])
        name = policy._idx_to_name[index]
        skill = policy.defined_skills.get(name)
        state: dict[str, Any] = {"skill": name}
        if skill is None:
            state["_skill_detail"] = {"_status": _UNAVAILABLE, "reason": "skill object unavailable"}
            return state
        state.update(
            {
                "cur_skill_step": _read(lambda: float(skill._cur_skill_step[0])),
                "max_skill_steps": _read(lambda: int(skill._max_skill_steps)),
                "force_end_on_timeout": _read(lambda: bool(skill._force_end_on_timeout)),
                "hl_called_this_step": _read(lambda: bool(policy._cur_call_high_level[0])),
            }
        )
        return state

    def _oracle_flags(self, habitat_env: Any, agent_id: int) -> dict[str, Any]:
        """Read the raw oracle-nav finished sensor and skill_done flag."""
        actions = getattr(getattr(habitat_env, "task", None), "actions", {})
        action = (
            actions.get(f"agent_{agent_id}_oracle_nav_action")
            if isinstance(actions, dict)
            else None
        )
        return {
            "oracle_skill_done": _read(lambda: bool(action.skill_done)) if action else _UNAVAILABLE,
        }

    def record_reset(self, habitat_env: Any, config: Any) -> None:
        """Record the actual post-reset world state (never inferred from seed)."""
        if not self._enabled:
            return
        episode = getattr(habitat_env, "current_episode", None)
        problem = getattr(getattr(habitat_env, "task", None), "pddl_problem", None)
        self._predicates = _read(lambda: _goal_conjuncts(problem)) or []
        sim = habitat_env.sim
        document: dict[str, Any] = {
            "schema_version": DIAGNOSTICS_SCHEMA,
            "phase": "initial_world_state",
            "episode_id": _read(lambda: str(episode.episode_id)) if episode else _UNAVAILABLE,
            "scene_id": _read(lambda: str(episode.scene_id)) if episode else _UNAVAILABLE,
            "seed": getattr(config, "seed", None),
            "habitat_seed_config": _read(lambda: habitat_env._config.habitat.seed),
            "agents": {
                str(agent_id): {
                    "position": _read(_position, sim, agent_id),
                    "rotation": _read(_rotation, sim, agent_id),
                }
                for agent_id in self._agent_ids
            },
            "goal_conjuncts": [
                _read(lambda p=predicate: repr(p)) for _, predicate in self._predicates
            ],
            "goal_conjunct_values": self._official_conjunct_values(problem),
            "goal_entity_positions": {},
            "official_pddl_success": self._metrics(habitat_env).get("pddl_success"),
        }
        sim_info = getattr(problem, "sim_info", None)
        if sim_info is not None:
            entity_positions: dict[str, Any] = {}
            for _label, predicate in self._predicates:
                for argument in getattr(predicate, "_arg_values", None) or ():
                    name = getattr(argument, "name", None)
                    if isinstance(name, str):
                        entity_positions[name] = _read(_entity_position, sim_info, problem, name)
            document["goal_entity_positions"] = entity_positions
        self._write_json("diagnostics-initial.json", document)

    def record_step(
        self,
        step: int,
        skills: list[str],
        env_action: Any,
        habitat_env: Any,
        actor: Any,
        done: bool,
        info: dict[str, Any],
        observations: Any,
    ) -> None:
        """Stream one post-step observation row with official predicate truth."""
        if not self._enabled:
            return
        try:
            problem = getattr(getattr(habitat_env, "task", None), "pddl_problem", None)
            sim = habitat_env.sim
            action_summary: dict[str, Any] = {}
            try:
                for agent_id in self._agent_ids:
                    row = env_action[agent_id]
                    action_summary[str(agent_id)] = {
                        "argmax": int(row.argmax()),
                        "nonzero": [int(index) for index in range(len(row)) if row[index] != 0],
                    }
            except Exception as error:  # noqa: BLE001 - keep observing through malformed views
                action_summary = {"_status": _UNAVAILABLE, "reason": str(error)}
            document = {
                "schema_version": DIAGNOSTICS_SCHEMA,
                "simulator_step": step,
                "skills": skills,
                "action_summary": action_summary,
                "agents": {
                    str(agent_id): {
                        "position": _read(_position, sim, agent_id),
                        "skill_state": _read(self._agent_skill_state, actor, agent_id),
                        "oracle_flags": _read(self._oracle_flags, habitat_env, agent_id),
                    }
                    for agent_id in self._agent_ids
                },
                "goal_conjunct_values": self._official_conjunct_values(problem),
                "official_pddl_success_step_info": info.get("pddl_success"),
                "official_pddl_success_metrics": self._metrics(habitat_env).get("pddl_success"),
                "done": bool(done),
                "episode_over": _read(lambda: bool(habitat_env.episode_over)),
            }
            self._append_jsonl("diagnostics-steps.jsonl", document)
        except Exception:  # noqa: BLE001 - diagnostics must never break execution
            self._dropped_records += 1

    def record_terminal(self, habitat_env: Any, steps: int, reason: str) -> None:
        """Record the true final world state at episode termination."""
        if not self._enabled:
            return
        problem = getattr(getattr(habitat_env, "task", None), "pddl_problem", None)
        sim = habitat_env.sim
        document: dict[str, Any] = {
            "schema_version": DIAGNOSTICS_SCHEMA,
            "phase": "terminal_world_state",
            "termination_reason": reason,
            "simulator_steps": steps,
            "agents": {
                str(agent_id): {
                    "position": _read(_position, sim, agent_id),
                    "rotation": _read(_rotation, sim, agent_id),
                }
                for agent_id in self._agent_ids
            },
            "goal_conjunct_values": self._official_conjunct_values(problem),
            "official_metrics": self._metrics(habitat_env),
            "dropped_diagnostic_records": self._dropped_records,
        }
        self._write_json("diagnostics-terminal.json", document)
