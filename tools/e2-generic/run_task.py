#!/usr/bin/env python3
"""Run one generic rolling-horizon RoboGuide mission on any COHERENT graph task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

from coherent_local_eaios.generic_bridge import (
    action_contexts,
    goal_status,
    initial_graph,
    load_task_data,
    read_graph_database,
)

CONTROLLER_API = "http://127.0.0.1:28070"
BRIDGE_API = "http://127.0.0.1:28120"


def json_write(path: Path, value: object) -> None:
    """Write deterministic, readable JSON evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def http_json(method: str, url: str, body: object | None = None) -> tuple[int, object]:
    """Perform one bounded local HTTP request and decode its JSON body."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"raw": raw}


def wait_http(url: str, attempts: int = 60) -> None:
    """Wait for a local service or fail with a bounded timeout."""
    for _ in range(attempts):
        try:
            status, _ = http_json("GET", url)
            if status < 400:
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {url}")


def git(repo: Path, *arguments: str) -> str:
    """Read one Git provenance value without mutating the repository."""
    return subprocess.check_output(["git", "-C", str(repo), *arguments], text=True).strip()


def operation_for(agent_id: int) -> str:
    """Return the unique generic operation identity for one physical agent."""
    return f"coherent.agent-{agent_id}-primitive@v1"


def catalog_for(contexts: dict[int, dict[str, object]]) -> dict[str, object]:
    """Build a capability catalog that routes each agent identity to one Node."""
    capabilities: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []
    for agent_id, context in sorted(contexts.items()):
        name = f"agent-{agent_id}-primitive"
        agent_class = str(context["agent_class"])
        contract = {"namespace": "coherent", "name": name, "version": "v1"}
        capabilities.append(
            {
                "contract": contract,
                "description": (
                    f"Execute exactly one currently available official COHERENT primitive with "
                    f"{agent_class} ({agent_id}). The Role must request one exclusive space unit."
                ),
                "attributes": [],
            }
        )
        operations.append(
            {
                "operation": contract,
                "description": (
                    f"Execute exactly one action from the current action list for {agent_class} "
                    f"({agent_id}); no hidden macro, replanning, or action correction occurs "
                    "locally."
                ),
                "parameters": [
                    {
                        "name": "env",
                        "type": "string",
                        "required": True,
                        "description": "Exact official environment name supplied in the request.",
                    },
                    {
                        "name": "task",
                        "type": "integer",
                        "required": True,
                        "description": "Exact official zero-based task index.",
                    },
                    {
                        "name": "agent_id",
                        "type": "integer",
                        "required": True,
                        "description": "Exact graph identity of the selected agent.",
                    },
                    {
                        "name": "agent_class",
                        "type": "string",
                        "required": True,
                        "description": "Exact class_name of the selected graph agent.",
                    },
                    {
                        "name": "action",
                        "type": "string",
                        "required": True,
                        "description": "One exact string from the current available action list.",
                    },
                ],
                "required_capabilities": [contract],
            }
        )
    return {
        "schema_version": "roboguide.capability-catalog/v0.3",
        "capabilities": capabilities,
        "operations": operations,
    }


def compact_observation(context: dict[str, object]) -> dict[str, object]:
    """Keep the official partial graph intact while omitting simulator transforms."""
    observation = cast(dict[str, object], context["observation"])
    nodes = []
    for raw in cast(list[dict[str, object]], observation["nodes"]):
        nodes.append(
            {
                key: raw[key]
                for key in ("id", "class_name", "category", "properties", "states")
                if key in raw
            }
        )
    return {"nodes": nodes, "edges": observation["edges"]}


def objective_text(
    env_name: str,
    task_index: int,
    task_data: dict[str, object],
    contexts: dict[int, dict[str, object]],
    decision: int,
    prior_feedback: list[dict[str, object]],
) -> str:
    """Construct one grounded rolling-horizon decision request."""
    state = [
        {
            "agent_id": agent_id,
            "agent_class": context["agent_class"],
            "operation": operation_for(agent_id),
            "observation": compact_observation(context),
            "available_actions": context["available_actions"],
        }
        for agent_id, context in sorted(contexts.items())
    ]
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    history = json.dumps(prior_feedback[-5:], ensure_ascii=False, separators=(",", ":"))
    instruction = cast(list[object], task_data["goal_instruction"])[0]
    return f"""COHERENT generic rolling-horizon decision {decision} for {env_name}/task{task_index}.
Official task instruction: {instruction}
Official task_goal: {json.dumps(task_data['task_goal'], ensure_ascii=False)}

