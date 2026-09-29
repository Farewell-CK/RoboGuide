"""Durable controlled bridge for the COHERENT public graph benchmark."""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .bridge import TERMINAL_STATES, IntegrationError

MISSION_ID = "mission-e2-s1-coherent-env4-task17"
SUPPORTED_ENV = "env4"
SUPPORTED_TASK = 17


@dataclass(frozen=True)
class GraphAction:
    """One fixed public-benchmark action with explicit agent and object identities."""

    agent_class: str
    agent_id: int
    verb: str
    primary_id: int
    secondary_id: int | None
    text: str


@dataclass(frozen=True)
class PhaseSpec:
    """One RoboGuide task mapped to an ordered COHERENT graph-action segment."""

    name: str
    task_id: str
    role_id: str
    operation: str
    actions: tuple[GraphAction, ...]
    cumulative_steps: int


PHASES = (
    PhaseSpec(
        name="arm-load-milk",
        task_id="arm-load-milk",
        role_id="arm-phase-role",
        operation="coherent.arm-phase@v1",
        actions=(
            GraphAction("robot arm", 23, "grab", 35, None, "[grab] <bottle of milk>(35)"),
            GraphAction(
                "robot arm",
                23,
                "putinto",
                35,
                29,
                "[putinto] <bottle of milk>(35) into <basket>(29)",
            ),
        ),
        cumulative_steps=2,
    ),
    PhaseSpec(
        name="drone-lower-basket",
        task_id="drone-lower-basket",
        role_id="drone-lower-role",
        operation="coherent.drone-phase@v1",
        actions=(
            GraphAction(
                "quadrotor", 25, "takeoff_from", 30, None, "[takeoff_from] <dining table>(30)"
            ),
            GraphAction(
                "quadrotor", 25, "movetowards", 10, None, "[movetowards] <coffee table>(10)"
            ),
            GraphAction("quadrotor", 25, "land_on", 10, None, "[land_on] <coffee table>(10)"),
        ),
        cumulative_steps=5,
    ),
    PhaseSpec(
        name="dog-load-book",
        task_id="dog-load-book",
        role_id="dog-phase-role",
        operation="coherent.dog-phase@v1",
        actions=(
            GraphAction("robot dog", 24, "movetowards", 34, None, "[movetowards] <book>(34)"),
            GraphAction("robot dog", 24, "grab", 34, None, "[grab] <book>(34)"),
            GraphAction("robot dog", 24, "movetowards", 29, None, "[movetowards] <basket>(29)"),
            GraphAction(
                "robot dog",
                24,
                "putinto",
                34,
                29,
                "[putinto] <book>(34) into <basket>(29)",
            ),
        ),
        cumulative_steps=9,
    ),
    PhaseSpec(
        name="drone-deliver",
        task_id="drone-deliver",
        role_id="drone-deliver-role",
        operation="coherent.drone-phase@v1",
        actions=(
            GraphAction(
                "quadrotor", 25, "takeoff_from", 10, None, "[takeoff_from] <coffee table>(10)"
            ),
            GraphAction("quadrotor", 25, "movetowards", 5, None, "[movetowards] <garden>(5)"),
            GraphAction(
                "quadrotor", 25, "movetowards", 16, None, "[movetowards] <swing table>(16)"
            ),
            GraphAction("quadrotor", 25, "land_on", 16, None, "[land_on] <swing table>(16)"),
        ),
        cumulative_steps=13,
    ),
)
PHASE_BY_NAME = {phase.name: phase for phase in PHASES}


