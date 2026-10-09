#!/usr/bin/env python3
"""Multi-task plan experiment using unchanged RoboGuide MI, Controller, and Node binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, cast

from coherent_local_eaios.generic_bridge import (
    action_contexts,
    goal_status,
    initial_graph,
    load_task_data,
    read_graph_database,
)
from dag_policy import DAG_PROFILES, PROMPT_PROFILES, objective_text, validate_plan
from run_task import (
    catalog_for,
    git,
    http_json,
    json_write,
    render_node,
    start_process,
    stop_processes,
)


def wait_service(url: str, processes: list[subprocess.Popen[bytes]]) -> None:
    """Wait for this run's service, failing promptly if any owned process exits."""
    for _ in range(60):
        if any(p.poll() is not None for p in processes):
            raise RuntimeError("owned service exited during startup; see process logs")
        try:
            code, _ = http_json("GET", url)
            if code == 200:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"service startup timeout: {url}")


def local_executions(database: Path) -> list[dict[str, Any]]:
    """Read raw local execution outcomes, including failures, without modifying the store."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM executions ORDER BY created_at_unix_ms, execution_id"
            )
        ]


def mission_executions(rows: list[dict[str, Any]], mission_id: str) -> list[dict[str, Any]]:
    """Return compact, decoded local outcomes owned by one Mission."""
    results = []
    for row in rows:
        invocation = json.loads(str(row["invocation_json"]))
        if invocation.get("mission_id") != mission_id:
            continue
        raw_outcome = row.get("local_outcome_json")
        outcome = None if raw_outcome is None else json.loads(str(raw_outcome))
        if isinstance(outcome, dict):
            outcome = {
                key: outcome[key]
                for key in (
                    "guard",
                    "primitive_executed",
                    "step_count_unchanged",
                    "step_before",
                    "transition",
                    "error_type",
                    "error",
                    "evidence_file",
                )
                if key in outcome
            }
        results.append(
            {
                "execution_id": row["execution_id"],
                "mission_id": invocation["mission_id"],
                "task_id": invocation["task_id"],
                "operation": invocation["operation"],
                "parameters": invocation["parameters"],
                "state": row["state"],
                "detail": row["detail"],
                "local_outcome": outcome,
            }
        )
    return results


def planner_feedback(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return bounded semantic feedback without internal identities or evidence paths."""
    feedback: list[dict[str, Any]] = []
    for row in records[-3:]:
        compact: dict[str, Any] = {
            key: row[key]
            for key in (
                "segment",
                "steps_before",
                "steps_after",
                "controller_status",
                "goal",
                "failure",
            )
            if key in row
        }
        planned = row.get("planned_actions")
        if isinstance(planned, list):
            compact["recent_planned_actions"] = planned[-8:]
        outcomes = row.get("execution_outcomes")
        if isinstance(outcomes, list):
            semantic_outcomes = []
            for outcome in outcomes[-8:]:
                if not isinstance(outcome, dict):
                    continue
                local = outcome.get("local_outcome")
                local_summary = (
                    {
                        key: local[key]
                        for key in (
                            "guard",
                            "primitive_executed",
                            "step_count_unchanged",
                            "step_before",
                            "transition",
                            "error_type",
                            "error",
                        )
                        if key in local
                    }
                    if isinstance(local, dict)
                    else None
                )
                semantic_outcomes.append(
                    {
                        key: value
                        for key, value in {
                            "operation": outcome.get("operation"),
                            "parameters": outcome.get("parameters"),
                            "state": outcome.get("state"),
                            "detail": outcome.get("detail"),
                            "local_outcome": local_summary,
                        }.items()
                        if value is not None
                    }
                )
            compact["recent_execution_outcomes"] = semantic_outcomes
        decision = row.get("recovery_decision")
        if isinstance(decision, dict):
            compact["recovery_decision"] = {
                key: decision[key] for key in ("eligible", "reason") if key in decision
            }
        feedback.append(compact)
    return feedback


def controller_diagnostics(api: str) -> dict[str, Any]:
    """Read Controller-owned evidence without changing lifecycle state."""
    evidence: dict[str, Any] = {}
    for route in ("events", "execution-attempts"):
        status, body = http_json("GET", f"{api}/v1/{route}")
        evidence[route] = {"http_status": status, "body": body}
    return evidence


