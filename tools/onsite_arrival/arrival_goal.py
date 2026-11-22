"""Use a Mission's accepted navigation goal for one on-site human observation."""

from __future__ import annotations

import argparse
import json
import ssl
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from mission.submission_evidence import canonical_plan_digest
from roboguide_workbench.client import WorkbenchError, identifier, request_json
from roboguide_workbench.views import safe_text

Doc = dict[str, Any]
VERIFIER = {"namespace": "observation.arrival", "name": "onsite", "version": "v1"}
PREDICATE = "arrived-and-stopped/v1"
AGE_MS = 5000
NAVIGATE = {"namespace": "mobility", "name": "navigate", "version": "v1"}
DELEGATE = {"namespace": "task", "name": "delegate", "version": "v1"}
SPEC = {"verifier": VERIFIER, "predicate": PREDICATE, "max_evidence_age_ms": AGE_MS}


@dataclass(frozen=True)
class Peer:
    """An enrolled principal and the exact tenant used for all Mission calls."""

    endpoint: str
    tenant: str
    principal: str
    tls: ssl.SSLContext

    @classmethod
    def load(cls, path: Path) -> Peer:
        """Open only the configured mTLS origin and resolve keys relative to its profile."""
        config = json.loads(path.read_text())
        origin = urlsplit(config["endpoint"])
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or origin.path not in ("", "/")
        ):
            raise ValueError("Expected a fixed HTTPS Controller origin")
        tenant = identifier(config["tenant"])
        if tenant == "*":
            raise ValueError("Select the Mission tenant")
        tls = ssl.create_default_context(cafile=str(path.parent / config["ca"]))
        tls.load_cert_chain(path.parent / config["certificate"], path.parent / config["key"])
        return cls(config["endpoint"].rstrip("/"), tenant, identifier(config["principal"]), tls)


