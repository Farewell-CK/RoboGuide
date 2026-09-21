"""Read-only physical execution diagnostics for the shared-world bridge.

Diagnostics add observation only. They never call ``actor.act``,
``gym_env.step``, skill transitions, or terminal evaluation differently,
and never invoke any simulator API that mutates state or RNG. Every field
is read through existing read-only attributes of the original EMOS/Habitat
objects; a missing or unreadable field is recorded as ``unavailable`` with
its reason instead of being synthesized.

Official truth values come from the authoritative Habitat PDDL machinery
itself: each conjunct of ``task.pddl_problem.goal`` is evaluated with the
official ``Predicate.is_true(sim_info)`` path used by ``PddlSuccess``. The
expression and its optional truth cache are isolated before evaluation.
Derived comparisons such as ``over_max_len`` and skill-exit candidates are
explicitly labeled as inferred and never presented as observed Stage2 events.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any, cast

DIAGNOSTICS_SCHEMA = "roboguide.e1.physical-diagnostics/v0.2"
DIAGNOSTICS_ENV_FLAG = "ROBOGUIDE_B1_PHYSICAL_DIAGNOSTICS"
DIAGNOSTICS_MAX_RECORD_BYTES = 65_536
DIAGNOSTICS_WRITE_BATCH_RECORDS = 32

_UNAVAILABLE = "unavailable"
_READ_ONLY_OFFICIAL_PREDICATES = frozenset({"any_at"})


class BufferedJsonlWriter:
    """Append bounded JSONL batches without making evidence I/O execution authority.

    Documents remain in a fixed-size pending batch until the batch fills or
    ``flush`` is called at an execution boundary. Serialization and disk I/O
    therefore never occur on every simulator step. Any serialization or write
    failure drops only diagnostic records and is exposed through ``stats``.
    """

    def __init__(
        self,
        path: Path,
        *,
        batch_records: int = DIAGNOSTICS_WRITE_BATCH_RECORDS,
        serializer: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        """Bind one bounded writer without touching the filesystem."""
        if batch_records <= 0:
            raise ValueError("batch_records must be positive")
        self._path = path
        self._batch_records = batch_records
        self._serializer = serializer or self._serialize
        self._pending: list[dict[str, Any]] = []
        self._records_written = 0
        self._records_dropped = 0
        self._flushes = 0
        self._write_failures = 0
        self._write_seconds = 0.0

    @staticmethod
    def _serialize(document: dict[str, Any]) -> str:
        """Encode one compact deterministic JSONL document."""
        return json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n"

    def append(self, document: dict[str, Any]) -> None:
        """Queue one record and flush only when the fixed batch is full."""
        self._pending.append(document)
        if len(self._pending) >= self._batch_records:
            self.flush()

    def flush(self) -> None:
        """Best-effort flush one batch while suppressing all diagnostics failures."""
        if not self._pending:
            return
        pending = self._pending
        self._pending = []
        started = time.perf_counter()
        try:
            encoded = "".join(self._serializer(document) for document in pending)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as output:
                output.write(encoded)
            self._records_written += len(pending)
            self._flushes += 1
        except Exception:  # noqa: BLE001 - evidence I/O must not change execution
            self._records_dropped += len(pending)
            self._write_failures += 1
        finally:
            self._write_seconds += time.perf_counter() - started

    def stats(self) -> dict[str, Any]:
        """Return bounded-buffer and persistence accounting for run evidence."""
        return {
            "batch_capacity_records": self._batch_records,
            "flushes": self._flushes,
            "pending_records": len(self._pending),
            "records_dropped": self._records_dropped,
            "records_written": self._records_written,
            "write_failures": self._write_failures,
            "write_seconds": self._write_seconds,
        }


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
    return [
        float(component) for component in sim.get_agent_data(agent_id).articulated_agent.base_pos
    ]


def _rotation(sim: Any, agent_id: int) -> dict[str, Any]:
    """Read one agent's base rotation with an explicit representation and unit."""
    agent = sim.get_agent_data(agent_id).articulated_agent
    rot = getattr(agent, "base_rot", None)
    if rot is None:
        return {"_status": _UNAVAILABLE, "reason": "embodiment exposes no base_rot"}
    try:
        return {"representation": "yaw", "unit": "radians", "value": float(rot)}
    except (TypeError, ValueError):
        return {
            "representation": "components",
            "unit": "implementation_defined",
            "value": [float(component) for component in rot],
        }


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
    return [float(component) for component in sim_info.get_entity_pos(entity)]