Choose exactly ONE best next primitive action from exactly one agent's available_actions below.
Return a roboguide.mission-plan/v0.8 with exactly one context, one task, and one task role.
Use the selected agent's exact operation. The execution_intent parameters must contain exactly:
env={env_name!r}, task={task_index}, agent_id=<selected integer>,
agent_class=<selected exact string>, action=<one exact available action string>.
The role must require that operation's matching capability and exactly one exclusive resource
with kind "space" and units 1. Use satisfaction basis "execution-report". Do not invent actions,
combine actions, encode a multi-step macro, or select a Node. Local How is only this one primitive.

Current official per-agent partial observations and exact available actions:
{payload}

Recent execution/validation feedback:
{history}
"""


def render_node(template: str, output: Path, run: Path, agent_id: int) -> tuple[str, str]:
    """Render one Node config with a unique generic operation contract."""
    node_id = f"coherent-agent-{agent_id}-generic"
    contract = operation_for(agent_id)
    rendered = template
    replacements = {
        "NODE_ID_PLACEHOLDER": node_id,
        "STATE_DIRECTORY_PLACEHOLDER": str(run / f"node-state-{node_id}"),
        "LOCAL_SYSTEM_PLACEHOLDER": f"coherent-agent-{agent_id}-local",
        "CONTRACT_PLACEHOLDER": contract,
        "READINESS_PLACEHOLDER": f"agent-{agent_id}-primitive",
        "RESOURCE_PLACEHOLDER": f"coherent-agent-{agent_id}-slot",
        "LOCK_PLACEHOLDER": f"coherent-agent-{agent_id}-world",
    }
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    output.write_text(rendered, encoding="utf-8")
    return node_id, contract


def validate_plan(
    plan: dict[str, object],
    mission_id: str,
    env_name: str,
    task_index: int,
    contexts: dict[int, dict[str, object]],
) -> tuple[bool, str, dict[str, object] | None]:
    """Fail closed unless the generated plan represents one exact current primitive."""
    try:
        mission = cast(dict[str, object], plan["mission"])
        if mission.get("id") != mission_id:
            return False, "generated mission.id differs from requested mission id", None
        tasks = cast(list[dict[str, object]], plan["tasks"])
        if len(tasks) != 1:
            return False, "generated plan must contain exactly one task", None
        roles = cast(list[dict[str, object]], tasks[0]["roles"])
        if len(roles) != 1:
            return False, "generated task must contain exactly one role", None
        intent = cast(dict[str, object], roles[0]["execution_intent"])
        op = cast(dict[str, object], intent["operation"])
        operation = f"{op['namespace']}.{op['name']}@{op['version']}"
        parameters = cast(dict[str, object], intent["parameters"])
        required = {"env", "task", "agent_id", "agent_class", "action"}
        if set(parameters) != required:
            return False, "execution parameters are not the exact primitive parameter set", None
        agent_id = parameters["agent_id"]
        if isinstance(agent_id, bool) or not isinstance(agent_id, int):
            return False, "agent_id is not an integer", None
        context = contexts.get(agent_id)
        if context is None:
            return False, "selected agent_id is not an official task agent", None
        if operation != operation_for(agent_id):
            return False, "selected operation does not match agent_id", None
        if parameters["env"] != env_name or parameters["task"] != task_index:
            return False, "selected env/task differs from the running task", None
        if parameters["agent_class"] != context["agent_class"]:
            return False, "selected agent_class differs from the official graph", None
        if parameters["action"] not in cast(list[str], context["available_actions"]):
            return False, "selected action is not in the current official action list", None
        return True, "validated exact current official primitive", parameters
    except (KeyError, TypeError, IndexError) as error:
        return False, f"generated plan shape is invalid: {error}", None


def start_process(
    command: list[str], log_path: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Start one evidence-logged local runtime process."""
    stream = log_path.open("wb")
    process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
    setattr(process, "_evidence_stream", stream)  # noqa: B010
    return process


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    """Stop only the child processes created by this task runner."""
    for process in reversed(processes):
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
    for process in reversed(processes):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stream = getattr(process, "_evidence_stream", None)
        if stream is not None:
            stream.close()


