"""Deployment policy for multi-action COHERENT plans; no Core lifecycle ownership."""

from __future__ import annotations

import json
import re
from typing import Any


def objective_text(
    env_name: str,
    task_index: int,
    task_data: dict[str, Any],
    contexts: dict[int, dict[str, Any]],
    feedback: list[dict[str, Any]],
    remaining_steps: int,
) -> str:
    """Ask the existing Planner for a complete plan or an observation-bounded segment."""
    observations = []
    for agent_id, context in sorted(contexts.items()):
        observation = context["observation"]
        observations.append(
            {
                "agent_id": agent_id,
                "agent_class": context["agent_class"],
                "operation": f"coherent.agent-{agent_id}-primitive@v1",
                "nodes": [
                    {
                        k: n[k]
                        for k in ("id", "class_name", "category", "properties", "states")
                        if k in n
                    }
                    for n in observation["nodes"]
                ],
                "edges": observation["edges"],
                "available_actions_now": context["available_actions"],
            }
        )
    return f"""Plan the COHERENT graph task {env_name}/task{task_index}.
Task instruction: {task_data["goal_instruction"][0]}
Goal predicates: {json.dumps(task_data["task_goal"], ensure_ascii=False)}
Remaining primitive execution budget: {remaining_steps}.

Generate ONE complete multi-task MissionPlan using the current catalog. Each Task has exactly
one Role and exactly one primitive action. Use one Context; reuse one logical Actor/ContextRole
per robot agent. Use a sequential dependency chain: the first Task has no dependency, and every
subsequent Task depends on its predecessor. The Controller executes the entire chain without
calling the Planner between actions. This serial ordering preserves the benchmark step semantics.
For every Role require the exact capability matching its operation, one space resource unit,
resource_scope=task, and satisfaction basis=execution-report. Do not set physical_entity.
Parameters are exactly env={env_name!r}, task={task_index}, agent_id (integer), agent_class
(exact graph class), action (canonical COHERENT string).

The first primitive must be available now. Later primitives may become available after earlier
Tasks execute; reason about their preconditions and dependencies. The adapter validates each action
against the then-current official available list immediately before execution. Do not add a correct
plan to parameters or use macros. Action grammar: [verb] <class name>(id); placement grammar is
[putinto] <object>(id) into <container>(id) or [puton] <object>(id) on <surface>(id).
Quadrotor verbs: takeoff_from, movetowards, land_on. Dog: movetowards, open, close, grab, putinto,
puton. Arm: open, close, grab, putinto, puton. A quadrotor transports its attached basket; an arm
manipulates objects reachable on its surface; a dog navigates and manipulates low reachable objects.
Graph action semantics (not a task-specific route): takeoff_from changes ON to ABOVE. While flying,
land_on is available only for the current ABOVE surface; movetowards a different landable surface
changes ABOVE to that surface. Moving into an adjacent room through an open door sets INSIDE to
that room and ABOVE to its floor, so landing on that floor needs no separate floor-approach action.
The current ABOVE surface is excluded from movetowards candidates. Landing changes ABOVE to ON.
Dog manipulation requires CLOSE, free hands for open/close/grab, and a held object for placement;
high surfaces are not reachable by the dog. Placement in containers requires OPEN or OPEN_FOREVER.
Use observed identities. Do not invent locations for objects hidden in closed containers. If the
current observations are insufficient for the full goal, produce a useful exploration segment,
ending immediately after the revealing action; the runner will supply fresh observations afterward.
Do not return a one-step plan when several necessary actions are grounded by the current evidence.

Official partial observations, shared between agents:
{json.dumps(observations, ensure_ascii=False, separators=(",", ":"))}

Previous segments and actual execution feedback:
{json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))}
"""


def validate_plan(
    plan: dict[str, Any],
    mission_id: str,
    env_name: str,
    task_index: int,
    contexts: dict[int, dict[str, Any]],
    remaining_steps: int,
) -> list[dict[str, Any]]:
    """Validate a serial DAG without simulating future states or rewriting the plan.

    Raises:
        ValueError: If dependencies, identities, budget, or primitive syntax are invalid.
    """
    if plan["mission"]["id"] != mission_id:
        raise ValueError("mission identity mismatch")
    tasks = plan["tasks"]
    if not 1 <= len(tasks) <= remaining_steps:
        raise ValueError("task count exceeds remaining primitive budget or is empty")
    if len(plan["contexts"]) != 1:
        raise ValueError("serial graph profile requires exactly one Context")
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids")
    todo = {t["id"]: t for t in tasks}
    ordered: list[dict[str, Any]] = []
    preceding: str | None = None
    while todo:
        expected = [] if preceding is None else [preceding]
        ready = [t for t in todo.values() if t["depends_on"] == expected]
        if len(ready) != 1:
            raise ValueError("plan must be one dependency chain without cycles or branches")
        task = ready[0]
        preceding = task["id"]
        del todo[preceding]
        if len(task["roles"]) != 1:
            raise ValueError("each Task must have exactly one primitive Role")
        role = task["roles"][0]
        if role["resource_scope"] != "task":
            raise ValueError("primitive resource scope must be task")
        intent = role["execution_intent"]
        params = intent["parameters"]
        if set(params) != {"env", "task", "agent_id", "agent_class", "action"}:
            raise ValueError("incorrect primitive parameter set")
        agent_id = params["agent_id"]
        if type(agent_id) is not int or agent_id not in contexts:
            raise ValueError("unknown agent identity")
        context = contexts[agent_id]
        if params["env"] != env_name or type(params["task"]) is not int:
            raise ValueError("invalid environment or task identity")
        if params["task"] != task_index or params["agent_class"] != context["agent_class"]:
            raise ValueError("task or agent class mismatch")
        contract = {"namespace": "coherent", "name": f"agent-{agent_id}-primitive", "version": "v1"}
        if intent["operation"] != contract:
            raise ValueError("operation does not route to the selected agent")
        capabilities = role["requirements"]["capabilities"]
        if len(capabilities) != 1 or capabilities[0]["contract"] != contract:
            raise ValueError("capability does not match selected primitive operation")
        if role["requirements"]["resources"] != [{"kind": "space", "units": 1}]:
            raise ValueError("primitive Role requires exactly one space unit")
        if task["satisfaction"]["basis"] != "execution-report":
            raise ValueError("primitive satisfaction basis must be execution-report")
        action = params["action"]
        if not isinstance(action, str) or not re.fullmatch(
            r"\[(?:open|close|grab|movetowards|land_on|takeoff_from)\] <[^<>]+>\(\d+\)"
            r"|\[putinto\] <[^<>]+>\(\d+\) into <[^<>]+>\(\d+\)"
            r"|\[puton\] <[^<>]+>\(\d+\) on <[^<>]+>\(\d+\)",
            action,
        ):
            raise ValueError("invalid primitive syntax")
        if not ordered and action not in context["available_actions"]:
            raise ValueError("first primitive is not currently available")
        ordered.append({"task_id": task["id"], **params})
    return ordered
