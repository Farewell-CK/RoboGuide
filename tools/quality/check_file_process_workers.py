"""Exercise actual file-worker parent death, original PID evidence and old-writer refusal."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from check_state_verifier_processes import ROOT, address, request, save, stop, wait_for


def original_process(database: Path, execution: str) -> dict[str, Any]:
    """Read the actual original instance, PID and budgets independently from SQLite."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT instance_id,backend_instance_id,invocation_digest,budget_json,pid,exit_json "
            "FROM original_processes WHERE execution_id=?",
            (execution,),
        ).fetchone()
    if row is None:
        return {}
    return {
        "instance_id": row[0],
        "backend_instance_id": row[1],
        "invocation_digest": row[2],
        "budget": json.loads(row[3]),
        "pid": row[4],
        "exit": None if row[5] is None else json.loads(row[5]),
    }


def old_reader(archive: Path, directory: Path) -> None:
    """Run the actual archived FileJobs implementation against the new readonly old-table fence."""
    from roboguide_memory_provider.store import MemoryStore

    specification = importlib.util.spec_from_file_location(
        "roboguide_memory_provider.original_thread_jobs", archive
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    store = MemoryStore(
        directory / "backend",
        "files",
        (directory,),
        16 * 1024 * 1024,
        export_config=directory / "exports.json",
        export_directory=directory,
    )
    try:
        try:
            jobs = module.FileJobs(store)
        except sqlite3.OperationalError as error:
            assert "view" in str(error).lower(), error
            print(json.dumps({"old_writer_refused": True, "error": str(error)}))
        else:
            jobs.close()
            raise AssertionError("actual original thread writer reopened process authority")
    finally:
        store.close()


def run(directory: Path, archive: Path) -> dict[str, Any]:
    """Reap only owned real workers; independent crash evidence never enters production SQL."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = directory / "source.bin"
    source.write_bytes((ROOT / "core/domain/src/memory.rs").read_bytes())
    expected = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    selector = {"memory_id": uuid.uuid4().hex, "revision_id": uuid.uuid4().hex}
    manifest = {
        "selector": selector,
        "artifact": {"content_digest": expected, "byte_size": source.stat().st_size},
    }
    save(directory / "exports.json", [{"manifest": manifest, "artifact_path": source.name}])
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ROBOGUIDE_", "OPENAI_", "EMOS_"))
    }
    environment["PYTHONPATH"] = str(ROOT / "integrations/memory-provider")
    processes: list[subprocess.Popen[bytes]] = []
    adopted: set[int] = set()
    library = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if (
        library.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0
        or library.prctl(36, 1, 0, 0, 0) != 0
    ):
        raise OSError(ctypes.get_errno(), "owned orphan reaping requires a process-local subreaper")
    database = directory / "backend/file-jobs.sqlite3"

    def start() -> tuple[subprocess.Popen[bytes], str]:
        """Start the production backend against the exact original SQL and writer lease."""
        route = address()
        with (directory / f"backend-{len(processes)}.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "roboguide_memory_provider",
                    "--root",
                    str(directory / "backend"),
                    "--provider-id",
                    "files",
                    "--input-root",
                    str(directory),
                    "--export-config",
                    str(directory / "exports.json"),
                    "--export-directory",
                    str(directory),
                    "--listen-port",
                    route.rsplit(":", 1)[1],
                    "--enable-file-jobs",
                    "--worker-processes",
                    "--max-job-seconds",
                    "30",
                    "--max-job-rounds",
                    "16777216",
                ],
                env=environment,
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
        processes.append(process)
        endpoint = "http://" + route
        wait_for(
            lambda: request(endpoint, "/v1/file-jobs/health")[1].get("ready"),
            "actual owned backend startup",
        )
        return process, endpoint

    def invocation(index: int, rounds: int) -> dict[str, Any]:
        """Declare software-only operation metadata without any Node or device fact."""
        group = "group" + str(index)
        return {
            "execution_id": f"attempt-{len(group)}:{group}-4:hash-6:worker-1",
            "group_id": group,
            "task_id": "hash",
            "role_id": "worker",
            "execution_generation": 1,
            "parameters": {
                "input_memory_id": selector["memory_id"],
                "input_revision_id": selector["revision_id"],
                "rounds": rounds,
            },
        }

    def outcome(endpoint: str, execution: str) -> dict[str, Any]:
        """Read one original software outcome through its actual maintained HTTP route."""
        return dict(
            request(endpoint, "/v1/file-jobs/status", {"execution_id": execution}, method="GET")[1]
        )

    try:
        parent, endpoint = start()
        completed = invocation(1, 2)
        assert request(endpoint, "/v1/file-jobs/compute", completed)[0] == 200
        first = wait_for(
            lambda: (
                row
                if (row := outcome(endpoint, completed["execution_id"]))["state"] == "COMPLETED"
                else None
            ),
            "actual child hash result",
        )
        assert first["result"]["input_digests"] == [expected]
        completed_process = original_process(database, completed["execution_id"])
        assert completed_process["exit"]["exit_code"] == 0
        assert request(endpoint, "/v1/file-jobs/compute", completed)[1] == first
        interrupted = invocation(2, 16_777_216)
        assert request(endpoint, "/v1/file-jobs/compute", interrupted)[0] == 200
        original = wait_for(
            lambda: (
                row
                if (row := original_process(database, interrupted["execution_id"])).get("pid")
                else None
            ),
            "actual original PID committed before work",
        )
        child = int(original["pid"])
        os.kill(child, signal.SIGSTOP)
        wait_for(
            lambda: (
                Path(f"/proc/{child}/status")
                .read_text()
                .split("State:", 1)[1]
                .lstrip()
                .startswith("T")
            ),
            "owned worker actually suspended",
        )
        time.sleep(0.15)
        parent.kill()
        parent.wait(timeout=5)
        adopted.add(child)
        restored, endpoint = start()
        # The stopped child still exists: the new parent must acquire the original writer anyway.
        assert Path(f"/proc/{child}").exists()
        assert outcome(endpoint, interrupted["execution_id"])["state"] == "UNKNOWN"
        assert request(endpoint, "/v1/file-jobs/compute", interrupted)[1]["state"] == "UNKNOWN"
        health = request(endpoint, "/v1/file-jobs/health")[1]
        assert health["safe"] is False and health["unverified_original_processes"] == 1
        assert original_process(database, interrupted["execution_id"]) == original
        os.kill(child, signal.SIGCONT)

        def reap_original() -> tuple[int, int] | None:
            """Reap only this adopted original child, without writing its independent proof back."""
            pid, status = os.waitpid(child, os.WNOHANG)
            return (pid, status) if pid else None

        _, status = wait_for(reap_original, "parent EOF stops the actual original orphan")
        adopted.remove(child)
        independent_exit = os.waitstatus_to_exitcode(status)
        assert independent_exit != 0
        assert original_process(database, interrupted["execution_id"]) == original
        assert all(
            row["execution_id"] != interrupted["execution_id"]
            for row in request(endpoint, "/v1/file-jobs/safe-state")[1]["observations"]
        )
        cancelled = invocation(3, 16_777_216)
        assert request(endpoint, "/v1/file-jobs/compute", cancelled)[0] == 200
        wait_for(
            lambda: original_process(database, cancelled["execution_id"]).get("pid"),
            "second parent's actual original child",
        )
        assert (
            request(endpoint, "/v1/file-jobs/cancel", {"execution_id": cancelled["execution_id"]})[
                0
            ]
            == 200
        )
        wait_for(
            lambda: outcome(endpoint, cancelled["execution_id"])["state"] == "CANCELLED",
            "actual cancel and original OS wait",
        )
        cancelled_process = original_process(database, cancelled["execution_id"])
        assert cancelled_process["exit"]["exit_code"] < 0
        stop(restored)
        sys.path.insert(0, str(ROOT / "integrations/memory-provider"))
        from roboguide_memory_provider.process_jobs import ProcessFileJobs
        from roboguide_memory_provider.store import MemoryStore

        with closing(
            MemoryStore(
                directory / "backend",
                "files",
                (directory,),
                16 * 1024 * 1024,
                export_config=directory / "exports.json",
                export_directory=directory,
            )
        ) as store:
            jobs = ProcessFileJobs(store, max_job_seconds=30, max_job_rounds=16_777_216)
            try:
                assert jobs.health()["safe"] is False
                assert (
                    "unverified_original_process:" + interrupted["execution_id"]
                    in jobs.reclamation_blockers()
                )
                assert jobs.compute(interrupted)["state"] == "UNKNOWN"
            finally:
                jobs.close()
        old = subprocess.run(
            [sys.executable, str(Path(__file__)), "--old-reader", str(archive), str(directory)],
            env=environment,
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=10,
        )
        old_result = json.loads(old.stdout)
        save(directory / "old-writer.json", old_result)
        assert original_process(database, interrupted["execution_id"]) == original
        result = {
            "passed": True,
            "devices": 0,
            "nodes": 0,
            "models": 0,
            "simulators": 0,
            "actual_original_children": 3,
            "actual_backend_exit_codes": [process.returncode for process in processes],
            "completed": completed_process,
            "cancelled": cancelled_process,
            "interrupted": original,
            "independent_orphan_exit_code": independent_exit,
            "independent_proof_not_imported": True,
            "original_budget_unchanged": True,
            "unknown_reclamation_fenced": True,
            "old_writer_refused": True,
            "source_sha256": expected,
            "old_implementation_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "implementation": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(
                    (ROOT / "integrations/memory-provider/roboguide_memory_provider").glob("*.py")
                )
            },
        }
        save(directory / "RESULT.json", result)
        return result
    finally:
        for process in reversed(processes):
            stop(process)
        for child in adopted:
            try:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            except ProcessLookupError:
                pass
        library.prctl(36, previous.value, 0, 0, 0)


def main() -> None:
    """Run an explicitly requested owned process gate or its actual archived-writer helper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--old-file-jobs", type=Path)
    parser.add_argument("--old-reader", nargs=2, type=Path)
    arguments = parser.parse_args()
    if arguments.old_reader:
        old_reader(*arguments.old_reader)
    elif arguments.output and arguments.old_file_jobs:
        print(
            json.dumps(
                run(arguments.output.resolve(), arguments.old_file_jobs.resolve()), sort_keys=True
            )
        )
    else:
        parser.error("--output and --old-file-jobs are required")


if __name__ == "__main__":
    main()