def exchange(
    peer: Peer, path: str, body: Doc | None = None, *, deadline_ns: int | None = None
) -> Doc:
    """Read or submit once. Only source registry administration needs a global selector."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if path != "/v1/verifier-sources":
        headers["X-RoboGuide-Tenant"] = peer.tenant
    payload = None if body is None else json.dumps(body, allow_nan=False).encode()
    timeout = 5.0 if deadline_ns is None else (deadline_ns - time.monotonic_ns()) / 1e9
    if timeout <= 0:
        raise ValueError("Arrival observation expired before contacting the Controller")
    return request_json(
        peer.endpoint + path,
        "GET" if body is None else "POST",
        headers,
        payload,
        timeout,
        peer.tls,
    )


def enroll(peer: Peer, source_id: str, observer: str) -> Doc:
    """Register once while preserving other sources and their original revisions."""
    path = "/v1/verifier-sources"
    registry = exchange(peer, path)["registry"]
    if registry is None:
        registry = {
            "schema_version": "roboguide.semantic-verifier-registry/v0.1",
            "registry_id": "onsite-arrival",
            "revision": 0,
            "sources": {},
        }
    source = {
        "source_id": source_id,
        "tenant_id": peer.tenant,
        "revision": 1,
        "producer_principals": [observer],
        "verifier": VERIFIER,
        "predicates": [PREDICATE],
        "enabled": True,
        "require_artifact": False,
        "continuous": False,
        "max_age_ms": AGE_MS,
        "stop_timeout_ms": 30000,
    }
    sources = registry["sources"]
    if any(
        other["enabled"]
        and other["tenant_id"] == peer.tenant
        and other["verifier"] == VERIFIER
        and PREDICATE in other["predicates"]
        for key, other in sources.items()
        if key != source_id
    ):
        raise ValueError("Another enabled source already covers this arrival verifier")
    if source_id in sources:
        if sources[source_id] != source:
            raise ValueError("Existing source has a different contract")
        return {"already_registered": source_id}
    revision = registry["revision"]
    return exchange(
        peer,
        path,
        {
            "command_id": uuid.uuid4().hex,
            "expected_revision": revision,
            "registry": {
                **registry,
                "revision": revision + 1,
                "sources": {**sources, source_id: source},
            },
        },
    )


def one(rows: list[Doc], label: str) -> Doc:
    """Require one unambiguous accepted binding."""
    if len(rows) != 1:
        raise ValueError(f"Expected one {label}, found {len(rows)}")
    return rows[0]


def select_goal(
    plan: Doc,
    task_id: str | None,
    scope: tuple[str, str] | None = None,
    expected_target: Doc | None = None,
) -> Doc:
    """Read target and Actor from the same Role in the actual accepted plan."""
    choices = []
    for task in plan["tasks"]:
        if task_id is not None and task["id"] != task_id:
            continue
        for role in task["roles"]:
            lane = (task["context_id"], role["context_role"])
            if scope is not None and scope != lane:
                continue
            intent = role["execution_intent"]
            params = intent["parameters"]
            if (
                intent["operation"] == NAVIGATE
                and isinstance(params.get("destination"), str)
                and params["destination"].strip()
            ):
                # Keep map, frame and coordinate qualifiers in the frozen target.
                target = {
                    "operation": NAVIGATE,
                    "navigation": json.dumps(
                        params,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                }
            elif intent["operation"] == DELEGATE and params == {"profile": "local-task-v1"}:
                # The deployed Robonix adapter receives the full local objective as text.
                # Show that exact instruction to the observer, including any map and action limits.
                target = {"operation": DELEGATE, "objective": intent["objective"]}
            else:
                continue
            if expected_target is not None and target != expected_target:
                continue
            context = one([c for c in plan["contexts"] if c["id"] == lane[0]], "Context")
            link = one([r for r in context["roles"] if r["id"] == lane[1]], "ContextRole")
            actor = one([a for a in plan["mission"]["actors"] if a["id"] == link["actor"]], "Actor")
            choices.append(
                {
                    "task_id": task["id"],
                    "role_id": role["id"],
                    "context_id": lane[0],
                    "context_role": lane[1],
                    "entity": actor.get("physical_entity"),
                    "target": target,
                }
            )
    if not choices and expected_target is not None:
        raise ValueError("Current Task destination differs from the frozen arrival goal")
    selected = one(choices, "arrival Role (select the navigation Task with --task if needed)")
    if task_id is None and expected_target is None and selected["target"]["operation"] == DELEGATE:
        raise ValueError(
            "Select the delegated arrival Task with --task; its objective is free text"
        )
    return selected


def policy_fields(plan: Doc, task_id: str | None = None, *, allow_handoff: bool = False) -> Doc:
    """Derive the two install-policy fields directly from the original navigation Task."""
    goal = select_goal(plan, task_id)
    if not allow_handoff and not goal["entity"]:
        raise ValueError("Fixed arrival requires a physical_entity in the accepted Actor")
    return {
        "goal_verifier": deepcopy(SPEC),
        "goal_context": {
            "schema_version": "onsite-arrival-context/v1",
            "original_task": goal["task_id"],
            "context_id": goal["context_id"],
            "context_role": goal["context_role"],
            "subject": {"kind": "context-role"}
            if allow_handoff
            else {"kind": "entity", "id": goal["entity"]},
            "target": deepcopy(goal["target"]),
        },
    }


def mission_path(mission: str) -> str:
    """Encode one Mission identifier for the original Controller routes."""
    return "/v1/missions/" + quote(identifier(mission), safe="")


def install(peer: Peer, mission: str, policy: Doc, task: str | None, handoff: bool) -> Doc:
    """Attach the observer to the already accepted Mission using its existing revision policy."""
    path = mission_path(mission) + "/plan-revisions"
    view = exchange(peer, path)
    if view["mission_id"] != mission or view["current_plan_revision"] != 0 or view["authority"]:
        raise ValueError("Install on the original Mission before its revision policy is installed")
    if {"goal_context", "goal_verifier"} & policy.keys():
        raise ValueError("Supply the base revision policy without duplicate goal fields")
    fields = policy_fields(view["current_plan"], task, allow_handoff=handoff)
    return exchange(
        peer,
        path,
        {
            "schema_version": "roboguide.runtime-plan-revision/v0.1",
            "command_id": uuid.uuid4().hex,
            "expected_revision": 0,
            "action": {"kind": "install-policy", "policy": {**policy, **fields}},
        },
    )


def observation(peer: Peer, mission: str, *, deadline_ns: int | None = None) -> tuple[str, Doc]:
    """Resolve the frozen destination and today's accepted Actor, Group and completed attempts."""
    base = mission_path(mission)
    view = exchange(peer, base + "/plan-revisions", deadline_ns=deadline_ns)
    contract = view["goal_contract"]
    ctx = view["goal_context"]
    if not contract or not ctx or not view["goal_source_current"]:
        raise ValueError("Mission has no current arrival source")
    source = contract["source"]
    if (
        view["mission_id"] != mission
        or contract["mission_id"] != mission
        or contract["tenant_id"] != peer.tenant
        or contract["spec"] != SPEC
        or ctx.get("schema_version") != "onsite-arrival-context/v1"
        or canonical_plan_digest(ctx) != contract["context_digest"]
        or peer.principal not in source["producer_principals"]
        or source["require_artifact"]
    ):
        raise ValueError("Invalid frozen arrival contract or observer")
    plan = view["current_plan"]
    scope = (ctx["context_id"], ctx["context_role"])
    # A reviewed replacement Task may have a new ID. Keep the original logical responsibility.
    original = ctx["original_task"]
    task_id = original if any(t["id"] == original for t in plan["tasks"]) else None
    goal = select_goal(plan, task_id, scope, ctx["target"])
    group = view["current_group_id"]
    rows = [
        r
        for r in exchange(peer, base + "/execution-attempts", deadline_ns=deadline_ns)["attempts"]
        if r["group_id"] == group
    ]
    state = exchange(peer, base, deadline_ns=deadline_ns)
    if state["isolation"] is not None:
        raise ValueError("Mission entered isolation before the arrival observation")
    if (
        state["status"] != "Running"
        or state["mission_id"] != mission
        or state["group_id"] != group
        or state["plan_revision"] != view["current_plan_revision"]
        or state["goal_gate"] != contract["digest"]
    ):
        raise ValueError("Mission is no longer running in the displayed Group")
    selected = one(
        [r for r in rows if (r["task_id"], r["role_id"]) == (goal["task_id"], goal["role_id"])],
        "arrival attempt",
    )
    if selected["status"] != "Completed" or any(r["mission_id"] != mission for r in rows):
        raise ValueError("Current arrival attempt has not completed")
    entity = goal["entity"]
    subject = ctx["subject"]
    if subject != {"kind": "context-role"} and (
        not entity or subject != {"kind": "entity", "id": entity}
    ):
        raise ValueError("Execution belongs to a different robot")
    attempts = sorted(rows, key=lambda r: (r["task_id"], r["role_id"]))
    if len({(r["task_id"], r["role_id"]) for r in attempts}) != len(attempts):
        raise ValueError("Current attempt is ambiguous in Controller history")
    binding = {
        "mission_id": mission,
        "tenant_id": peer.tenant,
        "contract_digest": contract["digest"],
        "source_id": source["source_id"],
        "source_revision": source["revision"],
        "valid_for_ms": min(source["max_age_ms"], AGE_MS),
        "plan_revision": view["current_plan_revision"],
        "group_id": group,
        "plan_digest": view["current_plan_digest"],
        "predicate": PREDICATE,
        "attempts": [
            {
                "task_ref": {"mission_id": mission, "task_id": r["task_id"]},
                "role_id": r["role_id"],
                "attempt_id": r["execution_id"],
            }
            for r in attempts
        ],
    }
    target = ctx["target"]
    description = target.get("navigation") or target["objective"]
    robot = f"{entity}（Node {selected['node_id']}）" if entity else f"Node {selected['node_id']}"
    return (
        f"{safe_text(robot)}\nMission 目标 {safe_text(plan['mission']['objective'])}"
        f"\n任务目标 {safe_text(description)}",
        binding,
    )


