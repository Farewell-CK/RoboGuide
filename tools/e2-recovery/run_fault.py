"""Run one clean or F1 Node-loss RoboGuide–COHERENT task."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GENERIC = HERE.parent / "e2-generic"
INTEGRATION = HERE.parent.parent / "integrations/coherent-local-eaios"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(GENERIC))
sys.path.insert(0, str(INTEGRATION))

import run_dag  # type: ignore[import-untyped]  # noqa: E402
from fault_runtime import FaultRuntime, json_write  # noqa: E402


def unused_port(port: int) -> None:
    """Fail before the run if the private upstream bridge port is occupied."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def fault_verdict(
    output: Path, profile: str, inject_after: int | None, target_agent_id: int | None
) -> dict[str, Any]:
    """Derive a fault-specific verdict without rewriting the task verdict."""
    base_path = output / "verdict.json"
    base = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None
    timeline_path = output / "fault-timeline.json"
    timeline = (
        json.loads(timeline_path.read_text(encoding="utf-8"))
        if timeline_path.exists()
        else []
    )
    events = [item.get("event") for item in timeline]
    pre_fault_path = output / "pre-fault-graph.json"
    pre_fault = (
        json.loads(pre_fault_path.read_text(encoding="utf-8"))
        if pre_fault_path.exists()
        else None
    )
    attempts_path = output / "execution-attempts.json"
    attempts = (
        json.loads(attempts_path.read_text(encoding="utf-8"))
        if attempts_path.exists()
        else {"attempts": []}
    )
    injected = "fault_triggered" in events
    infrastructure_ok = "injection_failed" not in events
    if profile == "f0-clean":
        recovery_valid = not injected
    else:
        recovery_valid = all(
            item in events
            for item in (
                "fault_triggered",
                "local_handle_confirmed",
                "primary_exited",
                "recovery_required",
                "recovery_authorized",
                "same_owner_registered",
                "rebind_completed",
            )
        ) and bool(pre_fault and pre_fault.get("primitive_steps") == inject_after)
    return {
        "schema": "roboguide.e2-node-failure-verdict/v0.1",
        "fault_profile": profile,
        "inject_after_completed_primitives": inject_after,
        "target_agent_id": target_agent_id,
        "base_task_verdict": base,
        "injection_valid": recovery_valid,
        "infrastructure_ok": infrastructure_ok,
        "task_success": bool(base and base.get("success")),
        "full_fault_run_success": bool(
            base and base.get("success") and recovery_valid and infrastructure_ok
        ),
        "timeline_events": events,
        "controller_attempts": attempts.get("attempts", []),
        "pre_fault_graph": pre_fault,
        "interpretation": (
            "F1 observes the accepted k+1 local handle while the generic bridge holds it before "
            "any graph effect, terminates Dog-A, authorizes explicit same-owner recovery, restarts "
            "the original Node identity, and requires a fresh Controller attempt. The harness "
            "never executes or edits a primitive action."
        ),
    }


def refresh_hashes(output: Path) -> None:
    """Regenerate the complete evidence manifest after wrapper artifacts are written."""
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}"
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (output / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    """Install a process-local fault boundary, then call the existing DAG runner unchanged."""
    output = args.output.resolve()
    expected_bridge_port = 28220 + args.port_offset
    actual_bridge_port = expected_bridge_port + args.upstream_port_delta
    unused_port(actual_bridge_port)
    inject_after = (
        None
        if args.fault_profile == "f0-clean" or args.fault_agent_id is not None
        else args.inject_after_steps
    )
    fault_spec = {
        "schema": "roboguide.e2-fault-spec/v0.1",
        "fault_profile": args.fault_profile,
        "public_task": f"{args.env}/task{args.task}",
        "inject_after_completed_primitives": inject_after,
        "target_agent_id": args.fault_agent_id,
        "prompt_profile": args.prompt_profile,
        "dag_profile": "serial",
        "model_is_read_from_frozen_config": True,
    }
    staging_spec = output.parent / f"{output.name}-fault-spec.json"
    json_write(staging_spec, fault_spec)
    manager = FaultRuntime(
        output=output,
        expected_bridge_port=expected_bridge_port,
        actual_bridge_port=actual_bridge_port,
        controller_api=f"http://127.0.0.1:{28170 + args.port_offset}",
        inject_after_steps=inject_after,
        target_agent_id=args.fault_agent_id,
        original_start=run_dag.start_process,
    )
    original = run_dag.start_process
    run_dag.start_process = manager.start_process
    namespace = argparse.Namespace(
        repo=args.repo,
        coherent_root=args.coherent_root,
        binary_root=args.binary_root,
        config=args.config,
        output=output,
        env=args.env,
        task=args.task,
        max_segments=args.max_segments,
        port_offset=args.port_offset,
        execution_timeout=args.execution_timeout,
        prompt_profile=args.prompt_profile,
        dag_profile="serial",
    )
    try:
        return_code = run_dag.run(namespace)
    finally:
        run_dag.start_process = original
        manager.finish()
        json_write(output / "fault-spec.json", fault_spec)
        staging_spec.unlink(missing_ok=True)
        verdict = fault_verdict(output, args.fault_profile, inject_after, args.fault_agent_id)
        json_write(output / "fault-verdict.json", verdict)
        refresh_hashes(output)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict["full_fault_run_success"] else max(return_code, 1)


def main() -> int:
    """Parse one frozen recovery run."""
    parser = argparse.ArgumentParser()
    for option in ("repo", "coherent-root", "binary-root", "config", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--env", default="env4")
    parser.add_argument("--task", type=int, default=17)
    parser.add_argument("--fault-profile", choices=("f0-clean", "f1-node-loss"), required=True)
    parser.add_argument("--inject-after-steps", type=int, default=6)
    parser.add_argument("--fault-agent-id", type=int)
    parser.add_argument("--prompt-profile", choices=("fair",), default="fair")
    parser.add_argument("--max-segments", type=int, default=3)
    parser.add_argument("--execution-timeout", type=int, default=360)
    parser.add_argument("--port-offset", type=int, default=0)
    parser.add_argument("--upstream-port-delta", type=int, default=20)
    arguments = parser.parse_args()
    if (
        arguments.inject_after_steps < 1 and arguments.fault_agent_id is None
    ) or arguments.upstream_port_delta < 1:
        parser.error("fault step and private port delta must be positive")
    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