def controller_attempts(api: str, mission_id: str) -> list[dict[str, Any]]:
    """Read Mission attempts with bounded retries across transient HTTP disconnects."""
    failure = "Controller execution-attempt history is unavailable or malformed"
    for retry in range(5):
        try:
            status, body = http_json("GET", f"{api}/v1/execution-attempts")
            if status == 200 and isinstance(body, dict) and isinstance(body.get("attempts"), list):
                return [
                    cast(dict[str, Any], attempt)
                    for attempt in body["attempts"]
                    if isinstance(attempt, dict) and attempt.get("mission_id") == mission_id
                ]
            failure = f"malformed Controller execution-attempt response: HTTP {status}"
        except (OSError, ValueError) as error:
            failure = f"{type(error).__name__}: {error}"
        if retry < 4:
            time.sleep(0.1)
    raise RuntimeError(failure)


def run(args: argparse.Namespace) -> int:
    """Run bounded planning segments and preserve artifacts even on infrastructure failure."""
    repo, output = args.repo.resolve(), args.output.resolve()
    pefa = args.coherent_root.resolve() / "src/experiment/PEFA"
    # A separate port range permits validation alongside a frozen experiment.
    ports = [
        25170 + args.port_offset,
        28170 + args.port_offset,
        28191 + args.port_offset,
        28220 + args.port_offset,
    ]
    if len(set(ports)) != 4 or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("invalid local port range")
    for port in ports:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    output.mkdir(parents=True, exist_ok=False)
    started = time.time()
    processes: list[subprocess.Popen[bytes]] = []
    graph: dict[str, Any] = {}
    task: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    final_steps = 0
    reason = "planning segment budget exhausted"
    controller_completed = False
    controller_cancelled_after_goal = False
    post_goal_primitive_guarded = False
    api = f"http://127.0.0.1:{ports[1]}"
    database = output / "coherent-generic.sqlite3"
    json_write(output / "run-command.json", {"argv": sys.argv})
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required")
        task = load_task_data(pefa, args.env, args.task)
        graph = initial_graph(task)
        contexts = action_contexts(pefa, task, graph)
        max_steps = 2 * int(cast(list[int], task["ground_truth_step_num"])[0]) + 1
        json_write(output / "official-task-input.json", task)
        json_write(output / "initial-action-context.json", contexts)
        catalog_path = output / "capability-catalog.json"
        catalog = catalog_for(contexts)
        # Later actions need not be executable at planning time.
        json_write(catalog_path, catalog)
        config = args.config.read_text(encoding="utf-8")
        config = re.sub(
            r"^capability_catalog_path\s*=.*$",
            f'capability_catalog_path = "{catalog_path.as_posix()}"',
            config,
            count=1,
            flags=re.MULTILINE,
        )
        settings = tomllib.loads(config)
        runtime_config = output / "mission-runtime.toml"
        runtime_config.write_text(config, encoding="utf-8")
        binary_root = args.binary_root.resolve()
        provenance = {
            "mode": "multi-task-dag",
            "prompt_profile": args.prompt_profile,
            "dag_profile": args.dag_profile,
            "public_task": f"{args.env}/task{args.task}",
            "roboguide_commit": git(repo, "rev-parse", "HEAD"),
            "roboguide_status": git(repo, "status", "--short"),
            "coherent_commit": git(args.coherent_root, "rev-parse", "HEAD"),
            "model": settings["mission"]["llm"]["model"],
            "review_model": settings["mission"]["llm"]["review_model"],
            "reasoning_effort": settings["mission"]["llm"]["model_reasoning_effort"],
            "max_steps": max_steps,
            "max_planning_segments": args.max_segments,
            "ports": ports,
            "binary_root": str(binary_root),
            "binary_sha256": {
                n: hashlib.sha256((binary_root / n).read_bytes()).hexdigest()
                for n in ("integration-server", "roboguide-node")
            },
            "source_sha256": {
                str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                for folder in (
                    repo / "tools/e2-generic",
                    repo / "integrations/coherent-local-eaios",
                )
                for p in folder.rglob("*.py")
            },
            "credentials_recorded": False,
        }
        json_write(output / "provenance.json", provenance)
        template = (repo / "scenarios/e2-coherent-graph/node.toml.template").read_text()
        template = template.replace(":25070", f":{ports[0]}").replace(":28120", f":{ports[3]}")
        node_ids = []
        for agent_id in sorted(contexts):
            node_id, _ = render_node(template, output / f"{agent_id}-node.toml", output, agent_id)
            node_ids.append(node_id)
        registry = output / "physical-entity-registry.json"
        json_write(
            registry,
            {
                "schema": "roboguide.physical-entity-registry/v0.1",
                "registry_id": "e2-dag",
                "revision": 1,
                "routing_profile": "one-routable-entity-per-node",
                "entities": [
                    {"entity_id": f"coherent-agent-{i}", "node_id": n}
                    for i, n in zip(sorted(contexts), node_ids, strict=True)
                ],
            },
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(repo / "integrations/coherent-local-eaios"), str(repo / "mission/src")]
        )
        environment["ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP"] = "1"
        processes.append(
            start_process(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "coherent_local_eaios.generic_main",
                    "--port",
                    str(ports[3]),
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
                output / "bridge.log",
                environment,
            )
        )
        wait_service(f"http://127.0.0.1:{ports[3]}/v1/health", processes)
        processes.append(
            start_process(
                [
                    str(binary_root / "integration-server"),
                    f"127.0.0.1:{ports[0]}",
                    str(output / "controller.sqlite3"),
                    f"127.0.0.1:{ports[1]}",
                    f"127.0.0.1:{ports[2]}",
                    str(output / "artifacts"),
                    "",
                    str(registry),
                ],
                output / "controller.log",
                environment,
            )
        )
        wait_service(api + "/healthz", processes)
        for agent_id in contexts:
            processes.append(
                start_process(
                    [
                        str(binary_root / "roboguide-node"),
                        str(output / f"{agent_id}-node.toml"),
                    ],
                    output / f"node-{agent_id}.log",
                    environment,
                )
            )
        for _ in range(120):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("service exited during node registration")
            _, inventory = http_json("GET", api + "/v1/inventory")
            if set(node_ids) <= {n["node_id"] for n in cast(dict[str, Any], inventory)["nodes"]}:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError("node registration timed out")
        json_write(output / "inventory.json", inventory)
        for number in range(1, args.max_segments + 1):
            graph, final_steps = read_graph_database(database)
            if final_steps >= max_steps:
                reason = "primitive budget exhausted"
                break
            if goal_status(task, graph)["passed"]:
                break
            contexts = action_contexts(pefa, task, graph)
            segment = output / "segments" / f"{number:03d}"
            segment.mkdir(parents=True)
            json_write(segment / "action-context.json", contexts)
            objective = segment / "objective.txt"
            objective.write_text(
                objective_text(
                    args.env,
                    args.task,
                    task,
                    contexts,
                    planner_feedback(records),
                    max_steps - final_steps,
                    prompt_profile=args.prompt_profile,
                    dag_profile=args.dag_profile,
                ),
                encoding="utf-8",
            )
            mission_id = f"e2-dag-{args.env}-{args.task}-segment-{number}"
            planning = segment / "planning"
            command = [
                sys.executable,
                str(repo / "tools/e2-full-task17/plan_and_record.py"),
                "--repo",
                str(repo),
                "--config",
                str(runtime_config),
                "--objective",
                str(objective),
                "--output",
                str(planning),
                "--mission-id",
                mission_id,
                "--public-task",
                f"{args.env}/task{args.task}",
                "--adapter-scope",
                f"{args.dag_profile} multi-task DAG with runtime primitive validation",
            ]
            record: dict[str, Any] = {
                "segment": number,
                "mission_id": mission_id,
                "steps_before": final_steps,
            }
            with (segment / "planner.log").open("wb") as log:
                result = subprocess.run(
                    command,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=3600,
                    check=False,
                )
            if result.returncode != 0:
                reason = "planning failed; see segment planner.log and raw provider evidence"
                record["failure"] = reason
                records.append(record)
                break
            plan_bytes = (planning / "mission-plan.json").read_bytes()
            plan = json.loads(plan_bytes)
            try:
                ordered = validate_plan(
                    plan,
                    mission_id,
                    args.env,
                    args.task,
                    contexts,
                    max_steps - final_steps,
                    dag_profile=args.dag_profile,
                )
            except (ValueError, KeyError, TypeError) as error:
                record["failure"] = f"plan admission failed: {error}"
                records.append(record)
                json_write(segment / "validation.json", record)
                reason = record["failure"]
                continue
            json_write(segment / "validation.json", {"passed": True, "actions": ordered})
            # Preserve the unedited submitted plan as evidence.
            (segment / "submitted-plan.json").write_bytes(plan_bytes)
            status, body = http_json("POST", api + "/v1/missions", plan)
            json_write(segment / "submission.json", {"status": status, "body": body})
            if status != 202:
                record["failure"] = "controller rejected plan"
                records.append(record)
                reason = record["failure"]
                break
            deadline = time.monotonic() + args.execution_timeout
            mission: dict[str, Any] = {}
            goal_cancel: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("owned runtime process exited during Mission")
                _, mission_body = http_json("GET", api + f"/v1/missions/{mission_id}")
                mission = cast(dict[str, Any], mission_body)
                live_graph, live_steps = read_graph_database(database)
                live_goal = goal_status(task, live_graph)
                if (
                    live_goal["passed"]
                    and goal_cancel is None
                    and mission.get("status") not in {"Completed", "Cancelled"}
                ):
                    cancel_status, cancel_body = http_json(
                        "POST", api + f"/v1/missions/{mission_id}/cancel"
                    )
                    goal_cancel = {
                        "reason": "official task_goal became true before Mission termination",
                        "steps_at_request": live_steps,
                        "goal": live_goal,
                        "http_status": cancel_status,
                        "body": cancel_body,
                    }
                    json_write(segment / "goal-cancel-request.json", goal_cancel)
                if mission.get("status") in {"Completed", "Failed", "Cancelled"}:
                    break
                time.sleep(0.25)
            json_write(segment / "mission.json", mission)
            graph, final_steps = read_graph_database(database)
            all_local_executions = local_executions(database)
            current_executions = mission_executions(all_local_executions, mission_id)
            controller_attempt_read_error = None
            try:
                current_controller_attempts = controller_attempts(api, mission_id)
            except RuntimeError as error:
                current_controller_attempts = []
                controller_attempt_read_error = str(error)
            local_terminal = bool(current_executions) and all(
                row["state"] in {"COMPLETED", "FAILED", "CANCELLED"} for row in current_executions
            )
            controller_attempts_terminal = bool(current_controller_attempts) and all(
                row.get("status") in {"Completed", "Failed", "Cancelled"}
                for row in current_controller_attempts
            )
            current_goal = goal_status(task, graph)
            post_goal_primitive_guarded = any(
                row["state"] == "CANCELLED"
                and isinstance(row["local_outcome"], dict)
                and row["local_outcome"].get("guard") == "official-goal-already-satisfied"
                for row in current_executions
            )
            record.update(
                {
                    "controller_status": mission.get("status"),
                    "steps_after": final_steps,
                    "planned_actions": ordered,
                    "goal": current_goal,
                    "goal_cancel_request": goal_cancel,
                    "execution_outcomes": current_executions,
                    "controller_execution_attempts": current_controller_attempts,
                    "controller_attempt_read_error": controller_attempt_read_error,
                }
            )
            controller_completed = mission.get("status") == "Completed"
            controller_cancelled_after_goal = bool(
                mission.get("status") == "Cancelled"
                and goal_cancel is not None
                and goal_cancel["http_status"] == 202
                and current_goal["passed"]
            )
            if current_goal["passed"] and (
                controller_completed
                or controller_cancelled_after_goal
                or post_goal_primitive_guarded
            ):
                record["recovery_decision"] = {
                    "eligible": False,
                    "reason": (
                        "official goal reached; Controller completed/cancelled the Mission or "
                        "the adapter recorded a non-mutating post-goal guard"
                    ),
                }
                reason = ""
            elif controller_completed:
                record["recovery_decision"] = {
                    "eligible": True,
                    "reason": "completed observation segment; replan from fresh graph",
                }
                reason = "completed segment did not satisfy official goal"
            elif (
                mission.get("status") == "Failed"
                and local_terminal
                and controller_attempts_terminal
            ):
                record["recovery_decision"] = {
                    "eligible": True,
                    "reason": (
                        "Controller reported failure and both Controller and local attempts "
                        "are terminal; "
                        "replan through Planner, Reviewer, and Repairer from preserved graph"
                    ),
                }
                reason = "terminal execution failure; structured replanning budget exhausted"
            else:
                record["recovery_decision"] = {
                    "eligible": False,
                    "reason": (
                        "execution authority or Controller attempt evidence is ambiguous, or "
                        "cancellation was not goal-triggered"
                    ),
                }
                reason = f"controller {mission.get('status')}; unsafe to start another Mission"
            if not record["recovery_decision"]["eligible"] or current_goal["passed"]:
                record["runtime_diagnostics_file"] = "runtime-diagnostics.json"
                json_write(segment / "runtime-diagnostics.json", controller_diagnostics(api))
            elif mission.get("status") == "Failed":
                record["runtime_diagnostics_file"] = "runtime-diagnostics.json"
                json_write(segment / "runtime-diagnostics.json", controller_diagnostics(api))
            records.append(record)
            json_write(segment / "result.json", record)
            json_write(output / "segments.json", records)
            if reason == "":
                break
            if not record["recovery_decision"]["eligible"]:
                break
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        json_write(output / "runner-error.json", {"error": reason})
    finally:
        # Read-only snapshots precede shutdown; hashes are taken only after writers have stopped.
        if processes:
            for route, filename in (
                ("events", "controller-events.json"),
                ("execution-attempts", "execution-attempts.json"),
            ):
                try:
                    _, body = http_json("GET", api + "/v1/" + route)
                    json_write(output / filename, body)
                except (OSError, ValueError):
                    pass
        stop_processes(processes)
        if database.exists():
            graph, final_steps = read_graph_database(database)
            json_write(output / "local-executions.json", local_executions(database))
        check = goal_status(task, graph) if task and graph else {"passed": False}
        success = bool(check["passed"]) and (
            controller_completed or controller_cancelled_after_goal or post_goal_primitive_guarded
        )
        requests = list(output.glob("segments/*/planning/llm/*-request.json"))
        responses = [
            json.loads(p.read_text())
            for p in output.glob("segments/*/planning/llm/*-response.json")
        ]
        verdict = {
            "mode": "multi-task-dag",
            "prompt_profile": args.prompt_profile,
            "dag_profile": args.dag_profile,
            "public_task": f"{args.env}/task{args.task}",
            "success": success,
            "failure_reason": None if success else reason,
            "official_goal_check": check,
            "controller_completed": controller_completed,
            "controller_cancelled_after_goal": controller_cancelled_after_goal,
            "post_goal_primitive_guarded": post_goal_primitive_guarded,
            "primitive_steps": final_steps,
            "planning_segments": len(records),
            "model_calls": len(requests),
            "model_elapsed_seconds": sum(r["elapsed_seconds"] for r in responses),
            "wall_time_seconds": time.time() - started,
        }
        json_write(output / "final-scene-graph.json", graph)
        json_write(output / "segments.json", records)
        json_write(output / "verdict.json", verdict)
        hashes = [
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(output).as_posix()}"
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name != "SHA256SUMS"
        ]
        (output / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if success else 1


def main() -> int:
    """Parse explicit deployment paths and finite run budgets."""
    parser = argparse.ArgumentParser()
    for option in ("repo", "coherent-root", "binary-root", "config", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--env", choices=[f"env{i}" for i in range(5)], required=True)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--max-segments", type=int, default=3)
    parser.add_argument("--port-offset", type=int, default=0)
    parser.add_argument("--execution-timeout", type=int, default=180)
    parser.add_argument("--prompt-profile", choices=PROMPT_PROFILES, default="fair")
    parser.add_argument("--dag-profile", choices=DAG_PROFILES, default="serial")
    args = parser.parse_args()
    if args.max_segments < 1 or args.execution_timeout < 1:
        parser.error("budgets must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