def confirm(peer: Peer, mission: str, ask: Callable[[str], str] = input) -> Doc:
    """Publish only an explicit human observation against the exact displayed binding."""
    display, binding = observation(peer, mission)
    answer = ask(f"{display}\n亲眼确认这台机器人已到达该地点并停稳？输入 yes 确认，其他输入跳过：")
    if answer.strip() != "yes":
        return {"observation": "unknown", "submitted": False}
    started = time.monotonic_ns()
    observed_ns = time.time_ns()
    deadline = started + binding["valid_for_ms"] * 1_000_000
    if observation(peer, mission, deadline_ns=deadline) != (display, binding):
        raise ValueError("Mission changed during confirmation; inspect the current robot again")
    # Reserve time for delivery. The Controller starts its own validity window at receive.
    valid_for_ms = (deadline - time.monotonic_ns()) // 2_000_000
    if valid_for_ms <= 0:
        raise ValueError("Observation expired during recheck")
    evidence = {
        **binding,
        "schema_version": "roboguide.mission-goal-evidence/v0.1",
        "evidence_id": uuid.uuid4().hex,
        "verdict": "satisfied",
        "finality": "final",
        "source_observed_at_ms": observed_ns // 1_000_000,
        "sequence": observed_ns,
        "valid_for_ms": valid_for_ms,
        "artifact_digest": None,
        "artifact_size_bytes": None,
    }
    digest = canonical_plan_digest(evidence)
    try:
        response = exchange(
            peer,
            "/v1/mission-goal-evidence",
            evidence,
            deadline_ns=deadline - valid_for_ms * 1_000_000,
        )
        receipt = response["receipt"]
        if (
            receipt["evidence"] != evidence
            or receipt["authority_subject"] != peer.principal
            or receipt["input_digest"] != digest
        ):
            raise WorkbenchError("Returned receipt differs from the submitted observation")
        return response
    except (WorkbenchError, KeyError, TypeError) as error:
        # The Controller may have committed before its reply was lost. Only look up this input.
        path = mission_path(mission) + "/plan-revisions"
        try:
            history = exchange(peer, path)
            if history["mission_id"] == mission:
                for row in history["goal_evidence"]:
                    if row["input_digest"] == digest:
                        return {"submission": "recorded", "goal_evidence": row}
        except (WorkbenchError, KeyError, TypeError):
            pass
        raise ValueError(f"未取得此观察的回执，可在 {path} 按 {digest} 查询。{error}") from error


def main() -> None:
    """Run the deployment registration, per-Mission install, or independent observation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", type=Path, required=True, help="existing mTLS peer JSON profile")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("enroll")
    register.add_argument("--source", default="onsite-arrival")
    register.add_argument("--observer", required=True)
    attach = commands.add_parser("install")
    attach.add_argument("mission")
    attach.add_argument("--policy", type=Path, required=True)
    attach.add_argument(
        "--task", help="arrival Task ID for free-text delegation or multiple possible goals"
    )
    attach.add_argument("--allow-handoff", action="store_true")
    commands.add_parser("confirm").add_argument("mission")
    args = parser.parse_args()
    try:
        peer = Peer.load(args.peer)
        if args.command == "enroll":
            result = enroll(peer, args.source, args.observer)
        elif args.command == "install":
            result = install(
                peer,
                args.mission,
                json.loads(args.policy.read_text()),
                args.task,
                args.allow_handoff,
            )
        else:
            result = confirm(peer, args.mission)
        print(json.dumps(result, ensure_ascii=False))
    except (EOFError, KeyError, OSError, TypeError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