def main() -> int:
    """Execute one full generic task while retaining all planning and runtime evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--coherent-root", type=Path, required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    coherent = args.coherent_root.resolve()
    pefa = coherent / "src" / "experiment" / "PEFA"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if os.environ.get("OPENAI_API_KEY", "") == "":
        raise SystemExit("OPENAI_API_KEY is required")
    task_data = load_task_data(pefa, args.env, args.task)
    graph = initial_graph(task_data)
    contexts = action_contexts(pefa, task_data, graph)
    json_write(output / "official-task-input.json", task_data)
    json_write(output / "initial-action-context.json", contexts)
    catalog = catalog_for(contexts)
    catalog_path = output / "capability-catalog.json"
    json_write(catalog_path, catalog)
    config_text = args.config.read_text(encoding="utf-8")
    old_line = 'capability_catalog_path = "scenarios/e2-coherent-graph/capability-catalog.json"'
    escaped_catalog = str(catalog_path).replace("\\", "\\\\").replace('"', '\\"')
    config_text = config_text.replace(
        old_line, f'capability_catalog_path = "{escaped_catalog}"'
    )
    runtime_config = output / "mission-runtime.toml"
    runtime_config.write_text(config_text, encoding="utf-8")
    provenance = {
        "schema": "roboguide.coherent-generic-run/v0.1",
        "public_task": f"{args.env}/task{args.task}",
        "roboguide_commit": git(repo, "rev-parse", "HEAD"),
        "roboguide_status": git(repo, "status", "--short"),
        "coherent_commit": git(coherent, "rev-parse", "HEAD"),
        "model": "gpt-6-sol",
        "planner_reviewer_repairer_model": "gpt-6-sol",
        "credentials_recorded": False,
        "controller_path": (
            "MissionPlan -> Controller -> Node -> generic adapter -> Get_env_info.step"
        ),
        "adapter_hidden_plan": False,
    }
    json_write(output / "provenance.json", provenance)
    template = (repo / "scenarios/e2-coherent-graph/node.toml.template").read_text(
        encoding="utf-8"
    )
    node_ids: list[str] = []
    for agent_id in sorted(contexts):
        node_id, _ = render_node(
            template, output / f"{agent_id}-node.toml", output, agent_id
        )
        node_ids.append(node_id)
    registry = {
        "schema": "roboguide.physical-entity-registry/v0.1",
        "registry_id": f"coherent-generic-{args.env}-task{args.task}",
        "revision": 1,
        "routing_profile": "one-routable-entity-per-node",
        "entities": [
            {"entity_id": f"coherent-agent-{agent_id}", "node_id": node_id}
            for agent_id, node_id in zip(sorted(contexts), node_ids, strict=True)
        ],
    }
    registry_path = output / "physical-entity-registry.json"
    json_write(registry_path, registry)
    server = repo / "target" / "debug" / "integration-server"
    node = repo / "target" / "debug" / "roboguide-node"
    if not server.is_file() or not node.is_file():
        raise SystemExit("build target/debug/integration-server and roboguide-node first")
    processes: list[subprocess.Popen[bytes]] = []
    runtime_env = dict(os.environ)
    runtime_env["PYTHONPATH"] = str(repo / "integrations" / "coherent-local-eaios")
    runtime_env["ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP"] = "1"
    database = output / "coherent-generic.sqlite3"
    trajectory: list[dict[str, object]] = []
    feedback: list[dict[str, object]] = []
    started = time.time()
    try:
        processes.append(
            start_process(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "coherent_local_eaios.generic_main",
                    "--state-db",
                    str(database),
                    "--evidence-dir",
                    str(output / "primitive-evidence"),
                    "--pefa-root",
                    str(pefa),
                    "--env",
                    args.env,
                    "--task",
                    str(args.task),
                ],
                output / "coherent-generic.log",
                runtime_env,
            )
        )
        wait_http(f"{BRIDGE_API}/v1/health")
        processes.append(
            start_process(
                [
                    str(server),
                    "127.0.0.1:25070",
                    str(output / "controller.sqlite3"),
                    "127.0.0.1:28070",
                    "127.0.0.1:28091",
                    str(output / "artifacts"),
                    "",
                    str(registry_path),
                ],
                output / "integration-server.log",
                runtime_env,
            )
        )
        wait_http(f"{CONTROLLER_API}/healthz")
        for agent_id in sorted(contexts):
            processes.append(
                start_process(
                    [str(node), str(output / f"{agent_id}-node.toml")],
                    output / f"node-{agent_id}.log",
                    runtime_env,
                )
            )
        for _ in range(120):
            status, inventory = http_json("GET", f"{CONTROLLER_API}/v1/inventory")
            registered = {
                item["node_id"]
                for item in cast(dict[str, list[dict[str, object]]], inventory).get("nodes", [])
            }
            if status == 200 and set(node_ids) <= registered:
                break
            time.sleep(1)
        else:
            raise RuntimeError("timeout waiting for all generic agent nodes")
        json_write(output / "inventory.json", inventory)
        gt_raw = cast(list[object], task_data["ground_truth_step_num"])[0]
        max_decisions = 2 * int(cast(int, gt_raw)) + 1
        for decision in range(1, max_decisions + 1):
            graph, primitive_steps = read_graph_database(database)
            status_before = goal_status(task_data, graph)
            if status_before["passed"] is True:
                break
            contexts = action_contexts(pefa, task_data, graph)
            decision_dir = output / "decisions" / f"{decision:03d}"
            decision_dir.mkdir(parents=True, exist_ok=False)
            objective = objective_text(
                args.env, args.task, task_data, contexts, decision, feedback
            )
            objective_path = decision_dir / "objective.txt"
            objective_path.write_text(objective, encoding="utf-8")
            mission_id = f"mission-{args.env}-task{args.task}-decision-{decision:03d}"
            planning_dir = decision_dir / "planning"
            command = [
                sys.executable,
                str(repo / "tools/e2-full-task17/plan_and_record.py"),
                "--repo",
                str(repo),
                "--config",
                str(runtime_config),
                "--objective",
                str(objective_path),
                "--output",
                str(planning_dir),
                "--mission-id",
                mission_id,
                "--public-task",
                f"{args.env}/task{args.task}",
                "--adapter-scope",
                "generic one-primitive contract through original Get_env_info.step",
            ]
            completed = subprocess.run(
                command,
                stdout=(decision_dir / "planner.stdout.log").open("wb"),
                stderr=subprocess.STDOUT,
                env=runtime_env,
                timeout=1900,
                check=False,
            )
            record: dict[str, object] = {
                "decision": decision,
                "mission_id": mission_id,
                "primitive_steps_before": primitive_steps,
                "planner_returncode": completed.returncode,
                "goal_before": status_before,
            }
            if completed.returncode != 0 or not (planning_dir / "mission-plan.json").is_file():
                record["outcome"] = "planning_failed"
                feedback.append({"decision": decision, "result": "planning_failed"})
                trajectory.append(record)
                json_write(decision_dir / "decision-result.json", record)
                continue
            plan = cast(
                dict[str, object],
                json.loads((planning_dir / "mission-plan.json").read_text(encoding="utf-8")),
            )
            valid, detail, parameters = validate_plan(
                plan, mission_id, args.env, args.task, contexts
            )
            record["validation"] = {"passed": valid, "detail": detail}
            record["selected_parameters"] = parameters
            if not valid:
                record["outcome"] = "plan_validation_failed"
                feedback.append({"decision": decision, "result": detail})
                trajectory.append(record)
                json_write(decision_dir / "decision-result.json", record)
                continue
            post_status, post_body = http_json(
                "POST", f"{CONTROLLER_API}/v1/missions", plan
            )
            json_write(
                decision_dir / "controller-post.json",
                {"status": post_status, "body": post_body},
            )
            if post_status != 202:
                record["outcome"] = "controller_rejected"
                record["controller"] = post_body
                feedback.append({"decision": decision, "result": "controller_rejected"})
                trajectory.append(record)
                json_write(decision_dir / "decision-result.json", record)
                continue
            mission: object = {}
            for _ in range(240):
                _, mission = http_json(
                    "GET", f"{CONTROLLER_API}/v1/missions/{mission_id}"
                )
                mission_status = cast(dict[str, object], mission).get("status")
                if mission_status in {"Completed", "Failed", "Cancelled"}:
                    break
                time.sleep(0.25)
            json_write(decision_dir / "mission.json", mission)
            graph_after, steps_after = read_graph_database(database)
            status_after = goal_status(task_data, graph_after)
            record["primitive_steps_after"] = steps_after
            record["goal_after"] = status_after
            record["controller_status"] = cast(dict[str, object], mission).get("status")
            record["outcome"] = (
                "primitive_completed" if steps_after == primitive_steps + 1 else "no_primitive"
            )
            feedback.append(
                {
                    "decision": decision,
                    "selected": parameters,
                    "result": record["outcome"],
                    "controller_status": record["controller_status"],
                }
            )
            trajectory.append(record)
            json_write(decision_dir / "decision-result.json", record)
            json_write(output / "trajectory.json", trajectory)
        final_graph, final_steps = read_graph_database(database)
        final_goal = goal_status(task_data, final_graph)
        json_write(output / "final-scene-graph.json", final_graph)
        _, events = http_json("GET", f"{CONTROLLER_API}/v1/events")
        _, attempts = http_json("GET", f"{CONTROLLER_API}/v1/execution-attempts")
        json_write(output / "controller-events.json", events)
        json_write(output / "execution-attempts.json", attempts)
        verdict = {
            "schema": "roboguide.coherent-generic-verdict/v0.1",
            "public_task": f"{args.env}/task{args.task}",
            "success": final_goal["passed"],
            "failure_reason": None if final_goal["passed"] else "decision budget exhausted",
            "official_goal_check": final_goal,
            "primitive_steps": final_steps,
            "decision_count": len(trajectory),
            "ground_truth_steps": int(cast(int, gt_raw)),
            "max_decisions": max_decisions,
            "wall_time_seconds": time.time() - started,
        }
        json_write(output / "trajectory.json", trajectory)
        json_write(output / "verdict.json", verdict)
        hashes = []
        for path in sorted(output.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                hashes.append(f"{digest}  {path.relative_to(output).as_posix()}")
        (output / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if final_goal["passed"] else 1
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