def _json_scalar(value: Any) -> Any:
    """Convert one array-library scalar without importing a vendor dependency."""
    item = getattr(value, "item", None)
    if callable(item):
        converted = item()
        if converted is None or isinstance(converted, (bool, int, float, str)):
            return converted
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _action_row_summary(row: Any) -> dict[str, Any]:
    """Summarize one indexable action row without interpreting action semantics."""
    width = len(row)
    if width <= 0:
        raise ValueError("action row is empty")
    return {
        "argmax": int(row.argmax()),
        "nonzero": [int(index) for index in range(width) if row[index] != 0],
    }


def _flat_action_values(action: Any) -> list[float]:
    """Read a flat joint action view for shape-compatible diagnostic fallback."""
    value = action
    detach = getattr(value, "detach", None)
    if callable(detach):
        value = detach()
    cpu = getattr(value, "cpu", None)
    if callable(cpu):
        value = cpu()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        value = tolist()

    flattened: list[float] = []

    def collect(item: Any) -> None:
        """Collect numeric leaves while rejecting opaque action objects."""
        if isinstance(item, (list, tuple)):
            for child in item:
                collect(child)
            return
        scalar = item
        converter = getattr(scalar, "item", None)
        if callable(converter):
            scalar = converter()
        if not isinstance(scalar, (bool, int, float)):
            raise TypeError(f"action leaf {type(scalar).__name__} is not numeric")
        flattened.append(float(scalar))

    collect(value)
    if not flattened:
        raise ValueError("joint action is empty")
    return flattened


def _action_summary(env_action: Any, agent_ids: tuple[int, ...]) -> dict[str, Any]:
    """Summarize per-agent rows or retain an explicit flat joint-action scope."""
    try:
        return {str(agent_id): _action_row_summary(env_action[agent_id]) for agent_id in agent_ids}
    except Exception:  # noqa: BLE001 - deployment action layout may be flat
        values = _flat_action_values(env_action)
        shape = getattr(env_action, "shape", None)
        shape_value = (
            [int(dimension) for dimension in shape] if shape is not None else [len(values)]
        )
        return {
            "_scope": "joint_flat_action",
            "shape": shape_value,
            "argmax": max(range(len(values)), key=values.__getitem__),
            "nonzero": [index for index, value in enumerate(values) if value != 0.0],
        }


def _predicate_sim_info(sim_info: Any) -> Any:
    """Return an equivalent PDDL view whose predicate cache cannot be mutated.

    Habitat's official ``Predicate.is_true`` calculation is read-only with
    respect to simulator state, but it can populate ``pred_truth_cache`` on
    its ``PddlSimInfo`` argument.  Diagnostics use a shallow dataclass copy
    with that optional cache disabled, so their observation cannot affect the
    cache subsequently consumed by Habitat's original measures.
    """
    if hasattr(sim_info, "pred_truth_cache"):
        if not is_dataclass(sim_info) or isinstance(sim_info, type):
            raise TypeError("PDDL sim_info cache cannot be isolated by dataclass replacement")
        return replace(cast(Any, sim_info), pred_truth_cache=None)
    return sim_info


def _clone_expression(expression: Any) -> Any:
    """Clone an official PDDL expression so diagnostic evaluation cannot mutate it."""
    clone = getattr(expression, "clone", None)
    if not callable(clone):
        raise TypeError("PDDL expression exposes no read-isolating clone operation")
    return clone()


def _finished_sensor(observations: Any, agent_id: int) -> bool:
    """Read one post-step OracleNav finished sensor without deriving a value."""
    key = f"agent_{agent_id}_has_finished_oracle_nav"
    if not isinstance(observations, dict) or key not in observations:
        raise ValueError(f"Habitat observation {key!r} is unavailable")
    value = observations[key]
    try:
        return bool(value[0])
    except (IndexError, KeyError, TypeError) as error:
        raise ValueError(f"Habitat observation {key!r} has invalid shape") from error


