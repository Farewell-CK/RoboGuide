"""Deployment policy for multi-action COHERENT plans; no Core lifecycle ownership."""

from __future__ import annotations

import json
import re
from typing import Any

PROMPT_PROFILES = ("fair", "informed")
DAG_PROFILES = ("serial", "partial-order")


def _action_knowledge(prompt_profile: str) -> str:
    """Return task-independent action knowledge for one frozen prompt profile."""
    if prompt_profile not in PROMPT_PROFILES:
        raise ValueError(f"unsupported prompt profile {prompt_profile!r}")
    official_aligned = """Quadrotor verbs: takeoff_from, movetowards, land_on. Dog verbs:
movetowards, open, close, grab, putinto, puton. Arm verbs: open, close, grab, putinto,
puton. A quadrotor transports its attached basket, can cross rooms only through an open
door, and changes landing surface step by step using takeoff, movetowards, then land. An
arm manipulates objects reachable on its own surface. A dog navigates and manipulates low
reachable objects, must move close before manipulation, needs a free hand to open or close,
and cannot reach HIGH_HEIGHT or ON_HIGH_SURFACE objects. Placement in a container requires
that container to be open. These task-independent capability rules mirror the public PEFA
prompts; the current official action list remains the execution authority."""
    if prompt_profile == "fair":
        return official_aligned
    return (
        official_aligned
        + """
Informed development-only graph semantics: takeoff_from changes ON to ABOVE. While flying,
land_on is available only for the current ABOVE surface; movetowards a different landable
surface changes ABOVE to that surface. Moving into an adjacent room sets INSIDE to that room
and ABOVE to its floor, so landing needs no separate floor-approach action. The current ABOVE
surface is excluded from movetowards candidates. Landing changes ABOVE to ON."""
    )


def _dependency_instructions(dag_profile: str) -> str:
    """Describe the admitted dependency shape without assigning execution authority."""
    if dag_profile == "serial":
        return """Use a sequential dependency chain: the first Task has no dependency, and every
subsequent Task depends on its predecessor. This serial profile preserves the official benchmark's
one-shared-graph step semantics and does not claim parallel scheduling."""
    if dag_profile == "partial-order":
        return """Use a valid acyclic dependency graph. Add dependencies only for real data,
precondition, handoff, or resource ordering. Independent root or branch Tasks are allowed only
when each frontier action is executable from the same current graph and the Tasks use
independent robot resources. This profile is for controlled scheduler studies, not PEFA
step-count comparison."""
    raise ValueError(f"unsupported DAG profile {dag_profile!r}")


def objective_text(
    env_name: str,
    task_index: int,
    task_data: dict[str, Any],
    contexts: dict[int, dict[str, Any]],
    feedback: list[dict[str, Any]],
    remaining_steps: int,
    prompt_profile: str = "fair",
    dag_profile: str = "serial",
) -> str:
    """Ask the existing Planner for a complete plan or an observation-bounded segment."""
    action_knowledge = _action_knowledge(prompt_profile)
    dependencies = _dependency_instructions(dag_profile)
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
per robot agent. {dependencies}
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
puton. Arm: open, close, grab, putinto, puton.
{action_knowledge}
Use observed identities. Do not invent locations for objects hidden in closed containers. If the
current observations are insufficient for the full goal, produce a useful exploration segment,
ending immediately after the revealing action; the runner will supply fresh observations afterward.
Do not return a one-step plan when several necessary actions are grounded by the current
evidence. End the plan at the earliest action expected to establish every goal predicate. Never
append cleanup, inspection, repositioning, or redundant actions after expected goal achievement;
a runtime goal guard will request Controller cancellation if the official goal becomes true
before the Mission ends.

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
    dag_profile: str = "serial",
) -> list[dict[str, Any]]:
    """Validate one admitted DAG profile without simulating or rewriting future state.

    Raises:
        ValueError: If dependencies, identities, budget, or primitive syntax are invalid.
    """
    if plan["mission"]["id"] != mission_id:
        raise ValueError("mission identity mismatch")
    tasks = plan["tasks"]
    if not 1 <= len(tasks) <= remaining_steps:
        raise ValueError("task count exceeds remaining primitive budget or is empty")
    if dag_profile not in DAG_PROFILES:
        raise ValueError(f"unsupported DAG profile {dag_profile!r}")
    if len(plan["contexts"]) != 1:
        raise ValueError("graph experiment requires exactly one Context")
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids")
    task_by_id = {t["id"]: t for t in tasks}
    for task in tasks:
        dependencies = task["depends_on"]
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)):
            raise ValueError("depends_on must be an array of unique Task ids")
        if task["id"] in dependencies or any(item not in task_by_id for item in dependencies):
            raise ValueError("dependency refers to self or an unknown Task")
    todo = dict(task_by_id)
    ordered: list[dict[str, Any]] = []
    completed: set[str] = set()
    while todo:
        ready = [t for t in tasks if t["id"] in todo and set(t["depends_on"]) <= completed]
        if not ready:
            raise ValueError("plan dependency graph contains a cycle")
        if dag_profile == "serial" and len(ready) != 1:
            raise ValueError("serial profile requires one dependency chain without branches")
        frontier = ready if dag_profile == "partial-order" else ready[:1]
        frontier_agents: set[int] = set()
        for task in frontier:
            _validate_task(task, env_name, task_index, contexts)
            params = task["roles"][0]["execution_intent"]["parameters"]
            if dag_profile == "partial-order" and params["agent_id"] in frontier_agents:
                raise ValueError("parallel frontier reuses one robot resource")
            frontier_agents.add(params["agent_id"])
            if (
                not task["depends_on"]
                and params["action"] not in contexts[params["agent_id"]]["available_actions"]
            ):
                raise ValueError("root primitive is not currently available")
            del todo[task["id"]]
            completed.add(task["id"])
            ordered.append({"task_id": task["id"], "depends_on": task["depends_on"], **params})
    return ordered


def _validate_task(
    task: dict[str, Any],
    env_name: str,
    task_index: int,
    contexts: dict[int, dict[str, Any]],
) -> None:
    """Validate one primitive Task against identity, capability, and syntax contracts."""
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
