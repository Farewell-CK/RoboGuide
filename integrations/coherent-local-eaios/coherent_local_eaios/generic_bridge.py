"""Generic per-primitive RoboGuide bridge for the COHERENT graph benchmark."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .bridge import TERMINAL_STATES, IntegrationError

OPERATION_PATTERN = re.compile(r"coherent\.agent-(\d+)-primitive@v1\Z")


@dataclass(frozen=True)
class PrimitiveInvocation:
    """One validated primitive action selected by Mission Intelligence."""

    mission_id: str
    task_id: str
    group_id: str
    role_id: str
    operation: str
    objective: str
    parameters: dict[str, object]
    resource_ids: tuple[str, ...]

    @classmethod
    def from_request(cls, request: object) -> PrimitiveInvocation:
        """Validate the canonical invocation and its generic primitive contract."""
        body = _object(request, "request")
        if set(body) != {"invocation"}:
            raise IntegrationError("execute request must contain only invocation")
        raw = _object(body["invocation"], "invocation")
        expected = {
            "mission_id",
            "task_id",
            "group_id",
            "role_id",
            "operation",
            "objective",
            "parameters",
            "resource_ids",
        }
        if set(raw) != expected:
            raise IntegrationError("canonical invocation fields do not match the generic contract")
        operation = _string(raw["operation"], "operation")
        match = OPERATION_PATTERN.fullmatch(operation)
        if match is None:
            raise IntegrationError(f"unsupported operation {operation!r}")
        parameters = _object(raw["parameters"], "parameters")
        required = {"env", "task", "agent_id", "agent_class", "action"}
        if set(parameters) != required:
            raise IntegrationError(
                "primitive parameters must be env/task/agent_id/agent_class/action"
            )
        agent_id = _integer(parameters["agent_id"], "parameters.agent_id")
        if agent_id != int(match.group(1)):
            raise IntegrationError("operation agent identity does not match parameters.agent_id")
        env_name = _string(parameters["env"], "parameters.env")
        if re.fullmatch(r"env[0-4]", env_name) is None:
            raise IntegrationError("parameters.env must be env0 through env4")
        task = _integer(parameters["task"], "parameters.task")
        if task < 0:
            raise IntegrationError("parameters.task must be non-negative")
        normalized: dict[str, object] = {
            "env": env_name,
            "task": task,
            "agent_id": agent_id,
            "agent_class": _string(parameters["agent_class"], "parameters.agent_class"),
            "action": _string(parameters["action"], "parameters.action"),
        }
        raw_resources = raw["resource_ids"]
        if not isinstance(raw_resources, list):
            raise IntegrationError("resource_ids must be an array")
        resources = tuple(_string(value, "resource_ids") for value in raw_resources)
        if len(resources) != len(set(resources)):
            raise IntegrationError("resource_ids contains duplicates")
        return cls(
            mission_id=_string(raw["mission_id"], "mission_id"),
            task_id=_string(raw["task_id"], "task_id"),
            group_id=_string(raw["group_id"], "group_id"),
            role_id=_string(raw["role_id"], "role_id"),
            operation=operation,
            objective=_string(raw["objective"], "objective"),
            parameters=normalized,
            resource_ids=resources,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable JSON form used for idempotency and evidence."""
        return {
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "group_id": self.group_id,
            "role_id": self.role_id,
            "operation": self.operation,
            "objective": self.objective,
            "parameters": dict(self.parameters),
            "resource_ids": list(self.resource_ids),
        }

    def request_key(self) -> str:
        """Return a stable idempotency digest for this exact invocation."""
        raw = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class PrimitiveStore:
    """Persist one task graph and exactly-once primitive executions in SQLite."""

    def __init__(self, database: Path, initial_graph: dict[str, object]) -> None:
        """Create the durable task state without replacing an existing run."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS executions (
                execution_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                invocation_json TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL,
                local_outcome_json TEXT, created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS graph_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1), graph_json TEXT NOT NULL,
                step_count INTEGER NOT NULL, updated_at_unix_ms INTEGER NOT NULL)"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO graph_state VALUES (1, ?, 0, ?)",
                (json.dumps(initial_graph, sort_keys=True), _now_ms()),
            )
            connection.execute(
                """UPDATE executions SET state='FAILED',
                detail='generic bridge restarted during execution', updated_at_unix_ms=?
                WHERE state IN ('ACCEPTED','RUNNING')""",
                (_now_ms(),),
            )

    def create_or_get(self, invocation: PrimitiveInvocation) -> tuple[dict[str, object], bool]:
        """Create a handle or return the prior handle for an identical request."""
        key = invocation.request_key()
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE request_key=?", (key,)
            ).fetchone()
            if row is not None:
                return self._decode(row), False
            now = _now_ms()
            execution_id = "coherent-primitive-" + key[:20]
            connection.execute(
                """INSERT INTO executions VALUES (?, ?, ?, 'ACCEPTED', ?, NULL, ?, ?)""",
                (
                    execution_id,
                    key,
                    json.dumps(invocation.as_dict(), sort_keys=True),
                    "accepted by generic COHERENT primitive bridge",
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if row is None:
                raise IntegrationError("primitive execution disappeared after creation")
            return self._decode(row), True

    def get(self, execution_id: str) -> dict[str, object] | None:
        """Read one durable primitive execution."""
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode(row)

    def graph_state(self) -> tuple[dict[str, object], int]:
        """Read a detached graph and the number of applied primitives."""
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM graph_state WHERE singleton=1").fetchone()
            if row is None:
                raise IntegrationError("durable graph state is missing")
            return _object(json.loads(str(row["graph_json"])), "graph"), int(row["step_count"])

    def mark_running(self, execution_id: str) -> None:
        """Move an accepted execution to running."""
        self._transition(execution_id, "ACCEPTED", "RUNNING", "primitive started", None)

    def complete(
        self,
        execution_id: str,
        expected_step: int,
        graph: dict[str, object],
        outcome: dict[str, object],
    ) -> None:
        """Atomically commit the new graph and terminal local execution fact."""
        with self.lock, self._connect() as connection:
            state = connection.execute(
                "SELECT step_count FROM graph_state WHERE singleton=1"
            ).fetchone()
            execution = connection.execute(
                "SELECT state FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if state is None or int(state["step_count"]) != expected_step:
                raise IntegrationError("graph changed before primitive commit")
            if execution is None or str(execution["state"]) != "RUNNING":
                raise IntegrationError("primitive is not running at graph commit")
            now = _now_ms()
            connection.execute(
                "UPDATE graph_state SET graph_json=?, step_count=?, updated_at_unix_ms=? "
                "WHERE singleton=1",
                (json.dumps(graph, sort_keys=True), expected_step + 1, now),
            )
            connection.execute(
                """UPDATE executions SET state='COMPLETED', detail=?,
                local_outcome_json=?, updated_at_unix_ms=? WHERE execution_id=?""",
                (
                    "official COHERENT primitive completed",
                    json.dumps(outcome, sort_keys=True),
                    now,
                    execution_id,
                ),
            )

    def fail(self, execution_id: str, detail: str, outcome: dict[str, object]) -> None:
        """Persist a terminal failure without changing the shared graph."""
        execution = self.get(execution_id)
        if execution is None or execution["state"] in TERMINAL_STATES:
            return
        self._transition(
            execution_id,
            cast(str, execution["state"]),
            "FAILED",
            detail,
            outcome,
        )

    def _transition(
        self,
        execution_id: str,
        expected: str,
        state: str,
        detail: str,
        outcome: dict[str, object] | None,
    ) -> None:
        """Apply one guarded execution-only transition."""
        with self.lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM executions WHERE execution_id=?", (execution_id,)
            ).fetchone()
            if row is None or str(row["state"]) != expected:
                raise IntegrationError("invalid primitive execution transition")
            connection.execute(
                """UPDATE executions SET state=?, detail=?, local_outcome_json=?,
                updated_at_unix_ms=? WHERE execution_id=?""",
                (
                    state,
                    detail[:2000],
                    None if outcome is None else json.dumps(outcome, sort_keys=True),
                    _now_ms(),
                    execution_id,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one named-row SQLite connection."""
        connection = sqlite3.connect(str(self.database), timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, object]:
        """Decode and revalidate one execution row."""
        invocation = PrimitiveInvocation.from_request(
            {"invocation": json.loads(str(row["invocation_json"]))}
        )
        raw_outcome = row["local_outcome_json"]
        return {
            "execution_id": str(row["execution_id"]),
            "invocation": invocation,
            "state": str(row["state"]),
            "detail": str(row["detail"]),
            "local_outcome": None if raw_outcome is None else json.loads(str(raw_outcome)),
            "updated_at_unix_ms": int(row["updated_at_unix_ms"]),
        }