def _over_max_len(current: Any, maximum: Any) -> dict[str, Any]:
    """Label a timeout threshold comparison as an inference, never an event."""
    if isinstance(current, dict) or isinstance(maximum, dict):
        return {
            "_status": _UNAVAILABLE,
            "reason": "skill step counters are unavailable",
        }
    try:
        maximum_int = int(maximum)
        return {
            "_status": "inferred",
            "basis": "max_skill_steps > 0 and cur_skill_step >= max_skill_steps",
            "value": bool(maximum_int > 0 and float(current) >= maximum_int),
        }
    except (TypeError, ValueError) as error:
        return {"_status": _UNAVAILABLE, "reason": f"invalid skill counters: {error}"}


class PhysicalDiagnostics:
    """Record initial, per-step, and terminal physical execution evidence.

    Output uses bounded JSONL batches plus initial/terminal JSON snapshots.
    Full batches are durable before episode termination; the terminal path
    flushes the last partial batch. A deployment-provided step-record budget,
    fixed batch capacity, and per-document byte cap bound memory and disk use.
    A capped or unserializable record is represented as unavailable rather
    than silently fabricated. Cross-step state is limited to the pending
    batch, counters, and one previous skill name per configured agent.
    """

    def __init__(
        self,
        evidence_dir: Any,
        agent_ids: tuple[int, ...],
        enabled: bool,
        max_step_records: int,
        write_batch_records: int = DIAGNOSTICS_WRITE_BATCH_RECORDS,
    ) -> None:
        """Bind diagnostics to one run's evidence directory."""
        if max_step_records < 0:
            raise ValueError("max_step_records must be non-negative")
        self._dir = evidence_dir
        self._agent_ids = agent_ids
        self._enabled = enabled
        self._max_step_records = max_step_records
        self._predicates: list[tuple[str, Any]] = []
        self._dropped_records = 0
        self._step_records_written = 0
        self._step_budget_reported = False
        self._previous_skills: dict[int, str] = {}
        self._step_observations = 0
        self._step_samples_dropped = 0
        self._capture_seconds = 0.0
        self._capture_max_seconds = 0.0
        self._step_writer = BufferedJsonlWriter(
            self._dir / "diagnostics-steps.jsonl",
            batch_records=write_batch_records,
            serializer=lambda document: self._serialize_document(document, indent=None),
        )

    def _unavailable_document(self, document: dict[str, Any], reason: str) -> dict[str, Any]:
        """Build a bounded record when a requested diagnostic document is unavailable."""
        unavailable = {
            "_status": _UNAVAILABLE,
            "phase": document.get("phase", "per_step"),
            "reason": reason,
            "schema_version": DIAGNOSTICS_SCHEMA,
            "simulator_step": document.get("simulator_step"),
        }
        for key in ("collection_stats", "dropped_diagnostic_records"):
            if key in document:
                unavailable[key] = document[key]
        return unavailable

    def _serialize_document(self, document: dict[str, Any], *, indent: int | None) -> str:
        """Encode one bounded document, degrading serialization failures to unavailable."""
        try:
            encoded = json.dumps(
                document,
                default=_json_scalar,
                ensure_ascii=False,
                indent=indent,
                sort_keys=True,
            )
        except Exception as error:  # noqa: BLE001 - serialization must not affect execution
            self._dropped_records += 1
            encoded = json.dumps(
                self._unavailable_document(document, f"JSON serialization failed: {error}"),
                ensure_ascii=False,
                indent=indent,
                sort_keys=True,
            )
        if len(encoded.encode("utf-8")) > DIAGNOSTICS_MAX_RECORD_BYTES:
            self._dropped_records += 1
            encoded = json.dumps(
                self._unavailable_document(
                    document,
                    f"diagnostic record exceeds {DIAGNOSTICS_MAX_RECORD_BYTES} byte limit",
                ),
                ensure_ascii=False,
                indent=indent,
                sort_keys=True,
            )
        return encoded + "\n"

    def _write_json(self, name: str, document: dict[str, Any]) -> None:
        """Write one bounded diagnostics snapshot document."""
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / name).write_text(
            self._serialize_document(document, indent=2), encoding="utf-8"
        )

    def _append_step(self, document: dict[str, Any]) -> None:
        """Queue one bounded step record for batched persistence."""
        self._step_writer.append(document)

    def _collection_stats(self) -> dict[str, Any]:
        """Report sampling, buffering, loss, and observer overhead explicitly."""
        writer = self._step_writer.stats()
        return {
            "capture_max_seconds": self._capture_max_seconds,
            "capture_seconds": self._capture_seconds,
            "dropped_step_records": self._step_samples_dropped + int(writer["records_dropped"]),
            "max_record_bytes": DIAGNOSTICS_MAX_RECORD_BYTES,
            "sampling_period_simulator_steps": 1,
            "step_observations": self._step_observations,
            "step_records_accepted": self._step_records_written,
            "writer": writer,
        }

    def _write_unavailable_snapshot(
        self, name: str, phase: str, error: Exception, step: int | None = None
    ) -> None:
        """Best-effort one unavailable snapshot after a top-level read failure."""
        try:
            document: dict[str, Any] = {
                "_status": _UNAVAILABLE,
                "phase": phase,
                "reason": f"{type(error).__name__}: {error}",
                "schema_version": DIAGNOSTICS_SCHEMA,
                "simulator_step": step,
            }
            if phase == "terminal_world_state":
                document["collection_stats"] = self._collection_stats()
                document["dropped_diagnostic_records"] = self._dropped_records
            self._write_json(
                name,
                document,
            )
        except Exception:  # noqa: BLE001 - storage may itself be unavailable
            return

    def _load_predicates(self, problem: Any) -> None:
        """Load goal conjunct references or retain an empty unavailable state."""
        predicates = _read(lambda: _goal_conjuncts(problem))
        self._predicates = predicates if isinstance(predicates, list) else []

    def _official_conjunct_values(self, problem: Any) -> dict[str, Any]:
        """Evaluate each goal conjunct through the official predicate path."""
        if not self._predicates:
            # The reset record may have failed or been skipped; conjunct labels
            # are pure configuration reads, so recover them lazily.
            self._load_predicates(problem)
        if not self._predicates:
            return {"_status": _UNAVAILABLE, "reason": "goal conjuncts unavailable"}
        sim_info = getattr(problem, "sim_info", None)
        if sim_info is None:
            return {
                label: {"_status": _UNAVAILABLE, "reason": "sim_info unbound"}
                for label, _ in self._predicates
            }
        values: dict[str, Any] = {}
        for label, predicate in self._predicates:
            predicate_name = getattr(predicate, "name", None)
            if predicate_name not in _READ_ONLY_OFFICIAL_PREDICATES:
                values[label] = {
                    "_status": _UNAVAILABLE,
                    "reason": (
                        f"predicate {predicate_name!r} is not admitted for side-effect-free "
                        "diagnostic evaluation"
                    ),
                }
                continue
            values[label] = _read(
                lambda p=predicate: bool(
                    _clone_expression(p).is_true(_predicate_sim_info(sim_info))
                )
            )
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
        skills = getattr(policy, "_skills", None)
        skill = skills.get(index) if isinstance(skills, dict) else None
        state: dict[str, Any] = {"skill": name}
        if skill is None:
            state["_skill_detail"] = {
                "_status": _UNAVAILABLE,
                "reason": "active skill object unavailable",
            }
            return state
        current = _read(lambda: float(skill._cur_skill_step[0]))
        maximum = _read(lambda: int(skill._max_skill_steps))
        call_high_level_next_step = _read(lambda: bool(policy._cur_call_high_level[0]))
        state.update(
            {
                "cur_skill_step": current,
                "max_skill_steps": maximum,
                "force_end_on_timeout": _read(lambda: bool(skill._force_end_on_timeout)),
                "call_high_level_next_step": call_high_level_next_step,
                "over_max_len": _over_max_len(current, maximum),
            }
        )
        return state

    def _skill_exit_reason(self, skill_state: Any, finished_sensor: Any) -> dict[str, Any]:
        """Describe exit-cause evidence while marking the conclusion as inferred."""
        if not isinstance(skill_state, dict):
            return {"_status": _UNAVAILABLE, "reason": "skill state unavailable"}
        call_next = skill_state.get("call_high_level_next_step")
        over_max = skill_state.get("over_max_len")
        if isinstance(call_next, dict):
            return {"_status": _UNAVAILABLE, "reason": "high-level call flag unavailable"}
        if call_next is not True:
            return {
                "_status": "not_applicable",
                "reason": "active skill did not request a high-level call for the next step",
            }
        candidates: list[str] = []
        if finished_sensor is True:
            candidates.append("oracle_finished_sensor")
        if isinstance(over_max, dict) and over_max.get("value") is True:
            candidates.append("skill_step_budget")
        if not candidates:
            candidates.append("high_level_requested_termination_or_unexposed_cause")
        return {
            "_status": "inferred",
            "basis": {
                "call_high_level_next_step": call_next,
                "oracle_nav_finished_sensor_policy_input": finished_sensor,
                "over_max_len": over_max,
            },
            "candidates": candidates,
            "reason": "original Stage2 exposes no durable per-skill exit-cause event",
        }

    def _oracle_flags(self, habitat_env: Any, agent_id: int) -> dict[str, Any]:
        """Read the raw oracle-nav finished sensor and skill_done flag."""
        actions = getattr(getattr(habitat_env, "task", None), "actions", {})
        action = (
            actions.get(f"agent_{agent_id}_oracle_nav_action")
            if isinstance(actions, dict)
            else None
        )
        return {
            "oracle_skill_done": _read(lambda: bool(action.skill_done))
            if action
            else {"_status": _UNAVAILABLE, "reason": "oracle action unavailable"},
        }

    def _annotate_skill_transition(self, agent_id: int, state: Any) -> Any:
        """Add bounded transition context without inferring an unavailable exit cause."""
        if not isinstance(state, dict):
            return state
        current = state.get("skill")
        if not isinstance(current, str):
            return state
        previous = self._previous_skills.get(agent_id)
        state["previous_skill"] = (
            previous
            if previous is not None
            else {
                "_status": _UNAVAILABLE,
                "reason": "no earlier diagnostic sample",
            }
        )
        state["skill_transition"] = previous is not None and previous != current
        self._previous_skills[agent_id] = current
        return state

    def _record_failure(self) -> None:
        """Count one diagnostics-only failure after suppressing it from execution."""
        self._dropped_records += 1

    def record_reset(self, habitat_env: Any, config: Any) -> None:
        """Record the actual post-reset world state (never inferred from seed)."""
        if not self._enabled:
            return
        try:
            episode = getattr(habitat_env, "current_episode", None)
            problem = getattr(getattr(habitat_env, "task", None), "pddl_problem", None)
            self._load_predicates(problem)
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
                "collection_configuration": {
                    "max_step_records": self._max_step_records,
                    "sampling_period_simulator_steps": 1,
                    "write_batch_records": self._step_writer.stats()["batch_capacity_records"],
                },
            }
            sim_info = getattr(problem, "sim_info", None)
            if sim_info is not None:
                entity_positions: dict[str, Any] = {}
                for _label, predicate in self._predicates:
                    for argument in getattr(predicate, "_arg_values", None) or ():
                        name = getattr(argument, "name", None)
                        if isinstance(name, str):
                            entity_positions[name] = _read(
                                _entity_position, sim_info, problem, name
                            )
                document["goal_entity_positions"] = entity_positions
            self._write_json("diagnostics-initial.json", document)
        except Exception as error:  # noqa: BLE001 - diagnostics must never break execution
            self._record_failure()
            self._write_unavailable_snapshot(
                "diagnostics-initial.json", "initial_world_state", error
            )

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
        policy_input_observations: Any = None,
    ) -> None:
        """Stream one post-step observation row with official predicate truth."""
        if not self._enabled:
            return
        started = time.perf_counter()
        self._step_observations += 1
        try:
            if self._step_records_written >= self._max_step_records:
                self._record_failure()
                self._step_samples_dropped += 1
                if not self._step_budget_reported:
                    self._append_step(
                        self._unavailable_document(
                            {"simulator_step": step},
                            f"step-record budget {self._max_step_records} exhausted",
                        ),
                    )
                    self._step_budget_reported = True
                return
            problem = getattr(getattr(habitat_env, "task", None), "pddl_problem", None)
            sim = habitat_env.sim
            action_summary = _read(_action_summary, env_action, self._agent_ids)
            document = {
                "schema_version": DIAGNOSTICS_SCHEMA,
                "simulator_step": step,
                "skills": skills,
                "action_summary": action_summary,
                "agents": {},
                "goal_conjunct_values": self._official_conjunct_values(problem),
                "official_pddl_success_step_info": info.get("pddl_success"),
                "official_pddl_success_metrics": self._metrics(habitat_env).get("pddl_success"),
                "done": bool(done),
                "episode_over": _read(lambda: bool(habitat_env.episode_over)),
            }
            agent_records: dict[str, Any] = {}
            for agent_id in self._agent_ids:
                skill_state = self._annotate_skill_transition(
                    agent_id, _read(self._agent_skill_state, actor, agent_id)
                )
                policy_input_finished = _read(
                    _finished_sensor,
                    policy_input_observations,
                    agent_id,
                )
                agent_records[str(agent_id)] = {
                    "position": _read(_position, sim, agent_id),
                    "rotation": _read(_rotation, sim, agent_id),
                    "skill_state": skill_state,
                    "skill_exit_reason": self._skill_exit_reason(
                        skill_state, policy_input_finished
                    ),
                    "oracle_flags": _read(self._oracle_flags, habitat_env, agent_id),
                    "oracle_nav_finished_sensor_policy_input": policy_input_finished,
                    "oracle_nav_finished_sensor_post_step": _read(
                        _finished_sensor, observations, agent_id
                    ),
                }
            document["agents"] = agent_records
            self._append_step(document)
            self._step_records_written += 1
        except Exception:  # noqa: BLE001 - diagnostics must never break execution
            self._record_failure()
            self._step_samples_dropped += 1
        finally:
            elapsed = time.perf_counter() - started
            self._capture_seconds += elapsed
            self._capture_max_seconds = max(self._capture_max_seconds, elapsed)

    def record_terminal(self, habitat_env: Any, steps: int, reason: str) -> None:
        """Record the true final world state at episode termination."""
        if not self._enabled:
            return
        self._step_writer.flush()
        try:
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
                "collection_stats": self._collection_stats(),
            }
            self._write_json("diagnostics-terminal.json", document)
        except Exception as error:  # noqa: BLE001 - diagnostics must never break execution
            self._record_failure()
            self._write_unavailable_snapshot(
                "diagnostics-terminal.json", "terminal_world_state", error, steps
            )