@dataclass(frozen=True)
class GraphInvocation:
    """Validated canonical invocation for one closed-scope graph phase."""

    mission_id: str
    task_id: str
    group_id: str
    role_id: str
    operation: str
    objective: str
    parameters: dict[str, object]
    resource_ids: tuple[str, ...]

    @classmethod
    def from_request(cls, request: object) -> GraphInvocation:
        """Validate exact identity, phase, operation, and parameter boundaries."""
        body = _object(request, "request")
        if set(body) != {"invocation"}:
            raise IntegrationError("execute request must contain only invocation")
        invocation = _object(body["invocation"], "invocation")
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
        if set(invocation) != expected:
            raise IntegrationError("canonical invocation fields do not match the E2-S1 contract")
        mission_id = _string(invocation["mission_id"], "mission_id")
        if mission_id != MISSION_ID:
            raise IntegrationError(f"unsupported Mission {mission_id!r}")
        parameters = _object(invocation["parameters"], "parameters")
        if set(parameters) != {"env", "task", "phase"}:
            raise IntegrationError("graph phase requires exactly env, task, and phase")
        if parameters["env"] != SUPPORTED_ENV or parameters["task"] != SUPPORTED_TASK:
            raise IntegrationError("only public COHERENT env4/task17 is enabled")
        phase_name = _string(parameters["phase"], "parameters.phase")
        phase = PHASE_BY_NAME.get(phase_name)
        if phase is None:
            raise IntegrationError(f"unsupported graph phase {phase_name!r}")
        task_id = _string(invocation["task_id"], "task_id")
        role_id = _string(invocation["role_id"], "role_id")
        operation = _string(invocation["operation"], "operation")
        # The controlled E2-S1 fixture uses the frozen task/role identifiers above.
        # E2-Full, however, must execute the identifiers emitted by Mission
        # Intelligence without rewriting its plan after generation.  The canonical
        # operation and phase remain deployment-owned and fail closed; task and role
        # identities are opaque MissionPlan identities.
        if operation != phase.operation:
            raise IntegrationError("operation does not match the requested COHERENT phase")
        raw_resources = invocation["resource_ids"]
        if not isinstance(raw_resources, list):
            raise IntegrationError("resource_ids must be an array")
        resources = tuple(_string(item, "resource_ids") for item in raw_resources)
        if len(resources) != len(set(resources)):
            raise IntegrationError("resource_ids contains duplicates")
        return cls(
            mission_id=mission_id,
            task_id=task_id,
            group_id=_string(invocation["group_id"], "group_id"),
            role_id=role_id,
            operation=operation,
            objective=_string(invocation["objective"], "objective"),
            parameters={"env": SUPPORTED_ENV, "task": SUPPORTED_TASK, "phase": phase_name},
            resource_ids=resources,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the stable canonical JSON shape retained as execution evidence."""
        return {
            "group_id": self.group_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "operation": self.operation,
            "parameters": dict(self.parameters),
            "resource_ids": list(self.resource_ids),
            "role_id": self.role_id,
            "task_id": self.task_id,
        }

    def request_key(self) -> str:
        """Hash the exact invocation for idempotent Node workflow retries."""
        payload = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class GraphExecutionStore:
    """Persist graph state and per-phase local handles in one SQLite transaction domain."""

    def __init__(self, database: Path, initial_graph: dict[str, object]) -> None:
        """Create durable tables and initialize the public task graph exactly once."""
        database.parent.mkdir(parents=True, exist_ok=True)
        self._database = database
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    invocation_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    local_outcome_json TEXT,
                    created_at_unix_ms INTEGER NOT NULL,
                    updated_at_unix_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    graph_json TEXT NOT NULL,
                    step_count INTEGER NOT NULL,
                    next_phase INTEGER NOT NULL,
                    updated_at_unix_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO graph_state VALUES (1, ?, 0, 0, ?)",
                (json.dumps(initial_graph, sort_keys=True), _now_ms()),
            )
            connection.execute(
                """
                UPDATE executions
                   SET state = 'FAILED', detail = 'graph bridge restarted during execution',
                       updated_at_unix_ms = ?
                 WHERE state IN ('ACCEPTED', 'RUNNING')
                """,
                (_now_ms(),),
            )

    def create_or_get(self, invocation: GraphInvocation) -> tuple[dict[str, object], bool]:
        """Create an accepted handle or return the prior idempotent execution."""
        key = invocation.request_key()
        encoded = json.dumps(invocation.as_dict(), sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE request_key = ?", (key,)
            ).fetchone()
            if row is not None:
                return self._decode(row), False
            now = _now_ms()
            execution_id = "coherent-graph-" + key[:20]
            connection.execute(
                """
                INSERT INTO executions(
                    execution_id, request_key, invocation_json, state, detail,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, 'ACCEPTED', 'accepted by COHERENT graph bridge', ?, ?)
                """,
                (execution_id, key, encoded, now, now),
            )
            created = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if created is None:
                raise IntegrationError("graph execution disappeared after creation")
            return self._decode(created), True

    def get(self, execution_id: str) -> dict[str, object] | None:
        """Read one persisted graph-phase execution."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode(row)

    def graph_state(self) -> tuple[dict[str, object], int, int]:
        """Return a detached graph plus its cumulative step and phase counters."""
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM graph_state WHERE singleton = 1").fetchone()
            if row is None:
                raise IntegrationError("durable graph state is missing")
            graph = _object(json.loads(str(row["graph_json"])), "graph")
            return graph, int(row["step_count"]), int(row["next_phase"])

    def mark_running(self, execution_id: str) -> None:
        """Transition one accepted phase to running before mutating graph state."""
        self._transition(execution_id, "ACCEPTED", "RUNNING", "graph phase started", None)

    def complete(
        self,
        execution_id: str,
        expected_phase: int,
        graph: dict[str, object],
        step_count: int,
        outcome: dict[str, object],
    ) -> None:
        """Atomically commit the phase graph and terminal local execution fact."""
        with self._lock, self._connect() as connection:
            state = connection.execute(
                "SELECT next_phase FROM graph_state WHERE singleton = 1"
            ).fetchone()
            execution = connection.execute(
                "SELECT state FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if state is None or int(state["next_phase"]) != expected_phase:
                raise IntegrationError("graph phase changed before atomic commit")
            if execution is None or str(execution["state"]) != "RUNNING":
                raise IntegrationError("execution is not running at graph commit")
            now = _now_ms()
            connection.execute(
                """
                UPDATE graph_state
                   SET graph_json = ?, step_count = ?, next_phase = ?, updated_at_unix_ms = ?
                 WHERE singleton = 1
                """,
                (json.dumps(graph, sort_keys=True), step_count, expected_phase + 1, now),
            )
            connection.execute(
                """
                UPDATE executions
                   SET state = 'COMPLETED', detail = ?, local_outcome_json = ?,
                       updated_at_unix_ms = ?
                 WHERE execution_id = ?
                """,
                (
                    "COHERENT graph phase and expected effect completed",
                    json.dumps(outcome, sort_keys=True),
                    now,
                    execution_id,
                ),
            )

    def fail(self, execution_id: str, detail: str, outcome: dict[str, object]) -> None:
        """Persist an explicit terminal failure without advancing shared graph state."""
        execution = self.get(execution_id)
        if execution is None or execution["state"] in TERMINAL_STATES:
            return
        expected = cast(str, execution["state"])
        self._transition(execution_id, expected, "FAILED", detail, outcome)

    def _transition(
        self,
        execution_id: str,
        expected: str,
        state: str,
        detail: str,
        outcome: dict[str, object] | None,
    ) -> None:
        """Apply one guarded execution-only state transition."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None or str(row["state"]) != expected:
                raise IntegrationError("invalid graph execution transition")
            connection.execute(
                """
                UPDATE executions
                   SET state = ?, detail = ?, local_outcome_json = ?, updated_at_unix_ms = ?
                 WHERE execution_id = ?
                """,
                (
                    state,
                    detail[:2000],
                    None if outcome is None else json.dumps(outcome, sort_keys=True),
                    _now_ms(),
                    execution_id,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        """Open one named-row SQLite connection for a bounded transaction."""
        connection = sqlite3.connect(str(self._database), timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, object]:
        """Decode one execution row and revalidate its canonical invocation."""
        invocation = GraphInvocation.from_request(
            {"invocation": json.loads(str(row["invocation_json"]))}
        )
        raw_outcome = row["local_outcome_json"]
        return {
            "execution_id": str(row["execution_id"]),
            "request_key": str(row["request_key"]),
            "invocation": invocation,
            "state": str(row["state"]),
            "detail": str(row["detail"]),
            "local_outcome": None if raw_outcome is None else json.loads(str(raw_outcome)),
            "updated_at_unix_ms": int(row["updated_at_unix_ms"]),
        }


@dataclass(frozen=True)
class GraphBackendConfig:
    """Startup-fixed locations and immutable public task metadata."""

    pefa_root: Path
    evidence_dir: Path
    task_data: dict[str, object]


class CoherentGraphAdapter:
    """Execute four ordered fixed-plan phases through original COHERENT graph transitions."""

    def __init__(self, store: GraphExecutionStore, config: GraphBackendConfig) -> None:
        """Retain the shared store and immutable task configuration."""
        self._store = store
        self._config = config
        self._lock = threading.RLock()

    def close(self) -> None:
        """Close the synchronous adapter; no background worker is retained."""

    def health(self) -> dict[str, object]:
        """Report process-local graph adapter health without mutating state."""
        return {"state": "ONLINE", "detail": "COHERENT graph adapter is online"}

    def readiness(self) -> dict[str, object]:
        """Report the exact three operation identities exposed to Node workflows."""
        return {
            "state": "READY",
            "detail": "fixed env4/task17 phase operations are available",
            "operations": sorted({phase.operation for phase in PHASES}),
        }

    def accept(self, request: object) -> dict[str, object]:
        """Execute a newly accepted phase exactly once and return its durable fact."""
        invocation = GraphInvocation.from_request(request)
        with self._lock:
            execution, created = self._store.create_or_get(invocation)
            if created:
                self._execute(cast(str, execution["execution_id"]), invocation)
                updated = self._store.get(cast(str, execution["execution_id"]))
                if updated is None:
                    raise IntegrationError("graph execution disappeared after execution")
                execution = updated
            return self._response(execution)

    def dispatch(self, execution_id: str) -> None:
        """Preserve the shared server contract after synchronous exactly-once execution."""
        if self._store.get(execution_id) is None:
            raise IntegrationError(f"unknown graph execution {execution_id!r}")

    def status(self, request: object) -> dict[str, object]:
        """Return one durable phase state for Node workflow polling."""
        execution_id = _execution_id(request)
        execution = self._store.get(execution_id)
        if execution is None:
            raise IntegrationError(f"unknown graph execution {execution_id!r}")
        return self._response(execution)

    def cancel(self, request: object) -> dict[str, object]:
        """Reject cancellation explicitly because graph phases commit synchronously."""
        response = self.status(request)
        response["accepted"] = False
        response["detail"] = "cancellation is unsupported for committed E2-S1 graph phases"
        return response

    def _execute(self, execution_id: str, invocation: GraphInvocation) -> None:
        """Validate phase order, run original transitions, and persist inspectable evidence."""
        phase_name = cast(str, invocation.parameters["phase"])
        phase = PHASE_BY_NAME[phase_name]
        try:
            self._store.mark_running(execution_id)
            graph, step_count, next_phase = self._store.graph_state()
            if next_phase >= len(PHASES) or PHASES[next_phase].name != phase_name:
                expected = "complete" if next_phase >= len(PHASES) else PHASES[next_phase].name
                raise IntegrationError(
                    f"phase order violation: expected {expected}, received {phase_name}"
                )
            steps_before = step_count
            records: list[dict[str, object]] = []
            final_done = False
            final_unsatisfied: list[str] = []
            for action in phase.actions:
                _validate_action(graph, action)
                graph, transition = _apply_original_step(
                    self._config.pefa_root,
                    self._config.task_data,
                    graph,
                    step_count,
                    action,
                )
                step_count += 1
                final_done = cast(bool, transition["done"])
                final_unsatisfied = cast(list[str], transition["unsatisfied"])
                records.append(transition)
            if step_count != phase.cumulative_steps:
                raise IntegrationError("phase did not end at its frozen cumulative GT step")
            _validate_phase_effect(phase_name, graph, final_done)
            outcome: dict[str, object] = {
                "env": SUPPORTED_ENV,
                "task": SUPPORTED_TASK,
                "phase": phase_name,
                "phase_index": next_phase,
                "steps_before": steps_before,
                "steps_after": step_count,
                "actions": records,
                "goal_satisfied": final_done,
                "unsatisfied": final_unsatisfied,
            }
            self._config.evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_path = self._config.evidence_dir / f"{next_phase + 1:02d}-{phase_name}.json"
            evidence_path.write_text(
                json.dumps(outcome, indent=2, sort_keys=True), encoding="utf-8"
            )
            outcome["evidence_file"] = str(evidence_path)
            self._store.complete(execution_id, next_phase, graph, step_count, outcome)
        except Exception as error:
            self._store.fail(
                execution_id,
                f"COHERENT graph phase failed: {error}",
                {"phase": phase_name, "error": str(error)},
            )

    @staticmethod
    def _response(execution: dict[str, object]) -> dict[str, object]:
        """Project one durable graph execution into the Node workflow response schema."""
        invocation = execution["invocation"]
        if not isinstance(invocation, GraphInvocation):
            raise IntegrationError("stored graph invocation has an invalid type")
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


def load_task_data(pefa_root: Path) -> dict[str, object]:
    """Load and validate the immutable public env4/task17 dataset entry."""
    path = pefa_root / "env" / "env4.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrationError(f"cannot read public task dataset: {error}") from error
    if not isinstance(document, list) or len(document) <= SUPPORTED_TASK:
        raise IntegrationError("env4 dataset does not contain task17")
    task = _object(document[SUPPORTED_TASK], "env4.task17")
    required = {
        "env_id",
        "task_name",
        "init_graph",
        "task_goal",
        "goal_instruction",
        "ground_truth_step_num",
    }
    if not required.issubset(task):
        raise IntegrationError("env4/task17 is missing required benchmark fields")
    return task


def initial_graph(task_data: dict[str, object]) -> dict[str, object]:
    """Return a detached validated initial graph for a fresh evidence database."""
    return _object(
        json.loads(json.dumps(_object(task_data["init_graph"], "init_graph"))),
        "init_graph",
    )


def _apply_original_step(
    pefa_root: Path,
    task_data: dict[str, object],
    graph: dict[str, object],
    step_count: int,
    action: GraphAction,
) -> tuple[dict[str, object], dict[str, object]]:
    """Invoke COHERENT's original Get_env_info.step and normalize its goal evidence."""
    root = str(pefa_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    module = importlib.import_module("get_env_info")
    environment_class: Any = module.Get_env_info
    agents = [
        [node["class_name"], node["id"]]
        for node in _nodes(graph)
        if node.get("category") == "Agents"
    ]
    environment: Any = environment_class(
        task_id=SUPPORTED_TASK,
        env_id=task_data["env_id"],
        task_name=task_data["task_name"],
        graph=graph,
        task_goal=task_data["task_goal"],
        goal_instruction=task_data["goal_instruction"],
        ground_truth_step_num=task_data["ground_truth_step_num"],
        agent=agents,
        num_agent=len(agents),
    )
    environment.steps = step_count
    done, results, satisfied, unsatisfied, steps = environment.step(
        action.agent_class,
        action.agent_id,
        action.text,
        task_data["task_goal"],
    )
    if int(steps) != step_count + 1:
        raise IntegrationError("original COHERENT environment returned an unexpected step count")
    transition = {
        "step": int(steps),
        "agent_class": action.agent_class,
        "agent_id": action.agent_id,
        "action": action.text,
        "done": bool(done),
        "task_results": results,
        "satisfied": satisfied,
        "unsatisfied": sorted(str(key) for key in cast(dict[object, object], unsatisfied)),
    }
    return _object(environment.graph, "graph"), transition


def _validate_action(graph: dict[str, object], action: GraphAction) -> None:
    """Fail closed unless the fixed action is legal in the current public graph state."""
    agent = _node(graph, action.agent_id)
    primary = _node(graph, action.primary_id)
    if agent.get("class_name") != action.agent_class:
        raise IntegrationError("action agent class does not match graph identity")
    if action.verb == "grab":
        if (
            "GRABABLE" not in _properties(primary)
            or _held_object(graph, action.agent_id) is not None
        ):
            raise IntegrationError("grab preconditions are not satisfied")
        if action.agent_class == "robot arm":
            if _on_surface(graph, action.agent_id) != _on_surface(graph, action.primary_id):
                raise IntegrationError("robot arm and object do not share a surface")
        elif not _has_edge(graph, action.agent_id, action.primary_id, "CLOSE"):
            raise IntegrationError("robot dog is not close to the object")
    elif action.verb == "putinto":
        if action.secondary_id is None:
            raise IntegrationError("putinto requires a container")
        container = _node(graph, action.secondary_id)
        if not _has_edge(graph, action.agent_id, action.primary_id, "HOLD"):
            raise IntegrationError("agent is not holding the object")
        if "CONTAINERS" not in _properties(container) or not {
            "OPEN",
            "OPEN_FOREVER",
        }.intersection(_states(container)):
            raise IntegrationError("target container is not open")
        if action.agent_class == "robot arm":
            if _on_surface(graph, action.agent_id) != _on_surface(graph, action.secondary_id):
                raise IntegrationError("robot arm and container do not share a surface")
        elif not _has_edge(graph, action.agent_id, action.secondary_id, "CLOSE"):
            raise IntegrationError("robot dog is not close to the container")
    elif action.verb == "takeoff_from":
        if "LAND" not in _states(agent) or not _has_edge(
            graph, action.agent_id, action.primary_id, "ON"
        ):
            raise IntegrationError("quadrotor is not landed on the takeoff surface")
    elif action.verb == "land_on":
        if "FLYING" not in _states(agent) or not _has_edge(
            graph, action.agent_id, action.primary_id, "ABOVE"
        ):
            raise IntegrationError("quadrotor is not flying above the landing surface")
    elif action.verb == "movetowards" and action.agent_class == "quadrotor":
        if "FLYING" not in _states(agent):
            raise IntegrationError("quadrotor movement requires flying state")
        if primary.get("category") == "Rooms":
            current_room = _room_of(graph, action.agent_id)
            target_room = action.primary_id
            if not _rooms_connected(graph, current_room, target_room):
                raise IntegrationError("target room is not connected by an open door")
        elif _room_of(graph, action.agent_id) != _room_of(graph, action.primary_id):
            raise IntegrationError("quadrotor surface target is outside the current room")
    elif action.verb == "movetowards" and action.agent_class == "robot dog":
        if "HIGH_HEIGHT" in _properties(primary) or "ON_HIGH_SURFACE" in _properties(primary):
            raise IntegrationError("robot dog cannot reach the high target")
        if _room_of(graph, action.agent_id) != _room_of(graph, action.primary_id):
            raise IntegrationError("robot dog target is outside the current room")
    else:
        raise IntegrationError(f"unsupported fixed graph action {action.verb!r}")


def _validate_phase_effect(phase: str, graph: dict[str, object], final_done: bool) -> None:
    """Separate per-phase execution effects from the final benchmark goal fact."""
    milk_loaded = _has_edge(graph, 35, 29, "INSIDE")
    book_loaded = _has_edge(graph, 34, 29, "INSIDE")
    if phase == "arm-load-milk" and not milk_loaded:
        raise IntegrationError("arm phase did not load milk into the basket")
    if phase == "drone-lower-basket" and (
        not _has_edge(graph, 25, 10, "ON") or "ON_HIGH_SURFACE" in _properties(_node(graph, 29))
    ):
        raise IntegrationError("drone phase did not lower the basket onto the coffee table")
    if phase == "dog-load-book" and not (milk_loaded and book_loaded):
        raise IntegrationError("dog phase did not establish both basket contents")
    if phase == "drone-deliver" and not (
        final_done and milk_loaded and book_loaded and _has_edge(graph, 25, 16, "ON")
    ):
        raise IntegrationError("final phase did not establish the full public task goal")


def _nodes(graph: dict[str, object]) -> list[dict[str, object]]:
    """Return validated graph nodes."""
    raw = graph.get("nodes")
    if not isinstance(raw, list):
        raise IntegrationError("graph nodes must be an array")
    return [_object(node, "node") for node in raw]


def _edges(graph: dict[str, object]) -> list[dict[str, object]]:
    """Return validated graph edges."""
    raw = graph.get("edges")
    if not isinstance(raw, list):
        raise IntegrationError("graph edges must be an array")
    return [_object(edge, "edge") for edge in raw]


def _node(graph: dict[str, object], node_id: int) -> dict[str, object]:
    """Resolve one required node identity."""
    for node in _nodes(graph):
        if node.get("id") == node_id:
            return node
    raise IntegrationError(f"graph node {node_id} is missing")


def _properties(node: dict[str, object]) -> set[str]:
    """Return normalized node properties."""
    raw = node.get("properties")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise IntegrationError("node properties must be a string array")
    return set(raw)


def _states(node: dict[str, object]) -> set[str]:
    """Return normalized node states."""
    raw = node.get("states")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise IntegrationError("node states must be a string array")
    return set(raw)


def _has_edge(graph: dict[str, object], source: int, target: int, relation: str) -> bool:
    """Test one exact directed graph relation."""
    return any(
        edge.get("from_id") == source
        and edge.get("to_id") == target
        and edge.get("relation_type") == relation
        for edge in _edges(graph)
    )


def _on_surface(graph: dict[str, object], node_id: int) -> int | None:
    """Return the direct surface identity beneath a node, if present."""
    for edge in _edges(graph):
        if edge.get("from_id") == node_id and edge.get("relation_type") == "ON":
            target = edge.get("to_id")
            return target if isinstance(target, int) else None
    return None


def _held_object(graph: dict[str, object], agent_id: int) -> int | None:
    """Return the object currently held by one agent, if any."""
    for edge in _edges(graph):
        if edge.get("from_id") == agent_id and edge.get("relation_type") == "HOLD":
            target = edge.get("to_id")
            return target if isinstance(target, int) else None
    return None


def _room_of(graph: dict[str, object], node_id: int) -> int:
    """Resolve a node's containing room through ON or INSIDE ancestry."""
    pending = [node_id]
    visited: set[int] = set()
    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        node = _node(graph, current)
        if node.get("category") == "Rooms":
            return current
        for edge in _edges(graph):
            if edge.get("from_id") == current and edge.get("relation_type") in {"ON", "INSIDE"}:
                target = edge.get("to_id")
                if isinstance(target, int):
                    pending.append(target)
    raise IntegrationError(f"cannot resolve room for node {node_id}")


def _rooms_connected(graph: dict[str, object], first: int, second: int) -> bool:
    """Return whether one open door declares both room endpoints."""
    for node in _nodes(graph):
        if node.get("class_name") != "door" or not {"OPEN", "OPEN_FOREVER"}.intersection(
            _states(node)
        ):
            continue
        door_id = node.get("id")
        endpoints = {
            edge.get("to_id")
            for edge in _edges(graph)
            if edge.get("from_id") == door_id and edge.get("relation_type") == "LEADING TO"
        }
        if {first, second}.issubset(endpoints):
            return True
    return False


def _execution_id(request: object) -> str:
    """Extract the sole handle allowed in status and cancel requests."""
    body = _object(request, "request")
    if set(body) != {"execution_id"}:
        raise IntegrationError("request must contain only execution_id")
    return _string(body["execution_id"], "execution_id")


def _object(value: object, field: str) -> dict[str, object]:
    """Validate and copy one JSON object with string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise IntegrationError(f"{field} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _string(value: object, field: str) -> str:
    """Validate one required non-empty string without normalizing identity."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError(f"{field} must be a non-empty string")
    return value


def _now_ms() -> int:
    """Return current Unix wall-clock time in integer milliseconds."""
    return time.time_ns() // 1_000_000