@dataclass(frozen=True)
class GenericBackendConfig:
    """Startup-frozen benchmark task and evidence locations."""

    env_name: str
    task_index: int
    pefa_root: Path
    evidence_dir: Path
    task_data: dict[str, object]


class CoherentPrimitiveAdapter:
    """Apply model-selected primitives through the original COHERENT environment."""

    def __init__(self, store: PrimitiveStore, config: GenericBackendConfig) -> None:
        """Retain one shared task state and immutable task identity."""
        self.store = store
        self.config = config
        self.lock = threading.RLock()

    def close(self) -> None:
        """Close the synchronous adapter; it owns no worker threads."""

    def health(self) -> dict[str, object]:
        """Report process health separately from operation readiness."""
        return {"state": "ONLINE", "detail": "generic COHERENT primitive bridge is online"}

    def readiness(self) -> dict[str, object]:
        """Report the generic per-agent primitive operation family."""
        return {"state": "READY", "detail": "current official primitive actions are available"}

    def accept(self, request: object) -> dict[str, object]:
        """Execute a newly accepted primitive exactly once."""
        invocation = PrimitiveInvocation.from_request(request)
        with self.lock:
            execution, created = self.store.create_or_get(invocation)
            if created:
                self._execute(cast(str, execution["execution_id"]), invocation)
                updated = self.store.get(cast(str, execution["execution_id"]))
                if updated is None:
                    raise IntegrationError("primitive disappeared after execution")
                execution = updated
            return self._response(execution)

    def dispatch(self, execution_id: str) -> None:
        """Honor the shared server contract after synchronous execution."""
        if self.store.get(execution_id) is None:
            raise IntegrationError(f"unknown primitive execution {execution_id!r}")

    def status(self, request: object) -> dict[str, object]:
        """Return one durable primitive execution state."""
        execution_id = _execution_id(request)
        execution = self.store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown primitive execution {execution_id!r}")
        return self._response(execution)

    def cancel(self, request: object) -> dict[str, object]:
        """Reject cancellation because a primitive commits synchronously."""
        response = self.status(request)
        response["accepted"] = False
        response["detail"] = "cancellation is unsupported for a committed graph primitive"
        return response

    def _execute(self, execution_id: str, invocation: PrimitiveInvocation) -> None:
        """Validate one current official action, apply it, and save raw evidence."""
        try:
            self.store.mark_running(execution_id)
            graph, step_count = self.store.graph_state()
            parameters = invocation.parameters
            if (
                parameters["env"] != self.config.env_name
                or parameters["task"] != self.config.task_index
            ):
                raise IntegrationError("invocation task identity differs from bridge startup task")
            agent_id = cast(int, parameters["agent_id"])
            agent_class = cast(str, parameters["agent_class"])
            action = cast(str, parameters["action"])
            contexts = action_contexts(self.config.pefa_root, self.config.task_data, graph)
            context = contexts.get(agent_id)
            if context is None or context["agent_class"] != agent_class:
                raise IntegrationError("invocation agent identity is not present in this task")
            allowed = cast(list[str], context["available_actions"])
            if action not in allowed:
                raise IntegrationError("selected action is not in the current official action list")
            new_graph, transition = apply_original_step(
                self.config.pefa_root,
                self.config.task_data,
                graph,
                step_count,
                agent_class,
                agent_id,
                action,
                self.config.task_index,
            )
            outcome: dict[str, object] = {
                "env": self.config.env_name,
                "task": self.config.task_index,
                "step_before": step_count,
                "allowed_actions": allowed,
                "transition": transition,
            }
            self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence = self.config.evidence_dir / f"{step_count + 1:03d}-primitive.json"
            evidence.write_text(json.dumps(outcome, indent=2, sort_keys=True), encoding="utf-8")
            outcome["evidence_file"] = str(evidence)
            self.store.complete(execution_id, step_count, new_graph, outcome)
        except Exception as error:
            self.store.fail(
                execution_id,
                f"COHERENT primitive failed: {error}",
                {"error_type": type(error).__name__, "error": str(error)},
            )

    @staticmethod
    def _response(execution: dict[str, object]) -> dict[str, object]:
        """Project durable execution state into the Node workflow response."""
        invocation = execution["invocation"]
        if not isinstance(invocation, PrimitiveInvocation):
            raise IntegrationError("stored primitive invocation has an invalid type")
        return {
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


def load_task_data(pefa_root: Path, env_name: str, task_index: int) -> dict[str, object]:
    """Load one indexed task from any official COHERENT environment file."""
    path = pefa_root / "env" / f"{env_name}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrationError(f"cannot read public task dataset: {error}") from error
    if not isinstance(document, list) or task_index < 0 or task_index >= len(document):
        raise IntegrationError(f"{env_name} does not contain task{task_index}")
    task = _object(document[task_index], f"{env_name}.task{task_index}")
    required = {
        "env_id",
        "task_name",
        "init_graph",
        "task_goal",
        "goal_instruction",
        "ground_truth_step_num",
    }
    if not required.issubset(task):
        raise IntegrationError("official task is missing required benchmark fields")
    return task


def initial_graph(task_data: dict[str, object]) -> dict[str, object]:
    """Return a detached initial graph for a fresh generic run."""
    return _object(copy.deepcopy(_object(task_data["init_graph"], "init_graph")), "init_graph")


def make_environment(
    pefa_root: Path,
    task_data: dict[str, object],
    graph: dict[str, object],
    task_index: int,
) -> Any:
    """Construct the original COHERENT environment around a supplied graph."""
    root = str(pefa_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    module = importlib.import_module("get_env_info")
    agents = [
        [node["class_name"], node["id"]]
        for node in _nodes(graph)
        if node.get("category") == "Agents"
    ]
    return module.Get_env_info(
        task_id=task_index,
        env_id=task_data["env_id"],
        task_name=task_data["task_name"],
        graph=graph,
        task_goal=task_data["task_goal"],
        goal_instruction=task_data["goal_instruction"],
        ground_truth_step_num=task_data["ground_truth_step_num"],
        agent=agents,
        num_agent=len(agents),
    )


def action_contexts(
    pefa_root: Path,
    task_data: dict[str, object],
    graph: dict[str, object],
) -> dict[int, dict[str, object]]:
    """Return official partial observations and current action lists for every agent."""
    environment = make_environment(pefa_root, task_data, graph, 0)
    observations = environment.get_observations()
    initial = _object(task_data["init_graph"], "init_graph")
    initial_nodes = {_integer(node["id"], "node.id"): node for node in _nodes(initial)}
    contexts: dict[int, dict[str, object]] = {}
    for ordinal, identity in environment.id_name_dict.items():
        agent_class, raw_agent_id = identity
        agent_id = int(raw_agent_id)
        observation = _object(observations[ordinal], "observation")
        nodes = _nodes(observation)
        edges = _edges(observation)
        by_id = {_integer(node["id"], "node.id"): node for node in nodes}
        agent = by_id[agent_id]
        current_room: dict[str, object] | None = None
        on_surface: dict[str, object] | None = None
        held: dict[str, object] | None = None
        reachable: list[dict[str, object]] = []
        landable: dict[str, object] | None = None
        same_ids: set[int] = set()
        for edge in edges:
            if edge.get("from_id") != agent_id:
                continue
            target = by_id.get(int(cast(int, edge.get("to_id"))))
            if target is None:
                continue
            relation = edge.get("relation_type")
            if relation == "INSIDE":
                current_room = target
            elif relation == "ON":
                on_surface = target
            elif relation == "HOLD":
                held = target
            elif relation == "CLOSE":
                reachable.append(target)
            elif relation == "ABOVE" and "LANDABLE" in cast(list[object], target["properties"]):
                landable = target
        same_surface: list[dict[str, object]] = []
        if str(agent_class).replace("_", " ") == "robot arm" and on_surface is not None:
            frontier = {_integer(on_surface["id"], "surface.id")}
            for _ in range(3):
                for edge in edges:
                    if (
                        edge.get("from_id") != agent_id
                        and edge.get("to_id") in frontier
                        and edge.get("relation_type") in {"ON", "INSIDE"}
                    ):
                        same_ids.add(int(cast(int, edge["from_id"])))
                frontier |= same_ids
            same_surface = [by_id[item] for item in sorted(same_ids) if item in by_id]
        excluded = {agent_id}
        excluded.update(_integer(item["id"], "reachable.id") for item in reachable)
        if held is not None:
            excluded.add(_integer(held["id"], "held.id"))
        unreached = [
            node
            for node in nodes
            if _integer(node["id"], "node.id") not in excluded
            and node.get("category") not in {"Rooms", "Agents", "Floor"}
            and "HIGH_HEIGHT" not in cast(list[object], node["properties"])
            and "ON_HIGH_SURFACE" not in cast(list[object], node["properties"])
        ]
        doors = [node for node in nodes if node.get("class_name") == "door"]
        next_rooms: list[list[dict[str, object]]] = []
        if current_room is not None:
            for door in doors:
                for edge in _edges(initial):
                    if (
                        edge.get("relation_type") == "LEADING TO"
                        and edge.get("from_id") == door.get("id")
                        and edge.get("to_id") != current_room.get("id")
                    ):
                        target = initial_nodes.get(int(cast(int, edge["to_id"])))
                        if target is not None:
                            next_rooms.append([target, door])
        actions = _available_actions(
            agent,
            next_rooms,
            copy.deepcopy(
                [node for node in nodes if "LANDABLE" in cast(list[object], node["properties"])]
            ),
            landable,
            on_surface,
            held,
            reachable,
            unreached,
            same_surface,
        )
        contexts[agent_id] = {
            "agent_id": agent_id,
            "agent_class": str(agent_class),
            "observation": observation,
            "available_actions": list(dict.fromkeys(actions)),
        }
    return contexts


def _available_actions(
    agent: dict[str, object],
    next_rooms: list[list[dict[str, object]]],
    all_landable: list[dict[str, object]],
    landable: dict[str, object] | None,
    on_surface: dict[str, object] | None,
    held: dict[str, object] | None,
    reachable: list[dict[str, object]],
    unreached: list[dict[str, object]],
    same_surface: list[dict[str, object]],
) -> list[str]:
    """Reproduce COHERENT ``LLM.get_available_plans`` without loading its API client."""
    actions: list[str] = []

    def unary(verb: str, item: dict[str, object]) -> str:
        return f"[{verb}] <{item['class_name']}>({item['id']})"

    def binary(verb: str, item: dict[str, object], target: dict[str, object]) -> str:
        separator = " into " if verb == "putinto" else " on "
        return (
            f"[{verb}] <{item['class_name']}>({item['id']}){separator}"
            f"<{target['class_name']}>({target['id']})"
        )

    agent_class = str(agent["class_name"]).replace("_", " ")
    states = cast(list[object], agent["states"])
    if agent_class == "quadrotor":
        if "FLYING" in states:
            if landable is not None:
                actions.append(unary("land_on", landable))
                all_landable = [item for item in all_landable if item != landable]
            actions.extend(unary("movetowards", item) for item in all_landable)
            for room, door in next_rooms:
                if {"OPEN", "OPEN_FOREVER"}.intersection(cast(list[object], door["states"])):
                    actions.append(unary("movetowards", room))
        if "LAND" in states and on_surface is not None:
            actions.append(unary("takeoff_from", on_surface))
    elif agent_class == "robot dog":
        for item in reachable:
            properties = cast(list[object], item["properties"])
            item_states = cast(list[object], item["states"])
            if held is None:
                if (
                    ("CONTAINERS" in properties or item.get("class_name") == "door")
                    and "CLOSED" in item_states
                ):
                    actions.append(unary("open", item))
                if (
                    ("CONTAINERS" in properties or item.get("class_name") == "door")
                    and "OPEN" in item_states
                ):
                    actions.append(unary("close", item))
                if "GRABABLE" in properties:
                    actions.append(unary("grab", item))
            else:
                if "CONTAINERS" in properties and {
                    "OPEN",
                    "OPEN_FOREVER",
                }.intersection(item_states):
                    actions.append(binary("putinto", held, item))
                if "SURFACES" in properties:
                    actions.append(binary("puton", held, item))
        actions.extend(unary("movetowards", item) for item in unreached)
        for room, door in next_rooms:
            if {"OPEN", "OPEN_FOREVER"}.intersection(cast(list[object], door["states"])):
                actions.append(unary("movetowards", room))
    elif agent_class == "robot arm":
        if held is not None and on_surface is not None:
            actions.append(binary("puton", held, on_surface))
        for item in same_surface:
            properties = cast(list[object], item["properties"])
            item_states = cast(list[object], item["states"])
            if held is None:
                if "CONTAINERS" in properties and "OPEN" in item_states:
                    actions.append(unary("close", item))
                if "CONTAINERS" in properties and "CLOSED" in item_states:
                    actions.append(unary("open", item))
                if "GRABABLE" in properties:
                    actions.append(unary("grab", item))
            else:
                if "CONTAINERS" in properties and {
                    "OPEN",
                    "OPEN_FOREVER",
                }.intersection(item_states):
                    actions.append(binary("putinto", held, item))
                if "SURFACES" in properties:
                    actions.append(binary("puton", held, item))
    return list(dict.fromkeys(actions))


def apply_original_step(
    pefa_root: Path,
    task_data: dict[str, object],
    graph: dict[str, object],
    step_count: int,
    agent_class: str,
    agent_id: int,
    action: str,
    task_index: int,
) -> tuple[dict[str, object], dict[str, object]]:
    """Apply exactly one action using COHERENT's original transition implementation."""
    environment = make_environment(pefa_root, task_data, graph, task_index)
    environment.steps = step_count
    done, results, satisfied, unsatisfied, steps = environment.step(
        agent_class, agent_id, action, task_data["task_goal"]
    )
    if int(steps) != step_count + 1:
        raise IntegrationError("original COHERENT environment returned an unexpected step count")
    transition = {
        "step": int(steps),
        "agent_class": agent_class,
        "agent_id": agent_id,
        "action": action,
        "done": bool(done),
        "task_results": results,
        "satisfied": satisfied,
        "unsatisfied": sorted(str(key) for key in cast(dict[object, object], unsatisfied)),
    }
    return _object(environment.graph, "graph"), transition


def goal_status(task_data: dict[str, object], graph: dict[str, object]) -> dict[str, object]:
    """Evaluate the official relation-count goal directly against the current graph."""
    goals = _object(task_data["task_goal"], "task_goal")
    unsatisfied: dict[str, object] = {}
    satisfied: list[str] = []
    edges = _edges(graph)
    for predicate, raw_value in goals.items():
        if not isinstance(raw_value, list) or not raw_value:
            raise IntegrationError(f"invalid official goal value for {predicate}")
        remaining = int(cast(int, raw_value[0]))
        parts = predicate.split("_")
        target_match = re.findall(r"\((.*?)\)", parts[-1])
        source_match = re.findall(r"\((.*?)\)", parts[1])
        if not target_match or not source_match:
            raise IntegrationError(f"unsupported official goal predicate {predicate}")
        target_id = int(target_match[0])
        source_id = int(source_match[0])
        for edge in edges:
            if (
                remaining > 0
                and str(edge.get("relation_type", "")).lower() == parts[0]
                and edge.get("to_id") == target_id
                and edge.get("from_id") == source_id
            ):
                remaining -= 1
                satisfied.append(predicate)
        if remaining > 0:
            unsatisfied[predicate] = raw_value
    return {"passed": not unsatisfied, "satisfied": satisfied, "unsatisfied": unsatisfied}


def read_graph_database(database: Path) -> tuple[dict[str, object], int]:
    """Read current task state from a bridge database after a Controller mission."""
    connection = sqlite3.connect(str(database), timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM graph_state WHERE singleton=1").fetchone()
    finally:
        connection.close()
    if row is None:
        raise IntegrationError("generic graph state is missing")
    return _object(json.loads(str(row["graph_json"])), "graph"), int(row["step_count"])


def _nodes(graph: dict[str, object]) -> list[dict[str, object]]:
    """Return validated graph nodes."""
    value = graph.get("nodes")
    if not isinstance(value, list):
        raise IntegrationError("graph.nodes must be an array")
    return [_object(item, "graph.nodes[]") for item in value]


def _edges(graph: dict[str, object]) -> list[dict[str, object]]:
    """Return validated graph edges."""
    value = graph.get("edges")
    if not isinstance(value, list):
        raise IntegrationError("graph.edges must be an array")
    return [_object(item, "graph.edges[]") for item in value]


def _execution_id(request: object) -> str:
    """Extract the sole execution handle accepted by status and cancel."""
    body = _object(request, "request")
    if set(body) != {"execution_id"}:
        raise IntegrationError("request must contain only execution_id")
    return _string(body["execution_id"], "execution_id")


def _object(value: object, field: str) -> dict[str, object]:
    """Validate and detach a JSON object with string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise IntegrationError(f"{field} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _string(value: object, field: str) -> str:
    """Validate one non-empty string without changing its identity."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    """Validate one integer while rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrationError(f"{field} must be an integer")
    return value


def _now_ms() -> int:
    """Return current Unix wall time in integer milliseconds."""
    return time.time_ns() // 1_000_000