class UnavailablePhysicalDiagnostics:
    """Fail-soft recorder used when optional diagnostics cannot initialize."""

    def __init__(self, evidence_dir: Any, enabled: bool, reason: str) -> None:
        """Retain the unavailable reason without touching the execution environment."""
        self._dir = evidence_dir
        self._enabled = enabled
        self._reason = reason
        self._step_reported = False

    def _write(self, name: str, phase: str, step: int | None = None) -> None:
        """Best-effort one unavailable document without propagating write failures."""
        if not self._enabled:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            document = {
                "_status": _UNAVAILABLE,
                "phase": phase,
                "reason": f"diagnostics initialization failed: {self._reason}",
                "schema_version": DIAGNOSTICS_SCHEMA,
                "simulator_step": step,
            }
            mode = "a" if name.endswith(".jsonl") else "w"
            with (self._dir / name).open(mode, encoding="utf-8") as output:
                output.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:  # noqa: BLE001 - unavailable diagnostics remain non-authoritative
            return

    def record_reset(self, habitat_env: Any, config: Any) -> None:
        """Record that initial diagnostics are unavailable, when storage permits."""
        self._write("diagnostics-initial.json", "initial_world_state")

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
        policy_input_observations: Any = None,
    ) -> None:
        """Record one bounded unavailable step marker and ignore later steps."""
        if not self._step_reported:
            self._write("diagnostics-steps.jsonl", "per_step", step)
            self._step_reported = True

    def record_terminal(self, habitat_env: Any, steps: int, reason: str) -> None:
        """Record that terminal diagnostics are unavailable, when storage permits."""
        self._write("diagnostics-terminal.json", "terminal_world_state", steps)


def create_physical_diagnostics(
    evidence_dir: Any,
    agent_ids: tuple[int, ...],
    enabled: bool,
    max_step_records: int,
) -> PhysicalDiagnostics | UnavailablePhysicalDiagnostics:
    """Create optional diagnostics without allowing initialization to block execution."""
    try:
        return PhysicalDiagnostics(evidence_dir, agent_ids, enabled, max_step_records)
    except Exception as error:  # noqa: BLE001 - optional diagnostics cannot block execution
        return UnavailablePhysicalDiagnostics(
            evidence_dir,
            enabled,
            f"{type(error).__name__}: {error}",
        )
