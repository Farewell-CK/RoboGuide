"""Private necessary-condition checks; passing them never substitutes for blind semantic review."""

from __future__ import annotations

from typing import cast

from mission.models import JSONObject, JSONValue, MissionPlan


def objects(value: JSONValue | object) -> list[JSONObject]:
    """Narrow an array of objects; malformed private rubric input fails rather than passes."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("expected array of objects")
    return value


def object_value(value: JSONValue | object) -> JSONObject:
    """Require one object at a typed rubric or normalized-plan boundary."""
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return cast(JSONObject, value)


def operation_id(value: JSONObject) -> str:
    """Format the exact canonical operation identity, preserving its version."""
    return f"{value['namespace']}.{value['name']}@{value['version']}"


def _matches(role: JSONObject, selector: JSONObject) -> bool:
    """Select operations by actual intent parameters, never objective keywords."""
    intent = object_value(role["execution_intent"])
    if operation_id(object_value(intent["operation"])) != selector["operation"]:
        return False
    parameters = object_value(intent["parameters"])
    return all(parameters.get(k) == v for k, v in object_value(selector["parameters"]).items())


def _ancestors(task_id: str, tasks: list[JSONObject]) -> set[str]:
    """Compute actual DAG precedence while allowing equivalent intermediate tasks."""
    graph = {str(task["id"]): cast(list[str], task["depends_on"]) for task in tasks}
    found: set[str] = set()
    pending = list(graph[task_id])
    while pending:
        node = pending.pop()
        if node not in found:
            found.add(node)
            pending.extend(graph[node])
    return found


def check_plan(plan: MissionPlan, checks: list[JSONObject]) -> JSONObject:
    """Check necessary field-level conditions and leave overall correctness pending review."""
    raw = plan.to_json()
    tasks = objects(raw["tasks"])
    roles = [(task, role) for task in tasks for role in objects(task["roles"])]
    contexts = objects(raw.get("contexts", []))
    findings: list[JSONValue] = []
    for check in checks:
        kind = check["kind"]
        selected = [
            (task, role)
            for task, role in roles
            if "selector" in check and _matches(role, object_value(check["selector"]))
        ]
        if kind == "operation":
            passed = bool(selected)
        elif kind == "precedence":
            before = [str(t["id"]) for t, r in roles if _matches(r, object_value(check["before"]))]
            after = [str(t["id"]) for t, r in roles if _matches(r, object_value(check["after"]))]
            passed = bool(before and after) and all(
                any(b in _ancestors(a, tasks) for b in before) for a in after
            )
        elif kind == "independent":
            passed = all(
                context["coupling_mode"] == "independent"
                and not context.get("relations")
                and not context.get("executor_constraints")
                for context in contexts
            )
        elif kind == "requires_active":
            sources = {
                (t["id"], r["id"]) for t, r in roles if _matches(r, object_value(check["source"]))
            }
            targets = {
                (t["id"], r["id"]) for t, r in roles if _matches(r, object_value(check["target"]))
            }
            passed = False
            for context in contexts:
                for relation in objects(context.get("relations", [])):
                    source, target = (
                        object_value(relation["source"]),
                        object_value(relation["target"]),
                    )
                    if (
                        relation["kind"] == "requires-active"
                        and (source["task_id"], source["role_id"]) in sources
                        and (target["task_id"], target["role_id"]) in targets
                    ):
                        passed = True
        elif kind == "resource":
            passed = bool(selected) and all(
                any(
                    resource["kind"] == check["resource_kind"]
                    and resource["units"] == check["units"]
                    for resource in objects(object_value(role["requirements"])["resources"])
                )
                for _, role in selected
            )
        elif kind == "timing":
            passed = bool(selected) and all(
                object_value(task["timing"]).get(str(check["field"])) == check["value"]
                for task, _ in selected
            )
        elif kind == "capability_constraint":
            passed = bool(selected) and all(
                any(
                    constraint == check["constraint"]
                    for capability in objects(object_value(role["requirements"])["capabilities"])
                    if operation_id(object_value(capability["contract"])) == check["contract"]
                    for constraint in objects(capability["constraints"])
                )
                for _, role in selected
            )
        elif kind == "requires_missing_evidence":
            # No admitted draft may manufacture a fact which this case intentionally withholds.
            passed = False
        else:
            raise ValueError(f"unsupported private scoring check: {kind}")
        findings.append({"id": check["id"], "kind": kind, "passed": passed})
    return {
        "necessary_conditions_pass": all(object_value(row)["passed"] is True for row in findings),
        "checks": findings,
        "semantic_correctness": "pending_blind_human_review",
        "basis": "necessary_conditions_only_not_a_complete_semantic_or_physical_verifier",
    }
